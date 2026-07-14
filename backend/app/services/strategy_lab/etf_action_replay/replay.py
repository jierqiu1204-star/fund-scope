from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date

from app.services.tracked_positions.lifecycle import stable_contract_hash

from .contracts import MAX_REPLAY_CANDIDATES
from .execution import (
    DailyExecutionBar,
    DeferredExecutionSession,
    ExecutionStatus,
    select_adjusted_open_fill,
)
from .features import MAX_BATCH_SECONDS, BoundedWorkLimitError, FeatureRow


class IncompleteCrossSectionError(ValueError):
    pass


def canonical_membership_hash(asset_codes: Iterable[str]) -> str:
    codes = tuple(sorted(code.strip() for code in asset_codes))
    if not codes or any(not code for code in codes) or len(codes) != len(set(codes)):
        raise IncompleteCrossSectionError(
            "point-in-time membership must be non-empty, canonical, and unique"
        )
    return stable_contract_hash({"eligible_asset_codes": codes})


@dataclass(frozen=True)
class PointInTimeUniverseDay:
    session_date: date
    eligible_asset_codes: tuple[str, ...]
    expected_universe_count: int
    canonical_membership_hash: str
    snapshot_hash: str

    def __post_init__(self) -> None:
        if self.eligible_asset_codes != tuple(sorted(self.eligible_asset_codes)):
            raise IncompleteCrossSectionError("universe membership must be canonical")
        if self.expected_universe_count != len(self.eligible_asset_codes):
            raise IncompleteCrossSectionError("universe expected count mismatch")
        if self.canonical_membership_hash != canonical_membership_hash(
            self.eligible_asset_codes
        ):
            raise IncompleteCrossSectionError("universe membership hash mismatch")
        if not self.snapshot_hash.strip():
            raise IncompleteCrossSectionError("universe snapshot hash is required")


@dataclass(frozen=True)
class CrossSectionCompletionManifest:
    run_id: str
    feature_contract_hash: str
    input_snapshot_hash: str
    session_date: date
    eligible_asset_codes: tuple[str, ...]
    expected_universe_count: int
    canonical_membership_hash: str
    universe_snapshot_hash: str
    feature_hashes: tuple[tuple[str, str], ...]
    manifest_hash: str


def _manifest_payload(manifest: CrossSectionCompletionManifest) -> dict[str, object]:
    return {
        "run_id": manifest.run_id,
        "feature_contract_hash": manifest.feature_contract_hash,
        "input_snapshot_hash": manifest.input_snapshot_hash,
        "session_date": manifest.session_date,
        "eligible_asset_codes": manifest.eligible_asset_codes,
        "expected_universe_count": manifest.expected_universe_count,
        "canonical_membership_hash": manifest.canonical_membership_hash,
        "universe_snapshot_hash": manifest.universe_snapshot_hash,
        "feature_hashes": manifest.feature_hashes,
    }


def build_completion_manifest(
    *,
    run_id: str,
    universe: PointInTimeUniverseDay,
    feature_rows: Iterable[FeatureRow],
) -> CrossSectionCompletionManifest:
    bounded_rows: list[FeatureRow] = []
    for row in feature_rows:
        if len(bounded_rows) >= universe.expected_universe_count:
            raise IncompleteCrossSectionError(
                "feature rows exceed authoritative universe count"
            )
        bounded_rows.append(row)
    rows = tuple(bounded_rows)
    if not run_id.strip() or not rows:
        raise IncompleteCrossSectionError("run id and feature rows are required")
    codes = tuple(sorted(row.asset_code for row in rows))
    if codes != universe.eligible_asset_codes:
        raise IncompleteCrossSectionError("feature membership does not match authoritative universe")
    if len(codes) != len(set(codes)):
        raise IncompleteCrossSectionError("feature membership contains duplicates")
    first = rows[0]
    for row in rows:
        if row.run_id != run_id or row.session_date != universe.session_date:
            raise IncompleteCrossSectionError("feature identity does not match manifest")
        if (
            row.feature_contract_hash != first.feature_contract_hash
            or row.input_snapshot_hash != first.input_snapshot_hash
        ):
            raise IncompleteCrossSectionError("feature contract is mixed within manifest")
    feature_hashes = tuple(
        sorted((row.asset_code, row.feature_hash) for row in rows)
    )
    draft = CrossSectionCompletionManifest(
        run_id=run_id,
        feature_contract_hash=first.feature_contract_hash,
        input_snapshot_hash=first.input_snapshot_hash,
        session_date=universe.session_date,
        eligible_asset_codes=universe.eligible_asset_codes,
        expected_universe_count=universe.expected_universe_count,
        canonical_membership_hash=universe.canonical_membership_hash,
        universe_snapshot_hash=universe.snapshot_hash,
        feature_hashes=feature_hashes,
        manifest_hash="pending",
    )
    return replace(draft, manifest_hash=stable_contract_hash(_manifest_payload(draft)))


