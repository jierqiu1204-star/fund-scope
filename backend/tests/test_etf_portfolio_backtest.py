from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import (
    EtfIntradayQuote,
    EtfPriceHistory,
    TrackedPosition,
    TradableEtf,
)
from app.services.short_research.backtest import (
    ReplayPosition,
    _comparison_target_weights,
    _first_execution_quote_after,
    _generate_target_weights,
    _risk_action,
    backtest_summary_payload,
    run_etf_portfolio_backtest,
)
from app.services.short_research.service import (
    ComputedAsset,
    PricePoint,
    compute_asset_for_replay_from_series,
)


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
    conclusion: str = "短线观察",
    entry: str = "健康回踩",
    score: float = 85.0,
    flags: list[str] | None = None,
) -> ComputedAsset:
    metadata = _metadata(code)
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
                daily = 0.001 + (index % 4) * 0.0002
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
                            change_percent=(price_multiplier - 1) * 100,
                            volume=1_000_000,
                            turnover=80_000_000,
                            source="test_intraday",
                            freshness_status="historical_replay",
                        )
                    )
        await session.commit()


@pytest.mark.asyncio
async def test_etf_portfolio_backtest_writes_only_backtest_tables(app) -> None:
    codes = [f"51{index:04d}" for index in range(20)]
    await _seed_backtest_etfs(app, codes=codes, days=150)

    async with app.state.db.session() as session:
        tracked_before = await session.scalar(select(func.count()).select_from(TrackedPosition))
        run = await run_etf_portfolio_backtest(
            session,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 5, 30),
            days=90,
            max_assets=20,
        )
        tracked_after = await session.scalar(select(func.count()).select_from(TrackedPosition))

    assert tracked_before == tracked_after
    assert run.status == "success"
    assert run.metrics_json["trade_count"] > 0
    assert run.data_coverage_json["trading_days"] >= 30


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
    assert created_body["evidence_status"] == "同源已验证"

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
    weights, mode, context = _generate_target_weights([_computed_asset("510300"), _computed_asset("512880")])

    assert weights
    assert mode == "risk_on"
    assert round(sum(weights.values()), 4) == 0.6
    assert context["cash_weight"] == 0.4
    assert "候选" in context["cash_reason"]


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
    assets = [_computed_asset(code) for code in ("510300", "512880", "513520", "588220")]
    for asset, theme in zip(assets, ("宽基", "金融", "跨境", "科技"), strict=False):
        asset.metrics["theme_profile"] = {"theme_group": theme}

    optimized_weights, optimized_mode = _comparison_target_weights("optimized_min_volatility", assets)
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


def test_intraday_alert_execution_uses_first_quote_after_delay() -> None:
    signal_time = datetime(2026, 6, 30, 10, 0, 0)
    quotes = [
        EtfIntradayQuote(etf_code="513520", quote_time=signal_time, trade_date=signal_time.date(), latest_price=2.50),
        EtfIntradayQuote(
            etf_code="513520",
            quote_time=signal_time + timedelta(minutes=2),
            trade_date=signal_time.date(),
            latest_price=2.48,
        ),
        EtfIntradayQuote(
            etf_code="513520",
            quote_time=signal_time + timedelta(minutes=4),
            trade_date=signal_time.date(),
            latest_price=2.46,
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
