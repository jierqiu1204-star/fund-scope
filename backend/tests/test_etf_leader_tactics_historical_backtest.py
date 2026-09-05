from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.market_data import next_etf_exchange_trading_day
from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    HISTORICAL_BACKTEST_CONTRACT,
    HISTORICAL_BACKTEST_CONTRACT_HASH,
    HISTORICAL_EXECUTION_POLICY,
    LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
    LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_BACKTEST_NOT_PIT,
    LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
    LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
    LEGACY_HISTORICAL_BACKTEST_CONTRACT_HASH,
    ROUND_TRIP_COST_RATE,
    HistoricalLeaderAsset,
    HistoricalLeaderBar,
    HistoricalLeaderEvent,
    LeaderHistoricalBacktestContractError,
    _entry_risk_context,
    build_historical_backtest_evidence,
    eligible_historical_signal_dates,
    summarize_historical_events,
)
from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    evaluate_historical_signal_date as _evaluate_historical_signal_date,
)
from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    replay_historical_exit_policy as _replay_historical_exit_policy,
)


def evaluate_historical_signal_date(assets, signal_date):
    # The sealed 2025 fixture deliberately has one bar per calendar day.
    return _evaluate_historical_signal_date(
        assets, signal_date,
        trading_sessions=tuple(sorted({bar.trade_date for asset in assets for bar in asset.bars})),
    )


def replay_historical_exit_policy(assets, events, **kwargs):
    return _replay_historical_exit_policy(
        assets, events,
        trading_sessions=tuple(sorted({bar.trade_date for asset in assets for bar in asset.bars})),
        **kwargs,
    )


def _asset(
    code: str,
    *,
    group: str,
    slope: float,
    turnover: float,
    target: bool = False,
) -> HistoricalLeaderAsset:
    start = date(2025, 1, 1)
    bars: list[HistoricalLeaderBar] = []
    for index in range(205):
        close = 10.0 + index * slope
        volume = 1_000_000.0 + index
        if target and index == 179:
            close += 1.0
            volume = 9_000_000.0
        bars.append(
            HistoricalLeaderBar(
                trade_date=start + timedelta(days=index),
                adjusted_open=close - 0.02,
                adjusted_high=close + 0.05,
                adjusted_low=close - 0.05,
                adjusted_close=close,
                volume=volume,
                turnover=turnover + index,
            )
        )
    return HistoricalLeaderAsset(
        asset_code=code,
        name=f"ETF-{code}",
        peer_group=group,
        clone_group=code,
        baseline_score=50.0,
        bars=tuple(bars),
    )


def _universe() -> tuple[HistoricalLeaderAsset, ...]:
    rows: list[HistoricalLeaderAsset] = []
    for group_index, group in enumerate(("强势", "中性", "弱势")):
        base_slope = (0.030, 0.012, -0.002)[group_index]
        for peer in range(5):
            rows.append(
                _asset(
                    f"{group_index}{peer}",
                    group=group,
                    slope=base_slope + peer * 0.001,
                    turnover=10_000_000.0 + peer * 1_000_000.0,
                    target=group_index == 0 and peer == 4,
                )
            )
    return tuple(rows)


def _confirmed_universe() -> tuple[HistoricalLeaderAsset, ...]:
    assets = list(_universe())
    target = assets[4]
    bars = list(target.bars)
    for index in range(159, 179):
        bar = bars[index]
        bars[index] = replace(
            bar,
            adjusted_high=bar.adjusted_close + 0.7,
            adjusted_low=bar.adjusted_close - 0.7,
        )
    bars[179] = replace(
        bars[179],
        adjusted_open=16.7,
        adjusted_high=17.0,
        adjusted_low=16.2,
        adjusted_close=16.9,
        volume=9_000_000.0,
    )
    bars[180] = replace(
        bars[180],
        adjusted_open=16.95,
        adjusted_high=17.3,
        adjusted_low=16.8,
        adjusted_close=17.2,
    )
    assets[4] = replace(target, bars=tuple(bars))
    return tuple(assets)


