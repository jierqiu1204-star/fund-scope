from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean
from typing import Any

from app.services.etf_research_evidence import stable_contract_hash

PORTFOLIO_RISK_SHADOW_VERSION = "portfolio_risk_shadow_v1"
PORTFOLIO_RISK_SHADOW_MAX_ASSETS = 20
PORTFOLIO_RISK_SHADOW_LOOKBACK_DAYS = 120
PORTFOLIO_RISK_SHADOW_MIN_COMMON_DAYS = 60
PORTFOLIO_RISK_SHADOW_DIAGONAL_SHRINKAGE = 0.25
PORTFOLIO_RISK_SHADOW_ANNUALIZATION_DAYS = 252
PORTFOLIO_RISK_SHADOW_MIN_MARKET_BUCKETS = 3
PORTFOLIO_RISK_SHADOW_RISK_OFF_RATIO = 0.40
PORTFOLIO_RISK_SHADOW_RECOVERY_RATIO = 0.60
PORTFOLIO_RISK_SHADOW_RECOVERY_SESSIONS = 2

PORTFOLIO_RISK_FACTOR_NAMES = (
    "market",
    "small_vs_large",
    "growth_vs_large",
)

_BROAD_MARKET_BUCKET_SHOCKS = {
    "equity": -0.05,
    "broad_base": -0.05,
    "cross_border": -0.05,
    "commodity": -0.03,
    "bond": -0.015,
    "money": 0.0,
}
_DOMINANT_THEME_OTHER_BUCKET_SHOCKS = {
    "equity": -0.02,
    "broad_base": -0.02,
    "cross_border": -0.02,
    "commodity": -0.02,
    "bond": -0.005,
    "money": 0.0,
}
_DOMINANT_THEME_SHOCK = -0.08
_CORRELATION_STRESS_SIGMA = 2.0
_BASE_FEE_RATE = 0.0005
_BASE_SLIPPAGE_RATE = 0.0005
_LIQUIDITY_STRESS_SLIPPAGE_MULTIPLIER = 3.0
_UNKNOWN_LABELS = {"", "unknown", "unresolved", "none", "未分类"}
_WEIGHT_TOLERANCE = 1e-9
_VARIANCE_EPSILON = 1e-18


@dataclass(frozen=True)
class MarketRiskPoolObservation:
    code: str
    bucket: str
    tracked_underlying_id: str | None
    return_20d: float | None
    distance_to_ma20: float | None
    decision_eligible: bool
    data_date: date | None
    data_cutoff: datetime | None
    priority: int = 100

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "bucket": self.bucket,
            "tracked_underlying_id": self.tracked_underlying_id,
            "return_20d": self.return_20d,
            "distance_to_ma20": self.distance_to_ma20,
            "decision_eligible": self.decision_eligible,
            "data_date": self.data_date,
            "data_cutoff": self.data_cutoff,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class MarketRiskRegimeShadowDecision:
    state: str
    raw_state: str
    trade_session: date
    recovery_sessions: tuple[date, ...]
    reason_codes: tuple[str, ...]
    changed: bool
    input_hash: str
    contract_version: str
    contract_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "raw_state": self.raw_state,
            "trade_session": self.trade_session.isoformat(),
            "recovery_sessions": [item.isoformat() for item in self.recovery_sessions],
            "reason_codes": list(self.reason_codes),
            "changed": self.changed,
            "input_hash": self.input_hash,
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
        }


@dataclass(frozen=True)
class PortfolioRiskAssetInput:
    code: str
    weight: float
    clone_group_id: str | None
    theme_group: str | None
    asset_bucket: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "weight": self.weight,
            "clone_group_id": self.clone_group_id,
            "theme_group": self.theme_group,
            "asset_bucket": self.asset_bucket,
        }


@dataclass(frozen=True)
class RiskShadowComponentResult:
    component: str
    status: str
    metrics: dict[str, Any]
    unavailable_reasons: tuple[str, ...]
    input_hash: str
    contract_version: str
    contract_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "status": self.status,
            "metrics": self.metrics,
            "unavailable_reasons": list(self.unavailable_reasons),
            "input_hash": self.input_hash,
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
        }


@dataclass(frozen=True)
class PortfolioRiskShadowResult:
    status: str
    exposure: RiskShadowComponentResult
    factor_beta: RiskShadowComponentResult
    marginal_risk_contribution: RiskShadowComponentResult
    stress_scenarios: RiskShadowComponentResult
    unavailable_reasons: tuple[str, ...]
    input_hash: str
    contract_version: str
    contract_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exposure": self.exposure.as_dict(),
            "factor_beta": self.factor_beta.as_dict(),
            "marginal_risk_contribution": self.marginal_risk_contribution.as_dict(),
            "stress_scenarios": self.stress_scenarios.as_dict(),
            "unavailable_reasons": list(self.unavailable_reasons),
            "input_hash": self.input_hash,
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
        }


