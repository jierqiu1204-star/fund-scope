from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.entities import (
    EtfPriceHistory,
    EtfUniverseMembership,
    JobRun,
    TradableEtf,
)
from app.services.workflows.etf_history_readiness import read_etf_history_readiness


def _etf(code: str, *, eligible: bool = True) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"ETF-{code}",
        exchange="SH",
        theme_tags_json=[],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="sector",
        is_short_term_eligible=eligible,
        is_watchlist=False,
    )


def _membership(
    code: str,
    *,
    effective_from: date,
    effective_to: date | None = None,
) -> EtfUniverseMembership:
    return EtfUniverseMembership(
        etf_code=code,
        effective_from=effective_from,
        effective_to=effective_to,
        source="fixture-authoritative-universe",
    )


def _price(code: str, trade_date: date, *, eligible: bool) -> EtfPriceHistory:
    close = 1.0
    return EtfPriceHistory(
        etf_code=code,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
        turnover=100_000_000.0,
        pct_change=0.0,
        research_adjusted_value=close if eligible else None,
        research_price_basis="total_return_adjusted" if eligible else None,
        data_provider="eastmoney" if eligible else "sina",
        provider_version="fixture-hfq-v1" if eligible else None,
        adjustment_version="fixture-hfq-v1" if eligible else None,
        decision_eligible=eligible,
    )


@pytest.mark.asyncio
async def test_readiness_separates_current_freshness_from_61_session_depth(app) -> None:
    target = date(2026, 7, 17)
    sessions = [target - timedelta(days=offset) for offset in reversed(range(61))]
    async with app.state.db.session() as session:
        session.add_all([_etf("510701"), _etf("510702")])
        session.add_all(
            [
                _membership("510701", effective_from=sessions[0]),
                _membership("510702", effective_from=sessions[0]),
            ]
        )
        session.add_all(_price("510701", day, eligible=True) for day in sessions)
        session.add(_price("510702", target, eligible=True))
        session.add_all(_price("510702", day, eligible=False) for day in sessions[:-1])
        await session.commit()

        report = await read_etf_history_readiness(
            session,
            target_date=target,
            horizons=(5,),
        )

    assert report["daily_freshness"]["covered_count"] == 2
    assert report["history_depth_61"]["covered_count"] == 1
    assert report["history_depth_61"]["pending_codes"] == ["510702"]
    assert report["contract_depth"]["required_sessions"] == 200
    assert report["contract_depth"]["covered_count"] == 0
    assert report["telemetry_depth_180"]["authoritative"] is False
    assert report["telemetry_depth_500"]["required_sessions"] == 500
    assert report["telemetry_depth_500"]["authoritative"] is False
    assert report["historical_production_snapshots"]["ready"] is False
    assert report["history_publication_gate_passed"] is False
    assert report["publication_coverage_thresholds"] == {
        "daily_freshness": 0.95,
        "history_depth_61": 0.90,
    }


@pytest.mark.asyncio
async def test_readiness_uses_ranking_point_in_time_universe_denominator(app) -> None:
    target = date(2026, 7, 17)
    start = target - timedelta(days=90)
    async with app.state.db.session() as session:
        session.add_all(
            [
                _etf("510710"),
                _etf("510711"),
                _etf("510712", eligible=False),
            ]
        )
        session.add_all(
            [
                _membership("510710", effective_from=start),
                _membership("510711", effective_from=target + timedelta(days=1)),
                _membership(
                    "510712",
                    effective_from=start,
                    effective_to=target + timedelta(days=1),
                ),
            ]
        )
        session.add_all(
            [
                _price("510710", target, eligible=True),
                _price("510711", target, eligible=True),
                _price("510712", target, eligible=True),
            ]
        )
        await session.commit()

        report = await read_etf_history_readiness(
            session,
            target_date=target,
            horizons=(5,),
        )

    assert report["universe"]["source"] == "ranking_point_in_time_snapshot"
    assert report["universe"]["codes"] == ["510710", "510712"]
    assert len(report["universe"]["snapshot_hash"]) == 64
    for lane_name in ("daily_freshness", "history_depth_61", "contract_depth"):
        lane = report[lane_name]
        assert lane["expected_count"] == 2
        assert set(lane) >= {
            "attempted_count",
            "covered_count",
            "eligible_count",
            "excluded_count",
        }
    assert report["daily_freshness"]["covered_codes"] == ["510710", "510712"]


@pytest.mark.asyncio
async def test_short_etf_data_status_exposes_independent_history_lanes(client) -> None:
    response = await client.get("/api/short-etf/data-status")

    assert response.status_code == 200
    readiness = response.json()["summary"]["history_readiness"]
    assert readiness["daily_freshness"]["scope"] == "daily_freshness"
    assert readiness["history_depth_61"]["scope"] == "history_depth_61"
    assert readiness["contract_depth"]["scope"].startswith("history_depth_required:")
    assert readiness["telemetry_depth_500"]["scope"] == "history_depth_500_telemetry"


@pytest.mark.asyncio
async def test_readiness_exposes_bounded_continuation_health(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            JobRun(
                job_name="etf_history_continuation:history_depth_61",
                status="partial",
                details_json={
                    "identity_hash": "a" * 64,
                    "stop_reason": "provider_circuit_open",
                    "last_completed_code": "510001",
                    "last_trade_date": "2026-07-16",
                    "remaining_candidate_count": 17,
                    "elapsed_seconds": 24.5,
                    "peak_rss_bytes": 128 * 1024 * 1024,
                    "rows_per_second": 20.0,
                    "sql_statements": 8,
                    "retries": 1,
                    "provider_attempt_count": 3,
                    "circuit_state": "open",
                    "exclusions": [["510002", "provider_timeout"]],
                },
            )
        )
        await session.commit()

        report = await read_etf_history_readiness(session)

    health = report["synchronization_health"]["history_depth_61"]
    assert health["status"] == "partial"
    assert health["stop_reason"] == "provider_circuit_open"
    assert health["remaining_candidate_count"] == 17
    assert health["circuit_state"] == "open"
