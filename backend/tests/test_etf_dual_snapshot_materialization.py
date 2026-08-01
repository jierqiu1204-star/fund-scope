from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import (
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
from app.services.short_research import dual_snapshot_materialization
from app.services.short_research import service as short_research_service
from app.services.short_research.advisor import run_advisor_generation
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.dual_snapshot_materialization import (
    materialize_dual_ranking_snapshot,
    publish_dual_ranking_snapshot,
)
from app.services.short_research.ranking_surfaces import actionable_rank_manifest
from app.services.short_research.service import (
    ComputedAsset,
    etf_observation_portfolio,
    latest_signal_run,
)
from app.services.short_research.snapshot_publication import EtfCoverageBarrier
from app.services.short_research.snapshot_selector import (
    resolve_current_etf_ranking_surface_snapshot,
)
from app.services.workflows.etf_live_rankings import (
    SOURCE_TOP20_SIGNAL,
    _build_research_watchlist,
)

TRADE_DATE = date(2026, 7, 17)
CUTOFF = datetime(2026, 7, 17, 15, 0)


def _asset(
    code: str,
    *,
    research_score: float,
    actionable_score: float | None,
    actionable_reasons: list[str] | None = None,
) -> ComputedAsset:
    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    return ComputedAsset(
        metadata=ShortResearchAsset(
            asset_type=ASSET_TYPE_ETF,
            code=code,
            name=f"ETF{code}",
            category="broad_index",
            theme_tags=("宽基",),
            investment_direction="fixture",
            trading_rule_label="T+1",
            exchange="SH",
        ),
        rank=None,
        total_score=50.0,
        conclusion="谨慎观察",
        latest_date=TRADE_DATE,
        latest_value=1.0,
        usable_days=250,
        sample_level="完整",
        metrics={
            "research_contract_id": research.contract_id,
            "research_score_field": research.score_field,
            "research_contract_hash": research.manifest_hash,
            "research_score": research_score,
            "research_score_eligible": True,
            "research_score_unavailable_reason": None,
            "actionable_contract_id": actionable.contract_id,
            "actionable_score_field": actionable.score_field,
            "actionable_contract_hash": actionable.manifest_hash,
            "actionable_score": actionable_score,
            "actionable_eligible": actionable_score is not None,
            "actionable_exclusion_reasons": actionable_reasons or [],
            "actionable_field_statuses": {},
            "actionable_source_times": {},
            "v3_adjusted_price_history_digest": code[0] * 64,
        },
        score_breakdown={},
        risk_flags=[],
        rationale={"reason": "fixture"},
        source_note="fixture",
        entry_timing_label="观察",
        entry_timing_reason="fixture",
    )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    assets: list[ComputedAsset],
    *,
    universe_codes: list[str] | None = None,
) -> None:
    codes = universe_codes or [asset.metadata.code for asset in assets]

    async def fake_universe(_session, *, as_of_date):
        return SimpleNamespace(
            members=[
                {
                    "asset_code": code,
                    "asset_bucket": "broad-equity",
                    "membership_source": "fixture",
                }
                for code in codes
            ]
        )

    async def fake_barrier(_session, *, as_of_trade_date, data_cutoff):
        return EtfCoverageBarrier(
            expected_codes=codes,
            included_codes=codes,
            excluded=[],
        )

    async def fake_compute(_session, **_kwargs):
        return assets

    monkeypatch.setattr(
        dual_snapshot_materialization,
        "build_point_in_time_universe_snapshot",
        fake_universe,
    )
    monkeypatch.setattr(
        dual_snapshot_materialization,
        "build_etf_coverage_barrier",
        fake_barrier,
    )
    monkeypatch.setattr(
        dual_snapshot_materialization,
        "compute_etf_snapshot_assets",
        fake_compute,
    )


