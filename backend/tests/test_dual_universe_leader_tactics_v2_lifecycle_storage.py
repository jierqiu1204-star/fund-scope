from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_lifecycle_storage import (
    V2LeaseLostError,
    acquire_v2_checkpoint_lease,
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
                    updated_at DATETIME NOT NULL
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
                    created_at DATETIME NOT NULL
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
