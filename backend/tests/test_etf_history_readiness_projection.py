from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import text

from app.models.entities import (
    EtfListingDateObservation,
    EtfPriceHistory,
    EtfUniverseMembership,
    JobRun,
    TradableEtf,
)
from app.services.workflows.etf_history_readiness import (
    _compatible_production_source_date_count,
    read_etf_history_readiness,
)


def _etf(
    code: str,
    *,
    eligible: bool = True,
    listing_date: date | None = date(2020, 1, 1),
) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"ETF-{code}",
        exchange="SH",
        theme_tags_json=[],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="sector",
        is_short_term_eligible=eligible,
        is_watchlist=False,
        listing_date=listing_date,
        listing_date_source=("fixture-authoritative-universe" if listing_date else None),
        listing_date_observed_at=(datetime(2026, 7, 1) if listing_date else None),
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


def _listing(code: str, listing_date: date) -> EtfListingDateObservation:
    evidence_hash = f"{int(code):064x}"[-64:]
    return EtfListingDateObservation(
        etf_code=code,
        exchange="SH",
        listing_date=listing_date,
        source="sse.etf.fundlist",
        provider_version="COMMON_JJZWZ_JJLB_L_v1",
        observed_at=datetime(2026, 7, 1),
        universe_snapshot_hash="a" * 64,
        raw_payload_hash="b" * 64,
        evidence_hash=evidence_hash,
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
        provider_version=("eastmoney.push2his.kline.hfq_v1" if eligible else None),
        source_timestamp=datetime.combine(trade_date, time(6, 0)),
        adjustment_version=("eastmoney.push2his.kline.hfq_v1" if eligible else None),
        decision_eligible=eligible,
    )


@pytest.mark.asyncio
async def test_readiness_separates_current_freshness_from_61_session_depth(app) -> None:
    target = date(2026, 7, 17)
    sessions = [target - timedelta(days=offset) for offset in reversed(range(61))]
    async with app.state.db.session() as session:
        session.add_all([_etf("510701"), _etf("510702")])
        session.add_all(
            [_listing("510701", date(2020, 1, 1)), _listing("510702", date(2020, 1, 1))]
        )
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
        "history_depth_61_preview": 0.90,
        "history_depth_61_complete": 0.90,
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
            [_listing("510710", date(2020, 1, 1)), _listing("510712", date(2020, 1, 1))]
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
    for lane_name in ("daily_freshness", "history_depth_61"):
        lane = report[lane_name]
        assert lane["expected_count"] == 2
        assert set(lane) >= {
            "attempted_count",
            "covered_count",
            "eligible_count",
            "excluded_count",
        }
    assert report["contract_depth"]["expected_count"] == 0
    assert report["contract_depth"]["session_calendar_complete"] is False
    assert report["contract_depth"]["full_universe_count"] == 2
    assert report["daily_freshness"]["covered_codes"] == ["510710", "510712"]


@pytest.mark.asyncio
async def test_research_depth_uses_a_seasoned_authoritative_cohort(app) -> None:
    target = date(2026, 7, 17)
    sessions = tuple(target - timedelta(days=offset) for offset in reversed(range(300)))
    old_code = "510720"
    new_code = "510721"
    unknown_code = "510722"
    async with app.state.db.session() as session:
        session.add_all(
            [
                _etf(old_code, listing_date=sessions[0] - timedelta(days=1)),
                _etf(new_code, listing_date=sessions[0] + timedelta(days=10)),
                _etf(unknown_code, listing_date=None),
            ]
        )
        session.add_all(
            [
                _listing(old_code, sessions[0] - timedelta(days=1)),
                _listing(new_code, sessions[0] + timedelta(days=10)),
            ]
        )
        session.add_all(
            _membership(code, effective_from=sessions[0])
            for code in (old_code, new_code, unknown_code)
        )
        session.add_all(_price(old_code, day, eligible=True) for day in sessions)
        session.add_all(_price(new_code, day, eligible=False) for day in sessions)
        await session.commit()

        report = await read_etf_history_readiness(
            session,
            target_date=target,
        )

    contract = report["contract_depth"]
    assert report["daily_freshness"]["expected_count"] == 3
    assert contract["full_universe_count"] == 3
    assert contract["expected_count"] == 1
    assert contract["covered_codes"] == [old_code]
    assert contract["structurally_unseasoned_count"] == 1
    assert contract["structurally_unseasoned_samples"] == [new_code]
    assert contract["unknown_listing_count"] == 1
    assert contract["unknown_listing_samples"] == [unknown_code]
    assert contract["listing_metadata_coverage_ratio"] == pytest.approx(2 / 3)
    assert contract["completion_gate_passed"] is False
    assert "authoritative_listing_metadata_below_95pct" in contract["completion_blockers"]


