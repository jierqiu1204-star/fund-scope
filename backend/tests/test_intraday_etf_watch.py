from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfIntradayDailySummary,
    EtfIntradayQuote,
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    TradableEtf,
    User,
    utcnow,
)
from app.services.intraday_etf.jobs import intraday_etf_watch_job
from app.services.intraday_etf.service import (
    ASIA_SHANGHAI,
    MarketState,
    build_watchlist,
    current_market_state,
    is_quote_stale,
    latest_quotes_by_code,
    normalize_spot_record,
    summarize_and_cleanup_intraday_quotes,
)
from app.services.short_research.service import CONCLUSION_HIGH_WATCH, CONCLUSION_WATCH
from app.services.tracked_positions.service import (
    create_alert_if_needed,
    create_position,
    position_analysis,
)


def _etf(code: str, name: str | None = None) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=name or f"ETF{code}",
        exchange="SH",
        theme_tags_json=["test"],
        trading_rule_label="T+1",
        asset_class="equity",
        is_short_term_eligible=True,
        is_watchlist=True,
    )


async def _seed_signal_run(
    app,
    *,
    count: int = 25,
    conclusions: list[str] | None = None,
    total_scores: list[float] | None = None,
) -> int:
    conclusions = conclusions or [CONCLUSION_WATCH] * count
    total_scores = total_scores or [100.0 - i for i in range(count)]
    async with app.state.db.session() as session:
        session.add_all([_etf(f"51{i:04d}") for i in range(count)])
        run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 6, 12),
            config_json={"asset_type": "etf", "theme": None, "codes": [], "language": "research_only"},
            summary_json={"item_count": count, "etf_count": count},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=f"51{i:04d}",
                    rank=i + 1,
                    total_score=total_scores[i] if i < len(total_scores) else 100.0 - i,
                    conclusion=conclusions[i] if i < len(conclusions) else CONCLUSION_WATCH,
                    score_breakdown_json={},
                    risk_flags_json=[],
                    rationale_json={
                        "key_reason": "test",
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "日线趋势仍在。",
                    },
                    metrics_json={
                        "default_display_eligible": True,
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "日线趋势仍在。",
                    },
                )
                for i in range(count)
            ]
        )
        await session.commit()
        return run.id


async def _seed_price_history(app, code: str, *, start_price: float = 1.0) -> None:
    start = date(2026, 5, 11)
    async with app.state.db.session() as session:
        if await session.get(TradableEtf, code) is None:
            session.add(_etf(code))
            await session.flush()
        for offset in range(25):
            close = start_price * (1 + offset * 0.001)
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=10_000_000,
                    turnover=100_000_000,
                    pct_change=0.1,
                )
            )
        await session.commit()


async def _seed_price_history_from_closes(
    app,
    code: str,
    closes: list[float],
    *,
    start: date,
    turnover: float = 100_000_000,
) -> None:
    async with app.state.db.session() as session:
        if await session.get(TradableEtf, code) is None:
            session.add(_etf(code))
            await session.flush()
        for offset, close in enumerate(closes):
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close,
                    high=close * 1.004,
                    low=close * 0.996,
                    close=close,
                    volume=10_000_000,
                    turnover=turnover,
                    pct_change=0.0,
                )
            )
        await session.commit()


def test_current_market_state_uses_half_open_trading_sessions() -> None:
    assert current_market_state(datetime(2026, 6, 17, 11, 29, 59, tzinfo=ASIA_SHANGHAI)).status == "open"
    assert current_market_state(datetime(2026, 6, 17, 11, 30, 0, tzinfo=ASIA_SHANGHAI)).status == "closed"
    assert current_market_state(datetime(2026, 6, 17, 14, 59, 59, tzinfo=ASIA_SHANGHAI)).status == "open"
    assert current_market_state(datetime(2026, 6, 17, 15, 0, 0, tzinfo=ASIA_SHANGHAI)).status == "closed"


