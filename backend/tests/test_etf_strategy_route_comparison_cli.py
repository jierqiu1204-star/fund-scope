from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.daily_reconstructable import (
    AdjustedOhlcvBar,
    AdjustmentProvenance,
)
from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
from app.services.strategy_lab.etf_ranking_forward_outcomes import ForwardAdjustedClose
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    PointInTimeAdjustedSeries,
    PointInTimeEtfMetadata,
)
from app.services.strategy_lab.etf_strategy_route_comparison import (
    BREAKOUT_V2,
    ComparisonValuationInput,
    V2ComparisonEvent,
)
from app.services.strategy_lab.etf_strategy_route_comparison_inputs import (
    ComparisonPreflightReport,
    ComparisonSnapshotAudit,
    ResearchStoreReadiness,
    V2DayEvidence,
)
from scripts import run_etf_strategy_route_comparison as cli
from scripts.run_etf_strategy_route_comparison import (
    COMPARISON_RESULT_PHASE,
    DECISION_CUTOFF_POLICY,
    _arguments,
    _read_valuation_calendar,
    _research_store_can_write,
    _safe_error,
    _write_comparison_artifacts,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_INTEGRATION_START = date(2026, 8, 26)
_INTEGRATION_END = date(2026, 8, 28)
_INTEGRATION_CODES = tuple(f"510{index:03d}" for index in range(10))


def _freeze_exit_research_clock(monkeypatch, hour=18):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 9, 11, hour, 0, tzinfo=_SHANGHAI)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)


@pytest.mark.parametrize(("policy", "day"), [
    ("legacy_ma5", "2026-09-11"),
    ("shared_daily", "2026-09-09"),
    ("shared_daily_2r", "2026-12-14"),
])
async def test_exit_policy_cannot_reopen_another_frozen_window(monkeypatch, policy, day):
    def unexpected_settings():
        raise AssertionError("invalid experiment must be rejected before DB setup")

    monkeypatch.setattr(cli, "get_settings", unexpected_settings)
    args = _arguments(["--start-date", day, "--end-date", day, "--leader-exit-policy", policy])
    with pytest.raises(ValueError):
        await cli._run(args)


async def test_new_exit_research_cannot_read_today_before_its_cutoff(monkeypatch):
    _freeze_exit_research_clock(monkeypatch, hour=18)

    def unexpected_settings():
        raise AssertionError("future cutoff must be rejected before DB setup")

    monkeypatch.setattr(cli, "get_settings", unexpected_settings)
    args = _arguments(["--start-date", "2026-09-11", "--end-date", "2026-09-11", "--leader-exit-policy", "shared_daily"])
    with pytest.raises(ValueError):
        await cli._run(args)


async def test_new_exit_research_accepts_elapsed_prospective_cutoff(monkeypatch):
    _freeze_exit_research_clock(monkeypatch, hour=20)

    def reached_settings():
        raise RuntimeError("valid window reached data setup")

    monkeypatch.setattr(cli, "get_settings", reached_settings)
    args = _arguments(["--start-date", "2026-09-11", "--end-date", "2026-09-11", "--leader-exit-policy", "shared_daily_2r"])
    with pytest.raises(RuntimeError, match="valid window reached data setup"):
        await cli._run(args)


def _fixture_hash(value: object) -> str:
    return stable_contract_hash({"cli-integration-fixture": value})


