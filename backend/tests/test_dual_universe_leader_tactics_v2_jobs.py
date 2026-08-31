from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.models.entities import JobRun
from app.services.strategy_lab.etf_point_in_time_decision_data import EtfDecisionDataSnapshot
from app.services.workflows import dual_universe_leader_tactics_v2_jobs as jobs
from app.services.workflows.dual_universe_leader_tactics_v2 import AshareReadinessReport


@pytest.fixture(autouse=True)
def _seasoned_etf_cohort(monkeypatch):
    async def readiness(*_args, **kwargs):
        assert kwargs["horizons"] == (1,)
        return {"contract_depth": {"cohort_codes": ["510001"]}}

    monkeypatch.setattr(jobs, "read_etf_history_readiness", readiness)


def _settings(*, enabled: bool = True, etf_enabled: bool = False) -> Settings:
    settings = Settings(_env_file=None)
    settings.etf_leader_tactics_v2_theme_graph_enabled = False
    settings.etf_leader_tactics_v2_materialize_enabled = enabled
    settings.etf_leader_tactics_v2_etf_materialize_enabled = etf_enabled
    return settings


def _readiness(*, count: int = 100, eligible: int = 95) -> AshareReadinessReport:
    return AshareReadinessReport(
        as_of=datetime(2026, 8, 5, 1, 10),
        universe_count=count,
        adjusted_daily_count=eligible,
        pit_theme_count=eligible,
        history_counts=((61, eligible), (120, eligible), (180, eligible), (300, 10)),
        provider_health=(("eastmoney", eligible * 180),),
        raw_decision_violations=0,
        non_finite_violations=0,
        exclusions=(("none", count),),
    )


def _etf_decision_snapshot() -> EtfDecisionDataSnapshot:
    return EtfDecisionDataSnapshot(
        snapshot_id=7,
        trade_date=date(2026, 8, 4),
        decision_cutoff=datetime.fromisoformat("2026-08-04T23:00:00+08:00"),
        daily_coverage_ratio=0.95,
        warmup_coverage_ratio=0.90,
        readiness_policy_version="etf_readiness_policy_v3",
        source_snapshot_hash="s" * 64,
        universe_manifest_hash="u" * 64,
        input_snapshot_hash="i" * 64,
        provider_health_hash="p" * 64,
        provider_health=(("eastmoney", "healthy"),),
    )


def test_capture_signal_date_continues_last_closed_session_without_backdating() -> None:
    assert jobs._capture_signal_date(datetime(2026, 8, 7, 14, 59)) is None
    assert jobs._capture_signal_date(datetime(2026, 8, 7, 15, 0)) == date(2026, 8, 7)
    assert jobs._capture_signal_date(datetime(2026, 8, 8, 0, 1)) == date(2026, 8, 7)


@pytest.mark.asyncio
async def test_materialization_preserves_degraded_capture_provider_health(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            JobRun(
                job_name=jobs.V2_CAPTURE_JOB_NAME,
                status="partial",
                started_at=datetime(2026, 8, 7, 15, 30),
                finished_at=datetime(2026, 8, 7, 15, 31),
                details_json={
                    "signal_date": "2026-08-07",
                    "provider_health": {
                        "provider": "tickflow",
                        "status": "degraded",
                    },
                },
            )
        )
        await session.commit()
        health = await jobs._latest_capture_provider_health(
            session,
            signal_date=date(2026, 8, 7),
            as_of=datetime(2026, 8, 8, 9, 12),
        )

    assert health == (
        (jobs.FINE_THEME_PROVIDER, "unavailable"),
        ("tickflow", "degraded"),
    )


def test_failed_symbol_retry_is_deferred_after_zero_progress() -> None:
    batch = SimpleNamespace(
        failed=(("920427", "adjusted_open_non_finite"),),
        checkpoint=SimpleNamespace(completed_codes=("000001",)),
    )

    assert jobs._should_defer_failed_retry(batch=batch, completed_before=1) is True
    assert jobs._should_defer_failed_retry(batch=batch, completed_before=0) is False


@pytest.mark.asyncio
async def test_fine_theme_capture_is_default_off_without_database_work() -> None:
    settings = _settings(enabled=False)
    settings.etf_leader_tactics_v2_capture_enabled = False

    result = await jobs.dual_universe_leader_tactics_v2_fine_theme_capture_job(
        object(),  # type: ignore[arg-type]
        settings,
    )

    assert result == {
        "status": "skipped",
        "reason": "leader_tactics_v2_capture_disabled",
        "research_only": True,
    }


