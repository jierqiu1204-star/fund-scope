from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import EtfIntradayQuote, TradableEtf
from app.services.short_research import service as short_research_service
from app.services.short_research.service import (
    ComputedAsset,
    PricePoint,
    _score_metrics,
    _v3_premium_inputs,
    _v3_structure_inputs,
    _v3_theme_catalyst_inputs,
    _with_final_score_v3_shadow,
    compute_asset_for_replay_from_series,
)


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


def _flat_then_gap_series(final_value: float) -> list[PricePoint]:
    start = date(2026, 1, 1)
    values = [100.0] * 20 + [final_value]
    return [
        PricePoint(
            point_date=start + timedelta(days=index),
            value=value,
            high=value + 1.0,
            low=value - 1.0,
            turnover=100_000_000,
        )
        for index, value in enumerate(values)
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


def test_score_metrics_passes_adjusted_ranges_to_atr_context() -> None:
    start = date(2026, 1, 1)
    series = [
        PricePoint(
            point_date=start + timedelta(days=index),
            value=1.0,
            high=1.02,
            low=0.98,
            turnover=100_000_000,
        )
        for index in range(30)
    ]

    metrics = _score_metrics(_etf_metadata(), series, series[-1].point_date)

    assert metrics["dynamic_threshold_context"]["atr_style_20d_pct"] == 4.0


def test_overextension_atr_uses_adjusted_atr20_and_penalizes_both_directions() -> None:
    upward = _flat_then_gap_series(110.0)
    downward = _flat_then_gap_series(90.0)

    upward_metrics = _score_metrics(_etf_metadata(), upward, upward[-1].point_date)
    downward_metrics = _score_metrics(_etf_metadata(), downward, downward[-1].point_date)

    expected = 9.5 / ((19 * 2.0 + 11.0) / 20)
    assert upward_metrics["overextension_atr"] == pytest.approx(expected)
    assert downward_metrics["overextension_atr"] == pytest.approx(expected)
    assert upward_metrics["overextension_atr_status"] == "available_adjusted_atr20"
    assert upward_metrics["effective_windows"]["overextension_atr"] == {
        "close_count": 21,
        "true_range_count": 20,
        "required_close_count": 21,
        "required_true_range_count": 20,
    }


def test_overextension_atr_fails_closed_without_adjusted_ranges() -> None:
    metrics = _score_metrics(_etf_metadata(), _price_series(21), date(2026, 1, 21))
    zero_range = [
        PricePoint(
            point_date=date(2026, 1, 1) + timedelta(days=index),
            value=100.0,
            high=100.0,
            low=100.0,
        )
        for index in range(21)
    ]
    zero_range_metrics = _score_metrics(_etf_metadata(), zero_range, zero_range[-1].point_date)

    assert metrics["overextension_atr"] is None
    assert metrics["overextension_atr_status"] == "unavailable_missing_adjusted_range"
    assert zero_range_metrics["overextension_atr"] is None
    assert zero_range_metrics["overextension_atr_status"] == "unavailable_non_positive_adjusted_atr20"


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


def test_replay_production_path_persists_missing_v3_market_inputs() -> None:
    asset = compute_asset_for_replay_from_series(
        _etf_metadata(),
        series=[
            PricePoint(point_date=date(2026, 1, 1) + timedelta(days=index), value=1.0 + index * 0.01)
            for index in range(19)
        ],
        as_of_date=date(2026, 1, 19),
    )

    assert asset.metrics["distance_to_ma20_pct"] is None
    assert asset.metrics["average_turnover_20d"] is None
    assert asset.metrics["average_turnover_60d"] is None
    assert asset.metrics["effective_windows"]["ma20"]["close_count"] == 19
    assert asset.metrics["effective_windows"]["average_turnover_60d"]["observation_count"] == 0


def test_v3_premium_input_requires_a_fresh_same_day_decision_quote() -> None:
    decision_cutoff = datetime(2026, 3, 1, 15, 0)
    fresh_quote = SimpleNamespace(
        trade_date=date(2026, 3, 1),
        quote_time=datetime(2026, 3, 1, 14, 50),
        latest_price=1.0033,
        iopv=1.0,
        premium_discount_pct=0.33,
        freshness_status="fresh",
        raw_json={
            "quote_time_is_fallback": False,
            "decision_eligible": True,
            "premium_consensus_status": "consistent",
            "premium_provider_consensus": 100,
            "premium_provider_count": 2,
            "premium_dispersion_bps": 5.0,
            "consensus_status": "consistent",
            "provider_quotes": [
                {
                    "provider": "akshare",
                    "quote_time": "2026-03-01T14:50:00",
                    "latest_price": 1.0033,
                    "iopv": 1.0,
                    "quote_time_is_fallback": False,
                },
                {
                    "provider": "eastmoney",
                    "quote_time": "2026-03-01T14:50:00",
                    "latest_price": 1.0038,
                    "iopv": 1.0,
                    "quote_time_is_fallback": False,
                },
            ],
        },
    )

    fresh = _v3_premium_inputs(
        fresh_quote,
        as_of_date=date(2026, 3, 1),
        decision_cutoff=decision_cutoff,
    )
    stale = _v3_premium_inputs(
        fresh_quote,
        as_of_date=date(2026, 3, 2),
        decision_cutoff=decision_cutoff,
    )

    assert fresh["premium_discount_bps"] == 33.0
    assert fresh["premium_provider_consensus"] == 100.0
    assert fresh["premium_input_reliability"] == "verified"
    assert stale["premium_discount_bps"] is None
    assert stale["premium_input_reliability"] == "stale"


def test_v3_premium_rejects_quote_after_decision_cutoff() -> None:
    quote = SimpleNamespace(
        trade_date=date(2026, 3, 1),
        quote_time=datetime(2026, 3, 1, 15, 1),
        latest_price=1.001,
        iopv=1.0,
        premium_discount_pct=0.1,
        freshness_status="fresh",
        raw_json={
            "quote_time_is_fallback": False,
            "decision_eligible": True,
            "premium_consensus_status": "consistent",
            "premium_provider_consensus": 100,
            "premium_provider_count": 2,
            "premium_dispersion_bps": 5.0,
        },
    )

    result = _v3_premium_inputs(
        quote,
        as_of_date=date(2026, 3, 1),
        decision_cutoff=datetime(2026, 3, 1, 15, 0),
    )

    assert result["premium_discount_bps"] is None
    assert result["premium_input_status"] == "after_decision_cutoff"
    assert result["premium_input_reliability"] == "unavailable"


def test_v3_premium_maps_single_and_diverged_provider_reliability() -> None:
    base_quote = {
        "trade_date": date(2026, 3, 1),
        "quote_time": datetime(2026, 3, 1, 14, 50),
        "latest_price": 1.001,
        "iopv": 1.0,
        "premium_discount_pct": 0.1,
        "freshness_status": "fresh",
    }
    single = SimpleNamespace(
        **base_quote,
        raw_json={
            "quote_time_is_fallback": False,
            "decision_eligible": True,
            "premium_consensus_status": "single_provider",
            "premium_provider_consensus": 60,
            "premium_provider_count": 1,
            "consensus_status": "single_provider",
            "provider_quotes": [
                {
                    "provider": "akshare",
                    "quote_time": "2026-03-01T14:50:00",
                    "latest_price": 1.001,
                    "iopv": 1.0,
                    "quote_time_is_fallback": False,
                }
            ],
        },
    )
    diverged = SimpleNamespace(
        **base_quote,
        raw_json={
            "quote_time_is_fallback": False,
            "decision_eligible": True,
            "premium_consensus_status": "diverged",
            "premium_provider_consensus": None,
            "premium_provider_count": 2,
            "premium_dispersion_bps": 31.0,
            "consensus_status": "consistent",
            "provider_quotes": [
                {
                    "provider": "akshare",
                    "quote_time": "2026-03-01T14:50:00",
                    "latest_price": 1.001,
                    "iopv": 1.0,
                    "quote_time_is_fallback": False,
                },
                {
                    "provider": "eastmoney",
                    "quote_time": "2026-03-01T14:50:00",
                    "latest_price": 1.001,
                    "iopv": 0.99,
                    "quote_time_is_fallback": False,
                },
            ],
        },
    )
    cutoff = datetime(2026, 3, 1, 15, 0)

    single_result = _v3_premium_inputs(single, as_of_date=date(2026, 3, 1), decision_cutoff=cutoff)
    diverged_result = _v3_premium_inputs(diverged, as_of_date=date(2026, 3, 1), decision_cutoff=cutoff)

    assert single_result["premium_provider_consensus"] == 60.0
    assert single_result["premium_input_reliability"] == "alternate_provider"
    assert diverged_result["premium_provider_consensus"] is None
    assert diverged_result["premium_input_reliability"] == "unavailable"


def test_v3_theme_input_requires_current_sourced_effective_event() -> None:
    cutoff = datetime(2026, 3, 1, 15)
    fresh_event = SimpleNamespace(
        id=7,
        status="active",
        source_url="https://example.com/event",
        effective_start=date(2026, 2, 28),
        effective_end=date(2026, 3, 3),
        updated_at=datetime(2026, 2, 28, 16),
        strength_score=90.0,
        confidence_score=80.0,
    )
    stale_event = SimpleNamespace(
        **{
            **fresh_event.__dict__,
            "updated_at": datetime(2026, 2, 20, 16),
        }
    )

    fresh = _v3_theme_catalyst_inputs([fresh_event], as_of_date=date(2026, 3, 1), cutoff=cutoff)
    stale = _v3_theme_catalyst_inputs([stale_event], as_of_date=date(2026, 3, 1), cutoff=cutoff)

    assert fresh["catalyst_quality"] == 90.0
    assert fresh["catalyst_confidence"] == 80.0
    assert fresh["catalyst_input_reliability"] == "alternate_provider"
    assert stale["catalyst_quality"] is None
    assert stale["catalyst_input_reliability"] == "unavailable"


def test_v3_structure_input_uses_only_a_fresh_quoted_spread_and_quality_gate() -> None:
    quote = SimpleNamespace(
        trade_date=date(2026, 3, 1),
        quote_time=datetime(2026, 3, 1, 10),
        bid_price=1.0,
        ask_price=1.002,
        freshness_status="fresh",
        raw_json={
            "quote_time_is_fallback": False,
            "decision_eligible": True,
            "consensus_status": "consistent",
        },
    )

    structure = _v3_structure_inputs(
        quote,
        metrics={"data_quality_score": 88.0, "default_display_eligible": True},
        as_of_date=date(2026, 3, 1),
        decision_cutoff=datetime(2026, 3, 1, 10, 5),
    )

    assert structure["spread_bps"] == 19.98
    assert structure["structure_quality"] == 88.0
    assert structure["structure_input_reliability"] == "verified"


@pytest.mark.asyncio
async def test_v3_shadow_propagates_verified_premium_reliability(app, monkeypatch) -> None:
    observed_at = datetime(2026, 3, 1, 14, 50)
    decision_cutoff = datetime(2026, 3, 1, 15, 0)
    metadata = _etf_metadata()
    captured_inputs = {}

    def capture_inputs(inputs, *, manifest):
        captured_inputs.update({item.asset_code: item for item in inputs})
        return {
            item.asset_code: SimpleNamespace(
                asset_bucket=item.asset_bucket,
                ranking_score=None,
                score_eligible=False,
                component_scores={},
                missing_by_component={},
                metric_peer_counts={},
                limitation_reasons=(),
            )
            for item in inputs
        }

    monkeypatch.setattr(short_research_service, "score_final_score_v3", capture_inputs)
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=metadata.code,
                name=metadata.name,
                exchange=metadata.exchange,
                theme_tags_json=list(metadata.theme_tags),
                trading_rule_label=metadata.trading_rule_label,
                asset_class=metadata.category,
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code=metadata.code,
                quote_time=observed_at,
                trade_date=observed_at.date(),
                latest_price=1.0,
                iopv=0.9999,
                premium_discount_pct=0.1,
                    source="fixture",
                    freshness_status="fresh",
                    created_at=observed_at - timedelta(hours=8),
                    raw_json={
                    "quote_time_is_fallback": False,
                    "decision_eligible": True,
                    "consensus_status": "consistent",
                    "premium_consensus_status": "consistent",
                    "premium_provider_consensus": 100,
                    "premium_provider_count": 2,
                    "premium_dispersion_bps": 5.0,
                    "provider_quotes": [
                        {
                            "provider": "akshare",
                            "quote_time": "2026-03-01T14:50:00",
                            "latest_price": 1.0,
                            "iopv": 0.9999,
                            "quote_time_is_fallback": False,
                        },
                        {
                            "provider": "eastmoney",
                            "quote_time": "2026-03-01T14:50:00",
                            "latest_price": 1.0001,
                            "iopv": 1.0,
                            "quote_time_is_fallback": False,
                        },
                    ],
                },
            )
        )
        await session.commit()
        [updated] = await _with_final_score_v3_shadow(
            session,
            [
                ComputedAsset(
                    metadata=metadata,
                    rank=1,
                    total_score=60.0,
                    conclusion="谨慎观察",
                    latest_date=observed_at.date(),
                    latest_value=1.0,
                    usable_days=120,
                    sample_level="充足",
                    metrics={
                        "ranking_asset_bucket": "broad-equity",
                        "ranking_profile_version": "final_score_v3",
                        "theme_profile": {},
                        "default_display_eligible": True,
                        "data_quality_score": 90.0,
                        "component_reliability": {},
                    },
                    score_breakdown={},
                    risk_flags=[],
                    rationale={},
                    source_note="fixture",
                    entry_timing_label="跌破等待",
                    entry_timing_reason="fixture",
                )
            ],
            observed_at.date(),
            decision_cutoff=decision_cutoff,
        )

    assert updated.metrics["premium_input_reliability"] == "verified"
    assert captured_inputs[metadata.code].values["component_reliability"]["premium_discount"] == "verified"
    assert updated.metrics["component_reliability"]["premium_discount"] == "verified"
