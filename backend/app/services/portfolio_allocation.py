from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any

from app.services.etf_research_evidence import stable_contract_hash

PORTFOLIO_SINGLE_WEIGHT_CAP = 0.30
PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP = 0.15
PORTFOLIO_SATELLITE_EXPOSURE_CAP = 0.35
PORTFOLIO_TOTAL_EXPOSURE_CAP = 1.00
PORTFOLIO_THEME_EXPOSURE_CAP = 0.60
PORTFOLIO_HIGH_CORRELATION = 0.85
PORTFOLIO_CORRELATION_MIN_POINTS = 40
PORTFOLIO_CORRELATION_CLUSTER_CAP = 0.50
PORTFOLIO_RISK_METRIC_MIN_POINTS = 60
PORTFOLIO_MARKET_BREADTH_MIN_ASSETS = 2
PORTFOLIO_RISK_BUDGET_VERSION = "portfolio_risk_budget_v1"
PORTFOLIO_MARKET_CLASSIFIER_THRESHOLDS = {
    "risk_on_breadth_min": 0.60,
    "risk_on_above_ma20_min": 0.60,
    "defensive_breadth_max_exclusive": 0.40,
    "defensive_above_ma20_max_exclusive": 0.50,
}
PORTFOLIO_ASSET_RISK_REDUCER = {
    "risk_flag_deduction": 0.05,
    "volatility_20d_threshold": 0.025,
    "volatility_deduction": 0.03,
    "max_drawdown_60d_threshold": -0.12,
    "drawdown_deduction": 0.03,
    "display_ineligible_deduction": 0.05,
    "minimum_positive_cap": 0.05,
}
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

PORTFOLIO_MARKET_STATE_LIMITS: dict[str, dict[str, float]] = {
    PORTFOLIO_MODE_RISK_ON: {
        "risk_exposure_cap": 1.00,
        "minimum_cash_weight": 0.00,
    },
    PORTFOLIO_MODE_NEUTRAL: {
        "risk_exposure_cap": 0.60,
        "minimum_cash_weight": 0.20,
    },
    PORTFOLIO_MODE_DEFENSIVE: {
        "risk_exposure_cap": 0.25,
        "minimum_cash_weight": 0.40,
    },
    PORTFOLIO_MODE_CASH_WAIT: {
        "risk_exposure_cap": 0.00,
        "minimum_cash_weight": 1.00,
    },
}

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

ReturnSeries = Mapping[Any, float] | Sequence[float]


@dataclass(frozen=True)
class MarketRiskObservation:
    code: str
    return_20d: float | None
    distance_to_ma20: float | None
    decision_eligible: bool
    broad_equity: bool = True


@dataclass(frozen=True)
class MarketRiskStateResult:
    state: str
    status: str
    metrics: dict[str, Any]
    unavailable_reasons: tuple[str, ...]
    contract_version: str
    contract_hash: str


@dataclass(frozen=True)
class ProportionalAllocationResult:
    weights: dict[str, float]
    invested_weight: float
    cash_weight: float
    binding_constraints: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioRiskBudgetResult:
    status: str
    market_state: str
    weights: dict[str, float]
    invested_weight: float
    cash_weight: float
    constraints: dict[str, Any]
    metrics: dict[str, Any]
    binding_constraints: tuple[str, ...]
    unavailable_reasons: tuple[str, ...]
    contract_version: str
    contract_hash: str


def portfolio_risk_budget_manifest() -> dict[str, Any]:
    payload = {
        "version": PORTFOLIO_RISK_BUDGET_VERSION,
        "market_state_limits": PORTFOLIO_MARKET_STATE_LIMITS,
        "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
        "satellite_single_weight_cap": PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
        "satellite_exposure_cap": PORTFOLIO_SATELLITE_EXPOSURE_CAP,
        "theme_exposure_cap": PORTFOLIO_THEME_EXPOSURE_CAP,
        "correlation_threshold": PORTFOLIO_HIGH_CORRELATION,
        "correlation_min_points": PORTFOLIO_CORRELATION_MIN_POINTS,
        "correlation_cluster_cap": PORTFOLIO_CORRELATION_CLUSTER_CAP,
        "risk_metric_min_points": PORTFOLIO_RISK_METRIC_MIN_POINTS,
        "market_breadth_min_assets": PORTFOLIO_MARKET_BREADTH_MIN_ASSETS,
        "market_classifier_thresholds": PORTFOLIO_MARKET_CLASSIFIER_THRESHOLDS,
        "asset_risk_reducer": PORTFOLIO_ASSET_RISK_REDUCER,
    }
    return {**payload, "contract_hash": stable_contract_hash(payload)}


