from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
from sqlalchemy import event, func, select, text

from app.models.entities import (
    EtfListingDateObservation,
    EtfPointInTimeMembershipFact,
    EtfPriceHistory,
    EtfUniverseMembership,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TradableEtf,
    authorize_snapshot_publication,
)
from app.services.short_research import service as short_research_service
from app.services.short_research import universe as universe_module
from app.services.short_research.ranking_contract import final_score_v3_contract
from app.services.short_research.snapshot_selector import required_etf_snapshot_trade_date
from app.services.short_research.universe import (
    EtfUniverseDiscovery,
    EtfUniverseRecord,
    build_point_in_time_universe_snapshot,
    discover_etf_universe,
    parse_provider_listing_date,
    refresh_etf_universe,
)
from app.services.strategy_lab.etf_point_in_time_decision_data import (
    load_point_in_time_etf_decision_inputs,
)
from app.services.workflows.short_research_data import (
    sync_short_research_data_with_tracking_priority,
)


def test_default_etf_sync_batch_size_is_bounded_for_small_servers(monkeypatch) -> None:
    monkeypatch.delenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", raising=False)

    assert short_research_service._etf_sync_batch_size() == 20


@pytest.mark.asyncio
async def test_universe_discovery_uses_complete_eastmoney_snapshot_before_akshare(monkeypatch) -> None:
    calls = {"eastmoney": 0, "akshare": 0}

    async def fetch_eastmoney_rows() -> SimpleNamespace:
        calls["eastmoney"] += 1
        return SimpleNamespace(
            rows=(
                {"f12": "510300", "f14": "沪深300ETF", "f26": 20120528},
                {"f12": "159915", "f14": "创业板ETF", "f26": "20110920"},
            ),
            expected_total=2,
            complete=True,
            error=None,
            elapsed_ms=5,
        )

    def fetch_akshare_rows() -> pd.DataFrame:
        calls["akshare"] += 1
        raise AssertionError("complete Eastmoney discovery must not call AKShare")

    monkeypatch.setattr(
        universe_module,
        "fetch_eastmoney_etf_spot_rows",
        fetch_eastmoney_rows,
        raising=False,
    )
    monkeypatch.setattr(universe_module.ak, "fund_etf_spot_em", fetch_akshare_rows)

    result = await discover_etf_universe()

    assert result.authoritative is True
    assert result.source == "eastmoney.push2.clist"
    assert result.source_row_count == 2
    assert result.normalized_row_count == 2
    assert [record.code for record in result.records] == ["159915", "510300"]
    assert [record.listing_date for record in result.records] == [
        date(2011, 9, 20),
        date(2012, 5, 28),
    ]
    assert all(record.listing_date_source == "eastmoney.push2.clist" for record in result.records)
    assert calls == {"eastmoney": 1, "akshare": 0}


def test_provider_listing_date_parser_never_uses_partial_or_invalid_values() -> None:
    assert parse_provider_listing_date("2020/01/02") == date(2020, 1, 2)
    assert parse_provider_listing_date(20200102) == date(2020, 1, 2)
    assert parse_provider_listing_date("--") is None
    assert parse_provider_listing_date("202001") is None


@pytest.mark.asyncio
async def test_sse_complete_snapshot_allows_out_of_scope_rows_without_listing_dates(
    monkeypatch,
) -> None:
    payload = {
        "pageHelp": {"total": 2},
        "result": [
            {"FUND_CODE": "510300", "LISTING_DATE": "2012-05-28"},
            {"FUND_CODE": "519899", "LISTING_DATE": "-"},
        ],
    }
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=payload)
    )
    async_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(universe_module.httpx, "AsyncClient", client_factory)

    result = await universe_module._discover_sse_official_listing_metadata()

    assert result.authoritative is True
    assert result.dates_by_code == {"510300": date(2012, 5, 28)}
    assert result.missing_listing_date_count == 1
    assert result.error_summary == "missing_listing_dates=1"


@pytest.mark.asyncio
async def test_szse_official_workbook_is_parsed_from_bytes(monkeypatch) -> None:
    workbook = BytesIO()
    pd.DataFrame(
        [
            {"基金代码": "159915", "上市日期": "2011-09-20"},
            {"基金代码": "159919", "上市日期": "2012-04-06"},
        ]
    ).to_excel(workbook, index=False)
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=workbook.getvalue())
    )
    async_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(universe_module.httpx, "AsyncClient", client_factory)

    result = await universe_module._discover_szse_official_listing_metadata()

    assert result.authoritative is True
    assert result.dates_by_code == {
        "159915": date(2011, 9, 20),
        "159919": date(2012, 4, 6),
    }
    assert result.missing_listing_date_count == 0
    assert result.error_summary is None


@pytest.mark.asyncio
async def test_official_listing_metadata_replaces_third_party_dates_only_from_complete_sources(
    monkeypatch,
) -> None:
    records = (
        EtfUniverseRecord(
            code="510300",
            name="沪深300ETF",
            exchange="SH",
            category="broad",
            theme_tags=["宽基"],
            trading_rule_label="证券账户 T+1 ETF",
            source="eastmoney.push2.clist",
            listing_date=date(2099, 1, 1),
            listing_date_source="eastmoney.push2.clist",
        ),
        EtfUniverseRecord(
            code="159915",
            name="创业板ETF",
            exchange="SZ",
            category="broad",
            theme_tags=["宽基"],
            trading_rule_label="证券账户 T+1 ETF",
            source="eastmoney.push2.clist",
        ),
    )

    async def sse_source():
        return universe_module.OfficialListingMetadataSource(
            exchange="SH",
            source=universe_module.SSE_LISTING_SOURCE,
            provider_version=universe_module.SSE_LISTING_PROVIDER_VERSION,
            status="authoritative",
            dates_by_code={"510300": date(2012, 5, 28)},
            source_row_count=1,
            expected_total=1,
            universe_snapshot_hash="1" * 64,
            raw_payload_hash="2" * 64,
        )

    async def szse_source():
        return universe_module.OfficialListingMetadataSource(
            exchange="SZ",
            source=universe_module.SZSE_LISTING_SOURCE,
            provider_version=universe_module.SZSE_LISTING_PROVIDER_VERSION,
            status="partial",
            dates_by_code={"159915": date(2011, 9, 20)},
            source_row_count=1,
            expected_total=2,
            universe_snapshot_hash="3" * 64,
            raw_payload_hash="4" * 64,
            error_summary="incomplete",
        )

    monkeypatch.setattr(universe_module, "_discover_sse_official_listing_metadata", sse_source)
    monkeypatch.setattr(universe_module, "_discover_szse_official_listing_metadata", szse_source)

    enriched, evidence = await universe_module.enrich_with_official_listing_metadata(records)

    assert enriched[0].listing_date == date(2012, 5, 28)
    assert enriched[0].listing_date_source == universe_module.SSE_LISTING_SOURCE
    assert enriched[1].listing_date is None
    assert enriched[1].listing_date_source is None
    assert evidence["observed_count"] == 1
    assert evidence["coverage_ratio"] == 0.5


