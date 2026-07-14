from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time
from pathlib import Path

from app.services.tracked_positions.lifecycle import stable_contract_hash

from .artifact_store import (
    ReplayArtifactStore,
    feature_artifact_identity,
    replay_artifact_identity,
)
from .checkpoint import (
    ReplayRunContract,
    build_replay_checkpoint,
    checkpoint_to_json,
)
from .features import AdjustedDailyInput, FeatureBatchRequest, compute_feature_batch
from .replay import (
    PointInTimeUniverseDay,
    ReplayBatchRequest,
    ReplayCandidateConfig,
    build_completion_manifest,
    canonical_candidate_config_hash,
    canonical_membership_hash,
    canonical_policy_input_hash,
    run_replay_batch,
)

DEFAULT_PEAK_RSS_LIMIT_BYTES = int(2.5 * 1024**3)


@dataclass(frozen=True)
class BoundedBatchProfile:
    worker_count: int
    synthetic_workload: bool
    cpu_affinity_limited: bool
    environment_label: str
    asset_count: int
    session_count: int
    source_rows_written: int
    source_rows_read: int
    feature_rows_processed: int
    replay_dates_processed: int
    replay_feature_rows_read: int
    ranking_rows_written: int
    replay_events_written: int
    buy_fills_written: int
    equity_rows_written: int
    final_position_count: int
    cumulative_fees: float
    ending_equity: float
    elapsed_seconds: float
    rows_per_second: float
    peak_rss_bytes: int
    statement_count: int
    rows_bound: int
    checkpoint_size_bytes: int
    database_size_bytes: int
    within_memory_budget: bool


def _peak_rss_bytes() -> int:
    if os.name == "nt":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        success = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        if not success:
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)

    import resource

    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024


def _sessions(*, start: date, count: int) -> tuple[date, ...]:
    sessions: list[date] = []
    candidate = start
    while len(sessions) < count:
        if candidate.weekday() < 5:
            sessions.append(candidate)
        candidate += timedelta(days=1)
    return tuple(sessions)


def _source_rows(
    *,
    asset_codes: tuple[str, ...],
    sessions: tuple[date, ...],
) -> tuple[AdjustedDailyInput, ...]:
    rows: list[AdjustedDailyInput] = []
    for asset_index, asset_code in enumerate(asset_codes):
        for session_index, session_date in enumerate(sessions):
            base = 10.0 + asset_index / 1_000.0 + session_index / 100.0
            known_at = datetime.combine(
                session_date,
                wall_time(15, 0),
                tzinfo=UTC,
            )
            rows.append(
                AdjustedDailyInput(
                    asset_code=asset_code,
                    session_date=session_date,
                    raw_open=base,
                    raw_high=base + 0.1,
                    raw_low=base - 0.1,
                    raw_close=base + 0.02,
                    volume=1_000_000.0 + asset_index,
                    adjustment_factor=1.0,
                    adjusted_data_source="synthetic_profile_adjusted",
                    adjustment_kind="total_return_adjusted",
                    decision_eligible=True,
                    provider_healthy=True,
                    fresh_at_cutoff=True,
                    known_at=known_at,
                    source_cutoff=known_at,
                    demonstrably_tradable=True,
                )
            )
    return tuple(rows)


