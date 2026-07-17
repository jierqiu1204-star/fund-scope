from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date

import pytest

from app.services.strategy_lab.etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10,
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME,
    FROZEN_RANKING_CANDIDATES,
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
    RankingCandidateContractError,
    RankingCandidateState,
    RankingRegimeLiquidityGateFact,
    evaluate_ranking_candidates,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    StageBRankedItem,
    StageBRankingEvent,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _ranking_event(
    session_date: date,
    ordered_codes: tuple[str, ...],
) -> StageBRankingEvent:
    items = tuple(
        StageBRankedItem(
            asset_code=code,
            rank=index,
            research_score=100.0 - index,
            feature_hash=_hash(f"feature:{session_date}:{code}"),
        )
        for index, code in enumerate(ordered_codes, start=1)
    )
    draft = StageBRankingEvent(
        replay_run_key="ranking-candidate-fixture",
        replay_date=session_date,
        ranking_source_kind="research_replay",
        score_contract_id="daily_reconstructable_v1",
        score_field="research_score",
        score_manifest_hash=_hash("score-manifest"),
        stage_b_schema_version="etf-ranking-stage-b-v1",
        date_manifest_hash=_hash(f"date-manifest:{session_date}"),
        source_date_manifest_hash=_hash(f"source-manifest:{session_date}"),
        universe_hash=_hash(f"universe:{session_date}"),
        input_hash=_hash(f"input:{session_date}"),
        feature_manifest_hash=_hash(f"features:{session_date}"),
        ranked_items=items,
        all_scored=ordered_codes,
        top5=ordered_codes[:5],
        top10=ordered_codes[:10],
        top20=ordered_codes[:20],
        event_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("event_hash")
    return replace(draft, event_hash=stable_contract_hash(payload))


def _codes(prefix: str = "A", count: int = 20) -> tuple[str, ...]:
    return tuple(f"{prefix}{index:02d}" for index in range(1, count + 1))


def test_ranking_candidate_registry_is_frozen_low_cardinality_and_hashed() -> None:
    first = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    second = freeze_ranking_candidate_registry(tuple(reversed(FROZEN_RANKING_CANDIDATES)))

    assert tuple(item.candidate_id for item in first.candidates) == (
        CANDIDATE_DAILY_CORE_TOP10,
        CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
        CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME,
    )
    assert len(first.candidates) == 3
    assert first.registry_hash == second.registry_hash
    assert all(item.manifest_hash for item in first.candidates)
    hysteresis = first.by_id[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS]
    assert hysteresis.top_n == 10
    assert hysteresis.retention_buffer_rank == 15
    assert hysteresis.max_replacements_per_session == 1
    assert hysteresis.minimum_hold_sessions == 3


def test_registry_rejects_extra_dynamic_and_joint_action_searches() -> None:
    with pytest.raises(RankingCandidateContractError, match="at most three"):
        freeze_ranking_candidate_registry(
            (*FROZEN_RANKING_CANDIDATES, FROZEN_RANKING_CANDIDATES[0])
        )
    with pytest.raises(RankingCandidateContractError, match="duplicate"):
        freeze_ranking_candidate_registry(
            (FROZEN_RANKING_CANDIDATES[0], FROZEN_RANKING_CANDIDATES[0])
        )
    dynamic = replace(FROZEN_RANKING_CANDIDATES[1], retention_buffer_rank=16)
    with pytest.raises(RankingCandidateContractError, match="frozen definition"):
        freeze_ranking_candidate_registry((dynamic,))
    with pytest.raises(RankingCandidateContractError, match="Cartesian"):
        freeze_ranking_candidate_registry(
            FROZEN_RANKING_CANDIDATES,
            action_policy_candidate_ids=("action-a", "action-b"),
        )


def test_daily_core_top10_selects_exact_global_top10() -> None:
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES[:1])
    event = _ranking_event(date(2026, 4, 1), _codes())

    batch = evaluate_ranking_candidates(
        ranking_event=event,
        registry=registry,
        previous_states={},
        gate_facts=(),
    )
    selection = batch.by_id[CANDIDATE_DAILY_CORE_TOP10]

    assert selection.selected_asset_codes == event.all_scored[:10]
    assert selection.entered_asset_codes == event.all_scored[:10]
    assert selection.exited_asset_codes == ()
    assert selection.source_ranking_event_hash == event.event_hash
    assert selection.selection_hash


