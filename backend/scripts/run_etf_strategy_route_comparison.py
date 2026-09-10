"""Bounded PIT preflight and research-only ETF route comparison.

The command is deliberately an orchestration layer. It reads the configured
database and existing research store, delegates input construction to the
PIT/data pipeline, and sends an already frozen ``ComparisonInput`` to the
comparison core. It never calls a provider, a production ranking job, or a
holdout evaluator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime, timedelta
from datetime import time as wall_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.services.etf_research_evidence import stable_contract_hash
from app.services.market_data import is_etf_exchange_trading_day
from app.services.short_research.daily_reconstructable import daily_reconstructable_manifest
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ArtifactConflictError,
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    RANKING_PORTFOLIO_BASE_COST_POLICY,
    RANKING_PORTFOLIO_STRESS_COST_POLICY,
)
from app.services.strategy_lab.etf_ranking_stage_b import StageBReplayContract
from app.services.strategy_lab.etf_strategy_route_comparison import (
    COMPARISON_EXPERIMENT_ID,
    COMPARISON_ROUTE_IDS,
    COMPARISON_ROUTE_POLICY_HASHES,
    COMPARISON_SCHEMA_VERSION,
    ComparisonInput,
    ComparisonValuationInput,
    DailyCorePITDateInput,
    FrozenComparisonProvenance,
    canonical_daily_core_targets_from_pit,
    comparison_result_payload,
    run_comparison,
)
from app.services.strategy_lab.etf_strategy_route_comparison_inputs import (
    COMPARISON_MAX_CONTINUATION_SECONDS,
    V2_DAY_CHECK_PHASE,
    ComparisonPreflightReport,
    _normalise_calendar,
    build_comparison_input,
    inspect_research_store,
    load_comparison_valuation_input,
    preflight_comparison_inputs,
    prepare_v2_day_checks,
)

_DECISION_CUTOFF = wall_time(19, 0)
DECISION_CUTOFF_POLICY = "19:00 Asia/Shanghai on each requested trading session"
COMPARISON_RESULT_PHASE = "route-comparison-result"
DEVELOPMENT_DIAGNOSTIC_START = date(2026, 8, 26)
DEVELOPMENT_DIAGNOSTIC_END = date(2026, 9, 9)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PIT_PAGE_SIZE = 16
_DATABASE_URL_PATTERN = re.compile(
    r"\b(?:postgres(?:ql)?(?:\+[^:/\s]+)?|sqlite(?:\+[^:/\s]+)?):/{2}[^\s'\")]+",
    re.IGNORECASE,
)


class ComparisonInputBuilderUnavailableError(RuntimeError):
    """Raised when no data pipeline can build the future comparison input."""


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one bounded, research-only ETF route comparison."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--preflight",
        dest="mode",
        action="store_const",
        const="preflight",
        help="read-only PIT/V2/store preflight (the default)",
    )
    modes.add_argument(
        "--prepare-research",
        dest="mode",
        action="store_const",
        const="prepare-research",
        help="seal compatible V2 day checks in the existing research store",
    )
    modes.add_argument(
        "--compare",
        dest="mode",
        action="store_const",
        const="compare",
        help="build frozen data inputs and run the three research routes",
    )
    parser.set_defaults(mode="preflight")
    parser.add_argument("--start-date", required=True, type=_parse_date)
    parser.add_argument("--end-date", required=True, type=_parse_date)
    parser.add_argument(
        "--calendar-file",
        type=Path,
        help="JSON date list or {trading_sessions: [...]} from the verified exchange calendar",
    )
    parser.add_argument(
        "--research-store",
        type=Path,
        help="existing research SQLite path; defaults to ETF_PIT_ARTIFACT_DIR/production-pit-research.sqlite3",
    )
    parser.add_argument("--run-id", default="etf-strategy-route-comparison")
    parser.add_argument("--phase", default=V2_DAY_CHECK_PHASE)
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=COMPARISON_MAX_CONTINUATION_SECONDS,
        help="one total wall-clock budget, bounded to 55 seconds",
    )
    parser.add_argument("--initial-capital", type=float, default=1.0)
    parser.add_argument(
        "--json-output",
        type=Path,
        help="write the same JSON object to this file; stdout remains JSON only",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        help="write the Chinese Markdown report to this file",
    )
    return parser.parse_args(argv)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _read_calendar(
    path: Path | None,
    *,
    start_date: date,
    end_date: date,
) -> tuple[date, ...]:
    values = _read_calendar_values(path)
    # The exchange calendar, rather than the last available price, decides
    # whether a session exists.
    return _normalise_calendar(
        start_date=start_date,
        end_date=end_date,
        trading_sessions=values,
    )


def _read_calendar_values(path: Path | None) -> tuple[date, ...] | None:
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"calendar file cannot be read: {type(exc).__name__}") from exc
    values: Any = raw.get("trading_sessions") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise ValueError("calendar file must contain a date list")
    try:
        sessions = tuple(date.fromisoformat(str(item)) for item in values)
    except ValueError as exc:
        raise ValueError("calendar file contains an invalid date") from exc
    return sessions


def _read_valuation_calendar(
    path: Path | None,
    *,
    start_date: date,
    end_date: date,
) -> tuple[date, ...]:
    """Return the requested sessions plus enough pre-start history for the core.

    The valuation reader intentionally receives exactly 126 pre-start sessions.
    The comparison ledger clips them back to the requested interval, while
    the common-pool and 126-session momentum gates still see their history.
    """

    values = _read_calendar_values(path)
    requested = _normalise_calendar(
        start_date=start_date,
        end_date=end_date,
        trading_sessions=values,
    )
    if values is not None:
        warmup = tuple(day for day in values if day < start_date)[-126:]
    else:
        preceding: list[date] = []
        candidate = start_date - timedelta(days=1)
        # Walk the verified calendar one day at a time.  The bound only
        # prevents an impossible calendar from spinning forever; it does not
        # request or retain a fixed natural-day warmup window.
        for _ in range(730):
            if is_etf_exchange_trading_day(candidate):
                preceding.append(candidate)
                if len(preceding) == 126:
                    break
            candidate -= timedelta(days=1)
        warmup = tuple(reversed(preceding))
    if len(warmup) < 126:
        raise ValueError("valuation calendar needs 126 verified warmup sessions")
    return (*warmup[-126:], *requested)


def _default_store(settings: Any) -> Path:
    return Path(settings.etf_pit_artifact_dir) / "production-pit-research.sqlite3"


def _research_store_can_write(readiness: Any) -> bool:
    """Allow a compatible empty namespace for the first comparison artifact."""

    return bool(
        readiness.exists
        and readiness.sqlite_readable
        and readiness.schema_compatible
        and readiness.invalid_artifact_count == 0
    )


def _write(path: Path | None, content: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _safe_error(exc: BaseException) -> str:
    """Keep operational errors useful without echoing a configured DB URL."""

    message = _DATABASE_URL_PATTERN.sub("<database-url>", str(exc))
    if "<database-url>" in message:
        return f"{type(exc).__name__}: database operation failed"
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _frozen_provenance(
    report: ComparisonPreflightReport,
    *,
    valuation_calendar_hash: str | None = None,
) -> FrozenComparisonProvenance:
    """Seal the PIT identities before the valuation reader is entered."""

    snapshots = report.snapshots
    return FrozenComparisonProvenance(
        source_mode="factual_pit",
        data_version="persisted_etf_pit_decision_data_v1",
        universe_policy_hash=stable_contract_hash(
            tuple((item.signal_date, item.universe_hash) for item in snapshots)
        ),
        membership_policy_hash=stable_contract_hash(
            tuple((item.signal_date, item.input_hash) for item in snapshots)
        ),
        clone_policy_hash=stable_contract_hash(
            tuple(
                (item.signal_date, item.clone_representatives)
                for item in snapshots
            )
        ),
        adjusted_price_policy_hash=stable_contract_hash(
            tuple(
                (item.signal_date, item.history_fact_provenance)
                for item in snapshots
            )
        ),
        calendar_hash=valuation_calendar_hash or report.calendar_hash,
        source_snapshot_hash=stable_contract_hash(
            tuple(item.source_hash for item in snapshots)
        ),
    )


def _daily_core_targets_from_report(
    report: ComparisonPreflightReport,
    *,
    requested_sessions: tuple[date, ...],
    run_id: str,
) -> tuple[Any, ...]:
    """Score the contiguous PIT prefix through the core's frozen bridge."""

    snapshots_by_date = {item.signal_date: item for item in report.snapshots}
    prefix: list[DailyCorePITDateInput] = []
    for signal_date in requested_sessions:
        snapshot = snapshots_by_date.get(signal_date)
        if snapshot is None or not snapshot.ready_for_signal_filters:
            break
        series = tuple(snapshot.pit_series)
        expected_codes = tuple(snapshot.history_eligible_asset_codes)
        if tuple(item.asset_code for item in series) != expected_codes:
            break
        prefix.append(
            DailyCorePITDateInput(
                replay_date=snapshot.signal_date,
                decision_cutoff=snapshot.decision_cutoff,
                universe_hash=snapshot.universe_hash,
                authoritative_asset_codes=expected_codes,
                series=series,
            )
        )
    if not prefix:
        return ()
    registry = freeze_ranking_candidate_registry((FROZEN_RANKING_CANDIDATES[1],))
    contract = StageBReplayContract(
        replay_run_key=f"{run_id}:daily-core",
        score_contract_id="daily_reconstructable_v1",
        score_manifest_hash=daily_reconstructable_manifest().manifest_hash,
        source_snapshot_hash=stable_contract_hash(
            tuple((item.signal_date, item.source_hash) for item in report.snapshots)
        ),
        universe_manifest_hash=stable_contract_hash(
            tuple((item.signal_date, item.universe_hash) for item in report.snapshots)
        ),
        feature_schema_version="etf-ranking-stage-b-v1",
        candidate_registry_hash=registry.registry_hash,
        decision_cutoff_semantics="recorded_available_at_lte_signal_cutoff",
    )
    return canonical_daily_core_targets_from_pit(
        date_inputs=tuple(prefix),
        stage_b_contract=contract,
    )


