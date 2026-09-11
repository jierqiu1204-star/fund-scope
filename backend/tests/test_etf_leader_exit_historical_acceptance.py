"""Root acceptance for current-vintage V2 reconstruction and real ledger exits."""

from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.market_data import is_etf_exchange_trading_day
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    HISTORICAL_RECONSTRUCTION_MODE,
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
    screen_dual_universe,
)

FROZEN = datetime(2026, 9, 11, 6, 30, tzinfo=UTC)
RECEIVED = datetime(2026, 9, 10, 14, tzinfo=UTC)
START = date(2026, 7, 15)
END = date(2026, 7, 24)
TARGET = "510001"


def _calendar(end=END):
    current = date(2026, 1, 5)
    result = []
    while current <= end:
        if is_etf_exchange_trading_day(current):
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)


def _assets():
    from app.services.strategy_lab.etf_leader_exit_historical import HistoricalV2Asset

    days = _calendar()
    path = dict(zip([day for day in days if day >= START],
                    (110.0, 112.0, 113.0, 123.0, 122.0, 111.0, 110.0, 109.0), strict=True))
    assets = []
    for group in range(3):
        for peer in range(6):
            code = f"51{group:01d}{peer + 1:03d}"
            membership = V2PITMembership(
                group_id=f"theme-{group}", effective_from=RECEIVED.date(),
                effective_to=None, observed_at=RECEIVED,
                mapping_kind="current_vintage_proxy", taxonomy_version="fixture-v1",
                theme=f"theme-{group}", sector=f"theme-{group}",
                tracked_index=f"index-{code}", clone_group=f"index-{code}",
            )
            membership = replace(membership, fact_hash=stable_contract_hash(membership.canonical_payload()))
            bars = []
            for index, day in enumerate(days):
                close = 100.0 + index * (0.01 if group == 0 else -0.01 * group)
                if code == TARGET:
                    close = path.get(day, 100.0 + index * 0.05)
                low = 108.0 if code == TARGET and day == START else close - 2
                high = 111.0 if code == TARGET and day == START else close + 2
                bars.append(V2AdjustedBar(
                    trade_date=day, adjusted_open=close - 0.1, adjusted_high=high,
                    adjusted_low=low, adjusted_close=close,
                    volume=10_000.0 if code == TARGET and day == START else 1_000.0,
                    amount=1_000_000.0 if code == TARGET else 100_000.0,
                    turnover=1_000_000.0 if code == TARGET else 100_000.0,
                    observed_at=RECEIVED, provider="eastmoney",
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    revision_id=stable_contract_hash((code, day, close)),
                ))
            assets.append(HistoricalV2Asset(
                asset_code=code, name=f"ETF {code}", listed_date=date(2025, 1, 1),
                membership=membership, bars=tuple(bars), underlying=f"index-{code}",
            ))
    return tuple(assets)


def _dataset(assets=None):
    from app.services.strategy_lab.etf_leader_exit_historical import HistoricalV2Dataset

    return HistoricalV2Dataset(
        frozen_at=FROZEN, start_date=START, end_date=END,
        trading_sessions=_calendar(), assets=_assets() if assets is None else assets,
        source_manifest=(("fixture", "real-v2-rules-synthetic-prices"),),
    )


def test_historical_mode_uses_real_v2_without_backdating_receipts():
    assets = _assets()
    inputs = tuple(V2AssetInput(
        universe="etf", asset_code=asset.asset_code, asset_name=asset.name,
        signal_date=START, source_cutoff=FROZEN,
        bars=tuple(bar for bar in asset.bars if bar.trade_date <= START),
        membership=asset.membership, decision_mode=HISTORICAL_RECONSTRUCTION_MODE,
        membership_evaluation_date=FROZEN.date(),
    ) for asset in assets)
    result = screen_dual_universe(inputs)
    candidate = next(row for row in result.observations if row.asset_code == TARGET and row.formula_id == BREAKOUT_V2)
    assert candidate.qualifies, candidate.exclusion_reasons
    assert dict(candidate.gate_facts)["historical_validation_eligible"] is False
    assert inputs[0].bars[-1].observed_at == RECEIVED
    strict = screen_dual_universe(tuple(replace(
        item, source_cutoff=datetime(2026, 7, 15, 11, tzinfo=UTC),
        decision_mode="session_pit", membership_evaluation_date=START,
    ) for item in inputs))
    assert not any(row.qualifies for row in strict.observations)


