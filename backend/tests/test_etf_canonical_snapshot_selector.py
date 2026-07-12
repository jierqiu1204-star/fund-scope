from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun
from app.services.short_research.snapshot_selector import select_canonical_etf_snapshot


async def _seed_run(
    app,
    *,
    scope_kind: str,
    score_version: str = "final_score_v3",
    contract_hash: str = "current-contract",
    price_basis: str = "total_return_adjusted",
    trade_date: date = date(2026, 1, 2),
    asset_type: str = "etf",
    published_at: datetime = datetime(2026, 1, 2, 16, 0),
) -> int:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=trade_date,
            scope_kind=scope_kind,
            score_version=score_version,
            ranking_contract_hash=contract_hash,
            price_basis=price_basis,
            as_of_trade_date=trade_date,
            publication_state="published",
            published_at=published_at,
        )
        session.add(run)
        await session.flush()
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type=asset_type,
                asset_code=f"{run.id:06d}",
                rank=1,
                total_score=80.0,
                conclusion="观察",
            )
        )
        await session.commit()
        return run.id


@pytest.mark.asyncio
async def test_canonical_selector_uses_only_compatible_published_full_etf_snapshot(app) -> None:
    canonical_id = await _seed_run(app, scope_kind="full")
    await _seed_run(app, scope_kind="codes", published_at=datetime(2026, 1, 2, 17, 0))
    await _seed_run(app, scope_kind="full", contract_hash="old-contract", published_at=datetime(2026, 1, 2, 18, 0))
    await _seed_run(app, scope_kind="full", asset_type="fund", published_at=datetime(2026, 1, 2, 19, 0))

    async with app.state.db.session() as session:
        selected = await select_canonical_etf_snapshot(
            session,
            score_version="final_score_v3",
            ranking_contract_hash="current-contract",
            price_basis="total_return_adjusted",
            required_trade_date=date(2026, 1, 2),
        )

    assert selected is not None
    assert selected.id == canonical_id


@pytest.mark.asyncio
async def test_canonical_selector_returns_none_when_trade_date_is_stale(app) -> None:
    await _seed_run(app, scope_kind="full")

    async with app.state.db.session() as session:
        selected = await select_canonical_etf_snapshot(
            session,
            score_version="final_score_v3",
            ranking_contract_hash="current-contract",
            price_basis="total_return_adjusted",
            required_trade_date=date(2026, 1, 2) + timedelta(days=1),
        )

    assert selected is None