def classify_portfolio_market_risk(
    observations: Sequence[MarketRiskObservation],
) -> MarketRiskStateResult:
    manifest = portfolio_risk_budget_manifest()
    eligible: list[MarketRiskObservation] = []
    for item in observations:
        if not item.broad_equity or not item.decision_eligible:
            continue
        if item.return_20d is None or item.distance_to_ma20 is None:
            continue
        if not math.isfinite(float(item.return_20d)) or not math.isfinite(float(item.distance_to_ma20)):
            continue
        eligible.append(item)
    if len(eligible) < PORTFOLIO_MARKET_BREADTH_MIN_ASSETS:
        return MarketRiskStateResult(
            state=PORTFOLIO_MODE_CASH_WAIT,
            status="unavailable",
            metrics={
                "eligible_broad_asset_count": len(eligible),
                "required_broad_asset_count": PORTFOLIO_MARKET_BREADTH_MIN_ASSETS,
            },
            unavailable_reasons=("insufficient_eligible_broad_equity_evidence",),
            contract_version=PORTFOLIO_RISK_BUDGET_VERSION,
            contract_hash=str(manifest["contract_hash"]),
        )

    positive_ratio = sum(float(item.return_20d or 0.0) > 0 for item in eligible) / len(eligible)
    above_ma20_ratio = sum(float(item.distance_to_ma20 or 0.0) >= 0 for item in eligible) / len(eligible)
    breadth_ratio = sum(
        float(item.return_20d or 0.0) > 0 and float(item.distance_to_ma20 or 0.0) >= 0
        for item in eligible
    ) / len(eligible)
    average_return_20d = mean(float(item.return_20d or 0.0) for item in eligible)
    if (
        breadth_ratio >= PORTFOLIO_MARKET_CLASSIFIER_THRESHOLDS["risk_on_breadth_min"]
        and above_ma20_ratio
        >= PORTFOLIO_MARKET_CLASSIFIER_THRESHOLDS["risk_on_above_ma20_min"]
        and average_return_20d > 0
    ):
        state = PORTFOLIO_MODE_RISK_ON
    elif (
        breadth_ratio
        < PORTFOLIO_MARKET_CLASSIFIER_THRESHOLDS["defensive_breadth_max_exclusive"]
        and above_ma20_ratio
        < PORTFOLIO_MARKET_CLASSIFIER_THRESHOLDS[
            "defensive_above_ma20_max_exclusive"
        ]
        and average_return_20d < 0
    ):
        state = PORTFOLIO_MODE_DEFENSIVE
    else:
        state = PORTFOLIO_MODE_NEUTRAL
    return MarketRiskStateResult(
        state=state,
        status="ready",
        metrics={
            "eligible_broad_asset_count": len(eligible),
            "positive_20d_ratio": round(positive_ratio, 6),
            "above_ma20_ratio": round(above_ma20_ratio, 6),
            "breadth_ratio": round(breadth_ratio, 6),
            "average_return_20d": round(average_return_20d, 8),
        },
        unavailable_reasons=(),
        contract_version=PORTFOLIO_RISK_BUDGET_VERSION,
        contract_hash=str(manifest["contract_hash"]),
    )


def portfolio_asset_risk_cap(
    *,
    base_cap: float,
    risk_flags: Sequence[str] = (),
    volatility_20d: float | None = None,
    max_drawdown_60d: float | None = None,
    display_eligible: bool = True,
) -> float:
    if not math.isfinite(float(base_cap)) or float(base_cap) <= 0:
        return 0.0
    cap = min(PORTFOLIO_SINGLE_WEIGHT_CAP, float(base_cap))
    if any(flag in PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT for flag in risk_flags):
        cap -= PORTFOLIO_ASSET_RISK_REDUCER["risk_flag_deduction"]
    if (
        volatility_20d is not None
        and math.isfinite(float(volatility_20d))
        and float(volatility_20d)
        > PORTFOLIO_ASSET_RISK_REDUCER["volatility_20d_threshold"]
    ):
        cap -= PORTFOLIO_ASSET_RISK_REDUCER["volatility_deduction"]
    if (
        max_drawdown_60d is not None
        and math.isfinite(float(max_drawdown_60d))
        and float(max_drawdown_60d)
        < PORTFOLIO_ASSET_RISK_REDUCER["max_drawdown_60d_threshold"]
    ):
        cap -= PORTFOLIO_ASSET_RISK_REDUCER["drawdown_deduction"]
    if not display_eligible:
        cap -= PORTFOLIO_ASSET_RISK_REDUCER["display_ineligible_deduction"]
    return float(
        min(
            base_cap,
            max(PORTFOLIO_ASSET_RISK_REDUCER["minimum_positive_cap"], cap),
        )
    )


