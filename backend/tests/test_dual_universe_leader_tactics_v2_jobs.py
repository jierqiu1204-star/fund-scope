from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.strategy_lab.etf_point_in_time_decision_data import EtfDecisionDataSnapshot
from app.services.workflows import dual_universe_leader_tactics_v2_jobs as jobs
from app.services.workflows.dual_universe_leader_tactics_v2 import AshareReadinessReport


def _settings(*, enabled: bool = True) -> Settings:
    settings = Settings(_env_file=None)
    settings.etf_leader_tactics_v2_materialize_enabled = enabled
    return settings


def _readiness(*, count: int = 100, eligible: int = 95) -> AshareReadinessReport:
    return AshareReadinessReport(
        as_of=datetime(2026, 8, 5, 1, 10),
        universe_count=count,
        adjusted_daily_count=eligible,
        pit_theme_count=eligible,
        history_counts=((61, eligible), (120, eligible), (180, eligible), (300, 10)),
        provider_health=(("eastmoney", eligible * 180),),
        raw_decision_violations=0,
        non_finite_violations=0,
        exclusions=(("none", count),),
    )


def _etf_decision_snapshot() -> EtfDecisionDataSnapshot:
    return EtfDecisionDataSnapshot(
        snapshot_id=7,
        trade_date=date(2026, 8, 4),
        decision_cutoff=datetime.fromisoformat("2026-08-04T23:00:00+08:00"),
        daily_coverage_ratio=0.95,
        warmup_coverage_ratio=0.90,
        readiness_policy_version="etf_readiness_policy_v3",
        source_snapshot_hash="s" * 64,
        universe_manifest_hash="u" * 64,
        input_snapshot_hash="i" * 64,
        provider_health_hash="p" * 64,
        provider_health=(("eastmoney", "healthy"),),
    )


def test_capture_signal_date_continues_last_closed_session_without_backdating() -> None:
    assert jobs._capture_signal_date(datetime(2026, 8, 7, 14, 59)) is None
    assert jobs._capture_signal_date(datetime(2026, 8, 7, 15, 0)) == date(2026, 8, 7)
    assert jobs._capture_signal_date(datetime(2026, 8, 8, 0, 1)) == date(2026, 8, 7)

@pytest.mark.asyncio
async def test_materialization_job_is_default_off_without_database_work(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "_latest_visible_ashare_signal_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("database read")),
    )

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(enabled=False),
    )

    assert result["status"] == "skipped"
    assert result["research_only"] is True


@pytest.mark.asyncio
async def test_materialization_gate_stops_before_full_cross_section(monkeypatch) -> None:
    async def signal_date(*_args, **_kwargs):
        return date(2026, 8, 4)

    async def readiness(*_args, **_kwargs):
        return _readiness(eligible=89)

    async def unexpected_assets(*_args, **_kwargs):
        raise AssertionError("full cross-section must not load below readiness")

    monkeypatch.setattr(jobs, "_latest_visible_ashare_signal_date", signal_date)
    monkeypatch.setattr(jobs, "read_ashare_readiness", readiness)
    monkeypatch.setattr(jobs, "read_ashare_authoritative_assets", unexpected_assets)

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["status"] == "waiting"
    assert result["unavailable_reason"] == "insufficient_adjusted_daily_coverage"


@pytest.mark.asyncio
async def test_materialization_memory_gate_stops_before_full_cross_section(monkeypatch) -> None:
    async def signal_date(*_args, **_kwargs):
        return date(2026, 8, 4)

    async def readiness(*_args, **_kwargs):
        return _readiness()

    async def unexpected_assets(*_args, **_kwargs):
        raise AssertionError("full cross-section must not load without headroom")

    monkeypatch.setattr(jobs, "_latest_visible_ashare_signal_date", signal_date)
    monkeypatch.setattr(jobs, "read_ashare_readiness", readiness)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 100)
    monkeypatch.setattr(jobs, "read_ashare_authoritative_assets", unexpected_assets)

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["unavailable_reason"] == "insufficient_materialization_memory_headroom"


