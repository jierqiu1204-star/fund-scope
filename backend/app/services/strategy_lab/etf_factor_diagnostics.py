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
            for index, row in enumerate(eligible):
                bucket = min(quantile_count - 1, index * quantile_count // len(eligible))
                quantile_returns[horizon][bucket].append(float(row.outcomes[horizon]))
    horizons_out: dict[str, Any] = {}
    for horizon in horizons:
        values = ic_by_horizon[horizon]
        dispersion = pstdev(values) if len(values) > 1 else 0.0
        quantiles = {
            str(bucket + 1): mean(returns)
            for bucket, returns in sorted(quantile_returns[horizon].items())
            if returns
        }
        horizons_out[str(horizon)] = {
            "ic_mean": mean(values) if values else None,
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
) -> dict[str, float | None]:
    eligible = [item for item in observations if item.outcomes.get(horizon) is not None]
    factor = [item.factor_value for item in eligible]
    baseline = [item.baseline_value for item in eligible]
    outcomes = [float(item.outcomes[horizon]) for item in eligible]
    correlation = spearman(factor, baseline)
    residualized = []
    if eligible:
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
        residualized = [
            y - (factor_mean + slope * (x - baseline_mean))
            for x, y in zip(baseline, factor, strict=True)
        ]
    return {
        "factor_baseline_spearman": correlation,
        "factor_outcome_spearman": spearman(factor, outcomes),
        "marginal_residual_spearman": spearman(residualized, outcomes),
    }


def portfolio_diagnostics(
    observations: Sequence[FactorObservation],
    *,
    score_attr: str,
    horizon: int,
    top_ns: Sequence[int] = (5, 10, 20),
    round_trip_cost: float,
    input_count: int | None = None,
) -> dict[str, Any]:
    dates: dict[date, list[FactorObservation]] = defaultdict(list)
    for item in observations:
        if item.outcomes.get(horizon) is not None:
            dates[item.signal_date].append(item)
    previous: dict[int, set[str]] = {}
    results: dict[str, Any] = {}
    for top_n in top_ns:
        gross_returns: list[float] = []
        net_returns: list[float] = []
        turnovers: list[float] = []
        churns: list[float] = []
        for signal_date in sorted(dates):
            rows = sorted(
                dates[signal_date],
                key=lambda item: (-float(getattr(item, score_attr)), item.asset_code),
            )[:top_n]
            if not rows:
                continue
            selected = {row.asset_code for row in rows}
            prior = previous.get(top_n, set())
            turnover = (
                1.0 if not prior else 1 - len(selected & prior) / max(len(selected), 1)
            )
            gross = mean(float(row.outcomes[horizon]) for row in rows)
            gross_returns.append(gross)
            net_returns.append(gross - turnover * round_trip_cost)
            turnovers.append(turnover)
            churns.append(turnover)
            previous[top_n] = selected
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
            "turnover_mean": mean(turnovers) if turnovers else None,
            "rank_churn_mean": mean(churns) if churns else None,
            "maximum_drawdown": max_drawdown if net_returns else None,
            "maximum_single_weight": 1 / top_n,
            "date_count": len(net_returns),
        }
    total = input_count if input_count is not None else len(observations)
    return {
        "top_n": results,
        "exclusion_rate": 1 - len(observations) / total if total else 0.0,
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
