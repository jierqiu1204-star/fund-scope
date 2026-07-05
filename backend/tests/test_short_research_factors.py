from __future__ import annotations

from app.services.short_research.factors import (
    AVAILABILITY_DISPLAY_ONLY,
    AVAILABILITY_INSUFFICIENT,
    AVAILABILITY_STALE,
    AVAILABILITY_UNAVAILABLE,
    FACTOR_GROUPS,
    FACTOR_PROFILE_DEGRADED_VERSION,
    FACTOR_PROFILE_FULL_VERSION,
    FACTOR_PROFILE_UNAVAILABLE_VERSION,
    RELIABILITY_ESTIMATED,
    RELIABILITY_SEED_ONLY,
    RELIABILITY_STALE,
    available_factor,
    build_asset_factor_payload,
    factor_definitions,
    unavailable_factor,
)


def test_factor_registry_covers_required_groups_with_stable_metadata() -> None:
    definitions = factor_definitions()
    groups = {item["group"] for item in definitions}

    assert set(FACTOR_GROUPS).issubset(groups)
    assert len({item["factor_id"] for item in definitions}) == len(definitions)
    for item in definitions:
        assert item["factor_id"]
        assert item["label"]
        assert item["window"]
        assert item["direction"]
        assert item["usage"] in {"score", "penalty", "gate", "display"}
        assert item["source_type"]
        assert item["description"]


def test_factor_result_serializes_availability_and_reliability_states() -> None:
    available = available_factor(
        "price_momentum_score",
        score=82.45,
        source="test",
        reason="可用",
        as_of_date="2026-07-03",
    ).as_dict()
    assert available["score"] == 82.45
    assert available["availability"] == "available"
    assert available["decision_eligible"] is True

    states = [
        (AVAILABILITY_INSUFFICIENT, "unavailable"),
        (AVAILABILITY_STALE, RELIABILITY_STALE),
        (AVAILABILITY_UNAVAILABLE, "unavailable"),
        (AVAILABILITY_DISPLAY_ONLY, RELIABILITY_SEED_ONLY),
        (AVAILABILITY_DISPLAY_ONLY, RELIABILITY_ESTIMATED),
    ]
    for availability, reliability in states:
        result = unavailable_factor(
            "fund_flow_score",
            reason="测试缺失",
            availability=availability,
            reliability=reliability,
            raw_value=66,
        ).as_dict()
        assert result["score"] is None
        assert result["availability"] == availability
        assert result["reliability"] == reliability
        assert result["reason"] == "测试缺失"
        assert result["decision_eligible"] is False


def test_factor_profile_uses_full_profile_when_all_scoring_groups_are_available() -> None:
    payload = build_asset_factor_payload(
        asset_name="机器人ETF",
        theme_tags=["机器人"],
        technical_score=70,
        risk_flags=[],
        metrics={
            "technical_score": 70,
            "risk_score": 88,
            "liquidity_score": 76,
            "default_display_eligible": True,
            "sector_trend_status": "success",
            "sector_trend_score": 80,
            "sector_trend_summary": "机器人板块趋势强。",
            "catalyst_status": "success",
            "catalyst_score": 90,
            "catalyst_summary": "机器人主题事件可用。",
            "sentiment_heat_score": 72,
            "fund_flow_score": 74,
            "fund_flow_reliability": "verified",
            "fund_flow_source": "test_flow",
            "constituent_breadth_score": 82,
            "constituent_breadth_reliability": "verified",
            "valuation_percentile_score": 60,
            "valuation_reliability": "verified",
            "macro_style_score": 55,
            "macro_style_reliability": "verified",
            "etf_structure_score": 78,
        },
    )

    profile = payload["metrics"]["opportunity_breakdown"]
    assert profile["profile_version"] == FACTOR_PROFILE_FULL_VERSION
    assert profile["score"] is not None
    assert round(sum(profile["weights"].values()), 6) == 1
    assert "sentiment_heat" not in profile["included_groups"]
    assert payload["metrics"]["factor_group_scores"]["sentiment_heat"]["availability"] == "display_only"


