from __future__ import annotations

from datetime import date

import pytest

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import ShortResearchSignalItem
from app.services.short_research.ranking import (
    FINAL_SCORE_VERSION,
    RankingRecord,
    apply_final_score_limits,
    apply_label_evidence,
    build_final_score_breakdowns,
    percentile_rank,
)
from app.services.short_research.service import (
    ComputedAsset,
    _cached_asset_from_signal_item,
    _portfolio_exposure_for_asset,
    _sort_key,
)


def _record(
    code: str,
    *,
    return_20d: float,
    drawdown: float,
    volatility: float,
    turnover: float,
    bucket: str = "equity",
    group: str = "technology",
    entry_label: str = "趋势延续",
    premium_state: str = "normal",
    decision_eligible: bool = True,
    default_display_eligible: bool = True,
    risk_flags: list[str] | None = None,
) -> RankingRecord:
    return RankingRecord(
        code=code,
        risk_flags=risk_flags or [],
        metrics={
            "return_5d": return_20d / 4,
            "return_20d": return_20d,
            "return_60d": return_20d * 1.8,
            "max_drawdown_60d": drawdown,
            "volatility_20d": volatility,
            "average_turnover_20d": turnover,
            "distance_to_ma5_pct": return_20d / 6,
            "data_quality_score": 100,
            "default_display_eligible": default_display_eligible,
            "entry_timing_label": entry_label,
            "theme_profile": {"asset_bucket": bucket, "theme_group": group},
            "dynamic_threshold_context": {
                "threshold_mode": "dynamic",
                "asset_bucket": bucket,
                "theme_group": group,
                "decision_eligible": decision_eligible,
                "premium_state": premium_state,
            },
        },
    )


def _portfolio_asset(*, validation_confidence: str | None = None) -> ComputedAsset:
    metrics = {
        "default_display_eligible": True,
        "volatility_20d": 0.01,
        "max_drawdown_60d": -0.04,
    }
    if validation_confidence is not None:
        metrics["validation_confidence"] = validation_confidence
    return ComputedAsset(
        metadata=ShortResearchAsset(
            asset_type=ASSET_TYPE_ETF,
            code="510300",
            name="沪深300ETF",
            category="broad_index",
            theme_tags=("宽基",),
            investment_direction="沪深300",
            trading_rule_label="T+1",
        ),
        rank=1,
        total_score=80.0,
        conclusion="短线观察",
        latest_date=date(2026, 1, 2),
        latest_value=1.0,
        usable_days=120,
        sample_level="充足",
        metrics=metrics,
        score_breakdown={},
        risk_flags=[],
        rationale={},
        source_note="fixture",
        entry_timing_label="趋势延续",
        entry_timing_reason="fixture",
    )


def test_percentile_rank_is_tie_stable_and_null_safe() -> None:
    assert percentile_rank(None, [1, 2, 3]) is None
    assert percentile_rank(2, [1, 2, 2, 3]) == 50.0
    assert percentile_rank(3, [1, 2, 3], higher_is_better=False) == 0.0


def test_validation_evidence_cannot_reduce_portfolio_exposure() -> None:
    baseline = _portfolio_exposure_for_asset(_portfolio_asset())
    evidence_tagged = _portfolio_exposure_for_asset(_portfolio_asset(validation_confidence="insufficient"))

    assert evidence_tagged == baseline


@pytest.mark.parametrize(
    ("risk_flags", "unavailable", "expected"),
    [
        (["数据不足"], False, 35.0),
        (["数据滞后"], False, 55.0),
        ([], True, 45.0),
    ],
)
def test_final_score_limits_are_shared_and_fail_closed(
    risk_flags: list[str],
    unavailable: bool,
    expected: float,
) -> None:
    score, limitations = apply_final_score_limits(99.0, risk_flags=risk_flags, unavailable=unavailable)

    assert score == expected
    assert limitations


@pytest.mark.parametrize(
    "risk_flag",
    ["数据不足", "数据滞后", "追高风险", "连续大涨", "高波动", "回撤较大", "流动性不足"],
)
def test_adding_any_risk_flag_never_raises_final_score(risk_flag: str) -> None:
    baseline = build_final_score_breakdowns(
        [_record("510300", return_20d=0.08, drawdown=-0.04, volatility=0.02, turnover=300_000_000)]
    )["510300"]["final_score"]
    with_risk = build_final_score_breakdowns(
        [_record("510300", return_20d=0.08, drawdown=-0.04, volatility=0.02, turnover=300_000_000, risk_flags=[risk_flag])]
    )["510300"]["final_score"]

    assert with_risk <= baseline


