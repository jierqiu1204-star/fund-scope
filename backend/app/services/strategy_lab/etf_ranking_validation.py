"""Pure research validation for point-in-time ETF ranking evidence."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from statistics import fmean
from typing import Literal

from app.services.etf_research_evidence import RankingSourceKind
from app.services.tracked_positions.lifecycle import stable_contract_hash

from .etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
    FrozenRankingCandidateRegistry,
    freeze_ranking_candidate_registry,
)
from .etf_ranking_forward_outcomes import (
    CONTINUOUS_RANKING_EXECUTION_MODEL,
    RANKING_PORTFOLIO_BASE_COST_POLICY,
    RankingPortfolioLedger,
)

PRODUCTION_SCORE_VERSION = "final_score_v3"
PRODUCTION_SCORE_FIELD = "ranking_score"
RESEARCH_SCORE_VERSION = "daily_reconstructable_v1"
RESEARCH_SCORE_FIELD = "research_score"
PRODUCTION_RULE_VERSION = "final_score_v3_rule_v2"
RESEARCH_RULE_VERSION = "daily_reconstructable_v1_rule_v1"
CURRENT_PRODUCTION_RULE_VERSION = "dual_ranking_surfaces_v1"
PRODUCTION_SOURCE_SCORE_CONTRACTS = {
    PRODUCTION_SCORE_VERSION: (PRODUCTION_SCORE_FIELD, PRODUCTION_RULE_VERSION),
    RESEARCH_SCORE_VERSION: (RESEARCH_SCORE_FIELD, CURRENT_PRODUCTION_RULE_VERSION),
}
TOTAL_RETURN_ADJUSTED = "total_return_adjusted"


class RankingValidationContractError(ValueError):
    """Raised when immutable ranking-validation evidence is incompatible."""


def _require_hash(name: str, value: str | None) -> str:
    if value is None or len(value) != 64:
        raise RankingValidationContractError(f"{name} is required")
    try:
        int(value, 16)
    except ValueError as exc:
        raise RankingValidationContractError(f"{name} is invalid") from exc
    return value


@dataclass(frozen=True)
class RankingValidationSourceEvent:
    """One immutable daily source event for exactly one ranking source kind."""

    ranking_source_kind: RankingSourceKind
    signal_date: date
    source_signal_run_id: int | None
    source_replay_run_key: str | None
    source_replay_contract_hash: str | None
    source_event_hash: str
    ranking_contract_hash: str
    scope_hash: str
    universe_snapshot_hash: str
    input_snapshot_hash: str
    score_version: str
    score_field: str
    rule_version: str
    price_basis: str
    publication_state: str | None
    scope_kind: str
    availability_cutoff: datetime
    immutable_hash: str
    source_status: str | None = None
    idempotency_key: str | None = None
    expected_asset_count: int | None = None
    decision_data_covered_count: int | None = None
    eligible_asset_count: int | None = None
    item_count: int | None = None
    decision_data_coverage_ratio: float | None = None
    score_coverage_ratio: float | None = None
    etf_item_count: int | None = None
    finite_eligible_score_count: int | None = None
    contiguous_global_rank: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ranking_source_kind, RankingSourceKind):
            raise RankingValidationContractError(
                "ranking source kind must be registered"
            )
        if not isinstance(self.availability_cutoff, datetime):
            raise RankingValidationContractError(
                "source availability cutoff is required"
            )
        for name, value in (
            ("source event hash", self.source_event_hash),
            ("ranking contract hash", self.ranking_contract_hash),
            ("scope hash", self.scope_hash),
            ("universe snapshot hash", self.universe_snapshot_hash),
            ("input snapshot hash", self.input_snapshot_hash),
        ):
            _require_hash(name, value)
        if self.price_basis != TOTAL_RETURN_ADJUSTED:
            raise RankingValidationContractError(
                "ranking validation requires total-return-adjusted prices"
            )
        if self.ranking_source_kind is RankingSourceKind.RESEARCH_REPLAY:
            if not self.source_replay_run_key or not self.source_replay_run_key.strip():
                raise RankingValidationContractError(
                    "research source replay run key is required"
                )
            _require_hash(
                "research source replay contract hash",
                self.source_replay_contract_hash,
            )
            if self.source_signal_run_id is not None:
                raise RankingValidationContractError(
                    "research source cannot declare a production source run id"
                )
            if self.score_version != RESEARCH_SCORE_VERSION:
                raise RankingValidationContractError(
                    "research source requires daily_reconstructable_v1"
                )
            if self.score_field != RESEARCH_SCORE_FIELD:
                raise RankingValidationContractError(
                    "research source requires research_score"
                )
            if self.rule_version != RESEARCH_RULE_VERSION:
                raise RankingValidationContractError(
                    "research source requires the registered rule version"
                )
            if self.publication_state is not None or self.scope_kind != "research_replay":
                raise RankingValidationContractError(
                    "research replay cannot be represented as a production publication"
                )
            if any(
                value is not None
                for value in (
                    self.source_status,
                    self.idempotency_key,
                    self.expected_asset_count,
                    self.decision_data_covered_count,
                    self.eligible_asset_count,
                    self.item_count,
                    self.decision_data_coverage_ratio,
                    self.score_coverage_ratio,
                    self.etf_item_count,
                    self.finite_eligible_score_count,
                    self.contiguous_global_rank,
                )
            ):
                raise RankingValidationContractError(
                    "research source cannot carry production publication metadata"
                )
        else:
            if self.source_signal_run_id is None or self.source_signal_run_id <= 0:
                raise RankingValidationContractError(
                    "production source run id is required"
                )
            if self.source_replay_run_key is not None:
                raise RankingValidationContractError(
                    "production source cannot declare a replay run key"
                )
            if self.source_replay_contract_hash is not None:
                raise RankingValidationContractError(
                    "production source cannot declare a replay contract hash"
                )
            if self.publication_state != "published":
                raise RankingValidationContractError(
                    "production source must be published"
                )
            if self.scope_kind != "full":
                raise RankingValidationContractError(
                    "production source must be full scope"
                )
            expected_score_contract = PRODUCTION_SOURCE_SCORE_CONTRACTS.get(
                self.score_version
            )
            if expected_score_contract is None:
                raise RankingValidationContractError(
                    "production source requires final_score_v3 or daily_reconstructable_v1"
                )
            expected_score_field, expected_rule_version = expected_score_contract
            if self.score_field != expected_score_field:
                raise RankingValidationContractError(
                    f"production source requires {expected_score_field}"
                )
            if self.rule_version != expected_rule_version:
                raise RankingValidationContractError(
                    "production source requires the registered rule version"
                )
            if self.source_status != "success":
                raise RankingValidationContractError(
                    "production source must be a successful run"
                )
            if not self.idempotency_key or not self.idempotency_key.strip():
                raise RankingValidationContractError(
                    "production source idempotency key is required"
                )
            counts = (
                self.expected_asset_count,
                self.decision_data_covered_count,
                self.eligible_asset_count,
                self.item_count,
                self.etf_item_count,
                self.finite_eligible_score_count,
            )
            if any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in counts
            ):
                raise RankingValidationContractError(
                    "production source publication counts are required"
                )
            expected = int(self.expected_asset_count or 0)
            decision_covered = int(self.decision_data_covered_count or 0)
            eligible = int(self.eligible_asset_count or 0)
            item_count = int(self.item_count or 0)
            etf_item_count = int(self.etf_item_count or 0)
            finite_count = int(self.finite_eligible_score_count or 0)
            if expected <= 0:
                raise RankingValidationContractError(
                    "production source expected asset count must be positive"
                )
            decision_ratio = self.decision_data_coverage_ratio
            score_ratio = self.score_coverage_ratio
            if not (
                0 <= eligible <= decision_covered <= expected
                and isinstance(decision_ratio, int | float)
                and not isinstance(decision_ratio, bool)
                and math.isfinite(float(decision_ratio))
                and math.isclose(
                    float(decision_ratio),
                    decision_covered / expected,
                    abs_tol=1e-12,
                )
                and float(decision_ratio) >= 0.95
            ):
                raise RankingValidationContractError(
                    "production source decision-data coverage is incompatible"
                )
            if not (
                isinstance(score_ratio, int | float)
                and not isinstance(score_ratio, bool)
                and math.isfinite(float(score_ratio))
                and math.isclose(
                    float(score_ratio),
                    eligible / expected,
                    abs_tol=1e-12,
                )
                and float(score_ratio) >= 0.95
            ):
                raise RankingValidationContractError(
                    "production source score coverage is incompatible"
                )
            if item_count != eligible:
                raise RankingValidationContractError(
                    "production source item count must equal eligible count"
                )
            if etf_item_count != item_count:
                raise RankingValidationContractError(
                    "production source must contain ETF-only items"
                )
            if finite_count != item_count:
                raise RankingValidationContractError(
                    "production source requires finite eligible scores"
                )
            if self.contiguous_global_rank is not True:
                raise RankingValidationContractError(
                    "production source requires contiguous global ranks"
                )
            if self.availability_cutoff.date() != self.signal_date:
                raise RankingValidationContractError(
                    "production source cutoff must match signal date"
                )


def _source_event_payload(event: RankingValidationSourceEvent) -> dict[str, object]:
    payload = asdict(event)
    payload.pop("immutable_hash")
    return payload


def freeze_ranking_validation_source_event(
    event: RankingValidationSourceEvent,
) -> RankingValidationSourceEvent:
    """Return an event with its canonical immutable hash populated."""

    return replace(
        event,
        immutable_hash=stable_contract_hash(_source_event_payload(event)),
    )


@dataclass(frozen=True)
class RankingValidationSourceCohort:
    ranking_source_kind: RankingSourceKind
    score_version: str
    score_field: str
    rule_version: str
    price_basis: str
    source_replay_run_key: str | None
    source_replay_contract_hash: str | None
    events: tuple[RankingValidationSourceEvent, ...]
    independent_dates: tuple[date, ...]
    cohort_hash: str


def _source_cohort_payload(
    cohort: RankingValidationSourceCohort,
) -> dict[str, object]:
    payload = asdict(cohort)
    payload.pop("cohort_hash")
    return payload


def _assert_source_cohort_integrity(
    cohort: RankingValidationSourceCohort,
) -> None:
    if cohort.cohort_hash != stable_contract_hash(_source_cohort_payload(cohort)):
        raise RankingValidationContractError("source cohort immutable hash is invalid")


def freeze_ranking_validation_source_cohort(
    *,
    ranking_source_kind: RankingSourceKind,
    events: Iterable[RankingValidationSourceEvent],
) -> RankingValidationSourceCohort:
    """Freeze one validation run's source without mixing source kinds."""

    if not isinstance(ranking_source_kind, RankingSourceKind):
        raise RankingValidationContractError("ranking source kind must be registered")
    values = tuple(events)
    if not values:
        raise RankingValidationContractError("validation source cohort cannot be empty")
    for event in values:
        if event.ranking_source_kind is not ranking_source_kind:
            raise RankingValidationContractError(
                "production and research validation sources cannot be merged"
            )
        if event.immutable_hash != stable_contract_hash(_source_event_payload(event)):
            raise RankingValidationContractError(
                "source event immutable hash is invalid"
            )
    ordered = tuple(sorted(values, key=lambda item: item.signal_date))
    dates = tuple(item.signal_date for item in ordered)
    if len(dates) != len(set(dates)):
        raise RankingValidationContractError(
            "validation source contains duplicate signal dates"
        )
    contracts = {
        (
            item.ranking_contract_hash,
            item.score_version,
            item.score_field,
            item.rule_version,
            item.price_basis,
        )
        for item in ordered
    }
    if len(contracts) != 1:
        raise RankingValidationContractError(
            "validation source contains mixed score contracts"
        )
    replay_keys = {item.source_replay_run_key for item in ordered}
    replay_contracts = {item.source_replay_contract_hash for item in ordered}
    if ranking_source_kind is RankingSourceKind.RESEARCH_REPLAY and (
        len(replay_keys) != 1 or len(replay_contracts) != 1
    ):
        raise RankingValidationContractError(
            "research validation source contains mixed replay identities"
        )
    draft = RankingValidationSourceCohort(
        ranking_source_kind=ranking_source_kind,
        score_version=ordered[0].score_version,
        score_field=ordered[0].score_field,
        rule_version=ordered[0].rule_version,
        price_basis=ordered[0].price_basis,
        source_replay_run_key=(
            ordered[0].source_replay_run_key
            if ranking_source_kind is RankingSourceKind.RESEARCH_REPLAY
            else None
        ),
        source_replay_contract_hash=(
            ordered[0].source_replay_contract_hash
            if ranking_source_kind is RankingSourceKind.RESEARCH_REPLAY
            else None
        ),
        events=ordered,
        independent_dates=dates,
        cohort_hash="pending",
    )
    return RankingValidationSourceCohort(
        **{
            **draft.__dict__,
            "cohort_hash": stable_contract_hash(_source_cohort_payload(draft)),
        }
    )


