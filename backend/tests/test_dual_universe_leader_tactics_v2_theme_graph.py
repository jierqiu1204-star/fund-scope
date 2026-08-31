from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import V2ContractError
from app.services.strategy_lab.dual_universe_leader_tactics_v2_theme_graph import (
    MAX_THEME_GRAPH_BATCH_SIZE,
    THEME_DEFINITIONS,
    THEME_REGISTRY_HASH,
    AshareIndustryPathFact,
    AshareThemeCaptureRunFact,
    AshareThemeRelationFact,
    AshareThemeStateFact,
    CaptureStatus,
    ThemeRelationKind,
    persist_industry_path_batch,
    persist_theme_capture_run_batch,
    persist_theme_relation_batch,
    persist_theme_state_batch,
    registered_theme_definitions,
    resolve_theme_definitions,
    theme_definitions_for_key,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_theme_graph_state import (
    load_persisted_theme_state_map,
)

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = (
    VERSIONS_DIR / "20260819_000073_ashare_multilayer_theme_graph.py"
)


def _migration():
    spec = spec_from_file_location(MIGRATION_PATH.stem, MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_legacy_fine_theme_table(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE ashare_fine_theme_membership_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_code VARCHAR(32) NOT NULL,
                group_id VARCHAR(256) NOT NULL,
                theme VARCHAR(256) NOT NULL,
                normalized_theme_key VARCHAR(128) NOT NULL,
                hierarchy_level VARCHAR(32) NOT NULL,
                effective_from DATE NOT NULL,
                effective_to DATE,
                received_at DATETIME NOT NULL,
                taxonomy_version VARCHAR(128) NOT NULL,
                source VARCHAR(128) NOT NULL,
                confidence VARCHAR(32) NOT NULL,
                mapping_kind VARCHAR(32) NOT NULL,
                fact_hash VARCHAR(128) NOT NULL UNIQUE
            )
            """
        )
    )


def test_additive_migration_preserves_legacy_rows_and_downgrades() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_fine_theme_table(connection)
        connection.execute(
            text(
                """
                INSERT INTO ashare_fine_theme_membership_facts
                    (asset_code, group_id, theme, normalized_theme_key,
                     hierarchy_level, effective_from, received_at,
                     taxonomy_version, source, confidence, mapping_kind, fact_hash)
                VALUES
                    ('600111', 'fine_theme:rare_earth', '稀土/稀土永磁',
                     'rare_earth', 'fine_theme', '2026-08-18',
                     '2026-08-18 16:00:00', 'legacy-v1', 'legacy-source',
                     'observed_current', 'historical_pit', :fact_hash)
                """
            ),
            {"fact_hash": "a" * 64},
        )

        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "ashare_industry_path_facts",
            "ashare_theme_capture_runs",
            "ashare_theme_state_facts",
        }.issubset(inspector.get_table_names())
        fine_columns = {
            item["name"]
            for item in inspector.get_columns("ashare_fine_theme_membership_facts")
        }
        assert {
            "provider_theme_code",
            "provider_theme_label",
            "membership_reason",
            "exposure_weight",
            "relation_kind",
            "source_snapshot_date",
            "source_snapshot_hash",
            "capture_run_hash",
        }.issubset(fine_columns)
        assert {
            "ix_ashare_fine_theme_context_pit",
            "ix_ashare_fine_theme_source_snapshot",
        }.issubset(
            {
                item["name"]
                for item in inspector.get_indexes(
                    "ashare_fine_theme_membership_facts"
                )
            }
        )
        assert connection.scalar(
            text("SELECT COUNT(*) FROM ashare_fine_theme_membership_facts")
        ) == 1

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "ashare_industry_path_facts" not in inspector.get_table_names()
        assert "ashare_theme_capture_runs" not in inspector.get_table_names()
        assert "ashare_theme_state_facts" not in inspector.get_table_names()
        fine_columns = {
            item["name"]
            for item in inspector.get_columns("ashare_fine_theme_membership_facts")
        }
        assert "relation_kind" not in fine_columns
        assert connection.scalar(
            text("SELECT COUNT(*) FROM ashare_fine_theme_membership_facts")
        ) == 1


def test_registry_keeps_provider_concepts_distinct_from_disclosed_proxies() -> None:
    assert registered_theme_definitions() == THEME_DEFINITIONS
    assert len(
        registered_theme_definitions(
            relation_kind=ThemeRelationKind.INDUSTRY_UNION_PROXY
        )
    ) == 3
    assert len(THEME_REGISTRY_HASH) == 64
    for canonical_key in {item.canonical_key for item in THEME_DEFINITIONS}:
        definitions = {
            item.relation_kind
            for item in THEME_DEFINITIONS
            if item.canonical_key == canonical_key
        }
        assert ThemeRelationKind.PROVIDER_CONCEPT in definitions

    liquid_cooling = resolve_theme_definitions(
        "液冷服务器", relation_kind=ThemeRelationKind.PROVIDER_CONCEPT
    )[0]
    assert liquid_cooling.provider_theme_code == "BK1138"
    assert liquid_cooling.provider_theme_label == "液冷服务器"
    assert theme_definitions_for_key("liquid_cooling") == (liquid_cooling,)

    provider = resolve_theme_definitions(
        "创新药", relation_kind=ThemeRelationKind.PROVIDER_CONCEPT
    )[0]
    proxy = resolve_theme_definitions(
        "创新药产业代理",
        relation_kind=ThemeRelationKind.INDUSTRY_UNION_PROXY,
    )[0]
    assert provider.provider_theme_code == "BK1106"
    assert provider.sw3_component_labels == ()
    assert proxy.provider_theme_code is None
    assert proxy.provider_theme_label is None
    assert proxy.sw3_component_labels == ("其他生物制品", "化学制剂", "疫苗")
    assert len(theme_definitions_for_key("innovation_drug")) == 2


def _industry_path() -> AshareIndustryPathFact:
    return AshareIndustryPathFact(
        asset_code="600111",
        taxonomy="sw2021",
        taxonomy_version="tickflow.sw2021.2026-08-18",
        mapping_kind="primary_hierarchy",
        level1_code="801050",
        level1_label="有色金属",
        level2_code="801054",
        level2_label="小金属",
        level3_code="850544",
        level3_label="稀土",
        effective_from=date(2026, 8, 18),
        effective_to=None,
        snapshot_date=date(2026, 8, 18),
        received_at=datetime(2026, 8, 18, 16, 0),
        source="tickflow.universes",
        confidence=1.0,
        source_snapshot_hash="s" * 64,
    )


def _relation(
    *, canonical_key: str, provider_label: str
) -> AshareThemeRelationFact:
    return AshareThemeRelationFact(
        asset_code="600111",
        canonical_theme_key=canonical_key,
        theme_label=provider_label,
        relation_kind=ThemeRelationKind.PROVIDER_CONCEPT,
        effective_from=date(2026, 8, 18),
        effective_to=None,
        received_at=datetime(2026, 8, 18, 16, 0),
        taxonomy_version="eastmoney.current.v1",
        source="eastmoney.concept.current",
        confidence=1.0,
        source_snapshot_date=date(2026, 8, 18),
        source_snapshot_hash=f"snapshot-{canonical_key}",
        capture_run_hash=f"capture-{canonical_key}",
        provider_theme_code=None,
        provider_theme_label=provider_label,
    )


def _complete_capture() -> AshareThemeCaptureRunFact:
    return AshareThemeCaptureRunFact(
        source="tickflow.universes",
        source_snapshot_id="tickflow-sw3:2026-08-18",
        source_snapshot_date=date(2026, 8, 18),
        taxonomy_version="sw2021",
        registry_version="registry-v1",
        status=CaptureStatus.COMPLETE,
        expected_count=10,
        completed_count=10,
        content_hash="c" * 64,
        cursor=None,
        started_at=datetime(2026, 8, 18, 15, 30),
        completed_at=datetime(2026, 8, 18, 15, 31),
        received_at=datetime(2026, 8, 18, 15, 31),
    )


def test_hashes_are_canonical_and_non_finite_values_fail_closed() -> None:
    path = _industry_path()
    assert len(path.fact_hash) == 64
    assert _industry_path().fact_hash == path.fact_hash
    with pytest.raises(V2ContractError, match="canonical payload"):
        AshareIndustryPathFact(
            **{
                key: value
                for key, value in path.canonical_payload().items()
                if key not in {"schema_version", "fact_type"}
            },
            fact_hash="wrong",
        )
    with pytest.raises(V2ContractError, match="finite"):
        AshareIndustryPathFact(
            **{
                key: value
                for key, value in path.canonical_payload().items()
                if key not in {"schema_version", "fact_type", "confidence"}
            },
            confidence=float("nan"),
        )
    with pytest.raises(V2ContractError, match="finite"):
        AshareThemeRelationFact(
            **{
                key: value
                for key, value in _relation(
                    canonical_key="rare_earth", provider_label="稀土永磁"
                ).canonical_payload().items()
                if key
                not in {
                    "schema_version",
                    "fact_type",
                    "relation_kind",
                    "exposure_weight",
                }
            },
            relation_kind=ThemeRelationKind.PROVIDER_CONCEPT,
            exposure_weight=float("inf"),
        )


def test_capture_cannot_be_sealed_until_source_snapshot_is_complete() -> None:
    complete = _complete_capture()
    assert len(complete.run_hash) == 64
    with pytest.raises(V2ContractError, match="sealed"):
        AshareThemeCaptureRunFact(
            **{
                key: value
                for key, value in complete.canonical_payload().items()
                if key
                not in {
                    "schema_version",
                    "fact_type",
                    "status",
                    "completed_count",
                }
            },
            status=CaptureStatus.COMPLETE,
            completed_count=9,
        )
    with pytest.raises(V2ContractError, match="sealed as complete"):
        AshareThemeCaptureRunFact(
            **{
                key: value
                for key, value in complete.canonical_payload().items()
                if key
                not in {
                    "schema_version",
                    "fact_type",
                    "status",
                    "completed_at",
                    "content_hash",
                }
            },
            status=CaptureStatus.PARTIAL,
            completed_at=None,
            content_hash=None,
        )


def test_theme_state_rejects_non_finite_and_unexplained_unavailable_values() -> None:
    common = {
        "context_kind": "provider_concept",
        "context_key": "innovation_drug",
        "state_date": date(2026, 8, 18),
        "source_cutoff": datetime(2026, 8, 18, 15, 0),
        "received_at": datetime(2026, 8, 18, 15, 10),
        "registry_version": "registry-v1",
        "source_snapshot_hash": "s" * 64,
        "eligible_member_count": 6,
        "up_member_count": 4,
        "median_return_1d": 0.01,
        "median_return_5d": 0.03,
        "relative_market_return_1d": 0.005,
        "amount_participation": 0.2,
        "leader_count": 1,
        "limit_up_count": None,
        "limit_down_count": None,
        "input_hash": "i" * 64,
    }
    with pytest.raises(V2ContractError, match="finite"):
        AshareThemeStateFact(
            **common,
            up_breadth=float("nan"),
            available=True,
            unavailable_reasons=(),
        )
    with pytest.raises(V2ContractError, match="exact reasons"):
        AshareThemeStateFact(
            **common,
            up_breadth=None,
            available=False,
            unavailable_reasons=(),
        )


def _available_state() -> AshareThemeStateFact:
    return AshareThemeStateFact(
        context_kind="industry_union_proxy",
        context_key="rare_earth",
        state_date=date(2026, 8, 18),
        source_cutoff=datetime(2026, 8, 18, 15, 0),
        received_at=datetime(2026, 8, 18, 15, 10),
        registry_version="registry-v1",
        source_snapshot_hash="s" * 64,
        eligible_member_count=6,
        up_member_count=4,
        up_breadth=4 / 6,
        median_return_1d=0.01,
        median_return_5d=0.03,
        relative_market_return_1d=0.005,
        amount_participation=0.2,
        leader_count=1,
        limit_up_count=None,
        limit_down_count=None,
        available=True,
        unavailable_reasons=(),
        input_hash="i" * 64,
    )


@pytest.mark.asyncio
async def test_multi_label_relations_and_complete_capture_persist_idempotently(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'theme-graph.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_fine_theme_membership_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code TEXT NOT NULL, group_id TEXT NOT NULL,
                    theme TEXT NOT NULL, normalized_theme_key TEXT NOT NULL,
                    hierarchy_level TEXT NOT NULL, effective_from DATE NOT NULL,
                    effective_to DATE, received_at DATETIME NOT NULL,
                    taxonomy_version TEXT NOT NULL, source TEXT NOT NULL,
                    confidence TEXT NOT NULL, mapping_kind TEXT NOT NULL,
                    fact_hash TEXT NOT NULL UNIQUE, provider_theme_code TEXT,
                    provider_theme_label TEXT, membership_reason TEXT,
                    exposure_weight REAL, relation_kind TEXT,
                    source_snapshot_date DATE, source_snapshot_hash TEXT,
                    capture_run_hash TEXT
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_industry_path_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code TEXT NOT NULL, taxonomy TEXT NOT NULL,
                    taxonomy_version TEXT NOT NULL, mapping_kind TEXT NOT NULL,
                    level1_code TEXT, level1_label TEXT, level2_code TEXT,
                    level2_label TEXT, level3_code TEXT, level3_label TEXT,
                    effective_from DATE NOT NULL, effective_to DATE,
                    snapshot_date DATE NOT NULL, received_at DATETIME NOT NULL,
                    source TEXT NOT NULL, confidence REAL NOT NULL,
                    source_snapshot_hash TEXT NOT NULL, fact_hash TEXT NOT NULL UNIQUE,
                    UNIQUE (asset_code, taxonomy, taxonomy_version, effective_from, source)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_theme_state_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    context_kind TEXT NOT NULL, context_key TEXT NOT NULL,
                    state_date DATE NOT NULL, source_cutoff DATETIME NOT NULL,
                    received_at DATETIME NOT NULL, registry_version TEXT NOT NULL,
                    source_snapshot_hash TEXT NOT NULL,
                    eligible_member_count INTEGER NOT NULL,
                    up_member_count INTEGER NOT NULL, up_breadth REAL,
                    median_return_1d REAL, median_return_5d REAL,
                    relative_market_return_1d REAL, amount_participation REAL,
                    leader_count INTEGER, limit_up_count INTEGER,
                    limit_down_count INTEGER, available BOOLEAN NOT NULL,
                    unavailable_reasons_json TEXT NOT NULL,
                    input_hash TEXT NOT NULL, state_hash TEXT NOT NULL UNIQUE,
                    UNIQUE (context_kind, context_key, state_date, input_hash)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_theme_capture_runs (
                    run_hash TEXT PRIMARY KEY, source TEXT NOT NULL,
                    source_snapshot_id TEXT NOT NULL,
                    source_snapshot_date DATE NOT NULL,
                    taxonomy_version TEXT NOT NULL, registry_version TEXT NOT NULL,
                    status TEXT NOT NULL, expected_count INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL, content_hash TEXT,
                    cursor TEXT, started_at DATETIME NOT NULL,
                    completed_at DATETIME, received_at DATETIME NOT NULL,
                    error_reason TEXT
                )
                """
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    relations = (
        _relation(canonical_key="rare_earth", provider_label="稀土永磁"),
        _relation(canonical_key="passive_components", provider_label="被动元件概念"),
    )
    async with session_factory() as session:
        inserted = await persist_theme_relation_batch(session, relations)
        duplicate = await persist_theme_relation_batch(session, relations)
        capture_inserted = await persist_theme_capture_run_batch(
            session, (_complete_capture(),)
        )
        capture_duplicate = await persist_theme_capture_run_batch(
            session, (_complete_capture(),)
        )
        path_inserted = await persist_industry_path_batch(session, (_industry_path(),))
        path_duplicate = await persist_industry_path_batch(session, (_industry_path(),))
        base_state = _available_state()
        states = (
            base_state,
            replace(
                base_state,
                context_key="passive_components",
                median_return_1d=0.02,
                median_return_5d=0.04,
                up_breadth=0.75,
                input_hash="j" * 64,
                state_hash="",
            ),
            replace(
                base_state,
                context_key="innovation_drug",
                median_return_1d=0.03,
                median_return_5d=0.05,
                up_breadth=0.8,
                input_hash="k" * 64,
                state_hash="",
            ),
        )
        state_inserted = await persist_theme_state_batch(session, states)
        state_duplicate = await persist_theme_state_batch(session, states)
        page_state = await load_persisted_theme_state_map(
            session,
            group_ids=("passive_components",),
            signal_date=date(2026, 8, 18),
            source_cutoff=datetime(2026, 8, 18, 15, 30),
        )
        relation_count = await session.scalar(
            text(
                "SELECT COUNT(*) FROM ashare_fine_theme_membership_facts "
                "WHERE asset_code = '600111'"
            )
        )
        capture_count = await session.scalar(
            text("SELECT COUNT(*) FROM ashare_theme_capture_runs")
        )
        path_count = await session.scalar(
            text("SELECT COUNT(*) FROM ashare_industry_path_facts")
        )
        state_count = await session.scalar(
            text("SELECT COUNT(*) FROM ashare_theme_state_facts")
        )
    await engine.dispose()

    assert (inserted, duplicate, relation_count) == (2, 0, 2)
    assert (capture_inserted, capture_duplicate, capture_count) == (1, 0, 1)
    assert (path_inserted, path_duplicate, path_count) == (1, 0, 1)
    assert (state_inserted, state_duplicate, state_count) == (3, 0, 3)
    assert page_state["passive_components"]["percentiles"] == (0.5, 0.5, 0.5)


@pytest.mark.asyncio
async def test_persistence_rejects_unbounded_batches() -> None:
    relation = _relation(canonical_key="rare_earth", provider_label="稀土永磁")
    with pytest.raises(V2ContractError, match="cannot exceed"):
        await persist_theme_relation_batch(
            object(),  # type: ignore[arg-type]
            [relation] * (MAX_THEME_GRAPH_BATCH_SIZE + 1),
        )
