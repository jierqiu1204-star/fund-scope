from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import (
    EtfIntradayQuote,
    EtfPortfolioBacktestTrade,
    EtfPriceHistory,
    TrackedPosition,
    TradableEtf,
)
from app.services.short_research import backtest as backtest_service
from app.services.short_research.backtest import (
    ReplayPosition,
    _comparison_target_weights,
    _daily_execution_resolution,
    _execution_cost_evidence,
    _first_execution_quote_after,
    _generate_target_weights,
    _risk_action,
    backtest_summary_payload,
    run_etf_portfolio_backtest,
    run_etf_strategy_comparison_backtest,
)
from app.services.short_research.service import (
    ComputedAsset,
    PricePoint,
    compute_asset_for_replay_from_series,
)
from app.services.strategy_lab.etf_action_replay import DailyExecutionBar, ExecutionStatus


def _metadata(code: str, *, asset_class: str = "sector", tags: tuple[str, ...] = ("科技",)) -> ShortResearchAsset:
    return ShortResearchAsset(
        ASSET_TYPE_ETF,
        code,
        f"回测ETF{code}",
        asset_class,
        tags,
        "回测测试 ETF",
        "证券账户 T+1 ETF",
        exchange="SH",
    )


def _computed_asset(
    code: str,
    *,
    asset_class: str = "sector",
    conclusion: str = "短线观察",
    entry: str = "健康回踩",
    score: float = 85.0,
    flags: list[str] | None = None,
) -> ComputedAsset:
    metadata = _metadata(code, asset_class=asset_class)
    metrics = {
        "return_5d": 0.02,
        "return_10d": 0.03,
        "return_20d": 0.05,
        "return_60d": 0.08,
        "volatility_20d": 0.018,
        "max_drawdown_60d": -0.04,
        "average_turnover_20d": 180_000_000,
        "today_return_pct": -0.004,
        "ma5": 1.02,
        "ma10": 1.01,
        "ma20": 1.0,
        "distance_to_ma5_pct": 0.0,
        "distance_to_ma10_pct": 0.0,
        "pullback_from_5d_high_pct": -0.01,
        "pullback_from_20d_high_pct": -0.02,
        "volume_ratio_20d": 1.0,
        "entry_timing_label": entry,
        "entry_timing_reason": "测试买点",
        "theme_profile": {"primary_theme": "科技", "theme_group": "科技"},
        "dynamic_threshold_context": {},
        "default_display_eligible": True,
    }
    return ComputedAsset(
        metadata=metadata,
        rank=None,
        total_score=score,
        conclusion=conclusion,
        latest_date=date(2026, 6, 1),
        latest_value=1.0,
        usable_days=80,
        sample_level="测试样本",
        metrics=metrics,
        score_breakdown={"trend": {"score": score}, "risk": {"score": 90}, "liquidity": {"score": 90}},
        risk_flags=flags or [],
        rationale={},
        source_note="测试",
        entry_timing_label=entry,
        entry_timing_reason="测试买点",
    )


def _risk_return_maps(codes: list[str], *, days: int = 60) -> dict[str, dict[date, float]]:
    start = date(2026, 1, 1)
    return {
        code: {
            start + timedelta(days=offset): ((offset * (index + 2)) % 17 - 8) / 10000
            for offset in range(days)
        }
        for index, code in enumerate(codes)
    }


