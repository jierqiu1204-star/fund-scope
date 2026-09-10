from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import run_etf_research_input_history_sync as cli


def test_input_repair_cli_requires_at_most_five_codes() -> None:
    arguments = cli._arguments(
        [
            "--codes",
            "510001",
            "510002",
            "510003",
            "510004",
            "510005",
            "--target-date",
            "2026-07-24",
        ]
    )

    assert arguments.codes == [
        "510001",
        "510002",
        "510003",
        "510004",
        "510005",
    ]
    assert arguments.target_date.isoformat() == "2026-07-24"

    with pytest.raises(ValueError, match="at most 5"):
        cli._normalise_codes([f"51000{index}" for index in range(6)])


@pytest.mark.asyncio
async def test_input_repair_cli_calls_workflow_with_explicit_scope(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _SessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Engine:
        async def dispose(self) -> None:
            captured["disposed"] = True

    class _Database:
        def __init__(self, _url: str) -> None:
            self.engine = _Engine()

        def session(self) -> _SessionContext:
            return _SessionContext()

    async def fake_workflow(session: object, **kwargs: object) -> dict[str, object]:
        captured["session"] = session
        captured.update(kwargs)
        return {"status": "partial", "job_status": "partial"}

    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql+asyncpg://fundscope:secret@postgres:5432/fundscope"
        ),
    )
    monkeypatch.setattr(cli, "_require_server_database", lambda _url: None)
    monkeypatch.setattr(cli, "DatabaseManager", _Database)
    monkeypatch.setattr(cli, "run_post_publication_etf_research_history_slice", fake_workflow)

    result = await cli._run(
        cli._arguments(
            ["--codes", "510002", "510001", "510002", "--max-seconds", "12"]
        )
    )

    assert result["status"] == "partial"
    assert captured["input_repair"] is True
    assert captured["input_repair_codes"] == ("510002", "510001")
    assert captured["target_date"] is None
    assert captured["disposed"] is True
