from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfCatalystEventStudyEvidence
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_validation import (
    holm_bonferroni,
    moving_block_bootstrap_interval,
    moving_block_bootstrap_positive_p_value,
)

HORIZONS = (1, 3, 5, 10)
FORBIDDEN_OUTPUT_FIELDS = frozenset(
    {
        "score",
        "weight",
        "cap",
        "rank_delta",
        "allocation",
        "alert",
        "email_rule",
    }
)


class CatalystEventStudyContractError(ValueError):
    pass


class CatalystEventStudyEvidenceConflictError(ValueError):
    pass


@dataclass(frozen=True)
class CatalystEventStudyManifest:
    study_id: str
    code_version: str
    ranking_contract_hash: str
    event_types: tuple[str, ...]
    mapping_cohorts: tuple[Literal["direct", "proxy"], ...] = ("direct", "proxy")
    control_policy: str = "same_date_propensity_common_support_v1"
    execution_policy: str = "next_session_adjusted_close_t_plus_1"
    price_basis: str = "total_return_adjusted"
    round_trip_cost_bps: float = 10.0
    horizons: tuple[int, ...] = HORIZONS
    minimum_direct_samples: int = 30
    minimum_common_support: float = 0.8
    maximum_exclusion_rate: float = 0.2
    chronological_folds: int = 3
    minimum_positive_folds: int = 2
    minimum_regimes: int = 2
    purge_sessions: int = 10
    embargo_sessions: int = 5
    uncertainty_method: str = "moving_block_bootstrap"
    bootstrap_resamples: int = 1000
    bootstrap_block_sessions: int = 3
    confidence: float = 0.95
    multiplicity_policy: str = "holm_family_wise"
    primary_endpoint: str = "5_session_net_matched_excess"

    def validate(self) -> None:
        if not self.study_id or not self.code_version or not self.ranking_contract_hash:
            raise CatalystEventStudyContractError("study identity is incomplete")
        if not self.event_types:
            raise CatalystEventStudyContractError("at least one frozen event type is required")
        if len(self.event_types) > 3:
            raise CatalystEventStudyContractError(
                "at most three primary event comparisons are allowed"
            )
        if tuple(self.horizons) != HORIZONS:
            raise CatalystEventStudyContractError("horizons must be exactly 1/3/5/10 sessions")
        if self.mapping_cohorts not in {("direct",), ("proxy",), ("direct", "proxy")}:
            raise CatalystEventStudyContractError("direct and proxy cohorts must be explicit")
        if self.execution_policy != "next_session_adjusted_close_t_plus_1":
            raise CatalystEventStudyContractError("T+1 adjusted-close execution is required")
        if self.price_basis != "total_return_adjusted":
            raise CatalystEventStudyContractError("decision-eligible adjusted prices are required")
        if self.minimum_direct_samples < 30:
            raise CatalystEventStudyContractError("minimum direct cohort size cannot be below 30")
        if self.chronological_folds < 3 or self.minimum_regimes < 2:
            raise CatalystEventStudyContractError("three folds and two regimes are required")
        if self.minimum_positive_folds < 2:
            raise CatalystEventStudyContractError("at least two positive folds are required")
        if not 0.8 <= self.minimum_common_support <= 1:
            raise CatalystEventStudyContractError("common support threshold cannot be below 80%")
        if not 0 <= self.maximum_exclusion_rate <= 0.2:
            raise CatalystEventStudyContractError("exclusion rate cap cannot exceed 20%")
        if self.uncertainty_method != "moving_block_bootstrap":
            raise CatalystEventStudyContractError("moving-block bootstrap is required")
        if self.multiplicity_policy != "holm_family_wise":
            raise CatalystEventStudyContractError("Holm family-wise control is required")
        if self.primary_endpoint != "5_session_net_matched_excess":
            raise CatalystEventStudyContractError("the frozen primary endpoint changed")

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return stable_contract_hash(asdict(self))


