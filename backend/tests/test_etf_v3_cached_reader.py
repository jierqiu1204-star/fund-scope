from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.routes.short_research import _asset_out
from app.models.entities import (
    EtfDataHealth,
    EtfOptimizedAllocationSnapshot,
    EtfPriceHistory,
    EtfThemeProfile,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
    utcnow,
)
from app.services.short_research import optimized_allocation as optimized_allocation_module
from app.services.short_research import service as short_research_service
from app.services.short_research.optimized_allocation import (
    OptimizerCandidate,
    _candidate_input_hash,
    _eligible_candidates,
    latest_optimized_allocation_snapshot,
    optimized_allocation_payload,
    run_etf_optimized_allocation,
)
from app.services.short_research.ranking_surfaces import actionable_rank_manifest
from app.services.short_research.service import cached_signal_assets
from app.services.short_research.snapshot_selector import CanonicalSnapshotSelection


@pytest.mark.asyncio
async def test_cached_v3_reader_uses_typed_score_for_output_and_opportunity_sort(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=code,
                    name=f"v3 reader {code}",
                    exchange="SH",
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_market",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
                for code in ("510301", "510302")
            ]
        )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 15),
            score_version="final_score_v3",
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="510301",
                    rank=1,
                    global_rank=1,
                    total_score=90.0,
                    ranking_score=90.0,
                    score_eligible=True,
                    conclusion="短线观察",
                    score_breakdown_json={"final_score_v2": {"final_score": 10.0}},
                    metrics_json={"default_display_eligible": True},
                ),
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="510302",
                    rank=2,
                    global_rank=2,
                    total_score=80.0,
                    ranking_score=80.0,
                    score_eligible=True,
                    conclusion="短线观察",
                    score_breakdown_json={"final_score_v2": {"final_score": 99.0}},
                    metrics_json={"default_display_eligible": True},
                ),
            ]
        )
        await session.commit()

        assets, total = await cached_signal_assets(
            session,
            run,
            asset_type="etf",
            sort="opportunity",
            universe="all",
        )

    assert total == 2
    assert [asset.metadata.code for asset in assets] == ["510301", "510302"]
    output = _asset_out(assets[0], signal_run=run)
    assert output.ranking_score == 90.0
    assert output.score_eligible is True


@pytest.mark.asyncio
async def test_observation_portfolio_never_reuses_cache_without_current_canonical_run(monkeypatch) -> None:
    cached = SimpleNamespace(
        source_signal_run_id=7,
        summary_json={"portfolio_mode": "cash_wait"},
    )

    async def fake_latest_snapshot(_session):
        return cached

    async def fake_latest_run(_session, **_kwargs):
        return None

    async def fail_cached_portfolio(*_args, **_kwargs):
        raise AssertionError("stale portfolio cache must not be reused")

    class FakeSession:
        async def scalar(self, _query):
            return None

    monkeypatch.setattr(short_research_service, "latest_observation_portfolio_snapshot", fake_latest_snapshot)
    monkeypatch.setattr(short_research_service, "latest_signal_run", fake_latest_run)
    monkeypatch.setattr(short_research_service, "observation_portfolio_from_snapshot", fail_cached_portfolio)

    result = await short_research_service.etf_observation_portfolio(
        FakeSession(),  # type: ignore[arg-type]
        include_optimized=False,
    )

    assert result["items"] == []
    assert result["evidence_status"] == "等待验证"


@pytest.mark.asyncio
async def test_static_etf_endpoints_expose_stale_state_without_legacy_fallback(client, monkeypatch) -> None:
    async def stale_selection(_session):
        return CanonicalSnapshotSelection("stale", None)

    monkeypatch.setattr(
        "app.api.routes.short_research.current_etf_snapshot_selection",
        stale_selection,
    )

    listing = await client.get("/api/short-research/assets?asset_type=etf")
    detail = await client.get("/api/short-research/assets/etf/510300")

    assert listing.status_code == 200
    assert listing.json()["items"] == []
    assert listing.json()["snapshot"]["freshness_status"] == "stale"
    assert "canonical_snapshot_stale" in listing.json()["snapshot"]["limitations"]
    assert detail.status_code == 503
    assert detail.json()["detail"]["snapshot_state"] == "stale"


@pytest.mark.asyncio
async def test_latest_optimized_allocation_never_returns_legacy_source(app) -> None:
    async with app.state.db.session() as session:
        legacy_run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 7, 15),
        )
        session.add(legacy_run)
        await session.flush()
        session.add(
            EtfOptimizedAllocationSnapshot(
                status="success",
                source_signal_run_id=legacy_run.id,
                as_of_date=legacy_run.as_of_date,
            )
        )
        await session.commit()

        snapshot = await latest_optimized_allocation_snapshot(session)

    assert snapshot is None