@pytest.mark.asyncio
async def test_scheduled_intraday_watch_skips_closed_market_without_fetching(app, monkeypatch) -> None:
    called = False

    def fake_fetcher() -> pd.DataFrame:
        nonlocal called
        called = True
        return pd.DataFrame()

    monkeypatch.setattr(
        "app.services.intraday_etf.jobs.current_market_state",
        lambda: MarketState("closed", None, datetime(2026, 6, 17, 11, 30, tzinfo=ASIA_SHANGHAI)),
    )

    async with app.state.db.session() as session:
        result = await intraday_etf_watch_job(session, run_type="scheduled", fetcher=fake_fetcher)

    assert result["status"] == "skipped"
    assert called is False


@pytest.mark.asyncio
async def test_watchlist_uses_all_eligible_etfs_and_marks_signal_sources(app) -> None:
    conclusions = [CONCLUSION_WATCH] * 22
    conclusions[20] = CONCLUSION_HIGH_WATCH
    conclusions[21] = CONCLUSION_WATCH
    await _seed_signal_run(app, count=22, conclusions=conclusions)
    async with app.state.db.session() as session:
        session.add(_etf("159998", "Eligible ETF"))
        session.add(_etf("159999", "Tracked ETF"))
        session.add(
            TrackedPosition(
                user_id=1,
                asset_type="etf",
                asset_code="159999",
                asset_name="Tracked ETF",
                buy_date=date(2026, 6, 12),
                buy_amount=3000,
                entry_price=1.0,
                entry_price_date=date(2026, 6, 12),
                estimated_shares=3000,
                status="active",
            )
        )
        await session.commit()

        watchlist = await build_watchlist(session)

    assert watchlist.message == "使用全部可交易 ETF 盘中行情；实时榜单优先显示最新评分前 20、短线观察、高位观察和已追踪 ETF。"
    assert len(watchlist.items) == 24
    assert [item.etf_code for item in watchlist.items[:3]] == ["510000", "510001", "510002"]
    assert watchlist.items[0].rank == 1
    assert watchlist.items[0].sources == {"all_etf", "top20_signal", "short_watch"}
    assert watchlist.items[19].rank == 20
    assert watchlist.items[20].etf_code == "510020"
    assert watchlist.items[20].rank == 21
    assert watchlist.items[20].sources == {"all_etf", "high_watch"}
    assert watchlist.items[21].etf_code == "510021"
    assert watchlist.items[21].sources == {"all_etf", "short_watch"}
    eligible = next(item for item in watchlist.items if item.etf_code == "159998")
    assert eligible.rank is None
    assert eligible.sources == {"all_etf"}
    tracked = next(item for item in watchlist.items if item.etf_code == "159999")
    assert tracked.rank is None
    assert tracked.sources == {"all_etf", "tracked_position"}
    assert watchlist.signal_status == "ready"


@pytest.mark.asyncio
async def test_live_rankings_order_and_rank_change(client, app, monkeypatch) -> None:
    await _seed_signal_run(
        app,
        count=3,
        conclusions=[CONCLUSION_WATCH, CONCLUSION_WATCH, CONCLUSION_HIGH_WATCH],
        total_scores=[60.0, 70.0, 80.0],
    )
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.0,
                change_percent=1.0,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code="510001",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.2,
                change_percent=3.0,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code="510002",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.4,
                change_percent=-1.0,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings?limit=2&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["watched_count"] == 3
    items = body["items"]
    assert [item["etf_code"] for item in items] == ["510002", "510001"]
    assert items[0]["live_rank"] == 1
    assert items[0]["base_rank"] == 3
    assert items[0]["rank_change"] == 2
    assert items[0]["score_source"] == "intraday"
    assert items[0]["intraday_adjustment_score"] is not None
    assert any("盘中涨跌" in reason for reason in items[0]["score_contribution_reasons"])
    assert items[0]["live_entry_timing_label"] == "健康回踩"
    assert items[1]["live_rank"] == 2
    assert items[1]["base_rank"] == 2
    assert items[1]["rank_change"] == 0
    assert items[1]["live_entry_timing_label"] == "冲高别追"


