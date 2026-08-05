from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
    adjusted_fact_exclusion_reason,
    ashare_price_fact_to_bar,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
    adaptive_batch_size,
    ordered_page,
    run_bounded_batch,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "trade_date": date(2026, 8, 1),
        "adjusted_open": 10.0,
        "adjusted_high": 10.5,
        "adjusted_low": 9.8,
        "adjusted_close": 10.2,
        "volume": 1000.0,
        "amount": 100000.0,
        "turnover": 100000.0,
        "price_basis": "total_return_adjusted",
        "provider": "eastmoney",
        "adjustment_version": "eastmoney.ashare.hfq.v2",
        "revision_id": "rev-1",
        "received_at": datetime(2026, 8, 1, 15),
        "source_cutoff": datetime(2026, 8, 1, 15, 30),
        "decision_eligible": True,
        "historical_research_only": False,
    }
    row.update(overrides)
    return row


def test_ashare_adapter_accepts_only_cutoff_visible_adjusted_facts() -> None:
    bar, reason = ashare_price_fact_to_bar(_row())
    assert reason is None
    assert bar is not None
    assert bar.price_basis == "total_return_adjusted"
    assert (
        adjusted_fact_exclusion_reason(
            provider="sina",
            price_basis="total_return_adjusted",
            adjustment_version="sina.raw.v1",
            received_at=datetime(2026, 8, 1, 15),
            source_cutoff=datetime(2026, 8, 1, 15, 30),
            decision_eligible=True,
            values=(10.0, 10.5, 9.8, 10.2, 1000.0, 100000.0, 100000.0),
        )
        == "raw_or_audit_only_provider"
    )


def test_ashare_adapter_rejects_illegal_adjusted_ohlc() -> None:
    bar, reason = ashare_price_fact_to_bar(_row(adjusted_high=1.0, adjusted_close=10.0))
    assert bar is None
    assert reason == "invalid_adjusted_ohlcv"


def test_collector_page_is_stable_and_adaptive() -> None:
    assert ordered_page(["3", "1", "2"], cursor=None, batch_size=5) == ("1", "2", "3")
    assert ordered_page(["1", "2", "3"], cursor="1", batch_size=20) == ("2", "3")
    assert adaptive_batch_size(requested=20, elapsed_seconds=9.0) == 10
    assert adaptive_batch_size(requested=5, elapsed_seconds=1.0) == 10


def test_collector_persists_serial_progress_and_does_not_start_second_worker() -> None:
    completed: list[str] = []

    async def fetch_one(code: str) -> None:
        completed.append(code)

    checkpoint = V2CollectorCheckpoint(
        cursor=None,
        batch_size=5,
        completed_codes=(),
        status="paused",
    )
    result = asyncio.run(
        run_bounded_batch(
            ["1", "2", "3", "4", "5", "6"],
            checkpoint=checkpoint,
            fetch_one=fetch_one,
        )
    )
    assert completed == ["1", "2", "3", "4", "5"]
    assert result.checkpoint.completed_codes == tuple(completed)
    assert result.checkpoint.cursor == "5"
    assert result.checkpoint.status == "paused"


def test_collector_failure_does_not_starve_unseen_codes() -> None:
    fetched: list[str] = []

    async def fetch_one(code: str) -> None:
        fetched.append(code)

    result = asyncio.run(
        run_bounded_batch(
            ["1", "2", "3", "4", "5", "6"],
            checkpoint=V2CollectorCheckpoint(
                cursor=None,
                batch_size=5,
                completed_codes=(),
                status="paused",
                failed_codes=(("1", "provider unavailable"),),
            ),
            fetch_one=fetch_one,
        )
    )

    assert fetched == ["2", "3", "4", "5", "6"]
    assert result.checkpoint.completed_codes == tuple(fetched)
    assert result.checkpoint.failed_codes == (("1", "provider unavailable"),)


