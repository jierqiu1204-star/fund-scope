from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.services.etf_catalyst_shadow.official_fetcher import OfficialCatalystFetcher
from app.services.etf_catalyst_shadow.runner import run_source_fetch_batch


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one bounded real official-source catalyst shadow batch."
    )
    parser.add_argument("--source-id", default="ndrc_policy")
    parser.add_argument("--session-key", required=True)
    parser.add_argument("--maximum-items", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser.parse_args()


def _require_server_database(database_url: str) -> None:
    url = make_url(database_url)
    host = (url.host or "").lower()
    if url.get_backend_name() == "sqlite" or host in {
        "",
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise RuntimeError(
            "real catalyst shadow sessions require the server database; "
            "local database targets are refused"
        )


async def _run(arguments: argparse.Namespace) -> None:
    database_url = get_settings().database_url
    _require_server_database(database_url)
    database = DatabaseManager(database_url)
    try:
        async with database.session() as session:
            result = await run_source_fetch_batch(
                session,
                source_id=arguments.source_id,
                session_key=arguments.session_key,
                fetcher=OfficialCatalystFetcher(),
                maximum_items=arguments.maximum_items,
                timeout_seconds=arguments.timeout_seconds,
            )
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    finally:
        await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
