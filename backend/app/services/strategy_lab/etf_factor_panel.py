from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from app.services.etf_research_evidence import stable_contract_hash
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
    candidate_observations: Mapping[str, CandidateFactorObservation] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class CandidateFactorObservation:
    candidate_id: str
    availability: str
    qualifies: bool
    score: float | None
    gate_reasons: tuple[str, ...] = ()
    unavailable_reasons: tuple[str, ...] = ()
    observation_hash: str | None = None

    def validate(self) -> None:
        if self.availability not in {"available", "unavailable"}:
            raise ValueError("candidate availability must be explicit")
        if self.availability == "unavailable":
            if self.qualifies or self.score is not None or not self.unavailable_reasons:
                raise ValueError("unavailable candidate observation is inconsistent")
        elif self.qualifies:
            if _finite(self.score) is None or self.gate_reasons:
                raise ValueError("qualified candidate observation requires a finite score")
        elif self.score is not None or not self.gate_reasons:
            raise ValueError("failed candidate gate requires reasons and no score")
        if self.observation_hash is not None:
            expected = stable_contract_hash(
                {
                    "candidate_id": self.candidate_id,
                    "availability": self.availability,
                    "qualifies": self.qualifies,
                    "score": self.score,
                    "gate_reasons": self.gate_reasons,
                    "unavailable_reasons": self.unavailable_reasons,
                }
            )
            if self.observation_hash != expected:
                raise ValueError("candidate observation hash is incompatible")


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


@dataclass(frozen=True)
class CandidateDateCohort:
    signal_date: date
    baseline_asset_codes: tuple[str, ...]
    candidate_asset_codes: tuple[str, ...]
    complete: bool
    exclusion_reason: str | None
    cohort_hash: str


@dataclass(frozen=True)
class CandidateCommonSupportPanel:
    candidate_id: str
    baseline_samples: tuple[CommonSupportSample, ...]
    candidate_samples: tuple[CommonSupportSample, ...]
    cohorts: tuple[CandidateDateCohort, ...]
    exclusions: Mapping[str, tuple[str, ...]]
    coverage: Mapping[str, float | int]
    panel_hash: str


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


def _base_factor_reasons(row: PointInTimeFactorInput) -> tuple[str, ...]:
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
    if _finite(row.baseline_score) is None:
        reasons.append("baseline_score_unavailable")
    return tuple(sorted(set(reasons)))


def _support_sample(
    row: PointInTimeFactorInput,
    outcomes_by_code: Mapping[str, list[AdjustedOutcomePrice]],
    *,
    exchange_session_dates: tuple[date, ...],
    candidate_scores: Mapping[str, float],
    horizons: tuple[int, ...],
    cost_policy: ExecutionCostPolicy,
) -> CommonSupportSample:
    baseline = _finite(row.baseline_score)
    if baseline is None:
        raise ValueError("support sample requires a finite baseline")
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
    entry = eligible_outcomes.get(future_sessions[0]) if future_sessions else None
    gross: dict[int, float | None] = {}
    net: dict[int, float | None] = {}
    pending: list[int] = []
    round_trip_cost = (
        2
        * (cost_policy.fee_bps_per_side + cost_policy.slippage_bps_per_side)
        / 10_000
    )
    for horizon in horizons:
        exit_date = future_sessions[horizon] if len(future_sessions) > horizon else None
        exit_price = eligible_outcomes.get(exit_date) if exit_date else None
        if entry is None or exit_price is None:
            gross[horizon] = None
            net[horizon] = None
            pending.append(horizon)
            continue
        value = exit_price.adjusted_close / entry.adjusted_close - 1
        gross[horizon] = value
        net[horizon] = value - round_trip_cost
    return CommonSupportSample(
        asset_code=row.asset_code,
        signal_date=row.signal_date,
        baseline_score=baseline,
        candidate_scores=dict(candidate_scores),
        peer_bucket=row.peer_bucket,
        history_tier=row.history_tier,
        entry_date=entry.trade_date if entry else None,
        gross_returns=gross,
        net_returns=net,
        pending_horizons=tuple(pending),
    )


def _candidate_observation(
    row: PointInTimeFactorInput,
    candidate_id: str,
) -> CandidateFactorObservation:
    explicit = row.candidate_observations.get(candidate_id)
    if explicit is not None:
        if explicit.candidate_id != candidate_id:
            raise ValueError("candidate observation identity mismatch")
        explicit.validate()
        return explicit
    score = _finite(row.candidate_scores.get(candidate_id))
    if score is None:
        return CandidateFactorObservation(
            candidate_id=candidate_id,
            availability="unavailable",
            qualifies=False,
            score=None,
            unavailable_reasons=(f"{candidate_id}:unavailable",),
        )
    return CandidateFactorObservation(
        candidate_id=candidate_id,
        availability="available",
        qualifies=True,
        score=score,
    )


def _candidate_panel_hash(
    *,
    candidate_id: str,
    baseline_samples: tuple[CommonSupportSample, ...],
    candidate_samples: tuple[CommonSupportSample, ...],
    cohorts: tuple[CandidateDateCohort, ...],
    exclusions: Mapping[str, tuple[str, ...]],
) -> str:
    return stable_contract_hash(
        {
            "candidate_id": candidate_id,
            "baseline_samples": tuple(asdict(item) for item in baseline_samples),
            "candidate_samples": tuple(asdict(item) for item in candidate_samples),
            "cohorts": tuple(asdict(item) for item in cohorts),
            "exclusions": dict(sorted(exclusions.items())),
        }
    )


