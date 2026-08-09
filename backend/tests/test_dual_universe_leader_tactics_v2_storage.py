from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2_FORMULA_REGISTRY_HASH,
    V2_SOURCE_REGISTRY,
    V2CandidateObservation,
    V2ScreenResult,
    build_v2_manifest,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    get_v2_materialized_manifest,
    persist_v2_screen_result,
    read_v2_candidates,
)


def _manifest_ddl() -> str:
    return """
    CREATE TABLE leader_tactics_v2_run_manifests (
        id INTEGER PRIMARY KEY,
        manifest_hash TEXT NOT NULL UNIQUE,
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


def _observation_ddl() -> str:
    return """
    CREATE TABLE leader_tactics_v2_candidate_observations (
        id INTEGER PRIMARY KEY,
        manifest_hash TEXT NOT NULL,
        universe TEXT NOT NULL,
        asset_code TEXT NOT NULL,
        asset_name TEXT NOT NULL,
        theme TEXT,
        sector TEXT,
        tracked_index TEXT,
        formula_id TEXT NOT NULL,
        state TEXT NOT NULL,
        availability TEXT NOT NULL,
        qualifies BOOLEAN NOT NULL,
        score FLOAT,
        signal_date DATE NOT NULL,
        source_cutoff DATETIME NOT NULL,
        gate_facts_json TEXT NOT NULL,
        exclusion_reasons_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        feature_hash TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE (manifest_hash, universe, asset_code, formula_id, signal_date)
    )
    """


def _transition_ddl() -> str:
    return """
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