def test_unconfirmed_signal_does_not_create_a_trade() -> None:
    assets = _universe()
    signal_date = assets[0].bars[179].trade_date

    events = evaluate_historical_signal_date(assets, signal_date)

    target = [
        item
        for item in events
        if item.asset_code == "04" and item.candidate_id == "leader_breakout_proxy_v1"
    ]
    assert len(target) == 1
    event = target[0]
    assert event.trade_status == "confirmation_overextended"
    assert event.entry_date is None
    assert event.net_return is None
    assert event.entry_quality_state == "overextended"
    assert "pivot_buy_zone_exceeded" in event.entry_quality_reason_codes
    assert event.next_session_confirmation_state == "overextended"


def test_confirmed_signal_enters_t2_open_and_exits_after_close_signal() -> None:
    assets = _confirmed_universe()
    signal_date = assets[0].bars[179].trade_date

    events = evaluate_historical_signal_date(assets, signal_date)

    event = next(
        item
        for item in events
        if item.asset_code == "04"
        and item.candidate_id == "leader_breakout_proxy_v1"
    )
    assert event.entry_quality_state == "disciplined"
    assert event.next_session_confirmation_state == "confirmed"
    assert event.confirmation_date == assets[4].bars[180].trade_date
    assert event.entry_date == assets[4].bars[181].trade_date
    assert event.entry_price == assets[4].bars[181].adjusted_open
    assert event.exit_signal_date is not None
    assert event.exit_date is not None
    assert event.exit_date > event.exit_signal_date
    assert event.gross_return > event.net_return
    assert event.net_return == pytest.approx(
        event.exit_price * 0.9995 ** 2 / (event.entry_price * 1.0005 ** 2) - 1
    )
    assert ROUND_TRIP_COST_RATE == 0.002
    row = next(
        item
        for item in summarize_historical_events(events)
        if item["candidate_id"] == "leader_breakout_proxy_v1"
    )
    assert row["next_session_confirmation_counts"]["confirmed"] == 1
    assert row["closed_trade_count"] == 1


@pytest.mark.parametrize(("missing_index", "expected_status"), [
    (180, "confirmation_price_missing"),
    (181, "entry_price_missing"),
    (182, "lifecycle_price_missing"),
])
def test_missing_exchange_session_never_uses_a_later_bar(missing_index, expected_status):
    days = [date(2026, 1, 5)]
    for _ in range(204):
        days.append(next_etf_exchange_trading_day(days[-1]))
    assets = tuple(replace(asset, bars=tuple(
        replace(bar, trade_date=days[index]) for index, bar in enumerate(asset.bars)
    )) for asset in _confirmed_universe())
    original = _evaluate_historical_signal_date(assets, days[179])[0]
    assert original.trade_status == "closed"
    changed = tuple(replace(asset, bars=tuple(
        bar for index, bar in enumerate(asset.bars)
        if asset.asset_code != "04" or index != missing_index
    )) for asset in assets)
    result = _evaluate_historical_signal_date(changed, days[179])[0]
    assert result.trade_status == expected_status
    assert result.net_return is None
    assert result.exit_date is None
    if missing_index == 182:
        replayed = _replay_historical_exit_policy(changed, (original,), take_profit_return=0.99)[0]
        assert replayed.trade_status == expected_status
        assert replayed.net_return is None


def test_unsupported_exchange_calendar_does_not_claim_a_historical_trade():
    assets = _confirmed_universe()
    result = _evaluate_historical_signal_date(assets, assets[4].bars[179].trade_date)[0]
    assert result.trade_status == "exchange_calendar_unavailable"
    assert result.entry_date is None
    assert result.net_return is None


