from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.schemas.dual_universe_leader_tactics_v2 import (
    LeaderTacticsV2CandidatesOut,
    LeaderTacticsV2ContractOut,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    LIFECYCLE_STATES,
    V2_CANDIDATE_IDS,
    V2_EXPERIMENT_FAMILY,
    V2_FORMULAS,
    V2_SCHEMA_VERSION,
    V2_SOURCE_REGISTRY,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    read_v2_candidates,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_summary import (
    read_v2_summary,
    unavailable_v2_summary,
)
from app.services.workflows.dual_universe_leader_tactics_v2 import read_ashare_readiness

router = APIRouter(prefix="/api/short-research", tags=["leader-tactics-v2"])

_CLASSIFICATION_REASON_ALIASES = {
    "partial_capture": "theme_capture_partial",
    "partial_theme_capture": "theme_capture_partial",
    "theme_capture_partial": "theme_capture_partial",
    "stale_snapshot": "theme_snapshot_stale",
    "stale_context_snapshot": "theme_snapshot_stale",
    "theme_snapshot_stale": "theme_snapshot_stale",
    "insufficient_peers": "theme_peer_count_insufficient",
    "insufficient_context_peers": "theme_peer_count_insufficient",
    "theme_peer_count_insufficient": "theme_peer_count_insufficient",
    "missing_state": "theme_state_unavailable",
    "theme_state_unavailable": "theme_state_unavailable",
    "incompatible_taxonomy": "incompatible_theme_taxonomy",
    "taxonomy_incompatible": "incompatible_theme_taxonomy",
    "incompatible_theme_taxonomy": "incompatible_theme_taxonomy",
    "missing_compatible_peer_context": "missing_compatible_peer_context",
    "classification_graph_not_materialized": "classification_graph_not_materialized",
}


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _stable_classification_reasons(*values: object) -> list[str]:
    reasons: set[str] = set()
    for value in values:
        items = value if isinstance(value, (list, tuple, set)) else (value,)
        for item in items:
            normalized = _CLASSIFICATION_REASON_ALIASES.get(str(item or "").strip())
            if normalized:
                reasons.add(normalized)
    return sorted(reasons)


def _candidate_classification_projection(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Project persisted classification evidence without provider or database work."""

    if candidate.get("universe") != "ashare":
        return {
            "classification_status": "not_applicable",
            "industry_path": None,
            "selected_context": None,
            "alternative_contexts": [],
            "rejected_contexts": [],
            "theme_state": None,
            "classification_provider_health": {},
            "classification_unavailable_reasons": [],
        }

    gate_facts = _mapping(candidate.get("gate_facts"))
    provenance = _mapping(candidate.get("provenance"))
    graph = _mapping(candidate.get("classification_graph"))
    if not graph:
        graph = _mapping(gate_facts.get("classification_graph"))
    if not graph:
        graph = _mapping(provenance.get("classification_graph"))

    def graph_value(key: str, *aliases: str) -> object:
        for container in (candidate, graph):
            for name in (key, *aliases):
                value = container.get(name)
                if value is not None:
                    return value
        return None

    industry_path = _mapping(graph_value("industry_path")) or None
    selected_context = _mapping(graph_value("selected_context", "selected_peer_context")) or None
    alternatives = _mapping_list(graph_value("alternative_contexts", "alternative_memberships"))
    rejected = _mapping_list(graph_value("rejected_contexts"))
    theme_state = _mapping(graph_value("theme_state", "selected_theme_state")) or None
    provider_health = _mapping(graph_value("classification_provider_health", "provider_health"))
    reasons = _stable_classification_reasons(
        graph_value("classification_unavailable_reasons", "unavailable_reasons"),
        candidate.get("exclusion_reasons"),
        theme_state.get("unavailable_reasons") if theme_state else None,
    )
    declared_status = graph_value("classification_status", "status")
    if declared_status not in {"available", "unavailable"}:
        declared_status = "available" if selected_context else "unavailable"
    if declared_status == "unavailable" and not reasons:
        reasons = ["classification_graph_not_materialized"]

    return {
        "classification_status": declared_status,
        "industry_path": industry_path,
        "selected_context": selected_context,
        "alternative_contexts": alternatives,
        "rejected_contexts": rejected,
        "theme_state": theme_state,
        "classification_provider_health": provider_health,
        "classification_unavailable_reasons": reasons,
    }


def _classification_readiness_projection(
    value: object,
    *,
    universe: Literal["etf", "ashare"],
) -> dict[str, Any]:
    if universe == "etf":
        return {
            "status": "not_applicable",
            "unavailable_reasons": [],
        }
    readiness = _mapping(value)
    reasons = _stable_classification_reasons(readiness.get("unavailable_reasons"))
    status = readiness.get("status")
    if status not in {"available", "unavailable"}:
        status = "available" if readiness else "unavailable"
    if status == "unavailable" and not reasons:
        reasons = ["classification_graph_not_materialized"]
    return {
        **readiness,
        "status": status,
        "unavailable_reasons": reasons,
    }


def _project_candidates_payload(
    payload: Mapping[str, Any],
    *,
    universe: Literal["etf", "ashare"],
) -> dict[str, Any]:
    projected = dict(payload)
    projected["candidates"] = [
        {**dict(candidate), **_candidate_classification_projection(candidate)}
        for candidate in payload.get("candidates", [])
        if isinstance(candidate, Mapping)
    ]
    summary = _mapping(payload.get("summary"))
    summary["classification_readiness"] = _classification_readiness_projection(
        summary.get("classification_readiness") or payload.get("classification_readiness"),
        universe=universe,
    )
    projected["summary"] = summary
    return projected


def _project_evidence_summary(
    payload: Mapping[str, Any],
    *,
    universe: Literal["etf", "ashare"],
) -> dict[str, Any]:
    projected = dict(payload)
    projected["classification_readiness"] = _classification_readiness_projection(
        payload.get("classification_readiness"),
        universe=universe,
    )
    return projected


def _empty_candidates_payload(
    *,
    universe: Literal["etf", "ashare"],
    formula: str,
    state: str,
    as_of: str | None,
    reason: str,
) -> dict:
    return {
        "schema_version": "dual_universe_leader_tactics_screen_v2",
        "experiment_family": V2_EXPERIMENT_FAMILY,
        "universe": universe,
        "formula": formula,
        "state": state,
        "as_of": as_of,
        "candidates": [],
        "next_cursor": None,
        "has_more": False,
        "summary": {
            "observation_count": 0,
            "qualifying_count": 0,
            "coverage": "insufficient_data",
            "unavailable_reason": reason,
            "classification_readiness": _classification_readiness_projection(
                None,
                universe=universe,
            ),
        },
        "ranking_source_kind": "research_replay",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "research_only": True,
        "production_mutation_allowed": False,
    }


@router.get("/leader-tactics-v2/contract", response_model=LeaderTacticsV2ContractOut)
async def get_leader_tactics_v2_contract() -> LeaderTacticsV2ContractOut:
    """Expose the frozen V2 contract without triggering data collection."""

    return LeaderTacticsV2ContractOut.model_validate(
        {
            "schema_version": V2_SCHEMA_VERSION,
            "experiment_family": V2_EXPERIMENT_FAMILY,
            "source_registry": asdict(V2_SOURCE_REGISTRY),
            "formulas": [asdict(formula) for formula in V2_FORMULAS],
            "candidate_ids": list(V2_CANDIDATE_IDS),
            "lifecycle_states": list(LIFECYCLE_STATES),
            "research_only": True,
            "production_mutation_allowed": False,
        }
    )


@router.get("/leader-tactics-v2/readiness")
async def get_leader_tactics_v2_readiness(
    request: Request,
    as_of: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Read A-share persisted readiness without starting collection."""

    if not request.app.state.settings.etf_leader_tactics_v2_api_enabled:
        return {
            "schema_version": "dual_universe_leader_tactics_readiness_v1",
            "as_of": as_of.isoformat() if as_of else None,
            "research_only": True,
            "unavailable_reason": "leader_tactics_v2_api_disabled",
        }
    try:
        report = await read_ashare_readiness(
            session,
            as_of=as_of or datetime.utcnow(),
        )
    except SQLAlchemyError:
        return {
            "schema_version": "dual_universe_leader_tactics_readiness_v1",
            "as_of": as_of.isoformat() if as_of else None,
            "research_only": True,
            "unavailable_reason": "ashare_research_tables_not_materialized",
        }
    return report.to_dict()


@router.get("/leader-tactics-v2/summary")
async def get_leader_tactics_v2_summary(
    request: Request,
    universe: Literal["etf", "ashare"] = Query(default="etf"),
    as_of: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return persisted V2 evidence layers without provider work."""

    if not request.app.state.settings.etf_leader_tactics_v2_api_enabled:
        return _project_evidence_summary(
            unavailable_v2_summary(
                universe=universe,
                as_of=as_of,
                reason="leader_tactics_v2_api_disabled",
            ),
            universe=universe,
        )
    if (
        universe == "etf"
        and not request.app.state.settings.etf_leader_tactics_v2_etf_materialize_enabled
    ):
        return _project_evidence_summary(
            unavailable_v2_summary(
                universe=universe,
                as_of=as_of,
                reason="leader_tactics_v2_etf_materialization_disabled",
            ),
            universe=universe,
        )
    try:
        payload = await read_v2_summary(session, universe=universe, as_of=as_of)
        return _project_evidence_summary(payload, universe=universe)
    except ValueError:
        return _project_evidence_summary(
            unavailable_v2_summary(
                universe=universe,
                as_of=as_of,
                reason="leader_tactics_v2_invalid_filter",
            ),
            universe=universe,
        )
    except SQLAlchemyError:
        return _project_evidence_summary(
            unavailable_v2_summary(
                universe=universe,
                as_of=as_of,
                reason="leader_tactics_v2_not_materialized",
            ),
            universe=universe,
        )


@router.get(
    "/leader-tactics-v2/candidates",
    response_model=LeaderTacticsV2CandidatesOut,
)
async def get_leader_tactics_v2_candidates(
    request: Request,
    universe: Literal["etf", "ashare"] = Query(default="etf"),
    formula: str = Query(default="all"),
    state: str = Query(default="all"),
    as_of: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> LeaderTacticsV2CandidatesOut:
    """Read bounded, materialized V2 research rows only.

    The endpoint is deliberately unavailable until the V2 API flag is enabled;
    neither branch invokes a provider or mutates production ranking state.
    """

    settings = request.app.state.settings
    if not settings.etf_leader_tactics_v2_api_enabled:
        return LeaderTacticsV2CandidatesOut.model_validate(
            _empty_candidates_payload(
                universe=universe,
                formula=formula,
                state=state,
                as_of=as_of,
                reason="leader_tactics_v2_api_disabled",
            )
        )
    if universe == "etf" and not settings.etf_leader_tactics_v2_etf_materialize_enabled:
        return LeaderTacticsV2CandidatesOut.model_validate(
            _empty_candidates_payload(
                universe=universe,
                formula=formula,
                state=state,
                as_of=as_of,
                reason="leader_tactics_v2_etf_materialization_disabled",
            )
        )
    try:
        payload = await read_v2_candidates(
            session,
            universe=universe,
            formula=formula,
            state=state,
            as_of=as_of,
            cursor=cursor,
            limit=limit,
        )
    except ValueError:
        return LeaderTacticsV2CandidatesOut.model_validate(
            _empty_candidates_payload(
                universe=universe,
                formula=formula,
                state=state,
                as_of=as_of,
                reason="leader_tactics_v2_invalid_filter",
            )
        )
    except SQLAlchemyError:
        return LeaderTacticsV2CandidatesOut.model_validate(
            _empty_candidates_payload(
                universe=universe,
                formula=formula,
                state=state,
                as_of=as_of,
                reason="leader_tactics_v2_not_materialized",
            )
        )
    return LeaderTacticsV2CandidatesOut.model_validate(
        _project_candidates_payload(payload, universe=universe)
    )


__all__ = ["router"]