def representative_profile(
    *,
    store_path: str | Path,
    asset_count: int = 1_200,
    session_count: int = 22,
    worker_count: int = 1,
    memory_limit_bytes: int = DEFAULT_PEAK_RSS_LIMIT_BYTES,
) -> BoundedBatchProfile:
    """Measure one real bounded SQLite -> StageA -> StageB -> checkpoint pass."""
    if worker_count != 1:
        raise ValueError("worker_count must be 1 for the bounded replay profile")
    if not 1_000 <= asset_count <= 1_500:
        raise ValueError("asset_count must be between 1000 and 1500")
    if not 22 <= session_count <= 31:
        raise ValueError("session_count must be between 22 and 31")
    if memory_limit_bytes < 1:
        raise ValueError("memory_limit_bytes must be positive")
    path = Path(store_path)
    run_id = "representative-profile-v2"
    feature_contract_hash = "representative-feature-contract-v2"
    input_snapshot_hash = "representative-input-snapshot-v2"
    candidates = (
        ReplayCandidateConfig(candidate_id="candidate-3", top_n=3),
        ReplayCandidateConfig(candidate_id="candidate-5", top_n=5),
    )
    frozen_parameter_hash = "representative-frozen-parameters-v2"
    candidate_config_hash = canonical_candidate_config_hash(
        candidates,
        frozen_parameter_hash=frozen_parameter_hash,
    )
    asset_codes = tuple(f"profile-{index:04d}" for index in range(asset_count))
    sessions = _sessions(start=date(2026, 1, 1), count=session_count)
    target_session = sessions[-1]
    output_sessions = sessions[-2:]
    warmup_sessions = session_count - len(output_sessions)
    decision_cutoffs = tuple(
        (
            session,
            datetime.combine(session, wall_time(15, 0), tzinfo=UTC),
        )
        for session in sessions
    )
    data_cutoff = datetime.combine(target_session, wall_time(15, 30), tzinfo=UTC)
    contract = ReplayRunContract(
        run_id=run_id,
        contract_hash="representative-replay-contract-v2",
        input_snapshot_hash=input_snapshot_hash,
        code_hash="representative-code-v2",
        schema_hash="representative-schema-v2",
        candidate_config_hash=candidate_config_hash,
        frozen_parameter_hash=frozen_parameter_hash,
        policy_input_hash=canonical_policy_input_hash(()),
        data_cutoff=target_session,
        warmup_boundary=sessions[0],
        candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
    )
    source_row_count = asset_count * session_count
    store = ReplayArtifactStore(path)

    started = time.perf_counter()
    source_rows = _source_rows(asset_codes=asset_codes, sessions=sessions)
    source_rows_written = store.write_source_rows(
        run_id=run_id,
        rows=source_rows,
        max_rows=source_row_count,
    )
    source_page = store.read_source_page(
        run_id=run_id,
        asset_codes=asset_codes,
        start_date=sessions[0],
        end_date=target_session,
        max_rows=source_row_count,
    )
    feature_result = compute_feature_batch(
        source_rows=source_page,
        request=FeatureBatchRequest(
            run_id=run_id,
            feature_contract_hash=feature_contract_hash,
            input_snapshot_hash=input_snapshot_hash,
            asset_codes=asset_codes,
            start_date=output_sessions[0],
            end_date=target_session,
            data_cutoff=data_cutoff,
            trading_sessions=sessions,
            decision_cutoffs=decision_cutoffs,
            warmup_sessions=warmup_sessions,
            max_source_rows=source_row_count,
            max_items=asset_count * len(output_sessions),
            max_seconds=55.0,
            worker_count=worker_count,
        ),
        candidate_ids=contract.candidate_ids,
    )
    expected_feature_rows = asset_count * len(output_sessions)
    if (
        not feature_result.complete
        or feature_result.processed_items != expected_feature_rows
    ):
        raise RuntimeError("representative StageA batch did not complete")

    manifests = tuple(
        build_completion_manifest(
            run_id=run_id,
            universe=PointInTimeUniverseDay(
                session_date=session,
                eligible_asset_codes=asset_codes,
                expected_universe_count=asset_count,
                canonical_membership_hash=canonical_membership_hash(asset_codes),
                snapshot_hash=stable_contract_hash(
                    {"session_date": session, "asset_codes": asset_codes}
                ),
            ),
            feature_rows=tuple(
                row for row in feature_result.rows if row.session_date == session
            ),
        )
        for session in output_sessions
    )
    feature_manifest_hash, feature_output_keys = feature_artifact_identity(
        feature_result.rows,
        manifests,
    )
    feature_checkpoint = build_replay_checkpoint(
        contract=contract,
        stage="feature",
        generation=1,
        manifest_hash=feature_manifest_hash,
        state=None,
        last_completed_unit=(
            f"feature:{feature_result.rows[-1].asset_code}:"
            f"{feature_result.rows[-1].session_date.isoformat()}"
        ),
        output_keys=feature_output_keys,
    )
    store.commit_feature_batch(
        feature_rows=feature_result.rows,
        manifests=manifests,
        checkpoint=feature_checkpoint,
        expected_generation=0,
        max_feature_rows=expected_feature_rows,
        max_manifests=len(output_sessions),
        max_seconds=55.0,
    )

    replay_page = store.load_replay_page(
        run_id=run_id,
        start_date=output_sessions[0],
        end_date=target_session,
        after_date=None,
        max_dates=len(output_sessions),
        max_feature_rows=expected_feature_rows,
    )
    replay_result = run_replay_batch(
        feature_rows=replay_page.feature_rows,
        manifests=replay_page.manifests,
        candidates=candidates,
        policy_outputs=(),
        request=ReplayBatchRequest(
            run_id=run_id,
            frozen_parameter_hash=frozen_parameter_hash,
            start_date=output_sessions[0],
            end_date=target_session,
            max_dates=len(output_sessions),
            max_feature_rows=expected_feature_rows,
            max_policy_outputs=1,
            max_pending_fills=asset_count,
            max_events=asset_count * len(candidates) * 4,
            max_seconds=55.0,
            worker_count=worker_count,
            has_more=replay_page.has_more,
        ),
    )
    if (
        not replay_result.complete
        or replay_result.processed_dates != len(output_sessions)
    ):
        raise RuntimeError("representative StageB batch did not complete")
    buy_fills = tuple(
        event for event in replay_result.events if event.event_type == "buy_filled"
    )
    final_states = tuple(replay_result.state.candidate_states.values())
    final_position_count = sum(len(state.positions) for state in final_states)
    cumulative_fees = sum(state.cumulative_fees for state in final_states)
    ending_equity = sum(state.equity for state in final_states)
    if not buy_fills or not final_position_count or cumulative_fees <= 0:
        raise RuntimeError("representative StageB did not exercise real T+1 fills")
    replay_manifest_hash, replay_output_keys = replay_artifact_identity(
        replay_result.rankings,
        replay_result.events,
        replay_result.equity_curve,
    )
    replay_checkpoint = build_replay_checkpoint(
        contract=contract,
        stage="replay",
        generation=1,
        manifest_hash=replay_manifest_hash,
        state=replay_result.state,
        last_completed_unit=f"replay:{target_session.isoformat()}",
        output_keys=replay_output_keys,
    )
    store.commit_replay_batch(
        rankings=replay_result.rankings,
        events=replay_result.events,
        equity_curve=replay_result.equity_curve,
        checkpoint=replay_checkpoint,
        expected_generation=0,
        max_rankings=len(output_sessions),
        max_events=max(1, len(replay_result.events)),
        max_equity_rows=len(candidates) * len(output_sessions),
        max_seconds=55.0,
    )

    loaded_checkpoint = store.load_checkpoint(
        run_id=run_id,
        stage="replay",
        expected_contract=contract,
    )
    if loaded_checkpoint is None or loaded_checkpoint.state != replay_result.state:
        raise RuntimeError("representative replay checkpoint was not persisted")
    counts = store.artifact_counts(run_id)
    elapsed = max(time.perf_counter() - started, 1e-12)
    peak_rss = _peak_rss_bytes()
    checkpoint_size = len(checkpoint_to_json(loaded_checkpoint).encode("utf-8"))
    return BoundedBatchProfile(
        worker_count=worker_count,
        synthetic_workload=True,
        cpu_affinity_limited=False,
        environment_label=(
            "synthetic_windows_process_no_cpu_affinity"
            if os.name == "nt"
            else f"synthetic_{sys.platform}_process_no_cpu_affinity"
        ),
        asset_count=asset_count,
        session_count=session_count,
        source_rows_written=source_rows_written,
        source_rows_read=len(source_page),
        feature_rows_processed=feature_result.processed_items,
        replay_dates_processed=replay_result.processed_dates,
        replay_feature_rows_read=replay_result.feature_rows_read,
        ranking_rows_written=counts["rankings"],
        replay_events_written=counts["events"],
        buy_fills_written=len(buy_fills),
        equity_rows_written=counts["equity"],
        final_position_count=final_position_count,
        cumulative_fees=cumulative_fees,
        ending_equity=ending_equity,
        elapsed_seconds=elapsed,
        rows_per_second=source_row_count / elapsed,
        peak_rss_bytes=peak_rss,
        statement_count=store.statement_count,
        rows_bound=store.rows_bound,
        checkpoint_size_bytes=checkpoint_size,
        database_size_bytes=path.stat().st_size,
        within_memory_budget=peak_rss <= memory_limit_bytes,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    arguments = parser.parse_args()
    print(
        json.dumps(
            asdict(representative_profile(store_path=arguments.store)),
            sort_keys=True,
        )
    )