@pytest.mark.asyncio
async def test_optimized_allocation_run_waits_without_canonical_v3(app) -> None:
    async with app.state.db.session() as session:
        legacy_run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 7, 15),
        )
        session.add(legacy_run)
        await session.commit()

        snapshot = await run_etf_optimized_allocation(session)

    assert snapshot.status == "waiting"
    assert snapshot.source_signal_run_id is None


@pytest.mark.asyncio
async def test_optimized_candidates_require_typed_eligible_ranking_score(app) -> None:
    async with app.state.db.session() as session:
        actionable = actionable_rank_manifest()
        signal_date = date(2026, 7, 15)
        run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=signal_date,
            as_of_trade_date=signal_date,
            data_cutoff=datetime(2026, 7, 15, 15, 0),
        )
        session.add(run)
        await session.flush()
        for code, ranking_score, score_eligible in (
            ("510311", 12.5, True),
            ("510312", 98.0, False),
        ):
            session.add(
                TradableEtf(
                    code=code,
                    name=f"optimizer {code}",
                    exchange="SH",
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_market",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            session.add(
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=1,
                    global_rank=1,
                    total_score=99.0,
                    ranking_score=ranking_score,
                    score_eligible=score_eligible,
                    conclusion="短线观察",
                    score_breakdown_json={},
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={
                        "actionable_contract_id": actionable.contract_id,
                        "actionable_contract_hash": actionable.manifest_hash,
                        "actionable_as_of_date": signal_date.isoformat(),
                        "actionable_eligible": score_eligible,
                        "actionable_rank": 1 if score_eligible else None,
                        "actionable_score": ranking_score if score_eligible else None,
                        "market_data_reliability": "verified",
                        "theme_group": "frozen_theme",
                    },
                )
            )
            for offset in range(60):
                close = 1.0 + offset / 1000
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=date(2026, 4, 1) + timedelta(days=offset),
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        volume=1_000_000,
                        turnover=10_000_000,
                        pct_change=0.1,
                        research_adjusted_value=close,
                        research_price_basis="total_return_adjusted",
                        data_provider="eastmoney",
                        provider_version="eastmoney.push2his.kline.hfq_v1",
                        source_timestamp=datetime(2026, 7, 15, 6, 30),
                        adjustment_version="eastmoney.push2his.kline.hfq_v1",
                        decision_eligible=True,
                    )
                )
        await session.commit()

        candidates = await _eligible_candidates(session, run)

    assert [(candidate.code, candidate.score) for candidate in candidates] == [("510311", 12.5)]


