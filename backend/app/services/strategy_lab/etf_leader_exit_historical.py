"""Historical current-vintage V2 leader-exit research.

This module is a pure replay boundary.  It accepts one frozen, current-vintage
dataset, rebuilds ETF BREAKOUT_V2 observations one signal date at a time, and
compares the three registered exit policies through the existing ranking
portfolio ledger.  It carries no production persistence or live-policy state.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.services.etf_research_evidence import stable_contract_hash
from app.services.leader_tactics_exit_policy import (
    evaluate_leader_exit_thresholds,
    initial_leader_risk,
    leader_atr20,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    HISTORICAL_RECONSTRUCTION_MODE,
    STANDARD_HISTORY,
    STATE_CONFIRMED,
    STATE_INVALIDATED,
    V2_CODE_VERSION,
    V2_FORMULA_REGISTRY_HASH,
    V2_LIFECYCLE_VERSION,
    V2_SCHEMA_VERSION,
    V2AdjustedBar,
    V2AssetInput,
    V2CandidateObservation,
    V2ContractError,
    V2LifecycleTransition,
    V2PITMembership,
    derive_lifecycle,
    screen_dual_universe,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    RANKING_PORTFOLIO_BASE_COST_POLICY,
    RANKING_PORTFOLIO_STRESS_COST_POLICY,
    ForwardAdjustedClose,
    RankingPortfolioLedger,
    RankingPortfolioPoint,
    RankingPortfolioTarget,
    RankingPortfolioUnavailableInterval,
    calculate_continuous_ranking_portfolio,
    freeze_ranking_portfolio_target,
)
from app.services.strategy_lab.etf_strategy_route_comparison import (
    LEADER_EXIT_POLICY_IDS,
    LEADER_EXIT_POLICY_LEGACY_MA5,
    LEADER_EXIT_POLICY_SHARED_DAILY_2R,
    leader_exit_policy_hash,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
HISTORICAL_V2_SCHEMA_VERSION = "etf_leader_exit_historical_v2"
HISTORICAL_V2_EXPERIMENT_ID = "etf_leader_exit_historical_v2_current_vintage"
HISTORICAL_V2_FORMAL_PIT_CREDIT = 0
HISTORICAL_V2_TOP_N = 10
HISTORICAL_V2_TARGET_WEIGHT = 0.10
HISTORICAL_V2_EXECUTION_MODEL = "next_session_adjusted_close_v1"
HISTORICAL_V2_ALLOCATION_VERSION = "top10_equal_weight_clone_exit_first_v1"
HISTORICAL_V2_ALLOCATION_HASH = stable_contract_hash(
    {
        "schema_version": HISTORICAL_V2_SCHEMA_VERSION,
        "allocation_version": HISTORICAL_V2_ALLOCATION_VERSION,
        "top_n": HISTORICAL_V2_TOP_N,
        "target_weight": HISTORICAL_V2_TARGET_WEIGHT,
        "selection_order": ("score_desc", "original_signal_date_desc", "asset_code_asc"),
        "clone_policy": "one_position_per_clone_group",
        "exit_before_entry": True,
        "execution_model": HISTORICAL_V2_EXECUTION_MODEL,
    }
)


class HistoricalV2ContractError(ValueError):
    """Raised when a frozen historical V2 contract is malformed."""


@dataclass(frozen=True, slots=True)
class HistoricalV2Asset:
    asset_code: str
    name: str
    listed_date: date
    membership: V2PITMembership | None
    bars: tuple[V2AdjustedBar, ...]
    underlying: str = ""

    @property
    def asset_name(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class HistoricalV2Dataset:
    frozen_at: datetime
    start_date: date
    end_date: date
    trading_sessions: tuple[date, ...]
    assets: tuple[HistoricalV2Asset, ...]
    source_hash: str = ""
    source_manifest: tuple[tuple[str, Any], ...] = ()
    exclusions: tuple[tuple[str, str], ...] = ()
    universe: str = "etf"
    theme: str = "current_vintage"
    underlying: str = "current_vintage"

    def __post_init__(self) -> None:
        _validate_dataset(self)
        _validate_frozen_facts(self)
        if not self.source_hash:
            object.__setattr__(self, "source_hash", historical_dataset_hash(self))
        elif self.source_hash != historical_dataset_hash(self, include_source_hash=False):
            raise HistoricalV2ContractError("historical dataset source hash is not canonical")

    @property
    def membership_evaluation_date(self) -> date:
        return self.frozen_at.astimezone(SHANGHAI).date()

    @property
    def manifest_hash(self) -> str:
        return self.source_hash


@dataclass(frozen=True, slots=True)
class HistoricalV2FlowEvent:
    event_type: Literal["candidate", "confirmation"]
    asset_code: str
    asset_name: str
    signal_date: date
    original_signal_date: date
    formula_id: str
    score: float
    clone_group: str
    feature_hash: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class HistoricalV2ExitTrade:
    policy_id: str
    policy_hash: str
    asset_code: str
    asset_name: str
    clone_group: str
    original_signal_date: date
    confirmation_date: date
    entry_execution_date: date | None
    entry_reference_price: float | None
    entry_atr20: float | None
    signal_low: float | None
    initial_stop: float | None
    risk_unit: float | None
    exit_signal_date: date | None
    exit_execution_date: date | None
    exit_price: float | None
    exit_reason: str | None
    status: Literal[
        "entry_pending", "open", "closed", "exit_pending", "unavailable"
    ]
    data_eligible: bool

    @property
    def entry_price(self) -> float | None:
        return self.entry_reference_price


@dataclass(frozen=True, slots=True)
class HistoricalV2PolicyResult:
    policy_id: str
    policy_hash: str
    targets: tuple[RankingPortfolioTarget, ...]
    ledger: RankingPortfolioLedger
    stress_ledger: RankingPortfolioLedger
    trades: tuple[HistoricalV2ExitTrade, ...]
    candidate_count: int
    confirmation_count: int
    entry_count: int
    exit_count: int
    exclusions: tuple[tuple[str, str], ...]
    input_hash: str

    @property
    def base_ledger(self) -> RankingPortfolioLedger:
        return self.ledger


@dataclass(frozen=True, slots=True)
class HistoricalV2ComparisonResult:
    dataset_hash: str
    experiment_id: str
    formal_pit_credit: int
    candidate_events: tuple[HistoricalV2FlowEvent, ...]
    confirmation_events: tuple[HistoricalV2FlowEvent, ...]
    policies: tuple[HistoricalV2PolicyResult, ...]
    exclusions: tuple[tuple[str, str], ...]

    @property
    def policy_results(self) -> tuple[HistoricalV2PolicyResult, ...]:
        return self.policies


def historical_dataset_hash(
    dataset: HistoricalV2Dataset,
    *,
    include_source_hash: bool = False,
) -> str:
    """Return the canonical identity of the frozen dataset manifest and facts."""

    payload: dict[str, Any] = {
        "schema_version": HISTORICAL_V2_SCHEMA_VERSION,
        "frozen_at": dataset.frozen_at,
        "start_date": dataset.start_date,
        "end_date": dataset.end_date,
        "trading_sessions": dataset.trading_sessions,
        "universe": dataset.universe,
        "theme": dataset.theme,
        "underlying": dataset.underlying,
        "v2_schema_version": V2_SCHEMA_VERSION,
        "v2_code_version": V2_CODE_VERSION,
        "v2_formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
        "v2_lifecycle_version": V2_LIFECYCLE_VERSION,
        "allocation_version": HISTORICAL_V2_ALLOCATION_VERSION,
        "allocation_hash": HISTORICAL_V2_ALLOCATION_HASH,
        "source_manifest": dataset.source_manifest,
        "exclusions": dataset.exclusions,
        "assets": tuple(
            {
                "asset_code": asset.asset_code,
                "name": asset.name,
                "listed_date": asset.listed_date,
                "underlying": asset.underlying,
                "membership": (
                    asdict(asset.membership) if asset.membership else None
                ),
                "bars": tuple(asdict(bar) for bar in asset.bars),
            }
            for asset in dataset.assets
        ),
    }
    if include_source_hash:
        payload["source_hash"] = dataset.source_hash
    return stable_contract_hash(payload)


def _validate_dataset(dataset: HistoricalV2Dataset) -> None:
    if dataset.frozen_at.tzinfo is None or dataset.frozen_at.utcoffset() is None:
        raise HistoricalV2ContractError("historical dataset frozen_at must be timezone-aware")
    if dataset.universe != "etf":
        raise HistoricalV2ContractError("historical V2 dataset must be an ETF universe")
    if dataset.start_date > dataset.end_date:
        raise HistoricalV2ContractError("historical dataset date range is invalid")
    sessions = tuple(dataset.trading_sessions)
    if not sessions or sessions != tuple(sorted(set(sessions))):
        raise HistoricalV2ContractError("historical trading sessions must be ordered and unique")
    if sessions[0] > dataset.start_date or sessions[-1] < dataset.end_date:
        raise HistoricalV2ContractError("historical calendar must cover the formal date range")
    if any(day < dataset.start_date or day > dataset.end_date for day in sessions if day >= dataset.start_date):
        raise HistoricalV2ContractError("historical formal sessions exceed the declared range")
    assets = tuple(dataset.assets)
    if tuple(item.asset_code for item in assets) != tuple(sorted(item.asset_code for item in assets)):
        raise HistoricalV2ContractError("historical assets must be sorted by asset_code")
    if len({item.asset_code for item in assets}) != len(assets):
        raise HistoricalV2ContractError("historical asset codes must be unique")
    for asset in assets:
        if not asset.asset_code.strip() or not asset.name.strip():
            raise HistoricalV2ContractError("historical asset identity is incomplete")
        if asset.listed_date > dataset.end_date:
            raise HistoricalV2ContractError("historical asset is listed after the dataset")
        bars = tuple(asset.bars)
        if tuple(item.trade_date for item in bars) != tuple(
            sorted(item.trade_date for item in bars)
        ) or len({item.trade_date for item in bars}) != len(bars):
            raise HistoricalV2ContractError(
                f"historical bars are not canonical for {asset.asset_code}"
            )
        if any(item.trade_date > dataset.end_date for item in bars):
            raise HistoricalV2ContractError(
                f"historical bars extend beyond dataset end for {asset.asset_code}"
            )
    if dataset.source_manifest and tuple(key for key, _ in dataset.source_manifest) != tuple(
        sorted(key for key, _ in dataset.source_manifest)
    ):
        raise HistoricalV2ContractError("historical source manifest must be ordered")


def run_historical_v2_exit_comparison(
    dataset: HistoricalV2Dataset,
    *,
    policies: Sequence[str] = LEADER_EXIT_POLICY_IDS,
) -> HistoricalV2ComparisonResult:
    """Run one frozen current-vintage V2 candidate stream through three exits."""

    policy_ids = tuple(policies)
    if policy_ids != tuple(dict.fromkeys(policy_ids)) or any(
        item not in LEADER_EXIT_POLICY_IDS for item in policy_ids
    ):
        raise HistoricalV2ContractError("historical exit policies are not registered")
    if not policy_ids:
        raise HistoricalV2ContractError("historical exit policy set is empty")
    _validate_frozen_facts(dataset)
    formal_sessions = _formal_sessions(dataset)
    candidates, confirmations, lifecycles, flow_exclusions = _build_historical_flow(
        dataset,
        formal_sessions=formal_sessions,
    )
    policy_results = tuple(
        _simulate_policy(
            dataset,
            formal_sessions=formal_sessions,
            candidate_events=candidates,
            confirmation_events=confirmations,
            lifecycles=lifecycles,
            policy_id=policy_id,
        )
        for policy_id in policy_ids
    )
    return HistoricalV2ComparisonResult(
        dataset_hash=dataset.source_hash,
        experiment_id=HISTORICAL_V2_EXPERIMENT_ID,
        formal_pit_credit=HISTORICAL_V2_FORMAL_PIT_CREDIT,
        candidate_events=candidates,
        confirmation_events=confirmations,
        policies=policy_results,
        exclusions=tuple(sorted(flow_exclusions)),
    )


@dataclass(frozen=True, slots=True)
class _HistoricalLifecycle:
    candidate: HistoricalV2FlowEvent
    observation: V2CandidateObservation
    confirmation: HistoricalV2FlowEvent
    transition: V2LifecycleTransition
    invalidation: V2LifecycleTransition | None


@dataclass(slots=True)
class _HistoricalPosition:
    lifecycle: _HistoricalLifecycle
    entry_execution_date: date | None
    entry_reference_price: float | None = None
    entry_atr20: float | None = None
    signal_low: float | None = None
    initial_stop: float | None = None
    risk_unit: float | None = None
    visible_closes: list[float] = None  # type: ignore[assignment]
    trade: HistoricalV2ExitTrade | None = None
    armed: bool = False

    def __post_init__(self) -> None:
        if self.visible_closes is None:
            self.visible_closes = []


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _formal_sessions(dataset: HistoricalV2Dataset) -> tuple[date, ...]:
    return tuple(
        day
        for day in dataset.trading_sessions
        if dataset.start_date <= day <= dataset.end_date
    )


def _validate_frozen_facts(dataset: HistoricalV2Dataset) -> None:
    for asset in dataset.assets:
        if asset.membership is not None:
            observed_at = asset.membership.observed_at
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise HistoricalV2ContractError(
                    f"membership receipt is naive for {asset.asset_code}"
                )
            if observed_at > dataset.frozen_at:
                raise HistoricalV2ContractError(
                    f"membership receipt follows frozen_at for {asset.asset_code}"
                )
            if not asset.membership.fact_hash.strip():
                raise HistoricalV2ContractError(
                    f"membership fact hash is missing for {asset.asset_code}"
                )
        provenance: tuple[str, str, str] | None = None
        for bar in asset.bars:
            observed_at = bar.observed_at
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise HistoricalV2ContractError(
                    f"bar receipt is naive for {asset.asset_code}:{bar.trade_date}"
                )
            if observed_at > dataset.frozen_at:
                raise HistoricalV2ContractError(
                    f"bar receipt follows frozen_at for {asset.asset_code}:{bar.trade_date}"
                )
            if observed_at.date() < bar.trade_date:
                raise HistoricalV2ContractError(
                    f"bar receipt precedes trade date for {asset.asset_code}:{bar.trade_date}"
                )
            if (
                not bar.provider.strip()
                or not bar.adjustment_version.strip()
                or not bar.revision_id.strip()
                or bar.price_basis != "total_return_adjusted"
            ):
                raise HistoricalV2ContractError(
                    f"bar provenance is incomplete for {asset.asset_code}:{bar.trade_date}"
                )
            current_provenance = (
                bar.provider,
                bar.adjustment_version,
                bar.price_basis,
            )
            if provenance is None:
                provenance = current_provenance
            elif current_provenance != provenance:
                raise HistoricalV2ContractError(
                    f"bar provenance changes within {asset.asset_code}"
                )
            values = (
                bar.adjusted_open,
                bar.adjusted_high,
                bar.adjusted_low,
                bar.adjusted_close,
                bar.volume,
                bar.amount,
                bar.turnover,
            )
            if any(
                isinstance(value, bool) or not math.isfinite(float(value))
                for value in values
            ) or any(
                value <= 0.0
                for value in (
                    bar.adjusted_open,
                    bar.adjusted_high,
                    bar.adjusted_low,
                    bar.adjusted_close,
                )
            ):
                raise HistoricalV2ContractError(
                    f"bar values are invalid for {asset.asset_code}:{bar.trade_date}"
                )


def _next_session(sessions: tuple[date, ...], value: date) -> date | None:
    index = bisect_right(sessions, value)
    return sessions[index] if index < len(sessions) and sessions[index - 1] == value else None


def _asset_inputs_for_date(
    dataset: HistoricalV2Dataset,
    signal_date: date,
) -> tuple[V2AssetInput, ...]:
    membership_date = dataset.membership_evaluation_date
    inputs: list[V2AssetInput] = []
    for asset in dataset.assets:
        if asset.listed_date > signal_date:
            continue
        bars = tuple(bar for bar in asset.bars if bar.trade_date <= signal_date)
        expected_sessions = tuple(
            day
            for day in dataset.trading_sessions
            if asset.listed_date <= day <= signal_date
        )
        required_sessions = expected_sessions[-STANDARD_HISTORY:]
        actual_dates = tuple(bar.trade_date for bar in bars)
        actual_window = actual_dates[-len(required_sessions) :] if required_sessions else ()
        input_reasons = (
            ("historical_session_gap",)
            if actual_window != required_sessions
            else ()
        )
        inputs.append(
            V2AssetInput(
                universe=dataset.universe,
                asset_code=asset.asset_code,
                asset_name=asset.name,
                signal_date=signal_date,
                source_cutoff=dataset.frozen_at,
                bars=bars,
                membership=asset.membership,
                input_unavailable_reasons=input_reasons,
                decision_mode=HISTORICAL_RECONSTRUCTION_MODE,
                membership_evaluation_date=membership_date,
                identity_cutoff=dataset.frozen_at,
            )
        )
    return tuple(inputs)


def _contiguous_visible_through(
    asset: HistoricalV2Asset,
    *,
    sessions: tuple[date, ...],
    signal_date: date,
    end_date: date,
) -> date:
    """Stop lifecycle evidence before the first missing/invalid exchange day."""

    last_visible = signal_date
    try:
        start_index = sessions.index(signal_date)
    except ValueError:
        return signal_date
    for day in sessions[start_index + 1 :]:
        if day > end_date:
            break
        bar = _bar_for_date(asset, day)
        if _valid_close(bar) is None or _finite(bar.adjusted_high if bar else None) is None:
            break
        last_visible = day
    return last_visible


def _transition_hash(transition: V2LifecycleTransition) -> str:
    payload = asdict(transition)
    payload.pop("transition_hash")
    return stable_contract_hash(payload)


def _build_historical_flow(
    dataset: HistoricalV2Dataset,
    *,
    formal_sessions: tuple[date, ...],
) -> tuple[
    tuple[HistoricalV2FlowEvent, ...],
    tuple[HistoricalV2FlowEvent, ...],
    tuple[_HistoricalLifecycle, ...],
    tuple[tuple[str, str], ...],
]:
    assets = {item.asset_code: item for item in dataset.assets}
    candidates: list[HistoricalV2FlowEvent] = []
    confirmations: list[HistoricalV2FlowEvent] = []
    lifecycles: list[_HistoricalLifecycle] = []
    exclusions: list[tuple[str, str]] = []
    for signal_date in formal_sessions:
        inputs = _asset_inputs_for_date(dataset, signal_date)
        if not inputs:
            exclusions.append((signal_date.isoformat(), "historical_universe_empty"))
            continue
        try:
            screen = screen_dual_universe(inputs)
        except V2ContractError as exc:
            exclusions.append((signal_date.isoformat(), f"screen_unavailable:{type(exc).__name__}"))
            continue
        for observation in screen.observations:
            if observation.formula_id != BREAKOUT_V2:
                continue
            key = f"{observation.asset_code}:{signal_date.isoformat()}"
            if observation.qualifies is not True or observation.availability != "available":
                if observation.exclusion_reasons:
                    exclusions.extend((key, reason) for reason in observation.exclusion_reasons)
                continue
            if not observation.clone_group:
                exclusions.append((key, "missing_clone_group"))
                continue
            if observation.score is None or not math.isfinite(observation.score):
                exclusions.append((key, "missing_candidate_score"))
                continue
            asset = assets[observation.asset_code]
            candidate = HistoricalV2FlowEvent(
                event_type="candidate",
                asset_code=observation.asset_code,
                asset_name=observation.asset_name,
                signal_date=observation.signal_date,
                original_signal_date=observation.signal_date,
                formula_id=observation.formula_id,
                score=float(observation.score),
                clone_group=observation.clone_group,
                feature_hash=observation.feature_hash,
                source_hash=screen.manifest_hash,
            )
            candidates.append(candidate)
            try:
                visible_through = _contiguous_visible_through(
                    asset,
                    sessions=dataset.trading_sessions,
                    signal_date=signal_date,
                    end_date=dataset.end_date,
                )
                transitions = derive_lifecycle(
                    observation=observation,
                    signal_bars=tuple(asset.bars),
                    evaluation_cutoff=dataset.frozen_at,
                    visible_through=visible_through,
                )
            except V2ContractError as exc:
                exclusions.append((key, f"lifecycle_unavailable:{type(exc).__name__}"))
                continue
            for transition in transitions:
                if transition.transition_hash != _transition_hash(transition):
                    exclusions.append((key, "transition_hash_mismatch"))
                    continue
                if transition.to_state != STATE_CONFIRMED:
                    continue
                if transition.transition_date not in formal_sessions:
                    continue
                confirmation = HistoricalV2FlowEvent(
                    event_type="confirmation",
                    asset_code=observation.asset_code,
                    asset_name=observation.asset_name,
                    signal_date=transition.transition_date,
                    original_signal_date=transition.signal_date,
                    formula_id=transition.formula_id,
                    score=float(observation.score),
                    clone_group=observation.clone_group,
                    feature_hash=observation.feature_hash,
                    source_hash=transition.transition_hash,
                )
                invalidation = next(
                    (
                        item
                        for item in transitions
                        if item.to_state == STATE_INVALIDATED
                        and item.transition_date > transition.transition_date
                    ),
                    None,
                )
                confirmations.append(confirmation)
                lifecycles.append(
                    _HistoricalLifecycle(
                        candidate=candidate,
                        observation=observation,
                        confirmation=confirmation,
                        transition=transition,
                        invalidation=invalidation,
                    )
                )
                break
    candidates = sorted(
        candidates,
        key=lambda item: (item.signal_date, -item.score, item.asset_code),
    )
    confirmations = sorted(
        confirmations,
        key=lambda item: (
            item.signal_date,
            -item.score,
            item.asset_code,
            item.original_signal_date,
        ),
    )
    lifecycles = sorted(
        lifecycles,
        key=lambda item: (
            item.confirmation.signal_date,
            -item.confirmation.score,
            item.confirmation.asset_code,
            item.confirmation.original_signal_date,
        ),
    )
    return tuple(candidates), tuple(confirmations), tuple(lifecycles), tuple(sorted(set(exclusions)))


def _bar_for_date(asset: HistoricalV2Asset, session_date: date) -> V2AdjustedBar | None:
    for bar in asset.bars:
        if bar.trade_date == session_date:
            return bar
    return None


def _valid_close(bar: V2AdjustedBar | None) -> float | None:
    if bar is None or bar.decision_eligible is not True or bar.price_basis != "total_return_adjusted":
        return None
    close = _finite(bar.adjusted_close)
    return close if close is not None and close > 0.0 else None


def _ma5(
    asset: HistoricalV2Asset,
    sessions: tuple[date, ...],
    session_date: date,
) -> float | None:
    try:
        index = sessions.index(session_date)
    except ValueError:
        return None
    required = sessions[max(0, index - 4) : index + 1]
    if len(required) != 5:
        return None
    closes = tuple(_valid_close(_bar_for_date(asset, day)) for day in required)
    if any(value is None for value in closes):
        return None
    return math.fsum(value for value in closes if value is not None) / 5.0


def _initialize_position(
    position: _HistoricalPosition,
    *,
    asset: HistoricalV2Asset,
    sessions: tuple[date, ...],
) -> str | None:
    entry_date = position.entry_execution_date
    if entry_date is None:
        return "entry_pending"
    entry_bar = _bar_for_date(asset, entry_date)
    entry_close = _valid_close(entry_bar)
    signal_bar = _bar_for_date(asset, position.lifecycle.candidate.original_signal_date)
    signal_low = _finite(signal_bar.adjusted_low) if signal_bar is not None else None
    if entry_close is None or signal_low is None or signal_low <= 0.0:
        return "entry_price_or_signal_low_missing"
    try:
        entry_index = sessions.index(entry_date)
    except ValueError:
        return "entry_session_missing"
    expected_sessions = sessions[max(0, entry_index - 20) : entry_index + 1]
    if len(expected_sessions) != 21:
        return "entry_atr20_sessions_missing"
    atr_bars = tuple(_bar_for_date(asset, day) for day in expected_sessions)
    if any(
        bar is None
        or _finite(bar.adjusted_high) is None
        or _finite(bar.adjusted_low) is None
        or _valid_close(bar) is None
        for bar in atr_bars
    ):
        return "entry_atr20_sessions_missing"
    concrete_bars = tuple(bar for bar in atr_bars if bar is not None)
    atr = leader_atr20(
        tuple(float(bar.adjusted_high) for bar in concrete_bars),
        tuple(float(bar.adjusted_low) for bar in concrete_bars),
        tuple(float(bar.adjusted_close) for bar in concrete_bars),
    )
    if atr is None:
        return "entry_atr20_missing"
    risk = initial_leader_risk(
        entry_close=entry_close,
        entry_atr20=atr,
        source_signal_low=signal_low,
    )
    if risk is None:
        return "entry_initial_risk_invalid"
    initial_stop, risk_unit, _ignored_signal_low = risk
    position.entry_reference_price = entry_close
    position.entry_atr20 = atr
    position.signal_low = signal_low
    position.initial_stop = initial_stop
    position.risk_unit = risk_unit
    return None


def _trade_for_position(
    position: _HistoricalPosition,
    *,
    policy_id: str,
    policy_hash: str,
    status: Literal[
        "entry_pending", "open", "closed", "exit_pending", "unavailable"
    ],
    exit_signal_date: date | None = None,
    exit_execution_date: date | None = None,
    exit_price: float | None = None,
    exit_reason: str | None = None,
    data_eligible: bool,
) -> HistoricalV2ExitTrade:
    event = position.lifecycle.confirmation
    return HistoricalV2ExitTrade(
        policy_id=policy_id,
        policy_hash=policy_hash,
        asset_code=event.asset_code,
        asset_name=event.asset_name,
        clone_group=event.clone_group,
        original_signal_date=event.original_signal_date,
        confirmation_date=event.signal_date,
        entry_execution_date=position.entry_execution_date,
        entry_reference_price=position.entry_reference_price,
        entry_atr20=position.entry_atr20,
        signal_low=position.signal_low,
        initial_stop=position.initial_stop,
        risk_unit=position.risk_unit,
        exit_signal_date=exit_signal_date,
        exit_execution_date=exit_execution_date,
        exit_price=exit_price,
        exit_reason=exit_reason,
        status=status,
        data_eligible=data_eligible,
    )


def _historical_adjusted_closes(
    dataset: HistoricalV2Dataset,
    *,
    sessions: Sequence[date],
) -> tuple[ForwardAdjustedClose, ...]:
    session_set = set(sessions)
    rows: list[ForwardAdjustedClose] = []
    for asset in dataset.assets:
        for bar in asset.bars:
            if bar.trade_date not in session_set or _valid_close(bar) is None:
                continue
            rows.append(
                ForwardAdjustedClose(
                    asset_code=asset.asset_code,
                    session_date=bar.trade_date,
                    adjusted_close=float(bar.adjusted_close),
                    price_basis=bar.price_basis,
                    decision_eligible=True,
                    provider=bar.provider,
                    adjustment_version=bar.adjustment_version,
                    source_hash=bar.fact_hash or stable_contract_hash(asdict(bar)),
                )
            )
    return tuple(rows)


def _block_completed_ledger(
    ledger: RankingPortfolioLedger,
    *,
    failure_date: date,
    reason: str,
) -> RankingPortfolioLedger:
    if ledger.status != "completed":
        return ledger
    interval = RankingPortfolioUnavailableInterval(
        start_session=failure_date,
        end_session=failure_date,
        asset_codes=(),
        reason=reason,
    )
    draft = replace(
        ledger,
        status="unavailable",
        net_return=None,
        gross_return=None,
        net_maximum_drawdown=None,
        gross_maximum_drawdown=None,
        final_net_cash=None,
        final_gross_cash=None,
        unavailable_intervals=(*ledger.unavailable_intervals, interval),
        ledger_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("ledger_hash")
    return replace(draft, ledger_hash=stable_contract_hash(payload))


def _ledger_trade_counts(
    ledger: RankingPortfolioLedger,
    *,
    formal_sessions: tuple[date, ...],
    trades: Sequence[HistoricalV2ExitTrade],
) -> tuple[int, int]:
    """Count only entries and exits proven by the ledger's valid prefix.

    A trade record can be marked ineligible after its entry when a later
    valuation is missing.  The ledger still contains the earlier rebalance,
    so entry and exit summaries must be derived from those actual points
    rather than from the final trade-level eligibility flag.
    """

    points_by_date = {point.session_date: point for point in ledger.points}
    session_index = {session: index for index, session in enumerate(formal_sessions)}

    def holdings(point: RankingPortfolioPoint | None, code: str) -> float | None:
        if point is None:
            return None
        values = dict(point.net_holdings)
        value = values.get(code)
        return float(value) if value is not None else None

    def previous_point(session_date: date):
        index = session_index.get(session_date)
        if index is None or index == 0:
            return None
        return points_by_date.get(formal_sessions[index - 1])

    entries = 0
    exits = 0
    for trade in trades:
        entry_date = trade.entry_execution_date
        if entry_date is not None:
            entry_point = points_by_date.get(entry_date)
            before_entry = previous_point(entry_date)
            entry_value = holdings(entry_point, trade.asset_code)
            before_value = holdings(before_entry, trade.asset_code)
            if (
                entry_point is not None
                and entry_point.target_hash is not None
                and entry_value is not None
                and entry_value > 0.0
                and (before_entry is None or before_value in (None, 0.0))
            ):
                entries += 1

        exit_date = trade.exit_execution_date
        if trade.status != "closed" or exit_date is None:
            continue
        exit_point = points_by_date.get(exit_date)
        before_exit = previous_point(exit_date)
        before_value = holdings(before_exit, trade.asset_code)
        after_value = holdings(exit_point, trade.asset_code)
        if (
            exit_point is not None
            and exit_point.target_hash is not None
            and before_value is not None
            and before_value > 0.0
            and (after_value is None or after_value == 0.0)
        ):
            exits += 1
    return entries, exits


def _simulate_policy(
    dataset: HistoricalV2Dataset,
    *,
    formal_sessions: tuple[date, ...],
    candidate_events: tuple[HistoricalV2FlowEvent, ...],
    confirmation_events: tuple[HistoricalV2FlowEvent, ...],
    lifecycles: tuple[_HistoricalLifecycle, ...],
    policy_id: str,
) -> HistoricalV2PolicyResult:
    policy_hash = leader_exit_policy_hash(policy_id)
    assets = {item.asset_code: item for item in dataset.assets}
    confirmations_by_date: dict[date, list[_HistoricalLifecycle]] = {}
    for item in lifecycles:
        confirmations_by_date.setdefault(item.confirmation.signal_date, []).append(item)
    held: dict[str, _HistoricalPosition] = {}
    targets: list[RankingPortfolioTarget] = []
    trades: list[HistoricalV2ExitTrade] = []
    exclusions: list[tuple[str, str]] = []
    previous_membership: tuple[str, ...] = ()
    stopped = False
    decision_failure_date: date | None = None
    for session_date in formal_sessions:
        if stopped:
            break
        removed_clone_groups: set[str] = set()
        halt_after_day = False
        stop_after_day = False
        for code, position in tuple(sorted(held.items())):
            entry_date = position.entry_execution_date
            if entry_date is None or session_date < entry_date:
                continue
            asset = assets[code]
            if position.entry_reference_price is None:
                reason = _initialize_position(
                    position,
                    asset=asset,
                    sessions=dataset.trading_sessions,
                )
                if reason is not None:
                    exclusions.append((f"{code}:{session_date.isoformat()}", reason))
                    position.trade = _trade_for_position(
                        position,
                        policy_id=policy_id,
                        policy_hash=policy_hash,
                        status="unavailable",
                        data_eligible=False,
                    )
                    trades.append(position.trade)
                    decision_failure_date = session_date
                    halt_after_day = True
                    break
            current = _valid_close(_bar_for_date(asset, session_date))
            if current is None:
                exclusions.append(
                    (f"{code}:{session_date.isoformat()}", "held_price_missing")
                )
                position.trade = _trade_for_position(
                    position,
                    policy_id=policy_id,
                    policy_hash=policy_hash,
                    status="unavailable",
                    data_eligible=False,
                )
                trades.append(position.trade)
                decision_failure_date = session_date
                halt_after_day = True
                break
            position.visible_closes.append(current)
            exit_reason: str | None = None
            if policy_id == LEADER_EXIT_POLICY_LEGACY_MA5:
                invalidation = position.lifecycle.invalidation
                if invalidation is not None and invalidation.transition_date == session_date:
                    exit_reason = invalidation.reason
            else:
                ma5 = _ma5(asset, dataset.trading_sessions, session_date)
                if ma5 is None:
                    exclusions.append(
                        (f"{code}:{session_date.isoformat()}", "exit_ma5_missing")
                    )
                    position.trade = _trade_for_position(
                        position,
                        policy_id=policy_id,
                        policy_hash=policy_hash,
                        status="unavailable",
                        data_eligible=False,
                    )
                    trades.append(position.trade)
                    decision_failure_date = session_date
                    halt_after_day = True
                    break
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
                exit_reason = threshold.reason_code
            if exit_reason is None:
                continue
            execution_date = _next_session(dataset.trading_sessions, session_date)
            execution_price = (
                _valid_close(_bar_for_date(asset, execution_date))
                if execution_date is not None
                else None
            )
            if execution_date is None:
                position.trade = _trade_for_position(
                    position,
                    policy_id=policy_id,
                    policy_hash=policy_hash,
                    status="exit_pending",
                    exit_signal_date=session_date,
                    exit_execution_date=execution_date,
                    exit_reason=exit_reason,
                    data_eligible=True,
                )
                trades.append(position.trade)
                held.pop(code, None)
                removed_clone_groups.add(position.lifecycle.confirmation.clone_group)
                continue
            if execution_price is None:
                exclusions.append(
                    (f"{code}:{session_date.isoformat()}", "exit_price_missing")
                )
                position.trade = _trade_for_position(
                    position,
                    policy_id=policy_id,
                    policy_hash=policy_hash,
                    status="unavailable",
                    exit_signal_date=session_date,
                    exit_execution_date=execution_date,
                    exit_reason=exit_reason,
                    data_eligible=False,
                )
                trades.append(position.trade)
                held.pop(code, None)
                removed_clone_groups.add(position.lifecycle.confirmation.clone_group)
                decision_failure_date = execution_date
                stop_after_day = True
                continue
            position.trade = _trade_for_position(
                position,
                policy_id=policy_id,
                policy_hash=policy_hash,
                status="closed",
                exit_signal_date=session_date,
                exit_execution_date=execution_date,
                exit_price=execution_price,
                exit_reason=exit_reason,
                data_eligible=True,
            )
            trades.append(position.trade)
            held.pop(code, None)
            removed_clone_groups.add(position.lifecycle.confirmation.clone_group)
        if halt_after_day:
            # Preserve every position that was already held when one
            # decision became unavailable.  Do not invent an exit for the
            # positions after the first missing decision.
            for _code, position in sorted(held.items()):
                if position.trade is not None:
                    continue
                position.trade = _trade_for_position(
                    position,
                    policy_id=policy_id,
                    policy_hash=policy_hash,
                    status="unavailable",
                    data_eligible=False,
                )
                trades.append(position.trade)
        if not halt_after_day:
            for lifecycle in sorted(
                confirmations_by_date.get(session_date, ()),
                key=lambda item: (
                    -item.confirmation.score,
                    -item.confirmation.original_signal_date.toordinal(),
                    item.confirmation.asset_code,
                ),
            ):
                event = lifecycle.confirmation
                if len(held) >= HISTORICAL_V2_TOP_N:
                    exclusions.append((event.asset_code, "top10_capacity"))
                    continue
                if (
                    event.asset_code in held
                    or event.clone_group in removed_clone_groups
                    or event.clone_group
                    in {item.lifecycle.confirmation.clone_group for item in held.values()}
                ):
                    exclusions.append((event.asset_code, "clone_or_existing_position"))
                    continue
                entry_date = _next_session(dataset.trading_sessions, session_date)
                held[event.asset_code] = _HistoricalPosition(
                    lifecycle=lifecycle,
                    entry_execution_date=entry_date,
                )
        membership = tuple(sorted(held))
        if membership != previous_membership:
            targets.append(
                freeze_ranking_portfolio_target(
                    signal_date=session_date,
                    target_weights={
                        code: HISTORICAL_V2_TARGET_WEIGHT for code in membership
                    },
                    source_hash=stable_contract_hash(
                        {
                            "schema_version": HISTORICAL_V2_SCHEMA_VERSION,
                            "dataset_hash": dataset.source_hash,
                            "policy_id": policy_id,
                            "policy_hash": policy_hash,
                            "signal_date": session_date,
                            "selected_asset_codes": membership,
                            "confirmation_events": tuple(
                                asdict(item.lifecycle.confirmation) for item in held.values()
                            ),
                        }
                    ),
                )
            )
            previous_membership = membership
        if halt_after_day or stop_after_day:
            stopped = True
            break
    if not stopped:
        for code, position in sorted(held.items()):
            if position.entry_execution_date is None:
                position.trade = _trade_for_position(
                    position,
                    policy_id=policy_id,
                    policy_hash=policy_hash,
                    status="entry_pending",
                    data_eligible=False,
                )
            elif position.entry_reference_price is None:
                reason = _initialize_position(
                    position,
                    asset=assets[code],
                    sessions=dataset.trading_sessions,
                )
                if reason is not None:
                    exclusions.append((code, reason))
                    position.trade = _trade_for_position(
                        position,
                        policy_id=policy_id,
                        policy_hash=policy_hash,
                        status="unavailable",
                        data_eligible=False,
                    )
                else:
                    position.trade = _trade_for_position(
                        position,
                        policy_id=policy_id,
                        policy_hash=policy_hash,
                        status="open",
                        data_eligible=True,
                    )
            else:
                position.trade = _trade_for_position(
                    position,
                    policy_id=policy_id,
                    policy_hash=policy_hash,
                    status="open",
                    data_eligible=True,
                )
            trades.append(position.trade)
    targets = tuple(targets)
    adjusted_closes = _historical_adjusted_closes(dataset, sessions=formal_sessions)
    target_dates = {item.signal_date for item in targets}
    required_signal_dates = tuple(
        sorted(
            target_dates
            | (
                {decision_failure_date}
                if decision_failure_date is not None
                and decision_failure_date not in target_dates
                else set()
            )
        )
    )
    base_ledger = calculate_continuous_ranking_portfolio(
        trading_sessions=formal_sessions,
        adjusted_closes=adjusted_closes,
        targets=targets,
        cost_policy=RANKING_PORTFOLIO_BASE_COST_POLICY,
        required_signal_dates=required_signal_dates,
    )
    stress_ledger = calculate_continuous_ranking_portfolio(
        trading_sessions=formal_sessions,
        adjusted_closes=adjusted_closes,
        targets=targets,
        cost_policy=RANKING_PORTFOLIO_STRESS_COST_POLICY,
        required_signal_dates=required_signal_dates,
    )
    if decision_failure_date is not None:
        base_ledger = _block_completed_ledger(
            base_ledger,
            failure_date=decision_failure_date,
            reason="historical_exit_decision_unavailable",
        )
        stress_ledger = _block_completed_ledger(
            stress_ledger,
            failure_date=decision_failure_date,
            reason="historical_exit_decision_unavailable",
        )
    input_hash = stable_contract_hash(
        {
            "schema_version": HISTORICAL_V2_SCHEMA_VERSION,
            "dataset_hash": dataset.source_hash,
            "v2_schema_version": V2_SCHEMA_VERSION,
            "v2_code_version": V2_CODE_VERSION,
            "v2_formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
            "v2_lifecycle_version": V2_LIFECYCLE_VERSION,
            "allocation_hash": HISTORICAL_V2_ALLOCATION_HASH,
            "policy_id": policy_id,
            "policy_hash": policy_hash,
            "candidate_events": tuple(asdict(item) for item in candidate_events),
            "confirmation_events": tuple(asdict(item) for item in confirmation_events),
            "targets": tuple(asdict(item) for item in targets),
            "trades": tuple(asdict(item) for item in trades),
            "exclusions": tuple(sorted(set(exclusions))),
        }
    )
    eligible_entries, exits = _ledger_trade_counts(
        base_ledger,
        formal_sessions=formal_sessions,
        trades=trades,
    )
    return HistoricalV2PolicyResult(
        policy_id=policy_id,
        policy_hash=policy_hash,
        targets=targets,
        ledger=base_ledger,
        stress_ledger=stress_ledger,
        trades=tuple(sorted(trades, key=lambda item: (item.confirmation_date, item.asset_code))),
        candidate_count=len(candidate_events),
        confirmation_count=len(confirmation_events),
        entry_count=eligible_entries,
        exit_count=exits,
        exclusions=tuple(sorted(set(exclusions))),
        input_hash=input_hash,
    )


__all__ = [
    "HISTORICAL_V2_EXECUTION_MODEL",
    "HISTORICAL_V2_ALLOCATION_HASH",
    "HISTORICAL_V2_ALLOCATION_VERSION",
    "HISTORICAL_V2_EXPERIMENT_ID",
    "HISTORICAL_V2_FORMAL_PIT_CREDIT",
    "HISTORICAL_V2_SCHEMA_VERSION",
    "HISTORICAL_V2_TARGET_WEIGHT",
    "HISTORICAL_V2_TOP_N",
    "HistoricalV2Asset",
    "HistoricalV2ComparisonResult",
    "HistoricalV2ContractError",
    "HistoricalV2Dataset",
    "HistoricalV2ExitTrade",
    "HistoricalV2FlowEvent",
    "HistoricalV2PolicyResult",
    "historical_dataset_hash",
    "run_historical_v2_exit_comparison",
]
