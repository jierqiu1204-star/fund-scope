from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

FACTOR_PROFILE_VERSION = "etf_factor_profile_v1"
FACTOR_PROFILE_FULL_VERSION = "etf_factor_profile_v1_full"
FACTOR_PROFILE_DEGRADED_VERSION = "etf_factor_profile_v1_degraded"
FACTOR_PROFILE_UNAVAILABLE_VERSION = "etf_factor_profile_v1_unavailable"

AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_INSUFFICIENT = "insufficient_data"
AVAILABILITY_STALE = "stale"
AVAILABILITY_UNAVAILABLE = "unavailable"
AVAILABILITY_DISPLAY_ONLY = "display_only"

RELIABILITY_VERIFIED = "verified"
RELIABILITY_ALTERNATE = "alternate_provider"
RELIABILITY_SEED_ONLY = "seed_only"
RELIABILITY_ESTIMATED = "estimated"
RELIABILITY_STALE = "stale"
RELIABILITY_UNAVAILABLE = "unavailable"

USAGE_SCORE = "score"
USAGE_PENALTY = "penalty"
USAGE_GATE = "gate"
USAGE_DISPLAY = "display"

DIRECTION_HIGHER = "higher_is_better"
DIRECTION_LOWER = "lower_is_better"
DIRECTION_BAND = "band_is_better"
DIRECTION_RISK = "risk_only"

FACTOR_GROUPS: tuple[str, ...] = (
    "price_momentum",
    "reversal_overheat",
    "volatility_risk",
    "liquidity",
    "sector_trend",
    "theme_event",
    "fund_flow",
    "sentiment_heat",
    "constituent_breadth",
    "fundamentals_quality",
    "valuation",
    "macro_style",
    "etf_structure",
)

FACTOR_GROUP_LABELS: dict[str, str] = {
    "price_momentum": "价格动量",
    "reversal_overheat": "反转/过热",
    "volatility_risk": "波动风险",
    "liquidity": "流动性",
    "sector_trend": "板块趋势",
    "theme_event": "主题事件",
    "fund_flow": "资金流",
    "sentiment_heat": "情绪热度",
    "constituent_breadth": "成分/同主题扩散",
    "fundamentals_quality": "基本面质量",
    "valuation": "估值",
    "macro_style": "宏观风格",
    "etf_structure": "ETF结构",
}

DECISION_RELIABILITIES = {RELIABILITY_VERIFIED, RELIABILITY_ALTERNATE}
ALPHA_GROUPS = {
    "sector_trend",
    "theme_event",
    "fund_flow",
    "constituent_breadth",
    "valuation",
    "macro_style",
}
PROFILE_BASE_WEIGHTS: dict[str, float] = {
    "price_momentum": 0.30,
    "sector_trend": 0.15,
    "theme_event": 0.10,
    "fund_flow": 0.15,
    "constituent_breadth": 0.10,
    "valuation": 0.05,
    "macro_style": 0.05,
    "liquidity": 0.05,
    "etf_structure": 0.05,
}


@dataclass(frozen=True)
class FactorDefinition:
    factor_id: str
    group: str
    label: str
    window: str
    direction: str
    usage: str
    source_type: str
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "group": self.group,
            "group_label": FACTOR_GROUP_LABELS.get(self.group, self.group),
            "label": self.label,
            "window": self.window,
            "direction": self.direction,
            "usage": self.usage,
            "source_type": self.source_type,
            "description": self.description,
        }


@dataclass(frozen=True)
class FactorResult:
    definition: FactorDefinition
    score: float | None
    availability: str
    reliability: str
    source: str
    reason: str
    as_of_date: date | str | None = None
    raw_value: Any = None
    components: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        as_of_value: str | None
        if isinstance(self.as_of_date, date):
            as_of_value = self.as_of_date.isoformat()
        elif self.as_of_date is None:
            as_of_value = None
        else:
            as_of_value = str(self.as_of_date)
        return {
            **self.definition.as_dict(),
            "score": self.score,
            "availability": self.availability,
            "reliability": self.reliability,
            "source": self.source,
            "as_of_date": as_of_value,
            "raw_value": self.raw_value,
            "reason": self.reason,
            "components": self.components,
            "decision_eligible": is_decision_eligible(self),
        }


