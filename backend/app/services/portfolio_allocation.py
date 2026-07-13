from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any

PORTFOLIO_SINGLE_WEIGHT_CAP = 0.30
PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP = 0.15
PORTFOLIO_SATELLITE_EXPOSURE_CAP = 0.35
PORTFOLIO_TOTAL_EXPOSURE_CAP = 1.00
PORTFOLIO_THEME_EXPOSURE_CAP = 0.60
PORTFOLIO_HIGH_CORRELATION = 0.85
PORTFOLIO_CORRELATION_MIN_POINTS = 40
PORTFOLIO_ENTRY_TIMING_OK = (
    "趋势延续",
    "健康回踩",
)
PORTFOLIO_ENTRY_TIMING_FORBIDDEN = (
    "冲高别追",
    "跌破等待",
    "放量转弱",
    "数据不足",
)
PORTFOLIO_RISK_FLAGS_FORBIDDEN = (
    "流动性不足",
    "数据滞后",
    "样本不足",
)
PORTFOLIO_RISK_FLAGS_WATCH_ONLY = (
    "追高风险",
    "高波动",
)
PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT = (
    "高波动",
    "短样本",
)
PORTFOLIO_MODE_RISK_ON = "risk_on"
PORTFOLIO_MODE_NEUTRAL = "neutral"
PORTFOLIO_MODE_DEFENSIVE = "defensive"
PORTFOLIO_MODE_CASH_WAIT = "cash_wait"
PORTFOLIO_LAYER_PRIMARY = "primary"
PORTFOLIO_LAYER_SATELLITE = "satellite"
PORTFOLIO_LAYER_DEFENSIVE = "defensive"
PORTFOLIO_LAYER_WATCH_ONLY = "watch_only"
PORTFOLIO_LAYER_EXCLUDED = "excluded"

BLACK_LITTERMAN_METHOD = "black_litterman"
BLACK_LITTERMAN_MIN_ASSETS = 4
BLACK_LITTERMAN_MIN_HISTORY_DAYS = 60
BLACK_LITTERMAN_ALLOWED_RELIABILITY = {
    "verified",
    "alternate_provider",
    "verified_daily_close",
}
BLACK_LITTERMAN_BLOCKED_RELIABILITY = {
    "estimated",
    "stale",
    "unavailable",
    "display_only",
    "web_only",
}


@dataclass(frozen=True)
class BlackLittermanCandidate:
    code: str
    name: str
    theme_group: str
    score: float
    observation_label: str
    entry_timing_label: str
    returns: tuple[float, ...]
    expected_return: float | None = None
    volatility: float | None = None
    prior_weight: float | None = None
    prior_source: str | None = None
    evidence_confidence: float | None = None
    data_reliability: str = "verified"
    liquidity_score: float | None = None
    market_regime: str | None = None
    validation_sample_count: int | None = None


@dataclass(frozen=True)
class BlackLittermanAllocationItem:
    code: str
    name: str
    target_weight: float
    prior_weight: float
    prior_source: str
    prior_return: float
    view_return: float
    posterior_return: float
    confidence: float
    volatility: float | None
    theme_group: str
    explanation: str
    metrics: dict[str, Any]


@dataclass(frozen=True)
class BlackLittermanAllocationResult:
    status: str
    items: tuple[BlackLittermanAllocationItem, ...]
    summary: dict[str, Any]
    excluded_items: tuple[dict[str, Any], ...] = ()
    unavailable_reason: str | None = None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_positive(values: dict[str, float], *, target_total: float = 1.0) -> dict[str, float] | None:
    cleaned = {key: max(0.0, float(value)) for key, value in values.items() if value > 0}
    total = sum(cleaned.values())
    if total <= 0:
        return None
    return {key: value / total * target_total for key, value in cleaned.items()}


