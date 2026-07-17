from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from app.services.strategy_lab.etf_ranking_walk_forward import (
    RankingHoldoutConsumedError,
    RankingWalkForwardCandidateEvidence,
    RankingWalkForwardContractError,
    build_expanding_ranking_walk_forward_plan,
    consume_final_ranking_holdout,
    empty_ranking_holdout_ledger,
    freeze_ranking_walk_forward_contract,
    freeze_walk_forward_candidate_selection,
)

from app.services.etf_research_evidence import RankingSourceKind
from app.services.strategy_lab.etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10,
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME,
    FROZEN_RANKING_CANDIDATES,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_validation import (
    RankingValidationSourceEvent,
    freeze_ranking_validation_source_cohort,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _without(value: object, field: str) -> dict[str, object]:
    payload = dict(value.__dict__)
    payload.pop(field)
    return payload


def _source_event(signal_date: date) -> RankingValidationSourceEvent:
    draft = RankingValidationSourceEvent(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        signal_date=signal_date,
        source_signal_run_id=None,
        source_replay_run_key="walk-forward-replay",
        source_replay_contract_hash=_hash("walk-forward-replay-contract"),
        source_event_hash=_hash(f"walk-forward-event:{signal_date}"),
        ranking_contract_hash=_hash("daily-reconstructable"),
        universe_snapshot_hash=_hash(f"universe:{signal_date}"),
        input_snapshot_hash=_hash(f"input:{signal_date}"),
        score_version="daily_reconstructable_v1",
        score_field="research_score",
        price_basis="total_return_adjusted",
        publication_state=None,
        scope_kind="research_replay",
        availability_cutoff=datetime.combine(
            signal_date,
            datetime.min.time(),
            UTC,
        ),
        immutable_hash="pending",
    )
    return replace(
        draft,
        immutable_hash=stable_contract_hash(_without(draft, "immutable_hash")),
    )


def _fixture():
    start = date(2026, 1, 1)
    sessions = tuple(start + timedelta(days=index) for index in range(80))
    cohort = freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        events=tuple(_source_event(session) for session in sessions),
    )
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    contract = freeze_ranking_walk_forward_contract(
        source_cohort=cohort,
        candidate_registry=registry,
        trading_sessions=sessions,
        validation_starts=(sessions[25], sessions[45]),
        final_holdout_start=sessions[65],
        final_holdout_input_hash=_hash("final-holdout-input"),
        minimum_independent_dates_per_fold=3,
        minimum_coverage_ratio=0.8,
    )
    plan = build_expanding_ranking_walk_forward_plan(
        contract=contract,
        source_cohort=cohort,
        trading_sessions=sessions,
    )
    return sessions, cohort, registry, contract, plan


def _evidence(
    *,
    plan,
    registry,
    fold,
    candidate_id: str,
    lower_bound: float,
    sample_gate_passed: bool = True,
    drawdown_gate_passed: bool = True,
    independent_dates: tuple[date, ...] | None = None,
    endpoint_role: str = "primary",
) -> RankingWalkForwardCandidateEvidence:
    candidate = registry.by_id[candidate_id]
    draft = RankingWalkForwardCandidateEvidence(
        walk_forward_plan_hash=plan.plan_hash,
        fold_id=fold.fold_id,
        candidate_registry_hash=registry.registry_hash,
        candidate_id=candidate_id,
        candidate_manifest_hash=candidate.manifest_hash,
        endpoint_role=endpoint_role,
        top_n=10,
        horizon_sessions=5,
        independent_dates=independent_dates or fold.validation_dates[:3],
        coverage_ratio=1.0,
        mean_paired_net_excess=lower_bound + 0.002,
        bootstrap_lower_bound=lower_bound,
        sample_gate_passed=sample_gate_passed,
        maximum_drawdown_gate_passed=drawdown_gate_passed,
        endpoint_result_hash=_hash(
            f"endpoint:{fold.fold_id}:{candidate_id}:{lower_bound}"
        ),
        evidence_hash="pending",
    )
    return replace(
        draft,
        evidence_hash=stable_contract_hash(_without(draft, "evidence_hash")),
    )


def _all_evidence(plan, registry):
    lower_bounds = {
        CANDIDATE_DAILY_CORE_TOP10: 0.005,
        CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS: 0.008,
        CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME: 0.007,
    }
    return tuple(
        _evidence(
            plan=plan,
            registry=registry,
            fold=fold,
            candidate_id=candidate.candidate_id,
            lower_bound=lower_bounds[candidate.candidate_id],
        )
        for fold in plan.splits
        for candidate in registry.candidates
    )


def _rehash_evidence(
    evidence: RankingWalkForwardCandidateEvidence,
    **changes: object,
) -> RankingWalkForwardCandidateEvidence:
    changed = replace(evidence, **changes, evidence_hash="pending")
    return replace(
        changed,
        evidence_hash=stable_contract_hash(_without(changed, "evidence_hash")),
    )


