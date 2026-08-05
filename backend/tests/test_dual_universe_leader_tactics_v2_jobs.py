from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.core.config import Settings
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
        return _readiness(eligible=94)

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
