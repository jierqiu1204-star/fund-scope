from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.defaults.short_research import ASSET_TYPE_ETF, ASSET_TYPE_FUND
from app.services.short_research import jobs as jobs_module


@pytest.mark.asyncio
async def test_daily_short_research_data_job_syncs_funds_and_etfs(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_sync_short_research_data(_session: object, **kwargs: Any) -> dict[str, Any]:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        return {"asset_count": 1, "failed": 0, "asset_type": asset_type}

    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)

    result = await jobs_module.daily_short_research_data_job(object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_types"] == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_count"] == 2
    assert result["failed"] == 0
    assert result["fund"]["asset_type"] == ASSET_TYPE_FUND
    assert result["etf"]["asset_type"] == ASSET_TYPE_ETF


@pytest.mark.asyncio
async def test_daily_short_research_data_job_reports_bounded_etf_sync_as_partial(monkeypatch) -> None:
    async def fake_sync_short_research_data(_session: object, **kwargs: Any) -> dict[str, Any]:
        if kwargs["asset_type"] == ASSET_TYPE_FUND:
            return {"asset_count": 1, "failed": 0}
        return {"asset_count": 3, "failed": 0, "etfs": {"processed": 1, "skipped": 2}}

    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)

    result = await jobs_module.daily_short_research_data_job(object())  # type: ignore[arg-type]

    assert result["job_status"] == "partial"
    assert result["job_message"] == "bounded ETF daily sync deferred remaining candidates"


@pytest.mark.asyncio
async def test_daily_etf_universe_job_reports_refresh_counts(monkeypatch) -> None:
    async def fake_refresh_etf_universe(_session: object) -> dict[str, Any]:
        return {
            "discovered": 3,
            "inserted": 2,
            "updated": 1,
            "excluded": 1,
            "default_display": 2,
            "failed": 0,
            "failures": [],
        }

    async def fake_refresh_etf_theme_profiles(_session: object) -> dict[str, Any]:
        return {"total": 3, "classified": 3, "unknown": 0, "low_confidence": 0, "inserted": 3, "updated": 0}

    monkeypatch.setattr(jobs_module, "refresh_etf_universe", fake_refresh_etf_universe)
    monkeypatch.setattr(jobs_module, "refresh_etf_theme_profiles", fake_refresh_etf_theme_profiles)

    result = await jobs_module.daily_etf_universe_job(object())  # type: ignore[arg-type]

    assert result["discovered"] == 3
    assert result["inserted"] == 2
    assert result["updated"] == 1
    assert result["excluded"] == 1
    assert result["default_display"] == 2
    assert result["taxonomy"]["classified"] == 3


@pytest.mark.asyncio
async def test_daily_etf_theme_catalyst_job_reports_refresh_counts(monkeypatch) -> None:
    async def fake_refresh_theme_catalyst_snapshots(_session: object) -> dict[str, Any]:
        return {
            "as_of_date": "2026-07-03",
            "seeded": {"inserted": 4, "updated": 0},
            "snapshots": 4,
            "unavailable": 0,
            "themes": ["光模块", "光模块_proxy", "机器人", "半导体"],
        }

    monkeypatch.setattr(jobs_module, "refresh_theme_catalyst_snapshots", fake_refresh_theme_catalyst_snapshots)

    result = await jobs_module.daily_etf_theme_catalyst_job(object())  # type: ignore[arg-type]

    assert result["snapshots"] == 4
    assert result["seeded"]["inserted"] == 4
    assert "机器人" in result["themes"]