@dataclass(frozen=True)
class ReplayCandidateConfig:
    candidate_id: str
    top_n: int
    initial_cash: float = 100_000.0
    fee_rate: float = 0.001
    lot_size: int = 1

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or self.top_n < 1:
            raise ValueError("candidate_id and positive top_n are required")
        if not math.isfinite(self.initial_cash) or self.initial_cash < 0:
            raise ValueError("initial_cash must be finite and non-negative")
        if not math.isfinite(self.fee_rate) or not 0 <= self.fee_rate < 1:
            raise ValueError("fee_rate must be a finite fraction in [0, 1)")
        if (
            isinstance(self.lot_size, bool)
            or not isinstance(self.lot_size, int)
            or self.lot_size < 1
        ):
            raise ValueError("lot_size must be a positive integer")


def canonical_candidate_config_hash(
    candidates: Iterable[ReplayCandidateConfig],
    *,
    frozen_parameter_hash: str,
) -> str:
    if not frozen_parameter_hash.strip():
        raise ValueError("frozen_parameter_hash is required")
    bounded: list[ReplayCandidateConfig] = []
    for candidate in candidates:
        if len(bounded) >= MAX_REPLAY_CANDIDATES:
            raise BoundedWorkLimitError(
                f"candidate configs exceed registered limit {MAX_REPLAY_CANDIDATES}"
            )
        bounded.append(candidate)
    items = tuple(sorted(bounded, key=lambda item: item.candidate_id))
    ids = tuple(item.candidate_id for item in items)
    if not items or len(ids) != len(set(ids)):
        raise ValueError("candidate configs must be non-empty and unique")
    return stable_contract_hash(
        {
            "frozen_parameter_hash": frozen_parameter_hash,
            "candidates": tuple(
                {
                    "candidate_id": item.candidate_id,
                    "top_n": item.top_n,
                    "initial_cash": item.initial_cash,
                    "fee_rate": item.fee_rate,
                    "lot_size": item.lot_size,
                }
                for item in items
            ),
        }
    )


@dataclass(frozen=True)
class ReplayPolicyOutput:
    candidate_id: str
    candidate_config_hash: str
    frozen_parameter_hash: str
    input_snapshot_hash: str
    session_date: date
    asset_code: str
    rule_id: str
    alert_episode_id: str
    action_cycle_id: str
    action_decision_id: str
    target_remaining_fraction: float
    decision_eligible: bool

    def __post_init__(self) -> None:
        identity = (
            self.candidate_id,
            self.candidate_config_hash,
            self.frozen_parameter_hash,
            self.input_snapshot_hash,
            self.asset_code,
            self.rule_id,
            self.alert_episode_id,
            self.action_cycle_id,
            self.action_decision_id,
        )
        if any(not value.strip() for value in identity):
            raise ValueError("policy output identity fields are required")
        if not isinstance(self.decision_eligible, bool):
            raise ValueError("policy output decision_eligible must be boolean")
        target = self.target_remaining_fraction
        if (
            isinstance(target, bool)
            or not math.isfinite(target)
            or not 0 <= target <= 1
        ):
            raise ValueError("policy output requires a finite absolute target")

    @property
    def policy_key(self) -> str:
        return stable_contract_hash(
            {
                "candidate_id": self.candidate_id,
                "session_date": self.session_date,
                "asset_code": self.asset_code,
                "action_decision_id": self.action_decision_id,
            }
        )


def canonical_policy_input_hash(outputs: Iterable[ReplayPolicyOutput]) -> str:
    items = tuple(sorted(outputs, key=lambda item: item.policy_key))
    keys = tuple(item.policy_key for item in items)
    if len(keys) != len(set(keys)):
        raise ValueError("policy output identities must be unique")
    return stable_contract_hash({"policy_outputs": items})


@dataclass(frozen=True)
class ReplayPositionState:
    shares: float
    exposure_baseline_shares: float
    high_watermark: float
    position_episode_id: str
    exposure_version: int
    rule_states: Mapping[str, str] = field(default_factory=dict)
    alert_episode_ids: Mapping[str, str] = field(default_factory=dict)
    action_decisions: Mapping[str, tuple[str, float]] = field(default_factory=dict)
    cycle_targets: Mapping[str, float] = field(default_factory=dict)
    action_cycle_id: str | None = None
    current_action_decision_id: str | None = None
    current_target_remaining_fraction: float | None = None


@dataclass(frozen=True)
class PendingFillState:
    order_id: str
    asset_code: str
    signal_date: date
    side: str
    rank: int
    target_weight: float | None = None
    target_remaining_fraction: float | None = None
    signal_equity: float | None = None
    reason: str = "ranked_target"
    action_cycle_id: str | None = None
    action_decision_id: str | None = None
    deferred_sessions: tuple[DeferredExecutionSession, ...] = ()

    def __post_init__(self) -> None:
        if self.side not in {"buy", "sell"}:
            raise ValueError("pending fill side must be buy or sell")
        if not self.order_id.strip() or not self.asset_code.strip() or self.rank < 0:
            raise ValueError("pending fill identity is invalid")
        if self.side == "buy" and (
            self.target_weight is None
            or self.signal_equity is None
            or not 0 < self.target_weight <= 1
            or self.signal_equity < 0
        ):
            raise ValueError("buy pending fill requires target weight and signal equity")
        if self.side == "sell" and (
            self.target_remaining_fraction is None
            or not 0 <= self.target_remaining_fraction <= 1
            or not self.action_cycle_id
            or not self.action_decision_id
        ):
            raise ValueError("sell pending fill requires an absolute action target")