@dataclass(frozen=True)
class RankingScoreObservation:
    source_event_hash: str
    asset_code: str
    declared_score: float | None
    declared_score_field: str | None
    score_eligible: bool
    legacy_total_score: float | None
    observation_hash: str

    def __post_init__(self) -> None:
        _require_hash("score observation source event hash", self.source_event_hash)
        if not self.asset_code.strip():
            raise RankingValidationContractError(
                "score observation asset code is required"
            )
        if not isinstance(self.score_eligible, bool):
            raise RankingValidationContractError(
                "score observation eligibility must be boolean"
            )


def _score_observation_payload(
    observation: RankingScoreObservation,
) -> dict[str, object]:
    payload = asdict(observation)
    payload.pop("observation_hash")
    return payload


@dataclass(frozen=True)
class RankingScoreExclusion:
    source_event_hash: str
    asset_code: str
    reason: str


@dataclass(frozen=True)
class ValidatedScoreBucketSource:
    ranking_source_kind: RankingSourceKind
    source_cohort_hash: str
    score_version: str
    score_field: str
    accepted: tuple[RankingScoreObservation, ...]
    exclusions: tuple[RankingScoreExclusion, ...]
    result_hash: str


def validate_score_bucket_observations(
    *,
    source_cohort: RankingValidationSourceCohort,
    observations: Iterable[RankingScoreObservation],
) -> ValidatedScoreBucketSource:
    """Validate only the declared score field; legacy total_score is never read."""

    _assert_source_cohort_integrity(source_cohort)
    event_dates = {
        event.source_event_hash: event.signal_date for event in source_cohort.events
    }
    seen: set[tuple[str, str]] = set()
    accepted: list[RankingScoreObservation] = []
    exclusions: list[RankingScoreExclusion] = []
    for observation in observations:
        if observation.observation_hash != stable_contract_hash(
            _score_observation_payload(observation)
        ):
            raise RankingValidationContractError(
                "score observation immutable hash is invalid"
            )
        if observation.source_event_hash not in event_dates:
            raise RankingValidationContractError(
                "score observation is outside the frozen source cohort"
            )
        key = (observation.source_event_hash, observation.asset_code)
        if key in seen:
            raise RankingValidationContractError(
                "duplicate score observation in source cohort"
            )
        seen.add(key)
        reason: str | None = None
        if observation.declared_score_field != source_cohort.score_field:
            reason = "incompatible_score_field"
        elif observation.declared_score is None:
            reason = "missing_declared_score_no_legacy_fallback"
        elif (
            isinstance(observation.declared_score, bool)
            or not math.isfinite(observation.declared_score)
        ):
            reason = "non_finite_declared_score"
        elif not observation.score_eligible:
            reason = "ineligible_declared_score"
        if reason is None:
            accepted.append(observation)
        else:
            exclusions.append(
                RankingScoreExclusion(
                    source_event_hash=observation.source_event_hash,
                    asset_code=observation.asset_code,
                    reason=reason,
                )
            )
    accepted.sort(
        key=lambda item: (
            event_dates[item.source_event_hash],
            -float(item.declared_score or 0.0),
            item.asset_code,
        )
    )
    exclusions.sort(
        key=lambda item: (
            event_dates[item.source_event_hash],
            item.asset_code,
            item.reason,
        )
    )
    payload = {
        "schema_version": "etf_ranking_score_bucket_source_v1",
        "ranking_source_kind": source_cohort.ranking_source_kind,
        "source_cohort_hash": source_cohort.cohort_hash,
        "score_version": source_cohort.score_version,
        "score_field": source_cohort.score_field,
        "accepted_observation_hashes": tuple(
            item.observation_hash for item in accepted
        ),
        "exclusions": tuple(asdict(item) for item in exclusions),
    }
    return ValidatedScoreBucketSource(
        ranking_source_kind=source_cohort.ranking_source_kind,
        source_cohort_hash=source_cohort.cohort_hash,
        score_version=source_cohort.score_version,
        score_field=source_cohort.score_field,
        accepted=tuple(accepted),
        exclusions=tuple(exclusions),
        result_hash=stable_contract_hash(payload),
    )


