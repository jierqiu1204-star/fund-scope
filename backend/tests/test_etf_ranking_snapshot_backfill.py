from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.entities import ShortResearchSignalRun
from app.services.short_research.snapshot_backfill import backfill_proven_snapshot_facts


@pytest.mark.asyncio
async def test_backfill_marks_only_explicit_legacy_scope_facts(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                ShortResearchSignalRun(
                    status="success",
                    as_of_date=date(2026, 1, 2),
                    config_json={"theme": "人工智能", "codes": []},
                ),
                ShortResearchSignalRun(
                    status="success",
                    as_of_date=date(2026, 1, 2),
                    config_json={"theme": None, "codes": ["510300"]},
                ),
                ShortResearchSignalRun(
                    status="success",
                    as_of_date=date(2026, 1, 2),
                    config_json={"theme": None, "codes": []},
                ),
            ]
        )
        await session.commit()

        result = await backfill_proven_snapshot_facts(session)
        runs = (await session.scalars(select(ShortResearchSignalRun).order_by(ShortResearchSignalRun.id))).all()

    assert result == {"scanned": 3, "scope_kind_backfilled": 2, "legacy_unreconstructed": 1}
    assert [run.scope_kind for run in runs] == ["theme", "codes", None]
    for run in runs:
        assert run.scope_hash is None
        assert run.universe_snapshot_hash is None
        assert run.input_snapshot_hash is None
        assert run.score_version is None
        assert run.ranking_contract_hash is None
        assert run.price_basis is None
        assert run.data_cutoff is None
        assert run.publication_state is None
