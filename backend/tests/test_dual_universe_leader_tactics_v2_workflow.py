from __future__ import annotations

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2_FORMULA_REGISTRY_HASH,
    V2_SOURCE_REGISTRY,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
)
from app.services.workflows.dual_universe_leader_tactics_v2 import (
    AshareReadinessReport,
    V2CheckpointContract,
    evaluate_v2_materialization_readiness,
    validate_v2_checkpoint_contract,
)


def _contract(manifest_hash: str = "m" * 64) -> V2CheckpointContract:
    return V2CheckpointContract(
        manifest_hash=manifest_hash,
        source_registry_hash=V2_SOURCE_REGISTRY.registry_hash,
        formula_registry_hash=V2_FORMULA_REGISTRY_HASH,
        adjustment_version="total_return_adjusted",
        taxonomy_version="pit_theme_taxonomy_v2",
        cost_model=(("fee_bps_per_side", 5.0), ("slippage_bps_per_side", 5.0)),
        state_policy="preparing_confirmed_invalidated_v2",
    )


def test_checkpoint_resume_rejects_contract_substitution() -> None:
    validate_v2_checkpoint_contract(expected=_contract(), checkpoint=_contract())
    with pytest.raises(ValueError, match="incompatible"):
        validate_v2_checkpoint_contract(expected=_contract(), checkpoint=_contract("x" * 64))
    checkpoint = V2CollectorCheckpoint(
        cursor="510001", batch_size=10, completed_codes=("510001",), status="paused"
    )
    assert checkpoint.cursor == "510001"


def test_readiness_report_keeps_layer_denominators_and_thresholds() -> None:
    report = AshareReadinessReport(
        as_of=__import__("datetime").datetime(2026, 8, 4, 15),
        universe_count=100,
        adjusted_daily_count=95,
        pit_theme_count=90,
        history_counts=((61, 95), (300, 30)),
        provider_health=(("eastmoney", 1000),),
        raw_decision_violations=0,
        non_finite_violations=0,
        exclusions=(("missing_pit_theme_membership", 10),),
    )
    payload = report.to_dict()
    assert payload["adjusted_daily"]["coverage"] == 0.95
    assert payload["history_tiers"]["300"]["coverage"] == 0.30
    assert payload["history_tiers"]["300"]["unavailable_reason"] == "insufficient_history"


def test_materialization_readiness_requires_formula_history_not_300_day_diagnostic() -> None:
    report = AshareReadinessReport(
        as_of=__import__("datetime").datetime(2026, 8, 4, 15),
        universe_count=100,
        adjusted_daily_count=95,
        pit_theme_count=95,
        history_counts=((61, 100), (120, 95), (180, 95), (300, 10)),
        provider_health=(("eastmoney", 18_000),),
        raw_decision_violations=0,
        non_finite_violations=0,
        exclusions=(("none", 100),),
    )

    decision = evaluate_v2_materialization_readiness(report)

    assert decision.ready is True
    assert decision.reasons == ()
    assert decision.to_dict()["research_only"] is True


def test_materialization_readiness_fails_closed_with_stable_reasons() -> None:
    report = AshareReadinessReport(
        as_of=__import__("datetime").datetime(2026, 8, 4, 15),
        universe_count=100,
        adjusted_daily_count=89,
        pit_theme_count=89,
        history_counts=((120, 90), (180, 89)),
        provider_health=(("sina", 1),),
        raw_decision_violations=1,
        non_finite_violations=2,
        exclusions=(),
    )

    decision = evaluate_v2_materialization_readiness(report)

    assert decision.ready is False
    assert decision.reasons == (
        "insufficient_adjusted_daily_coverage",
        "insufficient_pit_theme_coverage",
        "insufficient_history_180",
        "unsupported_decision_provider",
        "raw_decision_price_violation",
        "non_finite_adjusted_input",
    )


def test_materialization_readiness_accepts_tickflow_at_approved_ashare_threshold() -> None:
    report = AshareReadinessReport(
        as_of=__import__("datetime").datetime(2026, 8, 7, 20),
        universe_count=100,
        adjusted_daily_count=90,
        pit_theme_count=90,
        history_counts=((120, 90), (180, 90)),
        provider_health=(("tickflow", 16_200),),
        raw_decision_violations=0,
        non_finite_violations=0,
        exclusions=(),
    )

    decision = evaluate_v2_materialization_readiness(report)

    assert report.coverage_threshold == 0.90
    assert decision.ready is True


