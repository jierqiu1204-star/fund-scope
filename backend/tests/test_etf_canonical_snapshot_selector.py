from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun
from app.services.short_research.snapshot_selector import (
    resolve_canonical_etf_snapshot,
    select_canonical_etf_snapshot,
)


async def _seed_run(
    app,
    *,
    scope_kind: str,
    score_version: str = "final_score_v3",
    contract_hash: str = "current-contract",
    price_basis: str = "total_return_adjusted",
    trade_date: date = date(2026, 1, 2),
    asset_type: str = "etf",
    extra_asset_types: tuple[str, ...] = (),
    status: str = "success",
    published_at: datetime = datetime(2026, 1, 2, 16, 0),
) -> int:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status=status,
            as_of_date=trade_date,
            scope_kind=scope_kind,
            score_version=score_version,
            ranking_contract_hash=contract_hash,
            price_basis=price_basis,
            as_of_trade_date=trade_date,
            publication_state=None,
            published_at=None,
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type=item_asset_type,
                    asset_code=f"{run.id:05d}{index}",
                    rank=index + 1,
                    total_score=80.0,
                    conclusion="观察",
                )
                for index, item_asset_type in enumerate((asset_type, *extra_asset_types))
            ]
        )
        await session.commit()
        run.publication_state = "published"
        run.published_at = published_at
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


@pytest.mark.asyncio
async def test_later_noncanonical_runs_never_replace_a_compatible_full_etf_snapshot(app) -> None:
    canonical_id = await _seed_run(app, scope_kind="full")
    await _seed_run(app, scope_kind="theme", published_at=datetime(2026, 1, 2, 17, 0))
    await _seed_run(app, scope_kind="codes", published_at=datetime(2026, 1, 2, 18, 0))
    await _seed_run(app, scope_kind="full", asset_type="fund", published_at=datetime(2026, 1, 2, 19, 0))
    await _seed_run(
        app,
        scope_kind="full",
        extra_asset_types=("fund",),
        published_at=datetime(2026, 1, 2, 20, 0),
    )
    await _seed_run(app, scope_kind="full", status="failed", published_at=datetime(2026, 1, 2, 21, 0))
    await _seed_run(
        app,
        scope_kind="full",
        trade_date=date(2026, 1, 1),
        published_at=datetime(2026, 1, 2, 22, 0),
    )
    async with app.state.db.session() as session:
        session.add(ShortResearchSignalRun(status="success", as_of_date=date(2026, 1, 2)))
        await session.commit()
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
async def test_canonical_selector_reports_stale_and_version_mismatch_states(app) -> None:
    await _seed_run(app, scope_kind="full")

    async with app.state.db.session() as session:
        stale = await resolve_canonical_etf_snapshot(
            session,
            score_version="final_score_v3",
            ranking_contract_hash="current-contract",
            price_basis="total_return_adjusted",
            required_trade_date=date(2026, 1, 3),
        )
    assert stale.state == "stale"
    assert stale.run is None

    async with app.state.db.session() as session:
        mismatch = await resolve_canonical_etf_snapshot(
            session,
            score_version="final_score_v3",
            ranking_contract_hash="other-contract",
            price_basis="total_return_adjusted",
            required_trade_date=date(2026, 1, 2),
        )
    assert mismatch.state == "version_mismatch"

@pytest.mark.asyncio
async def test_canonical_selector_reports_legacy_when_only_legacy_runs_exist(app) -> None:
    async with app.state.db.session() as session:
        session.add(ShortResearchSignalRun(status="success", as_of_date=date(2026, 1, 2)))
        await session.commit()
        legacy = await resolve_canonical_etf_snapshot(
            session,
            score_version="final_score_v3",
            ranking_contract_hash="current-contract",
            price_basis="total_return_adjusted",
            required_trade_date=date(2026, 1, 2),
        )
    assert legacy.state == "legacy"
