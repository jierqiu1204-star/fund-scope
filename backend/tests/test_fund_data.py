from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
import respx
from httpx import Response

from app.services import fund_data
from app.services.fund_data import fetch_fund_nav, parse_eastmoney_nav_response


def _epoch_millis(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp() * 1000)


def _eastmoney_fixture() -> str:
    may_1 = _epoch_millis(date(2026, 5, 1))
    may_2 = _epoch_millis(date(2026, 5, 2))
    return f"""
    var Data_netWorthTrend = [
      {{"x": {may_1}, "y": 1.2345}},
      {{"x": {may_2}, "y": 1.25}}
    ];
    var Data_ACWorthTrend = [
      [{may_1}, 1.4567],
      [{may_2}, 1.47]
    ];
    """


def test_parse_eastmoney_nav_response_filters_range_and_pairs_accumulated_nav() -> None:
    rows = parse_eastmoney_nav_response(
        _eastmoney_fixture(),
        from_date=date(2026, 5, 1),
        to_date=date(2026, 5, 1),
    )

    assert rows == [
        {
            "date": "2026-05-01",
            "nav": 1.2345,
            "accumulated_nav": 1.4567,
        }
    ]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_fund_nav_uses_eastmoney_fallback_when_primary_fails(monkeypatch) -> None:
    def fail_primary(*_: object, **__: object) -> None:
        raise RuntimeError("akshare unavailable")

    async def no_retry(_: str, func, **__: object):
        return await func()

    monkeypatch.setattr(fund_data.ak, "fund_open_fund_info_em", fail_primary)
    monkeypatch.setattr(fund_data, "retry_async", no_retry)
    respx.get("https://fund.eastmoney.com/pingzhongdata/007339.js").mock(
        return_value=Response(200, text=_eastmoney_fixture())
    )

    rows = await fetch_fund_nav("007339", date(2026, 5, 1), date(2026, 5, 2))

    assert [row["date"] for row in rows] == ["2026-05-01", "2026-05-02"]
    assert rows[0]["nav"] == 1.2345
    assert rows[0]["accumulated_nav"] == 1.4567
