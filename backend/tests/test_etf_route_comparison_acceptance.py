"""Independent acceptance cases for the declared ETF comparison contract."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.market_data import is_etf_exchange_trading_day
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    ForwardAdjustedClose,
    freeze_ranking_portfolio_target,
)
from app.services.strategy_lab.etf_strategy_route_comparison import (
    ROUTE_DAILY_CORE_HYSTERESIS,
    ROUTE_MEDIUM_TERM_MOMENTUM,
    ROUTE_V2_BREAKOUT,
    ComparisonContractError,
    ComparisonDecisionSnapshot,
    ComparisonInput,
    ComparisonValuationInput,
    FrozenComparisonProvenance,
    V2StateCheck,
    generate_medium_term_momentum_targets,
    run_comparison,
    select_medium_term_momentum_top10,
)

START = date(2026, 8, 26)
END = date(2026, 8, 28)
CODES = tuple(f"510{index:03d}" for index in range(10))
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _hash(value):
    return stable_contract_hash({"acceptance": value})


def _calendar():
    origin = date(2026, 1, 1)
    return tuple(
        day
        for offset in range((date(2026, 9, 1) - origin).days + 1)
        if is_etf_exchange_trading_day(day := origin + timedelta(days=offset))
    )


def _close(code, day):
    value = 100.0 if day < START else 110.0
    if code == CODES[0] and day >= END:
        value = 220.0
    return ForwardAdjustedClose(
        asset_code=code,
        session_date=day,
        adjusted_close=value,
        price_basis="total_return_adjusted",
        decision_eligible=True,
        provider="eastmoney",
        adjustment_version="eastmoney.push2his.kline.hfq_v1",
        source_hash=_hash((code, day, value)),
    )


def _empty_v2_check(day):
    # This fixture explicitly seals a day with no qualifying lifecycle signals.
    # Its ten-asset market-data pool is separate from this empty signal set.
    payload = {
        "schema_version": "etf_strategy_route_comparison_v2_day_check_v1",
        "session_date": day,
        "manifest_hash": _hash(("manifest", day)),
        "input_hash": _hash(("checked-input", day)),
        "observation_digest": stable_contract_hash([]),
        "transition_digest": stable_contract_hash([]),
        "checked_through": day,
        "checked_through_cutoff": datetime.combine(day, time(19), SHANGHAI),
        "lifecycle_checked": True,
        "required_observation_keys": (),
        "checked_observation_keys": (),
        "transition_source_hashes": (),
    }
    return V2StateCheck(
        **{key: value for key, value in payload.items() if key != "schema_version"},
        state_hash=stable_contract_hash(payload),
        observation_count=0,
        transition_count=0,
    )


def _input(end=END):
    calendar = _calendar()
    sessions = tuple(day for day in calendar if day <= end)
    decisions = tuple(day for day in sessions if START <= day)
    snapshots = tuple(
        ComparisonDecisionSnapshot(
            signal_date=day,
            decision_cutoff=datetime.combine(day, time(19), SHANGHAI),
            source_hash=_hash(("snapshot", day)),
            eligible_asset_codes=CODES,
            nonclone_asset_codes=CODES,
            adjusted_closes=tuple(
                _close(code, historical_day)
                for code in CODES
                for historical_day in sessions
                if historical_day <= day
            ),
        )
        for day in decisions
    )
    provenance = FrozenComparisonProvenance(
        source_mode="factual_pit",
        data_version="independent-acceptance-v1",
        universe_policy_hash=_hash("universe"),
        membership_policy_hash=_hash("membership"),
        clone_policy_hash=_hash("clone"),
        adjusted_price_policy_hash=_hash("price"),
        calendar_hash=_hash(calendar),
        source_snapshot_hash=_hash(tuple(s.source_hash for s in snapshots)),
    )
    return ComparisonInput(
        provenance=provenance,
        start_date=START,
        end_date=end,
        valuation=ComparisonValuationInput(
            trading_sessions=sessions,
            adjusted_closes=tuple(_close(code, day) for code in CODES for day in decisions),
            calendar_sessions=calendar,
            calendar_complete_through=calendar[-1],
        ),
        decision_snapshots=snapshots,
        v2_events=(),
        v2_state_checks=tuple(_empty_v2_check(day) for day in decisions),
        daily_core_targets=tuple(
            freeze_ranking_portfolio_target(
                signal_date=day,
                target_weights={code: 0.1 for code in CODES},
                source_hash=_hash(("daily-target", day)),
            )
            for day in decisions[:-1]
        ),
        daily_core_required_signal_dates=decisions[:-1],
    )


def test_incomplete_eleventh_asset_cannot_enter_after_ten_pass_history_gate():
    data = _input()
    first = data.decision_snapshots[0]
    missing_middle = data.valuation.trading_sessions[-50]
    extra = tuple(
        replace(_close("519999", day), adjusted_close=500.0 if day == START else 1.0)
        for day in data.valuation.trading_sessions
        if day <= START and day != missing_middle
    )
    snapshot = replace(
        first,
        eligible_asset_codes=(*CODES, "519999"),
        nonclone_asset_codes=(*CODES, "519999"),
        adjusted_closes=(*first.adjusted_closes, *extra),
    )
    selection = select_medium_term_momentum_top10(
        snapshot=snapshot, trading_sessions=data.valuation.trading_sessions
    )
    assert selection.selected_asset_codes == CODES
    assert any(code == "519999" for code, _reason in selection.exclusions)


def test_complete_zero_event_v2_is_cash_without_missing_target_errors():
    result = run_comparison(_input())
    route = next(r for r in result.routes if r.route_id == ROUTE_V2_BREAKOUT)
    assert route.status == "completed"
    assert route.base_ledger.net_return == 0.0
    assert route.base_ledger.order_count == 0
    assert route.targets == ()


def test_no_common_history_cannot_leave_other_routes_successfully_tradable():
    data = replace(_input(), decision_snapshots=())
    result = run_comparison(data)
    assert all(route.status == "unavailable" for route in result.routes)
    assert result.common_status == "unavailable"


def test_absent_v2_has_no_three_route_common_interval():
    result = run_comparison(replace(_input(), v2_state_checks=()))
    assert (
        next(r for r in result.routes if r.route_id == ROUTE_MEDIUM_TERM_MOMENTUM).status
        == "completed"
    )
    assert (
        next(r for r in result.routes if r.route_id == ROUTE_DAILY_CORE_HYSTERESIS).status
        == "completed"
    )
    assert result.common_status == "unavailable"
    assert result.common_start_date is None
    assert result.common_end_date is None


def test_later_missing_v2_check_preserves_only_the_verified_prefix():
    data = _input()
    result = run_comparison(replace(data, v2_state_checks=data.v2_state_checks[:-1]))
    route = next(r for r in result.routes if r.route_id == ROUTE_V2_BREAKOUT)
    assert route.status == "unavailable"
    assert route.base_ledger is not None
    assert tuple(point.session_date for point in route.base_ledger.points) == (
        START,
        date(2026, 8, 27),
    )
    assert result.common_status == "prefix_only"
    assert result.common_end_date == date(2026, 8, 27)


def test_monthly_net_value_matches_independent_cash_and_shares_arithmetic():
    result = run_comparison(_input())
    route = next(r for r in result.routes if r.route_id == ROUTE_MEDIUM_TERM_MOMENTUM)
    assert len(route.targets) == 1
    assert route.base_ledger.rebalance_count == 1
    # Ten equal initial buys cost 0.1%; only one ETF doubles thereafter.
    assert route.base_ledger.net_return == pytest.approx(1.1 / 1.001 - 1.0)
    assert route.base_ledger.gross_return == pytest.approx(0.1)
    assert route.base_ledger.points[-1].transaction_cost == 0.0
    assert route.base_ledger.points[-1].order_count == 0


def test_real_month_end_target_executes_on_next_month_first_session():
    data = _input(end=date(2026, 9, 1))
    result = run_comparison(data)
    route = next(r for r in result.routes if r.route_id == ROUTE_MEDIUM_TERM_MOMENTUM)
    assert tuple(target.signal_date for target in route.targets) == (
        START,
        date(2026, 8, 31),
    )
    assert route.base_ledger.rebalance_count == 2
    assert tuple(point.session_date for point in route.base_ledger.points if point.order_count) == (
        date(2026, 8, 27),
        date(2026, 9, 1),
    )


def test_terminal_month_end_target_remains_unexecuted_and_start_is_not_duplicated():
    data = _input(end=date(2026, 8, 31))
    targets = generate_medium_term_momentum_targets(replace(data, start_date=date(2026, 8, 31)))
    assert len(targets) == 1
    assert targets[0].signal_date == date(2026, 8, 31)
    result = run_comparison(data)
    route = next(r for r in result.routes if r.route_id == ROUTE_MEDIUM_TERM_MOMENTUM)
    assert len(route.targets) == 2
    assert route.base_ledger.rebalance_count == 1
    assert route.base_ledger.points[-1].order_count == 0


def test_explicit_month_end_cannot_override_verified_exchange_calendar():
    data = _input()
    with pytest.raises(ComparisonContractError):
        changed = replace(
            data,
            valuation=replace(data.valuation, month_end_sessions=(date(2026, 8, 27),)),
        )
        generate_medium_term_momentum_targets(changed)


def test_missing_calendar_session_cannot_skip_a_trading_day():
    data = _input()
    with pytest.raises(ComparisonContractError):
        replace(
            data.valuation,
            trading_sessions=tuple(
                day for day in data.valuation.trading_sessions if day != date(2026, 8, 27)
            ),
        )


def test_decision_cutoff_cannot_precede_its_price_session():
    first = _input().decision_snapshots[0]
    with pytest.raises(ComparisonContractError):
        replace(first, decision_cutoff=first.decision_cutoff - timedelta(days=1))


def test_future_valuation_prices_cannot_change_past_momentum_targets():
    original = _input()
    changed = replace(
        original,
        valuation=replace(
            original.valuation,
            adjusted_closes=tuple(
                replace(row, adjusted_close=row.adjusted_close * 7.0)
                if row.session_date == END
                else row
                for row in original.valuation.adjusted_closes
            ),
        ),
    )
    assert generate_medium_term_momentum_targets(original) == generate_medium_term_momentum_targets(
        changed
    )


def test_corrupt_store_cannot_be_reported_ready():
    from app.services.strategy_lab.etf_strategy_route_comparison_inputs import (
        ResearchStoreReadiness,
    )

    readiness = ResearchStoreReadiness(
        path="unused.sqlite3",
        exists=True,
        sqlite_readable=True,
        schema_compatible=True,
        nonempty=True,
        requested_artifact_count=1,
        requested_checkpoint_count=0,
        invalid_artifact_count=1,
        reason="corrupt",
    )
    assert readiness.ready is False


def test_run_specific_store_inspection_handles_empty_namespace(tmp_path):
    from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
    from app.services.strategy_lab.etf_strategy_route_comparison_inputs import (
        inspect_research_store,
    )

    path = tmp_path / "research.sqlite3"
    ReplayArtifactStore(path)
    readiness = inspect_research_store(path, run_id="independent-acceptance")
    assert readiness.ready is False
    assert readiness.requested_artifact_count == 0
    assert readiness.invalid_artifact_count == 0
