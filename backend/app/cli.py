from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from typing import cast

from sqlalchemy import delete

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.models.entities import Index, IndexValuationHistory
from app.services.index_data import fetch_index_valuation
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


def main() -> None:
    parser = argparse.ArgumentParser(description="FundScope CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("backfill-valuation")
    backfill.add_argument("--index", required=True)
    backfill.add_argument("--years", type=int, default=10)

    args = parser.parse_args()
    if args.command == "backfill-valuation":
        asyncio.run(backfill_valuation(args.index, args.years))


if __name__ == "__main__":
    main()
