from __future__ import annotations

import asyncio
import time

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
    run_bounded_batch,
)


def test_checkpoint_rejects_completed_and_failed_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        V2CollectorCheckpoint(
            cursor=None,
            batch_size=5,
            completed_codes=("000001",),
            failed_codes=(("000001", "transient provider failure"),),
            status="paused",
        )


def test_checkpoint_keeps_legacy_legal_shape_compatible() -> None:
    checkpoint = V2CollectorCheckpoint(
        cursor="000001",
        batch_size=5,
        completed_codes=("000001",),
        status="complete",
    )
    assert checkpoint.completed_codes == ("000001",)
    assert checkpoint.failed_codes == ()


@pytest.mark.asyncio
async def test_each_fetch_is_hard_bounded_by_remaining_continuation_budget() -> None:
    started = time.monotonic()
    fetched: list[str] = []

    async def fetch_one(code: str) -> None:
        fetched.append(code)
        await asyncio.sleep(0.20)

    result = await run_bounded_batch(
        ("000001", "000002"),
        checkpoint=V2CollectorCheckpoint(
            cursor=None,
            batch_size=5,
            completed_codes=(),
            status="paused",
        ),
        fetch_one=fetch_one,
        budget_seconds=0.05,
        provider_cooldown_seconds=0,
    )

    assert time.monotonic() - started < 0.15
    assert result.stopped_reason == "budget_exhausted"
    assert result.failed and result.failed[0][0] == "000001"
    assert result.checkpoint.completed_codes == ()


@pytest.mark.asyncio
async def test_blocking_fetch_returned_late_is_not_marked_completed() -> None:
    async def fetch_one(_: str) -> str:
        # ``wait_for`` cannot interrupt this same-thread block. The collector
        # must still reject the late result after control returns to asyncio.
        time.sleep(0.15)  # noqa: ASYNC251 - intentional blocking-callback regression
        return "a" * 64

    result = await run_bounded_batch(
        ("000001",),
        checkpoint=V2CollectorCheckpoint(
            cursor=None,
            batch_size=5,
            completed_codes=(),
            status="paused",
        ),
        fetch_one=fetch_one,
        budget_seconds=0.03,
        provider_cooldown_seconds=0,
    )

    assert result.stopped_reason == "budget_exhausted"
    assert result.completed == ()
    assert result.checkpoint.completed_codes == ()
    assert result.failed and result.failed[0][0] == "000001"
    assert "returned after continuation budget" in result.failed[0][1]


@pytest.mark.asyncio
async def test_non_awaitable_fetch_result_fails_closed() -> None:
    def fetch_one(_: str) -> str:
        return "a" * 64

    result = await run_bounded_batch(
        ("000001",),
        checkpoint=V2CollectorCheckpoint(
            cursor=None,
            batch_size=5,
            completed_codes=(),
            status="paused",
        ),
        fetch_one=fetch_one,  # type: ignore[arg-type]
        provider_cooldown_seconds=0,
    )

    assert result.completed == ()
    assert result.checkpoint.completed_codes == ()
    assert result.failed == (("000001", "TypeError: fetch_one must return an awaitable"),)


@pytest.mark.asyncio
async def test_failed_code_is_retried_and_cleared_after_transient_recovery() -> None:
    calls: list[str] = []
    failures_left = {"000002": 1}

    async def fetch_one(code: str) -> None:
        calls.append(code)
        if failures_left.get(code, 0):
            failures_left[code] -= 1
            raise OSError("temporary provider failure")

    checkpoint = V2CollectorCheckpoint(
        cursor=None,
        batch_size=5,
        completed_codes=(),
        status="paused",
    )
    first = await run_bounded_batch(
        ("000001", "000002", "000003"),
        checkpoint=checkpoint,
        fetch_one=fetch_one,
        provider_cooldown_seconds=0,
    )
    second = await run_bounded_batch(
        ("000001", "000002", "000003"),
        checkpoint=first.checkpoint,
        fetch_one=fetch_one,
        provider_cooldown_seconds=0,
    )

    assert calls == ["000001", "000002", "000003", "000002"]
    assert first.checkpoint.failed_codes == (("000002", "OSError: temporary provider failure"),)
    assert second.failed == ()
    assert second.checkpoint.failed_codes == ()
    assert second.checkpoint.status == "complete"
    assert second.checkpoint.error_summary is None


@pytest.mark.asyncio
async def test_cooldown_does_not_start_a_fetch_after_budget_is_exhausted() -> None:
    fetched: list[str] = []

    async def fetch_one(code: str) -> None:
        fetched.append(code)

    result = await run_bounded_batch(
        ("000001", "000002"),
        checkpoint=V2CollectorCheckpoint(
            cursor=None,
            batch_size=5,
            completed_codes=(),
            status="paused",
        ),
        fetch_one=fetch_one,
        budget_seconds=0.03,
        provider_cooldown_seconds=0.03,
    )

    assert fetched == ["000001"]
    assert result.stopped_reason == "budget_exhausted"