@dataclass(frozen=True)
class CandidatePortfolioState:
    cash: float
    positions: Mapping[str, ReplayPositionState] = field(default_factory=dict)
    pending_fills: tuple[PendingFillState, ...] = ()
    cumulative_fees: float = 0.0
    turnover: float = 0.0
    equity: float = 0.0


@dataclass(frozen=True)
class ReplayState:
    candidate_states: Mapping[str, CandidatePortfolioState]


@dataclass(frozen=True)
class DailyRankingEvent:
    run_id: str
    session_date: date
    ordered_asset_codes: tuple[str, ...]
    cross_section_hash: str
    manifest_hash: str


@dataclass(frozen=True)
class CandidateReplayEvent:
    event_key: str
    run_id: str
    candidate_id: str
    candidate_config_hash: str
    session_date: date
    event_type: str
    asset_code: str
    rank: int = 0
    side: str | None = None
    quantity: float = 0.0
    price: float | None = None
    fee: float = 0.0
    cash_after: float = 0.0
    action_cycle_id: str | None = None
    action_decision_id: str | None = None
    target_remaining_fraction: float | None = None
    reason: str | None = None
    signal_to_fill_sessions: int | None = None


@dataclass(frozen=True)
class EquityCurvePoint:
    run_id: str
    candidate_id: str
    candidate_config_hash: str
    session_date: date
    equity: float
    cash: float
    market_value: float
    cumulative_fees: float
    turnover: float

    @property
    def stable_key(self) -> str:
        return stable_contract_hash(
            {
                "run_id": self.run_id,
                "candidate_id": self.candidate_id,
                "candidate_config_hash": self.candidate_config_hash,
                "session_date": self.session_date,
            }
        )


@dataclass(frozen=True)
class ReplayBatchRequest:
    run_id: str
    frozen_parameter_hash: str
    start_date: date
    end_date: date
    max_dates: int
    max_feature_rows: int
    max_policy_outputs: int
    max_pending_fills: int
    max_events: int
    max_seconds: float = MAX_BATCH_SECONDS
    worker_count: int = 1
    after_date: date | None = None
    has_more: bool = False

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.frozen_parameter_hash.strip():
            raise BoundedWorkLimitError("run_id and frozen_parameter_hash are required")
        if self.start_date > self.end_date:
            raise BoundedWorkLimitError("start_date must not exceed end_date")
        if self.max_dates < 1:
            raise BoundedWorkLimitError("max_dates must be positive")
        if (
            self.max_feature_rows < 1
            or self.max_policy_outputs < 1
            or self.max_pending_fills < 1
            or self.max_events < 1
        ):
            raise BoundedWorkLimitError("replay input row bounds must be positive")
        if not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= MAX_BATCH_SECONDS:
            raise BoundedWorkLimitError("max_seconds must be within (0, 55]")
        if self.worker_count != 1:
            raise BoundedWorkLimitError("worker_count must be 1")


@dataclass(frozen=True)
class ReplayBatchResult:
    candidate_config_hash: str
    state: ReplayState
    rankings: tuple[DailyRankingEvent, ...]
    events: tuple[CandidateReplayEvent, ...]
    equity_curve: tuple[EquityCurvePoint, ...]
    processed_dates: int
    complete: bool
    last_completed_date: date | None
    next_after_date: date | None
    worker_count: int
    query_count: int
    batch_invocations: int
    elapsed_seconds: float
    feature_rows_read: int
    manifest_rows_read: int
    policy_outputs_read: int


def rank_feature_cross_section(rows: tuple[FeatureRow, ...]) -> tuple[FeatureRow, ...]:
    return tuple(sorted(rows, key=lambda row: (-row.score, row.asset_code)))


def _initial_state(candidates: tuple[ReplayCandidateConfig, ...]) -> ReplayState:
    return ReplayState(
        candidate_states={
            candidate.candidate_id: CandidatePortfolioState(
                cash=candidate.initial_cash,
                equity=candidate.initial_cash,
            )
            for candidate in candidates
        }
    )


def _bounded_collect(
    items: Iterable[object],
    *,
    limit: int,
    label: str,
    started: float,
    max_seconds: float,
    clock: Callable[[], float],
    error_type: type[ValueError],
) -> list[object]:
    output: list[object] = []
    for item in items:
        if len(output) >= limit:
            raise error_type(f"{label} exceeds explicit bound {limit}")
        if clock() - started >= max_seconds:
            raise BoundedWorkLimitError(f"{label} read exceeded max_seconds")
        output.append(item)
    return output