@pytest.mark.asyncio
async def test_live_rankings_keeps_intraday_entry_timing_when_daily_cache_is_high_chase(client, app, monkeypatch) -> None:
    run_id = await _seed_signal_run(app, count=1, conclusions=[CONCLUSION_HIGH_WATCH], total_scores=[94.6])
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "afternoon", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        signal_item = await session.scalar(
            select(ShortResearchSignalItem).where(
                ShortResearchSignalItem.run_id == run_id,
                ShortResearchSignalItem.asset_code == "510000",
            )
        )
        assert signal_item is not None
        daily_timing = {
            "entry_timing_label": "冲高别追",
            "entry_timing_reason": "今天 3.09%，且近20日 22.23%、近60日 44.86% 已经不低，追高风险上升。",
        }
        signal_item.metrics_json = {**dict(signal_item.metrics_json or {}), **daily_timing}
        signal_item.rationale_json = daily_timing
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=2.447,
                change_percent=-6.1,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings?limit=1")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["etf_code"] == "510000"
    assert item["score_source"] == "intraday"
    assert item["quote"]["change_percent"] == -6.1
    assert item["live_entry_timing_label"] == "跌破等待"
    assert "跌破" in item["live_entry_timing_reason"]
    assert item["daily_entry_timing_label"] == "冲高别追"
    assert "今天 3.09%" in item["daily_entry_timing_reason"]


@pytest.mark.asyncio
async def test_live_rankings_marks_data_insufficient_without_faking_quote(client, app) -> None:
    await _seed_signal_run(app, count=1)
    response = await client.get("/api/etf-quotes/live-rankings")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    items = body["items"]
    assert len(items) == 1
    assert items[0]["etf_code"] == "510000"
    assert items[0]["base_score"] == 100
    assert items[0]["live_total_score"] == 100
    assert items[0]["score_source"] == "daily"
    assert items[0]["intraday_adjustment_score"] is None
    assert any("日线基础分" in reason for reason in items[0]["score_contribution_reasons"])
    assert items[0]["quote"] is None
    assert items[0]["live_entry_timing_label"] == "数据不足"
    assert items[0]["live_entry_timing_reason"] == "暂无新鲜盘中行情，暂不做盘中加分。"
    assert items[0]["daily_entry_timing_label"] == "趋势延续"
    assert items[0]["daily_entry_timing_reason"] == "日线趋势仍在。"


@pytest.mark.asyncio
async def test_quote_normalization_stale_handling_and_persist_all_eligible_etfs(app) -> None:
    run_id = await _seed_signal_run(app, count=1)
    quote_time = datetime.now().replace(microsecond=0)
    quote = normalize_spot_record(
        {
            "code": "510000",
            "latest_price": "1.234",
            "change_percent": "1.20",
            "volume": "10000",
            "turnover": "20000000",
            "bid_price": "1.233",
            "ask_price": "1.235",
            "iopv": "1.230",
            "premium_discount_pct": "0.33",
            "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    assert quote is not None
    assert quote.etf_code == "510000"
    assert quote.latest_price == 1.234
    assert quote.raw["quote_time_is_fallback"] is False
    assert is_quote_stale(datetime.now() - timedelta(minutes=5)) is True

    def fake_fetcher() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": "510000",
                    "latest_price": 1.25,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "provider_timestamp": pd.Timestamp(quote_time),
                },
                {
                    "code": "510999",
                    "latest_price": 1.55,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                {
                    "code": "999999",
                    "latest_price": 9.99,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            ]
        )

    async with app.state.db.session() as session:
        session.add(_etf("510999", "Non Signal ETF"))
        await session.commit()

        result = await intraday_etf_watch_job(session, run_type="manual", force=True, fetcher=fake_fetcher)
        rows = (await session.scalars(select(EtfIntradayQuote).order_by(EtfIntradayQuote.etf_code))).all()

    assert result["details"]["signal_run_id"] == run_id
    assert result["updated_quote_count"] == 2
    assert [row.etf_code for row in rows] == ["510000", "510999"]
    assert rows[0].raw_json["provider_timestamp"] == quote_time.isoformat()


def test_quote_normalization_marks_missing_time_as_display_only() -> None:
    fallback_time = datetime(2026, 6, 17, 10, 1, 0, tzinfo=ASIA_SHANGHAI)
    quote = normalize_spot_record(
        {
            "code": "510000",
            "latest_price": "1.234",
            "change_percent": "0.8",
        },
        fallback_time=fallback_time,
    )

    assert quote is not None
    assert quote.quote_time == fallback_time.replace(tzinfo=None)
    assert quote.raw["quote_time_is_fallback"] is True


def test_quote_normalization_parses_timezone_update_time() -> None:
    quote = normalize_spot_record(
        {
            "代码": "513520",
            "最新价": "2.585",
            "涨跌幅": "0.12",
            "更新时间": "2026-06-22 12:47:26+08:00",
        }
    )

    assert quote is not None
    assert quote.etf_code == "513520"
    assert quote.quote_time == datetime(2026, 6, 22, 12, 47, 26)
    assert quote.trade_date == date(2026, 6, 22)
    assert quote.raw["quote_time_is_fallback"] is False


@pytest.mark.asyncio
async def test_intraday_watch_reports_watch_codes_missing_from_provider(app) -> None:
    await _seed_signal_run(app, count=2)
    quote_time = datetime.now().replace(microsecond=0)

    def fake_fetcher() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": "510000",
                    "latest_price": 1.25,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            ]
        )

    async with app.state.db.session() as session:
        result = await intraday_etf_watch_job(session, run_type="manual", force=True, fetcher=fake_fetcher)

    assert result["updated_quote_count"] == 1
    assert result["details"]["missing_watch_count"] == 1
    assert result["details"]["missing_watch_codes"] == ["510001"]
    assert result["details"]["quote_audit"]["510000"]["decision_eligible"] is True
    assert result["details"]["quote_audit"]["510001"]["decision_eligible"] is False
    assert result["details"]["quote_audit"]["510001"]["quote_freshness"] == "unavailable"


