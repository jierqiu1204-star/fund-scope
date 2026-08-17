from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.strategy_lab import dual_universe_leader_tactics_v2_lifecycle_storage
from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_lifecycle_storage import (
    V2LeaseLostError,
    acquire_v2_checkpoint_lease,
    load_v2_checkpoint,
    release_v2_checkpoint_lease,
    save_v2_checkpoint,
)


@pytest.mark.asyncio
async def test_checkpoint_lease_is_database_cas_and_save_cannot_clobber_owner(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
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
    manifest_hash = "m" * 64
    checkpoint = V2CollectorCheckpoint(
        cursor="510001",
        batch_size=5,
        completed_codes=("510001",),
        status="paused",
        manifest_hash=manifest_hash,
    )
    async with session_factory() as owner_session, session_factory() as other_session:
        owner_expiry = await acquire_v2_checkpoint_lease(
            owner_session,
            manifest_hash=manifest_hash,
            lease_owner="owner-a",
        )
        assert owner_expiry is not None
        assert (
            await acquire_v2_checkpoint_lease(
                other_session,
                manifest_hash=manifest_hash,
                lease_owner="owner-b",
            )
            is None
        )

        with pytest.raises(V2LeaseLostError):
            await save_v2_checkpoint(
                other_session,
                manifest_hash=manifest_hash,
                checkpoint=checkpoint,
                lease_owner="owner-b",
                lease_expires_at=datetime.utcnow(),
            )

        await save_v2_checkpoint(
            owner_session,
            manifest_hash=manifest_hash,
            checkpoint=checkpoint,
            lease_owner="owner-a",
            lease_expires_at=owner_expiry,
        )
        row = (
            (
                await owner_session.execute(
                    text(
                        "SELECT lease_owner, completed_count FROM leader_tactics_v2_checkpoints "
                        "WHERE manifest_hash = :manifest_hash"
                    ),
                    {"manifest_hash": manifest_hash},
                )
            )
            .mappings()
            .one()
        )
        assert row["lease_owner"] == "owner-a"
        assert row["completed_count"] == 1

        assert await release_v2_checkpoint_lease(
            owner_session,
            manifest_hash=manifest_hash,
            lease_owner="owner-a",
        )
        other_expiry = await acquire_v2_checkpoint_lease(
            other_session,
            manifest_hash=manifest_hash,
            lease_owner="owner-b",
        )
        assert other_expiry is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_incremental_checkpoint_writes_only_changed_items(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'incremental.db'}")
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
    item_batch_sizes: list[int] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record_item_writes(_conn, _cursor, statement, parameters, _context, executemany):
        if "INSERT INTO leader_tactics_v2_checkpoint_items" in statement:
            item_batch_sizes.append(len(parameters) if executemany else 1)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    manifest_hash = "i" * 64
    empty = V2CollectorCheckpoint(
        cursor=None,
        batch_size=5,
        completed_codes=(),
        status="paused",
        manifest_hash=manifest_hash,
    )
    first = V2CollectorCheckpoint(
        cursor="000005",
        batch_size=5,
        completed_codes=tuple(f"{index:06d}" for index in range(1, 6)),
        completed_hashes=tuple((f"{index:06d}", f"{index:064x}") for index in range(1, 6)),
        status="paused",
        manifest_hash=manifest_hash,
    )
    second = V2CollectorCheckpoint(
        cursor="000006",
        batch_size=5,
        completed_codes=(*first.completed_codes, "000006"),
        completed_hashes=(*first.completed_hashes, ("000006", f"{6:064x}")),
        status="paused",
        manifest_hash=manifest_hash,
    )
    async with session_factory() as session:
        expiry = await acquire_v2_checkpoint_lease(
            session,
            manifest_hash=manifest_hash,
            lease_owner="incremental-owner",
        )
        assert expiry is not None
        monkeypatch.setattr(
            dual_universe_leader_tactics_v2_lifecycle_storage,
            "_json",
            lambda _value: (_ for _ in ()).throw(
                AssertionError("version-2 saves must not serialize cumulative JSON")
            ),
        )
        await save_v2_checkpoint(
            session,
            manifest_hash=manifest_hash,
            checkpoint=first,
            previous_checkpoint=empty,
            lease_owner="incremental-owner",
            lease_expires_at=expiry,
        )
        await save_v2_checkpoint(
            session,
            manifest_hash=manifest_hash,
            checkpoint=second,
            previous_checkpoint=first,
            lease_owner="incremental-owner",
            lease_expires_at=expiry,
        )
        loaded = await load_v2_checkpoint(session, manifest_hash=manifest_hash)

    await engine.dispose()

    assert item_batch_sizes == [5, 1]
    assert loaded == second


@pytest.mark.asyncio
async def test_lifecycle_transitions_are_idempotent_and_caller_transaction_owned(tmp_path) -> None:
    from datetime import date

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
        V2LifecycleTransition,
    )
    from app.services.strategy_lab.dual_universe_leader_tactics_v2_lifecycle_storage import (
        persist_v2_lifecycle_transitions,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'transitions.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_state_transitions (
                    id INTEGER PRIMARY KEY,
                    manifest_hash TEXT NOT NULL,
                    universe TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    formula_id TEXT NOT NULL,
                    signal_date DATE NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    transition_date DATE NOT NULL,
                    payload_json TEXT NOT NULL,
                    transition_hash TEXT NOT NULL UNIQUE,
                    created_at DATETIME NOT NULL,
                    evidence_cutoff DATETIME,
                    projected_entry_status TEXT
                )
                """
            )
        )

    transitions = tuple(
        V2LifecycleTransition(
            universe="etf",
            asset_code=code,
            formula_id="leader_breakout_proxy_v2",
            signal_date=date(2026, 8, 1),
            from_state=None,
            to_state="preparing",
            transition_date=date(2026, 8, 1),
            signal_high=10.0,
            adjusted_close=10.0,
            adjusted_ma5=9.9,
            simulated_execution_date=None,
            execution_model="research_only",
            reason="test",
            transition_hash=f"transition-{code}",
        )
        for code in ("510001", "510002")
    )
    manifest_hash = "m" * 64
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # The helper must not commit: the caller can roll back the whole unit.
    async with session_factory() as session:
        assert (
            await persist_v2_lifecycle_transitions(
                session, manifest_hash=manifest_hash, transitions=transitions
            )
            == 2
        )
        assert (
            await persist_v2_lifecycle_transitions(
                session, manifest_hash=manifest_hash, transitions=transitions
            )
            == 0
        )
        count = await session.scalar(
            text("SELECT COUNT(*) FROM leader_tactics_v2_state_transitions")
        )
        assert count == 2
        await session.rollback()

    async with session_factory() as session:
        count = await session.scalar(
            text("SELECT COUNT(*) FROM leader_tactics_v2_state_transitions")
        )
        assert count == 0

        assert (
            await persist_v2_lifecycle_transitions(
                session, manifest_hash=manifest_hash, transitions=transitions
            )
            == 2
        )
        await session.commit()

        # Idempotency also holds across an explicit caller commit.
        assert (
            await persist_v2_lifecycle_transitions(
                session, manifest_hash=manifest_hash, transitions=transitions
            )
            == 0
        )
        await session.commit()

    async with session_factory() as session:
        count = await session.scalar(
            text("SELECT COUNT(*) FROM leader_tactics_v2_state_transitions")
        )
        assert count == 2

    await engine.dispose()
