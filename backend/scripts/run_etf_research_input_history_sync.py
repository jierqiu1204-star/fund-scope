from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from typing import Any

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.services.workflows.etf_research_history_sync import (
    RESEARCH_WORKFLOW_TIMEOUT_SECONDS,
    run_post_publication_etf_research_history_slice,
)

MAX_CODES = 5
MAX_SECONDS = RESEARCH_WORKFLOW_TIMEOUT_SECONDS


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "target-date must use YYYY-MM-DD"
        ) from exc


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one bounded ETF research-history input-repair slice. Omit --codes "
            "for the full point-in-time universe cursor, or pass up to five codes "
            "for a sample batch."
        )
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        help=(
            "optional one to five ETF codes from the point-in-time universe; "
            "omit to continue the full-universe cursor"
        ),
    )
    parser.add_argument(
        "--target-date",
        type=_parse_date,
        help=(
            "completed ETF session date, for example the latest closed session "
            "2026-09-10; the CLI does not infer or guess this date"
        ),
    )
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    return parser.parse_args(argv)


def _require_server_database(database_url: str) -> None:
    url = make_url(database_url)
    host = (url.host or "").lower()
    if url.get_backend_name() == "sqlite" or host in {
        "",
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise RuntimeError("ETF research input repair requires the server database")


def _normalise_codes(values: list[str]) -> tuple[str, ...]:
    codes = tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )
    if not codes:
        raise ValueError("at least one ETF code is required")
    if len(codes) > MAX_CODES:
        raise ValueError(f"at most {MAX_CODES} codes may be supplied")
    return codes


async def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    if not 0 < arguments.max_seconds <= MAX_SECONDS:
        raise ValueError("max-seconds must be within (0, 55]")
    codes = _normalise_codes(arguments.codes) if arguments.codes else None
    settings = get_settings()
    _require_server_database(settings.database_url)
    database = DatabaseManager(settings.database_url)
    try:
        async with database.session() as session:
            return await run_post_publication_etf_research_history_slice(
                session,
                target_date=arguments.target_date,
                input_repair=True,
                input_repair_codes=codes,
                max_seconds=arguments.max_seconds,
            )
    finally:
        await database.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        result = asyncio.run(_run(arguments))
    except Exception as exc:  # noqa: BLE001 - CLI emits a bounded redacted result
        result = {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
