from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.entities import IndexValuationHistory
from app.services.valuation import compute_percentile


def test_compute_percentile_uses_available_window_when_history_is_short() -> None:
    values = [10, 12, 14, 16]

    result = compute_percentile(values=values, current_value=14, max_window_days=3650)

    assert result.percentile == 75.0
    assert result.effective_window == 4


@pytest.mark.asyncio
async def test_current_valuation_endpoint_returns_latest_snapshot(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            IndexValuationHistory(
                index_code="CSI300",
                valuation_date=date(2026, 5, 1),
                pe=12.3,
                pb=1.4,
                dividend_yield=2.1,
                pe_percentile=15,
                pb_percentile=22,
                effective_window=3650,
            )
        )
        await session.commit()

    response = await client.get("/api/valuation/current")

    assert response.status_code == 200
    assert response.json()[0]["index_code"] == "CSI300"
    assert response.json()[0]["pe_percentile"] == 15


@pytest.mark.asyncio
async def test_valuation_history_endpoint_returns_rows_sorted_ascending(client, app) -> None:
    async with app.state.db.session() as session:
        base_date = date(2026, 5, 1)
        for offset, pe in enumerate([10.0, 11.0, 12.0]):
            session.add(
                IndexValuationHistory(
                    index_code="CSI300",
                    valuation_date=base_date + timedelta(days=offset),
                    pe=pe,
                    pb=1.2,
                    dividend_yield=2.0,
                    pe_percentile=offset * 10,
                    pb_percentile=offset * 10,
                    effective_window=3650,
                )
            )
        await session.commit()

    response = await client.get("/api/valuation/CSI300/history?from=2026-05-01&to=2026-05-03")

    assert response.status_code == 200
    assert [row["date"] for row in response.json()] == ["2026-05-01", "2026-05-02", "2026-05-03"]