@pytest.mark.asyncio
async def test_universe_discovery_falls_back_to_complete_akshare_snapshot(monkeypatch) -> None:
    eastmoney_calls = 0

    async def fetch_eastmoney_rows() -> SimpleNamespace:
        nonlocal eastmoney_calls
        eastmoney_calls += 1
        return SimpleNamespace(
            rows=(),
            expected_total=None,
            complete=False,
            error="eastmoney disconnected",
            elapsed_ms=5,
        )

    monkeypatch.setattr(
        universe_module,
        "fetch_eastmoney_etf_spot_rows",
        fetch_eastmoney_rows,
        raising=False,
    )
    monkeypatch.setattr(
        universe_module.ak,
        "fund_etf_spot_em",
        lambda: pd.DataFrame([{"代码": "588000", "名称": "科创50ETF"}]),
    )

    result = await discover_etf_universe()

    assert result.authoritative is True
    assert result.source == "akshare.fund_etf_spot_em"
    assert [record.code for record in result.records] == ["588000"]
    assert eastmoney_calls == 1


@pytest.mark.asyncio
async def test_universe_discovery_reports_both_provider_failures(monkeypatch) -> None:
    async def fetch_eastmoney_rows() -> SimpleNamespace:
        return SimpleNamespace(
            rows=(),
            expected_total=None,
            complete=False,
            error="eastmoney disconnected",
            elapsed_ms=5,
        )

    def fetch_akshare_rows() -> pd.DataFrame:
        raise ConnectionError("akshare proxy refused")

    monkeypatch.setattr(
        universe_module,
        "fetch_eastmoney_etf_spot_rows",
        fetch_eastmoney_rows,
        raising=False,
    )
    monkeypatch.setattr(universe_module.ak, "fund_etf_spot_em", fetch_akshare_rows)

    result = await discover_etf_universe()

    assert result.status == "failure"
    assert result.error_summary is not None
    assert "eastmoney disconnected" in result.error_summary
    assert "akshare proxy refused" in result.error_summary


@pytest.mark.asyncio
async def test_universe_discovery_rejects_duplicate_primary_snapshot(monkeypatch) -> None:
    async def fetch_eastmoney_rows() -> SimpleNamespace:
        return SimpleNamespace(
            rows=(
                {"f12": "510300", "f14": "沪深300ETF"},
                {"f12": "510300", "f14": "沪深300ETF"},
            ),
            expected_total=2,
            complete=True,
            error=None,
            elapsed_ms=5,
        )

    def fetch_akshare_rows() -> pd.DataFrame:
        raise ConnectionError("akshare unavailable")

    monkeypatch.setattr(
        universe_module,
        "fetch_eastmoney_etf_spot_rows",
        fetch_eastmoney_rows,
        raising=False,
    )
    monkeypatch.setattr(universe_module.ak, "fund_etf_spot_em", fetch_akshare_rows)

    result = await discover_etf_universe()

    assert result.status == "partial"
    assert result.source_row_count == 2
    assert result.normalized_row_count == 1
    assert result.error_summary is not None
    assert "duplicate" in result.error_summary.lower()


@pytest.mark.asyncio
async def test_universe_discovery_rejects_incomplete_primary_snapshot(monkeypatch) -> None:
    async def fetch_eastmoney_rows() -> SimpleNamespace:
        return SimpleNamespace(
            rows=({"f12": "510300", "f14": "沪深300ETF"},),
            expected_total=2,
            complete=False,
            error=None,
            elapsed_ms=5,
        )

    def fetch_akshare_rows() -> pd.DataFrame:
        raise ConnectionError("akshare unavailable")

    monkeypatch.setattr(
        universe_module,
        "fetch_eastmoney_etf_spot_rows",
        fetch_eastmoney_rows,
        raising=False,
    )
    monkeypatch.setattr(universe_module.ak, "fund_etf_spot_em", fetch_akshare_rows)

    result = await discover_etf_universe()

    assert result.status == "partial"
    assert result.source_row_count == 1
    assert result.normalized_row_count == 1
    assert result.error_summary is not None
    assert "expected 2 rows" in result.error_summary


