from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ShortResearchSignalRun


def _proven_scope_kind(config: dict[str, Any]) -> str | None:
    theme = config.get("theme")
    codes = config.get("codes")
    has_theme = isinstance(theme, str) and bool(theme.strip())
    has_codes = isinstance(codes, list) and any(isinstance(code, str) and code.strip() for code in codes)
    if has_theme == has_codes:
        return None
    return "theme" if has_theme else "codes"


async def backfill_proven_snapshot_facts(session: AsyncSession) -> dict[str, int]:
    runs = (
        await session.scalars(
            select(ShortResearchSignalRun).where(ShortResearchSignalRun.scope_kind.is_(None))
        )
    ).all()
    scope_kind_backfilled = 0
    for run in runs:
        scope_kind = _proven_scope_kind(dict(run.config_json or {}))
        if scope_kind is not None:
            run.scope_kind = scope_kind
            scope_kind_backfilled += 1

    await session.flush()
    return {
        "scanned": len(runs),
        "scope_kind_backfilled": scope_kind_backfilled,
        "legacy_unreconstructed": len(runs) - scope_kind_backfilled,
    }