def test_legacy_cli_config_identity_and_new_policy_scope():
    sessions = _read_valuation_calendar(None, start_date=_INTEGRATION_START, end_date=_INTEGRATION_END)
    report = _integration_report(valuation_sessions=sessions, store_path=Path("/tmp/unused-exit-acceptance.sqlite"))
    args = _arguments(["--start-date", "2026-08-26", "--end-date", "2026-08-28", "--run-id", "legacy-config-acceptance"])
    config = cli._frozen_config(report, arguments=args, calendar_hash=report.calendar_hash)
    # Captured from the original HEAD implementation before adding policy fields.
    assert stable_contract_hash(config) == "6f14e7b6260096fc65001ddd2f23208a2641f188f006afd1d1bc968f99601ef9"
    args.leader_exit_policy = "shared_daily_2r"
    new = cli._frozen_config(report, arguments=args, calendar_hash=report.calendar_hash)
    assert new["experiment_id"] != config["experiment_id"]
    assert new["schema_version"] != config["schema_version"]
    assert dict(new["route_policy_hashes"])["v2_breakout"] != dict(config["route_policy_hashes"])["v2_breakout"]
    assert new["diagnostic_window_guard"]["start_date"] == date(2026, 9, 11)
    assert new["diagnostic_window_guard"]["end_date"] == date(2026, 12, 11)


def _fixture_series(
    code: str,
    signal_date: date,
    history_sessions: tuple[date, ...],
) -> PointInTimeAdjustedSeries:
    bars = tuple(
        AdjustedOhlcvBar(
            session_date=session,
            adjusted_open=100.0 + index,
            adjusted_high=101.0 + index,
            adjusted_low=99.0 + index,
            adjusted_close=100.0 + index,
            volume=1_000.0 + index,
            turnover=100_000.0 + index,
        )
        for index, session in enumerate(history_sessions)
    )
    provenance = AdjustmentProvenance(
        provider="fixture",
        adjustment_version="fixture-v1",
        price_basis="total_return_adjusted",
        transform_kind="constant_multiplicative",
        scale_invariance_proven=True,
    )
    metadata = PointInTimeEtfMetadata(
        asset_code=code,
        membership_source="fixture",
        membership_external_source_id=f"membership:{code}",
        membership_provider_version="fixture-v1",
        membership_evidence_hash=_fixture_hash(("membership-evidence", code, signal_date)),
        membership_raw_payload_hash=_fixture_hash(("membership-raw", code, signal_date)),
        membership_fact_hash=_fixture_hash(("membership-fact", code, signal_date)),
        tracked_underlying_id=f"underlying:{code}",
        membership_known_at=datetime.combine(signal_date, time(18), _SHANGHAI),
        membership_last_modified_at=datetime.combine(signal_date, time(18), _SHANGHAI),
        membership_ingested_at=datetime.combine(signal_date, time(18), _SHANGHAI),
        eligible_from=history_sessions[0],
        eligible_at=signal_date,
    )
    payload = {
        "asset_code": code,
        "metadata": asdict(metadata),
        "bars": [asdict(item) for item in bars],
        "provenance": asdict(provenance),
        "earliest_source_timestamp": datetime.combine(history_sessions[0], time(16), _SHANGHAI),
        "latest_source_timestamp": datetime.combine(signal_date, time(16), _SHANGHAI),
        "revision_hashes": tuple(
            _fixture_hash(("revision", code, session)) for session in history_sessions
        ),
    }
    return PointInTimeAdjustedSeries(
        asset_code=code,
        metadata=metadata,
        bars=bars,
        provenance=provenance,
        earliest_source_timestamp=payload["earliest_source_timestamp"],
        latest_source_timestamp=payload["latest_source_timestamp"],
        synchronized_after_cutoff=False,
        revision_hashes=payload["revision_hashes"],
        series_hash=stable_contract_hash(payload),
    )


