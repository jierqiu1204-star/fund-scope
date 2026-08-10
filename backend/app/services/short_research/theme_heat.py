from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol


class _AssetMetadata(Protocol):
    asset_type: str
    code: str
    name: str


class ThemeHeatAsset(Protocol):
    metadata: _AssetMetadata
    metrics: Mapping[str, Any]
    rationale: Mapping[str, Any]
    total_score: float


def theme_heat_summary(
    assets: Iterable[ThemeHeatAsset],
    *,
    scores_by_code: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate immutable ranking assets without re-reading market data."""

    groups: dict[str, dict[str, Any]] = {}
    score_overrides = scores_by_code or {}
    for asset in assets:
        if asset.metadata.asset_type != "etf":
            continue
        profile = dict(
            asset.metrics.get("theme_profile")
            or asset.rationale.get("theme_profile")
            or {}
        )
        primary_theme = str(profile.get("primary_theme") or "").strip() or "未分类"
        if primary_theme == "未分类":
            primary_theme = str(profile.get("theme_group") or "未分类")
        score = float(score_overrides.get(asset.metadata.code, asset.total_score))
        bucket = groups.setdefault(
            primary_theme,
            {
                "theme": primary_theme,
                "count": 0,
                "score_sum": 0.0,
                "change_sum": 0.0,
                "change_count": 0,
                "top_asset": None,
                "top_score": 0.0,
            },
        )
        bucket["count"] += 1
        bucket["score_sum"] += score
        today_return = asset.metrics.get("today_return_pct")
        if isinstance(today_return, (int, float)):
            bucket["change_sum"] += float(today_return)
            bucket["change_count"] += 1
        if score >= float(bucket["top_score"]):
            bucket["top_score"] = round(score, 2)
            bucket["top_asset"] = {
                "code": asset.metadata.code,
                "name": asset.metadata.name,
            }

    result: list[dict[str, Any]] = []
    for item in groups.values():
        count = max(int(item["count"]), 1)
        change_count = int(item["change_count"])
        result.append(
            {
                "theme": item["theme"],
                "count": count,
                "avg_score": round(float(item["score_sum"]) / count, 2),
                "avg_today_return": (
                    round(float(item["change_sum"]) / change_count, 4)
                    if change_count
                    else None
                ),
                "top_score": item["top_score"],
                "top_asset": item["top_asset"],
            }
        )
    return sorted(
        result,
        key=lambda item: (item["avg_score"], item["count"]),
        reverse=True,
    )[:12]
