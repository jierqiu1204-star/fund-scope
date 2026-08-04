"""Bounded, resumable collector primitives for A-share V2 research facts."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

MIN_BATCH_SIZE = 5
MAX_BATCH_SIZE = 20
MAX_CONTINUATION_SECONDS = 55.0
_SHA256_HEX = frozenset("0123456789abcdef")


def _validate_sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_HEX for character in value)
    ):
        raise ValueError("fetch_one must return a canonical lowercase 64-character sha256")
    return value


@dataclass(frozen=True)
class V2CollectorCheckpoint:
    cursor: str | None
    batch_size: int
    completed_codes: tuple[str, ...]
    status: str
    failed_codes: tuple[tuple[str, str], ...] = ()
    error_summary: str | None = None
    manifest_hash: str | None = None
    # Older callers only have completed_codes. New captures add the immutable
    # content identity without changing the completed-count contract.
    completed_hashes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        completed_codes = {code for code in self.completed_codes if isinstance(code, str)}
        failed_codes = {
            item[0]
            for item in self.failed_codes
            if isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[0], str)
        }
        overlap = completed_codes & failed_codes
        if overlap:
            raise ValueError(
                "checkpoint completed_codes and failed_codes overlap: " + ", ".join(sorted(overlap))
            )
        normalized: dict[str, str] = {}
        for item in self.completed_hashes:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("completed_hashes must contain (code, sha256) pairs")
            code, content_hash = item
            if not isinstance(code, str) or not code or code != code.strip():
                raise ValueError("completed_hashes contains an invalid code")
            validated_hash = _validate_sha256(content_hash)
            previous = normalized.get(code)
            if previous is not None and previous != validated_hash:
                raise ValueError("completed_hashes contains conflicting hashes for one code")
            normalized[code] = validated_hash
        object.__setattr__(self, "completed_hashes", tuple(sorted(normalized.items())))


@dataclass(frozen=True)
class V2CollectorBatch:
    completed: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]
    checkpoint: V2CollectorCheckpoint
    elapsed_seconds: float
    stopped_reason: str


def adaptive_batch_size(
    *, requested: int, elapsed_seconds: float, memory_pressure: bool = False
) -> int:
    """Choose a deterministic 5-20 batch without exceeding the resource policy."""

    size = max(MIN_BATCH_SIZE, min(MAX_BATCH_SIZE, requested))
    if memory_pressure or elapsed_seconds > 8.0:
        return max(MIN_BATCH_SIZE, size // 2)
    if elapsed_seconds < 2.0:
        return min(MAX_BATCH_SIZE, size + 5)
    return size


def ordered_page(
    codes: Sequence[str],
    *,
    cursor: str | None,
    batch_size: int,
) -> tuple[str, ...]:
    normalized = tuple(sorted({str(code).strip() for code in codes if str(code).strip()}))
    after_cursor = tuple(code for code in normalized if cursor is None or code > cursor)
    return after_cursor[: max(MIN_BATCH_SIZE, min(MAX_BATCH_SIZE, batch_size))]


_PROCESS_LEASE = asyncio.Lock()


async def run_bounded_batch(
    codes: Sequence[str],
    *,
    checkpoint: V2CollectorCheckpoint,
    fetch_one: Callable[[str], Awaitable[str | None]],
    budget_seconds: float = MAX_CONTINUATION_SECONDS,
    provider_cooldown_seconds: float = 0.25,
    clock: Callable[[], float] = time.monotonic,
) -> V2CollectorBatch:
    """Run one serial batch; callers persist the returned checkpoint atomically.

    ``fetch_one`` is an async, non-blocking callback contract. The collector
    cannot physically preempt same-thread Python such as ``time.sleep``: while
    that code owns the event loop, ``asyncio.wait_for`` cannot run its timeout
    callback. Callers must adapt blocking provider work before handing it to
    this function, and must not offload a callback that also uses an
    ``AsyncSession``. The post-return wall-clock check below is deliberately
    fail-closed so a late result is recorded as a retryable failure rather than
    as completed evidence.
    """

    if budget_seconds <= 0 or budget_seconds > MAX_CONTINUATION_SECONDS:
        raise ValueError("collector budget must be in (0, 55] seconds")
    if provider_cooldown_seconds < 0 or provider_cooldown_seconds > 5:
        raise ValueError("provider cooldown must be between 0 and 5 seconds")
    if _PROCESS_LEASE.locked():
        return V2CollectorBatch(
            completed=(),
            failed=(),
            checkpoint=checkpoint,
            elapsed_seconds=0.0,
            stopped_reason="single_worker_lease_busy",
        )
    async with _PROCESS_LEASE:
        started = clock()
        completed = list(dict.fromkeys(checkpoint.completed_codes))
        completed_hashes_by_code = dict(checkpoint.completed_hashes)
        failed_by_code = dict(checkpoint.failed_codes)
        normalized = tuple(sorted({str(code).strip() for code in codes if str(code).strip()}))
        completed_codes = set(completed)
        page_limit = max(MIN_BATCH_SIZE, min(MAX_BATCH_SIZE, checkpoint.batch_size))
        retry_page = tuple(
            code for code in normalized if code in failed_by_code and code not in completed_codes
        )[:page_limit]
        retry_codes = set(retry_page)
        normal_page = tuple(
            code
            for code in ordered_page(
                codes, cursor=checkpoint.cursor, batch_size=checkpoint.batch_size
            )
            if code not in completed_codes
            and code not in failed_by_code
            and code not in retry_codes
        )
        page = retry_page + normal_page[: max(0, page_limit - len(retry_page))]
        failed: list[tuple[str, str]] = []
        last_cursor = checkpoint.cursor
        budget_exhausted = False
        for index, code in enumerate(page):
            remaining_budget = budget_seconds - (clock() - started)
            if remaining_budget <= 0:
                budget_exhausted = True
                break
            if index and provider_cooldown_seconds:
                if provider_cooldown_seconds >= remaining_budget:
                    budget_exhausted = True
                    break
                try:
                    await asyncio.wait_for(
                        asyncio.sleep(provider_cooldown_seconds), timeout=remaining_budget
                    )
                except TimeoutError:
                    budget_exhausted = True
                    break
                remaining_budget = budget_seconds - (clock() - started)
                if remaining_budget <= 0:
                    budget_exhausted = True
                    break
            try:
                fetch_awaitable = fetch_one(code)
                if not inspect.isawaitable(fetch_awaitable):
                    raise TypeError("fetch_one must return an awaitable")
                returned_hash = await asyncio.wait_for(fetch_awaitable, timeout=remaining_budget)
                elapsed_after_fetch = clock() - started
                if elapsed_after_fetch > budget_seconds:
                    message = (
                        "TimeoutError: fetch returned after continuation budget "
                        f"({elapsed_after_fetch:.3f}s)"
                    )[:500]
                    failed.append((code, message))
                    failed_by_code[code] = message
                    budget_exhausted = True
                    break
                if returned_hash is not None:
                    returned_hash = _validate_sha256(returned_hash)
            except TimeoutError:
                message = (
                    f"TimeoutError: fetch exceeded remaining budget ({remaining_budget:.3f}s)"[:500]
                )
                failed.append((code, message))
                failed_by_code[code] = message
                budget_exhausted = True
                break
            except Exception as exc:  # noqa: BLE001 - error is persisted as factual evidence
                message = f"{type(exc).__name__}: {exc}"[:500]
                failed.append((code, message))
                failed_by_code[code] = message
            else:
                completed.append(code)
                completed_codes.add(code)
                if returned_hash is not None:
                    completed_hashes_by_code[code] = returned_hash
                failed_by_code.pop(code, None)
            if code not in retry_codes:
                last_cursor = code
        elapsed = clock() - started
        stop_reason = (
            "budget_exhausted" if budget_exhausted or elapsed >= budget_seconds else "page_complete"
        )
        next_size = adaptive_batch_size(
            requested=checkpoint.batch_size,
            elapsed_seconds=elapsed / max(1, len(page)),
        )
        failed_codes = tuple(sorted(failed_by_code.items()))
        remaining = any(
            code not in completed_codes and code not in failed_by_code for code in normalized
        )
        status = (
            "complete"
            if not remaining and not failed_codes
            else "partial"
            if not remaining
            else "paused"
        )
        return V2CollectorBatch(
            completed=tuple(dict.fromkeys(completed)),
            failed=tuple(failed),
            checkpoint=V2CollectorCheckpoint(
                cursor=last_cursor,
                batch_size=next_size,
                completed_codes=tuple(dict.fromkeys(completed)),
                status=status,
                failed_codes=failed_codes,
                error_summary=failed_codes[0][1] if failed_codes else None,
                manifest_hash=checkpoint.manifest_hash,
                completed_hashes=tuple(sorted(completed_hashes_by_code.items())),
            ),
            elapsed_seconds=elapsed,
            stopped_reason=stop_reason,
        )


__all__ = [
    "MAX_CONTINUATION_SECONDS",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "V2CollectorBatch",
    "V2CollectorCheckpoint",
    "adaptive_batch_size",
    "ordered_page",
    "run_bounded_batch",
]
