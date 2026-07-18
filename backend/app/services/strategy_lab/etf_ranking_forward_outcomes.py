"""Point-in-time forward outcomes for frozen ETF ranking candidates."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, Literal

from app.services.tracked_positions.lifecycle import stable_contract_hash

from .etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
    RankingCandidateSelection,
)

FORWARD_HORIZONS = (1, 3, 5, 10)
FORWARD_EXECUTION_MODEL = "t_plus_one_adjusted_close_v1"
_BPS_DENOMINATOR = 10_000.0


class ForwardOutcomeContractError(ValueError):
    """Raised when a forward-outcome input violates the frozen contract."""


@dataclass(frozen=True)
class ForwardAdjustedClose:
    """One decision-eligible total-return-adjusted close observation."""

    asset_code: str
    session_date: date
    adjusted_close: float
    price_basis: str
    decision_eligible: bool
    provider: str
    adjustment_version: str
    source_hash: str

    def __post_init__(self) -> None:
        if not self.asset_code.strip():
            raise ForwardOutcomeContractError("adjusted close asset code is required")
        if self.price_basis != "total_return_adjusted":
            raise ForwardOutcomeContractError(
                "forward outcomes require total-return-adjusted prices"
            )
        if self.decision_eligible is not True:
            raise ForwardOutcomeContractError(
                "forward outcomes require decision-eligible adjusted prices"
            )
        if (
            isinstance(self.adjusted_close, bool)
            or not math.isfinite(self.adjusted_close)
            or self.adjusted_close <= 0.0
        ):
            raise ForwardOutcomeContractError(
                "adjusted close must be finite and positive"
            )
        if not self.provider.strip() or not self.adjustment_version.strip():
            raise ForwardOutcomeContractError(
                "adjusted close provider and adjustment version are required"
            )
        if not self.source_hash.strip():
            raise ForwardOutcomeContractError("adjusted close source hash is required")


@dataclass(frozen=True)
class RankingForwardOutcome:
    replay_run_key: str
    replay_date: date
    candidate_id: str
    candidate_manifest_hash: str
    candidate_registry_hash: str
    source_selection_hash: str
    asset_code: str
    horizon_sessions: int
    status: Literal["completed", "excluded", "pending"]
    entry_session: date | None
    exit_session: date | None
    entry_adjusted_close: float | None
    exit_adjusted_close: float | None
    gross_return: float | None
    net_return: float | None
    fee_bps_per_side: int
    slippage_bps_per_side: int
    round_trip_cost_bps: int
    cost_contract_hash: str
    exclusion_reason: str | None
    missing_leg: Literal["entry", "exit"] | None
    input_hash: str
    outcome_hash: str


@dataclass(frozen=True)
class RankingForwardOutcomeStatusCount:
    horizon_sessions: int
    requested_count: int
    completed_count: int
    pending_count: int
    excluded_count: int


@dataclass(frozen=True)
class RankingForwardOutcomeBundle:
    replay_run_key: str
    replay_date: date
    candidate_id: str
    candidate_manifest_hash: str
    candidate_registry_hash: str
    source_selection_hash: str
    execution_model: str
    horizons: tuple[int, ...]
    fee_bps_per_side: int
    slippage_bps_per_side: int
    round_trip_cost_bps: int
    cost_contract_hash: str
    requested_outcome_count: int
    completed_outcome_count: int
    pending_outcome_count: int
    excluded_outcome_count: int
    status_counts_by_horizon: tuple[RankingForwardOutcomeStatusCount, ...]
    outcomes: tuple[RankingForwardOutcome, ...]
    input_hash: str
    bundle_hash: str


def _validate_selection(selection: RankingCandidateSelection) -> None:
    selection_payload = asdict(selection)
    selection_payload.pop("selection_hash")
    if (
        not selection.replay_run_key.strip()
        or not selection.candidate_id.strip()
        or not selection.candidate_manifest_hash.strip()
        or not selection.candidate_registry_hash.strip()
        or not selection.source_ranking_event_hash.strip()
        or selection.selection_hash
        != stable_contract_hash(selection_payload)
    ):
        raise ForwardOutcomeContractError("ranking candidate selection is invalid")
    if len(selection.selected_asset_codes) != len(
        set(selection.selected_asset_codes)
    ) or any(not code.strip() for code in selection.selected_asset_codes):
        raise ForwardOutcomeContractError(
            "ranking candidate selection contains invalid assets"
        )


def _validate_sessions(
    trading_sessions: Sequence[date],
    *,
    signal_date: date,
) -> tuple[date, ...]:
    sessions = tuple(trading_sessions)
    if not sessions or sessions != tuple(sorted(sessions)):
        raise ForwardOutcomeContractError(
            "trading sessions must be non-empty and increasing"
        )
    if len(sessions) != len(set(sessions)):
        raise ForwardOutcomeContractError("trading sessions contain duplicates")
    if signal_date not in sessions:
        raise ForwardOutcomeContractError(
            "selection date is absent from the trading-session calendar"
        )
    return sessions


def _validate_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(horizons)
    if not values or len(values) != len(set(values)):
        raise ForwardOutcomeContractError(
            "forward horizons must be a non-empty unique frozen subset"
        )
    if any(value not in FORWARD_HORIZONS for value in values):
        raise ForwardOutcomeContractError(
            "forward horizons must use the frozen 1/3/5/10-session set"
        )
    return tuple(value for value in FORWARD_HORIZONS if value in set(values))


def _index_adjusted_closes(
    rows: Iterable[ForwardAdjustedClose],
    *,
    trading_sessions: tuple[date, ...],
) -> tuple[
    dict[tuple[str, date], ForwardAdjustedClose],
    tuple[ForwardAdjustedClose, ...],
]:
    by_key: dict[tuple[str, date], ForwardAdjustedClose] = {}
    valid_sessions = set(trading_sessions)
    for row in rows:
        if row.session_date not in valid_sessions:
            raise ForwardOutcomeContractError(
                "adjusted close is outside the trading-session calendar"
            )
        key = (row.asset_code, row.session_date)
        if key in by_key:
            raise ForwardOutcomeContractError("duplicate adjusted close observation")
        by_key[key] = row
    ordered = tuple(
        sorted(by_key.values(), key=lambda row: (row.asset_code, row.session_date))
    )
    return by_key, ordered


def _outcome_payload(outcome: RankingForwardOutcome) -> dict[str, Any]:
    payload = asdict(outcome)
    payload.pop("outcome_hash")
    return payload


def _outcome(
    *,
    selection: RankingCandidateSelection,
    asset_code: str,
    horizon: int,
    sessions: tuple[date, ...],
    signal_index: int,
    closes: dict[tuple[str, date], ForwardAdjustedClose],
    input_hash: str,
) -> RankingForwardOutcome:
    entry_index = signal_index + 1
    exit_index = entry_index + horizon
    common: dict[str, Any] = {
        "replay_run_key": selection.replay_run_key,
        "replay_date": selection.replay_date,
        "candidate_id": selection.candidate_id,
        "candidate_manifest_hash": selection.candidate_manifest_hash,
        "candidate_registry_hash": selection.candidate_registry_hash,
        "source_selection_hash": selection.selection_hash,
        "asset_code": asset_code,
        "horizon_sessions": horizon,
        "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
        "slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        "round_trip_cost_bps": 2
        * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
        "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "input_hash": input_hash,
        "outcome_hash": "pending",
    }
    if entry_index >= len(sessions) or exit_index >= len(sessions):
        draft = RankingForwardOutcome(
            **common,
            status="pending",
            entry_session=(
                sessions[entry_index] if entry_index < len(sessions) else None
            ),
            exit_session=None,
            entry_adjusted_close=None,
            exit_adjusted_close=None,
            gross_return=None,
            net_return=None,
            exclusion_reason="future_window_pending",
            missing_leg=None,
        )
        return replace(
            draft,
            outcome_hash=stable_contract_hash(_outcome_payload(draft)),
        )

    entry_session = sessions[entry_index]
    exit_session = sessions[exit_index]
    entry = closes.get((asset_code, entry_session))
    exit_row = closes.get((asset_code, exit_session))
    missing_leg: Literal["entry", "exit"] | None = None
    if entry is None:
        missing_leg = "entry"
    elif exit_row is None:
        missing_leg = "exit"
    if missing_leg is not None:
        draft = RankingForwardOutcome(
            **common,
            status="excluded",
            entry_session=entry_session,
            exit_session=exit_session,
            entry_adjusted_close=(entry.adjusted_close if entry is not None else None),
            exit_adjusted_close=(
                exit_row.adjusted_close if exit_row is not None else None
            ),
            gross_return=None,
            net_return=None,
            exclusion_reason="missing_adjusted_entry_or_exit",
            missing_leg=missing_leg,
        )
        return replace(
            draft,
            outcome_hash=stable_contract_hash(_outcome_payload(draft)),
        )

    assert entry is not None and exit_row is not None
    fee = RANKING_FEE_BPS_PER_SIDE / _BPS_DENOMINATOR
    slippage = RANKING_SLIPPAGE_BPS_PER_SIDE / _BPS_DENOMINATOR
    gross_return = exit_row.adjusted_close / entry.adjusted_close - 1.0
    net_return = (
        exit_row.adjusted_close
        * (1.0 - slippage)
        * (1.0 - fee)
        / (entry.adjusted_close * (1.0 + slippage) * (1.0 + fee))
        - 1.0
    )
    draft = RankingForwardOutcome(
        **common,
        status="completed",
        entry_session=entry_session,
        exit_session=exit_session,
        entry_adjusted_close=entry.adjusted_close,
        exit_adjusted_close=exit_row.adjusted_close,
        gross_return=gross_return,
        net_return=net_return,
        exclusion_reason=None,
        missing_leg=None,
    )
    return replace(
        draft,
        outcome_hash=stable_contract_hash(_outcome_payload(draft)),
    )


def calculate_ranking_forward_outcomes(
    *,
    selection: RankingCandidateSelection,
    trading_sessions: Sequence[date],
    adjusted_closes: Iterable[ForwardAdjustedClose],
    horizons: Iterable[int] = FORWARD_HORIZONS,
) -> RankingForwardOutcomeBundle:
    """Calculate immutable research outcomes without writing production state."""

    _validate_selection(selection)
    sessions = _validate_sessions(
        trading_sessions,
        signal_date=selection.replay_date,
    )
    frozen_horizons = _validate_horizons(horizons)
    closes_by_key, ordered_closes = _index_adjusted_closes(
        adjusted_closes,
        trading_sessions=sessions,
    )
    input_hash = stable_contract_hash(
        {
            "contract_id": "ranking_forward_outcome_input_v1",
            "selection_hash": selection.selection_hash,
            "trading_sessions": sessions,
            "horizons": frozen_horizons,
            "adjusted_closes": tuple(asdict(row) for row in ordered_closes),
            "execution_model": FORWARD_EXECUTION_MODEL,
            "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        }
    )
    signal_index = sessions.index(selection.replay_date)
    outcomes = tuple(
        _outcome(
            selection=selection,
            asset_code=asset_code,
            horizon=horizon,
            sessions=sessions,
            signal_index=signal_index,
            closes=closes_by_key,
            input_hash=input_hash,
        )
        for asset_code in selection.selected_asset_codes
        for horizon in frozen_horizons
    )
    status_counts_by_horizon = tuple(
        RankingForwardOutcomeStatusCount(
            horizon_sessions=horizon,
            requested_count=sum(
                outcome.horizon_sessions == horizon for outcome in outcomes
            ),
            completed_count=sum(
                outcome.horizon_sessions == horizon and outcome.status == "completed"
                for outcome in outcomes
            ),
            pending_count=sum(
                outcome.horizon_sessions == horizon and outcome.status == "pending"
                for outcome in outcomes
            ),
            excluded_count=sum(
                outcome.horizon_sessions == horizon and outcome.status == "excluded"
                for outcome in outcomes
            ),
        )
        for horizon in frozen_horizons
    )
    draft = RankingForwardOutcomeBundle(
        replay_run_key=selection.replay_run_key,
        replay_date=selection.replay_date,
        candidate_id=selection.candidate_id,
        candidate_manifest_hash=selection.candidate_manifest_hash,
        candidate_registry_hash=selection.candidate_registry_hash,
        source_selection_hash=selection.selection_hash,
        execution_model=FORWARD_EXECUTION_MODEL,
        horizons=frozen_horizons,
        fee_bps_per_side=RANKING_FEE_BPS_PER_SIDE,
        slippage_bps_per_side=RANKING_SLIPPAGE_BPS_PER_SIDE,
        round_trip_cost_bps=2
        * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        requested_outcome_count=len(outcomes),
        completed_outcome_count=sum(
            outcome.status == "completed" for outcome in outcomes
        ),
        pending_outcome_count=sum(
            outcome.status == "pending" for outcome in outcomes
        ),
        excluded_outcome_count=sum(
            outcome.status == "excluded" for outcome in outcomes
        ),
        status_counts_by_horizon=status_counts_by_horizon,
        outcomes=outcomes,
        input_hash=input_hash,
        bundle_hash="pending",
    )
    bundle_payload = asdict(draft)
    bundle_payload.pop("bundle_hash")
    return replace(
        draft,
        bundle_hash=stable_contract_hash(bundle_payload),
    )
