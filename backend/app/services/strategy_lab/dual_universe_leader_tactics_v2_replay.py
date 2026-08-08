"""Bridge V2 observations into the existing PIT forward-outcome engine."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from statistics import mean

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    POST_CLOSE_WATCHLIST_MODE,
    V2_CANDIDATE_IDS,
    V2_FORMULA_REGISTRY_HASH,
    V2ScreenResult,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_availability import (
    validate_v2_unavailable_reason,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    V2PairedOutcome,
    paired_five_session_net_excess,
)
from app.services.strategy_lab.etf_factor_experiment import ChronologicalSplit
from app.services.strategy_lab.etf_factor_validation import (
    BootstrapInterval,
    WalkForwardFold,
    expanding_walk_forward_folds,
    moving_block_bootstrap_interval,
)
from app.services.strategy_lab.etf_ranking_candidates import RankingCandidateSelection
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    ForwardAdjustedClose,
    RankingForwardOutcomeBundle,
    calculate_ranking_forward_outcomes,
)


@dataclass(frozen=True)
class V2EtfPrimaryEvidence:
    formula_id: str
    selection: RankingCandidateSelection
    forward_bundle: RankingForwardOutcomeBundle
    paired_outcomes: tuple[V2PairedOutcome, ...]
    selected_count: int
    completed_asset_count: int
    unavailable_reason: str | None

    def __post_init__(self) -> None:
        validate_v2_unavailable_reason(self.unavailable_reason)

    @property
    def mean_net_excess(self) -> float | None:
        if not self.paired_outcomes:
            return None
        return sum(item.net_excess_return for item in self.paired_outcomes) / len(
            self.paired_outcomes
        )


def build_v2_replay_selection(
    result: V2ScreenResult,
    *,
    formula_id: str,
    replay_run_key: str,
) -> RankingCandidateSelection:
    """Convert frozen V2 qualifying observations into an existing replay selection."""

    if formula_id not in V2_CANDIDATE_IDS:
        raise ValueError("formula_id is not a frozen V2 candidate")
    if not replay_run_key.strip():
        raise ValueError("replay_run_key is required")
    if any(
        dict(row.gate_facts).get("decision_mode") == POST_CLOSE_WATCHLIST_MODE
        for row in result.observations
    ):
        raise ValueError("post_close_watchlist_not_historical_pit")
    rows = sorted(
        (
            row
            for row in result.observations
            if row.formula_id == formula_id and row.qualifies and row.availability == "available"
        ),
        key=lambda row: (-(row.score if row.score is not None else float("-inf")), row.asset_code),
    )[:10]
    if not rows:
        raise ValueError("v2_candidate_not_materialized")
    selected = tuple(row.asset_code for row in rows)
    candidate_hash = stable_contract_hash(
        {"v2_screen_manifest": result.manifest_hash, "formula_id": formula_id}
    )
    draft = RankingCandidateSelection(
        replay_run_key=replay_run_key,
        replay_date=result.signal_date,
        candidate_id=formula_id,
        candidate_manifest_hash=candidate_hash,
        candidate_registry_hash=V2_FORMULA_REGISTRY_HASH,
        source_ranking_event_hash=result.manifest_hash,
        selected_asset_codes=selected,
        underlying_hysteresis_asset_codes=selected,
        entered_asset_codes=selected,
        exited_asset_codes=(),
        retained_asset_codes=(),
        gate_exclusions=(),
        selection_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("selection_hash")
    return draft.__class__(**{**asdict(draft), "selection_hash": stable_contract_hash(payload)})


def evaluate_v2_etf_primary(
    *,
    result: V2ScreenResult,
    formula_id: str,
    replay_run_key: str,
    trading_sessions: Sequence[date],
    adjusted_closes: Sequence[ForwardAdjustedClose],
    frozen_baseline_gross_returns: Mapping[date, float],
) -> V2EtfPrimaryEvidence:
    """Run the V2 ETF primary endpoint through the existing forward-outcome engine."""

    selection = build_v2_replay_selection(
        result,
        formula_id=formula_id,
        replay_run_key=replay_run_key,
    )
    bundle = calculate_ranking_forward_outcomes(
        selection=selection,
        trading_sessions=trading_sessions,
        adjusted_closes=adjusted_closes,
        horizons=(5,),
    )
    completed = tuple(
        item
        for item in bundle.outcomes
        if item.horizon_sessions == 5
        and item.status == "completed"
        and item.gross_return is not None
    )
    grouped: dict[date, list[float]] = defaultdict(list)
    for item in completed:
        assert item.gross_return is not None
        grouped[selection.replay_date].append(float(item.gross_return))
    candidate_returns = {
        signal_date: mean(values) for signal_date, values in grouped.items() if values
    }
    paired = paired_five_session_net_excess(
        candidate_returns,
        frozen_baseline_gross_returns,
    )
    return V2EtfPrimaryEvidence(
        formula_id=formula_id,
        selection=selection,
        forward_bundle=bundle,
        paired_outcomes=paired,
        selected_count=len(selection.selected_asset_codes),
        completed_asset_count=len({item.asset_code for item in completed}),
        unavailable_reason=(None if paired else "insufficient_common_support_for_v2_etf_primary"),
    )


def build_v2_walk_forward_folds(
    session_dates: Sequence[date],
    split: ChronologicalSplit,
    *,
    fold_sessions: int,
) -> tuple[WalkForwardFold, ...]:
    """Reuse the frozen existing expanding walk-forward implementation."""

    return expanding_walk_forward_folds(session_dates, split, fold_sessions=fold_sessions)


def bootstrap_v2_primary(
    net_excess_returns: Sequence[float],
    *,
    block_sessions: int = 5,
    resamples: int = 2_000,
    seed: int = 20260804,
) -> BootstrapInterval:
    """Reuse the existing moving block bootstrap; no parameter search is performed."""

    return moving_block_bootstrap_interval(
        net_excess_returns,
        block_sessions=block_sessions,
        resamples=resamples,
        confidence=0.95,
        seed=seed,
    )


__all__ = [
    "V2EtfPrimaryEvidence",
    "bootstrap_v2_primary",
    "build_v2_replay_selection",
    "build_v2_walk_forward_folds",
    "evaluate_v2_etf_primary",
]