def test_factor_profile_degrades_and_waits_when_only_technical_data_exists() -> None:
    sector_only = build_asset_factor_payload(
        asset_name="创新药ETF",
        theme_tags=["创新药"],
        technical_score=76,
        risk_flags=[],
        metrics={
            "technical_score": 76,
            "risk_score": 90,
            "liquidity_score": 82,
            "default_display_eligible": True,
            "sector_trend_status": "success",
            "sector_trend_score": 84,
            "sector_trend_summary": "创新药板块趋势强。",
            "catalyst_status": "unavailable",
            "catalyst_summary": "暂无可用于评分的主题催化事件。",
        },
    )
    assert sector_only["metrics"]["factor_profile_version"] == FACTOR_PROFILE_DEGRADED_VERSION
    assert sector_only["metrics"]["factor_profile_score"] is not None

    technical_only = build_asset_factor_payload(
        asset_name="普通ETF",
        theme_tags=["宽基"],
        technical_score=90,
        risk_flags=[],
        metrics={
            "technical_score": 90,
            "risk_score": 90,
            "liquidity_score": 90,
            "default_display_eligible": True,
            "sector_trend_status": "unavailable",
            "catalyst_status": "unavailable",
        },
    )
    assert technical_only["metrics"]["factor_profile_version"] == FACTOR_PROFILE_UNAVAILABLE_VERSION
    assert technical_only["metrics"]["factor_profile_score"] is None
    assert technical_only["metrics"]["opportunity_breakdown"]["score"] is None


def test_risk_gates_survive_strong_positive_factors() -> None:
    payload = build_asset_factor_payload(
        asset_name="机器人ETF",
        theme_tags=["机器人"],
        technical_score=92,
        risk_flags=["追高风险", "流动性不足", "数据滞后", "回撤较大"],
        metrics={
            "technical_score": 92,
            "entry_timing_label": "冲高别追",
            "risk_score": 35,
            "liquidity_score": 20,
            "default_display_eligible": False,
            "sector_trend_status": "success",
            "sector_trend_score": 88,
            "sector_trend_summary": "机器人板块强势。",
            "catalyst_status": "success",
            "catalyst_score": 91,
            "catalyst_summary": "机器人主题事件可用。",
            "sentiment_heat_score": 80,
        },
    )

    active_gate_ids = {
        item["gate_id"]
        for item in payload["metrics"]["risk_gates"]
        if item["active"]
    }
    assert {"overheat", "low_liquidity", "stale_data", "high_drawdown", "etf_structure"}.issubset(
        active_gate_ids
    )
    assert payload["metrics"]["factor_profile_status"] == "gated"


def test_factor_payload_schema_is_consistent_for_theme_etf_fixtures() -> None:
    fixtures = [
        ("机器人ETF", ["机器人"]),
        ("港股创新药ETF", ["创新药"]),
        ("半导体ETF", ["半导体"]),
        ("光模块CPO ETF", ["光模块"]),
        ("沪深300ETF", ["宽基"]),
        ("未分类ETF", []),
    ]

    for name, tags in fixtures:
        payload = build_asset_factor_payload(
            asset_name=name,
            theme_tags=tags,
            technical_score=70,
            risk_flags=[],
            metrics={
                "technical_score": 70,
                "risk_score": 80,
                "liquidity_score": 78,
                "default_display_eligible": True,
                "sector_trend_status": "unavailable",
                "sector_trend_reason": "测试暂无板块趋势。",
                "catalyst_status": "unavailable",
                "catalyst_summary": "测试暂无主题催化。",
            },
        )
        metrics = payload["metrics"]
        assert set(metrics["factor_group_scores"]) == set(FACTOR_GROUPS)
        assert "factor_scores" in metrics
        assert "factor_availability" in metrics
        assert "risk_gates" in metrics
        assert metrics["factor_profile_version"] == FACTOR_PROFILE_UNAVAILABLE_VERSION