def portfolio_risk_shadow_manifest() -> dict[str, Any]:
    payload = {
        "version": PORTFOLIO_RISK_SHADOW_VERSION,
        "max_assets": PORTFOLIO_RISK_SHADOW_MAX_ASSETS,
        "lookback_days": PORTFOLIO_RISK_SHADOW_LOOKBACK_DAYS,
        "minimum_common_days": PORTFOLIO_RISK_SHADOW_MIN_COMMON_DAYS,
        "diagonal_shrinkage": PORTFOLIO_RISK_SHADOW_DIAGONAL_SHRINKAGE,
        "annualization_days": PORTFOLIO_RISK_SHADOW_ANNUALIZATION_DAYS,
        "minimum_market_buckets": PORTFOLIO_RISK_SHADOW_MIN_MARKET_BUCKETS,
        "market_regime": {
            "risk_off_ratio": PORTFOLIO_RISK_SHADOW_RISK_OFF_RATIO,
            "recovery_ratio": PORTFOLIO_RISK_SHADOW_RECOVERY_RATIO,
            "recovery_sessions": PORTFOLIO_RISK_SHADOW_RECOVERY_SESSIONS,
            "downgrade": "immediate",
            "upgrade": "distinct_session_hysteresis",
        },
        "factor_names": list(PORTFOLIO_RISK_FACTOR_NAMES),
        "stress_scenarios": {
            "broad_market_-5pct": {
                "bucket_shocks": dict(_BROAD_MARKET_BUCKET_SHOCKS),
            },
            "dominant_theme_-8pct": {
                "dominant_theme_shock": _DOMINANT_THEME_SHOCK,
                "other_bucket_shocks": dict(_DOMINANT_THEME_OTHER_BUCKET_SHOCKS),
            },
            "correlation_one_2sigma": {
                "correlation": 1.0,
                "sigma": _CORRELATION_STRESS_SIGMA,
            },
            "liquidity_cost_3x": {
                "fee_rate": _BASE_FEE_RATE,
                "base_slippage_rate": _BASE_SLIPPAGE_RATE,
                "slippage_multiplier": _LIQUIDITY_STRESS_SLIPPAGE_MULTIPLIER,
            },
        },
    }
    return {**payload, "contract_hash": stable_contract_hash(payload)}


def _contract() -> tuple[str, str]:
    manifest = portfolio_risk_shadow_manifest()
    return PORTFOLIO_RISK_SHADOW_VERSION, str(manifest["contract_hash"])


def _stable_reasons(reasons: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({reason for reason in reasons if reason}))


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _known_label(value: str | None) -> str | None:
    if value is None:
        return None
    result = value.strip()
    return None if result.lower() in _UNKNOWN_LABELS else result


def _rounded(value: float) -> float:
    return round(float(value), 12)


def _normalise_return_series(series: Mapping[date, float]) -> tuple[dict[date, float], int]:
    result: dict[date, float] = {}
    rejected = 0
    for point_date, raw_value in series.items():
        if not isinstance(point_date, date) or isinstance(point_date, datetime):
            rejected += 1
            continue
        value = _finite_float(raw_value)
        if value is None:
            rejected += 1
            continue
        result[point_date] = value
    return result, rejected


def _normalised_return_payload(
    return_maps: Mapping[str, Mapping[date, float]],
) -> tuple[dict[str, list[list[Any]]], int]:
    payload: dict[str, list[list[Any]]] = {}
    rejected = 0
    for raw_code in sorted(return_maps, key=str):
        code = str(raw_code)
        normalised, series_rejected = _normalise_return_series(return_maps[raw_code])
        rejected += series_rejected
        payload[code] = [
            [point_date.isoformat(), _rounded(value)]
            for point_date, value in sorted(normalised.items())
        ]
    return payload, rejected


def _component_result(
    component: str,
    *,
    metrics: dict[str, Any],
    unavailable_reasons: Sequence[str],
    input_payload: dict[str, Any],
) -> RiskShadowComponentResult:
    version, contract_hash = _contract()
    reasons = _stable_reasons(unavailable_reasons)
    return RiskShadowComponentResult(
        component=component,
        status="unavailable" if reasons else "ready",
        metrics=metrics,
        unavailable_reasons=reasons,
        input_hash=stable_contract_hash(input_payload),
        contract_version=version,
        contract_hash=contract_hash,
    )