@pytest.mark.asyncio
async def test_ready_materialization_reads_persisted_facts_and_writes_one_manifest(
    monkeypatch,
) -> None:
    assets = tuple((f"{index:06d}", f"asset-{index}") for index in range(100))
    inputs = tuple(object() for _ in assets)
    observations = (SimpleNamespace(asset_code="000001"),)
    screen_result = SimpleNamespace(
        observations=observations,
        qualifying=observations,
    )
    calls: list[str] = []

    async def signal_date(*_args, **_kwargs):
        return date(2026, 8, 4)

    async def readiness(*_args, **_kwargs):
        return _readiness()

    async def read_assets(*_args, **_kwargs):
        calls.append("assets")
        return assets

    async def read_inputs(*_args, **_kwargs):
        calls.append("inputs")
        return inputs

    def screen(*_args, **_kwargs):
        calls.append("screen")
        return screen_result

    async def persist(*_args, **_kwargs):
        calls.append("persist")
        return "m" * 64

    monkeypatch.setattr(jobs, "_latest_visible_ashare_signal_date", signal_date)
    monkeypatch.setattr(jobs, "read_ashare_readiness", readiness)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_ashare_authoritative_assets", read_assets)
    monkeypatch.setattr(jobs, "read_ashare_asset_inputs", read_inputs)
    monkeypatch.setattr(jobs, "screen_dual_universe", screen)
    monkeypatch.setattr(jobs, "materialize_v2_result", persist)

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert calls == ["assets", "inputs", "screen", "persist"]
    assert result["status"] == "materialized"
    assert result["candidate_codes"] == ["000001"]
    assert result["notification_provenance"] == "none"
    assert result["execution_provenance"] == "none"


@pytest.mark.asyncio
async def test_etf_materialization_is_default_off_without_database_work(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "latest_ready_etf_decision_data_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("database read")),
    )

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(enabled=False),
    )

    assert result["status"] == "skipped"
    assert result["research_only"] is True


@pytest.mark.asyncio
async def test_etf_materialization_reads_persisted_pit_inputs_and_writes_manifest(
    monkeypatch,
) -> None:
    snapshot = _etf_decision_snapshot()
    inputs = tuple(object() for _ in range(10))
    bundle = SimpleNamespace(
        inputs=inputs,
        universe_count=10,
        adjusted_120_count=9,
        adjusted_180_count=8,
        pit_group_count=9,
        provider_health=(("eastmoney", "healthy"),),
        raw_decision_violations=0,
        non_finite_violations=0,
        readiness_dict=lambda *, threshold: {"threshold": threshold},
    )
    observations = (SimpleNamespace(asset_code="510001"),)
    screen_result = SimpleNamespace(
        observations=observations,
        qualifying=observations,
    )
    calls: list[str] = []

    async def latest(*_args, **_kwargs):
        calls.append("source")
        return snapshot

    async def existing(*_args, **_kwargs):
        calls.append("existing")
        return None

    async def read_inputs(*_args, **_kwargs):
        calls.append("inputs")
        return bundle

    def screen(*_args, **_kwargs):
        calls.append("screen")
        return screen_result

    async def persist(*_args, **_kwargs):
        calls.append("persist")
        return "e" * 64

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", existing)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", read_inputs)
    monkeypatch.setattr(jobs, "screen_dual_universe", screen)
    monkeypatch.setattr(jobs, "materialize_v2_result", persist)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert calls == ["source", "existing", "inputs", "screen", "persist"]
    assert result["status"] == "materialized"
    assert result["candidate_codes"] == ["510001"]
    assert result["readiness"]["provider_health"] == {"eastmoney": "healthy"}
    assert result["decision_data_snapshot_id"] == 7
    assert result["readiness"]["decision_data_snapshot"]["provenance_kind"] == "persisted_etf_pit_decision_data"
    assert "source_signal_run_id" not in result["readiness"]["decision_data_snapshot"]
    assert result["notification_provenance"] == "none"
    assert result["execution_provenance"] == "none"


@pytest.mark.asyncio
async def test_etf_materialization_history_gate_stops_before_screen(monkeypatch) -> None:
    snapshot = _etf_decision_snapshot()
    bundle = SimpleNamespace(
        inputs=tuple(object() for _ in range(10)),
        universe_count=10,
        adjusted_120_count=8,
        adjusted_180_count=8,
        pit_group_count=9,
        provider_health=(("eastmoney", "healthy"),),
        raw_decision_violations=0,
        non_finite_violations=0,
        readiness_dict=lambda *, threshold: {"threshold": threshold},
    )

    async def latest(*_args, **_kwargs):
        return snapshot

    async def existing(*_args, **_kwargs):
        return None

    async def read_inputs(*_args, **_kwargs):
        return bundle

    def unexpected_screen(*_args, **_kwargs):
        raise AssertionError("screen must not run below the frozen history gate")

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", existing)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", read_inputs)
    monkeypatch.setattr(jobs, "screen_dual_universe", unexpected_screen)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["status"] == "waiting"
    assert result["unavailable_reason"] == "insufficient_etf_history_120_coverage"