def _remaining(started: float, max_seconds: float) -> float:
    return max(0.01, max_seconds - (time.monotonic() - started))


async def _set_statement_timeout(
    database: DatabaseManager,
    session: Any,
    seconds: float,
    *,
    read_only: bool,
) -> None:
    """Apply the read/write transaction guard and PostgreSQL timeout."""

    backend = getattr(getattr(database.engine, "url", None), "get_backend_name", lambda: "")()
    if backend != "postgresql":
        return
    milliseconds = max(1, int(seconds * 1000))
    try:
        if read_only:
            await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(
            text("SELECT set_config('statement_timeout', :timeout_value, true)"),
            {"timeout_value": f"{milliseconds}ms"},
        )
    except SQLAlchemyError as exc:
        raise RuntimeError("database statement timeout could not be applied") from exc


async def _build_input(
    session: Any,
    *,
    report: ComparisonPreflightReport,
    valuation_sessions: tuple[date, ...],
    arguments: argparse.Namespace,
    max_seconds: float,
    valuation_calendar_hash: str,
) -> ComparisonInput:
    if not report.snapshots:
        raise ComparisonInputBuilderUnavailableError(
            "comparison_input_builder_missing_pit_snapshots"
        )
    provenance = _frozen_provenance(
        report,
        valuation_calendar_hash=valuation_calendar_hash,
    )
    history_codes = tuple(
        sorted(
            {
                code
                for snapshot in report.snapshots
                for code in snapshot.history_eligible_asset_codes
            }
        )
    )
    valuation_codes = history_codes or tuple(
        sorted(
            {
                code
                for snapshot in report.snapshots
                for code in snapshot.eligible_asset_codes
            }
        )
    )
    if any(item.ready_for_signal_filters for item in report.snapshots):
        valuation = await asyncio.wait_for(
            load_comparison_valuation_input(
                session,
                trading_sessions=valuation_sessions,
                asset_codes=valuation_codes,
                decision_cutoff=datetime.combine(
                    arguments.end_date,
                    _DECISION_CUTOFF,
                    tzinfo=_SHANGHAI,
                ),
                page_size=_PIT_PAGE_SIZE,
            ),
            timeout=max_seconds,
        )
    else:
        # No date passed the common 127-session gate, so reading future prices
        # could not make any route tradable. Preserve a real core result with
        # an empty valuation stream rather than spending the budget on unused
        # outcome rows.
        valuation = ComparisonValuationInput(
            trading_sessions=valuation_sessions,
            adjusted_closes=(),
            calendar_sessions=valuation_sessions,
            calendar_complete_through=valuation_sessions[-1],
        )
    requested_sessions = tuple(
        day
        for day in valuation_sessions
        if arguments.start_date <= day <= arguments.end_date
    )
    required_daily_dates = requested_sessions[:-1]
    daily_targets = await asyncio.wait_for(
        asyncio.to_thread(
            _daily_core_targets_from_report,
            report,
            requested_sessions=required_daily_dates,
            run_id=arguments.run_id,
        ),
        timeout=max_seconds,
    )
    result = build_comparison_input(
        provenance=provenance,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        valuation=valuation,
        snapshots=report.snapshots,
        v2_days=report.v2_days,
        daily_core_targets=daily_targets,
        daily_core_required_signal_dates=required_daily_dates,
        initial_capital=arguments.initial_capital,
    )
    if not isinstance(result, ComparisonInput):
        raise ComparisonInputBuilderUnavailableError(
            "comparison_input_builder_invalid"
        )
    return result


