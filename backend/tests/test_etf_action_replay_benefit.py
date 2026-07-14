from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.strategy_lab.etf_action_replay.benefit import (
    FULL_EXECUTION_SCENARIO,
    SMTP_ACCEPTED_SENSITIVITY_SCENARIO,
    ActionCyclePath,
    ActionFill,
    ActionMark,
    ActionNotification,
    evaluate_action_benefit,
    summarize_action_benefit,
)


def _marks(start: date, prices: list[float]) -> tuple[ActionMark, ...]:
    return tuple(
        ActionMark(trade_date=start + timedelta(days=index + 1), adjusted_close=price)
        for index, price in enumerate(prices)
    )


def test_escalated_absolute_targets_are_one_action_cycle_sample() -> None:
    signal_date = date(2026, 7, 1)
    first_fill_date = date(2026, 7, 2)
    path = ActionCyclePath(
        action_cycle_id="cycle-1",
        baseline_quantity=100.0,
        complete=True,
        fills=(
            ActionFill(
                action_decision_id=1,
                signal_date=signal_date,
                fill_date=first_fill_date,
                target_remaining_fraction=0.5,
                adjusted_fill_price=10.0,
                fees=1.0,
                taxes=0.0,
                signal_to_fill_delay=1,
            ),
            ActionFill(
                action_decision_id=2,
                signal_date=first_fill_date,
                fill_date=first_fill_date + timedelta(days=1),
                target_remaining_fraction=0.0,
                adjusted_fill_price=9.0,
                fees=1.0,
                taxes=0.0,
                signal_to_fill_delay=1,
            ),
        ),
    )

    samples = evaluate_action_benefit(
        paths=(path,),
        marks_by_asset={"cycle-1": _marks(first_fill_date, [9.0, 8.0, 7.0, 6.0])},
        horizons=(1, 3),
        scenario=FULL_EXECUTION_SCENARIO,
    )

    assert len(samples) == 2
    assert {sample.action_cycle_id for sample in samples} == {"cycle-1"}
    horizon_one = next(sample for sample in samples if sample.horizon == 1)
    assert horizon_one.action_policy_value == pytest.approx(948.0)
    assert horizon_one.hold_counterfactual_value == pytest.approx(900.0)
    assert horizon_one.benefit_return == pytest.approx(0.048)
    assert horizon_one.incurred_costs == pytest.approx(2.0)
    assert horizon_one.avoided_loss == pytest.approx(0.048)
    assert horizon_one.missed_upside == 0.0


def test_cash_growth_is_explicit_and_no_terminal_liquidation_fee_is_added() -> None:
    fill_date = date(2026, 7, 2)
    horizon_date = date(2026, 7, 3)
    path = ActionCyclePath(
        action_cycle_id="cycle-cash",
        baseline_quantity=100.0,
        complete=True,
        fills=(
            ActionFill(
                action_decision_id=11,
                signal_date=date(2026, 7, 1),
                fill_date=fill_date,
                target_remaining_fraction=0.5,
                adjusted_fill_price=10.0,
                fees=5.0,
                taxes=5.0,
                signal_to_fill_delay=1,
            ),
        ),
    )

    sample = evaluate_action_benefit(
        paths=(path,),
        marks_by_asset={"cycle-cash": (ActionMark(horizon_date, 8.0),)},
        horizons=(1,),
        scenario=FULL_EXECUTION_SCENARIO,
        cash_growth_factors={(fill_date, horizon_date): 1.1},
    )[0]

    assert sample.action_policy_value == pytest.approx(939.0)
    assert sample.hold_counterfactual_value == pytest.approx(800.0)
    assert sample.incurred_costs == 10.0
    assert sample.turnover_notional == 500.0


def test_smtp_sensitivity_uses_acceptance_only_without_repeating_actions() -> None:
    fill_date = date(2026, 7, 2)
    path = ActionCyclePath(
        action_cycle_id="cycle-mail",
        baseline_quantity=100.0,
        complete=True,
        fills=(
            ActionFill(
                action_decision_id=21,
                signal_date=date(2026, 7, 1),
                fill_date=fill_date,
                target_remaining_fraction=0.5,
                adjusted_fill_price=10.0,
                fees=0.0,
                taxes=0.0,
                signal_to_fill_delay=1,
            ),
        ),
        notifications=(
            ActionNotification(action_decision_id=21, status="failed"),
            ActionNotification(action_decision_id=21, status="smtp_accepted"),
            ActionNotification(action_decision_id=21, status="smtp_accepted"),
        ),
    )

    samples = evaluate_action_benefit(
        paths=(path,),
        marks_by_asset={"cycle-mail": _marks(fill_date, [9.0])},
        horizons=(1,),
        scenario=SMTP_ACCEPTED_SENSITIVITY_SCENARIO,
    )

    assert len(samples) == 1
    assert samples[0].turnover_notional == 500.0
    assert samples[0].scenario_label == "仅 SMTP 已接受邮件被执行敏感性"
    assert samples[0].simulated_not_observed is True


