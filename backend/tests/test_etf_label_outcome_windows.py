from __future__ import annotations

from datetime import date, timedelta

from app.models.entities import EtfPriceHistory
from app.services.short_research.service import _completed_outcome_payload


def _price_row(offset: int, close: float) -> EtfPriceHistory:
    return EtfPriceHistory(
        etf_code="513520",
        trade_date=date(2026, 6, 1) + timedelta(days=offset),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        turnover=100_000_000,
        pct_change=0.0,
    )


def test_outcome_window_remains_pending_until_future_window_completes() -> None:
    status, payload = _completed_outcome_payload([_price_row(0, 1.0), _price_row(1, 1.01)], 3)

    assert status == "pending"
    assert payload["exclusion_reason"] == "missing_future_price"


def test_completed_outcome_uses_full_forward_window() -> None:
    rows = [_price_row(offset, 1.0 + offset * 0.01) for offset in range(4)]

    status, payload = _completed_outcome_payload(rows, 3)

    assert status == "completed"
    assert payload["future_price"] == rows[3].close
    assert payload["forward_return"] > 0
