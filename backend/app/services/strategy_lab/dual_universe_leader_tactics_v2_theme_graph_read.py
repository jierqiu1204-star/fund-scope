"""Read-only readiness projection for the persisted A-share theme graph."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.dual_universe_leader_tactics_v2_tickflow_provider import (
    TICKFLOW_SW_PATH_SOURCE,
)


def _cutoff(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _coverage(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator else 0.0,
    }


async def read_ashare_theme_graph_readiness(
    session: AsyncSession,
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Aggregate sealed facts only; missing migrations fail closed."""

    cutoff = _cutoff(as_of)
    unavailable: list[str] = []
    try:
        universe_date = (
            await session.execute(
                text(
                    """
                    SELECT MAX(snapshot_date)
                    FROM ashare_research_universe_snapshots
                    WHERE received_at <= :cutoff
                      AND source_cutoff <= :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
        ).scalar_one_or_none()
        universe_count = 0
        if universe_date is not None:
            universe_count = int(
                (
                    await session.execute(
                        text(
                            """
                            SELECT COUNT(DISTINCT asset_code)
                            FROM ashare_research_universe_snapshots
                            WHERE snapshot_date = :snapshot_date
                              AND received_at <= :cutoff
                              AND source_cutoff <= :cutoff
                              AND listing_state = 'listed'
                            """
                        ),
                        {"snapshot_date": universe_date, "cutoff": cutoff},
                    )
                ).scalar_one()
                or 0
            )
        complete = (
            await session.execute(
                text(
                    """
                    SELECT source_snapshot_id, source_snapshot_date, received_at,
                           content_hash, expected_count, completed_count
                    FROM ashare_theme_capture_runs
                    WHERE source = :source
                      AND status = 'complete'
                      AND received_at <= :cutoff
                    ORDER BY source_snapshot_date DESC, received_at DESC, run_hash DESC
                    LIMIT 1
                    """
                ),
                {"source": TICKFLOW_SW_PATH_SOURCE, "cutoff": cutoff},
            )
        ).mappings().first()
        latest_attempt = (
            await session.execute(
                text(
                    """
                    SELECT status, source_snapshot_date, received_at, error_reason,
                           expected_count, completed_count
                    FROM ashare_theme_capture_runs
                    WHERE source = :source
                      AND received_at <= :cutoff
                    ORDER BY received_at DESC, run_hash DESC
                    LIMIT 1
                    """
                ),
                {"source": TICKFLOW_SW_PATH_SOURCE, "cutoff": cutoff},
            )
        ).mappings().first()
        snapshot_id = str(complete["source_snapshot_id"]) if complete else None
        path_count = l1_count = l2_count = l3_count = 0
        theme_member_count = theme_relation_count = 0
        if snapshot_id is not None:
            path_row = (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(DISTINCT asset_code) AS path_count,
                               COUNT(DISTINCT CASE WHEN level1_label IS NOT NULL
                                                   THEN asset_code END) AS l1_count,
                               COUNT(DISTINCT CASE WHEN level2_label IS NOT NULL
                                                   THEN asset_code END) AS l2_count,
                               COUNT(DISTINCT CASE WHEN level3_label IS NOT NULL
                                                   THEN asset_code END) AS l3_count
                        FROM ashare_industry_path_facts
                        WHERE source_snapshot_hash = :snapshot_id
                          AND received_at <= :cutoff
                        """
                    ),
                    {"snapshot_id": snapshot_id, "cutoff": cutoff},
                )
            ).mappings().one()
            path_count = int(path_row["path_count"] or 0)
            l1_count = int(path_row["l1_count"] or 0)
            l2_count = int(path_row["l2_count"] or 0)
            l3_count = int(path_row["l3_count"] or 0)
            relation_row = (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(DISTINCT asset_code) AS member_count,
                               COUNT(*) AS relation_count
                        FROM ashare_fine_theme_membership_facts
                        WHERE source_snapshot_hash = :snapshot_id
                          AND received_at <= :cutoff
                        """
                    ),
                    {"snapshot_id": snapshot_id, "cutoff": cutoff},
                )
            ).mappings().one()
            theme_member_count = int(relation_row["member_count"] or 0)
            theme_relation_count = int(relation_row["relation_count"] or 0)
        state_date = (
            await session.execute(
                text(
                    """
                    SELECT MAX(state_date)
                    FROM ashare_theme_state_facts
                    WHERE received_at <= :cutoff
                      AND source_cutoff <= :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
        ).scalar_one_or_none()
        state_count = 0
        if state_date is not None:
            state_count = int(
                (
                    await session.execute(
                        text(
                            """
                            SELECT COUNT(DISTINCT context_kind || ':' || context_key)
                            FROM ashare_theme_state_facts
                            WHERE state_date = :state_date
                              AND received_at <= :cutoff
                              AND source_cutoff <= :cutoff
                              AND available = TRUE
                            """
                        ),
                        {"state_date": state_date, "cutoff": cutoff},
                    )
                ).scalar_one()
                or 0
            )
        if complete is None:
            unavailable.append(
                "theme_capture_partial"
                if latest_attempt is not None
                else "classification_graph_not_materialized"
            )
        if complete is not None and path_count == 0:
            unavailable.append("classification_graph_not_materialized")
        if state_count == 0:
            unavailable.append("theme_state_unavailable")
        stale_sources: list[str] = []
        if complete is not None:
            age = (cutoff.date() - complete["source_snapshot_date"]).days
            if age > 7:
                stale_sources.append(TICKFLOW_SW_PATH_SOURCE)
                unavailable.append("theme_snapshot_stale")
        fallback_count = max(0, universe_count - path_count)
        provider_status = (
            str(latest_attempt["status"]) if latest_attempt is not None else "unavailable"
        )
        return {
            "status": "available" if not unavailable else "unavailable",
            "authoritative_universe_count": universe_count,
            "industry_path_count": path_count,
            "industry_level_1_count": l1_count,
            "industry_level_2_count": l2_count,
            "industry_level_3_count": l3_count,
            "theme_member_count": theme_member_count,
            "theme_relation_count": theme_relation_count,
            "theme_state_count": state_count,
            "fallback_count": fallback_count,
            "coverage": {
                "industry_path": _coverage(path_count, universe_count),
                "industry_level_3": _coverage(l3_count, universe_count),
                "theme_members": _coverage(theme_member_count, universe_count),
            },
            "latest_snapshots": {
                "industry_path": (
                    complete["source_snapshot_date"].isoformat() if complete else None
                ),
                "theme_state": state_date.isoformat() if state_date else None,
                "source_snapshot_hash": snapshot_id,
                "content_hash": complete["content_hash"] if complete else None,
            },
            "provider_health": {
                TICKFLOW_SW_PATH_SOURCE: {
                    "status": provider_status,
                    "error_summary": (
                        latest_attempt["error_reason"] if latest_attempt else None
                    ),
                    "completed_count": (
                        int(latest_attempt["completed_count"])
                        if latest_attempt is not None
                        else 0
                    ),
                    "expected_count": (
                        int(latest_attempt["expected_count"])
                        if latest_attempt is not None
                        else 0
                    ),
                }
            },
            "stale_sources": stale_sources,
            "unavailable_reasons": sorted(set(unavailable)),
        }
    except (SQLAlchemyError, ValueError, TypeError):
        return {
            "status": "unavailable",
            "coverage": {},
            "latest_snapshots": {},
            "provider_health": {},
            "stale_sources": [],
            "unavailable_reasons": ["classification_graph_not_materialized"],
        }


__all__ = ["read_ashare_theme_graph_readiness"]