@pytest.mark.asyncio
async def test_etf_entry_uses_manual_price_then_fresh_intraday_fallback(app, monkeypatch) -> None:
    await _seed_price_history(app, "512800")
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="512800",
                quote_time=now,
                trade_date=now.date(),
                latest_price=0.814,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

        manual = await create_position(
            session,
            asset_type="etf",
            asset_code="512800",
            user_id=1,
            buy_amount=3000,
            buy_date=now.date(),
            order_time_bucket="before_15",
            confirmed_nav_date=None,
            confirmed_nav=0.8,
        )
        fallback = await create_position(
            session,
            asset_type="etf",
            asset_code="512800",
            user_id=1,
            buy_amount=3000,
            buy_date=now.date(),
            order_time_bucket="before_15",
            confirmed_nav_date=None,
            confirmed_nav=None,
        )

    assert manual.entry_price == 0.8
    assert fallback.entry_price == 0.814
    assert fallback.entry_price_date == now.date()


@pytest.mark.asyncio
async def test_etf_entry_requires_manual_price_without_fresh_intraday_quote(app) -> None:
    await _seed_price_history(app, "512800", start_price=0.8)
    buy_date = date(2026, 6, 17)

    async with app.state.db.session() as session:
        position = await create_position(
            session,
            asset_type="etf",
            asset_code="512800",
            user_id=1,
            buy_amount=3000,
            buy_date=buy_date,
            order_time_bucket="before_15",
            confirmed_nav_date=None,
            confirmed_nav=None,
        )

    assert position.entry_price is None
    assert position.entry_price_date is None
    assert position.estimated_shares is None


