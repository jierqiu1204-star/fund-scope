from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    HISTORICAL_BACKTEST_CONTRACT_HASH,
    LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
    LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_BACKTEST_NOT_PIT,
    LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
    LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
    ROUND_TRIP_COST_RATE,
    HistoricalLeaderAsset,
    HistoricalLeaderBar,
    LeaderHistoricalBacktestContractError,
    build_historical_backtest_evidence,
    eligible_historical_signal_dates,
    evaluate_historical_signal_date,
    summarize_historical_events,
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


def test_rolling_backtest_selects_at_t_and_executes_at_t_plus_one() -> None:
    assets = _universe()
    signal_date = assets[0].bars[179].trade_date

    events = evaluate_historical_signal_date(assets, signal_date)

    target = [
        item
        for item in events
        if item.asset_code == "04" and item.candidate_id == "leader_breakout_proxy_v1"
    ]
    assert {item.horizon_sessions for item in target} == {1, 3, 5, 10, 20}
    one_day = next(item for item in target if item.horizon_sessions == 1)
    assert one_day.entry_date == assets[4].bars[180].trade_date
    assert one_day.exit_date == assets[4].bars[181].trade_date
    assert one_day.net_return == pytest.approx(
        one_day.gross_return - ROUND_TRIP_COST_RATE
    )


def test_future_prices_cannot_change_signal_identity_but_change_outcome() -> None:
    assets = _universe()
    signal_date = assets[0].bars[179].trade_date
    original = evaluate_historical_signal_date(assets, signal_date)
    target = assets[4]
    changed_bars = list(target.bars)
    for index in range(180, len(changed_bars)):
        bar = changed_bars[index]
        changed_close = bar.adjusted_close * max(
            0.5, 1.0 - 0.02 * (index - 179)
        )
        changed_bars[index] = replace(
            bar,
            adjusted_open=changed_close,
            adjusted_high=changed_close * 1.01,
            adjusted_low=changed_close * 0.99,
            adjusted_close=changed_close,
        )
    changed_assets = (*assets[:4], replace(target, bars=tuple(changed_bars)), *assets[5:])

    changed = evaluate_historical_signal_date(changed_assets, signal_date)

    original_event = next(
        item
        for item in original
        if item.asset_code == "04" and item.horizon_sessions == 5
    )
    changed_event = next(
        item
        for item in changed
        if item.asset_code == "04" and item.horizon_sessions == 5
    )
    assert original_event.feature_hash == changed_event.feature_hash
    assert original_event.score == changed_event.score
    assert original_event.net_return != changed_event.net_return


def test_eligible_dates_require_trailing_180_and_complete_20_day_outcome() -> None:
    assets = _universe()
    dates = eligible_historical_signal_dates(assets)

    assert dates[0] == assets[0].bars[179].trade_date
    assert dates[-1] == assets[0].bars[183].trade_date


def test_asset_page_merge_order_cannot_change_results() -> None:
    assets = _universe()
    signal_date = assets[0].bars[179].trade_date

    canonical = evaluate_historical_signal_date(assets, signal_date)
    reversed_pages = evaluate_historical_signal_date(
        tuple(reversed(assets)), signal_date
    )

    assert canonical == reversed_pages


def test_summary_reports_costed_return_excess_and_event_series_drawdown() -> None:
    events = evaluate_historical_signal_date(_universe(), date(2025, 6, 29))
    summary = summarize_historical_events(events)

    row = next(
        item
        for item in summary
        if item["candidate_id"] == "all_leader_candidates"
        and item["horizon_sessions"] == 5
    )
    assert row["event_count"] > 0
    assert row["signal_date_count"] == 1
    assert row["mean_net_return"] is not None
    assert row["mean_net_excess_return"] is not None
    assert row["event_series_max_drawdown"] is not None


def _report() -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
        "report_kind": LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
        "experiment_family": LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
        "evidence_mode": LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
        "status": "insufficient_data",
        "unavailable_reason": LEADER_HISTORICAL_BACKTEST_NOT_PIT,
        "contract_hash": HISTORICAL_BACKTEST_CONTRACT_HASH,
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
