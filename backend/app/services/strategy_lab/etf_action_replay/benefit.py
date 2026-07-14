from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import mean, median

FULL_EXECUTION_SCENARIO = "动作建议完全执行情景"
SMTP_ACCEPTED_SENSITIVITY_SCENARIO = "仅 SMTP 已接受邮件被执行敏感性"
SUPPORTED_HORIZONS = frozenset({1, 3, 5, 10})


def _finite(name: str, value: float, *, positive: bool = False) -> None:
    if not math.isfinite(value) or (positive and value <= 0):
        qualifier = "positive " if positive else ""
        raise ValueError(f"{name} must be {qualifier}finite")


@dataclass(frozen=True)
class ActionFill:
    action_decision_id: int
    signal_date: date
    fill_date: date
    target_remaining_fraction: float
    adjusted_fill_price: float
    fees: float
    taxes: float
    signal_to_fill_delay: int

    def __post_init__(self) -> None:
        if self.action_decision_id <= 0:
            raise ValueError("action_decision_id must be positive")
        if not 0.0 <= self.target_remaining_fraction <= 1.0:
            raise ValueError("action target must be an absolute fraction between 0 and 1")
        _finite("adjusted_fill_price", self.adjusted_fill_price, positive=True)
        _finite("fees", self.fees)
        _finite("taxes", self.taxes)
        if self.fees < 0 or self.taxes < 0:
            raise ValueError("incurred costs must be non-negative")
        if self.fill_date < self.signal_date or self.signal_to_fill_delay < 0:
            raise ValueError("signal-to-fill delay must be non-negative")


@dataclass(frozen=True)
class ActionNotification:
    action_decision_id: int
    status: str


@dataclass(frozen=True)
class ActionMark:
    trade_date: date
    adjusted_close: float

    def __post_init__(self) -> None:
        _finite("adjusted_close", self.adjusted_close, positive=True)


@dataclass(frozen=True)
class ActionCyclePath:
    action_cycle_id: str
    baseline_quantity: float
    complete: bool
    fills: tuple[ActionFill, ...]
    notifications: tuple[ActionNotification, ...] = ()

    def __post_init__(self) -> None:
        if not self.action_cycle_id.strip():
            raise ValueError("action_cycle_id is required")
        _finite("baseline_quantity", self.baseline_quantity, positive=True)
        decision_ids = [fill.action_decision_id for fill in self.fills]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("duplicate action decision fill")


@dataclass(frozen=True)
class ActionBenefitSample:
    action_cycle_id: str
    horizon: int
    valuation_date: date
    scenario_label: str
    simulated_not_observed: bool
    action_policy_value: float
    hold_counterfactual_value: float
    benefit_return: float
    avoided_loss: float
    missed_upside: float
    incurred_costs: float
    turnover_notional: float
    signal_to_fill_delay: int


@dataclass(frozen=True)
class HorizonBenefitSummary:
    horizon: int
    sample_count: int
    positive_benefit_accuracy: float | None
    mean_benefit: float | None
    median_benefit: float | None
    mean_avoided_loss: float | None
    mean_missed_upside: float | None


@dataclass(frozen=True)
class ActionBenefitSummary:
    horizons: tuple[HorizonBenefitSummary, ...]
    unique_action_cycle_count: int
    total_sample_count: int
    mean_signal_to_fill_delay: float | None
    total_turnover_notional: float
    total_fees_and_taxes: float
    research_only: bool = True
    simulated_not_observed: bool = True


def evaluate_action_benefit(
    *,
    paths: Sequence[ActionCyclePath],
    marks_by_asset: Mapping[str, Sequence[ActionMark]],
    horizons: Sequence[int] = (1, 3, 5, 10),
    scenario: str = FULL_EXECUTION_SCENARIO,
    cash_growth_factors: Mapping[tuple[date, date], float] | None = None,
) -> tuple[ActionBenefitSample, ...]:
    if scenario not in {
        FULL_EXECUTION_SCENARIO,
        SMTP_ACCEPTED_SENSITIVITY_SCENARIO,
    }:
        raise ValueError("unsupported simulated execution scenario")
    requested_horizons = tuple(sorted(set(horizons)))
    if not requested_horizons or any(
        horizon not in SUPPORTED_HORIZONS for horizon in requested_horizons
    ):
        raise ValueError("horizons must be selected from 1/3/5/10 trading days")

    cycle_ids = [path.action_cycle_id for path in paths]
    if len(cycle_ids) != len(set(cycle_ids)):
        raise ValueError("duplicate action cycle")

    samples: list[ActionBenefitSample] = []
    for path in paths:
        if not path.complete:
            continue
        fills = _scenario_fills(path, scenario)
        if not fills:
            continue
        _validate_monotonic_targets(fills)
        first_fill = fills[0]
        start_value = path.baseline_quantity * first_fill.adjusted_fill_price
        marks = tuple(
            sorted(
                (
                    mark
                    for mark in marks_by_asset.get(path.action_cycle_id, ())
                    if mark.trade_date > first_fill.fill_date
                ),
                key=lambda mark: mark.trade_date,
            )
        )
        for horizon in requested_horizons:
            if len(marks) < horizon:
                continue
            mark = marks[horizon - 1]
            policy_value, costs, turnover = _policy_value_at(
                path=path,
                fills=fills,
                mark=mark,
                cash_growth_factors=cash_growth_factors or {},
            )
            hold_value = path.baseline_quantity * mark.adjusted_close
            benefit = (policy_value - hold_value) / start_value
            samples.append(
                ActionBenefitSample(
                    action_cycle_id=path.action_cycle_id,
                    horizon=horizon,
                    valuation_date=mark.trade_date,
                    scenario_label=scenario,
                    simulated_not_observed=True,
                    action_policy_value=policy_value,
                    hold_counterfactual_value=hold_value,
                    benefit_return=benefit,
                    avoided_loss=max(0.0, benefit),
                    missed_upside=max(0.0, -benefit),
                    incurred_costs=costs,
                    turnover_notional=turnover,
                    signal_to_fill_delay=first_fill.signal_to_fill_delay,
                )
            )
    return tuple(samples)