@pytest.mark.parametrize(
    "return_20d,drawdown,volatility,turnover",
    [
        (0.20, -0.01, 0.01, 500_000_000),
        (0.08, -0.04, 0.02, 300_000_000),
        (-0.15, -0.25, 0.05, 10_000_000),
    ],
)
def test_hard_limits_survive_all_soft_component_inputs(
    return_20d: float,
    drawdown: float,
    volatility: float,
    turnover: float,
) -> None:
    stale = build_final_score_breakdowns(
        [_record("510300", return_20d=return_20d, drawdown=drawdown, volatility=volatility, turnover=turnover, risk_flags=["数据滞后"])]
    )["510300"]
    unavailable = build_final_score_breakdowns(
        [
            _record(
                "510301",
                return_20d=return_20d,
                drawdown=drawdown,
                volatility=volatility,
                turnover=turnover,
                default_display_eligible=False,
            )
        ]
    )["510301"]

    assert stale["final_score"] <= 55
    assert unavailable["final_score"] <= 45


def test_final_score_v2_separates_metric_profiles() -> None:
    records = [
        _record("510001", return_20d=0.12, drawdown=-0.03, volatility=0.012, turnover=500_000_000),
        _record("510002", return_20d=0.04, drawdown=-0.12, volatility=0.035, turnover=60_000_000),
        _record("510003", return_20d=0.07, drawdown=-0.06, volatility=0.020, turnover=200_000_000),
    ]

    scores = build_final_score_breakdowns(records)

    assert scores["510001"]["score_version"] == FINAL_SCORE_VERSION
    assert scores["510001"]["final_score"] > scores["510002"]["final_score"]
    assert scores["510001"]["components"]["cross_sectional_percentile"]["score"] > scores["510002"]["components"]["cross_sectional_percentile"]["score"]
    assert "label_evidence" not in scores["510001"]["weights"]


def test_dynamic_threshold_and_premium_penalize_ineligible_etf() -> None:
    records = [
        _record("513520", return_20d=0.10, drawdown=-0.04, volatility=0.020, turnover=300_000_000),
        _record(
            "513521",
            return_20d=0.11,
            drawdown=-0.04,
            volatility=0.020,
            turnover=300_000_000,
            premium_state="extreme",
            decision_eligible=False,
        ),
    ]

    scores = build_final_score_breakdowns(records)

    assert scores["513521"]["components"]["dynamic_threshold"]["score"] < scores["513520"]["components"]["dynamic_threshold"]["score"]
    assert scores["513521"]["components"]["liquidity_premium"]["score"] < scores["513520"]["components"]["liquidity_premium"]["score"]


def test_unavailable_data_cannot_improve_final_score() -> None:
    records = [
        _record("588001", return_20d=0.15, drawdown=-0.04, volatility=0.020, turnover=300_000_000, risk_flags=["数据滞后"]),
        _record("588002", return_20d=0.02, drawdown=-0.04, volatility=0.020, turnover=300_000_000),
    ]

    scores = build_final_score_breakdowns(records)

    assert scores["588001"]["final_score"] <= 55
    assert scores["588001"]["components"]["data_reliability"]["reliability"] == "stale"


def test_label_evidence_is_display_only() -> None:
    base = build_final_score_breakdowns(
        [_record("515000", return_20d=0.08, drawdown=-0.04, volatility=0.020, turnover=300_000_000)]
    )["515000"]

    boosted = apply_label_evidence(base, confidence="sufficient", sample_count=80, median_return=0.01, win_rate=0.58)
    weakened = apply_label_evidence(base, confidence="recent_weakening", sample_count=80, median_return=-0.01, win_rate=0.42)
    insufficient = apply_label_evidence(base, confidence="sufficient", sample_count=5)

    assert boosted["final_score"] == insufficient["final_score"]
    assert weakened["final_score"] == insufficient["final_score"]
    assert boosted["components"]["label_evidence"]["sample_count"] == 80
    assert boosted["components"]["label_evidence"]["display_only"] is True
    assert boosted["components"]["label_evidence"]["score_contribution"] == 0.0
    assert "score" not in boosted["components"]["label_evidence"]


def test_stale_cap_is_reapplied_after_label_evidence_enrichment() -> None:
    stale = build_final_score_breakdowns(
        [_record("588003", return_20d=0.20, drawdown=-0.02, volatility=0.01, turnover=500_000_000, risk_flags=["数据滞后"])]
    )["588003"]

    enriched = apply_label_evidence(stale, confidence="sufficient", sample_count=100, median_return=0.08, win_rate=0.9)

    assert enriched["final_score"] <= 55


