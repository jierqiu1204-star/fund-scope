from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun


async def select_canonical_etf_snapshot(
    session: AsyncSession,
    *,
    score_version: str,
    ranking_contract_hash: str,
    price_basis: str,
    required_trade_date: date,
) -> ShortResearchSignalRun | None:
    has_etf_item = (
        select(ShortResearchSignalItem.id)
        .where(
            ShortResearchSignalItem.run_id == ShortResearchSignalRun.id,
            ShortResearchSignalItem.asset_type == "etf",
        )
        .exists()
    )
    has_non_etf_item = (
        select(ShortResearchSignalItem.id)
        .where(
            ShortResearchSignalItem.run_id == ShortResearchSignalRun.id,
            ShortResearchSignalItem.asset_type != "etf",
        )
        .exists()
    )
    return await session.scalar(
        select(ShortResearchSignalRun)
        .where(
            ShortResearchSignalRun.status == "success",
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.scope_kind == "full",
            ShortResearchSignalRun.score_version == score_version,
            ShortResearchSignalRun.ranking_contract_hash == ranking_contract_hash,
            ShortResearchSignalRun.price_basis == price_basis,
            ShortResearchSignalRun.as_of_trade_date == required_trade_date,
            has_etf_item,
            ~has_non_etf_item,
        )
        .order_by(ShortResearchSignalRun.published_at.desc(), ShortResearchSignalRun.id.desc())
    )
