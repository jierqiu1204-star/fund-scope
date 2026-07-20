from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_experiment import (
    PRIMARY_ENDPOINT,
    ChronologicalSplit,
    FactorExperimentContractError,
    FactorExperimentManifest,
)

SplitRole = Literal["development", "validation", "holdout", "outside"]


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_dates: tuple[date, ...]
    test_dates: tuple[date, ...]
    purged_dates: tuple[date, ...]
    embargoed_dates: tuple[date, ...]


@dataclass(frozen=True)
class BootstrapInterval:
    estimate: float
    lower: float
    upper: float
    confidence: float
    seed: int
    resamples: int
    block_sessions: int


@dataclass(frozen=True)
class PromotionMetrics:
    endpoint: str
    adjusted_primary_lower_bound: float
    stable_fold_ratio: float
    stable_regime_ratio: float
    common_support_coverage: float
    turnover_deterioration: float
    drawdown_deterioration: float
    maximum_concentration: float
    exclusion_rate: float


@dataclass(frozen=True)
class PromotionDecision:
    state: Literal["eligible_for_v4_proposal", "retain_current_ranking"]
    passed: bool
    failed_gates: tuple[str, ...]
    endpoint: str


@dataclass(frozen=True)
class FrozenHoldoutAuthorization:
    manifest_hash: str
    code_version: str
    candidate_ids: tuple[str, ...]
    promotion_gates_hash: str
    frozen_non_holdout_evidence_hash: str
    authorization_hash: str


def split_role(session_date: date, split: ChronologicalSplit) -> SplitRole:
    if split.development_start <= session_date <= split.development_end:
        return "development"
    if split.validation_start <= session_date <= split.validation_end:
        return "validation"
    if split.holdout_start <= session_date <= split.holdout_end:
        return "holdout"
    return "outside"


def expanding_walk_forward_folds(
    session_dates: Sequence[date],
    split: ChronologicalSplit,
    *,
    fold_sessions: int,
) -> tuple[WalkForwardFold, ...]:
    if fold_sessions < 1:
        raise ValueError("fold_sessions must be positive")
    dates = tuple(sorted(set(session_dates)))
    validation = tuple(item for item in dates if split_role(item, split) == "validation")
    folds: list[WalkForwardFold] = []
    for start in range(0, len(validation), fold_sessions):
        test_dates = validation[start : start + fold_sessions]
        if len(test_dates) < fold_sessions:
            break
        test_start_index = dates.index(test_dates[0])
        purge_start = max(0, test_start_index - split.purge_horizon_sessions)
        embargo_start = max(0, purge_start - split.embargo_sessions)
        train_dates = tuple(
            item
            for item in dates[:embargo_start]
            if split_role(item, split) in {"development", "validation"}
        )
        if not train_dates:
            continue
        folds.append(
            WalkForwardFold(
                fold_id=len(folds) + 1,
                train_dates=train_dates,
                test_dates=test_dates,
                purged_dates=dates[purge_start:test_start_index],
                embargoed_dates=dates[embargo_start:purge_start],
            )
        )
    return tuple(folds)


def moving_block_bootstrap_interval(
    values: Sequence[float],
    *,
    block_sessions: int,
    resamples: int = 2_000,
    confidence: float = 0.95,
    seed: int = 20260719,
) -> BootstrapInterval:
    finite = tuple(float(value) for value in values if math.isfinite(value))
    if not finite:
        raise ValueError("bootstrap requires finite observations")
    if not 1 <= block_sessions <= len(finite):
        raise ValueError("block_sessions must fit the observation window")
    if resamples < 100:
        raise ValueError("at least 100 resamples are required")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    rng = random.Random(seed)
    sample_means: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        while len(sample) < len(finite):
            start = rng.randrange(len(finite))
            sample.extend(
                finite[(start + offset) % len(finite)]
                for offset in range(block_sessions)
            )
        sample_means.append(sum(sample[: len(finite)]) / len(finite))
    sample_means.sort()
    tail = (1.0 - confidence) / 2.0
    lower_index = max(0, int(tail * resamples))
    upper_index = min(resamples - 1, int((1.0 - tail) * resamples) - 1)
    return BootstrapInterval(
        estimate=sum(finite) / len(finite),
        lower=sample_means[lower_index],
        upper=sample_means[upper_index],
        confidence=confidence,
        seed=seed,
        resamples=resamples,
        block_sessions=block_sessions,
    )