def _fixture_snapshot(signal_date: date, valuation_sessions: tuple[date, ...]):
    history_sessions = tuple(
        day for day in valuation_sessions if day <= signal_date
    )[-127:]
    series = tuple(
        _fixture_series(code, signal_date, history_sessions)
        for code in _INTEGRATION_CODES
    )
    adjusted = tuple(
        (
            code,
            tuple(
                (day, 100.0 + index * 0.5 + code_index)
                for index, day in enumerate(history_sessions)
            ),
        )
        for code_index, code in enumerate(_INTEGRATION_CODES)
    )
    cutoff = datetime.combine(signal_date, time(19), _SHANGHAI)
    return ComparisonSnapshotAudit(
        signal_date=signal_date,
        decision_cutoff=cutoff,
        source_hash=_fixture_hash(("snapshot", signal_date)),
        eligible_asset_codes=_INTEGRATION_CODES,
        nonclone_asset_codes=_INTEGRATION_CODES,
        adjusted_closes=adjusted,
        history_eligible_asset_codes=_INTEGRATION_CODES,
        history_missing_asset_codes=(),
        eligible_denominator=len(_INTEGRATION_CODES),
        nonclone_denominator=len(_INTEGRATION_CODES),
        history_numerator=len(_INTEGRATION_CODES),
        clone_groups=tuple(
            (f"underlying:{code}", (code,)) for code in _INTEGRATION_CODES
        ),
        clone_representatives=tuple(
            (f"underlying:{code}", code) for code in _INTEGRATION_CODES
        ),
        clone_representative_liquidity=tuple(
            (f"underlying:{code}", code, 100_000.0) for code in _INTEGRATION_CODES
        ),
        universe_hash=_fixture_hash(("universe", signal_date)),
        input_hash=_fixture_hash(("input", signal_date)),
        coverage_manifest_hash=_fixture_hash(("coverage", signal_date)),
        history_provenance=tuple((code, series_item.series_hash) for code, series_item in zip(_INTEGRATION_CODES, series, strict=True)),
        history_fact_provenance=tuple(
            (
                code,
                "fixture",
                "fixture-v1",
                series_item.series_hash,
                series_item.revision_hashes,
            )
            for code, series_item in zip(_INTEGRATION_CODES, series, strict=True)
        ),
        pit_series=series,
    )


def _fixture_v2_day(
    signal_date: date,
    event: V2ComparisonEvent | None,
) -> V2DayEvidence:
    key = f"{_INTEGRATION_CODES[0]}:{_INTEGRATION_START.isoformat()}:{BREAKOUT_V2}"
    keys = (key,)
    transition_hashes = (event.source_hash,) if event is not None else ()
    return V2DayEvidence(
        signal_date=signal_date,
        decision_cutoff=datetime.combine(signal_date, time(19), _SHANGHAI),
        available=True,
        reason="fixture_v2_checked",
        manifest_hash=_fixture_hash(("manifest", signal_date)),
        manifest_input_hash=_fixture_hash(("manifest-input", signal_date)),
        required_observation_keys=keys,
        observed_observation_keys=keys,
        observation_count=1,
        screen_observation_count=1,
        lifecycle_checked_observation_count=1,
        observation_digest=_fixture_hash(("observations", signal_date)),
        transition_hashes=transition_hashes,
        transition_count=len(transition_hashes),
        transition_digest=_fixture_hash(("transitions", signal_date, transition_hashes)),
        checked_through_date=signal_date,
        checked_through_cutoff=datetime.combine(signal_date, time(19), _SHANGHAI),
        lifecycle_checked=True,
        checked_observation_keys=keys,
        checked_asset_codes=(_INTEGRATION_CODES[0],),
        held_asset_codes=(_INTEGRATION_CODES[0],),
        materialization_mode="fixture",
        source_fact_hash=_fixture_hash(("facts", signal_date)),
        core_events=(event,) if event is not None else (),
    )