def test_future_prices_cannot_change_signal_identity_but_change_outcome() -> None:
    assets = _confirmed_universe()
    signal_date = assets[0].bars[179].trade_date
    original = evaluate_historical_signal_date(assets, signal_date)
    target = assets[4]
    changed_bars = list(target.bars)
    bar = changed_bars[182]
    changed_bars[182] = replace(
        bar,
        adjusted_open=bar.adjusted_open * 0.9,
        adjusted_high=max(bar.adjusted_high, bar.adjusted_open),
        adjusted_low=min(bar.adjusted_low, bar.adjusted_open * 0.89),
    )
    changed_assets = (*assets[:4], replace(target, bars=tuple(changed_bars)), *assets[5:])

    changed = evaluate_historical_signal_date(changed_assets, signal_date)

    original_event = next(item for item in original if item.asset_code == "04")
    changed_event = next(item for item in changed if item.asset_code == "04")
    assert original_event.feature_hash == changed_event.feature_hash
    assert original_event.score == changed_event.score
    assert original_event.net_return != changed_event.net_return


def test_eligible_dates_require_trailing_180_and_visible_t1_t2() -> None:
    assets = _universe()
    dates = eligible_historical_signal_dates(assets)

    assert dates[0] == assets[0].bars[179].trade_date
    assert dates[-1] == assets[0].bars[202].trade_date


def test_asset_page_merge_order_cannot_change_results() -> None:
    assets = _universe()
    signal_date = assets[0].bars[179].trade_date

    canonical = evaluate_historical_signal_date(assets, signal_date)
    reversed_pages = evaluate_historical_signal_date(
        tuple(reversed(assets)), signal_date
    )

    assert canonical == reversed_pages


def test_summary_reports_research_cost_lifecycle_results() -> None:
    events = evaluate_historical_signal_date(_confirmed_universe(), date(2025, 6, 29))
    summary = summarize_historical_events(events)

    row = next(
        item
        for item in summary
        if item["candidate_id"] == "all_leader_candidates"
    )
    assert row["event_count"] > 0
    assert row["signal_date_count"] == 1
    assert row["mean_net_return"] is not None
    assert row["mean_net_excess_return"] is not None
    assert row["event_series_max_drawdown"] is not None
    assert row["execution_policy"] == HISTORICAL_EXECUTION_POLICY
    assert row["mean_gross_return"] > row["mean_net_return"]
    assert row["mean_cost_drag"] > 0


@pytest.mark.parametrize("field", ["adjusted_close", "adjusted_high", "adjusted_low"])
def test_entry_session_ohlc_cannot_change_frozen_risk_or_cost_anchor(field) -> None:
    assets = _confirmed_universe()
    original = evaluate_historical_signal_date(assets, assets[4].bars[179].trade_date)[0]
    target = assets[4]
    bars = list(target.bars)
    value = getattr(bars[181], field)
    bars[181] = replace(bars[181], **{field: value - 0.01 if field == "adjusted_low" else value + 0.01})
    changed_assets = (*assets[:4], replace(target, bars=tuple(bars)), *assets[5:])
    changed = evaluate_historical_signal_date(changed_assets, original.signal_date)[0]
    replayed = replay_historical_exit_policy(changed_assets, (original,), take_profit_return=0.99)[0]

    assert original.research_context == changed.research_context == replayed.research_context
    context = original.research_context
    assert context["entry_price"] == original.entry_price
    assert context["signal_low"] == target.bars[179].adjusted_low
    assert context["signal_low_date"] == target.bars[179].trade_date.isoformat()
    assert context["atr_as_of_date"] == target.bars[180].trade_date.isoformat()
    assert context["entry_cash_cost_per_unit"] == pytest.approx(original.entry_price * 1.0005 ** 2)
    assert context["initial_stop"] == pytest.approx(original.entry_price - 2 * context["atr20"])
    assert replayed.peer_net_return == pytest.approx(changed.peer_net_return)
    assert replayed.net_excess_return == pytest.approx(replayed.net_return - replayed.peer_net_return)