@pytest.mark.asyncio
async def test_dynamic_hard_stop_and_intraday_cooldown(app, settings, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history(app, "510000")
    now = datetime.now().replace(microsecond=0)
    sent: list[dict] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="ETF510000",
            buy_date=date(2026, 6, 1),
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=date(2026, 6, 1),
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=0.95,
                bid_price=0.949,
                ask_price=0.951,
                turnover=100_000_000,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        await session.refresh(position)

        first_alert, first_status = await create_alert_if_needed(session, position, settings, evaluation_mode="intraday")
        second_alert, second_status = await create_alert_if_needed(session, position, settings, evaluation_mode="intraday")
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()

    assert first_status == "email_sent"
    assert first_alert is not None
    assert first_alert.alert_type == "hard_stop"
    assert first_alert.alert_level == "urgent"
    assert first_alert.alert_source == "intraday_quote"
    assert first_alert.threshold_context_json["alert_type"] == "hard_stop"
    assert first_alert.threshold_context_json["hard_stop_pct"] is not None
    assert first_alert.threshold_context_json["threshold_mode"] in {"rule_dynamic", "fixed_fallback"}
    assert sent[0]["threshold_context"]["alert_type"] == "hard_stop"
    assert sent[0]["threshold_context"]["hard_stop_pct"] == first_alert.threshold_context_json["hard_stop_pct"]
    assert second_status == "suppressed"
    assert second_alert is not None
    assert second_alert.id == first_alert.id
    assert len(alerts) == 1
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_intraday_email_requires_fresh_quote_and_skips_daily_close_fallback(app, settings, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history_from_closes(
        app,
        "510000",
        [1.00, 0.99, 0.98, 0.97, 0.96, 0.95],
        start=datetime.now().date() - timedelta(days=8),
    )
    sent: list[dict] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="ETF510000",
            buy_date=date(2026, 6, 1),
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=date(2026, 6, 1),
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        await session.refresh(position)

        alert, status = await create_alert_if_needed(session, position, settings, evaluation_mode="intraday")
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()
        analysis = await position_analysis(session, position)

    assert alert is None
    assert status == "data_ineligible"
    assert alerts == []
    assert sent == []
    assert analysis.intraday_snapshot is not None
    assert analysis.intraday_snapshot.price_source == "daily_close"
    assert analysis.intraday_snapshot.email_eligible is False
    assert analysis.exit_signal.email_eligible is False


@pytest.mark.asyncio
async def test_intraday_structure_warning_is_web_only(app, settings, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history_from_closes(
        app,
        "510000",
        [1.00, 1.002, 1.001, 1.003, 1.002, 1.001, 1.000, 1.001, 1.000, 1.001],
        start=datetime.now().date() - timedelta(days=14),
    )
    now = datetime.now().replace(microsecond=0)
    sent: list[dict] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="Structure ETF",
            buy_date=now.date() - timedelta(days=10),
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=now.date() - timedelta(days=10),
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.001,
                bid_price=0.995,
                ask_price=1.007,
                turnover=5_000_000,
                iopv=None,
                premium_discount_pct=1.2,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        await session.refresh(position)

        alert, status = await create_alert_if_needed(session, position, settings)

    assert alert is not None
    assert alert.alert_type == "risk_warning"
    assert alert.alert_level == "watch"
    assert alert.email_status == "skipped"
    assert status == "web_only"
    assert sent == []


@pytest.mark.asyncio
async def test_dynamic_trailing_profit_trend_and_structure_warnings(app) -> None:
    today = datetime.now().date()
    base = today - timedelta(days=14)

    await _seed_price_history_from_closes(
        app,
        "510880",
        [1.00, 1.01, 1.02, 1.04, 1.06, 1.08, 1.075, 1.07, 1.065, 1.06],
        start=base,
    )
    await _seed_price_history_from_closes(
        app,
        "510881",
        [1.005, 1.004, 1.003, 1.002, 1.001, 1.000, 0.998, 0.996, 0.994, 0.992],
        start=base,
    )
    await _seed_price_history_from_closes(
        app,
        "510882",
        [1.00, 1.002, 1.001, 1.003, 1.002, 1.001, 1.000, 1.001, 1.000, 1.001],
        start=base,
    )
    now = datetime.now().replace(microsecond=0)

    async with app.state.db.session() as session:
        trailing_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510880",
            asset_name="Trailing ETF",
            buy_date=base,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=base,
            estimated_shares=3000,
            status="active",
        )
        trend_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510881",
            asset_name="Trend ETF",
            buy_date=base,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=base,
            estimated_shares=3000,
            status="active",
        )
        structure_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510882",
            asset_name="Structure ETF",
            buy_date=base,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=base,
            estimated_shares=3000,
            status="active",
        )
        session.add_all([trailing_position, trend_position, structure_position])
        session.add_all(
            [
                EtfIntradayQuote(
                    etf_code="510880",
                    quote_time=now,
                    trade_date=today,
                    latest_price=1.055,
                    bid_price=1.054,
                    ask_price=1.056,
                    turnover=100_000_000,
                    iopv=1.055,
                    source="test",
                    freshness_status="fresh",
                    raw_json={},
                ),
                EtfIntradayQuote(
                    etf_code="510881",
                    quote_time=now,
                    trade_date=today,
                    latest_price=0.99,
                    bid_price=0.989,
                    ask_price=0.991,
                    turnover=100_000_000,
                    iopv=0.99,
                    source="test",
                    freshness_status="fresh",
                    raw_json={},
                ),
                EtfIntradayQuote(
                    etf_code="510882",
                    quote_time=now,
                    trade_date=today,
                    latest_price=1.001,
                    bid_price=0.995,
                    ask_price=1.007,
                    turnover=5_000_000,
                    iopv=None,
                    premium_discount_pct=1.2,
                    source="test",
                    freshness_status="fresh",
                    raw_json={},
                ),
            ]
        )
        await session.commit()
        await session.refresh(trailing_position)
        await session.refresh(trend_position)
        await session.refresh(structure_position)

        trailing = await position_analysis(session, trailing_position)
        trend = await position_analysis(session, trend_position)
        structure = await position_analysis(session, structure_position)

    assert trailing.exit_signal.alert_type == "trailing_take_profit"
    assert trailing.exit_signal.level == "warning"
    assert trailing.dynamic_thresholds is not None
    assert trailing.dynamic_thresholds.trailing_giveback_pct is not None
    assert trend.exit_signal.alert_type == "trend_weakening"
    assert trend.dynamic_thresholds is not None
    assert trend.dynamic_thresholds.trend_weakening is True
    assert structure.exit_signal.alert_type == "risk_warning"
    assert structure.exit_signal.level == "watch"
    assert structure.dynamic_thresholds is not None
    assert structure.dynamic_thresholds.liquidity_warnings
    assert structure.dynamic_thresholds.structure_warnings