def select_independent_market_risk_pool(
    observations: Sequence[MarketRiskPoolObservation],
    *,
    required_data_date: date,
    required_data_cutoff: datetime,
    required_bucket_count: int = PORTFOLIO_RISK_SHADOW_MIN_MARKET_BUCKETS,
) -> RiskShadowComponentResult:
    input_payload = {
        "required_data_date": required_data_date,
        "required_data_cutoff": required_data_cutoff,
        "required_bucket_count": required_bucket_count,
        "observations": [
            item.as_dict()
            for item in sorted(
                observations,
                key=lambda item: (
                    item.bucket,
                    item.priority,
                    item.code,
                    item.tracked_underlying_id or "",
                    item.data_date.isoformat() if item.data_date else "",
                    item.data_cutoff.isoformat() if item.data_cutoff else "",
                ),
            )
        ],
    }
    if required_bucket_count <= 0:
        return _component_result(
            "market_risk_pool",
            metrics={"selected_bucket_count": 0, "selected_observations": []},
            unavailable_reasons=("invalid_required_market_bucket_count",),
            input_payload=input_payload,
        )

    valid_by_bucket: dict[str, list[MarketRiskPoolObservation]] = {}
    rejection_counts: dict[str, int] = {
        "decision_ineligible": 0,
        "missing_identity": 0,
        "non_finite": 0,
        "cutoff_mismatch": 0,
        "date_mismatch": 0,
        "invalid_code_or_bucket": 0,
    }
    for item in observations:
        code = item.code.strip()
        bucket = _known_label(item.bucket)
        underlying = _known_label(item.tracked_underlying_id)
        if not code or bucket is None:
            rejection_counts["invalid_code_or_bucket"] += 1
            continue
        if not item.decision_eligible:
            rejection_counts["decision_ineligible"] += 1
            continue
        if underlying is None:
            rejection_counts["missing_identity"] += 1
            continue
        if _finite_float(item.return_20d) is None or _finite_float(item.distance_to_ma20) is None:
            rejection_counts["non_finite"] += 1
            continue
        if item.data_date != required_data_date:
            rejection_counts["date_mismatch"] += 1
            continue
        if item.data_cutoff != required_data_cutoff:
            rejection_counts["cutoff_mismatch"] += 1
            continue
        valid_by_bucket.setdefault(bucket, []).append(item)

    selected: list[MarketRiskPoolObservation] = []
    used_underlyings: set[str] = set()
    duplicate_underlying_count = 0
    duplicate_bucket_candidate_count = sum(max(0, len(rows) - 1) for rows in valid_by_bucket.values())
    for bucket in sorted(valid_by_bucket):
        for item in sorted(valid_by_bucket[bucket], key=lambda row: (row.priority, row.code)):
            underlying = _known_label(item.tracked_underlying_id)
            if underlying is None:
                continue
            if underlying in used_underlyings:
                duplicate_underlying_count += 1
                continue
            selected.append(item)
            used_underlyings.add(underlying)
            break

    selected.sort(key=lambda item: (item.bucket, item.priority, item.code))
    reasons: list[str] = []
    if len(selected) < required_bucket_count:
        reasons.append("insufficient_distinct_market_buckets")
    selected_returns = [float(item.return_20d or 0.0) for item in selected]
    selected_distances = [float(item.distance_to_ma20 or 0.0) for item in selected]
    metrics = {
        "required_bucket_count": required_bucket_count,
        "selected_bucket_count": len(selected),
        "selected_underlying_count": len(used_underlyings),
        "selected_observations": [item.as_dict() for item in selected],
        "duplicate_bucket_candidate_count": duplicate_bucket_candidate_count,
        "duplicate_underlying_count": duplicate_underlying_count,
        "rejection_counts": rejection_counts,
        "positive_20d_ratio": (
            _rounded(sum(value > 0 for value in selected_returns) / len(selected_returns))
            if selected_returns
            else None
        ),
        "above_ma20_ratio": (
            _rounded(sum(value >= 0 for value in selected_distances) / len(selected_distances))
            if selected_distances
            else None
        ),
        "average_return_20d": _rounded(mean(selected_returns)) if selected_returns else None,
    }
    return _component_result(
        "market_risk_pool",
        metrics=metrics,
        unavailable_reasons=reasons,
        input_payload=input_payload,
    )