async def _seed_backtest_etfs(app, *, codes: list[str], days: int = 120, future_spike: bool = False) -> None:
    start = date(2026, 1, 1)
    async with app.state.db.session() as session:
        for index, code in enumerate(codes):
            session.add(
                TradableEtf(
                    code=code,
                    name=f"回测ETF{code}",
                    exchange="SH",
                    theme_tags_json=["回测", f"主题{index % 3}"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index" if index % 2 == 0 else "sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            close = 1.0 + index * 0.02
            for offset in range(days):
                current = start + timedelta(days=offset)
                daily = (
                    0.001
                    + (index % 4) * 0.0002
                    + (((offset * (index + 2)) % 11) - 5) * 0.00002
                )
                close = close * (1 + daily)
                if future_spike and offset == days - 1:
                    close *= 5
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=current,
                        open=close * 0.99,
                        high=close * 1.01,
                        low=close * 0.98,
                        close=close,
                        volume=2_000_000,
                        turnover=180_000_000,
                        pct_change=daily * 100,
                        research_adjusted_value=close,
                        research_price_basis="total_return_adjusted",
                        data_provider="fixture",
                        provider_version="fixture-v1",
                        source_timestamp=datetime.combine(current, datetime.min.time()),
                        adjustment_version="fixture-total-return-v1",
                        decision_eligible=True,
                    )
                )
        await session.commit()


async def _seed_intraday_quotes(app, *, codes: list[str], start_date: date, days: int = 20) -> None:
    async with app.state.db.session() as session:
        for index, code in enumerate(codes):
            base = 1.0 + index * 0.02
            for offset in range(days):
                current = start_date + timedelta(days=offset)
                day_price = base * (1 + offset * 0.001)
                for minute, price_multiplier in ((31, 1.0), (34, 0.995), (38, 0.99)):
                    quote_time = datetime(current.year, current.month, current.day, 9, minute, 0)
                    session.add(
                        EtfIntradayQuote(
                            etf_code=code,
                            trade_date=current,
                            quote_time=quote_time,
                            latest_price=day_price * price_multiplier,
                            bid_price=day_price * price_multiplier * 0.999,
                            ask_price=day_price * price_multiplier * 1.001,
                            change_percent=(price_multiplier - 1) * 100,
                            volume=1_000_000,
                            turnover=80_000_000,
                            source="test_intraday",
                            freshness_status="historical_replay",
                            raw_json={
                                "decision_eligible": True,
                                "consensus_status": "consistent",
                                "provider_count": 2,
                            },
                        )
                    )
        await session.commit()


@pytest.mark.asyncio
async def test_etf_portfolio_backtest_writes_only_backtest_tables(app, monkeypatch) -> None:
    codes = [f"51{index:04d}" for index in range(20)]
    await _seed_backtest_etfs(app, codes=codes, days=150)
    monkeypatch.setattr(
        backtest_service,
        "_generate_target_weights",
        lambda assets, **_kwargs: (
            {asset.metadata.code: 0.2 for asset in assets[:4]},
            "risk_on",
            {"cash_weight": 0.2, "cash_reason": "frozen_execution_fixture"},
        ),
    )

    async with app.state.db.session() as session:
        tracked_before = await session.scalar(select(func.count()).select_from(TrackedPosition))
        run = await run_etf_portfolio_backtest(
            session,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 5, 30),
            days=90,
            max_assets=20,
        )
        first_trade = await session.scalar(
            select(EtfPortfolioBacktestTrade)
            .where(EtfPortfolioBacktestTrade.run_id == run.id)
            .order_by(EtfPortfolioBacktestTrade.trade_date.asc(), EtfPortfolioBacktestTrade.id.asc())
            .limit(1)
        )
        tracked_after = await session.scalar(select(func.count()).select_from(TrackedPosition))

    assert tracked_before == tracked_after
    assert run.status == "success", run.error_message
    assert run.metrics_json["trade_count"] > 0, {
        "metrics": run.metrics_json,
        "coverage": run.data_coverage_json,
    }
    assert run.data_coverage_json["trading_days"] >= 30
    assert first_trade is not None
    execution = first_trade.metadata_json["execution_evidence"]
    assert first_trade.trade_date > date.fromisoformat(execution["signal_date"])
    assert execution["price_basis"] == "total_return_adjusted_open"
    assert execution["fee_bps_per_side"] == 5
    assert execution["base_slippage_bps_per_side"] == 5
    assert execution["stress_slippage_bps_per_side"] == 20
    assert execution["simulated_not_observed"] is True
    assert first_trade.shares % 100 == 0


