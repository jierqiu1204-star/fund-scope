"""Frozen, research-only comparison of the three ETF strategy routes.

The module deliberately keeps PIT selection inputs separate from the future
valuation stream.  A decision snapshot is the only source used to choose a
monthly momentum target; the shared continuous ledger receives only the
already-frozen targets and its independently supplied adjusted closes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.services.etf_research_evidence import stable_contract_hash
from app.services.leader_tactics_exit_policy import (
    evaluate_leader_exit_thresholds,
    initial_leader_risk,
    leader_atr20,
)
from app.services.market_data import (
    ExchangeCalendarUnavailableError,
    is_etf_exchange_trading_day,
    next_etf_exchange_trading_day,
)
from app.services.short_research.daily_reconstructable import (
    REQUIRED_BAR_COUNT,
    DailyReconstructableUnavailableError,
    daily_reconstructable_manifest,
    score_daily_reconstructable,
)

from .dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    V2CandidateObservation,
    V2LifecycleTransition,
)
from .etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
    FROZEN_RANKING_CANDIDATES,
    RankingCandidateState,
    RankingRegimeLiquidityGateFact,
    evaluate_ranking_candidates,
    freeze_ranking_candidate_registry,
)
from .etf_ranking_forward_outcomes import (
    RANKING_PORTFOLIO_BASE_COST_POLICY,
    RANKING_PORTFOLIO_STRESS_COST_POLICY,
    ForwardAdjustedClose,
    RankingPortfolioLedger,
    RankingPortfolioTarget,
    calculate_continuous_ranking_portfolio,
    freeze_ranking_portfolio_target,
)
from .etf_ranking_replay_inputs import PointInTimeAdjustedSeries
from .etf_ranking_stage_b import (
    StageBRankingEvent,
    StageBReplayContract,
    _validate_source_date,
    build_stage_b_feature_input,
    build_stage_b_source_date,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")

COMPARISON_SCHEMA_VERSION = "etf_strategy_route_comparison_v1"
COMPARISON_EXPERIMENT_ID = "etf_leader_v2_vs_126d_momentum_vs_daily_core_hysteresis"

MEDIUM_TERM_MOMENTUM_LOOKBACK_SESSIONS = 126
MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS = 127
MEDIUM_TERM_MOMENTUM_TOP_N = 10
MEDIUM_TERM_MOMENTUM_TARGET_WEIGHT = 0.10

ROUTE_V2_BREAKOUT = "v2_breakout"
ROUTE_MEDIUM_TERM_MOMENTUM = "medium_term_momentum_126"
ROUTE_DAILY_CORE_HYSTERESIS = "daily_core_hysteresis"
COMPARISON_ROUTE_IDS = (
    ROUTE_V2_BREAKOUT,
    ROUTE_MEDIUM_TERM_MOMENTUM,
    ROUTE_DAILY_CORE_HYSTERESIS,
)

V2_EVENT_CONFIRMATION = "confirmation"
V2_EVENT_EXIT = "exit"
V2_STATE_CHECK_SCHEMA_VERSION = "etf_strategy_route_comparison_v2_day_check_v1"

# The legacy route is deliberately the default.  The two shared-rule modes
# are research identities and do not alter the production leader policy.
LEADER_EXIT_POLICY_LEGACY_MA5 = "legacy_ma5"
LEADER_EXIT_POLICY_SHARED_DAILY = "shared_daily"
LEADER_EXIT_POLICY_SHARED_DAILY_2R = "shared_daily_2r"
LEADER_EXIT_POLICY_IDS = (
    LEADER_EXIT_POLICY_LEGACY_MA5,
    LEADER_EXIT_POLICY_SHARED_DAILY,
    LEADER_EXIT_POLICY_SHARED_DAILY_2R,
)
LEADER_EXIT_POLICY_DEFAULT = LEADER_EXIT_POLICY_LEGACY_MA5
LEADER_EXIT_EXECUTION_MODEL = "next_session_adjusted_close_v1"
LEADER_EXIT_RESEARCH_SCHEMA_VERSION = "etf_leader_exit_comparison_v1"
LEADER_EXIT_RESEARCH_EXPERIMENT_PREFIX = f"{COMPARISON_EXPERIMENT_ID}:leader_exit"


class ComparisonContractError(ValueError):
    """Raised when a frozen comparison input or result is invalid."""


class ComparisonDataUnavailableError(ComparisonContractError):
    """Raised when a route cannot be evaluated without inventing data."""

    def __init__(
        self,
        reason: str,
        detail: str = "",
        *,
        signal_date: date | None = None,
    ) -> None:
        self.reason = reason
        self.signal_date = signal_date
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ComparisonContractError(f"{label} must be a SHA-256 hash")


def _finite(value: object) -> float:
    if isinstance(value, bool):
        raise ComparisonContractError("numeric values cannot be boolean")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ComparisonContractError("numeric value is invalid") from exc
    if not math.isfinite(parsed):
        raise ComparisonContractError("numeric value must be finite")
    return parsed


def _ordered_dates(values: Iterable[date], *, label: str) -> tuple[date, ...]:
    result = tuple(values)
    if not result or result != tuple(sorted(set(result))):
        raise ComparisonContractError(f"{label} must be non-empty, ordered and unique")
    return result


def _ordered_codes(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    result = tuple(values)
    if (
        len(result) != len(set(result))
        or any(not isinstance(value, str) or not value.strip() for value in result)
    ):
        raise ComparisonContractError(f"{label} must contain unique non-empty codes")
    return tuple(sorted(result))


def _row_key(row: ForwardAdjustedClose) -> tuple[str, date]:
    return row.asset_code, row.session_date


def _pit_series_payload(series: PointInTimeAdjustedSeries) -> dict[str, Any]:
    """Use the same value-level identity that the PIT series builder seals."""

    return {
        "asset_code": series.asset_code,
        "metadata": asdict(series.metadata),
        "bars": [asdict(item) for item in series.bars],
        "provenance": asdict(series.provenance),
        "earliest_source_timestamp": series.earliest_source_timestamp,
        "latest_source_timestamp": series.latest_source_timestamp,
        "revision_hashes": series.revision_hashes,
    }


def _safe(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass(frozen=True, slots=True)
class FrozenComparisonProvenance:
    """Immutable identities supplied by the actual PIT data pipeline."""

    source_mode: str
    data_version: str
    universe_policy_hash: str
    membership_policy_hash: str
    clone_policy_hash: str
    adjusted_price_policy_hash: str
    calendar_hash: str
    source_snapshot_hash: str
    cutoff_policy: str = "recorded_available_at_lte_signal_cutoff"

    def __post_init__(self) -> None:
        if not self.source_mode.strip() or not self.data_version.strip():
            raise ComparisonContractError("comparison source mode and data version are required")
        if self.cutoff_policy != "recorded_available_at_lte_signal_cutoff":
            raise ComparisonContractError("comparison cutoff policy is not frozen")
        for label, value in (
            ("universe policy hash", self.universe_policy_hash),
            ("membership policy hash", self.membership_policy_hash),
            ("clone policy hash", self.clone_policy_hash),
            ("adjusted price policy hash", self.adjusted_price_policy_hash),
            ("calendar hash", self.calendar_hash),
            ("source snapshot hash", self.source_snapshot_hash),
        ):
            _sha256(value, label)


@dataclass(frozen=True, slots=True)
class ComparisonDecisionSnapshot:
    """One cutoff-visible cross-section and its warmup history.

    ``adjusted_closes`` is intentionally local to this snapshot.  It is not
    reused for outcome valuation and may contain fewer than 127 rows; the
    preflight then reports that decision as unavailable.
    """

    signal_date: date
    decision_cutoff: datetime
    source_hash: str
    eligible_asset_codes: tuple[str, ...]
    nonclone_asset_codes: tuple[str, ...]
    adjusted_closes: tuple[ForwardAdjustedClose, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.decision_cutoff, datetime):
            raise ComparisonContractError("decision cutoff must be a datetime")
        if self.decision_cutoff.tzinfo is None or self.decision_cutoff.utcoffset() is None:
            raise ComparisonContractError("decision cutoff must be timezone-aware")
        if self.decision_cutoff.date() != self.signal_date:
            raise ComparisonContractError("decision cutoff must be on its signal session")
        _sha256(self.source_hash, "decision snapshot source hash")
        eligible = _ordered_codes(self.eligible_asset_codes, label="eligible assets")
        nonclone = _ordered_codes(self.nonclone_asset_codes, label="nonclone assets")
        if not set(nonclone).issubset(eligible):
            raise ComparisonContractError("nonclone assets must be cutoff-visible eligible assets")
        rows = tuple(self.adjusted_closes)
        seen: set[tuple[str, date]] = set()
        for row in rows:
            if not isinstance(row, ForwardAdjustedClose):
                raise ComparisonContractError("decision snapshot requires adjusted close facts")
            key = _row_key(row)
            if key in seen:
                raise ComparisonContractError("decision snapshot has duplicate adjusted close")
            seen.add(key)
            if row.session_date > self.signal_date:
                raise ComparisonContractError("decision snapshot contains future price")
            if row.asset_code not in eligible:
                raise ComparisonContractError("decision snapshot price is outside eligible assets")
            _sha256(row.source_hash, "adjusted close source hash")
        if rows != tuple(sorted(rows, key=_row_key)):
            raise ComparisonContractError("decision snapshot rows must be canonical")

    @property
    def snapshot_hash(self) -> str:
        return self.source_hash


@dataclass(frozen=True, slots=True)
class ComparisonValuationInput:
    """Future valuation prices and a separately proven exchange calendar."""

    trading_sessions: tuple[date, ...]
    adjusted_closes: tuple[ForwardAdjustedClose, ...]
    calendar_sessions: tuple[date, ...]
    calendar_complete_through: date
    month_end_sessions: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        sessions = _ordered_dates(self.trading_sessions, label="valuation sessions")
        calendar = _ordered_dates(self.calendar_sessions, label="calendar sessions")
        if not set(sessions).issubset(calendar):
            raise ComparisonContractError("valuation sessions must be in exchange calendar")
        self._validate_exchange_calendar(sessions=sessions, calendar=calendar)
        if self.calendar_complete_through < calendar[-1]:
            raise ComparisonContractError("calendar completeness ends before supplied calendar")
        month_ends = tuple(self.month_end_sessions)
        if len(month_ends) != len(set(month_ends)) or any(item not in calendar for item in month_ends):
            raise ComparisonContractError("month-end sessions must be unique calendar sessions")
        for month_end in month_ends:
            try:
                next_session = next_etf_exchange_trading_day(month_end)
            except ExchangeCalendarUnavailableError as exc:
                raise ComparisonContractError(
                    "month-end session cannot be verified by the exchange calendar"
                ) from exc
            if (month_end.year, month_end.month) == (next_session.year, next_session.month):
                raise ComparisonContractError(
                    f"month-end session is not the last exchange session: {month_end.isoformat()}"
                )
        rows = tuple(self.adjusted_closes)
        seen: set[tuple[str, date]] = set()
        for row in rows:
            if not isinstance(row, ForwardAdjustedClose):
                raise ComparisonContractError("valuation requires adjusted close facts")
            key = _row_key(row)
            if key in seen:
                raise ComparisonContractError("valuation contains duplicate adjusted close")
            seen.add(key)
            if row.session_date not in sessions:
                raise ComparisonContractError("valuation price is outside trading sessions")
            _sha256(row.source_hash, "adjusted close source hash")
        if rows != tuple(sorted(rows, key=_row_key)):
            raise ComparisonContractError("valuation rows must be canonical")
        if sessions != self.trading_sessions or calendar != self.calendar_sessions:
            raise ComparisonContractError("calendar sequences must be canonical")

    @staticmethod
    def _validate_exchange_calendar(
        *, sessions: tuple[date, ...], calendar: tuple[date, ...]
    ) -> None:
        for label, values in (("calendar", calendar), ("valuation", sessions)):
            for day in values:
                try:
                    if not is_etf_exchange_trading_day(day):
                        raise ComparisonContractError(
                            f"{label} contains non-exchange session {day.isoformat()}"
                        )
                    next_etf_exchange_trading_day(day)
                except ExchangeCalendarUnavailableError as exc:
                    raise ComparisonContractError(
                        f"{label} uses an unknown exchange-calendar year"
                    ) from exc
        for label, values in (("calendar", calendar), ("valuation", sessions)):
            for current, following in zip(values, values[1:], strict=False):
                try:
                    expected = next_etf_exchange_trading_day(current)
                except ExchangeCalendarUnavailableError as exc:
                    raise ComparisonContractError(
                        f"{label} uses an unknown exchange-calendar year"
                    ) from exc
                if following != expected:
                    raise ComparisonContractError(
                        f"{label} omits an exchange session after {current.isoformat()}"
                    )

    def month_end_dates(self, *, start_date: date, end_date: date) -> tuple[date, ...]:
        """Return only exchange-calendar-confirmed month ends in the range.

        An explicit month-end fact is preferred.  Otherwise a date is inferred
        only when the supplied calendar contains a later session and proves the
        next session belongs to another month.  A terminal partial calendar is
        therefore never mistaken for a month end.
        """

        calendar = self.calendar_sessions
        if self.month_end_sessions:
            return tuple(
                day for day in self.month_end_sessions if start_date <= day <= end_date
            )
        result: list[date] = []
        for _index, day in enumerate(calendar):
            if not start_date <= day <= end_date:
                continue
            try:
                next_day = next_etf_exchange_trading_day(day)
            except ExchangeCalendarUnavailableError:
                continue
            if (day.year, day.month) != (next_day.year, next_day.month):
                result.append(day)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class V2ComparisonEvent:
    """A causal V2 confirmation or exit at its decision date.

    The event has no execution date.  The existing ledger applies the one
    next-session shift exactly once.
    """

    signal_date: date
    original_signal_date: date
    asset_code: str
    event_type: Literal["confirmation", "exit"]
    score: float
    formula_id: str
    clone_group: str
    source_hash: str

    def __post_init__(self) -> None:
        if not self.asset_code.strip() or not self.clone_group.strip():
            raise ComparisonContractError("V2 event asset and clone group are required")
        if self.original_signal_date > self.signal_date:
            raise ComparisonContractError(
                "V2 event original signal cannot follow its transition date"
            )
        if self.event_type not in {V2_EVENT_CONFIRMATION, V2_EVENT_EXIT}:
            raise ComparisonContractError("V2 event type is unsupported")
        if self.formula_id != BREAKOUT_V2:
            raise ComparisonContractError("comparison V2 route accepts breakout events only")
        if not math.isfinite(self.score):
            raise ComparisonContractError("V2 event score must be finite")
        _sha256(self.source_hash, "V2 event source hash")


@dataclass(frozen=True, slots=True)
class V2StateCheck:
    """Evidence that a full V2 state observation was checked for one date.

    The observation key sets are populated by a replay that has the durable
    required and checked manifests.  They must match exactly; the bridge also
    checks every consumed event and held lifecycle key against that set.
    """

    session_date: date
    manifest_hash: str
    input_hash: str
    state_hash: str
    checked_through: date
    observation_count: int
    transition_count: int
    checked_through_cutoff: datetime
    required_observation_keys: tuple[str, ...]
    checked_observation_keys: tuple[str, ...]
    transition_source_hashes: tuple[str, ...]
    observation_digest: str
    transition_digest: str
    lifecycle_checked: bool

    def __post_init__(self) -> None:
        for label, value in (
            ("V2 manifest hash", self.manifest_hash),
            ("V2 input hash", self.input_hash),
            ("V2 state hash", self.state_hash),
        ):
            _sha256(value, label)
        if self.checked_through < self.session_date:
            raise ComparisonContractError("V2 state check ends before its session")
        if (
            self.checked_through_cutoff.tzinfo is None
            or self.checked_through_cutoff.utcoffset() is None
            or self.checked_through_cutoff.date() < self.checked_through
        ):
            raise ComparisonContractError(
                "V2 checked-through cutoff must be timezone-aware and complete"
            )
        for label, value in (
            ("V2 observation count", self.observation_count),
            ("V2 transition count", self.transition_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ComparisonContractError(f"{label} must be non-negative")
        for label, values in (
            ("V2 required observation keys", self.required_observation_keys),
            ("V2 checked observation keys", self.checked_observation_keys),
        ):
            if (
                tuple(values) != tuple(sorted(set(values)))
                or any(not isinstance(code, str) or not code.strip() for code in values)
            ):
                raise ComparisonContractError(f"{label} must be unique non-empty codes")
            for key in values:
                parts = key.split(":")
                if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
                    raise ComparisonContractError(f"{label} have invalid lifecycle keys")
                try:
                    date.fromisoformat(parts[1])
                except ValueError as exc:
                    raise ComparisonContractError(f"{label} have invalid lifecycle keys") from exc
        if self.required_observation_keys != self.checked_observation_keys:
            raise ComparisonContractError(
                "V2 required observation keys are not fully checked"
            )
        if len(self.checked_observation_keys) != self.observation_count:
            raise ComparisonContractError(
                "V2 observed asset evidence count does not match observation count"
            )
        hashes = tuple(self.transition_source_hashes)
        if hashes != tuple(sorted(set(hashes))):
            raise ComparisonContractError("V2 transition evidence hashes must be unique")
        for value in hashes:
            _sha256(value, "V2 transition evidence hash")
        if len(hashes) != self.transition_count:
            raise ComparisonContractError(
                "V2 transition evidence count does not match transition count"
            )
        _sha256(self.observation_digest, "V2 observation digest")
        _sha256(self.transition_digest, "V2 transition digest")
        if self.lifecycle_checked is not True:
            raise ComparisonContractError("V2 lifecycle evidence must be checked")
        expected_state_hash = stable_contract_hash(
            {
                "schema_version": V2_STATE_CHECK_SCHEMA_VERSION,
                "session_date": self.session_date,
                "manifest_hash": self.manifest_hash,
                "input_hash": self.input_hash,
                "observation_digest": self.observation_digest,
                "transition_digest": self.transition_digest,
                "checked_through": self.checked_through,
                "checked_through_cutoff": self.checked_through_cutoff,
                "lifecycle_checked": self.lifecycle_checked,
                "required_observation_keys": self.required_observation_keys,
                "checked_observation_keys": self.checked_observation_keys,
                "transition_source_hashes": self.transition_source_hashes,
            }
        )
        if self.state_hash != expected_state_hash:
            raise ComparisonContractError("V2 state hash is not canonical")


def _v2_observation_key(
    *,
    asset_code: str,
    signal_date: date,
    formula_id: str,
) -> str:
    return f"{asset_code}:{signal_date.isoformat()}:{formula_id}"


def build_v2_comparison_events(
    *,
    observations: Iterable[V2CandidateObservation],
    transitions: Iterable[V2LifecycleTransition],
) -> tuple[V2ComparisonEvent, ...]:
    """Adapt sealed V2 lifecycle output without using its execution hint.

    ``transition_date`` is the causal decision date.  A lifecycle's
    ``simulated_execution_date`` is intentionally ignored; the shared ledger
    performs the single next-session execution shift.
    """

    observation_by_key: dict[tuple[str, date, str], V2CandidateObservation] = {}
    for observation in observations:
        key = (observation.asset_code, observation.signal_date, observation.formula_id)
        if key in observation_by_key:
            raise ComparisonContractError("duplicate V2 observation key")
        observation_by_key[key] = observation
    events: list[V2ComparisonEvent] = []
    for transition in transitions:
        if transition.universe != "etf" or transition.to_state not in {
            "confirmed",
            "invalidated",
        }:
            continue
        if transition.formula_id != BREAKOUT_V2:
            raise ComparisonContractError("V2 comparison transitions must be breakout-only")
        transition_payload = asdict(transition)
        transition_payload.pop("transition_hash")
        if transition.transition_hash != stable_contract_hash(transition_payload):
            raise ComparisonContractError("V2 transition hash is invalid")
        observation = observation_by_key.get(
            (transition.asset_code, transition.signal_date, transition.formula_id)
        )
        if observation is None or observation.qualifies is not True:
            raise ComparisonContractError("V2 transition has no qualifying observation")
        if observation.universe != "etf" or observation.availability != "available":
            raise ComparisonContractError("V2 event observation is not an available ETF fact")
        source_cutoff = observation.source_cutoff
        # The persisted V2 ETF reader historically materializes UTC timestamps
        # as naive datetimes.  Preserve that exact object for the canonical
        # observation hash, then interpret it as UTC only for chronology.
        if source_cutoff.tzinfo is None or source_cutoff.utcoffset() is None:
            source_cutoff = source_cutoff.replace(tzinfo=UTC)
        local_cutoff_date = source_cutoff.astimezone(_SHANGHAI).date()
        if local_cutoff_date < observation.signal_date:
            raise ComparisonContractError("V2 observation cutoff precedes signal date")
        if local_cutoff_date > transition.transition_date:
            raise ComparisonContractError(
                "V2 observation cutoff follows its causal transition date"
            )
        if observation.feature_hash != stable_contract_hash(observation.canonical_payload()):
            raise ComparisonContractError("V2 observation feature hash is invalid")
        if observation.score is None or not math.isfinite(observation.score):
            raise ComparisonContractError("V2 observation score is unavailable")
        if not observation.clone_group:
            raise ComparisonContractError("V2 observation clone group is unavailable")
        _sha256(observation.feature_hash, "V2 observation feature hash")
        events.append(
            V2ComparisonEvent(
                signal_date=transition.transition_date,
                original_signal_date=transition.signal_date,
                asset_code=transition.asset_code,
                event_type=(
                    V2_EVENT_CONFIRMATION
                    if transition.to_state == "confirmed"
                    else V2_EVENT_EXIT
                ),
                score=float(observation.score),
                formula_id=transition.formula_id,
                clone_group=observation.clone_group,
                source_hash=transition.transition_hash,
            )
        )
    if len(events) != len({
        (item.signal_date, item.original_signal_date, item.asset_code, item.event_type)
        for item in events
    }):
        raise ComparisonContractError("duplicate V2 transition event")
    return tuple(
        sorted(
            events,
            key=lambda item: (
                item.signal_date,
                item.event_type,
                -item.score,
                item.asset_code,
                item.source_hash,
            ),
        )
    )


def build_v2_state_check(
    *,
    session_date: date,
    manifest_hash: str,
    input_hash: str,
    state_hash: str,
    checked_through: date,
    observation_count: int,
    transition_count: int,
    checked_through_cutoff: datetime,
    required_observation_keys: tuple[str, ...],
    checked_observation_keys: tuple[str, ...],
    transition_source_hashes: tuple[str, ...],
    observation_digest: str,
    transition_digest: str,
    lifecycle_checked: bool,
) -> V2StateCheck:
    """Construct the explicit daily state-evidence record supplied by replay."""

    return V2StateCheck(
        session_date=session_date,
        manifest_hash=manifest_hash,
        input_hash=input_hash,
        state_hash=state_hash,
        checked_through=checked_through,
        observation_count=observation_count,
        transition_count=transition_count,
        checked_through_cutoff=checked_through_cutoff,
        required_observation_keys=required_observation_keys,
        checked_observation_keys=checked_observation_keys,
        transition_source_hashes=transition_source_hashes,
        observation_digest=observation_digest,
        transition_digest=transition_digest,
        lifecycle_checked=lifecycle_checked,
    )


@dataclass(frozen=True, slots=True)
class CommonPoolReadiness:
    signal_date: date
    required_history_sessions: int
    denominator: int
    numerator: int
    available: bool
    missing_by_asset: tuple[tuple[str, str], ...]
    reason: str
    earliest_evaluable_date: date | None
    source_hash: str
    readiness_hash: str


@dataclass(frozen=True, slots=True)
class MediumTermMomentumSelection:
    signal_date: date
    selected_asset_codes: tuple[str, ...]
    momentum_returns: tuple[tuple[str, float], ...]
    target_weights: tuple[tuple[str, float], ...]
    cash_target_weight: float
    requested_asset_count: int
    priced_asset_count: int
    positive_asset_count: int
    coverage_ratio: float
    exclusions: tuple[tuple[str, str], ...]
    contract_hash: str
    input_hash: str
    selection_hash: str


@dataclass(frozen=True, slots=True)
class ComparisonRouteResult:
    route_id: str
    status: Literal["completed", "unavailable"]
    reason: str | None
    required_signal_dates: tuple[date, ...]
    targets: tuple[RankingPortfolioTarget, ...]
    base_ledger: RankingPortfolioLedger | None
    stress_ledger: RankingPortfolioLedger | None
    input_hash: str
    leader_exit_policy: str = LEADER_EXIT_POLICY_DEFAULT
    leader_exit_policy_hash: str | None = None
    leader_exit_records: tuple[LeaderExitComparisonRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class LeaderExitComparisonRecord:
    """Auditable daily exit identity for a shared-rule V2 holding."""

    policy_id: str
    policy_hash: str
    asset_code: str
    original_signal_date: date
    confirmation_date: date
    entry_execution_date: date
    entry_reference_price: float
    entry_atr20: float
    signal_low: float
    initial_stop: float
    risk_unit: float
    exit_signal_date: date | None
    exit_execution_date: date | None
    exit_reason: str | None
    data_eligible: bool
    execution_model: str = LEADER_EXIT_EXECUTION_MODEL

    def __post_init__(self) -> None:
        if self.policy_id not in LEADER_EXIT_POLICY_IDS:
            raise ComparisonContractError("leader exit policy is unsupported")
        _sha256(self.policy_hash, "leader exit policy hash")
        if not self.asset_code.strip():
            raise ComparisonContractError("leader exit record asset is required")
        for value in (
            self.entry_reference_price,
            self.entry_atr20,
            self.signal_low,
            self.initial_stop,
            self.risk_unit,
        ):
            _finite(value)
        if self.entry_reference_price <= 0 or self.entry_atr20 <= 0:
            raise ComparisonContractError("leader exit entry facts must be positive")
        if self.initial_stop <= 0 or self.initial_stop >= self.entry_reference_price:
            raise ComparisonContractError("leader exit initial stop is invalid")
        if self.risk_unit <= 0:
            raise ComparisonContractError("leader exit risk unit must be positive")
        if self.exit_execution_date is not None and self.exit_signal_date is None:
            raise ComparisonContractError("leader exit execution requires a signal date")
        if self.exit_signal_date is not None and self.exit_signal_date < self.entry_execution_date:
            raise ComparisonContractError("leader exit cannot precede entry execution")
        if self.exit_execution_date is not None and self.exit_execution_date <= self.exit_signal_date:
            raise ComparisonContractError("leader exit execution must follow its signal")
        if not isinstance(self.data_eligible, bool):
            raise ComparisonContractError("leader exit data eligibility must be boolean")


@dataclass(frozen=True, slots=True)
class SharedDailyV2BridgeResult:
    """Targets plus the shared-rule diagnostics used to produce them."""

    targets: tuple[RankingPortfolioTarget, ...]
    exit_records: tuple[LeaderExitComparisonRecord, ...]


@dataclass(frozen=True, slots=True)
class ComparisonInput:
    provenance: FrozenComparisonProvenance
    start_date: date
    end_date: date
    valuation: ComparisonValuationInput
    decision_snapshots: tuple[ComparisonDecisionSnapshot, ...]
    v2_events: tuple[V2ComparisonEvent, ...]
    v2_state_checks: tuple[V2StateCheck, ...]
    daily_core_targets: tuple[RankingPortfolioTarget, ...]
    daily_core_required_signal_dates: tuple[date, ...]
    initial_capital: float = 1.0
    leader_exit_policy: str = LEADER_EXIT_POLICY_DEFAULT
    leader_exit_pit_series: tuple[PointInTimeAdjustedSeries, ...] = ()

    def __post_init__(self) -> None:
        sessions = self.valuation.trading_sessions
        if self.start_date not in sessions or self.end_date not in sessions:
            raise ComparisonContractError("comparison bounds must be valuation sessions")
        if self.start_date > self.end_date:
            raise ComparisonContractError("comparison start must not follow end")
        if isinstance(self.initial_capital, bool) or not math.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ComparisonContractError("comparison initial capital must be positive")
        if self.leader_exit_policy not in LEADER_EXIT_POLICY_IDS:
            raise ComparisonContractError("leader exit policy is unsupported")
        pit_series = tuple(self.leader_exit_pit_series)
        if any(
            not isinstance(series, PointInTimeAdjustedSeries) or not series.bars
            for series in pit_series
        ):
            raise ComparisonContractError("leader exit PIT series has an invalid type or no bars")
        if pit_series != tuple(
            sorted(
                pit_series,
                key=lambda item: (item.bars[-1].session_date, item.asset_code),
            )
        ):
            raise ComparisonContractError("leader exit PIT series must be canonical")
        seen_pit_keys: set[tuple[str, date]] = set()
        for series in pit_series:
            key = (series.asset_code, series.bars[-1].session_date)
            if key in seen_pit_keys:
                raise ComparisonContractError("leader exit PIT series has duplicates")
            seen_pit_keys.add(key)
            if series.bars[-1].session_date not in sessions:
                raise ComparisonContractError(
                    "leader exit PIT series is outside the valuation calendar"
                )
            if series.metadata.asset_code != series.asset_code:
                raise ComparisonContractError("leader exit PIT metadata code does not match series")
            if series.synchronized_after_cutoff:
                raise ComparisonContractError("leader exit PIT series is synchronized after cutoff")
            if stable_contract_hash(_pit_series_payload(series)) != series.series_hash:
                raise ComparisonContractError("leader exit PIT series hash is invalid")
            if not series.bars or tuple(item.session_date for item in series.bars) != tuple(
                sorted({item.session_date for item in series.bars})
            ):
                raise ComparisonContractError("leader exit PIT bars must be ordered and unique")
        snapshots = tuple(self.decision_snapshots)
        dates = tuple(item.signal_date for item in snapshots)
        if len(dates) != len(set(dates)) or dates != tuple(sorted(dates)):
            raise ComparisonContractError("decision snapshots must be chronologically unique")
        if any(item.signal_date not in sessions for item in snapshots):
            raise ComparisonContractError("decision snapshot is outside valuation calendar")
        checks = tuple(self.v2_state_checks)
        check_dates = tuple(item.session_date for item in checks)
        if len(check_dates) != len(set(check_dates)) or check_dates != tuple(sorted(check_dates)):
            raise ComparisonContractError("V2 state checks must be chronologically unique")
        if any(item.session_date not in sessions for item in checks):
            raise ComparisonContractError("V2 state check is outside valuation calendar")
        _ordered_dates(self.daily_core_required_signal_dates, label="daily core required dates") if self.daily_core_required_signal_dates else None
        if any(item not in sessions for item in self.daily_core_required_signal_dates):
            raise ComparisonContractError("daily core required date is outside calendar")
        for target in self.daily_core_targets:
            _validate_target(target, sessions)
        for event in self.v2_events:
            if event.signal_date not in sessions:
                raise ComparisonContractError("V2 event is outside valuation calendar")


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    schema_version: str
    experiment_id: str
    provenance: FrozenComparisonProvenance
    initial_capital: float
    start_date: date
    end_date: date
    routes: tuple[ComparisonRouteResult, ...]
    common_status: Literal["complete", "prefix_only", "unavailable"]
    common_start_date: date | None
    common_end_date: date | None
    common_reason: str | None
    input_hash: str
    result_hash: str
    report_markdown: str
    leader_exit_policy: str = LEADER_EXIT_POLICY_DEFAULT
    leader_exit_policy_hash: str = ""


MEDIUM_TERM_MOMENTUM_CONTRACT_HASH = stable_contract_hash(
    {
        "schema_version": "positive_adjusted_return_126_session_top10_v1",
        "lookback_sessions": MEDIUM_TERM_MOMENTUM_LOOKBACK_SESSIONS,
        "required_history_sessions": MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS,
        "eligibility": "strictly_positive_return_after_common_nonclone_history_gate",
        "ordering": ("adjusted_return_desc", "asset_code_asc"),
        "top_n": MEDIUM_TERM_MOMENTUM_TOP_N,
        "target_weight": MEDIUM_TERM_MOMENTUM_TARGET_WEIGHT,
        "unfilled_weight": "cash",
        "promotion_candidate": False,
        "parameter_search": False,
    }
)
V2_COMPARISON_ALLOCATION_POLICY_HASH = stable_contract_hash(
    {
        "schema_version": "etf_v2_comparison_allocation_v1",
        "max_positions": 10,
        "target_weight": 0.1,
        "exit_before_entry": True,
        "membership_change": "rebalance_all_held_and_new_positions",
        "unchanged_membership": "no_target",
        "capacity": "no_delayed_entry_queue_no_displacement",
    }
)
_LEADER_EXIT_SHARED_DAILY_POLICY_PAYLOAD = {
    "schema_version": LEADER_EXIT_RESEARCH_SCHEMA_VERSION,
    "policy_id": LEADER_EXIT_POLICY_SHARED_DAILY,
    "initial_risk": "max(signal_low, entry_reference - 2*entry_atr20)",
    "arming": "entry_reference + 1*risk_unit",
    "exit_line": "max(initial_stop, breakeven_after_arming, ma5)",
    "priority": ("hard_stop", "breakeven", "ma5"),
    "execution_model": LEADER_EXIT_EXECUTION_MODEL,
    "entry_reference": "next_session_adjusted_close",
    "signal_low_and_atr": "point_in_time_bars_only",
    "parameter_search": False,
}
LEADER_EXIT_SHARED_DAILY_POLICY_HASH = stable_contract_hash(
    _LEADER_EXIT_SHARED_DAILY_POLICY_PAYLOAD
)
LEADER_EXIT_POLICY_HASHES = (
    (
        LEADER_EXIT_POLICY_LEGACY_MA5,
        stable_contract_hash(
            {
                "schema_version": LEADER_EXIT_RESEARCH_SCHEMA_VERSION,
                "policy_id": LEADER_EXIT_POLICY_LEGACY_MA5,
                "lifecycle": "existing_v2_confirmation_and_ma5_invalidation",
                "preserve_existing_results": True,
                "parameter_search": False,
            }
        ),
    ),
    (
        LEADER_EXIT_POLICY_SHARED_DAILY,
        LEADER_EXIT_SHARED_DAILY_POLICY_HASH,
    ),
    (
        LEADER_EXIT_POLICY_SHARED_DAILY_2R,
        stable_contract_hash(
            {
                "schema_version": LEADER_EXIT_RESEARCH_SCHEMA_VERSION,
                "policy_id": LEADER_EXIT_POLICY_SHARED_DAILY_2R,
                "base_policy_hash": LEADER_EXIT_SHARED_DAILY_POLICY_HASH,
                "take_profit": "entry_reference + 2*risk_unit",
                "execution_model": LEADER_EXIT_EXECUTION_MODEL,
                "parameter_search": False,
            }
        ),
    ),
)
LEADER_EXIT_POLICY_HASH_MAP = dict(LEADER_EXIT_POLICY_HASHES)


def leader_exit_policy_hash(policy_id: str) -> str:
    try:
        return LEADER_EXIT_POLICY_HASH_MAP[policy_id]
    except KeyError as exc:
        raise ComparisonContractError("leader exit policy is unsupported") from exc


def comparison_experiment_id_for_policy(policy_id: str) -> str:
    if policy_id == LEADER_EXIT_POLICY_LEGACY_MA5:
        return COMPARISON_EXPERIMENT_ID
    if policy_id not in LEADER_EXIT_POLICY_IDS:
        raise ComparisonContractError("leader exit policy is unsupported")
    return f"{LEADER_EXIT_RESEARCH_EXPERIMENT_PREFIX}:{policy_id}"


def comparison_schema_version_for_policy(policy_id: str) -> str:
    if policy_id == LEADER_EXIT_POLICY_LEGACY_MA5:
        return COMPARISON_SCHEMA_VERSION
    if policy_id not in LEADER_EXIT_POLICY_IDS:
        raise ComparisonContractError("leader exit policy is unsupported")
    return "etf_strategy_route_comparison_v2"


DAILY_CORE_COMPARISON_POLICY_HASH = stable_contract_hash(
    {
        "schema_version": "daily_core_hysteresis_comparison_input_v1",
        "source": "daily_reconstructable_v1_target_facts",
        "selection": "caller_supplied_frozen_daily_core_targets",
        "parameters_mutated": False,
    }
)
COMPARISON_ROUTE_POLICY_HASHES = (
    (ROUTE_V2_BREAKOUT, V2_COMPARISON_ALLOCATION_POLICY_HASH),
    (ROUTE_MEDIUM_TERM_MOMENTUM, MEDIUM_TERM_MOMENTUM_CONTRACT_HASH),
    (ROUTE_DAILY_CORE_HYSTERESIS, DAILY_CORE_COMPARISON_POLICY_HASH),
)


def comparison_route_policy_hashes_for_policy(
    policy_id: str,
) -> tuple[tuple[str, str], ...]:
    """Return route identities bound to the selected leader-exit policy."""

    if policy_id == LEADER_EXIT_POLICY_LEGACY_MA5:
        return COMPARISON_ROUTE_POLICY_HASHES
    if policy_id not in LEADER_EXIT_POLICY_IDS:
        raise ComparisonContractError("leader exit policy is unsupported")
    v2_hash = stable_contract_hash(
        {
            "allocation_policy_hash": V2_COMPARISON_ALLOCATION_POLICY_HASH,
            "leader_exit_policy_hash": leader_exit_policy_hash(policy_id),
        }
    )
    return (
        (ROUTE_V2_BREAKOUT, v2_hash),
        (ROUTE_MEDIUM_TERM_MOMENTUM, MEDIUM_TERM_MOMENTUM_CONTRACT_HASH),
        (ROUTE_DAILY_CORE_HYSTERESIS, DAILY_CORE_COMPARISON_POLICY_HASH),
    )


def _validate_target(target: RankingPortfolioTarget, sessions: Sequence[date]) -> None:
    if not isinstance(target, RankingPortfolioTarget) or target.signal_date not in sessions:
        raise ComparisonContractError("route target is outside the valuation calendar")
    expected = freeze_ranking_portfolio_target(
        signal_date=target.signal_date,
        target_weights=target.target_weights,
        source_hash=target.source_hash,
    )
    if expected != target:
        raise ComparisonContractError("route target hash is invalid")


def _snapshot_rows(snapshot: ComparisonDecisionSnapshot) -> dict[tuple[str, date], ForwardAdjustedClose]:
    return {_row_key(row): row for row in snapshot.adjusted_closes}


def _complete_history_codes(
    *,
    snapshot: ComparisonDecisionSnapshot,
    trading_sessions: Sequence[date],
) -> tuple[str, ...]:
    expected = _snapshot_history_sessions(snapshot, trading_sessions)
    if len(expected) != MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS:
        return ()
    rows = _snapshot_rows(snapshot)
    return tuple(
        code
        for code in snapshot.nonclone_asset_codes
        if all((code, day) in rows for day in expected)
    )


def _snapshot_history_sessions(
    snapshot: ComparisonDecisionSnapshot,
    trading_sessions: Sequence[date],
) -> tuple[date, ...]:
    try:
        index = tuple(trading_sessions).index(snapshot.signal_date)
    except ValueError as exc:
        raise ComparisonContractError("snapshot date is outside valuation sessions") from exc
    return tuple(trading_sessions[: index + 1][-MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS :])


def preflight_common_pool(
    *,
    snapshot: ComparisonDecisionSnapshot,
    trading_sessions: Sequence[date],
) -> CommonPoolReadiness:
    """Check the non-clone history denominator before any signal filter."""

    sessions = _ordered_dates(trading_sessions, label="preflight sessions")
    expected = _snapshot_history_sessions(snapshot, sessions)
    rows = _snapshot_rows(snapshot)
    missing: list[tuple[str, str]] = []
    complete = 0
    complete_codes = _complete_history_codes(
        snapshot=snapshot,
        trading_sessions=sessions,
    )
    complete_set = set(complete_codes)
    for code in snapshot.nonclone_asset_codes:
        code_rows = [rows.get((code, day)) for day in expected]
        if len(expected) != MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS:
            missing.append((code, "insufficient_127_session_calendar_history"))
        elif code not in complete_set or any(row is None for row in code_rows):
            missing.append((code, "missing_cutoff_visible_adjusted_close"))
        else:
            complete += 1
    denominator = len(snapshot.nonclone_asset_codes)
    available = denominator >= MEDIUM_TERM_MOMENTUM_TOP_N and complete >= MEDIUM_TERM_MOMENTUM_TOP_N
    if denominator < MEDIUM_TERM_MOMENTUM_TOP_N:
        reason = "insufficient_common_nonclone_universe"
    elif complete < MEDIUM_TERM_MOMENTUM_TOP_N:
        reason = "insufficient_common_127_session_history"
    else:
        reason = "ready"
    earliest = (
        expected[-1]
        if available and expected
        else None
    )
    payload = {
        "signal_date": snapshot.signal_date,
        "required_history_sessions": MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS,
        "denominator": denominator,
        "numerator": complete,
        "missing_by_asset": tuple(sorted(missing)),
        "source_hash": snapshot.source_hash,
        "expected_sessions": expected,
    }
    return CommonPoolReadiness(
        signal_date=snapshot.signal_date,
        required_history_sessions=MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS,
        denominator=denominator,
        numerator=complete,
        available=available,
        missing_by_asset=tuple(sorted(missing)),
        reason=reason,
        earliest_evaluable_date=earliest,
        source_hash=snapshot.source_hash,
        readiness_hash=stable_contract_hash(payload),
    )


def _selection_for_snapshot(
    *,
    snapshot: ComparisonDecisionSnapshot,
    trading_sessions: Sequence[date],
) -> MediumTermMomentumSelection:
    readiness = preflight_common_pool(snapshot=snapshot, trading_sessions=trading_sessions)
    if not readiness.available:
        raise ComparisonDataUnavailableError(
            readiness.reason,
            f"signal_date={snapshot.signal_date.isoformat()} denominator={readiness.denominator} numerator={readiness.numerator}",
            signal_date=snapshot.signal_date,
        )
    expected = _snapshot_history_sessions(snapshot, trading_sessions)
    rows = _snapshot_rows(snapshot)
    momentum: list[tuple[str, float]] = []
    exclusions: list[tuple[str, str]] = list(readiness.missing_by_asset)
    for code in _complete_history_codes(
        snapshot=snapshot,
        trading_sessions=trading_sessions,
    ):
        start = rows[(code, expected[0])].adjusted_close
        end = rows[(code, expected[-1])].adjusted_close
        value = end / start - 1.0
        if value > 0.0:
            momentum.append((code, value))
        else:
            exclusions.append((code, "non_positive_126_session_momentum"))
    momentum.sort(key=lambda item: (-item[1], item[0]))
    selected = tuple(code for code, _value in momentum[:MEDIUM_TERM_MOMENTUM_TOP_N])
    target_weights = tuple((code, MEDIUM_TERM_MOMENTUM_TARGET_WEIGHT) for code in selected)
    payload = {
        "contract_hash": MEDIUM_TERM_MOMENTUM_CONTRACT_HASH,
        "signal_date": snapshot.signal_date,
        "readiness_hash": readiness.readiness_hash,
        "momentum_returns": tuple(momentum),
        "selected_asset_codes": selected,
        "target_weights": target_weights,
        "cash_target_weight": 1.0 - len(selected) * MEDIUM_TERM_MOMENTUM_TARGET_WEIGHT,
        "source_hash": snapshot.source_hash,
    }
    input_hash = stable_contract_hash(
        {
            "snapshot": asdict(snapshot),
            "expected_sessions": expected,
            "contract_hash": MEDIUM_TERM_MOMENTUM_CONTRACT_HASH,
        }
    )
    draft = MediumTermMomentumSelection(
        signal_date=snapshot.signal_date,
        selected_asset_codes=selected,
        momentum_returns=tuple(momentum),
        target_weights=target_weights,
        cash_target_weight=1.0 - len(selected) * MEDIUM_TERM_MOMENTUM_TARGET_WEIGHT,
        requested_asset_count=readiness.denominator,
        priced_asset_count=readiness.numerator,
        positive_asset_count=len(momentum),
        coverage_ratio=readiness.numerator / readiness.denominator,
        exclusions=tuple(sorted(exclusions)),
        contract_hash=MEDIUM_TERM_MOMENTUM_CONTRACT_HASH,
        input_hash=input_hash,
        selection_hash="pending",
    )
    return replace(draft, selection_hash=stable_contract_hash(payload | {"input_hash": input_hash}))


def select_medium_term_momentum_top10(
    *,
    snapshot: ComparisonDecisionSnapshot,
    trading_sessions: Sequence[date],
) -> MediumTermMomentumSelection:
    """Select the one fixed 126-session positive momentum control."""

    return _selection_for_snapshot(snapshot=snapshot, trading_sessions=trading_sessions)


def _snapshot_by_date(input_data: ComparisonInput) -> dict[date, ComparisonDecisionSnapshot]:
    return {item.signal_date: item for item in input_data.decision_snapshots}


def _momentum_signal_dates(input_data: ComparisonInput) -> tuple[date, ...]:
    month_ends = input_data.valuation.month_end_dates(
        start_date=input_data.start_date,
        end_date=input_data.end_date,
    )
    return tuple(sorted({input_data.start_date, *month_ends}))


def generate_medium_term_momentum_targets(
    input_data: ComparisonInput,
) -> tuple[RankingPortfolioTarget, ...]:
    """Create one start target and one target per proven exchange month end."""

    snapshots = _snapshot_by_date(input_data)
    targets: list[RankingPortfolioTarget] = []
    for signal_date in _momentum_signal_dates(input_data):
        snapshot = snapshots.get(signal_date)
        if snapshot is None:
            raise ComparisonDataUnavailableError(
                "missing_cutoff_snapshot",
                f"momentum signal date={signal_date.isoformat()}",
                signal_date=signal_date,
            )
        selection = select_medium_term_momentum_top10(
            snapshot=snapshot,
            trading_sessions=input_data.valuation.trading_sessions,
        )
        targets.append(
            freeze_ranking_portfolio_target(
                signal_date=signal_date,
                target_weights=selection.target_weights,
                source_hash=selection.selection_hash,
            )
        )
    return tuple(targets)


def canonical_daily_core_targets(
    *,
    ranking_events: Iterable[StageBRankingEvent],
    gate_facts_by_date: Mapping[date, Iterable[RankingRegimeLiquidityGateFact]] | None = None,
) -> tuple[RankingPortfolioTarget, ...]:
    """Freeze daily_core targets from the existing Stage-B candidate contract.

    Stage-B has already consumed the frozen daily_reconstructable score and
    complete-date evidence.  This thin adapter only advances the existing
    candidate state and freezes its selected codes for the shared ledger; it
    does not recalculate scores or expose tuning parameters.
    """

    events = tuple(ranking_events)
    if not events:
        raise ComparisonDataUnavailableError("daily_core_ranking_events_missing")
    event_dates = tuple(item.replay_date for item in events)
    if event_dates != tuple(sorted(set(event_dates))):
        raise ComparisonContractError("daily_core ranking events must be chronological and unique")
    registry = freeze_ranking_candidate_registry((FROZEN_RANKING_CANDIDATES[1],))
    facts_by_date = gate_facts_by_date or {}
    previous_states: dict[str, RankingCandidateState] = {}
    targets: list[RankingPortfolioTarget] = []
    for event in events:
        batch = evaluate_ranking_candidates(
            ranking_event=event,
            registry=registry,
            previous_states=previous_states,
            gate_facts=tuple(facts_by_date.get(event.replay_date, ())),
        )
        selection = batch.by_id[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS]
        source_hash = stable_contract_hash(
            {
                "policy_hash": DAILY_CORE_COMPARISON_POLICY_HASH,
                "candidate_manifest_hash": selection.candidate_manifest_hash,
                "candidate_registry_hash": selection.candidate_registry_hash,
                "selection_hash": selection.selection_hash,
            }
        )
        targets.append(
            freeze_ranking_portfolio_target(
                signal_date=event.replay_date,
                target_weights={code: 0.1 for code in selection.selected_asset_codes},
                source_hash=source_hash,
            )
        )
        previous_states = dict(batch.next_states)
    return tuple(targets)


@dataclass(frozen=True, slots=True)
class DailyCorePITDateInput:
    """One complete, cutoff-visible PIT input date for daily_core replay.

    The caller supplies the factual series retained by the PIT audit sidecar.
    A date is complete only when the series set exactly covers the declared
    authoritative universe; there is no readiness flag that can substitute for
    those rows or their immutable hashes.
    """

    replay_date: date
    decision_cutoff: datetime
    universe_hash: str
    authoritative_asset_codes: tuple[str, ...]
    series: tuple[PointInTimeAdjustedSeries, ...]

    def __post_init__(self) -> None:
        if self.decision_cutoff.tzinfo is None or self.decision_cutoff.utcoffset() is None:
            raise ComparisonContractError("daily_core PIT decision cutoff must be timezone-aware")
        if self.decision_cutoff.astimezone(_SHANGHAI).date() != self.replay_date:
            raise ComparisonContractError(
                "daily_core PIT decision cutoff must identify its replay date"
            )
        _sha256(self.universe_hash, "daily_core PIT universe hash")
        codes = tuple(self.authoritative_asset_codes)
        if (
            not codes
            or codes != tuple(sorted(codes))
            or len(codes) != len(set(codes))
            or any(not isinstance(code, str) or not code.strip() for code in codes)
        ):
            raise ComparisonContractError(
                "daily_core PIT authoritative assets must be sorted and unique"
            )
        values = tuple(self.series)
        series_codes = tuple(item.asset_code for item in values)
        if series_codes != codes:
            raise ComparisonContractError(
                "daily_core PIT series must cover the authoritative assets exactly"
            )
        for code, item in zip(codes, values, strict=True):
            if not isinstance(item, PointInTimeAdjustedSeries) or item.asset_code != code:
                raise ComparisonContractError("daily_core PIT series identity is invalid")
            if item.metadata.asset_code != code:
                raise ComparisonContractError("daily_core PIT metadata identity is invalid")
            if item.synchronized_after_cutoff is not False:
                raise ComparisonDataUnavailableError(
                    "daily_core_series_synchronized_after_cutoff",
                    code,
                    signal_date=self.replay_date,
                )
            for label, observed_at in (
                ("latest_source_timestamp", item.latest_source_timestamp),
                ("membership_known_at", item.metadata.membership_known_at),
                ("membership_last_modified_at", item.metadata.membership_last_modified_at),
                ("membership_ingested_at", item.metadata.membership_ingested_at),
                ("taxonomy_observed_at", item.metadata.taxonomy_observed_at),
                ("underlying_observed_at", item.metadata.underlying_observed_at),
            ):
                if observed_at is None:
                    continue
                if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                    raise ComparisonContractError(
                        f"daily_core PIT {label} must be timezone-aware"
                    )
                if observed_at > self.decision_cutoff:
                    raise ComparisonDataUnavailableError(
                        "daily_core_pit_fact_after_cutoff",
                        f"{code}:{label}",
                        signal_date=self.replay_date,
                    )
            _sha256(item.series_hash, "daily_core PIT series hash")
            if item.series_hash != stable_contract_hash(_pit_series_payload(item)):
                raise ComparisonContractError("daily_core PIT series hash is not canonical")
            if not item.bars or item.bars[-1].session_date != self.replay_date:
                raise ComparisonDataUnavailableError(
                    "daily_core_series_not_cutoff_visible",
                    f"{code}:{self.replay_date.isoformat()}",
                    signal_date=self.replay_date,
                )


def canonical_daily_core_targets_from_pit(
    *,
    date_inputs: Iterable[DailyCorePITDateInput],
    stage_b_contract: StageBReplayContract,
    gate_facts_by_date: Mapping[date, Iterable[RankingRegimeLiquidityGateFact]] | None = None,
) -> tuple[RankingPortfolioTarget, ...]:
    """Build daily_core Stage-B events from immutable PIT series, then reuse hysteresis.

    Each date is scored from exactly the last ``REQUIRED_BAR_COUNT`` bars of
    each supplied PIT series.  The resulting feature/date manifests are
    validated by the existing Stage-B contract before the existing frozen
    candidate evaluator advances hysteresis state.  Missing or unusable score
    inputs become explicit Stage-B exclusions; no fallback score or fabricated
    readiness is introduced.
    """

    dates = tuple(date_inputs)
    if not dates:
        raise ComparisonDataUnavailableError("daily_core_pit_inputs_missing")
    replay_dates = tuple(item.replay_date for item in dates)
    if replay_dates != tuple(sorted(set(replay_dates))):
        raise ComparisonContractError(
            "daily_core PIT inputs must be chronologically unique"
        )
    if stage_b_contract.score_contract_id != "daily_reconstructable_v1":
        raise ComparisonContractError("daily_core Stage-B score contract is not frozen")
    manifest = daily_reconstructable_manifest()
    if stage_b_contract.score_manifest_hash != manifest.manifest_hash:
        raise ComparisonContractError("daily_core Stage-B score manifest is incompatible")
    registry = freeze_ranking_candidate_registry((FROZEN_RANKING_CANDIDATES[1],))
    if stage_b_contract.candidate_registry_hash != registry.registry_hash:
        raise ComparisonContractError(
            "daily_core Stage-B candidate registry is incompatible"
        )
    facts_by_date = gate_facts_by_date or {}
    extra_fact_dates = set(facts_by_date) - set(replay_dates)
    if extra_fact_dates:
        raise ComparisonContractError(
            "daily_core gate facts contain a date outside the PIT input"
        )

    events: list[StageBRankingEvent] = []
    for item in dates:
        features = []
        for series in item.series:
            unit_input_hash = stable_contract_hash(
                {
                    "replay_date": item.replay_date,
                    "asset_code": series.asset_code,
                    "series_hash": series.series_hash,
                }
            )
            try:
                score = score_daily_reconstructable(
                    series.bars[-REQUIRED_BAR_COUNT:],
                    provenance=series.provenance,
                )
            except DailyReconstructableUnavailableError as exc:
                features.append(
                    build_stage_b_feature_input(
                        contract=stage_b_contract,
                        replay_date=item.replay_date,
                        asset_code=series.asset_code,
                        universe_hash=item.universe_hash,
                        unit_input_hash=unit_input_hash,
                        upstream_feature_hash=series.series_hash,
                        series_hash=series.series_hash,
                        score_eligible=False,
                        exclusion_reason="daily_reconstructable_score_unavailable",
                        exclusion_detail=str(exc),
                    )
                )
                continue
            if (
                score.contract_id != stage_b_contract.score_contract_id
                or score.manifest_hash != stage_b_contract.score_manifest_hash
            ):
                raise ComparisonContractError(
                    "daily_core PIT score does not match the Stage-B contract"
                )
            features.append(
                build_stage_b_feature_input(
                    contract=stage_b_contract,
                    replay_date=item.replay_date,
                    asset_code=series.asset_code,
                    universe_hash=item.universe_hash,
                    unit_input_hash=unit_input_hash,
                    upstream_feature_hash=series.series_hash,
                    series_hash=series.series_hash,
                    score_eligible=True,
                    research_score=score.research_score,
                    trend_score=score.trend_score,
                    risk_score=score.risk_score,
                    liquidity_score=score.liquidity_score,
                )
            )
        source_date = build_stage_b_source_date(
            contract=stage_b_contract,
            replay_date=item.replay_date,
            decision_cutoff=item.decision_cutoff,
            universe_hash=item.universe_hash,
            authoritative_asset_codes=item.authoritative_asset_codes,
            features=features,
        )
        _manifest, event = _validate_source_date(
            source_date,
            contract=stage_b_contract,
        )
        events.append(event)
    return canonical_daily_core_targets(
        ranking_events=events,
        gate_facts_by_date=facts_by_date,
    )


def _validate_v2_checks(
    *,
    input_data: ComparisonInput,
    required_dates: tuple[date, ...],
) -> dict[date, V2StateCheck]:
    checks = {item.session_date: item for item in input_data.v2_state_checks}
    missing = tuple(day for day in required_dates if day not in checks)
    if missing:
        raise ComparisonDataUnavailableError(
            "v2_state_evidence_missing",
            ",".join(day.isoformat() for day in missing),
            signal_date=missing[0],
        )
    return checks


def bridge_v2_targets(
    *,
    input_data: ComparisonInput,
    leader_exit_policy: str | None = None,
) -> tuple[RankingPortfolioTarget, ...]:
    """Map causal V2 events to sparse targets with deterministic capacity rules."""

    selected_policy = leader_exit_policy or input_data.leader_exit_policy
    if selected_policy not in LEADER_EXIT_POLICY_IDS:
        raise ComparisonContractError("leader exit policy is unsupported")
    if selected_policy != LEADER_EXIT_POLICY_LEGACY_MA5:
        return bridge_shared_daily_v2_targets(
            input_data=input_data,
            take_profit=selected_policy == LEADER_EXIT_POLICY_SHARED_DAILY_2R,
        )

    sessions = input_data.valuation.trading_sessions
    state_dates = tuple(
        day
        for day in sessions
        if input_data.start_date <= day <= input_data.end_date
    )
    checks = _validate_v2_checks(input_data=input_data, required_dates=state_dates)
    snapshots = _snapshot_by_date(input_data)
    complete_codes_by_date: dict[date, set[str]] = {}
    for session_date in state_dates:
        snapshot = snapshots.get(session_date)
        if snapshot is None:
            raise ComparisonDataUnavailableError(
                "common_pool_snapshot_missing",
                session_date.isoformat(),
                signal_date=session_date,
            )
        readiness = preflight_common_pool(
            snapshot=snapshot,
            trading_sessions=sessions,
        )
        if not readiness.available:
            raise ComparisonDataUnavailableError(
                readiness.reason,
                session_date.isoformat(),
                signal_date=session_date,
            )
        complete_codes_by_date[session_date] = set(
            _complete_history_codes(snapshot=snapshot, trading_sessions=sessions)
        )
    events_by_date: dict[date, list[V2ComparisonEvent]] = {}
    for event in input_data.v2_events:
        if input_data.start_date <= event.signal_date <= input_data.end_date:
            if (
                event.event_type == V2_EVENT_CONFIRMATION
                and event.asset_code not in complete_codes_by_date[event.signal_date]
            ):
                raise ComparisonDataUnavailableError(
                    "v2_event_outside_common_pool",
                    f"{event.signal_date.isoformat()}:{event.asset_code}",
                    signal_date=event.signal_date,
                )
            events_by_date.setdefault(event.signal_date, []).append(event)
    held: dict[str, tuple[str, date]] = {}
    targets: list[RankingPortfolioTarget] = []
    for session_date in state_dates:
        events = events_by_date.get(session_date, [])
        check = checks[session_date]
        actual_hashes = tuple(sorted(item.source_hash for item in events))
        if actual_hashes != tuple(sorted(check.transition_source_hashes)):
            raise ComparisonDataUnavailableError(
                "v2_transition_evidence_mismatch",
                session_date.isoformat(),
                signal_date=session_date,
            )
        checked_keys = set(check.checked_observation_keys)
        if any(
            _v2_observation_key(
                asset_code=item.asset_code,
                signal_date=item.original_signal_date,
                formula_id=item.formula_id,
            )
            not in checked_keys
            for item in events
        ):
            raise ComparisonDataUnavailableError(
                "v2_event_missing_from_observation_evidence",
                session_date.isoformat(),
                signal_date=session_date,
            )
        exited_assets: set[str] = set()
        exited_groups: set[str] = set()
        membership_before = tuple(sorted(held))
        for event in sorted(
            (item for item in events if item.event_type == V2_EVENT_EXIT),
            key=lambda item: (item.asset_code, item.clone_group, item.source_hash),
        ):
            holding = held.get(event.asset_code)
            if holding is None or holding != (event.clone_group, event.original_signal_date):
                continue
            exited_assets.add(event.asset_code)
            exited_groups.add(event.clone_group)
            held.pop(event.asset_code, None)
        candidates = sorted(
            (item for item in events if item.event_type == V2_EVENT_CONFIRMATION),
            key=lambda item: (
                -item.score,
                -item.original_signal_date.toordinal(),
                item.formula_id,
                item.asset_code,
            ),
        )
        for event in candidates:
            if len(held) >= MEDIUM_TERM_MOMENTUM_TOP_N:
                break
            if (
                event.asset_code in held
                or event.asset_code in exited_assets
                or event.clone_group in exited_groups
                or event.clone_group in {group for group, _signal_date in held.values()}
            ):
                continue
            held[event.asset_code] = (event.clone_group, event.original_signal_date)
        membership_after = tuple(sorted(held))
        if any(
            _v2_observation_key(
                asset_code=code,
                signal_date=signal_date,
                formula_id=BREAKOUT_V2,
            )
            not in checked_keys
            for code, (_clone_group, signal_date) in held.items()
        ):
            raise ComparisonDataUnavailableError(
                "v2_held_lifecycle_missing_from_observation_evidence",
                session_date.isoformat(),
                signal_date=session_date,
            )
        # An empty transition sequence is valid only because ``checks`` proves
        # the full state was checked; absent checks were rejected above.
        if membership_after != membership_before:
            source_hash = stable_contract_hash(
                {
                    "policy_hash": V2_COMPARISON_ALLOCATION_POLICY_HASH,
                    "session_date": session_date,
                    "state_check": asdict(check),
                    "events": tuple(asdict(item) for item in events),
                    "selected_asset_codes": membership_after,
                }
            )
            targets.append(
                freeze_ranking_portfolio_target(
                    signal_date=session_date,
                    target_weights={code: 0.1 for code in membership_after},
                    source_hash=source_hash,
                )
            )
    return tuple(targets)


@dataclass
class _SharedDailyPosition:
    event: V2ComparisonEvent
    entry_execution_date: date | None
    entry_reference_price: float | None
    entry_atr20: float | None
    signal_low: float | None
    initial_stop: float | None
    risk_unit: float | None
    visible_closes: list[float]
    provenance_signature: tuple[object, ...] | None = None
    armed: bool = False
    exit_record: LeaderExitComparisonRecord | None = None


def _leader_series_index(
    input_data: ComparisonInput,
) -> dict[tuple[str, date], PointInTimeAdjustedSeries]:
    return {
        (series.asset_code, series.bars[-1].session_date): series
        for series in input_data.leader_exit_pit_series
    }


def _leader_provenance_signature(
    series: PointInTimeAdjustedSeries,
) -> tuple[object, ...]:
    return tuple(asdict(series.provenance).values())


def _leader_valuation_index(
    input_data: ComparisonInput,
) -> dict[tuple[str, date], ForwardAdjustedClose]:
    return {
        (row.asset_code, row.session_date): row
        for row in input_data.valuation.adjusted_closes
    }


def _leader_series_for_date(
    *,
    series_index: Mapping[tuple[str, date], PointInTimeAdjustedSeries],
    snapshots: Mapping[date, ComparisonDecisionSnapshot],
    asset_code: str,
    session_date: date,
    signal_date: date,
    failure_date: date | None = None,
) -> PointInTimeAdjustedSeries:
    unavailable_date = failure_date or signal_date
    series = series_index.get((asset_code, session_date))
    snapshot = snapshots.get(session_date)
    if series is None:
        raise ComparisonDataUnavailableError(
            "leader_exit_pit_series_missing",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=unavailable_date,
        )
    if snapshot is None:
        raise ComparisonDataUnavailableError(
            "leader_exit_pit_snapshot_missing",
            session_date.isoformat(),
            signal_date=unavailable_date,
        )
    if (
        series.synchronized_after_cutoff
        or series.metadata.eligible_at > session_date
        or not series.provenance.provider.strip()
        or not series.provenance.adjustment_version.strip()
        or series.provenance.price_basis != "total_return_adjusted"
    ):
        raise ComparisonDataUnavailableError(
            "leader_exit_pit_series_late_or_ineligible",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=unavailable_date,
        )
    try:
        # Reuse the existing daily PIT validator so every receipt/metadata
        # timestamp and the canonical series hash have one implementation.
        DailyCorePITDateInput(
            replay_date=session_date,
            decision_cutoff=snapshot.decision_cutoff,
            universe_hash=snapshot.source_hash,
            authoritative_asset_codes=(asset_code,),
            series=(series,),
        )
    except ComparisonDataUnavailableError as exc:
        raise ComparisonDataUnavailableError(
            exc.reason,
            str(exc),
            signal_date=unavailable_date,
        ) from exc
    if series.bars[-1].session_date != session_date:
        raise ComparisonDataUnavailableError(
            "leader_exit_pit_series_stale_or_future",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=unavailable_date,
        )
    snapshot_rows = _snapshot_rows(snapshot)
    current_snapshot_row = snapshot_rows.get((asset_code, session_date))
    if current_snapshot_row is None:
        raise ComparisonDataUnavailableError(
            "leader_exit_snapshot_close_missing",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=unavailable_date,
        )
    for bar in series.bars:
        snapshot_row = snapshot_rows.get((asset_code, bar.session_date))
        if snapshot_row is None:
            continue
        if not math.isclose(
            _finite(bar.adjusted_close),
            _finite(snapshot_row.adjusted_close),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ComparisonDataUnavailableError(
                "leader_exit_snapshot_close_mismatch",
                f"{asset_code}:{bar.session_date.isoformat()}",
                signal_date=unavailable_date,
            )
    return series


def _leader_bar_for_date(
    series: PointInTimeAdjustedSeries,
    session_date: date,
    *,
    signal_date: date,
) -> Any:
    for bar in series.bars:
        if bar.session_date == session_date:
            return bar
    raise ComparisonDataUnavailableError(
        "leader_exit_pit_bar_missing",
        f"{series.asset_code}:{session_date.isoformat()}",
        signal_date=signal_date,
    )


def _shared_daily_entry(
    *,
    event: V2ComparisonEvent,
    sessions: tuple[date, ...],
) -> _SharedDailyPosition:
    try:
        confirmation_index = sessions.index(event.signal_date)
    except ValueError as exc:
        raise ComparisonDataUnavailableError(
            "leader_exit_confirmation_outside_calendar",
            event.signal_date.isoformat(),
            signal_date=event.signal_date,
        ) from exc
    entry_date = sessions[confirmation_index + 1] if confirmation_index + 1 < len(sessions) else None
    return _SharedDailyPosition(
        event=event,
        entry_execution_date=entry_date,
        entry_reference_price=None,
        entry_atr20=None,
        signal_low=None,
        initial_stop=None,
        risk_unit=None,
        visible_closes=[],
    )


def _initialize_shared_daily_position(
    *,
    position: _SharedDailyPosition,
    input_data: ComparisonInput,
    series_index: Mapping[tuple[str, date], PointInTimeAdjustedSeries],
    snapshots: Mapping[date, ComparisonDecisionSnapshot],
    valuation_index: Mapping[tuple[str, date], ForwardAdjustedClose],
) -> None:
    entry_date = position.entry_execution_date
    if entry_date is None:
        return
    event = position.event
    entry_row = valuation_index.get((event.asset_code, entry_date))
    if entry_row is None or not entry_row.decision_eligible:
        raise ComparisonDataUnavailableError(
            "leader_exit_entry_valuation_missing",
            f"{event.asset_code}:{entry_date.isoformat()}",
            signal_date=entry_date,
        )
    entry_series = _leader_series_for_date(
        series_index=series_index,
        snapshots=snapshots,
        asset_code=event.asset_code,
        session_date=entry_date,
        signal_date=event.signal_date,
        failure_date=entry_date,
    )
    signal_series = _leader_series_for_date(
        series_index=series_index,
        snapshots=snapshots,
        asset_code=event.asset_code,
        session_date=event.original_signal_date,
        signal_date=event.signal_date,
    )
    signal_bar = _leader_bar_for_date(
        signal_series,
        event.original_signal_date,
        signal_date=event.signal_date,
    )
    entry_bar = _leader_bar_for_date(
        entry_series,
        entry_date,
        signal_date=event.signal_date,
    )
    entry_price = _finite(entry_bar.adjusted_close)
    valuation_entry_price = _finite(entry_row.adjusted_close)
    if not math.isclose(entry_price, valuation_entry_price, rel_tol=0.0, abs_tol=1e-9):
        raise ComparisonDataUnavailableError(
            "leader_exit_entry_price_mismatch",
            f"{event.asset_code}:{entry_date.isoformat()}",
            signal_date=entry_date,
        )
    signal_low = _finite(signal_bar.adjusted_low)
    if signal_low <= 0:
        raise ComparisonDataUnavailableError(
            "leader_exit_signal_low_invalid",
            f"{event.asset_code}:{event.original_signal_date.isoformat()}",
            signal_date=event.signal_date,
        )
    bars = tuple(item for item in entry_series.bars if item.session_date <= entry_date)
    try:
        entry_index = input_data.valuation.trading_sessions.index(entry_date)
    except ValueError as exc:
        raise ComparisonDataUnavailableError(
            "leader_exit_entry_session_missing",
            entry_date.isoformat(),
            signal_date=event.signal_date,
        ) from exc
    expected_atr_sessions = input_data.valuation.trading_sessions[: entry_index + 1][-21:]
    actual_atr_sessions = tuple(item.session_date for item in bars[-21:])
    if actual_atr_sessions != expected_atr_sessions:
        raise ComparisonDataUnavailableError(
            "leader_exit_entry_atr_sessions_missing",
            f"{event.asset_code}:{entry_date.isoformat()}",
            signal_date=entry_date,
        )
    if _leader_provenance_signature(signal_series) != _leader_provenance_signature(entry_series):
        raise ComparisonDataUnavailableError(
            "leader_exit_price_basis_mismatch",
            event.asset_code,
            signal_date=entry_date,
        )
    atr = leader_atr20(
        tuple(float(item.adjusted_high) for item in bars),
        tuple(float(item.adjusted_low) for item in bars),
        tuple(float(item.adjusted_close) for item in bars),
    )
    if atr is None:
        raise ComparisonDataUnavailableError(
            "leader_exit_entry_atr20_missing",
            f"{event.asset_code}:{event.signal_date.isoformat()}",
            signal_date=entry_date,
        )
    risk = initial_leader_risk(
        entry_close=entry_price,
        entry_atr20=atr,
        source_signal_low=signal_low,
    )
    if risk is None:
        raise ComparisonDataUnavailableError(
            "leader_exit_initial_risk_invalid",
            f"{event.asset_code}:{event.signal_date.isoformat()}",
            signal_date=event.signal_date,
        )
    initial_stop, risk_unit, _ignored_signal_low = risk
    position.entry_reference_price = entry_price
    position.entry_atr20 = atr
    position.signal_low = signal_low
    position.initial_stop = initial_stop
    position.risk_unit = risk_unit
    position.provenance_signature = _leader_provenance_signature(entry_series)


def _leader_daily_pit_facts(
    *,
    series: PointInTimeAdjustedSeries,
    sessions: tuple[date, ...],
    asset_code: str,
    session_date: date,
    signal_date: date,
) -> tuple[float, float]:
    """Return one cutoff-visible close and its complete five-session MA."""

    bar = _leader_bar_for_date(series, session_date, signal_date=signal_date)
    try:
        session_index = sessions.index(session_date)
    except ValueError as exc:
        raise ComparisonDataUnavailableError(
            "leader_exit_session_outside_calendar",
            session_date.isoformat(),
            signal_date=signal_date,
        ) from exc
    expected_sessions = sessions[: session_index + 1][-5:]
    if len(expected_sessions) != 5:
        raise ComparisonDataUnavailableError(
            "leader_exit_ma5_warmup_missing",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=signal_date,
        )
    bars_by_date = {item.session_date: item for item in series.bars}
    if tuple(item.session_date for item in series.bars) != tuple(
        sorted(item.session_date for item in series.bars)
    ):
        raise ComparisonDataUnavailableError(
            "leader_exit_pit_bars_not_canonical",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=signal_date,
        )
    if any(day not in bars_by_date for day in expected_sessions):
        raise ComparisonDataUnavailableError(
            "leader_exit_ma5_session_missing",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=signal_date,
        )
    ma5_closes = tuple(float(bars_by_date[day].adjusted_close) for day in expected_sessions)
    if any(not math.isfinite(value) or value <= 0 for value in ma5_closes):
        raise ComparisonDataUnavailableError(
            "leader_exit_ma5_value_invalid",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=signal_date,
        )
    current = _finite(bar.adjusted_close)
    if current <= 0:
        raise ComparisonDataUnavailableError(
            "leader_exit_current_close_invalid",
            f"{asset_code}:{session_date.isoformat()}",
            signal_date=signal_date,
        )
    return current, math.fsum(ma5_closes) / len(ma5_closes)


def _shared_daily_policy_id(input_data: ComparisonInput, take_profit: bool) -> str:
    if take_profit:
        return LEADER_EXIT_POLICY_SHARED_DAILY_2R
    if input_data.leader_exit_policy == LEADER_EXIT_POLICY_SHARED_DAILY_2R:
        return LEADER_EXIT_POLICY_SHARED_DAILY_2R
    return LEADER_EXIT_POLICY_SHARED_DAILY


def build_shared_daily_v2_targets(
    input_data: ComparisonInput,
    *,
    take_profit: bool = False,
) -> SharedDailyV2BridgeResult:
    """Replay V2 confirmations with the shared daily leader exit policy."""

    policy_id = _shared_daily_policy_id(input_data, take_profit)
    policy_hash = leader_exit_policy_hash(policy_id)
    sessions = tuple(
        day
        for day in input_data.valuation.trading_sessions
        if input_data.start_date <= day <= input_data.end_date
    )
    checks = _validate_v2_checks(input_data=input_data, required_dates=sessions)
    snapshots = _snapshot_by_date(input_data)
    complete_codes_by_date: dict[date, set[str]] = {}
    for session_date in sessions:
        snapshot = snapshots.get(session_date)
        if snapshot is None:
            raise ComparisonDataUnavailableError(
                "common_pool_snapshot_missing",
                session_date.isoformat(),
                signal_date=session_date,
            )
        readiness = preflight_common_pool(snapshot=snapshot, trading_sessions=input_data.valuation.trading_sessions)
        if not readiness.available:
            raise ComparisonDataUnavailableError(
                readiness.reason,
                session_date.isoformat(),
                signal_date=session_date,
            )
        complete_codes_by_date[session_date] = set(
            _complete_history_codes(
                snapshot=snapshot,
                trading_sessions=input_data.valuation.trading_sessions,
            )
        )
    events_by_date: dict[date, list[V2ComparisonEvent]] = {}
    for event in input_data.v2_events:
        if input_data.start_date <= event.signal_date <= input_data.end_date:
            if (
                event.event_type == V2_EVENT_CONFIRMATION
                and event.asset_code not in complete_codes_by_date[event.signal_date]
            ):
                raise ComparisonDataUnavailableError(
                    "v2_event_outside_common_pool",
                    f"{event.signal_date.isoformat()}:{event.asset_code}",
                    signal_date=event.signal_date,
                )
            events_by_date.setdefault(event.signal_date, []).append(event)

    series_index = _leader_series_index(input_data)
    valuation_index = _leader_valuation_index(input_data)
    held: dict[str, _SharedDailyPosition] = {}
    targets: list[RankingPortfolioTarget] = []
    records: list[LeaderExitComparisonRecord] = []
    for session_date in sessions:
        events = events_by_date.get(session_date, [])
        check = checks[session_date]
        actual_hashes = tuple(sorted(item.source_hash for item in events))
        if actual_hashes != tuple(sorted(check.transition_source_hashes)):
            raise ComparisonDataUnavailableError(
                "v2_transition_evidence_mismatch",
                session_date.isoformat(),
                signal_date=session_date,
            )
        checked_keys = set(check.checked_observation_keys)
        if any(
            _v2_observation_key(
                asset_code=item.asset_code,
                signal_date=item.original_signal_date,
                formula_id=item.formula_id,
            )
            not in checked_keys
            for item in events
        ):
            raise ComparisonDataUnavailableError(
                "v2_event_missing_from_observation_evidence",
                session_date.isoformat(),
                signal_date=session_date,
            )
        membership_before = tuple(sorted(held))
        removed_clone_groups: set[str] = set()
        exited_records: list[LeaderExitComparisonRecord] = []
        for code, position in tuple(sorted(held.items())):
            if position.entry_execution_date is None or session_date < position.entry_execution_date:
                continue
            row = valuation_index.get((code, session_date))
            if row is None or not row.decision_eligible:
                raise ComparisonDataUnavailableError(
                    "leader_exit_daily_valuation_missing",
                    f"{code}:{session_date.isoformat()}",
                    signal_date=session_date,
                )
            if position.entry_reference_price is None:
                _initialize_shared_daily_position(
                    position=position,
                    input_data=input_data,
                    series_index=series_index,
                    snapshots=snapshots,
                    valuation_index=valuation_index,
                )
            current_series = _leader_series_for_date(
                series_index=series_index,
                snapshots=snapshots,
                asset_code=code,
                session_date=session_date,
                signal_date=session_date,
            )
            if _leader_provenance_signature(current_series) != position.provenance_signature:
                raise ComparisonDataUnavailableError(
                    "leader_exit_price_basis_mismatch",
                    f"{code}:{session_date.isoformat()}",
                    signal_date=session_date,
                )
            current, ma5 = _leader_daily_pit_facts(
                series=current_series,
                sessions=input_data.valuation.trading_sessions,
                asset_code=code,
                session_date=session_date,
                signal_date=session_date,
            )
            position.visible_closes.append(current)
            assert position.entry_reference_price is not None
            assert position.initial_stop is not None
            assert position.risk_unit is not None
            threshold = evaluate_leader_exit_thresholds(
                entry_close=position.entry_reference_price,
                initial_stop=position.initial_stop,
                risk_unit=position.risk_unit,
                previous_high=None,
                visible_closes=tuple(position.visible_closes),
                ma5=ma5,
                previously_armed=position.armed,
                take_profit_line=(
                    position.entry_reference_price + 2.0 * position.risk_unit
                    if policy_id == LEADER_EXIT_POLICY_SHARED_DAILY_2R
                    else None
                ),
            )
            position.armed = threshold.armed
            if threshold.reason_code is None:
                continue
            try:
                execution_date = next_etf_exchange_trading_day(session_date)
            except ExchangeCalendarUnavailableError:
                execution_date = None
            record = LeaderExitComparisonRecord(
                policy_id=policy_id,
                policy_hash=policy_hash,
                asset_code=code,
                original_signal_date=position.event.original_signal_date,
                confirmation_date=position.event.signal_date,
                entry_execution_date=position.entry_execution_date,
                entry_reference_price=position.entry_reference_price,
                entry_atr20=position.entry_atr20,
                signal_low=position.signal_low,
                initial_stop=position.initial_stop,
                risk_unit=position.risk_unit,
                exit_signal_date=session_date,
                exit_execution_date=execution_date,
                exit_reason=threshold.reason_code,
                data_eligible=True,
            )
            position.exit_record = record
            records.append(record)
            exited_records.append(record)
            removed_clone_groups.add(position.event.clone_group)
            held.pop(code, None)

        candidates = sorted(
            (item for item in events if item.event_type == V2_EVENT_CONFIRMATION),
            key=lambda item: (
                -item.score,
                -item.original_signal_date.toordinal(),
                item.formula_id,
                item.asset_code,
            ),
        )
        exited_assets = {item.asset_code for item in exited_records}
        for event in candidates:
            if len(held) >= MEDIUM_TERM_MOMENTUM_TOP_N:
                break
            if (
                event.asset_code in held
                or event.asset_code in exited_assets
                or event.clone_group in removed_clone_groups
                or event.clone_group in {position.event.clone_group for position in held.values()}
            ):
                continue
            held[event.asset_code] = _shared_daily_entry(
                event=event,
                sessions=input_data.valuation.trading_sessions,
            )
        membership_after = tuple(sorted(held))
        for code, position in held.items():
            if (
                _v2_observation_key(
                    asset_code=code,
                    signal_date=position.event.original_signal_date,
                    formula_id=position.event.formula_id,
                )
                not in checked_keys
            ):
                raise ComparisonDataUnavailableError(
                    "v2_held_lifecycle_missing_from_observation_evidence",
                    session_date.isoformat(),
                    signal_date=session_date,
                )
        if membership_after != membership_before:
            targets.append(
                freeze_ranking_portfolio_target(
                    signal_date=session_date,
                    target_weights={code: 0.1 for code in membership_after},
                    source_hash=stable_contract_hash(
                        {
                            "policy_id": policy_id,
                            "policy_hash": policy_hash,
                            "session_date": session_date,
                            "state_check": asdict(check),
                            "events": tuple(asdict(item) for item in events),
                            "exit_records": tuple(asdict(item) for item in exited_records),
                            "selected_asset_codes": membership_after,
                        }
                    ),
                )
            )
    for position in held.values():
        if (
            position.entry_reference_price is None
            or position.entry_atr20 is None
            or position.signal_low is None
            or position.initial_stop is None
            or position.risk_unit is None
        ):
            # A confirmation on the terminal decision date is a pending
            # target; without a subsequent PIT entry bar it has no frozen
            # risk context and must not become a fabricated record.
            continue
        records.append(
            LeaderExitComparisonRecord(
                policy_id=policy_id,
                policy_hash=policy_hash,
                asset_code=position.event.asset_code,
                original_signal_date=position.event.original_signal_date,
                confirmation_date=position.event.signal_date,
                entry_execution_date=position.entry_execution_date,
                entry_reference_price=position.entry_reference_price,
                entry_atr20=position.entry_atr20,
                signal_low=position.signal_low,
                initial_stop=position.initial_stop,
                risk_unit=position.risk_unit,
                exit_signal_date=None,
                exit_execution_date=None,
                exit_reason=None,
                data_eligible=True,
            )
        )
    return SharedDailyV2BridgeResult(
        targets=tuple(targets),
        exit_records=tuple(
            sorted(
                records,
                key=lambda item: (
                    item.confirmation_date,
                    item.asset_code,
                    item.exit_signal_date or date.max,
                ),
            )
        ),
    )


def bridge_shared_daily_v2_targets(
    input_data: ComparisonInput,
    *,
    take_profit: bool = False,
) -> tuple[RankingPortfolioTarget, ...]:
    """Return targets from the shared daily policy, retaining legacy bridge separately."""

    return build_shared_daily_v2_targets(
        input_data,
        take_profit=take_profit,
    ).targets


def _route_required_dates(
    *,
    input_data: ComparisonInput,
    route_id: str,
    targets: tuple[RankingPortfolioTarget, ...],
) -> tuple[date, ...]:
    sessions = tuple(
        day
        for day in input_data.valuation.trading_sessions
        if input_data.start_date <= day <= input_data.end_date
    )
    if route_id == ROUTE_V2_BREAKOUT:
        return tuple(item.signal_date for item in targets)
    if route_id == ROUTE_MEDIUM_TERM_MOMENTUM:
        return tuple(item.signal_date for item in targets)
    if input_data.daily_core_required_signal_dates:
        return tuple(input_data.daily_core_required_signal_dates)
    return sessions[:-1]


def _ledger_pair(
    *,
    input_data: ComparisonInput,
    targets: tuple[RankingPortfolioTarget, ...],
    required_signal_dates: tuple[date, ...],
) -> tuple[RankingPortfolioLedger, RankingPortfolioLedger]:
    sessions = tuple(
        day
        for day in input_data.valuation.trading_sessions
        if input_data.start_date <= day <= input_data.end_date
    )
    session_set = set(sessions)
    valuation_closes = tuple(
        row
        for row in input_data.valuation.adjusted_closes
        if row.session_date in session_set
    )
    base = calculate_continuous_ranking_portfolio(
        trading_sessions=sessions,
        adjusted_closes=valuation_closes,
        targets=targets,
        cost_policy=RANKING_PORTFOLIO_BASE_COST_POLICY,
        initial_capital=input_data.initial_capital,
        required_signal_dates=required_signal_dates,
    )
    stress = calculate_continuous_ranking_portfolio(
        trading_sessions=sessions,
        adjusted_closes=valuation_closes,
        targets=targets,
        cost_policy=RANKING_PORTFOLIO_STRESS_COST_POLICY,
        initial_capital=input_data.initial_capital,
        required_signal_dates=required_signal_dates,
    )
    return base, stress


def _ensure_common_pool_for_dates(
    *,
    input_data: ComparisonInput,
    required_dates: tuple[date, ...],
) -> None:
    snapshots = _snapshot_by_date(input_data)
    sessions = input_data.valuation.trading_sessions
    for signal_date in required_dates:
        snapshot = snapshots.get(signal_date)
        if snapshot is None:
            raise ComparisonDataUnavailableError(
                "common_pool_snapshot_missing",
                signal_date.isoformat(),
                signal_date=signal_date,
            )
        readiness = preflight_common_pool(snapshot=snapshot, trading_sessions=sessions)
        if not readiness.available:
            raise ComparisonDataUnavailableError(
                readiness.reason,
                signal_date.isoformat(),
                signal_date=signal_date,
            )


def _ensure_targets_in_common_pool(
    *,
    input_data: ComparisonInput,
    targets: tuple[RankingPortfolioTarget, ...],
) -> None:
    """Reject a route target that names an asset outside its PIT pool."""

    snapshots = _snapshot_by_date(input_data)
    sessions = input_data.valuation.trading_sessions
    for target in targets:
        snapshot = snapshots.get(target.signal_date)
        if snapshot is None:
            raise ComparisonDataUnavailableError(
                "common_pool_snapshot_missing",
                target.signal_date.isoformat(),
                signal_date=target.signal_date,
            )
        complete = set(_complete_history_codes(snapshot=snapshot, trading_sessions=sessions))
        outside = tuple(code for code, _weight in target.target_weights if code not in complete)
        if outside:
            raise ComparisonDataUnavailableError(
                "route_target_outside_common_pool",
                f"{target.signal_date.isoformat()}:{','.join(outside)}",
                signal_date=target.signal_date,
            )


def _blocked_route(
    route_id: str,
    reason: str,
    required_signal_dates: tuple[date, ...],
    input_hash: str,
    *,
    leader_exit_policy: str = LEADER_EXIT_POLICY_DEFAULT,
) -> ComparisonRouteResult:
    return ComparisonRouteResult(
        route_id=route_id,
        status="unavailable",
        reason=reason,
        required_signal_dates=required_signal_dates,
        targets=(),
        base_ledger=None,
        stress_ledger=None,
        input_hash=input_hash,
        leader_exit_policy=leader_exit_policy,
        leader_exit_policy_hash=leader_exit_policy_hash(leader_exit_policy)
        if route_id == ROUTE_V2_BREAKOUT
        else None,
    )


def _route_result(
    *,
    route_id: str,
    targets: tuple[RankingPortfolioTarget, ...],
    required_signal_dates: tuple[date, ...],
    base: RankingPortfolioLedger,
    stress: RankingPortfolioLedger,
    leader_exit_policy: str = LEADER_EXIT_POLICY_DEFAULT,
    leader_exit_records: tuple[LeaderExitComparisonRecord, ...] = (),
) -> ComparisonRouteResult:
    status = "completed" if base.status == stress.status == "completed" else "unavailable"
    reason = None
    if status != "completed":
        reason = (
            base.unavailable_intervals[0].reason
            if base.unavailable_intervals
            else stress.unavailable_intervals[0].reason
            if stress.unavailable_intervals
            else "ledger_unavailable"
        )
    route_identity: dict[str, Any] = {
        "route_id": route_id,
        "targets": tuple(asdict(item) for item in targets),
        "required_signal_dates": required_signal_dates,
        "base_ledger_hash": base.ledger_hash,
        "stress_ledger_hash": stress.ledger_hash,
    }
    if route_id == ROUTE_V2_BREAKOUT and leader_exit_policy != LEADER_EXIT_POLICY_LEGACY_MA5:
        route_identity.update(
            {
                "leader_exit_policy": leader_exit_policy,
                "leader_exit_policy_hash": leader_exit_policy_hash(leader_exit_policy),
                "route_policy_hash": dict(
                    comparison_route_policy_hashes_for_policy(leader_exit_policy)
                )[ROUTE_V2_BREAKOUT],
                "leader_exit_records": tuple(asdict(item) for item in leader_exit_records),
            }
        )
    return ComparisonRouteResult(
        route_id=route_id,
        status=status,
        reason=reason,
        required_signal_dates=required_signal_dates,
        targets=targets,
        base_ledger=base,
        stress_ledger=stress,
        input_hash=stable_contract_hash(route_identity),
        leader_exit_policy=leader_exit_policy,
        leader_exit_policy_hash=(
            leader_exit_policy_hash(leader_exit_policy)
            if route_id == ROUTE_V2_BREAKOUT
            else None
        ),
        leader_exit_records=(
            leader_exit_records if route_id == ROUTE_V2_BREAKOUT else ()
        ),
    )


def _route_prefix(route: ComparisonRouteResult) -> tuple[date, date] | None:
    if route.base_ledger is None or not route.base_ledger.points:
        return None
    return route.base_ledger.points[0].session_date, route.base_ledger.points[-1].session_date


def _route_payload(route: ComparisonRouteResult) -> dict[str, Any]:
    def ledger_payload(ledger: RankingPortfolioLedger | None) -> dict[str, Any] | None:
        if ledger is None:
            return None
        values = asdict(ledger)
        values["unavailable_intervals"] = [asdict(item) for item in ledger.unavailable_intervals]
        return values

    actual_interval = None
    metrics = None
    if route.base_ledger is not None and route.base_ledger.points:
        points = route.base_ledger.points
        actual_interval = {
            "start_date": points[0].session_date,
            "end_date": points[-1].session_date,
            "coverage_points": len(points),
        }
        average_exposure, average_cash, average_target_hhi = _ledger_report_metrics(route)
        metrics = {
            "average_exposure": average_exposure,
            "average_cash": average_cash,
            "average_target_hhi": average_target_hhi,
            "concentration_measure": "target_hhi",
            "classification_concentration": None,
        }

    payload = {
        "route_id": route.route_id,
        "policy_hash": dict(
            comparison_route_policy_hashes_for_policy(route.leader_exit_policy)
        ).get(route.route_id),
        "status": route.status,
        "reason": route.reason,
        "required_signal_dates": route.required_signal_dates,
        "targets": [asdict(item) for item in route.targets],
        "base": ledger_payload(route.base_ledger),
        "stress": ledger_payload(route.stress_ledger),
        "actual_interval": actual_interval,
        "metrics": metrics,
        "input_hash": route.input_hash,
    }
    if (
        route.route_id == ROUTE_V2_BREAKOUT
        and route.leader_exit_policy != LEADER_EXIT_POLICY_LEGACY_MA5
    ):
        payload.update(
            {
                "leader_exit_policy": route.leader_exit_policy,
                "leader_exit_policy_hash": route.leader_exit_policy_hash,
                "leader_exit_records": [asdict(item) for item in route.leader_exit_records],
            }
        )
    return payload


def _partial_route_result(
    *,
    input_data: ComparisonInput,
    route_id: str,
    blocked: ComparisonDataUnavailableError,
) -> ComparisonRouteResult | None:
    """Preserve a verified valuation prefix before a later route gap."""

    blocked_date = blocked.signal_date
    if blocked_date is None:
        return None
    sessions = tuple(
        day
        for day in input_data.valuation.trading_sessions
        if input_data.start_date <= day <= input_data.end_date
    )
    prefix_sessions = tuple(day for day in sessions if day < blocked_date)
    if not prefix_sessions:
        return None
    prefix_input = replace(input_data, end_date=prefix_sessions[-1])
    leader_exit_records: tuple[LeaderExitComparisonRecord, ...] = ()
    try:
        if route_id == ROUTE_V2_BREAKOUT:
            if prefix_input.leader_exit_policy == LEADER_EXIT_POLICY_LEGACY_MA5:
                targets = bridge_v2_targets(input_data=prefix_input)
            else:
                shared = build_shared_daily_v2_targets(prefix_input)
                targets = shared.targets
                leader_exit_records = shared.exit_records
        elif route_id == ROUTE_MEDIUM_TERM_MOMENTUM:
            targets = generate_medium_term_momentum_targets(prefix_input)
            required = _route_required_dates(
                input_data=prefix_input,
                route_id=route_id,
                targets=targets,
            )
            _ensure_common_pool_for_dates(input_data=prefix_input, required_dates=required)
            _ensure_targets_in_common_pool(input_data=prefix_input, targets=targets)
        else:
            targets = tuple(prefix_input.daily_core_targets)
            required = _route_required_dates(
                input_data=prefix_input,
                route_id=route_id,
                targets=targets,
            )
            if not targets:
                return None
            _ensure_common_pool_for_dates(input_data=prefix_input, required_dates=required)
            _ensure_targets_in_common_pool(input_data=prefix_input, targets=targets)
        required = _route_required_dates(
            input_data=prefix_input,
            route_id=route_id,
            targets=targets,
        )
        base, stress = _ledger_pair(
            input_data=prefix_input,
            targets=targets,
            required_signal_dates=required,
        )
    except ComparisonDataUnavailableError:
        return None
    partial = _route_result(
        route_id=route_id,
        targets=targets,
        required_signal_dates=required,
        base=base,
        stress=stress,
        leader_exit_policy=(
            input_data.leader_exit_policy
            if route_id == ROUTE_V2_BREAKOUT
            else LEADER_EXIT_POLICY_DEFAULT
        ),
        leader_exit_records=leader_exit_records,
    )
    return replace(
        partial,
        status="unavailable",
        reason=f"{blocked.reason}:{blocked_date.isoformat()}",
    )


def comparison_result_payload(result: ComparisonResult) -> dict[str, Any]:
    common_metrics = None
    if result.common_start_date is not None and result.common_end_date is not None:
        common_metrics = {
            route.route_id: _ledger_common_summary(
                route=route,
                start_date=result.common_start_date,
                end_date=result.common_end_date,
            )
            for route in result.routes
            if route.base_ledger is not None
        }
    payload = {
        "schema_version": result.schema_version,
        "experiment_id": result.experiment_id,
        "provenance": asdict(result.provenance),
        "initial_capital": result.initial_capital,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "routes": [_route_payload(route) for route in result.routes],
        "route_policy_hashes": dict(
            comparison_route_policy_hashes_for_policy(result.leader_exit_policy)
        ),
        "common_status": result.common_status,
        "common_start_date": result.common_start_date,
        "common_end_date": result.common_end_date,
        "common_reason": result.common_reason,
        "common_metrics": common_metrics,
        "input_hash": result.input_hash,
        "result_hash": result.result_hash,
    }
    if result.leader_exit_policy != LEADER_EXIT_POLICY_LEGACY_MA5:
        payload.update(
            {
                "leader_exit_policy": result.leader_exit_policy,
                "leader_exit_policy_hash": result.leader_exit_policy_hash,
            }
        )
    return _safe(payload)  # type: ignore[return-value]


def comparison_result_to_json(result: ComparisonResult) -> str:
    return json.dumps(comparison_result_payload(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ledger_report_metrics(
    route: ComparisonRouteResult,
) -> tuple[float, float, float | None]:
    ledger = route.base_ledger
    assert ledger is not None
    exposure: list[float] = []
    cash: list[float] = []
    for point in ledger.points:
        value = point.post_rebalance_net_value
        if value <= 0.0:
            continue
        cash_ratio = max(0.0, min(1.0, point.net_cash / value))
        cash.append(cash_ratio)
        exposure.append(1.0 - cash_ratio)
    target_concentration = [
        math.fsum(weight * weight for _code, weight in target.target_weights)
        for target in route.targets
    ]
    return (
        sum(exposure) / len(exposure) if exposure else 0.0,
        sum(cash) / len(cash) if cash else 1.0,
        (
            sum(target_concentration) / len(target_concentration)
            if target_concentration
            else None
        ),
    )


def _ledger_common_summary(
    *,
    route: ComparisonRouteResult,
    start_date: date,
    end_date: date,
) -> dict[str, float | int | None]:
    ledger = route.base_ledger
    assert ledger is not None
    points = tuple(
        point for point in ledger.points if start_date <= point.session_date <= end_date
    )
    if not points:
        return {
            "net_return": None,
            "gross_return": None,
            "maximum_drawdown": None,
            "turnover": 0.0,
            "cost": 0.0,
            "points": 0,
            "average_exposure": None,
            "average_cash": None,
            "average_target_hhi": None,
            "classification_concentration": None,
        }
    initial_net = points[0].pre_rebalance_net_value
    initial_gross = points[0].pre_rebalance_gross_value
    net_path = [initial_net]
    for point in points:
        net_path.extend((point.pre_rebalance_net_value, point.post_rebalance_net_value))
    peak = net_path[0]
    maximum_drawdown = 0.0
    for value in net_path:
        peak = max(peak, value)
        maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)
    exposure: list[float] = []
    cash: list[float] = []
    for point in points:
        if point.post_rebalance_net_value <= 0.0:
            continue
        cash_ratio = max(
            0.0,
            min(1.0, point.net_cash / point.post_rebalance_net_value),
        )
        cash.append(cash_ratio)
        exposure.append(1.0 - cash_ratio)
    hhi_values = [
        math.fsum(weight * weight for _code, weight in target.target_weights)
        for target in route.targets
        if start_date <= target.signal_date <= end_date
    ]
    return {
        "net_return": points[-1].post_rebalance_net_value / initial_net - 1.0,
        "gross_return": points[-1].post_rebalance_gross_value / initial_gross - 1.0,
        "maximum_drawdown": maximum_drawdown,
        "turnover": sum(point.net_trade_notional for point in points) / initial_net,
        "cost": sum(point.transaction_cost for point in points),
        "points": len(points),
        "average_exposure": sum(exposure) / len(exposure) if exposure else 0.0,
        "average_cash": sum(cash) / len(cash) if cash else 1.0,
        "average_target_hhi": sum(hhi_values) / len(hhi_values) if hhi_values else None,
        "classification_concentration": None,
    }


def comparison_result_to_markdown(result: ComparisonResult) -> str:
    route_policy_hashes = dict(
        comparison_route_policy_hashes_for_policy(result.leader_exit_policy)
    )
    lines = [
        "# ETF 三路线连续账户比较",
        "",
        "本报告是研究用途的探索性诊断，不改变生产策略，也不构成已验证赢家或实盘成交证明。",
        "",
        f"- 区间：{result.start_date.isoformat()} 至 {result.end_date.isoformat()}",
        f"- 初始资金：{result.initial_capital:g}",
        f"- 数据版本：{result.provenance.data_version}",
        f"- 三路线策略身份：{', '.join(f'{route}={policy_hash}' for route, policy_hash in route_policy_hashes.items())}",
        f"- 共同连续区间：{result.common_start_date.isoformat() if result.common_start_date else '暂无'} 至 {result.common_end_date.isoformat() if result.common_end_date else '暂无'}（{result.common_status}）",
        "",
        "| 路线 | 状态 | 基础净收益 | 基础毛收益 | 基础最大回撤 | 换手 | 成本 | 再平衡/订单 | 平均仓位/现金 | 平均目标 HHI（分类集中度暂无） | 压力净收益 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        ROUTE_V2_BREAKOUT: "ETF 龙头突破 V2",
        ROUTE_MEDIUM_TERM_MOMENTUM: "126 日正动量 Top10",
        ROUTE_DAILY_CORE_HYSTERESIS: "daily_core 低换手",
    }
    if result.leader_exit_policy != LEADER_EXIT_POLICY_LEGACY_MA5:
        labels[ROUTE_V2_BREAKOUT] = (
            "ETF 龙头突破 V2（共享日线 + 固定 2R 研究对照）"
            if result.leader_exit_policy == LEADER_EXIT_POLICY_SHARED_DAILY_2R
            else "ETF 龙头突破 V2（共享日线退出）"
        )
        lines.insert(
            7,
            f"- 龙头退出研究政策：{result.leader_exit_policy}（hash={result.leader_exit_policy_hash}；执行模型={LEADER_EXIT_EXECUTION_MODEL}）",
        )
        lines.insert(
            8,
            "- 规则：入场日 PIT 收盘冻结 ATR20/初始止损；盈利达到 1R 后启用保本线（entry reference，费用在连续账本执行时计入）；MA5 退出按同一份 PIT 日线重放。固定 2R 仅作为预注册、尚未验证的研究对照，不改变生产阈值。",
        )
        lines.insert(
            9,
            "- 研究边界：计划退出日按下一交易日计算，实际成交由连续账本证明；signal_low 重放自原始 V2 signal_date 的 PIT adjusted_low，与旧候选 gate 是否提供该字段分开记录；手续费、保本与盘中执行不作超出 PIT 证据的推断。",
        )
    interval_lines: list[str] = []
    for route in result.routes:
        base = route.base_ledger
        stress = route.stress_ledger
        if base is None or stress is None:
            lines.append(
                f"| {labels[route.route_id]} | 不可用：{route.reason or '暂无'} | 暂无 | 暂无 | 暂无 | 暂无 | 暂无 | 暂无 | 暂无 | 暂无 | 暂无 |"
            )
            continue
        def fmt(value: float | None) -> str:
            return "暂无" if value is None else f"{value:+.2%}"
        average_exposure, average_cash, average_concentration = _ledger_report_metrics(route)
        concentration_text = "暂无" if average_concentration is None else f"{average_concentration:.4f}"
        lines.append(
            f"| {labels[route.route_id]} | {route.status} | {fmt(base.net_return)} | {fmt(base.gross_return)} | {fmt(base.net_maximum_drawdown)} | {base.turnover:.4f} | {base.total_transaction_cost:.6f} | {base.rebalance_count}/{base.order_count} | {average_exposure:.2%}/{average_cash:.2%} | {concentration_text} | {fmt(stress.net_return)} |"
        )
        interval_lines.append(
            f"  - {labels[route.route_id]} 实际估值区间：{base.points[0].session_date.isoformat() if base.points else '暂无'} 至 {base.points[-1].session_date.isoformat() if base.points else '暂无'}；覆盖 {len(base.points)} 个交易日。"
        )
    lines.extend(
        [
            "",
            "策略选择、账户执行与证据状态分开记录。月度动量只在声明起始日和真实交易所月末生成目标；无目标的月内日期仍进行每日估值。缺少持仓价格、V2 状态检查或共同历史时，报告保留已完成前缀并显示具体阻断原因，不使用旧价格、零收益或拼接曲线。",
            "",
            f"结果身份：`{result.result_hash}`",
        ]
    )
    lines.extend(["", "## 各路线实际估值区间", "", *interval_lines])
    if result.leader_exit_policy != LEADER_EXIT_POLICY_LEGACY_MA5:
        lines.extend(
            [
                "",
                "## 龙头退出诊断",
                "",
                "| 资产 | 原始信号日 | 入场执行日 | 退出信号日 | 计划退出日 | 原因 | 信号数据合格 |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        v2_route = next(
            (route for route in result.routes if route.route_id == ROUTE_V2_BREAKOUT),
            None,
        )
        for item in v2_route.leader_exit_records if v2_route is not None else ():
            lines.append(
                f"| {item.asset_code} | {item.original_signal_date.isoformat()} | {item.entry_execution_date.isoformat()} | {item.exit_signal_date.isoformat() if item.exit_signal_date else '持有中'} | {item.exit_execution_date.isoformat() if item.exit_execution_date else '暂无'} | {item.exit_reason or '暂无'} | {'是' if item.data_eligible else '否'} |"
            )
    if result.common_start_date is not None and result.common_end_date is not None:
        lines.extend(
            [
                "",
                f"## 共同连续区间指标（{result.common_start_date.isoformat()} 至 {result.common_end_date.isoformat()}）",
                "",
                "| 路线 | 共同净收益 | 共同毛收益 | 共同最大回撤 | 共同换手 | 共同成本 | 估值点数 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for route in result.routes:
            if route.base_ledger is None:
                continue
            summary = _ledger_common_summary(
                route=route,
                start_date=result.common_start_date,
                end_date=result.common_end_date,
            )
            lines.append(
                f"| {labels[route.route_id]} | {fmt(summary['net_return'])} | {fmt(summary['gross_return'])} | {fmt(-float(summary['maximum_drawdown']) if summary['maximum_drawdown'] is not None else None)} | {float(summary['turnover']):.4f} | {float(summary['cost']):.6f} | {int(summary['points'])} |"
            )
    else:
        lines.extend(["", "共同连续区间指标：暂无，三条路线没有共同完整账本前缀。"])
    return "\n".join(lines) + "\n"


def run_comparison(input_data: ComparisonInput) -> ComparisonResult:
    """Run all available routes under one frozen capital and valuation contract."""

    sessions = tuple(
        day
        for day in input_data.valuation.trading_sessions
        if input_data.start_date <= day <= input_data.end_date
    )
    input_identity: dict[str, Any] = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "experiment_id": COMPARISON_EXPERIMENT_ID,
        "provenance": asdict(input_data.provenance),
        "start_date": input_data.start_date,
        "end_date": input_data.end_date,
        "valuation": asdict(input_data.valuation),
        "decision_snapshots": tuple(asdict(item) for item in input_data.decision_snapshots),
        "v2_events": tuple(asdict(item) for item in input_data.v2_events),
        "v2_state_checks": tuple(asdict(item) for item in input_data.v2_state_checks),
        "daily_core_targets": tuple(asdict(item) for item in input_data.daily_core_targets),
        "daily_core_required_signal_dates": input_data.daily_core_required_signal_dates,
        "route_policy_hashes": COMPARISON_ROUTE_POLICY_HASHES,
        "cost_policies": (
            asdict(RANKING_PORTFOLIO_BASE_COST_POLICY),
            asdict(RANKING_PORTFOLIO_STRESS_COST_POLICY),
        ),
        "execution_model": "t_plus_one_adjusted_close_continuous_cash_share_v1",
        "initial_capital": input_data.initial_capital,
    }
    if input_data.leader_exit_policy != LEADER_EXIT_POLICY_LEGACY_MA5:
        input_identity.update(
            {
                "schema_version": comparison_schema_version_for_policy(
                    input_data.leader_exit_policy
                ),
                "experiment_id": comparison_experiment_id_for_policy(
                    input_data.leader_exit_policy
                ),
                "leader_exit_policy": input_data.leader_exit_policy,
                "leader_exit_policy_hash": leader_exit_policy_hash(
                    input_data.leader_exit_policy
                ),
                "leader_exit_pit_series": tuple(
                    _pit_series_payload(item)
                    for item in input_data.leader_exit_pit_series
                ),
                "route_policy_hashes": comparison_route_policy_hashes_for_policy(
                    input_data.leader_exit_policy
                ),
            }
        )
    input_hash = stable_contract_hash(input_identity)
    routes: list[ComparisonRouteResult] = []

    try:
        v2_records: tuple[LeaderExitComparisonRecord, ...] = ()
        if input_data.leader_exit_policy == LEADER_EXIT_POLICY_LEGACY_MA5:
            v2_targets = bridge_v2_targets(input_data=input_data)
        else:
            shared = build_shared_daily_v2_targets(input_data)
            v2_targets = shared.targets
            v2_records = shared.exit_records
        v2_required = _route_required_dates(input_data=input_data, route_id=ROUTE_V2_BREAKOUT, targets=v2_targets)
        base, stress = _ledger_pair(input_data=input_data, targets=v2_targets, required_signal_dates=v2_required)
        routes.append(
            _route_result(
                route_id=ROUTE_V2_BREAKOUT,
                targets=v2_targets,
                required_signal_dates=v2_required,
                base=base,
                stress=stress,
                leader_exit_policy=input_data.leader_exit_policy,
                leader_exit_records=v2_records,
            )
        )
    except ComparisonDataUnavailableError as exc:
        partial = _partial_route_result(
            input_data=input_data,
            route_id=ROUTE_V2_BREAKOUT,
            blocked=exc,
        )
        routes.append(
            partial
            if partial is not None
            else _blocked_route(
                ROUTE_V2_BREAKOUT,
                exc.reason,
                sessions,
                input_hash,
                leader_exit_policy=input_data.leader_exit_policy,
            )
        )

    try:
        momentum_targets = generate_medium_term_momentum_targets(input_data)
        momentum_required = _route_required_dates(input_data=input_data, route_id=ROUTE_MEDIUM_TERM_MOMENTUM, targets=momentum_targets)
        _ensure_common_pool_for_dates(input_data=input_data, required_dates=momentum_required)
        _ensure_targets_in_common_pool(input_data=input_data, targets=momentum_targets)
        base, stress = _ledger_pair(input_data=input_data, targets=momentum_targets, required_signal_dates=momentum_required)
        routes.append(_route_result(route_id=ROUTE_MEDIUM_TERM_MOMENTUM, targets=momentum_targets, required_signal_dates=momentum_required, base=base, stress=stress))
    except ComparisonDataUnavailableError as exc:
        partial = _partial_route_result(
            input_data=input_data,
            route_id=ROUTE_MEDIUM_TERM_MOMENTUM,
            blocked=exc,
        )
        routes.append(
            partial
            if partial is not None
            else _blocked_route(
                ROUTE_MEDIUM_TERM_MOMENTUM,
                exc.reason,
                tuple(_momentum_signal_dates(input_data)),
                input_hash,
            )
        )

    try:
        daily_targets = tuple(input_data.daily_core_targets)
        daily_required = _route_required_dates(input_data=input_data, route_id=ROUTE_DAILY_CORE_HYSTERESIS, targets=daily_targets)
        if not daily_targets:
            raise ComparisonDataUnavailableError("daily_core_targets_missing")
        _ensure_common_pool_for_dates(input_data=input_data, required_dates=daily_required)
        _ensure_targets_in_common_pool(input_data=input_data, targets=daily_targets)
        base, stress = _ledger_pair(input_data=input_data, targets=daily_targets, required_signal_dates=daily_required)
        routes.append(_route_result(route_id=ROUTE_DAILY_CORE_HYSTERESIS, targets=daily_targets, required_signal_dates=daily_required, base=base, stress=stress))
    except ComparisonDataUnavailableError as exc:
        partial = _partial_route_result(
            input_data=input_data,
            route_id=ROUTE_DAILY_CORE_HYSTERESIS,
            blocked=exc,
        )
        routes.append(
            partial
            if partial is not None
            else _blocked_route(
                ROUTE_DAILY_CORE_HYSTERESIS,
                exc.reason,
                sessions,
                input_hash,
            )
        )

    route_prefixes = tuple(_route_prefix(route) for route in routes)
    all_routes_have_prefix = len(route_prefixes) == len(COMPARISON_ROUTE_IDS) and all(
        prefix is not None for prefix in route_prefixes
    )
    prefixes = tuple(prefix for prefix in route_prefixes if prefix is not None)
    common_start = max(item[0] for item in prefixes) if all_routes_have_prefix else None
    common_end = min(item[1] for item in prefixes) if all_routes_have_prefix else None
    complete = all_routes_have_prefix and all(route.status == "completed" for route in routes)
    if common_start is None or common_end is None or common_start > common_end:
        common_status: Literal["complete", "prefix_only", "unavailable"] = "unavailable"
        common_reason = "no_common_continuous_prefix"
    elif complete and common_start == input_data.start_date and common_end == input_data.end_date:
        common_status = "complete"
        common_reason = None
    else:
        common_status = "prefix_only"
        common_reason = "route_or_valuation_prefix_incomplete"
    draft = ComparisonResult(
        schema_version=comparison_schema_version_for_policy(input_data.leader_exit_policy),
        experiment_id=comparison_experiment_id_for_policy(input_data.leader_exit_policy),
        provenance=input_data.provenance,
        initial_capital=input_data.initial_capital,
        start_date=input_data.start_date,
        end_date=input_data.end_date,
        routes=tuple(routes),
        common_status=common_status,
        common_start_date=common_start,
        common_end_date=common_end,
        common_reason=common_reason,
        input_hash=input_hash,
        result_hash="pending",
        report_markdown="",
        leader_exit_policy=input_data.leader_exit_policy,
        leader_exit_policy_hash=leader_exit_policy_hash(input_data.leader_exit_policy)
        if input_data.leader_exit_policy != LEADER_EXIT_POLICY_LEGACY_MA5
        else "",
    )
    result_identity = asdict(draft)
    result_identity.pop("result_hash", None)
    result_identity.pop("report_markdown", None)
    if input_data.leader_exit_policy == LEADER_EXIT_POLICY_LEGACY_MA5:
        result_identity.pop("leader_exit_policy", None)
        result_identity.pop("leader_exit_policy_hash", None)
        for route in result_identity.get("routes", ()):
            route.pop("leader_exit_policy", None)
            route.pop("leader_exit_policy_hash", None)
            route.pop("leader_exit_records", None)
    result_hash = stable_contract_hash(result_identity)
    result = replace(draft, result_hash=result_hash)
    return replace(result, report_markdown=comparison_result_to_markdown(result))


__all__ = [
    "COMPARISON_EXPERIMENT_ID",
    "COMPARISON_ROUTE_IDS",
    "COMPARISON_ROUTE_POLICY_HASHES",
    "COMPARISON_SCHEMA_VERSION",
    "LEADER_EXIT_EXECUTION_MODEL",
    "LEADER_EXIT_POLICY_DEFAULT",
    "LEADER_EXIT_POLICY_HASHES",
    "LEADER_EXIT_POLICY_HASH_MAP",
    "LEADER_EXIT_POLICY_IDS",
    "LEADER_EXIT_POLICY_LEGACY_MA5",
    "LEADER_EXIT_POLICY_SHARED_DAILY",
    "LEADER_EXIT_POLICY_SHARED_DAILY_2R",
    "LEADER_EXIT_SHARED_DAILY_POLICY_HASH",
    "LEADER_EXIT_RESEARCH_EXPERIMENT_PREFIX",
    "LEADER_EXIT_RESEARCH_SCHEMA_VERSION",
    "ComparisonContractError",
    "ComparisonDataUnavailableError",
    "ComparisonDecisionSnapshot",
    "ComparisonInput",
    "ComparisonResult",
    "ComparisonRouteResult",
    "ComparisonValuationInput",
    "CommonPoolReadiness",
    "DailyCorePITDateInput",
    "DAILY_CORE_COMPARISON_POLICY_HASH",
    "FrozenComparisonProvenance",
    "LeaderExitComparisonRecord",
    "MEDIUM_TERM_MOMENTUM_CONTRACT_HASH",
    "MEDIUM_TERM_MOMENTUM_LOOKBACK_SESSIONS",
    "MEDIUM_TERM_MOMENTUM_REQUIRED_HISTORY_SESSIONS",
    "MEDIUM_TERM_MOMENTUM_TARGET_WEIGHT",
    "MEDIUM_TERM_MOMENTUM_TOP_N",
    "MediumTermMomentumSelection",
    "ROUTE_DAILY_CORE_HYSTERESIS",
    "ROUTE_MEDIUM_TERM_MOMENTUM",
    "ROUTE_V2_BREAKOUT",
    "V2ComparisonEvent",
    "V2_STATE_CHECK_SCHEMA_VERSION",
    "V2StateCheck",
    "V2_COMPARISON_ALLOCATION_POLICY_HASH",
    "SharedDailyV2BridgeResult",
    "build_v2_comparison_events",
    "build_v2_state_check",
    "bridge_v2_targets",
    "bridge_shared_daily_v2_targets",
    "build_shared_daily_v2_targets",
    "comparison_result_payload",
    "comparison_result_to_json",
    "comparison_result_to_markdown",
    "comparison_experiment_id_for_policy",
    "comparison_route_policy_hashes_for_policy",
    "comparison_schema_version_for_policy",
    "generate_medium_term_momentum_targets",
    "canonical_daily_core_targets",
    "canonical_daily_core_targets_from_pit",
    "preflight_common_pool",
    "run_comparison",
    "select_medium_term_momentum_top10",
    "leader_exit_policy_hash",
]