@pytest.mark.asyncio
async def test_high_volatility_etf_receives_wider_dynamic_thresholds(app) -> None:
    today = datetime.now().date()
    start = today - timedelta(days=10)
    await _seed_price_history_from_closes(
        app,
        "510890",
        [1.000, 1.002, 1.001, 1.003, 1.004, 1.003, 1.005, 1.006, 1.005, 1.007],
        start=start,
    )
    await _seed_price_history_from_closes(
        app,
        "510891",
        [1.00, 1.06, 1.01, 1.09, 1.02, 1.11, 1.04, 1.13, 1.05, 1.12],
        start=start,
    )

    async with app.state.db.session() as session:
        low_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510890",
            asset_name="Low Vol ETF",
            buy_date=start,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=start,
            estimated_shares=3000,
            status="active",
        )
        high_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510891",
            asset_name="High Vol ETF",
            buy_date=start,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=start,
            estimated_shares=3000,
            status="active",
        )
        session.add_all([low_position, high_position])
        await session.commit()
        await session.refresh(low_position)
        await session.refresh(high_position)

        low = await position_analysis(session, low_position)
        high = await position_analysis(session, high_position)

    assert low.dynamic_thresholds is not None
    assert high.dynamic_thresholds is not None
    assert high.dynamic_thresholds.hard_stop_pct is not None
    assert low.dynamic_thresholds.hard_stop_pct is not None
    assert high.dynamic_thresholds.trailing_giveback_pct is not None
    assert low.dynamic_thresholds.trailing_giveback_pct is not None
    assert high.dynamic_thresholds.hard_stop_pct < low.dynamic_thresholds.hard_stop_pct
    assert high.dynamic_thresholds.trailing_giveback_pct > low.dynamic_thresholds.trailing_giveback_pct


@pytest.mark.asyncio
async def test_missing_iopv_warning_is_web_only(app, settings, monkeypatch) -> None:
    today = datetime.now().date()
    start = today - timedelta(days=10)
    await _seed_signal_run(app, count=1)
    await _seed_price_history_from_closes(
        app,
        "510000",
        [1.00, 1.01, 1.00, 1.01, 1.00, 1.01, 1.00, 1.01, 1.00, 1.01],
        start=start,
    )
    sent: list[dict[str, object]] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="ETF510000",
            buy_date=start,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=start,
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=datetime.now().replace(microsecond=0),
                trade_date=today,
                latest_price=1.01,
                bid_price=1.009,
                ask_price=1.011,
                turnover=100_000_000,
                iopv=None,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()
        await session.refresh(position)
        alert, status = await create_alert_if_needed(session, position, settings)

    assert alert is not None
    assert status == "web_only"
    assert alert.alert_type == "risk_warning"
    assert alert.email_status == "skipped"
    assert sent == []