def _frozen_config(
    report: ComparisonPreflightReport,
    *,
    arguments: argparse.Namespace,
    calendar_hash: str,
    valuation_calendar_hash: str | None = None,
) -> dict[str, Any]:
    source_snapshot_hash = stable_contract_hash(
        tuple(item.source_hash for item in report.snapshots)
    )
    provenance = _frozen_provenance(
        report,
        valuation_calendar_hash=valuation_calendar_hash,
    )
    v2_manifest_hashes = tuple(
        item.manifest_hash for item in report.v2_days if item.manifest_hash
    )
    return {
        "schema_version": "etf_route_comparison_cli_config_v1",
        "experiment_id": COMPARISON_EXPERIMENT_ID,
        "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
        "run_id": arguments.run_id,
        "phase": COMPARISON_RESULT_PHASE,
        "start_date": arguments.start_date,
        "end_date": arguments.end_date,
        "initial_capital": arguments.initial_capital,
        "decision_cutoff_policy": DECISION_CUTOFF_POLICY,
        "timezone": "Asia/Shanghai",
        "calendar_hash": calendar_hash,
        "valuation_calendar_hash": valuation_calendar_hash or calendar_hash,
        "source_mode": "factual_pit",
        "source_snapshot_hash": source_snapshot_hash,
        "provenance": asdict(provenance),
        "v2_manifest_hashes": v2_manifest_hashes,
        "cost_policies": {
            "base": asdict(RANKING_PORTFOLIO_BASE_COST_POLICY),
            "stress": asdict(RANKING_PORTFOLIO_STRESS_COST_POLICY),
        },
        "route_ids": COMPARISON_ROUTE_IDS,
        "route_policy_hashes": COMPARISON_ROUTE_POLICY_HASHES,
        "diagnostic_window_guard": {
            "start_date": DEVELOPMENT_DIAGNOSTIC_START,
            "end_date": DEVELOPMENT_DIAGNOSTIC_END,
            "universe": "ETF",
            "holdout_consumption": False,
        },
        "production_policy": "no_production_writes",
        "parameter_search": False,
    }