def evaluate_market_risk_regime_shadow(
    market_pool: RiskShadowComponentResult,
    *,
    trade_session: date,
    previous_state: str = "risk_on",
    previous_evaluated_session: date | None = None,
    previous_recovery_sessions: Sequence[date] = (),
) -> MarketRiskRegimeShadowDecision:
    """Classify broad-market risk with immediate downgrade and slow recovery.

    This is intentionally shadow-only.  Repeated runs for one trade session do
    not advance recovery, and missing market evidence fails closed.
    """

    allowed_states = {"risk_on", "neutral", "defensive", "data_halt"}
    normalized_previous = previous_state if previous_state in allowed_states else "data_halt"
    reasons: list[str] = []
    positive_ratio = _finite_float(market_pool.metrics.get("positive_20d_ratio"))
    above_ma_ratio = _finite_float(market_pool.metrics.get("above_ma20_ratio"))
    if market_pool.status != "ready" or positive_ratio is None or above_ma_ratio is None:
        raw_state = "data_halt"
        state = "data_halt"
        recovery_sessions: tuple[date, ...] = ()
        reasons.append("market_risk_pool_unavailable")
    elif (
        positive_ratio < PORTFOLIO_RISK_SHADOW_RISK_OFF_RATIO
        or above_ma_ratio < PORTFOLIO_RISK_SHADOW_RISK_OFF_RATIO
    ):
        raw_state = "defensive"
        state = "defensive"
        recovery_sessions = ()
        reasons.append("market_breadth_risk_off")
    elif (
        positive_ratio >= PORTFOLIO_RISK_SHADOW_RECOVERY_RATIO
        and above_ma_ratio >= PORTFOLIO_RISK_SHADOW_RECOVERY_RATIO
    ):
        raw_state = "risk_on"
        valid_previous_sessions = tuple(
            sorted(
                {
                    item
                    for item in previous_recovery_sessions
                    if isinstance(item, date) and item <= trade_session
                }
            )
        )[-(PORTFOLIO_RISK_SHADOW_RECOVERY_SESSIONS - 1) :]
        if normalized_previous in {"defensive", "data_halt"}:
            recovery_sessions = valid_previous_sessions
            if previous_evaluated_session != trade_session:
                recovery_sessions = (*recovery_sessions, trade_session)
            recovery_sessions = tuple(sorted(set(recovery_sessions)))[
                -PORTFOLIO_RISK_SHADOW_RECOVERY_SESSIONS:
            ]
            if len(recovery_sessions) < PORTFOLIO_RISK_SHADOW_RECOVERY_SESSIONS:
                state = "defensive"
                reasons.append("market_risk_recovery_pending")
            else:
                state = "risk_on"
                reasons.append("market_risk_recovery_confirmed")
        else:
            state = "risk_on"
            recovery_sessions = ()
            reasons.append("market_breadth_risk_on")
    else:
        raw_state = "neutral"
        recovery_sessions = ()
        if normalized_previous in {"defensive", "data_halt"}:
            state = "defensive"
            reasons.append("market_risk_recovery_not_strong_enough")
        else:
            state = "neutral"
            reasons.append("market_breadth_neutral")

    payload = {
        "market_pool_input_hash": market_pool.input_hash,
        "market_pool_status": market_pool.status,
        "trade_session": trade_session,
        "previous_state": normalized_previous,
        "previous_evaluated_session": previous_evaluated_session,
        "previous_recovery_sessions": list(previous_recovery_sessions),
        "positive_20d_ratio": positive_ratio,
        "above_ma20_ratio": above_ma_ratio,
    }
    version, contract_hash = _contract()
    return MarketRiskRegimeShadowDecision(
        state=state,
        raw_state=raw_state,
        trade_session=trade_session,
        recovery_sessions=recovery_sessions,
        reason_codes=_stable_reasons(reasons),
        changed=state != normalized_previous,
        input_hash=stable_contract_hash(payload),
        contract_version=version,
        contract_hash=contract_hash,
    )


def _validated_weights(
    raw_weights: Mapping[str, float],
    *,
    allow_empty: bool = False,
) -> tuple[dict[str, float], tuple[str, ...]]:
    weights: dict[str, float] = {}
    reasons: list[str] = []
    for raw_code in sorted(raw_weights, key=str):
        if not isinstance(raw_code, str) or not raw_code.strip():
            reasons.append("invalid_portfolio_code")
            continue
        value = _finite_float(raw_weights[raw_code])
        if value is None or value < 0:
            reasons.append("invalid_portfolio_weights")
            continue
        if value > 0:
            weights[raw_code.strip()] = value
    if not weights and not allow_empty:
        reasons.append("empty_portfolio")
    if sum(weights.values()) > 1.0 + _WEIGHT_TOLERANCE:
        reasons.append("portfolio_weight_exceeds_one")
    return weights, _stable_reasons(reasons)


def _asset_weights(
    assets: Sequence[PortfolioRiskAssetInput],
) -> tuple[dict[str, float], dict[str, PortfolioRiskAssetInput], tuple[str, ...]]:
    raw_weights: dict[str, float] = {}
    by_code: dict[str, PortfolioRiskAssetInput] = {}
    reasons: list[str] = []
    for item in assets:
        code = item.code.strip()
        if not code or code in by_code:
            reasons.append("duplicate_or_invalid_portfolio_code")
            continue
        by_code[code] = item
        raw_weights[code] = item.weight
    weights, weight_reasons = _validated_weights(raw_weights)
    reasons.extend(weight_reasons)
    return weights, by_code, _stable_reasons(reasons)


def _sorted_exposure(values: Mapping[str, float]) -> dict[str, float]:
    return {key: _rounded(values[key]) for key in sorted(values)}


