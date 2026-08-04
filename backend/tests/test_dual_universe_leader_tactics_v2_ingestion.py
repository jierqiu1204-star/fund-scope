from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    PRICE_BASIS,
    V2ContractError,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_ingestion import (
    MAX_FACT_BATCH_SIZE,
    AshareAdjustedPriceFact,
    AshareThemeMembershipFact,
    AshareUniverseSnapshotFact,
    persist_ashare_adjusted_price_batch,
    persist_ashare_theme_membership_batch,
    persist_ashare_universe_snapshot_batch,
)

SOURCE_TIME = datetime(2026, 8, 4, 15, 0)


def _universe(code: str = "000001", *, provider: str = "akshare") -> AshareUniverseSnapshotFact:
    return AshareUniverseSnapshotFact(
        snapshot_date=date(2026, 8, 4),
        asset_code=code,
        asset_name="测试股票",
        listing_state="listed",
        board="main",
        effective_at=datetime(2026, 8, 4, 9),
        received_at=SOURCE_TIME,
        provider=provider,
        source_cutoff=SOURCE_TIME,
    )


def _theme(
    code: str = "000001", *, mapping_kind: str = "historical_pit"
) -> AshareThemeMembershipFact:
    return AshareThemeMembershipFact(
        asset_code=code,
        group_id="ai_application",
        theme="AI应用",
        sector="软件",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        received_at=SOURCE_TIME,
        taxonomy_version="taxonomy-v1",
        source="akshare",
        confidence="high",
        supersedes_fact_hash=None,
        mapping_kind=mapping_kind,
    )


def _price(
    code: str = "000001",
    *,
    provider: str = "eastmoney",
    price_basis: str = PRICE_BASIS,
    received_at: datetime | None = SOURCE_TIME,
    historical_research_only: bool = False,
    decision_eligible: bool = True,
    trade_date: date | None = None,
) -> AshareAdjustedPriceFact:
    effective_trade_date = trade_date or date(2026, 8, 4)
    return AshareAdjustedPriceFact(
        asset_code=code,
        trade_date=effective_trade_date,
        adjusted_open=10.0,
        adjusted_high=11.0,
        adjusted_low=9.0,
        adjusted_close=10.5,
        volume=0.0,
        amount=0.0,
        turnover=0.0,
        price_basis=price_basis,
        provider=provider,
        adjustment_version="total-return-v1",
        revision_id=f"rev-{code}-{effective_trade_date.isoformat()}",
        received_at=received_at,
        historical_research_only=historical_research_only,
        decision_eligible=decision_eligible,
    )


