from __future__ import annotations

import pytest

from scripts.run_etf_catalyst_shadow_source import _require_server_database


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///./fundscope.db",
        "postgresql+asyncpg://fundscope:secret@localhost:5432/fundscope",
        "postgresql+asyncpg://fundscope:secret@127.0.0.1:5432/fundscope",
    ],
)
def test_real_shadow_script_refuses_local_database(database_url: str) -> None:
    with pytest.raises(RuntimeError, match="server database"):
        _require_server_database(database_url)


def test_real_shadow_script_accepts_compose_server_database() -> None:
    _require_server_database(
        "postgresql+asyncpg://fundscope:secret@postgres:5432/fundscope"
    )