def test_incomplete_cycles_missing_windows_and_nonaccepted_mail_fail_closed() -> None:
    fill_date = date(2026, 7, 2)
    fill = ActionFill(
        action_decision_id=31,
        signal_date=date(2026, 7, 1),
        fill_date=fill_date,
        target_remaining_fraction=0.5,
        adjusted_fill_price=10.0,
        fees=0.0,
        taxes=0.0,
        signal_to_fill_delay=1,
    )
    incomplete = ActionCyclePath(
        action_cycle_id="incomplete",
        baseline_quantity=100.0,
        complete=False,
        fills=(fill,),
    )
    no_accepted_mail = ActionCyclePath(
        action_cycle_id="no-mail",
        baseline_quantity=100.0,
        complete=True,
        fills=(fill,),
        notifications=(ActionNotification(action_decision_id=31, status="failed"),),
    )

    assert (
        evaluate_action_benefit(
            paths=(incomplete,),
            marks_by_asset={"incomplete": _marks(fill_date, [9.0])},
            horizons=(1,),
            scenario=FULL_EXECUTION_SCENARIO,
        )
        == ()
    )
    assert (
        evaluate_action_benefit(
            paths=(no_accepted_mail,),
            marks_by_asset={"no-mail": _marks(fill_date, [9.0])},
            horizons=(1,),
            scenario=SMTP_ACCEPTED_SENSITIVITY_SCENARIO,
        )
        == ()
    )


def test_summary_reports_accuracy_opportunity_cost_delay_turnover_and_counts() -> None:
    fill_date = date(2026, 7, 2)
    paths = (
        ActionCyclePath(
            action_cycle_id="positive",
            baseline_quantity=100.0,
            complete=True,
            fills=(
                ActionFill(1, date(2026, 7, 1), fill_date, 0.0, 10.0, 1.0, 0.0, 1),
            ),
        ),
        ActionCyclePath(
            action_cycle_id="negative",
            baseline_quantity=100.0,
            complete=True,
            fills=(
                ActionFill(2, date(2026, 6, 30), fill_date, 0.0, 10.0, 2.0, 0.0, 2),
            ),
        ),
    )
    samples = evaluate_action_benefit(
        paths=paths,
        marks_by_asset={
            "positive": _marks(fill_date, [8.0]),
            "negative": _marks(fill_date, [12.0]),
        },
        horizons=(1,),
        scenario=FULL_EXECUTION_SCENARIO,
    )

    summary = summarize_action_benefit(samples)
    horizon = summary.horizons[0]
    assert summary.unique_action_cycle_count == 2
    assert summary.total_sample_count == 2
    assert summary.mean_signal_to_fill_delay == pytest.approx(1.5)
    assert summary.total_fees_and_taxes == 3.0
    assert summary.total_turnover_notional == 2000.0
    assert horizon.sample_count == 2
    assert horizon.positive_benefit_accuracy == 0.5
    assert horizon.mean_benefit < 0
    assert horizon.median_benefit == pytest.approx(horizon.mean_benefit)
    assert horizon.mean_avoided_loss > 0
    assert horizon.mean_missed_upside > 0


def test_relative_or_increasing_targets_and_duplicate_cycles_are_rejected() -> None:
    fill_date = date(2026, 7, 2)
    with pytest.raises(ValueError, match="absolute"):
        ActionFill(1, date(2026, 7, 1), fill_date, 1.2, 10.0, 0.0, 0.0, 1)

    invalid_path = ActionCyclePath(
        action_cycle_id="bad",
        baseline_quantity=100.0,
        complete=True,
        fills=(
            ActionFill(1, date(2026, 7, 1), fill_date, 0.5, 10.0, 0.0, 0.0, 1),
            ActionFill(2, date(2026, 7, 2), fill_date, 0.8, 10.0, 0.0, 0.0, 0),
        ),
    )
    with pytest.raises(ValueError, match="monotonic"):
        evaluate_action_benefit(
            paths=(invalid_path,),
            marks_by_asset={"bad": _marks(fill_date, [9.0])},
            horizons=(1,),
            scenario=FULL_EXECUTION_SCENARIO,
        )

    valid = ActionCyclePath(
        action_cycle_id="duplicate",
        baseline_quantity=100.0,
        complete=True,
        fills=(ActionFill(3, date(2026, 7, 1), fill_date, 0.5, 10.0, 0.0, 0.0, 1),),
    )
    with pytest.raises(ValueError, match="duplicate action cycle"):
        evaluate_action_benefit(
            paths=(valid, valid),
            marks_by_asset={"duplicate": _marks(fill_date, [9.0])},
            horizons=(1,),
            scenario=FULL_EXECUTION_SCENARIO,
        )