@pytest.mark.asyncio
async def test_raw_rows_extend_calendar_but_never_adjusted_coverage(app) -> None:
    target = date(2026, 7, 17)
    sessions = tuple(target - timedelta(days=offset) for offset in reversed(range(200)))
    seasoned_code = "510730"
    calendar_only_code = "510731"
    async with app.state.db.session() as session:
        session.add_all(
            [
                _etf(
                    seasoned_code,
                    listing_date=sessions[0] - timedelta(days=1),
                ),
                _etf(
                    calendar_only_code,
                    listing_date=sessions[0] + timedelta(days=1),
                ),
            ]
        )
        session.add_all(
            [
                _listing(seasoned_code, sessions[0] - timedelta(days=1)),
                _listing(calendar_only_code, sessions[0] + timedelta(days=1)),
            ]
        )
        session.add_all(
            _membership(code, effective_from=sessions[0])
            for code in (seasoned_code, calendar_only_code)
        )
        session.add_all(_price(calendar_only_code, day, eligible=False) for day in sessions)
        session.add_all(_price(seasoned_code, day, eligible=True) for day in sessions[1:])
        await session.commit()

        report = await read_etf_history_readiness(
            session,
            target_date=target,
            horizons=(5,),
        )

    contract = report["contract_depth"]
    assert report["observed_session_calendar"]["session_count"] == 200
    assert report["observed_session_calendar"]["raw_rows_count_as_adjusted_coverage"] is False
    assert contract["session_calendar_complete"] is True
    assert contract["expected_count"] == 1
    assert contract["covered_count"] == 0
    assert contract["pending_codes"] == [seasoned_code]


@pytest.mark.asyncio
async def test_readiness_cutoff_and_central_provider_registry_fail_closed(app) -> None:
    target = date(2026, 7, 17)
    accepted_code = "510740"
    forged_code = "510741"
    async with app.state.db.session() as session:
        session.add_all([_etf(accepted_code), _etf(forged_code)])
        session.add_all(
            [
                _membership(accepted_code, effective_from=date(2020, 1, 1)),
                _membership(forged_code, effective_from=date(2020, 1, 1)),
            ]
        )
        session.add(_listing(accepted_code, date(2020, 1, 1)))
        session.add(
            EtfListingDateObservation(
                etf_code=accepted_code,
                exchange="SH",
                listing_date=date(2026, 7, 18),
                source="sse.etf.fundlist",
                provider_version="COMMON_JJZWZ_JJLB_L_v1",
                observed_at=datetime(2026, 7, 17, 8, 0),
                universe_snapshot_hash="c" * 64,
                raw_payload_hash="d" * 64,
                evidence_hash="e" * 64,
            )
        )
        accepted = _price(accepted_code, target, eligible=True)
        accepted.data_provider = "tencent"
        accepted.provider_version = "tencent.ifzq.fqkline.hfq_turnover_yuan_v2"
        accepted.adjustment_version = "tencent.ifzq.fqkline.hfq_turnover_yuan_v2"
        forged = _price(forged_code, target, eligible=True)
        forged.data_provider = "tencent"
        forged.provider_version = "tencent.forged_v9"
        forged.adjustment_version = "tencent.forged_v9"
        session.add_all([accepted, forged])
        await session.commit()

        report = await read_etf_history_readiness(
            session,
            target_date=target,
            horizons=(5,),
            data_cutoff=datetime(2026, 7, 17, 15, 0),
        )

    assert report["daily_freshness"]["covered_codes"] == [accepted_code]
    assert report["listing_metadata"]["observed_count"] == 1
    assert report["contract_depth"]["unknown_listing_samples"] == [forged_code]
    assert report["data_cutoff_utc"] == "2026-07-17T07:00:00"


@pytest.mark.asyncio
async def test_production_source_count_excludes_snapshots_after_effective_date(app) -> None:
    async with app.state.db.session() as session:
        for suffix, as_of_date in (("current", "2026-07-17"), ("future", "2026-07-18")):
            await session.execute(
                text(
                    """
                    INSERT INTO short_research_signal_runs (
                        status, started_at, as_of_date, as_of_trade_date, scope_kind,
                        universe_snapshot_hash, input_snapshot_hash, score_version,
                        rule_version, ranking_contract_hash, score_field, price_basis,
                        decision_data_coverage_ratio, coverage_ratio, publication_state,
                        idempotency_key, config_json, summary_json
                    ) VALUES (
                        :status, :started_at, :as_of_date, :as_of_date, :scope_kind,
                        :universe_hash, :input_hash, :score_version,
                        :rule_version, :contract_hash, :score_field,
                        :price_basis, 1.0, 1.0, :publication_state, :key, :empty_json, :empty_json
                    )
                    """
                ),
                {
                    "status": "success",
                    "started_at": "2026-07-17 15:00:00",
                    "empty_json": "{}",
                    "as_of_date": as_of_date,
                    "scope_kind": "full",
                    "score_version": "final_score_v3",
                    "rule_version": "final_score_v3_rule_v2",
                    "score_field": "ranking_score",
                    "price_basis": "total_return_adjusted",
                    "publication_state": "published",
                    "universe_hash": suffix + "-universe",
                    "input_hash": suffix + "-input",
                    "contract_hash": suffix + "-contract",
                    "key": suffix + "-published",
                },
            )
        await session.commit()

        count = await _compatible_production_source_date_count(
            session,
            effective_date=date(2026, 7, 17),
        )

    assert count == 1


@pytest.mark.asyncio
async def test_short_etf_data_status_exposes_independent_history_lanes(client) -> None:
    response = await client.get("/api/short-etf/data-status")

    assert response.status_code == 200
    body = response.json()
    readiness = body["history_readiness"]
    assert readiness == body["summary"]["history_readiness"]
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