def _validate_manifest(
    manifest: CrossSectionCompletionManifest,
    rows: tuple[FeatureRow, ...],
) -> None:
    expected_hash = stable_contract_hash(_manifest_payload(manifest))
    if manifest.manifest_hash != expected_hash:
        raise IncompleteCrossSectionError("manifest hash mismatch")
    if len(rows) != manifest.expected_universe_count:
        raise IncompleteCrossSectionError("manifest feature count mismatch")
    by_code: dict[str, FeatureRow] = {}
    for row in rows:
        if row.asset_code in by_code:
            raise IncompleteCrossSectionError("manifest feature rows contain duplicates")
        by_code[row.asset_code] = row
    if tuple(sorted(by_code)) != manifest.eligible_asset_codes:
        raise IncompleteCrossSectionError("manifest feature membership mismatch")
    actual_hashes = tuple(
        (code, by_code[code].feature_hash) for code in manifest.eligible_asset_codes
    )
    if actual_hashes != manifest.feature_hashes:
        raise IncompleteCrossSectionError("manifest feature hash mismatch")
    for row in rows:
        if (
            row.run_id != manifest.run_id
            or row.session_date != manifest.session_date
            or row.feature_contract_hash != manifest.feature_contract_hash
            or row.input_snapshot_hash != manifest.input_snapshot_hash
        ):
            raise IncompleteCrossSectionError("manifest feature identity mismatch")


def _event(
    *,
    run_id: str,
    candidate_id: str,
    candidate_config_hash: str,
    session_date: date,
    event_type: str,
    asset_code: str,
    discriminator: str,
    **values: object,
) -> CandidateReplayEvent:
    key = stable_contract_hash(
        {
            "run_id": run_id,
            "candidate_id": candidate_id,
            "candidate_config_hash": candidate_config_hash,
            "session_date": session_date,
            "event_type": event_type,
            "asset_code": asset_code,
            "discriminator": discriminator,
        }
    )
    return CandidateReplayEvent(
        event_key=key,
        run_id=run_id,
        candidate_id=candidate_id,
        candidate_config_hash=candidate_config_hash,
        session_date=session_date,
        event_type=event_type,
        asset_code=asset_code,
        **values,  # type: ignore[arg-type]
    )


def _execution_bar(row: FeatureRow) -> DailyExecutionBar:
    return DailyExecutionBar(
        session_date=row.session_date,
        raw_open=row.raw_open,
        adjustment_factor=row.adjustment_factor,
        volume=row.volume,
        suspended=row.suspended,
        limit_locked=row.limit_locked,
        demonstrably_tradable=row.demonstrably_tradable,
        delisted=row.delisted,
        raw_high=row.raw_high,
        raw_low=row.raw_low,
        raw_close=row.raw_close,
    )


