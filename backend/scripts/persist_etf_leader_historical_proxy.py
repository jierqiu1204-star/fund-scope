from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.services.strategy_lab.etf_factor_evidence import persist_factor_evidence
from app.services.strategy_lab.etf_leader_tactics_historical_proxy import (
    build_leader_historical_proxy_evidence,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Persist one validated, research-only ETF leader historical proxy "
            "artifact."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--code-version", required=True)
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
            "historical proxy persistence requires the server database; "
            "local database targets are refused"
        )


async def _run(arguments: argparse.Namespace) -> None:
    source = json.loads(arguments.input.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise RuntimeError("historical proxy artifact must be a JSON object")
    evidence = build_leader_historical_proxy_evidence(
        source,
        code_version=arguments.code_version,
    )
    database_url = get_settings().database_url
    _require_server_database(database_url)
    database = DatabaseManager(database_url)
    try:
        async with database.session() as session:
            row = await persist_factor_evidence(session, evidence)
        print(
            json.dumps(
                {
                    "evidence_id": row.id,
                    "experiment_family": row.experiment_family,
                    "manifest_hash": row.manifest_hash,
                    "evidence_hash": row.evidence_hash,
                    "promotion_state": row.promotion_state,
                    "production_mutation_allowed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