@pytest.mark.asyncio
async def test_theme_graph_only_flag_runs_bounded_hierarchy_before_provider_concepts(
    monkeypatch,
) -> None:
    settings = _settings(enabled=False)
    settings.etf_leader_tactics_v2_capture_enabled = False
    settings.etf_leader_tactics_v2_theme_graph_enabled = True
    settings.etf_leader_tactics_v2_code_version = "test-v2"
    captured: dict[str, object] = {}

    async def capture_graph(_session, *, signal_date, received_at):
        captured["signal_date"] = signal_date
        captured["received_at"] = received_at
        return {
            "status": "partial",
            "job_status": "partial",
            "next_cursor": 200,
            "research_only": True,
        }

    async def unexpected_concept(*_args, **_kwargs):
        raise AssertionError("provider concepts must wait for a complete hierarchy snapshot")

    monkeypatch.setattr(jobs, "_capture_theme_graph_page", capture_graph)
    monkeypatch.setattr(
        jobs,
        "load_fine_theme_facts_for_registered_theme",
        unexpected_concept,
    )
    result = await jobs.dual_universe_leader_tactics_v2_fine_theme_capture_job(
        object(),  # type: ignore[arg-type]
        settings,
        now=datetime(2026, 8, 7, 20, 30),
        timeout_seconds=10.0,
    )

    assert result["status"] == "partial"
    assert result["next_cursor"] == 200
    assert captured["signal_date"] == date(2026, 8, 7)


@pytest.mark.asyncio
async def test_fine_theme_capture_uses_durable_checkpoint_path(monkeypatch) -> None:
    settings = _settings(enabled=False)
    settings.etf_leader_tactics_v2_capture_enabled = True
    settings.etf_leader_tactics_v2_code_version = "test-v2"
    captured: dict[str, object] = {}

    async def load_checkpoint(*_args, **_kwargs):
        return None

    async def load_facts(provider_label: str, *, received_at: datetime):
        captured.setdefault("provider_labels", []).append(provider_label)
        captured["received_at"] = received_at
        return (SimpleNamespace(fact_hash="f" * 64),)

    async def persist(_session, facts):
        captured.setdefault("persisted", []).extend(fact.fact_hash for fact in facts)
        return len(facts)

    async def run_batch(
        _session,
        *,
        manifest_hash,
        codes,
        checkpoint,
        fetch_one,
        persist_completed,
        expected_contract,
        checkpoint_contract,
        **_kwargs,
    ):
        captured["manifest_hash"] = manifest_hash
        captured["codes"] = codes
        captured["initial_status"] = checkpoint.status
        content_hashes = []
        for code in codes:
            content_hashes.append(await fetch_one(code))
            await persist_completed(code)
        captured["content_hashes"] = tuple(content_hashes)
        assert expected_contract == checkpoint_contract
        return SimpleNamespace(
            checkpoint=SimpleNamespace(
                status="complete",
                completed_codes=codes,
                failed_codes=(),
                error_summary=None,
            ),
            stopped_reason="page_complete",
        )

    monkeypatch.setattr(jobs, "load_v2_checkpoint", load_checkpoint)
    monkeypatch.setattr(jobs, "load_fine_theme_facts_for_registered_theme", load_facts)
    monkeypatch.setattr(jobs, "persist_fine_theme_membership_batch", persist)
    monkeypatch.setattr(jobs, "run_v2_capture_batch", run_batch)

    result = await jobs.dual_universe_leader_tactics_v2_fine_theme_capture_job(
        object(),  # type: ignore[arg-type]
        settings,
        now=datetime(2026, 8, 7, 20, 30),
        timeout_seconds=10.0,
    )

    assert result["status"] == "complete"
    assert result["completed_source_count"] == 6
    assert result["provider_health"]["status"] == "healthy"
    assert captured["provider_labels"] == [
        "创新药",
        "稀土",
        "稀土永磁",
        "被动元件概念",
        "MLCC",
        "液冷服务器",
    ]
    assert captured["codes"] == (
        "创新药",
        "稀土",
        "稀土永磁",
        "被动元件概念",
        "MLCC",
        "液冷服务器",
    )
    assert captured["initial_status"] == "paused"
    assert captured["persisted"] == ["f" * 64] * 6
    assert all(isinstance(item, str) for item in captured["content_hashes"])