def moving_block_bootstrap_positive_p_value(
    values: Sequence[float],
    *,
    block_sessions: int,
    resamples: int = 2_000,
    seed: int = 20260719,
) -> float:
    finite = tuple(float(value) for value in values if math.isfinite(value))
    if not finite:
        raise ValueError("bootstrap requires finite observations")
    if not 1 <= block_sessions <= len(finite):
        raise ValueError("block_sessions must fit the observation window")
    if resamples < 100:
        raise ValueError("at least 100 resamples are required")
    observed = sum(finite) / len(finite)
    centered = tuple(value - observed for value in finite)
    rng = random.Random(seed)
    at_least_observed = 0
    for _ in range(resamples):
        sample: list[float] = []
        while len(sample) < len(centered):
            start = rng.randrange(len(centered))
            sample.extend(
                centered[(start + offset) % len(centered)]
                for offset in range(block_sessions)
            )
        bootstrap_mean = sum(sample[: len(centered)]) / len(centered)
        if bootstrap_mean >= observed:
            at_least_observed += 1
    return (at_least_observed + 1) / (resamples + 1)


def holm_bonferroni(p_values: Sequence[float]) -> tuple[float, ...]:
    if not 1 <= len(p_values) <= 3:
        raise ValueError("one to three primary comparisons are required")
    if any(not 0 <= value <= 1 for value in p_values):
        raise ValueError("p-values must be between zero and one")
    ordered = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted = [0.0] * len(ordered)
    running = 0.0
    total = len(ordered)
    for position, (original_index, value) in enumerate(ordered):
        running = max(running, min(1.0, (total - position) * value))
        adjusted[original_index] = running
    return tuple(adjusted)


def evaluate_promotion(
    manifest: FactorExperimentManifest,
    metrics: PromotionMetrics,
) -> PromotionDecision:
    manifest.validate()
    failures: list[str] = []
    gates = manifest.promotion_gates
    if metrics.endpoint != PRIMARY_ENDPOINT:
        failures.append("primary_endpoint")
    comparisons = (
        (
            "adjusted_primary_lower_bound",
            metrics.adjusted_primary_lower_bound
            >= gates.adjusted_primary_lower_bound_min,
        ),
        ("fold_stability", metrics.stable_fold_ratio >= gates.minimum_stable_fold_ratio),
        (
            "regime_stability",
            metrics.stable_regime_ratio >= gates.minimum_stable_fold_ratio,
        ),
        (
            "common_support_coverage",
            metrics.common_support_coverage >= gates.minimum_common_support_coverage,
        ),
        (
            "turnover",
            metrics.turnover_deterioration <= gates.maximum_turnover_deterioration,
        ),
        (
            "drawdown",
            metrics.drawdown_deterioration <= gates.maximum_drawdown_deterioration,
        ),
        (
            "concentration",
            metrics.maximum_concentration <= gates.maximum_concentration,
        ),
        ("exclusions", metrics.exclusion_rate <= gates.maximum_exclusion_rate),
    )
    failures.extend(name for name, passed in comparisons if not passed)
    passed = not failures
    return PromotionDecision(
        state="eligible_for_v4_proposal" if passed else "retain_current_ranking",
        passed=passed,
        failed_gates=tuple(failures),
        endpoint=PRIMARY_ENDPOINT,
    )


def freeze_holdout_authorization(
    manifest: FactorExperimentManifest,
    *,
    frozen_non_holdout_evidence_hash: str,
) -> FrozenHoldoutAuthorization:
    manifest.validate()
    if not frozen_non_holdout_evidence_hash:
        raise FactorExperimentContractError("non-holdout evidence hash is required")
    payload = {
        "manifest_hash": manifest.manifest_hash,
        "code_version": manifest.code_version,
        "candidate_ids": tuple(item.candidate_id for item in manifest.candidates),
        "promotion_gates": asdict(manifest.promotion_gates),
        "frozen_non_holdout_evidence_hash": frozen_non_holdout_evidence_hash,
    }
    return FrozenHoldoutAuthorization(
        manifest_hash=manifest.manifest_hash,
        code_version=manifest.code_version,
        candidate_ids=payload["candidate_ids"],
        promotion_gates_hash=stable_contract_hash(payload["promotion_gates"]),
        frozen_non_holdout_evidence_hash=frozen_non_holdout_evidence_hash,
        authorization_hash=stable_contract_hash(payload),
    )


def verify_holdout_authorization(
    authorization: FrozenHoldoutAuthorization,
    manifest: FactorExperimentManifest,
    *,
    frozen_non_holdout_evidence_hash: str,
) -> None:
    expected = freeze_holdout_authorization(
        manifest,
        frozen_non_holdout_evidence_hash=frozen_non_holdout_evidence_hash,
    )
    if expected != authorization:
        raise FactorExperimentContractError("holdout inputs changed after freeze")
