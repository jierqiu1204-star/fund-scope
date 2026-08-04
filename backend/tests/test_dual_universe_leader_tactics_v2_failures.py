from __future__ import annotations

import asyncio

from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
    run_bounded_batch,
)


def test_failed_codes_are_retained_as_explicit_partial_evidence() -> None:
    async def run() -> tuple[object, list[str], object]:
        fetched: list[str] = []

        async def fetch_one(code: str) -> None:
            fetched.append(code)
            if code == "000002":
                raise OSError("provider unavailable")

        first = await run_bounded_batch(
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
        second = await run_bounded_batch(
            ("000001", "000002", "000003", "000004", "000005"),
            checkpoint=first.checkpoint,
            fetch_one=fetch_one,
            provider_cooldown_seconds=0,
        )
        return first.checkpoint, fetched, second

    checkpoint, fetched, second = asyncio.run(run())
    assert fetched == ["000001", "000002", "000003", "000004", "000005", "000002"]
    assert checkpoint.status == "partial"
    assert checkpoint.failed_codes == (("000002", "OSError: provider unavailable"),)
    assert checkpoint.completed_codes == ("000001", "000003", "000004", "000005")
    assert second.checkpoint.failed_codes == checkpoint.failed_codes
    assert second.failed == (("000002", "OSError: provider unavailable"),)
    assert second.checkpoint.status == "partial"