def test_missing_or_nonpositive_risk_is_not_fabricated() -> None:
    asset = _confirmed_universe()[4]
    missing = _entry_risk_context(asset, 17, 19, asset.bars[19].adjusted_open)
    assert missing["unavailable_reason"] == "entry_risk_inputs_unavailable"
    assert "risk_unit" not in missing
    bars = tuple(
        replace(bar, adjusted_high=100.0, adjusted_low=bar.adjusted_low if index == 179 else 0.01)
        for index, bar in enumerate(asset.bars)
    )
    invalid = _entry_risk_context(replace(asset, bars=bars), 179, 181, asset.bars[181].adjusted_open)
    assert invalid["unavailable_reason"] == "entry_risk_nonpositive"
    assert "initial_stop" not in invalid
    event = evaluate_historical_signal_date(_confirmed_universe(), asset.bars[179].trade_date)[0]
    unavailable = replay_historical_exit_policy(
        (replace(asset, bars=asset.bars[170:]),), (event,), take_profit_return=0.99
    )[0]
    assert unavailable.trade_status == "entry_risk_unavailable"
    assert unavailable.net_return is None
    assert unavailable.entry_price is None
    assert unavailable.research_context["unavailable_reason"] == "entry_risk_inputs_unavailable"


def test_old_completed_events_cannot_be_relabelled_as_current_summary() -> None:
    event = evaluate_historical_signal_date(_confirmed_universe(), date(2025, 6, 29))[0]
    old = replace(event, research_context=None, net_return=event.gross_return)
    with pytest.raises(LeaderHistoricalBacktestContractError, match="risk/cost identity"):
        summarize_historical_events((old,))
    report = _report()
    report["sample_events"] = [asdict(old)]
    report["manifest_hash"] = stable_contract_hash({key: value for key, value in report.items() if key != "manifest_hash"})
    with pytest.raises(LeaderHistoricalBacktestContractError, match="risk/cost identity"):
        build_historical_backtest_evidence(report, code_version="test")


def test_sealed_synthetic_fixture_preserves_old_results_and_reproduces_new_results() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/leader_historical_lifecycle_v2.json").read_text())
    assert fixture["fixture_hash"] == stable_contract_hash({
        key: value for key, value in fixture.items() if key != "fixture_hash"
    })
    assets = _confirmed_universe()
    assert fixture["input_hash"] == stable_contract_hash([asdict(asset) for asset in assets])
    assert fixture["legacy_contract_hash"] == LEGACY_HISTORICAL_BACKTEST_CONTRACT_HASH
    assert stable_contract_hash(fixture["legacy_contract"]) == fixture["legacy_contract_hash"]
    assert fixture["legacy_contract_hash"] != HISTORICAL_BACKTEST_CONTRACT_HASH
    assert fixture["current_contract_hash"] == HISTORICAL_BACKTEST_CONTRACT_HASH
    events = evaluate_historical_signal_date(assets, date.fromisoformat(fixture["signal_date"]))
    assert json.loads(json.dumps([asdict(event) for event in events], default=str)) == fixture["current_events"]
    assert list(summarize_historical_events(events)) == fixture["current_aggregates"]
    old, new = fixture["legacy_events"][0], fixture["current_events"][0]
    assert old["entry_price"] == new["entry_price"]
    assert old["gross_return"] == new["gross_return"]
    assert old["net_return"] > new["net_return"]
    assert fixture["legacy_risk_inputs"]["atr_as_of_date"] != new["research_context"]["atr_as_of_date"]
    assert fixture["current_aggregates"][2]["event_count"] == 0
    assert fixture["current_aggregates"][2]["mean_net_return"] is None
    assert not any(fixture["promotion_gate_credit"].values())