async def _create_tables(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_research_universe_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date DATE NOT NULL,
                    asset_code VARCHAR(32) NOT NULL,
                    asset_name VARCHAR(256) NOT NULL,
                    listing_state VARCHAR(32) NOT NULL,
                    board VARCHAR(32),
                    effective_at DATETIME NOT NULL,
                    received_at DATETIME NOT NULL,
                    provider VARCHAR(64) NOT NULL,
                    source_cutoff DATETIME NOT NULL,
                    exclusion_reason VARCHAR(256),
                    fact_hash VARCHAR(128) NOT NULL UNIQUE
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_theme_membership_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code VARCHAR(32) NOT NULL,
                    group_id VARCHAR(256) NOT NULL,
                    theme VARCHAR(256),
                    sector VARCHAR(256),
                    effective_from DATE NOT NULL,
                    effective_to DATE,
                    received_at DATETIME NOT NULL,
                    taxonomy_version VARCHAR(128) NOT NULL,
                    source VARCHAR(64) NOT NULL,
                    confidence VARCHAR(32) NOT NULL,
                    supersedes_fact_hash VARCHAR(128),
                    mapping_kind VARCHAR(32) NOT NULL,
                    tracked_index VARCHAR(64),
                    clone_group VARCHAR(128),
                    issuer VARCHAR(128),
                    fact_hash VARCHAR(128) NOT NULL UNIQUE
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_adjusted_price_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code VARCHAR(32) NOT NULL,
                    trade_date DATE NOT NULL,
                    adjusted_open FLOAT NOT NULL,
                    adjusted_high FLOAT NOT NULL,
                    adjusted_low FLOAT NOT NULL,
                    adjusted_close FLOAT NOT NULL,
                    volume FLOAT NOT NULL,
                    amount FLOAT NOT NULL,
                    turnover FLOAT NOT NULL,
                    price_basis VARCHAR(64) NOT NULL,
                    provider VARCHAR(64) NOT NULL,
                    adjustment_version VARCHAR(128) NOT NULL,
                    revision_id VARCHAR(128) NOT NULL,
                    received_at DATETIME,
                    historical_research_only BOOLEAN NOT NULL,
                    decision_eligible BOOLEAN NOT NULL,
                    fact_hash VARCHAR(128) NOT NULL UNIQUE,
                    UNIQUE (asset_code, trade_date, revision_id)
                )
                """
            )
        )


def test_fact_contracts_are_immutable_and_hashes_are_reproducible() -> None:
    first = _price()
    second = _price()
    assert first.fact_hash == second.fact_hash
    assert stable_contract_hash(first.canonical_payload()) == first.fact_hash
    with pytest.raises((AttributeError, TypeError)):
        first.adjusted_close = 12.0  # type: ignore[misc]
    with pytest.raises(V2ContractError, match="fact_hash"):
        AshareAdjustedPriceFact(**{**first_payload(first), "fact_hash": "0" * 64})


def first_payload(fact: AshareAdjustedPriceFact) -> dict[str, object]:
    return asdict(fact)


def test_price_governance_is_fail_closed() -> None:
    unknown_receipt = _price(received_at=None, decision_eligible=True)
    assert unknown_receipt.historical_research_only is True
    assert unknown_receipt.decision_eligible is False

    with pytest.raises(V2ContractError):
        _price(provider="sina", decision_eligible=True)
    with pytest.raises(V2ContractError):
        _price(provider="tencent", decision_eligible=True)
    with pytest.raises(V2ContractError):
        _price(price_basis="close", decision_eligible=True)
    with pytest.raises(V2ContractError):
        _price(historical_research_only=True, decision_eligible=True)
    with pytest.raises(V2ContractError, match="^received_at cannot precede trade_date$"):
        _price(
            trade_date=date(2026, 8, 4),
            received_at=datetime(2026, 8, 3, 23, 59, 59),
        )


def test_universe_and_membership_governance_is_fail_closed() -> None:
    assert _theme().mapping_kind == "historical_pit"
    assert _universe(provider="eastmoney").provider == "eastmoney"
    with pytest.raises(V2ContractError, match="mapping_kind must be historical_pit"):
        _theme(mapping_kind="theme")
    with pytest.raises(V2ContractError, match="authoritative universe"):
        _universe(provider="sina")
    with pytest.raises(V2ContractError, match="authoritative universe"):
        _universe(provider="unknown")


@pytest.mark.parametrize(
    "changes",
    [
        {"adjusted_high": 10.2},
        {"adjusted_low": 10.6},
        {"adjusted_close": float("nan")},
        {"volume": -1.0},
        {"amount": -1.0},
        {"turnover": -1.0},
    ],
)
def test_bad_price_facts_are_rejected(changes: dict[str, object]) -> None:
    values = first_payload(_price())
    values.update(changes)
    values.pop("fact_hash", None)
    with pytest.raises(V2ContractError):
        AshareAdjustedPriceFact(**values)


@pytest.mark.asyncio
async def test_batch_limit_is_explicit() -> None:
    assert MAX_FACT_BATCH_SIZE == 500
    with pytest.raises(V2ContractError, match=str(MAX_FACT_BATCH_SIZE)):
        persist_batch = tuple(
            _price(str(index).zfill(6)) for index in range(MAX_FACT_BATCH_SIZE + 1)
        )
        # This is the fact-row bound, not the collector's 5-20-security page bound.
        await persist_ashare_adjusted_price_batch(None, persist_batch)  # type: ignore[arg-type]


class _RecordingSession:
    def __init__(self) -> None:
        self.parameter_batches: list[list[dict[str, object]]] = []

    async def execute(self, _statement: object, parameters: object) -> SimpleNamespace:
        assert isinstance(parameters, list)
        self.parameter_batches.append(parameters)
        return SimpleNamespace(rowcount=len(parameters))


@pytest.mark.asyncio
async def test_one_security_300_day_history_uses_one_executemany() -> None:
    session = _RecordingSession()
    facts = tuple(
        _price("000001", trade_date=date(2026, 8, 4) - timedelta(days=offset))
        for offset in range(300)
    )

    inserted = await persist_ashare_adjusted_price_batch(session, facts)  # type: ignore[arg-type]

    assert inserted == 300
    assert len(session.parameter_batches) == 1
    assert len(session.parameter_batches[0]) == 300


@pytest.mark.asyncio
async def test_batch_writers_are_idempotent_and_do_not_commit(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingestion.db'}")
    await _create_tables(engine)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    universe = (_universe("000001"), _universe("000002"))
    themes = (_theme("000001"), _theme("000002"))
    prices = (_price("000001"), _price("000002"))

    async with session_factory() as session:
        assert await persist_ashare_universe_snapshot_batch(session, universe) == 2
        assert await persist_ashare_universe_snapshot_batch(session, universe) == 0
        assert await persist_ashare_theme_membership_batch(session, themes) == 2
        assert await persist_ashare_theme_membership_batch(session, themes) == 0
        assert await persist_ashare_adjusted_price_batch(session, prices) == 2
        assert await persist_ashare_adjusted_price_batch(session, prices) == 0
        await session.rollback()

    async with session_factory() as session:
        counts = await session.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM ashare_research_universe_snapshots),
                    (SELECT COUNT(*) FROM ashare_theme_membership_facts),
                    (SELECT COUNT(*) FROM ashare_adjusted_price_facts)
                """
            )
        )
        assert tuple(counts.one()) == (0, 0, 0)

    async with session_factory() as session:
        assert await persist_ashare_universe_snapshot_batch(session, universe) == 2
        assert await persist_ashare_theme_membership_batch(session, themes) == 2
        assert await persist_ashare_adjusted_price_batch(session, prices) == 2
        await session.commit()

    async with session_factory() as session:
        counts = await session.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM ashare_research_universe_snapshots),
                    (SELECT COUNT(*) FROM ashare_theme_membership_facts),
                    (SELECT COUNT(*) FROM ashare_adjusted_price_facts)
                """
            )
        )
        assert tuple(counts.one()) == (2, 2, 2)
    await engine.dispose()


@pytest.mark.asyncio
async def test_universe_writer_keeps_same_day_revisions_and_deduplicates_fact_hash(
    tmp_path,
) -> None:
    from dataclasses import replace

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'universe-revisions.db'}")
    await _create_tables(engine)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    first = _universe("000001")
    revision = replace(first, asset_name="修订后的测试股票", fact_hash="")

    async with session_factory() as session:
        assert await persist_ashare_universe_snapshot_batch(session, (first,)) == 1
        assert await persist_ashare_universe_snapshot_batch(session, (first,)) == 0
        assert await persist_ashare_universe_snapshot_batch(session, (revision,)) == 1
        count = await session.scalar(
            text(
                "SELECT COUNT(*) FROM ashare_research_universe_snapshots "
                "WHERE snapshot_date = :snapshot_date AND asset_code = :asset_code"
            ),
            {"snapshot_date": first.snapshot_date, "asset_code": first.asset_code},
        )
        assert count == 2
        await session.commit()
    await engine.dispose()