@pytest.mark.asyncio
async def test_ninety_percent_materialization_is_complete_and_publishable(
    app,
    monkeypatch,
) -> None:
    universe_codes = [f"5100{index:02d}" for index in range(10)]
    assets = [
        _asset(code, research_score=80.0 - index, actionable_score=90.0 - index)
        for index, code in enumerate(universe_codes[:9])
    ]
    _install(monkeypatch, assets, universe_codes=universe_codes)

    async with app.state.db.session() as session:
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        await session.commit()
        run_id = run.id
        await publish_dual_ranking_snapshot(session, run_id=run_id)
        persisted_run = await session.get(ShortResearchSignalRun, run_id)
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run_id)
                .order_by(ShortResearchSignalItem.global_rank)
            )
        ).all()
        research = await resolve_current_etf_ranking_surface_snapshot(
            session,
            required_trade_date=TRADE_DATE,
            ranking_surface="research",
        )
        actionable = await resolve_current_etf_ranking_surface_snapshot(
            session,
            required_trade_date=TRADE_DATE,
            ranking_surface="actionable",
        )

    assert persisted_run is not None
    assert persisted_run.publication_state == "published"
    assert persisted_run.summary_json["readiness_state"] == "complete"
    assert persisted_run.summary_json["snapshot_state"] == "complete_candidate"
    assert persisted_run.summary_json["readiness_policy_version"] == (
        "etf_readiness_policy_v2"
    )
    assert (
        persisted_run.summary_json["ranking_surfaces"]["actionable"][
            "eligible_count"
        ]
        == 9
    )
    assert all(item.metrics_json["actionable_rank"] is not None for item in items)
    assert all(item.metrics_json["actionable_score"] is not None for item in items)
    assert research.state == "ready"
    assert research.run is not None and research.run.id == run_id
    assert actionable.state == "ready"
    assert actionable.run is not None and actionable.run.id == run_id


@pytest.mark.asyncio
async def test_legacy_ninety_percent_snapshot_is_not_promoted_by_v2(
    app,
    monkeypatch,
) -> None:
    universe_codes = [f"5100{index:02d}" for index in range(10)]
    assets = [
        _asset(code, research_score=80.0 - index, actionable_score=90.0 - index)
        for index, code in enumerate(universe_codes[:9])
    ]
    _install(monkeypatch, assets, universe_codes=universe_codes)

    async with app.state.db.session() as session:
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        summary = dict(run.summary_json)
        readiness = dict(summary["readiness_policy"])
        readiness["policy_version"] = "etf_readiness_policy_v1"
        readiness["state"] = "degraded"
        summary["readiness_policy"] = readiness
        summary["readiness_policy_version"] = "etf_readiness_policy_v1"
        summary["readiness_state"] = "degraded"
        run.summary_json = summary
        await session.commit()

        with pytest.raises(
            ValueError,
            match="95 percent daily and 90 percent warmup readiness",
        ):
            await publish_dual_ranking_snapshot(session, run_id=run.id)


@pytest.mark.asyncio
async def test_one_run_persists_independent_research_and_actionable_ordering(
    app,
    monkeypatch,
) -> None:
    assets = [
        _asset("510001", research_score=70.0, actionable_score=90.0),
        _asset(
            "510002",
            research_score=80.0,
            actionable_score=None,
            actionable_reasons=["bid:missing"],
        ),
    ]
    _install(monkeypatch, assets)

    async with app.state.db.session() as session:
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        await session.commit()
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run.id)
                .order_by(ShortResearchSignalItem.global_rank)
            )
        ).all()

    assert run.score_version == "daily_reconstructable_v1"
    assert run.score_field == "research_score"
    assert run.rule_version == "dual_ranking_surfaces_v1"
    assert [(item.asset_code, item.global_rank, item.ranking_score) for item in items] == [
        ("510002", 1, 80.0),
        ("510001", 2, 70.0),
    ]
    assert items[0].metrics_json["actionable_rank"] is None
    assert items[1].metrics_json["actionable_rank"] == 1
    assert run.summary_json["ranking_surfaces"]["research"]["eligible_count"] == 2
    assert run.summary_json["ranking_surfaces"]["actionable"]["eligible_count"] == 1
    assert run.summary_json["ranking_surfaces"]["actionable"]["excluded"] == [
        {"asset_code": "510002", "reasons": ["bid:missing"]}
    ]
    assert run.summary_json["draft_seal"]["snapshot_content_hash"]


@pytest.mark.asyncio
async def test_dual_snapshot_materialization_is_idempotent(app, monkeypatch) -> None:
    assets = [_asset("510001", research_score=70.0, actionable_score=90.0)]
    _install(monkeypatch, assets)

    async with app.state.db.session() as session:
        first = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        second = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )

    assert second.id == first.id