@pytest.mark.asyncio
async def test_daily_short_research_signals_job_generates_fund_and_etf_runs(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run_signal_generation(_session: object, **kwargs: Any) -> SimpleNamespace:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        is_fund = asset_type == ASSET_TYPE_FUND
        return SimpleNamespace(
            id=1 if is_fund else 2,
            status="success",
            as_of_date=date(2026, 6, 12),
            summary_json={
                "item_count": 3 if is_fund else 5,
                "fund_count": 3 if is_fund else 0,
                "etf_count": 0 if is_fund else 5,
                "conclusion_counts": {"短线观察": 1},
            },
        )

    monkeypatch.setattr(jobs_module, "run_signal_generation", fake_run_signal_generation)

    result = await jobs_module.daily_short_research_signals_job(object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_types"] == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["items"] == 8
    assert result["fund"]["funds"] == 3
    assert result["etf"]["etfs"] == 5


@pytest.mark.asyncio
async def test_daily_short_research_advisor_job_generates_fund_and_etf_reports(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run_advisor_generation(_session: object, _settings: object, **kwargs: Any) -> dict[str, Any]:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        return {"selected": 1, "succeeded": 1, "failed": 0, "asset_type": asset_type}

    monkeypatch.setattr(jobs_module, "run_advisor_generation", fake_run_advisor_generation)

    result = await jobs_module.daily_short_research_advisor_job(object(), settings=object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_types"] == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["selected"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["fund"]["asset_type"] == ASSET_TYPE_FUND
    assert result["etf"]["asset_type"] == ASSET_TYPE_ETF


@pytest.mark.asyncio
async def test_post_close_etf_data_job_prefers_intraday_snapshot(monkeypatch) -> None:
    history_called = False

    async def fake_snapshot(_session: object, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["trade_date"] == date.today()
        return {"etfs": 4, "inserted": 2, "updated": 2, "missing": 0, "quote_rows": 8}

    async def fake_sync_short_research_data(_session: object, **_kwargs: Any) -> dict[str, Any]:
        nonlocal history_called
        history_called = True
        return {"asset_count": 0, "failed": 0}

    monkeypatch.setattr(jobs_module, "sync_etf_price_history_from_intraday_snapshot", fake_snapshot)
    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)

    result = await jobs_module.post_close_etf_data_job(object())  # type: ignore[arg-type]

    assert history_called is False
    assert result["asset_type"] == ASSET_TYPE_ETF
    assert result["asset_count"] == 4
    assert result["failed"] == 0
    assert result["source"] == "intraday_snapshot"


@pytest.mark.asyncio
async def test_post_close_etf_data_job_defers_history_when_snapshot_is_partial(monkeypatch) -> None:
    history_called = False

    async def fake_snapshot(_session: object, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["trade_date"] == date.today()
        return {
            "etfs": 4,
            "inserted": 1,
            "updated": 0,
            "missing": 1,
            "skipped_too_early": 2,
            "quote_rows": 6,
            "needs_history_provider": True,
        }

    async def fake_sync_short_research_data(_session: object, **_kwargs: Any) -> dict[str, Any]:
        nonlocal history_called
        history_called = True
        return {"asset_count": 4, "failed": 0, "asset_type": ASSET_TYPE_ETF}

    monkeypatch.setattr(jobs_module, "sync_etf_price_history_from_intraday_snapshot", fake_snapshot)
    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)

    result = await jobs_module.post_close_etf_data_job(object())  # type: ignore[arg-type]

    assert history_called is False
    assert result["asset_type"] == ASSET_TYPE_ETF
    assert result["asset_count"] == 4
    assert result["failed"] == 0
    assert result["source"] == "intraday_snapshot_partial"
    assert result["needs_history_provider"] is True
    assert result["history_provider_deferred"] is True
    assert result["deferred_history_provider_count"] == 3
    assert result["snapshot"]["skipped_too_early"] == 2
    assert result["job_status"] == "partial"


@pytest.mark.asyncio
async def test_post_close_etf_data_job_defers_history_when_snapshot_unavailable(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_snapshot(_session: object, **_kwargs: Any) -> dict[str, Any]:
        return {"etfs": 4, "inserted": 0, "updated": 0, "missing": 4, "quote_rows": 0}

    async def fake_sync_short_research_data(_session: object, **kwargs: Any) -> dict[str, Any]:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        assert kwargs["sync_all_etfs"] is True
        return {"asset_count": 4, "failed": 1, "asset_type": asset_type}

    monkeypatch.setattr(jobs_module, "sync_etf_price_history_from_intraday_snapshot", fake_snapshot)
    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)

    result = await jobs_module.post_close_etf_data_job(object())  # type: ignore[arg-type]

    assert calls == []
    assert result["asset_type"] == ASSET_TYPE_ETF
    assert result["asset_count"] == 4
    assert result["failed"] == 0
    assert result["source"] == "intraday_snapshot_unavailable"
    assert result["needs_history_provider"] is True
    assert result["history_provider_deferred"] is True
    assert result["deferred_history_provider_count"] == 4
    assert result["etf"]["missing"] == 4
    assert result["job_status"] == "skipped"


@pytest.mark.asyncio
async def test_etf_history_backfill_job_syncs_all_etfs(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_sync_short_research_data(_session: object, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"asset_count": 8, "failed": 0, "asset_type": kwargs["asset_type"]}

    async def fake_coverage(_session: object) -> dict[str, Any]:
        return {
            "rows": 1200,
            "etfs": 8,
            "earliest_trade_date": "2024-06-01",
            "latest_trade_date": "2026-06-26",
        }

    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)
    monkeypatch.setattr(jobs_module, "_etf_price_history_coverage", fake_coverage)

    result = await jobs_module.etf_history_backfill_job(object(), days=730)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert calls[0]["asset_type"] == ASSET_TYPE_ETF
    assert calls[0]["sync_all_etfs"] is True
    assert result["days"] == 730
    assert result["asset_count"] == 8
    assert result["coverage"]["etfs"] == 8
    assert result["source"] == "history_provider_long_backfill"


@pytest.mark.asyncio
async def test_etf_label_historical_replay_job_defaults_to_full_batched_run(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    async def fake_run_etf_label_historical_replay(
        _session: object,
        *,
        days: int,
        max_assets: int | None,
        batch_size: int,
    ) -> SimpleNamespace:
        calls["days"] = days
        calls["max_assets"] = max_assets
        calls["batch_size"] = batch_size
        return SimpleNamespace(
            id=23,
            status="success",
            validation_mode="historical_replay",
            as_of_date=date(2026, 7, 3),
            rule_version="label_validation_v1",
            summary_json={
                "asset_count": 1442,
                "evaluated_asset_count": 1442,
                "completed_samples": 100,
                "excluded_samples": 10,
                "groups": [{"key": "高位观察 / 冲高别追"}],
                "universe_scope": "all_eligible",
                "batch_size": 25,
            },
        )

    monkeypatch.setattr(jobs_module, "run_etf_label_historical_replay", fake_run_etf_label_historical_replay)

    result = await jobs_module.etf_label_historical_replay_job(object(), days=180)  # type: ignore[arg-type]

    assert calls == {"days": 180, "max_assets": None, "batch_size": 25}
    assert result["processed_etfs"] == 1442
    assert result["universe_scope"] == "all_eligible"
    assert result["batch_size"] == 25


@pytest.mark.asyncio
async def test_etf_score_bucket_validation_job_defaults_to_opportunity_topn(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    async def fake_run_etf_score_bucket_validation(
        _session: object,
        *,
        days: int,
        score_basis: str,
        top_n: list[int],
    ) -> SimpleNamespace:
        calls["days"] = days
        calls["score_basis"] = score_basis
        calls["top_n"] = top_n
        return SimpleNamespace(
            id=31,
            status="success",
            validation_mode="score_bucket_replay",
            as_of_date=date(2026, 7, 3),
            rule_version="score_bucket_replay_v2",
            summary_json={
                "score_basis": "opportunity",
                "top_n": [5, 10, 20, 50],
                "baseline": "all_scored",
                "source_signal_run_count": 7,
                "groups": [
                    {"label": "Top 5", "entry_timing_label": "cumulative"},
                    {"label": "all_scored", "entry_timing_label": "baseline"},
                ],
            },
        )

    monkeypatch.setattr(jobs_module, "run_etf_score_bucket_validation", fake_run_etf_score_bucket_validation)

    result = await jobs_module.etf_score_bucket_validation_job(object(), days=180)  # type: ignore[arg-type]

    assert calls == {"days": 180, "score_basis": "opportunity", "top_n": [5, 10, 20, 50]}
    assert result["validation_mode"] == "score_bucket_replay"
    assert result["score_basis"] == "opportunity"
    assert result["top_n"] == [5, 10, 20, 50]
    assert result["baseline"] == "all_scored"
    assert result["source_signal_runs"] == 7
    assert result["groups"] == 2


@pytest.mark.asyncio
async def test_post_close_etf_signals_job_generates_only_etf_run(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run_signal_generation(_session: object, **kwargs: Any) -> SimpleNamespace:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        return SimpleNamespace(
            id=7,
            status="success",
            as_of_date=date(2026, 6, 25),
            summary_json={
                "item_count": 9,
                "fund_count": 0,
                "etf_count": 9,
                "conclusion_counts": {"短线观察": 3},
            },
        )

    monkeypatch.setattr(jobs_module, "run_signal_generation", fake_run_signal_generation)

    result = await jobs_module.post_close_etf_signals_job(object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_ETF]
    assert result["asset_type"] == ASSET_TYPE_ETF
    assert result["run_id"] == 7
    assert result["items"] == 9
    assert result["etfs"] == 9


@pytest.mark.asyncio
async def test_post_close_etf_observation_portfolio_job_refreshes_weights(monkeypatch) -> None:
    async def fake_run_etf_observation_portfolio_optimization(_session: object) -> SimpleNamespace:
        return SimpleNamespace(
            id=11,
            status="success",
            as_of_date=date(2026, 6, 25),
            summary_json={
                "cash_weight": 0.0,
                "weight_sum": 1.0,
                "unavailable_reason": None,
                    "constraint_summary": {
                        "primary_count": 3,
                        "satellite_count": 0,
                        "defensive_count": 0,
                        "watch_only_count": 2,
                        "excluded_count": 1,
                    },
            },
        )

    monkeypatch.setattr(
        jobs_module,
        "run_etf_observation_portfolio_optimization",
        fake_run_etf_observation_portfolio_optimization,
    )

    result = await jobs_module.post_close_etf_observation_portfolio_job(object())  # type: ignore[arg-type]

    assert result == {
        "snapshot_id": 11,
        "status": "success",
        "as_of_date": "2026-06-25",
        "cash_weight": 0.0,
        "weight_sum": 1.0,
        "unavailable_reason": None,
        "primary_count": 3,
        "satellite_count": 0,
        "defensive_count": 0,
        "watch_only_count": 2,
        "excluded_count": 1,
    }
