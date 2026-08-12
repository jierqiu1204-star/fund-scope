from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import scheduler as scheduler_module


async def _fake_capture(_session: AsyncSession, _settings: Settings) -> dict[str, Any]:
    return {"status": "capture_stub"}


async def _fake_materialize(_session: AsyncSession, _settings: Settings) -> dict[str, Any]:
    return {"status": "materialize_stub"}


async def _fake_etf_materialize(
    _session: AsyncSession,
    _settings: Settings,
) -> dict[str, Any]:
    return {"status": "etf_materialize_stub"}


def _trigger_field(job: object, name: str) -> str:
    return str(next(field for field in job.trigger.fields if field.name == name))


def test_v2_scheduler_is_default_off_and_does_not_import_workflow_jobs(app, monkeypatch) -> None:
    app.state.settings.etf_leader_tactics_v2_capture_enabled = False
    app.state.settings.etf_leader_tactics_v2_materialize_enabled = False
    app.state.settings.etf_leader_tactics_v2_etf_materialize_enabled = False
    app.state.settings.etf_leader_tactics_v2_api_enabled = True

    def unexpected_loader() -> object:
        raise AssertionError("default-off V2 scheduler must not load workflow jobs")

    monkeypatch.setattr(scheduler_module, "_load_v2_scheduler_job_contract", unexpected_loader)
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")

    scheduler_module.register_default_jobs(scheduler, app.state.db, app.state.settings)

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert scheduler_module.V2_CAPTURE_JOB_NAME not in job_ids
    assert scheduler_module.V2_MATERIALIZE_JOB_NAME not in job_ids
    assert scheduler_module.V2_ETF_MATERIALIZE_JOB_NAME not in job_ids


@pytest.mark.parametrize(
    ("capture_enabled", "materialize_enabled", "etf_materialize_enabled", "expected"),
    [
        (True, False, False, {scheduler_module.V2_CAPTURE_JOB_NAME}),
        (
            False,
            True,
            False,
            {scheduler_module.V2_MATERIALIZE_JOB_NAME},
        ),
        (
            True,
            True,
            True,
            {
                scheduler_module.V2_CAPTURE_JOB_NAME,
                scheduler_module.V2_MATERIALIZE_JOB_NAME,
                scheduler_module.V2_ETF_MATERIALIZE_JOB_NAME,
            },
        ),
        (
            False,
            False,
            True,
            {scheduler_module.V2_ETF_MATERIALIZE_JOB_NAME},
        ),
    ],
)
def test_v2_scheduler_registers_only_enabled_stages(
    app,
    monkeypatch: pytest.MonkeyPatch,
    capture_enabled: bool,
    materialize_enabled: bool,
    etf_materialize_enabled: bool,
    expected: set[str],
) -> None:
    app.state.settings.etf_leader_tactics_v2_capture_enabled = capture_enabled
    app.state.settings.etf_leader_tactics_v2_materialize_enabled = materialize_enabled
    app.state.settings.etf_leader_tactics_v2_etf_materialize_enabled = etf_materialize_enabled
    contract = scheduler_module._V2SchedulerJobContract(
        capture_name=scheduler_module.V2_CAPTURE_JOB_NAME,
        materialize_name=scheduler_module.V2_MATERIALIZE_JOB_NAME,
        etf_materialize_name=scheduler_module.V2_ETF_MATERIALIZE_JOB_NAME,
        capture_job=_fake_capture,
        materialize_job=_fake_materialize,
        etf_materialize_job=_fake_etf_materialize,
    )
    monkeypatch.setattr(scheduler_module, "_load_v2_scheduler_job_contract", lambda: contract)
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")

    scheduler_module.register_default_jobs(scheduler, app.state.db, app.state.settings)

    v2_jobs = {
        job.id: job
        for job in scheduler.get_jobs()
        if job.id
        in {
            scheduler_module.V2_CAPTURE_JOB_NAME,
            scheduler_module.V2_MATERIALIZE_JOB_NAME,
            scheduler_module.V2_ETF_MATERIALIZE_JOB_NAME,
        }
    }
    assert set(v2_jobs) == expected
    for job in v2_jobs.values():
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.args[0] is app.state.db

    capture = v2_jobs.get(scheduler_module.V2_CAPTURE_JOB_NAME)
    if capture is not None:
        assert _trigger_field(capture, "day_of_week") == "mon-fri"
        assert _trigger_field(capture, "hour") == "21-23"
        assert _trigger_field(capture, "minute") == "*/2"
        assert _trigger_field(capture, "second") == "0"

    etf_materialize = v2_jobs.get(scheduler_module.V2_ETF_MATERIALIZE_JOB_NAME)
    if etf_materialize is not None:
        assert _trigger_field(etf_materialize, "day_of_week") == "tue-sat"
        assert _trigger_field(etf_materialize, "hour") == "9"
        assert _trigger_field(etf_materialize, "minute") == "10"
        assert _trigger_field(etf_materialize, "second") == "0"

    materialize = v2_jobs.get(scheduler_module.V2_MATERIALIZE_JOB_NAME)
    if materialize is not None:
        assert _trigger_field(materialize, "day_of_week") == "tue-sat"
        assert _trigger_field(materialize, "hour") == "9-10"
        assert _trigger_field(materialize, "minute") == "12-58/2"
        assert _trigger_field(materialize, "second") == "0"


def test_v2_scheduler_fails_closed_if_enabled_jobs_module_is_missing(app, monkeypatch) -> None:
    app.state.settings.etf_leader_tactics_v2_capture_enabled = True
    app.state.settings.etf_leader_tactics_v2_materialize_enabled = False
    app.state.settings.etf_leader_tactics_v2_etf_materialize_enabled = False

    def missing_module(_module_name: str) -> object:
        raise ModuleNotFoundError(
            "missing V2 workflow jobs module",
            name=scheduler_module.V2_JOB_MODULE,
        )

    monkeypatch.setattr(scheduler_module, "import_module", missing_module)
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")

    with pytest.raises(RuntimeError, match="workflow jobs module is unavailable"):
        scheduler_module.register_default_jobs(scheduler, app.state.db, app.state.settings)


def test_v2_staged_ashare_rollout_is_wired_without_etf_materialization() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "frontend/Dockerfile").read_text(encoding="utf-8")
    standard_compose = (root / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    ip_compose = (root / "deploy/docker-compose.ip.yml").read_text(encoding="utf-8")
    tracked_env = (root / "deploy/etf-research.env").read_text(encoding="utf-8")

    assert "ARG NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED=false" in dockerfile
    assert (
        "ENV NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED=$NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED"
        in dockerfile
    )
    expected_arg = "NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED: ${NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED:-false}"
    assert expected_arg in standard_compose
    assert expected_arg in ip_compose
    assert "ETF_LEADER_TACTICS_V2_API_ENABLED=true" in tracked_env
    assert "ETF_LEADER_TACTICS_V2_CAPTURE_ENABLED=true" in tracked_env
    assert "ETF_LEADER_TACTICS_V2_MATERIALIZE_ENABLED=true" in tracked_env
    assert "ETF_LEADER_TACTICS_V2_ETF_MATERIALIZE_ENABLED=true" in tracked_env