async def _seed_etf_history(
    app: Any,
    *,
    code: str,
    name: str,
    latest: date = date(2026, 6, 5),
    days: int = 90,
    turnover: float = 120_000_000,
    daily_return: float = 0.002,
    theme_tags: list[str] | None = None,
) -> None:
    async with app.state.db.session() as session:
        existing = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
        if existing is None:
            session.add(
                TradableEtf(
                    code=code,
                    name=name,
                    exchange="SH" if code.startswith("5") else "SZ",
                    theme_tags_json=theme_tags or ["测试主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
        start = latest - timedelta(days=days - 1)
        for offset in range(days):
            current = start + timedelta(days=offset)
            close = 1.0 * (1 + daily_return) ** offset
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=current,
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=turnover / close,
                    turnover=turnover,
                    pct_change=0.0 if offset == 0 else daily_return * 100,
                    research_adjusted_value=close,
                    research_price_basis="total_return_adjusted",
                    data_provider="fixture",
                    provider_version="fixture-v1",
                    adjustment_version="fixture-v1",
                    decision_eligible=True,
                )
            )
        await session.commit()


async def _seed_cached_etf_signals(
    app: Any,
    count: int = 3,
    *,
    item_overrides: dict[int, dict[str, Any]] | None = None,
) -> int:
    async with app.state.db.session() as session:
        contract = final_score_v3_contract()
        selector = contract["selector"]
        calculation = contract["calculation"]
        trade_date = required_etf_snapshot_trade_date()
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=trade_date,
            scope_kind=str(selector["required_scope"]),
            scope_hash="cached-etf-full-scope",
            universe_snapshot_hash="cached-etf-universe",
            input_snapshot_hash="cached-etf-input",
            score_version=str(selector["target_score_version"]),
            rule_version=str(contract["rule_version"]),
            ranking_contract_hash="cached-etf-contract",
            score_field=str(selector["score_field"]),
            data_cutoff=datetime.combine(trade_date, time(15, 0)),
            as_of_trade_date=trade_date,
            price_basis=str(calculation["price_basis"]),
            expected_item_count=count,
            decision_data_item_count=count,
            decision_data_coverage_ratio=1.0,
            eligible_item_count=count,
            coverage_ratio=1.0,
            publication_state="unpublished",
            idempotency_key=f"cached-etf-{trade_date.isoformat()}-{count}",
            config_json={"asset_type": "etf"},
            summary_json={
                "item_count": count,
                "fund_count": 0,
                "etf_count": count,
                "score_version": "final_score_v3",
            },
        )
        session.add(run)
        await session.flush()
        for index in range(count):
            code = f"5620{index:02d}"
            override = (item_overrides or {}).get(index, {})
            entry_timing_label = override.get("entry_timing_label", "健康回踩")
            entry_timing_reason = override.get(
                "entry_timing_reason",
                "测试缓存资产处于健康回踩，允许进入观察组合。",
            )
            session.add(
                TradableEtf(
                    code=code,
                    name=f"Cached ETF {index}",
                    exchange="SH",
                    theme_tags_json=[f"cached-{index}"],
                    trading_rule_label="T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            session.add(
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=index + 1,
                    global_rank=index + 1,
                    total_score=90 - index,
                    ranking_score=90 - index,
                    score_eligible=True,
                    conclusion=override.get("conclusion", "短线观察"),
                    score_breakdown_json={
                        "final_score_v3": {
                            "score_version": "final_score_v3",
                            "ranking_score": 90 - index,
                            "score_eligible": True,
                        },
                        "trend": {"score": 80 - index, "weight": 0.55},
                        "risk": {"score": 70, "weight": 0.30},
                        "liquidity": {"score": 90, "weight": 0.15},
                        "metrics": {"return_20d": 0.02 + index / 100},
                    },
                    risk_flags_json=[],
                    rationale_json={"key_reason": "cached", "risk_explanation": "cached"},
                    metrics_json={
                        "return_5d": 0.01,
                        "return_20d": 0.02 + index / 100,
                        "return_60d": 0.03,
                        "max_drawdown_60d": -0.04,
                        "average_turnover_20d": 100_000_000,
                        "latest_date": "2026-06-12",
                        "latest_value": 1.23 + index,
                        "usable_days": 120,
                        "sample_level": "样本充足",
                        "source_note": "cached signal",
                        "default_display_eligible": True,
                        "entry_timing_label": entry_timing_label,
                        "entry_timing_reason": entry_timing_reason,
                        "score_version": "final_score_v3",
                        "ranking_score": 90 - index,
                        "score_eligible": True,
                    },
                )
            )
        await session.flush()
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = datetime.combine(trade_date, time(15, 1))
            await session.flush()
        await session.commit()
        return run.id



async def _seed_observation_portfolio_signal_run(app: Any, *, items: list[dict[str, Any]]) -> None:
    async with app.state.db.session() as session:
        contract = final_score_v3_contract()
        selector = contract["selector"]
        calculation = contract["calculation"]
        trade_date = required_etf_snapshot_trade_date()
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=trade_date,
            scope_kind=str(selector["required_scope"]),
            scope_hash="observation-etf-full-scope",
            universe_snapshot_hash="observation-etf-universe",
            input_snapshot_hash="observation-etf-input",
            score_version=str(selector["target_score_version"]),
            rule_version=str(contract["rule_version"]),
            ranking_contract_hash="observation-etf-contract",
            score_field=str(selector["score_field"]),
            data_cutoff=datetime.combine(trade_date, time(15, 0)),
            as_of_trade_date=trade_date,
            price_basis=str(calculation["price_basis"]),
            expected_item_count=len(items),
            decision_data_item_count=len(items),
            decision_data_coverage_ratio=1.0,
            eligible_item_count=len(items),
            coverage_ratio=1.0,
            publication_state="unpublished",
            idempotency_key=f"observation-etf-{trade_date.isoformat()}-{len(items)}",
            config_json={"asset_type": "etf"},
            summary_json={
                "item_count": len(items),
                "fund_count": 0,
                "etf_count": len(items),
                "score_version": str(selector["target_score_version"]),
            },
        )
        session.add(run)
        await session.flush()
        for index, item in enumerate(items):
            code = item["code"]
            existing = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
            if existing is None:
                session.add(
                    TradableEtf(
                        code=code,
                        name=item.get("name", f"观察组合ETF{index}"),
                        exchange="SH" if code.startswith("5") else "SZ",
                        theme_tags_json=item.get("theme_tags", ["测试主题"]),
                        trading_rule_label="证券账户 T+1 ETF",
                        asset_class=item.get("asset_class", "sector"),
                        is_short_term_eligible=True,
                        is_watchlist=True,
                    )
                )
            score = item.get("total_score", 80)
            session.add(
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=index + 1,
                    global_rank=index + 1,
                    total_score=score,
                    ranking_score=score,
                    score_eligible=True,
                    conclusion=item.get("conclusion", "短线观察"),
                    score_breakdown_json={
                        "final_score_v3": {
                            "score_version": str(selector["target_score_version"]),
                            "ranking_score": score,
                            "score_eligible": True,
                        },
                        "trend": {"score": item.get("trend_score", 80), "weight": 0.55},
                        "risk": {"score": 80, "weight": 0.30},
                        "liquidity": {"score": 90, "weight": 0.15},
                    },
                    risk_flags_json=item.get("risk_flags", []),
                    rationale_json={"key_reason": item.get("entry_timing_reason", "测试原因。")},
                    metrics_json={
                        "return_5d": 0.02,
                        "return_20d": 0.06,
                        "return_60d": 0.12,
                        "max_drawdown_60d": -0.05,
                        "volatility_20d": 0.02,
                        "average_turnover_20d": 160_000_000,
                        "latest_date": trade_date.isoformat(),
                        "latest_value": 1.2 + index / 10,
                        "usable_days": 100,
                        "sample_level": "样本充足",
                        "source_note": "pytest",
                        "default_display_eligible": True,
                        "entry_timing_label": item.get("entry_timing_label", "趋势延续"),
                        "entry_timing_reason": item.get("entry_timing_reason", "测试原因。"),
                        "score_version": str(selector["target_score_version"]),
                        "ranking_score": score,
                        "score_eligible": True,
                        **dict(item.get("metrics") or {}),
                    },
                )
            )
        await session.flush()
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = datetime.combine(trade_date, time(15, 1))
            await session.flush()
        await session.commit()


