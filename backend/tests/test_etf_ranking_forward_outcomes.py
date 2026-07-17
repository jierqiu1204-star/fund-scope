from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.services.strategy_lab.etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10,
    RANKING_COST_CONTRACT_HASH,
    RankingCandidateSelection,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    FORWARD_HORIZONS,
    ForwardAdjustedClose,
    ForwardOutcomeContractError,
    calculate_ranking_forward_outcomes,
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