@dataclass(frozen=True)
class CatalystEventStudySample:
    event_id: str
    event_version: int
    event_type: str
    mapping_kind: Literal["direct", "proxy"]
    verification_state: str
    snapshot_hash: str
    event_session: date
    decision_cutoff: datetime
    etf_code: str
    control_code: str
    fold_id: int
    regime: str
    price_basis: str
    decision_eligible: bool
    common_support: bool
    session_dates: tuple[date, ...]
    adjusted_closes: tuple[float, ...]
    adjusted_highs: tuple[float, ...]
    adjusted_lows: tuple[float, ...]
    control_adjusted_closes: tuple[float, ...]


@dataclass(frozen=True)
class CatalystEventStudyResult:
    manifest: CatalystEventStudyManifest
    cohorts: tuple[dict[str, Any], ...]
    outcomes: dict[str, Any]
    exclusions: tuple[dict[str, Any], ...]
    intervals: dict[str, Any]
    result_state: Literal["research_only", "insufficient_data"]
    limitations: tuple[str, ...]

    @property
    def evidence_hash(self) -> str:
        return stable_contract_hash(
            {
                "manifest_hash": self.manifest.manifest_hash,
                "cohorts": self.cohorts,
                "outcomes": self.outcomes,
                "exclusions": self.exclusions,
                "intervals": self.intervals,
                "result_state": self.result_state,
                "limitations": self.limitations,
                "production_isolation": production_isolation_contract(),
            }
        )


def production_isolation_contract() -> dict[str, Any]:
    return {
        "research_only": True,
        "ranking_weight": 0.0,
        "may_derive_score": False,
        "may_derive_weight": False,
        "may_derive_cap": False,
        "may_change_rank": False,
        "may_change_allocation": False,
        "may_change_alert": False,
        "may_change_email": False,
        "future_scoring_change_requires_separate_proposal": True,
    }


def _finite_prices(values: tuple[float, ...]) -> bool:
    return len(values) >= 11 and all(math.isfinite(value) and value > 0 for value in values)


def _sample_exclusion(
    sample: CatalystEventStudySample,
    manifest: CatalystEventStudyManifest,
    exchange_session_dates: tuple[date, ...],
) -> str | None:
    if sample.event_type not in manifest.event_types:
        return "event_type_not_registered"
    if sample.mapping_kind not in manifest.mapping_cohorts:
        return "mapping_cohort_not_registered"
    if sample.verification_state != "verified" or not sample.snapshot_hash:
        return "not_point_in_time_verified"
    if not sample.decision_eligible or sample.price_basis != manifest.price_basis:
        return "adjusted_price_not_decision_eligible"
    if not sample.common_support or not sample.control_code:
        return "no_common_support_control"
    expected_sessions = tuple(
        session_date
        for session_date in sorted(set(exchange_session_dates))
        if session_date > sample.event_session
    )
    required_length = max(manifest.horizons) + 1
    if len(expected_sessions) < required_length:
        return "exchange_session_calendar_incomplete"
    if (
        len(sample.session_dates) < required_length
        or sample.session_dates[:required_length]
        != expected_sessions[:required_length]
    ):
        return "non_consecutive_exchange_session_path"
    if not (
        _finite_prices(sample.adjusted_closes)
        and _finite_prices(sample.adjusted_highs)
        and _finite_prices(sample.adjusted_lows)
        and _finite_prices(sample.control_adjusted_closes)
        and len(sample.session_dates) == len(sample.adjusted_closes)
        and len(sample.session_dates) == len(sample.adjusted_highs)
        and len(sample.session_dates) == len(sample.adjusted_lows)
        and len(sample.session_dates) == len(sample.control_adjusted_closes)
    ):
        return "incomplete_adjusted_price_path"
    if sample.fold_id < 1 or not sample.regime:
        return "split_or_regime_missing"
    return None


def _outcome(
    sample: CatalystEventStudySample,
    *,
    horizon: int,
    cost_rate: float,
) -> dict[str, float]:
    entry = sample.adjusted_closes[0]
    observed = sample.adjusted_closes[horizon] / entry - 1.0 - cost_rate
    control = (
        sample.control_adjusted_closes[horizon]
        / sample.control_adjusted_closes[0]
        - 1.0
        - cost_rate
    )
    adverse = min(value / entry - 1.0 for value in sample.adjusted_lows[1 : horizon + 1])
    favorable = max(value / entry - 1.0 for value in sample.adjusted_highs[1 : horizon + 1])
    return {
        "observed_return": observed,
        "control_return": control,
        "matched_excess_return": observed - control,
        "adverse_excursion": adverse,
        "favorable_excursion": favorable,
    }