def _write_comparison_artifacts(
    store_path: Path,
    *,
    run_id: str,
    phase: str,
    config: Mapping[str, Any],
    result_payload: Mapping[str, Any],
    max_seconds: float,
    include_config: bool = True,
    include_result: bool = True,
) -> dict[str, Any]:
    if phase != COMPARISON_RESULT_PHASE:
        raise ArtifactConflictError(f"comparison result phase is fixed at {COMPARISON_RESULT_PHASE}")
    store = ReplayArtifactStore(store_path)
    artifacts: list[tuple[str, Mapping[str, Any]]] = []
    if include_config:
        artifacts.append(("comparison-config", dict(config)))
    if include_result:
        artifacts.append(
            (
                "comparison-result",
                {
                    "schema_version": "etf_route_comparison_result_reference_v1",
                    "result_hash": result_payload.get("result_hash"),
                    "input_hash": result_payload.get("input_hash"),
                    "common_status": result_payload.get("common_status"),
                    "route_ids": tuple(
                        str(route.get("route_id"))
                        for route in result_payload.get("routes", ())
                        if isinstance(route, Mapping)
                    ),
                },
            )
        )
    for route in result_payload.get("routes", ()):
        if not isinstance(route, Mapping):
            continue
        route_id = str(route.get("route_id") or "unknown")
        artifacts.append(
            (
                f"target:{route_id}",
                {
                    "route_id": route_id,
                    "status": route.get("status"),
                    "reason": route.get("reason"),
                    "required_signal_dates": route.get("required_signal_dates", ()),
                    "targets": route.get("targets", ()),
                    "input_hash": route.get("input_hash"),
                },
            )
        )
        for cost_name in ("base", "stress"):
            ledger = route.get(cost_name)
            if isinstance(ledger, Mapping):
                artifacts.append(
                    (
                        f"ledger:{route_id}:{cost_name}",
                        {
                            "route_id": route_id,
                            "cost_scenario": cost_name,
                            "ledger": ledger,
                        },
                    )
                )
    if len(artifacts) > 20:
        raise ArtifactConflictError("comparison artifact page exceeds the bounded row count")
    store.write_research_artifacts(
        run_id=run_id,
        phase=phase,
        artifacts=artifacts,
        max_seconds=max(0.1, min(5.0, max_seconds)),
    )
    digest, count = store.research_phase_digest(
        run_id=run_id,
        phase=phase,
        max_seconds=max(0.1, min(5.0, max_seconds)),
    )
    return {
        "run_id": run_id,
        "phase": phase,
        "artifact_count": count,
        "phase_digest": digest,
        "path": str(store_path),
        "research_only": True,
    }