FROZEN_VALIDATION_TOP_N = (5, 10, 20)
FROZEN_VALIDATION_HORIZONS = (1, 3, 5, 10)
PRIMARY_TOP_N = 10
PRIMARY_HORIZON_SESSIONS = 5
PRIMARY_ENDPOINT_NAME = "top10_five_session_paired_net_excess"
CONTINUOUS_FIVE_SESSION_ENDPOINT_CONTRACT_HASH = stable_contract_hash(
    {
        "contract_id": "ranking_continuous_top10_five_session_paired_endpoint_v1",
        "execution_model": CONTINUOUS_RANKING_EXECUTION_MODEL,
        "window": "t_plus_1_pre_rebalance_to_t_plus_6_pre_rebalance",
        "included_trade_cost_sessions": "t_plus_1_through_t_plus_5",
        "end_boundary_rebalance": "excluded",
        "artificial_liquidation": False,
        "gross_account": "same_targets_zero_cost_companion",
        "cost_contract_hash": RANKING_PORTFOLIO_BASE_COST_POLICY.contract_hash,
    }
)


@dataclass(frozen=True)
class RankingEndpointContract:
    source_cohort_hash: str
    candidate_registry_hash: str
    candidate_manifest_hashes: tuple[tuple[str, str], ...]
    bootstrap_seed: int
    bootstrap_resamples: int
    bootstrap_block_length: int
    minimum_independent_dates: int
    minimum_coverage_ratio: float
    maximum_drawdown_noninferiority_tolerance: float
    contract_hash: str

    def __post_init__(self) -> None:
        _require_hash("endpoint source cohort hash", self.source_cohort_hash)
        _require_hash(
            "endpoint candidate registry hash",
            self.candidate_registry_hash,
        )
        if not self.candidate_manifest_hashes or len(
            self.candidate_manifest_hashes
        ) > 3:
            raise RankingValidationContractError(
                "endpoint contract requires one to three frozen candidates"
            )
        if self.bootstrap_resamples < 100:
            raise RankingValidationContractError(
                "bootstrap requires at least 100 resamples"
            )
        if self.bootstrap_block_length <= 0:
            raise RankingValidationContractError(
                "bootstrap block length must be positive"
            )
        if self.minimum_independent_dates <= 0:
            raise RankingValidationContractError(
                "minimum independent dates must be positive"
            )
        if not (
            math.isfinite(self.minimum_coverage_ratio)
            and 0.0 < self.minimum_coverage_ratio <= 1.0
        ):
            raise RankingValidationContractError(
                "minimum coverage ratio must be in (0, 1]"
            )
        if not (
            math.isfinite(self.maximum_drawdown_noninferiority_tolerance)
            and self.maximum_drawdown_noninferiority_tolerance >= 0.0
        ):
            raise RankingValidationContractError(
                "maximum drawdown tolerance must be non-negative"
            )


