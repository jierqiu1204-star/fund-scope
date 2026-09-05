from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.services.strategy_lab.etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10,
    RANKING_COST_CONTRACT_HASH,
    RankingCandidateSelection,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    FORWARD_HORIZONS,
    PURE_MOMENTUM_CONTROL_CONTRACT_HASH,
    RANKING_PORTFOLIO_BASE_COST_POLICY,
    RANKING_PORTFOLIO_STRESS_COST_POLICY,
    ForwardAdjustedClose,
    ForwardExecutionCostEvidence,
    ForwardOutcomeContractError,
    RankingPortfolioTarget,
    calculate_continuous_ranking_portfolio,
    calculate_ranking_forward_outcomes,
    freeze_ranking_portfolio_target,
    select_positive_momentum_top10,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _sessions(count: int = 14) -> tuple[date, ...]:
    start = date(2026, 4, 1)
    return tuple(start + timedelta(days=index) for index in range(count))


def _selection(
    signal_date: date,
    codes: tuple[str, ...] = ("510001",),
) -> RankingCandidateSelection:
    draft = RankingCandidateSelection(
        replay_run_key="forward-outcome-fixture",
        replay_date=signal_date,
        candidate_id=CANDIDATE_DAILY_CORE_TOP10,
        candidate_manifest_hash=_hash("candidate-manifest"),
        candidate_registry_hash=_hash("candidate-registry"),
        source_ranking_event_hash=_hash(f"ranking:{signal_date}"),
        selected_asset_codes=codes,
        underlying_hysteresis_asset_codes=codes,
        entered_asset_codes=codes,
        exited_asset_codes=(),
        retained_asset_codes=(),
        gate_exclusions=(),
        selection_hash="pending",
    )
    return replace(
        draft,
        selection_hash=stable_contract_hash(
            {
                key: value
                for key, value in draft.__dict__.items()
                if key != "selection_hash"
            }
        ),
    )


def _close(code: str, session_date: date, value: float) -> ForwardAdjustedClose:
    return ForwardAdjustedClose(
        asset_code=code,
        session_date=session_date,
        adjusted_close=value,
        price_basis="total_return_adjusted",
        decision_eligible=True,
        provider="eastmoney",
        adjustment_version="eastmoney.push2his.kline.hfq_v1",
        source_hash=_hash(f"close:{code}:{session_date}:{value}"),
    )


def _cost_evidence(
    code: str,
    session_date: date,
    *,
    available_at: datetime | None = None,
) -> ForwardExecutionCostEvidence:
    quote_time = datetime.combine(session_date, datetime.min.time()).replace(hour=10)
    cutoff = quote_time.replace(minute=2)
    return ForwardExecutionCostEvidence(
        asset_code=code,
        execution_session=session_date,
        provider="eastmoney",
        source_hash=_hash(f"cost:{code}:{session_date}"),
        quote_time=quote_time,
        available_at=available_at or quote_time.replace(minute=1),
        execution_cutoff=cutoff,
        bid=99.9,
        ask=100.1,
        liquidity_notional=50_000_000.0,
        liquidity_cost_bps_per_side=2.0,
    )


def test_forward_outcomes_use_t_plus_one_close_and_full_session_exits() -> None:
    sessions = _sessions()
    closes = tuple(
        _close("510001", session, 100.0 + index)
        for index, session in enumerate(sessions)
    )

    bundle = calculate_ranking_forward_outcomes(
        selection=_selection(sessions[0]),
        trading_sessions=sessions,
        adjusted_closes=closes,
    )

    assert FORWARD_HORIZONS == (1, 3, 5, 10)
    by_horizon = {item.horizon_sessions: item for item in bundle.outcomes}
    assert by_horizon[1].entry_session == sessions[1]
    assert by_horizon[1].exit_session == sessions[2]
    assert by_horizon[3].exit_session == sessions[4]
    assert by_horizon[5].exit_session == sessions[6]
    assert by_horizon[10].exit_session == sessions[11]
    assert by_horizon[1].entry_adjusted_close == 101.0
    assert all(item.status == "completed" for item in bundle.outcomes)
    assert bundle.execution_model == "t_plus_one_adjusted_close_v1"
    assert bundle.bundle_hash