async def _seed_read_db(engine) -> dict[str, str]:
    async with engine.begin() as connection:
        await connection.execute(text(_manifest_ddl()))
        await connection.execute(text(_observation_ddl()))
        await connection.execute(text(_transition_ddl()))
        old_manifest = build_v2_manifest(
            universe="etf",
            decision_cutoff=datetime(2026, 8, 1, 15),
            data_receipt_cutoff=datetime(2026, 8, 1, 15, 1),
            input_hash="1" * 64,
            exclusions=(("insufficient_adjusted_history", 1),),
        )
        new_manifest = build_v2_manifest(
            universe="etf",
            decision_cutoff=datetime(2026, 8, 4, 15),
            data_receipt_cutoff=datetime(2026, 8, 4, 15, 1),
            input_hash="2" * 64,
            exclusions=(("insufficient_adjusted_history", 1),),
        )
        future_receipt_manifest = build_v2_manifest(
            universe="etf",
            decision_cutoff=datetime(2026, 8, 2, 15),
            data_receipt_cutoff=datetime(2026, 8, 3, 15),
            input_hash="3" * 64,
        )
        draft_manifest = build_v2_manifest(
            universe="etf",
            decision_cutoff=datetime(2026, 8, 3, 15),
            data_receipt_cutoff=datetime(2026, 8, 3, 15, 1),
            input_hash="4" * 64,
        )
        manifests = [
            (1, old_manifest, "materialized", datetime(2026, 8, 1, 15, 1)),
            (2, new_manifest, "materialized", datetime(2026, 8, 4, 15, 1)),
            (3, draft_manifest, "draft", datetime(2026, 8, 3, 15, 1)),
            (
                4,
                future_receipt_manifest,
                "materialized",
                datetime(2026, 8, 2, 15, 1),
            ),
        ]
        for identifier, manifest, status, created_at in manifests:
            await connection.execute(
                text(
                    """
                    INSERT INTO leader_tactics_v2_run_manifests
                        (id, manifest_hash, universe, decision_cutoff,
                         data_receipt_cutoff, input_hash, source_registry_hash,
                         formula_registry_hash, code_version, holdout_identity,
                         status, research_only, provider_health_json,
                         exclusions_json, manifest_payload_json, created_at)
                    VALUES (:id, :manifest_hash, 'etf', :decision_cutoff,
                            :data_receipt_cutoff, :input_hash, :source_hash,
                            :formula_hash, :code_version, :holdout_identity,
                            :status, :research_only, :provider_health,
                            :exclusions, :manifest_payload, :created_at)
                    """
                ),
                {
                    "id": identifier,
                    "manifest_hash": manifest.manifest_hash,
                    "decision_cutoff": manifest.decision_cutoff,
                    "data_receipt_cutoff": manifest.data_receipt_cutoff,
                    "input_hash": manifest.input_hash,
                    "source_hash": V2_SOURCE_REGISTRY.registry_hash,
                    "formula_hash": V2_FORMULA_REGISTRY_HASH,
                    "code_version": manifest.code_version,
                    "holdout_identity": manifest.holdout_identity,
                    "status": status,
                    "research_only": manifest.research_only,
                    "provider_health": json.dumps(dict(manifest.provider_health)),
                    "exclusions": json.dumps(dict(manifest.exclusions)),
                    "manifest_payload": json.dumps(asdict(manifest), default=str),
                    "created_at": created_at,
                },
            )

        rows = [
            ("000001", "leader_breakout_proxy_v2", "available", True, 0.90, []),
            (
                "000002",
                "leader_breakout_proxy_v2",
                "available",
                False,
                0.10,
                ["batch_breadth_gate_failed"],
            ),
            ("000003", "base_launch_proxy_v2", "available", True, 0.80, []),
            (
                "000004",
                "base_launch_proxy_v2",
                "unavailable",
                False,
                None,
                ["insufficient_adjusted_history"],
            ),
            ("000005", "former_leader_repair_proxy_v2", "available", True, 0.70, []),
            ("000006", "former_leader_repair_proxy_v2", "available", True, 0.60, []),
        ]
        for index, (code, formula, availability, qualifies, score, exclusions) in enumerate(
            rows, 1
        ):
            await connection.execute(
                text(
                    """
                    INSERT INTO leader_tactics_v2_candidate_observations
                        (id, manifest_hash, universe, asset_code, asset_name, theme,
                         sector, tracked_index, formula_id, state, availability,
                         qualifies, score, signal_date, source_cutoff,
                         gate_facts_json, exclusion_reasons_json, provenance_json,
                         feature_hash, created_at)
                    VALUES (:id, :manifest_hash, 'etf', :asset_code, :asset_name,
                            'AI', 'technology', NULL, :formula, 'preparing',
                            :availability, :qualifies, :score, '2026-08-04',
                            '2026-08-04 15:00:00', '{}', :exclusions,
                            :provenance, :feature_hash, '2026-08-04 15:01:00')
                    """
                ),
                {
                    "id": index,
                    "manifest_hash": new_manifest.manifest_hash,
                    "asset_code": code,
                    "asset_name": f"ETF {code}",
                    "formula": formula,
                    "availability": availability,
                    "qualifies": qualifies,
                    "score": score,
                    "exclusions": json.dumps(exclusions),
                    "provenance": json.dumps(
                        {
                            "research_only": True,
                            "notification_provenance": "none",
                            "execution_provenance": "none",
                        }
                    ),
                    "feature_hash": f"{index:064d}",
                },
            )
        await connection.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_candidate_observations
                    (id, manifest_hash, universe, asset_code, asset_name, theme,
                     sector, tracked_index, formula_id, state, availability,
                     qualifies, score, signal_date, source_cutoff,
                     gate_facts_json, exclusion_reasons_json, provenance_json,
                     feature_hash, created_at)
                VALUES (100, :manifest_hash, 'etf', 'OLD', 'Old ETF', 'AI',
                        'technology', NULL, 'leader_breakout_proxy_v2', 'preparing',
                        'available', 1, 0.99, '2026-08-01', '2026-08-01 15:00:00',
                        '{}', '[]', '{}', :feature_hash, '2026-08-01 15:01:00')
                """
            ),
            {
                "manifest_hash": old_manifest.manifest_hash,
                "feature_hash": "1" * 64,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_state_transitions
                    (id, manifest_hash, universe, asset_code, formula_id,
                     signal_date, from_state, to_state, transition_date,
                     payload_json, transition_hash, created_at)
                VALUES (1, :manifest_hash, 'etf', '000001',
                        'leader_breakout_proxy_v2', '2026-08-04', 'preparing',
                        'confirmed', '2026-08-06', '{}', 'transition-1',
                        '2026-08-06 15:01:00'),
                       (2, :manifest_hash, 'etf', '000001',
                        'leader_breakout_proxy_v2', '2026-08-04', 'confirmed',
                        'invalidated', '2026-08-07', '{}', 'transition-2',
                        '2026-08-07 15:01:00')
                """
            ),
            {"manifest_hash": new_manifest.manifest_hash},
        )

    return {
        "old": old_manifest.manifest_hash,
        "new": new_manifest.manifest_hash,
        "future_receipt": future_receipt_manifest.manifest_hash,
    }