def _endpoint_contract_payload(
    contract: RankingEndpointContract,
) -> dict[str, object]:
    payload = asdict(contract)
    payload.pop("contract_hash")
    return payload


def _assert_endpoint_contract_integrity(
    contract: RankingEndpointContract,
) -> None:
    if contract.contract_hash != stable_contract_hash(
        _endpoint_contract_payload(contract)
    ):
        raise RankingValidationContractError(
            "endpoint contract immutable hash is invalid"
        )


def freeze_ranking_endpoint_contract(
    *,
    source_cohort: RankingValidationSourceCohort,
    candidate_registry: FrozenRankingCandidateRegistry,
    bootstrap_seed: int,
    bootstrap_resamples: int,
    bootstrap_block_length: int,
    minimum_independent_dates: int,
    minimum_coverage_ratio: float,
    maximum_drawdown_noninferiority_tolerance: float,
) -> RankingEndpointContract:
    """Freeze endpoint and uncertainty rules before reading any returns."""

    _assert_source_cohort_integrity(source_cohort)
    canonical_registry = freeze_ranking_candidate_registry(
        candidate_registry.candidates
    )
    if canonical_registry != candidate_registry:
        raise RankingValidationContractError(
            "candidate registry is not a frozen canonical registry"
        )
    draft = RankingEndpointContract(
        source_cohort_hash=source_cohort.cohort_hash,
        candidate_registry_hash=candidate_registry.registry_hash,
        candidate_manifest_hashes=tuple(
            (candidate.candidate_id, candidate.manifest_hash)
            for candidate in candidate_registry.candidates
        ),
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
        bootstrap_block_length=bootstrap_block_length,
        minimum_independent_dates=minimum_independent_dates,
        minimum_coverage_ratio=minimum_coverage_ratio,
        maximum_drawdown_noninferiority_tolerance=(
            maximum_drawdown_noninferiority_tolerance
        ),
        contract_hash="pending",
    )
    return replace(
        draft,
        contract_hash=stable_contract_hash(_endpoint_contract_payload(draft)),
    )


@dataclass(frozen=True)
class RankingPairedReturnSample:
    source_event_hash: str
    candidate_id: str
    candidate_manifest_hash: str
    signal_date: date
    entry_session: date | None
    exit_session: date | None
    top_n: int
    horizon_sessions: int
    status: Literal["completed", "excluded", "pending"]
    selected_ranked_asset_codes: tuple[str, ...]
    candidate_gross_return: float | None
    candidate_net_return: float | None
    baseline_gross_return: float | None
    baseline_net_return: float | None
    fee_bps_per_side: int
    slippage_bps_per_side: int
    round_trip_cost_bps: int
    cost_contract_hash: str
    exclusion_reason: str | None
    sample_hash: str
    execution_model: str | None = None
    endpoint_contract_hash: str | None = None
    continuous_cost_contract_hash: str | None = None
    candidate_actual_cost: float | None = None
    baseline_actual_cost: float | None = None
    window_cost_sessions: tuple[date, ...] = ()
    candidate_ledger_hash: str | None = None
    baseline_ledger_hash: str | None = None

    def __post_init__(self) -> None:
        _require_hash("paired sample source event hash", self.source_event_hash)
        _require_hash(
            "paired sample candidate manifest hash",
            self.candidate_manifest_hash,
        )
        if not self.candidate_id.strip():
            raise RankingValidationContractError(
                "paired sample candidate id is required"
            )
        if self.top_n not in FROZEN_VALIDATION_TOP_N:
            raise RankingValidationContractError(
                "paired sample must use a frozen Top-N cell"
            )
        if self.horizon_sessions not in FROZEN_VALIDATION_HORIZONS:
            raise RankingValidationContractError(
                "paired sample must use a frozen horizon"
            )
        if self.status not in {"completed", "excluded", "pending"}:
            raise RankingValidationContractError(
                "paired sample status is invalid"
            )
        if (
            self.fee_bps_per_side != RANKING_FEE_BPS_PER_SIDE
            or self.slippage_bps_per_side != RANKING_SLIPPAGE_BPS_PER_SIDE
            or self.round_trip_cost_bps
            != 2
            * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE)
            or self.cost_contract_hash != RANKING_COST_CONTRACT_HASH
        ):
            raise RankingValidationContractError(
                "paired sample cost contract is incompatible"
            )
        if (
            len(self.selected_ranked_asset_codes) > self.top_n
            or len(self.selected_ranked_asset_codes)
            != len(set(self.selected_ranked_asset_codes))
        ):
            raise RankingValidationContractError(
                "paired sample selected ranking is invalid"
            )
        returns = (
            self.candidate_gross_return,
            self.candidate_net_return,
            self.baseline_gross_return,
            self.baseline_net_return,
        )
        if self.status == "completed":
            if self.entry_session is None or self.exit_session is None:
                raise RankingValidationContractError(
                    "completed paired sample requires entry and exit sessions"
                )
            if self.exclusion_reason is not None:
                raise RankingValidationContractError(
                    "completed paired sample cannot have an exclusion reason"
                )
            if any(
                value is None
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= -1.0
                for value in returns
            ):
                raise RankingValidationContractError(
                    "completed paired sample returns must be finite"
                )
        else:
            if any(value is not None for value in returns):
                raise RankingValidationContractError(
                    "incomplete paired sample cannot contain returns"
                )
            if not self.exclusion_reason:
                raise RankingValidationContractError(
                    "incomplete paired sample requires a stable reason"
                )
        continuous_fields = (
            self.endpoint_contract_hash,
            self.continuous_cost_contract_hash,
            self.candidate_actual_cost,
            self.baseline_actual_cost,
            self.candidate_ledger_hash,
            self.baseline_ledger_hash,
        )
        if self.execution_model == CONTINUOUS_RANKING_EXECUTION_MODEL:
            if (
                self.endpoint_contract_hash
                != CONTINUOUS_FIVE_SESSION_ENDPOINT_CONTRACT_HASH
                or self.continuous_cost_contract_hash
                != RANKING_PORTFOLIO_BASE_COST_POLICY.contract_hash
                or self.top_n != PRIMARY_TOP_N
                or self.horizon_sessions != PRIMARY_HORIZON_SESSIONS
            ):
                raise RankingValidationContractError(
                    "continuous paired sample contract is incompatible"
                )
            for value in (self.candidate_ledger_hash, self.baseline_ledger_hash):
                _require_hash("continuous paired sample ledger hash", value)
            if self.status == "completed":
                if len(self.window_cost_sessions) != 5:
                    raise RankingValidationContractError(
                        "continuous paired sample requires five cost sessions"
                    )
                for value in (
                    self.candidate_actual_cost,
                    self.baseline_actual_cost,
                ):
                    if (
                        value is None
                        or isinstance(value, bool)
                        or not math.isfinite(value)
                        or value < 0.0
                    ):
                        raise RankingValidationContractError(
                            "continuous paired sample actual costs are invalid"
                        )
            elif (
                self.candidate_actual_cost is not None
                or self.baseline_actual_cost is not None
                or self.window_cost_sessions
            ):
                raise RankingValidationContractError(
                    "incomplete continuous paired sample cannot carry costs"
                )
        elif any(value is not None for value in continuous_fields) or self.window_cost_sessions:
            raise RankingValidationContractError(
                "legacy paired sample cannot carry continuous-account fields"
            )