async def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    if not 0 < arguments.max_seconds <= COMPARISON_MAX_CONTINUATION_SECONDS:
        raise ValueError("max-seconds must be within (0, 55]")
    if not arguments.run_id.strip() or not arguments.phase.strip():
        raise ValueError("run-id and phase must be non-empty")
    if arguments.phase != V2_DAY_CHECK_PHASE:
        raise ValueError(f"phase is fixed at {V2_DAY_CHECK_PHASE}")
    if not math.isfinite(arguments.initial_capital) or arguments.initial_capital <= 0:
        raise ValueError("initial-capital must be positive")
    if (
        arguments.start_date < DEVELOPMENT_DIAGNOSTIC_START
        or arguments.end_date > DEVELOPMENT_DIAGNOSTIC_END
    ):
        raise ValueError("comparison dates must remain within the frozen 2026-08-26..2026-09-09 diagnostic window")
    started = time.monotonic()
    settings = get_settings()
    sessions = _read_calendar(
        arguments.calendar_file,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
    )
    valuation_sessions: tuple[date, ...] = ()
    valuation_calendar_hash = ""
    if arguments.mode == "compare":
        valuation_sessions = _read_valuation_calendar(
            arguments.calendar_file,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
        valuation_calendar_hash = stable_contract_hash(
            {
                "schema_version": "etf_strategy_route_comparison_valuation_calendar_v1",
                "sessions": valuation_sessions,
            }
        )
    store_path = arguments.research_store or _default_store(settings)
    database = DatabaseManager(settings.database_url)
    try:
        async with database.session() as session:
            await _set_statement_timeout(
                database,
                session,
                _remaining(started, arguments.max_seconds),
                read_only=arguments.mode != "prepare-research",
            )
            report = await asyncio.wait_for(
                preflight_comparison_inputs(
                    session,
                    start_date=arguments.start_date,
                    end_date=arguments.end_date,
                    trading_sessions=sessions,
                    research_store=store_path,
                    run_id=arguments.run_id,
                    phase=arguments.phase,
                    max_seconds=_remaining(started, arguments.max_seconds),
                ),
                timeout=_remaining(started, arguments.max_seconds),
            )
            result: dict[str, Any] = {
                "mode": arguments.mode,
                "status": "ready" if report.ready else "blocked",
                "research_only": True,
                "decision_cutoff_policy": DECISION_CUTOFF_POLICY,
                "preflight": report.as_dict(),
                "report_markdown": report.to_markdown(),
            }
            if arguments.mode == "prepare-research":
                preparation = await asyncio.wait_for(
                    prepare_v2_day_checks(
                        session,
                        snapshots=report.snapshots,
                        research_store=store_path,
                        run_id=arguments.run_id,
                        phase=arguments.phase,
                        lease_owner=f"route-comparison:{os.getpid()}",
                        max_seconds=_remaining(started, arguments.max_seconds),
                    ),
                    timeout=_remaining(started, arguments.max_seconds),
                )
                result["status"] = preparation.status
                result["preparation"] = preparation.as_dict()
                result["research_store_after"] = inspect_research_store(
                    store_path,
                    run_id=arguments.run_id,
                    phase=arguments.phase,
                ).as_dict()
            elif arguments.mode == "compare":
                if not report.snapshots:
                    result["status"] = "blocked"
                    result["reason"] = "comparison_requires_at_least_one_pit_snapshot"
                else:
                    readiness = inspect_research_store(
                        store_path,
                        run_id=arguments.run_id,
                        phase=COMPARISON_RESULT_PHASE,
                    )
                    if not _research_store_can_write(readiness):
                        result["status"] = "blocked"
                        result["reason"] = readiness.reason
                    else:
                        # Freeze the diagnostic identity before reading future
                        # valuation prices or calculating any return.
                        config = _frozen_config(
                            report,
                            arguments=arguments,
                            calendar_hash=report.calendar_hash,
                            valuation_calendar_hash=valuation_calendar_hash,
                        )
                        config_ref: dict[str, Any] | None = None
                        payload: Mapping[str, Any] | None = None
                        try:
                            config_ref = await asyncio.wait_for(
                                asyncio.to_thread(
                                    _write_comparison_artifacts,
                                    store_path,
                                    run_id=arguments.run_id,
                                    phase=COMPARISON_RESULT_PHASE,
                                    config=config,
                                    result_payload={"routes": ()},
                                    max_seconds=_remaining(started, arguments.max_seconds),
                                    include_config=True,
                                    include_result=False,
                                ),
                                timeout=_remaining(started, arguments.max_seconds),
                            )
                            result["config_artifact"] = config_ref
                            input_data = await asyncio.wait_for(
                                _build_input(
                                    session,
                                    report=report,
                                    valuation_sessions=valuation_sessions,
                                    arguments=arguments,
                                    max_seconds=_remaining(started, arguments.max_seconds),
                                    valuation_calendar_hash=valuation_calendar_hash,
                                ),
                                timeout=_remaining(started, arguments.max_seconds),
                            )
                            comparison = await asyncio.wait_for(
                                asyncio.to_thread(run_comparison, input_data),
                                timeout=_remaining(started, arguments.max_seconds),
                            )
                            payload = comparison_result_payload(comparison)
                            result["comparison"] = payload
                            result["report_markdown"] = (
                                report.to_markdown()
                                + "\n---\n\n"
                                + comparison.report_markdown
                            )
                            # A config-only artifact is immutable; append the result
                            # namespace with final targets and ledgers in the same
                            # phase, preserving idempotent retries.
                            artifact_ref = await asyncio.wait_for(
                                asyncio.to_thread(
                                    _write_comparison_artifacts,
                                    store_path,
                                    run_id=arguments.run_id,
                                    phase=COMPARISON_RESULT_PHASE,
                                    config=config,
                                    result_payload=payload,
                                    max_seconds=_remaining(started, arguments.max_seconds),
                                    include_config=False,
                                ),
                                timeout=_remaining(started, arguments.max_seconds),
                            )
                            result["status"] = "completed"
                            result["artifact_store"] = artifact_ref
                        except Exception as exc:  # noqa: BLE001 - CLI returns a redacted block
                            result["status"] = "blocked"
                            result["reason"] = _safe_error(exc)
                            if config_ref is not None:
                                result["config_artifact"] = config_ref
    finally:
        await database.engine.dispose()
    return result


def main(argv: list[str] | None = None) -> None:
    arguments = _arguments(argv)
    try:
        if not 0 < arguments.max_seconds <= COMPARISON_MAX_CONTINUATION_SECONDS:
            raise ValueError("max-seconds must be within (0, 55]")
        result = asyncio.run(
            asyncio.wait_for(_run(arguments), timeout=arguments.max_seconds)
        )
    except Exception as exc:  # noqa: BLE001 - stdout must remain one JSON object
        result = {
            "mode": arguments.mode,
            "research_only": True,
            "status": "blocked",
            "reason": _safe_error(exc),
        }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    print(encoded)
    _write(arguments.report_output, str(result.get("report_markdown") or ""))
    if arguments.json_output is not None:
        _write(arguments.json_output, encoded + "\n")


if __name__ == "__main__":
    main()
