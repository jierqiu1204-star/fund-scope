from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    build_v2_manifest,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_summary import (
    read_v2_summary,
)
from tests.test_dual_universe_leader_tactics_v2_storage import _seed_read_db


@pytest.mark.asyncio
async def test_summary_reads_materialized_layers_and_sqlite_text_dates(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'summary.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_run_manifests (
                    id INTEGER PRIMARY KEY,
                    manifest_hash TEXT NOT NULL,
                    universe TEXT NOT NULL,
                    decision_cutoff DATETIME NOT NULL,
                    data_receipt_cutoff DATETIME NOT NULL,
                    input_hash TEXT NOT NULL,
                    source_registry_hash TEXT NOT NULL,
                    formula_registry_hash TEXT NOT NULL,
                    code_version TEXT NOT NULL,
                    holdout_identity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    research_only BOOLEAN NOT NULL,
                    provider_health_json TEXT NOT NULL,
                    exclusions_json TEXT NOT NULL,
                    manifest_payload_json TEXT NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_candidate_observations (
                    id INTEGER PRIMARY KEY,
                    manifest_hash TEXT NOT NULL,
                    formula_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    availability TEXT NOT NULL,
                    qualifies BOOLEAN NOT NULL,
                    exclusion_reasons_json TEXT NOT NULL
                )
                """
            )
        )
        now = datetime(2026, 8, 4, 15)
        manifest = build_v2_manifest(
            universe="etf",
            decision_cutoff=now,
            data_receipt_cutoff=now,
            input_hash="a" * 64,
            exclusions=(("missing_pit_theme_membership", 1),),
            provider_health=(("eastmoney", "healthy"),),
        )
        await connection.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_run_manifests
                    (id, manifest_hash, universe, decision_cutoff,
                     data_receipt_cutoff, input_hash, source_registry_hash,
                     formula_registry_hash, code_version, holdout_identity,
                     status, research_only, provider_health_json, exclusions_json,
                     manifest_payload_json, created_at)
                VALUES (1, :manifest, 'etf', :cutoff, :receipt_cutoff, :input_hash,
                        :source_hash, :formula_hash, :code_version,
                        :holdout_identity, 'materialized', :research_only,
                        :provider_health, :exclusions, :manifest_payload,
                        :created_at)
                """
            ),
            {
                "manifest": manifest.manifest_hash,
                "cutoff": manifest.decision_cutoff,
                "receipt_cutoff": manifest.data_receipt_cutoff,
                "input_hash": manifest.input_hash,
                "source_hash": manifest.source_registry_hash,
                "formula_hash": manifest.formula_registry_hash,
                "code_version": manifest.code_version,
                "holdout_identity": manifest.holdout_identity,
                "research_only": manifest.research_only,
                "provider_health": json.dumps(dict(manifest.provider_health)),
                "exclusions": json.dumps(dict(manifest.exclusions)),
                "manifest_payload": json.dumps(asdict(manifest), default=str),
                "created_at": now,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_candidate_observations
                    (id, manifest_hash, formula_id, state, availability,
                     qualifies, exclusion_reasons_json)
                VALUES (1, :manifest, 'leader_breakout_proxy_v2', 'preparing',
                        'available', 1, '[]'),
                       (2, :manifest, 'base_launch_proxy_v2', 'preparing',
                        'unavailable', 0, '[\"insufficient_adjusted_history\"]')
                """
            ),
            {"manifest": manifest.manifest_hash},
        )
    async with engine.begin() as connection:
        payload = await read_v2_summary(connection, universe="etf")
    await engine.dispose()

    assert payload["availability"] == "materialized_only"
    assert payload["candidate_counts"]["total"] == 2
    assert payload["candidate_counts"]["qualifying"] == 1
    assert payload["layer_coverage"]["available_observations"]["numerator"] == 1
    assert payload["manifest"]["decision_cutoff"].startswith("2026-08-04")
    assert payload["exclusion_counts"]["missing_pit_theme_membership"] == 1


@pytest.mark.asyncio
async def test_summary_returns_unavailable_for_corrupt_latest_manifest(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'corrupt-summary.db'}")
    manifest_ids = await _seed_read_db(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE leader_tactics_v2_run_manifests
                SET manifest_payload_json = :payload
                WHERE manifest_hash = :manifest_hash
                """
            ),
            {"manifest_hash": manifest_ids["new"], "payload": "{}"},
        )
        payload = await read_v2_summary(connection, universe="etf")
    await engine.dispose()

    assert payload["availability"] == "unavailable"
    assert payload["unavailable_reason"] == "leader_tactics_v2_not_materialized"
