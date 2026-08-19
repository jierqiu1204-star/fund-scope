"""Durable two-stage A-share V2 materialization with bounded memory."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2_CANDIDATE_IDS,
    V2_FORMULA_REGISTRY_HASH,
    V2_SOURCE_REGISTRY,
    V2CandidateObservation,
    V2ScreenResult,
    V2StagedAssetFeature,
    attach_sentiment_risk_snapshot,
    build_v2_manifest,
    build_v2_staged_asset_feature,
    screen_dual_universe,
    staged_sentiment_risk_snapshot,
    staged_theme_percentile_overrides,
    staged_v2_input_hash,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
    read_ashare_asset_inputs,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_theme_graph_state import (
    materialize_theme_states_from_features,
)

STAGE_BATCH_MIN = 5
STAGE_BATCH_MAX = 20
STAGE_BATCH_DEFAULT = 20


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=lambda item: item.isoformat() if isinstance(item, (date, datetime)) else str(item),
    )


async def staging_tables_available(session: AsyncSession) -> bool:
    if not hasattr(session, "run_sync"):
        return False
    return await session.run_sync(
        lambda sync_session: inspect(sync_session.connection()).has_table(
            "leader_tactics_v2_materialization_runs"
        )
    )


def _feature_payload(feature: V2StagedAssetFeature) -> dict[str, Any]:
    return asdict(feature)


def _feature_from_payload(payload: str) -> V2StagedAssetFeature:
    value = json.loads(payload)
    return V2StagedAssetFeature(**value)


def _observation_from_payload(payload: dict[str, Any]) -> V2CandidateObservation:
    payload = dict(payload)
    payload["signal_date"] = date.fromisoformat(payload["signal_date"])
    payload["source_cutoff"] = datetime.fromisoformat(payload["source_cutoff"])
    payload["gate_facts"] = tuple(tuple(item) for item in payload["gate_facts"])
    payload["exclusion_reasons"] = tuple(payload["exclusion_reasons"])
    return V2CandidateObservation(**payload)


async def advance_ashare_materialization(
    session: AsyncSession,
    *,
    assets: tuple[tuple[str, str], ...],
    signal_date: date,
    source_cutoff: datetime,
    decision_date: date,
    decision_mode: str,
    next_eligible_date: date | None,
    code_version: str,
    provider_health: tuple[tuple[str, str], ...],
    budget_seconds: float = 48.0,
    batch_size: int = STAGE_BATCH_DEFAULT,
    memory_reader: Callable[[], int | None] | None = None,
    theme_graph_enabled: bool = False,
) -> tuple[V2ScreenResult | None, dict[str, Any]]:
    """Advance Stage A or B and commit every bounded unit of work."""

    if not STAGE_BATCH_MIN <= batch_size <= STAGE_BATCH_MAX:
        raise ValueError(
            f"materialization batch_size must be in [{STAGE_BATCH_MIN}, {STAGE_BATCH_MAX}]"
        )
    started = time.monotonic()
    ordered_assets = tuple(sorted(assets))
    universe_hash = stable_contract_hash(ordered_assets)
    existing_run = (
        (
            await session.execute(
                text(
                    """
                    SELECT run_hash, source_cutoff, provider_health_json
                    FROM leader_tactics_v2_materialization_runs
                    WHERE universe = 'ashare'
                      AND status <> 'state_ready'
                      AND signal_date = :signal_date
                      AND decision_date = :decision_date
                      AND universe_hash = :universe_hash
                      AND expected_count = :expected_count
                      AND code_version = :code_version
                      AND source_registry_hash = :source_registry_hash
                      AND formula_registry_hash = :formula_registry_hash
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ),
                {
                    "signal_date": signal_date,
                    "decision_date": decision_date,
                    "universe_hash": universe_hash,
                    "expected_count": len(ordered_assets),
                    "code_version": code_version,
                    "source_registry_hash": V2_SOURCE_REGISTRY.registry_hash,
                    "formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
                },
            )
        )
        .mappings()
        .first()
    )
    if existing_run is not None:
        run_hash = str(existing_run["run_hash"])
        frozen_cutoff = existing_run["source_cutoff"]
        source_cutoff = (
            datetime.fromisoformat(frozen_cutoff)
            if isinstance(frozen_cutoff, str)
            else frozen_cutoff
        )
        frozen_health = json.loads(str(existing_run["provider_health_json"]))
        provider_health = tuple(
            sorted((str(key), str(value)) for key, value in frozen_health.items())
        )
    else:
        run_hash = stable_contract_hash(
            {
                "schema": "leader_tactics_v2_materialization_stage_v1",
                "signal_date": signal_date,
                "source_cutoff": source_cutoff,
                "decision_date": decision_date,
                "universe_hash": universe_hash,
                "code_version": code_version,
                "source_registry_hash": V2_SOURCE_REGISTRY.registry_hash,
                "formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
            }
        )
    now = _utc_now_naive()
    await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_materialization_runs
                (run_hash, universe, signal_date, source_cutoff, decision_date,
                 universe_hash, expected_count, status, code_version,
                 source_registry_hash, formula_registry_hash,
                 provider_health_json, created_at, updated_at)
            VALUES
                (:run_hash, 'ashare', :signal_date, :source_cutoff, :decision_date,
                 :universe_hash, :expected_count, 'extracting', :code_version,
                 :source_registry_hash, :formula_registry_hash,
                 :provider_health, :now, :now)
            ON CONFLICT (run_hash) DO NOTHING
            """
        ),
        {
            "run_hash": run_hash,
            "signal_date": signal_date,
            "source_cutoff": source_cutoff,
            "decision_date": decision_date,
            "universe_hash": universe_hash,
            "expected_count": len(ordered_assets),
            "code_version": code_version,
            "source_registry_hash": V2_SOURCE_REGISTRY.registry_hash,
            "formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
            "provider_health": _json(dict(provider_health)),
            "now": now,
        },
    )
    await session.commit()
    lease_owner = f"materializer:{uuid4().hex}"
    lease_now = _utc_now_naive()
    lease_result = await session.execute(
        text(
            """
            UPDATE leader_tactics_v2_materialization_runs
            SET lease_owner = :lease_owner,
                lease_expires_at = :lease_expires_at,
                updated_at = :updated_at
            WHERE run_hash = :run_hash
              AND (
                    lease_owner IS NULL
                    OR lease_expires_at IS NULL
                    OR lease_expires_at <= :updated_at
                  )
            """
        ),
        {
            "run_hash": run_hash,
            "lease_owner": lease_owner,
            "lease_expires_at": lease_now + timedelta(seconds=55),
            "updated_at": lease_now,
        },
    )
    if int(lease_result.rowcount or 0) != 1:
        await session.rollback()
        return None, {
            "status": "partial",
            "materialization_stage": "lease_wait",
            "run_hash": run_hash,
            "unavailable_reason": "materialization_lease_busy",
            "research_only": True,
        }
    await session.commit()

    async def release_lease(*, status: str | None = None) -> None:
        status_sql = ", status = :status" if status is not None else ""
        params: dict[str, object] = {
            "run_hash": run_hash,
            "lease_owner": lease_owner,
            "updated_at": _utc_now_naive(),
        }
        if status is not None:
            params["status"] = status
        await session.execute(
            text(
                "UPDATE leader_tactics_v2_materialization_runs "
                "SET lease_owner = NULL, lease_expires_at = NULL, "
                f"updated_at = :updated_at{status_sql} "
                "WHERE run_hash = :run_hash AND lease_owner = :lease_owner"
            ),
            params,
        )
        await session.commit()

    existing = set(
        (
            await session.execute(
                text(
                    "SELECT asset_code FROM leader_tactics_v2_materialization_features "
                    "WHERE run_hash = :run_hash"
                ),
                {"run_hash": run_hash},
            )
        ).scalars()
    )
    pending = [item for item in ordered_assets if item[0] not in existing]
    while pending and time.monotonic() - started < budget_seconds - 4.0:
        page = tuple(pending[:batch_size])
        inputs = await read_ashare_asset_inputs(
            session,
            assets=page,
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            history_limit=180,
            page_size=len(page),
            decision_mode=decision_mode,
            membership_evaluation_date=decision_date,
            next_eligible_date=next_eligible_date,
            theme_graph_enabled=theme_graph_enabled,
        )
        expected_codes = tuple(code for code, _name in page)
        received_codes = tuple(item.asset_code for item in inputs)
        if len(received_codes) != len(set(received_codes)) or set(received_codes) != set(
            expected_codes
        ):
            await release_lease(status="extracting")
            return None, {
                "status": "partial",
                "materialization_stage": "feature_extraction",
                "run_hash": run_hash,
                "unavailable_reason": "staged_asset_input_set_mismatch",
                "expected_page_count": len(expected_codes),
                "received_page_count": len(received_codes),
                "research_only": True,
            }
        rows = []
        for item in inputs:
            feature = build_v2_staged_asset_feature(item)
            feature_payload = _feature_payload(feature)
            rows.append(
                {
                    "run_hash": run_hash,
                    "asset_code": feature.asset_code,
                    "asset_name": feature.asset_name,
                    "group_key": feature.group_key,
                    "feature_json": _json(feature_payload),
                    "feature_hash": stable_contract_hash(feature_payload),
                    "input_digest": feature.input_digest,
                    "created_at": _utc_now_naive(),
                }
            )
        await session.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_materialization_features
                    (run_hash, asset_code, asset_name, group_key, feature_json,
                     feature_hash, input_digest, created_at)
                VALUES
                    (:run_hash, :asset_code, :asset_name, :group_key, :feature_json,
                     :feature_hash, :input_digest, :created_at)
                ON CONFLICT (run_hash, asset_code) DO NOTHING
                """
            ),
            rows,
        )
        await session.commit()
        pending = pending[len(page) :]

    if pending:
        completed = len(ordered_assets) - len(pending)
        await release_lease(status="extracting")
        return None, {
            "status": "partial",
            "materialization_stage": "feature_extraction",
            "run_hash": run_hash,
            "completed_count": completed,
            "expected_count": len(ordered_assets),
            "batch_size": batch_size,
            "research_only": True,
        }

    feature_rows = (
        (
            await session.execute(
                text(
                    "SELECT asset_code, feature_json, feature_hash, input_digest "
                    "FROM leader_tactics_v2_materialization_features "
                    "WHERE run_hash = :run_hash ORDER BY asset_code"
                ),
                {"run_hash": run_hash},
            )
        )
        .mappings()
        .all()
    )
    features_list: list[V2StagedAssetFeature] = []
    for row in feature_rows:
        raw_payload = json.loads(str(row["feature_json"]))
        if (
            stable_contract_hash(raw_payload) != str(row["feature_hash"])
            or str(raw_payload.get("asset_code")) != str(row["asset_code"])
            or str(raw_payload.get("input_digest")) != str(row["input_digest"])
        ):
            await session.execute(
                text(
                    "DELETE FROM leader_tactics_v2_materialization_groups "
                    "WHERE run_hash = :run_hash"
                ),
                {"run_hash": run_hash},
            )
            await session.execute(
                text(
                    "DELETE FROM leader_tactics_v2_materialization_features "
                    "WHERE run_hash = :run_hash AND asset_code = :asset_code"
                ),
                {"run_hash": run_hash, "asset_code": str(row["asset_code"])},
            )
            await session.commit()
            await release_lease(status="extracting")
            return None, {
                "status": "partial",
                "materialization_stage": "feature_integrity",
                "run_hash": run_hash,
                "unavailable_reason": "staged_feature_hash_mismatch_requeued",
                "research_only": True,
            }
        features_list.append(_feature_from_payload(str(row["feature_json"])))
    features = tuple(features_list)
    if len(features) != len(ordered_assets):
        await release_lease(status="extracting")
        return None, {
            "status": "partial",
            "materialization_stage": "feature_integrity",
            "run_hash": run_hash,
            "unavailable_reason": "staged_feature_count_mismatch",
            "completed_count": len(features),
            "expected_count": len(ordered_assets),
            "research_only": True,
        }
    await session.execute(
        text(
            "UPDATE leader_tactics_v2_materialization_runs "
            "SET status = 'screening', updated_at = :updated_at "
            "WHERE run_hash = :run_hash AND status <> 'ready'"
        ),
        {
            "run_hash": run_hash,
            "updated_at": _utc_now_naive(),
        },
    )
    await session.commit()
    if (
        theme_graph_enabled
        and any(feature.group_key for feature in features)
        and any(
            feature.group_key and feature.theme_state_hash is None
            for feature in features
        )
    ):
        states = await materialize_theme_states_from_features(
            session,
            features=features,
            state_date=signal_date,
            source_cutoff=source_cutoff,
            received_at=_utc_now_naive(),
        )
        await session.commit()
        await release_lease(status="state_ready")
        return None, {
            "status": "partial",
            "materialization_stage": "theme_state",
            "run_hash": run_hash,
            "theme_state_count": len(states),
            "unavailable_reason": "theme_state_materialized_waiting_next_cutoff",
            "research_only": True,
        }
    overrides = staged_theme_percentile_overrides(features)
    sentiment_risk = staged_sentiment_risk_snapshot(
        features,
        theme_percentile_overrides=overrides,
        signal_date=signal_date,
        source_cutoff=source_cutoff,
    )
    assets_by_group: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for feature in features:
        key = feature.group_key or f"__ungrouped__:{feature.asset_code}"
        assets_by_group[key].append((feature.asset_code, feature.asset_name))

    completed_groups = set(
        (
            await session.execute(
                text(
                    "SELECT group_key FROM leader_tactics_v2_materialization_groups "
                    "WHERE run_hash = :run_hash"
                ),
                {"run_hash": run_hash},
            )
        ).scalars()
    )
    for group_key in sorted(assets_by_group):
        if group_key in completed_groups:
            continue
        if time.monotonic() - started >= budget_seconds - 4.0:
            break
        group_assets = tuple(assets_by_group[group_key])
        required_group_headroom = max(
            192 * 1024 * 1024,
            128 * 1024 * 1024 + len(group_assets) * 512 * 1024,
        )
        current_headroom = memory_reader() if memory_reader is not None else None
        if current_headroom is not None and current_headroom < required_group_headroom:
            await release_lease(status="screening")
            return None, {
                "status": "partial",
                "materialization_stage": "group_screening",
                "run_hash": run_hash,
                "unavailable_reason": "insufficient_materialization_memory_headroom",
                "waiting_group": group_key,
                "waiting_group_size": len(group_assets),
                "available_memory_bytes": current_headroom,
                "required_memory_bytes": required_group_headroom,
                "completed_group_count": len(completed_groups),
                "expected_group_count": len(assets_by_group),
                "research_only": True,
            }
        inputs = await read_ashare_asset_inputs(
            session,
            assets=group_assets,
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            history_limit=180,
            page_size=min(500, max(1, len(group_assets))),
            decision_mode=decision_mode,
            membership_evaluation_date=decision_date,
            next_eligible_date=next_eligible_date,
            theme_graph_enabled=theme_graph_enabled,
        )
        partial = screen_dual_universe(
            inputs,
            code_version=code_version,
            provider_health=provider_health,
            theme_percentile_overrides=overrides,
        )
        payload = [asdict(item) for item in partial.observations]
        encoded = _json(payload)
        await session.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_materialization_groups
                    (run_hash, group_key, observation_json, content_hash, created_at)
                VALUES (:run_hash, :group_key, :payload, :content_hash, :created_at)
                ON CONFLICT (run_hash, group_key) DO NOTHING
                """
            ),
            {
                "run_hash": run_hash,
                "group_key": group_key,
                "payload": encoded,
                "content_hash": stable_contract_hash(payload),
                "created_at": _utc_now_naive(),
            },
        )
        await session.commit()
        completed_groups.add(group_key)

    if len(completed_groups) != len(assets_by_group):
        await release_lease(status="screening")
        return None, {
            "status": "partial",
            "materialization_stage": "group_screening",
            "run_hash": run_hash,
            "completed_group_count": len(completed_groups),
            "expected_group_count": len(assets_by_group),
            "research_only": True,
        }

    group_rows = (
        (
            await session.execute(
                text(
                    "SELECT group_key, observation_json, content_hash "
                    "FROM leader_tactics_v2_materialization_groups "
                    "WHERE run_hash = :run_hash ORDER BY group_key"
                ),
                {"run_hash": run_hash},
            )
        )
        .mappings()
        .all()
    )
    decoded_observations: list[V2CandidateObservation] = []
    for row in group_rows:
        payload = json.loads(str(row["observation_json"]))
        if stable_contract_hash(payload) != str(row["content_hash"]):
            await session.execute(
                text(
                    "DELETE FROM leader_tactics_v2_materialization_groups "
                    "WHERE run_hash = :run_hash AND group_key = :group_key"
                ),
                {"run_hash": run_hash, "group_key": str(row["group_key"])},
            )
            await session.commit()
            await release_lease(status="screening")
            return None, {
                "status": "partial",
                "materialization_stage": "group_integrity",
                "run_hash": run_hash,
                "unavailable_reason": "staged_group_hash_mismatch_requeued",
                "research_only": True,
            }
        decoded_observations.extend(_observation_from_payload(item) for item in payload)
    observations = attach_sentiment_risk_snapshot(
        decoded_observations,
        sentiment_risk,
    )
    observation_identities = {
        (row.asset_code, row.formula_id, row.signal_date) for row in observations
    }
    expected_observation_count = len(features) * len(V2_CANDIDATE_IDS)
    if (
        len(observations) != expected_observation_count
        or len(observation_identities) != len(observations)
    ):
        await session.execute(
            text("DELETE FROM leader_tactics_v2_materialization_groups WHERE run_hash = :run_hash"),
            {"run_hash": run_hash},
        )
        await session.commit()
        await release_lease(status="screening")
        return None, {
            "status": "partial",
            "materialization_stage": "group_integrity",
            "run_hash": run_hash,
            "unavailable_reason": "staged_observation_terminal_count_mismatch_requeued",
            "research_only": True,
        }
    exclusions: dict[str, int] = defaultdict(int)
    for observation in observations:
        for reason in observation.exclusion_reasons:
            exclusions[reason] += 1
    input_hash = staged_v2_input_hash(features)
    manifest = build_v2_manifest(
        universe="ashare",
        decision_cutoff=source_cutoff,
        data_receipt_cutoff=source_cutoff,
        input_hash=input_hash,
        code_version=code_version,
        exclusions=tuple(sorted(exclusions.items())),
        provider_health=provider_health,
    )
    result = V2ScreenResult(
        universe="ashare",
        signal_date=signal_date,
        source_cutoff=source_cutoff,
        observations=observations,
        manifest_hash=manifest.manifest_hash,
        input_hash=input_hash,
        exclusions=tuple(sorted(exclusions.items())),
        data_receipt_cutoff=source_cutoff,
        code_version=code_version,
        provider_health=provider_health,
    )
    await session.execute(
        text(
            "UPDATE leader_tactics_v2_materialization_runs "
            "SET status = 'ready', updated_at = :updated_at, "
            "lease_owner = NULL, lease_expires_at = NULL "
            "WHERE run_hash = :run_hash AND lease_owner = :lease_owner"
        ),
        {
            "run_hash": run_hash,
            "lease_owner": lease_owner,
            "updated_at": _utc_now_naive(),
        },
    )
    await session.commit()
    return result, {
        "status": "ready",
        "materialization_stage": "complete",
        "run_hash": run_hash,
        "completed_count": len(features),
        "completed_group_count": len(assets_by_group),
        "research_only": True,
    }


__all__ = ["advance_ashare_materialization", "staging_tables_available"]