@pytest.mark.asyncio
async def test_etf_portfolio_backtest_api_create_list_and_detail(client, app) -> None:
    codes = [f"52{index:04d}" for index in range(20)]
    await _seed_backtest_etfs(app, codes=codes, days=150)

    created = await client.post(
        "/api/short-research/etf-backtests",
        json={
            "start_date": "2026-03-01",
            "end_date": "2026-05-30",
            "days": 90,
            "max_assets": 20,
            "fee_rate": 0.001,
        },
    )

    assert created.status_code == 200
    created_body = created.json()
    assert created_body["status"] == "success"
    assert created_body["metrics"]["trade_count"] > 0
    assert created_body["replay_contract"]["contract_hash"]
    assert created_body["evidence_status"] == "旧口径结果"
    assert created_body["research_only"] is True
    assert created_body["promotion_eligible"] is False
    assert created_body["action_evidence"]["policy_semantics"] == "legacy_current_position"
    risk_shadow = created_body["metrics"]["portfolio_risk_budget"][
        "portfolio_risk_shadow_v1"
    ]
    assert risk_shadow["contract_version"] == "portfolio_risk_shadow_v1"
    assert risk_shadow["marginal_risk_contribution"]["metrics"]["asset_count"] <= 20

    listed = await client.get("/api/short-research/etf-backtests?limit=5")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["items"]
    assert listed_body["items"][0]["id"] == created_body["id"]

    detail = await client.get(f"/api/short-research/etf-backtests/{created_body['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["id"] == created_body["id"]
    assert detail_body["equity_curve"]
    assert detail_body["trades"]
    assert detail_body["label_summaries"]


@pytest.mark.asyncio
async def test_etf_strategy_comparison_includes_exit_v2_evidence(app) -> None:
    codes = [f"57{index:04d}" for index in range(24)]
    await _seed_backtest_etfs(app, codes=codes, days=150)

    async with app.state.db.session() as session:
        run = await run_etf_strategy_comparison_backtest(session, days=120, max_assets=80)

    assert run.status == "success", run.error_message
    metrics = run.metrics_json or {}
    baselines = metrics["exit_v2_baseline_comparison"]["baselines"]

    assert {"topn_fixed_hold", "current_live_exit_rules", "guard_only", "exit_v2_reentry"} <= set(baselines)
    assert run.config_json["exit_v2_evidence_contract"]["research_only"] is True
    assert run.config_json["exit_action_version"]
    assert run.config_json["reentry_rule_version"]
    for strategy in metrics["strategies"]:
        strategy_metrics = strategy["metrics"]
        assert "missed_upside_rate" in strategy_metrics
        assert "protection_success_rate" in strategy_metrics
        assert "reentry_count" in strategy_metrics
        assert "alert_count" in strategy_metrics


@pytest.mark.asyncio
async def test_etf_intraday_alert_backtest_api_uses_intraday_execution_model(client, app) -> None:
    codes = [f"53{index:04d}" for index in range(20)]
    await _seed_backtest_etfs(app, codes=codes, days=150)
    await _seed_intraday_quotes(app, codes=codes, start_date=date(2026, 3, 1), days=25)

    created = await client.post(
        "/api/short-research/etf-backtests",
        json={
            "start_date": "2026-03-01",
            "end_date": "2026-03-25",
            "days": 90,
            "max_assets": 20,
            "fee_rate": 0.001,
            "execution_model": "intraday_alert",
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "success"
    assert body["execution_model"] == "intraday_alert_v1"
    assert body["replay_contract"]["execution_model"] == "intraday_alert_v1"
    assert body["data_coverage"]["intraday_coverage"]["quote_count"] > 0
    assert "盘中" in " ".join(body["caveats"])


def test_backtest_portfolio_partially_allocates_when_candidates_are_insufficient() -> None:
    assets = [
        _computed_asset("512880"),
        _computed_asset("513520"),
        _computed_asset("510300", asset_class="broad_index", conclusion="谨慎观察", entry="冲高别追"),
        _computed_asset("510500", asset_class="broad_index", conclusion="谨慎观察", entry="冲高别追"),
    ]
    weights, mode, context = _generate_target_weights(
        assets,
        return_maps=_risk_return_maps([asset.metadata.code for asset in assets]),
    )

    assert weights
    assert mode == "risk_on"
    assert round(sum(weights.values()), 4) == 0.6
    assert context["cash_weight"] == 0.4
    assert "风险预算" in context["cash_reason"]
    assert context["risk_summary"]["common_sample_count"] == 60


def test_backtest_portfolio_cash_wait_when_no_candidate_passes_filters() -> None:
    weights, mode, context = _generate_target_weights(
        [
            _computed_asset("510300", conclusion="高位观察", entry="冲高别追"),
            _computed_asset("512880", conclusion="短线观察", entry="数据不足", flags=["数据滞后"]),
        ]
    )

    assert weights == {}
    assert mode == "cash_wait"
    assert context["cash_weight"] == 1.0


def test_strategy_comparison_optimized_and_equal_weight_are_independent() -> None:
    assets = [
        _computed_asset("510300", asset_class="broad_index"),
        _computed_asset("510500", asset_class="broad_index"),
        _computed_asset("513520"),
        _computed_asset("588220"),
    ]
    for asset, theme in zip(assets, ("宽基", "金融", "跨境", "科技"), strict=False):
        asset.metrics["theme_profile"] = {"theme_group": theme}
    for index, asset in enumerate(assets, start=1):
        asset.metrics["volatility_20d"] = 0.01 * index
    return_maps = _risk_return_maps([asset.metadata.code for asset in assets])

    optimized_weights, optimized_mode = _comparison_target_weights(
        "optimized_min_volatility",
        assets,
        return_maps=return_maps,
    )
    equal_weights, equal_mode = _comparison_target_weights("equal_weight_benchmark", assets)

    assert optimized_mode == "risk_on"
    assert equal_mode == "risk_on"
    assert optimized_weights
    assert equal_weights
    assert optimized_weights != equal_weights
    assert max(optimized_weights.values()) <= 0.3


def test_backtest_summary_marks_old_method_when_contract_is_missing() -> None:
    run = type(
        "Run",
        (),
        {
            "id": 1,
            "status": "success",
            "started_at": datetime(2026, 6, 1, 10, 0),
            "finished_at": datetime(2026, 6, 1, 10, 1),
            "start_date": date(2026, 5, 1),
            "end_date": date(2026, 6, 1),
            "initial_cash": 10000.0,
            "fee_rate": 0.001,
            "metrics_json": {},
            "benchmark_json": {},
            "data_coverage_json": {},
            "caveats_json": [],
            "config_json": {},
            "error_message": None,
        },
    )()

    payload = backtest_summary_payload(run)

    assert payload["evidence_status"] == "旧口径结果"
    assert payload["execution_model"] is None


def test_backtest_trailing_take_profit_daily_action() -> None:
    asset = _computed_asset("513520")
    position = ReplayPosition(
        code="513520",
        name="日经ETF",
        shares=1000,
        avg_cost=1.0,
        entry_date=date(2026, 6, 1),
        max_profit_pct=4.8,
    )

    alert_type, fraction, context = _risk_action(position, asset, 1.015)

    assert alert_type == "trailing_take_profit"
    assert fraction == 0.5
    assert context["profit_giveback_pct"] > context["trailing_giveback_pct"]


def test_backtest_ma5_close_break_is_daily_close_only_and_full_exit() -> None:
    asset = _computed_asset("513520")
    position = ReplayPosition(
        code="513520",
        name="日经ETF",
        shares=1000,
        avg_cost=1.0,
        entry_date=date(2026, 6, 1),
    )

    intraday_alert_type, _, intraday_context = _risk_action(position, asset, 1.0)
    daily_alert_type, daily_fraction, daily_context = _risk_action(
        position, asset, 1.0, close_only=True
    )

    assert intraday_alert_type is None
    assert intraday_context["ma5_close_break_condition_met"] is False
    assert daily_alert_type == "ma5_close_break_exit"
    assert daily_fraction == 1.0
    assert daily_context["ma5_close_break_price_basis"] == "total_return_adjusted"
    assert daily_context["ma5_close_break_intraday_trigger_allowed"] is False


def test_backtest_data_insufficient_is_frozen_not_exit_watch() -> None:
    position = ReplayPosition(
        code="513520",
        name="日经ETF",
        shares=1000,
        avg_cost=1.0,
        entry_date=date(2026, 6, 1),
    )

    alert_type, fraction, context = _risk_action(
        position,
        _computed_asset("513520", conclusion="数据不足"),
        0.95,
    )

    assert alert_type is None
    assert fraction == 0.0
    assert context["data_state"] == "data_waiting"
    assert context["reason_code"] == "ranking_data_insufficient"


def test_legacy_current_semantics_fixture_is_research_only_and_non_promotable() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "etf_alert_action" / "legacy_current_semantics_v1.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "etf-alert-action-diagnostic-v1"
    assert payload["policy_key"] == "legacy_current_semantics"
    assert [step["remaining_shares"] for step in payload["relative_reduction_path"]] == [500.0, 250.0, 125.0]
    assert payload["final_remaining_fraction"] == 0.125
    assert payload["research_only"] is True
    assert payload["promotion_eligible"] is False


def test_intraday_alert_execution_uses_first_quote_after_delay() -> None:
    signal_time = datetime(2026, 6, 30, 10, 0, 0)
    quotes = [
        EtfIntradayQuote(
            etf_code="513520",
            quote_time=signal_time,
            trade_date=signal_time.date(),
            latest_price=2.50,
            bid_price=2.49,
            ask_price=2.51,
            volume=1_000,
            freshness_status="historical_replay",
            raw_json={"decision_eligible": True, "consensus_status": "consistent"},
        ),
        EtfIntradayQuote(
            etf_code="513520",
            quote_time=signal_time + timedelta(minutes=2),
            trade_date=signal_time.date(),
            latest_price=2.48,
            bid_price=2.47,
            ask_price=2.49,
            volume=1_000,
            freshness_status="historical_replay",
            raw_json={"decision_eligible": True, "consensus_status": "consistent"},
        ),
        EtfIntradayQuote(
            etf_code="513520",
            quote_time=signal_time + timedelta(minutes=4),
            trade_date=signal_time.date(),
            latest_price=2.46,
            bid_price=2.45,
            ask_price=2.47,
            volume=1_000,
            freshness_status="historical_replay",
            raw_json={"decision_eligible": True, "consensus_status": "consistent"},
        ),
    ]

    execution_quote = _first_execution_quote_after(quotes, signal_time, delay_minutes=3)

    assert execution_quote is quotes[2]
    assert execution_quote.latest_price == 2.46


def test_intraday_alert_execution_does_not_fallback_without_later_quote() -> None:
    signal_time = datetime(2026, 6, 30, 14, 58, 0)
    quotes = [
        EtfIntradayQuote(etf_code="513520", quote_time=signal_time, trade_date=signal_time.date(), latest_price=2.50),
    ]

    assert _first_execution_quote_after(quotes, signal_time, delay_minutes=3) is None


def test_daily_execution_resolution_skips_signal_close_and_waits_for_eligible_open() -> None:
    signal_date = date(2026, 7, 1)
    resolution = _daily_execution_resolution(
        signal_date=signal_date,
        through_date=date(2026, 7, 4),
        bars=(
            DailyExecutionBar(signal_date, 99.0, 1.0, 1_000),
            DailyExecutionBar(date(2026, 7, 2), 10.0, 1.0, 0),
            DailyExecutionBar(date(2026, 7, 3), None, 1.0, 1_000),
            DailyExecutionBar(date(2026, 7, 4), 10.5, 1.2, 1_000),
        ),
    )

    assert resolution.status is ExecutionStatus.FILLED
    assert resolution.fill is not None
    assert resolution.fill.session_date == date(2026, 7, 4)
    assert resolution.fill.adjusted_open == pytest.approx(12.6)
    assert resolution.fill.signal_to_fill_trading_sessions == 3
    assert [item.reason for item in resolution.deferred_sessions] == [
        "zero_or_missing_volume",
        "missing_or_invalid_open",
    ]


def test_execution_cost_evidence_separates_base_and_stress() -> None:
    evidence = _execution_cost_evidence(
        side="buy",
        signal_date=date(2026, 7, 1),
        fill_date=date(2026, 7, 2),
        signal_price=10.0,
        reference_price=11.0,
        shares=100.0,
        signal_to_fill_sessions=1,
        deferred_reasons=(),
        price_basis="total_return_adjusted_open",
    )

    assert evidence["simulated_not_observed"] is True
    assert evidence["signal_to_fill_gap_return"] == pytest.approx(0.1)
    assert evidence["spread_cost"] is None
    assert evidence["spread_evidence"] == "modeled_sensitivity_not_observed"
    assert evidence["base"]["fee"] == pytest.approx(0.55)
    assert evidence["base"]["slippage_cost"] == pytest.approx(0.55)
    assert evidence["stress"]["slippage_cost"] == pytest.approx(2.2)
    assert evidence["stress"]["total_cost"] > evidence["base"]["total_cost"]


def test_daily_buy_is_capped_by_pit_turnover_capacity() -> None:
    positions: dict[str, ReplayPosition] = {}
    order = backtest_service.PendingDailyOrder(
        order_id=1,
        signal_date=date(2026, 7, 1),
        code="513520",
        name="日经ETF",
        side="buy",
        reason="rebalance_buy",
        signal_price=10.0,
        requested_amount=5_000.0,
    )

    attempt = backtest_service._attempt_daily_order(
        order,
        through_date=date(2026, 7, 2),
        bars=(
            DailyExecutionBar(
                date(2026, 7, 2),
                10.0,
                1.0,
                100_000,
                median_turnover_20d=200_000.0,
            ),
        ),
        positions=positions,
        metadata=_metadata("513520"),
        cash=10_000.0,
    )

    assert attempt.status is ExecutionStatus.FILLED
    assert attempt.trade is not None
    assert attempt.trade.amount <= 2_000.0
    capacity = (attempt.execution_evidence or {})["liquidity_capacity_stress"]
    assert capacity["status"] == "capped"
    assert capacity["fill_capped"] is True
    assert capacity["entry_capacity_exceeded"] is True
    assert capacity["requested_notional"] == 5_000.0


def test_daily_exit_is_partial_when_pit_turnover_cannot_support_full_order() -> None:
    positions = {
        "513520": ReplayPosition(
            code="513520",
            name="日经ETF",
            shares=1_000.0,
            avg_cost=9.0,
            entry_date=date(2026, 6, 1),
        )
    }
    order = backtest_service.PendingDailyOrder(
        order_id=2,
        signal_date=date(2026, 7, 1),
        code="513520",
        name="日经ETF",
        side="sell",
        reason="hard_stop",
        signal_price=10.0,
        requested_fraction=1.0,
    )

    attempt = backtest_service._attempt_daily_order(
        order,
        through_date=date(2026, 7, 2),
        bars=(
            DailyExecutionBar(
                date(2026, 7, 2),
                10.0,
                1.0,
                100_000,
                median_turnover_20d=100_000.0,
            ),
        ),
        positions=positions,
        metadata=_metadata("513520"),
        cash=0.0,
    )

    assert attempt.status is ExecutionStatus.FILLED
    assert attempt.trade is not None
    assert attempt.trade.shares == 500.0
    assert positions["513520"].shares == 500.0
    capacity = (attempt.execution_evidence or {})["liquidity_capacity_stress"]
    assert capacity["status"] == "capped"
    assert capacity["normal_exit_days"] == pytest.approx(2.0)
    assert capacity["unfilled_notional"] == pytest.approx(5_000.0)


def test_daily_entry_is_excluded_when_pit_turnover_capacity_is_unavailable() -> None:
    order = backtest_service.PendingDailyOrder(
        order_id=3,
        signal_date=date(2026, 7, 1),
        code="513520",
        name="日经ETF",
        side="buy",
        reason="rebalance_buy",
        signal_price=10.0,
        requested_amount=1_000.0,
    )

    attempt = backtest_service._attempt_daily_order(
        order,
        through_date=date(2026, 7, 2),
        bars=(DailyExecutionBar(date(2026, 7, 2), 10.0, 1.0, 100_000),),
        positions={},
        metadata=_metadata("513520"),
        cash=10_000.0,
    )

    assert attempt.status is ExecutionStatus.REJECTED
    assert attempt.reason == "liquidity_capacity_turnover_unavailable"


def test_intraday_execution_rejects_latest_price_without_explicit_eligible_book() -> None:
    signal_time = datetime(2026, 7, 1, 10, 0)
    display_only = EtfIntradayQuote(
        etf_code="513520",
        quote_time=signal_time + timedelta(minutes=4),
        trade_date=signal_time.date(),
        latest_price=2.46,
        volume=1_000,
        freshness_status="historical_replay",
        raw_json={},
    )

    assert _first_execution_quote_after([display_only], signal_time, delay_minutes=3, side="sell") is None


def test_replay_compute_uses_supplied_date_slice_not_future_price() -> None:
    metadata = _metadata("510300")
    start = date(2026, 1, 1)
    visible = [
        PricePoint(point_date=start + timedelta(days=offset), value=1.0 + offset * 0.01, close=1.0 + offset * 0.01, turnover=200_000_000)
        for offset in range(80)
    ]
    with_future = [*visible, PricePoint(point_date=start + timedelta(days=81), value=99.0, close=99.0, turnover=200_000_000)]

    replay_asset = compute_asset_for_replay_from_series(
        metadata,
        series=[item for item in with_future if item.point_date <= visible[-1].point_date],
        as_of_date=visible[-1].point_date,
    )

    assert replay_asset.latest_date == visible[-1].point_date
    assert replay_asset.latest_value == visible[-1].value
    assert replay_asset.latest_value != 99.0