@pytest.mark.asyncio
async def test_candidates_isolate_latest_manifest_and_use_composite_cursor(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'candidates.db'}")
    manifest_ids = await _seed_read_db(engine)
    async with engine.begin() as connection:
        first = await read_v2_candidates(connection, limit=2)
        second = await read_v2_candidates(connection, cursor=first["next_cursor"], limit=2)
        as_of_old = await read_v2_candidates(connection, as_of="2026-08-02", limit=10)
        as_of_same_day = await read_v2_candidates(connection, as_of="2026-08-01", limit=10)
    await engine.dispose()

    assert [row["asset_code"] for row in first["candidates"]] == ["000001", "000003"]
    assert [row["asset_code"] for row in second["candidates"]] == ["000005", "000006"]
    assert [row["score"] for row in first["candidates"] + second["candidates"]] == [
        0.90,
        0.80,
        0.70,
        0.60,
    ]
    assert first["manifest_hash"] == second["manifest_hash"] == manifest_ids["new"]
    assert first["summary"] == second["summary"]
    assert first["summary"]["observation_count"] == 6
    assert first["summary"]["available_count"] == 5
    assert first["summary"]["qualifying_count"] == 4
    assert first["summary"]["returned_count"] == 4
    assert first["summary"]["exclusion_counts"]["batch_breadth_gate_failed"] == 1
    assert first["summary"]["exclusion_counts"]["insufficient_adjusted_history"] == 1
    assert as_of_old["manifest_hash"] == manifest_ids["old"]
    assert [row["asset_code"] for row in as_of_old["candidates"]] == ["OLD"]
    # Date-only as_of is end-of-day, so the 15:00 manifest is visible.
    assert as_of_same_day["manifest_hash"] == manifest_ids["old"]
    # The 8/2 decision with an 8/3 receipt is excluded at the 8/2 cutoff;
    # otherwise it would win over the 8/1 manifest above.


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["hash", "payload", "provider_health", "status"])
async def test_corrupt_latest_manifest_is_unavailable_and_not_fallen_back(
    tmp_path, corruption: str
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'{corruption}.db'}")
    manifest_ids = await _seed_read_db(engine)
    async with engine.begin() as connection:
        if corruption == "hash":
            await connection.execute(
                text(
                    """
                    UPDATE leader_tactics_v2_run_manifests
                    SET manifest_hash = :tampered_hash
                    WHERE manifest_hash = :manifest_hash
                    """
                ),
                {"manifest_hash": manifest_ids["new"], "tampered_hash": "f" * 64},
            )
        elif corruption == "payload":
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
        elif corruption == "provider_health":
            await connection.execute(
                text(
                    """
                    UPDATE leader_tactics_v2_run_manifests
                    SET provider_health_json = :provider_health
                    WHERE manifest_hash = :manifest_hash
                    """
                ),
                {
                    "manifest_hash": manifest_ids["new"],
                    "provider_health": json.dumps({"eastmoney": "tampered"}),
                },
            )
        else:
            await connection.execute(
                text(
                    """
                    UPDATE leader_tactics_v2_run_manifests
                    SET status = :status
                    WHERE manifest_hash = :manifest_hash
                    """
                ),
                {"manifest_hash": manifest_ids["new"], "status": "tampered"},
            )

        assert (await get_v2_materialized_manifest(connection, universe="etf")) is None
        payload = await read_v2_candidates(connection, limit=10)

    await engine.dispose()

    assert payload["manifest_hash"] is None
    assert payload["candidates"] == []
    assert payload["summary"]["unavailable_reason"] == ("leader_tactics_v2_not_materialized")


