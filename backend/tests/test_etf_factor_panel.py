from __future__ import annotations

from datetime import date, datetime

from app.services.strategy_lab.etf_factor_experiment import ExecutionCostPolicy
from app.services.strategy_lab.etf_factor_panel import (
    AdjustedOutcomePrice,
    PointInTimeFactorInput,
    build_common_support_panel,
)


def _input(code: str = "510001") -> PointInTimeFactorInput:
    return PointInTimeFactorInput(
        asset_code=code,
        signal_date=date(2026, 7, 1),
        source_cutoff=datetime(2026, 7, 1, 15),
        historical_member=True,
        decision_eligible=True,
        price_basis="total_return_adjusted",
        data_provider="eastmoney",
        provider_version="v1",
        adjustment_version="hfq-v1",
        latest_factor_input_date=date(2026, 7, 1),
        eligible_history_sessions=250,
        baseline_score=70,
        candidate_scores={"candidate": 80},
        peer_bucket="broad-equity",
        history_tier="full_history_context",
    )


def _exchange_sessions() -> tuple[date, ...]:
    return (
        date(2026, 7, 2),
        date(2026, 7, 3),
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 8),
        date(2026, 7, 9),
        date(2026, 7, 10),
        date(2026, 7, 13),
        date(2026, 7, 14),
        date(2026, 7, 15),
        date(2026, 7, 16),
        date(2026, 7, 17),
    )


def _outcomes(code: str = "510001", count: int = 12) -> list[AdjustedOutcomePrice]:
    return [
        AdjustedOutcomePrice(
            asset_code=code,
            trade_date=trade_date,
            adjusted_close=100 + index,
            source_timestamp=datetime.combine(trade_date, datetime.min.time()).replace(
                hour=16
            ),
            decision_eligible=True,
            price_basis="total_return_adjusted",
            data_provider="eastmoney",
            provider_version="v1",
            adjustment_version="hfq-v1",
        )
        for index, trade_date in enumerate(_exchange_sessions()[:count])
    ]


def _costs() -> ExecutionCostPolicy:
    return ExecutionCostPolicy(
        entry_rule="t_plus_1_adjusted_close",
        exit_rule="declared_horizon_adjusted_close",
        fee_bps_per_side=1,
        slippage_bps_per_side=2,
        turnover_charge_rule="two_sided_realized_turnover",
    )


def test_panel_uses_t_plus_one_and_non_zero_costs_without_factor_lookahead() -> None:
    panel = build_common_support_panel(
        [_input()],
        {"510001": _outcomes()},
        exchange_session_dates=_exchange_sessions(),
        candidate_ids=("candidate",),
        horizons=(1, 3, 5, 10),
        cost_policy=_costs(),
    )
    sample = panel.samples[0]

    assert sample.entry_date == date(2026, 7, 2)
    assert sample.gross_returns[5] == 105 / 100 - 1
    assert sample.net_returns[5] == sample.gross_returns[5] - 0.0006
    assert sample.pending_horizons == ()


def test_panel_excludes_future_factor_inputs_and_missing_membership() -> None:
    future = _input("510002")
    future = PointInTimeFactorInput(
        **{
            **future.__dict__,
            "latest_factor_input_date": date(2026, 7, 2),
            "historical_member": False,
        }
    )
    panel = build_common_support_panel(
        [future],
        {"510002": _outcomes("510002")},
        exchange_session_dates=_exchange_sessions(),
        candidate_ids=("candidate",),
        horizons=(1, 3, 5, 10),
        cost_policy=_costs(),
    )

    assert panel.samples == ()
    assert panel.exclusions["2026-07-01:510002"] == (
        "factor_window_uses_future_input",
        "missing_historical_membership",
    )


def test_panel_reports_common_support_separately_from_all_available() -> None:
    missing_candidate = _input("510002")
    missing_candidate = PointInTimeFactorInput(
        **{
            **missing_candidate.__dict__,
            "candidate_scores": {"candidate": None},
        }
    )
    panel = build_common_support_panel(
        [_input(), missing_candidate],
        {"510001": _outcomes(), "510002": _outcomes("510002", count=3)},
        exchange_session_dates=_exchange_sessions(),
        candidate_ids=("candidate",),
        horizons=(1, 3, 5, 10),
        cost_policy=_costs(),
    )

    assert panel.coverage == {
        "input_count": 2,
        "common_support_count": 1,
        "common_support_ratio": 0.5,
        "baseline_all_available_count": 2,
        "candidate_all_available_count": 1,
    }


def test_panel_does_not_shift_t_plus_one_or_declared_exit_when_rows_are_missing() -> None:
    outcomes = _outcomes()
    without_entry = outcomes[1:]
    entry_missing = build_common_support_panel(
        [_input()],
        {"510001": without_entry},
        exchange_session_dates=_exchange_sessions(),
        candidate_ids=("candidate",),
        horizons=(1, 3, 5, 10),
        cost_policy=_costs(),
    ).samples[0]

    assert entry_missing.entry_date is None
    assert entry_missing.pending_horizons == (1, 3, 5, 10)

    without_three_session_exit = [
        outcome
        for outcome in outcomes
        if outcome.trade_date != _exchange_sessions()[3]
    ]
    exit_missing = build_common_support_panel(
        [_input()],
        {"510001": without_three_session_exit},
        exchange_session_dates=_exchange_sessions(),
        candidate_ids=("candidate",),
        horizons=(1, 3, 5, 10),
        cost_policy=_costs(),
    ).samples[0]

    assert exit_missing.entry_date == _exchange_sessions()[0]
    assert exit_missing.gross_returns[3] is None
    assert exit_missing.gross_returns[5] is not None
    assert exit_missing.pending_horizons == (3,)