def proportional_capped_redistribution(
    raw_weights: Mapping[str, float],
    individual_caps: Mapping[str, float],
    *,
    group_caps: Mapping[str, tuple[Sequence[str], float]] | None = None,
    target_total: float = 1.0,
) -> ProportionalAllocationResult:
    cleaned = {
        str(code): float(weight)
        for code, weight in raw_weights.items()
        if isinstance(weight, int | float)
        and not isinstance(weight, bool)
        and math.isfinite(float(weight))
        and float(weight) > 0
    }
    target = min(1.0, max(0.0, float(target_total)))
    if not cleaned or target <= 0:
        return ProportionalAllocationResult({}, 0.0, 1.0, ("target_exposure_zero",))

    caps = {
        code: min(1.0, max(0.0, float(individual_caps.get(code, 0.0))))
        for code in cleaned
    }
    groups: dict[str, tuple[frozenset[str], float]] = {}
    for name, (members, cap) in sorted((group_caps or {}).items()):
        member_set = frozenset(str(code) for code in members if str(code) in cleaned)
        if member_set:
            groups[str(name)] = (member_set, min(1.0, max(0.0, float(cap))))

    weights = {code: 0.0 for code in sorted(cleaned)}
    tolerance = 1e-10
    max_rounds = len(weights) + len(groups) + 4
    for _round in range(max_rounds):
        invested = sum(weights.values())
        remaining = target - invested
        if remaining <= tolerance:
            break
        group_used = {
            name: sum(weights[code] for code in members)
            for name, (members, _cap) in groups.items()
        }
        active: list[str] = []
        for code in sorted(cleaned):
            if caps[code] - weights[code] <= tolerance:
                continue
            if any(
                code in members and cap - group_used[name] <= tolerance
                for name, (members, cap) in groups.items()
            ):
                continue
            active.append(code)
        if not active:
            break
        active_raw_total = sum(cleaned[code] for code in active)
        if active_raw_total <= tolerance:
            break
        alpha_limits = [remaining / active_raw_total]
        alpha_limits.extend(
            (caps[code] - weights[code]) / cleaned[code]
            for code in active
        )
        for name, (members, cap) in groups.items():
            active_group_raw = sum(cleaned[code] for code in active if code in members)
            if active_group_raw > tolerance:
                alpha_limits.append((cap - group_used[name]) / active_group_raw)
        alpha = max(0.0, min(alpha_limits))
        if alpha <= tolerance:
            break
        for code in active:
            weights[code] += alpha * cleaned[code]

    rounded = {
        code: round(weight, 8)
        for code, weight in weights.items()
        if weight > 1e-8
    }
    invested = min(1.0, max(0.0, sum(rounded.values())))
    binding: list[str] = []
    for code, cap in caps.items():
        if cap > 0 and abs(rounded.get(code, 0.0) - cap) <= 1e-6:
            binding.append(f"asset:{code}")
    for name, (members, cap) in groups.items():
        exposure = sum(rounded.get(code, 0.0) for code in members)
        if cap >= 0 and abs(exposure - cap) <= 1e-6:
            binding.append(name)
    if invested + 1e-6 < target:
        binding.append("capacity_residual_to_cash")
    return ProportionalAllocationResult(
        weights=rounded,
        invested_weight=round(invested, 8),
        cash_weight=round(max(0.0, 1.0 - invested), 8),
        binding_constraints=tuple(dict.fromkeys(binding)),
    )


