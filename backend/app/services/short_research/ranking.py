from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any

FINAL_SCORE_VERSION = "final_score_v2"
MIN_PEER_SAMPLE_COUNT = 12


@dataclass(frozen=True)
class RankingRecord:
    code: str
    base_score: float
    metrics: Mapping[str, Any]
    risk_flags: Sequence[str]


def numeric_metric(metrics: Mapping[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def percentile_rank(value: float | None, values: Sequence[float | None], *, higher_is_better: bool = True) -> float | None:
    if value is None:
        return None
    clean = sorted(float(item) for item in values if item is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return 50.0
    less = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    rank = (less + (equal - 1) / 2) / (len(clean) - 1) * 100
    if not higher_is_better:
        rank = 100 - rank
    return round(max(0.0, min(100.0, rank)), 2)


def _average(values: Sequence[float | None], *, fallback: float) -> float:
    clean = [float(value) for value in values if value is not None]
    return round(mean(clean), 2) if clean else fallback


def _theme_key(record: RankingRecord) -> str:
    profile = record.metrics.get("theme_profile")
    if isinstance(profile, Mapping):
        bucket = str(profile.get("asset_bucket") or "unknown")
        group = str(profile.get("theme_group") or "unknown")
        return f"{bucket}:{group}"
    dynamic_context = record.metrics.get("dynamic_threshold_context")
    if isinstance(dynamic_context, Mapping):
        bucket = str(dynamic_context.get("asset_bucket") or "unknown")
        group = str(dynamic_context.get("theme_group") or "unknown")
        return f"{bucket}:{group}"
    return "unknown:unknown"


def _metric_values(records: Sequence[RankingRecord], key: str) -> list[float | None]:
    return [numeric_metric(record.metrics, key) for record in records]


def _score_cross_sectional(record: RankingRecord, records: Sequence[RankingRecord]) -> dict[str, Any]:
    global_scores = _cross_sectional_parts(record, records)
    peers = [item for item in records if _theme_key(item) == _theme_key(record)]
    peer_scores = _cross_sectional_parts(record, peers) if len(peers) >= MIN_PEER_SAMPLE_COUNT else None
    global_score = global_scores["score"]
    if peer_scores is None:
        score = global_score
        peer_reason = "同类样本不足，使用全市场分位。"
    else:
        score = round(global_score * 0.65 + peer_scores["score"] * 0.35, 2)
        peer_reason = f"同类样本 {len(peers)} 只，合并同类分位。"
    return {
        "score": score,
        "global_score": global_score,
        "peer_score": peer_scores["score"] if peer_scores else None,
        "peer_sample_count": len(peers),
        "reason": peer_reason,
        "parts": global_scores["parts"],
    }


def _cross_sectional_parts(record: RankingRecord, records: Sequence[RankingRecord]) -> dict[str, Any]:
    returns = [
        percentile_rank(numeric_metric(record.metrics, "return_5d"), _metric_values(records, "return_5d")),
        percentile_rank(numeric_metric(record.metrics, "return_20d"), _metric_values(records, "return_20d")),
        percentile_rank(numeric_metric(record.metrics, "return_60d"), _metric_values(records, "return_60d")),
    ]
    trend = _average(returns, fallback=50.0)
    risk = _average(
        [
            percentile_rank(numeric_metric(record.metrics, "max_drawdown_60d"), _metric_values(records, "max_drawdown_60d")),
            percentile_rank(
                numeric_metric(record.metrics, "volatility_20d"),
                _metric_values(records, "volatility_20d"),
                higher_is_better=False,
            ),
        ],
        fallback=50.0,
    )
    liquidity = percentile_rank(
        numeric_metric(record.metrics, "average_turnover_20d"),
        _metric_values(records, "average_turnover_20d"),
    )
    distance = numeric_metric(record.metrics, "distance_to_ma5_pct")
    overextension = percentile_rank(
        abs(distance) if distance is not None else None,
        [abs(value) if value is not None else None for value in _metric_values(records, "distance_to_ma5_pct")],
        higher_is_better=False,
    )
    parts = {
        "trend_percentile": trend,
        "risk_percentile": risk,
        "liquidity_percentile": liquidity if liquidity is not None else 50.0,
        "overextension_percentile": overextension if overextension is not None else 50.0,
    }
    score = round(
        parts["trend_percentile"] * 0.45
        + parts["risk_percentile"] * 0.25
        + parts["liquidity_percentile"] * 0.20
        + parts["overextension_percentile"] * 0.10,
        2,
    )
    return {"score": score, "parts": parts}


def _score_dynamic_threshold(record: RankingRecord) -> dict[str, Any]:
    context = record.metrics.get("dynamic_threshold_context")
    if not isinstance(context, Mapping):
        return {"score": 50.0, "reason": "缺少动态阈值上下文。"}
    score = 70.0
    reasons: list[str] = []
    if context.get("threshold_mode") == "dynamic":
        score += 10
        reasons.append("动态阈值样本可用。")
    else:
        score -= 18
        reasons.append("动态阈值样本不足。")
    if context.get("decision_eligible") is False:
        score -= 18
        reason = context.get("ineligible_reason") or "数据不满足决策口径"
        reasons.append(f"决策资格不足：{reason}。")
    premium_state = str(context.get("premium_state") or "unavailable")
    premium_adjustment = {
        "normal": 8,
        "mild": 0,
        "unavailable": -4,
        "high": -20,
        "extreme": -35,
    }.get(premium_state, -4)
    score += premium_adjustment
    if premium_state != "normal":
        reasons.append(f"折溢价状态：{premium_state}。")
    label = str(record.metrics.get("entry_timing_label") or "")
    if label in {"健康回踩", "趋势延续"}:
        score += 8
        reasons.append(f"买点状态为{label}。")
    elif label in {"冲高别追", "跌破等待", "放量转弱"}:
        score -= 14
        reasons.append(f"买点状态为{label}。")
    elif label in {"数据不足", "休市", "行情滞后"}:
        score -= 8
        reasons.append(f"买点状态为{label}。")
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "reason": " ".join(reasons) or "动态阈值未触发额外调整。",
        "premium_state": premium_state,
        "threshold_mode": context.get("threshold_mode"),
        "decision_eligible": bool(context.get("decision_eligible", False)),
    }


def _reliability(record: RankingRecord) -> tuple[str, float, list[str]]:
    reasons: list[str] = []
    data_quality = numeric_metric(record.metrics, "data_quality_score")
    score = data_quality if data_quality is not None else 75.0
    reliability = "verified"
    if "数据不足" in record.risk_flags:
        reliability = "unavailable"
        score = min(score, 20.0)
        reasons.append("数据不足。")
    elif "数据滞后" in record.risk_flags:
        reliability = "stale"
        score = min(score, 40.0)
        reasons.append("数据滞后。")
    elif record.metrics.get("default_display_eligible") is False:
        reliability = "unavailable"
        score = min(score, 45.0)
        reasons.extend(str(item) for item in (record.metrics.get("default_exclusion_reasons") or []))
    return reliability, round(max(0.0, min(100.0, score)), 2), reasons


def apply_final_score_limits(
    score: float,
    *,
    risk_flags: Sequence[str],
    unavailable: bool = False,
) -> tuple[float, list[str]]:
    bounded = max(0.0, min(100.0, score))
    if "数据不足" in risk_flags:
        return min(bounded, 35.0), ["数据不足不能形成高分排序。"]
    if "数据滞后" in risk_flags:
        return min(bounded, 55.0), ["旧数据不能提高最终排序。"]
    if unavailable:
        return min(bounded, 45.0), ["不可决策数据不能提高最终排序。"]
    return bounded, []


def _score_liquidity_premium(record: RankingRecord, records: Sequence[RankingRecord]) -> dict[str, Any]:
    liquidity = percentile_rank(
        numeric_metric(record.metrics, "average_turnover_20d"),
        _metric_values(records, "average_turnover_20d"),
    )
    score = liquidity if liquidity is not None else 45.0
    reasons: list[str] = []
    if liquidity is None:
        reasons.append("缺少近 20 日成交额。")
    if "流动性不足" in record.risk_flags:
        score = min(score, 30.0)
        reasons.append("触发流动性不足。")
    context = record.metrics.get("dynamic_threshold_context")
    premium_state = str(context.get("premium_state")) if isinstance(context, Mapping) else "unavailable"
    if premium_state == "high":
        score = min(score, 55.0)
        reasons.append("折溢价偏高。")
    elif premium_state == "extreme":
        score = min(score, 35.0)
        reasons.append("折溢价极端。")
    elif premium_state == "unavailable":
        score = min(score, 80.0)
        reasons.append("暂无折溢价数据。")
    return {"score": round(max(0.0, min(100.0, score)), 2), "reason": " ".join(reasons) or "流动性与折溢价未触发主要惩罚。"}


def build_final_score_breakdowns(records: Sequence[RankingRecord]) -> dict[str, dict[str, Any]]:
    if not records:
        return {}
    results: dict[str, dict[str, Any]] = {}
    for record in records:
        cross = _score_cross_sectional(record, records)
        dynamic = _score_dynamic_threshold(record)
        reliability, reliability_score, reliability_reasons = _reliability(record)
        liquidity_premium = _score_liquidity_premium(record, records)
        final_score = round(
            cross["score"] * 0.52
            + dynamic["score"] * 0.20
            + reliability_score * 0.16
            + liquidity_premium["score"] * 0.12,
            2,
        )
        final_score, limitation_reasons = apply_final_score_limits(
            final_score,
            risk_flags=record.risk_flags,
            unavailable=reliability == "unavailable",
        )
        confidence = "high" if reliability in {"verified", "alternate_provider"} and cross["peer_sample_count"] >= MIN_PEER_SAMPLE_COUNT else "medium"
        if reliability in {"stale", "unavailable"}:
            confidence = "low"
        results[record.code] = {
            "score_version": FINAL_SCORE_VERSION,
            "final_score": round(final_score, 2),
            "confidence": confidence,
            "components": {
                "cross_sectional_percentile": cross,
                "dynamic_threshold": dynamic,
                "data_reliability": {
                    "score": reliability_score,
                    "reliability": reliability,
                    "reason": " ".join(reliability_reasons) or "数据质量满足评分口径。",
                },
                "liquidity_premium": liquidity_premium,
            },
            "weights": {
                "cross_sectional_percentile": 0.52,
                "dynamic_threshold": 0.20,
                "data_reliability": 0.16,
                "liquidity_premium": 0.12,
            },
            "limitation_reasons": limitation_reasons,
        }
    return results


def apply_label_evidence(
    breakdown: Mapping[str, Any],
    *,
    confidence: str | None,
    sample_count: int,
    median_return: float | None = None,
    win_rate: float | None = None,
) -> dict[str, Any]:
    updated = dict(breakdown)
    components = dict(updated.get("components") or {})
    weights = dict(updated.get("weights") or {})
    weights.pop("label_evidence", None)
    if sample_count < 20 or not confidence:
        evidence_state = "insufficient"
        reason = "标签样本不足，仅用于展示，不调整排序。"
    elif confidence == "recent_weakening":
        evidence_state = "stale"
        reason = "标签验证已转弱，仅用于展示，不调整排序。"
    elif (median_return is not None and median_return < 0) or (win_rate is not None and win_rate < 0.5):
        evidence_state = "negative"
        reason = "标签历史结果偏弱，仅用于展示，不调整排序。"
    elif confidence != "sufficient":
        evidence_state = "inconclusive"
        reason = "标签证据尚无定论，仅用于展示，不调整排序。"
    else:
        evidence_state = "high_sample"
        reason = "标签样本充足，仅用于展示，不调整排序。"
    components["label_evidence"] = {
        "score_contribution": 0.0,
        "evidence_state": evidence_state,
        "confidence": confidence or "insufficient",
        "sample_count": sample_count,
        "median_return": median_return,
        "win_rate": win_rate,
        "reason": reason,
        "display_only": True,
    }
    updated["components"] = components
    updated["weights"] = weights
    return updated
