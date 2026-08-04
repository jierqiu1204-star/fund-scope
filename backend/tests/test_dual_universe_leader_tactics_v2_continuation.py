from __future__ import annotations

import asyncio
import hashlib
import tracemalloc

from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
    ordered_page,
    run_bounded_batch,
)


def _digest(codes: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(codes).encode()).hexdigest()


def test_resume_from_each_allowed_batch_size_is_deterministic() -> None:
    async def collect(initial_batch_size: int) -> tuple[tuple[str, ...], list[str]]:
        codes = tuple(f"{index:06d}" for index in range(25))
        fetched: list[str] = []
        checkpoint = V2CollectorCheckpoint(
            cursor=None,
            batch_size=initial_batch_size,
            completed_codes=(),
            status="paused",
        )
        while checkpoint.status != "complete":
            result = await run_bounded_batch(
                codes,
                checkpoint=checkpoint,
                fetch_one=lambda code: _record(code, fetched),
                provider_cooldown_seconds=0,
            )
            assert len(result.checkpoint.completed_codes) - len(checkpoint.completed_codes) <= 20
            assert len(result.checkpoint.completed_codes) >= len(checkpoint.completed_codes)
            checkpoint = result.checkpoint
        return checkpoint.completed_codes, fetched

    async def _record(code: str, fetched: list[str]) -> None:
        fetched.append(code)

    outcomes = [asyncio.run(collect(size)) for size in (5, 10, 20)]
    expected = tuple(f"{index:06d}" for index in range(25))
    assert [completed for completed, _ in outcomes] == [expected] * 3
    assert [_digest(completed) for completed, _ in outcomes] == [_digest(expected)] * 3
    assert [fetched for _, fetched in outcomes] == [list(expected)] * 3


def test_budget_interrupt_keeps_a_resumable_checkpoint() -> None:
    async def run() -> tuple[V2CollectorCheckpoint, list[str]]:
        now = 0.0
        fetched: list[str] = []

        def clock() -> float:
            return now

        async def fetch_one(code: str) -> None:
            nonlocal now
            fetched.append(code)
            now += 1.0

        result = await run_bounded_batch(
            tuple(f"{index:06d}" for index in range(10)),
            checkpoint=V2CollectorCheckpoint(
                cursor=None,
                batch_size=5,
                completed_codes=(),
                status="paused",
            ),
            fetch_one=fetch_one,
            budget_seconds=3.0,
            provider_cooldown_seconds=0,
            clock=clock,
        )
        return result.checkpoint, fetched

    checkpoint, fetched = asyncio.run(run())
    assert fetched == ["000000", "000001", "000002"]
    assert checkpoint.cursor == "000002"
    assert checkpoint.completed_codes == tuple(fetched)
    assert checkpoint.status == "paused"


def test_second_worker_is_rejected_while_first_worker_holds_process_lease() -> None:
    async def run() -> tuple[str, str]:
        started = asyncio.Event()
        release = asyncio.Event()

        async def fetch_one(_: str) -> None:
            started.set()
            await release.wait()

        first = asyncio.create_task(
            run_bounded_batch(
                ("000001", "000002", "000003", "000004", "000005"),
                checkpoint=V2CollectorCheckpoint(
                    cursor=None,
                    batch_size=5,
                    completed_codes=(),
                    status="paused",
                ),
                fetch_one=fetch_one,
                provider_cooldown_seconds=0,
            )
        )
        await started.wait()
        second = await run_bounded_batch(
            ("000001", "000002", "000003", "000004", "000005"),
            checkpoint=V2CollectorCheckpoint(
                cursor=None,
                batch_size=5,
                completed_codes=(),
                status="paused",
            ),
            fetch_one=fetch_one,
            provider_cooldown_seconds=0,
        )
        release.set()
        first_result = await first
        return first_result.stopped_reason, second.stopped_reason

    first_reason, second_reason = asyncio.run(run())
    assert first_reason == "page_complete"
    assert second_reason == "single_worker_lease_busy"


def test_ordered_page_has_a_bounded_working_set_for_large_universe() -> None:
    codes = tuple(f"{index:06d}" for index in range(100_000))
    tracemalloc.start()
    try:
        page = ordered_page(codes, cursor=None, batch_size=20)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(page) == 20
    assert len(set(page)) == len(page)
    assert peak < 16 * 1024 * 1024
