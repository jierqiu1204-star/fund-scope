from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab import dual_universe_leader_tactics_v2_staging as staging
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
    screen_dual_universe,
)


def _membership() -> V2PITMembership:
    draft = V2PITMembership(
        group_id="theme-a",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        observed_at=datetime(2026, 8, 12, 15),
        mapping_kind="historical_pit",
        taxonomy_version="theme-v1",
        theme="theme-a",
    )
    return replace(draft, fact_hash=stable_contract_hash(draft.canonical_payload()))


def _input(code: str, name: str) -> V2AssetInput:
    signal_date = date(2026, 8, 12)
    start = signal_date - timedelta(days=179)
    bars = tuple(
        V2AdjustedBar(
            trade_date=start + timedelta(days=index),
            adjusted_open=10 + index * 0.01,
            adjusted_high=10.2 + index * 0.01,
            adjusted_low=9.8 + index * 0.01,
            adjusted_close=10.1 + index * 0.01,
            volume=1_000 + index,
            amount=100_000 + index,
            turnover=0.1,
            observed_at=datetime.combine(start + timedelta(days=index), time(15)),
            provider="eastmoney",
            adjustment_version="total-return-v1",
            revision_id=f"{code}-{index}",
        )
        for index in range(180)
    )
    return V2AssetInput(
        universe="ashare",
        asset_code=code,
        asset_name=name,
        signal_date=signal_date,
        source_cutoff=datetime(2026, 8, 12, 15, 30),
        bars=bars,
        membership=_membership(),
    )


@pytest.mark.asyncio
async def test_two_stage_materialization_is_bounded_resumable_and_idempotent(
    tmp_path, monkeypatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'staging.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_materialization_runs (
                    run_hash TEXT PRIMARY KEY, universe TEXT, signal_date DATE,
                    source_cutoff DATETIME, decision_date DATE, universe_hash TEXT,
                    expected_count INTEGER, status TEXT, code_version TEXT,
                    source_registry_hash TEXT, formula_registry_hash TEXT,
                    provider_health_json TEXT, lease_owner TEXT,
                    lease_expires_at DATETIME, created_at DATETIME, updated_at DATETIME
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_materialization_features (
                    run_hash TEXT, asset_code TEXT, asset_name TEXT, group_key TEXT,
                    feature_json TEXT, feature_hash TEXT, input_digest TEXT,
                    created_at DATETIME,
                    PRIMARY KEY (run_hash, asset_code)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_materialization_groups (
                    run_hash TEXT, group_key TEXT, observation_json TEXT,
                    content_hash TEXT, created_at DATETIME,
                    PRIMARY KEY (run_hash, group_key)
                )
                """
            )
        )

    page_sizes: list[int] = []

    async def fake_read(_session, *, assets, **_kwargs):
        page_sizes.append(len(assets))
        return tuple(_input(code, name) for code, name in assets)

    monkeypatch.setattr(staging, "read_ashare_asset_inputs", fake_read)
    assets = tuple((f"{index:06d}", f"asset-{index}") for index in range(45))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    kwargs = {
        "assets": assets,
        "signal_date": date(2026, 8, 12),
        "source_cutoff": datetime(2026, 8, 12, 15, 30),
        "decision_date": date(2026, 8, 12),
        "decision_mode": "session_pit",
        "next_eligible_date": None,
        "code_version": "test-v3",
        "provider_health": (("eastmoney", "healthy"),),
    }
    async with session_factory() as session:
        waiting, waiting_progress = await staging.advance_ashare_materialization(
            session,
            **kwargs,
            batch_size=5,
            memory_reader=lambda: 1,
        )
        await session.execute(
            text(
                "UPDATE leader_tactics_v2_materialization_runs "
                "SET lease_owner = 'other-worker', lease_expires_at = :expires "
                "WHERE run_hash = :run_hash"
            ),
            {
                "expires": datetime(2099, 1, 1),
                "run_hash": waiting_progress["run_hash"],
            },
        )
        await session.commit()
        blocked, blocked_progress = await staging.advance_ashare_materialization(
            session,
            **kwargs,
            memory_reader=lambda: 2**30,
        )
        await session.execute(
            text(
                "UPDATE leader_tactics_v2_materialization_runs "
                "SET lease_owner = NULL, lease_expires_at = NULL "
                "WHERE run_hash = :run_hash"
            ),
            {"run_hash": waiting_progress["run_hash"]},
        )
        await session.commit()
        first, first_progress = await staging.advance_ashare_materialization(
            session,
            **{**kwargs, "source_cutoff": datetime(2026, 8, 12, 15, 32)},
            memory_reader=lambda: 2**30,
        )
        second, second_progress = await staging.advance_ashare_materialization(
            session,
            **{**kwargs, "source_cutoff": datetime(2026, 8, 12, 15, 34)},
            memory_reader=lambda: 2**30,
        )
        await session.execute(
            text(
                "UPDATE leader_tactics_v2_materialization_features "
                "SET feature_json = '{}' WHERE asset_code = '000000'"
            )
        )
        await session.commit()
        corrupt, corrupt_progress = await staging.advance_ashare_materialization(
            session,
            **kwargs,
            memory_reader=lambda: 2**30,
        )
        repaired, repaired_progress = await staging.advance_ashare_materialization(
            session,
            **kwargs,
            memory_reader=lambda: 2**30,
        )
        feature_count = await session.scalar(
            text("SELECT COUNT(*) FROM leader_tactics_v2_materialization_features")
        )
        group_count = await session.scalar(
            text("SELECT COUNT(*) FROM leader_tactics_v2_materialization_groups")
        )
    await engine.dispose()

    assert waiting is None
    assert waiting_progress["unavailable_reason"] == (
        "insufficient_materialization_memory_headroom"
    )
    assert blocked is None
    assert blocked_progress["unavailable_reason"] == "materialization_lease_busy"
    assert first is not None and second is not None
    assert corrupt is None
    assert corrupt_progress["unavailable_reason"] == ("staged_feature_hash_mismatch_requeued")
    assert repaired is not None
    assert repaired_progress["materialization_stage"] == "complete"
    direct = screen_dual_universe(
        tuple(_input(code, name) for code, name in assets),
        code_version="test-v3",
        provider_health=(("eastmoney", "healthy"),),
    )
    assert first.observations == direct.observations
    assert first.manifest_hash == second.manifest_hash
    assert first_progress["materialization_stage"] == "complete"
    assert second_progress["materialization_stage"] == "complete"
    assert int(feature_count or 0) == 45
    assert int(group_count or 0) == 1
    assert page_sizes[:9] == [5] * 9
    assert max(page_sizes[:9]) <= staging.STAGE_BATCH_MAX
