import asyncio
from datetime import date, timedelta

import pytest

from app.services.strategy_lab.etf_factor_runner import (
    ConcurrentFactorExperimentError,
    FactorBatchResult,
    run_bounded_factor_experiment,
)


def _codes(count: int) -> tuple[str, ...]:
    return tuple(f"{index:06d}" for index in range(count))


async def test_production_shaped_run_is_single_worker_bounded_and_cached(app) -> None:
    batch_sizes: list[int] = []
    concurrent_fetches = 0
    peak_concurrent_fetches = 0

    async def fetch(
        batch: tuple[str, ...],
        cursor: date | None,
        cache: dict[str, dict],
    ) -> FactorBatchResult:
        nonlocal concurrent_fetches, peak_concurrent_fetches
        concurrent_fetches += 1
        peak_concurrent_fetches = max(peak_concurrent_fetches, concurrent_fetches)
        batch_sizes.append(len(batch))
        assert all(code not in cache for code in batch)
        await asyncio.sleep(0)
        concurrent_fetches -= 1
        return FactorBatchResult(
            factor_rows={code: {"factor": int(code)} for code in batch},
            cursor_date=(cursor or date(2026, 1, 1)) + timedelta(days=1),
            exclusion_count=1 if len(batch_sizes) == 1 else 0,
        )

    async with app.state.db.session() as session:
        checkpoint = await run_bounded_factor_experiment(
            session,
            manifest_hash="production-shaped",
            code_version="v1",
            asset_codes=_codes(1_405),
            fetch_batch=fetch,
            initial_cursor_date=date(2025, 12, 31),
            timeout_seconds=1.0,
        )

    assert checkpoint.status == "complete"
    assert checkpoint.batch_count == 71
    assert checkpoint.peak_batch_size == 20
    assert max(batch_sizes) == 20
    assert peak_concurrent_fetches == 1
    assert len(checkpoint.cached_factor_rows_json) == 1_405
    assert checkpoint.coverage_ratio == 1.0
    assert checkpoint.exclusion_count == 1
    assert checkpoint.cursor_date == date(2026, 3, 12)
    assert 0 < checkpoint.peak_memory_bytes < 256 * 1024 * 1024
    assert 0 < checkpoint.runtime_seconds < 30


async def test_interruption_persists_cursor_and_resume_is_idempotent(app) -> None:
    codes = _codes(45)
    calls = 0

    async def interrupted_fetch(
        batch: tuple[str, ...],
        cursor: date | None,
        _cache: dict[str, dict],
    ) -> FactorBatchResult:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("provider unavailable")
        return FactorBatchResult(
            factor_rows={code: {"factor": code} for code in batch},
            cursor_date=(cursor or date(2026, 1, 1)) + timedelta(days=1),
        )

    async with app.state.db.session() as session:
        partial = await run_bounded_factor_experiment(
            session,
            manifest_hash="resume",
            code_version="v1",
            asset_codes=codes,
            fetch_batch=interrupted_fetch,
            timeout_seconds=1.0,
        )
        assert partial.status == "partial"
        assert partial.error_summary == "OSError: provider unavailable"
        frozen_hashes = tuple(partial.completed_batch_hashes_json)
        frozen_rows = dict(partial.cached_factor_rows_json)

        async def resumed_fetch(
            batch: tuple[str, ...],
            cursor: date | None,
            cache: dict[str, dict],
        ) -> FactorBatchResult:
            assert cache == frozen_rows
            assert batch == codes[40:]
            return FactorBatchResult(
                factor_rows={code: {"factor": code} for code in batch},
                cursor_date=(cursor or date(2026, 1, 1)) + timedelta(days=1),
            )

        complete = await run_bounded_factor_experiment(
            session,
            manifest_hash="resume",
            code_version="v1",
            asset_codes=codes,
            fetch_batch=resumed_fetch,
            timeout_seconds=1.0,
        )

    assert complete.status == "complete"
    assert tuple(complete.completed_batch_hashes_json[:2]) == frozen_hashes
    assert len(complete.cached_factor_rows_json) == 45
    assert complete.batch_count == 3


async def test_timeout_is_bounded_and_duplicate_run_is_rejected(app) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_fetch(
        batch: tuple[str, ...],
        cursor: date | None,
        _cache: dict[str, dict],
    ) -> FactorBatchResult:
        entered.set()
        await release.wait()
        return FactorBatchResult(
            factor_rows={code: {} for code in batch},
            cursor_date=cursor,
        )

    async with app.state.db.session() as first_session:
        first = asyncio.create_task(
            run_bounded_factor_experiment(
                first_session,
                manifest_hash="concurrent",
                code_version="v1",
                asset_codes=("510300",),
                fetch_batch=blocked_fetch,
                timeout_seconds=0.5,
            )
        )
        await entered.wait()
        async with app.state.db.session() as second_session:
            with pytest.raises(
                ConcurrentFactorExperimentError,
                match="already running",
            ):
                await run_bounded_factor_experiment(
                    second_session,
                    manifest_hash="concurrent",
                    code_version="v1",
                    asset_codes=("510300",),
                    fetch_batch=blocked_fetch,
                    timeout_seconds=0.5,
                )
        release.set()
        completed = await first
    assert completed.status == "complete"

    async def too_slow(
        batch: tuple[str, ...],
        cursor: date | None,
        _cache: dict[str, dict],
    ) -> FactorBatchResult:
        await asyncio.sleep(0.05)
        return FactorBatchResult(
            factor_rows={code: {} for code in batch},
            cursor_date=cursor,
        )

    async with app.state.db.session() as session:
        timed_out = await run_bounded_factor_experiment(
            session,
            manifest_hash="timeout",
            code_version="v1",
            asset_codes=("510300",),
            fetch_batch=too_slow,
            timeout_seconds=0.01,
        )
    assert timed_out.status == "partial"
    assert timed_out.error_summary is not None
    assert timed_out.error_summary.startswith("TimeoutError:")

    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="at most 55"):
            await run_bounded_factor_experiment(
                session,
                manifest_hash="too-long",
                code_version="v1",
                asset_codes=(),
                fetch_batch=too_slow,
                timeout_seconds=55.1,
            )
