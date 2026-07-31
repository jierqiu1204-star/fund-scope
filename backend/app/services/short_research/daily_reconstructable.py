"""Pure, outcome-free score for point-in-time daily ETF research replay.

The contract deliberately uses only dimensionless features reconstructable from a
61-session adjusted OHLCV window.  Its 55/30/15 component weights are frozen
before outcome inspection.  Absolute liquidity eligibility remains an upstream
data gate; the score uses relative volume so a later uniform adjustment scale
cannot change the result.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import cached_property, lru_cache
from statistics import mean, pstdev
from typing import Any

CONTRACT_ID = "daily_reconstructable_v1"
SCORE_FIELD = "research_score"
PRICE_BASIS = "total_return_adjusted"
REQUIRED_BAR_COUNT = 61
OUTPUT_DECIMAL_PLACES = 12

_TREND_WEIGHT = 0.55
_RISK_WEIGHT = 0.30
_LIQUIDITY_WEIGHT = 0.15
_VOLATILITY_ZERO_QUALITY = 0.04
_DRAWDOWN_ZERO_QUALITY = 0.20
_OVEREXTENSION_ZERO_QUALITY = 4.0


class DailyReconstructableUnavailableError(ValueError):
    """The declared replay score cannot be calculated without a fallback."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class AdjustmentProvenance:
    provider: str
    adjustment_version: str
    price_basis: str
    transform_kind: str
    scale_invariance_proven: bool


@dataclass(frozen=True)
class AdjustedOhlcvBar:
    session_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    volume: float
    turnover: float | None = None


