from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.services.short_research.theme_catalysts import (
    build_asset_opportunity_payload,
    score_theme_catalysts,
)


def _event(
    *,
    status: str = "active",
    source_url: str | None = "https://example.com/source",
    event_date: date | None = date(2026, 7, 1),
    effective_start: date | None = date(2026, 7, 1),
    effective_end: date | None = date(2026, 12, 31),
    strength_score: float = 90,
    confidence_score: float = 80,
    metadata_json: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        theme_key="机器人",
        theme_name="机器人/具身智能",
        catalyst_type="ipo",
        title="测试催化",
        summary="测试催化摘要",
        source_url=source_url,
        event_date=event_date,
        effective_start=effective_start,
        effective_end=effective_end,
        strength_score=strength_score,
        confidence_score=confidence_score,
        status=status,
        source_type="manual_seed",
        metadata_json=metadata_json or {},
    )


def test_score_theme_catalysts_ignores_unverified_pending_and_expired_events() -> None:
    scored = score_theme_catalysts(
        [
            _event(source_url=None),
            _event(status="pending"),
            _event(effective_end=date(2026, 1, 31)),
        ],
        as_of_date=date(2026, 7, 3),
        theme_key="机器人",
        theme_name="机器人/具身智能",
    )

    assert scored["status"] == "unavailable"
    assert scored["catalyst_score"] == 50.0
    assert scored["event_count"] == 0
    assert scored["score_breakdown"]["skipped"]["expired"] == 1
    assert scored["score_breakdown"]["skipped"]["pending"] == 1
    assert scored["score_breakdown"]["skipped"]["unverified"] == 1


def test_score_theme_catalysts_caps_proxy_theme_confidence() -> None:
    scored = score_theme_catalysts(
        [_event(metadata_json={"proxy_theme": True}, strength_score=95, confidence_score=95)],
        as_of_date=date(2026, 7, 3),
        theme_key="光模块_proxy",
        theme_name="光模块/CPO 代理主题",
    )

    assert scored["status"] == "success"
    assert scored["catalyst_score"] <= 75
    assert scored["sentiment_heat_score"] <= 75
    assert any("代理 ETF" in item for item in scored["limitations"])


def test_opportunity_score_lifts_attention_without_removing_chase_risk() -> None:
    snapshot = SimpleNamespace(
        theme_key="机器人",
        theme_name="机器人/具身智能",
        status="success",
        catalyst_score=90.0,
        sentiment_heat_score=80.0,
        key_events_json=[{"summary": "宇树科技 IPO 催化机器人主题。"}],
        limitations_json=[],
        score_breakdown_json={"score_version": "theme_catalyst_v1"},
    )

    payload = build_asset_opportunity_payload(
        asset_name="机器人ETF",
        theme_tags=["机器人"],
        metrics={
            "entry_timing_label": "冲高别追",
            "theme_profile": {"primary_theme": "人工智能", "secondary_themes": ["机器人"]},
            "default_display_eligible": True,
        },
        risk_flags=[],
        technical_score=70,
        snapshots_by_key={"机器人": snapshot},
    )

    assert payload["metrics"]["opportunity_score"] == 75.0
    assert payload["metrics"]["opportunity_label"] == "主题强但等买点"
    assert any("冲高别追" in item for item in payload["metrics"]["catalyst_limitations"])


def test_opportunity_score_uses_full_evidence_with_sector_trend() -> None:
    snapshot = SimpleNamespace(
        theme_key="机器人",
        theme_name="机器人/具身智能",
        status="success",
        catalyst_score=90.0,
        sentiment_heat_score=60.0,
        key_events_json=[{"summary": "机器人主题催化。"}],
        limitations_json=[],
        score_breakdown_json={"score_version": "theme_catalyst_v1"},
    )

    payload = build_asset_opportunity_payload(
        asset_name="机器人ETF",
        theme_tags=["机器人"],
        metrics={
            "entry_timing_label": "趋势延续",
            "theme_profile": {"primary_theme": "机器人"},
            "default_display_eligible": True,
            "sector_trend_score": 80.0,
            "sector_trend_label": "板块强势",
            "sector_trend_status": "success",
        },
        risk_flags=[],
        technical_score=70,
        snapshots_by_key={"机器人": snapshot},
    )

    assert payload["metrics"]["opportunity_score"] == 74.5
    assert payload["metrics"]["opportunity_score_version"] == "opportunity_score_v2_full"
    assert payload["breakdown"]["weights"] == {
        "technical": 0.60,
        "sector_trend": 0.20,
        "theme_catalyst": 0.15,
        "news_sentiment_heat": 0.05,
    }


def test_opportunity_score_uses_sector_trend_when_catalyst_is_unavailable() -> None:
    payload = build_asset_opportunity_payload(
        asset_name="创新药ETF",
        theme_tags=["创新药"],
        metrics={
            "entry_timing_label": "趋势延续",
            "theme_profile": {"primary_theme": "创新药"},
            "default_display_eligible": True,
            "sector_trend_score": 84.0,
            "sector_trend_label": "板块强势",
            "sector_trend_status": "success",
        },
        risk_flags=[],
        technical_score=76,
        snapshots_by_key={},
    )

    assert payload["metrics"]["opportunity_score"] == 78.0
    assert payload["metrics"]["opportunity_label"] == "板块强但等催化"
    assert payload["metrics"]["catalyst_status"] == "unavailable"
    assert payload["metrics"]["opportunity_score_version"] == "opportunity_score_v2_sector_only"
    assert payload["breakdown"]["weights"] == {"technical": 0.75, "sector_trend": 0.25}


def test_opportunity_score_is_unavailable_without_sector_or_catalyst_evidence() -> None:
    payload = build_asset_opportunity_payload(
        asset_name="普通ETF",
        theme_tags=["宽基"],
        metrics={
            "entry_timing_label": "趋势延续",
            "theme_profile": {"primary_theme": "宽基"},
            "default_display_eligible": True,
        },
        risk_flags=[],
        technical_score=88,
        snapshots_by_key={},
    )

    assert payload["metrics"]["opportunity_score"] is None
    assert payload["metrics"]["opportunity_label"] == "暂无主题辅助"
    assert payload["metrics"]["opportunity_score_version"] == "opportunity_score_v2_unavailable"


def test_opportunity_score_waits_when_data_or_liquidity_is_limited() -> None:
    payload = build_asset_opportunity_payload(
        asset_name="机器人ETF",
        theme_tags=["机器人"],
        metrics={"entry_timing_label": "趋势延续", "theme_profile": {}, "default_display_eligible": False},
        risk_flags=["流动性不足"],
        technical_score=72,
        snapshots_by_key={},
    )

    assert payload["metrics"]["opportunity_label"] == "等待数据"
    assert payload["metrics"]["catalyst_score"] == 50.0
    assert any("限制" in item for item in payload["metrics"]["catalyst_limitations"])