@pytest.mark.asyncio
async def test_adjusted_history_ignores_invalid_revision_before_applying_limit(tmp_path) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
        read_ashare_adjusted_bars,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'adjusted.db'}")
    async with engine.begin() as connection:
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
                    revision_id TEXT NOT NULL,
                    received_at DATETIME,
                    decision_eligible BOOLEAN NOT NULL,
                    historical_research_only BOOLEAN NOT NULL
                )
                """
            )
        )
        rows = [
            _row(
                trade_date=date(2026, 8, 1),
                revision_id="rev-old",
                received_at=datetime(2026, 8, 1, 10),
            ),
            _row(
                trade_date=date(2026, 8, 1),
                revision_id="rev-invalid-latest",
                received_at=datetime(2026, 8, 1, 11),
                adjusted_close=-1.0,
            ),
            _row(
                trade_date=date(2026, 7, 31),
                revision_id="rev-zero-metrics",
                received_at=datetime(2026, 7, 31, 11),
                volume=0.0,
                amount=0.0,
                turnover=0.0,
            ),
        ]
        for index, row in enumerate(rows, 1):
            await connection.execute(
                text(
                    """
                    INSERT INTO ashare_adjusted_price_facts
                        (id, asset_code, trade_date, adjusted_open, adjusted_high,
                         adjusted_low, adjusted_close, volume, amount, turnover,
                         price_basis, provider, adjustment_version, revision_id,
                         received_at, decision_eligible, historical_research_only)
                    VALUES (:id, '510001', :trade_date, :adjusted_open, :adjusted_high,
                            :adjusted_low, :adjusted_close, :volume, :amount, :turnover,
                            :price_basis, :provider, :adjustment_version, :revision_id,
                            :received_at, :decision_eligible, :historical_research_only)
                    """
                ),
                {"id": index, **row},
            )

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        bars, exclusions = await read_ashare_adjusted_bars(
            session,
            asset_code="510001",
            signal_date=date(2026, 8, 1),
            source_cutoff=datetime(2026, 8, 1, 15),
            limit=2,
        )
    await engine.dispose()

    assert [bar.revision_id for bar in bars] == ["rev-zero-metrics", "rev-old"]
    assert exclusions == ()


def test_membership_adapter_normalizes_sqlite_text_dates() -> None:
    from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
        ashare_membership_to_v2,
    )

    membership = ashare_membership_to_v2(
        {
            "group_id": "ai",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
            "received_at": "2026-08-02 12:00:00",
            "mapping_kind": "pit",
            "taxonomy_version": "v2",
            "fact_hash": "h",
        }
    )
    assert membership.effective_from == date(2026, 1, 1)
    assert membership.effective_to == date(2026, 12, 31)
    assert membership.observed_at == datetime(2026, 8, 2, 12)


@pytest.mark.asyncio
async def test_batch_asset_reader_is_ordered_and_bounded(tmp_path) -> None:
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
        read_ashare_asset_input,
        read_ashare_asset_inputs,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'batch-input.db'}")
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
                CREATE TABLE ashare_theme_membership_facts (
                    id INTEGER PRIMARY KEY,
                    asset_code TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    theme TEXT,
                    sector TEXT,
                    effective_from DATE NOT NULL,
                    effective_to DATE,
                    received_at DATETIME NOT NULL,
                    taxonomy_version TEXT NOT NULL,
                    mapping_kind TEXT NOT NULL,
                    tracked_index TEXT,
                    clone_group TEXT,
                    issuer TEXT,
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
                    revision_id TEXT NOT NULL,
                    received_at DATETIME,
                    decision_eligible BOOLEAN NOT NULL,
                    historical_research_only BOOLEAN NOT NULL
                )
                """
            )
        )
        for index, code in enumerate(("C", "A", "B"), 1):
            await connection.execute(
                text(
                    """
                    INSERT INTO ashare_research_universe_snapshots
                        (id, snapshot_date, asset_code, asset_name, listing_state,
                         effective_at, received_at, provider, source_cutoff,
                         exclusion_reason, fact_hash)
                    VALUES (:id, '2026-08-03', :asset_code, :asset_name, 'listed',
                            '2026-08-01 10:00:00', '2026-08-03 10:00:00', 'eastmoney',
                            '2026-08-03 10:00:00', NULL, :fact_hash)
                    """
                ),
                {
                    "id": index,
                    "asset_code": code,
                    "asset_name": f"Name-{code}",
                    "fact_hash": f"universe-{code}",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ashare_theme_membership_facts
                        (id, asset_code, group_id, theme, sector, effective_from,
                         effective_to, received_at, taxonomy_version, mapping_kind,
                         tracked_index, clone_group, issuer, fact_hash)
                    VALUES (:id, :asset_code, 'ai', 'AI', 'tech', '2026-01-01',
                            NULL, '2026-08-03 10:00:00', 'v2', 'historical_pit',
                            NULL, NULL, NULL, :fact_hash)
                    """
                ),
                {
                    "id": index,
                    "asset_code": code,
                    "fact_hash": f"theme-{code}",
                },
            )
            for offset, trade_date in enumerate(("2026-08-01", "2026-08-02"), 1):
                await connection.execute(
                    text(
                        """
                        INSERT INTO ashare_adjusted_price_facts
                            (id, asset_code, trade_date, adjusted_open, adjusted_high,
                             adjusted_low, adjusted_close, volume, amount, turnover,
                             price_basis, provider, adjustment_version, revision_id,
                             received_at, decision_eligible, historical_research_only)
                        VALUES (:id, :asset_code, :trade_date, 10, 11, 9, 10.5,
                                100, 1000, 0.1, 'total_return_adjusted', 'eastmoney',
                                'v2', :revision_id, '2026-08-03 10:00:00', 1, 0)
                        """
                    ),
                    {
                        "id": index * 10 + offset,
                        "asset_code": code,
                        "trade_date": trade_date,
                        "revision_id": f"{code}-{offset}",
                    },
                )

    statement_count = 0

    def count_statement(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    source_cutoff = datetime(2026, 8, 3, 15)
    async with session_factory() as session:
        batched = await read_ashare_asset_inputs(
            session,
            assets=(("C", "fallback-c"), ("A", "fallback-a"), ("B", "fallback-b")),
            signal_date=date(2026, 8, 2),
            source_cutoff=source_cutoff,
            history_limit=2,
            page_size=2,
        )
    event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    assert [item.asset_code for item in batched] == ["C", "A", "B"]
    assert [item.asset_name for item in batched] == ["Name-C", "Name-A", "Name-B"]
    assert all(len(item.bars) == 2 for item in batched)
    assert all(item.bars[0].trade_date == date(2026, 8, 1) for item in batched)
    assert all(item.membership is not None for item in batched)
    assert statement_count == 6

    async with session_factory() as session:
        scalar = await read_ashare_asset_input(
            session,
            asset_code="A",
            asset_name="fallback-a",
            signal_date=date(2026, 8, 2),
            source_cutoff=source_cutoff,
            history_limit=2,
        )
    await engine.dispose()

    batch_a = next(item for item in batched if item.asset_code == "A")
    assert scalar == batch_a