def test_take_profit_replay_suppresses_next_three_trading_session_candidates() -> None:
    start = date(2026, 1, 1)
    bars = tuple(
        HistoricalLeaderBar(
            trade_date=start + timedelta(days=index),
            adjusted_open=10.2 if index == 24 else 10.0,
            adjusted_high=10.9 if index == 23 else 10.5,
            adjusted_low=9.5,
            adjusted_close=(10.1 if index == 22 else 10.4 if index >= 23 else 10.0),
            volume=1_000_000.0,
            turnover=10_000_000.0,
        )
        for index in range(40)
    )
    asset = HistoricalLeaderAsset(
        asset_code="510300",
        name="沪深300ETF",
        peer_group="宽基",
        clone_group="510300",
        baseline_score=50.0,
        bars=bars,
    )

    def event(signal_index: int) -> HistoricalLeaderEvent:
        entry_index = signal_index + 2
        return HistoricalLeaderEvent(
            signal_date=bars[signal_index].trade_date,
            candidate_id="leader_breakout_proxy_v1",
            asset_code=asset.asset_code,
            name=asset.name,
            peer_group=asset.peer_group,
            score=0.9,
            feature_hash=f"{signal_index:064d}",
            trade_status="closed",
            confirmation_date=bars[signal_index + 1].trade_date,
            entry_date=bars[entry_index].trade_date,
            entry_price=bars[entry_index].adjusted_open,
            exit_signal_date=None,
            exit_date=None,
            exit_price=None,
            exit_reason=None,
            holding_sessions=None,
            gross_return=None,
            net_return=None,
            peer_net_return=None,
            net_excess_return=None,
            entry_quality_state="disciplined",
            entry_quality_reason_codes=(),
            next_session_confirmation_state="confirmed",
            next_session_confirmation_reason_codes=(),
        )

    replayed = replay_historical_exit_policy(
        (asset,),
        tuple(event(index) for index in (20, 25, 26, 27, 28)),
        take_profit_return=0.03,
        cooldown_sessions=3,
    )

    assert replayed[0].exit_reason == "leader_tactics_take_profit"
    assert [item.trade_status for item in replayed[1:4]] == [
        "cooldown_suppressed",
        "cooldown_suppressed",
        "cooldown_suppressed",
    ]
    assert replayed[4].trade_status != "cooldown_suppressed"


def _report() -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
        "report_kind": LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
        "experiment_family": LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
        "evidence_mode": LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
        "status": "insufficient_data",
        "unavailable_reason": LEADER_HISTORICAL_BACKTEST_NOT_PIT,
        "contract_hash": HISTORICAL_BACKTEST_CONTRACT_HASH,
        **{key: HISTORICAL_BACKTEST_CONTRACT[key] for key in (
            "execution_contract_hash", "risk_contract_hash", "cost_contract_hash"
        )},
        "source_ranking_contract_hash": "a" * 64,
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "coverage": {"event_count": 10},
        "exclusion_counts": {},
        "aggregates": [],
        "sample_events": [],
        "promotion_gate_credit": {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        },
        "limitations": ["current-vintage membership"],
        "research_only": True,
        "production_mutation_allowed": False,
    }
    report["manifest_hash"] = stable_contract_hash(report)
    return report


def test_historical_backtest_evidence_keeps_formal_credit_at_zero() -> None:
    evidence = build_historical_backtest_evidence(_report(), code_version="test")

    evidence.validate()
    assert evidence.promotion.passed is False
    assert evidence.promotion.production_mutation_allowed is False
    assert evidence.report["promotion_gate_credit"] == {
        "eligible_pit_sessions": 0,
        "independent_primary_dates": 0,
        "walk_forward_folds": 0,
    }


def test_historical_backtest_rejects_forged_pit_credit() -> None:
    report = _report()
    report["promotion_gate_credit"] = {
        "eligible_pit_sessions": 1,
        "independent_primary_dates": 0,
        "walk_forward_folds": 0,
    }
    report["manifest_hash"] = stable_contract_hash(
        {key: value for key, value in report.items() if key != "manifest_hash"}
    )

    with pytest.raises(
        LeaderHistoricalBacktestContractError, match="zero PIT promotion credit"
    ):
        build_historical_backtest_evidence(report, code_version="test")
