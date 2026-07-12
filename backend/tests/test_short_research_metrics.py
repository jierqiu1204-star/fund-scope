from __future__ import annotations

from datetime import date, timedelta

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.services.short_research.service import PricePoint, _score_metrics


def _etf_metadata() -> ShortResearchAsset:
    return ShortResearchAsset(
        asset_type=ASSET_TYPE_ETF,
        code="510300",
        name="测试ETF",
        category="broad_index",
        theme_tags=("宽基",),
        investment_direction="测试",
        trading_rule_label="T+1",
        exchange="SH",
    )


def _price_series(count: int) -> list[PricePoint]:
    start = date(2026, 1, 1)
    return [
        PricePoint(
            point_date=start + timedelta(days=index),
            value=1.0 + index * 0.01,
            turnover=100_000_000,
        )
        for index in range(count)
    ]


def test_twenty_day_volatility_requires_twenty_one_eligible_closes() -> None:
    metadata = _etf_metadata()
    twenty_close_metrics = _score_metrics(metadata, _price_series(20), date(2026, 1, 20))
    twenty_one_close_metrics = _score_metrics(metadata, _price_series(21), date(2026, 1, 21))

    assert twenty_close_metrics["volatility_20d"] is None
    assert twenty_close_metrics["effective_windows"]["volatility_20d"] == {
        "close_count": 20,
        "return_count": 19,
        "required_close_count": 21,
        "required_return_count": 20,
    }
    assert twenty_one_close_metrics["volatility_20d"] is not None
    assert twenty_one_close_metrics["effective_windows"]["volatility_20d"]["return_count"] == 20
