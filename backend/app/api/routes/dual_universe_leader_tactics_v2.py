from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Literal

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
        return unavailable_v2_summary(
            universe=universe,
            as_of=as_of,
            reason="leader_tactics_v2_api_disabled",
        )
    try:
        return await read_v2_summary(session, universe=universe, as_of=as_of)
    except ValueError:
        return unavailable_v2_summary(
            universe=universe,
            as_of=as_of,
            reason="leader_tactics_v2_invalid_filter",
        )
    except SQLAlchemyError:
        return unavailable_v2_summary(
            universe=universe,
            as_of=as_of,
            reason="leader_tactics_v2_not_materialized",
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
    return LeaderTacticsV2CandidatesOut.model_validate(payload)


__all__ = ["router"]
