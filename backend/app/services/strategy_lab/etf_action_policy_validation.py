from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from statistics import fmean
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfActionValidationRun
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ArtifactConflictError,
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_action_replay.checkpoint import ReplayRunContract

PRIMARY_ENDPOINT = "top20_10_trading_day_tax_fee_adjusted_mean_action_cycle_benefit"
REGISTRY_SCHEMA_VERSION = "etf_action_candidate_registry_v1"
VALIDATION_CONTRACT_SCHEMA_VERSION = "etf_action_validation_contract_v1"


class CandidateRegistryError(ValueError):
    pass


class FrozenValidationError(ValueError):
    pass


class HoldoutConsumedError(ValueError):
    pass


class SampleGateError(ValueError):
    pass


class CandidateName(StrEnum):
    CURRENT_SEMANTICS_BASELINE = "current_semantics_baseline"
    IDEMPOTENT_ABSOLUTE_TARGETS = "idempotent_absolute_targets"
    IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING = (
        "idempotent_absolute_targets_with_monotonic_trailing"
    )


class ContractCompatibility(StrEnum):
    SAME_CONTRACT = "same_contract"
    VERSION_MISMATCH = "version_mismatch"
    LEGACY_RESULT = "legacy_result"
    UNAVAILABLE = "unavailable"


class DataEligibility(StrEnum):
    DECISION_ELIGIBLE_ADJUSTED = "decision_eligible_adjusted"
    INELIGIBLE = "ineligible"
    WAITING = "waiting"


class ExecutionProvenance(StrEnum):
    SIMULATED_EXECUTION = "simulated_execution"
    USER_CONFIRMED = "user_confirmed"
    LEGACY_UNVERIFIED = "legacy_unverified"
    UNKNOWN = "unknown"


class SampleSufficiency(StrEnum):
    SUFFICIENT = "sufficient"
    SAMPLE_INSUFFICIENT = "sample_insufficient"
    WAITING_VALIDATION = "waiting_validation"


class EvidencePromotionStatus(StrEnum):
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    RESEARCH_ONLY = "research_only"
    SAMPLE_INSUFFICIENT = "sample_insufficient"
    WAITING_VALIDATION = "waiting_validation"


@dataclass(frozen=True)
class CandidateDefinition:
    name: CandidateName | str
    policy_version: str
    parameters: Mapping[str, Any]
    promotion_eligible: bool

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise CandidateRegistryError("candidate policy_version is required")


@dataclass(frozen=True)
class Candidate3Parameters:
    atr_k: float
    atr_warmup_trading_days: int
    peak_source: Literal["adjusted_high", "adjusted_close"]
    trigger_field: Literal["adjusted_close", "adjusted_low"]
    recovery_hysteresis_atr: float
    same_day_ohlc_ordering: Literal[
        "close_only", "prior_stop_then_low_then_close"
    ]
    missing_data_behavior: Literal["fail_closed"]
    monotonic_stop_invariant: bool

    def __post_init__(self) -> None:
        if not _finite(self.atr_k) or self.atr_k <= 0:
            raise ValueError("atr_k must be finite and positive")
        if self.atr_warmup_trading_days < 20:
            raise ValueError("ATR warm-up must contain at least 20 trading days")
        if self.peak_source not in {"adjusted_high", "adjusted_close"}:
            raise ValueError("peak_source must be adjusted_high or adjusted_close")
        if self.trigger_field not in {"adjusted_close", "adjusted_low"}:
            raise ValueError("trigger_field must use an adjusted decision field")
        if (
            not _finite(self.recovery_hysteresis_atr)
            or self.recovery_hysteresis_atr < 0
        ):
            raise ValueError("recovery hysteresis must be finite and non-negative")
        if self.same_day_ohlc_ordering not in {
            "close_only",
            "prior_stop_then_low_then_close",
        }:
            raise ValueError("same-day OHLC ordering is not pre-registered")
        if (
            self.same_day_ohlc_ordering == "close_only"
            and self.trigger_field != "adjusted_close"
        ):
            raise ValueError("close_only ordering requires an adjusted_close trigger")
        if (
            self.same_day_ohlc_ordering == "prior_stop_then_low_then_close"
            and self.trigger_field != "adjusted_low"
        ):
            raise ValueError(
                "prior-stop ordering requires an adjusted_low trigger"
            )
        if self.missing_data_behavior != "fail_closed":
            raise ValueError("candidate 3 missing-data behavior must be fail_closed")
        if self.monotonic_stop_invariant is not True:
            raise ValueError("candidate 3 must enforce the monotonic-stop invariant")

    def as_dict(self) -> dict[str, Any]:
        return {
            "atr_k": self.atr_k,
            "atr_warmup_trading_days": self.atr_warmup_trading_days,
            "peak_source": self.peak_source,
            "trigger_field": self.trigger_field,
            "recovery_hysteresis_atr": self.recovery_hysteresis_atr,
            "same_day_ohlc_ordering": self.same_day_ohlc_ordering,
            "missing_data_behavior": self.missing_data_behavior,
            "monotonic_stop_invariant": self.monotonic_stop_invariant,
        }


@dataclass(frozen=True)
class _FactoryAttestation:
    fingerprint: str


