"""Read-only summary projection for persisted V2 research evidence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2_CANDIDATE_IDS,
    V2_EXPERIMENT_FAMILY,
    V2_FORMULA_REGISTRY_HASH,
    V2_SCHEMA_VERSION,
    V2_SOURCE_REGISTRY,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_availability import (
    v2_availability,
    validate_v2_unavailable_reason,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    get_v2_materialized_manifest,
)


def _decode(value: object, fallback: Any) -> Any:
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _iso(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "coverage": numerator / denominator if denominator else 0.0,
    }


def unavailable_v2_summary(
    *,
    universe: str,
    as_of: datetime | str | None,
    reason: str,
) -> dict[str, Any]:
    validate_v2_unavailable_reason(reason)
    return {
        "schema_version": "dual_universe_leader_tactics_evidence_summary_v2",
        "experiment_family": V2_EXPERIMENT_FAMILY,
        "universe": universe,
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
        "availability": "unavailable",
        "unavailable_reason": reason,
        "registry_identity": {
            "schema_version": V2_SCHEMA_VERSION,
            "source_registry_hash": V2_SOURCE_REGISTRY.registry_hash,
            "formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
            "candidate_ids": list(V2_CANDIDATE_IDS),
        },
        "manifest": None,
        "layer_coverage": {},
        "availability_gates": {
            "manifest": v2_availability(0, 1, reason=reason).to_dict(),
            "economic_evidence": v2_availability(
                0, 1, reason="economic_validation_not_materialized"
            ).to_dict(),
        },
        "candidate_counts": {},
        "exclusion_counts": {},
        "provider_health": {},
        "economic_evidence": {
            "status": "unavailable",
            "unavailable_reason": "economic_validation_not_materialized",
        },
        "notification_provenance": "none",
        "execution_provenance": "none",
        "ranking_source_kind": "research_replay",
        "research_only": True,
        "production_mutation_allowed": False,
    }


async def read_v2_summary(
    session: AsyncSession,
    *,
    universe: str = "etf",
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Read one compatible materialized manifest; never invokes providers."""

    if universe not in {"etf", "ashare"}:
        raise ValueError("universe must be etf or ashare")
    manifest = await get_v2_materialized_manifest(
        session,
        universe=universe,
        as_of=as_of,
    )
    if manifest is None:
        return unavailable_v2_summary(
            universe=universe,
            as_of=as_of,
            reason="leader_tactics_v2_not_materialized",
        )

    manifest_hash = str(manifest["manifest_hash"])
    observations = (
        (
            await session.execute(
                text(
                    """
                SELECT formula_id, state, availability, qualifies,
                       exclusion_reasons_json
                FROM leader_tactics_v2_candidate_observations
                WHERE manifest_hash = :manifest_hash
                ORDER BY formula_id, state
                """
                ),
                {"manifest_hash": manifest_hash},
            )
        )
        .mappings()
        .all()
    )
    total = len(observations)
    available = sum(row["availability"] == "available" for row in observations)
    qualifying = sum(bool(row["qualifies"]) for row in observations)
    by_formula: dict[str, int] = {}
    by_state: dict[str, int] = {}
    exclusions: dict[str, int] = {}
    for row in observations:
        formula_id = str(row["formula_id"])
        state = str(row["state"])
        by_formula[formula_id] = by_formula.get(formula_id, 0) + 1
        by_state[state] = by_state.get(state, 0) + 1
        reasons = _decode(row["exclusion_reasons_json"], [])
        if isinstance(reasons, list):
            for reason in reasons:
                exclusions[str(reason)] = exclusions.get(str(reason), 0) + 1
    manifest_exclusions = _decode(manifest["exclusions_json"], {})
    if isinstance(manifest_exclusions, dict):
        for reason, count in manifest_exclusions.items():
            exclusions[str(reason)] = max(exclusions.get(str(reason), 0), int(count))

    payload = _decode(manifest["manifest_payload_json"], {})
    payload_evidence = payload.get("economic_evidence") if isinstance(payload, dict) else None
    economic = (
        payload_evidence
        if isinstance(payload_evidence, dict)
        else {
            "status": "unavailable",
            "unavailable_reason": "economic_validation_not_materialized",
        }
    )
    return {
        "schema_version": "dual_universe_leader_tactics_evidence_summary_v2",
        "experiment_family": V2_EXPERIMENT_FAMILY,
        "universe": universe,
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
        "availability": "materialized_only" if total else "insufficient_data",
        "unavailable_reason": None if total else "leader_tactics_v2_empty_materialization",
        "registry_identity": {
            "schema_version": V2_SCHEMA_VERSION,
            "source_registry_hash": str(manifest["source_registry_hash"]),
            "formula_registry_hash": str(manifest["formula_registry_hash"]),
            "candidate_ids": list(V2_CANDIDATE_IDS),
        },
        "manifest": {
            "manifest_hash": manifest_hash,
            "decision_cutoff": _iso(manifest["decision_cutoff"]),
            "data_receipt_cutoff": _iso(manifest["data_receipt_cutoff"]),
            "input_hash": str(manifest["input_hash"]),
            "code_version": str(manifest["code_version"]),
            "holdout_identity": str(manifest["holdout_identity"]),
            "status": str(manifest["status"]),
        },
        "layer_coverage": {
            "candidate_observations": _ratio(total, total),
            "available_observations": _ratio(available, total),
            "qualifying_observations": _ratio(qualifying, total),
        },
        "availability_gates": {
            "manifest": v2_availability(1, 1).to_dict(),
            "candidate_observations": v2_availability(
                total,
                total,
                reason=None if total else "leader_tactics_v2_empty_materialization",
            ).to_dict(),
            "adjusted_observations": v2_availability(
                available,
                total,
                reason=None if available == total else "insufficient_data",
            ).to_dict(),
            "economic_evidence": v2_availability(
                1 if economic.get("status") not in {None, "unavailable"} else 0,
                1,
                reason=(
                    None
                    if economic.get("status") not in {None, "unavailable"}
                    else "economic_validation_not_materialized"
                ),
            ).to_dict(),
        },
        "candidate_counts": {
            "total": total,
            "available": available,
            "qualifying": qualifying,
            "by_formula": by_formula,
            "by_state": by_state,
        },
        "exclusion_counts": dict(sorted(exclusions.items())),
        "provider_health": _decode(manifest["provider_health_json"], {}),
        "economic_evidence": economic,
        "notification_provenance": "none",
        "execution_provenance": "none",
        "ranking_source_kind": "research_replay",
        "research_only": True,
        "production_mutation_allowed": False,
    }


__all__ = ["read_v2_summary", "unavailable_v2_summary"]