def test_forward_outcomes_apply_each_side_fee_and_slippage_multiplicatively() -> None:
    sessions = _sessions()
    closes = tuple(_close("510001", session, 100.0) for session in sessions)

    bundle = calculate_ranking_forward_outcomes(
        selection=_selection(sessions[0]),
        trading_sessions=sessions,
        adjusted_closes=closes,
        horizons=(1,),
    )
    outcome = bundle.outcomes[0]
    expected = (0.9995 * 0.9995) / (1.0005 * 1.0005) - 1.0

    assert outcome.gross_return == pytest.approx(0.0)
    assert outcome.net_return == pytest.approx(expected)
    assert outcome.fee_bps_per_side == 5
    assert outcome.slippage_bps_per_side == 5
    assert outcome.round_trip_cost_bps == 20
    assert outcome.cost_contract_hash == RANKING_COST_CONTRACT_HASH
    assert outcome.cost_provenance == "frozen_conservative_fallback"
    assert outcome.cost_unavailable_reasons == (
        "entry_cost_evidence_missing",
        "exit_cost_evidence_missing",
    )


def test_forward_outcomes_use_cutoff_valid_factual_spread_and_liquidity_costs() -> None:
    sessions = _sessions()
    closes = tuple(_close("510001", session, 100.0) for session in sessions)
    evidence = (
        _cost_evidence("510001", sessions[1]),
        _cost_evidence("510001", sessions[2]),
    )

    bundle = calculate_ranking_forward_outcomes(
        selection=_selection(sessions[0]),
        trading_sessions=sessions,
        adjusted_closes=closes,
        execution_cost_evidence=evidence,
        horizons=(1,),
    )
    outcome = bundle.outcomes[0]
    expected = (1.0 - 0.0012) * (1.0 - 0.0005) / (
        (1.0 + 0.0012) * (1.0 + 0.0005)
    ) - 1.0

    assert outcome.cost_provenance == "factual_cutoff_valid"
    assert outcome.entry_factual_spread_bps == pytest.approx(20.0)
    assert outcome.entry_liquidity_cost_bps_per_side == 2.0
    assert outcome.entry_slippage_bps_per_side == pytest.approx(12.0)
    assert outcome.exit_slippage_bps_per_side == pytest.approx(12.0)
    assert outcome.round_trip_cost_bps == pytest.approx(34.0)
    assert outcome.net_return == pytest.approx(expected)
    assert len(outcome.cost_input_hashes) == 2
    assert len(outcome.cost_source_hashes) == 2
    assert outcome.cost_evidence == evidence
    assert bundle.cost_provenance_counts == (("factual_cutoff_valid", 1),)
    assert bundle.cost_unavailable_reason_counts == ()


def test_late_or_missing_cost_evidence_uses_explicit_frozen_fallback() -> None:
    sessions = _sessions()
    closes = tuple(_close("510001", session, 100.0) for session in sessions)
    late_evidence = _cost_evidence(
        "510001",
        sessions[1],
        available_at=datetime.combine(sessions[1], datetime.min.time()).replace(
            hour=10,
            minute=3,
        ),
    )

    bundle = calculate_ranking_forward_outcomes(
        selection=_selection(sessions[0]),
        trading_sessions=sessions,
        adjusted_closes=closes,
        execution_cost_evidence=(late_evidence,),
        horizons=(1,),
    )
    outcome = bundle.outcomes[0]

    assert outcome.cost_provenance == "frozen_conservative_fallback"
    assert outcome.entry_factual_spread_bps is None
    assert outcome.entry_slippage_bps_per_side == 5.0
    assert "entry_available_after_execution_cutoff" in outcome.cost_unavailable_reasons
    assert "exit_cost_evidence_missing" in outcome.cost_unavailable_reasons
    assert outcome.cost_source_hashes == (late_evidence.source_hash,)
    assert outcome.cost_evidence == (late_evidence,)
    assert bundle.execution_cost_evidence_hash


