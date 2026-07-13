from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.entities import EtfDataHealth, EtfPriceHistory, TradableEtf
from app.services.short_etf import data


class _Frame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def iterrows(self):  # type: ignore[no-untyped-def]
        return enumerate(self.rows)


@pytest.mark.asyncio
async def test_akshare_hfq_prices_attach_versioned_total_return_provenance(monkeypatch) -> None:
    calls: list[str] = []

    def fake_history(*, adjust: str, **_kwargs: object) -> _Frame:
        calls.append(adjust)
        close = 1.0 if adjust == "" else 2.0
        return _Frame(
            [
                {
                    "日期": "2026-07-10",
                    "开盘": close,
                    "最高": close,
                    "最低": close,
                    "收盘": close,
                    "成交量": 100.0,
                    "成交额": 200.0,
                    "涨跌幅": 0.0,
                }
            ]
        )

    monkeypatch.setattr(data.ak, "fund_etf_hist_em", fake_history)

    rows = await data.fetch_akshare_etf_price_history(
        "159605", date(2026, 7, 10), date(2026, 7, 10)
    )

    assert calls == ["", "hfq"]
    assert rows[0]["close"] == 1.0
    assert rows[0]["research_adjusted_value"] == 2.0
    assert rows[0]["research_price_basis"] == data.TOTAL_RETURN_PRICE_BASIS
    assert rows[0]["adjustment_version"] == "akshare.fund_etf_hist_em.hfq_v1"
    assert rows[0]["provider_version"] == "akshare.fund_etf_hist_em.hfq_v1"


@pytest.mark.asyncio
async def test_sync_records_primary_source_failure_for_raw_fallback(app, monkeypatch) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="159605",
                name="新能源ETF",
                exchange="SZ",
                theme_tags_json=["新能源"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        await session.commit()

        async def fallback_history(*_args: object, **_kwargs: object) -> data.ProviderFetchResult:
            return data.ProviderFetchResult(
                rows=[
                    {
                        "date": "2026-07-10",
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 100.0,
                        "turnover": 200.0,
                        "pct_change": 0.0,
                    }
                ],
                provider="sina",
                fallback_used=True,
                primary_error="AKShare: external_call_failed",
            )

        monkeypatch.setattr(data, "fetch_etf_price_history_with_provider", fallback_history)

        await data.sync_etf_price_history(
            session, date(2026, 7, 10), date(2026, 7, 10), ["159605"]
        )
        health = await session.scalar(select(EtfDataHealth).where(EtfDataHealth.etf_code == "159605"))
        price = await session.scalar(select(EtfPriceHistory).where(EtfPriceHistory.etf_code == "159605"))

    assert health is not None
    assert health.status == "success"
    assert health.last_error_message == "AKShare: external_call_failed"
    assert price is not None
    assert price.decision_eligible is False
    assert price.decision_ineligibility_reason == "missing_total_return_provenance"