def run_catalyst_event_study(
    manifest: CatalystEventStudyManifest,
    samples: tuple[CatalystEventStudySample, ...],
    *,
    exchange_session_dates: tuple[date, ...],
) -> CatalystEventStudyResult:
    manifest.validate()
    exclusions: list[dict[str, Any]] = []
    eligible: list[CatalystEventStudySample] = []
    for sample in samples:
        reason = _sample_exclusion(sample, manifest, exchange_session_dates)
        if reason:
            exclusions.append(
                {
                    "event_id": sample.event_id,
                    "event_version": sample.event_version,
                    "event_type": sample.event_type,
                    "mapping_kind": sample.mapping_kind,
                    "etf_code": sample.etf_code,
                    "reason": reason,
                }
            )
        else:
            eligible.append(sample)

    grouped: dict[tuple[str, str], list[CatalystEventStudySample]] = {}
    for sample in eligible:
        grouped.setdefault((sample.mapping_kind, sample.event_type), []).append(sample)

    cohort_reports: list[dict[str, Any]] = []
    outcome_reports: dict[str, Any] = {}
    interval_reports: dict[str, Any] = {}
    limitations: set[str] = set()
    direct_sufficient = False
    total_count = len(samples)
    exclusion_rate = len(exclusions) / total_count if total_count else 1.0
    cost_rate = manifest.round_trip_cost_bps / 10_000.0
    primary_by_group = {
        key: [
            _outcome(sample, horizon=5, cost_rate=cost_rate)[
                "matched_excess_return"
            ]
            for sample in cohort
        ]
        for key, cohort in grouped.items()
    }
    direct_keys = tuple(
        key for key in sorted(primary_by_group) if key[0] == "direct"
    )
    raw_primary_p_values = tuple(
        moving_block_bootstrap_positive_p_value(
            primary_by_group[key],
            block_sessions=min(
                manifest.bootstrap_block_sessions,
                len(primary_by_group[key]),
            ),
            resamples=manifest.bootstrap_resamples,
        )
        for key in direct_keys
    )
    adjusted_primary_p_values = (
        holm_bonferroni(raw_primary_p_values)
        if raw_primary_p_values
        else ()
    )
    multiplicity_by_group = {
        key: (raw, adjusted)
        for key, raw, adjusted in zip(
            direct_keys,
            raw_primary_p_values,
            adjusted_primary_p_values,
            strict=True,
        )
    }

    for (mapping_kind, event_type), cohort in sorted(grouped.items()):
        cohort_id = f"{mapping_kind}:{event_type}"
        outcomes_by_horizon = {
            horizon: [_outcome(sample, horizon=horizon, cost_rate=cost_rate) for sample in cohort]
            for horizon in HORIZONS
        }
        primary = primary_by_group[(mapping_kind, event_type)]
        fold_means = {
            fold: sum(
                value
                for sample, value in zip(cohort, primary, strict=True)
                if sample.fold_id == fold
            )
            / sum(1 for sample in cohort if sample.fold_id == fold)
            for fold in sorted({sample.fold_id for sample in cohort})
        }
        regimes = sorted({sample.regime for sample in cohort})
        positive_folds = sum(value > 0 for value in fold_means.values())
        minimum = manifest.minimum_direct_samples if mapping_kind == "direct" else 1
        cohort_exclusions = sum(
            item["mapping_kind"] == mapping_kind
            and item["event_type"] == event_type
            for item in exclusions
        )
        common_support_coverage = len(cohort) / max(
            1,
            len(cohort) + cohort_exclusions,
        )
        sufficient = (
            len(cohort) >= minimum
            and common_support_coverage >= manifest.minimum_common_support
            and len(fold_means) >= manifest.chronological_folds
            and positive_folds >= manifest.minimum_positive_folds
            and len(regimes) >= manifest.minimum_regimes
            and exclusion_rate <= manifest.maximum_exclusion_rate
        )
        if mapping_kind == "direct" and sufficient:
            direct_sufficient = True
        if not sufficient:
            limitations.add(f"{cohort_id}:insufficient_or_unstable")
        interval = moving_block_bootstrap_interval(
            primary,
            block_sessions=min(manifest.bootstrap_block_sessions, len(primary)),
            resamples=manifest.bootstrap_resamples,
            confidence=manifest.confidence,
        )
        interval_report = asdict(interval)
        multiplicity = multiplicity_by_group.get((mapping_kind, event_type))
        if multiplicity is not None:
            interval_report.update(
                {
                    "multiplicity_method": "holm_bonferroni",
                    "raw_primary_p_value": multiplicity[0],
                    "holm_adjusted_primary_p_value": multiplicity[1],
                }
            )
        interval_reports[cohort_id] = interval_report
        outcome_reports[cohort_id] = {
            str(horizon): {
                metric: sum(item[metric] for item in values) / len(values)
                for metric in (
                    "observed_return",
                    "matched_excess_return",
                    "adverse_excursion",
                    "favorable_excursion",
                )
            }
            for horizon, values in outcomes_by_horizon.items()
        }
        cohort_reports.append(
            {
                "cohort_id": cohort_id,
                "mapping_kind": mapping_kind,
                "event_type": event_type,
                "sample_count": len(cohort),
                "fold_means": fold_means,
                "regimes": regimes,
                "positive_fold_count": positive_folds,
                "common_support_coverage": common_support_coverage,
                "exclusion_rate": exclusion_rate,
                "state": "research_only" if sufficient else "insufficient_data",
                "primary_lower_bound_positive": (
                    interval.lower > 0
                    and multiplicity is not None
                    and multiplicity[1] <= 1.0 - manifest.confidence
                ),
            }
        )

    if not grouped:
        limitations.add("no_eligible_event_cohort")
    if exclusion_rate > manifest.maximum_exclusion_rate:
        limitations.add("exclusion_rate_above_frozen_cap")
    result_state: Literal["research_only", "insufficient_data"] = (
        "research_only" if direct_sufficient else "insufficient_data"
    )
    result = CatalystEventStudyResult(
        manifest=manifest,
        cohorts=tuple(cohort_reports),
        outcomes={
            **outcome_reports,
            "primary_endpoint": manifest.primary_endpoint,
            "research_only": True,
            "production_isolation": production_isolation_contract(),
        },
        exclusions=tuple(exclusions),
        intervals=interval_reports,
        result_state=result_state,
        limitations=tuple(sorted(limitations)),
    )
    _assert_no_production_outputs(result)
    return result