def test_missing_entry_or_exit_is_excluded_without_signal_close_substitution() -> None:
    sessions = _sessions(4)
    closes = (
        # Signal-date closes exist and must never substitute either missing leg.
        _close("510001", sessions[0], 90.0),
        _close("510001", sessions[2], 110.0),
        _close("510002", sessions[0], 80.0),
        _close("510002", sessions[1], 100.0),
    )

    bundle = calculate_ranking_forward_outcomes(
        selection=_selection(sessions[0], ("510001", "510002")),
        trading_sessions=sessions,
        adjusted_closes=closes,
        horizons=(1,),
    )
    by_code = {item.asset_code: item for item in bundle.outcomes}

    assert by_code["510001"].status == "excluded"
    assert by_code["510001"].exclusion_reason == "missing_adjusted_entry_or_exit"
    assert by_code["510001"].missing_leg == "entry"
    assert by_code["510001"].entry_adjusted_close is None
    assert by_code["510002"].status == "excluded"
    assert by_code["510002"].exclusion_reason == "missing_adjusted_entry_or_exit"
    assert by_code["510002"].missing_leg == "exit"
    assert by_code["510002"].exit_adjusted_close is None
    assert all(item.net_return is None for item in bundle.outcomes)
    assert bundle.requested_outcome_count == 2
    assert bundle.completed_outcome_count == 0
    assert bundle.pending_outcome_count == 0
    assert bundle.excluded_outcome_count == 2
    assert bundle.status_counts_by_horizon[0].excluded_count == 2


def test_incomplete_future_window_is_pending_not_fabricated() -> None:
    sessions = _sessions(5)
    closes = tuple(_close("510001", session, 100.0) for session in sessions)

    bundle = calculate_ranking_forward_outcomes(
        selection=_selection(sessions[0]),
        trading_sessions=sessions,
        adjusted_closes=closes,
        horizons=(5,),
    )
    outcome = bundle.outcomes[0]

    assert outcome.status == "pending"
    assert outcome.exclusion_reason == "future_window_pending"
    assert outcome.exit_session is None
    assert outcome.net_return is None
    assert bundle.requested_outcome_count == 1
    assert bundle.pending_outcome_count == 1
    assert bundle.excluded_outcome_count == 0


def test_forward_inputs_reject_duplicates_raw_prices_and_dynamic_horizons() -> None:
    sessions = _sessions()
    row = _close("510001", sessions[1], 100.0)
    with pytest.raises(ForwardOutcomeContractError, match="duplicate"):
        calculate_ranking_forward_outcomes(
            selection=_selection(sessions[0]),
            trading_sessions=sessions,
            adjusted_closes=(row, row),
            horizons=(1,),
        )
    with pytest.raises(ForwardOutcomeContractError, match="total-return-adjusted"):
        calculate_ranking_forward_outcomes(
            selection=_selection(sessions[0]),
            trading_sessions=sessions,
            adjusted_closes=(replace(row, price_basis="raw"),),
            horizons=(1,),
        )
    with pytest.raises(ForwardOutcomeContractError, match="frozen"):
        calculate_ranking_forward_outcomes(
            selection=_selection(sessions[0]),
            trading_sessions=sessions,
            adjusted_closes=(row,),
            horizons=(2,),
        )
    with pytest.raises(ForwardOutcomeContractError, match="duplicate execution"):
        calculate_ranking_forward_outcomes(
            selection=_selection(sessions[0]),
            trading_sessions=sessions,
            adjusted_closes=(row,),
            execution_cost_evidence=(
                _cost_evidence("510001", sessions[1]),
                _cost_evidence("510001", sessions[1]),
            ),
            horizons=(1,),
        )


def test_forward_outcome_hashes_are_input_order_invariant() -> None:
    sessions = _sessions()
    closes = tuple(
        _close(code, session, 100.0 + index)
        for code in ("510001", "510002")
        for index, session in enumerate(sessions)
    )
    selection = _selection(sessions[0], ("510001", "510002"))

    first = calculate_ranking_forward_outcomes(
        selection=selection,
        trading_sessions=sessions,
        adjusted_closes=closes,
    )
    second = calculate_ranking_forward_outcomes(
        selection=selection,
        trading_sessions=sessions,
        adjusted_closes=tuple(reversed(closes)),
    )

    assert first == second
    assert all(item.outcome_hash for item in first.outcomes)


def test_positive_momentum_control_is_deterministic_and_keeps_empty_slots_cash() -> None:
    sessions = _sessions(21)
    closes = (
        _close("A", sessions[0], 100.0),
        _close("A", sessions[20], 120.0),
        _close("B", sessions[0], 100.0),
        _close("B", sessions[20], 120.0),
        _close("C", sessions[0], 100.0),
        _close("C", sessions[20], 110.0),
        _close("D", sessions[0], 100.0),
        _close("D", sessions[20], 90.0),
    )

    selection = select_positive_momentum_top10(
        signal_date=sessions[20],
        eligible_asset_codes=("D", "C", "B", "A", "E"),
        trading_sessions=sessions,
        adjusted_closes=tuple(reversed(closes)),
    )

    assert selection.selected_asset_codes == ("A", "B", "C")
    assert selection.target_weights == (("A", 0.1), ("B", 0.1), ("C", 0.1))
    assert selection.cash_target_weight == pytest.approx(0.7)
    assert selection.requested_asset_count == 5
    assert selection.priced_asset_count == 4
    assert selection.coverage_ratio == pytest.approx(0.8)
    assert selection.contract_hash == PURE_MOMENTUM_CONTROL_CONTRACT_HASH
    assert ("D", "non_positive_20_session_momentum") in selection.exclusions
    assert ("E", "missing_adjusted_momentum_price") in selection.exclusions