def calculate_portfolio_exposures(
    assets: Sequence[PortfolioRiskAssetInput],
) -> RiskShadowComponentResult:
    input_payload = {
        "assets": [item.as_dict() for item in sorted(assets, key=lambda item: item.code)],
    }
    weights, by_code, validation_reasons = _asset_weights(assets)
    if validation_reasons:
        return _component_result(
            "exposure",
            metrics={
                "invested_weight": _rounded(sum(weights.values())),
                "clone_exposure": {},
                "theme_exposure": {},
                "asset_bucket_exposure": {},
            },
            unavailable_reasons=validation_reasons,
            input_payload=input_payload,
        )

    clone_exposure: dict[str, float] = {}
    theme_exposure: dict[str, float] = {}
    bucket_exposure: dict[str, float] = {}
    unknown_clone_weight = 0.0
    unknown_theme_weight = 0.0
    unknown_bucket_weight = 0.0
    for code, weight in weights.items():
        item = by_code[code]
        clone = _known_label(item.clone_group_id)
        theme = _known_label(item.theme_group)
        bucket = _known_label(item.asset_bucket)
        if clone is None:
            unknown_clone_weight += weight
        else:
            clone_exposure[clone] = clone_exposure.get(clone, 0.0) + weight
        if theme is None:
            unknown_theme_weight += weight
        else:
            theme_exposure[theme] = theme_exposure.get(theme, 0.0) + weight
        if bucket is None:
            unknown_bucket_weight += weight
        else:
            bucket_exposure[bucket] = bucket_exposure.get(bucket, 0.0) + weight

    reasons: list[str] = []
    if unknown_clone_weight > _WEIGHT_TOLERANCE:
        reasons.append("clone_exposure_incomplete")
    if unknown_theme_weight > _WEIGHT_TOLERANCE:
        reasons.append("theme_exposure_incomplete")
    if unknown_bucket_weight > _WEIGHT_TOLERANCE:
        reasons.append("asset_bucket_exposure_incomplete")
    metrics = {
        "asset_count": len(weights),
        "invested_weight": _rounded(sum(weights.values())),
        "cash_weight": _rounded(max(0.0, 1.0 - sum(weights.values()))),
        "clone_exposure": _sorted_exposure(clone_exposure),
        "theme_exposure": _sorted_exposure(theme_exposure),
        "asset_bucket_exposure": _sorted_exposure(bucket_exposure),
        "unknown_clone_weight": _rounded(unknown_clone_weight),
        "unknown_theme_weight": _rounded(unknown_theme_weight),
        "unknown_asset_bucket_weight": _rounded(unknown_bucket_weight),
        "largest_clone_weight": _rounded(max(clone_exposure.values(), default=0.0)),
        "largest_theme_weight": _rounded(max(theme_exposure.values(), default=0.0)),
    }
    return _component_result(
        "exposure",
        metrics=metrics,
        unavailable_reasons=reasons,
        input_payload=input_payload,
    )


def _common_return_matrix(
    return_maps: Mapping[str, Mapping[date, float]],
    labels: Sequence[str],
    *,
    lookback: int = PORTFOLIO_RISK_SHADOW_LOOKBACK_DAYS,
) -> tuple[list[date], dict[str, list[float]], int]:
    normalised: dict[str, dict[date, float]] = {}
    rejected = 0
    for label in labels:
        series, series_rejected = _normalise_return_series(return_maps.get(label, {}))
        normalised[label] = series
        rejected += series_rejected
    if not labels:
        return [], {}, rejected
    common_dates = set(normalised[labels[0]])
    for label in labels[1:]:
        common_dates.intersection_update(normalised[label])
    dates = sorted(common_dates)[-lookback:]
    matrix = {label: [normalised[label][point_date] for point_date in dates] for label in labels}
    return dates, matrix, rejected


def calculate_factor_betas(
    portfolio_returns: Mapping[date, float],
    factor_returns: Mapping[str, Mapping[date, float]],
) -> RiskShadowComponentResult:
    factor_payload, factor_rejected = _normalised_return_payload(factor_returns)
    portfolio_payload, portfolio_rejected = _normalised_return_payload(
        {"portfolio": portfolio_returns}
    )
    input_payload = {
        "portfolio_returns": portfolio_payload.get("portfolio", []),
        "factor_returns": factor_payload,
    }
    missing_factors = [name for name in PORTFOLIO_RISK_FACTOR_NAMES if name not in factor_returns]
    if missing_factors:
        return _component_result(
            "factor_beta",
            metrics={
                "required_factors": list(PORTFOLIO_RISK_FACTOR_NAMES),
                "missing_factors": missing_factors,
                "common_sample_count": 0,
                "betas": {},
            },
            unavailable_reasons=("missing_required_factor_returns",),
            input_payload=input_payload,
        )

    joined_maps: dict[str, Mapping[date, float]] = {"portfolio": portfolio_returns}
    joined_maps.update({name: factor_returns[name] for name in PORTFOLIO_RISK_FACTOR_NAMES})
    labels = ("portfolio", *PORTFOLIO_RISK_FACTOR_NAMES)
    dates, matrix, rejected = _common_return_matrix(joined_maps, labels)
    rejected = max(rejected, factor_rejected + portfolio_rejected)
    reasons: list[str] = []
    if len(dates) < PORTFOLIO_RISK_SHADOW_MIN_COMMON_DAYS:
        reasons.append("insufficient_common_factor_history")
    betas: dict[str, float] = {}
    zero_variance_factors: list[str] = []
    if not reasons:
        portfolio_values = matrix["portfolio"]
        portfolio_mean = mean(portfolio_values)
        for factor_name in PORTFOLIO_RISK_FACTOR_NAMES:
            values = matrix[factor_name]
            factor_mean = mean(values)
            denominator = sum((value - factor_mean) ** 2 for value in values)
            if denominator <= _VARIANCE_EPSILON:
                zero_variance_factors.append(factor_name)
                continue
            numerator = sum(
                (portfolio_value - portfolio_mean) * (factor_value - factor_mean)
                for portfolio_value, factor_value in zip(portfolio_values, values, strict=True)
            )
            betas[factor_name] = _rounded(numerator / denominator)
    if zero_variance_factors:
        reasons.append("zero_variance_required_factor")
    metrics = {
        "required_factors": list(PORTFOLIO_RISK_FACTOR_NAMES),
        "betas": {name: betas[name] for name in PORTFOLIO_RISK_FACTOR_NAMES if name in betas},
        "zero_variance_factors": zero_variance_factors,
        "common_sample_count": len(dates),
        "window_start": dates[0].isoformat() if dates else None,
        "window_end": dates[-1].isoformat() if dates else None,
        "rejected_return_value_count": rejected,
        "beta_method": "pairwise_covariance_over_variance_on_shared_three_factor_dates",
    }
    return _component_result(
        "factor_beta",
        metrics=metrics,
        unavailable_reasons=reasons,
        input_payload=input_payload,
    )