def _aligned_return_values(left: ReturnSeries, right: ReturnSeries) -> tuple[list[float], list[float]]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        common = sorted(set(left).intersection(right), key=str)
        pairs = [
            (float(left[key]), float(right[key]))
            for key in common
            if math.isfinite(float(left[key])) and math.isfinite(float(right[key]))
        ]
        return [item[0] for item in pairs], [item[1] for item in pairs]
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        return [], []
    size = min(len(left), len(right))
    pairs = [
        (float(a), float(b))
        for a, b in zip(left[-size:], right[-size:], strict=False)
        if math.isfinite(float(a)) and math.isfinite(float(b))
    ]
    return [item[0] for item in pairs], [item[1] for item in pairs]


def _return_correlation(left: ReturnSeries, right: ReturnSeries) -> tuple[float | None, int]:
    left_values, right_values = _aligned_return_values(left, right)
    size = len(left_values)
    if size < PORTFOLIO_CORRELATION_MIN_POINTS:
        return None, size
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_values, right_values, strict=True)
    )
    left_var = sum((value - left_mean) ** 2 for value in left_values)
    right_var = sum((value - right_mean) ** 2 for value in right_values)
    if left_var <= 0 or right_var <= 0:
        return None, size
    return float(numerator / (left_var * right_var) ** 0.5), size


def _correlation_clusters(
    codes: Sequence[str],
    returns_by_code: Mapping[str, ReturnSeries],
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...], int]:
    ordered = sorted(dict.fromkeys(codes))
    parent = {code: code for code in ordered}

    def find(code: str) -> str:
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    missing: list[str] = []
    pair_count = 0
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            left_returns = returns_by_code.get(left)
            right_returns = returns_by_code.get(right)
            if left_returns is None or right_returns is None:
                missing.append(f"{left}:{right}")
                continue
            correlation, sample_count = _return_correlation(left_returns, right_returns)
            if correlation is None:
                missing.append(f"{left}:{right}:{sample_count}")
                continue
            pair_count += 1
            if correlation >= PORTFOLIO_HIGH_CORRELATION:
                union(left, right)

    grouped: dict[str, list[str]] = {}
    for code in ordered:
        grouped.setdefault(find(code), []).append(code)
    clusters = {
        f"cluster:{'+'.join(members)}": tuple(members)
        for _root, members in sorted(grouped.items())
        if len(members) > 1
    }
    return clusters, tuple(missing), pair_count


def _common_portfolio_returns(
    codes: Sequence[str],
    returns_by_code: Mapping[str, ReturnSeries],
) -> list[dict[str, float]]:
    ordered = sorted(dict.fromkeys(codes))
    if not ordered:
        return []
    series = [returns_by_code.get(code) for code in ordered]
    if any(item is None for item in series):
        return []
    if all(isinstance(item, Mapping) for item in series):
        mapping_series = [item for item in series if isinstance(item, Mapping)]
        common_keys = set(mapping_series[0])
        for item in mapping_series[1:]:
            common_keys.intersection_update(item)
        rows: list[dict[str, float]] = []
        for key in sorted(common_keys, key=str):
            values = {code: float(returns_by_code[code][key]) for code in ordered}  # type: ignore[index]
            if all(math.isfinite(value) for value in values.values()):
                rows.append(values)
        return rows
    if any(isinstance(item, Mapping) for item in series):
        return []
    sequence_series = [item for item in series if isinstance(item, Sequence)]
    size = min(len(item) for item in sequence_series)
    rows = []
    for offset in range(-size, 0):
        values = {code: float(returns_by_code[code][offset]) for code in ordered}  # type: ignore[index]
        if all(math.isfinite(value) for value in values.values()):
            rows.append(values)
    return rows


def _unavailable_risk_budget(
    *,
    market_state: str,
    reasons: Sequence[str],
    market_metrics: Mapping[str, Any] | None = None,
) -> PortfolioRiskBudgetResult:
    manifest = portfolio_risk_budget_manifest()
    return PortfolioRiskBudgetResult(
        status="unavailable",
        market_state=PORTFOLIO_MODE_CASH_WAIT,
        weights={},
        invested_weight=0.0,
        cash_weight=1.0,
        constraints={
            **manifest,
            "requested_market_state": market_state,
            "risk_exposure_cap": 0.0,
            "minimum_cash_weight": 1.0,
        },
        metrics={
            "threshold_status": "unavailable",
            "market_state_metrics": dict(market_metrics or {}),
        },
        binding_constraints=("cash_wait",),
        unavailable_reasons=tuple(dict.fromkeys(str(reason) for reason in reasons)),
        contract_version=PORTFOLIO_RISK_BUDGET_VERSION,
        contract_hash=str(manifest["contract_hash"]),
    )