@pytest.mark.asyncio
async def test_dual_snapshot_publishes_research_when_actionable_surface_is_empty(
    app,
    monkeypatch,
) -> None:
    assets = [
        _asset(
            "510001",
            research_score=70.0,
            actionable_score=None,
            actionable_reasons=["provider_health:unhealthy"],
        )
    ]
    _install(monkeypatch, assets)

    async with app.state.db.session() as session:
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        await session.commit()
        published = await publish_dual_ranking_snapshot(session, run_id=run.id)

    assert published.publication_state == "published"
    assert published.summary_json["ranking_surfaces"]["research"]["eligible_count"] == 1
    assert published.summary_json["ranking_surfaces"]["actionable"]["eligible_count"] == 0

    async with app.state.db.session() as session:
        research = await resolve_current_etf_ranking_surface_snapshot(
            session,
            required_trade_date=TRADE_DATE,
            ranking_surface="research",
        )
        actionable = await resolve_current_etf_ranking_surface_snapshot(
            session,
            required_trade_date=TRADE_DATE,
            ranking_surface="actionable",
        )
        published_run = await session.get(ShortResearchSignalRun, published.id)
        assert published_run is not None
        portfolio = await etf_observation_portfolio(
            session,
            source_run=published_run,
            use_snapshot=False,
            include_optimized=False,
        )

    assert research.state == "ready"
    assert actionable.state == "waiting"
    assert portfolio["items"] == []
    assert portfolio["cash_weight"] == 1.0


@pytest.mark.asyncio
async def test_cached_assets_api_defaults_to_research_and_filters_actionable(
    app,
    client,
    monkeypatch,
) -> None:
    assets = [
        _asset("510001", research_score=70.0, actionable_score=90.0),
        _asset(
            "510002",
            research_score=80.0,
            actionable_score=None,
            actionable_reasons=["bid:missing"],
        ),
    ]
    _install(monkeypatch, assets)
    monkeypatch.setattr(
        short_research_service,
        "required_etf_snapshot_trade_date",
        lambda _now=None: TRADE_DATE,
    )

    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=asset.metadata.code,
                    name=asset.metadata.name,
                    exchange="SH",
                    theme_tags_json=["宽基"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
                for asset in assets
            ]
        )
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        await session.commit()
        await publish_dual_ranking_snapshot(session, run_id=run.id)
        run_id = run.id

    research_response = await client.get(
        "/api/short-research/assets?asset_type=etf&universe=all"
    )
    actionable_response = await client.get(
        "/api/short-research/assets"
        "?asset_type=etf&universe=all&ranking_surface=actionable"
    )

    assert research_response.status_code == 200
    research = research_response.json()
    assert research["ranking_surface"] == "research"
    assert research["snapshot"]["ranking_surface"] == "research"
    assert (
        research["snapshot"]["score_version"] == "daily_reconstructable_v1"
    ), research["snapshot"]
    assert research["snapshot"]["score_field"] == "research_score"
    assert [item["code"] for item in research["items"]] == ["510002", "510001"]
    assert [
        (item["rank"], item["research_rank"], item["research_score"])
        for item in research["items"]
    ] == [(1, 1, 80.0), (2, 2, 70.0)]
    assert research["items"][0]["actionable_rank"] is None
    assert research["items"][0]["actionable_exclusion_reasons"] == ["bid:missing"]

    assert actionable_response.status_code == 200
    actionable = actionable_response.json()
    assert actionable["ranking_surface"] == "actionable"
    assert actionable["snapshot"]["ranking_surface"] == "actionable"
    assert actionable["snapshot"]["score_version"] == "actionable_rank_v1"
    assert actionable["snapshot"]["score_field"] == "actionable_score"
    assert [
        (
            item["code"],
            item["rank"],
            item["actionable_rank"],
            item["actionable_score"],
        )
        for item in actionable["items"]
    ] == [("510001", 1, 1, 90.0)]
    assert actionable["items"][0]["ranking_score"] == 90.0

    async with app.state.db.session() as session:
        published_run = await session.get(ShortResearchSignalRun, run_id)
        assert published_run is not None
        portfolio = await etf_observation_portfolio(
            session,
            source_run=published_run,
            use_snapshot=False,
            include_optimized=False,
        )
    portfolio_codes = {
        item["code"]
        for key in (
            "items",
            "satellite_items",
            "defensive_items",
            "watch_only_items",
            "excluded_items",
        )
        for item in portfolio[key]
    }
    assert "510002" not in portfolio_codes
    assert portfolio["source_ranking_snapshot"]["snapshot_id"] == run_id