def calculate_marginal_risk_contribution(
    weights: Mapping[str, float],
    returns_by_code: Mapping[str, Mapping[date, float]],
) -> RiskShadowComponentResult:
    normalised_return_payload, rejected_payload_values = _normalised_return_payload(returns_by_code)
    input_payload = {
        "weights": {code: weights[code] for code in sorted(weights)},
        "returns_by_code": normalised_return_payload,
    }
    clean_weights, weight_reasons = _validated_weights(weights)
    reasons = list(weight_reasons)
    if len(clean_weights) > PORTFOLIO_RISK_SHADOW_MAX_ASSETS:
        reasons.append("asset_count_exceeds_shadow_cap")
    missing_codes = [code for code in clean_weights if code not in returns_by_code]
    if missing_codes:
        reasons.append("missing_asset_return_history")
    if reasons:
        return _component_result(
            "marginal_risk_contribution",
            metrics={
                "asset_count": len(clean_weights),
                "missing_return_codes": missing_codes,
                "common_sample_count": 0,
                "portfolio_annualized_volatility": None,
                "contributions": {},
            },
            unavailable_reasons=reasons,
            input_payload=input_payload,
        )

    codes = sorted(clean_weights)
    dates, matrix, rejected = _common_return_matrix(returns_by_code, codes)
    reasons = []
    if len(dates) < PORTFOLIO_RISK_SHADOW_MIN_COMMON_DAYS:
        reasons.append("insufficient_common_return_history")
    if reasons:
        return _component_result(
            "marginal_risk_contribution",
            metrics={
                "asset_count": len(codes),
                "common_sample_count": len(dates),
                "portfolio_annualized_volatility": None,
                "contributions": {},
                "rejected_return_value_count": max(rejected, rejected_payload_values),
            },
            unavailable_reasons=reasons,
            input_payload=input_payload,
        )

    sample_count = len(dates)
    means = {code: mean(matrix[code]) for code in codes}
    covariance: list[list[float]] = []
    for left_index, left_code in enumerate(codes):
        row: list[float] = []
        for right_index, right_code in enumerate(codes):
            sample_covariance = sum(
                (left - means[left_code]) * (right - means[right_code])
                for left, right in zip(matrix[left_code], matrix[right_code], strict=True)
            ) / (sample_count - 1)
            if left_index != right_index:
                sample_covariance *= 1.0 - PORTFOLIO_RISK_SHADOW_DIAGONAL_SHRINKAGE
            row.append(sample_covariance * PORTFOLIO_RISK_SHADOW_ANNUALIZATION_DAYS)
        covariance.append(row)

    weight_vector = [clean_weights[code] for code in codes]
    covariance_times_weight = [
        sum(covariance[row_index][column_index] * weight_vector[column_index] for column_index in range(len(codes)))
        for row_index in range(len(codes))
    ]
    portfolio_variance = sum(
        weight_vector[index] * covariance_times_weight[index]
        for index in range(len(codes))
    )
    if not math.isfinite(portfolio_variance) or portfolio_variance <= _VARIANCE_EPSILON:
        return _component_result(
            "marginal_risk_contribution",
            metrics={
                "asset_count": len(codes),
                "common_sample_count": len(dates),
                "portfolio_annualized_volatility": None,
                "contributions": {},
                "rejected_return_value_count": max(rejected, rejected_payload_values),
            },
            unavailable_reasons=("non_positive_portfolio_variance",),
            input_payload=input_payload,
        )

    portfolio_volatility = math.sqrt(portfolio_variance)
    contributions: dict[str, dict[str, float]] = {}
    for index, code in enumerate(codes):
        marginal = covariance_times_weight[index] / portfolio_volatility
        component = weight_vector[index] * marginal
        contributions[code] = {
            "weight": _rounded(weight_vector[index]),
            "marginal_annualized_volatility": _rounded(marginal),
            "component_annualized_volatility": _rounded(component),
            "component_share": _rounded(component / portfolio_volatility),
        }
    metrics = {
        "asset_count": len(codes),
        "common_sample_count": sample_count,
        "window_start": dates[0].isoformat(),
        "window_end": dates[-1].isoformat(),
        "lookback_cap": PORTFOLIO_RISK_SHADOW_LOOKBACK_DAYS,
        "diagonal_shrinkage": PORTFOLIO_RISK_SHADOW_DIAGONAL_SHRINKAGE,
        "portfolio_annualized_volatility": _rounded(portfolio_volatility),
        "component_risk_sum": _rounded(
            sum(row["component_annualized_volatility"] for row in contributions.values())
        ),
        "contributions": contributions,
        "rejected_return_value_count": max(rejected, rejected_payload_values),
    }
    return _component_result(
        "marginal_risk_contribution",
        metrics=metrics,
        unavailable_reasons=(),
        input_payload=input_payload,
    )