def test_unavailable_cap_is_reapplied_after_label_evidence_enrichment() -> None:
    unavailable = build_final_score_breakdowns(
        [_record("588004", return_20d=0.20, drawdown=-0.02, volatility=0.01, turnover=500_000_000, risk_flags=["数据不足"])]
    )["588004"]

    enriched = apply_label_evidence(unavailable, confidence="sufficient", sample_count=100, median_return=0.08, win_rate=0.9)

    assert enriched["final_score"] <= 45


@pytest.mark.parametrize(
    ("confidence", "median_return", "win_rate"),
    [
        ("sufficient", -0.02, 0.3),
        ("limited", 0.0, 0.5),
        ("recent_weakening", -0.01, 0.42),
        ("sufficient", 0.08, 0.9),
    ],
    ids=["negative", "inconclusive", "stale", "high-sample"],
)
def test_label_evidence_is_display_only_and_cannot_change_current_score_or_rank(
    confidence: str,
    median_return: float,
    win_rate: float,
) -> None:
    evidence_candidate = {
        "final_score": 68.8,
        "components": {"base": {"score": 70.0}, "label_evidence": {"score": 60.0}},
        "weights": {"base": 0.88, "label_evidence": 0.12},
    }
    higher_candidate = {"final_score": 69.0}
    baseline_rank = ["higher", "evidence"]

    enriched = apply_label_evidence(
        evidence_candidate,
        confidence=confidence,
        sample_count=100,
        median_return=median_return,
        win_rate=win_rate,
    )
    enriched_rank = [
        code
        for code, _ in sorted(
            (("higher", higher_candidate), ("evidence", enriched)),
            key=lambda item: item[1]["final_score"],
            reverse=True,
        )
    ]

    assert enriched["final_score"] == evidence_candidate["final_score"]
    assert enriched_rank == baseline_rank
    assert enriched["components"]["label_evidence"]["score_contribution"] == 0.0
    assert "label_evidence" not in enriched["weights"]


def test_legacy_cached_signal_is_marked_as_old_scoring() -> None:
    item = ShortResearchSignalItem(
        asset_type=ASSET_TYPE_ETF,
        asset_code="510300",
        rank=1,
        total_score=80,
        conclusion="短线观察",
        score_breakdown_json={"trend": {"score": 80}},
        risk_flags_json=[],
        rationale_json={},
        metrics_json={
            "entry_timing_label": "趋势延续",
            "entry_timing_reason": "测试",
            "latest_date": "2026-06-30",
            "latest_value": 1.23,
            "usable_days": 120,
        },
    )
    metadata = ShortResearchAsset(
        ASSET_TYPE_ETF,
        "510300",
        "沪深300ETF",
        "broad_index",
        ("宽基",),
        "沪深300",
        "T+1股票ETF",
        exchange="SH",
    )

    asset = _cached_asset_from_signal_item(item, metadata, as_of_date=date(2026, 6, 30))

    assert asset.metrics["score_version"] == "legacy"
    assert asset.score_breakdown["final_score_v2"]["score_version"] == "legacy"


def test_opportunity_sort_uses_final_decision_score_not_theme_heat() -> None:
    metadata = ShortResearchAsset(
        ASSET_TYPE_ETF,
        "510300",
        "沪深300ETF",
        "broad_index",
        ("宽基",),
        "沪深300",
        "T+1股票ETF",
        exchange="SH",
    )
    hot_theme = _cached_asset_from_signal_item(
        ShortResearchSignalItem(
            asset_type=ASSET_TYPE_ETF,
            asset_code="510301",
            rank=1,
            total_score=50,
            conclusion="高位观察",
            score_breakdown_json={"final_score_v2": {"final_score": 50, "score_version": FINAL_SCORE_VERSION}},
            risk_flags_json=[],
            rationale_json={},
            metrics_json={
                "opportunity_score": 95,
                "opportunity_score_version": "opportunity_score_v2_full",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "主题热但买点不好",
                "latest_date": "2026-06-30",
                "latest_value": 1.23,
                "usable_days": 120,
            },
        ),
        metadata,
        as_of_date=date(2026, 6, 30),
    )
    safer_final = _cached_asset_from_signal_item(
        ShortResearchSignalItem(
            asset_type=ASSET_TYPE_ETF,
            asset_code="510302",
            rank=2,
            total_score=80,
            conclusion="短线观察",
            score_breakdown_json={"final_score_v2": {"final_score": 80, "score_version": FINAL_SCORE_VERSION}},
            risk_flags_json=[],
            rationale_json={},
            metrics_json={
                "opportunity_score": 60,
                "opportunity_score_version": "opportunity_score_v2_full",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "最终分更好",
                "latest_date": "2026-06-30",
                "latest_value": 1.23,
                "usable_days": 120,
            },
        ),
        metadata,
        as_of_date=date(2026, 6, 30),
    )

    assert _sort_key(safer_final, "opportunity") > _sort_key(hot_theme, "opportunity")