async def _seed_observation_price_series(app: Any, *, code: str) -> None:
    async with app.state.db.session() as session:
        existing = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
        if existing is None:
            session.add(
                TradableEtf(
                    code=code,
                    name=f"观察组合价格{code}",
                    exchange="SH" if code.startswith("5") else "SZ",
                    theme_tags_json=["测试主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
        latest = date(2026, 6, 15)
        start = latest - timedelta(days=99)
        value = 1.0
        patterns = {
            "1": [0.0010, 0.0022, -0.0008, 0.0017, 0.0004, 0.0028, -0.0002],
            "2": [0.0024, -0.0007, 0.0011, 0.0002, 0.0030, -0.0004, 0.0015],
            "3": [-0.0005, 0.0018, 0.0006, 0.0026, -0.0009, 0.0012, 0.0020],
            "4": [0.0016, 0.0001, 0.0029, -0.0006, 0.0013, 0.0005, 0.0023],
        }
        pattern = patterns.get(code[-1], patterns["1"])
        for offset in range(100):
            current = start + timedelta(days=offset)
            change = pattern[offset % len(pattern)]
            value *= 1 + change
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=current,
                    open=value * 0.995,
                    high=value * 1.01,
                    low=value * 0.99,
                    close=value,
                    volume=160_000_000 / value,
                    turnover=160_000_000,
                    pct_change=0.0 if offset == 0 else change * 100,
                )
            )
        await session.commit()

@pytest.mark.asyncio
async def test_etf_universe_refresh_is_idempotent_excludes_unsuitable_and_preserves_manual_theme(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="588000",
                name="旧名称科创ETF",
                exchange="SH",
                theme_tags_json=["人工维护主题"],
                trading_rule_label="旧规则",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        await session.commit()

        records = [
            EtfUniverseRecord(
                code="588000",
                name="科创50ETF",
                exchange="SH",
                category="broad",
                theme_tags=["科创"],
                trading_rule_label="证券账户 T+1 ETF",
                source="pytest",
                listing_date=date(2018, 11, 15),
                listing_date_source="pytest.authoritative_snapshot",
            ),
            EtfUniverseRecord(
                code="511990",
                name="华宝添益货币ETF",
                exchange="SH",
                category="money",
                theme_tags=["货币"],
                trading_rule_label="货币 ETF",
                source="pytest",
            ),
        ]

        first = await refresh_etf_universe(session, records=records)
        second = await refresh_etf_universe(session, records=records)

        assert first["discovered"] == 2
        assert first["excluded"] == 1
        assert second["inserted"] == 0
        assert await session.scalar(select(func.count()).select_from(TradableEtf).where(TradableEtf.code == "588000")) == 1

        kept = await session.scalar(select(TradableEtf).where(TradableEtf.code == "588000"))
        assert kept is not None
        assert kept.name == "科创50ETF"
        assert kept.theme_tags_json == ["人工维护主题"]
        assert kept.is_short_term_eligible is True
        assert kept.listing_date == date(2018, 11, 15)
        assert kept.listing_date_source == "pytest.authoritative_snapshot"
        assert kept.listing_date_observed_at is not None

        money = await session.scalar(select(TradableEtf).where(TradableEtf.code == "511990"))
        assert money is not None
        assert money.is_short_term_eligible is False


@pytest.mark.asyncio
async def test_universe_refresh_persists_listing_observation_idempotently(app) -> None:
    record = EtfUniverseRecord(
        code="588000",
        name="科创50ETF",
        exchange="SH",
        category="broad",
        theme_tags=["科创"],
        trading_rule_label="证券账户 T+1 ETF",
        source="eastmoney.push2.clist",
        listing_date=date(2020, 11, 16),
        listing_date_source=universe_module.SSE_LISTING_SOURCE,
        listing_provider_version=universe_module.SSE_LISTING_PROVIDER_VERSION,
        listing_universe_snapshot_hash="a" * 64,
        listing_raw_payload_hash="b" * 64,
    )
    async with app.state.db.session() as session:
        await refresh_etf_universe(session, records=[record])
        await refresh_etf_universe(session, records=[record])
        observations = (
            await session.scalars(select(EtfListingDateObservation))
        ).all()

    assert len(observations) == 1
    assert observations[0].listing_date == date(2020, 11, 16)
    assert observations[0].source == universe_module.SSE_LISTING_SOURCE
    assert len(observations[0].evidence_hash) == 64


@pytest.mark.asyncio
async def test_live_authoritative_refresh_persists_daily_membership_facts_idempotently(
    app,
    monkeypatch,
) -> None:
    effective_date = date(2026, 8, 25)
    observed_at = datetime(2026, 8, 25, 7, 0)
    records = (
        EtfUniverseRecord(
            code="588000",
            name="科创50ETF",
            exchange="SH",
            category="broad",
            theme_tags=["科创"],
            trading_rule_label="证券账户 T+1 ETF",
            source=universe_module.EASTMONEY_UNIVERSE_SOURCE,
        ),
        EtfUniverseRecord(
            code="511990",
            name="华宝添益货币ETF",
            exchange="SH",
            category="money",
            theme_tags=["货币"],
            trading_rule_label="货币 ETF",
            source=universe_module.EASTMONEY_UNIVERSE_SOURCE,
        ),
    )
    discovery = EtfUniverseDiscovery(
        records=records,
        status="authoritative",
        source=universe_module.EASTMONEY_UNIVERSE_SOURCE,
        source_row_count=2,
        normalized_row_count=2,
        provider_version=universe_module.EASTMONEY_UNIVERSE_PROVIDER_VERSION,
        observed_at=observed_at,
        universe_snapshot_hash="a" * 64,
        raw_payload_hash="b" * 64,
    )

    async def discover() -> EtfUniverseDiscovery:
        return discovery

    async def enrich(items):
        return tuple(items), {
            "source_kind": "not_observed",
            "expected_count": 2,
            "observed_count": 0,
            "coverage_ratio": 0.0,
            "sources": [],
        }

    monkeypatch.setattr(universe_module, "discover_etf_universe", discover)
    monkeypatch.setattr(universe_module, "enrich_with_official_listing_metadata", enrich)

    async with app.state.db.session() as session:
        first = await refresh_etf_universe(session, as_of_date=effective_date)
        second = await refresh_etf_universe(session, as_of_date=effective_date)
        facts = (
            await session.scalars(
                select(EtfPointInTimeMembershipFact).order_by(
                    EtfPointInTimeMembershipFact.etf_code
                )
            )
        ).all()
        decision_inputs = await load_point_in_time_etf_decision_inputs(
            session,
            replay_date=effective_date,
            decision_cutoff=datetime(
                2026,
                8,
                25,
                18,
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ),
            max_source_rows=61 * 2,
            max_codes=2,
            required_history_sessions=61,
        )

    assert first["pit_membership_facts_added"] == 2
    assert second["pit_membership_facts_added"] == 0
    assert len(facts) == 2
    assert {fact.etf_code: fact.membership_state for fact in facts} == {
        "511990": "excluded",
        "588000": "included",
    }
    assert all(fact.observed_at == observed_at for fact in facts)
    assert all(fact.effective_from == effective_date for fact in facts)
    assert all(fact.effective_to == effective_date for fact in facts)
    assert [item.asset_code for item in decision_inputs.authoritative_universe] == [
        "588000"
    ]


@pytest.mark.asyncio
async def test_universe_refresh_closes_missing_memberships_and_preserves_historical_coverage(app) -> None:
    record = EtfUniverseRecord(
        code="588001",
        name="科创50ETF",
        exchange="SH",
        category="broad",
        theme_tags=["科创"],
        trading_rule_label="证券账户 T+1 ETF",
        source="pytest",
    )
    async with app.state.db.session() as session:
        first = await refresh_etf_universe(session, records=[record], as_of_date=date(2026, 1, 2))
        second = await refresh_etf_universe(session, records=[], as_of_date=date(2026, 1, 3))
        membership = await session.scalar(
            select(EtfUniverseMembership).where(EtfUniverseMembership.etf_code == "588001")
        )
        etf = await session.get(TradableEtf, "588001")
        historical = await build_point_in_time_universe_snapshot(session, as_of_date=date(2026, 1, 2))
        current = await build_point_in_time_universe_snapshot(session, as_of_date=date(2026, 1, 4))

    assert first["activated"] == 1
    assert second["deactivated"] == 1
    assert membership is not None
    assert membership.effective_to == date(2026, 1, 3)
    assert membership.exclusion_reason == "missing_from_refresh"
    assert etf is not None
    assert etf.is_short_term_eligible is False
    assert etf.is_watchlist is False
    assert [member["asset_code"] for member in historical.members] == ["588001"]
    assert current.members == []


@pytest.mark.asyncio
async def test_universe_refresh_preserves_frozen_membership_when_live_discovery_is_not_authoritative(
    app,
    monkeypatch,
) -> None:
    record = EtfUniverseRecord(
        code="588001",
        name="科创50ETF",
        exchange="SH",
        category="broad",
        theme_tags=["科创"],
        trading_rule_label="证券账户 T+1 ETF",
        source="pytest",
    )
    async with app.state.db.session() as session:
        await refresh_etf_universe(session, records=[record], as_of_date=date(2026, 1, 2))

        async def failed_discovery() -> EtfUniverseDiscovery:
            return EtfUniverseDiscovery(
                records=(),
                status="failure",
                source="akshare.fund_etf_spot_em",
                source_row_count=0,
                normalized_row_count=0,
                error_summary="proxy connection refused",
            )

        monkeypatch.setattr(universe_module, "discover_etf_universe", failed_discovery)
        result = await refresh_etf_universe(session, as_of_date=date(2026, 1, 3))
        membership = await session.scalar(
            select(EtfUniverseMembership).where(EtfUniverseMembership.etf_code == record.code)
        )
        current = await build_point_in_time_universe_snapshot(session, as_of_date=date(2026, 1, 4))

    assert result["authoritative"] is False
    assert result["discovery_status"] == "failure"
    assert result["stale_universe"] is True
    assert result["deactivated"] == 0
    assert membership is not None
    assert membership.effective_to is None
    assert [member["asset_code"] for member in current.members] == [record.code]


@pytest.mark.asyncio
async def test_universe_refresh_rejects_equal_sized_disjoint_live_replacement(app, monkeypatch) -> None:
    old_records = [
        EtfUniverseRecord(
            code=f"56000{index}",
            name=f"旧池{index}ETF",
            exchange="SH",
            category="sector",
            theme_tags=["旧池"],
            trading_rule_label="证券账户 T+1 ETF",
            source="pytest.seed",
        )
        for index in range(1, 4)
    ]
    replacement_records = tuple(
        EtfUniverseRecord(
            code=f"56100{index}",
            name=f"替换池{index}ETF",
            exchange="SH",
            category="sector",
            theme_tags=["替换池"],
            trading_rule_label="证券账户 T+1 ETF",
            source="eastmoney.push2.clist",
        )
        for index in range(1, 4)
    )

    async with app.state.db.session() as session:
        await refresh_etf_universe(session, records=old_records, as_of_date=date(2026, 7, 15))

        async def disjoint_discovery() -> EtfUniverseDiscovery:
            return EtfUniverseDiscovery(
                records=replacement_records,
                status="authoritative",
                source="eastmoney.push2.clist",
                source_row_count=3,
                normalized_row_count=3,
            )

        monkeypatch.setattr(universe_module, "discover_etf_universe", disjoint_discovery)
        result = await refresh_etf_universe(session, as_of_date=date(2026, 7, 16))
        active_old = await session.scalar(
            select(func.count())
            .select_from(EtfUniverseMembership)
            .where(
                EtfUniverseMembership.etf_code.in_([record.code for record in old_records]),
                EtfUniverseMembership.effective_to.is_(None),
            )
        )
        replacement_count = await session.scalar(
            select(func.count())
            .select_from(TradableEtf)
            .where(TradableEtf.code.in_([record.code for record in replacement_records]))
        )

    assert result["authoritative"] is False
    assert result["discovery_status"] == "partial"
    assert result["inserted"] == 0
    assert result["deactivated"] == 0
    assert active_old == 3
    assert replacement_count == 0


@pytest.mark.asyncio
async def test_universe_refresh_flushes_new_etf_before_membership_insert(app) -> None:
    record = EtfUniverseRecord(
        code="159605",
        name="新能源ETF",
        exchange="SZ",
        category="sector",
        theme_tags=["新能源"],
        trading_rule_label="证券账户 T+1 ETF",
        source="pytest",
    )
    async with app.state.db.session() as session:
        await session.execute(text("PRAGMA foreign_keys = ON"))
        result = await refresh_etf_universe(session, records=[record], as_of_date=date(2026, 7, 13))
        membership = await session.scalar(
            select(EtfUniverseMembership).where(EtfUniverseMembership.etf_code == record.code)
        )

    assert result["inserted"] == 1
    assert result["activated"] == 1
    assert membership is not None


@pytest.mark.asyncio
async def test_universe_refresh_bulk_loads_seed_expansion_without_syncing_history(app) -> None:
    records = [
        EtfUniverseRecord(
            code=f"560{index:03d}",
            name=f"扩容样本{index}ETF",
            exchange="SH",
            category="sector",
            theme_tags=["测试"],
            trading_rule_label="证券账户 T+1 ETF",
            source="pytest.full_universe",
        )
        for index in range(1, 41)
    ]
    tradable_selects: list[str] = []

    def capture_select(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = str(statement).strip().upper()
        if normalized.startswith("SELECT") and "FROM TRADABLE_ETFS" in normalized:
            tradable_selects.append(normalized)

    event.listen(app.state.db.engine.sync_engine, "before_cursor_execute", capture_select)
    try:
        async with app.state.db.session() as session:
            result = await refresh_etf_universe(session, records=records, as_of_date=date(2026, 7, 16))
            history_count = await session.scalar(select(func.count()).select_from(EtfPriceHistory))
    finally:
        event.remove(app.state.db.engine.sync_engine, "before_cursor_execute", capture_select)

    assert result["inserted"] == 40
    assert result["activated"] == 40
    assert result["default_display"] == 40
    assert history_count == 0
    assert len(tradable_selects) <= 2


@pytest.mark.asyncio
async def test_short_research_etf_status_universe_filter_and_dynamic_detail(client, app) -> None:
    await _seed_etf_history(app, code="560001", name="动态科技ETF", turnover=150_000_000)
    await _seed_etf_history(app, code="560002", name="低流动ETF", turnover=3_000_000)

    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560001",
                "total_score": 90,
                "metrics": {
                    "default_display_eligible": True,
                    "default_exclusion_reasons": [],
                    "data_quality_score": 90,
                },
            },
            {
                "code": "560002",
                "total_score": 70,
                "metrics": {
                    "default_display_eligible": False,
                    "default_exclusion_reasons": ["20日平均成交额不足，流动性不满足默认展示门槛。"],
                    "data_quality_score": 70,
                },
            },
        ],
    )

    status = await client.get("/api/short-research/status")
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["etf_total_count"] >= 2
    assert status_body["etf_eligible_count"] >= 2
    assert status_body["etf_default_display_count"] >= 1
    assert "etf_data_stale_count" in status_body
    assert "etf_failed_count" in status_body

    default_response = await client.get("/api/short-research/assets?asset_type=etf&universe=default")
    assert default_response.status_code == 200
    default_items = default_response.json()["items"]
    assert any(item["code"] == "560001" for item in default_items)
    assert all(item["code"] != "560002" for item in default_items)
    included = next(item for item in default_items if item["code"] == "560001")
    assert included["metrics"]["default_display_eligible"] is True
    assert included["metrics"]["data_quality_score"] > 0

    all_response = await client.get("/api/short-research/assets?asset_type=etf&universe=all")
    assert all_response.status_code == 200
    all_items = all_response.json()["items"]
    low_liquidity = next(item for item in all_items if item["code"] == "560002")
    assert low_liquidity["metrics"]["default_display_eligible"] is False
    assert any("成交额" in reason or "流动性" in reason for reason in low_liquidity["metrics"]["default_exclusion_reasons"])

    detail = await client.get("/api/short-research/assets/etf/560001")
    assert detail.status_code == 200
    assert detail.json()["asset"]["name"] == "动态科技ETF"


