"""Root acceptance: real V2 target bridge, shared exits, and account execution."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from test_etf_strategy_route_comparison import (
    CODES,
    END,
    MIDDLE,
    START,
    _calendar,
    _close,
    _event,
    _hash,
    _input,
    _key,
    _pit_series,
    _snapshot,
    _state_check,
)

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_strategy_route_comparison import (
    ComparisonContractError,
    ComparisonDataUnavailableError,
    _pit_series_payload,
    bridge_v2_targets,
    build_shared_daily_v2_targets,
    run_comparison,
)

MONDAY = date(2026, 8, 31)
TUESDAY = date(2026, 9, 1)


def test_legacy_diagnostic_identity_is_unchanged():
    # Recorded by running HEAD aa17aee's unmodified engine on this same input.
    event = _event(START, CODES[0], "confirmation")
    data = _input(events=(event,), keys_by_day={
        day: (_key(CODES[0]),) for day in (START, MIDDLE, END)
    })
    result = run_comparison(data)
    assert result.input_hash == "55f8964ca863cafac3f4b3a0408d88b1342fdaf22ca135ef0bd33f3a92fb92e6"
    assert result.result_hash == "36349003fba019b495cb20529b75cfb7b94f74372635e480bbcaddb197431607"


def _scenario(prices, *, policy="shared_daily"):
    days = tuple(sorted(prices))
    event = _event(START, CODES[0], "confirmation")
    original = _input(events=(event,))
    calendar = _calendar()

    def price(code, day):
        return prices.get(day, 100.0) if code == CODES[0] else 100.0

    snapshots = []
    series = []
    for day in days:
        snapshot = _snapshot(day)
        snapshots.append(replace(
            snapshot,
            adjusted_closes=tuple(
                _close(row.asset_code, row.session_date, price(row.asset_code, row.session_date))
                for row in snapshot.adjusted_closes
            ),
            source_hash=_hash(("exit-snapshot", day, tuple((d, p) for d, p in prices.items() if d <= day))),
        ))
        item = _pit_series(CODES[0], day)
        item = replace(item, bars=tuple(
            replace(
                bar,
                adjusted_open=price(CODES[0], bar.session_date),
                adjusted_high=price(CODES[0], bar.session_date) + 1,
                adjusted_low=price(CODES[0], bar.session_date) - 1,
                adjusted_close=price(CODES[0], bar.session_date),
            )
            for bar in item.bars
        ))
        series.append(replace(item, series_hash=stable_contract_hash(_pit_series_payload(item))))
    return replace(
        original,
        end_date=days[-1],
        valuation=replace(
            original.valuation,
            trading_sessions=tuple(day for day in calendar if day <= days[-1]),
            adjusted_closes=tuple(_close(code, day, price(code, day)) for code in CODES for day in days),
        ),
        decision_snapshots=tuple(snapshots),
        v2_state_checks=tuple(
            _state_check(day, keys=(_key(CODES[0]),), events=(event,) if day == START else ())
            for day in days
        ),
        leader_exit_policy=policy,
        leader_exit_pit_series=tuple(series),
    )


def test_two_r_is_optional_and_execution_uses_next_close_with_costs():
    prices = {START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0}
    baseline = _scenario(prices)
    take_profit = replace(baseline, leader_exit_policy="shared_daily_2r")
    assert tuple(item.signal_date for item in bridge_v2_targets(input_data=baseline)) == (START,)
    targets = bridge_v2_targets(input_data=take_profit)
    assert tuple(item.signal_date for item in targets) == (START, END)
    assert targets[-1].target_weights == ()
    result = run_comparison(take_profit)
    ledger = result.routes[0].base_ledger
    assert ledger is not None and ledger.status == "completed"
    # One 10% slot enters at 101, exits at 104 rather than the 105 trigger.
    assert ledger.gross_return == pytest.approx(0.1 * (104 / 101 - 1))
    assert ledger.net_return < ledger.gross_return


def test_shared_exit_catches_hard_stop_without_legacy_invalidation_event():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 98.0, MONDAY: 98.5})
    targets = bridge_v2_targets(input_data=data)
    assert tuple(item.signal_date for item in targets) == (START, END)
    assert targets[-1].target_weights == ()


def test_profit_above_entry_can_exit_ma5_without_fixed_take_profit():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 110.0, MONDAY: 104.0, TUESDAY: 103.0})
    targets = bridge_v2_targets(input_data=data)
    assert tuple(item.signal_date for item in targets) == (START, TUESDAY)
    assert targets[-1].target_weights == ()


def test_missing_entry_day_pit_cannot_borrow_later_history():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    data = replace(data, leader_exit_pit_series=tuple(
        item for item in data.leader_exit_pit_series if item.bars[-1].session_date != MIDDLE
    ))
    with pytest.raises(ComparisonDataUnavailableError):
        bridge_v2_targets(input_data=data)


def test_late_entry_history_cannot_become_a_past_decision():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    changed = []
    for item in data.leader_exit_pit_series:
        if item.bars[-1].session_date == MIDDLE:
            item = replace(item, latest_source_timestamp=item.latest_source_timestamp + timedelta(days=1))
            item = replace(item, series_hash=stable_contract_hash(_pit_series_payload(item)))
        changed.append(item)
    with pytest.raises(ComparisonDataUnavailableError):
        data = replace(data, leader_exit_pit_series=tuple(changed))
        bridge_v2_targets(input_data=data)


def test_future_execution_price_changes_pnl_but_not_exit_signal():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0}, policy="shared_daily_2r")
    changed = replace(data, valuation=replace(data.valuation, adjusted_closes=tuple(
        _close(row.asset_code, row.session_date, 90.0)
        if row.asset_code == CODES[0] and row.session_date == MONDAY else row
        for row in data.valuation.adjusted_closes
    )))
    assert bridge_v2_targets(input_data=changed) == bridge_v2_targets(input_data=data)
    loss = run_comparison(changed).routes[0].base_ledger
    assert loss is not None and loss.net_return < 0


def test_policy_change_has_distinct_input_and_result_identity():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    baseline = run_comparison(data)
    profit = run_comparison(replace(data, leader_exit_policy="shared_daily_2r"))
    assert baseline.input_hash != profit.input_hash
    assert baseline.result_hash != profit.result_hash


@pytest.mark.parametrize(("path", "policy", "reason", "exit_day"), [
    ({START: 100.0, MIDDLE: 101.0, END: 98.0, MONDAY: 98.5}, "shared_daily", "leader_tactics_hard_stop", END),
    ({START: 100.0, MIDDLE: 101.0, END: 103.0, MONDAY: 101.0}, "shared_daily", "leader_tactics_breakeven_exit", MONDAY),
    ({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0}, "shared_daily_2r", "leader_tactics_take_profit", END),
    ({START: 100.0, MIDDLE: 101.0, END: 110.0, MONDAY: 104.0, TUESDAY: 103.0}, "shared_daily", "leader_tactics_ma5_exit", TUESDAY),
])
def test_exit_record_identifies_frozen_entry_and_actual_rule(path, policy, reason, exit_day):
    replay = build_shared_daily_v2_targets(_scenario(path, policy=policy), take_profit=policy == "shared_daily_2r")
    record = next(item for item in replay.exit_records if item.exit_reason is not None)
    assert record.entry_execution_date == MIDDLE
    assert record.entry_reference_price == 101.0
    assert record.initial_stop == 99.0
    assert record.risk_unit == 2.0
    assert record.exit_signal_date == exit_day
    assert record.exit_reason == reason
    assert record.exit_signal_date >= record.entry_execution_date


def test_pre_entry_peak_does_not_arm_position_breakeven():
    data = _scenario({START: 110.0, MIDDLE: 101.0, END: 102.0, MONDAY: 102.0})
    replay = build_shared_daily_v2_targets(data)
    record = next(item for item in replay.exit_records if item.exit_reason is not None)
    assert record.exit_signal_date == MIDDLE
    assert record.exit_reason == "leader_tactics_ma5_exit"


def _change_series(data, day, transform):
    changed = []
    for item in data.leader_exit_pit_series:
        if item.bars[-1].session_date == day:
            item = transform(item)
            item = replace(item, series_hash=stable_contract_hash(_pit_series_payload(item)))
        changed.append(item)
    return replace(data, leader_exit_pit_series=tuple(changed))


def test_exit_decision_uses_cutoff_pit_not_later_valuation_revision():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0}, policy="shared_daily_2r")
    revised = replace(data, valuation=replace(data.valuation, adjusted_closes=tuple(
        _close(row.asset_code, row.session_date, 90.0)
        if row.asset_code == CODES[0] and row.session_date == END else row
        for row in data.valuation.adjusted_closes
    )))
    assert bridge_v2_targets(input_data=revised) == bridge_v2_targets(input_data=data)


def test_missing_ma5_session_does_not_shorten_window_or_skip_day():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    data = _change_series(data, END, lambda item: replace(item, bars=tuple(
        bar for bar in item.bars if bar.session_date != START
    )))
    with pytest.raises(ComparisonDataUnavailableError):
        bridge_v2_targets(input_data=data)


def test_late_pit_membership_cannot_bypass_quote_cutoff():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    data = _change_series(data, END, lambda item: replace(item, metadata=replace(
        item.metadata, membership_known_at=item.metadata.membership_known_at + timedelta(days=1),
    )))
    with pytest.raises(ComparisonDataUnavailableError):
        bridge_v2_targets(input_data=data)


def test_tampered_pit_hash_cannot_supply_a_stop_price():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    with pytest.raises(ComparisonContractError):
        data = replace(data, leader_exit_pit_series=tuple(
            replace(item, series_hash="0" * 64) if item.bars[-1].session_date == MIDDLE else item
            for item in data.leader_exit_pit_series
        ))
        bridge_v2_targets(input_data=data)


def test_terminal_confirmation_stays_pending_without_reading_future_entry():
    data = _scenario({START: 100.0})
    replay = build_shared_daily_v2_targets(data)
    assert tuple(item.signal_date for item in replay.targets) == (START,)
    assert replay.targets[0].target_weights == ((CODES[0], 0.1),)
    assert replay.exit_records == ()


def test_entry_atr_cannot_skip_a_missing_exchange_session():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    data = _change_series(data, MIDDLE, lambda item: replace(item, bars=tuple(
        bar for bar in item.bars if bar.session_date != item.bars[-10].session_date
    )))
    with pytest.raises(ComparisonDataUnavailableError):
        bridge_v2_targets(input_data=data)


def test_later_adjustment_basis_cannot_mix_with_frozen_entry_stop():
    data = _scenario({START: 100.0, MIDDLE: 101.0, END: 105.0, MONDAY: 104.0})
    data = _change_series(data, END, lambda item: replace(item, provenance=replace(
        item.provenance, adjustment_version="fixture-v2",
    )))
    with pytest.raises(ComparisonDataUnavailableError):
        bridge_v2_targets(input_data=data)