def _portfolio_closes(
    sessions: tuple[date, ...],
    values: dict[str, tuple[float, ...]],
) -> tuple[ForwardAdjustedClose, ...]:
    return tuple(
        _close(code, session, values[code][index])
        for code in sorted(values)
        for index, session in enumerate(sessions)
    )


def _target(
    signal_date: date,
    weights: dict[str, float],
) -> RankingPortfolioTarget:
    return freeze_ranking_portfolio_target(
        signal_date=signal_date,
        target_weights=weights,
        source_hash=_hash(f"target:{signal_date}:{sorted(weights.items())}"),
    )


def test_continuous_ledger_rebalances_actual_drift_and_charges_actual_trades() -> None:
    sessions = _sessions(3)
    closes = _portfolio_closes(
        sessions,
        {"A": (1.0, 1.0, 2.0), "B": (1.0, 1.0, 0.5)},
    )
    ledger = calculate_continuous_ranking_portfolio(
        trading_sessions=sessions,
        adjusted_closes=closes,
        targets=(
            _target(sessions[0], {"A": 0.5, "B": 0.5}),
            _target(sessions[1], {"A": 0.5, "B": 0.5}),
        ),
    )

    first_rebalance = ledger.points[1]
    drift_rebalance = ledger.points[2]
    assert first_rebalance.transaction_cost > 0.0
    assert drift_rebalance.net_trade_notional > 0.0
    assert drift_rebalance.transaction_cost > 0.0
    assert drift_rebalance.pre_rebalance_net_value > drift_rebalance.post_rebalance_net_value
    assert ledger.gross_return != ledger.net_return
    assert all(point.net_cash >= 0.0 for point in ledger.points)


def test_retained_position_has_no_fictitious_repeat_cost() -> None:
    sessions = _sessions(8)
    closes = _portfolio_closes(
        sessions,
        {"A": tuple(100.0 + index for index in range(len(sessions)))},
    )
    ledger = calculate_continuous_ranking_portfolio(
        trading_sessions=sessions,
        adjusted_closes=closes,
        targets=tuple(
            _target(signal_date, {"A": 1.0}) for signal_date in sessions[:-1]
        ),
    )

    assert ledger.points[1].transaction_cost > 0.0
    assert all(point.transaction_cost == 0.0 for point in ledger.points[2:])
    assert ledger.order_count == 1


def test_cost_stress_is_diagnostic_and_missing_valuation_stops_the_curve() -> None:
    sessions = _sessions(3)
    complete_closes = _portfolio_closes(
        sessions,
        {"A": (100.0, 100.0, 101.0)},
    )
    target = _target(sessions[0], {"A": 1.0})
    base = calculate_continuous_ranking_portfolio(
        trading_sessions=sessions,
        adjusted_closes=complete_closes,
        targets=(target,),
        cost_policy=RANKING_PORTFOLIO_BASE_COST_POLICY,
    )
    stress = calculate_continuous_ranking_portfolio(
        trading_sessions=sessions,
        adjusted_closes=complete_closes,
        targets=(target,),
        cost_policy=RANKING_PORTFOLIO_STRESS_COST_POLICY,
    )
    missing = calculate_continuous_ranking_portfolio(
        trading_sessions=sessions,
        adjusted_closes=complete_closes[:-1],
        targets=(target,),
    )

    assert base.fee_bps_per_side == stress.fee_bps_per_side == 5
    assert base.slippage_bps_per_side == 5
    assert stress.slippage_bps_per_side == 10
    assert stress.total_transaction_cost > base.total_transaction_cost
    assert stress.net_return < base.net_return
    assert missing.status == "unavailable"
    assert missing.net_return is None
    assert missing.unavailable_intervals[0].start_session == sessions[2]
    assert missing.unavailable_intervals[0].asset_codes == ("A",)