FACTOR_DEFINITIONS: tuple[FactorDefinition, ...] = (
    FactorDefinition(
        "price_momentum_score",
        "price_momentum",
        "价格动量",
        "5d/20d/60d",
        DIRECTION_HIGHER,
        USAGE_SCORE,
        "derived_daily_price",
        "由 ETF 近期收益、相对强弱和趋势延续性生成。",
    ),
    FactorDefinition(
        "reversal_overheat_risk",
        "reversal_overheat",
        "反转/过热",
        "5d/20d/60d",
        DIRECTION_RISK,
        USAGE_GATE,
        "derived_daily_price",
        "识别短期涨太快、冲高别追和偏离均线风险。",
    ),
    FactorDefinition(
        "volatility_drawdown_risk",
        "volatility_risk",
        "波动/回撤风险",
        "20d/60d",
        DIRECTION_RISK,
        USAGE_GATE,
        "derived_daily_price",
        "识别 20 日波动、最大回撤和跳空风险。",
    ),
    FactorDefinition(
        "liquidity_quality",
        "liquidity",
        "流动性质量",
        "20d",
        DIRECTION_HIGHER,
        USAGE_GATE,
        "derived_daily_price",
        "由成交额、换手和默认展示资格衡量 ETF 是否适合短线观察。",
    ),
    FactorDefinition(
        "sector_trend_score",
        "sector_trend",
        "板块趋势",
        "5d/20d",
        DIRECTION_HIGHER,
        USAGE_SCORE,
        "derived_peer_etf",
        "由同主题 ETF 涨幅、均线参与度、成交额变化和技术均值生成。",
    ),
    FactorDefinition(
        "theme_event_score",
        "theme_event",
        "主题事件",
        "event_window",
        DIRECTION_HIGHER,
        USAGE_SCORE,
        "verified_theme_event",
        "由 IPO、政策、订单、产业大会、产品发布和药审节点等主题事件生成。",
    ),
    FactorDefinition(
        "fund_flow_score",
        "fund_flow",
        "资金流",
        "5d/20d",
        DIRECTION_HIGHER,
        USAGE_SCORE,
        "external_flow",
        "由 ETF 份额变化、净申购、主力资金或行业资金生成。",
    ),
    FactorDefinition(
        "sentiment_heat_score",
        "sentiment_heat",
        "情绪热度",
        "1d/5d/20d",
        DIRECTION_HIGHER,
        USAGE_DISPLAY,
        "external_sentiment",
        "由新闻、社媒、搜索、研报覆盖等真实外部热度源生成。",
    ),
    FactorDefinition(
        "constituent_breadth_score",
        "constituent_breadth",
        "成分/同主题扩散",
        "5d/20d",
        DIRECTION_HIGHER,
        USAGE_SCORE,
        "derived_constituents_or_peers",
        "由成分股或同主题 ETF 的上涨家数和均线参与度生成。",
    ),
    FactorDefinition(
        "fundamentals_quality_score",
        "fundamentals_quality",
        "基本面质量",
        "quarterly",
        DIRECTION_HIGHER,
        USAGE_SCORE,
        "external_fundamentals",
        "由成分股 ROE、盈利稳定性、现金流和利润增速加权聚合生成。",
    ),
    FactorDefinition(
        "valuation_percentile_score",
        "valuation",
        "估值分位",
        "daily/monthly",
        DIRECTION_BAND,
        USAGE_SCORE,
        "external_valuation",
        "由 PE/PB、股息率、ERP 或行业估值分位生成。",
    ),
    FactorDefinition(
        "macro_style_score",
        "macro_style",
        "宏观/风格",
        "daily/weekly",
        DIRECTION_BAND,
        USAGE_SCORE,
        "external_macro",
        "由利率、信用、汇率、商品、风险偏好和成长价值风格生成。",
    ),
    FactorDefinition(
        "etf_structure_quality",
        "etf_structure",
        "ETF结构",
        "latest",
        DIRECTION_HIGHER,
        USAGE_GATE,
        "etf_metadata_or_nav",
        "由规模、费率、折溢价、跟踪误差、基金公司和集中度衡量结构风险。",
    ),
)
FACTOR_REGISTRY: dict[str, FactorDefinition] = {item.factor_id: item for item in FACTOR_DEFINITIONS}


