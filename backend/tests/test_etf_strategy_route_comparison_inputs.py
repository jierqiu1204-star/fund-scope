"""Focused tests for the PIT input and research-evidence boundary."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import market_data
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
from app.services.strategy_lab.etf_strategy_route_comparison import ComparisonDecisionSnapshot
from app.services.strategy_lab.etf_strategy_route_comparison_inputs import (
    COMPARISON_DECISION_CUTOFF_TIME,
    V2DayEvidence,
    _build_comparison_snapshot,
    _v2_marker_payload,
    inspect_research_store,
    load_comparison_valuation_input,
    prepare_v2_day_checks,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SIGNAL_DATE = date(2026, 9, 9)


def _hash(value: object) -> str:
    return stable_contract_hash({"fixture": value})


def _metadata(code: str, underlying: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        asset_code=code,
        tracked_underlying_id=underlying,
        membership_fact_hash=_hash(("membership", code)),
    )


def _series(code: str, turnover: float, count: int = 127) -> SimpleNamespace:
    bars = tuple(
        SimpleNamespace(
            session_date=SIGNAL_DATE - timedelta(days=count - 1 - index),
            adjusted_close=100.0 + index,
            turnover=turnover,
        )
        for index in range(count)
    )
    provenance = SimpleNamespace(
        provider="eastmoney",
        adjustment_version="eastmoney.push2his.kline.hfq_v1",
        price_basis="total_return_adjusted",
    )
    return SimpleNamespace(
        asset_code=code,
        bars=bars,
        provenance=provenance,
        earliest_source_timestamp=datetime(2026, 9, 9, 10, tzinfo=UTC),
        latest_source_timestamp=datetime(2026, 9, 9, 10, tzinfo=UTC),
        synchronized_after_cutoff=False,
        revision_hashes=tuple(_hash((code, index)) for index in range(127)),
        series_hash=_hash(("series", code)),
    )


def test_clone_representative_uses_cutoff_turnover_and_unknown_mapping_is_excluded():
    codes = ["510000", "510001", *(f"5101{index:02d}" for index in range(9)), "519999"]
    metadata = tuple(
        _metadata(code, "index-a" if code in {"510000", "510001"} else None if code == "519999" else code)
        for code in codes
    )
    series = tuple(
        _series(code, 1000.0 if code == "510001" else 10.0)
        for code in codes
    )
    page = SimpleNamespace(
        authoritative_universe=metadata,
        eligible_inputs=series,
        exclusions=(),
        universe_hash=_hash("universe"),
        source_snapshot_hash=_hash("source"),
        input_hash=_hash("input"),
        coverage_manifest_hash=_hash("coverage"),
        page_asset_codes=tuple(codes),
        has_more=False,
        next_code_after=None,
    )
    audit = _build_comparison_snapshot(
        (page,),
        signal_date=SIGNAL_DATE,
        decision_cutoff=datetime(2026, 9, 9, 19, tzinfo=SHANGHAI),
    )

    assert dict(audit.clone_representatives)["underlying:index-a"] == "510001"
    assert "519999" not in audit.nonclone_asset_codes
    assert audit.clone_mapping_missing_asset_codes == ("519999",)
    assert audit.nonclone_denominator == 10
    assert audit.history_numerator == 10


def test_clone_representative_stays_high_liquidity_when_history_is_short():
    codes = ["510000", "510001", *(f"5101{index:02d}" for index in range(9))]
    metadata = tuple(_metadata(code, "index-a" if code in {"510000", "510001"} else code) for code in codes)
    series = tuple(
        _series(
            code,
            1000.0 if code == "510000" else 10.0,
            count=126 if code == "510000" else 127,
        )
        for code in codes
    )
    page = SimpleNamespace(
        authoritative_universe=metadata,
        eligible_inputs=series,
        exclusions=(),
        universe_hash=_hash("universe-short-representative"),
        source_snapshot_hash=_hash("source-short-representative"),
        input_hash=_hash("input-short-representative"),
        coverage_manifest_hash=_hash("coverage-short-representative"),
        page_asset_codes=tuple(codes),
        has_more=False,
        next_code_after=None,
    )

    audit = _build_comparison_snapshot(
        (page,),
        signal_date=SIGNAL_DATE,
        decision_cutoff=datetime(2026, 9, 9, 19, tzinfo=SHANGHAI),
    )

    assert dict(audit.clone_representatives)["underlying:index-a"] == "510000"
    assert audit.nonclone_denominator == 10
    assert audit.history_numerator == 9
    assert "510000" in audit.history_missing_asset_codes


def test_research_store_hash_failure_is_not_ready(tmp_path):
    path = tmp_path / "research.sqlite3"
    store = ReplayArtifactStore(path)
    payload = {"schema_version": "v2-day-check"}
    store.write_research_artifacts(
        run_id="test-run",
        phase="test-phase",
        artifacts=[("one", payload)],
        max_seconds=5,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE research_artifacts SET artifact_hash=?",
            ("0" * 64,),
        )
        connection.commit()

    readiness = inspect_research_store(path, run_id="test-run", phase="test-phase")
    assert readiness.invalid_artifact_count == 1
    assert readiness.ready is False


def test_rebuild_marker_round_trip_keeps_fixed_cutoff_and_source_identity(tmp_path):
    source_hash = _hash("snapshot")
    snapshot = ComparisonDecisionSnapshot(
        signal_date=SIGNAL_DATE,
        decision_cutoff=datetime.combine(SIGNAL_DATE, COMPARISON_DECISION_CUTOFF_TIME, SHANGHAI),
        source_hash=source_hash,
        eligible_asset_codes=("510000",),
        nonclone_asset_codes=("510000",),
        adjusted_closes=(),
    )
    evidence = V2DayEvidence(
        signal_date=SIGNAL_DATE,
        decision_cutoff=snapshot.decision_cutoff,
        available=True,
        reason="pit_rebuilt_screen_and_lifecycle_checked",
        manifest_hash=_hash("manifest"),
        manifest_input_hash=_hash("input"),
        required_observation_keys=("510000:2026-09-09:leader_breakout_proxy_v2",),
        observed_observation_keys=("510000:2026-09-09:leader_breakout_proxy_v2",),
        observation_count=1,
        screen_observation_count=1,
        lifecycle_checked_observation_count=1,
        observation_digest=_hash("observation"),
        transition_digest=_hash("transition"),
        checked_through_date=SIGNAL_DATE,
        checked_through_cutoff=snapshot.decision_cutoff,
        lifecycle_checked=True,
        checked_observation_keys=("510000:2026-09-09:leader_breakout_proxy_v2",),
        checked_asset_codes=("510000",),
        materialization_mode="pit_rebuild_research_artifact_v1",
        source_fact_hash=_hash("facts"),
        computed_at=datetime.now(UTC),
    )
    payload = _v2_marker_payload(snapshot=snapshot, evidence=evidence)
    assert payload["materialization_mode"] == "pit_rebuild_research_artifact_v1"
    assert payload["decision_cutoff"].endswith("19:00:00+08:00")
    assert payload["source_fact_hash"] == evidence.source_fact_hash


@pytest.mark.asyncio
async def test_valuation_loader_uses_the_bounded_pit_page_default(monkeypatch):
    calls: list[int] = []

    async def facts(_session, *, etf_codes, **_kwargs):
        calls.append(len(etf_codes))
        return (
            SimpleNamespace(
                etf_code=etf_codes[0],
                trade_date=SIGNAL_DATE,
                adjusted_close=101.0,
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                revision_hash=_hash("revision"),
                source_timestamp=datetime(2026, 9, 9, 10, tzinfo=UTC),
                decision_eligible=True,
            ),
        )

    monkeypatch.setattr(
        market_data,
        "etf_decision_adjusted_provider_versions",
        lambda: (("eastmoney", "eastmoney.push2his.kline.hfq_v1"),),
    )
    monkeypatch.setattr(market_data, "etf_adjusted_daily_facts_on_or_before", facts)
    result = await load_comparison_valuation_input(
        object(),
        trading_sessions=(SIGNAL_DATE,),
        asset_codes=("510000",),
        decision_cutoff=datetime.combine(SIGNAL_DATE, COMPARISON_DECISION_CUTOFF_TIME, SHANGHAI),
    )

    assert calls == [1]
    assert result.calendar_sessions == (SIGNAL_DATE,)
    assert result.adjusted_closes[0].asset_code == "510000"


@pytest.mark.asyncio
async def test_prepare_initializes_only_an_existing_store_parent(monkeypatch, tmp_path):
    async def lease(*_args, **_kwargs):
        return datetime.now(UTC)

    async def release(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.strategy_lab.etf_strategy_route_comparison_inputs.acquire_v2_global_run_lease",
        lease,
    )
    monkeypatch.setattr(
        "app.services.strategy_lab.etf_strategy_route_comparison_inputs.release_v2_global_run_lease",
        release,
    )
    path = tmp_path / "research.sqlite3"
    result = await prepare_v2_day_checks(
        object(),
        snapshots=(),
        research_store=path,
        run_id="initialization-test",
        lease_owner="test-worker",
    )

    assert result.status == "complete"
    assert path.is_file()
    assert inspect_research_store(path, run_id="initialization-test").schema_compatible


@pytest.mark.asyncio
async def test_prepare_rejects_a_corrupt_store_before_lease(monkeypatch, tmp_path):
    called = False

    async def lease(*_args, **_kwargs):
        nonlocal called
        called = True
        return datetime.now(UTC)

    monkeypatch.setattr(
        "app.services.strategy_lab.etf_strategy_route_comparison_inputs.acquire_v2_global_run_lease",
        lease,
    )
    path = tmp_path / "research.sqlite3"
    path.write_bytes(b"not sqlite")
    result = await prepare_v2_day_checks(
        object(),
        snapshots=(),
        research_store=path,
        run_id="corrupt-test",
        lease_owner="test-worker",
    )

    assert result.status == "blocked"
    assert called is False