@pytest.mark.asyncio
async def test_optimized_candidates_use_only_adjusted_prices_available_by_signal_date(app) -> None:
    code = "510313"
    signal_date = date(2026, 7, 15)
    actionable = actionable_rank_manifest()
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name="optimizer cutoff",
                exchange="SH",
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad_market",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=signal_date,
            as_of_trade_date=signal_date,
            data_cutoff=datetime(2026, 7, 15, 15, 0),
        )
        session.add(run)
        await session.flush()
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type="etf",
                asset_code=code,
                rank=1,
                global_rank=1,
                total_score=80.0,
                ranking_score=80.0,
                score_eligible=True,
                conclusion="短线观察",
                risk_flags_json=[],
                metrics_json={
                    "actionable_contract_id": actionable.contract_id,
                    "actionable_contract_hash": actionable.manifest_hash,
                    "actionable_as_of_date": signal_date.isoformat(),
                    "actionable_eligible": True,
                    "actionable_rank": 1,
                    "actionable_score": 80.0,
                    "market_data_reliability": "verified",
                    "theme_group": "frozen_theme",
                },
            )
        )
        session.add(EtfDataHealth(etf_code=code, status="failed"))
        session.add(
            EtfThemeProfile(
                etf_code=code,
                asset_bucket="future_bucket",
                theme_group="future_theme",
                primary_theme="future_theme",
            )
        )
        for offset in range(61):
            adjusted = 100.0 + offset
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=date(2026, 5, 1) + timedelta(days=offset),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0 if offset % 2 == 0 else 10_000.0,
                    volume=1_000_000,
                    turnover=10_000_000,
                    pct_change=0.0,
                    research_adjusted_value=adjusted,
                    research_price_basis="total_return_adjusted",
                    data_provider="eastmoney",
                    provider_version="eastmoney.push2his.kline.hfq_v1",
                    source_timestamp=(
                        datetime(2026, 7, 15, 7, 30)
                        if offset == 30
                        else datetime(2026, 7, 15, 6, 30)
                    ),
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    decision_eligible=True,
                )
            )
        session.add(
            EtfPriceHistory(
                etf_code=code,
                trade_date=signal_date + timedelta(days=1),
                open=999_999.0,
                high=999_999.0,
                low=999_999.0,
                close=999_999.0,
                volume=1_000_000,
                turnover=10_000_000,
                pct_change=999.0,
                research_adjusted_value=999_999.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                source_timestamp=datetime(2026, 7, 16, 15, 30),
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
        )
        await session.commit()

        candidates = await _eligible_candidates(session, run)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.data_date == date(2026, 6, 30)
    assert len(candidate.returns) == 59
    assert candidate.returns[-1] == pytest.approx(160.0 / 159.0 - 1.0)
    assert candidate.theme_group == "frozen_theme"
    assert candidate.adjusted_input_hash is not None


def test_optimizer_candidate_input_hash_covers_adjusted_return_history() -> None:
    common = {
        "code": "510313",
        "name": "optimizer cutoff",
        "score": 80.0,
        "theme_group": "frozen_theme",
        "data_date": date(2026, 7, 15),
        "expected_return": 0.01,
        "volatility": 0.02,
        "data_reliability": "verified",
    }

    first = OptimizerCandidate(**common, returns=(0.01, 0.02))
    revised_adjusted_history = OptimizerCandidate(**common, returns=(0.01, 0.03))

    assert _candidate_input_hash([first]) != _candidate_input_hash(
        [revised_adjusted_history]
    )


@pytest.mark.asyncio
async def test_optimizer_evidence_hash_changes_when_adjusted_inputs_change(
    app,
    monkeypatch,
) -> None:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 15),
            as_of_trade_date=date(2026, 7, 15),
            data_cutoff=datetime(2026, 7, 15, 15, 0),
            input_snapshot_hash="ranking-input-v1",
            ranking_contract_hash="ranking-contract-v1",
            price_basis="total_return_adjusted",
        )
        session.add(run)
        await session.commit()

        candidate_returns = [tuple(0.001 * (index + 1) for index in range(60))]

        async def frozen_selection(_session):
            return CanonicalSnapshotSelection(state="ready", run=run)

        async def no_observation(_session, *, source_signal_run_id):
            assert source_signal_run_id == run.id
            return None

        async def frozen_candidates(_session, _run):
            return [
                OptimizerCandidate(
                    code=f"51031{index}",
                    name=f"candidate {index}",
                    score=80.0 - index,
                    theme_group=f"theme-{index}",
                    data_date=run.as_of_date,
                    expected_return=0.01,
                    volatility=0.02,
                    returns=candidate_returns[0],
                    data_reliability="verified",
                )
                for index in range(4)
            ]

        monkeypatch.setattr(
            optimized_allocation_module,
            "_current_canonical_etf_selection",
            frozen_selection,
        )
        monkeypatch.setattr(
            optimized_allocation_module,
            "_latest_observation_snapshot",
            no_observation,
        )
        monkeypatch.setattr(
            optimized_allocation_module,
            "_eligible_candidates",
            frozen_candidates,
        )

        first = await run_etf_optimized_allocation(session)
        candidate_returns[0] = (*candidate_returns[0][:-1], 0.5)
        revised = await run_etf_optimized_allocation(session)

    assert first.data_window_json["candidate_input_hash"] != revised.data_window_json[
        "candidate_input_hash"
    ]
    assert first.evidence_contract_hash != revised.evidence_contract_hash


@pytest.mark.asyncio
async def test_optimized_payload_pins_the_canonical_source_selected_for_the_request(app, monkeypatch) -> None:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(status="success", as_of_date=date(2026, 7, 15))
        session.add(run)
        await session.flush()
        session.add(
            EtfOptimizedAllocationSnapshot(
                status="success",
                source_signal_run_id=run.id,
                as_of_date=run.as_of_date,
            )
        )
        await session.commit()

        calls = 0

        async def one_selection(_session):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise AssertionError("one request must not resolve a second canonical source")
            return CanonicalSnapshotSelection(state="ready", run=run)

        monkeypatch.setattr(optimized_allocation_module, "_current_canonical_etf_selection", one_selection)
        snapshot = await latest_optimized_allocation_snapshot(session)
        payload = await optimized_allocation_payload(
            session,
            snapshot,
            expected_source_signal_run_id=run.id,
        )

    assert payload is not None
    assert payload["status"] == "success"
    assert calls == 1


@pytest.mark.asyncio
async def test_observation_portfolio_rejects_optimized_cache_from_another_source(monkeypatch) -> None:
    stale = SimpleNamespace(source_signal_run_id=41)

    async def fake_latest(_session):
        return stale

    async def fake_payload(_session, snapshot, **_kwargs):
        return {"status": "success", "source": snapshot.source_signal_run_id} if snapshot else {"status": "waiting"}

    monkeypatch.setattr(short_research_service, "latest_optimized_allocation_snapshot", fake_latest)
    monkeypatch.setattr(short_research_service, "optimized_allocation_payload", fake_payload)

    portfolio = {"source_ranking_snapshot": {"snapshot_id": 42}}
    result = await short_research_service._attach_optimized_allocation(object(), portfolio)  # type: ignore[arg-type]

    assert result["optimized_allocation"]["status"] == "waiting"