def _assert_no_production_outputs(result: CatalystEventStudyResult) -> None:
    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {item for nested in value.values() for item in keys(nested)}
        if isinstance(value, (list, tuple)):
            return {item for nested in value for item in keys(nested)}
        return set()

    unexpected = keys(result.outcomes) & FORBIDDEN_OUTPUT_FIELDS
    if unexpected:
        raise CatalystEventStudyContractError(
            f"event study attempted production outputs: {sorted(unexpected)}"
        )


async def persist_catalyst_event_study(
    session: AsyncSession,
    result: CatalystEventStudyResult,
) -> EtfCatalystEventStudyEvidence:
    existing = await session.scalar(
        select(EtfCatalystEventStudyEvidence).where(
            EtfCatalystEventStudyEvidence.manifest_hash
            == result.manifest.manifest_hash
        )
    )
    if existing is not None:
        if existing.evidence_hash != result.evidence_hash:
            raise CatalystEventStudyEvidenceConflictError(
                "study identity already has different immutable evidence"
            )
        return existing
    evidence = EtfCatalystEventStudyEvidence(
        manifest_hash=result.manifest.manifest_hash,
        ranking_contract_hash=result.manifest.ranking_contract_hash,
        evidence_hash=result.evidence_hash,
        manifest_json=asdict(result.manifest),
        cohorts_json=list(result.cohorts),
        outcomes_json=result.outcomes,
        exclusions_json=list(result.exclusions),
        intervals_json=result.intervals,
        result_state=result.result_state,
        limitations_json=list(result.limitations),
    )
    session.add(evidence)
    await session.commit()
    await session.refresh(evidence)
    return evidence
