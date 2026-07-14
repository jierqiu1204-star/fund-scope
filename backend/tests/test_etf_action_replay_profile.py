from __future__ import annotations

from pathlib import Path

import pytest

from app.services.strategy_lab.etf_action_replay.profiling import (
    DEFAULT_PEAK_RSS_LIMIT_BYTES,
    representative_profile,
)


def test_representative_profile_measures_real_bounded_two_stage_pipeline(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "representative-profile.sqlite3"

    profile = representative_profile(
        store_path=store_path,
        asset_count=1_200,
        session_count=22,
    )

    assert profile.worker_count == 1
    assert profile.synthetic_workload is True
    assert profile.cpu_affinity_limited is False
    assert profile.environment_label == "synthetic_windows_process_no_cpu_affinity"
    assert profile.asset_count == 1_200
    assert profile.session_count == 22
    assert profile.source_rows_written == 26_400
    assert profile.source_rows_read == 26_400
    assert profile.feature_rows_processed == 2_400
    assert profile.replay_dates_processed == 2
    assert profile.replay_feature_rows_read == 2_400
    assert profile.ranking_rows_written == 2
    assert profile.buy_fills_written == 8
    assert profile.equity_rows_written == 4
    assert profile.final_position_count == 8
    assert profile.cumulative_fees > 0
    assert profile.ending_equity > 0
    assert 0 < profile.statement_count < 100
    assert profile.rows_bound >= 27_600
    assert profile.checkpoint_size_bytes > 0
    assert profile.database_size_bytes == store_path.stat().st_size
    assert profile.rows_per_second > 0
    assert 0 < profile.peak_rss_bytes <= DEFAULT_PEAK_RSS_LIMIT_BYTES
    assert profile.within_memory_budget is True


def test_representative_profile_rejects_non_single_worker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="worker_count"):
        representative_profile(
            store_path=tmp_path / "invalid.sqlite3",
            asset_count=1_200,
            session_count=22,
            worker_count=2,
        )
