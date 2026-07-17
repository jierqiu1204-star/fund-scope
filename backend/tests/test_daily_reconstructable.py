from __future__ import annotations

import hashlib
import inspect
import json
import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.services.short_research.daily_reconstructable import (
    AdjustedOhlcvBar,
    AdjustmentProvenance,
    DailyReconstructableUnavailableError,
    daily_reconstructable_manifest,
    score_daily_reconstructable,
)


def _provenance(**overrides: object) -> AdjustmentProvenance:
    values: dict[str, object] = {
        "provider": "eastmoney",
        "adjustment_version": "total-return-v1",
        "price_basis": "total_return_adjusted",
        "transform_kind": "constant_multiplicative",
        "scale_invariance_proven": True,
    }
    values.update(overrides)
    return AdjustmentProvenance(**values)  # type: ignore[arg-type]


def _bars(
    *,
    last_close: float | None = None,
    price_scale: float = 1.0,
    volume_scale: float = 1.0,
) -> tuple[AdjustedOhlcvBar, ...]:
    start = date(2025, 1, 1)
    rows: list[AdjustedOhlcvBar] = []
    for index in range(61):
        close = 100.0 + index * 0.15
        if index == 60 and last_close is not None:
            close = last_close
        rows.append(
            AdjustedOhlcvBar(
                session_date=start + timedelta(days=index),
                adjusted_open=(close - 0.2) * price_scale,
                adjusted_high=(close + 1.0) * price_scale,
                adjusted_low=(close - 1.0) * price_scale,
                adjusted_close=close * price_scale,
                volume=(1_000_000.0 + index * 1_000.0) * volume_scale,
            )
        )
    return tuple(rows)


def _flat_bars(*, last_close: float) -> tuple[AdjustedOhlcvBar, ...]:
    rows = list(_bars())
    for index, row in enumerate(rows):
        close = last_close if index == 60 else 100.0
        rows[index] = replace(
            row,
            adjusted_open=close,
            adjusted_high=close + 1.0,
            adjusted_low=close - 1.0,
            adjusted_close=close,
            volume=1_000_000.0,
        )
    return tuple(rows)


def test_manifest_freezes_daily_reconstructable_identity_and_weights() -> None:
    manifest = daily_reconstructable_manifest()
    payload = manifest.canonical_payload()

    assert manifest.contract_id == "daily_reconstructable_v1"
    assert manifest.score_field == "research_score"
    assert manifest.price_basis == "total_return_adjusted"
    assert manifest.required_bar_count == 61
    assert payload["component_weights"] == {
        "trend": 0.55,
        "risk": 0.30,
        "liquidity": 0.15,
    }
    assert payload["missing_data_behavior"] == (
        "score_unavailable_no_fallback_no_fill_no_weight_renormalization"
    )
    assert payload["outcome_inputs_allowed"] is False
    expected_hash = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert manifest.manifest_hash == expected_hash


def test_score_uses_frozen_component_weights_without_renormalization() -> None:
    result = score_daily_reconstructable(_bars(), provenance=_provenance())

    assert math.isfinite(result.research_score)
    assert result.research_score == pytest.approx(
        result.trend_score * 0.55
        + result.risk_score * 0.30
        + result.liquidity_score * 0.15,
        abs=1e-10,
    )
    assert result.contract_id == "daily_reconstructable_v1"
    assert result.score_field == "research_score"
    assert result.price_basis == "total_return_adjusted"
    assert result.manifest_hash == daily_reconstructable_manifest().manifest_hash


@pytest.mark.parametrize("bar_count", [0, 1, 20, 21, 60, 62])
def test_score_requires_exactly_61_adjusted_ohlcv_bars(bar_count: int) -> None:
    rows = list(_bars())
    if bar_count > len(rows):
        rows.append(
            replace(
                rows[-1],
                session_date=rows[-1].session_date + timedelta(days=1),
            )
        )
    with pytest.raises(
        DailyReconstructableUnavailableError,
        match="required_adjusted_ohlcv_bar_count",
    ) as exc_info:
        score_daily_reconstructable(rows[:bar_count], provenance=_provenance())

    assert exc_info.value.reason == "insufficient_or_ambiguous_adjusted_history"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("adjusted_open", float("nan"), "non_finite_adjusted_ohlcv"),
        ("adjusted_high", float("inf"), "non_finite_adjusted_ohlcv"),
        ("adjusted_low", None, "missing_adjusted_ohlcv"),
        ("adjusted_close", 0.0, "invalid_adjusted_ohlcv"),
        ("volume", float("nan"), "non_finite_adjusted_ohlcv"),
        ("volume", 0.0, "invalid_adjusted_ohlcv"),
    ],
)
def test_score_rejects_missing_non_finite_or_invalid_inputs_without_fill(
    field: str,
    value: float | None,
    reason: str,
) -> None:
    rows = list(_bars())
    rows[30] = replace(rows[30], **{field: value})  # type: ignore[arg-type]

    with pytest.raises(DailyReconstructableUnavailableError) as exc_info:
        score_daily_reconstructable(rows, provenance=_provenance())

    assert exc_info.value.reason == reason