@pytest.mark.asyncio
async def test_provider_concept_snapshot_is_sealed_as_an_independent_graph_source(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def persist_relations(_session, facts):
        captured["relations"] = facts
        return len(facts)

    async def persist_runs(_session, facts):
        captured["runs"] = facts
        return len(facts)

    monkeypatch.setattr(jobs, "persist_theme_relation_batch", persist_relations)
    monkeypatch.setattr(jobs, "persist_theme_capture_run_batch", persist_runs)
    facts = (
        SimpleNamespace(
            asset_code="600111",
            taxonomy_version="eastmoney.concept.current_v1",
            source=jobs.FINE_THEME_PROVIDER,
            fact_hash="a" * 64,
        ),
        SimpleNamespace(
            asset_code="000831",
            taxonomy_version="eastmoney.concept.current_v1",
            source=jobs.FINE_THEME_PROVIDER,
            fact_hash="b" * 64,
        ),
    )

    await jobs._persist_provider_concept_graph_snapshot(
        object(),  # type: ignore[arg-type]
        provider_label="稀土永磁",
        facts=facts,
        signal_date=date(2026, 8, 19),
        received_at=datetime(2026, 8, 19, 8),
    )

    relations = captured["relations"]
    runs = captured["runs"]
    assert len(relations) == 2  # type: ignore[arg-type]
    assert all(item.relation_kind.value == "provider_concept" for item in relations)  # type: ignore[union-attr]
    assert all(item.provider_theme_label == "稀土永磁" for item in relations)  # type: ignore[union-attr]
    assert len(runs) == 1  # type: ignore[arg-type]
    assert runs[0].status.value == "complete"  # type: ignore[index,union-attr]
    assert runs[0].expected_count == 2  # type: ignore[index,union-attr]


@pytest.mark.asyncio
async def test_materialization_job_is_default_off_without_database_work(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "_latest_visible_ashare_signal_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("database read")),
    )

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(enabled=False),
    )

    assert result["status"] == "skipped"
    assert result["research_only"] is True


@pytest.mark.asyncio
async def test_materialization_gate_stops_before_full_cross_section(monkeypatch) -> None:
    async def signal_date(*_args, **_kwargs):
        return date(2026, 8, 4)

    async def readiness(*_args, **_kwargs):
        return _readiness(eligible=89)

    async def unexpected_assets(*_args, **_kwargs):
        raise AssertionError("full cross-section must not load below readiness")

    monkeypatch.setattr(jobs, "_latest_visible_ashare_signal_date", signal_date)
    monkeypatch.setattr(jobs, "read_ashare_readiness", readiness)
    monkeypatch.setattr(jobs, "read_ashare_authoritative_assets", unexpected_assets)

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["status"] == "waiting"
    assert result["unavailable_reason"] == "insufficient_adjusted_daily_coverage"


@pytest.mark.asyncio
async def test_materialization_memory_gate_stops_before_full_cross_section(monkeypatch) -> None:
    async def signal_date(*_args, **_kwargs):
        return date(2026, 8, 4)

    async def readiness(*_args, **_kwargs):
        return _readiness()

    async def unexpected_assets(*_args, **_kwargs):
        raise AssertionError("full cross-section must not load without headroom")

    monkeypatch.setattr(jobs, "_latest_visible_ashare_signal_date", signal_date)
    monkeypatch.setattr(jobs, "read_ashare_readiness", readiness)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 100)
    monkeypatch.setattr(jobs, "read_ashare_authoritative_assets", unexpected_assets)

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["unavailable_reason"] == "insufficient_materialization_memory_headroom"