@pytest.mark.asyncio
async def test_etf_observation_portfolio_is_research_only_and_fully_invested(client, app) -> None:
    await _seed_cached_etf_signals(app, count=4)

    response = await client.get("/api/short-research/observation-portfolio?asset_type=etf&limit=4")

    assert response.status_code == 200
    body = response.json()
    assert body["research_only"] is True
    assert body["no_trade_instruction"] is True
    assert len(body["items"]) == 4
    assert body["target_invested_weight"] == 1.0
    assert body["weight_sum"] == 1.0
    assert body["cash_weight"] == 0.0
    assert abs(sum(item["target_weight"] for item in body["items"]) - 1) < 0.01
    assert all(item["evidence"] for item in body["items"])
    assert all(item["target_weight"] <= 0.3 for item in body["items"])

    payload = json.dumps(body, ensure_ascii=False).lower()
    for forbidden in ["buy", "sell", "target_price", "expected_return", "guaranteed_profit"]:
        assert forbidden not in payload


@pytest.mark.asyncio
async def test_etf_observation_portfolio_fills_to_full_exposure_with_defensive_candidates(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "562101",
                "total_score": 92,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "测试主组合候选 1。",
                "theme_tags": ["证券"],
            },
            {
                "code": "562102",
                "total_score": 91,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "测试主组合候选 2。",
                "theme_tags": ["宽基"],
            },
            {
                "code": "562103",
                "total_score": 90,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "测试主组合候选 3。",
                "theme_tags": ["红利"],
            },
            {
                "code": "562104",
                "total_score": 82,
                "conclusion": "谨慎观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "测试宽基补位候选。",
                "theme_tags": ["宽基"],
            },
        ],
    )
    for code in ["562101", "562102", "562103", "562104"]:
        await _seed_observation_price_series(app, code=code)

    response = await client.get("/api/short-research/observation-portfolio?asset_type=etf&limit=4")

    assert response.status_code == 200
    body = response.json()
    assert body["unavailable_reason"] is None
    assert body["target_invested_weight"] == 1.0
    assert body["weight_sum"] == 1.0
    assert body["cash_weight"] == 0.0
    assert len(body["items"]) == 3
    assert len(body["defensive_items"]) == 1
    assert any(item["code"] == "562104" for item in body["defensive_items"])
    assert all(item["target_weight"] <= 0.3 for item in [*body["items"], *body["defensive_items"]])
    assert body["data_as_of_time"] is not None
    assert body["daily_signal_date"] == required_etf_snapshot_trade_date().isoformat()
    assert body["portfolio_generated_at"] is not None
    assert any(item["weight_reason_json"].get("weight_fill_reason") for item in body["defensive_items"])