@pytest.mark.asyncio
async def test_transition_state_is_causal_to_as_of(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'transition.db'}")
    await _seed_read_db(engine)
    async with engine.begin() as connection:
        before = await read_v2_candidates(connection, as_of="2026-08-05", state="confirmed")
        after = await read_v2_candidates(connection, as_of="2026-08-06", state="confirmed")
        invalidated = await read_v2_candidates(connection, as_of="2026-08-07", state="invalidated")
        current = await read_v2_candidates(connection, state="invalidated")
    await engine.dispose()

    assert before["candidates"] == []
    assert [row["asset_code"] for row in after["candidates"]] == ["000001"]
    assert str(after["candidates"][0]["transition_date"])[:10] == "2026-08-06"
    assert [row["asset_code"] for row in invalidated["candidates"]] == ["000001"]
    assert [row["asset_code"] for row in current["candidates"]] == ["000001"]


@pytest.mark.asyncio
async def test_persist_observations_uses_executemany_and_is_idempotent(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'persist.db'}")
    events: list[bool] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def collect_executemany(_conn, _cursor, _statement, _parameters, _context, executemany):
        events.append(executemany)

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_source_registries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    registry_version TEXT NOT NULL,
                    registry_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        await connection.execute(text(_manifest_ddl()))
        await connection.execute(text(_observation_ddl()))

    observation = V2CandidateObservation(
        universe="etf",
        asset_code="510001",
        asset_name="Test ETF",
        signal_date=date(2026, 8, 4),
        formula_id="leader_breakout_proxy_v2",
        state="preparing",
        availability="available",
        qualifies=True,
        score=0.5,
        gate_facts=(("hot_theme", True),),
        exclusion_reasons=(),
        source_cutoff=datetime(2026, 8, 4, 15),
        theme="AI",
        sector="technology",
        tracked_index=None,
        clone_group=None,
        issuer=None,
        feature_hash="f" * 64,
    )
    canonical_manifest_hash = build_v2_manifest(
        universe="etf",
        decision_cutoff=datetime(2026, 8, 4, 15),
        data_receipt_cutoff=datetime(2026, 8, 4, 15, 1),
        input_hash="a" * 64,
        exclusions=(("missing_pit_theme_membership", 1),),
    ).manifest_hash
    result = V2ScreenResult(
        universe="etf",
        signal_date=date(2026, 8, 4),
        source_cutoff=datetime(2026, 8, 4, 15),
        data_receipt_cutoff=datetime(2026, 8, 4, 15, 1),
        observations=(
            observation,
            observation.__class__(
                **{
                    **observation.__dict__,
                    "asset_code": "510002",
                    "asset_name": "Test ETF 2",
                    "feature_hash": "e" * 64,
                }
            ),
        ),
        manifest_hash=canonical_manifest_hash,
        input_hash="a" * 64,
        exclusions=(("missing_pit_theme_membership", 1),),
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await persist_v2_screen_result(session, result)
        await persist_v2_screen_result(session, result)
        count = int(
            (
                await session.execute(
                    text("SELECT COUNT(*) FROM leader_tactics_v2_candidate_observations")
                )
            ).scalar_one()
        )
    await engine.dispose()

    assert count == 2
    assert any(events)