def _paired_sample_payload(
    sample: RankingPairedReturnSample,
) -> dict[str, object]:
    payload = asdict(sample)
    payload.pop("sample_hash")
    if sample.execution_model is None:
        for field in (
            "execution_model",
            "endpoint_contract_hash",
            "continuous_cost_contract_hash",
            "candidate_actual_cost",
            "baseline_actual_cost",
            "window_cost_sessions",
            "candidate_ledger_hash",
            "baseline_ledger_hash",
        ):
            payload.pop(field)
    return payload


def _paired_sample_hash_is_valid(sample: RankingPairedReturnSample) -> bool:
    expected = {stable_contract_hash(_paired_sample_payload(sample))}
    if sample.execution_model is None:
        expanded = asdict(sample)
        expanded.pop("sample_hash")
        expected.add(stable_contract_hash(expanded))
    return sample.sample_hash in expected


@dataclass(frozen=True)
class RankingEndpointResult:
    ranking_source_kind: RankingSourceKind
    source_cohort_hash: str
    candidate_registry_hash: str
    candidate_id: str
    candidate_manifest_hash: str
    endpoint_contract_hash: str
    top_n: int
    horizon_sessions: int
    endpoint_role: Literal["primary", "exploratory"]
    endpoint_name: str
    coverage_numerator: int
    coverage_denominator: int
    coverage_ratio: float
    completed_outcome_count: int
    independent_dates: tuple[date, ...]
    overlapping_excluded_dates: tuple[date, ...]
    mean_candidate_gross_return: float
    mean_candidate_net_return: float
    mean_baseline_gross_return: float
    mean_baseline_net_return: float
    mean_paired_net_excess: float
    bootstrap_confidence_interval: tuple[float, float]
    bootstrap_seed: int
    bootstrap_resamples: int
    bootstrap_block_length: int
    bootstrap_input_hash: str
    average_turnover: float
    average_rank_churn: float
    mean_candidate_cost_drag: float
    mean_baseline_cost_drag: float
    fee_bps_per_side: int
    slippage_bps_per_side: int
    round_trip_cost_bps: int
    cost_contract_hash: str
    candidate_maximum_drawdown: float | None
    baseline_maximum_drawdown: float | None
    maximum_drawdown_gate_passed: bool
    sample_gate_passed: bool
    accepted_sample_hashes: tuple[str, ...]
    result_hash: str
    execution_model: str | None = None
    endpoint_contract_identity_hash: str | None = None
    mean_candidate_actual_cost: float | None = None
    mean_baseline_actual_cost: float | None = None

    @property
    def evidence_status(self) -> str:
        return "sufficient" if self.sample_gate_passed else "sample_insufficient"


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _moving_block_bootstrap(
    values: Sequence[float],
    *,
    seed: int,
    resamples: int,
    block_length: int,
) -> tuple[float, float]:
    generator = random.Random(seed)
    size = len(values)
    effective_block = min(block_length, size)
    estimates: list[float] = []
    for _ in range(resamples):
        sampled: list[float] = []
        while len(sampled) < size:
            start = generator.randrange(size)
            sampled.extend(
                values[(start + offset) % size]
                for offset in range(effective_block)
            )
        estimates.append(fmean(sampled[:size]))
    return _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def _maximum_drawdown(returns: Sequence[float]) -> float:
    wealth = 1.0
    peak = 1.0
    maximum = 0.0
    for value in returns:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        maximum = max(maximum, (peak - wealth) / peak)
    return maximum


def _turnover(
    previous: tuple[str, ...],
    current: tuple[str, ...],
) -> float:
    common = len(set(previous) & set(current))
    return 1.0 - common / max(len(previous), len(current))


