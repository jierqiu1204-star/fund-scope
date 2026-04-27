from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    operation_name: str,
    func: Callable[[], Awaitable[T]],
    retries: int = 2,
    base_delay: float = 0.2,
) -> T:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("external_call_failed", extra={"operation": operation_name, "attempt": attempt + 1})
            if attempt >= retries:
                break
            await asyncio.sleep(base_delay * (2**attempt))
    assert last_error is not None
    raise last_error