def _process_pending_fills(
    *,
    run_id: str,
    candidate_config_hash: str,
    candidate: ReplayCandidateConfig,
    session_date: date,
    rows_by_code: Mapping[str, FeatureRow],
    state: CandidatePortfolioState,
) -> tuple[
    float,
    dict[str, ReplayPositionState],
    list[PendingFillState],
    float,
    float,
    list[CandidateReplayEvent],
]:
    cash = state.cash
    positions = dict(state.positions)
    remaining: list[PendingFillState] = []
    fees = state.cumulative_fees
    turnover = state.turnover
    events: list[CandidateReplayEvent] = []
    ordered = sorted(
        state.pending_fills,
        key=lambda item: (
            0 if item.side == "sell" else 1,
            item.signal_date,
            item.rank,
            item.asset_code,
            item.order_id,
        ),
    )
    for pending in ordered:
        if pending.signal_date >= session_date:
            remaining.append(pending)
            continue
        row = rows_by_code.get(pending.asset_code)
        if row is None:
            deferred = DeferredExecutionSession(session_date, "missing_execution_bar")
            remaining.append(
                replace(
                    pending,
                    deferred_sessions=(*pending.deferred_sessions, deferred),
                )
            )
            events.append(
                _event(
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    candidate_config_hash=candidate_config_hash,
                    session_date=session_date,
                    event_type="fill_deferred",
                    asset_code=pending.asset_code,
                    discriminator=pending.order_id,
                    rank=pending.rank,
                    side=pending.side,
                    cash_after=cash,
                    reason=deferred.reason,
                )
            )
            continue
        resolution = select_adjusted_open_fill(
            signal_session=pending.signal_date,
            bars=(_execution_bar(row),),
        )
        if resolution.status is ExecutionStatus.DEFERRED:
            remaining.append(
                replace(
                    pending,
                    deferred_sessions=(
                        *pending.deferred_sessions,
                        *resolution.deferred_sessions,
                    ),
                )
            )
            reason = (
                resolution.deferred_sessions[-1].reason
                if resolution.deferred_sessions
                else "execution_deferred"
            )
            events.append(
                _event(
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    candidate_config_hash=candidate_config_hash,
                    session_date=session_date,
                    event_type="fill_deferred",
                    asset_code=pending.asset_code,
                    discriminator=pending.order_id,
                    rank=pending.rank,
                    side=pending.side,
                    cash_after=cash,
                    reason=reason,
                )
            )
            continue
        if resolution.status is ExecutionStatus.REJECTED:
            events.append(
                _event(
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    candidate_config_hash=candidate_config_hash,
                    session_date=session_date,
                    event_type="fill_rejected",
                    asset_code=pending.asset_code,
                    discriminator=pending.order_id,
                    rank=pending.rank,
                    side=pending.side,
                    cash_after=cash,
                    reason=resolution.rejection_reason,
                )
            )
            continue
        assert resolution.fill is not None
        price = resolution.fill.normalized_execution_price
        delay = len(pending.deferred_sessions) + 1
        if pending.side == "sell":
            position = positions.get(pending.asset_code)
            if position is None:
                events.append(
                    _event(
                        run_id=run_id,
                        candidate_id=candidate.candidate_id,
                        candidate_config_hash=candidate_config_hash,
                        session_date=session_date,
                        event_type="fill_rejected",
                        asset_code=pending.asset_code,
                        discriminator=pending.order_id,
                        rank=pending.rank,
                        side="sell",
                        cash_after=cash,
                        reason="position_missing",
                    )
                )
                continue
            target_fraction = float(pending.target_remaining_fraction)
            target_shares = position.exposure_baseline_shares * target_fraction
            quantity = max(position.shares - target_shares, 0.0)
            if quantity <= 1e-12:
                events.append(
                    _event(
                        run_id=run_id,
                        candidate_id=candidate.candidate_id,
                        candidate_config_hash=candidate_config_hash,
                        session_date=session_date,
                        event_type="target_already_satisfied",
                        asset_code=pending.asset_code,
                        discriminator=pending.order_id,
                        rank=pending.rank,
                        side="sell",
                        cash_after=cash,
                        action_cycle_id=pending.action_cycle_id,
                        action_decision_id=pending.action_decision_id,
                        target_remaining_fraction=target_fraction,
                    )
                )
                continue
            gross = quantity * price
            fee = gross * candidate.fee_rate
            cash += gross - fee
            fees += fee
            turnover += gross
            remaining_shares = position.shares - quantity
            if remaining_shares <= 1e-12:
                positions.pop(pending.asset_code, None)
            else:
                positions[pending.asset_code] = replace(
                    position,
                    shares=remaining_shares,
                )
            events.append(
                _event(
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    candidate_config_hash=candidate_config_hash,
                    session_date=session_date,
                    event_type="sell_filled",
                    asset_code=pending.asset_code,
                    discriminator=pending.order_id,
                    rank=pending.rank,
                    side="sell",
                    quantity=quantity,
                    price=price,
                    fee=fee,
                    cash_after=cash,
                    action_cycle_id=pending.action_cycle_id,
                    action_decision_id=pending.action_decision_id,
                    target_remaining_fraction=target_fraction,
                    signal_to_fill_sessions=delay,
                )
            )
            continue

        if pending.asset_code in positions:
            continue
        target_value = float(pending.signal_equity) * float(pending.target_weight)
        gross_limit = min(target_value, cash / (1.0 + candidate.fee_rate))
        lots = math.floor(gross_limit / price / candidate.lot_size)
        quantity = float(lots * candidate.lot_size)
        if quantity <= 0:
            events.append(
                _event(
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    candidate_config_hash=candidate_config_hash,
                    session_date=session_date,
                    event_type="fill_rejected",
                    asset_code=pending.asset_code,
                    discriminator=pending.order_id,
                    rank=pending.rank,
                    side="buy",
                    cash_after=cash,
                    reason="insufficient_cash_for_one_lot",
                )
            )
            continue
        gross = quantity * price
        fee = gross * candidate.fee_rate
        cash -= gross + fee
        fees += fee
        turnover += gross
        positions[pending.asset_code] = ReplayPositionState(
            shares=quantity,
            exposure_baseline_shares=quantity,
            high_watermark=price,
            position_episode_id=stable_contract_hash(
                {
                    "run_id": run_id,
                    "candidate_id": candidate.candidate_id,
                    "candidate_config_hash": candidate_config_hash,
                    "order_id": pending.order_id,
                }
            ),
            exposure_version=1,
        )
        events.append(
            _event(
                run_id=run_id,
                candidate_id=candidate.candidate_id,
                candidate_config_hash=candidate_config_hash,
                session_date=session_date,
                event_type="buy_filled",
                asset_code=pending.asset_code,
                discriminator=pending.order_id,
                rank=pending.rank,
                side="buy",
                quantity=quantity,
                price=price,
                fee=fee,
                cash_after=cash,
                signal_to_fill_sessions=delay,
            )
        )
    return cash, positions, remaining, fees, turnover, events