@pytest.mark.asyncio
async def test_ready_materialization_reads_persisted_facts_and_writes_one_manifest(
    monkeypatch,
) -> None:
    assets = tuple((f"{index:06d}", f"asset-{index}") for index in range(100))
    inputs = tuple(object() for _ in assets)
    observations = (SimpleNamespace(asset_code="000001"),)
    screen_result = SimpleNamespace(
        observations=observations,
        qualifying=observations,
    )
    calls: list[str] = []
    captured: dict[str, object] = {}

    async def signal_date(*_args, **_kwargs):
        return date(2026, 8, 4)

    async def readiness(*_args, **kwargs):
        captured["membership_date"] = kwargs.get("membership_date")
        return _readiness()

    async def read_assets(*_args, **_kwargs):
        calls.append("assets")
        return assets

    async def read_inputs(*_args, **kwargs):
        calls.append("inputs")
        captured["decision_mode"] = kwargs.get("decision_mode")
        captured["membership_evaluation_date"] = kwargs.get("membership_evaluation_date")
        captured["next_eligible_date"] = kwargs.get("next_eligible_date")
        return inputs

    async def provider_health(*_args, **_kwargs):
        return (("eastmoney", "degraded"),)

    def screen(*_args, **kwargs):
        calls.append("screen")
        captured["provider_health"] = kwargs.get("provider_health")
        return screen_result

    async def persist(*_args, **_kwargs):
        calls.append("persist")
        return "m" * 64

    monkeypatch.setattr(jobs, "_latest_visible_ashare_signal_date", signal_date)
    monkeypatch.setattr(jobs, "read_ashare_readiness", readiness)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_ashare_authoritative_assets", read_assets)
    monkeypatch.setattr(jobs, "read_ashare_asset_inputs", read_inputs)
    monkeypatch.setattr(jobs, "_latest_capture_provider_health", provider_health)
    monkeypatch.setattr(jobs, "screen_dual_universe", screen)
    monkeypatch.setattr(jobs, "materialize_v2_result", persist)

    result = await jobs.dual_universe_leader_tactics_v2_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert calls == ["assets", "inputs", "screen", "persist"]
    assert result["status"] == "materialized"
    assert result["candidate_codes"] == ["000001"]
    assert result["decision_mode"] == "post_close_watchlist"
    assert result["decision_date"] == "2026-08-05"
    assert result["next_eligible_date"] == "2026-08-06"
    assert captured == {
        "membership_date": date(2026, 8, 5),
        "decision_mode": "post_close_watchlist",
        "membership_evaluation_date": date(2026, 8, 5),
        "next_eligible_date": date(2026, 8, 6),
        "provider_health": (("eastmoney", "degraded"),),
    }
    assert result["notification_provenance"] == "none"
    assert result["execution_provenance"] == "none"


@pytest.mark.asyncio
async def test_etf_materialization_is_default_off_without_database_work(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "latest_ready_etf_decision_data_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("database read")),
    )

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(etf_enabled=False),
    )

    assert result["status"] == "skipped"
    assert result["research_only"] is True


@pytest.mark.asyncio
async def test_etf_materialization_rejects_stale_ready_snapshot_before_input_load(
    monkeypatch,
) -> None:
    snapshot = _etf_decision_snapshot()

    async def latest(*_args, **_kwargs):
        return snapshot

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("stale ETF snapshot must stop before materialization reads")

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", unexpected)
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", unexpected)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(etf_enabled=True),
        now=datetime(2026, 8, 6, 9, 10),
    )

    assert result["status"] == "waiting"
    assert result["unavailable_reason"] == "etf_decision_data_snapshot_stale"
    assert result["signal_date"] == "2026-08-04"
    assert result["required_trade_date"] == "2026-08-05"


@pytest.mark.asyncio
async def test_etf_materialization_reads_persisted_pit_inputs_and_writes_manifest(
    monkeypatch,
) -> None:
    snapshot = _etf_decision_snapshot()
    inputs = tuple(object() for _ in range(10))
    bundle = SimpleNamespace(
        inputs=inputs,
        universe_count=10,
        adjusted_120_count=9,
        adjusted_180_count=8,
        pit_group_count=9,
        provider_health=(("eastmoney", "healthy"),),
        raw_decision_violations=0,
        non_finite_violations=0,
        readiness_dict=lambda *, threshold: {"threshold": threshold},
    )
    observations = (SimpleNamespace(asset_code="510001"),)
    screen_result = SimpleNamespace(
        observations=observations,
        qualifying=observations,
    )
    calls: list[str] = []

    async def latest(*_args, **_kwargs):
        calls.append("source")
        return snapshot

    async def existing(*_args, **kwargs):
        calls.append("existing")
        assert kwargs["as_of"] == datetime(2026, 8, 5, 1, 10)
        return None

    async def read_inputs(*_args, **kwargs):
        calls.append("inputs")
        assert kwargs["decision_cutoff"] == snapshot.decision_cutoff
        assert kwargs["identity_cutoff"] == datetime.fromisoformat(
            "2026-08-05T09:10:00+08:00"
        )
        assert kwargs["eligible_codes"] == ("510001",)
        return bundle

    def screen(*_args, **_kwargs):
        calls.append("screen")
        return screen_result

    async def persist(*_args, **_kwargs):
        calls.append("persist")
        return "e" * 64

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", existing)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", read_inputs)
    monkeypatch.setattr(jobs, "screen_dual_universe", screen)
    monkeypatch.setattr(jobs, "materialize_v2_result", persist)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(etf_enabled=True),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert calls == ["source", "existing", "inputs", "screen", "persist"]
    assert result["status"] == "materialized"
    assert result["candidate_codes"] == ["510001"]
    assert result["readiness"]["provider_health"] == {"eastmoney": "healthy"}
    assert result["decision_data_snapshot_id"] == 7
    assert (
        result["readiness"]["decision_data_snapshot"]["provenance_kind"]
        == "persisted_etf_pit_decision_data"
    )
    assert "source_signal_run_id" not in result["readiness"]["decision_data_snapshot"]
    assert result["notification_provenance"] == "none"
    assert result["execution_provenance"] == "none"