def _cap_long_only_weights(
    raw_weights: dict[str, float],
    theme_by_code: dict[str, str],
    *,
    single_cap: float = PORTFOLIO_SINGLE_WEIGHT_CAP,
    theme_cap: float = PORTFOLIO_THEME_EXPOSURE_CAP,
    target_total: float = 1.0,
) -> dict[str, float] | None:
    cleaned = {code: max(0.0, float(weight)) for code, weight in raw_weights.items() if weight > 0}
    if len(cleaned) * single_cap + 1e-9 < target_total:
        return None
    theme_capacity: dict[str, float] = {}
    for code in cleaned:
        theme = theme_by_code.get(code) or "unknown"
        theme_capacity[theme] = theme_capacity.get(theme, 0.0) + single_cap
    if sum(min(theme_cap, capacity) for capacity in theme_capacity.values()) + 1e-9 < target_total:
        return None

    normalized = _normalize_positive(cleaned, target_total=target_total)
    if normalized is None:
        return None
    weights = dict.fromkeys(normalized, 0.0)
    theme_used: dict[str, float] = {}
    remaining = target_total
    ranked = sorted(normalized.items(), key=lambda item: item[1], reverse=True)
    while remaining > 1e-6:
        changed = False
        for code, _weight in ranked:
            if remaining <= 1e-6:
                break
            theme = theme_by_code.get(code) or "unknown"
            used = theme_used.get(theme, 0.0)
            room = min(single_cap - weights[code], theme_cap - used, remaining)
            if room <= 1e-6:
                continue
            step = min(room, remaining)
            weights[code] += step
            theme_used[theme] = used + step
            remaining -= step
            changed = True
        if not changed:
            break
    if remaining > 1e-4:
        return None
    return {code: round(weight, 6) for code, weight in weights.items() if weight > 1e-6}


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    size = min(len(left), len(right))
    if size < PORTFOLIO_CORRELATION_MIN_POINTS:
        return None
    left_values = left[-size:]
    right_values = right[-size:]
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values, strict=False))
    left_var = sum((a - left_mean) ** 2 for a in left_values)
    right_var = sum((b - right_mean) ** 2 for b in right_values)
    if left_var <= 0 or right_var <= 0:
        return None
    return float(numerator / (left_var * right_var) ** 0.5)


def black_litterman_covariance_summary(
    candidates: list[BlackLittermanCandidate],
    *,
    min_history_days: int = BLACK_LITTERMAN_MIN_HISTORY_DAYS,
) -> dict[str, Any]:
    eligible = [candidate for candidate in candidates if len(candidate.returns) >= min_history_days]
    if len(eligible) < BLACK_LITTERMAN_MIN_ASSETS:
        return {
            "status": "unavailable",
            "eligible_count": len(eligible),
            "min_assets": BLACK_LITTERMAN_MIN_ASSETS,
            "min_history_days": min_history_days,
            "reason": "可用于协方差估计的 ETF 数量不足。",
        }
    volatilities = [
        pstdev(candidate.returns[-min_history_days:])
        for candidate in eligible
        if len(candidate.returns[-min_history_days:]) > 1
    ]
    if not volatilities or any(value <= 0 for value in volatilities):
        return {
            "status": "unavailable",
            "eligible_count": len(eligible),
            "min_history_days": min_history_days,
            "reason": "收益序列波动率无效，不能估计协方差。",
        }
    pairs = 0
    high_corr_pairs = 0
    for index, left in enumerate(eligible):
        for right in eligible[index + 1:]:
            corr = _correlation(left.returns, right.returns)
            if corr is None:
                continue
            pairs += 1
            if corr >= PORTFOLIO_HIGH_CORRELATION:
                high_corr_pairs += 1
    return {
        "status": "ready",
        "eligible_count": len(eligible),
        "min_history_days": min_history_days,
        "avg_daily_volatility": mean(volatilities),
        "max_daily_volatility": max(volatilities),
        "pair_count": pairs,
        "high_correlation_pair_count": high_corr_pairs,
    }


