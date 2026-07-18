from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.defaults.short_research import ASSET_TYPE_ETF, ASSET_TYPE_FUND
from app.services.short_research import jobs as jobs_module


class _CoverageBarrier:
    def __init__(self, included: int, expected: int = 2) -> None:
        self.expected_codes = [f"5103{index:02d}" for index in range(expected)]
        self.included_codes = self.expected_codes[:included]
        self.excluded = [
            {"asset_code": code, "reason": "missing_trade_date_price"}
            for code in self.expected_codes[included:]
        ]

    @property
    def coverage_ratio(self) -> float:
        return len(self.included_codes) / len(self.expected_codes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_count": len(self.expected_codes),
            "included_count": len(self.included_codes),
            "coverage_ratio": self.coverage_ratio,
        }


@pytest.mark.asyncio
async def test_daily_short_research_data_job_syncs_funds_and_etfs(monkeypatch) -> None:
    calls: list[str] = []
    ranges: dict[str, tuple[date, date]] = {}

    async def fake_sync_short_research_data(_session: object, **kwargs: Any) -> dict[str, Any]:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        ranges[asset_type] = (kwargs["from_date"], kwargs["to_date"])
        return {"asset_count": 1, "failed": 0, "asset_type": asset_type}

    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)

    result = await jobs_module.daily_short_research_data_job(object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert ranges[ASSET_TYPE_FUND] == (date.today() - timedelta(days=120), date.today())
    assert ranges[ASSET_TYPE_ETF] == (date.today(), date.today())
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
            "authoritative": True,
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
async def test_daily_short_research_signals_job_never_generates_legacy_etf_run(monkeypatch) -> None:
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

    assert calls == [ASSET_TYPE_FUND]
    assert result["asset_types"] == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["items"] == 3
    assert result["fund"]["funds"] == 3
    assert result["etf"]["status"] == "delegated_to_canonical_v3"
    assert result["etf"]["etfs"] == 0


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
async def test_daily_advisor_job_keeps_fund_result_when_canonical_etf_is_waiting(monkeypatch) -> None:
    async def fake_run_advisor_generation(
        _session: object,
        _settings: object,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if kwargs["asset_type"] == ASSET_TYPE_ETF:
            raise ValueError("当前 canonical ETF 排名快照不可用（waiting）。")
        return {
            "selected": 1,
            "succeeded": 1,
            "failed": 0,
            "asset_type": ASSET_TYPE_FUND,
        }

    monkeypatch.setattr(jobs_module, "run_advisor_generation", fake_run_advisor_generation)

    result = await jobs_module.daily_short_research_advisor_job(
        object(),
        settings=object(),
    )  # type: ignore[arg-type]

    assert result["selected"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["etf"] == {
        "asset_type": ASSET_TYPE_ETF,
        "status": "waiting",
        "selected": 0,
        "succeeded": 0,
        "failed": 0,
        "reason": "canonical_etf_snapshot_unavailable",
    }


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
async def test_etf_history_backfill_job_uses_bounded_continuation(monkeypatch) -> None:
    calls: list[Any] = []
    eligible_codes = tuple(f"5100{index:02d}" for index in range(8))

    async def fake_run(_session: object, *, request: Any) -> SimpleNamespace:
        calls.append(request)
        return SimpleNamespace(
            status="partial",
            stop_reason="continuation_required",
            attempted_codes=("510000", "510001"),
            completed_codes=("510000",),
            exclusions=(("510001", "provider_timeout"),),
            fetched_rows=500,
            persisted_rows=480,
            inserted_rows=400,
            updated_rows=80,
            unchanged_rows=20,
            excluded_rows=0,
            max_page_rows=500,
            elapsed_seconds=44.0,
            peak_rss_bytes=128 * 1024 * 1024,
            sql_statements=8,
            max_page_sql_statements=3,
            retries=0,
            last_durable_checkpoint={"active_code": "510001"},
        )

    async def fake_coverage(_session: object) -> dict[str, Any]:
        return {
            "rows": 1200,
            "etfs": 8,
            "earliest_trade_date": "2024-06-01",
            "latest_trade_date": "2026-06-26",
        }

    async def fake_readiness(_session: object, **_kwargs: Any) -> dict[str, Any]:
        return {
            "universe": {
                "codes": list(eligible_codes),
                "snapshot_hash": "d" * 64,
            },
            "daily_freshness": {"coverage_ratio": 1.0},
            "history_depth_61": {"coverage_ratio": 1.0},
        }

    monkeypatch.setattr(jobs_module, "run_bounded_history_sync_slice", fake_run)
    monkeypatch.setattr(jobs_module, "_etf_price_history_coverage", fake_coverage)
    monkeypatch.setattr(jobs_module, "read_etf_history_readiness", fake_readiness)

    result = await jobs_module.etf_history_backfill_job(object(), days=730)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert calls[0].max_codes == 10
    assert calls[0].page_size == 500
    assert calls[0].max_rows == 5_000
    assert calls[0].required_sessions == 300
    assert calls[0].process_deadline_seconds == 60
    assert calls[0].eligible_codes == eligible_codes
    assert calls[0].universe_hash == "d" * 64
    assert result["days"] == 730
    assert result["asset_count"] == 8
    assert result["coverage"]["etfs"] == 8
    assert result["etf"]["status"] == "partial"
    assert result["source"] == "history_provider_bounded_continuation"
    assert result["lane_scope"].startswith("history_depth_required:")


@pytest.mark.asyncio
async def test_etf_history_backfill_prioritizes_61_session_warmup(monkeypatch) -> None:
    calls: list[Any] = []
    eligible_codes = ("510001", "510002")

    async def fake_readiness(_session: object, **_kwargs: Any) -> dict[str, Any]:
        return {
            "universe": {
                "codes": list(eligible_codes),
                "snapshot_hash": "e" * 64,
            },
            "daily_freshness": {"coverage_ratio": 1.0},
            "history_depth_61": {"coverage_ratio": 0.5},
        }

    async def fake_run(_session: object, *, request: Any) -> SimpleNamespace:
        calls.append(request)
        return SimpleNamespace(
            status="partial",
            stop_reason="continuation_required",
            attempted_codes=("510001",),
            completed_codes=("510001",),
            exclusions=(),
            fetched_rows=61,
            persisted_rows=61,
            inserted_rows=61,
            updated_rows=0,
            unchanged_rows=0,
            excluded_rows=0,
            max_page_rows=61,
            elapsed_seconds=1.0,
            peak_rss_bytes=64 * 1024 * 1024,
            sql_statements=6,
            max_page_sql_statements=3,
            retries=0,
            last_durable_checkpoint={"active_code": "510001"},
        )

    async def fake_coverage(_session: object) -> dict[str, Any]:
        return {"rows": 61, "etfs": 1}

    monkeypatch.setattr(jobs_module, "read_etf_history_readiness", fake_readiness)
    monkeypatch.setattr(jobs_module, "run_bounded_history_sync_slice", fake_run)
    monkeypatch.setattr(jobs_module, "_etf_price_history_coverage", fake_coverage)

    result = await jobs_module.etf_history_backfill_job(object(), days=730)  # type: ignore[arg-type]

    assert calls[0].scope == "history_depth_61"
    assert calls[0].required_sessions == 61
    assert calls[0].eligible_codes == eligible_codes
    assert calls[0].universe_hash == "e" * 64
    assert result["lane_scope"] == "history_depth_61"


@pytest.mark.asyncio
async def test_etf_history_backfill_yields_to_daily_freshness(monkeypatch) -> None:
    called = False
    eligible_codes = ("510001", "510002")

    async def fake_readiness(_session: object, **_kwargs: Any) -> dict[str, Any]:
        return {
            "universe": {
                "codes": list(eligible_codes),
                "snapshot_hash": "f" * 64,
            },
            "daily_freshness": {"coverage_ratio": 0.5},
            "history_depth_61": {"coverage_ratio": 0.0},
        }

    async def fake_run(_session: object, *, request: Any) -> SimpleNamespace:
        nonlocal called
        called = True
        raise AssertionError(f"history provider should not run: {request}")

    async def fake_coverage(_session: object) -> dict[str, Any]:
        return {"rows": 1, "etfs": 1}

    monkeypatch.setattr(jobs_module, "read_etf_history_readiness", fake_readiness)
    monkeypatch.setattr(jobs_module, "run_bounded_history_sync_slice", fake_run)
    monkeypatch.setattr(jobs_module, "_etf_price_history_coverage", fake_coverage)

    result = await jobs_module.etf_history_backfill_job(object(), days=730)  # type: ignore[arg-type]

    assert called is False
    assert result["etf"]["status"] == "skipped"
    assert result["etf"]["stop_reason"] == "daily_freshness_below_publication_priority"


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
async def test_post_close_etf_signals_job_materializes_and_publishes_without_sync(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_generate_and_publish(_session: object, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            id=7,
            status="success",
            as_of_date=date(2026, 6, 25),
            publication_state="published",
            summary_json={
                "item_count": 9,
                "fund_count": 0,
                "etf_count": 9,
                "conclusion_counts": {"短线观察": 3},
            },
        )

    async def fail_legacy_generation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("post-close ETF job must not call the legacy writer")

    async def fake_authoritative(*_args: Any, **_kwargs: Any) -> tuple[bool, str | None]:
        return True, None

    async def fake_selection(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(state="waiting", run=None)

    async def fake_barrier(*_args: Any, **_kwargs: Any) -> _CoverageBarrier:
        return _CoverageBarrier(2)

    monkeypatch.setattr(jobs_module, "generate_and_publish_etf_snapshot", fake_generate_and_publish)
    monkeypatch.setattr(jobs_module, "run_signal_generation", fail_legacy_generation)
    monkeypatch.setattr(jobs_module, "_latest_authoritative_etf_universe_refresh", fake_authoritative)
    monkeypatch.setattr(jobs_module, "resolve_current_canonical_etf_snapshot", fake_selection)
    monkeypatch.setattr(jobs_module, "build_etf_coverage_barrier", fake_barrier)
    monkeypatch.setattr(
        jobs_module,
        "etf_source_availability_cutoff",
        lambda _trade_date: datetime(2026, 6, 25, 15, 5),
    )
    monkeypatch.setattr(
        jobs_module,
        "post_close_etf_decision_context",
        lambda: (date(2026, 6, 25), datetime(2026, 6, 25, 15, 0)),
    )

    result = await jobs_module.post_close_etf_signals_job(object())  # type: ignore[arg-type]

    assert calls == [
        {
            "trade_date": date(2026, 6, 25),
            "decision_cutoff": datetime(2026, 6, 25, 15, 0),
            "source_availability_cutoff": datetime(2026, 6, 25, 15, 5),
        }
    ]
    assert result["asset_type"] == ASSET_TYPE_ETF
    assert result["run_id"] == 7
    assert result["items"] == 9
    assert result["etfs"] == 9
    assert result["publication_state"] == "published"


@pytest.mark.asyncio
async def test_post_close_etf_signals_job_waits_without_authoritative_universe(monkeypatch) -> None:
    async def not_authoritative(*_args: Any, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, "proxy connection refused"

    async def unexpected_generation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("stale universe must not publish a full snapshot")

    monkeypatch.setattr(jobs_module, "_latest_authoritative_etf_universe_refresh", not_authoritative)
    monkeypatch.setattr(jobs_module, "generate_and_publish_etf_snapshot", unexpected_generation)
    monkeypatch.setattr(
        jobs_module,
        "post_close_etf_decision_context",
        lambda: (date(2026, 6, 25), datetime(2026, 6, 25, 15, 0)),
    )

    result = await jobs_module.post_close_etf_signals_job(object())  # type: ignore[arg-type]

    assert result["status"] == "waiting"
    assert result["publication_state"] == "not_run"
    assert result["reason"] == "universe_not_authoritative"


@pytest.mark.asyncio
async def test_adjusted_sync_job_resumes_one_bounded_batch_then_publishes_at_coverage_gate(monkeypatch) -> None:
    coverage_calls = 0
    sync_calls: list[dict[str, Any]] = []
    publish_calls: list[dict[str, Any]] = []

    async def fake_authoritative(*_args: Any, **_kwargs: Any) -> tuple[bool, str | None]:
        return True, None

    async def fake_selection(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(state="waiting", run=None)

    async def fake_barrier(*_args: Any, **_kwargs: Any) -> _CoverageBarrier:
        nonlocal coverage_calls
        coverage_calls += 1
        return _CoverageBarrier(0 if coverage_calls == 1 else 2)

    async def fake_sync(_session: object, **kwargs: Any) -> dict[str, Any]:
        sync_calls.append(kwargs)
        return {"asset_count": 2, "failed": 0, "etfs": {"processed": 2, "skipped": 0}}

    async def fake_publish(_session: object, **kwargs: Any) -> SimpleNamespace:
        publish_calls.append(kwargs)
        return SimpleNamespace(
            id=88,
            status="success",
            as_of_date=date(2026, 6, 25),
            publication_state="published",
            summary_json={"item_count": 2, "fund_count": 0, "etf_count": 2},
        )

    monkeypatch.setattr(jobs_module, "_latest_authoritative_etf_universe_refresh", fake_authoritative)
    monkeypatch.setattr(jobs_module, "resolve_current_canonical_etf_snapshot", fake_selection)
    monkeypatch.setattr(jobs_module, "build_etf_coverage_barrier", fake_barrier)
    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync)
    monkeypatch.setattr(jobs_module, "generate_and_publish_etf_snapshot", fake_publish)
    monkeypatch.setattr(
        jobs_module,
        "post_close_etf_decision_context",
        lambda: (date(2026, 6, 25), datetime(2026, 6, 25, 15, 0)),
    )
    monkeypatch.setattr(
        jobs_module,
        "etf_source_availability_cutoff",
        lambda _trade_date: datetime(2026, 6, 25, 21, 5),
    )

    result = await jobs_module.post_close_etf_adjusted_sync_job(object())  # type: ignore[arg-type]

    assert sync_calls == [
        {
            "from_date": date(2026, 6, 25),
            "to_date": date(2026, 6, 25),
            "asset_type": ASSET_TYPE_ETF,
        }
    ]
    assert publish_calls == [
        {
            "trade_date": date(2026, 6, 25),
            "decision_cutoff": datetime(2026, 6, 25, 15, 0),
            "source_availability_cutoff": datetime(2026, 6, 25, 21, 5),
        }
    ]
    assert result["publication_state"] == "published"
    assert result["coverage"]["coverage_ratio"] == 1.0


def test_post_close_etf_decision_context_requires_a_completed_trading_session() -> None:
    assert jobs_module.post_close_etf_decision_context(datetime(2026, 6, 25, 14, 59)) is None
    assert jobs_module.post_close_etf_decision_context(datetime(2026, 6, 20, 16, 0)) is None
    assert jobs_module.post_close_etf_decision_context(datetime(2026, 6, 19, 16, 0)) is None
    assert jobs_module.post_close_etf_decision_context(datetime(2027, 7, 15, 16, 0)) is None
    assert jobs_module.post_close_etf_decision_context(datetime(2026, 6, 25, 15, 0)) == (
        date(2026, 6, 25),
        datetime(2026, 6, 25, 15, 0),
    )


@pytest.mark.asyncio
async def test_post_close_etf_signals_job_skips_without_a_completed_trading_session(monkeypatch) -> None:
    async def unexpected_generation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("an incomplete exchange session must not generate a snapshot")

    monkeypatch.setattr(jobs_module, "generate_and_publish_etf_snapshot", unexpected_generation)
    monkeypatch.setattr(jobs_module, "post_close_etf_decision_context", lambda: None)

    result = await jobs_module.post_close_etf_signals_job(object())  # type: ignore[arg-type]

    assert result == {
        "asset_type": ASSET_TYPE_ETF,
        "status": "skipped",
        "publication_state": "not_run",
        "reason": "no_completed_trading_session",
    }


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