def test_three_policies_share_confirmations_and_execute_on_next_close():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    result = run_historical_v2_exit_comparison(_dataset())
    assert result.formal_pit_credit == 0
    policies = {row.policy_id: row for row in result.policies}
    assert set(policies) == {"legacy_ma5", "shared_daily", "shared_daily_2r"}
    assert len({row.confirmation_count for row in result.policies}) == 1
    assert min(row.confirmation_count for row in result.policies) >= 1
    for policy in result.policies:
        assert policy.ledger.status == "completed"
        assert policy.ledger.points[0].session_date == START
        assert policy.ledger.points[-1].session_date == END
        assert policy.ledger.total_transaction_cost > 0
        assert policy.ledger.net_return < policy.ledger.gross_return
        assert {trade.entry_execution_date for trade in policy.trades} == {date(2026, 7, 17)}
    profit_trade = policies["shared_daily_2r"].trades[0]
    assert profit_trade.entry_reference_price == 113.0
    assert profit_trade.initial_stop == 108.0
    assert profit_trade.risk_unit == 5.0
    assert profit_trade.exit_signal_date == date(2026, 7, 20)
    assert profit_trade.exit_execution_date == date(2026, 7, 21)
    assert profit_trade.exit_price == 122.0
    assert profit_trade.exit_reason == "leader_tactics_take_profit"
    assert policies["shared_daily_2r"].ledger.gross_return == pytest.approx(0.1 * (122 / 113 - 1))
    assert policies["shared_daily_2r"].ledger.net_return > policies["shared_daily"].ledger.net_return


def test_noncanonical_asset_order_is_rejected():
    data = _dataset()
    with pytest.raises(ValueError, match="sorted"):
        replace(data, assets=tuple(reversed(data.assets)), source_hash="")


def test_receipt_after_frozen_dataset_is_rejected():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    changed = replace(data.assets[0], bars=tuple(
        replace(bar, observed_at=FROZEN + timedelta(days=1)) for bar in data.assets[0].bars
    ))
    with pytest.raises(ValueError):
        run_historical_v2_exit_comparison(replace(data, assets=(changed, *data.assets[1:])))


def test_future_prices_cannot_change_earlier_candidates_or_confirmation():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    later = date(2026, 7, 21)
    changed = replace(data.assets[0], bars=tuple(
        replace(bar, adjusted_open=80.0, adjusted_high=82.0,
                adjusted_low=78.0, adjusted_close=80.0,
                revision_id=stable_contract_hash((bar.revision_id, "changed")))
        if bar.trade_date >= later else bar for bar in data.assets[0].bars
    ))
    first = run_historical_v2_exit_comparison(data)
    second = run_historical_v2_exit_comparison(replace(
        data, assets=(changed, *data.assets[1:]), source_hash="",
    ))
    def decisions(result):
        return tuple((row.event_type, row.asset_code, row.signal_date,
                      row.original_signal_date, row.score)
                     for row in (*result.candidate_events, *result.confirmation_events)
                     if row.signal_date < later)
    assert decisions(first) == decisions(second)
    assert first.dataset_hash != second.dataset_hash
    assert first.policies[2].ledger.net_return != second.policies[2].ledger.net_return


def test_missing_future_holding_price_preserves_earlier_candidate_and_blocks_result():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    changed = replace(data.assets[0], bars=tuple(
        bar for bar in data.assets[0].bars if bar.trade_date != date(2026, 7, 21)
    ))
    result = run_historical_v2_exit_comparison(replace(
        data, assets=(changed, *data.assets[1:]), source_hash="",
    ))
    assert any(row.asset_code == TARGET and row.signal_date == START for row in result.candidate_events)
    assert any(row.asset_code == TARGET for row in result.confirmation_events)
    for policy in result.policies:
        assert policy.ledger.status == "unavailable"
        assert policy.ledger.net_return is None
        assert policy.ledger.unavailable_intervals
        assert policy.entry_count == 1
        assert policy.exit_count == 0
    profit_policy = next(row for row in result.policies if row.policy_id == "shared_daily_2r")
    assert any(row.signal_date == date(2026, 7, 20) and not row.target_weights
               for row in profit_policy.targets)


def test_frozen_input_replays_identically_and_rejects_tampered_hash():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    assert asdict(run_historical_v2_exit_comparison(data)) == asdict(run_historical_v2_exit_comparison(data))
    with pytest.raises(ValueError, match="hash"):
        replace(data, source_hash="f" * 64)