@pytest.mark.asyncio
async def test_ninety_percent_assets_api_exposes_complete_dual_ranking(
    app,
    client,
    monkeypatch,
) -> None:
    universe_codes = [f"5101{index:02d}" for index in range(10)]
    assets = [
        _asset(code, research_score=80.0 - index, actionable_score=90.0 - index)
        for index, code in enumerate(universe_codes[:9])
    ]
    _install(monkeypatch, assets, universe_codes=universe_codes)
    monkeypatch.setattr(
        short_research_service,
        "required_etf_snapshot_trade_date",
        lambda _now=None: TRADE_DATE,
    )

    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=code,
                    name=f"ETF{code}",
                    exchange="SH",
                    theme_tags_json=["宽基"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
                for code in universe_codes
            ]
        )
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        await session.commit()
        await publish_dual_ranking_snapshot(session, run_id=run.id)
        watchlist = await _build_research_watchlist(session)

    research_response = await client.get(
        "/api/short-research/assets?asset_type=etf&universe=all"
    )
    actionable_response = await client.get(
        "/api/short-research/assets"
        "?asset_type=etf&universe=all&ranking_surface=actionable"
    )

    assert research_response.status_code == 200
    research = research_response.json()
    assert research["snapshot"]["snapshot_state"] == "complete"
    assert research["snapshot"]["readiness_state"] == "complete"
    assert research["snapshot"]["policy_version"] == "etf_readiness_policy_v2"
    assert len(research["items"]) == 9
    assert all(item["actionable_rank"] is not None for item in research["items"])
    assert all(item["actionable_score"] is not None for item in research["items"])

    assert actionable_response.status_code == 200
    actionable = actionable_response.json()
    assert len(actionable["items"]) == 9
    assert actionable["snapshot"]["snapshot_state"] == "complete"
    assert watchlist.signal_status == "ready"
    assert all(SOURCE_TOP20_SIGNAL in item.sources for item in watchlist.items)


@pytest.mark.asyncio
async def test_dual_snapshot_feeds_research_consumers_and_actionable_live_top20(
    app,
    monkeypatch,
    settings,
) -> None:
    assets = [
        _asset("510001", research_score=70.0, actionable_score=90.0),
        _asset(
            "510002",
            research_score=80.0,
            actionable_score=None,
            actionable_reasons=["bid:missing"],
        ),
    ]
    _install(monkeypatch, assets)
    monkeypatch.setattr(
        short_research_service,
        "required_etf_snapshot_trade_date",
        lambda _now=None: TRADE_DATE,
    )

    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=asset.metadata.code,
                    name=asset.metadata.name,
                    exchange="SH",
                    theme_tags_json=["宽基"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
                for asset in assets
            ]
        )
        run = await materialize_dual_ranking_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=CUTOFF,
        )
        await session.commit()
        await publish_dual_ranking_snapshot(session, run_id=run.id)

        latest = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
        advisor = await run_advisor_generation(
            session,
            settings.model_copy(update={"llm_advisor_enabled": False}),
            asset_type=ASSET_TYPE_ETF,
            max_assets=2,
        )
        watchlist = await _build_research_watchlist(session, now=CUTOFF)

    assert latest is not None
    assert latest.id == run.id
    assert advisor["source_signal_run_id"] == run.id
    assert advisor["selected"] == 2
    assert advisor["fallback"] == 2

    by_code = {item.etf_code: item for item in watchlist.items}
    assert by_code["510001"].rank == 1
    assert SOURCE_TOP20_SIGNAL in by_code["510001"].sources
    assert by_code["510002"].rank is None
    assert SOURCE_TOP20_SIGNAL not in by_code["510002"].sources