def test_overextension_uses_exact_adjusted_ma20_and_adjusted_atr20() -> None:
    rows = _flat_bars(last_close=110.0)
    result = score_daily_reconstructable(rows, provenance=_provenance())

    expected_ma20 = (19 * 100.0 + 110.0) / 20.0
    expected_atr20 = (19 * 2.0 + 11.0) / 20.0
    expected_overextension = abs(110.0 - expected_ma20) / expected_atr20
    assert result.adjusted_ma20 == pytest.approx(expected_ma20)
    assert result.adjusted_atr20 == pytest.approx(expected_atr20)
    assert result.overextension_atr == pytest.approx(expected_overextension)


def test_overextension_penalty_is_symmetric_above_and_below_adjusted_ma20() -> None:
    above = score_daily_reconstructable(
        _flat_bars(last_close=110.0),
        provenance=_provenance(),
    )
    below = score_daily_reconstructable(
        _flat_bars(last_close=90.0),
        provenance=_provenance(),
    )

    assert above.overextension_atr == pytest.approx(below.overextension_atr)
    assert above.overextension_penalty > 0.0
    assert above.overextension_penalty == pytest.approx(below.overextension_penalty)


def test_uniform_multiplicative_adjustment_scale_cannot_change_the_score() -> None:
    original = score_daily_reconstructable(_bars(), provenance=_provenance())
    rescaled = score_daily_reconstructable(
        _bars(price_scale=7.25),
        provenance=_provenance(adjustment_version="total-return-v2-rescaled"),
    )

    assert rescaled.return_5d == pytest.approx(original.return_5d)
    assert rescaled.return_60d == pytest.approx(original.return_60d)
    assert rescaled.realized_volatility_20d == pytest.approx(
        original.realized_volatility_20d
    )
    assert rescaled.max_drawdown_60d == pytest.approx(original.max_drawdown_60d)
    assert rescaled.overextension_atr == pytest.approx(original.overextension_atr)
    assert rescaled.liquidity_score == pytest.approx(original.liquidity_score)
    assert rescaled.research_score == pytest.approx(original.research_score)


@pytest.mark.parametrize(
    "provenance",
    [
        _provenance(scale_invariance_proven=False),
        _provenance(transform_kind="additive"),
        _provenance(price_basis="raw"),
        _provenance(provider=""),
        _provenance(adjustment_version=""),
    ],
)
def test_score_rejects_unproven_or_non_multiplicative_adjustment_provenance(
    provenance: AdjustmentProvenance,
) -> None:
    with pytest.raises(DailyReconstructableUnavailableError) as exc_info:
        score_daily_reconstructable(_bars(), provenance=provenance)

    assert exc_info.value.reason in {
        "unproven_adjustment_point_in_time",
        "incompatible_price_basis",
    }


def test_manifest_forbids_non_daily_and_outcome_domains_and_never_matches_v3() -> None:
    manifest = daily_reconstructable_manifest()

    assert set(manifest.forbidden_input_domains) >= {
        "intraday",
        "theme",
        "catalyst",
        "validation",
        "position",
        "alert",
        "notification",
    }
    assert set(inspect.signature(score_daily_reconstructable).parameters) == {
        "bars",
        "provenance",
    }
    assert manifest.matches_identity(
        contract_id="daily_reconstructable_v1",
        score_field="research_score",
        manifest_hash=manifest.manifest_hash,
    )
    assert not manifest.matches_identity(
        contract_id="final_score_v3",
        score_field="final_score_v3",
        manifest_hash=manifest.manifest_hash,
    )
    assert "final_score_v3" in manifest.distinct_from_contracts
    assert all(
        token not in manifest.canonical_json
        for token in ("forward_return", "future_return", "realized_outcome")
    )