def test_expanding_plan_uses_exact_ten_session_purge_and_embargo() -> None:
    sessions, _cohort, _registry, contract, plan = _fixture()
    first, second = plan.splits

    assert contract.purge_sessions == contract.embargo_sessions == 10
    assert first.purged_trading_sessions == sessions[15:25]
    assert first.embargoed_trading_sessions == sessions[25:35]
    assert first.validation_dates == sessions[35:45]
    assert second.purged_trading_sessions == sessions[35:45]
    assert second.embargoed_trading_sessions == sessions[45:55]
    assert second.validation_dates == sessions[55:65]
    assert set(first.training_dates) < set(second.training_dates)
    assert not set(first.training_dates) & set(first.validation_dates)
    assert plan.final_holdout_dates == sessions[75:]
    assert plan.plan_hash

    with pytest.raises(RankingWalkForwardContractError, match="frozen to 10"):
        replace(contract, purge_sessions=9)


def test_selection_uses_every_candidate_fold_and_freezes_before_holdout() -> None:
    _sessions, _cohort, registry, contract, plan = _fixture()
    evidence = _all_evidence(plan, registry)

    first = freeze_walk_forward_candidate_selection(
        contract=contract,
        plan=plan,
        candidate_registry=registry,
        evidence=tuple(reversed(evidence)),
    )
    second = freeze_walk_forward_candidate_selection(
        contract=contract,
        plan=plan,
        candidate_registry=registry,
        evidence=evidence,
    )

    assert first == second
    assert first.selected_candidate_id == CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS
    assert first.selection_status == "frozen_for_final_holdout"
    assert first.sample_gate_passed is True
    assert first.holdout_ready is True
    assert first.frozen_before_holdout is True
    assert first.selection_rule == "highest_mean_walk_forward_lower_bound"


def test_selection_rejects_exploratory_partial_or_holdout_evidence() -> None:
    _sessions, _cohort, registry, contract, plan = _fixture()
    evidence = list(_all_evidence(plan, registry))
    exploratory = _rehash_evidence(evidence[0], endpoint_role="exploratory")
    with pytest.raises(RankingWalkForwardContractError, match="primary"):
        freeze_walk_forward_candidate_selection(
            contract=contract,
            plan=plan,
            candidate_registry=registry,
            evidence=(exploratory, *evidence[1:]),
        )
    with pytest.raises(RankingWalkForwardContractError, match="complete candidate-by-fold"):
        freeze_walk_forward_candidate_selection(
            contract=contract,
            plan=plan,
            candidate_registry=registry,
            evidence=evidence[:-1],
        )
    holdout_leak = _rehash_evidence(
        evidence[0],
        independent_dates=(plan.final_holdout_dates[0],),
    )
    with pytest.raises(RankingWalkForwardContractError, match="validation scope"):
        freeze_walk_forward_candidate_selection(
            contract=contract,
            plan=plan,
            candidate_registry=registry,
            evidence=(holdout_leak, *evidence[1:]),
        )


def test_sample_insufficiency_blocks_selection_and_holdout() -> None:
    _sessions, _cohort, registry, contract, plan = _fixture()
    evidence = tuple(
        _rehash_evidence(item, sample_gate_passed=False)
        for item in _all_evidence(plan, registry)
    )
    selection = freeze_walk_forward_candidate_selection(
        contract=contract,
        plan=plan,
        candidate_registry=registry,
        evidence=evidence,
    )

    assert selection.selected_candidate_id is None
    assert selection.selection_status == "sample_insufficient"
    assert selection.holdout_ready is False
    with pytest.raises(RankingWalkForwardContractError, match="sample insufficient"):
        consume_final_ranking_holdout(
            ledger=empty_ranking_holdout_ledger(),
            contract=contract,
            plan=plan,
            selection=selection,
            final_holdout_input_hash=contract.final_holdout_input_hash,
            final_holdout_result_hash=_hash("blocked-result"),
            consumed_at=datetime(2026, 7, 15, tzinfo=UTC),
        )


def test_final_holdout_is_consumed_once_for_the_frozen_selection() -> None:
    _sessions, _cohort, registry, contract, plan = _fixture()
    selection = freeze_walk_forward_candidate_selection(
        contract=contract,
        plan=plan,
        candidate_registry=registry,
        evidence=_all_evidence(plan, registry),
    )
    ledger, receipt = consume_final_ranking_holdout(
        ledger=empty_ranking_holdout_ledger(),
        contract=contract,
        plan=plan,
        selection=selection,
        final_holdout_input_hash=contract.final_holdout_input_hash,
        final_holdout_result_hash=_hash("final-result-1"),
        consumed_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    assert ledger.receipts == (receipt,)
    assert receipt.selected_candidate_id == selection.selected_candidate_id
    assert receipt.final_holdout_input_hash == contract.final_holdout_input_hash
    assert ledger.ledger_hash
    with pytest.raises(RankingHoldoutConsumedError, match="already consumed"):
        consume_final_ranking_holdout(
            ledger=ledger,
            contract=contract,
            plan=plan,
            selection=selection,
            final_holdout_input_hash=contract.final_holdout_input_hash,
            final_holdout_result_hash=_hash("attempted-retune-result"),
            consumed_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )
    with pytest.raises(RankingWalkForwardContractError, match="input hash"):
        consume_final_ranking_holdout(
            ledger=empty_ranking_holdout_ledger(),
            contract=contract,
            plan=plan,
            selection=selection,
            final_holdout_input_hash=_hash("wrong-holdout-input"),
            final_holdout_result_hash=_hash("wrong-scope-result"),
            consumed_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        )