def _rank_churn(
    previous: tuple[str, ...],
    current: tuple[str, ...],
) -> float:
    previous_rank = {code: index for index, code in enumerate(previous)}
    current_rank = {code: index for index, code in enumerate(current)}
    common = sorted(set(previous_rank) & set(current_rank))
    if not common:
        return 1.0
    denominator = max(max(len(previous), len(current)) - 1, 1)
    return fmean(
        abs(previous_rank[code] - current_rank[code]) / denominator
        for code in common
    )


def _endpoint_name(top_n: int, horizon_sessions: int) -> str:
    if top_n == PRIMARY_TOP_N and horizon_sessions == PRIMARY_HORIZON_SESSIONS:
        return PRIMARY_ENDPOINT_NAME
    horizon_names = {1: "one", 3: "three", 5: "five", 10: "ten"}
    return (
        f"top{top_n}_{horizon_names[horizon_sessions]}_session_"
        "paired_net_excess"
    )


def build_continuous_five_session_paired_sample(
    *,
    source_event_hash: str,
    candidate_id: str,
    candidate_manifest_hash: str,
    signal_date: date,
    selected_ranked_asset_codes: Iterable[str],
    candidate_ledger: RankingPortfolioLedger,
    baseline_ledger: RankingPortfolioLedger,
    trading_sessions: Sequence[date],
) -> RankingPairedReturnSample:
    """Extract the primary T+1-pre to T+6-pre sample from two ledgers."""

    calendar = tuple(trading_sessions)
    if calendar != tuple(sorted(set(calendar))):
        raise RankingValidationContractError(
            "trading sessions must be unique and chronological"
        )
    try:
        signal_index = calendar.index(signal_date)
    except ValueError as exc:
        raise RankingValidationContractError(
            "paired sample signal date is outside the trading calendar"
        ) from exc
    selected = tuple(selected_ranked_asset_codes)
    if len(selected) != len(set(selected)):
        raise RankingValidationContractError(
            "paired sample selected ranking contains duplicates"
        )
    common = {
        "source_event_hash": source_event_hash,
        "candidate_id": candidate_id,
        "candidate_manifest_hash": candidate_manifest_hash,
        "signal_date": signal_date,
        "top_n": PRIMARY_TOP_N,
        "horizon_sessions": PRIMARY_HORIZON_SESSIONS,
        "selected_ranked_asset_codes": selected,
        "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
        "slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        "round_trip_cost_bps": 2
        * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
        "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "execution_model": CONTINUOUS_RANKING_EXECUTION_MODEL,
        "endpoint_contract_hash": CONTINUOUS_FIVE_SESSION_ENDPOINT_CONTRACT_HASH,
        "continuous_cost_contract_hash": RANKING_PORTFOLIO_BASE_COST_POLICY.contract_hash,
        "candidate_ledger_hash": candidate_ledger.ledger_hash,
        "baseline_ledger_hash": baseline_ledger.ledger_hash,
        "sample_hash": "pending",
    }
    if signal_index + 6 >= len(calendar):
        draft = RankingPairedReturnSample(
            **common,
            entry_session=(
                calendar[signal_index + 1]
                if signal_index + 1 < len(calendar)
                else None
            ),
            exit_session=None,
            status="pending",
            candidate_gross_return=None,
            candidate_net_return=None,
            baseline_gross_return=None,
            baseline_net_return=None,
            candidate_actual_cost=None,
            baseline_actual_cost=None,
            exclusion_reason="future_window_pending",
        )
        return replace(
            draft,
            sample_hash=stable_contract_hash(_paired_sample_payload(draft)),
        )
    if any(
        ledger.execution_model != CONTINUOUS_RANKING_EXECUTION_MODEL
        or ledger.cost_scenario != "base"
        or ledger.cost_contract_hash != RANKING_PORTFOLIO_BASE_COST_POLICY.contract_hash
        or ledger.initial_capital <= 0.0
        for ledger in (candidate_ledger, baseline_ledger)
    ) or not math.isclose(
        candidate_ledger.initial_capital,
        baseline_ledger.initial_capital,
        rel_tol=0.0,
        abs_tol=1e-12,
    ) or candidate_ledger.market_data_hash != baseline_ledger.market_data_hash:
        raise RankingValidationContractError(
            "paired ledgers must use the same frozen base execution contract"
        )
    entry_session = calendar[signal_index + 1]
    exit_session = calendar[signal_index + 6]
    cost_sessions = calendar[signal_index + 1 : signal_index + 6]
    candidate_by_date = {item.session_date: item for item in candidate_ledger.points}
    baseline_by_date = {item.session_date: item for item in baseline_ledger.points}
    required_dates = (entry_session, *cost_sessions[1:], exit_session)
    if any(
        session not in candidate_by_date or session not in baseline_by_date
        for session in required_dates
    ):
        draft = RankingPairedReturnSample(
            **common,
            entry_session=entry_session,
            exit_session=exit_session,
            status="excluded",
            candidate_gross_return=None,
            candidate_net_return=None,
            baseline_gross_return=None,
            baseline_net_return=None,
            candidate_actual_cost=None,
            baseline_actual_cost=None,
            exclusion_reason="missing_continuous_account_valuation",
        )
        return replace(
            draft,
            sample_hash=stable_contract_hash(_paired_sample_payload(draft)),
        )
    candidate_start = candidate_by_date[entry_session]
    candidate_end = candidate_by_date[exit_session]
    baseline_start = baseline_by_date[entry_session]
    baseline_end = baseline_by_date[exit_session]
    draft = RankingPairedReturnSample(
        **common,
        entry_session=entry_session,
        exit_session=exit_session,
        status="completed",
        candidate_gross_return=(
            candidate_end.pre_rebalance_gross_value
            / candidate_start.pre_rebalance_gross_value
            - 1.0
        ),
        candidate_net_return=(
            candidate_end.pre_rebalance_net_value
            / candidate_start.pre_rebalance_net_value
            - 1.0
        ),
        baseline_gross_return=(
            baseline_end.pre_rebalance_gross_value
            / baseline_start.pre_rebalance_gross_value
            - 1.0
        ),
        baseline_net_return=(
            baseline_end.pre_rebalance_net_value
            / baseline_start.pre_rebalance_net_value
            - 1.0
        ),
        candidate_actual_cost=sum(
            candidate_by_date[session].transaction_cost for session in cost_sessions
        ),
        baseline_actual_cost=sum(
            baseline_by_date[session].transaction_cost for session in cost_sessions
        ),
        window_cost_sessions=cost_sessions,
        exclusion_reason=None,
    )
    return replace(
        draft,
        sample_hash=stable_contract_hash(_paired_sample_payload(draft)),
    )