def _scenario_fills(path: ActionCyclePath, scenario: str) -> tuple[ActionFill, ...]:
    ordered = tuple(sorted(path.fills, key=lambda fill: (fill.fill_date, fill.action_decision_id)))
    if scenario == FULL_EXECUTION_SCENARIO:
        return ordered
    accepted_action_ids = {
        notification.action_decision_id
        for notification in path.notifications
        if notification.status == "smtp_accepted"
    }
    return tuple(fill for fill in ordered if fill.action_decision_id in accepted_action_ids)


def _validate_monotonic_targets(fills: Sequence[ActionFill]) -> None:
    previous = 1.0
    for fill in fills:
        target = fill.target_remaining_fraction
        if target > previous:
            raise ValueError("absolute action targets must be monotonic non-increasing")
        if math.isclose(target, previous, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("duplicate absolute target cannot create another fill")
        previous = target


def _policy_value_at(
    *,
    path: ActionCyclePath,
    fills: Sequence[ActionFill],
    mark: ActionMark,
    cash_growth_factors: Mapping[tuple[date, date], float],
) -> tuple[float, float, float]:
    previous_target = 1.0
    remaining_quantity = path.baseline_quantity
    cash_value = 0.0
    incurred_costs = 0.0
    turnover = 0.0
    for fill in fills:
        if fill.fill_date > mark.trade_date:
            break
        sold_quantity = path.baseline_quantity * (
            previous_target - fill.target_remaining_fraction
        )
        gross_proceeds = sold_quantity * fill.adjusted_fill_price
        costs = fill.fees + fill.taxes
        growth = cash_growth_factors.get((fill.fill_date, mark.trade_date), 1.0)
        _finite("cash growth factor", growth)
        if growth < 0:
            raise ValueError("cash growth factor must be non-negative")
        cash_value += (gross_proceeds - costs) * growth
        incurred_costs += costs
        turnover += gross_proceeds
        previous_target = fill.target_remaining_fraction
        remaining_quantity = path.baseline_quantity * previous_target
    return (
        cash_value + remaining_quantity * mark.adjusted_close,
        incurred_costs,
        turnover,
    )


def summarize_action_benefit(
    samples: Sequence[ActionBenefitSample],
) -> ActionBenefitSummary:
    grouped: dict[int, list[ActionBenefitSample]] = {}
    representative_by_cycle: dict[str, ActionBenefitSample] = {}
    for sample in samples:
        grouped.setdefault(sample.horizon, []).append(sample)
        current = representative_by_cycle.get(sample.action_cycle_id)
        if current is None or sample.horizon > current.horizon:
            representative_by_cycle[sample.action_cycle_id] = sample

    horizon_summaries = []
    for horizon in sorted(grouped):
        rows = grouped[horizon]
        benefits = [row.benefit_return for row in rows]
        horizon_summaries.append(
            HorizonBenefitSummary(
                horizon=horizon,
                sample_count=len(rows),
                positive_benefit_accuracy=(
                    sum(value > 0 for value in benefits) / len(benefits)
                ),
                mean_benefit=mean(benefits),
                median_benefit=median(benefits),
                mean_avoided_loss=mean(row.avoided_loss for row in rows),
                mean_missed_upside=mean(row.missed_upside for row in rows),
            )
        )

    representatives = tuple(representative_by_cycle.values())
    return ActionBenefitSummary(
        horizons=tuple(horizon_summaries),
        unique_action_cycle_count=len(representatives),
        total_sample_count=len(samples),
        mean_signal_to_fill_delay=(
            mean(row.signal_to_fill_delay for row in representatives)
            if representatives
            else None
        ),
        total_turnover_notional=sum(row.turnover_notional for row in representatives),
        total_fees_and_taxes=sum(row.incurred_costs for row in representatives),
    )