def test_missing_signal_warmup_day_is_not_replaced_by_an_older_bar():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    changed = replace(data.assets[0], bars=tuple(
        bar for bar in data.assets[0].bars if bar.trade_date != date(2026, 7, 14)
    ))
    result = run_historical_v2_exit_comparison(replace(
        data, assets=(changed, *data.assets[1:]), source_hash="",
    ))
    assert not any(row.asset_code == TARGET and row.signal_date == START for row in result.candidate_events)
    assert any(TARGET in key and ("session" in reason or "history" in reason)
               for key, reason in result.exclusions)


def test_signal_on_terminal_day_is_pending_without_fabricating_sell():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    last = date(2026, 7, 20)
    shortened = replace(data, end_date=last, source_hash="",
                        trading_sessions=tuple(day for day in data.trading_sessions if day <= last),
                        assets=tuple(replace(asset, bars=tuple(bar for bar in asset.bars if bar.trade_date <= last))
                                     for asset in data.assets))
    result = run_historical_v2_exit_comparison(shortened)
    profit_policy = next(row for row in result.policies if row.policy_id == "shared_daily_2r")
    trade = profit_policy.trades[0]
    assert trade.exit_signal_date == last
    assert trade.exit_price is None
    assert trade.status in {"exit_pending", "open"}
    assert profit_policy.ledger.status == "completed"
    assert profit_policy.ledger.points[-1].net_holdings
    assert profit_policy.exit_count == 0


def test_mixed_adjustment_versions_are_not_a_single_historical_price_series():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    changed = replace(data.assets[0], bars=tuple(
        replace(bar, adjustment_version="another-vintage") if bar.trade_date >= date(2026, 7, 20)
        else bar for bar in data.assets[0].bars
    ))
    with pytest.raises(ValueError):
        run_historical_v2_exit_comparison(replace(
            data, assets=(changed, *data.assets[1:]), source_hash="",
        ))


def test_screen_programming_failure_is_not_reported_as_successful_cash_return(monkeypatch):
    from app.services.strategy_lab import etf_leader_exit_historical as historical

    def broken_screen(*args, **kwargs):
        raise RuntimeError("root-acceptance-screen-failure")

    monkeypatch.setattr(historical, "screen_dual_universe", broken_screen)
    with pytest.raises(RuntimeError, match="root-acceptance-screen-failure"):
        historical.run_historical_v2_exit_comparison(_dataset())


@pytest.mark.parametrize("policy_id", ("legacy_ma5", "shared_daily", "shared_daily_2r"))
def test_missing_future_fill_cannot_change_other_positions_same_day_decisions(policy_id):
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    second = replace(data.assets[1], bars=data.assets[0].bars)
    complete = replace(data, assets=(data.assets[0], second, *data.assets[2:]), source_hash="")
    missing = replace(complete.assets[0], bars=tuple(
        bar for bar in complete.assets[0].bars if bar.trade_date != date(2026, 7, 21)
    ))
    incomplete = replace(complete, assets=(missing, *complete.assets[1:]), source_hash="")
    first = run_historical_v2_exit_comparison(complete, policies=(policy_id,)).policies[0]
    second = run_historical_v2_exit_comparison(incomplete, policies=(policy_id,)).policies[0]
    assert first.entry_count == 2
    assert second.entry_count == 2
    assert {trade.asset_code for trade in second.trades} == {complete.assets[0].asset_code, complete.assets[1].asset_code}
    def targets_until_exit(policy):
        return [(row.signal_date, row.target_weights) for row in policy.targets
                if row.signal_date <= date(2026, 7, 20)]
    assert targets_until_exit(first) == targets_until_exit(second)
    assert second.ledger.status == "unavailable"


def test_gap_older_than_required_warmup_does_not_permanently_remove_asset():
    from app.services.strategy_lab.etf_leader_exit_historical import (
        run_historical_v2_exit_comparison,
    )

    data = _dataset()
    early = data.trading_sessions[0]
    assert len([day for day in data.trading_sessions if early < day <= START]) >= 120
    changed = replace(data.assets[0], bars=tuple(bar for bar in data.assets[0].bars if bar.trade_date != early))
    result = run_historical_v2_exit_comparison(replace(
        data, assets=(changed, *data.assets[1:]), source_hash="",
    ))
    assert any(row.asset_code == TARGET and row.signal_date == START for row in result.candidate_events)
