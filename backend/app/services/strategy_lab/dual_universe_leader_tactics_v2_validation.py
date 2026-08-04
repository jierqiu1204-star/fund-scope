"""Pre-registered economic and source-label validation for V2 research."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2_CANDIDATE_IDS,
    V2_SOURCE_REGISTRY,
    V2ScreenResult,
    validate_runtime_contract,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_availability import (
    validate_v2_unavailable_reason,
)

PRIMARY_HORIZON = 5
PURGE_SESSIONS = 10
EMBARGO_SESSIONS = 10
COST_BPS_PER_SIDE = 10.0  # 5 bps fee + 5 bps slippage
MIN_PIT_SESSIONS = 252
MIN_PRIMARY_DATES = 40
MIN_WALK_FORWARD_FOLDS = 3
MAX_DRAWDOWN_DEGRADATION = 0.02


@dataclass(frozen=True)
class V2SourceLabel:
    article_id: str
    asset_code: str | None
    label_date: date | None
    theme: str | None
    label_kind: str
    observability: str
    evidence_hash: str


@dataclass(frozen=True)
class V2PairedOutcome:
    signal_date: date
    candidate_gross_return: float
    benchmark_gross_return: float
    candidate_net_return: float
    benchmark_net_return: float
    net_excess_return: float
    cost_drag: float


@dataclass(frozen=True)
class V2PromotionEvidence:
    candidate_id: str
    factual_pit_sessions: int
    non_overlapping_primary_dates: int
    walk_forward_folds: int
    bootstrap_lower_bound_95_holm: float | None
    max_drawdown: float | None
    baseline_max_drawdown: float | None
    coverage: float
    non_finite_exclusions: int
    raw_price_violations: int
    clone_violations: int
    holdout_status: str
    holdout_access_count: int
    turnover: float | None
    concentration: float | None
    regime_dependence: float | None
    purge_sessions: int = PURGE_SESSIONS
    embargo_sessions: int = EMBARGO_SESSIONS
    holm_adjusted: bool = True
    primary_endpoint: str = "five_session_paired_net_excess"


def source_label(
    *,
    article_id: str,
    asset_code: str | None,
    label_date: date | None,
    theme: str | None,
    label_kind: str,
    observability: str,
    evidence_hash: str,
) -> V2SourceLabel:
    """Create an explicit label; callers cannot turn unmentioned assets negative."""

    if label_kind not in {"positive", "core", "formula", "unavailable"}:
        raise ValueError("source label kind is unsupported")
    if observability not in {"observed", "unresolved", "unavailable"}:
        raise ValueError("source label observability is unsupported")
    if observability == "observed" and not asset_code:
        raise ValueError("observed source labels require an asset identity")
    if len(evidence_hash) != 64:
        raise ValueError("source label evidence hash is required")
    return V2SourceLabel(
        article_id=article_id,
        asset_code=asset_code,
        label_date=label_date,
        theme=theme,
        label_kind=label_kind,
        observability=observability,
        evidence_hash=evidence_hash,
    )


def _net_return(gross_return: float, *, cost_bps_per_side: float) -> float:
    if not math.isfinite(gross_return) or gross_return <= -1.0:
        raise ValueError("gross return is not finite")
    cost = cost_bps_per_side / 10_000.0
    if not 0 <= cost < 1:
        raise ValueError("cost bps is invalid")
    return (1.0 + gross_return) * (1.0 - cost) ** 2 - 1.0


def paired_five_session_net_excess(
    candidate_returns: Mapping[date, float],
    benchmark_returns: Mapping[date, float],
    *,
    cost_bps_per_side: float = COST_BPS_PER_SIDE,
) -> tuple[V2PairedOutcome, ...]:
    """Compute common-support, cost-adjusted paired outcomes only."""

    outcomes: list[V2PairedOutcome] = []
    for signal_date in sorted(set(candidate_returns) & set(benchmark_returns)):
        candidate_gross = float(candidate_returns[signal_date])
        benchmark_gross = float(benchmark_returns[signal_date])
        candidate_net = _net_return(candidate_gross, cost_bps_per_side=cost_bps_per_side)
        benchmark_net = _net_return(benchmark_gross, cost_bps_per_side=cost_bps_per_side)
        outcomes.append(
            V2PairedOutcome(
                signal_date=signal_date,
                candidate_gross_return=candidate_gross,
                benchmark_gross_return=benchmark_gross,
                candidate_net_return=candidate_net,
                benchmark_net_return=benchmark_net,
                net_excess_return=candidate_net - benchmark_net,
                cost_drag=(candidate_gross - candidate_net),
            )
        )
    return tuple(outcomes)


def validate_v2_promotion_evidence(evidence: V2PromotionEvidence) -> tuple[str, ...]:
    """Return all failed hard gates; no auxiliary metric can replace the primary."""

    validate_runtime_contract(formula_ids=V2_CANDIDATE_IDS)
    failures: list[str] = []
    if evidence.candidate_id not in V2_CANDIDATE_IDS:
        failures.append("candidate_identity_not_frozen")
    if evidence.primary_endpoint != "five_session_paired_net_excess":
        failures.append("primary_endpoint_substituted")
    if evidence.purge_sessions != PURGE_SESSIONS:
        failures.append("purge_contract_incompatible")
    if evidence.embargo_sessions != EMBARGO_SESSIONS:
        failures.append("embargo_contract_incompatible")
    if evidence.holm_adjusted is not True:
        failures.append("holm_adjustment_missing")
    if evidence.factual_pit_sessions < MIN_PIT_SESSIONS:
        failures.append("insufficient_factual_pit_sessions")
    if evidence.non_overlapping_primary_dates < MIN_PRIMARY_DATES:
        failures.append("insufficient_non_overlapping_primary_dates")
    if evidence.walk_forward_folds < MIN_WALK_FORWARD_FOLDS:
        failures.append("insufficient_walk_forward_folds")
    lower = evidence.bootstrap_lower_bound_95_holm
    if lower is None or not math.isfinite(lower) or lower <= 0:
        failures.append("primary_bootstrap_lower_bound_not_above_zero")
    if evidence.max_drawdown is None or evidence.baseline_max_drawdown is None:
        failures.append("missing_drawdown_evidence")
    elif (
        abs(evidence.max_drawdown) - abs(evidence.baseline_max_drawdown) > MAX_DRAWDOWN_DEGRADATION
    ):
        failures.append("drawdown_degradation_exceeds_two_percent")
    if evidence.coverage < 0.95:
        failures.append("coverage_below_95_percent")
    if evidence.non_finite_exclusions:
        failures.append("non_finite_input_violations")
    if evidence.raw_price_violations:
        failures.append("raw_price_violations")
    if evidence.clone_violations:
        failures.append("clone_policy_violations")
    if evidence.holdout_status != "success" or evidence.holdout_access_count != 1:
        failures.append("holdout_not_successfully_used_once")
    if (
        evidence.turnover is None
        or evidence.concentration is None
        or evidence.regime_dependence is None
    ):
        failures.append("missing_stability_diagnostics")
    for failure in failures:
        validate_v2_unavailable_reason(failure)
    return tuple(failures)


@dataclass(frozen=True)
class V2LockedCaseEvidence:
    case_date: date
    expected_codes: tuple[str, ...]
    observed_codes: tuple[str, ...]
    status: str
    source_registry_hash: str
    data_available: bool


def evaluate_locked_case(
    result: V2ScreenResult,
    *,
    data_available: bool,
) -> V2LockedCaseEvidence:
    """Evaluate the pre-registered case only after ordinary screening."""

    expected = tuple(sorted(V2_SOURCE_REGISTRY.locked_case_assets))
    if result.universe != "ashare":
        status = "locked_case_wrong_universe"
        observed: tuple[str, ...] = ()
    elif result.signal_date != V2_SOURCE_REGISTRY.locked_case_date:
        status = "locked_case_wrong_cutoff"
        observed = ()
    elif not data_available:
        status = "locked_case_unavailable"
        observed = ()
    else:
        observed = tuple(
            sorted(
                {
                    row.asset_code
                    for row in result.observations
                    if row.qualifies and row.availability == "available"
                }
            )
        )
        status = locked_case_status(
            observed_candidate_codes=observed,
            locked_codes=expected,
            data_available=True,
        )
    return V2LockedCaseEvidence(
        case_date=V2_SOURCE_REGISTRY.locked_case_date,
        expected_codes=expected,
        observed_codes=observed,
        status=status,
        source_registry_hash=V2_SOURCE_REGISTRY.registry_hash,
        data_available=data_available,
    )


def locked_case_status(
    *,
    observed_candidate_codes: Sequence[str],
    locked_codes: Sequence[str],
    data_available: bool,
) -> str:
    """Evaluate the locked case after screening; identifiers never enter the screen."""

    if not data_available:
        return "locked_case_unavailable"
    expected = set(locked_codes)
    observed = set(observed_candidate_codes)
    return "locked_case_match" if expected <= observed else "locked_case_mismatch"


__all__ = [
    "COST_BPS_PER_SIDE",
    "EMBARGO_SESSIONS",
    "MIN_PIT_SESSIONS",
    "MIN_PRIMARY_DATES",
    "PURGE_SESSIONS",
    "V2LockedCaseEvidence",
    "V2PairedOutcome",
    "V2PromotionEvidence",
    "V2SourceLabel",
    "evaluate_locked_case",
    "locked_case_status",
    "paired_five_session_net_excess",
    "source_label",
    "validate_v2_promotion_evidence",
]