def build_candidate_common_support_panels(
    factor_inputs: list[PointInTimeFactorInput],
    outcomes_by_code: Mapping[str, list[AdjustedOutcomePrice]],
    *,
    exchange_session_dates: tuple[date, ...],
    candidate_ids: tuple[str, ...],
    horizons: tuple[int, ...],
    cost_policy: ExecutionCostPolicy,
    primary_top_n: int = 10,
) -> Mapping[str, CandidateCommonSupportPanel]:
    """Build one candidate-versus-baseline panel without joint score intersection.

    An observed tactical gate failure remains in the complete baseline universe.
    A missing candidate input is excluded only from that candidate. Candidate
    cohorts contain qualified rows only and are never padded.
    """

    if primary_top_n < 1:
        raise ValueError("primary_top_n must be positive")
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_ids must be unique and non-empty")
    ordered_inputs = tuple(
        sorted(factor_inputs, key=lambda item: (item.signal_date, item.asset_code))
    )
    panels: dict[str, CandidateCommonSupportPanel] = {}
    for candidate_id in candidate_ids:
        baseline_samples: list[CommonSupportSample] = []
        candidate_samples: list[CommonSupportSample] = []
        exclusions: dict[str, tuple[str, ...]] = {}
        available_count = 0
        qualified_count = 0
        for row in ordered_inputs:
            key = f"{row.signal_date.isoformat()}:{row.asset_code}"
            base_reasons = _base_factor_reasons(row)
            if base_reasons:
                exclusions[key] = base_reasons
                continue
            baseline_sample = _support_sample(
                row,
                outcomes_by_code,
                exchange_session_dates=exchange_session_dates,
                candidate_scores={},
                horizons=horizons,
                cost_policy=cost_policy,
            )
            baseline_samples.append(baseline_sample)
            observation = _candidate_observation(row, candidate_id)
            if observation.availability == "unavailable":
                exclusions[key] = observation.unavailable_reasons
                continue
            available_count += 1
            if not observation.qualifies:
                exclusions[key] = observation.gate_reasons
                continue
            score = _finite(observation.score)
            if score is None:
                raise ValueError("qualified candidate score must be finite")
            qualified_count += 1
            candidate_samples.append(
                _support_sample(
                    row,
                    outcomes_by_code,
                    exchange_session_dates=exchange_session_dates,
                    candidate_scores={candidate_id: score},
                    horizons=horizons,
                    cost_policy=cost_policy,
                )
            )

        baseline_tuple = tuple(baseline_samples)
        candidate_tuple = tuple(candidate_samples)
        dates = sorted({row.signal_date for row in baseline_tuple})
        cohorts: list[CandidateDateCohort] = []
        for signal_date in dates:
            baseline_codes = tuple(
                row.asset_code
                for row in sorted(
                    (
                        item
                        for item in baseline_tuple
                        if item.signal_date == signal_date
                    ),
                    key=lambda item: (-item.baseline_score, item.asset_code),
                )[:primary_top_n]
            )
            candidate_codes = tuple(
                row.asset_code
                for row in sorted(
                    (
                        item
                        for item in candidate_tuple
                        if item.signal_date == signal_date
                    ),
                    key=lambda item: (
                        -float(item.candidate_scores[candidate_id]),
                        item.asset_code,
                    ),
                )[:primary_top_n]
            )
            if len(candidate_codes) < primary_top_n:
                reason = "insufficient_candidate_cohort"
            elif len(baseline_codes) < primary_top_n:
                reason = "insufficient_baseline_cohort"
            else:
                reason = None
            payload = {
                "candidate_id": candidate_id,
                "signal_date": signal_date,
                "baseline_asset_codes": baseline_codes,
                "candidate_asset_codes": candidate_codes,
                "complete": reason is None,
                "exclusion_reason": reason,
            }
            cohorts.append(
                CandidateDateCohort(
                    signal_date=signal_date,
                    baseline_asset_codes=baseline_codes,
                    candidate_asset_codes=candidate_codes,
                    complete=reason is None,
                    exclusion_reason=reason,
                    cohort_hash=stable_contract_hash(payload),
                )
            )
        cohort_tuple = tuple(cohorts)
        exclusion_map = dict(sorted(exclusions.items()))
        complete_count = sum(item.complete for item in cohort_tuple)
        total = len(ordered_inputs)
        panel_hash = _candidate_panel_hash(
            candidate_id=candidate_id,
            baseline_samples=baseline_tuple,
            candidate_samples=candidate_tuple,
            cohorts=cohort_tuple,
            exclusions=exclusion_map,
        )
        panels[candidate_id] = CandidateCommonSupportPanel(
            candidate_id=candidate_id,
            baseline_samples=baseline_tuple,
            candidate_samples=candidate_tuple,
            cohorts=cohort_tuple,
            exclusions=exclusion_map,
            coverage={
                "input_count": total,
                "baseline_complete_count": len(baseline_tuple),
                "candidate_available_count": available_count,
                "candidate_qualified_count": qualified_count,
                "complete_primary_date_count": complete_count,
                "candidate_support_ratio": (
                    len(candidate_tuple) / len(baseline_tuple)
                    if baseline_tuple
                    else 0.0
                ),
            },
            panel_hash=panel_hash,
        )
    return panels
