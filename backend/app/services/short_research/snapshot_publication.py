from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun, utcnow


class SnapshotPublicationError(ValueError):
    pass


_REQUIRED_IDENTITY_FIELDS = (
    "scope_kind",
    "scope_hash",
    "universe_snapshot_hash",
    "input_snapshot_hash",
    "score_version",
    "rule_version",
    "ranking_contract_hash",
    "score_field",
    "data_cutoff",
    "as_of_trade_date",
    "price_basis",
    "expected_item_count",
    "eligible_item_count",
    "coverage_ratio",
    "idempotency_key",
)


def _validate_publishable(run: ShortResearchSignalRun, items: list[ShortResearchSignalItem]) -> None:
    if run.status != "success":
        raise SnapshotPublicationError("snapshot status must be success")
    if run.scope_kind != "full":
        raise SnapshotPublicationError("only full scope snapshots can publish")
    missing = [field for field in _REQUIRED_IDENTITY_FIELDS if getattr(run, field) is None]
    if missing:
        raise SnapshotPublicationError(f"missing snapshot identity: {', '.join(missing)}")
    if run.coverage_ratio is None or run.coverage_ratio < 0.95:
        raise SnapshotPublicationError("coverage is below publication threshold")
    if len(items) != run.eligible_item_count:
        raise SnapshotPublicationError("item count does not match eligible item count")
    if (run.summary_json or {}).get("item_count") != len(items):
        raise SnapshotPublicationError("summary item count does not match persisted items")
    if any(item.asset_type != "etf" for item in items):
        raise SnapshotPublicationError("full ETF snapshot contains a non-ETF item")
    if [item.global_rank for item in items] != list(range(1, len(items) + 1)):
        raise SnapshotPublicationError("global ranks are not continuous")
    if any(
        item.score_eligible is not True
        or item.ranking_score is None
        or not math.isfinite(item.ranking_score)
        for item in items
    ):
        raise SnapshotPublicationError("snapshot contains an ineligible or non-finite ranking score")


async def publish_full_snapshot(session: AsyncSession, *, run_id: int) -> ShortResearchSignalRun:
    async with session.begin():
        run = await session.scalar(
            select(ShortResearchSignalRun).where(ShortResearchSignalRun.id == run_id).with_for_update()
        )
        if run is None:
            raise SnapshotPublicationError("snapshot run does not exist")
        if run.publication_state == "published":
            return run
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run.id)
                .order_by(ShortResearchSignalItem.global_rank.asc(), ShortResearchSignalItem.asset_code.asc())
            )
        ).all()
        _validate_publishable(run, items)
        run.publication_state = "published"
        run.published_at = utcnow()
    return run