@pytest.mark.asyncio
async def test_intraday_watch_status_api_and_tracked_position_fields(client, app, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history(app, "510000")
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            TrackedPosition(
                user_id=1,
                asset_type="etf",
                asset_code="510000",
                asset_name="ETF510000",
                buy_date=date(2026, 6, 1),
                buy_amount=3000,
                entry_price=1.0,
                entry_price_date=date(2026, 6, 1),
                estimated_shares=3000,
                status="active",
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.02,
                bid_price=1.019,
                ask_price=1.021,
                iopv=1.018,
                premium_discount_pct=0.2,
                turnover=80_000_000,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

    status_response = await client.get("/api/etf-quotes/tracked")
    tracked_response = await client.get("/api/tracked-positions")

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["watched_count"] == 1
    assert status_body["items"][0]["quote"]["latest_price"] == 1.02
    assert tracked_response.status_code == 200
    tracked = tracked_response.json()["items"][0]
    assert tracked["intraday_snapshot"]["price_source"] == "intraday_quote"
    assert tracked["intraday_snapshot"]["current_price"] == 1.02
    assert tracked["dynamic_thresholds"]["hard_stop_pct"] is not None
    assert tracked["recent_intraday_alerts"] == []


@pytest.mark.asyncio
async def test_intraday_provider_failure_falls_back_to_cached_quote(app, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.01,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

        def broken_fetcher() -> pd.DataFrame:
            raise RuntimeError("provider down")

        result = await intraday_etf_watch_job(session, run_type="manual", force=True, fetcher=broken_fetcher)
        quote_count = await session.scalar(select(func.count()).select_from(EtfIntradayQuote))

    assert result["status"] == "degraded"
    assert result["updated_quote_count"] == 0
    assert result["details"]["provider_error"]
    assert quote_count == 1


@pytest.mark.asyncio
async def test_latest_quotes_by_code_returns_one_latest_quote_per_etf(app) -> None:
    async with app.state.db.session() as session:
        session.add_all([_etf("510001"), _etf("510002")])
        await session.flush()
        session.add_all(
            [
                EtfIntradayQuote(
                    etf_code="510001",
                    quote_time=datetime(2026, 6, 18, 9, 31),
                    trade_date=date(2026, 6, 18),
                    latest_price=1.0,
                ),
                EtfIntradayQuote(
                    etf_code="510001",
                    quote_time=datetime(2026, 6, 18, 9, 35),
                    trade_date=date(2026, 6, 18),
                    latest_price=1.1,
                ),
                EtfIntradayQuote(
                    etf_code="510002",
                    quote_time=datetime(2026, 6, 18, 9, 33),
                    trade_date=date(2026, 6, 18),
                    latest_price=2.0,
                ),
            ]
        )
        await session.commit()

        quotes = await latest_quotes_by_code(session, ["510001", "510002", "510001", "599999"])

    assert set(quotes) == {"510001", "510002"}
    assert quotes["510001"].latest_price == 1.1
    assert quotes["510002"].latest_price == 2.0


@pytest.mark.asyncio
async def test_intraday_cleanup_summarizes_and_deletes_old_raw_quotes(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510003"))
        await session.flush()
        for offset in range(4):
            trade_date = date(2026, 6, 10 + offset)
            session.add_all(
                [
                    EtfIntradayQuote(
                        etf_code="510003",
                        quote_time=datetime(2026, 6, 10 + offset, 9, 31),
                        trade_date=trade_date,
                        latest_price=1.0 + offset,
                        volume=1000 + offset,
                        turnover=10_000 + offset,
                    ),
                    EtfIntradayQuote(
                        etf_code="510003",
                        quote_time=datetime(2026, 6, 10 + offset, 14, 59),
                        trade_date=trade_date,
                        latest_price=1.1 + offset,
                        volume=2000 + offset,
                        turnover=20_000 + offset,
                    ),
                ]
            )
        await session.commit()

        result = await summarize_and_cleanup_intraday_quotes(session, retention_trading_days=2)
        raw_count = await session.scalar(select(func.count()).select_from(EtfIntradayQuote))
        summary_count = await session.scalar(select(func.count()).select_from(EtfIntradayDailySummary))

    assert result["cutoff_date"] == "2026-06-12"
    assert result["summarized_groups"] == 2
    assert result["deleted_rows"] == 4
    assert raw_count == 4
    assert summary_count == 2