def _scenario_unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "portfolio_return": None, "unavailable_reason": reason}


def _scenario_ready(portfolio_return: float, **details: Any) -> dict[str, Any]:
    return {
        "status": "ready",
        "portfolio_return": _rounded(portfolio_return),
        "loss_magnitude": _rounded(max(0.0, -portfolio_return)),
        **details,
    }


def run_fixed_stress_scenarios(
    assets: Sequence[PortfolioRiskAssetInput],
    returns_by_code: Mapping[str, Mapping[date, float]],
    *,
    previous_weights: Mapping[str, float] | None,
) -> RiskShadowComponentResult:
    return_payload, rejected_payload_values = _normalised_return_payload(returns_by_code)
    input_payload = {
        "assets": [item.as_dict() for item in sorted(assets, key=lambda item: item.code)],
        "returns_by_code": return_payload,
        "previous_weights": (
            {code: previous_weights[code] for code in sorted(previous_weights)}
            if previous_weights is not None
            else None
        ),
    }
    weights, by_code, validation_reasons = _asset_weights(assets)
    scenarios: dict[str, dict[str, Any]] = {}
    reasons = list(validation_reasons)
    if len(weights) > PORTFOLIO_RISK_SHADOW_MAX_ASSETS:
        reasons.append("asset_count_exceeds_shadow_cap")
    if reasons:
        for name in (
            "broad_market_-5pct",
            "dominant_theme_-8pct",
            "correlation_one_2sigma",
            "liquidity_cost_3x",
        ):
            scenarios[name] = _scenario_unavailable("invalid_portfolio_inputs")
        return _component_result(
            "stress_scenarios",
            metrics={"scenarios": scenarios},
            unavailable_reasons=reasons,
            input_payload=input_payload,
        )

    unknown_bucket_codes = [
        code for code in weights if _known_label(by_code[code].asset_bucket) not in _BROAD_MARKET_BUCKET_SHOCKS
    ]
    if unknown_bucket_codes:
        reason = "broad_market_scenario_missing_asset_bucket"
        reasons.append(reason)
        scenarios["broad_market_-5pct"] = _scenario_unavailable(reason)
    else:
        broad_return = sum(
            weight * _BROAD_MARKET_BUCKET_SHOCKS[str(_known_label(by_code[code].asset_bucket))]
            for code, weight in weights.items()
        )
        scenarios["broad_market_-5pct"] = _scenario_ready(
            broad_return,
            bucket_shocks=dict(_BROAD_MARKET_BUCKET_SHOCKS),
        )

    theme_exposure: dict[str, float] = {}
    unknown_theme_codes: list[str] = []
    for code, weight in weights.items():
        theme = _known_label(by_code[code].theme_group)
        if theme is None:
            unknown_theme_codes.append(code)
        else:
            theme_exposure[theme] = theme_exposure.get(theme, 0.0) + weight
    if unknown_theme_codes or unknown_bucket_codes or not theme_exposure:
        reason = "dominant_theme_scenario_missing_exposure"
        reasons.append(reason)
        scenarios["dominant_theme_-8pct"] = _scenario_unavailable(reason)
    else:
        dominant_theme = sorted(theme_exposure.items(), key=lambda item: (-item[1], item[0]))[0][0]
        dominant_return = 0.0
        for code, weight in weights.items():
            item = by_code[code]
            theme = _known_label(item.theme_group)
            bucket = str(_known_label(item.asset_bucket))
            shock = (
                _DOMINANT_THEME_SHOCK
                if theme == dominant_theme
                else _DOMINANT_THEME_OTHER_BUCKET_SHOCKS[bucket]
            )
            dominant_return += weight * shock
        scenarios["dominant_theme_-8pct"] = _scenario_ready(
            dominant_return,
            dominant_theme=dominant_theme,
            dominant_theme_weight=_rounded(theme_exposure[dominant_theme]),
        )

    codes = sorted(weights)
    missing_return_codes = [code for code in codes if code not in returns_by_code]
    if missing_return_codes:
        reason = "correlation_scenario_missing_return_history"
        reasons.append(reason)
        scenarios["correlation_one_2sigma"] = _scenario_unavailable(reason)
        common_dates: list[date] = []
        rejected = rejected_payload_values
    else:
        common_dates, matrix, rejected = _common_return_matrix(returns_by_code, codes)
        rejected = max(rejected, rejected_payload_values)
        if len(common_dates) < PORTFOLIO_RISK_SHADOW_MIN_COMMON_DAYS:
            reason = "correlation_scenario_insufficient_common_history"
            reasons.append(reason)
            scenarios["correlation_one_2sigma"] = _scenario_unavailable(reason)
        else:
            weighted_daily_volatility = 0.0
            for code in codes:
                values = matrix[code]
                value_mean = mean(values)
                variance = sum((value - value_mean) ** 2 for value in values) / (len(values) - 1)
                weighted_daily_volatility += weights[code] * math.sqrt(max(0.0, variance))
            if weighted_daily_volatility <= _VARIANCE_EPSILON:
                reason = "correlation_scenario_non_positive_volatility"
                reasons.append(reason)
                scenarios["correlation_one_2sigma"] = _scenario_unavailable(reason)
            else:
                scenarios["correlation_one_2sigma"] = _scenario_ready(
                    -_CORRELATION_STRESS_SIGMA * weighted_daily_volatility,
                    common_sample_count=len(common_dates),
                    assumed_correlation=1.0,
                    sigma_multiplier=_CORRELATION_STRESS_SIGMA,
                )

    if previous_weights is None:
        reason = "liquidity_scenario_requires_previous_weights"
        reasons.append(reason)
        scenarios["liquidity_cost_3x"] = _scenario_unavailable(reason)
    else:
        clean_previous, previous_reasons = _validated_weights(previous_weights, allow_empty=True)
        if previous_reasons:
            reason = "liquidity_scenario_invalid_previous_weights"
            reasons.append(reason)
            scenarios["liquidity_cost_3x"] = _scenario_unavailable(reason)
        else:
            traded_codes = set(weights) | set(clean_previous)
            turnover = sum(
                abs(weights.get(code, 0.0) - clean_previous.get(code, 0.0))
                for code in traded_codes
            )
            stressed_cost_rate = (
                _BASE_FEE_RATE
                + _BASE_SLIPPAGE_RATE * _LIQUIDITY_STRESS_SLIPPAGE_MULTIPLIER
            )
            scenarios["liquidity_cost_3x"] = _scenario_ready(
                -turnover * stressed_cost_rate,
                turnover=_rounded(turnover),
                stressed_cost_rate=_rounded(stressed_cost_rate),
            )

    metrics = {
        "scenario_count": 4,
        "ready_scenario_count": sum(row["status"] == "ready" for row in scenarios.values()),
        "scenarios": scenarios,
        "rejected_return_value_count": rejected,
    }
    return _component_result(
        "stress_scenarios",
        metrics=metrics,
        unavailable_reasons=reasons,
        input_payload=input_payload,
    )


