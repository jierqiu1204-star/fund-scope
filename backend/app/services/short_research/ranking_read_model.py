from __future__ import annotations

import math
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun
from app.services.short_research.ranking_surfaces import DUAL_RANKING_RULE_VERSION
from app.services.short_research.service import (
    ComputedAsset,
    _attach_optimized_allocation,
    _cached_asset_from_signal_item,
    _metadata_map_for_signal_items,
    _observation_snapshot_is_usable,
    cached_signal_assets,
    etf_observation_portfolio,
    latest_observation_portfolio_snapshot,
    observation_portfolio_from_snapshot,
)


class IndexedRankingReadError(RuntimeError):
    """The immutable snapshot does not satisfy the indexed read contract."""


def can_read_indexed_research_page(
    run: ShortResearchSignalRun,
    *,
    asset_type: str | None,
    ranking_surface: str | None,
    sort: str,
    universe: str,
    limit: int | None,
    theme: str | None,
    codes: Collection[str] | None,
    q: str | None,
    observation_labels: Collection[str] | None,
    entry_labels: Collection[str] | None,
    tracking_states: Collection[str] | None,
) -> bool:
    return (
        asset_type == ASSET_TYPE_ETF
        and ranking_surface == "research"
        and sort == "score"
        and run.status == "success"
        and run.publication_state == "published"
        and run.score_version == "daily_reconstructable_v1"
        and run.rule_version == DUAL_RANKING_RULE_VERSION
        and run.eligible_item_count is not None
        and universe in {"default", "all"}
        and limit is not None
        and not theme
        and not codes
        and not q
        and not observation_labels
        and not entry_labels
        and not tracking_states
    )


async def indexed_research_assets_page(
    session: AsyncSession,
    run: ShortResearchSignalRun,
    *,
    limit: int,
    offset: int,
) -> tuple[list[ComputedAsset], int]:
    """Read one canonical research page using the persisted rank index."""

    conditions = (
        ShortResearchSignalItem.run_id == run.id,
        ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
        ShortResearchSignalItem.score_eligible.is_(True),
        ShortResearchSignalItem.ranking_score.is_not(None),
        ShortResearchSignalItem.global_rank.is_not(None),
    )
    total = int(run.eligible_item_count or 0)
    rows = await session.scalars(
        select(ShortResearchSignalItem)
        .where(*conditions)
        .order_by(
            ShortResearchSignalItem.global_rank.asc(),
            ShortResearchSignalItem.asset_code.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    items = list(rows.all())
    metadata_by_key = await _metadata_map_for_signal_items(session, items)
    assets: list[ComputedAsset] = []
    for index, item in enumerate(items, start=offset + 1):
        metadata = metadata_by_key.get((item.asset_type, item.asset_code))
        if metadata is None:
            raise IndexedRankingReadError(
                f"missing metadata for canonical ETF {item.asset_code}"
            )
        asset = _cached_asset_from_signal_item(
            item,
            metadata,
            as_of_date=run.as_of_date,
        )
        score = asset.metrics.get("research_score")
        rank = asset.metrics.get("research_rank")
        if (
            asset.metrics.get("research_score_eligible") is not True
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or isinstance(rank, bool)
            or not isinstance(rank, int)
            or rank < 1
            or rank != item.global_rank
        ):
            raise IndexedRankingReadError(
                f"canonical research fields are inconsistent for {item.asset_code}"
            )
        assets.append(
            ComputedAsset(
                metadata=asset.metadata,
                rank=rank,
                total_score=float(score),
                conclusion=asset.conclusion,
                latest_date=asset.latest_date,
                latest_value=asset.latest_value,
                usable_days=asset.usable_days,
                sample_level=asset.sample_level,
                metrics={**asset.metrics, "ranking_surface": "research"},
                score_breakdown=asset.score_breakdown,
                risk_flags=asset.risk_flags,
                rationale=asset.rationale,
                source_note=asset.source_note,
                entry_timing_label=asset.entry_timing_label,
                entry_timing_reason=asset.entry_timing_reason,
                global_rank=rank,
                filtered_position=index,
                ranking_score=float(score),
                score_eligible=True,
            )
        )
    return assets, total


async def ranking_assets_page(
    session: AsyncSession,
    run: ShortResearchSignalRun,
    *,
    asset_type: str | None,
    theme: str | None,
    q: str | None,
    sort: str,
    universe: str,
    limit: int | None,
    offset: int,
    observation_labels: set[str],
    entry_labels: set[str],
    tracking_states: set[str],
    ranking_surface: str | None,
) -> tuple[list[ComputedAsset], int]:
    if can_read_indexed_research_page(
        run,
        asset_type=asset_type,
        ranking_surface=ranking_surface,
        sort=sort,
        universe=universe,
        limit=limit,
        theme=theme,
        codes=None,
        q=q,
        observation_labels=observation_labels,
        entry_labels=entry_labels,
        tracking_states=tracking_states,
    ):
        assert limit is not None
        try:
            return await indexed_research_assets_page(
                session,
                run,
                limit=limit,
                offset=offset,
            )
        except IndexedRankingReadError:
            pass
    return await cached_signal_assets(
        session,
        run,
        asset_type=asset_type,
        theme=theme,
        q=q,
        sort=sort,
        universe=universe,
        limit=limit,
        offset=offset,
        observation_labels=observation_labels,
        entry_labels=entry_labels,
        ranking_surface=ranking_surface,
    )


async def observation_portfolio_for_run(
    session: AsyncSession,
    run: ShortResearchSignalRun,
) -> dict[str, object]:
    """Reuse a persisted portfolio only when it belongs to the exact snapshot."""

    snapshot = await latest_observation_portfolio_snapshot(session)
    if (
        snapshot is not None
        and snapshot.source_signal_run_id == run.id
        and _observation_snapshot_is_usable(snapshot)
    ):
        portfolio = await observation_portfolio_from_snapshot(session, snapshot)
        return await _attach_optimized_allocation(session, portfolio)
    return await etf_observation_portfolio(session, source_run=run)