def evaluate_ranking_endpoint(
    *,
    source_cohort: RankingValidationSourceCohort,
    candidate_registry: FrozenRankingCandidateRegistry,
    contract: RankingEndpointContract,
    candidate_id: str,
    top_n: int,
    horizon_sessions: int,
    samples: Iterable[RankingPairedReturnSample],
    trading_sessions: Sequence[date],
    candidate_ledger: RankingPortfolioLedger | None = None,
    baseline_ledger: RankingPortfolioLedger | None = None,
) -> RankingEndpointResult:
    """Evaluate one predeclared candidate/cell without post-hoc relabeling."""

    if top_n not in FROZEN_VALIDATION_TOP_N:
        raise RankingValidationContractError(
            "endpoint must use the frozen Top-N cells"
        )
    if horizon_sessions not in FROZEN_VALIDATION_HORIZONS:
        raise RankingValidationContractError(
            "endpoint must use a frozen horizon"
        )
    _assert_source_cohort_integrity(source_cohort)
    _assert_endpoint_contract_integrity(contract)
    canonical_registry = freeze_ranking_candidate_registry(
        candidate_registry.candidates
    )
    if canonical_registry != candidate_registry:
        raise RankingValidationContractError(
            "candidate registry is not canonical"
        )
    if not (
        contract.source_cohort_hash == source_cohort.cohort_hash
        and contract.candidate_registry_hash == candidate_registry.registry_hash
        and contract.candidate_manifest_hashes
        == tuple(
            (candidate.candidate_id, candidate.manifest_hash)
            for candidate in candidate_registry.candidates
        )
    ):
        raise RankingValidationContractError(
            "endpoint contract does not match its source or candidate registry"
        )
    candidate = candidate_registry.by_id.get(candidate_id)
    if candidate is None:
        raise RankingValidationContractError(
            "endpoint candidate is outside the frozen registry"
        )
    calendar = tuple(trading_sessions)
    if calendar != tuple(sorted(set(calendar))):
        raise RankingValidationContractError(
            "trading sessions must be unique and chronological"
        )
    event_by_hash = {
        event.source_event_hash: event for event in source_cohort.events
    }
    values = tuple(samples)
    seen_dates: set[date] = set()
    completed: list[RankingPairedReturnSample] = []
    for sample in values:
        if not _paired_sample_hash_is_valid(sample):
            raise RankingValidationContractError(
                "paired sample immutable hash is invalid"
            )
        event = event_by_hash.get(sample.source_event_hash)
        if event is None:
            raise RankingValidationContractError(
                "paired sample is outside the frozen source cohort"
            )
        if event.signal_date != sample.signal_date:
            raise RankingValidationContractError(
                "paired sample signal date does not match its source event"
            )
        if sample.signal_date in seen_dates:
            raise RankingValidationContractError(
                "duplicate paired sample for endpoint date"
            )
        seen_dates.add(sample.signal_date)
        if not (
            sample.candidate_id == candidate.candidate_id
            and sample.candidate_manifest_hash == candidate.manifest_hash
            and sample.top_n == top_n
            and sample.horizon_sessions == horizon_sessions
        ):
            raise RankingValidationContractError(
                "paired sample does not match the endpoint cell"
            )
        if sample.status == "completed":
            try:
                signal_index = calendar.index(sample.signal_date)
            except ValueError as exc:
                raise RankingValidationContractError(
                    "paired sample signal date is outside the trading calendar"
                ) from exc
            exit_index = signal_index + 1 + horizon_sessions
            if exit_index >= len(calendar):
                raise RankingValidationContractError(
                    "paired sample future window is incomplete"
                )
            if not (
                sample.entry_session == calendar[signal_index + 1]
                and sample.exit_session == calendar[exit_index]
            ):
                raise RankingValidationContractError(
                    "paired sample does not use T+1/full-session execution"
                )
            if (
                sample.execution_model == CONTINUOUS_RANKING_EXECUTION_MODEL
                and sample.window_cost_sessions
                != calendar[signal_index + 1 : exit_index]
            ):
                raise RankingValidationContractError(
                    "continuous paired sample cost window is invalid"
                )
            completed.append(sample)
    completed.sort(key=lambda item: item.signal_date)
    if not completed:
        raise RankingValidationContractError(
            "endpoint requires at least one completed paired outcome"
        )
    execution_identities = {
        (sample.execution_model, sample.endpoint_contract_hash)
        for sample in completed
    }
    if len(execution_identities) != 1:
        raise RankingValidationContractError(
            "paired endpoint cannot mix execution contract identities"
        )
    accepted: list[RankingPairedReturnSample] = []
    overlapping_dates: list[date] = []
    last_exit: date | None = None
    for sample in completed:
        assert sample.exit_session is not None
        if last_exit is not None and sample.signal_date <= last_exit:
            overlapping_dates.append(sample.signal_date)
            continue
        accepted.append(sample)
        last_exit = sample.exit_session
    candidate_gross = [float(item.candidate_gross_return) for item in accepted]
    candidate_net = [float(item.candidate_net_return) for item in accepted]
    baseline_gross = [float(item.baseline_gross_return) for item in accepted]
    baseline_net = [float(item.baseline_net_return) for item in accepted]
    excess = [
        candidate_value - baseline_value
        for candidate_value, baseline_value in zip(
            candidate_net,
            baseline_net,
            strict=True,
        )
    ]
    transitions = tuple(zip(accepted, accepted[1:], strict=False))
    turnover = [
        _turnover(
            previous.selected_ranked_asset_codes,
            current.selected_ranked_asset_codes,
        )
        for previous, current in transitions
    ]
    rank_churn = [
        _rank_churn(
            previous.selected_ranked_asset_codes,
            current.selected_ranked_asset_codes,
        )
        for previous, current in transitions
    ]
    input_hash = stable_contract_hash(
        {
            "schema_version": "etf_ranking_endpoint_input_v1",
            "source_cohort_hash": source_cohort.cohort_hash,
            "candidate_registry_hash": candidate_registry.registry_hash,
            "endpoint_contract_hash": contract.contract_hash,
            "candidate_id": candidate.candidate_id,
            "candidate_manifest_hash": candidate.manifest_hash,
            "top_n": top_n,
            "horizon_sessions": horizon_sessions,
            "trading_sessions": calendar,
            "sample_hashes": tuple(item.sample_hash for item in sorted(values, key=lambda item: item.signal_date)),
            "accepted_sample_hashes": tuple(item.sample_hash for item in accepted),
            "overlapping_excluded_dates": tuple(overlapping_dates),
        }
    )
    interval = _moving_block_bootstrap(
        excess,
        seed=contract.bootstrap_seed,
        resamples=contract.bootstrap_resamples,
        block_length=contract.bootstrap_block_length,
    )
    candidate_drawdown = _maximum_drawdown(candidate_net)
    baseline_drawdown = _maximum_drawdown(baseline_net)
    if accepted[0].execution_model == CONTINUOUS_RANKING_EXECUTION_MODEL:
        candidate_drawdown = baseline_drawdown = None
        if candidate_ledger is not None and baseline_ledger is not None:
            if any(
                sample.candidate_ledger_hash != candidate_ledger.ledger_hash
                or sample.baseline_ledger_hash != baseline_ledger.ledger_hash
                for sample in accepted
            ):
                raise RankingValidationContractError("endpoint ledger identity mismatch")
            if (
                candidate_ledger.status == baseline_ledger.status == "completed"
                and candidate_ledger.market_data_hash == baseline_ledger.market_data_hash
                and tuple(point.session_date for point in candidate_ledger.points)
                == tuple(point.session_date for point in baseline_ledger.points)
            ):
                candidate_drawdown = candidate_ledger.net_maximum_drawdown
                baseline_drawdown = baseline_ledger.net_maximum_drawdown
    coverage_denominator = len(source_cohort.events)
    coverage_numerator = len(completed)
    coverage_ratio = coverage_numerator / coverage_denominator
    sample_gate_passed = (
        len(accepted) >= contract.minimum_independent_dates
        and coverage_ratio >= contract.minimum_coverage_ratio
    )
    draft = RankingEndpointResult(
        ranking_source_kind=source_cohort.ranking_source_kind,
        source_cohort_hash=source_cohort.cohort_hash,
        candidate_registry_hash=candidate_registry.registry_hash,
        candidate_id=candidate.candidate_id,
        candidate_manifest_hash=candidate.manifest_hash,
        endpoint_contract_hash=contract.contract_hash,
        top_n=top_n,
        horizon_sessions=horizon_sessions,
        endpoint_role=(
            "primary"
            if top_n == PRIMARY_TOP_N
            and horizon_sessions == PRIMARY_HORIZON_SESSIONS
            else "exploratory"
        ),
        endpoint_name=_endpoint_name(top_n, horizon_sessions),
        coverage_numerator=coverage_numerator,
        coverage_denominator=coverage_denominator,
        coverage_ratio=coverage_ratio,
        completed_outcome_count=len(completed),
        independent_dates=tuple(item.signal_date for item in accepted),
        overlapping_excluded_dates=tuple(overlapping_dates),
        mean_candidate_gross_return=fmean(candidate_gross),
        mean_candidate_net_return=fmean(candidate_net),
        mean_baseline_gross_return=fmean(baseline_gross),
        mean_baseline_net_return=fmean(baseline_net),
        mean_paired_net_excess=fmean(excess),
        bootstrap_confidence_interval=interval,
        bootstrap_seed=contract.bootstrap_seed,
        bootstrap_resamples=contract.bootstrap_resamples,
        bootstrap_block_length=contract.bootstrap_block_length,
        bootstrap_input_hash=input_hash,
        average_turnover=fmean(turnover) if turnover else 0.0,
        average_rank_churn=fmean(rank_churn) if rank_churn else 0.0,
        mean_candidate_cost_drag=fmean(
            gross - net
            for gross, net in zip(candidate_gross, candidate_net, strict=True)
        ),
        mean_baseline_cost_drag=fmean(
            gross - net
            for gross, net in zip(baseline_gross, baseline_net, strict=True)
        ),
        fee_bps_per_side=RANKING_FEE_BPS_PER_SIDE,
        slippage_bps_per_side=RANKING_SLIPPAGE_BPS_PER_SIDE,
        round_trip_cost_bps=2
        * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        candidate_maximum_drawdown=candidate_drawdown,
        baseline_maximum_drawdown=baseline_drawdown,
        maximum_drawdown_gate_passed=(
            candidate_drawdown is not None
            and baseline_drawdown is not None
            and candidate_drawdown
            <= baseline_drawdown
            + contract.maximum_drawdown_noninferiority_tolerance
        ),
        sample_gate_passed=sample_gate_passed,
        accepted_sample_hashes=tuple(item.sample_hash for item in accepted),
        result_hash="pending",
        execution_model=accepted[0].execution_model,
        endpoint_contract_identity_hash=accepted[0].endpoint_contract_hash,
        mean_candidate_actual_cost=(
            fmean(float(item.candidate_actual_cost) for item in accepted)
            if accepted[0].execution_model == CONTINUOUS_RANKING_EXECUTION_MODEL
            else None
        ),
        mean_baseline_actual_cost=(
            fmean(float(item.baseline_actual_cost) for item in accepted)
            if accepted[0].execution_model == CONTINUOUS_RANKING_EXECUTION_MODEL
            else None
        ),
    )
    result_payload = asdict(draft)
    result_payload.pop("result_hash")
    if draft.execution_model is None:
        for field in (
            "execution_model",
            "endpoint_contract_identity_hash",
            "mean_candidate_actual_cost",
            "mean_baseline_actual_cost",
        ):
            result_payload.pop(field)
    return replace(
        draft,
        result_hash=stable_contract_hash(result_payload),
    )