@dataclass(frozen=True, init=False)
class FrozenCandidate:
    name: CandidateName
    policy_version: str
    parameters_json: str
    parameter_hash: str
    promotion_eligible: bool
    _factory_attestation: _FactoryAttestation | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise CandidateRegistryError(
            "frozen candidates must be created by the registry factory attestation"
        )

    def assert_integrity(self) -> None:
        if self._factory_attestation is None:
            raise CandidateRegistryError(
                "frozen candidates must be created by the registry factory attestation"
            )
        if not isinstance(self.name, CandidateName) or not self.policy_version.strip():
            raise CandidateRegistryError("frozen candidate identity is invalid")
        try:
            parameters = json.loads(self.parameters_json)
        except (TypeError, ValueError) as exc:
            raise CandidateRegistryError("frozen candidate parameters are invalid JSON") from exc
        if not isinstance(parameters, dict):
            raise CandidateRegistryError("frozen candidate parameters must be an object")
        canonical_json = json.dumps(
            parameters,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if canonical_json != self.parameters_json:
            raise CandidateRegistryError("frozen candidate parameters are not canonical")
        if stable_contract_hash(parameters) != self.parameter_hash:
            raise CandidateRegistryError("frozen candidate parameter hash failed integrity check")
        if self._factory_attestation.fingerprint != _candidate_attestation_fingerprint(
            name=self.name,
            policy_version=self.policy_version,
            parameter_hash=self.parameter_hash,
            promotion_eligible=self.promotion_eligible,
        ):
            raise CandidateRegistryError("frozen candidate factory attestation failed integrity check")

    @property
    def parameters(self) -> dict[str, Any]:
        self.assert_integrity()
        return json.loads(self.parameters_json)

    def as_dict(self) -> dict[str, Any]:
        self.assert_integrity()
        return {
            "name": self.name.value,
            "policy_version": self.policy_version,
            "parameters": self.parameters,
            "parameter_hash": self.parameter_hash,
            "promotion_eligible": self.promotion_eligible,
        }


@dataclass(frozen=True, init=False)
class FrozenCandidateRegistry:
    candidates: tuple[FrozenCandidate, ...]
    registry_hash: str
    _factory_attestation: _FactoryAttestation | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise CandidateRegistryError(
            "frozen registries must be created by the registry factory attestation"
        )

    def assert_integrity(self) -> None:
        if self._factory_attestation is None:
            raise CandidateRegistryError(
                "frozen registries must be created by the registry factory attestation"
            )
        if tuple(candidate.name for candidate in self.candidates) != tuple(CandidateName):
            raise CandidateRegistryError("frozen registry candidate set or order is invalid")
        for candidate in self.candidates:
            candidate.assert_integrity()
        payload = self._payload()
        expected_registry_hash = stable_contract_hash(payload)
        if self.registry_hash != expected_registry_hash:
            raise CandidateRegistryError("frozen registry hash failed integrity check")
        if self._factory_attestation.fingerprint != _registry_attestation_fingerprint(
            expected_registry_hash
        ):
            raise CandidateRegistryError("frozen registry factory attestation failed integrity check")

    def by_name(self, name: CandidateName) -> FrozenCandidate:
        self.assert_integrity()
        for candidate in self.candidates:
            if candidate.name is name:
                return candidate
        raise KeyError(name)

    def as_dict(self) -> dict[str, Any]:
        self.assert_integrity()
        return self._payload()

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


def replay_run_contract_identity_hash(contract: ReplayRunContract) -> str:
    """Hash every frozen Section 9 run input, not its caller-supplied label alone."""
    return stable_contract_hash(
        {
            "schema_version": "etf_action_replay_run_identity_v1",
            "run_id": contract.run_id,
            "contract_hash": contract.contract_hash,
            "input_snapshot_hash": contract.input_snapshot_hash,
            "code_hash": contract.code_hash,
            "schema_hash": contract.schema_hash,
            "candidate_config_hash": contract.candidate_config_hash,
            "frozen_parameter_hash": contract.frozen_parameter_hash,
            "policy_input_hash": contract.policy_input_hash,
            "data_cutoff": contract.data_cutoff.isoformat(),
            "warmup_boundary": contract.warmup_boundary.isoformat(),
            "candidate_ids": list(contract.candidate_ids),
        }
    )


@dataclass(frozen=True)
class ValidationRunContract:
    run_key: str
    policy_version: str
    h_day_mark_to_market: str
    counterfactual_cash_handling: str
    cost_model_version: str
    maximum_drawdown_noninferiority_tolerance: float
    minimum_practical_benefit: float
    sample_gate_independent_days: int
    sample_gate_complete_action_cycles: int
    sample_gate_minimum_coverage_ratio: float
    bootstrap_seed: int
    bootstrap_resamples: int
    bootstrap_block_length: int
    walk_forward_boundaries: tuple[date, ...]
    development_replay_run_contract_hash: str
    development_input_snapshot_hash: str
    final_holdout_input_snapshot_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("run_key", self.run_key),
            ("policy_version", self.policy_version),
            ("h_day_mark_to_market", self.h_day_mark_to_market),
            ("counterfactual_cash_handling", self.counterfactual_cash_handling),
            ("cost_model_version", self.cost_model_version),
        ):
            if not value.strip():
                raise FrozenValidationError(f"{name} must be frozen before outcomes")
        if (
            not _finite(self.maximum_drawdown_noninferiority_tolerance)
            or self.maximum_drawdown_noninferiority_tolerance < 0
        ):
            raise FrozenValidationError("maximum-drawdown tolerance must be non-negative")
        if not _finite(self.minimum_practical_benefit) or self.minimum_practical_benefit < 0:
            raise FrozenValidationError("minimum practical benefit must be non-negative")
        if self.sample_gate_independent_days <= 0:
            raise FrozenValidationError("sample gate must require independent trading days")
        if self.sample_gate_complete_action_cycles <= 0:
            raise FrozenValidationError("sample gate must require complete action cycles")
        if not (
            _finite(self.sample_gate_minimum_coverage_ratio)
            and 0 < self.sample_gate_minimum_coverage_ratio <= 1
        ):
            raise FrozenValidationError("sample coverage gate must be in (0, 1]")
        if self.bootstrap_resamples < 100:
            raise FrozenValidationError("bootstrap resample count must be at least 100")
        if self.bootstrap_block_length <= 0:
            raise FrozenValidationError("bootstrap block length must be positive")
        if (
            len(self.walk_forward_boundaries) < 2
            or self.walk_forward_boundaries
            != tuple(sorted(set(self.walk_forward_boundaries)))
        ):
            raise FrozenValidationError(
                "walk-forward boundaries must be pre-registered, unique, and chronological"
            )
        _require_sha256(
            "development replay run contract hash",
            self.development_replay_run_contract_hash,
        )
        _require_sha256(
            "development input snapshot hash",
            self.development_input_snapshot_hash,
        )
        _require_sha256(
            "final holdout input snapshot hash",
            self.final_holdout_input_snapshot_hash,
        )
        if self.development_input_snapshot_hash == self.final_holdout_input_snapshot_hash:
            raise FrozenValidationError(
                "development and final holdout snapshot scopes must be distinct"
            )

    @property
    def contract_hash(self) -> str:
        return stable_contract_hash(self.as_dict())

    @property
    def walk_forward_boundaries_hash(self) -> str:
        return stable_contract_hash(
            {
                "schema_version": "etf_action_walk_forward_boundaries_v1",
                "boundaries": [
                    boundary.isoformat()
                    for boundary in self.walk_forward_boundaries
                ],
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATION_CONTRACT_SCHEMA_VERSION,
            "run_key": self.run_key,
            "policy_version": self.policy_version,
            "primary_endpoint": PRIMARY_ENDPOINT,
            "top_n": 20,
            "horizon_trading_days": 10,
            "aggregation": "mean_action_cycle_benefit",
            "tax_fee_adjusted": True,
            "h_day_mark_to_market": self.h_day_mark_to_market,
            "counterfactual_cash_handling": self.counterfactual_cash_handling,
            "cost_model_version": self.cost_model_version,
            "maximum_drawdown_noninferiority_tolerance": (
                self.maximum_drawdown_noninferiority_tolerance
            ),
            "minimum_practical_benefit": self.minimum_practical_benefit,
            "sample_gate_independent_days": self.sample_gate_independent_days,
            "sample_gate_complete_action_cycles": self.sample_gate_complete_action_cycles,
            "sample_gate_minimum_coverage_ratio": (
                self.sample_gate_minimum_coverage_ratio
            ),
            "bootstrap_seed": self.bootstrap_seed,
            "bootstrap_resamples": self.bootstrap_resamples,
            "bootstrap_block_length": self.bootstrap_block_length,
            "walk_forward_boundaries": [
                boundary.isoformat() for boundary in self.walk_forward_boundaries
            ],
            "walk_forward_boundaries_hash": self.walk_forward_boundaries_hash,
            "development_replay_run_contract_hash": (
                self.development_replay_run_contract_hash
            ),
            "development_input_snapshot_hash": (
                self.development_input_snapshot_hash
            ),
            "final_holdout_input_snapshot_hash": (
                self.final_holdout_input_snapshot_hash
            ),
            "candidate_selection_rule": (
                "candidate3_vs_candidate2_clustered_lower_bound_exceeds_minimum_"
                "and_maximum_drawdown_is_noninferior"
            ),
        }


def freeze_candidate_registry(
    definitions: Sequence[CandidateDefinition],
    *,
    candidate3_parameters: Candidate3Parameters,
) -> FrozenCandidateRegistry:
    expected = tuple(CandidateName)
    if len(definitions) != len(expected):
        raise CandidateRegistryError("validation requires exactly three pre-registered candidates")
    try:
        names = tuple(CandidateName(definition.name) for definition in definitions)
    except ValueError as exc:
        raise CandidateRegistryError("candidate is not in the pre-registered set") from exc
    if len(set(names)) != len(names) or set(names) != set(expected):
        raise CandidateRegistryError("validation requires exactly three pre-registered candidates")

    by_name = {CandidateName(definition.name): definition for definition in definitions}
    canonical_by_name: dict[CandidateName, dict[str, Any]] = {}
    for name, definition in by_name.items():
        parameters = _canonical_mapping(definition.parameters)
        _reject_search_space(parameters)
        canonical_by_name[name] = parameters
    legacy = by_name[CandidateName.CURRENT_SEMANTICS_BASELINE]
    if legacy.promotion_eligible:
        raise CandidateRegistryError("legacy baseline is diagnostic-only and cannot be promoted")
    if canonical_by_name[CandidateName.CURRENT_SEMANTICS_BASELINE].get("target_semantics") != "relative":
        raise CandidateRegistryError("legacy diagnostic candidate semantics were changed")
    absolute_parameters = canonical_by_name[CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS]
    if not (
        absolute_parameters.get("target_semantics") == "absolute"
        and absolute_parameters.get("episode_idempotency") is True
        and absolute_parameters.get("take_profit_watch") == "hold"
    ):
        raise CandidateRegistryError("candidate 2 must isolate the registered semantic fix")
    legacy_parameters = canonical_by_name[CandidateName.CURRENT_SEMANTICS_BASELINE]
    shared_keys = {
        key
        for key in set(legacy_parameters) | set(absolute_parameters)
        if any(
            marker in key.lower()
            for marker in (
                "ranking",
                "threshold",
                "hard_stop",
                "profit_start",
                "trailing_giveback",
            )
        )
    }
    if any(legacy_parameters.get(key) != absolute_parameters.get(key) for key in shared_keys):
        raise CandidateRegistryError(
            "candidate 2 must keep ranking inputs and threshold values equal to the baseline"
        )
    semantic_keys = {
        "target_semantics",
        "episode_idempotency",
        "take_profit_watch",
    }
    legacy_nonsemantic = {
        key: value
        for key, value in legacy_parameters.items()
        if key not in semantic_keys
    }
    absolute_nonsemantic = {
        key: value
        for key, value in absolute_parameters.items()
        if key not in semantic_keys
    }
    if legacy_nonsemantic != absolute_nonsemantic:
        raise CandidateRegistryError(
            "candidate 2 may change only registered semantic-fix fields"
        )

    candidate3_extension = canonical_by_name[
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
    ]
    if candidate3_extension != {
        "base_candidate": CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS.value
    }:
        raise CandidateRegistryError(
            "candidate 3 may only extend candidate 2 with the frozen typed trailing-stop contract"
        )

    frozen: list[FrozenCandidate] = []
    for name in expected:
        definition = by_name[name]
        parameters = dict(canonical_by_name[name])
        if name is CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING:
            if parameters.get("base_candidate") != CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS.value:
                raise CandidateRegistryError("candidate 3 must extend the correctness baseline")
            parameters = {**absolute_parameters, **parameters}
            parameters.update(candidate3_parameters.as_dict())
        parameter_hash = stable_contract_hash(parameters)
        policy_version = definition.policy_version.strip()
        candidate = object.__new__(FrozenCandidate)
        object.__setattr__(candidate, "name", name)
        object.__setattr__(candidate, "policy_version", policy_version)
        object.__setattr__(
            candidate,
            "parameters_json",
            json.dumps(
                parameters,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        object.__setattr__(candidate, "parameter_hash", parameter_hash)
        object.__setattr__(
            candidate,
            "promotion_eligible",
            definition.promotion_eligible,
        )
        object.__setattr__(
            candidate,
            "_factory_attestation",
            _FactoryAttestation(
                _candidate_attestation_fingerprint(
                    name=name,
                    policy_version=policy_version,
                    parameter_hash=parameter_hash,
                    promotion_eligible=definition.promotion_eligible,
                )
            ),
        )
        candidate.assert_integrity()
        frozen.append(candidate)
    candidates = tuple(frozen)
    payload = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "candidates": [candidate.as_dict() for candidate in candidates],
    }
    registry_hash = stable_contract_hash(payload)
    registry = object.__new__(FrozenCandidateRegistry)
    object.__setattr__(registry, "candidates", candidates)
    object.__setattr__(registry, "registry_hash", registry_hash)
    object.__setattr__(
        registry,
        "_factory_attestation",
        _FactoryAttestation(_registry_attestation_fingerprint(registry_hash)),
    )
    registry.assert_integrity()
    return registry


def candidate3_effective_stop(
    *,
    previous_effective_stop: float | None,
    adjusted_peak: float,
    adjusted_atr20: float,
    parameters: Candidate3Parameters,
) -> float:
    if not _finite(adjusted_peak) or adjusted_peak <= 0:
        raise ValueError("adjusted peak must be finite and positive")
    if not _finite(adjusted_atr20) or adjusted_atr20 <= 0:
        raise ValueError("adjusted ATR20 must be finite and positive")
    raw_stop = adjusted_peak - parameters.atr_k * adjusted_atr20
    if previous_effective_stop is None:
        return raw_stop
    if not _finite(previous_effective_stop):
        raise ValueError("previous effective stop must be finite")
    return max(previous_effective_stop, raw_stop)


@dataclass(frozen=True)
class Candidate3TrailingEvaluation:
    data_eligible: bool
    reason: str
    high_watermark: float | None
    effective_stop: float | None
    recovery_threshold: float | None
    triggered_this_bar: bool
    recovered_this_bar: bool
    is_firing: bool


def evaluate_candidate3_trailing(
    *,
    previous_high_watermark: float | None,
    previous_effective_stop: float | None,
    was_firing: bool,
    adjusted_high: float | None,
    adjusted_close: float | None,
    adjusted_low: float | None,
    adjusted_atr20: float | None,
    atr_observation_count: int,
    parameters: Candidate3Parameters,
) -> Candidate3TrailingEvaluation:
    if previous_high_watermark is not None and (
        not _finite(previous_high_watermark) or previous_high_watermark <= 0
    ):
        raise ValueError("previous high watermark must be finite and positive")
    if previous_effective_stop is not None and not _finite(previous_effective_stop):
        raise ValueError("previous effective stop must be finite")
    required_values = [adjusted_high, adjusted_close, adjusted_low, adjusted_atr20]
    if atr_observation_count < parameters.atr_warmup_trading_days:
        return Candidate3TrailingEvaluation(
            data_eligible=False,
            reason="atr_warmup_incomplete",
            high_watermark=previous_high_watermark,
            effective_stop=previous_effective_stop,
            recovery_threshold=None,
            triggered_this_bar=False,
            recovered_this_bar=False,
            is_firing=was_firing,
        )
    if parameters.missing_data_behavior == "fail_closed" and any(
        value is None or not _finite(value) or value <= 0 for value in required_values
    ):
        return Candidate3TrailingEvaluation(
            data_eligible=False,
            reason="missing_data_fail_closed",
            high_watermark=previous_high_watermark,
            effective_stop=previous_effective_stop,
            recovery_threshold=None,
            triggered_this_bar=False,
            recovered_this_bar=False,
            is_firing=was_firing,
        )
    if (
        adjusted_high is not None
        and adjusted_close is not None
        and adjusted_low is not None
        and all(_finite(value) for value in (adjusted_high, adjusted_close, adjusted_low))
        and not adjusted_high >= adjusted_close >= adjusted_low
    ):
        return Candidate3TrailingEvaluation(
            data_eligible=False,
            reason="invalid_ohlc_fail_closed",
            high_watermark=previous_high_watermark,
            effective_stop=previous_effective_stop,
            recovery_threshold=None,
            triggered_this_bar=False,
            recovered_this_bar=False,
            is_firing=was_firing,
        )

    peak = adjusted_high if parameters.peak_source == "adjusted_high" else adjusted_close
    assert peak is not None
    assert adjusted_close is not None
    assert adjusted_low is not None
    assert adjusted_atr20 is not None
    high_watermark = max(previous_high_watermark or peak, peak)
    effective_stop = candidate3_effective_stop(
        previous_effective_stop=previous_effective_stop,
        adjusted_peak=high_watermark,
        adjusted_atr20=adjusted_atr20,
        parameters=parameters,
    )
    recovery_threshold = (
        effective_stop + parameters.recovery_hysteresis_atr * adjusted_atr20
    )
    if parameters.same_day_ohlc_ordering == "close_only":
        triggered = adjusted_close <= effective_stop
    else:
        triggered = (
            previous_effective_stop is not None
            and adjusted_low <= previous_effective_stop
        )
    if parameters.same_day_ohlc_ordering == "close_only":
        recovered = was_firing and adjusted_close >= recovery_threshold
        is_firing = (was_firing and not recovered) or (not was_firing and triggered)
    else:
        firing_after_stop = was_firing or triggered
        recovered = firing_after_stop and adjusted_close >= recovery_threshold
        is_firing = firing_after_stop and not recovered
    if triggered and recovered:
        reason = "triggered_then_recovered"
    elif triggered:
        reason = "triggered"
    elif recovered:
        reason = "recovered"
    elif is_firing:
        reason = "firing"
    else:
        reason = "clear"
    return Candidate3TrailingEvaluation(
        data_eligible=True,
        reason=reason,
        high_watermark=high_watermark,
        effective_stop=effective_stop,
        recovery_threshold=recovery_threshold,
        triggered_this_bar=triggered,
        recovered_this_bar=recovered,
        is_firing=is_firing,
    )


@dataclass(frozen=True)
class ActionOutcomeWindow:
    action_cycle_id: str
    signal_trading_day: date
    actual_fill_trading_day: date
    outcome_end_trading_day: date
    horizon_trading_days: int = 10

    def __post_init__(self) -> None:
        if not self.action_cycle_id:
            raise ValueError("action_cycle_id is required")
        if self.horizon_trading_days != 10:
            raise ValueError("walk-forward purge is frozen to the longest 10-day outcome")
        if self.actual_fill_trading_day < self.signal_trading_day:
            raise ValueError("actual fill cannot precede signal day")
        if self.outcome_end_trading_day < self.actual_fill_trading_day:
            raise ValueError("outcome end cannot precede actual fill")


@dataclass(frozen=True, init=False)
class PurgedWalkForwardSamples:
    all_samples: tuple[ActionOutcomeWindow, ...]
    kept: tuple[ActionOutcomeWindow, ...]
    purged_action_cycle_ids: tuple[str, ...]
    walk_forward_boundaries: tuple[date, ...]
    trading_calendar: tuple[date, ...]
    contract_hash: str
    walk_forward_boundaries_hash: str
    purged_samples_hash: str

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise FrozenValidationError(
            "walk-forward purge evidence factory integrity check failed"
        )

    def assert_integrity(self) -> None:
        calendar_hash = trading_calendar_snapshot_hash(self.trading_calendar)
        expected_kept: list[ActionOutcomeWindow] = []
        expected_purged: list[str] = []
        for sample in self.all_samples:
            try:
                expected_window = ten_trading_day_outcome_window(
                    sample.action_cycle_id,
                    sample.signal_trading_day,
                    actual_fill_trading_day=sample.actual_fill_trading_day,
                    trading_days=self.trading_calendar,
                )
            except (SampleGateError, ValueError) as exc:
                raise FrozenValidationError(
                    "purge evidence has an invalid 10-trading-day outcome window"
                ) from exc
            if sample != expected_window:
                raise FrozenValidationError(
                    "purge evidence outcome end is not the frozen 10-trading-day outcome"
                )
            crosses = any(
                sample.signal_trading_day < boundary <= sample.outcome_end_trading_day
                for boundary in self.walk_forward_boundaries
            )
            if crosses:
                expected_purged.append(sample.action_cycle_id)
            else:
                expected_kept.append(sample)
        expected_boundaries_hash = stable_contract_hash(
            {
                "schema_version": "etf_action_walk_forward_boundaries_v1",
                "boundaries": [
                    boundary.isoformat()
                    for boundary in self.walk_forward_boundaries
                ],
            }
        )
        expected_payload = _purged_walk_forward_payload(
            samples=self.all_samples,
            purged_action_cycle_ids=tuple(expected_purged),
            contract_hash=self.contract_hash,
            walk_forward_boundaries_hash=expected_boundaries_hash,
            trading_calendar_hash=calendar_hash,
        )
        if not (
            self.kept == tuple(expected_kept)
            and self.purged_action_cycle_ids == tuple(expected_purged)
            and self.walk_forward_boundaries_hash == expected_boundaries_hash
            and self.purged_samples_hash == stable_contract_hash(expected_payload)
        ):
            raise FrozenValidationError("walk-forward purge evidence failed integrity check")


def ten_trading_day_outcome_window(
    action_cycle_id: str,
    signal_trading_day: date,
    *,
    actual_fill_trading_day: date,
    trading_days: Sequence[date],
) -> ActionOutcomeWindow:
    calendar = tuple(trading_days)
    if calendar != tuple(sorted(set(calendar))):
        raise ValueError("trading-day calendar must be unique and chronological")
    try:
        calendar.index(signal_trading_day)
    except ValueError as exc:
        raise ValueError("signal day is absent from the trading-day calendar") from exc
    try:
        fill_index = calendar.index(actual_fill_trading_day)
    except ValueError as exc:
        raise ValueError("actual fill day is absent from the trading-day calendar") from exc
    if actual_fill_trading_day < signal_trading_day:
        raise ValueError("actual fill cannot precede signal day")
    outcome_index = fill_index + 10
    if outcome_index >= len(calendar):
        raise SampleGateError("10-trading-day outcome is incomplete")
    return ActionOutcomeWindow(
        action_cycle_id=action_cycle_id,
        signal_trading_day=signal_trading_day,
        actual_fill_trading_day=actual_fill_trading_day,
        outcome_end_trading_day=calendar[outcome_index],
    )


def purge_boundary_crossing(
    samples: Sequence[ActionOutcomeWindow],
    *,
    contract: ValidationRunContract,
    trading_calendar: Sequence[date],
) -> PurgedWalkForwardSamples:
    ordered_boundaries = contract.walk_forward_boundaries
    calendar = tuple(trading_calendar)
    calendar_hash = trading_calendar_snapshot_hash(calendar)
    kept: list[ActionOutcomeWindow] = []
    purged: list[str] = []
    ordered_samples = sorted(
        samples,
        key=lambda item: (item.signal_trading_day, item.action_cycle_id),
    )
    for sample in ordered_samples:
        crosses = any(
            sample.signal_trading_day < boundary <= sample.outcome_end_trading_day
            for boundary in ordered_boundaries
        )
        if crosses:
            purged.append(sample.action_cycle_id)
        else:
            kept.append(sample)
    payload = _purged_walk_forward_payload(
        samples=tuple(ordered_samples),
        purged_action_cycle_ids=tuple(purged),
        contract_hash=contract.contract_hash,
        walk_forward_boundaries_hash=contract.walk_forward_boundaries_hash,
        trading_calendar_hash=calendar_hash,
    )
    result = object.__new__(PurgedWalkForwardSamples)
    for field_name, field_value in (
        ("all_samples", tuple(ordered_samples)),
        ("kept", tuple(kept)),
        ("purged_action_cycle_ids", tuple(purged)),
        ("walk_forward_boundaries", contract.walk_forward_boundaries),
        ("trading_calendar", calendar),
        ("contract_hash", contract.contract_hash),
        ("walk_forward_boundaries_hash", contract.walk_forward_boundaries_hash),
        ("purged_samples_hash", stable_contract_hash(payload)),
    ):
        object.__setattr__(result, field_name, field_value)
    result.assert_integrity()
    return result


def _purged_walk_forward_payload(
    *,
    samples: Sequence[ActionOutcomeWindow],
    purged_action_cycle_ids: tuple[str, ...],
    contract_hash: str,
    walk_forward_boundaries_hash: str,
    trading_calendar_hash: str,
) -> dict[str, Any]:
    purged_ids = set(purged_action_cycle_ids)
    return {
        "schema_version": "etf_action_purged_walk_forward_samples_v1",
        "contract_hash": contract_hash,
        "walk_forward_boundaries_hash": walk_forward_boundaries_hash,
        "trading_calendar_hash": trading_calendar_hash,
        "samples": [
            {
                "action_cycle_id": sample.action_cycle_id,
                "signal_trading_day": sample.signal_trading_day.isoformat(),
                "actual_fill_trading_day": sample.actual_fill_trading_day.isoformat(),
                "outcome_end_trading_day": sample.outcome_end_trading_day.isoformat(),
                "purged": sample.action_cycle_id in purged_ids,
            }
            for sample in samples
        ],
    }


@dataclass(frozen=True)
class ActionCycleBenefit:
    candidate: CandidateName
    registry_hash: str
    candidate_parameter_hash: str
    signal_trading_day: date
    experimental_unit_id: str
    action_cycle_id: str
    tax_fee_adjusted_benefit: float
    top_n: int
    horizon_trading_days: int

    def __post_init__(self) -> None:
        _require_sha256("action-cycle registry hash", self.registry_hash)
        _require_sha256(
            "action-cycle candidate parameter hash", self.candidate_parameter_hash
        )
        if not self.experimental_unit_id:
            raise ValueError("experimental unit id is required")
        if not self.action_cycle_id:
            raise ValueError("action cycle id is required")
        if not _finite(self.tax_fee_adjusted_benefit):
            raise ValueError("action-cycle benefit must be finite")


@dataclass(frozen=True)
class CandidateEndpointEvidence:
    development_benefits: tuple[float, ...]
    validation_benefits: tuple[ActionCycleBenefit, ...]
    equity_curve: tuple[float, ...]
    eligible_experimental_unit_ids: tuple[str, ...]
    opportunity_costs: tuple[float, ...]
    trading_calendar: tuple[date, ...]
    input_snapshot_hash: str

    def __post_init__(self) -> None:
        if not self.development_benefits or not all(
            _finite(value) for value in self.development_benefits
        ):
            raise FrozenValidationError(
                "raw endpoint evidence requires finite development benefits"
            )
        if not self.validation_benefits:
            raise FrozenValidationError(
                "raw endpoint evidence requires completed validation benefits"
            )
        if len(self.equity_curve) < 2 or any(
            not _finite(value) or value <= 0 for value in self.equity_curve
        ):
            raise FrozenValidationError(
                "raw endpoint evidence requires a positive finite equity curve"
            )
        if (
            not self.eligible_experimental_unit_ids
            or len(set(self.eligible_experimental_unit_ids))
            != len(self.eligible_experimental_unit_ids)
        ):
            raise FrozenValidationError(
                "raw endpoint evidence requires unique eligible experimental units"
            )
        if len(self.opportunity_costs) != len(self.validation_benefits) or not all(
            _finite(value) for value in self.opportunity_costs
        ):
            raise FrozenValidationError(
                "raw endpoint evidence opportunity costs must align with completed benefits"
            )
        _require_sha256("raw endpoint input snapshot hash", self.input_snapshot_hash)
        trading_calendar_snapshot_hash(self.trading_calendar)

    @property
    def evidence_hash(self) -> str:
        return stable_contract_hash(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "etf_action_candidate_endpoint_evidence_v1",
            "development_benefits": list(self.development_benefits),
            "validation_benefits": [
                {
                    "candidate": sample.candidate.value,
                    "registry_hash": sample.registry_hash,
                    "candidate_parameter_hash": sample.candidate_parameter_hash,
                    "signal_trading_day": sample.signal_trading_day.isoformat(),
                    "experimental_unit_id": sample.experimental_unit_id,
                    "action_cycle_id": sample.action_cycle_id,
                    "tax_fee_adjusted_benefit": sample.tax_fee_adjusted_benefit,
                    "top_n": sample.top_n,
                    "horizon_trading_days": sample.horizon_trading_days,
                }
                for sample in self.validation_benefits
            ],
            "equity_curve": list(self.equity_curve),
            "eligible_experimental_unit_ids": list(
                self.eligible_experimental_unit_ids
            ),
            "opportunity_costs": list(self.opportunity_costs),
            "trading_calendar": [day.isoformat() for day in self.trading_calendar],
            "input_snapshot_hash": self.input_snapshot_hash,
        }


@dataclass(frozen=True)
class BootstrapDifference:
    point_estimate: float
    confidence_interval: tuple[float, float]
    action_cycle_count: int
    independent_trading_day_count: int
    seed: int
    resamples: int
    block_length: int
    registry_hash: str
    contract_hash: str
    input_snapshot_hash: str
    trading_calendar_hash: str
    paired_samples_hash: str
    result_hash: str

    def __post_init__(self) -> None:
        if not _finite(self.point_estimate) or not all(
            _finite(value) for value in self.confidence_interval
        ):
            raise FrozenValidationError("bootstrap estimates must be finite")
        if self.confidence_interval[0] > self.confidence_interval[1]:
            raise FrozenValidationError("bootstrap confidence interval is inverted")
        if self.action_cycle_count <= 0 or self.independent_trading_day_count <= 0:
            raise FrozenValidationError("bootstrap sample counts must be positive")
        for name, value in (
            ("bootstrap registry hash", self.registry_hash),
            ("bootstrap contract hash", self.contract_hash),
            ("bootstrap input snapshot hash", self.input_snapshot_hash),
            ("bootstrap trading calendar hash", self.trading_calendar_hash),
            ("bootstrap paired samples hash", self.paired_samples_hash),
            ("bootstrap result hash", self.result_hash),
        ):
            _require_sha256(name, value)


def trading_day_block_bootstrap_difference(
    candidate2: Sequence[ActionCycleBenefit],
    candidate3: Sequence[ActionCycleBenefit],
    *,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    trading_calendar: Sequence[date],
    input_snapshot_hash: str,
) -> BootstrapDifference:
    _require_sha256("bootstrap input snapshot hash", input_snapshot_hash)
    calendar = tuple(trading_calendar)
    calendar_hash = trading_calendar_snapshot_hash(calendar)
    left = _primary_benefit_map(
        candidate2,
        expected_candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        registry=registry,
    )
    right = _primary_benefit_map(
        candidate3,
        expected_candidate=(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
        ),
        registry=registry,
    )
    if set(left) != set(right):
        raise FrozenValidationError("candidate 2 and candidate 3 require paired action cycles")
    if not left:
        raise FrozenValidationError("bootstrap requires completed action-cycle outcomes")

    by_day: dict[date, list[float]] = defaultdict(list)
    paired_payload: list[dict[str, Any]] = []
    for experimental_unit_id in sorted(left):
        left_day, left_cycle_id, left_value = left[experimental_unit_id]
        right_day, right_cycle_id, right_value = right[experimental_unit_id]
        if left_day != right_day:
            raise FrozenValidationError(
                "paired experimental units must share one signal trading day"
            )
        if left_day not in calendar:
            raise FrozenValidationError(
                "signal trading day is absent from the frozen trading calendar"
            )
        by_day[left_day].append(right_value - left_value)
        paired_payload.append(
            {
                "experimental_unit_id": experimental_unit_id,
                "signal_trading_day": left_day.isoformat(),
                "candidate2_action_cycle_id": left_cycle_id,
                "candidate3_action_cycle_id": right_cycle_id,
                "candidate2_benefit": left_value,
                "candidate3_benefit": right_value,
            }
        )
    days = sorted(by_day)
    if contract.bootstrap_block_length > len(calendar):
        raise FrozenValidationError("bootstrap block length exceeds the trading calendar")
    differences = [value for day in days for value in by_day[day]]
    point_estimate = fmean(differences)
    generator = random.Random(contract.bootstrap_seed)
    resampled_means: list[float] = []
    attempts = 0
    maximum_attempts = contract.bootstrap_resamples * 100
    while len(resampled_means) < contract.bootstrap_resamples:
        attempts += 1
        if attempts > maximum_attempts:
            raise FrozenValidationError(
                "full-calendar bootstrap could not draw a non-empty action sample"
            )
        sampled: list[float] = []
        sampled_calendar_days = 0
        while sampled_calendar_days < len(calendar):
            start = generator.randrange(len(calendar))
            block_size = min(
                contract.bootstrap_block_length,
                len(calendar) - sampled_calendar_days,
            )
            for offset in range(block_size):
                day = calendar[(start + offset) % len(calendar)]
                sampled.extend(by_day.get(day, ()))
            sampled_calendar_days += block_size
        if sampled:
            resampled_means.append(fmean(sampled))
    interval = (
        _percentile(resampled_means, 0.025),
        _percentile(resampled_means, 0.975),
    )
    paired_samples_hash = stable_contract_hash(
        {
            "contract_hash": contract.contract_hash,
            "registry_hash": registry.registry_hash,
            "input_snapshot_hash": input_snapshot_hash,
            "trading_calendar_hash": calendar_hash,
            "paired_samples": paired_payload,
        }
    )
    result_payload = {
        "point_estimate": point_estimate,
        "confidence_interval": list(interval),
        "action_cycle_count": len(differences),
        "independent_trading_day_count": len(days),
        "seed": contract.bootstrap_seed,
        "resamples": contract.bootstrap_resamples,
        "block_length": contract.bootstrap_block_length,
        "registry_hash": registry.registry_hash,
        "contract_hash": contract.contract_hash,
        "input_snapshot_hash": input_snapshot_hash,
        "trading_calendar_hash": calendar_hash,
        "paired_samples_hash": paired_samples_hash,
    }
    return BootstrapDifference(
        point_estimate=point_estimate,
        confidence_interval=interval,
        action_cycle_count=len(differences),
        independent_trading_day_count=len(days),
        seed=contract.bootstrap_seed,
        resamples=contract.bootstrap_resamples,
        block_length=contract.bootstrap_block_length,
        registry_hash=registry.registry_hash,
        contract_hash=contract.contract_hash,
        input_snapshot_hash=input_snapshot_hash,
        trading_calendar_hash=calendar_hash,
        paired_samples_hash=paired_samples_hash,
        result_hash=stable_contract_hash(result_payload),
    )


@dataclass(frozen=True)
class CandidateEndpointResult:
    candidate: CandidateName
    registry_hash: str
    candidate_parameter_hash: str
    top_n: int
    horizon_trading_days: int
    tax_fee_adjusted: bool
    endpoint: str
    development_mean_benefit: float
    validation_mean_benefit: float
    maximum_drawdown: float
    confidence_interval: tuple[float, float]
    action_cycle_count: int
    independent_trading_day_count: int
    coverage_numerator: int
    coverage_denominator: int
    input_snapshot_hash: str
    trading_calendar_hash: str
    opportunity_cost: float
    derivation_contract_hash: str
    raw_evidence: CandidateEndpointEvidence

    def __post_init__(self) -> None:
        numeric = (
            self.development_mean_benefit,
            self.validation_mean_benefit,
            self.maximum_drawdown,
            *self.confidence_interval,
            self.opportunity_cost,
        )
        if not all(_finite(value) for value in numeric):
            raise FrozenValidationError("endpoint results must be finite")
        if self.maximum_drawdown < 0:
            raise FrozenValidationError("maximum drawdown must be a non-negative loss magnitude")
        if self.confidence_interval[0] > self.confidence_interval[1]:
            raise FrozenValidationError("confidence interval is inverted")
        if self.action_cycle_count < 0 or self.independent_trading_day_count < 0:
            raise FrozenValidationError("sample counts cannot be negative")
        if self.independent_trading_day_count > self.action_cycle_count:
            raise FrozenValidationError("independent days cannot exceed action cycles")
        if not (
            0 <= self.coverage_numerator <= self.coverage_denominator
            and self.coverage_denominator > 0
        ):
            raise FrozenValidationError("coverage counts are invalid")
        if self.coverage_numerator != self.action_cycle_count:
            raise FrozenValidationError(
                "coverage numerator must equal completed action-cycle outcomes"
            )
        _require_sha256("endpoint registry hash", self.registry_hash)
        _require_sha256(
            "endpoint candidate parameter hash", self.candidate_parameter_hash
        )
        _require_sha256("endpoint input snapshot hash", self.input_snapshot_hash)
        _require_sha256("endpoint trading calendar hash", self.trading_calendar_hash)
        _require_sha256("endpoint derivation contract hash", self.derivation_contract_hash)

    @property
    def raw_evidence_hash(self) -> str:
        return self.raw_evidence.evidence_hash

    @property
    def coverage_ratio(self) -> float:
        return self.coverage_numerator / self.coverage_denominator


def derive_candidate_endpoint(
    *,
    candidate: CandidateName,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    raw_evidence: CandidateEndpointEvidence,
) -> CandidateEndpointResult:
    statistics = _derive_endpoint_statistics(
        candidate=candidate,
        registry=registry,
        contract=contract,
        raw_evidence=raw_evidence,
    )
    return CandidateEndpointResult(
        candidate=candidate,
        registry_hash=registry.registry_hash,
        candidate_parameter_hash=registry.by_name(candidate).parameter_hash,
        top_n=20,
        horizon_trading_days=10,
        tax_fee_adjusted=True,
        endpoint="mean_action_cycle_benefit",
        development_mean_benefit=statistics["development_mean_benefit"],
        validation_mean_benefit=statistics["validation_mean_benefit"],
        maximum_drawdown=statistics["maximum_drawdown"],
        confidence_interval=statistics["confidence_interval"],
        action_cycle_count=statistics["action_cycle_count"],
        independent_trading_day_count=statistics[
            "independent_trading_day_count"
        ],
        coverage_numerator=statistics["coverage_numerator"],
        coverage_denominator=statistics["coverage_denominator"],
        input_snapshot_hash=raw_evidence.input_snapshot_hash,
        trading_calendar_hash=trading_calendar_snapshot_hash(
            raw_evidence.trading_calendar
        ),
        opportunity_cost=statistics["opportunity_cost"],
        derivation_contract_hash=contract.contract_hash,
        raw_evidence=raw_evidence,
    )


_REPLAY_EVIDENCE_ATTESTATION_TOKEN = object()


@dataclass(frozen=True, init=False)
class SealedReplayEvidenceAttestation:
    replay_run_contract_hash: str
    replay_checkpoint_hash: str
    replay_manifest_hash: str
    input_snapshot_hash: str
    registry_hash: str
    evidence_bundle_hash: str
    attestation_hash: str
    _factory_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise FrozenValidationError(
            "replay evidence attestation must be loaded from a sealed replay artifact"
        )

    def assert_integrity(self) -> None:
        if self._factory_token is not _REPLAY_EVIDENCE_ATTESTATION_TOKEN:
            raise FrozenValidationError("sealed replay artifact attestation is not authentic")
        expected = stable_contract_hash(self._payload())
        if self.attestation_hash != expected:
            raise FrozenValidationError("sealed replay artifact attestation was modified")

    def as_dict(self) -> dict[str, Any]:
        self.assert_integrity()
        return {**self._payload(), "attestation_hash": self.attestation_hash}

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": "etf_action_replay_evidence_attestation_v1",
            "replay_run_contract_hash": self.replay_run_contract_hash,
            "replay_checkpoint_hash": self.replay_checkpoint_hash,
            "replay_manifest_hash": self.replay_manifest_hash,
            "input_snapshot_hash": self.input_snapshot_hash,
            "registry_hash": self.registry_hash,
            "evidence_bundle_hash": self.evidence_bundle_hash,
        }


def attest_development_replay_evidence(
    *,
    store: ReplayArtifactStore,
    replay_contract: ReplayRunContract,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    endpoints: Sequence[CandidateEndpointResult],
    purged_samples: PurgedWalkForwardSamples,
    max_seconds: float = 55.0,
) -> SealedReplayEvidenceAttestation:
    """Load an atomic Section 9 checkpoint and bind it to the exact gate inputs."""
    registry.assert_integrity()
    replay_identity = replay_run_contract_identity_hash(replay_contract)
    if replay_identity != contract.development_replay_run_contract_hash:
        raise FrozenValidationError(
            "sealed replay artifact does not match the pre-registered replay contract"
        )
    if replay_contract.input_snapshot_hash != contract.development_input_snapshot_hash:
        raise FrozenValidationError(
            "sealed replay artifact does not match the development snapshot"
        )
    if replay_contract.frozen_parameter_hash != registry.registry_hash:
        raise FrozenValidationError(
            "sealed replay artifact does not match the frozen candidate registry"
        )
    required_candidate_ids = {
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS.value,
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING.value,
    }
    if not required_candidate_ids.issubset(replay_contract.candidate_ids):
        raise FrozenValidationError(
            "sealed replay artifact is missing a required validation candidate"
        )
    bundle_hash = development_evidence_bundle_hash(
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    try:
        checkpoint = store.require_validation_evidence_bundle(
            run_id=replay_contract.run_id,
            expected_contract=replay_contract,
            evidence_bundle_hash=bundle_hash,
            max_seconds=max_seconds,
        )
    except ArtifactConflictError as exc:
        raise FrozenValidationError(
            "development evidence was not loaded from a sealed replay artifact"
        ) from exc
    payload = {
        "schema_version": "etf_action_replay_evidence_attestation_v1",
        "replay_run_contract_hash": replay_identity,
        "replay_checkpoint_hash": checkpoint.checkpoint_hash,
        "replay_manifest_hash": checkpoint.manifest_hash,
        "input_snapshot_hash": replay_contract.input_snapshot_hash,
        "registry_hash": registry.registry_hash,
        "evidence_bundle_hash": bundle_hash,
    }
    result = object.__new__(SealedReplayEvidenceAttestation)
    for name, value in payload.items():
        if name != "schema_version":
            object.__setattr__(result, name, value)
    object.__setattr__(result, "attestation_hash", stable_contract_hash(payload))
    object.__setattr__(result, "_factory_token", _REPLAY_EVIDENCE_ATTESTATION_TOKEN)
    result.assert_integrity()
    return result


@dataclass(frozen=True)
class PrimarySelectionDecision:
    review_candidate: CandidateName | None
    sample_gate_passed: bool
    maximum_drawdown_gate_passed: bool
    minimum_practical_benefit_gate_passed: bool
    clustered_improvement: float
    clustered_improvement_confidence_interval: tuple[float, float]
    minimum_practical_benefit: float
    maximum_drawdown_noninferiority_tolerance: float
    promotion_status: str
    reasons: tuple[str, ...]
    bootstrap_result_hash: str
    paired_samples_hash: str
    bootstrap_registry_hash: str
    bootstrap_contract_hash: str
    bootstrap_input_snapshot_hash: str
    bootstrap_trading_calendar_hash: str
    bootstrap_seed: int
    bootstrap_resamples: int
    bootstrap_block_length: int
    auto_promoted: bool = False


def evaluate_primary_selection(
    candidate2: CandidateEndpointResult,
    candidate3: CandidateEndpointResult,
    *,
    registry: FrozenCandidateRegistry,
    candidate2_benefits: Sequence[ActionCycleBenefit],
    candidate3_benefits: Sequence[ActionCycleBenefit],
    trading_calendar: Sequence[date],
    input_snapshot_hash: str,
    contract: ValidationRunContract,
) -> PrimarySelectionDecision:
    registry.assert_integrity()
    if candidate2.candidate is CandidateName.CURRENT_SEMANTICS_BASELINE:
        raise FrozenValidationError("legacy baseline is diagnostic-only and cannot be selected")
    for endpoint, benefits in (
        (candidate2, candidate2_benefits),
        (candidate3, candidate3_benefits),
    ):
        _require_endpoint_is_derived(endpoint, registry=registry, contract=contract)
        if endpoint.raw_evidence.validation_benefits != tuple(benefits):
            raise FrozenValidationError(
                "derived endpoint benefits do not match the raw selector evidence"
            )
        if (
            endpoint.raw_evidence.trading_calendar != tuple(trading_calendar)
            or endpoint.raw_evidence.input_snapshot_hash != input_snapshot_hash
        ):
            raise FrozenValidationError(
                "derived endpoint scope does not match the raw selector evidence"
            )
    improvement = trading_day_block_bootstrap_difference(
        candidate2_benefits,
        candidate3_benefits,
        registry=registry,
        contract=contract,
        trading_calendar=trading_calendar,
        input_snapshot_hash=input_snapshot_hash,
    )
    return _evaluate_primary_selection_from_bootstrap(
        candidate2,
        candidate3,
        registry=registry,
        improvement=improvement,
        contract=contract,
    )


def _selection_for_endpoints(
    candidate2: CandidateEndpointResult,
    candidate3: CandidateEndpointResult,
    *,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
) -> PrimarySelectionDecision:
    return evaluate_primary_selection(
        candidate2,
        candidate3,
        registry=registry,
        candidate2_benefits=candidate2.raw_evidence.validation_benefits,
        candidate3_benefits=candidate3.raw_evidence.validation_benefits,
        trading_calendar=candidate2.raw_evidence.trading_calendar,
        input_snapshot_hash=candidate2.raw_evidence.input_snapshot_hash,
        contract=contract,
    )


def _evaluate_primary_selection_from_bootstrap(
    candidate2: CandidateEndpointResult,
    candidate3: CandidateEndpointResult,
    *,
    registry: FrozenCandidateRegistry,
    improvement: BootstrapDifference,
    contract: ValidationRunContract,
) -> PrimarySelectionDecision:
    if candidate2.candidate is CandidateName.CURRENT_SEMANTICS_BASELINE:
        raise FrozenValidationError("legacy baseline is diagnostic-only and cannot be selected")
    if candidate2.candidate is not CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS:
        raise FrozenValidationError("candidate 2 must be the absolute-target correctness baseline")
    if (
        candidate3.candidate
        is not CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
    ):
        raise FrozenValidationError("candidate 3 identity does not match the frozen registry")
    _require_primary_endpoint(candidate2)
    _require_primary_endpoint(candidate3)
    _require_endpoint_candidate_binding(
        candidate2,
        expected_candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        registry=registry,
    )
    _require_endpoint_candidate_binding(
        candidate3,
        expected_candidate=(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
        ),
        registry=registry,
    )
    expected_result_hash = stable_contract_hash(
        {
            "point_estimate": improvement.point_estimate,
            "confidence_interval": list(improvement.confidence_interval),
            "action_cycle_count": improvement.action_cycle_count,
            "independent_trading_day_count": improvement.independent_trading_day_count,
            "seed": improvement.seed,
            "resamples": improvement.resamples,
            "block_length": improvement.block_length,
            "registry_hash": improvement.registry_hash,
            "contract_hash": improvement.contract_hash,
            "input_snapshot_hash": improvement.input_snapshot_hash,
            "trading_calendar_hash": improvement.trading_calendar_hash,
            "paired_samples_hash": improvement.paired_samples_hash,
        }
    )
    if not (
        improvement.registry_hash == registry.registry_hash
        and improvement.contract_hash == contract.contract_hash
        and improvement.seed == contract.bootstrap_seed
        and improvement.resamples == contract.bootstrap_resamples
        and improvement.block_length == contract.bootstrap_block_length
    ):
        raise FrozenValidationError("bootstrap result does not match the frozen contract")
    if improvement.result_hash != expected_result_hash:
        raise FrozenValidationError("bootstrap result does not match its sealed artifact hash")
    if not (
        candidate2.input_snapshot_hash
        == candidate3.input_snapshot_hash
        == improvement.input_snapshot_hash
        and candidate2.trading_calendar_hash
        == candidate3.trading_calendar_hash
        == improvement.trading_calendar_hash
    ):
        raise FrozenValidationError(
            "bootstrap input snapshot or trading calendar does not match the endpoints"
        )
    if not (
        candidate2.action_cycle_count
        == candidate3.action_cycle_count
        == improvement.action_cycle_count
        and candidate2.independent_trading_day_count
        == candidate3.independent_trading_day_count
        == improvement.independent_trading_day_count
    ):
        raise FrozenValidationError("bootstrap sample counts do not match the endpoints")
    if not (
        candidate2.coverage_numerator == candidate3.coverage_numerator
        and candidate2.coverage_denominator == candidate3.coverage_denominator
    ):
        raise FrozenValidationError("candidate endpoint coverage counts are not comparable")
    expected_improvement = candidate3.validation_mean_benefit - candidate2.validation_mean_benefit
    if not math.isclose(
        improvement.point_estimate,
        expected_improvement,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise FrozenValidationError("clustered improvement does not match the primary endpoint")

    independent_day_gate_passed = min(
        candidate2.independent_trading_day_count,
        candidate3.independent_trading_day_count,
        improvement.independent_trading_day_count,
    ) >= contract.sample_gate_independent_days
    action_cycle_gate_passed = min(
        candidate2.action_cycle_count,
        candidate3.action_cycle_count,
        improvement.action_cycle_count,
    ) >= contract.sample_gate_complete_action_cycles
    coverage_gate_passed = min(
        candidate2.coverage_ratio,
        candidate3.coverage_ratio,
    ) >= contract.sample_gate_minimum_coverage_ratio
    sample_gate_passed = (
        independent_day_gate_passed
        and action_cycle_gate_passed
        and coverage_gate_passed
    )
    drawdown_gate_passed = candidate3.maximum_drawdown <= (
        candidate2.maximum_drawdown
        + contract.maximum_drawdown_noninferiority_tolerance
    )
    benefit_gate_passed = (
        improvement.confidence_interval[0] > contract.minimum_practical_benefit
    )
    reasons: list[str] = []
    if not sample_gate_passed:
        if not independent_day_gate_passed:
            reasons.append("independent_trading_day_sample_gate_failed")
        if not action_cycle_gate_passed:
            reasons.append("complete_action_cycle_sample_gate_failed")
        if not coverage_gate_passed:
            reasons.append("completed_outcome_coverage_gate_failed")
        return PrimarySelectionDecision(
            review_candidate=None,
            sample_gate_passed=False,
            maximum_drawdown_gate_passed=drawdown_gate_passed,
            minimum_practical_benefit_gate_passed=benefit_gate_passed,
            clustered_improvement=improvement.point_estimate,
            clustered_improvement_confidence_interval=improvement.confidence_interval,
            minimum_practical_benefit=contract.minimum_practical_benefit,
            maximum_drawdown_noninferiority_tolerance=(
                contract.maximum_drawdown_noninferiority_tolerance
            ),
            promotion_status="sample_insufficient",
            reasons=tuple(reasons),
            bootstrap_result_hash=improvement.result_hash,
            paired_samples_hash=improvement.paired_samples_hash,
            bootstrap_registry_hash=improvement.registry_hash,
            bootstrap_contract_hash=improvement.contract_hash,
            bootstrap_input_snapshot_hash=improvement.input_snapshot_hash,
            bootstrap_trading_calendar_hash=improvement.trading_calendar_hash,
            bootstrap_seed=improvement.seed,
            bootstrap_resamples=improvement.resamples,
            bootstrap_block_length=improvement.block_length,
        )
    review_candidate = CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS
    if not drawdown_gate_passed:
        reasons.append("candidate3_maximum_drawdown_noninferiority_failed")
    if not benefit_gate_passed:
        reasons.append("candidate3_clustered_lower_bound_below_minimum_practical_benefit")
    if drawdown_gate_passed and benefit_gate_passed:
        review_candidate = (
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
        )
        reasons.append("candidate3_passed_frozen_validation_gates")
    else:
        reasons.append("candidate2_correctness_baseline_retained")
    return PrimarySelectionDecision(
        review_candidate=review_candidate,
        sample_gate_passed=True,
        maximum_drawdown_gate_passed=drawdown_gate_passed,
        minimum_practical_benefit_gate_passed=benefit_gate_passed,
        clustered_improvement=improvement.point_estimate,
        clustered_improvement_confidence_interval=improvement.confidence_interval,
        minimum_practical_benefit=contract.minimum_practical_benefit,
        maximum_drawdown_noninferiority_tolerance=(
            contract.maximum_drawdown_noninferiority_tolerance
        ),
        promotion_status="manual_review_required",
        reasons=tuple(reasons),
        bootstrap_result_hash=improvement.result_hash,
        paired_samples_hash=improvement.paired_samples_hash,
        bootstrap_registry_hash=improvement.registry_hash,
        bootstrap_contract_hash=improvement.contract_hash,
        bootstrap_input_snapshot_hash=improvement.input_snapshot_hash,
        bootstrap_trading_calendar_hash=improvement.trading_calendar_hash,
        bootstrap_seed=improvement.seed,
        bootstrap_resamples=improvement.resamples,
        bootstrap_block_length=improvement.block_length,
    )


@dataclass(frozen=True)
class DevelopmentGateArtifact:
    registry_hash: str
    contract_hash: str
    input_snapshot_hash: str
    trading_calendar_hash: str
    coverage_numerator: int
    coverage_denominator: int
    complete_action_cycle_count: int
    independent_trading_day_count: int
    review_candidate: CandidateName | None
    sample_gate_passed: bool
    maximum_drawdown_gate_passed: bool
    minimum_practical_benefit_gate_passed: bool
    holdout_ready: bool
    bootstrap_result_hash: str
    paired_samples_hash: str
    candidate2_endpoint_evidence_hash: str
    candidate3_endpoint_evidence_hash: str
    walk_forward_boundaries_hash: str
    purged_samples_hash: str
    purged_action_cycle_ids: tuple[str, ...]

    @property
    def artifact_hash(self) -> str:
        return stable_contract_hash(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "etf_action_development_gate_v2",
            "registry_hash": self.registry_hash,
            "contract_hash": self.contract_hash,
            "input_snapshot_hash": self.input_snapshot_hash,
            "trading_calendar_hash": self.trading_calendar_hash,
            "coverage_numerator": self.coverage_numerator,
            "coverage_denominator": self.coverage_denominator,
            "complete_action_cycle_count": self.complete_action_cycle_count,
            "independent_trading_day_count": self.independent_trading_day_count,
            "review_candidate": (
                self.review_candidate.value
                if self.review_candidate is not None
                else None
            ),
            "sample_gate_passed": self.sample_gate_passed,
            "maximum_drawdown_gate_passed": self.maximum_drawdown_gate_passed,
            "minimum_practical_benefit_gate_passed": (
                self.minimum_practical_benefit_gate_passed
            ),
            "holdout_ready": self.holdout_ready,
            "bootstrap_result_hash": self.bootstrap_result_hash,
            "paired_samples_hash": self.paired_samples_hash,
            "candidate2_endpoint_evidence_hash": (
                self.candidate2_endpoint_evidence_hash
            ),
            "candidate3_endpoint_evidence_hash": (
                self.candidate3_endpoint_evidence_hash
            ),
            "walk_forward_boundaries_hash": self.walk_forward_boundaries_hash,
            "purged_samples_hash": self.purged_samples_hash,
            "purged_action_cycle_ids": list(self.purged_action_cycle_ids),
        }


def development_evidence_bundle_hash(
    *,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    endpoints: Sequence[CandidateEndpointResult],
    purged_samples: PurgedWalkForwardSamples,
) -> str:
    purged_samples.assert_integrity()
    return stable_contract_hash(
        {
            "schema_version": "etf_action_development_replay_evidence_bundle_v1",
            "registry_hash": registry.registry_hash,
            "validation_contract_hash": contract.contract_hash,
            "candidate_endpoint_evidence": sorted(
                (
                    endpoint.candidate.value,
                    endpoint.raw_evidence_hash,
                )
                for endpoint in endpoints
            ),
            "purged_samples_hash": purged_samples.purged_samples_hash,
        }
    )


def build_development_gate_artifact(
    *,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    endpoints: Sequence[CandidateEndpointResult],
    purged_samples: PurgedWalkForwardSamples,
) -> DevelopmentGateArtifact:
    purged_samples.assert_integrity()
    endpoint_by_candidate = {endpoint.candidate: endpoint for endpoint in endpoints}
    if len(endpoint_by_candidate) != len(endpoints):
        raise FrozenValidationError("development artifact has duplicate candidate endpoints")
    try:
        candidate2 = endpoint_by_candidate[CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS]
        candidate3 = endpoint_by_candidate[
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
        ]
    except KeyError as exc:
        raise FrozenValidationError(
            "development artifact is missing a required candidate endpoint"
        ) from exc
    _require_endpoint_candidate_binding(
        candidate2,
        expected_candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        registry=registry,
    )
    if not (
        purged_samples.contract_hash == contract.contract_hash
        and purged_samples.walk_forward_boundaries_hash
        == contract.walk_forward_boundaries_hash
    ):
        raise FrozenValidationError(
            "development purge evidence does not match the frozen split contract"
        )
    purge_calendar_hash = trading_calendar_snapshot_hash(
        purged_samples.trading_calendar
    )
    if not (
        candidate2.trading_calendar_hash
        == candidate3.trading_calendar_hash
        == purge_calendar_hash
    ):
        raise FrozenValidationError(
            "development purge evidence does not match the endpoint trading calendar"
        )
    window_by_action_cycle_id: dict[str, ActionOutcomeWindow] = {}
    for sample in purged_samples.all_samples:
        if sample.action_cycle_id in window_by_action_cycle_id:
            raise FrozenValidationError(
                "development purge evidence contains a duplicate action cycle"
            )
        window_by_action_cycle_id[sample.action_cycle_id] = sample
    kept_action_cycle_ids = {
        sample.action_cycle_id for sample in purged_samples.kept
    }
    final_holdout_start = contract.walk_forward_boundaries[-1]
    for endpoint in (candidate2, candidate3):
        for benefit in endpoint.raw_evidence.validation_benefits:
            window = window_by_action_cycle_id.get(benefit.action_cycle_id)
            if window is None:
                raise FrozenValidationError(
                    "development endpoint is absent from the sealed purge evidence"
                )
            if window.signal_trading_day != benefit.signal_trading_day:
                raise FrozenValidationError(
                    "development endpoint signal day does not match purge evidence"
                )
            if benefit.action_cycle_id not in kept_action_cycle_ids:
                raise FrozenValidationError(
                    "development endpoint contains a purged endpoint action cycle"
                )
            if benefit.signal_trading_day >= final_holdout_start:
                raise FrozenValidationError(
                    "development endpoint cannot consume the final holdout window"
                )
    selection = _selection_for_endpoints(
        candidate2,
        candidate3,
        registry=registry,
        contract=contract,
    )
    _require_endpoint_is_derived(candidate2, registry=registry, contract=contract)
    _require_endpoint_is_derived(candidate3, registry=registry, contract=contract)
    _require_endpoint_candidate_binding(
        candidate3,
        expected_candidate=(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
        ),
        registry=registry,
    )
    if selection.bootstrap_registry_hash != registry.registry_hash:
        raise FrozenValidationError(
            "development selection does not match the frozen candidate registry"
        )
    _require_selection_matches_endpoints(
        contract=contract,
        candidate2=candidate2,
        candidate3=candidate3,
        selection=selection,
    )
    if not (
        candidate2.coverage_numerator == candidate3.coverage_numerator
        and candidate2.coverage_denominator == candidate3.coverage_denominator
        and candidate2.action_cycle_count == candidate3.action_cycle_count
        and candidate2.independent_trading_day_count
        == candidate3.independent_trading_day_count
    ):
        raise FrozenValidationError(
            "development candidate sample and coverage evidence is not comparable"
        )
    holdout_ready = selection.sample_gate_passed and selection.review_candidate is not None
    return DevelopmentGateArtifact(
        registry_hash=registry.registry_hash,
        contract_hash=contract.contract_hash,
        input_snapshot_hash=candidate2.input_snapshot_hash,
        trading_calendar_hash=candidate2.trading_calendar_hash,
        coverage_numerator=candidate2.coverage_numerator,
        coverage_denominator=candidate2.coverage_denominator,
        complete_action_cycle_count=candidate2.action_cycle_count,
        independent_trading_day_count=candidate2.independent_trading_day_count,
        review_candidate=selection.review_candidate,
        sample_gate_passed=selection.sample_gate_passed,
        maximum_drawdown_gate_passed=selection.maximum_drawdown_gate_passed,
        minimum_practical_benefit_gate_passed=(
            selection.minimum_practical_benefit_gate_passed
        ),
        holdout_ready=holdout_ready,
        bootstrap_result_hash=selection.bootstrap_result_hash,
        paired_samples_hash=selection.paired_samples_hash,
        candidate2_endpoint_evidence_hash=candidate2.raw_evidence_hash,
        candidate3_endpoint_evidence_hash=candidate3.raw_evidence_hash,
        walk_forward_boundaries_hash=purged_samples.walk_forward_boundaries_hash,
        purged_samples_hash=purged_samples.purged_samples_hash,
        purged_action_cycle_ids=purged_samples.purged_action_cycle_ids,
    )


@dataclass(frozen=True)
class EvidenceDimensions:
    contract_compatibility: ContractCompatibility
    data_eligibility: DataEligibility
    execution_provenance: ExecutionProvenance

    def __post_init__(self) -> None:
        if not (
            isinstance(self.contract_compatibility, ContractCompatibility)
            and isinstance(self.data_eligibility, DataEligibility)
            and isinstance(self.execution_provenance, ExecutionProvenance)
        ):
            raise ValueError("registered evidence enum values are required")

    def as_dict(
        self,
        *,
        sample_sufficiency: SampleSufficiency,
        promotion_status: EvidencePromotionStatus,
    ) -> dict[str, str]:
        return {
            "contract_compatibility": self.contract_compatibility.value,
            "data_eligibility": self.data_eligibility.value,
            "execution_provenance": self.execution_provenance.value,
            "sample_sufficiency": sample_sufficiency.value,
            "promotion_status": promotion_status.value,
        }


@dataclass(frozen=True)
class SecondarySlice:
    candidate: CandidateName
    top_n: int
    horizon_trading_days: int
    value: float
    exit_reason: str | None = None

    def __post_init__(self) -> None:
        if self.top_n not in {3, 5, 10, 20}:
            raise ValueError("secondary Top-N slice is not registered")
        if self.horizon_trading_days not in {1, 3, 5, 10}:
            raise ValueError("secondary horizon is not registered")
        if self.top_n == 20 and self.horizon_trading_days == 10 and self.exit_reason is None:
            raise ValueError("the fixed primary endpoint cannot be relabeled as a secondary slice")
        if not _finite(self.value):
            raise ValueError("secondary slice value must be finite")

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.value,
            "top_n": self.top_n,
            "horizon_trading_days": self.horizon_trading_days,
            "exit_reason": self.exit_reason,
            "value": self.value,
            "role": "secondary",
        }


@dataclass(frozen=True)
class ValidationReport:
    contract: ValidationRunContract
    candidates: tuple[dict[str, Any], ...]
    selection: PrimarySelectionDecision
    secondary_slices: tuple[SecondarySlice, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_hash": self.contract.contract_hash,
            "primary_endpoint": PRIMARY_ENDPOINT,
            "candidates": list(self.candidates),
            "selection": {
                "review_candidate": (
                    self.selection.review_candidate.value
                    if self.selection.review_candidate is not None
                    else None
                ),
                "sample_gate_passed": self.selection.sample_gate_passed,
                "maximum_drawdown_gate_passed": (
                    self.selection.maximum_drawdown_gate_passed
                ),
                "minimum_practical_benefit_gate_passed": (
                    self.selection.minimum_practical_benefit_gate_passed
                ),
                "clustered_improvement": self.selection.clustered_improvement,
                "clustered_improvement_confidence_interval": list(
                    self.selection.clustered_improvement_confidence_interval
                ),
                "minimum_practical_benefit": (
                    self.selection.minimum_practical_benefit
                ),
                "maximum_drawdown_noninferiority_tolerance": (
                    self.selection.maximum_drawdown_noninferiority_tolerance
                ),
                "promotion_status": self.selection.promotion_status,
                "reasons": list(self.selection.reasons),
                "bootstrap_result_hash": self.selection.bootstrap_result_hash,
                "paired_samples_hash": self.selection.paired_samples_hash,
                "bootstrap_registry_hash": self.selection.bootstrap_registry_hash,
                "bootstrap_contract_hash": self.selection.bootstrap_contract_hash,
                "bootstrap_input_snapshot_hash": (
                    self.selection.bootstrap_input_snapshot_hash
                ),
                "bootstrap_trading_calendar_hash": (
                    self.selection.bootstrap_trading_calendar_hash
                ),
                "bootstrap_seed": self.selection.bootstrap_seed,
                "bootstrap_resamples": self.selection.bootstrap_resamples,
                "bootstrap_block_length": self.selection.bootstrap_block_length,
            },
            "secondary_slices": [item.as_dict() for item in self.secondary_slices],
            "auto_promoted": False,
            "research_only": True,
        }


def build_validation_report(
    *,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    endpoints: Sequence[CandidateEndpointResult],
    evidence_by_candidate: Mapping[CandidateName, EvidenceDimensions],
    secondary_slices: Sequence[SecondarySlice],
) -> ValidationReport:
    endpoint_by_candidate = {endpoint.candidate: endpoint for endpoint in endpoints}
    if len(endpoint_by_candidate) != len(endpoints):
        raise FrozenValidationError("duplicate candidate endpoint in validation report")
    required_candidates = {
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
    }
    if not required_candidates.issubset(endpoint_by_candidate):
        raise FrozenValidationError("validation report is missing a required candidate endpoint")
    if set(evidence_by_candidate) != set(endpoint_by_candidate):
        raise FrozenValidationError(
            "evidence dimensions must exactly match the reported candidate endpoints"
        )
    for endpoint in endpoints:
        _require_endpoint_is_derived(endpoint, registry=registry, contract=contract)
    selection = _selection_for_endpoints(
        endpoint_by_candidate[CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS],
        endpoint_by_candidate[
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
        ],
        registry=registry,
        contract=contract,
    )
    _require_selection_matches_endpoints(
        contract=contract,
        candidate2=endpoint_by_candidate[CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS],
        candidate3=endpoint_by_candidate[
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
        ],
        selection=selection,
    )
    if any(item.candidate not in endpoint_by_candidate for item in secondary_slices):
        raise FrozenValidationError("secondary slice candidate is absent from the report")
    rows: list[dict[str, Any]] = []
    for endpoint in endpoints:
        _require_primary_endpoint(endpoint)
        evidence = evidence_by_candidate.get(endpoint.candidate)
        if evidence is None:
            raise FrozenValidationError("every candidate report requires separate evidence dimensions")
        sample_gate_passed = _endpoint_sample_gate_passed(endpoint, contract=contract)
        sample_sufficiency = (
            SampleSufficiency.SUFFICIENT
            if sample_gate_passed
            else SampleSufficiency.SAMPLE_INSUFFICIENT
        )
        if not sample_gate_passed or not selection.sample_gate_passed:
            promotion_status = EvidencePromotionStatus.SAMPLE_INSUFFICIENT
        elif endpoint.candidate is selection.review_candidate:
            promotion_status = EvidencePromotionStatus.MANUAL_REVIEW_REQUIRED
        else:
            promotion_status = EvidencePromotionStatus.RESEARCH_ONLY
        rows.append(
            {
                "candidate": endpoint.candidate.value,
                "sample_gate": {
                    "observed_action_cycles": endpoint.action_cycle_count,
                    "observed_independent_days": endpoint.independent_trading_day_count,
                    "required_independent_days": contract.sample_gate_independent_days,
                    "observed_complete_action_cycles": endpoint.action_cycle_count,
                    "required_complete_action_cycles": (
                        contract.sample_gate_complete_action_cycles
                    ),
                    "coverage_numerator": endpoint.coverage_numerator,
                    "coverage_denominator": endpoint.coverage_denominator,
                    "coverage_ratio": endpoint.coverage_ratio,
                    "required_coverage_ratio": (
                        contract.sample_gate_minimum_coverage_ratio
                    ),
                    "passed": sample_gate_passed,
                },
                "confidence_interval": list(endpoint.confidence_interval),
                "development_mean_benefit": endpoint.development_mean_benefit,
                "validation_mean_benefit": endpoint.validation_mean_benefit,
                "validation_degradation": (
                    endpoint.validation_mean_benefit - endpoint.development_mean_benefit
                ),
                "maximum_drawdown": endpoint.maximum_drawdown,
                "opportunity_cost": endpoint.opportunity_cost,
                "evidence": evidence.as_dict(
                    sample_sufficiency=sample_sufficiency,
                    promotion_status=promotion_status,
                ),
                "role": "primary",
            }
        )
    return ValidationReport(
        contract=contract,
        candidates=tuple(rows),
        selection=selection,
        secondary_slices=tuple(secondary_slices),
    )


async def seal_validation_run(
    session: AsyncSession,
    *,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    sealed_at: datetime,
) -> EtfActionValidationRun:
    registry.assert_integrity()
    existing = await session.scalar(
        select(EtfActionValidationRun)
        .where(EtfActionValidationRun.run_key == contract.run_key)
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.candidate_registry_hash != registry.registry_hash
            or existing.validation_contract_hash != contract.contract_hash
        ):
            raise FrozenValidationError(
                "candidate registry or validation contract changed after the run was sealed"
            )
        return existing
    row = EtfActionValidationRun(
        run_key=contract.run_key,
        policy_version=contract.policy_version,
        candidate_registry_json=registry.as_dict(),
        candidate_registry_hash=registry.registry_hash,
        validation_contract_json=contract.as_dict(),
        validation_contract_hash=contract.contract_hash,
        sealed_at=sealed_at,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        concurrent = await session.scalar(
            select(EtfActionValidationRun)
            .where(EtfActionValidationRun.run_key == contract.run_key)
            .with_for_update()
        )
        if concurrent is None:
            raise
        if (
            concurrent.candidate_registry_hash != registry.registry_hash
            or concurrent.validation_contract_hash != contract.contract_hash
        ):
            raise FrozenValidationError(
                "concurrent validation seal used a different registry or contract"
            ) from exc
        return concurrent
    return row


async def mark_development_outcomes_calculated(
    session: AsyncSession,
    *,
    run_key: str,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    endpoints: Sequence[CandidateEndpointResult],
    purged_samples: PurgedWalkForwardSamples,
    calculated_at: datetime,
    replay_attestation: SealedReplayEvidenceAttestation | None = None,
) -> EtfActionValidationRun:
    if replay_attestation is None:
        raise FrozenValidationError(
            "development evidence requires a sealed replay artifact attestation"
        )
    replay_attestation.assert_integrity()
    expected_bundle_hash = development_evidence_bundle_hash(
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    if not (
        replay_attestation.replay_run_contract_hash
        == contract.development_replay_run_contract_hash
        and replay_attestation.input_snapshot_hash
        == contract.development_input_snapshot_hash
        and replay_attestation.registry_hash == registry.registry_hash
        and replay_attestation.evidence_bundle_hash == expected_bundle_hash
    ):
        raise FrozenValidationError(
            "sealed replay artifact attestation does not match development evidence"
        )
    row = await _locked_validation_run(session, run_key)
    registry.assert_integrity()
    _require_frozen_hashes(
        row,
        registry_hash=registry.registry_hash,
        contract_hash=contract.contract_hash,
    )
    if not (
        row.candidate_registry_json == registry.as_dict()
        and row.validation_contract_json == contract.as_dict()
    ):
        raise FrozenValidationError(
            "development raw evidence does not match the sealed registry and contract"
        )
    artifact = build_development_gate_artifact(
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    artifact_payload = artifact.as_dict()
    artifact_payload["replay_evidence_attestation_hash"] = (
        replay_attestation.attestation_hash
    )
    artifact_payload["replay_evidence_attestation"] = replay_attestation.as_dict()
    artifact_hash = stable_contract_hash(artifact_payload)
    if calculated_at < row.sealed_at:
        raise FrozenValidationError("outcomes cannot predate the frozen validation contract")
    if row.development_outcomes_calculated_at is None:
        result = await session.execute(
            update(EtfActionValidationRun)
            .where(
                EtfActionValidationRun.id == row.id,
                EtfActionValidationRun.candidate_registry_hash
                == registry.registry_hash,
                EtfActionValidationRun.validation_contract_hash
                == contract.contract_hash,
                EtfActionValidationRun.development_outcomes_calculated_at.is_(None),
                EtfActionValidationRun.development_gate_artifact_json.is_(None),
                EtfActionValidationRun.development_gate_artifact_hash.is_(None),
            )
            .values(
                development_gate_artifact_json=artifact_payload,
                development_gate_artifact_hash=artifact_hash,
                development_outcomes_calculated_at=calculated_at,
            )
            .execution_options(synchronize_session=False)
        )
        await session.refresh(row)
        if result.rowcount != 1 and not (
            row.development_gate_artifact_hash == artifact_hash
            and row.development_gate_artifact_json == artifact_payload
        ):
            raise FrozenValidationError(
                "development outcomes were concurrently sealed with a different gate artifact"
            )
    elif not (
        row.development_gate_artifact_hash == artifact_hash
        and row.development_gate_artifact_json == artifact_payload
    ):
        raise FrozenValidationError(
            "development outcomes were already sealed with a different gate artifact"
        )
    return row


async def consume_final_holdout(
    session: AsyncSession,
    *,
    run_key: str,
    registry_hash: str,
    contract_hash: str,
    input_snapshot_hash: str,
    gates_passed: bool,
    consumed_at: datetime,
) -> EtfActionValidationRun:
    row = await _locked_validation_run(session, run_key)
    _require_frozen_hashes(row, registry_hash=registry_hash, contract_hash=contract_hash)
    if row.development_outcomes_calculated_at is None:
        raise FrozenValidationError("development/validation outcomes are not complete")
    development_gate = _require_persisted_development_gate(row)
    if not development_gate["holdout_ready"]:
        raise SampleGateError("persisted development gate is not ready for final holdout")
    if gates_passed is not True:
        raise FrozenValidationError(
            "caller gate assertion disagrees with the persisted development gate"
        )
    _require_sha256("holdout input snapshot hash", input_snapshot_hash)
    contract_payload = row.validation_contract_json
    if not isinstance(contract_payload, dict):
        raise FrozenValidationError("sealed validation contract payload is invalid")
    development_snapshot_hash = contract_payload.get(
        "development_input_snapshot_hash"
    )
    final_holdout_snapshot_hash = contract_payload.get(
        "final_holdout_input_snapshot_hash"
    )
    if not isinstance(development_snapshot_hash, str) or not isinstance(
        final_holdout_snapshot_hash, str
    ):
        raise FrozenValidationError("sealed validation snapshot scopes are missing")
    _require_sha256("sealed development snapshot hash", development_snapshot_hash)
    _require_sha256("sealed final holdout snapshot hash", final_holdout_snapshot_hash)
    if development_snapshot_hash == final_holdout_snapshot_hash:
        raise FrozenValidationError(
            "development and final holdout snapshot scopes must be distinct"
        )
    if development_gate.get("input_snapshot_hash") != development_snapshot_hash:
        raise FrozenValidationError(
            "persisted development evidence does not match its pre-registered snapshot"
        )
    if input_snapshot_hash != final_holdout_snapshot_hash:
        raise FrozenValidationError(
            "final holdout input does not match the pre-registered holdout snapshot"
        )
    if row.holdout_first_consumed_at is not None:
        raise HoldoutConsumedError("final holdout was already consumed for this frozen run")
    if consumed_at < row.development_outcomes_calculated_at:
        raise FrozenValidationError("holdout consumption cannot predate validation outcomes")
    result = await session.execute(
        update(EtfActionValidationRun)
        .where(
            EtfActionValidationRun.id == row.id,
            EtfActionValidationRun.candidate_registry_hash == registry_hash,
            EtfActionValidationRun.validation_contract_hash == contract_hash,
            EtfActionValidationRun.holdout_first_consumed_at.is_(None),
        )
        .values(
            holdout_first_consumed_at=consumed_at,
            holdout_input_snapshot_hash=input_snapshot_hash,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HoldoutConsumedError("final holdout was already consumed concurrently")
    await session.refresh(row)
    return row


async def _locked_validation_run(
    session: AsyncSession,
    run_key: str,
) -> EtfActionValidationRun:
    row = await session.scalar(
        select(EtfActionValidationRun)
        .where(EtfActionValidationRun.run_key == run_key)
        .with_for_update()
    )
    if row is None:
        raise FrozenValidationError("validation run must be sealed before outcomes are calculated")
    return row


def _require_frozen_hashes(
    row: EtfActionValidationRun,
    *,
    registry_hash: str,
    contract_hash: str,
) -> None:
    if row.candidate_registry_hash != registry_hash:
        raise FrozenValidationError("candidate registry changed after validation was sealed")
    if row.validation_contract_hash != contract_hash:
        raise FrozenValidationError("validation contract changed after validation was sealed")


def _require_persisted_development_gate(
    row: EtfActionValidationRun,
) -> dict[str, Any]:
    payload = row.development_gate_artifact_json
    artifact_hash = row.development_gate_artifact_hash
    if not isinstance(payload, dict) or artifact_hash is None:
        raise FrozenValidationError("development gate artifact was not persisted atomically")
    _require_sha256("development gate artifact hash", artifact_hash)
    if stable_contract_hash(payload) != artifact_hash:
        raise FrozenValidationError("persisted development gate artifact hash is invalid")
    if not (
        payload.get("registry_hash") == row.candidate_registry_hash
        and payload.get("contract_hash") == row.validation_contract_hash
        and isinstance(payload.get("holdout_ready"), bool)
        and isinstance(payload.get("coverage_denominator"), int)
        and payload["coverage_denominator"] > 0
    ):
        raise FrozenValidationError(
            "persisted development gate artifact does not match the sealed run"
        )
    for name in (
        "replay_evidence_attestation_hash",
        "candidate2_endpoint_evidence_hash",
        "candidate3_endpoint_evidence_hash",
        "walk_forward_boundaries_hash",
        "purged_samples_hash",
    ):
        value = payload.get(name)
        if not isinstance(value, str):
            raise FrozenValidationError(
                "persisted development gate artifact is missing derived evidence"
            )
        _require_sha256(f"development gate {name}", value)
    contract_payload = row.validation_contract_json
    if not isinstance(contract_payload, dict) or not (
        payload.get("walk_forward_boundaries_hash")
        == contract_payload.get("walk_forward_boundaries_hash")
        and payload.get("input_snapshot_hash")
        == contract_payload.get("development_input_snapshot_hash")
    ):
        raise FrozenValidationError(
            "persisted development gate split evidence does not match the sealed contract"
        )
    replay_attestation = payload.get("replay_evidence_attestation")
    if not isinstance(replay_attestation, dict):
        raise FrozenValidationError(
            "persisted development gate is missing replay artifact lineage"
        )
    attestation_hash = replay_attestation.get("attestation_hash")
    attestation_payload = {
        key: value
        for key, value in replay_attestation.items()
        if key != "attestation_hash"
    }
    if not (
        attestation_hash == payload.get("replay_evidence_attestation_hash")
        and stable_contract_hash(attestation_payload) == attestation_hash
        and replay_attestation.get("registry_hash") == row.candidate_registry_hash
        and replay_attestation.get("input_snapshot_hash")
        == contract_payload.get("development_input_snapshot_hash")
        and replay_attestation.get("replay_run_contract_hash")
        == contract_payload.get("development_replay_run_contract_hash")
    ):
        raise FrozenValidationError(
            "persisted replay artifact lineage does not match the sealed run"
        )
    return payload


def _canonical_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CandidateRegistryError("candidate parameters must be finite JSON values") from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise CandidateRegistryError("candidate parameters must be an object")
    return decoded


def _candidate_attestation_fingerprint(
    *,
    name: CandidateName,
    policy_version: str,
    parameter_hash: str,
    promotion_eligible: bool,
) -> str:
    return stable_contract_hash(
        {
            "kind": "frozen_candidate_factory_attestation_v1",
            "name": name.value,
            "policy_version": policy_version,
            "parameter_hash": parameter_hash,
            "promotion_eligible": promotion_eligible,
        }
    )


def _registry_attestation_fingerprint(registry_hash: str) -> str:
    return stable_contract_hash(
        {
            "kind": "frozen_candidate_registry_factory_attestation_v1",
            "registry_hash": registry_hash,
        }
    )


def _reject_search_space(value: Any, *, path: str = "parameters") -> None:
    if isinstance(value, list):
        field_name = path.rsplit(".", 1)[-1].lower()
        if len(value) > 1 and all(_finite(child) for child in value):
            raise CandidateRegistryError(
                f"multi-value numeric grid/search parameter is forbidden at {path}"
            )
        if field_name == "k" or any(
            marker in field_name
            for marker in (
                "atr",
                "grid",
                "hysteresis",
                "search",
                "threshold",
                "variant",
                "warmup",
            )
        ):
            raise CandidateRegistryError(f"generated/grid/search values are forbidden at {path}")
        for index, child in enumerate(value):
            _reject_search_space(child, path=f"{path}[{index}]")
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if any(marker in lowered for marker in ("grid", "search", "generated", "variants")):
                raise CandidateRegistryError(
                    f"generated/grid/search parameter is forbidden at {path}.{key}"
                )
            _reject_search_space(child, path=f"{path}.{key}")


def _primary_benefit_map(
    samples: Sequence[ActionCycleBenefit],
    *,
    expected_candidate: CandidateName,
    registry: FrozenCandidateRegistry,
) -> dict[str, tuple[date, str, float]]:
    result: dict[str, tuple[date, str, float]] = {}
    seen_action_cycle_ids: set[str] = set()
    frozen_candidate = registry.by_name(expected_candidate)
    for sample in samples:
        if sample.candidate is not expected_candidate:
            raise FrozenValidationError("bootstrap received a changed candidate definition")
        if sample.registry_hash != registry.registry_hash:
            raise FrozenValidationError(
                "action-cycle sample does not match the frozen candidate registry"
            )
        if sample.candidate_parameter_hash != frozen_candidate.parameter_hash:
            raise FrozenValidationError(
                "action-cycle candidate parameter hash does not match the frozen candidate"
            )
        if sample.top_n != 20 or sample.horizon_trading_days != 10:
            raise FrozenValidationError("bootstrap accepts only the fixed Top20/10-day endpoint")
        if sample.action_cycle_id in seen_action_cycle_ids:
            raise FrozenValidationError(
                "one action cycle must be counted once, independently of notification attempts"
            )
        seen_action_cycle_ids.add(sample.action_cycle_id)
        if sample.experimental_unit_id in result:
            raise FrozenValidationError(
                "one experimental unit must be counted once per candidate"
            )
        result[sample.experimental_unit_id] = (
            sample.signal_trading_day,
            sample.action_cycle_id,
            sample.tax_fee_adjusted_benefit,
        )
    return result


def trading_calendar_snapshot_hash(trading_days: Sequence[date]) -> str:
    calendar = tuple(trading_days)
    if not calendar:
        raise FrozenValidationError("trading calendar cannot be empty")
    if calendar != tuple(sorted(set(calendar))):
        raise FrozenValidationError("trading calendar must be unique and chronological")
    return stable_contract_hash(
        {
            "schema_version": "etf_action_trading_calendar_v1",
            "trading_days": [day.isoformat() for day in calendar],
        }
    )


def _derive_endpoint_statistics(
    *,
    candidate: CandidateName,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
    raw_evidence: CandidateEndpointEvidence,
) -> dict[str, Any]:
    registry.assert_integrity()
    if raw_evidence.input_snapshot_hash != contract.development_input_snapshot_hash:
        raise FrozenValidationError(
            "raw endpoint evidence does not match the pre-registered development snapshot"
        )
    completed = _primary_benefit_map(
        raw_evidence.validation_benefits,
        expected_candidate=candidate,
        registry=registry,
    )
    completed_ids = set(completed)
    eligible_ids = set(raw_evidence.eligible_experimental_unit_ids)
    if not completed_ids.issubset(eligible_ids):
        raise FrozenValidationError(
            "raw endpoint evidence contains a completed cycle outside the eligible population"
        )
    interval = _clustered_endpoint_interval(
        raw_evidence.validation_benefits,
        trading_calendar=raw_evidence.trading_calendar,
        contract=contract,
    )
    peak = raw_evidence.equity_curve[0]
    maximum_drawdown = 0.0
    for value in raw_evidence.equity_curve:
        peak = max(peak, value)
        maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)
    validation_values = [
        sample.tax_fee_adjusted_benefit
        for sample in raw_evidence.validation_benefits
    ]
    return {
        "development_mean_benefit": fmean(raw_evidence.development_benefits),
        "validation_mean_benefit": fmean(validation_values),
        "maximum_drawdown": maximum_drawdown,
        "confidence_interval": interval,
        "action_cycle_count": len(raw_evidence.validation_benefits),
        "independent_trading_day_count": len(
            {sample.signal_trading_day for sample in raw_evidence.validation_benefits}
        ),
        "coverage_numerator": len(completed_ids),
        "coverage_denominator": len(eligible_ids),
        "opportunity_cost": fmean(raw_evidence.opportunity_costs),
    }


def _clustered_endpoint_interval(
    samples: Sequence[ActionCycleBenefit],
    *,
    trading_calendar: Sequence[date],
    contract: ValidationRunContract,
) -> tuple[float, float]:
    calendar = tuple(trading_calendar)
    trading_calendar_snapshot_hash(calendar)
    if contract.bootstrap_block_length > len(calendar):
        raise FrozenValidationError("bootstrap block length exceeds the trading calendar")
    by_day: dict[date, list[float]] = defaultdict(list)
    for sample in samples:
        if sample.signal_trading_day not in calendar:
            raise FrozenValidationError(
                "signal trading day is absent from the frozen trading calendar"
            )
        by_day[sample.signal_trading_day].append(sample.tax_fee_adjusted_benefit)
    generator = random.Random(contract.bootstrap_seed)
    resampled_means: list[float] = []
    attempts = 0
    maximum_attempts = contract.bootstrap_resamples * 100
    while len(resampled_means) < contract.bootstrap_resamples:
        attempts += 1
        if attempts > maximum_attempts:
            raise FrozenValidationError(
                "full-calendar bootstrap could not draw a non-empty action sample"
            )
        sampled: list[float] = []
        sampled_calendar_days = 0
        while sampled_calendar_days < len(calendar):
            start = generator.randrange(len(calendar))
            block_size = min(
                contract.bootstrap_block_length,
                len(calendar) - sampled_calendar_days,
            )
            for offset in range(block_size):
                day = calendar[(start + offset) % len(calendar)]
                sampled.extend(by_day.get(day, ()))
            sampled_calendar_days += block_size
        if sampled:
            resampled_means.append(fmean(sampled))
    return (
        _percentile(resampled_means, 0.025),
        _percentile(resampled_means, 0.975),
    )


def _require_endpoint_is_derived(
    endpoint: CandidateEndpointResult,
    *,
    registry: FrozenCandidateRegistry,
    contract: ValidationRunContract,
) -> None:
    expected = _derive_endpoint_statistics(
        candidate=endpoint.candidate,
        registry=registry,
        contract=contract,
        raw_evidence=endpoint.raw_evidence,
    )
    consistent = (
        endpoint.registry_hash == registry.registry_hash
        and endpoint.candidate_parameter_hash
        == registry.by_name(endpoint.candidate).parameter_hash
        and endpoint.derivation_contract_hash == contract.contract_hash
        and endpoint.development_mean_benefit
        == expected["development_mean_benefit"]
        and endpoint.validation_mean_benefit
        == expected["validation_mean_benefit"]
        and endpoint.maximum_drawdown == expected["maximum_drawdown"]
        and endpoint.confidence_interval == expected["confidence_interval"]
        and endpoint.action_cycle_count == expected["action_cycle_count"]
        and endpoint.independent_trading_day_count
        == expected["independent_trading_day_count"]
        and endpoint.coverage_numerator == expected["coverage_numerator"]
        and endpoint.coverage_denominator == expected["coverage_denominator"]
        and endpoint.input_snapshot_hash == endpoint.raw_evidence.input_snapshot_hash
        and endpoint.trading_calendar_hash
        == trading_calendar_snapshot_hash(endpoint.raw_evidence.trading_calendar)
        and endpoint.opportunity_cost == expected["opportunity_cost"]
    )
    if not consistent:
        raise FrozenValidationError(
            "derived endpoint does not match its raw endpoint evidence"
        )


def _require_primary_endpoint(result: CandidateEndpointResult) -> None:
    if not (
        result.top_n == 20
        and result.horizon_trading_days == 10
        and result.tax_fee_adjusted is True
        and result.endpoint == "mean_action_cycle_benefit"
    ):
        raise FrozenValidationError("selection may use only the fixed primary endpoint")


def _require_endpoint_candidate_binding(
    endpoint: CandidateEndpointResult,
    *,
    expected_candidate: CandidateName,
    registry: FrozenCandidateRegistry,
) -> None:
    if endpoint.candidate is not expected_candidate:
        raise FrozenValidationError("endpoint candidate identity is not pre-registered")
    if endpoint.registry_hash != registry.registry_hash:
        raise FrozenValidationError(
            "endpoint does not match the frozen candidate registry"
        )
    if (
        endpoint.candidate_parameter_hash
        != registry.by_name(expected_candidate).parameter_hash
    ):
        raise FrozenValidationError(
            "endpoint candidate parameter hash does not match the frozen candidate"
        )


def _endpoint_sample_gate_passed(
    endpoint: CandidateEndpointResult,
    *,
    contract: ValidationRunContract,
) -> bool:
    return (
        endpoint.independent_trading_day_count
        >= contract.sample_gate_independent_days
        and endpoint.action_cycle_count
        >= contract.sample_gate_complete_action_cycles
        and endpoint.coverage_ratio
        >= contract.sample_gate_minimum_coverage_ratio
    )


def _require_selection_matches_endpoints(
    *,
    contract: ValidationRunContract,
    candidate2: CandidateEndpointResult,
    candidate3: CandidateEndpointResult,
    selection: PrimarySelectionDecision,
) -> None:
    expected_improvement = (
        candidate3.validation_mean_benefit - candidate2.validation_mean_benefit
    )
    sample_gate_passed = _endpoint_sample_gate_passed(
        candidate2, contract=contract
    ) and _endpoint_sample_gate_passed(candidate3, contract=contract)
    drawdown_gate_passed = candidate3.maximum_drawdown <= (
        candidate2.maximum_drawdown
        + contract.maximum_drawdown_noninferiority_tolerance
    )
    benefit_gate_passed = (
        selection.clustered_improvement_confidence_interval[0]
        > contract.minimum_practical_benefit
    )
    if sample_gate_passed:
        expected_candidate = (
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
            if drawdown_gate_passed and benefit_gate_passed
            else CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS
        )
        expected_promotion_status = "manual_review_required"
    else:
        expected_candidate = None
        expected_promotion_status = "sample_insufficient"
    consistent = (
        math.isclose(
            selection.clustered_improvement,
            expected_improvement,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and selection.sample_gate_passed is sample_gate_passed
        and selection.maximum_drawdown_gate_passed is drawdown_gate_passed
        and selection.minimum_practical_benefit_gate_passed is benefit_gate_passed
        and selection.review_candidate is expected_candidate
        and selection.promotion_status == expected_promotion_status
        and selection.minimum_practical_benefit
        == contract.minimum_practical_benefit
        and selection.maximum_drawdown_noninferiority_tolerance
        == contract.maximum_drawdown_noninferiority_tolerance
        and selection.bootstrap_registry_hash
        == candidate2.registry_hash
        == candidate3.registry_hash
        and selection.bootstrap_contract_hash == contract.contract_hash
        and selection.bootstrap_input_snapshot_hash
        == candidate2.input_snapshot_hash
        == candidate3.input_snapshot_hash
        and selection.bootstrap_trading_calendar_hash
        == candidate2.trading_calendar_hash
        == candidate3.trading_calendar_hash
        and selection.bootstrap_seed == contract.bootstrap_seed
        and selection.bootstrap_resamples == contract.bootstrap_resamples
        and selection.bootstrap_block_length == contract.bootstrap_block_length
        and selection.auto_promoted is False
    )
    if not consistent:
        raise FrozenValidationError("selection does not match the reported endpoints")


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _require_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise FrozenValidationError(f"{name} must be a lowercase SHA-256 hex digest")