@pytest.mark.asyncio
async def test_readiness_is_limited_to_as_of_authoritative_pool(tmp_path) -> None:
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.services.workflows.dual_universe_leader_tactics_v2 import read_ashare_readiness

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'readiness.db'}")
    as_of = __import__("datetime").datetime(2026, 8, 4, 15)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_research_universe_snapshots (
                    id INTEGER PRIMARY KEY,
                    snapshot_date DATE NOT NULL,
                    asset_code TEXT NOT NULL,
                    asset_name TEXT NOT NULL,
                    listing_state TEXT NOT NULL,
                    effective_at DATETIME NOT NULL,
                    received_at DATETIME NOT NULL,
                    provider TEXT NOT NULL,
                    source_cutoff DATETIME NOT NULL,
                    exclusion_reason TEXT,
                    fact_hash TEXT NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_adjusted_price_facts (
                    id INTEGER PRIMARY KEY,
                    asset_code TEXT NOT NULL,
                    trade_date DATE NOT NULL,
                    adjusted_open FLOAT,
                    adjusted_high FLOAT,
                    adjusted_low FLOAT,
                    adjusted_close FLOAT,
                    volume FLOAT,
                    amount FLOAT,
                    turnover FLOAT,
                    price_basis TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    adjustment_version TEXT,
                    received_at DATETIME,
                    historical_research_only BOOLEAN NOT NULL,
                    decision_eligible BOOLEAN NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_theme_membership_facts (
                    id INTEGER PRIMARY KEY,
                    asset_code TEXT NOT NULL,
                    effective_from DATE NOT NULL,
                    effective_to DATE,
                    received_at DATETIME NOT NULL
                )
                """
            )
        )
        universe_rows = [
            ("A", "listed", "none", "fact-a"),
            ("B", "listed", "none", "fact-b"),
            ("C", "listed", "excluded_by_policy", "fact-c"),
            ("D", "listed", "none", "fact-d-future"),
            ("E", "listed", "none", "fact-e-illegal-ohlc"),
        ]
        for index, (code, state, exclusion, fact_hash) in enumerate(universe_rows, 1):
            snapshot_date = "2026-08-05" if code == "D" else "2026-08-01"
            received_at = "2026-08-05 10:00:00" if code == "D" else "2026-08-02 10:00:00"
            await connection.execute(
                text(
                    """
                    INSERT INTO ashare_research_universe_snapshots
                        (id, snapshot_date, asset_code, asset_name, listing_state,
                         effective_at, received_at, provider, source_cutoff,
                         exclusion_reason, fact_hash)
                    VALUES (:id, :snapshot_date, :asset_code, :asset_name, :listing_state,
                            :effective_at, :received_at, 'eastmoney', :source_cutoff,
                            :exclusion_reason, :fact_hash)
                    """
                ),
                {
                    "id": index,
                    "snapshot_date": snapshot_date,
                    "asset_code": code,
                    "asset_name": code,
                    "listing_state": state,
                    "effective_at": received_at,
                    "received_at": received_at,
                    "source_cutoff": received_at,
                    "exclusion_reason": None if exclusion == "none" else exclusion,
                    "fact_hash": fact_hash,
                },
            )
        price_rows = [
            ("A", "2026-08-01", "eastmoney", 10.0, 10.5, 9.8, 10.2, 0.0, 0.0, 0.0, "a-1"),
            ("A", "2026-07-31", "eastmoney", 9.8, 10.2, 9.5, 10.0, 1.0, 1.0, 1.0, "a-2"),
            ("B", "2026-08-01", "sina", 10.0, 10.5, 9.8, 10.2, 1.0, 1.0, 1.0, "b-raw"),
            ("B", "2026-07-31", "eastmoney", -1.0, 10.2, 9.5, 10.0, 1.0, 1.0, 1.0, "b-invalid"),
            ("D", "2026-08-01", "eastmoney", 10.0, 10.5, 9.8, 10.2, 1.0, 1.0, 1.0, "d-future"),
            ("E", "2026-08-01", "eastmoney", 10.0, 1.0, 9.8, 10.0, 1.0, 1.0, 1.0, "e-illegal-ohlc"),
        ]
        for index, row in enumerate(price_rows, 1):
            (
                code,
                trade_date,
                provider,
                opn,
                high,
                low,
                close,
                volume,
                amount,
                turnover,
                revision_id,
            ) = row
            await connection.execute(
                text(
                    """
                    INSERT INTO ashare_adjusted_price_facts
                        (id, asset_code, trade_date, adjusted_open, adjusted_high,
                         adjusted_low, adjusted_close, volume, amount, turnover,
                         price_basis, provider, adjustment_version, received_at,
                         historical_research_only, decision_eligible)
                    VALUES (:id, :asset_code, :trade_date, :adjusted_open, :adjusted_high,
                            :adjusted_low, :adjusted_close, :volume, :amount, :turnover,
                            'total_return_adjusted', :provider, :adjustment_version,
                            :received_at, 0, 1)
                    """
                ),
                {
                    "id": index,
                    "asset_code": code,
                    "trade_date": trade_date,
                    "adjusted_open": opn,
                    "adjusted_high": high,
                    "adjusted_low": low,
                    "adjusted_close": close,
                    "volume": volume,
                    "amount": amount,
                    "turnover": turnover,
                    "provider": provider,
                    "adjustment_version": "v2",
                    "received_at": "2026-08-02 12:00:00",
                },
            )
        await connection.execute(
            text(
                """
                INSERT INTO ashare_theme_membership_facts
                    (id, asset_code, effective_from, effective_to, received_at)
                VALUES (1, 'A', '2026-01-01', NULL, '2026-08-02 12:00:00'),
                       (2, 'D', '2026-01-01', NULL, '2026-08-05 12:00:00')
                """
            )
        )

    statement_count = 0

    def count_statement(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        report = await read_ashare_readiness(
            session,
            as_of=as_of,
            required_history_tiers=(2,),
            required_trade_date=__import__("datetime").date(2026, 8, 1),
        )
    event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    await engine.dispose()

    assert statement_count == 6
    payload = report.to_dict()
    assert report.universe_count == 3
    assert report.adjusted_daily_count == 1
    assert report.pit_theme_count == 1
    assert report.history_counts == ((2, 1),)
    assert report.raw_decision_violations == 1
    assert report.non_finite_violations == 2
    assert payload["adjusted_daily"]["coverage"] == 1 / 3
    assert payload["history_tiers"]["2"]["coverage"] == 1 / 3
    assert all(
        layer.get("coverage", 0.0) <= 1.0
        for key, layer in payload.items()
        if isinstance(layer, dict) and "coverage" in layer
    )


@pytest.mark.asyncio
async def test_capture_resume_validates_full_checkpoint_contract_at_entry(tmp_path) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.services.workflows.dual_universe_leader_tactics_v2 import run_v2_capture_batch

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'capture.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_checkpoints (
                    id INTEGER PRIMARY KEY,
                    manifest_hash TEXT NOT NULL UNIQUE,
                    cursor TEXT,
                    batch_size INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL,
                    completed_hashes_json TEXT NOT NULL,
                    failed_codes_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at DATETIME,
                    error_summary TEXT,
                    updated_at DATETIME NOT NULL,
                    storage_version INTEGER NOT NULL DEFAULT 1
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_checkpoint_items (
                    manifest_hash TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    item_state TEXT NOT NULL,
                    content_hash TEXT,
                    error_message TEXT,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (manifest_hash, asset_code)
                )
                """
            )
        )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    contract = _contract()
    async with session_factory() as session:
        first = await run_v2_capture_batch(
            session,
            manifest_hash=contract.manifest_hash,
            codes=("510001",),
            checkpoint=V2CollectorCheckpoint(
                cursor=None,
                batch_size=5,
                completed_codes=(),
                status="paused",
            ),
            fetch_one=lambda _: __import__("asyncio").sleep(0),
            lease_owner="worker-a",
            expected_contract=contract,
            checkpoint_contract=contract,
        )
        assert first.checkpoint.manifest_hash == contract.manifest_hash
        with pytest.raises(ValueError, match="required"):
            await run_v2_capture_batch(
                session,
                manifest_hash=contract.manifest_hash,
                codes=("510001",),
                checkpoint=first.checkpoint,
                fetch_one=lambda _: __import__("asyncio").sleep(0),
                lease_owner="worker-a",
            )
    await engine.dispose()