def _candidate_exclusion_reason(candidate: BlackLittermanCandidate, min_history_days: int) -> str | None:
    reliability = (candidate.data_reliability or "").strip()
    if reliability in BLACK_LITTERMAN_BLOCKED_RELIABILITY:
        return f"数据可信度为 {reliability}，不能参与 Black-Litterman 决策权重。"
    if reliability and reliability not in BLACK_LITTERMAN_ALLOWED_RELIABILITY:
        return f"数据可信度 {reliability} 未列入可决策口径。"
    if len(candidate.returns) < min_history_days:
        return f"历史收益样本不足 {min_history_days} 天。"
    volatility = candidate.volatility
    if volatility is None and len(candidate.returns[-min_history_days:]) > 1:
        volatility = pstdev(candidate.returns[-min_history_days:])
    if volatility is None or volatility <= 0:
        return "波动率无效，不能参与协方差和权重计算。"
    return None


def _prior_weights(candidates: list[BlackLittermanCandidate]) -> tuple[dict[str, float], str]:
    explicit = {
        candidate.code: float(candidate.prior_weight or 0.0)
        for candidate in candidates
        if candidate.prior_weight is not None and candidate.prior_weight > 0
    }
    normalized = _normalize_positive(explicit)
    if normalized and len(normalized) == len(candidates):
        source = "aum" if all(candidate.prior_source == "aum" for candidate in candidates) else "liquidity_proxy"
        return normalized, source

    liquidity = {
        candidate.code: float(candidate.liquidity_score or 0.0)
        for candidate in candidates
        if candidate.liquidity_score is not None and candidate.liquidity_score > 0
    }
    normalized = _normalize_positive(liquidity)
    if normalized and len(normalized) >= max(2, len(candidates) // 2):
        fallback_fill = {
            candidate.code: normalized.get(candidate.code, 1.0 / len(candidates))
            for candidate in candidates
        }
        return _normalize_positive(fallback_fill) or {}, "liquidity_proxy"

    inverse_vol = {
        candidate.code: 1.0 / max(0.0001, candidate.volatility or pstdev(candidate.returns))
        for candidate in candidates
    }
    return _normalize_positive(inverse_vol) or {}, "deterministic_inverse_volatility"


def _view_return(candidate: BlackLittermanCandidate) -> tuple[float, list[str]]:
    base = candidate.expected_return
    if base is None and candidate.returns:
        window = candidate.returns[-60:] if len(candidate.returns) >= 60 else candidate.returns
        base = mean(window)
    base = float(base or 0.0)
    score_adjustment = _clamp((float(candidate.score or 0.0) - 75.0) / 10000.0, -0.004, 0.004)
    label_adjustment = {
        "短线观察": 0.0012,
        "高位观察": -0.0006,
        "谨慎观察": -0.0012,
        "不适合短线": -0.0025,
        "数据不足": -0.003,
    }.get(candidate.observation_label, 0.0)
    entry_adjustment = {
        "趋势延续": 0.0010,
        "健康回踩": 0.0008,
        "冲高别追": -0.0012,
        "跌破等待": -0.0020,
        "放量转弱": -0.0025,
        "数据不足": -0.003,
    }.get(candidate.entry_timing_label, 0.0)
    regime_adjustment = {
        PORTFOLIO_MODE_RISK_ON: 0.0008,
        PORTFOLIO_MODE_NEUTRAL: 0.0,
        PORTFOLIO_MODE_DEFENSIVE: -0.0008,
        PORTFOLIO_MODE_CASH_WAIT: -0.0015,
    }.get(candidate.market_regime or "", 0.0)
    reasons = [
        f"基础收益 {base:.4%}",
        f"分数调整 {score_adjustment:.4%}",
        f"买入观察 {candidate.observation_label or '未分类'}",
        f"今日买点 {candidate.entry_timing_label or '未分类'}",
    ]
    return base + score_adjustment + label_adjustment + entry_adjustment + regime_adjustment, reasons


def _confidence(candidate: BlackLittermanCandidate, *, min_history_days: int) -> tuple[float, list[str]]:
    reason: list[str] = []
    reliability = candidate.data_reliability or "unknown"
    if reliability in BLACK_LITTERMAN_BLOCKED_RELIABILITY:
        return 0.0, [f"数据可信度 {reliability} 不可决策"]
    if reliability not in BLACK_LITTERMAN_ALLOWED_RELIABILITY:
        return 0.0, [f"数据可信度 {reliability} 未验证"]

    confidence = 0.30
    if candidate.evidence_confidence is not None:
        confidence = _clamp(float(candidate.evidence_confidence), 0.05, 0.85)
        reason.append("使用标签/体检证据置信度")
    else:
        score = float(candidate.score or 0.0)
        if score >= 90:
            confidence += 0.18
        elif score >= 80:
            confidence += 0.10
        elif score < 65:
            confidence -= 0.10
        reason.append("使用结构化分数估算置信度")

    sample_count = candidate.validation_sample_count or 0
    if sample_count > 0:
        confidence *= _clamp(sample_count / 60.0, 0.45, 1.0)
        reason.append(f"标签样本 {sample_count}")
    coverage = _clamp(len(candidate.returns) / max(1, min_history_days * 2), 0.4, 1.0)
    confidence *= coverage
    if candidate.entry_timing_label in PORTFOLIO_ENTRY_TIMING_FORBIDDEN:
        confidence *= 0.55
        reason.append("今日买点偏弱，降低置信度")
    return _clamp(confidence, 0.0, 0.85), reason


def _remove_duplicate_exposure(
    candidates: list[BlackLittermanCandidate],
    *,
    min_assets: int,
) -> tuple[list[BlackLittermanCandidate], list[dict[str, Any]]]:
    kept: list[BlackLittermanCandidate] = []
    excluded: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        duplicate_of = None
        for existing in kept:
            if existing.theme_group != candidate.theme_group:
                continue
            corr = _correlation(existing.returns, candidate.returns)
            if corr is not None and corr >= PORTFOLIO_HIGH_CORRELATION:
                duplicate_of = existing
                break
        if duplicate_of is not None and len(candidates) - len(excluded) - 1 >= min_assets:
            excluded.append(
                {
                    "code": candidate.code,
                    "name": candidate.name,
                    "reason": f"与 {duplicate_of.code} 同主题且相关性过高，避免重复暴露。",
                }
            )
            continue
        kept.append(candidate)
    return kept, excluded


def build_black_litterman_allocation(
    candidates: list[BlackLittermanCandidate],
    *,
    min_assets: int = BLACK_LITTERMAN_MIN_ASSETS,
    min_history_days: int = BLACK_LITTERMAN_MIN_HISTORY_DAYS,
    single_cap: float = PORTFOLIO_SINGLE_WEIGHT_CAP,
    theme_cap: float = PORTFOLIO_THEME_EXPOSURE_CAP,
) -> BlackLittermanAllocationResult:
    excluded: list[dict[str, Any]] = []
    eligible: list[BlackLittermanCandidate] = []
    for candidate in candidates:
        reason = _candidate_exclusion_reason(candidate, min_history_days)
        if reason:
            excluded.append({"code": candidate.code, "name": candidate.name, "reason": reason})
            continue
        eligible.append(candidate)
    eligible, duplicate_exclusions = _remove_duplicate_exposure(eligible, min_assets=min_assets)
    excluded.extend(duplicate_exclusions)

    covariance = black_litterman_covariance_summary(eligible, min_history_days=min_history_days)
    if covariance["status"] != "ready" or len(eligible) < min_assets:
        return BlackLittermanAllocationResult(
            status="unavailable",
            items=(),
            excluded_items=tuple(excluded),
            summary={
                "method": BLACK_LITTERMAN_METHOD,
                "status": "unavailable",
                "covariance": covariance,
                "candidate_count": len(candidates),
                "eligible_count": len(eligible),
                "constraints": {"single_weight_cap": single_cap, "theme_exposure_cap": theme_cap},
                "research_only": True,
                "no_trade_instruction": True,
            },
            unavailable_reason=str(covariance.get("reason") or "候选 ETF 不足，不能生成 Black-Litterman 权重。"),
        )

    priors, prior_source = _prior_weights(eligible)
    if not priors:
        return BlackLittermanAllocationResult(
            status="unavailable",
            items=(),
            excluded_items=tuple(excluded),
            summary={"method": BLACK_LITTERMAN_METHOD, "status": "unavailable"},
            unavailable_reason="无法构造市场先验权重。",
        )

    prior_return = sum(priors.get(candidate.code, 0.0) * float(candidate.expected_return or mean(candidate.returns[-60:])) for candidate in eligible)
    raw_scores: dict[str, float] = {}
    item_inputs: dict[str, dict[str, Any]] = {}
    confidence_values: list[float] = []
    for candidate in eligible:
        view, view_reasons = _view_return(candidate)
        confidence, confidence_reasons = _confidence(candidate, min_history_days=min_history_days)
        confidence_values.append(confidence)
        posterior = prior_return * (1.0 - confidence) + view * confidence
        volatility = candidate.volatility or pstdev(candidate.returns[-min_history_days:])
        tilt = 1.0 + _clamp((posterior - prior_return) / max(0.0001, volatility), -0.8, 1.5)
        raw_scores[candidate.code] = max(0.001, priors.get(candidate.code, 0.0) * tilt)
        item_inputs[candidate.code] = {
            "view_return": view,
            "view_reasons": view_reasons,
            "confidence": confidence,
            "confidence_reasons": confidence_reasons,
            "posterior_return": posterior,
            "volatility": volatility,
        }

    weights = _cap_long_only_weights(
        raw_scores,
        {candidate.code: candidate.theme_group for candidate in eligible},
        single_cap=single_cap,
        theme_cap=theme_cap,
    )
    if not weights:
        return BlackLittermanAllocationResult(
            status="unavailable",
            items=(),
            excluded_items=tuple(excluded),
            summary={
                "method": BLACK_LITTERMAN_METHOD,
                "status": "unavailable",
                "candidate_count": len(candidates),
                "eligible_count": len(eligible),
                "constraints": {"single_weight_cap": single_cap, "theme_exposure_cap": theme_cap},
            },
            unavailable_reason="约束下无法生成满仓、长-only 的 Black-Litterman 权重。",
        )

    by_code = {candidate.code: candidate for candidate in eligible}
    items = []
    for code, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
        candidate = by_code[code]
        inputs = item_inputs[code]
        items.append(
            BlackLittermanAllocationItem(
                code=code,
                name=candidate.name,
                target_weight=weight,
                prior_weight=round(priors.get(code, 0.0), 6),
                prior_source=prior_source,
                prior_return=prior_return,
                view_return=inputs["view_return"],
                posterior_return=inputs["posterior_return"],
                confidence=inputs["confidence"],
                volatility=inputs["volatility"],
                theme_group=candidate.theme_group,
                explanation="Black-Litterman 对照：市场先验 + 结构化标签 view + 置信度，不使用 AI 文本。",
                metrics={
                    "score": candidate.score,
                    "observation_label": candidate.observation_label,
                    "entry_timing_label": candidate.entry_timing_label,
                    "view_reasons": inputs["view_reasons"],
                    "confidence_reasons": inputs["confidence_reasons"],
                },
            )
        )

    return BlackLittermanAllocationResult(
        status="success",
        items=tuple(items),
        excluded_items=tuple(excluded),
        summary={
            "method": BLACK_LITTERMAN_METHOD,
            "status": "success",
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "prior_source": prior_source,
            "view_count": len(items),
            "confidence_summary": {
                "min": min(confidence_values) if confidence_values else None,
                "avg": mean(confidence_values) if confidence_values else None,
                "max": max(confidence_values) if confidence_values else None,
            },
            "covariance": covariance,
            "constraints": {"single_weight_cap": single_cap, "theme_exposure_cap": theme_cap},
            "excluded_count": len(excluded),
            "research_only": True,
            "no_trade_instruction": True,
        },
    )
