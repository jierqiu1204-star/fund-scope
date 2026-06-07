from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import (
    RecommendationItem,
    RecommendationReview,
    RecommendationReviewItem,
    RecommendationRun,
)
from app.schemas.recommendations import (
    AssetType,
    LatestRecommendationsResponse,
    LatestRecommendationsWithReviewResponse,
    RecommendationItemOut,
    RecommendationReviewItemOut,
    RecommendationReviewOut,
    RecommendationRunSummary,
)
from app.services.recommendations.constants import (
    ASSET_TYPE_FUND,
    ASSET_TYPE_STOCK,
    RECOMMENDATION_DISCLAIMER,
    RUN_STATUS_SUCCESS,
    SAFE_LABELS,
)
from app.services.recommendations.reviews import (
    generate_review_for_run,
    get_review_for_run,
    list_review_items,
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


def _review_item_out(item: RecommendationReviewItem) -> RecommendationReviewItemOut:
    return RecommendationReviewItemOut(
        id=item.id,
        recommendation_item_id=item.recommendation_item_id,
        asset_code=item.asset_code,
        verdict=item.verdict,
        agent_notes=item.agent_notes_json,
        risk_flags=item.risk_flags_json,
    )


async def _review_out(session: AsyncSession, review: RecommendationReview) -> RecommendationReviewOut:
    return RecommendationReviewOut(
        id=review.id,
        run_id=review.run_id,
        status=review.status,
        model_name=review.model_name,
        started_at=review.started_at,
        finished_at=review.finished_at,
        summary=review.summary_json,
        error_message=review.error_message,
        items=[_review_item_out(item) for item in await list_review_items(session, review.id)],
    )


async def _latest_successful_run(session: AsyncSession, asset_type: AssetType) -> RecommendationRun | None:
    run = await session.scalar(
        select(RecommendationRun)
        .where(
            RecommendationRun.asset_type == asset_type,
            RecommendationRun.status == RUN_STATUS_SUCCESS,
        )
        .order_by(
            RecommendationRun.as_of_date.desc(),
            RecommendationRun.finished_at.desc(),
            RecommendationRun.id.desc(),
        )
    )
    return cast(RecommendationRun | None, run)


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
    run = await _latest_successful_run(session, validated_type)
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


@router.get("/latest-with-review", response_model=LatestRecommendationsWithReviewResponse)
async def latest_recommendations_with_review(
    asset_type: str = Query(default=ASSET_TYPE_FUND),
    session: AsyncSession = Depends(get_db_session),
) -> LatestRecommendationsWithReviewResponse:
    validated_type = _validate_asset_type(asset_type)
    run = await _latest_successful_run(session, validated_type)
    if run is None:
        return LatestRecommendationsWithReviewResponse(
            asset_type=validated_type,
            disclaimer=RECOMMENDATION_DISCLAIMER,
            run=None,
            items=[],
            review=None,
        )
    review = await get_review_for_run(session, run.id)
    return LatestRecommendationsWithReviewResponse(
        asset_type=validated_type,
        disclaimer=RECOMMENDATION_DISCLAIMER,
        run=_run_summary(run),
        items=await _items_for_run(session, run.id),
        review=await _review_out(session, review) if review is not None else None,
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


@router.post("/runs/{run_id}/review", response_model=RecommendationReviewOut)
async def create_recommendation_review(
    run_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> RecommendationReviewOut:
    run = await session.scalar(select(RecommendationRun).where(RecommendationRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Recommendation run not found")
    try:
        review = await generate_review_for_run(session, run)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _review_out(session, review)


@router.get("/runs/{run_id}/review", response_model=RecommendationReviewOut)
async def get_recommendation_review(
    run_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> RecommendationReviewOut:
    run = await session.scalar(select(RecommendationRun).where(RecommendationRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Recommendation run not found")
    review = await get_review_for_run(session, run.id)
    if review is None:
        raise HTTPException(status_code=404, detail="Recommendation review not found")
    return await _review_out(session, review)
