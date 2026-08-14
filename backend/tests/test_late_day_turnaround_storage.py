from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.services.strategy_lab.late_day_turnaround_shadow import CONTRACT_HASH
from app.services.strategy_lab.late_day_turnaround_storage import (
    persist_run,
    read_latest_evidence,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


async def _create_storage_tables(session) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS late_day_turnaround_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manifest_hash VARCHAR(128) NOT NULL UNIQUE,
                universe VARCHAR(16) NOT NULL,
                signal_date DATE NOT NULL,
                decision_at DATETIME NOT NULL,
                status VARCHAR(32) NOT NULL,
                policy_mode VARCHAR(32) NOT NULL,
                strategy_version VARCHAR(64) NOT NULL,
                contract_hash VARCHAR(128) NOT NULL,
                universe_hash VARCHAR(128) NOT NULL,
                input_hash VARCHAR(128) NOT NULL,
                expected_count INTEGER NOT NULL,
                evaluated_count INTEGER NOT NULL,
                available_count INTEGER NOT NULL,
                qualifying_count INTEGER NOT NULL,
                provider_health_json JSON NOT NULL,
                exclusion_counts_json JSON NOT NULL,
                unavailable_reason VARCHAR(128),
                created_at DATETIME NOT NULL,
                UNIQUE(universe, decision_at, contract_hash)
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS late_day_turnaround_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                asset_code VARCHAR(32) NOT NULL,
                asset_name VARCHAR(255) NOT NULL,
                observation_kind VARCHAR(32) NOT NULL,
                available BOOLEAN NOT NULL,
                reason VARCHAR(128) NOT NULL,
                score FLOAT,
                ma5 FLOAT,
                gain_pct FLOAT,
                ma_deviation_pct FLOAT,
                amount_ratio FLOAT,
                signal_date DATE NOT NULL,
                decision_at DATETIME NOT NULL,
                input_hash VARCHAR(128) NOT NULL,
                provenance_json JSON NOT NULL,
                created_at DATETIME NOT NULL,
                UNIQUE(run_id, asset_code)
            )
            """
        )
    )


@pytest.mark.asyncio
async def test_persist_and_read_are_idempotent_at_one_cutoff(app) -> None:
    decision_at = datetime(2026, 8, 14, 14, 30, tzinfo=SHANGHAI)
    first_observation = {
        "asset_code": "510300",
        "asset_name": "沪深300ETF",
        "observation_kind": "formal_candidate",
        "available": True,
        "reason": "ok",
        "score": 12.3,
        "ma5": 4.2,
        "gain_pct": 1.1,
        "ma_deviation_pct": 0.4,
        "amount_ratio": 1.2,
        "provenance": {"source": "persisted_quote"},
    }
    async with app.state.db.session() as session:
        await _create_storage_tables(session)
        first = await persist_run(
            session,
            universe="etf",
            decision_at=decision_at,
            status="complete",
            universe_codes=["510300"],
            observations=[first_observation],
            expected_count=1,
            provider_health={"status": "ok"},
        )
        repeated = await persist_run(
            session,
            universe="etf",
            decision_at=decision_at,
            status="complete",
            universe_codes=["510300"],
            observations=[first_observation],
            expected_count=1,
            provider_health={"status": "ok"},
        )
        conflicting = await persist_run(
            session,
            universe="etf",
            decision_at=decision_at,
            status="unavailable",
            universe_codes=["510300"],
            observations=[],
            expected_count=1,
            provider_health={"status": "changed"},
            unavailable_reason="incomplete_declared_coverage",
        )
        assert first == repeated == conflicting
        assert (
            await session.scalar(text("SELECT COUNT(*) FROM late_day_turnaround_runs"))
        ) == 1
        assert (
            await session.scalar(
                text("SELECT COUNT(*) FROM late_day_turnaround_observations")
            )
        ) == 1
        assert CONTRACT_HASH != "incompatible-contract"
        await session.execute(
            text(
                """
                INSERT INTO late_day_turnaround_runs
                    (manifest_hash, universe, signal_date, decision_at, status,
                     policy_mode, strategy_version, contract_hash, universe_hash,
                     input_hash, expected_count, evaluated_count, available_count,
                     qualifying_count, provider_health_json, exclusion_counts_json,
                     unavailable_reason, created_at)
                VALUES
                    ('newer-incompatible', 'etf', '2026-08-14',
                     '2026-08-14 14:40:00', 'complete', 'shadow_only', 'old.v0',
                     'incompatible-contract', 'universe', 'input', 0, 0, 0, 0,
                     '{}', '{}', NULL, '2026-08-14 14:40:01')
                """
            )
        )

        payload = await read_latest_evidence(
            session,
            universe="etf",
            observation_kind="formal_candidate",
        )
        assert payload["manifest_hash"] == first
        assert payload["summary"]["coverage_ratio"] == 1.0
        assert payload["items"][0]["provenance_json"] == {
            "source": "persisted_quote"
        }
        assert payload["production_mutation_allowed"] is False
