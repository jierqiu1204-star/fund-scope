from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import insert

from app.models.entities import EtfPriceHistory, TradableEtf
from app.services.short_etf.bounded_history_sync import run_bounded_history_sync_slice
from app.services.short_etf.data import ProviderFetchResult
from app.services.workflows.etf_publish_readiness import (
    CONSERVATIVE_PUBLICATION_PROFILE,
    MAXIMUM_PUBLICATION_PROFILE,
    build_publication_readiness_request,
    publication_window_start,
)


@pytest.mark.asyncio
async def test_postgresql_shaped_1500_by_61_publication_profiles(app) -> None:
    trade_date = date(2026, 7, 20)
    codes = tuple(f"51{index:04d}" for index in range(1_500))
    base_request = build_publication_readiness_request(
        profile=CONSERVATIVE_PUBLICATION_PROFILE,
        trade_date=trade_date,
        from_date=publication_window_start(trade_date),
        contract_hash="a" * 64,
        universe_hash="b" * 64,
        eligible_codes=codes,
    )
    prior_dates = base_request.required_trade_dates[:-1]
    provider_rows = [
        {
            "date": item.isoformat(),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1_000_000.0,
            "turnover": 100_000_000.0,
            "pct_change": 0.0,
            "research_adjusted_value": 1.0,
            "research_price_basis": "total_return_adjusted",
            "provider_version": "benchmark-hfq-v1",
            "adjustment_version": "benchmark-hfq-v1",
        }
        for item in base_request.required_trade_dates
    ]

    async def fetch(*_args: object) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=provider_rows,
            provider="eastmoney",
            fallback_used=False,
        )

    async with app.state.db.session() as session:
        await session.execute(
            insert(TradableEtf),
            [
                {
                    "code": code,
                    "name": f"ETF-{code}",
                    "exchange": "SH",
                    "theme_tags_json": [],
                    "trading_rule_label": "证券账户 T+1 ETF",
                    "asset_class": "sector",
                    "is_short_term_eligible": True,
                    "is_watchlist": False,
                }
                for code in codes
            ],
        )
        await session.commit()
        history_page: list[dict[str, object]] = []
        for code in codes:
            for session_date in prior_dates:
                history_page.append(
                    {
                        "etf_code": code,
                        "trade_date": session_date,
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1_000_000.0,
                        "turnover": 100_000_000.0,
                        "pct_change": 0.0,
                        "raw_price_basis": "raw_ohlc",
                        "research_adjusted_value": 1.0,
                        "research_price_basis": "total_return_adjusted",
                        "data_provider": "eastmoney",
                        "provider_version": "benchmark-hfq-v1",
                        "adjustment_version": "benchmark-hfq-v1",
                        "decision_eligible": True,
                    }
                )
                if len(history_page) == 5_000:
                    await session.execute(insert(EtfPriceHistory), history_page)
                    await session.commit()
                    history_page.clear()
        if history_page:
            await session.execute(insert(EtfPriceHistory), history_page)
            await session.commit()

        measurements: dict[str, list[dict[str, float | int | str | None]]] = {
            "conservative": [],
            "maximum": [],
        }
        checkpoints: list[str] = []
        for profile in (
            CONSERVATIVE_PUBLICATION_PROFILE,
            MAXIMUM_PUBLICATION_PROFILE,
        ):
            request = replace(
                base_request,
                max_codes=profile.max_codes,
            )
            for _sample in range(3):
                result = await run_bounded_history_sync_slice(
                    session,
                    request=request,
                    fetcher=fetch,
                )
                checkpoint = result.last_durable_checkpoint or {}
                if checkpoint.get("active_code"):
                    checkpoints.append(str(checkpoint["active_code"]))
                measurements[profile.name].append(
                    {
                        "elapsed_seconds": result.elapsed_seconds,
                        "peak_rss_bytes": result.peak_rss_bytes,
                        "rows_per_second": round(
                            result.persisted_rows
                            / max(result.elapsed_seconds, 0.000001),
                            3,
                        ),
                        "sql_statements": result.sql_statements,
                        "max_page_sql_statements": result.max_page_sql_statements,
                        "stop_reason": result.stop_reason,
                    }
                )

    maximum_elapsed = sorted(
        float(item["elapsed_seconds"]) for item in measurements["maximum"]
    )
    p95_elapsed = maximum_elapsed[-1]
    assert p95_elapsed < 30
    assert all(
        int(item["peak_rss_bytes"]) < 512 * 1024 * 1024
        for profile in measurements.values()
        for item in profile
    )
    assert all(
        int(item["max_page_sql_statements"]) <= 8
        for profile in measurements.values()
        for item in profile
    )
    assert all(
        float(item["elapsed_seconds"]) < 60
        for profile in measurements.values()
        for item in profile
    )
    assert checkpoints == sorted(set(checkpoints))
    print(json.dumps(measurements, ensure_ascii=False, sort_keys=True))
