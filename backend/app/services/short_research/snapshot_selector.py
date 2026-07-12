from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun


@dataclass(frozen=True)
class CanonicalSnapshotSelection:
    state: Literal["ready", "waiting", "stale", "legacy", "version_mismatch"]
    run: ShortResearchSignalRun | None


def snapshot_metadata(run: ShortResearchSignalRun | None) -> dict[str, object]:
    if run is None:
        return {
            "snapshot_id": None,
            "score_version": None,
            "ranking_contract_hash": None,
            "scope_kind": None,
            "as_of_trade_date": None,
            "generated_at": None,
            "coverage_ratio": None,
            "freshness_status": "waiting",
            "limitations": ["no_snapshot"],
        }
    limitations: list[str] = []
    for field in ("score_version", "ranking_contract_hash", "scope_kind", "as_of_trade_date", "coverage_ratio"):
        if getattr(run, field) is None:
            limitations.append(f"missing_{field}")
    if run.scope_kind not in {None, "full"}:
        limitations.append("partial_scope")
    if run.publication_state != "published":
        limitations.append("not_published")
        freshness_status = "unpublished"
    elif limitations:
        freshness_status = "legacy"
    else:
        freshness_status = "unverified"
    return {
        "snapshot_id": run.id,
        "score_version": run.score_version,
        "ranking_contract_hash": run.ranking_contract_hash,
        "scope_kind": run.scope_kind,
        "as_of_trade_date": run.as_of_trade_date,
        "generated_at": run.published_at or run.finished_at or run.started_at,
        "coverage_ratio": run.coverage_ratio,
        "freshness_status": freshness_status,
        "limitations": limitations,
    }


def _etf_item_clauses() -> tuple[object, object]:
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
    return has_etf_item, has_non_etf_item


async def select_canonical_etf_snapshot(
    session: AsyncSession,
    *,
    score_version: str,
    ranking_contract_hash: str,
    price_basis: str,
    required_trade_date: date,
) -> ShortResearchSignalRun | None:
    has_etf_item, has_non_etf_item = _etf_item_clauses()
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


async def resolve_canonical_etf_snapshot(
    session: AsyncSession,
    *,
    score_version: str,
    ranking_contract_hash: str,
    price_basis: str,
    required_trade_date: date,
) -> CanonicalSnapshotSelection:
    run = await select_canonical_etf_snapshot(
        session,
        score_version=score_version,
        ranking_contract_hash=ranking_contract_hash,
        price_basis=price_basis,
        required_trade_date=required_trade_date,
    )
    if run is not None:
        return CanonicalSnapshotSelection("ready", run)

    has_etf_item, has_non_etf_item = _etf_item_clauses()
    base = (
        ShortResearchSignalRun.status == "success",
        ShortResearchSignalRun.publication_state == "published",
        ShortResearchSignalRun.scope_kind == "full",
        has_etf_item,
        ~has_non_etf_item,
    )
    stale = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            *base,
            ShortResearchSignalRun.score_version == score_version,
            ShortResearchSignalRun.ranking_contract_hash == ranking_contract_hash,
            ShortResearchSignalRun.price_basis == price_basis,
            ShortResearchSignalRun.as_of_trade_date != required_trade_date,
        )
    )
    if stale is not None:
        return CanonicalSnapshotSelection("stale", None)
    mismatch = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            *base,
            (
                (ShortResearchSignalRun.score_version != score_version)
                | (ShortResearchSignalRun.ranking_contract_hash != ranking_contract_hash)
                | (ShortResearchSignalRun.price_basis != price_basis)
            ),
        )
    )
    if mismatch is not None:
        return CanonicalSnapshotSelection("version_mismatch", None)
    legacy = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            ShortResearchSignalRun.status == "success",
            (
                ShortResearchSignalRun.scope_kind.is_(None)
                | ShortResearchSignalRun.score_version.is_(None)
                | ShortResearchSignalRun.ranking_contract_hash.is_(None)
            ),
        )
    )
    return CanonicalSnapshotSelection("legacy" if legacy is not None else "waiting", None)