def test_hysteresis_enforces_buffer_minimum_hold_and_one_replacement() -> None:
    registry = freeze_ranking_candidate_registry(
        (FROZEN_RANKING_CANDIDATES[1],)
    )
    initial_codes = _codes("A")
    state: dict[str, RankingCandidateState] = {}

    first = evaluate_ranking_candidates(
        ranking_event=_ranking_event(date(2026, 4, 1), initial_codes),
        registry=registry,
        previous_states=state,
        gate_facts=(),
    )
    state = dict(first.next_states)
    # A09/A10 fall outside the retention buffer, while B01/B02 enter Top10.
    changed = (
        "B01",
        "B02",
        *initial_codes[:8],
        *initial_codes[10:18],
        "A09",
        "A10",
    )
    for session_date in (date(2026, 4, 2), date(2026, 4, 3)):
        held = evaluate_ranking_candidates(
            ranking_event=_ranking_event(session_date, changed),
            registry=registry,
            previous_states=state,
            gate_facts=(),
        )
        selection = held.by_id[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS]
        assert selection.entered_asset_codes == ()
        assert selection.exited_asset_codes == ()
        assert "A09" in selection.selected_asset_codes
        assert "A10" in selection.selected_asset_codes
        state = dict(held.next_states)

    replaced = evaluate_ranking_candidates(
        ranking_event=_ranking_event(date(2026, 4, 4), changed),
        registry=registry,
        previous_states=state,
        gate_facts=(),
    )
    selection = replaced.by_id[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS]
    assert selection.entered_asset_codes == ("B01",)
    assert selection.exited_asset_codes == ("A10",)
    assert "A09" in selection.selected_asset_codes
    assert len(selection.entered_asset_codes) <= 1
    assert len(selection.exited_asset_codes) <= 1


def _gate_facts(
    event: StageBRankingEvent,
    *,
    market_regime: str,
    ineligible: tuple[str, ...] = (),
) -> tuple[RankingRegimeLiquidityGateFact, ...]:
    return tuple(
        RankingRegimeLiquidityGateFact(
            replay_date=event.replay_date,
            asset_code=code,
            market_regime=market_regime,
            liquidity_decision_eligible=code not in ineligible,
            gate_contract_hash=REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
        )
        for code in event.all_scored[:10]
    )


def test_regime_candidate_uses_typed_cash_wait_and_liquidity_gate() -> None:
    registry = freeze_ranking_candidate_registry(
        (FROZEN_RANKING_CANDIDATES[2],)
    )
    first_event = _ranking_event(date(2026, 4, 1), _codes())
    first = evaluate_ranking_candidates(
        ranking_event=first_event,
        registry=registry,
        previous_states={},
        gate_facts=_gate_facts(
            first_event,
            market_regime="risk_on",
            ineligible=("A03",),
        ),
    )
    selection = first.by_id[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME]
    assert "A03" not in selection.selected_asset_codes
    assert ("A03", "liquidity_not_decision_eligible") in selection.gate_exclusions
    assert len(selection.selected_asset_codes) == 9

    cash_event = _ranking_event(date(2026, 4, 2), _codes())
    cash = evaluate_ranking_candidates(
        ranking_event=cash_event,
        registry=registry,
        previous_states=first.next_states,
        gate_facts=_gate_facts(cash_event, market_regime="cash_wait"),
    )
    cash_selection = cash.by_id[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME]
    assert cash_selection.selected_asset_codes == ()
    assert all(reason == "market_regime_cash_wait" for _, reason in cash_selection.gate_exclusions)


def test_regime_candidate_fails_closed_when_gate_fact_is_missing() -> None:
    registry = freeze_ranking_candidate_registry(
        (FROZEN_RANKING_CANDIDATES[2],)
    )
    event = _ranking_event(date(2026, 4, 1), _codes())
    facts = _gate_facts(event, market_regime="risk_on")[:-1]

    result = evaluate_ranking_candidates(
        ranking_event=event,
        registry=registry,
        previous_states={},
        gate_facts=facts,
    )
    selection = result.by_id[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME]

    assert "A10" not in selection.selected_asset_codes
    assert ("A10", "missing_regime_liquidity_gate_fact") in selection.gate_exclusions


def test_candidate_outputs_and_state_hashes_are_deterministic() -> None:
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    event = _ranking_event(date(2026, 4, 1), _codes())
    facts = _gate_facts(event, market_regime="neutral")

    first = evaluate_ranking_candidates(
        ranking_event=event,
        registry=registry,
        previous_states={},
        gate_facts=facts,
    )
    second = evaluate_ranking_candidates(
        ranking_event=event,
        registry=registry,
        previous_states={},
        gate_facts=tuple(reversed(facts)),
    )

    assert first == second
    assert first.batch_hash
    assert all(state.state_hash for state in first.next_states.values())