def _float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _definition(factor_id: str) -> FactorDefinition:
    try:
        return FACTOR_REGISTRY[factor_id]
    except KeyError as exc:
        raise KeyError(f"Unknown ETF factor id: {factor_id}") from exc


def factor_definitions() -> list[dict[str, Any]]:
    return [item.as_dict() for item in FACTOR_DEFINITIONS]


def is_decision_eligible(result: FactorResult | Mapping[str, Any]) -> bool:
    if isinstance(result, FactorResult):
        availability = result.availability
        reliability = result.reliability
        score = result.score
    else:
        availability = str(result.get("availability") or "")
        reliability = str(result.get("reliability") or "")
        score = result.get("score")
    return availability == AVAILABILITY_AVAILABLE and reliability in DECISION_RELIABILITIES and isinstance(score, int | float)


def available_factor(
    factor_id: str,
    *,
    score: float,
    source: str,
    reason: str,
    as_of_date: date | str | None = None,
    reliability: str = RELIABILITY_VERIFIED,
    raw_value: Any = None,
    components: Mapping[str, Any] | None = None,
) -> FactorResult:
    return FactorResult(
        definition=_definition(factor_id),
        score=_clamp_score(float(score)),
        availability=AVAILABILITY_AVAILABLE,
        reliability=reliability,
        source=source,
        reason=reason,
        as_of_date=as_of_date,
        raw_value=raw_value,
        components=dict(components or {}),
    )


def unavailable_factor(
    factor_id: str,
    *,
    reason: str,
    availability: str = AVAILABILITY_UNAVAILABLE,
    reliability: str = RELIABILITY_UNAVAILABLE,
    source: str = "not_available",
    as_of_date: date | str | None = None,
    raw_value: Any = None,
    components: Mapping[str, Any] | None = None,
) -> FactorResult:
    return FactorResult(
        definition=_definition(factor_id),
        score=None,
        availability=availability,
        reliability=reliability,
        source=source,
        reason=reason,
        as_of_date=as_of_date,
        raw_value=raw_value,
        components=dict(components or {}),
    )


def factor_from_external_score(
    factor_id: str,
    *,
    score: Any,
    reliability: str | None,
    source: str | None,
    reason: str,
    as_of_date: date | str | None = None,
    components: Mapping[str, Any] | None = None,
) -> FactorResult:
    numeric_score = _float(score)
    reliability_value = str(reliability or RELIABILITY_UNAVAILABLE)
    source_value = str(source or "external_source_unverified")
    if numeric_score is not None and reliability_value in DECISION_RELIABILITIES:
        return available_factor(
            factor_id,
            score=numeric_score,
            source=source_value,
            reason=reason,
            as_of_date=as_of_date,
            reliability=reliability_value,
            raw_value=score,
            components=components,
        )
    if numeric_score is not None:
        return unavailable_factor(
            factor_id,
            reason=f"{reason}，但来源未验证，只能展示，暂不参与评分。",
            availability=AVAILABILITY_DISPLAY_ONLY,
            reliability=reliability_value,
            source=source_value,
            as_of_date=as_of_date,
            raw_value=score,
            components=components,
        )
    return unavailable_factor(factor_id, reason=f"{reason}数据不可用。", components=components)


