from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models.entities import (
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    authorize_snapshot_publication,
)
from app.services.short_research.snapshot_selector import (
    required_etf_snapshot_trade_date,
    resolve_canonical_etf_snapshot,
    select_canonical_etf_snapshot,
    select_current_canonical_etf_snapshot,
    snapshot_metadata,
)


async def _seed_run(
    app,
    *,
    scope_kind: str,
    score_version: str = "final_score_v3",
    rule_version: str = "final_score_v3_rule_v2",
    contract_hash: str = "current-contract",
    score_field: str = "ranking_score",
    price_basis: str = "total_return_adjusted",
    trade_date: date = date(2026, 1, 2),
    asset_type: str = "etf",
    extra_asset_types: tuple[str, ...] = (),
    status: str = "success",
    decision_data_coverage_ratio: float = 1.0,
    score_coverage_ratio: float = 1.0,
    published_at: datetime = datetime(2026, 1, 2, 16, 0),
) -> int:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status=status,
            as_of_date=trade_date,
            scope_kind=scope_kind,
            scope_hash=f"scope-{scope_kind}-{trade_date.isoformat()}",
            universe_snapshot_hash=f"universe-{trade_date.isoformat()}",
            input_snapshot_hash=f"input-{published_at.isoformat()}",
            score_version=score_version,
            rule_version=rule_version,
            ranking_contract_hash=contract_hash,
            score_field=score_field,
            data_cutoff=datetime.combine(trade_date, datetime.min.time()).replace(hour=15),
            price_basis=price_basis,
            as_of_trade_date=trade_date,
            expected_item_count=1,
            decision_data_item_count=1,
            decision_data_coverage_ratio=decision_data_coverage_ratio,
            eligible_item_count=1,
            coverage_ratio=score_coverage_ratio,
            idempotency_key=(
                f"seed-{scope_kind}-{score_version}-{contract_hash}-{published_at.isoformat()}-{asset_type}-{trade_date}"
            ),
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
                    ranking_score=80.0,
                    score_eligible=True,
                    global_rank=index + 1,
                    conclusion="观察",
                )
                for index, item_asset_type in enumerate((asset_type, *extra_asset_types))
            ]
        )
        await session.commit()
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = published_at
            await session.flush()
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


@pytest.mark.asyncio
async def test_current_selector_rejects_wrong_rule_field_date_and_coverage(app) -> None:
    canonical_id = await _seed_run(app, scope_kind="full")
    await _seed_run(
        app,
        scope_kind="full",
        rule_version="obsolete-rule",
        published_at=datetime(2026, 1, 2, 17, 0),
    )
    await _seed_run(
        app,
        scope_kind="full",
        score_field="total_score",
        published_at=datetime(2026, 1, 2, 18, 0),
    )
    await _seed_run(
        app,
        scope_kind="full",
        score_coverage_ratio=0.94,
        published_at=datetime(2026, 1, 2, 19, 0),
    )
    await _seed_run(
        app,
        scope_kind="full",
        trade_date=date(2026, 1, 1),
        published_at=datetime(2026, 1, 2, 20, 0),
    )

    async with app.state.db.session() as session:
        selected = await select_current_canonical_etf_snapshot(
            session,
            required_trade_date=date(2026, 1, 2),
        )

    assert selected is not None
    assert selected.id == canonical_id


def test_required_etf_snapshot_trade_date_uses_completed_exchange_session() -> None:
    assert required_etf_snapshot_trade_date(datetime(2026, 7, 15, 14, 59)) == date(2026, 7, 14)
    assert required_etf_snapshot_trade_date(datetime(2026, 7, 15, 15, 0)) == date(2026, 7, 15)
    assert required_etf_snapshot_trade_date(datetime(2026, 7, 18, 12, 0)) == date(2026, 7, 17)


@pytest.mark.asyncio
async def test_snapshot_metadata_exposes_decision_and_score_coverage(app) -> None:
    run_id = await _seed_run(app, scope_kind="full")
    async with app.state.db.session() as session:
        run = await session.get(ShortResearchSignalRun, run_id)
        metadata = snapshot_metadata(run)

    assert metadata["decision_data_item_count"] == 1
    assert metadata["decision_data_coverage_ratio"] == 1.0
    assert metadata["score_eligible_item_count"] == 1
    assert metadata["score_coverage_ratio"] == 1.0