@pytest.mark.asyncio
async def test_etf_materialization_fails_closed_without_seasoned_cohort(monkeypatch) -> None:
    async def latest(*_args, **_kwargs):
        return _etf_decision_snapshot()

    async def existing(*_args, **_kwargs):
        return None

    async def empty_readiness(*_args, **_kwargs):
        return {"contract_depth": {"cohort_codes": []}}

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("inputs must not load without a seasoned cohort")

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", existing)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_etf_history_readiness", empty_readiness)
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", unexpected)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(etf_enabled=True),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["unavailable_reason"] == "etf_seasoned_history_cohort_unavailable"


@pytest.mark.asyncio
async def test_etf_materialization_keeps_a_bounded_container_headroom_gate(
    monkeypatch,
) -> None:
    snapshot = _etf_decision_snapshot()

    async def latest(*_args, **_kwargs):
        return snapshot

    async def existing(*_args, **_kwargs):
        return None

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("ETF inputs must not load below the ETF headroom gate")

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", existing)
    monkeypatch.setattr(
        jobs,
        "available_memory_bytes",
        lambda: jobs.V2_ETF_MIN_MATERIALIZATION_HEADROOM_BYTES - 1,
    )
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", unexpected)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(etf_enabled=True),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["unavailable_reason"] == "insufficient_materialization_memory_headroom"
    assert result["required_memory_bytes"] == 640 * 1024 * 1024


@pytest.mark.asyncio
async def test_etf_materialization_history_gate_stops_before_screen(monkeypatch) -> None:
    snapshot = _etf_decision_snapshot()
    bundle = SimpleNamespace(
        inputs=tuple(object() for _ in range(10)),
        universe_count=10,
        adjusted_120_count=8,
        adjusted_180_count=8,
        pit_group_count=9,
        provider_health=(("eastmoney", "healthy"),),
        raw_decision_violations=0,
        non_finite_violations=0,
        readiness_dict=lambda *, threshold: {"threshold": threshold},
    )

    async def latest(*_args, **_kwargs):
        return snapshot

    async def existing(*_args, **_kwargs):
        return None

    async def read_inputs(*_args, **_kwargs):
        return bundle

    def unexpected_screen(*_args, **_kwargs):
        raise AssertionError("screen must not run below the frozen history gate")

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", existing)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", read_inputs)
    monkeypatch.setattr(jobs, "screen_dual_universe", unexpected_screen)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(etf_enabled=True),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["status"] == "waiting"
    assert result["unavailable_reason"] == "insufficient_etf_history_120_coverage"


@pytest.mark.asyncio
async def test_etf_materialization_pit_group_gate_stops_before_screen(monkeypatch) -> None:
    snapshot = _etf_decision_snapshot()
    bundle = SimpleNamespace(
        inputs=tuple(object() for _ in range(10)),
        universe_count=10,
        adjusted_120_count=9,
        adjusted_180_count=8,
        pit_group_count=8,
        provider_health=(("eastmoney", "healthy"),),
        raw_decision_violations=0,
        non_finite_violations=0,
        readiness_dict=lambda *, threshold: {"threshold": threshold},
    )

    async def latest(*_args, **_kwargs):
        return snapshot

    async def existing(*_args, **_kwargs):
        return None

    async def read_inputs(*_args, **_kwargs):
        return bundle

    def unexpected_screen(*_args, **_kwargs):
        raise AssertionError("screen must not run below the frozen PIT group gate")

    monkeypatch.setattr(jobs, "latest_ready_etf_decision_data_snapshot", latest)
    monkeypatch.setattr(jobs, "get_v2_materialized_manifest", existing)
    monkeypatch.setattr(jobs, "available_memory_bytes", lambda: 2**30)
    monkeypatch.setattr(jobs, "read_etf_v2_asset_inputs", read_inputs)
    monkeypatch.setattr(jobs, "screen_dual_universe", unexpected_screen)

    result = await jobs.dual_universe_leader_tactics_v2_etf_materialize_job(
        object(),  # type: ignore[arg-type]
        _settings(etf_enabled=True),
        now=datetime(2026, 8, 5, 9, 10),
    )

    assert result["status"] == "waiting"
    assert result["unavailable_reason"] == "insufficient_etf_pit_group_coverage"
