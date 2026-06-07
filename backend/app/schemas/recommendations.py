from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

AssetType = Literal["fund", "stock"]


class RecommendationRunSummary(BaseModel):
    id: int
    asset_type: AssetType
    status: str
    as_of_date: date
    started_at: datetime
    finished_at: datetime | None
    data_cutoff: dict[str, Any]
    details: dict[str, Any]
    error_message: str | None


class RecommendationItemOut(BaseModel):
    id: int
    asset_code: str
    asset_name: str
    asset_type: AssetType
    rank: int
    total_score: float
    score_breakdown: dict[str, Any]
    rationale: dict[str, Any]
    risk_flags: list[str]
    data_freshness: dict[str, Any]
    safe_label: str


class LatestRecommendationsResponse(BaseModel):
    asset_type: AssetType
    disclaimer: str
    run: RecommendationRunSummary | None
    items: list[RecommendationItemOut]


class RecommendationReviewItemOut(BaseModel):
    id: int
    recommendation_item_id: int
    asset_code: str
    verdict: str
    agent_notes: dict[str, Any]
    risk_flags: list[str]


class RecommendationReviewOut(BaseModel):
    id: int
    run_id: int
    status: str
    model_name: str
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any]
    error_message: str | None
    items: list[RecommendationReviewItemOut]


class LatestRecommendationsWithReviewResponse(LatestRecommendationsResponse):
    review: RecommendationReviewOut | None
