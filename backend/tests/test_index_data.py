from __future__ import annotations

from datetime import date

import pytest

from app.services import index_data
from app.services.index_data import fetch_index_valuation, fetch_primary_index_valuation


@pytest.mark.asyncio
async def test_fetch_primary_index_valuation_reads_akshare_columns(monkeypatch) -> None:
    class FakeFrame:
        @property
        def iloc(self):
            return self

        def __getitem__(self, index: int):
            assert index == -1
            return {
                "\u5e02\u76c8\u73871": 12.4,
                "\u5e02\u51c0\u7387": 1.5,
                "\u80a1\u606f\u7387": 2.6,
            }

    def fake_primary(*_: object, **__: object) -> FakeFrame:
        return FakeFrame()

    monkeypatch.setattr(index_data.ak, "stock_zh_index_value_csindex", fake_primary)

    result = await fetch_primary_index_valuation("CSI300", date(2026, 5, 1))

    assert result == {
        "date": date(2026, 5, 1),
        "pe": 12.4,
        "pb": 1.5,
        "dividend_yield": 2.6,
    }


@pytest.mark.asyncio
async def test_fetch_index_valuation_uses_fallback_after_primary_retries_fail(monkeypatch) -> None:
    def fail_primary(*_: object, **__: object) -> None:
        raise RuntimeError("akshare unavailable")

    async def no_retry(_: str, func, **__: object):
        return await func()

    async def fallback(index_code: str, valuation_date: date) -> dict[str, float | date]:
        return {
            "date": valuation_date,
            "pe": 11.2,
            "pb": 1.3,
            "dividend_yield": 2.4,
        }

    monkeypatch.setattr(index_data.ak, "stock_zh_index_value_csindex", fail_primary)
    monkeypatch.setattr(index_data, "retry_async", no_retry)
    monkeypatch.setattr(index_data, "fetch_index_valuation_fallback", fallback, raising=False)

    result = await fetch_index_valuation("CSI300", date(2026, 5, 1))

    assert result == {
        "date": date(2026, 5, 1),
        "pe": 11.2,
        "pb": 1.3,
        "dividend_yield": 2.4,
    }
