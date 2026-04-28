from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import RecommendationItem, RecommendationRun
from app.schemas.recommendations import (
    AssetType,
    LatestRecommendationsResponse,
    RecommendationItemOut,
    RecommendationRunSummary,
)
from app.services.recommendations.constants import (
    ASSET_TYPE_FUND,
    ASSET_TYPE_STOCK,
    RECOMMENDATION_DISCLAIMER,
    RUN_STATUS_SUCCESS,
    SAFE_LABELS,
)

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


def _validate_asset_type(asset_type: str) -> AssetType:
    if asset_type not in {ASSET_TYPE_FUND, ASSET_TYPE_STOCK}:
        raise HTTPException(status_code=422, detail="asset_type must be fund or stock")
    return asset_type  # type: ignore[return-value]


def _run_summary(run: RecommendationRun) -> RecommendationRunSummary:
    return RecommendationRunSummary(
        id=run.id,
        asset_type=_validate_asset_type(run.asset_type),
        status=run.status,
        as_of_date=run.as_of_date,
        started_at=run.started_at,
        finished_at=run.finished_at,
        data_cutoff=run.data_cutoff_json,
        details=run.details_json,
        error_message=run.error_message,
    )


def _item_out(item: RecommendationItem) -> RecommendationItemOut:
    asset_type = _validate_asset_type(item.asset_type)
    return RecommendationItemOut(
        id=item.id,
        asset_code=item.asset_code,
        asset_name=item.asset_name,
        asset_type=asset_type,
        rank=item.rank,
        total_score=round(item.total_score, 2),
        score_breakdown=item.score_breakdown_json,
        rationale=item.rationale_json,
        risk_flags=item.risk_flags_json,
        data_freshness=item.data_freshness_json,
        safe_label=SAFE_LABELS[asset_type],
    )


async def _items_for_run(session: AsyncSession, run_id: int) -> list[RecommendationItemOut]:
    rows = (
        await session.scalars(
            select(RecommendationItem)
            .where(RecommendationItem.run_id == run_id)
            .order_by(RecommendationItem.rank.asc(), RecommendationItem.asset_code.asc())
        )
    ).all()
    return [_item_out(item) for item in rows]


@router.get("/runs", response_model=list[RecommendationRunSummary])
async def list_recommendation_runs(
    asset_type: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> list[RecommendationRunSummary]:
    query = select(RecommendationRun)
    if asset_type is not None:
        query = query.where(RecommendationRun.asset_type == _validate_asset_type(asset_type))
    rows = (
        await session.scalars(
            query.order_by(
                RecommendationRun.started_at.desc(),
                RecommendationRun.id.desc(),
            ).limit(20)
        )
    ).all()
    return [_run_summary(row) for row in rows]


@router.get("/latest", response_model=LatestRecommendationsResponse)
async def latest_recommendations(
    asset_type: str = Query(default=ASSET_TYPE_FUND),
    session: AsyncSession = Depends(get_db_session),
) -> LatestRecommendationsResponse:
    validated_type = _validate_asset_type(asset_type)
    run = await session.scalar(
        select(RecommendationRun)
        .where(
            RecommendationRun.asset_type == validated_type,
            RecommendationRun.status == RUN_STATUS_SUCCESS,
        )
        .order_by(
            RecommendationRun.as_of_date.desc(),
            RecommendationRun.finished_at.desc(),
            RecommendationRun.id.desc(),
        )
    )
    if run is None:
        return LatestRecommendationsResponse(
            asset_type=validated_type,
            disclaimer=RECOMMENDATION_DISCLAIMER,
            run=None,
            items=[],
        )
    return LatestRecommendationsResponse(
        asset_type=validated_type,
        disclaimer=RECOMMENDATION_DISCLAIMER,
        run=_run_summary(run),
        items=await _items_for_run(session, run.id),
    )


@router.get("/runs/{run_id}", response_model=LatestRecommendationsResponse)
async def get_recommendation_run(
    run_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> LatestRecommendationsResponse:
    run = await session.scalar(select(RecommendationRun).where(RecommendationRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Recommendation run not found")
    return LatestRecommendationsResponse(
        asset_type=_validate_asset_type(run.asset_type),
        disclaimer=RECOMMENDATION_DISCLAIMER,
        run=_run_summary(run),
        items=await _items_for_run(session, run.id),
    )
