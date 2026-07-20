from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

from app.services.etf_research_evidence import stable_contract_hash

PRIMARY_ENDPOINT = "paired_top10_5_session_net_excess_common_support"
MAX_CANDIDATES = 3
REQUIRED_HORIZONS = (1, 3, 5, 10)
REQUIRED_TOP_NS = (5, 10, 20)
REQUIRED_EMBARGO_SESSIONS = 10


class FactorExperimentContractError(ValueError):
    pass


@dataclass(frozen=True)
class FactorCandidate:
    candidate_id: str
    formula: str
    direction: Literal["higher_is_better", "lower_is_better"]
    transform: str
    missing_value_rule: str
    peer_bucket: str
    minimum_peer_count: int
    required_history_sessions: int


@dataclass(frozen=True)
class ChronologicalSplit:
    development_start: date
    development_end: date
    validation_start: date
    validation_end: date
    holdout_start: date
    holdout_end: date
    purge_horizon_sessions: int = 10
    embargo_sessions: int = REQUIRED_EMBARGO_SESSIONS


@dataclass(frozen=True)
class ExecutionCostPolicy:
    entry_rule: str
    exit_rule: str
    fee_bps_per_side: float
    slippage_bps_per_side: float
    turnover_charge_rule: str


@dataclass(frozen=True)
class PromotionGates:
    adjusted_primary_lower_bound_min: float
    minimum_common_support_coverage: float
    minimum_stable_fold_ratio: float
    maximum_turnover_deterioration: float
    maximum_drawdown_deterioration: float
    maximum_concentration: float
    maximum_exclusion_rate: float


@dataclass(frozen=True)
class FactorExperimentManifest:
    experiment_name: str
    code_version: str
    baseline_contract_id: str
    baseline_contract_hash: str
    baseline_score_field: str
    research_surface_contract_hash: str
    actionable_surface_contract_hash: str
    candidates: tuple[FactorCandidate, ...]
    universe_policy: str
    adjusted_data_policy: str
    source_cutoff_policy: str
    peer_buckets: tuple[str, ...]
    horizons: tuple[int, ...]
    top_ns: tuple[int, ...]
    execution_cost: ExecutionCostPolicy
    split: ChronologicalSplit
    exclusion_rules: tuple[str, ...]
    uncertainty_method: str
    bootstrap_block_sessions: int
    multiplicity_method: str
    declared_regimes: tuple[str, ...]
    promotion_gates: PromotionGates
    primary_endpoint: str = PRIMARY_ENDPOINT

    def validate(self) -> None:
        required_text = (
            self.experiment_name,
            self.code_version,
            self.baseline_contract_id,
            self.baseline_contract_hash,
            self.baseline_score_field,
            self.research_surface_contract_hash,
            self.actionable_surface_contract_hash,
            self.universe_policy,
            self.adjusted_data_policy,
            self.source_cutoff_policy,
            self.uncertainty_method,
            self.multiplicity_method,
        )
        if any(not value.strip() for value in required_text):
            raise FactorExperimentContractError("pre-registration fields must be complete")
        if not 1 <= len(self.candidates) <= MAX_CANDIDATES:
            raise FactorExperimentContractError("factor experiment requires one to three candidates")
        if len({candidate.candidate_id for candidate in self.candidates}) != len(
            self.candidates
        ):
            raise FactorExperimentContractError("candidate identities must be unique")
        for candidate in self.candidates:
            if (
                not candidate.candidate_id
                or not candidate.formula
                or not candidate.transform
                or not candidate.missing_value_rule
                or not candidate.peer_bucket
                or candidate.minimum_peer_count < 2
                or candidate.required_history_sessions < 61
            ):
                raise FactorExperimentContractError("candidate pre-registration is incomplete")
        if tuple(sorted(set(self.horizons))) != REQUIRED_HORIZONS:
            raise FactorExperimentContractError("horizons must be frozen to 1/3/5/10")
        if tuple(sorted(set(self.top_ns))) != REQUIRED_TOP_NS:
            raise FactorExperimentContractError("Top N must be frozen to 5/10/20")
        if self.primary_endpoint != PRIMARY_ENDPOINT:
            raise FactorExperimentContractError("primary endpoint is immutable")
        if (
            self.execution_cost.fee_bps_per_side <= 0
            or self.execution_cost.slippage_bps_per_side <= 0
        ):
            raise FactorExperimentContractError("fee and slippage must both be non-zero")
        if self.split.embargo_sessions != REQUIRED_EMBARGO_SESSIONS:
            raise FactorExperimentContractError("embargo must be 10 sessions")
        if not (
            self.split.development_start
            <= self.split.development_end
            < self.split.validation_start
            <= self.split.validation_end
            < self.split.holdout_start
            <= self.split.holdout_end
        ):
            raise FactorExperimentContractError("splits must be chronological and disjoint")
        if not self.exclusion_rules or not self.peer_buckets or not self.declared_regimes:
            raise FactorExperimentContractError("exclusions, peer buckets, and regimes are required")
        if self.bootstrap_block_sessions < max(self.horizons):
            raise FactorExperimentContractError("bootstrap block must cover overlapping labels")

    def canonical_payload(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return stable_contract_hash(self.canonical_payload())


@dataclass(frozen=True)
class RegisteredFactorExperiment:
    manifest: FactorExperimentManifest
    manifest_hash: str
    registered_at: datetime
    outcome_read_count: int = 0
    frozen: bool = True


@dataclass(frozen=True)
class HoldoutConsumption:
    manifest_hash: str
    frozen_non_holdout_evidence_hash: str
    consumed_at: datetime | None = None
    holdout_evidence_hash: str | None = None


def register_factor_experiment(
    manifest: FactorExperimentManifest,
    *,
    registered_at: datetime,
) -> RegisteredFactorExperiment:
    manifest.validate()
    return RegisteredFactorExperiment(
        manifest=manifest,
        manifest_hash=manifest.manifest_hash,
        registered_at=registered_at,
    )


def consume_holdout_once(
    record: HoldoutConsumption,
    *,
    manifest_hash: str,
    frozen_non_holdout_evidence_hash: str,
    holdout_evidence_hash: str,
    consumed_at: datetime,
) -> HoldoutConsumption:
    if record.consumed_at is not None:
        raise FactorExperimentContractError("holdout already consumed")
    if record.manifest_hash != manifest_hash:
        raise FactorExperimentContractError("holdout manifest identity mismatch")
    if record.frozen_non_holdout_evidence_hash != frozen_non_holdout_evidence_hash:
        raise FactorExperimentContractError("non-holdout evidence changed after freeze")
    if not holdout_evidence_hash:
        raise FactorExperimentContractError("holdout evidence hash is required")
    return replace(
        record,
        consumed_at=consumed_at,
        holdout_evidence_hash=holdout_evidence_hash,
    )