@pytest.mark.asyncio
async def test_assets_endpoint_uses_cached_signal_items_and_paginates(client, app, monkeypatch) -> None:
    await _seed_cached_etf_signals(app, count=4)

    async def fail_full_recompute(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("assets endpoint should not recompute the full ETF universe")

    monkeypatch.setattr(short_research_service, "list_computed_assets", fail_full_recompute)

    response = await client.get(
        "/api/short-research/assets?asset_type=etf&universe=default&limit=2&offset=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [item["code"] for item in body["items"]] == ["562001", "562002"]
    assert body["items"][0]["latest_date"] == "2026-06-12"
    assert body["items"][0]["latest_value"] == 2.23
    assert body["items"][0]["usable_days"] == 120


@pytest.mark.asyncio
async def test_assets_endpoint_filters_labels_before_pagination(client, app, monkeypatch) -> None:
    await _seed_cached_etf_signals(
        app,
        count=4,
        item_overrides={
            3: {
                "conclusion": "高位观察",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "测试高位冲高，不应混入健康回踩筛选。",
            }
        },
    )

    async def fail_full_recompute(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("assets endpoint should filter cached signal items")

    monkeypatch.setattr(short_research_service, "list_computed_assets", fail_full_recompute)

    observed = await client.get(
        "/api/short-research/assets?asset_type=etf&limit=2&observation_labels=高位观察"
    )
    assert observed.status_code == 200
    observed_body = observed.json()
    assert observed_body["total"] == 1
    assert [item["code"] for item in observed_body["items"]] == ["562003"]

    entry = await client.get(
        "/api/short-research/assets?asset_type=etf&limit=2&entry_labels=健康回踩"
    )
    assert entry.status_code == 200
    entry_body = entry.json()
    assert entry_body["total"] == 3
    assert all(item["entry_timing_label"] == "健康回踩" for item in entry_body["items"])


@pytest.mark.asyncio
async def test_observation_portfolio_uses_cached_signals(client, app, monkeypatch) -> None:
    await _seed_cached_etf_signals(app, count=4)

    async def fail_full_recompute(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("observation portfolio should not recompute the full ETF universe")

    monkeypatch.setattr(short_research_service, "list_computed_assets", fail_full_recompute)

    response = await client.get("/api/short-research/observation-portfolio?asset_type=etf&limit=4")

    assert response.status_code == 200
    body = response.json()
    assert body["research_only"] is True
    assert len(body["items"]) == 4
    assert [item["code"] for item in body["items"]] == ["562000", "562001", "562002", "562003"]
    assert body["items"][0]["weight_explanation"]
    assert body["items"][0]["decision_factors"]
    assert body["weight_sum"] == 1.0
    assert body["cash_weight"] == 0.0
    assert abs(sum(item["target_weight"] for item in body["items"]) - 1) < 0.01


@pytest.mark.asyncio
async def test_status_endpoint_is_lightweight_by_default(client, app, monkeypatch) -> None:
    await _seed_cached_etf_signals(app, count=2)

    async def fail_health_scan(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("status endpoint should not scan full data health by default")

    async def fail_default_recompute(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("status endpoint should not recompute default ETF assets")

    monkeypatch.setattr(short_research_service, "data_health", fail_health_scan)
    monkeypatch.setattr(short_research_service, "list_computed_assets", fail_default_recompute)

    response = await client.get("/api/short-research/status")

    assert response.status_code == 200
    body = response.json()
    assert body["etf_default_display_count"] == 2
    assert body["data_health"] == []


@pytest.mark.asyncio
async def test_scoped_etf_status_avoids_universe_seed_and_full_item_load(
    client,
    app,
    monkeypatch,
) -> None:
    run_id = await _seed_cached_etf_signals(app, count=2)
    async with app.state.db.session() as session:
        seeded_run = await session.get(ShortResearchSignalRun, run_id)
    assert seeded_run is not None

    async def fail_expensive_path(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("scoped ETF status should use persisted counts")

    async def latest_seeded_run(*_args: Any, **_kwargs: Any) -> ShortResearchSignalRun:
        return seeded_run

    monkeypatch.setattr(
        short_research_service,
        "ensure_short_research_universe",
        fail_expensive_path,
    )
    monkeypatch.setattr(
        short_research_service,
        "list_signal_items",
        fail_expensive_path,
    )
    monkeypatch.setattr(
        short_research_service,
        "latest_signal_run",
        latest_seeded_run,
    )

    response = await client.get("/api/short-research/status?asset_type=etf")

    assert response.status_code == 200
    body = response.json()
    assert body["etf_default_display_count"] == 2
    assert body["data_health"] == []


@pytest.mark.asyncio
async def test_dynamic_etf_sync_batches_and_prioritizes_tracked_etfs(app, monkeypatch) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="560201",
                    name="普通主题ETF",
                    exchange="SH",
                    theme_tags_json=["主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                ),
                TradableEtf(
                    code="560202",
                    name="默认关注ETF",
                    exchange="SH",
                    theme_tags_json=["主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                TradableEtf(
                    code="560203",
                    name="已追踪ETF",
                    exchange="SH",
                    theme_tags_json=["主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                ),
                TrackedPosition(
                    user_id=1,
                    asset_type="etf",
                    asset_code="560203",
                    asset_name="已追踪ETF",
                    buy_date=date(2026, 6, 1),
                    buy_amount=3000,
                    status="active",
                ),
            ]
        )
        await session.commit()

        calls: list[list[str]] = []

        async def fake_etf_sync(_session: Any, _from_date: date, _to_date: date, codes: list[str] | None = None) -> dict[str, Any]:
            batch = list(codes or [])
            calls.append(batch)
            return {"etfs": len(batch), "inserted": 0, "updated": 0, "failed": 0, "failures": []}

        monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", "2")
        monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

        result = await sync_short_research_data_with_tracking_priority(
            session,
            from_date=date(2026, 6, 1),
            to_date=date(2026, 6, 5),
            asset_type="etf",
            codes=["560201", "560202", "560203"],
        )

    assert calls[0][0] == "560203"
    assert calls[0][1] == "560202"
    assert calls[1] == ["560201"]
    assert result["etfs"]["batches"] == 2
    assert result["etfs"]["skipped"] == 0
    assert result["asset_count"] == 3


@pytest.mark.asyncio
async def test_dynamic_etf_sync_limits_daily_batches_when_codes_are_not_explicit(app, monkeypatch) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=f"56100{index}",
                    name=f"批量测试ETF{index}",
                    exchange="SH",
                    theme_tags_json=["批量"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                )
                for index in range(5)
            ]
        )
        await session.commit()

        calls: list[list[str]] = []

        async def fake_etf_sync(
            _session: Any, _from_date: date, _to_date: date, codes: list[str] | None = None
        ) -> dict[str, Any]:
            batch = list(codes or [])
            calls.append(batch)
            return {"etfs": len(batch), "inserted": 0, "updated": 0, "failed": 0, "failures": []}

        monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", "2")
        monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_MAX_BATCHES", "1")
        monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

        result = await short_research_service.sync_short_research_data(
            session,
            from_date=date(2026, 6, 1),
            to_date=date(2026, 6, 5),
            asset_type="etf",
        )

    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert result["etfs"]["batches"] == 1
    assert result["etfs"]["batches_total"] >= 3
    assert result["etfs"]["processed"] == 2
    assert result["etfs"]["skipped"] >= 3


@pytest.mark.asyncio
async def test_bounded_etf_sync_rotates_regular_codes_without_starvation(app, monkeypatch) -> None:
    codes = ["561100", "561101", "561102", "561103", "561104"]
    calls: list[list[str]] = []

    async def fake_dynamic_etf_codes(_session: Any, _codes: list[str] | None = None) -> list[str]:
        return codes

    async def fake_etf_sync(
        session: Any, _from_date: date, sync_to_date: date, batch_codes: list[str] | None = None
    ) -> dict[str, Any]:
        batch = list(batch_codes or [])
        calls.append(batch)
        for code in batch:
            existing = await session.scalar(
                select(EtfPriceHistory.id).where(
                    EtfPriceHistory.etf_code == code,
                    EtfPriceHistory.trade_date == sync_to_date,
                )
            )
            if existing is not None:
                continue
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=sync_to_date,
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=0.0,
                )
            )
        return {"etfs": len(batch), "inserted": 0, "updated": 0, "failed": 0, "failures": []}

    monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", "2")
    monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_MAX_BATCHES", "1")
    monkeypatch.setattr(short_research_service, "_dynamic_etf_codes", fake_dynamic_etf_codes)
    monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="561100",
                    name="跟踪优先ETF",
                    exchange="SH",
                    theme_tags_json=["批量"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                ),
                TradableEtf(
                    code="561101",
                    name="默认展示ETF",
                    exchange="SH",
                    theme_tags_json=["批量"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                *[
                    TradableEtf(
                        code=code,
                        name=f"普通ETF{code}",
                        exchange="SH",
                        theme_tags_json=["批量"],
                        trading_rule_label="证券账户 T+1 ETF",
                        asset_class="sector",
                        is_short_term_eligible=True,
                        is_watchlist=False,
                    )
                    for code in codes[2:]
                ],
                EtfPriceHistory(
                    etf_code="561103",
                    trade_date=date(2026, 6, 4),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=0.0,
                ),
                EtfPriceHistory(
                    etf_code="561104",
                    trade_date=date(2026, 6, 5),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=0.0,
                ),
            ]
        )
        await session.commit()
        await short_research_service.sync_short_research_data(
            session,
            from_date=date(2026, 6, 1),
            to_date=date(2026, 6, 5),
            asset_type="etf",
            priority_etf_codes=["561100"],
        )

    for _ in range(2):
        async with app.state.db.session() as session:
            await short_research_service.sync_short_research_data(
                session,
                from_date=date(2026, 6, 1),
                to_date=date(2026, 6, 5),
                asset_type="etf",
                priority_etf_codes=["561100"],
            )

    monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", "1")
    for _ in range(4):
        async with app.state.db.session() as session:
            await short_research_service.sync_short_research_data(
                session,
                from_date=date(2026, 6, 1),
                to_date=date(2026, 6, 5),
                asset_type="etf",
                priority_etf_codes=["561100"],
            )

    assert calls == [
        ["561100", "561102"],
        ["561101", "561103"],
        ["561100", "561104"],
        ["561101"],
        ["561102"],
        ["561100"],
        ["561103"],
    ]


@pytest.mark.asyncio
async def test_bounded_etf_sync_finishes_all_gaps_before_rotating_current_codes(app, monkeypatch) -> None:
    to_date = date(2026, 6, 5)
    priority_gaps = [f"5620{index:02d}" for index in range(20)]
    priority_current = [f"5620{index:02d}" for index in range(90, 95)]
    regular_gaps = [f"5630{index:02d}" for index in range(20)]
    regular_current = [f"5630{index:02d}" for index in range(90, 95)]
    codes = [*priority_gaps, *priority_current, *regular_gaps, *regular_current]
    calls: list[list[str]] = []

    async def fake_dynamic_etf_codes(_session: Any, _codes: list[str] | None = None) -> list[str]:
        return codes

    async def fake_etf_sync(
        session: Any, _from_date: date, sync_to_date: date, batch_codes: list[str] | None = None
    ) -> dict[str, Any]:
        batch = list(batch_codes or [])
        calls.append(batch)
        for code in batch:
            existing = await session.scalar(
                select(EtfPriceHistory.id).where(
                    EtfPriceHistory.etf_code == code,
                    EtfPriceHistory.trade_date == sync_to_date,
                )
            )
            if existing is not None:
                continue
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=sync_to_date,
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=0.0,
                )
            )
        return {"etfs": len(batch), "inserted": len(batch), "updated": 0, "failed": 0, "failures": []}

    monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", "20")
    monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_MAX_BATCHES", "1")
    monkeypatch.setattr(short_research_service, "_dynamic_etf_codes", fake_dynamic_etf_codes)
    monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=code,
                    name=f"批次ETF{code}",
                    exchange="SH",
                    theme_tags_json=["批量"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                )
                for code in codes
            ]
        )
        session.add_all(
            [
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=to_date,
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=0.0,
                )
                for code in [*priority_current, *regular_current]
            ]
        )
        await session.commit()

        for _ in range(2):
            await short_research_service.sync_short_research_data(
                session,
                from_date=date(2026, 6, 1),
                to_date=to_date,
                asset_type="etf",
                priority_etf_codes=[*priority_gaps, *priority_current],
            )

    selected = [code for batch in calls for code in batch]
    assert len(calls) == 2
    assert all(len(batch) == 20 for batch in calls)
    assert set(selected) == set([*priority_gaps, *regular_gaps])
    assert len(selected) == len(set(selected))