def _integration_report(
    *,
    valuation_sessions: tuple[date, ...],
    store_path,
) -> ComparisonPreflightReport:
    event = V2ComparisonEvent(
        signal_date=_INTEGRATION_START,
        original_signal_date=_INTEGRATION_START,
        asset_code=_INTEGRATION_CODES[0],
        event_type="confirmation",
        score=1.0,
        formula_id=BREAKOUT_V2,
        clone_group=f"underlying:{_INTEGRATION_CODES[0]}",
        source_hash=_fixture_hash(("v2-event", _INTEGRATION_START)),
    )
    snapshots = tuple(
        _fixture_snapshot(day, valuation_sessions)
        for day in (
            _INTEGRATION_START,
            date(2026, 8, 27),
            _INTEGRATION_END,
        )
    )
    v2_days = tuple(
        _fixture_v2_day(day, event if day == _INTEGRATION_START else None)
        for day in (
            _INTEGRATION_START,
            date(2026, 8, 27),
            _INTEGRATION_END,
        )
    )
    return ComparisonPreflightReport(
        start_date=_INTEGRATION_START,
        end_date=_INTEGRATION_END,
        calendar_hash=_fixture_hash(valuation_sessions),
        snapshots=snapshots,
        v2_days=v2_days,
        research_store=ResearchStoreReadiness(
            path=str(store_path),
            exists=True,
            sqlite_readable=True,
            schema_compatible=True,
            nonempty=False,
            requested_artifact_count=0,
            requested_checkpoint_count=0,
            invalid_artifact_count=0,
            reason="empty fixture namespace",
        ),
        ready=True,
        reasons=(),
        elapsed_seconds=0.0,
    )


def _fixture_valuation(sessions: tuple[date, ...]) -> ComparisonValuationInput:
    rows = tuple(
        ForwardAdjustedClose(
            asset_code=code,
            session_date=day,
            adjusted_close=100.0 + index * 0.5 + code_index,
            price_basis="total_return_adjusted",
            decision_eligible=True,
            provider="fixture",
            adjustment_version="fixture-v1",
            source_hash=_fixture_hash(("valuation", code, day)),
        )
        for code_index, code in enumerate(_INTEGRATION_CODES)
        for index, day in enumerate(sessions)
    )
    return ComparisonValuationInput(
        trading_sessions=sessions,
        adjusted_closes=rows,
        calendar_sessions=sessions,
        calendar_complete_through=sessions[-1],
    )


def test_cli_modes_are_explicit_and_cutoff_is_frozen() -> None:
    arguments = _arguments(
        [
            "--compare",
            "--start-date",
            "2026-08-26",
            "--end-date",
            "2026-09-09",
        ]
    )

    assert arguments.mode == "compare"
    assert arguments.start_date == date(2026, 8, 26)
    assert arguments.end_date == date(2026, 9, 9)
    assert DECISION_CUTOFF_POLICY == "19:00 Asia/Shanghai on each requested trading session"


def test_cli_error_redacts_database_url() -> None:
    reason = _safe_error(
        RuntimeError(
            "could not connect to postgresql+asyncpg://fundscope:secret@db:5432/fundscope"
        )
    )

    assert "fundscope:secret" not in reason
    assert "postgresql+asyncpg://" not in reason
    assert "database operation failed" in reason


def test_comparison_artifacts_are_immutable_and_round_trip(tmp_path) -> None:
    path = tmp_path / "research.sqlite3"
    config = {
        "schema_version": "test",
        "start_date": date(2026, 8, 26),
        "end_date": date(2026, 9, 9),
    }
    first = _write_comparison_artifacts(
        path,
        run_id="cli-test",
        phase=COMPARISON_RESULT_PHASE,
        config=config,
        result_payload={"routes": ()},
        max_seconds=2.0,
        include_result=False,
    )
    second = _write_comparison_artifacts(
        path,
        run_id="cli-test",
        phase=COMPARISON_RESULT_PHASE,
        config=config,
        result_payload={
            "input_hash": "a" * 64,
            "result_hash": "b" * 64,
            "common_status": "unavailable",
            "routes": [
                {
                    "route_id": "v2_breakout",
                    "status": "unavailable",
                    "reason": "missing",
                    "required_signal_dates": [],
                    "targets": [],
                    "input_hash": "c" * 64,
                }
            ],
        },
        max_seconds=2.0,
        include_config=False,
    )

    assert first["artifact_count"] == 1
    assert second["artifact_count"] == 3


