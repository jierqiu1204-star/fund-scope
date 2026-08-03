from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev
from typing import Any


@dataclass(frozen=True)
class FactorObservation:
    signal_date: date
    asset_code: str
    peer_bucket: str
    history_tier: str
    factor_value: float
    baseline_value: float
    technical_momentum: float
    outcomes: Mapping[int, float | None]
    portfolio_weight: float | None = None


def _rank(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for position in range(index, end):
            ranks[ordered[position][0]] = average_rank
        index = end
    return ranks


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator > 0 else None


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return pearson(_rank(left), _rank(right))


def cross_sectional_factor_diagnostics(
    observations: Sequence[FactorObservation],
    *,
    horizons: Sequence[int] = (1, 3, 5, 10),
    quantile_count: int = 5,
    minimum_peer_count: int = 5,
) -> dict[str, Any]:
    groups: dict[tuple[date, str], list[FactorObservation]] = defaultdict(list)
    for item in observations:
        groups[(item.signal_date, item.peer_bucket)].append(item)
    ic_by_horizon: dict[int, list[float]] = defaultdict(list)
    residual_ic_by_horizon: dict[int, list[float]] = defaultdict(list)
    peer_counts: list[int] = []
    quantile_returns: dict[int, dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for rows in groups.values():
        ordered = sorted(rows, key=lambda item: (item.factor_value, item.asset_code))
        if len(ordered) < minimum_peer_count:
            continue
        peer_counts.append(len(ordered))
        for horizon in horizons:
            eligible = [
                row for row in ordered if row.outcomes.get(horizon) is not None
            ]
            if len(eligible) < minimum_peer_count:
                continue
            ic = spearman(
                [row.factor_value for row in eligible],
                [float(row.outcomes[horizon]) for row in eligible],
            )
            if ic is not None:
                ic_by_horizon[horizon].append(ic)
            residual_rows = [
                row
                for row in eligible
                if _finite_number(row.baseline_value) is not None
            ]
            if len(residual_rows) >= minimum_peer_count:
                residual_values = _residual_values_against_baseline(residual_rows)
                residual_ic = spearman(
                    residual_values,
                    [float(row.outcomes[horizon]) for row in residual_rows],
                )
                if residual_ic is not None:
                    residual_ic_by_horizon[horizon].append(residual_ic)
            for index, row in enumerate(eligible):
                bucket = min(quantile_count - 1, index * quantile_count // len(eligible))
                quantile_returns[horizon][bucket].append(float(row.outcomes[horizon]))
    horizons_out: dict[str, Any] = {}
    for horizon in horizons:
        values = ic_by_horizon[horizon]
        residual_values = residual_ic_by_horizon[horizon]
        dispersion = pstdev(values) if len(values) > 1 else 0.0
        quantiles = {
            str(bucket + 1): mean(returns)
            for bucket, returns in sorted(quantile_returns[horizon].items())
            if returns
        }
        horizons_out[str(horizon)] = {
            "ic_mean": mean(values) if values else None,
            "residual_ic_mean": (
                mean(residual_values) if residual_values else None
            ),
            "ic_dispersion": dispersion if values else None,
            "information_ratio": (
                mean(values) / dispersion if values and dispersion > 0 else None
            ),
            "sign_consistency": (
                sum(value > 0 for value in values) / len(values) if values else None
            ),
            "date_bucket_count": len(values),
            "quantile_returns": quantiles,
            "top_minus_bottom_spread": (
                quantiles.get(str(quantile_count), 0.0) - quantiles.get("1", 0.0)
                if len(quantiles) == quantile_count
                else None
            ),
        }
    return {
        "horizons": horizons_out,
        "peer_count_min": min(peer_counts) if peer_counts else 0,
        "peer_count_max": max(peer_counts) if peer_counts else 0,
    }


def residualize_sector_trend(
    observations: Sequence[FactorObservation],
) -> dict[tuple[date, str, str], float]:
    groups: dict[tuple[date, str], list[FactorObservation]] = defaultdict(list)
    for item in observations:
        groups[(item.signal_date, item.peer_bucket)].append(item)
    result: dict[tuple[date, str, str], float] = {}
    for (signal_date, bucket), rows in groups.items():
        x = [row.technical_momentum for row in rows]
        y = [row.factor_value for row in rows]
        x_mean = mean(x)
        y_mean = mean(y)
        variance = sum((value - x_mean) ** 2 for value in x)
        slope = (
            sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y, strict=True))
            / variance
            if variance > 0
            else 0.0
        )
        intercept = y_mean - slope * x_mean
        for row in rows:
            result[(signal_date, bucket, row.asset_code)] = row.factor_value - (
                intercept + slope * row.technical_momentum
            )
    return result


def redundancy_and_marginal_diagnostics(
    observations: Sequence[FactorObservation],
    *,
    horizon: int = 5,
) -> dict[str, float | int | None]:
    """Measure incremental factor evidence only on common, same-date support.

    A raw factor name is not evidence of a new signal when it reuses the baseline
    momentum/risk/liquidity inputs.  The residual is therefore fitted inside each
    factual signal-date and peer bucket, never across later dates.
    """

    eligible = [
        item
        for item in observations
        if (
            _finite_number(item.factor_value) is not None
            and _finite_number(item.baseline_value) is not None
            and _finite_number(item.outcomes.get(horizon)) is not None
        )
    ]
    factor = [float(item.factor_value) for item in eligible]
    baseline = [float(item.baseline_value) for item in eligible]
    outcomes = [float(item.outcomes[horizon]) for item in eligible]
    correlation = spearman(factor, baseline)
    residualized: list[float] = []
    residual_outcomes: list[float] = []
    groups: dict[tuple[date, str], list[FactorObservation]] = defaultdict(list)
    for item in eligible:
        groups[(item.signal_date, item.peer_bucket)].append(item)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        residualized.extend(_residual_values_against_baseline(rows))
        residual_outcomes.extend(float(item.outcomes[horizon]) for item in rows)
    factor_ic = spearman(factor, outcomes)
    baseline_ic = spearman(baseline, outcomes)
    residual_ic = spearman(residualized, residual_outcomes)
    return {
        "factor_baseline_spearman": correlation,
        "factor_outcome_spearman": factor_ic,
        "baseline_outcome_spearman": baseline_ic,
        "residual_ic": residual_ic,
        "marginal_residual_spearman": residual_ic,
        "common_support_marginal_contribution": residual_ic,
        "common_support_ic_delta": (
            factor_ic - baseline_ic
            if factor_ic is not None and baseline_ic is not None
            else None
        ),
        "common_support_observation_count": len(eligible),
        "common_support_date_count": len(
            {item.signal_date for item in eligible}
        ),
        "common_support_bucket_count": len(groups),
    }


def portfolio_diagnostics(
    observations: Sequence[FactorObservation],
    *,
    score_attr: str,
    horizon: int,
    top_ns: Sequence[int] = (5, 10, 20),
    round_trip_cost: float,
    input_count: int | None = None,
    primary_dates: Sequence[date] | None = None,
    weight_attr: str | None = None,
) -> dict[str, Any]:
    """Calculate outcome diagnostics without dropping daily implementation facts.

    `primary_dates` may contain non-overlapping outcome dates.  It deliberately
    does *not* constrain daily membership/weight turnover or common-member rank
    churn: those are measured on every consecutive eligible ranking date.
    """

    if not math.isfinite(round_trip_cost) or round_trip_cost < 0.0:
        raise ValueError("round-trip cost must be finite and non-negative")
    frozen_primary_dates = _validate_primary_dates(primary_dates)
    dates: dict[date, list[FactorObservation]] = defaultdict(list)
    invalid_score_count = 0
    for item in observations:
        if _score_value(item, score_attr) is None:
            invalid_score_count += 1
            continue
        dates[item.signal_date].append(item)
    _validate_daily_asset_uniqueness(dates)
    results: dict[str, Any] = {}
    for top_n in top_ns:
        if top_n <= 0:
            raise ValueError("Top-N diagnostics require positive portfolio size")
        gross_returns: list[float] = []
        net_returns: list[float] = []
        membership_turnovers: list[float] = []
        weight_turnovers: list[float] = []
        rank_churns: list[float] = []
        transitions: list[dict[str, Any]] = []
        prior_date: date | None = None
        prior_rows: tuple[FactorObservation, ...] | None = None
        incomplete_outcome_dates = 0
        for signal_date in sorted(dates):
            rows = _ranked_portfolio_rows(
                dates[signal_date],
                score_attr=score_attr,
                top_n=top_n,
            )
            if not rows:
                continue
            if prior_rows is not None and prior_date is not None:
                transition = _portfolio_transition(
                    previous_date=prior_date,
                    previous_rows=prior_rows,
                    current_date=signal_date,
                    current_rows=rows,
                    weight_attr=weight_attr,
                )
                transitions.append(transition)
                membership_turnovers.append(
                    float(transition["membership_turnover"])
                )
                weight_turnovers.append(float(transition["weight_turnover"]))
                if transition["normalized_rank_churn"] is not None:
                    rank_churns.append(
                        float(transition["normalized_rank_churn"])
                    )
            prior_date = signal_date
            prior_rows = rows
            if (
                frozen_primary_dates is not None
                and signal_date not in frozen_primary_dates
            ):
                continue
            outcome_values = [_finite_number(row.outcomes.get(horizon)) for row in rows]
            if any(value is None for value in outcome_values):
                incomplete_outcome_dates += 1
                continue
            gross = mean(float(value) for value in outcome_values if value is not None)
            gross_returns.append(gross)
            most_recent_turnover = (
                membership_turnovers[-1] if membership_turnovers else 1.0
            )
            net_returns.append(gross - most_recent_turnover * round_trip_cost)
        wealth = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in net_returns:
            wealth *= 1 + value
            peak = max(peak, wealth)
            max_drawdown = min(max_drawdown, wealth / peak - 1)
        results[str(top_n)] = {
            "gross_return_mean": mean(gross_returns) if gross_returns else None,
            "net_return_mean": mean(net_returns) if net_returns else None,
            # `turnover_mean` is retained as the legacy membership-turnover alias.
            "turnover_mean": (
                mean(membership_turnovers) if membership_turnovers else None
            ),
            "membership_turnover_mean": (
                mean(membership_turnovers) if membership_turnovers else None
            ),
            "weight_turnover_mean": (
                mean(weight_turnovers) if weight_turnovers else None
            ),
            "rank_churn_mean": mean(rank_churns) if rank_churns else None,
            "daily_transition_count": len(transitions),
            "common_member_rank_churn_transition_count": len(rank_churns),
            "no_common_member_transition_count": sum(
                item["normalized_rank_churn"] is None for item in transitions
            ),
            "daily_transitions": transitions,
            "outcome_incomplete_date_count": incomplete_outcome_dates,
            "maximum_drawdown": max_drawdown if net_returns else None,
            "maximum_single_weight": 1 / top_n,
            "date_count": len(net_returns),
        }
    total = input_count if input_count is not None else len(observations)
    return {
        "top_n": results,
        "exclusion_rate": 1 - len(observations) / total if total else 0.0,
        "invalid_score_exclusion_count": invalid_score_count,
        "primary_dates_are_outcome_only": frozen_primary_dates is not None,
    }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _residual_values_against_baseline(
    rows: Sequence[FactorObservation],
) -> list[float]:
    baseline = [float(item.baseline_value) for item in rows]
    factor = [float(item.factor_value) for item in rows]
    baseline_mean = mean(baseline)
    factor_mean = mean(factor)
    variance = sum((value - baseline_mean) ** 2 for value in baseline)
    slope = (
        sum(
            (x - baseline_mean) * (y - factor_mean)
            for x, y in zip(baseline, factor, strict=True)
        )
        / variance
        if variance > 0
        else 0.0
    )
    return [
        value - (factor_mean + slope * (base - baseline_mean))
        for base, value in zip(baseline, factor, strict=True)
    ]


def _score_value(item: FactorObservation, score_attr: str) -> float | None:
    try:
        raw_value = getattr(item, score_attr)
    except AttributeError as exc:
        raise ValueError(f"unknown score attribute: {score_attr}") from exc
    return _finite_number(raw_value)


def _validate_primary_dates(
    primary_dates: Sequence[date] | None,
) -> set[date] | None:
    if primary_dates is None:
        return None
    values = tuple(primary_dates)
    if len(values) != len(set(values)):
        raise ValueError("primary dates must be unique")
    return set(values)


def _validate_daily_asset_uniqueness(
    dates: Mapping[date, Sequence[FactorObservation]],
) -> None:
    for signal_date, rows in dates.items():
        codes = tuple(item.asset_code for item in rows)
        if len(codes) != len(set(codes)):
            raise ValueError(
                f"duplicate asset observations on signal date: {signal_date.isoformat()}"
            )


def _ranked_portfolio_rows(
    rows: Sequence[FactorObservation],
    *,
    score_attr: str,
    top_n: int,
) -> tuple[FactorObservation, ...]:
    return tuple(
        sorted(
            rows,
            key=lambda item: (-float(_score_value(item, score_attr) or 0.0), item.asset_code),
        )[:top_n]
    )


def _portfolio_weights(
    rows: Sequence[FactorObservation],
    *,
    weight_attr: str | None,
) -> dict[str, float]:
    if weight_attr is None:
        return {item.asset_code: 1.0 / len(rows) for item in rows}
    raw_weights: list[float] = []
    for item in rows:
        try:
            value = _finite_number(getattr(item, weight_attr))
        except AttributeError as exc:
            raise ValueError(f"unknown weight attribute: {weight_attr}") from exc
        if value is None or value < 0.0:
            raise ValueError("portfolio weights must be finite and non-negative")
        raw_weights.append(value)
    total = sum(raw_weights)
    if total <= 0.0:
        raise ValueError("portfolio weights must contain positive total weight")
    return {
        item.asset_code: weight / total
        for item, weight in zip(rows, raw_weights, strict=True)
    }


def _portfolio_transition(
    *,
    previous_date: date,
    previous_rows: Sequence[FactorObservation],
    current_date: date,
    current_rows: Sequence[FactorObservation],
    weight_attr: str | None,
) -> dict[str, Any]:
    previous_ranks = {
        item.asset_code: index for index, item in enumerate(previous_rows)
    }
    current_ranks = {
        item.asset_code: index for index, item in enumerate(current_rows)
    }
    common = tuple(sorted(set(previous_ranks) & set(current_ranks)))
    membership_turnover = 1.0 - len(common) / max(
        len(previous_rows), len(current_rows), 1
    )
    previous_weights = _portfolio_weights(previous_rows, weight_attr=weight_attr)
    current_weights = _portfolio_weights(current_rows, weight_attr=weight_attr)
    weight_turnover = 0.5 * sum(
        abs(previous_weights.get(code, 0.0) - current_weights.get(code, 0.0))
        for code in sorted(set(previous_weights) | set(current_weights))
    )
    denominator = max(max(len(previous_rows), len(current_rows)) - 1, 1)
    normalized_rank_churn = (
        mean(
            abs(previous_ranks[code] - current_ranks[code]) / denominator
            for code in common
        )
        if common
        else None
    )
    return {
        "from_date": previous_date.isoformat(),
        "to_date": current_date.isoformat(),
        "previous_member_count": len(previous_rows),
        "current_member_count": len(current_rows),
        "common_member_count": len(common),
        "membership_turnover": membership_turnover,
        "weight_turnover": weight_turnover,
        "normalized_rank_churn": normalized_rank_churn,
    }


def history_tier_diagnostics(
    observations: Sequence[FactorObservation],
    *,
    horizon: int = 5,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for tier in ("standard_history", "full_history_context"):
        rows = [
            item
            for item in observations
            if item.history_tier == tier and item.outcomes.get(horizon) is not None
        ]
        output[tier] = {
            "sample_count": len(rows),
            "factor_ic": spearman(
                [item.factor_value for item in rows],
                [float(item.outcomes[horizon]) for item in rows],
            ),
        }
    signs = {
        math.copysign(1, item["factor_ic"])
        for item in output.values()
        if item["factor_ic"] not in (None, 0)
    }
    output["stable_across_history_tiers"] = len(signs) <= 1 and bool(signs)
    return output
