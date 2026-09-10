from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.models.entities import utcnow
from app.services.short_research.etf_identity_facts import (
    IdentityFactProviderPage,
    identity_fact_coverage_at_cutoff,
    persist_etf_identity_facts,
    try_acquire_identity_fact_worker_lock,
)
from app.services.short_research.etf_tracked_underlying import (
    MAX_TRACKED_UNDERLYING_PAGE_CODES,
    EtfTrackedUnderlyingProviderError,
    fetch_eastmoney_tracked_underlying_page,
)
from app.services.short_research.jobs import etf_tracked_underlying_ingestion_job

MAX_SECONDS = 55.0


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one bounded ETF tracked-underlying ingestion slice."
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        help=(
            "optional explicit ETF codes for one bounded acceptance batch; omit to resume "
            "the scheduler cursor"
        ),
    )
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    return parser.parse_args(argv)


def _require_server_database(database_url: str) -> None:
    url = make_url(database_url)
    host = (url.host or "").lower()
    if url.get_backend_name() == "sqlite" or host in {"", "localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("ETF identity ingestion requires the server database")


async def _ingest_explicit_codes(
    database: DatabaseManager,
    codes: tuple[str, ...],
    *,
    max_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    async with database.session() as session:
        if not await try_acquire_identity_fact_worker_lock(session):
            await session.rollback()
            return {
                "mode": "explicit_codes",
                "status": "skipped",
                "reason": "identity_fact_worker_lock_busy",
                "selected_count": 0,
            }
        try:
            page: IdentityFactProviderPage = await asyncio.wait_for(
                fetch_eastmoney_tracked_underlying_page(codes),
                timeout=max_seconds,
            )
        except EtfTrackedUnderlyingProviderError as exc:
            await session.rollback()
            return {
                "mode": "explicit_codes",
                "status": "failed",
                "job_status": "failed",
                "selected_count": len(codes),
                "provider_failure_count": len(exc.failures),
                "provider_failures": [
                    {"etf_code": code, "reason": reason}
                    for code, reason in exc.failures
                ],
                "coverage": None,
            }
        persisted = await persist_etf_identity_facts(
            session,
            underlying_records=page.underlying_records,
        )
        coverage = await identity_fact_coverage_at_cutoff(session, cutoff=utcnow())
        await session.commit()
    coverage_payload = coverage.as_dict()
    coverage_payload["cutoff"] = coverage.cutoff.isoformat()
    status = "partial" if page.provider_errors else "complete"
    return {
        "mode": "explicit_codes",
        "status": status,
        "job_status": status if status == "partial" else None,
        "selected_count": len(codes),
        "underlying_facts_inserted": persisted.underlying_facts_inserted,
        "underlying_facts_existing": persisted.underlying_facts_existing,
        "unresolved_count": sum(
            record.identity_state == "unresolved" for record in page.underlying_records
        ),
        "provider_failure_count": len(page.provider_errors),
        "provider_failures": [
            {"etf_code": code, "reason": reason}
            for code, reason in page.provider_errors
        ],
        "coverage": coverage_payload,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }


async def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    if not 0 < arguments.max_seconds <= MAX_SECONDS:
        raise ValueError("max-seconds must be within (0, 55]")
    settings = get_settings()
    _require_server_database(settings.database_url)
    codes = tuple(dict.fromkeys(str(code).strip() for code in (arguments.codes or ()) if str(code).strip()))
    if len(codes) > MAX_TRACKED_UNDERLYING_PAGE_CODES:
        raise ValueError(f"at most {MAX_TRACKED_UNDERLYING_PAGE_CODES} codes may be supplied")
    database = DatabaseManager(settings.database_url)
    try:
        if codes:
            return await _ingest_explicit_codes(
                database,
                codes,
                max_seconds=arguments.max_seconds,
            )
        async with database.session() as session:
            return await etf_tracked_underlying_ingestion_job(session)
    finally:
        await database.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        result = asyncio.run(
            asyncio.wait_for(_run(arguments), timeout=arguments.max_seconds)
        )
    except Exception as exc:  # noqa: BLE001 - CLI emits a bounded redacted result
        result = {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
