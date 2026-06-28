from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.entities import EtfThemeProfile, TradableEtf
from app.services.short_research.dynamic_thresholds import (
    ThresholdPricePoint,
    dynamic_threshold_context,
)
from app.services.short_research.theme_taxonomy import (
    UNKNOWN_THEME,
    classify_etf_theme,
    refresh_etf_theme_profiles,
)


def _points(*, count: int = 80, start: float = 1.0, up: float = 0.01, down: float = -0.006) -> list[ThresholdPricePoint]:
    value = start
    rows: list[ThresholdPricePoint] = []
    for index in range(count):
        move = up if index % 2 == 0 else down
        value *= 1 + move
        rows.append(
            ThresholdPricePoint(
                value=value,
                high=value * (1 + abs(up) / 2),
                low=value * (1 - abs(down) / 2),
                pct_change=move,
            )
        )
    return rows


def test_etf_theme_classifier_uses_manual_override() -> None:
    profile = classify_etf_theme(code="515070", name="人工智能AIETF", asset_class="sector")

    assert profile.primary_theme == "人工智能"
    assert profile.theme_group == "technology"
    assert profile.classification_source == "manual_override"
    assert profile.classification_confidence == "high"


def test_etf_theme_classifier_keeps_unknown_auditable() -> None:
    profile = classify_etf_theme(code="599999", name="测试增强ETF", asset_class="sector")

    assert profile.primary_theme == UNKNOWN_THEME
    assert profile.theme_group == "unknown"
    assert profile.classification_confidence == "unknown"
    assert "没有命中" in profile.classification_reason


def test_dynamic_thresholds_adapt_to_asset_bucket_and_volatility() -> None:
    high_vol = dynamic_threshold_context(
        asset_bucket="equity",
        theme_group="technology",
        points=_points(up=0.035, down=-0.025),
        today_return=0.028,
    )
    low_vol = dynamic_threshold_context(
        asset_bucket="bond",
        theme_group="bond",
        points=_points(up=0.0015, down=-0.001),
        today_return=0.008,
    )

    assert high_vol["threshold_mode"] == "dynamic"
    assert low_vol["threshold_mode"] == "dynamic"
    assert high_vol["thresholds"]["chase_daily"] > low_vol["thresholds"]["chase_daily"]
    assert high_vol["volatility_unit_pct"] > low_vol["volatility_unit_pct"]


def test_dynamic_thresholds_use_conservative_default_for_short_history() -> None:
    context = dynamic_threshold_context(
        asset_bucket="equity",
        theme_group="technology",
        points=_points(count=10),
        today_return=0.02,
    )

    assert context["threshold_mode"] == "conservative_default"
    assert context["decision_eligible"] is False
    assert context["ineligible_reason"] == "insufficient_history"


def test_high_premium_makes_threshold_context_ineligible() -> None:
    context = dynamic_threshold_context(
        asset_bucket="cross_border",
        theme_group="cross_border",
        points=_points(up=0.018, down=-0.01),
        today_return=0.01,
        premium_discount_pct=9.5,
    )

    assert context["premium_state"] == "extreme"
    assert context["decision_eligible"] is False


@pytest.mark.asyncio
async def test_theme_refresh_is_idempotent(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="515070",
                name="人工智能AIETF",
                exchange="SH",
                theme_tags_json=[],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        await session.commit()

        first = await refresh_etf_theme_profiles(session)
        second = await refresh_etf_theme_profiles(session)
        rows = (await session.scalars(select(EtfThemeProfile).where(EtfThemeProfile.etf_code == "515070"))).all()

    assert len(rows) == 1
    assert first["inserted"] >= 1
    assert second["inserted"] == 0
