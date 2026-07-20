from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from app.services.strategy_lab.etf_factor_experiment import ExecutionCostPolicy


@dataclass(frozen=True)
class PointInTimeFactorInput:
    asset_code: str
    signal_date: date
    source_cutoff: datetime
    historical_member: bool
    decision_eligible: bool
    price_basis: str
    data_provider: str | None
    provider_version: str | None
    adjustment_version: str | None
    latest_factor_input_date: date
    eligible_history_sessions: int
    baseline_score: float | None
    candidate_scores: Mapping[str, float | None]
    peer_bucket: str
    history_tier: str


@dataclass(frozen=True)
class AdjustedOutcomePrice:
    asset_code: str
    trade_date: date
    adjusted_close: float
    source_timestamp: datetime
    decision_eligible: bool
    price_basis: str
    data_provider: str | None
    provider_version: str | None
    adjustment_version: str | None


@dataclass(frozen=True)
class CommonSupportSample:
    asset_code: str
    signal_date: date
    baseline_score: float
    candidate_scores: Mapping[str, float]
    peer_bucket: str
    history_tier: str
    entry_date: date | None
    gross_returns: Mapping[int, float | None]
    net_returns: Mapping[int, float | None]
    pending_horizons: tuple[int, ...]


@dataclass(frozen=True)
class CommonSupportPanel:
    samples: tuple[CommonSupportSample, ...]
    exclusions: Mapping[str, tuple[str, ...]]
    coverage: Mapping[str, float | int]


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def build_common_support_panel(
    factor_inputs: list[PointInTimeFactorInput],
    outcomes_by_code: Mapping[str, list[AdjustedOutcomePrice]],
    *,
    exchange_session_dates: tuple[date, ...],
    candidate_ids: tuple[str, ...],
    horizons: tuple[int, ...],
    cost_policy: ExecutionCostPolicy,
) -> CommonSupportPanel:
    samples: list[CommonSupportSample] = []
    exclusions: dict[str, tuple[str, ...]] = {}
    baseline_available = 0
    candidate_available = 0
    for row in sorted(factor_inputs, key=lambda item: (item.signal_date, item.asset_code)):
        reasons: list[str] = []
        if not row.historical_member:
            reasons.append("missing_historical_membership")
        if not row.decision_eligible:
            reasons.append("factor_input_not_decision_eligible")
        if row.price_basis != "total_return_adjusted":
            reasons.append("factor_input_not_total_return_adjusted")
        if not row.data_provider or not row.provider_version or not row.adjustment_version:
            reasons.append("factor_input_missing_provenance")
        if row.latest_factor_input_date > row.signal_date:
            reasons.append("factor_window_uses_future_input")
        baseline = _finite(row.baseline_score)
        if baseline is None:
            reasons.append("baseline_score_unavailable")
        else:
            baseline_available += 1
        candidates = {
            candidate_id: _finite(row.candidate_scores.get(candidate_id))
            for candidate_id in candidate_ids
        }
        if all(value is not None for value in candidates.values()):
            candidate_available += 1
        for candidate_id, value in candidates.items():
            if value is None:
                reasons.append(f"{candidate_id}:unavailable")
        key = f"{row.signal_date.isoformat()}:{row.asset_code}"
        if reasons:
            exclusions[key] = tuple(sorted(set(reasons)))
            continue

        future_sessions = tuple(
            session_date
            for session_date in sorted(set(exchange_session_dates))
            if session_date > row.signal_date
        )
        eligible_outcomes = {
            outcome.trade_date: outcome
            for outcome in sorted(
                outcomes_by_code.get(row.asset_code, []),
                key=lambda item: item.trade_date,
            )
            if outcome.trade_date in future_sessions
            and outcome.decision_eligible
            and outcome.price_basis == "total_return_adjusted"
            and outcome.data_provider
            and outcome.provider_version
            and outcome.adjustment_version
            and outcome.source_timestamp > row.source_cutoff
            and _finite(outcome.adjusted_close) is not None
            and outcome.adjusted_close > 0
        }
        entry = (
            eligible_outcomes.get(future_sessions[0])
            if future_sessions
            else None
        )
        gross: dict[int, float | None] = {}
        net: dict[int, float | None] = {}
        pending: list[int] = []
        round_trip_cost = (
            2
            * (
                cost_policy.fee_bps_per_side
                + cost_policy.slippage_bps_per_side
            )
            / 10_000
        )
        for horizon in horizons:
            exit_date = (
                future_sessions[horizon]
                if len(future_sessions) > horizon
                else None
            )
            exit_price = eligible_outcomes.get(exit_date) if exit_date else None
            if entry is None or exit_price is None:
                gross[horizon] = None
                net[horizon] = None
                pending.append(horizon)
                continue
            value = exit_price.adjusted_close / entry.adjusted_close - 1
            gross[horizon] = value
            net[horizon] = value - round_trip_cost
        samples.append(
            CommonSupportSample(
                asset_code=row.asset_code,
                signal_date=row.signal_date,
                baseline_score=baseline,  # type: ignore[arg-type]
                candidate_scores={key: value for key, value in candidates.items() if value is not None},
                peer_bucket=row.peer_bucket,
                history_tier=row.history_tier,
                entry_date=entry.trade_date if entry else None,
                gross_returns=gross,
                net_returns=net,
                pending_horizons=tuple(pending),
            )
        )
    total = len(factor_inputs)
    common = len(samples)
    return CommonSupportPanel(
        samples=tuple(samples),
        exclusions=exclusions,
        coverage={
            "input_count": total,
            "common_support_count": common,
            "common_support_ratio": common / total if total else 0.0,
            "baseline_all_available_count": baseline_available,
            "candidate_all_available_count": candidate_available,
        },
    )