def _portfolio_return_series(
    weights: Mapping[str, float],
    returns_by_code: Mapping[str, Mapping[date, float]],
) -> dict[date, float]:
    clean_weights, reasons = _validated_weights(weights)
    if reasons or len(clean_weights) > PORTFOLIO_RISK_SHADOW_MAX_ASSETS:
        return {}
    codes = sorted(clean_weights)
    if any(code not in returns_by_code for code in codes):
        return {}
    dates, matrix, _rejected = _common_return_matrix(returns_by_code, codes)
    return {
        point_date: sum(clean_weights[code] * matrix[code][index] for code in codes)
        for index, point_date in enumerate(dates)
    }


def build_portfolio_risk_shadow(
    assets: Sequence[PortfolioRiskAssetInput],
    returns_by_code: Mapping[str, Mapping[date, float]],
    factor_returns: Mapping[str, Mapping[date, float]],
    *,
    previous_weights: Mapping[str, float] | None,
) -> PortfolioRiskShadowResult:
    weights = {item.code: item.weight for item in assets}
    exposure = calculate_portfolio_exposures(assets)
    factor_beta = calculate_factor_betas(
        _portfolio_return_series(weights, returns_by_code),
        factor_returns,
    )
    marginal_risk = calculate_marginal_risk_contribution(weights, returns_by_code)
    stress = run_fixed_stress_scenarios(
        assets,
        returns_by_code,
        previous_weights=previous_weights,
    )
    components = (exposure, factor_beta, marginal_risk, stress)
    reasons = _stable_reasons(
        [
            f"{component.component}:{reason}"
            for component in components
            for reason in component.unavailable_reasons
        ]
    )
    version, contract_hash = _contract()
    input_hash = stable_contract_hash(
        {
            "component_input_hashes": {
                component.component: component.input_hash for component in components
            }
        }
    )
    return PortfolioRiskShadowResult(
        status="unavailable" if reasons else "ready",
        exposure=exposure,
        factor_beta=factor_beta,
        marginal_risk_contribution=marginal_risk,
        stress_scenarios=stress,
        unavailable_reasons=reasons,
        input_hash=input_hash,
        contract_version=version,
        contract_hash=contract_hash,
    )
