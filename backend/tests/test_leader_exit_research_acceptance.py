"""Acceptance checks for the shared live/research leader exit inputs."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.services.leader_tactics_exit_policy import leader_atr20
from app.services.risk_alerts import LeaderTacticsDailyBar, _leader_atr20


def test_atr_counts_overnight_gap_and_live_wrapper_ignores_later_bars():
    # Nineteen ranges of 2 and one overnight gap range of 11: ATR = 2.45.
    closes = (100.0,) * 20 + (110.0,)
    highs = tuple(value + 1 for value in closes)
    lows = tuple(value - 1 for value in closes)
    assert leader_atr20(highs, lows, closes) == pytest.approx(2.45)
    bars = [
        LeaderTacticsDailyBar(
            trade_date=date(2026, 1, 1) + timedelta(days=index),
            adjusted_open=close,
            adjusted_high=close + 1,
            adjusted_low=close - 1,
            adjusted_close=close,
            provider="acceptance-fixture",
            adjustment_version="fixture-v1",
            revision_id=f"bar-{index}",
            received_at=None,
        )
        for index, close in enumerate((*closes, 1000.0))
    ]
    assert _leader_atr20(bars, 20) == pytest.approx(2.45)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0.0, True])
def test_atr_rejects_unusable_entry_risk_instead_of_inventing_stop(bad):
    closes = (100.0,) * 21
    assert leader_atr20((101.0,) * 20 + (bad,), (99.0,) * 21, closes) is None


def test_atr_requires_full_aligned_warmup_and_nonzero_true_range():
    assert leader_atr20((101.0,) * 20, (99.0,) * 20, (100.0,) * 20) is None
    assert leader_atr20((101.0,) * 21, (99.0,) * 20, (100.0,) * 21) is None
    assert leader_atr20((100.0,) * 21, (100.0,) * 21, (100.0,) * 21) is None


@pytest.mark.parametrize("universe", ["etf", "ashare"])
def test_real_qualifying_v2_producer_emits_signal_low_for_tracking(universe):
    from test_dual_universe_leader_tactics_v2_formula_boundaries import (
        _asset,
        _bars,
        _group_membership,
    )

    from app.services.etf_research_evidence import stable_contract_hash
    from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
        BREAKOUT_V2,
        screen_dual_universe,
    )

    membership = _group_membership("fine_theme:passive_components", "被动元件/MLCC")
    bars = list(_bars())
    bars[-1] = replace(bars[-1], adjusted_open=119.0, adjusted_high=121.0,
                       adjusted_low=118.0, adjusted_close=120.0,
                       volume=10_000.0, amount=10_000_000.0, turnover=10_000_000.0)
    code = "510999" if universe == "etf" else "000636"
    items = (_asset(code, universe=universe, bars=tuple(bars), membership=membership), *(
        _asset(f"51000{i}", universe=universe, membership=membership) for i in range(1, 6)
    ))
    result = screen_dual_universe(items, theme_percentile_overrides={"fine_theme:passive_components": (1.0, 1.0, 1.0)})
    candidate = next(row for row in result.observations if row.asset_code == code and row.formula_id == BREAKOUT_V2)
    assert candidate.qualifies is True
    assert dict(candidate.gate_facts)["adjusted_low"] == 118.0
    assert candidate.feature_hash == stable_contract_hash(candidate.canonical_payload())
