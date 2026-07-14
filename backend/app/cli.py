from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import date, timedelta
from typing import cast

from sqlalchemy import delete

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.models.entities import Index, IndexValuationHistory
from app.services.index_data import fetch_index_valuation
from app.services.tracked_positions.lifecycle_backfill import backfill_position_lifecycle_batch
from app.services.valuation import compute_percentile


async def backfill_valuation(index_code: str, years: int) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    async with db.session() as session:
        if await session.get(Index, index_code) is None:
            session.add(Index(code=index_code, name=index_code, region="CN", is_watchlist=True))
            await session.commit()

        await session.execute(delete(IndexValuationHistory).where(IndexValuationHistory.index_code == index_code))
        history_values: list[float] = []
        start = date.today() - timedelta(days=years * 365)
        current = start

        while current <= date.today():
            if current.weekday() < 5:
                payload = await fetch_index_valuation(index_code, current)
                pe = float(cast(float, payload["pe"]))
                pb = float(cast(float, payload["pb"]))
                history_values.append(pe)
                pe_stats = compute_percentile(history_values, pe)
                pb_stats = compute_percentile(history_values, pb)
                session.add(
                    IndexValuationHistory(
                        index_code=index_code,
                        valuation_date=current,
                        pe=pe,
                        pb=pb,
                        dividend_yield=float(cast(float, payload["dividend_yield"])),
                        pe_percentile=pe_stats.percentile,
                        pb_percentile=pb_stats.percentile,
                        effective_window=pe_stats.effective_window,
                    )
                )
            current += timedelta(days=1)
        await session.commit()
    await db.engine.dispose()


async def backfill_etf_alert_lifecycle(
    position_ids: list[int],
    cutoff: date,
    max_items: int,
) -> None:
    settings = get_settings()
    db = DatabaseManager(settings.database_url)
    async with db.session() as session:
        result = await backfill_position_lifecycle_batch(
            session,
            position_ids=position_ids,
            cutoff=cutoff,
            max_items=max_items,
        )
        await session.commit()
    await db.engine.dispose()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FundScope CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("backfill-valuation")
    backfill.add_argument("--index", required=True)
    backfill.add_argument("--years", type=int, default=10)

    lifecycle = subparsers.add_parser("backfill-etf-alert-lifecycle")
    lifecycle.add_argument("--position-id", dest="position_ids", action="append", type=int, required=True)
    lifecycle.add_argument("--cutoff", type=date.fromisoformat, required=True)
    lifecycle.add_argument("--max-items", type=int, default=100)
    return parser


def main() -> None:
    parser = build_parser()

    args = parser.parse_args()
    if args.command == "backfill-valuation":
        asyncio.run(backfill_valuation(args.index, args.years))
    elif args.command == "backfill-etf-alert-lifecycle":
        asyncio.run(backfill_etf_alert_lifecycle(args.position_ids, args.cutoff, args.max_items))


if __name__ == "__main__":
    main()