def _apply_policy_outputs(
    *,
    run_id: str,
    candidate_config_hash: str,
    candidate: ReplayCandidateConfig,
    session_date: date,
    outputs: tuple[ReplayPolicyOutput, ...],
    cash: float,
    positions: dict[str, ReplayPositionState],
    pending: list[PendingFillState],
) -> list[CandidateReplayEvent]:
    events: list[CandidateReplayEvent] = []
    for output in sorted(
        outputs,
        key=lambda item: (item.asset_code, item.rule_id, item.action_decision_id),
    ):
        if not output.decision_eligible:
            raise ValueError("ineligible policy output cannot create an action")
        position = positions.get(output.asset_code)
        if position is None:
            raise ValueError("policy output requires an active candidate position")
        action_decisions = dict(position.action_decisions)
        identity = (output.action_cycle_id, output.target_remaining_fraction)
        prior_identity = action_decisions.get(output.action_decision_id)
        if prior_identity is not None:
            if prior_identity != identity:
                raise ValueError("conflicting replay action decision identity")
            continue
        action_decisions[output.action_decision_id] = identity
        cycle_targets = dict(position.cycle_targets)
        previous_target = cycle_targets.get(output.action_cycle_id)
        rule_states = dict(position.rule_states)
        alert_episode_ids = dict(position.alert_episode_ids)
        rule_states[output.rule_id] = "firing"
        alert_episode_ids[output.rule_id] = output.alert_episode_id
        if previous_target is not None and output.target_remaining_fraction >= previous_target:
            positions[output.asset_code] = replace(
                position,
                rule_states=rule_states,
                alert_episode_ids=alert_episode_ids,
                action_decisions=action_decisions,
            )
            continue
        cycle_targets[output.action_cycle_id] = output.target_remaining_fraction
        position = replace(
            position,
            rule_states=rule_states,
            alert_episode_ids=alert_episode_ids,
            action_decisions=action_decisions,
            cycle_targets=cycle_targets,
            action_cycle_id=output.action_cycle_id,
            current_action_decision_id=output.action_decision_id,
            current_target_remaining_fraction=output.target_remaining_fraction,
        )
        positions[output.asset_code] = position
        events.append(
            _event(
                run_id=run_id,
                candidate_id=candidate.candidate_id,
                candidate_config_hash=candidate_config_hash,
                session_date=session_date,
                event_type="action_decision",
                asset_code=output.asset_code,
                discriminator=output.action_decision_id,
                side="sell",
                cash_after=cash,
                action_cycle_id=output.action_cycle_id,
                action_decision_id=output.action_decision_id,
                target_remaining_fraction=output.target_remaining_fraction,
                reason=output.rule_id,
            )
        )
        current_fraction = position.shares / position.exposure_baseline_shares
        if output.target_remaining_fraction >= current_fraction - 1e-12:
            continue
        order_id = stable_contract_hash(
            {
                "run_id": run_id,
                "candidate_id": candidate.candidate_id,
                "candidate_config_hash": candidate_config_hash,
                "action_decision_id": output.action_decision_id,
                "side": "sell",
            }
        )
        if any(item.order_id == order_id for item in pending):
            continue
        pending.append(
            PendingFillState(
                order_id=order_id,
                asset_code=output.asset_code,
                signal_date=session_date,
                side="sell",
                rank=0,
                target_remaining_fraction=output.target_remaining_fraction,
                reason=output.rule_id,
                action_cycle_id=output.action_cycle_id,
                action_decision_id=output.action_decision_id,
            )
        )
    return events


def _mark_positions(
    *,
    positions: dict[str, ReplayPositionState],
    rows_by_code: Mapping[str, FeatureRow],
) -> tuple[dict[str, ReplayPositionState], float]:
    marked: dict[str, ReplayPositionState] = {}
    market_value = 0.0
    for code, position in positions.items():
        row = rows_by_code.get(code)
        if row is None:
            raise IncompleteCrossSectionError(
                f"active position {code} lacks a decision-eligible close mark"
            )
        marked[code] = replace(
            position,
            high_watermark=max(position.high_watermark, row.adjusted_close),
        )
        market_value += position.shares * row.adjusted_close
    return marked, market_value


def _advance_candidate_state(
    *,
    run_id: str,
    candidate_config_hash: str,
    candidate: ReplayCandidateConfig,
    session_date: date,
    ranked_rows: tuple[FeatureRow, ...],
    policy_outputs: tuple[ReplayPolicyOutput, ...],
    state: CandidatePortfolioState,
) -> tuple[CandidatePortfolioState, tuple[CandidateReplayEvent, ...], EquityCurvePoint]:
    rows_by_code = {row.asset_code: row for row in ranked_rows}
    cash, positions, pending, fees, turnover, events = _process_pending_fills(
        run_id=run_id,
        candidate_config_hash=candidate_config_hash,
        candidate=candidate,
        session_date=session_date,
        rows_by_code=rows_by_code,
        state=state,
    )
    positions, _ = _mark_positions(positions=positions, rows_by_code=rows_by_code)
    events.extend(
        _apply_policy_outputs(
            run_id=run_id,
            candidate_config_hash=candidate_config_hash,
            candidate=candidate,
            session_date=session_date,
            outputs=policy_outputs,
            cash=cash,
            positions=positions,
            pending=pending,
        )
    )
    positions, market_value = _mark_positions(
        positions=positions,
        rows_by_code=rows_by_code,
    )
    equity = cash + market_value
    selected = ranked_rows[: candidate.top_n]
    target_weight = 1.0 / len(selected) if selected else 0.0
    pending_buy_codes = {
        item.asset_code for item in pending if item.side == "buy"
    }
    for rank, row in enumerate(selected, start=1):
        events.append(
            _event(
                run_id=run_id,
                candidate_id=candidate.candidate_id,
                candidate_config_hash=candidate_config_hash,
                session_date=session_date,
                event_type="target_selected",
                asset_code=row.asset_code,
                discriminator=f"rank-{rank}",
                rank=rank,
                cash_after=cash,
            )
        )
        if row.asset_code in positions or row.asset_code in pending_buy_codes:
            continue
        order_id = stable_contract_hash(
            {
                "run_id": run_id,
                "candidate_id": candidate.candidate_id,
                "candidate_config_hash": candidate_config_hash,
                "session_date": session_date,
                "asset_code": row.asset_code,
                "side": "buy",
            }
        )
        pending.append(
            PendingFillState(
                order_id=order_id,
                asset_code=row.asset_code,
                signal_date=session_date,
                side="buy",
                rank=rank,
                target_weight=target_weight,
                signal_equity=equity,
            )
        )
        pending_buy_codes.add(row.asset_code)

    next_state = CandidatePortfolioState(
        cash=cash,
        positions=positions,
        pending_fills=tuple(
            sorted(
                pending,
                key=lambda item: (
                    item.signal_date,
                    0 if item.side == "sell" else 1,
                    item.rank,
                    item.asset_code,
                    item.order_id,
                ),
            )
        ),
        cumulative_fees=fees,
        turnover=turnover,
        equity=equity,
    )
    point = EquityCurvePoint(
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        candidate_config_hash=candidate_config_hash,
        session_date=session_date,
        equity=equity,
        cash=cash,
        market_value=market_value,
        cumulative_fees=fees,
        turnover=turnover,
    )
    return next_state, tuple(events), point