def test_arguments_default_to_read_only_preflight() -> None:
    arguments = _arguments(
        ["--start-date", "2026-09-07", "--end-date", "2026-09-09"]
    )
    assert arguments.mode == "preflight"


def test_valuation_calendar_includes_common_pool_warmup() -> None:
    sessions = _read_valuation_calendar(
        None,
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 9),
    )
    requested = tuple(day for day in sessions if day >= date(2026, 9, 7))
    assert len(sessions) >= 127
    assert requested == (
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
    )


def test_empty_compatible_store_is_a_valid_first_write_namespace() -> None:
    readiness = ResearchStoreReadiness(
        path="unused.sqlite3",
        exists=True,
        sqlite_readable=True,
        schema_compatible=True,
        nonempty=False,
        requested_artifact_count=0,
        requested_checkpoint_count=0,
        invalid_artifact_count=0,
        reason="empty research namespace",
    )
    assert _research_store_can_write(readiness)


@pytest.mark.asyncio
async def test_cli_compare_wires_pit_daily_core_and_all_ledgers(monkeypatch, tmp_path) -> None:
    valuation_sessions = _read_valuation_calendar(
        None,
        start_date=_INTEGRATION_START,
        end_date=_INTEGRATION_END,
    )
    store_path = tmp_path / "research.sqlite3"
    ReplayArtifactStore(store_path)
    report = _integration_report(
        valuation_sessions=valuation_sessions,
        store_path=store_path,
    )
    valuation = _fixture_valuation(valuation_sessions)
    calls: list[tuple[tuple[str, ...], int]] = []

    async def valuation_loader(_session, *, trading_sessions, asset_codes, page_size, **_kwargs):
        calls.append((tuple(asset_codes), page_size))
        assert tuple(trading_sessions) == valuation_sessions
        return valuation

    class FakeEngine:
        url = SimpleNamespace(get_backend_name=lambda: "sqlite")

        async def dispose(self):
            return None

    class FakeDatabase:
        def __init__(self, _url):
            self.engine = FakeEngine()

        def session(self):
            class Session:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, _exc_type, _exc, _traceback):
                    return False

            return Session()

    async def preflight_loader(_session, **kwargs):
        assert kwargs["run_id"] == arguments.run_id
        assert kwargs["phase"] == arguments.phase
        return report

    captured: dict[str, object] = {}
    original_run_comparison = cli.run_comparison

    def comparison_spy(input_data):
        captured["input"] = input_data
        return original_run_comparison(input_data)

    monkeypatch.setattr(cli, "DatabaseManager", FakeDatabase)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="sqlite+aiosqlite:///fixture.sqlite3",
            etf_pit_artifact_dir=str(tmp_path),
        ),
    )
    monkeypatch.setattr(cli, "preflight_comparison_inputs", preflight_loader)
    monkeypatch.setattr(cli, "load_comparison_valuation_input", valuation_loader)
    monkeypatch.setattr(cli, "run_comparison", comparison_spy)
    arguments = _arguments(
        [
            "--compare",
            "--start-date",
            _INTEGRATION_START.isoformat(),
            "--end-date",
            _INTEGRATION_END.isoformat(),
            "--research-store",
            str(store_path),
        ]
    )

    result = await cli._run(arguments)
    input_data = captured["input"]
    assert result["status"] == "completed"
    assert calls == [(_INTEGRATION_CODES, 16)]
    assert len(input_data.daily_core_targets) == 2
    assert all(len(target.target_weights) == 10 for target in input_data.daily_core_targets)
    routes = result["comparison"]["routes"]
    assert tuple(route["status"] for route in routes) == (
        "completed", "completed", "completed"
    )
    assert result["comparison"]["common_status"] == "complete"
    assert result["config_artifact"]["artifact_count"] == 1
    assert result["artifact_store"]["artifact_count"] == 11
