from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.entities import (
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
    authorize_snapshot_publication,
    utcnow,
)
from app.services.short_research import ranking_read_model
from app.services.short_research.ranking_read_model import (
    canonical_research_item_count,
    observation_portfolio_for_run,
    ranking_assets_page,
)
from app.services.short_research.ranking_surfaces import DUAL_RANKING_RULE_VERSION


@pytest.mark.asyncio
async def test_canonical_research_page_hydrates_only_requested_rows(
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_count = 30
    trade_date = date(2026, 8, 7)
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=trade_date,
            score_version="daily_reconstructable_v1",
            rule_version=DUAL_RANKING_RULE_VERSION,
            publication_state="unpublished",
            eligible_item_count=item_count,
            idempotency_key="indexed-research-page-fixture",
        )
        session.add(run)
        await session.flush()
        for rank in range(1, item_count + 1):
            code = f"58{rank:04d}"
            score = float(101 - rank)
            session.add(
                TradableEtf(
                    code=code,
                    name=f"Indexed ETF {rank}",
                    exchange="SH",
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            session.add(
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=rank,
                    global_rank=rank,
                    total_score=score,
                    ranking_score=score,
                    score_eligible=True,
                    conclusion="短线观察",
                    score_breakdown_json={},
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={
                        "research_score": score,
                        "research_rank": rank,
                        "research_score_eligible": True,
                        "latest_date": trade_date.isoformat(),
                        "latest_value": 1.0,
                        "usable_days": 120,
                        "sample_level": "样本充足",
                        "source_note": "indexed fixture",
                    },
                )
            )
        await session.flush()
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = utcnow()
            await session.flush()
        await session.commit()

        original_metadata_loader = ranking_read_model._metadata_map_for_signal_items
        hydrated_row_counts: list[int] = []

        async def recording_metadata_loader(
            active_session: Any,
            items: list[ShortResearchSignalItem],
        ):
            hydrated_row_counts.append(len(items))
            return await original_metadata_loader(active_session, items)

        monkeypatch.setattr(
            ranking_read_model,
            "_metadata_map_for_signal_items",
            recording_metadata_loader,
        )
        assets, total = await ranking_assets_page(
            session,
            run,
            asset_type="etf",
            theme=None,
            q=None,
            sort="score",
            universe="all",
            limit=5,
            offset=7,
            observation_labels=set(),
            entry_labels=set(),
            tracking_states=set(),
            ranking_surface="research",
        )

    assert total == item_count
    assert hydrated_row_counts == [5]
    assert [asset.metadata.code for asset in assets] == [
        "580008",
        "580009",
        "580010",
        "580011",
        "580012",
    ]
    assert [asset.filtered_position for asset in assets] == [8, 9, 10, 11, 12]


@pytest.mark.asyncio
async def test_observation_portfolio_cache_requires_exact_source_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(id=7)
    snapshot = SimpleNamespace(
        source_signal_run_id=7,
        summary_json={"portfolio_mode": "cash_wait"},
    )

    async def latest_snapshot(_session: Any):
        return snapshot

    async def snapshot_payload(_session: Any, selected_snapshot: Any):
        assert selected_snapshot is snapshot
        return {"items": [], "source": "persisted"}

    async def attach_allocation(_session: Any, portfolio: dict[str, object]):
        return {**portfolio, "allocation_attached": True}

    async def fail_recompute(*_args: Any, **_kwargs: Any):
        raise AssertionError("an exact persisted portfolio must not be recomputed")

    monkeypatch.setattr(
        ranking_read_model,
        "latest_observation_portfolio_snapshot",
        latest_snapshot,
    )
    monkeypatch.setattr(
        ranking_read_model,
        "observation_portfolio_from_snapshot",
        snapshot_payload,
    )
    monkeypatch.setattr(
        ranking_read_model,
        "_attach_optimized_allocation",
        attach_allocation,
    )
    monkeypatch.setattr(
        ranking_read_model,
        "etf_observation_portfolio",
        fail_recompute,
    )

    result = await observation_portfolio_for_run(object(), run)  # type: ignore[arg-type]

    assert result == {
        "items": [],
        "source": "persisted",
        "allocation_attached": True,
    }


def test_canonical_research_count_is_distinct_from_score_ready_count() -> None:
    run = SimpleNamespace(
        eligible_item_count=90,
        summary_json={
            "ranking_surfaces": {
                "research": {
                    "eligible_count": 37,
                }
            }
        },
    )

    assert canonical_research_item_count(run) == 37  # type: ignore[arg-type]
