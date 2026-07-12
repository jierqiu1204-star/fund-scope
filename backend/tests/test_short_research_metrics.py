from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.services.short_research.service import PricePoint, _score_metrics, _v3_premium_inputs


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


def test_score_metrics_do_not_synthesize_ma20_or_premium_inputs() -> None:
    metadata = _etf_metadata()
    short_metrics = _score_metrics(metadata, _price_series(19), date(2026, 1, 19))
    complete_metrics = _score_metrics(metadata, _price_series(60), date(2026, 3, 1))

    assert short_metrics["ma20"] is None
    assert short_metrics["distance_to_ma20_pct"] is None
    assert short_metrics["effective_windows"]["ma20"] == {
        "close_count": 19,
        "required_close_count": 20,
    }
    assert complete_metrics["average_turnover_60d"] == 100_000_000
    assert complete_metrics["source_trade_date"] == "2026-03-01"
    assert complete_metrics["market_data_reliability"] == "verified"
    assert complete_metrics["ranking_asset_bucket"] == "broad-equity"
    assert complete_metrics["ranking_profile_version"] == "final_score_v3"
    assert complete_metrics["premium_discount_bps"] is None
    assert complete_metrics["premium_input_status"] == "unavailable"
    assert complete_metrics["component_source_dates"]["technical_momentum_cross_section"] == "2026-03-01"
    assert complete_metrics["component_reliability"]["theme_catalyst"] == "unavailable"


def test_v3_premium_input_requires_a_fresh_same_day_decision_quote() -> None:
    now = datetime(2026, 3, 1, 10, 5)
    fresh_quote = SimpleNamespace(
        trade_date=date(2026, 3, 1),
        quote_time=datetime(2026, 3, 1, 10),
        premium_discount_pct=0.33,
        freshness_status="fresh",
        raw_json={"premium_provider_consensus": 95.0},
    )

    fresh = _v3_premium_inputs(fresh_quote, as_of_date=date(2026, 3, 1), now=now)
    stale = _v3_premium_inputs(fresh_quote, as_of_date=date(2026, 3, 2), now=now)

    assert fresh["premium_discount_bps"] == 33.0
    assert fresh["premium_provider_consensus"] == 95.0
    assert fresh["premium_input_reliability"] == "verified"
    assert stale["premium_discount_bps"] is None
    assert stale["premium_input_reliability"] == "stale"
