"""Frozen, low-cardinality ranking candidates for Strategy Lab replay."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any

from app.services.tracked_positions.lifecycle import stable_contract_hash

from .etf_ranking_stage_b import StageBRankingEvent

CANDIDATE_DAILY_CORE_TOP10 = "daily_core_top10"
CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS = "daily_core_top10_hysteresis"
CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME = (
    "daily_core_top10_hysteresis_regime"
)
MAX_FROZEN_RANKING_CANDIDATES = 3
REGIME_LIQUIDITY_GATE_CONTRACT_HASH = stable_contract_hash(
    {
        "contract_id": "fundscope_regime_liquidity_gate_v1",
        "market_regimes": ("risk_on", "neutral", "defensive", "cash_wait"),
        "cash_wait_action": "exclude_all",
        "liquidity_fact": "decision_eligible_boolean_required",
        "missing_fact": "exclude",
    }
)
RANKING_FEE_BPS_PER_SIDE = 5
RANKING_SLIPPAGE_BPS_PER_SIDE = 5
RANKING_COST_CONTRACT_HASH = stable_contract_hash(
    {
        "contract_id": "ranking_round_trip_cost_v1",
        "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
        "slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        "application": "multiplicative_each_side",
    }
)


class RankingCandidateContractError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenRankingCandidate:
    candidate_id: str
    top_n: int
    retention_buffer_rank: int | None
    max_replacements_per_session: int
    minimum_hold_sessions: int
    regime_liquidity_gate_contract_hash: str | None
    manifest_hash: str


@dataclass(frozen=True)
class FrozenRankingCandidateRegistry:
    candidates: tuple[FrozenRankingCandidate, ...]
    registry_hash: str

    @property
    def by_id(self) -> dict[str, FrozenRankingCandidate]:
        return {item.candidate_id: item for item in self.candidates}


@dataclass(frozen=True)
class RankingRegimeLiquidityGateFact:
    replay_date: date
    asset_code: str
    market_regime: str
    liquidity_decision_eligible: bool
    gate_contract_hash: str

    def __post_init__(self) -> None:
        if not self.asset_code.strip():
            raise RankingCandidateContractError("gate fact asset code is required")
        if self.market_regime not in {"risk_on", "neutral", "defensive", "cash_wait"}:
            raise RankingCandidateContractError("gate fact market regime is invalid")
        if not isinstance(self.liquidity_decision_eligible, bool):
            raise RankingCandidateContractError(
                "gate liquidity eligibility must be boolean"
            )
        if self.gate_contract_hash != REGIME_LIQUIDITY_GATE_CONTRACT_HASH:
            raise RankingCandidateContractError("gate contract hash is incompatible")


@dataclass(frozen=True)
class RankingCandidateHolding:
    asset_code: str
    sessions_held: int


@dataclass(frozen=True)
class RankingCandidateState:
    candidate_id: str
    candidate_manifest_hash: str
    holdings: tuple[RankingCandidateHolding, ...]
    last_selected_asset_codes: tuple[str, ...]
    state_hash: str


@dataclass(frozen=True)
class RankingCandidateSelection:
    replay_run_key: str
    replay_date: date
    candidate_id: str
    candidate_manifest_hash: str
    candidate_registry_hash: str
    source_ranking_event_hash: str
    selected_asset_codes: tuple[str, ...]
    underlying_hysteresis_asset_codes: tuple[str, ...]
    entered_asset_codes: tuple[str, ...]
    exited_asset_codes: tuple[str, ...]
    retained_asset_codes: tuple[str, ...]
    gate_exclusions: tuple[tuple[str, str], ...]
    selection_hash: str


@dataclass(frozen=True)
class RankingCandidateBatch:
    replay_run_key: str
    replay_date: date
    candidate_registry_hash: str
    source_ranking_event_hash: str
    selections: tuple[RankingCandidateSelection, ...]
    states: tuple[RankingCandidateState, ...]
    batch_hash: str

    @property
    def by_id(self) -> dict[str, RankingCandidateSelection]:
        return {item.candidate_id: item for item in self.selections}

    @property
    def next_states(self) -> dict[str, RankingCandidateState]:
        return {item.candidate_id: item for item in self.states}


def _candidate_payload(candidate: FrozenRankingCandidate) -> dict[str, Any]:
    payload = asdict(candidate)
    payload.pop("manifest_hash")
    return payload


def _candidate(
    candidate_id: str,
    *,
    retention_buffer_rank: int | None,
    max_replacements_per_session: int,
    minimum_hold_sessions: int,
    regime_gate: bool,
) -> FrozenRankingCandidate:
    draft = FrozenRankingCandidate(
        candidate_id=candidate_id,
        top_n=10,
        retention_buffer_rank=retention_buffer_rank,
        max_replacements_per_session=max_replacements_per_session,
        minimum_hold_sessions=minimum_hold_sessions,
        regime_liquidity_gate_contract_hash=(
            REGIME_LIQUIDITY_GATE_CONTRACT_HASH if regime_gate else None
        ),
        manifest_hash="pending",
    )
    return replace(
        draft,
        manifest_hash=stable_contract_hash(_candidate_payload(draft)),
    )


FROZEN_RANKING_CANDIDATES = (
    _candidate(
        CANDIDATE_DAILY_CORE_TOP10,
        retention_buffer_rank=None,
        max_replacements_per_session=10,
        minimum_hold_sessions=1,
        regime_gate=False,
    ),
    _candidate(
        CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
        retention_buffer_rank=15,
        max_replacements_per_session=1,
        minimum_hold_sessions=3,
        regime_gate=False,
    ),
    _candidate(
        CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME,
        retention_buffer_rank=15,
        max_replacements_per_session=1,
        minimum_hold_sessions=3,
        regime_gate=True,
    ),
)
_FROZEN_BY_ID = {item.candidate_id: item for item in FROZEN_RANKING_CANDIDATES}
_FROZEN_ORDER = {
    item.candidate_id: index for index, item in enumerate(FROZEN_RANKING_CANDIDATES)
}


def freeze_ranking_candidate_registry(
    candidates: Iterable[FrozenRankingCandidate],
    *,
    action_policy_candidate_ids: Iterable[str] = (),
) -> FrozenRankingCandidateRegistry:
    values = tuple(candidates)
    if len(values) > MAX_FROZEN_RANKING_CANDIDATES:
        raise RankingCandidateContractError("ranking registry allows at most three candidates")
    if not values:
        raise RankingCandidateContractError("ranking registry cannot be empty")
    ids = tuple(item.candidate_id for item in values)
    if len(ids) != len(set(ids)):
        raise RankingCandidateContractError("ranking registry contains duplicate candidates")
    if tuple(action_policy_candidate_ids):
        raise RankingCandidateContractError(
            "ranking-by-action Cartesian searches are forbidden"
        )
    for value in values:
        expected = _FROZEN_BY_ID.get(value.candidate_id)
        if expected is None or value != expected:
            raise RankingCandidateContractError(
                "candidate does not match its frozen definition"
            )
    ordered = tuple(sorted(values, key=lambda item: _FROZEN_ORDER[item.candidate_id]))
    return FrozenRankingCandidateRegistry(
        candidates=ordered,
        registry_hash=stable_contract_hash(
            {
                "registry_id": "daily_reconstructable_ranking_candidates_v1",
                "candidates": tuple(
                    (item.candidate_id, item.manifest_hash) for item in ordered
                ),
                "max_candidates": MAX_FROZEN_RANKING_CANDIDATES,
                "joint_action_search": False,
            }
        ),
    )


def _event_payload(event: StageBRankingEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload.pop("event_hash")
    return payload


def _validate_ranking_event(event: StageBRankingEvent) -> None:
    if (
        event.ranking_source_kind != "research_replay"
        or event.score_contract_id != "daily_reconstructable_v1"
        or event.score_field != "research_score"
        or event.event_hash != stable_contract_hash(_event_payload(event))
    ):
        raise RankingCandidateContractError("ranking event contract or hash is invalid")
    item_codes = tuple(item.asset_code for item in event.ranked_items)
    item_ranks = tuple(item.rank for item in event.ranked_items)
    if (
        item_codes != event.all_scored
        or len(item_codes) != len(set(item_codes))
        or item_ranks != tuple(range(1, len(item_codes) + 1))
        or event.top10 != event.all_scored[:10]
    ):
        raise RankingCandidateContractError("ranking event global order is invalid")


def _state_payload(state: RankingCandidateState) -> dict[str, Any]:
    payload = asdict(state)
    payload.pop("state_hash")
    return payload


def _selection_payload(selection: RankingCandidateSelection) -> dict[str, Any]:
    payload = asdict(selection)
    payload.pop("selection_hash")
    return payload


def _validate_previous_state(
    state: RankingCandidateState,
    candidate: FrozenRankingCandidate,
) -> None:
    codes = tuple(item.asset_code for item in state.holdings)
    if (
        state.candidate_id != candidate.candidate_id
        or state.candidate_manifest_hash != candidate.manifest_hash
        or state.state_hash != stable_contract_hash(_state_payload(state))
        or len(codes) != len(set(codes))
        or any(item.sessions_held < 1 for item in state.holdings)
    ):
        raise RankingCandidateContractError("previous ranking candidate state is invalid")


def _baseline_underlying(
    event: StageBRankingEvent,
    candidate: FrozenRankingCandidate,
) -> tuple[str, ...]:
    return event.all_scored[: candidate.top_n]


def _hysteresis_underlying(
    event: StageBRankingEvent,
    candidate: FrozenRankingCandidate,
    previous: RankingCandidateState | None,
) -> tuple[str, ...]:
    if previous is None or not previous.holdings:
        return _baseline_underlying(event, candidate)
    rank_by_code = {
        item.asset_code: item.rank for item in event.ranked_items
    }
    available = [
        item.asset_code for item in previous.holdings if item.asset_code in rank_by_code
    ]
    held_by_code = {item.asset_code: item.sessions_held for item in previous.holdings}
    buffer_rank = candidate.retention_buffer_rank or candidate.top_n
    replaceable = sorted(
        (
            code
            for code in available
            if held_by_code[code] >= candidate.minimum_hold_sessions
            and rank_by_code[code] > buffer_rank
        ),
        key=lambda code: (rank_by_code[code], code),
        reverse=True,
    )
    newcomers = [
        code for code in event.all_scored[: candidate.top_n] if code not in available
    ]
    replacements = min(
        candidate.max_replacements_per_session,
        len(replaceable),
        len(newcomers),
    )
    selected = list(available)
    for index in range(replacements):
        selected.remove(replaceable[index])
        selected.append(newcomers[index])
    remaining_capacity = candidate.top_n - len(selected)
    if remaining_capacity > 0:
        already = set(selected)
        fills = [
            code
            for code in event.all_scored
            if code not in already
        ][: min(remaining_capacity, candidate.max_replacements_per_session)]
        selected.extend(fills)
    return tuple(sorted(selected, key=lambda code: (rank_by_code[code], code)))


def _gate_facts_by_code(
    facts: Iterable[RankingRegimeLiquidityGateFact],
    *,
    event: StageBRankingEvent,
) -> dict[str, RankingRegimeLiquidityGateFact]:
    output: dict[str, RankingRegimeLiquidityGateFact] = {}
    regimes: set[str] = set()
    for fact in facts:
        if fact.replay_date != event.replay_date:
            raise RankingCandidateContractError("gate fact date is outside ranking event")
        if fact.asset_code in output:
            raise RankingCandidateContractError("duplicate regime/liquidity gate fact")
        if fact.asset_code not in set(event.all_scored):
            raise RankingCandidateContractError("gate fact asset is outside ranking event")
        output[fact.asset_code] = fact
        regimes.add(fact.market_regime)
    if len(regimes) > 1:
        raise RankingCandidateContractError("gate facts contain mixed market regimes")
    return output


def _apply_gate(
    underlying: tuple[str, ...],
    facts: Mapping[str, RankingRegimeLiquidityGateFact],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    selected: list[str] = []
    exclusions: list[tuple[str, str]] = []
    for code in underlying:
        fact = facts.get(code)
        if fact is None:
            exclusions.append((code, "missing_regime_liquidity_gate_fact"))
        elif fact.market_regime == "cash_wait":
            exclusions.append((code, "market_regime_cash_wait"))
        elif not fact.liquidity_decision_eligible:
            exclusions.append((code, "liquidity_not_decision_eligible"))
        else:
            selected.append(code)
    return tuple(selected), tuple(sorted(exclusions))


def _next_state(
    *,
    candidate: FrozenRankingCandidate,
    underlying: tuple[str, ...],
    selected: tuple[str, ...],
    previous: RankingCandidateState | None,
) -> RankingCandidateState:
    previous_holds = (
        {item.asset_code: item.sessions_held for item in previous.holdings}
        if previous is not None
        else {}
    )
    draft = RankingCandidateState(
        candidate_id=candidate.candidate_id,
        candidate_manifest_hash=candidate.manifest_hash,
        holdings=tuple(
            RankingCandidateHolding(
                asset_code=code,
                sessions_held=previous_holds.get(code, 0) + 1,
            )
            for code in underlying
        ),
        last_selected_asset_codes=selected,
        state_hash="pending",
    )
    return replace(draft, state_hash=stable_contract_hash(_state_payload(draft)))


def evaluate_ranking_candidates(
    *,
    ranking_event: StageBRankingEvent,
    registry: FrozenRankingCandidateRegistry,
    previous_states: Mapping[str, RankingCandidateState],
    gate_facts: Iterable[RankingRegimeLiquidityGateFact],
) -> RankingCandidateBatch:
    """Evaluate one complete ranking date without reading any forward outcome."""

    _validate_ranking_event(ranking_event)
    if set(previous_states) - set(registry.by_id):
        raise RankingCandidateContractError("previous state is outside candidate registry")
    facts = _gate_facts_by_code(gate_facts, event=ranking_event)
    selections: list[RankingCandidateSelection] = []
    states: list[RankingCandidateState] = []
    for candidate in registry.candidates:
        previous = previous_states.get(candidate.candidate_id)
        if previous is not None:
            _validate_previous_state(previous, candidate)
        if candidate.candidate_id == CANDIDATE_DAILY_CORE_TOP10:
            underlying = _baseline_underlying(ranking_event, candidate)
        else:
            underlying = _hysteresis_underlying(
                ranking_event,
                candidate,
                previous,
            )
        if candidate.regime_liquidity_gate_contract_hash is not None:
            selected, exclusions = _apply_gate(underlying, facts)
        else:
            selected, exclusions = underlying, ()
        previous_selected = (
            previous.last_selected_asset_codes if previous is not None else ()
        )
        selected_set = set(selected)
        previous_set = set(previous_selected)
        entered = tuple(code for code in selected if code not in previous_set)
        exited = tuple(code for code in previous_selected if code not in selected_set)
        retained = tuple(code for code in selected if code in previous_set)
        selection_draft = RankingCandidateSelection(
            replay_run_key=ranking_event.replay_run_key,
            replay_date=ranking_event.replay_date,
            candidate_id=candidate.candidate_id,
            candidate_manifest_hash=candidate.manifest_hash,
            candidate_registry_hash=registry.registry_hash,
            source_ranking_event_hash=ranking_event.event_hash,
            selected_asset_codes=selected,
            underlying_hysteresis_asset_codes=underlying,
            entered_asset_codes=entered,
            exited_asset_codes=exited,
            retained_asset_codes=retained,
            gate_exclusions=exclusions,
            selection_hash="pending",
        )
        selections.append(
            replace(
                selection_draft,
                selection_hash=stable_contract_hash(
                    _selection_payload(selection_draft)
                ),
            )
        )
        states.append(
            _next_state(
                candidate=candidate,
                underlying=underlying,
                selected=selected,
                previous=previous,
            )
        )
    batch_payload = {
        "replay_run_key": ranking_event.replay_run_key,
        "replay_date": ranking_event.replay_date,
        "candidate_registry_hash": registry.registry_hash,
        "source_ranking_event_hash": ranking_event.event_hash,
        "selections": tuple(item.selection_hash for item in selections),
        "states": tuple(item.state_hash for item in states),
    }
    return RankingCandidateBatch(
        replay_run_key=ranking_event.replay_run_key,
        replay_date=ranking_event.replay_date,
        candidate_registry_hash=registry.registry_hash,
        source_ranking_event_hash=ranking_event.event_hash,
        selections=tuple(selections),
        states=tuple(states),
        batch_hash=stable_contract_hash(batch_payload),
    )