def apply_portfolio_risk_budget(
    raw_weights: Mapping[str, float],
    *,
    layer_by_code: Mapping[str, str],
    theme_by_code: Mapping[str, str],
    returns_by_code: Mapping[str, ReturnSeries],
    market_risk: MarketRiskStateResult,
    individual_caps: Mapping[str, float] | None = None,
    max_total_exposure: float | None = None,
) -> PortfolioRiskBudgetResult:
    codes = sorted(
        str(code)
        for code, weight in raw_weights.items()
        if isinstance(weight, int | float)
        and not isinstance(weight, bool)
        and math.isfinite(float(weight))
        and float(weight) > 0
    )
    if market_risk.status != "ready" or market_risk.state == PORTFOLIO_MODE_CASH_WAIT:
        return _unavailable_risk_budget(
            market_state=market_risk.state,
            reasons=market_risk.unavailable_reasons or ("market_risk_state_unavailable",),
            market_metrics=market_risk.metrics,
        )
    if market_risk.state not in PORTFOLIO_MARKET_STATE_LIMITS:
        return _unavailable_risk_budget(
            market_state=market_risk.state,
            reasons=("unsupported_market_risk_state",),
            market_metrics=market_risk.metrics,
        )
    if not codes:
        return _unavailable_risk_budget(
            market_state=market_risk.state,
            reasons=("no_positive_candidate_weights",),
            market_metrics=market_risk.metrics,
        )
    missing_layers = [code for code in codes if layer_by_code.get(code) not in {
        PORTFOLIO_LAYER_PRIMARY,
        PORTFOLIO_LAYER_SATELLITE,
        PORTFOLIO_LAYER_DEFENSIVE,
    }]
    if missing_layers:
        return _unavailable_risk_budget(
            market_state=market_risk.state,
            reasons=("portfolio_layer_evidence_missing",),
            market_metrics=market_risk.metrics,
        )

    common_rows = _common_portfolio_returns(codes, returns_by_code)
    clusters, missing_pairs, comparable_pair_count = _correlation_clusters(codes, returns_by_code)
    unavailable_reasons: list[str] = []
    if missing_pairs:
        unavailable_reasons.append("correlation_evidence_insufficient")
    if len(common_rows) < PORTFOLIO_RISK_METRIC_MIN_POINTS:
        unavailable_reasons.append("portfolio_risk_history_insufficient")
    if unavailable_reasons:
        result = _unavailable_risk_budget(
            market_state=market_risk.state,
            reasons=unavailable_reasons,
            market_metrics=market_risk.metrics,
        )
        return PortfolioRiskBudgetResult(
            **{
                **result.__dict__,
                "metrics": {
                    **result.metrics,
                    "common_sample_count": len(common_rows),
                    "missing_correlation_pairs": list(missing_pairs),
                },
            }
        )

    limits = PORTFOLIO_MARKET_STATE_LIMITS[market_risk.state]
    total_exposure_cap = 1.0 - limits["minimum_cash_weight"]
    if max_total_exposure is not None:
        if not math.isfinite(float(max_total_exposure)) or float(max_total_exposure) < 0:
            return _unavailable_risk_budget(
                market_state=market_risk.state,
                reasons=("source_total_exposure_invalid",),
                market_metrics=market_risk.metrics,
            )
        total_exposure_cap = min(total_exposure_cap, float(max_total_exposure))

    caps: dict[str, float] = {}
    for code in codes:
        default_cap = (
            PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP
            if layer_by_code[code] == PORTFOLIO_LAYER_SATELLITE
            else PORTFOLIO_SINGLE_WEIGHT_CAP
        )
        cap = (individual_caps or {}).get(code, default_cap)
        caps[code] = min(default_cap, max(0.0, float(cap)))

    satellite_codes = [code for code in codes if layer_by_code[code] == PORTFOLIO_LAYER_SATELLITE]
    risk_codes = [
        code
        for code in codes
        if layer_by_code[code] in {PORTFOLIO_LAYER_PRIMARY, PORTFOLIO_LAYER_SATELLITE}
    ]
    theme_members: dict[str, list[str]] = {}
    for code in codes:
        theme_members.setdefault(str(theme_by_code.get(code) or "unknown"), []).append(code)
    group_caps: dict[str, tuple[Sequence[str], float]] = {
        "risk_exposure_cap": (risk_codes, limits["risk_exposure_cap"]),
        **{
            f"theme:{theme}": (members, PORTFOLIO_THEME_EXPOSURE_CAP)
            for theme, members in sorted(theme_members.items())
        },
        **{
            name: (members, PORTFOLIO_CORRELATION_CLUSTER_CAP)
            for name, members in clusters.items()
        },
    }
    if satellite_codes:
        group_caps["satellite_exposure_cap"] = (
            satellite_codes,
            PORTFOLIO_SATELLITE_EXPOSURE_CAP,
        )
    allocation = proportional_capped_redistribution(
        {code: float(raw_weights[code]) for code in codes},
        caps,
        group_caps=group_caps,
        target_total=total_exposure_cap,
    )

    theme_exposure = {
        theme: round(sum(allocation.weights.get(code, 0.0) for code in members), 8)
        for theme, members in sorted(theme_members.items())
    }
    cluster_exposure = {
        name.removeprefix("cluster:"): round(
            sum(allocation.weights.get(code, 0.0) for code in members),
            8,
        )
        for name, members in clusters.items()
    }
    portfolio_returns = [
        sum(allocation.weights.get(code, 0.0) * row[code] for code in codes)
        for row in common_rows[-PORTFOLIO_RISK_METRIC_MIN_POINTS:]
    ]
    annualized_volatility = (
        pstdev(portfolio_returns) * math.sqrt(252)
        if len(portfolio_returns) > 1
        else None
    )
    wealth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for item in portfolio_returns:
        wealth *= 1.0 + item
        peak = max(peak, wealth)
        if peak > 0:
            max_drawdown = min(max_drawdown, wealth / peak - 1.0)
    risk_exposure = sum(allocation.weights.get(code, 0.0) for code in risk_codes)
    satellite_exposure = sum(allocation.weights.get(code, 0.0) for code in satellite_codes)
    manifest = portfolio_risk_budget_manifest()
    constraints = {
        **manifest,
        "market_state": market_risk.state,
        "risk_exposure_cap": limits["risk_exposure_cap"],
        "minimum_cash_weight": limits["minimum_cash_weight"],
        "total_exposure_cap": round(total_exposure_cap, 8),
    }
    metrics = {
        "threshold_status": "passed",
        "market_state_metrics": market_risk.metrics,
        "portfolio_annualized_volatility": (
            round(annualized_volatility, 8)
            if annualized_volatility is not None
            else None
        ),
        "portfolio_max_drawdown_60d": round(max_drawdown, 8),
        "common_sample_count": len(common_rows),
        "comparable_pair_count": comparable_pair_count,
        "theme_exposure": theme_exposure,
        "correlation_cluster_exposure": cluster_exposure,
        "risk_exposure_weight": round(risk_exposure, 8),
        "satellite_exposure_weight": round(satellite_exposure, 8),
        "max_single_weight": max(allocation.weights.values(), default=0.0),
        "invested_weight": allocation.invested_weight,
        "cash_weight": allocation.cash_weight,
    }
    return PortfolioRiskBudgetResult(
        status="ready",
        market_state=market_risk.state,
        weights=allocation.weights,
        invested_weight=allocation.invested_weight,
        cash_weight=allocation.cash_weight,
        constraints=constraints,
        metrics=metrics,
        binding_constraints=allocation.binding_constraints,
        unavailable_reasons=(),
        contract_version=PORTFOLIO_RISK_BUDGET_VERSION,
        contract_hash=str(manifest["contract_hash"]),
    )


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
    if not cleaned:
        return None
    theme_members: dict[str, list[str]] = {}
    for code in cleaned:
        theme = theme_by_code.get(code) or "unknown"
        theme_members.setdefault(theme, []).append(code)
    allocation = proportional_capped_redistribution(
        cleaned,
        {code: single_cap for code in cleaned},
        group_caps={
            f"theme:{theme}": (members, theme_cap)
            for theme, members in theme_members.items()
        },
        target_total=target_total,
    )
    return allocation.weights or None


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
            unavailable_reason="约束下无法生成可验证的长-only Black-Litterman 权重。",
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
            "weight_sum": round(sum(weights.values()), 6),
            "cash_weight": round(max(0.0, 1.0 - sum(weights.values())), 6),
            "excluded_count": len(excluded),
            "research_only": True,
            "no_trade_instruction": True,
        },
    )