def aggregate_factor_groups(results: Sequence[FactorResult]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[FactorResult]] = {group: [] for group in FACTOR_GROUPS}
    for result in results:
        grouped.setdefault(result.definition.group, []).append(result)

    output: dict[str, dict[str, Any]] = {}
    for group in FACTOR_GROUPS:
        items = grouped.get(group, [])
        eligible = [item for item in items if is_decision_eligible(item)]
        if eligible:
            score = round(sum(float(item.score or 0.0) for item in eligible) / len(eligible), 2)
            availability = AVAILABILITY_AVAILABLE
            reliability = RELIABILITY_VERIFIED
            reason = "可用于综合评分。"
        elif any(item.availability == AVAILABILITY_DISPLAY_ONLY for item in items):
            score = None
            availability = AVAILABILITY_DISPLAY_ONLY
            reliability = next(
                (item.reliability for item in items if item.availability == AVAILABILITY_DISPLAY_ONLY),
                RELIABILITY_UNAVAILABLE,
            )
            reason = next(
                (item.reason for item in items if item.availability == AVAILABILITY_DISPLAY_ONLY),
                "仅展示，不参与评分。",
            )
        else:
            score = None
            availability = next((item.availability for item in items), AVAILABILITY_UNAVAILABLE)
            reliability = next((item.reliability for item in items), RELIABILITY_UNAVAILABLE)
            reason = next((item.reason for item in items), "暂无可用因子数据。")
        output[group] = {
            "group": group,
            "label": FACTOR_GROUP_LABELS.get(group, group),
            "score": score,
            "availability": availability,
            "reliability": reliability,
            "factor_count": len(items),
            "available_factor_count": len(eligible),
            "factor_ids": [item.definition.factor_id for item in items],
            "reason": reason,
        }
    return output


def build_risk_gates(metrics: Mapping[str, Any], risk_flags: Sequence[str]) -> list[dict[str, Any]]:
    flags = {str(item) for item in risk_flags}
    entry_label = str(metrics.get("entry_timing_label") or "")
    default_display_eligible = bool(metrics.get("default_display_eligible", True))
    gates = [
        {
            "gate_id": "overheat",
            "label": "反转/过热",
            "active": bool(flags.intersection({"追高风险", "连续大涨"}) or entry_label == "冲高别追"),
            "severity": "high",
            "reason": "短期涨幅或买点状态提示不要追高。",
            "source_flags": sorted(flags.intersection({"追高风险", "连续大涨"}) or ({entry_label} if entry_label == "冲高别追" else set())),
        },
        {
            "gate_id": "low_liquidity",
            "label": "流动性不足",
            "active": "流动性不足" in flags,
            "severity": "high",
            "reason": "成交额或换手不足，不能靠高分硬推。",
            "source_flags": ["流动性不足"] if "流动性不足" in flags else [],
        },
        {
            "gate_id": "stale_data",
            "label": "数据滞后",
            "active": "数据滞后" in flags,
            "severity": "high",
            "reason": "行情数据滞后，不能作为当前评分依据。",
            "source_flags": ["数据滞后"] if "数据滞后" in flags else [],
        },
        {
            "gate_id": "insufficient_history",
            "label": "样本不足",
            "active": bool(flags.intersection({"数据不足", "样本很短", "短样本"})),
            "severity": "medium",
            "reason": "历史样本不足，评分可信度受限。",
            "source_flags": sorted(flags.intersection({"数据不足", "样本很短", "短样本"})),
        },
        {
            "gate_id": "high_drawdown",
            "label": "回撤/波动过高",
            "active": bool(flags.intersection({"回撤较大", "高波动"})),
            "severity": "medium",
            "reason": "近期波动或回撤偏高，仓位和买点需要更谨慎。",
            "source_flags": sorted(flags.intersection({"回撤较大", "高波动"})),
        },
        {
            "gate_id": "etf_structure",
            "label": "ETF结构限制",
            "active": not default_display_eligible,
            "severity": "medium",
            "reason": "ETF 默认展示质量门槛未通过，结构或数据条件受限。",
            "source_flags": [] if default_display_eligible else ["default_display_ineligible"],
        },
    ]
    return gates


