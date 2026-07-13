from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    utcnow,
)
from app.services.short_research.universe import build_point_in_time_universe_snapshot


class SnapshotPublicationError(ValueError):
    pass


@dataclass(frozen=True)
class EtfCoverageBarrier:
    expected_codes: list[str]
    included_codes: list[str]
    excluded: list[dict[str, str]]

    @property
    def coverage_ratio(self) -> float:
        return len(self.included_codes) / len(self.expected_codes) if self.expected_codes else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "expected_count": len(self.expected_codes),
            "included_count": len(self.included_codes),
            "coverage_ratio": round(self.coverage_ratio, 6),
        }


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


async def build_etf_coverage_barrier(
    session: AsyncSession,
    *,
    as_of_trade_date: date | None,
) -> EtfCoverageBarrier:
    if as_of_trade_date is None:
        raise SnapshotPublicationError("snapshot trade date is required")
    universe = await build_point_in_time_universe_snapshot(session, as_of_date=as_of_trade_date)
    expected_codes = [str(member["asset_code"]) for member in universe.members]
    if not expected_codes:
        return EtfCoverageBarrier(expected_codes=[], included_codes=[], excluded=[])
    rows = (
        await session.scalars(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code.in_(expected_codes),
                EtfPriceHistory.trade_date == as_of_trade_date,
            )
        )
    ).all()
    by_code = {row.etf_code: row for row in rows}
    included_codes: list[str] = []
    excluded: list[dict[str, str]] = []
    for code in expected_codes:
        row = by_code.get(code)
        if row is None:
            reason = "missing_trade_date_price"
        elif row.decision_eligible is not True:
            reason = row.decision_ineligibility_reason or "decision_ineligible_price"
        elif row.research_price_basis != "total_return_adjusted":
            reason = "incompatible_research_price_basis"
        elif row.research_adjusted_value is None or not math.isfinite(row.research_adjusted_value):
            reason = "missing_research_adjusted_value"
        else:
            included_codes.append(code)
            continue
        excluded.append({"asset_code": code, "reason": reason})
    return EtfCoverageBarrier(expected_codes=expected_codes, included_codes=included_codes, excluded=excluded)


def _validate_publishable(run: ShortResearchSignalRun, items: Sequence[ShortResearchSignalItem]) -> None:
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
        barrier = await build_etf_coverage_barrier(session, as_of_trade_date=run.as_of_trade_date)
        if not barrier.expected_codes:
            raise SnapshotPublicationError("expected universe coverage is unavailable")
        if run.expected_item_count != len(barrier.expected_codes):
            raise SnapshotPublicationError("expected item count does not match point-in-time universe")
        if run.eligible_item_count != len(barrier.included_codes):
            raise SnapshotPublicationError("eligible item count does not match decision-data coverage")
        if run.coverage_ratio is None or not math.isclose(run.coverage_ratio, barrier.coverage_ratio, abs_tol=1e-6):
            raise SnapshotPublicationError("coverage ratio does not match decision-data coverage")
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run.id)
                .order_by(ShortResearchSignalItem.global_rank.asc(), ShortResearchSignalItem.asset_code.asc())
            )
        ).all()
        _validate_publishable(run, items)
        summary = dict(run.summary_json or {})
        summary["coverage"] = barrier.to_dict()
        run.summary_json = summary
        run.publication_state = "published"
        run.published_at = utcnow()
    return run