def run_replay_batch(
    *,
    feature_rows: Iterable[FeatureRow],
    manifests: Iterable[CrossSectionCompletionManifest],
    candidates: Iterable[ReplayCandidateConfig],
    policy_outputs: Iterable[ReplayPolicyOutput],
    request: ReplayBatchRequest,
    state: ReplayState | None = None,
    ranker: Callable[[tuple[FeatureRow, ...]], tuple[FeatureRow, ...]] = (
        rank_feature_cross_section
    ),
    clock: Callable[[], float] = time.monotonic,
) -> ReplayBatchResult:
    started = clock()
    candidate_values: list[ReplayCandidateConfig] = []
    for candidate in candidates:
        if len(candidate_values) >= MAX_REPLAY_CANDIDATES:
            raise BoundedWorkLimitError(
                f"candidates exceed registered limit {MAX_REPLAY_CANDIDATES}"
            )
        candidate_values.append(candidate)
    candidate_tuple = tuple(candidate_values)
    candidate_ids = tuple(candidate.candidate_id for candidate in candidate_tuple)
    if not candidate_tuple or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidates must be non-empty and unique")
    candidate_config_hash = canonical_candidate_config_hash(
        candidate_tuple,
        frozen_parameter_hash=request.frozen_parameter_hash,
    )
    current_state = state or _initial_state(candidate_tuple)
    if set(current_state.candidate_states) != set(candidate_ids):
        raise ValueError("replay state candidate ids do not match the run contract")
    if any(
        len(candidate_state.pending_fills) > request.max_pending_fills
        for candidate_state in current_state.candidate_states.values()
    ):
        raise BoundedWorkLimitError("checkpoint pending fills exceed explicit bound")

    manifest_items = _bounded_collect(
        manifests,
        limit=request.max_dates,
        label="manifest input max_dates",
        started=started,
        max_seconds=request.max_seconds,
        clock=clock,
        error_type=IncompleteCrossSectionError,
    )
    manifest_tuple = tuple(manifest_items)  # type: ignore[arg-type]
    if not manifest_tuple:
        raise IncompleteCrossSectionError("replay batch requires at least one manifest")
    page_input_snapshot_hash = manifest_tuple[0].input_snapshot_hash
    dates = tuple(manifest.session_date for manifest in manifest_tuple)
    if dates != tuple(sorted(set(dates))):
        raise IncompleteCrossSectionError("manifest dates must be sorted and unique")
    if any(
        manifest.run_id != request.run_id
        or manifest.input_snapshot_hash != page_input_snapshot_hash
        or not request.start_date <= manifest.session_date <= request.end_date
        or (request.after_date is not None and manifest.session_date <= request.after_date)
        for manifest in manifest_tuple
    ):
        raise IncompleteCrossSectionError("manifest page does not match replay cursor")
    expected_feature_rows = sum(
        manifest.expected_universe_count for manifest in manifest_tuple
    )
    if expected_feature_rows > request.max_feature_rows:
        raise BoundedWorkLimitError("selected manifests exceed max_feature_rows")
    feature_items = _bounded_collect(
        feature_rows,
        limit=expected_feature_rows,
        label="feature input for selected manifests",
        started=started,
        max_seconds=request.max_seconds,
        clock=clock,
        error_type=IncompleteCrossSectionError,
    )
    feature_tuple = tuple(feature_items)  # type: ignore[arg-type]
    if len(feature_tuple) != expected_feature_rows:
        raise IncompleteCrossSectionError("feature input count does not match manifests")
    policy_items = _bounded_collect(
        policy_outputs,
        limit=request.max_policy_outputs,
        label="policy output input",
        started=started,
        max_seconds=request.max_seconds,
        clock=clock,
        error_type=BoundedWorkLimitError,
    )
    policy_tuple = tuple(policy_items)  # type: ignore[arg-type]

    features_by_date: dict[date, tuple[FeatureRow, ...]] = {}
    for manifest in manifest_tuple:
        daily = tuple(
            row for row in feature_tuple if row.session_date == manifest.session_date
        )
        _validate_manifest(manifest, daily)
        features_by_date[manifest.session_date] = daily
    policies_by_candidate_date: dict[
        tuple[str, date], list[ReplayPolicyOutput]
    ] = {}
    for output in policy_tuple:
        if (
            output.candidate_id not in candidate_ids
            or output.session_date not in dates
            or output.candidate_config_hash != candidate_config_hash
            or output.frozen_parameter_hash != request.frozen_parameter_hash
            or output.input_snapshot_hash != page_input_snapshot_hash
        ):
            raise ValueError("policy output identity is outside the bounded replay page")
        policies_by_candidate_date.setdefault(
            (output.candidate_id, output.session_date), []
        ).append(output)

    rankings: list[DailyRankingEvent] = []
    events: list[CandidateReplayEvent] = []
    equity_curve: list[EquityCurvePoint] = []
    candidate_states = dict(current_state.candidate_states)
    for manifest in manifest_tuple:
        if clock() - started >= request.max_seconds:
            raise BoundedWorkLimitError("replay processing exceeded max_seconds")
        daily = features_by_date[manifest.session_date]
        ranked = tuple(
            _bounded_collect(
                ranker(daily),
                limit=manifest.expected_universe_count,
                label="ranker output",
                started=started,
                max_seconds=request.max_seconds,
                clock=clock,
                error_type=IncompleteCrossSectionError,
            )
        )
        ranked_codes = tuple(row.asset_code for row in ranked)
        if (
            len(ranked) != manifest.expected_universe_count
            or len(ranked_codes) != len(set(ranked_codes))
            or set(ranked_codes) != set(manifest.eligible_asset_codes)
        ):
            raise IncompleteCrossSectionError(
                "ranker output must contain each manifest asset exactly once"
            )
        rankings.append(
            DailyRankingEvent(
                run_id=request.run_id,
                session_date=manifest.session_date,
                ordered_asset_codes=ranked_codes,
                cross_section_hash=stable_contract_hash(
                    {
                        "manifest_hash": manifest.manifest_hash,
                        "features": tuple(
                            (row.feature_key, row.feature_hash) for row in ranked
                        ),
                    }
                ),
                manifest_hash=manifest.manifest_hash,
            )
        )
        for candidate in candidate_tuple:
            if clock() - started >= request.max_seconds:
                raise BoundedWorkLimitError("replay processing exceeded max_seconds")
            next_state, daily_events, point = _advance_candidate_state(
                run_id=request.run_id,
                candidate_config_hash=candidate_config_hash,
                candidate=candidate,
                session_date=manifest.session_date,
                ranked_rows=ranked,
                policy_outputs=tuple(
                    policies_by_candidate_date.get(
                        (candidate.candidate_id, manifest.session_date), []
                    )
                ),
                state=candidate_states[candidate.candidate_id],
            )
            if len(next_state.pending_fills) > request.max_pending_fills:
                raise BoundedWorkLimitError("replay pending fills exceed explicit bound")
            if len(events) + len(daily_events) > request.max_events:
                raise BoundedWorkLimitError("replay events exceed explicit bound")
            candidate_states[candidate.candidate_id] = next_state
            events.extend(daily_events)
            equity_curve.append(point)
            if clock() - started >= request.max_seconds:
                raise BoundedWorkLimitError("replay processing exceeded max_seconds")

    elapsed = max(clock() - started, 0.0)
    last_date = manifest_tuple[-1].session_date
    if not request.has_more and last_date != request.end_date:
        raise IncompleteCrossSectionError(
            "final replay page must end at the declared end_date"
        )
    return ReplayBatchResult(
        candidate_config_hash=candidate_config_hash,
        state=ReplayState(candidate_states=candidate_states),
        rankings=tuple(rankings),
        events=tuple(events),
        equity_curve=tuple(equity_curve),
        processed_dates=len(manifest_tuple),
        complete=not request.has_more,
        last_completed_date=last_date,
        next_after_date=last_date if request.has_more else None,
        worker_count=request.worker_count,
        query_count=3,
        batch_invocations=1,
        elapsed_seconds=elapsed,
        feature_rows_read=len(feature_tuple),
        manifest_rows_read=len(manifest_tuple),
        policy_outputs_read=len(policy_tuple),
    )


def merge_replay_events(
    *groups: Iterable[CandidateReplayEvent],
) -> tuple[CandidateReplayEvent, ...]:
    merged: dict[str, CandidateReplayEvent] = {}
    for group in groups:
        for event in group:
            existing = merged.get(event.event_key)
            if existing is not None and existing != event:
                raise ValueError(f"conflicting replay event: {event.event_key}")
            merged[event.event_key] = event
    return tuple(merged[key] for key in sorted(merged))