@dataclass(frozen=True)
class DailyReconstructableManifest:
    contract_id: str = CONTRACT_ID
    score_field: str = SCORE_FIELD
    price_basis: str = PRICE_BASIS
    required_bar_count: int = REQUIRED_BAR_COUNT
    forbidden_input_domains: tuple[str, ...] = (
        "intraday",
        "spread",
        "iopv",
        "premium",
        "provider_consensus",
        "theme",
        "catalyst",
        "validation",
        "position",
        "alert",
        "notification",
    )
    distinct_from_contracts: tuple[str, ...] = ("final_score_v3",)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "score_field": self.score_field,
            "price_basis": self.price_basis,
            "required_bar_count": self.required_bar_count,
            "required_inputs": [
                "adjusted_open",
                "adjusted_high",
                "adjusted_low",
                "adjusted_close",
                "volume",
            ],
            "component_weights": {
                "trend": _TREND_WEIGHT,
                "risk": _RISK_WEIGHT,
                "liquidity": _LIQUIDITY_WEIGHT,
            },
            "component_formulas": {
                "trend": {
                    "formula_id": "clipped_multi_horizon_return_v1",
                    "base_score": 50.0,
                    "horizons": [
                        {"sessions": 5, "coefficient": 150.0, "absolute_clip": 0.12},
                        {"sessions": 10, "coefficient": 110.0, "absolute_clip": 0.18},
                        {"sessions": 20, "coefficient": 85.0, "absolute_clip": 0.30},
                        {"sessions": 60, "coefficient": 35.0, "absolute_clip": 0.60},
                    ],
                    "bounds": [0.0, 100.0],
                },
                "risk": {
                    "formula_id": "mean_dimensionless_risk_quality_v1",
                    "realized_volatility_20d_zero_quality": _VOLATILITY_ZERO_QUALITY,
                    "max_drawdown_60d_zero_quality": _DRAWDOWN_ZERO_QUALITY,
                    "overextension_atr20_zero_quality": _OVEREXTENSION_ZERO_QUALITY,
                    "overextension_definition": (
                        "abs(adjusted_close-adjusted_MA20)/adjusted_ATR20"
                    ),
                    "primitive_weights": {
                        "realized_volatility_20d": 1 / 3,
                        "max_drawdown_60d": 1 / 3,
                        "overextension_atr": 1 / 3,
                    },
                    "bounds": [0.0, 100.0],
                },
                "liquidity": {
                    "formula_id": "relative_volume_20_over_prior_40_v1",
                    "formula": "clamp_0_100(50*mean(volume[-20:])/mean(volume[-60:-20]))",
                    "neutral_ratio": 1.0,
                    "neutral_score": 50.0,
                    "bounds": [0.0, 100.0],
                },
            },
            "score_formula": "round_12(trend*0.55+risk*0.30+liquidity*0.15)",
            "output_decimal_places": OUTPUT_DECIMAL_PLACES,
            "adjustment_requirement": {
                "price_basis": PRICE_BASIS,
                "transform_kind": "constant_multiplicative",
                "scale_invariance_must_be_proven": True,
            },
            "allowed_input_domains": [
                "adjusted_daily_ohlcv",
                "adjustment_provenance",
            ],
            "forbidden_input_domains": list(self.forbidden_input_domains),
            "outcome_inputs_allowed": False,
            "missing_data_behavior": (
                "score_unavailable_no_fallback_no_fill_no_weight_renormalization"
            ),
            "distinct_from_contracts": list(self.distinct_from_contracts),
            "selection_rationale": [
                "reuse_frozen_existing_daily_return_horizons_without_outcome_tuning",
                "use_only_dimensionless_risk_features_invariant_to_price_rescaling",
                "penalize_equal_atr_distance_above_and_below_adjusted_ma20",
                "use_relative_volume_while_absolute_liquidity_remains_an_upstream_gate",
            ],
        }

    @cached_property
    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @cached_property
    def manifest_hash(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    def matches_identity(
        self,
        *,
        contract_id: str,
        score_field: str,
        manifest_hash: str,
    ) -> bool:
        return (
            contract_id == self.contract_id
            and score_field == self.score_field
            and manifest_hash == self.manifest_hash
        )


@dataclass(frozen=True)
class DailyReconstructableScore:
    contract_id: str
    score_field: str
    price_basis: str
    manifest_hash: str
    research_score: float
    trend_score: float
    risk_score: float
    liquidity_score: float
    return_5d: float
    return_10d: float
    return_20d: float
    return_60d: float
    realized_volatility_20d: float
    max_drawdown_60d: float
    adjusted_ma20: float
    adjusted_atr20: float
    overextension_atr: float
    overextension_penalty: float
    relative_volume_20_over_prior_40: float


@lru_cache(maxsize=1)
def daily_reconstructable_manifest() -> DailyReconstructableManifest:
    return DailyReconstructableManifest()


def _unavailable(reason: str, detail: str) -> DailyReconstructableUnavailableError:
    return DailyReconstructableUnavailableError(reason, detail)


def _validate_provenance(provenance: AdjustmentProvenance) -> None:
    if provenance.price_basis != PRICE_BASIS:
        raise _unavailable(
            "incompatible_price_basis",
            f"required price_basis={PRICE_BASIS}",
        )
    if (
        not provenance.provider.strip()
        or not provenance.adjustment_version.strip()
        or provenance.transform_kind != "constant_multiplicative"
        or provenance.scale_invariance_proven is not True
    ):
        raise _unavailable(
            "unproven_adjustment_point_in_time",
            "provider/version and proven constant multiplicative transform are required",
        )


def _finite_number(value: object, *, field: str, index: int) -> float:
    if value is None:
        raise _unavailable("missing_adjusted_ohlcv", f"bar[{index}].{field}")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _unavailable("invalid_adjusted_ohlcv", f"bar[{index}].{field}")
    number = float(value)
    if not math.isfinite(number):
        raise _unavailable("non_finite_adjusted_ohlcv", f"bar[{index}].{field}")
    return number


def _validated_bars(bars: Sequence[AdjustedOhlcvBar]) -> tuple[AdjustedOhlcvBar, ...]:
    rows = tuple(bars)
    if len(rows) != REQUIRED_BAR_COUNT:
        raise _unavailable(
            "insufficient_or_ambiguous_adjusted_history",
            f"required_adjusted_ohlcv_bar_count={REQUIRED_BAR_COUNT}, actual={len(rows)}",
        )

    previous_date: date | None = None
    for index, bar in enumerate(rows):
        if previous_date is not None and bar.session_date <= previous_date:
            raise _unavailable(
                "invalid_adjusted_ohlcv",
                "session dates must be unique and strictly increasing",
            )
        previous_date = bar.session_date
        adjusted_open = _finite_number(bar.adjusted_open, field="adjusted_open", index=index)
        adjusted_high = _finite_number(bar.adjusted_high, field="adjusted_high", index=index)
        adjusted_low = _finite_number(bar.adjusted_low, field="adjusted_low", index=index)
        adjusted_close = _finite_number(bar.adjusted_close, field="adjusted_close", index=index)
        volume = _finite_number(bar.volume, field="volume", index=index)
        if (
            min(adjusted_open, adjusted_high, adjusted_low, adjusted_close, volume) <= 0.0
            or adjusted_high < max(adjusted_open, adjusted_close)
            or adjusted_low > min(adjusted_open, adjusted_close)
            or adjusted_high < adjusted_low
        ):
            raise _unavailable("invalid_adjusted_ohlcv", f"invalid bar[{index}]")
    return rows


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _rounded(value: float) -> float:
    return round(value, OUTPUT_DECIMAL_PLACES)


def _window_return(closes: Sequence[float], sessions: int) -> float:
    return closes[-1] / closes[-(sessions + 1)] - 1.0


def _maximum_drawdown(closes: Sequence[float]) -> float:
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        worst = min(worst, close / peak - 1.0)
    return worst


def _adjusted_atr20(rows: Sequence[AdjustedOhlcvBar]) -> float:
    window = rows[-21:]
    true_ranges = [
        max(
            current.adjusted_high - current.adjusted_low,
            abs(current.adjusted_high - previous.adjusted_close),
            abs(current.adjusted_low - previous.adjusted_close),
        )
        for previous, current in zip(window[:-1], window[1:], strict=True)
    ]
    adjusted_atr20 = mean(true_ranges)
    if adjusted_atr20 <= 0.0 or not math.isfinite(adjusted_atr20):
        raise _unavailable(
            "invalid_adjusted_ohlcv",
            "adjusted_ATR20 must be finite and positive",
        )
    return adjusted_atr20


def score_daily_reconstructable(
    bars: Sequence[AdjustedOhlcvBar],
    *,
    provenance: AdjustmentProvenance,
) -> DailyReconstructableScore:
    """Calculate the frozen research score or fail closed without substitutions."""

    _validate_provenance(provenance)
    rows = _validated_bars(bars)
    closes = tuple(float(row.adjusted_close) for row in rows)
    volumes = tuple(float(row.volume) for row in rows)

    return_5d = _window_return(closes, 5)
    return_10d = _window_return(closes, 10)
    return_20d = _window_return(closes, 20)
    return_60d = _window_return(closes, 60)
    trend_score = _clamp(
        50.0
        + _clamp(return_5d, -0.12, 0.12) * 150.0
        + _clamp(return_10d, -0.18, 0.18) * 110.0
        + _clamp(return_20d, -0.30, 0.30) * 85.0
        + _clamp(return_60d, -0.60, 0.60) * 35.0
    )

    daily_returns_20d = tuple(
        current / previous - 1.0
        for previous, current in zip(closes[-21:-1], closes[-20:], strict=True)
    )
    realized_volatility_20d = pstdev(daily_returns_20d)
    max_drawdown_60d = _maximum_drawdown(closes[-60:])
    adjusted_ma20 = mean(closes[-20:])
    adjusted_atr20 = _adjusted_atr20(rows)
    overextension_atr = abs(closes[-1] - adjusted_ma20) / adjusted_atr20

    volatility_quality = _clamp(
        100.0 * (1.0 - realized_volatility_20d / _VOLATILITY_ZERO_QUALITY)
    )
    drawdown_quality = _clamp(
        100.0 * (1.0 - abs(min(max_drawdown_60d, 0.0)) / _DRAWDOWN_ZERO_QUALITY)
    )
    overextension_quality = _clamp(
        100.0 * (1.0 - overextension_atr / _OVEREXTENSION_ZERO_QUALITY)
    )
    risk_score = mean((volatility_quality, drawdown_quality, overextension_quality))

    prior_40_volume = mean(volumes[-60:-20])
    recent_20_volume = mean(volumes[-20:])
    relative_volume = recent_20_volume / prior_40_volume
    liquidity_score = _clamp(50.0 * relative_volume)

    trend_score = _rounded(trend_score)
    risk_score = _rounded(risk_score)
    liquidity_score = _rounded(liquidity_score)
    research_score = _rounded(
        trend_score * _TREND_WEIGHT
        + risk_score * _RISK_WEIGHT
        + liquidity_score * _LIQUIDITY_WEIGHT
    )
    manifest = daily_reconstructable_manifest()
    return DailyReconstructableScore(
        contract_id=manifest.contract_id,
        score_field=manifest.score_field,
        price_basis=manifest.price_basis,
        manifest_hash=manifest.manifest_hash,
        research_score=research_score,
        trend_score=trend_score,
        risk_score=risk_score,
        liquidity_score=liquidity_score,
        return_5d=_rounded(return_5d),
        return_10d=_rounded(return_10d),
        return_20d=_rounded(return_20d),
        return_60d=_rounded(return_60d),
        realized_volatility_20d=_rounded(realized_volatility_20d),
        max_drawdown_60d=_rounded(max_drawdown_60d),
        adjusted_ma20=_rounded(adjusted_ma20),
        adjusted_atr20=_rounded(adjusted_atr20),
        overextension_atr=_rounded(overextension_atr),
        overextension_penalty=_rounded(100.0 - overextension_quality),
        relative_volume_20_over_prior_40=_rounded(relative_volume),
    )
