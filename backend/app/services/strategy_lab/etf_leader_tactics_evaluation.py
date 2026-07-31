"""Research-only evaluation bridge for the frozen ETF leader proxies."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from statistics import fmean
from typing import Any, Literal

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_diagnostics import spearman
from app.services.strategy_lab.etf_factor_experiment import (
    PRIMARY_ENDPOINT,
    ChronologicalSplit,
    ExecutionCostPolicy,
    FactorCandidate,
    FactorExperimentManifest,
    PromotionGates,
    RegisteredFactorExperiment,
    register_factor_experiment,
)
from app.services.strategy_lab.etf_factor_panel import (
    CandidateCommonSupportPanel,
    CandidateFactorObservation,
    PointInTimeFactorInput,
)
from app.services.strategy_lab.etf_factor_validation import (
    holm_bonferroni,
    moving_block_bootstrap_interval,
    moving_block_bootstrap_positive_p_value,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_CANDIDATE_IDS,
    LEADER_EXPERIMENT_FAMILY,
    LEADER_HYPOTHESIS_REGISTRY,
    LeaderExperimentManifest,
    LeaderFeaturePanel,
    LeaderPitAssetInput,
    build_leader_experiment_manifest,
)
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    PromotionGateEvidence,
    ResearchPromotionState,
    evaluate_research_promotion,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
    freeze_ranking_candidate_registry,
)

PRIMARY_TOP_N = 10
PRIMARY_HORIZON = 5
LEADER_TOP_NS = (5, 10, 20)
LEADER_HORIZONS = (1, 3, 5, 10)
LEADER_MULTIPLICITY_METHOD = "holm_bonferroni_stepdown_interval_v1"


@dataclass(frozen=True)
class LeaderExperimentRegistration:
    leader_manifest: LeaderExperimentManifest
    factor_experiment: RegisteredFactorExperiment
    registration_hash: str


@dataclass(frozen=True)
class LeaderEndpointResult:
    candidate_id: str
    top_n: int
    horizon_sessions: int
    endpoint_role: Literal["primary", "exploratory"]
    endpoint_name: str
    complete_cohort_dates: tuple[date, ...]
    completed_outcome_dates: tuple[date, ...]
    independent_dates: tuple[date, ...]
    overlapping_excluded_dates: tuple[date, ...]
    selected_by_date: tuple[tuple[date, tuple[str, ...]], ...]
    candidate_net_returns: tuple[float, ...]
    baseline_net_returns: tuple[float, ...]
    paired_net_excess: tuple[float, ...]
    mean_candidate_net_return: float | None
    mean_baseline_net_return: float | None
    mean_paired_net_excess: float | None
    average_turnover: float | None
    average_rank_churn: float | None
    candidate_maximum_drawdown: float | None
    baseline_maximum_drawdown: float | None
    coverage_ratio: float
    exclusion_counts: Mapping[str, int]
    cost_contract_hash: str
    result_hash: str


@dataclass(frozen=True)
class LeaderPrimaryInference:
    candidate_id: str
    available: bool
    sample_count: int
    estimate: float | None
    raw_p_value: float | None
    holm_adjusted_p_value: float | None
    simultaneous_confidence: float | None
    simultaneous_interval: tuple[float, float] | None
    block_sessions: int
    resamples: int
    method: str
    inference_hash: str


@dataclass(frozen=True)
class LeaderHardGateFacts:
    decision_data_coverage_ratio: float
    score_coverage_ratio: float
    eligible_point_in_time_sessions: int
    completed_walk_forward_folds: int
    fold_sign_stable: bool
    regime_sign_stable: bool
    residual_incremental_alpha_positive: bool
    maximum_concentration: float
    purge_embargo_passed: bool = True
    turnover_gate_passed: bool = True
    non_finite_violations: int = 0
    concentration_violations: int = 0
    clone_policy_violations: int = 0
    raw_decision_price_violations: int = 0
    exclusion_gate_passed: bool = True
    holdout_consumed: bool = False


@dataclass(frozen=True)
class LeaderCandidateDecision:
    candidate_id: str
    status: Literal[
        "insufficient_data",
        "unconfirmed",
        "rejected",
        "eligible_for_v4_proposal",
    ]
    failed_gates: tuple[str, ...]
    primary_endpoint: str = PRIMARY_ENDPOINT
    production_mutation_allowed: Literal[False] = False


@dataclass(frozen=True)
class LeaderControlObservation:
    signal_date: date
    asset_code: str
    candidate_id: str
    candidate_score: float
    outcome_5_session: float
    momentum: float
    sector_trend: float
    risk: float
    liquidity: float
    overextension: float
    fold_id: str
    regime: str
    peer_group: str
    history_tier: str


def leader_execution_cost_policy() -> ExecutionCostPolicy:
    return ExecutionCostPolicy(
        entry_rule="signal_t_plus_one_decision_eligible_adjusted_close",
        exit_rule="entry_plus_declared_full_sessions_adjusted_close",
        fee_bps_per_side=RANKING_FEE_BPS_PER_SIDE,
        slippage_bps_per_side=RANKING_SLIPPAGE_BPS_PER_SIDE,
        turnover_charge_rule="multiplicative_each_side",
    )


def build_leader_factor_experiment_registration(
    *,
    code_version: str,
    baseline_contract_id: str,
    baseline_contract_hash: str,
    baseline_score_field: str,
    research_surface_contract_hash: str,
    actionable_surface_contract_hash: str,
    split: ChronologicalSplit,
    split_contract_hash: str,
    holdout_identity_hash: str,
    registered_at: datetime,
) -> LeaderExperimentRegistration:
    production_registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    leader_manifest = build_leader_experiment_manifest(
        baseline_contract_id=baseline_contract_id,
        baseline_contract_hash=baseline_contract_hash,
        split_contract_hash=split_contract_hash,
        code_version=code_version,
        holdout_identity_hash=holdout_identity_hash,
        production_candidate_registry_hash=production_registry.registry_hash,
    )
    candidates = tuple(
        FactorCandidate(
            candidate_id=item.candidate_id,
            formula=item.formula,
            direction="higher_is_better",
            transform="frozen_gate_then_equal_weight_percentile",
            missing_value_rule=item.missing_value_rule,
            peer_bucket="historical_pit_peer_group",
            minimum_peer_count=5,
            required_history_sessions=item.required_history_sessions,
        )
        for item in FROZEN_LEADER_CANDIDATE_REGISTRY.candidates
    )
    manifest = FactorExperimentManifest(
        experiment_name=LEADER_EXPERIMENT_FAMILY,
        code_version=code_version,
        baseline_contract_id=baseline_contract_id,
        baseline_contract_hash=baseline_contract_hash,
        baseline_score_field=baseline_score_field,
        research_surface_contract_hash=research_surface_contract_hash,
        actionable_surface_contract_hash=actionable_surface_contract_hash,
        candidates=candidates,
        universe_policy="factual_membership_visible_by_signal_cutoff",
        adjusted_data_policy="decision_eligible_total_return_adjusted_only",
        source_cutoff_policy="recorded_available_at_lte_signal_cutoff",
        peer_buckets=("historical_pit_peer_group",),
        horizons=LEADER_HORIZONS,
        top_ns=LEADER_TOP_NS,
        execution_cost=leader_execution_cost_policy(),
        split=split,
        exclusion_rules=(
            "no_current_taxonomy_backfill",
            "no_raw_price_substitution",
            "no_candidate_padding",
            "clone_representative_only",
            "future_window_pending",
        ),
        uncertainty_method="moving_block_bootstrap_holm_stepdown",
        bootstrap_block_sessions=10,
        multiplicity_method="holm_bonferroni",
        declared_regimes=("risk_on", "neutral", "defensive", "cash_wait"),
        promotion_gates=PromotionGates(
            adjusted_primary_lower_bound_min=0.0,
            minimum_common_support_coverage=0.95,
            minimum_stable_fold_ratio=1.0,
            maximum_turnover_deterioration=0.0,
            maximum_drawdown_deterioration=0.02,
            maximum_concentration=0.40,
            maximum_exclusion_rate=0.05,
        ),
    )
    registered = register_factor_experiment(manifest, registered_at=registered_at)
    return LeaderExperimentRegistration(
        leader_manifest=leader_manifest,
        factor_experiment=registered,
        registration_hash=stable_contract_hash(
            {
                "leader_manifest_hash": leader_manifest.manifest_hash,
                "factor_manifest_hash": registered.manifest_hash,
                "hypothesis_registry_hash": LEADER_HYPOTHESIS_REGISTRY.registry_hash,
                "production_candidate_registry_hash": production_registry.registry_hash,
            }
        ),
    )


def factor_inputs_from_leader_panel(
    panel: LeaderFeaturePanel,
    pit_inputs: Sequence[LeaderPitAssetInput],
) -> list[PointInTimeFactorInput]:
    by_code = {item.asset_code: item for item in pit_inputs}
    if set(by_code) != {item.asset_code for item in panel.observations}:
        raise ValueError("leader panel and PIT inputs do not cover the same assets")
    observations: dict[str, dict[str, CandidateFactorObservation]] = defaultdict(dict)
    for item in panel.observations:
        observations[item.asset_code][item.candidate_id] = CandidateFactorObservation(
            candidate_id=item.candidate_id,
            availability=item.availability,
            qualifies=item.qualifies,
            score=item.score,
            gate_reasons=item.gate_reasons,
            unavailable_reasons=item.unavailable_reasons,
            observation_hash=stable_contract_hash(
                {
                    "candidate_id": item.candidate_id,
                    "availability": item.availability,
                    "qualifies": item.qualifies,
                    "score": item.score,
                    "gate_reasons": item.gate_reasons,
                    "unavailable_reasons": item.unavailable_reasons,
                }
            ),
        )
    output: list[PointInTimeFactorInput] = []
    for code, item in sorted(by_code.items()):
        bars = item.bars
        latest = bars[-1] if bars else None
        output.append(
            PointInTimeFactorInput(
                asset_code=code,
                signal_date=item.signal_date,
                source_cutoff=item.source_cutoff,
                historical_member=item.historical_member,
                decision_eligible=not item.input_unavailable_reasons and latest is not None,
                price_basis=latest.price_basis if latest is not None else "unavailable",
                data_provider=latest.data_provider if latest is not None else None,
                provider_version=latest.provider_version if latest is not None else None,
                adjustment_version=(
                    latest.adjustment_version if latest is not None else None
                ),
                latest_factor_input_date=(
                    latest.trade_date if latest is not None else item.signal_date
                ),
                eligible_history_sessions=len(bars),
                baseline_score=item.baseline_score,
                candidate_scores={
                    candidate_id: observation.score
                    for candidate_id, observation in observations[code].items()
                },
                peer_bucket=item.peer_group or "unavailable",
                history_tier=next(
                    row.history_tier
                    for row in panel.observations
                    if row.asset_code == code
                ),
                candidate_observations=observations[code],
            )
        )
    return output


def _maximum_drawdown(values: Sequence[float]) -> float:
    wealth = 1.0
    peak = 1.0
    maximum = 0.0
    for value in values:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        maximum = max(maximum, (peak - wealth) / peak)
    return maximum


def _turnover(previous: Sequence[str], current: Sequence[str]) -> float:
    return 1.0 - len(set(previous) & set(current)) / max(len(previous), len(current))


def _rank_churn(previous: Sequence[str], current: Sequence[str]) -> float:
    before = {code: index for index, code in enumerate(previous)}
    after = {code: index for index, code in enumerate(current)}
    common = sorted(set(before) & set(after))
    if not common:
        return 1.0
    denominator = max(max(len(previous), len(current)) - 1, 1)
    return fmean(abs(before[code] - after[code]) / denominator for code in common)


def _endpoint_name(top_n: int, horizon: int) -> str:
    if (top_n, horizon) == (PRIMARY_TOP_N, PRIMARY_HORIZON):
        return PRIMARY_ENDPOINT
    return f"exploratory_top{top_n}_{horizon}_session_paired_net_excess"


def evaluate_leader_endpoints(
    panels: Mapping[str, CandidateCommonSupportPanel],
    *,
    trading_sessions: Sequence[date],
) -> tuple[LeaderEndpointResult, ...]:
    calendar = tuple(trading_sessions)
    if calendar != tuple(sorted(set(calendar))):
        raise ValueError("trading_sessions must be unique and chronological")
    if set(panels) != set(LEADER_CANDIDATE_IDS):
        raise ValueError("leader endpoint panels must contain the frozen candidates")
    results: list[LeaderEndpointResult] = []
    for candidate_id in LEADER_CANDIDATE_IDS:
        panel = panels[candidate_id]
        baseline_by_date: dict[date, list[Any]] = defaultdict(list)
        candidate_by_date: dict[date, list[Any]] = defaultdict(list)
        for item in panel.baseline_samples:
            baseline_by_date[item.signal_date].append(item)
        for item in panel.candidate_samples:
            candidate_by_date[item.signal_date].append(item)
        for top_n in LEADER_TOP_NS:
            for horizon in LEADER_HORIZONS:
                exclusions: Counter[str] = Counter()
                completed: list[
                    tuple[date, date, tuple[str, ...], float, float]
                ] = []
                complete_cohort_dates: list[date] = []
                for signal_date in sorted(baseline_by_date):
                    baseline_rows = sorted(
                        baseline_by_date[signal_date],
                        key=lambda item: (-item.baseline_score, item.asset_code),
                    )[:top_n]
                    candidate_rows = sorted(
                        candidate_by_date[signal_date],
                        key=lambda item: (
                            -float(item.candidate_scores[candidate_id]),
                            item.asset_code,
                        ),
                    )[:top_n]
                    if len(candidate_rows) < top_n:
                        exclusions["insufficient_candidate_cohort"] += 1
                        continue
                    if len(baseline_rows) < top_n:
                        exclusions["insufficient_baseline_cohort"] += 1
                        continue
                    complete_cohort_dates.append(signal_date)
                    rows = (*candidate_rows, *baseline_rows)
                    if any(horizon in item.pending_horizons for item in rows):
                        exclusions["future_window_pending"] += 1
                        continue
                    candidate_returns = [item.net_returns[horizon] for item in candidate_rows]
                    baseline_returns = [item.net_returns[horizon] for item in baseline_rows]
                    if any(value is None for value in (*candidate_returns, *baseline_returns)):
                        exclusions["missing_adjusted_entry_or_exit"] += 1
                        continue
                    try:
                        signal_index = calendar.index(signal_date)
                    except ValueError:
                        exclusions["signal_date_outside_calendar"] += 1
                        continue
                    exit_index = signal_index + 1 + horizon
                    if exit_index >= len(calendar):
                        exclusions["future_window_pending"] += 1
                        continue
                    completed.append(
                        (
                            signal_date,
                            calendar[exit_index],
                            tuple(item.asset_code for item in candidate_rows),
                            fmean(float(value) for value in candidate_returns),
                            fmean(float(value) for value in baseline_returns),
                        )
                    )
                accepted: list[tuple[date, date, tuple[str, ...], float, float]] = []
                overlapping: list[date] = []
                last_exit: date | None = None
                for row in completed:
                    if last_exit is not None and row[0] <= last_exit:
                        overlapping.append(row[0])
                        continue
                    accepted.append(row)
                    last_exit = row[1]
                candidate_returns = tuple(row[3] for row in accepted)
                baseline_returns = tuple(row[4] for row in accepted)
                excess = tuple(
                    candidate - baseline
                    for candidate, baseline in zip(
                        candidate_returns, baseline_returns, strict=True
                    )
                )
                selections = tuple((row[0], row[2]) for row in accepted)
                transitions = tuple(zip(selections, selections[1:], strict=False))
                turnovers = tuple(_turnover(a[1], b[1]) for a, b in transitions)
                churns = tuple(_rank_churn(a[1], b[1]) for a, b in transitions)
                payload = {
                    "candidate_id": candidate_id,
                    "top_n": top_n,
                    "horizon_sessions": horizon,
                    "endpoint_name": _endpoint_name(top_n, horizon),
                    "panel_hash": panel.panel_hash,
                    "selected_by_date": selections,
                    "candidate_net_returns": candidate_returns,
                    "baseline_net_returns": baseline_returns,
                    "paired_net_excess": excess,
                    "exclusion_counts": dict(sorted(exclusions.items())),
                    "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
                }
                results.append(
                    LeaderEndpointResult(
                        candidate_id=candidate_id,
                        top_n=top_n,
                        horizon_sessions=horizon,
                        endpoint_role=(
                            "primary"
                            if (top_n, horizon) == (PRIMARY_TOP_N, PRIMARY_HORIZON)
                            else "exploratory"
                        ),
                        endpoint_name=_endpoint_name(top_n, horizon),
                        complete_cohort_dates=tuple(complete_cohort_dates),
                        completed_outcome_dates=tuple(row[0] for row in completed),
                        independent_dates=tuple(row[0] for row in accepted),
                        overlapping_excluded_dates=tuple(overlapping),
                        selected_by_date=selections,
                        candidate_net_returns=candidate_returns,
                        baseline_net_returns=baseline_returns,
                        paired_net_excess=excess,
                        mean_candidate_net_return=(
                            fmean(candidate_returns) if candidate_returns else None
                        ),
                        mean_baseline_net_return=(
                            fmean(baseline_returns) if baseline_returns else None
                        ),
                        mean_paired_net_excess=fmean(excess) if excess else None,
                        average_turnover=fmean(turnovers) if turnovers else 0.0,
                        average_rank_churn=fmean(churns) if churns else 0.0,
                        candidate_maximum_drawdown=(
                            _maximum_drawdown(candidate_returns)
                            if candidate_returns
                            else None
                        ),
                        baseline_maximum_drawdown=(
                            _maximum_drawdown(baseline_returns)
                            if baseline_returns
                            else None
                        ),
                        coverage_ratio=(
                            len(completed) / len(complete_cohort_dates)
                            if complete_cohort_dates
                            else 0.0
                        ),
                        exclusion_counts=dict(sorted(exclusions.items())),
                        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
                        result_hash=stable_contract_hash(payload),
                    )
                )
    return tuple(results)


def infer_leader_primaries(
    endpoint_results: Sequence[LeaderEndpointResult],
    *,
    block_sessions: int = 5,
    resamples: int = 2_000,
    seed: int = 20260731,
) -> tuple[LeaderPrimaryInference, ...]:
    primary = {
        item.candidate_id: item
        for item in endpoint_results
        if item.endpoint_role == "primary"
    }
    if set(primary) != set(LEADER_CANDIDATE_IDS):
        raise ValueError("all frozen candidate primary endpoints are required")
    raw: dict[str, float] = {}
    for candidate_id in LEADER_CANDIDATE_IDS:
        values = primary[candidate_id].paired_net_excess
        if values:
            raw[candidate_id] = moving_block_bootstrap_positive_p_value(
                values,
                block_sessions=min(block_sessions, len(values)),
                resamples=resamples,
                seed=seed + LEADER_CANDIDATE_IDS.index(candidate_id),
            )
    ordered_available = sorted(raw, key=lambda candidate_id: (raw[candidate_id], candidate_id))
    adjusted_values = holm_bonferroni(tuple(raw[item] for item in ordered_available)) if raw else ()
    adjusted = dict(zip(ordered_available, adjusted_values, strict=True))
    inference: list[LeaderPrimaryInference] = []
    total = len(ordered_available)
    position = {candidate_id: index for index, candidate_id in enumerate(ordered_available)}
    for candidate_id in LEADER_CANDIDATE_IDS:
        values = primary[candidate_id].paired_net_excess
        if not values:
            payload = {
                "candidate_id": candidate_id,
                "available": False,
                "sample_count": 0,
                "method": LEADER_MULTIPLICITY_METHOD,
            }
            inference.append(
                LeaderPrimaryInference(
                    candidate_id=candidate_id,
                    available=False,
                    sample_count=0,
                    estimate=None,
                    raw_p_value=None,
                    holm_adjusted_p_value=None,
                    simultaneous_confidence=None,
                    simultaneous_interval=None,
                    block_sessions=block_sessions,
                    resamples=resamples,
                    method=LEADER_MULTIPLICITY_METHOD,
                    inference_hash=stable_contract_hash(payload),
                )
            )
            continue
        rank = position[candidate_id]
        confidence = 1.0 - 0.05 / (total - rank)
        interval = moving_block_bootstrap_interval(
            values,
            block_sessions=min(block_sessions, len(values)),
            resamples=resamples,
            confidence=confidence,
            seed=seed + rank,
        )
        payload = {
            "candidate_id": candidate_id,
            "sample_count": len(values),
            "estimate": interval.estimate,
            "raw_p_value": raw[candidate_id],
            "holm_adjusted_p_value": adjusted[candidate_id],
            "simultaneous_confidence": confidence,
            "simultaneous_interval": (interval.lower, interval.upper),
            "block_sessions": block_sessions,
            "resamples": resamples,
            "method": LEADER_MULTIPLICITY_METHOD,
        }
        inference.append(
            LeaderPrimaryInference(
                candidate_id=candidate_id,
                available=True,
                sample_count=len(values),
                estimate=interval.estimate,
                raw_p_value=raw[candidate_id],
                holm_adjusted_p_value=adjusted[candidate_id],
                simultaneous_confidence=confidence,
                simultaneous_interval=(interval.lower, interval.upper),
                block_sessions=block_sessions,
                resamples=resamples,
                method=LEADER_MULTIPLICITY_METHOD,
                inference_hash=stable_contract_hash(payload),
            )
        )
    return tuple(inference)


def evaluate_leader_candidate(
    *,
    primary_result: LeaderEndpointResult,
    inference: LeaderPrimaryInference,
    hard_gates: LeaderHardGateFacts,
) -> LeaderCandidateDecision:
    if (
        primary_result.candidate_id != inference.candidate_id
        or primary_result.endpoint_role != "primary"
        or primary_result.top_n != PRIMARY_TOP_N
        or primary_result.horizon_sessions != PRIMARY_HORIZON
    ):
        raise ValueError("leader promotion requires the frozen primary endpoint")
    gate_evidence = PromotionGateEvidence(
        decision_data_coverage_ratio=hard_gates.decision_data_coverage_ratio,
        score_coverage_ratio=hard_gates.score_coverage_ratio,
        eligible_point_in_time_sessions=hard_gates.eligible_point_in_time_sessions,
        independent_primary_dates=len(primary_result.independent_dates),
        completed_walk_forward_folds=hard_gates.completed_walk_forward_folds,
        adjusted_primary_interval_lower=(
            inference.simultaneous_interval[0]
            if inference.simultaneous_interval is not None
            else None
        ),
        fold_sign_stable=hard_gates.fold_sign_stable,
        regime_sign_stable=hard_gates.regime_sign_stable,
        candidate_maximum_drawdown=primary_result.candidate_maximum_drawdown,
        baseline_maximum_drawdown=primary_result.baseline_maximum_drawdown,
        non_finite_violations=hard_gates.non_finite_violations,
        concentration_violations=(
            hard_gates.concentration_violations
            + int(hard_gates.maximum_concentration > 0.40)
        ),
        clone_policy_violations=hard_gates.clone_policy_violations,
        raw_decision_price_violations=hard_gates.raw_decision_price_violations,
        exclusion_gate_passed=hard_gates.exclusion_gate_passed,
        holdout_consumed=hard_gates.holdout_consumed,
    )
    base = evaluate_research_promotion(gate_evidence)
    extra: list[str] = []
    if inference.holm_adjusted_p_value is None:
        extra.append("holm_adjusted_primary_test")
    elif inference.holm_adjusted_p_value > 0.05:
        extra.append("holm_adjusted_primary_test")
    if not hard_gates.residual_incremental_alpha_positive:
        extra.append("residual_incremental_alpha")
    if not hard_gates.purge_embargo_passed:
        extra.append("purge_embargo")
    if not hard_gates.turnover_gate_passed:
        extra.append("turnover")
    failures = tuple(dict.fromkeys((*base.failed_gates, *extra)))
    substantive_insufficient = tuple(
        item for item in base.failed_gates if item != "holdout_not_consumed"
    )
    if base.state is ResearchPromotionState.INSUFFICIENT_DATA and substantive_insufficient:
        status = "insufficient_data"
    elif not failures:
        status = "eligible_for_v4_proposal"
    elif hard_gates.holdout_consumed:
        status = "rejected"
    else:
        status = "unconfirmed"
    return LeaderCandidateDecision(
        candidate_id=primary_result.candidate_id,
        status=status,
        failed_gates=failures,
    )


def leader_factor_diagnostics(
    *,
    feature_panels: Sequence[LeaderFeaturePanel],
    endpoint_results: Sequence[LeaderEndpointResult],
    controls: Sequence[LeaderControlObservation] = (),
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    primary = {
        item.candidate_id: item
        for item in endpoint_results
        if item.endpoint_role == "primary"
    }
    for candidate_id in LEADER_CANDIDATE_IDS:
        rows = [
            item
            for panel in feature_panels
            for item in panel.by_candidate[candidate_id]
        ]
        dates = {item.signal_date for item in rows}
        qualified = [item for item in rows if item.qualifies]
        clone_removals = sum("clone_not_representative" in item.gate_reasons for item in rows)
        complete_top10_dates = (
            len(primary[candidate_id].complete_cohort_dates)
            if candidate_id in primary
            else 0
        )
        concentrations: dict[str, float] = {}
        for field in ("issuer", "theme", "sector", "clone_group"):
            values = [getattr(item, field) for item in qualified if getattr(item, field)]
            concentrations[field] = (
                max(Counter(values).values()) / len(values) if values else 0.0
            )
        control_rows = [item for item in controls if item.candidate_id == candidate_id]
        overlap: dict[str, float | None] = {}
        residual_values = [item.candidate_score for item in control_rows]
        for field in ("momentum", "sector_trend", "risk", "liquidity", "overextension"):
            values = [float(getattr(item, field)) for item in control_rows]
            overlap[field] = spearman(
                [item.candidate_score for item in control_rows], values
            )
            if residual_values and values:
                x_mean = fmean(values)
                y_mean = fmean(residual_values)
                variance = sum((value - x_mean) ** 2 for value in values)
                slope = (
                    sum(
                        (x - x_mean) * (y - y_mean)
                        for x, y in zip(values, residual_values, strict=True)
                    )
                    / variance
                    if variance > 0
                    else 0.0
                )
                residual_values = [
                    y - (y_mean + slope * (x - x_mean))
                    for x, y in zip(values, residual_values, strict=True)
                ]
        residual_ic = spearman(
            residual_values,
            [item.outcome_5_session for item in control_rows],
        )
        slices: dict[str, dict[str, float | None]] = {}
        for field in ("fold_id", "regime", "peer_group", "history_tier", "asset_code"):
            grouped: dict[str, list[LeaderControlObservation]] = defaultdict(list)
            for item in control_rows:
                grouped[str(getattr(item, field))].append(item)
            slices[field] = {
                key: spearman(
                    [item.candidate_score for item in values],
                    [item.outcome_5_session for item in values],
                )
                for key, values in sorted(grouped.items())
            }
        result = primary.get(candidate_id)
        diagnostics[candidate_id] = {
            "signal_date_count": len(dates),
            "signal_frequency": len({item.signal_date for item in qualified}) / len(dates) if dates else 0.0,
            "qualifying_count": len(qualified),
            "complete_top10_date_count": complete_top10_dates,
            "history_tier_counts": dict(sorted(Counter(item.history_tier for item in rows).items())),
            "peer_mapping_coverage": sum(item.peer_group is not None for item in rows) / len(rows) if rows else 0.0,
            "clone_removal_count": clone_removals,
            "turnover": result.average_turnover if result is not None else None,
            "rank_churn": result.average_rank_churn if result is not None else None,
            "cost_drag_bps": 2 * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
            "maximum_drawdown": result.candidate_maximum_drawdown if result is not None else None,
            "concentration": concentrations,
            "control_spearman": overlap,
            "residual_ic_5_session": residual_ic,
            "stability_slices": slices,
        }
    return diagnostics