def build_factor_profile(
    group_scores: Mapping[str, Mapping[str, Any]],
    *,
    risk_gates: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    available_groups = {
        group
        for group, payload in group_scores.items()
        if group in PROFILE_BASE_WEIGHTS and isinstance(payload.get("score"), int | float)
    }
    technical_available = "price_momentum" in available_groups
    alpha_available = bool(available_groups.intersection(ALPHA_GROUPS))
    if not technical_available or not alpha_available:
        missing_reasons = {
            group: str(payload.get("reason") or "暂无可用因子数据。")
            for group, payload in group_scores.items()
            if group in PROFILE_BASE_WEIGHTS and group not in available_groups
        }
        return {
            "profile_version": FACTOR_PROFILE_UNAVAILABLE_VERSION,
            "base_version": FACTOR_PROFILE_VERSION,
            "status": AVAILABILITY_UNAVAILABLE,
            "score": None,
            "included_groups": sorted(available_groups),
            "excluded_groups": sorted(set(PROFILE_BASE_WEIGHTS) - available_groups),
            "weights": {},
            "missing_reasons": missing_reasons,
            "risk_gates": list(risk_gates or []),
            "reason": "只有技术或质量类数据时不生成综合关注分，等待板块、主题、资金流或扩散等非技术证据。",
        }

    included = {group for group in available_groups if group in PROFILE_BASE_WEIGHTS}
    total_weight = sum(PROFILE_BASE_WEIGHTS[group] for group in included)
    weights = {group: round(PROFILE_BASE_WEIGHTS[group] / total_weight, 6) for group in sorted(included)}
    score = round(
        sum(float(group_scores[group]["score"]) * weights[group] for group in included),
        2,
    )
    excluded = sorted(set(PROFILE_BASE_WEIGHTS) - included)
    missing_reasons = {
        group: str(group_scores[group].get("reason") or "暂无可用因子数据。")
        for group in excluded
        if group in group_scores
    }
    profile_version = FACTOR_PROFILE_FULL_VERSION if not excluded else FACTOR_PROFILE_DEGRADED_VERSION
    active_gates = [gate for gate in risk_gates or [] if bool(gate.get("active"))]
    return {
        "profile_version": profile_version,
        "base_version": FACTOR_PROFILE_VERSION,
        "status": "gated" if active_gates else AVAILABILITY_AVAILABLE,
        "score": score,
        "included_groups": sorted(included),
        "excluded_groups": excluded,
        "weights": weights,
        "missing_reasons": missing_reasons,
        "risk_gates": list(risk_gates or []),
        "reason": "综合关注分由可用因子按版本化权重生成，缺失因子未参与本次评分。",
    }


def _sector_breadth_score(score_breakdown: Mapping[str, Any]) -> float | None:
    sector = score_breakdown.get("sector_trend_v1")
    if not isinstance(sector, Mapping):
        return None
    components = sector.get("components")
    if not isinstance(components, Mapping):
        return None
    breadth = components.get("breadth")
    if not isinstance(breadth, Mapping):
        return None
    return _float(breadth.get("score"))


def build_asset_factor_payload(
    *,
    asset_name: str,
    theme_tags: Sequence[str],
    metrics: Mapping[str, Any],
    risk_flags: Sequence[str],
    technical_score: float,
    score_breakdown: Mapping[str, Any] | None = None,
    as_of_date: date | str | None = None,
) -> dict[str, Any]:
    breakdown = score_breakdown if isinstance(score_breakdown, Mapping) else {}
    factors: list[FactorResult] = []

    technical = _float(metrics.get("technical_score")) or _float(technical_score)
    if technical is not None:
        factors.append(
            available_factor(
                "price_momentum_score",
                score=technical,
                source="short_research_technical_score",
                reason="来自短线 ETF 技术分。",
                as_of_date=as_of_date,
                raw_value={
                    "return_5d": metrics.get("return_5d"),
                    "return_20d": metrics.get("return_20d"),
                    "return_60d": metrics.get("return_60d"),
                },
            )
        )
    else:
        factors.append(unavailable_factor("price_momentum_score", reason="缺少技术分。", as_of_date=as_of_date))

    gates = build_risk_gates(metrics, risk_flags)
    active_gate_ids = {str(gate["gate_id"]) for gate in gates if gate.get("active")}
    overheat_score = 45.0 if "overheat" in active_gate_ids else 85.0
    factors.append(
        available_factor(
            "reversal_overheat_risk",
            score=overheat_score,
            source="short_research_risk_flags",
            reason="过热风险已触发。" if "overheat" in active_gate_ids else "未触发短期过热门槛。",
            as_of_date=as_of_date,
            components={"risk_flags": list(risk_flags), "entry_timing_label": metrics.get("entry_timing_label")},
        )
    )

    risk_score = _float(metrics.get("risk_score"))
    if risk_score is not None:
        factors.append(
            available_factor(
                "volatility_drawdown_risk",
                score=risk_score,
                source="short_research_risk_score",
                reason="来自波动、回撤、数据质量和风险旗标。",
                as_of_date=as_of_date,
                raw_value={
                    "volatility_20d": metrics.get("volatility_20d"),
                    "max_drawdown_60d": metrics.get("max_drawdown_60d"),
                },
            )
        )
    else:
        factors.append(
            unavailable_factor(
                "volatility_drawdown_risk",
                reason="缺少波动/回撤风险分。",
                availability=AVAILABILITY_INSUFFICIENT,
                as_of_date=as_of_date,
            )
        )

    liquidity_score = _float(metrics.get("liquidity_score"))
    turnover_20d = _float(metrics.get("average_turnover_20d"))
    if liquidity_score is None and turnover_20d is not None:
        liquidity_score = min(100.0, max(0.0, turnover_20d / 1_000_000))
    if liquidity_score is not None:
        factors.append(
            available_factor(
                "liquidity_quality",
                score=liquidity_score,
                source="short_research_liquidity",
                reason="来自 20 日平均成交额和流动性风险门槛。",
                as_of_date=as_of_date,
                raw_value={"average_turnover_20d": metrics.get("average_turnover_20d")},
            )
        )
    else:
        factors.append(
            unavailable_factor(
                "liquidity_quality",
                reason="缺少成交额或换手数据。",
                availability=AVAILABILITY_INSUFFICIENT,
                as_of_date=as_of_date,
            )
        )

    sector_score = _float(metrics.get("sector_trend_score"))
    if str(metrics.get("sector_trend_status") or "") == "success" and sector_score is not None:
        factors.append(
            available_factor(
                "sector_trend_score",
                score=sector_score,
                source="short_research_sector_trend",
                reason=str(metrics.get("sector_trend_summary") or "板块趋势可用。"),
                as_of_date=as_of_date,
                raw_value={"peer_count": metrics.get("sector_peer_count"), "theme": metrics.get("sector_trend_theme")},
            )
        )
    else:
        factors.append(
            unavailable_factor(
                "sector_trend_score",
                reason=str(metrics.get("sector_trend_reason") or "暂无板块趋势数据。"),
                availability=AVAILABILITY_UNAVAILABLE,
                as_of_date=as_of_date,
            )
        )

    catalyst_score = _float(metrics.get("catalyst_score"))
    if str(metrics.get("catalyst_status") or "") == "success" and catalyst_score is not None:
        factors.append(
            available_factor(
                "theme_event_score",
                score=catalyst_score,
                source="verified_theme_catalyst_snapshot",
                reason=str(metrics.get("catalyst_summary") or "主题事件可用。"),
                as_of_date=as_of_date,
                raw_value={"theme": metrics.get("catalyst_theme_name"), "events": metrics.get("catalyst_events")},
            )
        )
    else:
        factors.append(
            unavailable_factor(
                "theme_event_score",
                reason=str(metrics.get("catalyst_summary") or "暂无可用于评分的主题事件。"),
                availability=AVAILABILITY_UNAVAILABLE,
                as_of_date=as_of_date,
            )
        )

    sentiment_score = _float(metrics.get("sentiment_heat_score"))
    if sentiment_score is not None and str(metrics.get("catalyst_status") or "") == "success":
        factors.append(
            unavailable_factor(
                "sentiment_heat_score",
                reason="当前热度为主题事件热度，非实时新闻/社媒/搜索舆情热度，只做展示。",
                availability=AVAILABILITY_DISPLAY_ONLY,
                reliability=RELIABILITY_SEED_ONLY,
                source="theme_event_heat",
                as_of_date=as_of_date,
                raw_value=sentiment_score,
            )
        )
    else:
        factors.append(unavailable_factor("sentiment_heat_score", reason="暂无真实外部舆情热度源。", as_of_date=as_of_date))

    factors.append(
        factor_from_external_score(
            "fund_flow_score",
            score=metrics.get("fund_flow_score"),
            reliability=str(metrics.get("fund_flow_reliability") or RELIABILITY_UNAVAILABLE),
            source=str(metrics.get("fund_flow_source") or "fund_flow_source_missing"),
            reason="资金流",
            as_of_date=metrics.get("fund_flow_as_of_date") or as_of_date,
        )
    )

    breadth_score = _float(metrics.get("constituent_breadth_score"))
    breadth_reliability = str(metrics.get("constituent_breadth_reliability") or RELIABILITY_UNAVAILABLE)
    if breadth_score is None:
        breadth_score = _sector_breadth_score(breakdown)
        breadth_reliability = RELIABILITY_VERIFIED if breadth_score is not None else RELIABILITY_UNAVAILABLE
    factors.append(
        factor_from_external_score(
            "constituent_breadth_score",
            score=breadth_score,
            reliability=breadth_reliability,
            source=str(metrics.get("constituent_breadth_source") or "sector_trend_peer_breadth"),
            reason="成分股或同主题扩散",
            as_of_date=metrics.get("constituent_breadth_as_of_date") or as_of_date,
        )
    )

    factors.append(
        factor_from_external_score(
            "fundamentals_quality_score",
            score=metrics.get("fundamentals_quality_score"),
            reliability=str(metrics.get("fundamentals_quality_reliability") or RELIABILITY_UNAVAILABLE),
            source=str(metrics.get("fundamentals_quality_source") or "fundamentals_source_missing"),
            reason="成分股基本面质量",
            as_of_date=metrics.get("fundamentals_quality_as_of_date") or as_of_date,
        )
    )
    factors.append(
        factor_from_external_score(
            "valuation_percentile_score",
            score=metrics.get("valuation_percentile_score"),
            reliability=str(metrics.get("valuation_reliability") or RELIABILITY_UNAVAILABLE),
            source=str(metrics.get("valuation_source") or "valuation_source_missing"),
            reason="估值分位",
            as_of_date=metrics.get("valuation_as_of_date") or as_of_date,
        )
    )
    factors.append(
        factor_from_external_score(
            "macro_style_score",
            score=metrics.get("macro_style_score"),
            reliability=str(metrics.get("macro_style_reliability") or RELIABILITY_UNAVAILABLE),
            source=str(metrics.get("macro_style_source") or "macro_style_source_missing"),
            reason="宏观/风格",
            as_of_date=metrics.get("macro_style_as_of_date") or as_of_date,
        )
    )

    structure_score = _float(metrics.get("etf_structure_score"))
    if structure_score is None:
        structure_score = 75.0 if bool(metrics.get("default_display_eligible", True)) else 35.0
    threshold_context = metrics.get("dynamic_threshold_context")
    premium_state = threshold_context.get("premium_state") if isinstance(threshold_context, Mapping) else None
    factors.append(
        available_factor(
            "etf_structure_quality",
            score=structure_score,
            source=str(metrics.get("etf_structure_source") or "short_research_quality_gate"),
            reason="来自默认展示资格、折溢价/跟踪误差可用性和 ETF 结构质量门槛。",
            as_of_date=metrics.get("etf_structure_as_of_date") or as_of_date,
            raw_value={
                "asset_name": asset_name,
                "theme_tags": list(theme_tags),
                "default_display_eligible": metrics.get("default_display_eligible", True),
                "premium_state": premium_state,
            },
        )
    )

    group_scores = aggregate_factor_groups(factors)
    profile = build_factor_profile(group_scores, risk_gates=gates)
    factor_dicts = [item.as_dict() for item in factors]
    factor_availability = {
        item["factor_id"]: {
            "availability": item["availability"],
            "reliability": item["reliability"],
            "reason": item["reason"],
            "decision_eligible": item["decision_eligible"],
        }
        for item in factor_dicts
    }
    return {
        "metrics": {
            "factor_profile_version": profile["profile_version"],
            "factor_profile_status": profile["status"],
            "factor_profile_score": profile["score"],
            "factor_scores": factor_dicts,
            "factor_group_scores": group_scores,
            "factor_availability": factor_availability,
            "risk_gates": gates,
            "opportunity_breakdown": profile,
        },
        "breakdown": {
            "score_version": profile["profile_version"],
            "factor_profile": profile,
            "factor_groups": group_scores,
            "factors": factor_dicts,
            "risk_gates": gates,
        },
    }
