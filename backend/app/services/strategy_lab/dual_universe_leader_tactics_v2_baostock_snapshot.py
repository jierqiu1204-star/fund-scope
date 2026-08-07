"""Small subprocess entry point for one bounded BaoStock industry snapshot.

BaoStock is a synchronous socket client.  The async capture workflow launches
this module in a child process so a timeout can terminate provider work
physically instead of leaving a blocking call alive in the scheduler process.
Only current industry labels are returned; callers must stamp their factual
receipt time and must not use this snapshot for an earlier cutoff.
"""

from __future__ import annotations

import contextlib
import io
import json
import signal
import sys
from collections.abc import Callable, Sequence
from typing import Any

import baostock as bs

MAX_SNAPSHOT_SECONDS = 24
MAX_BATCH_SIZE = 20


class BaoStockSnapshotError(RuntimeError):
    """Raised when the synchronous provider snapshot is incomplete."""


def _normalise_symbol(value: object) -> tuple[str, str | None]:
    if not isinstance(value, str):
        raise BaoStockSnapshotError("industry_symbol_invalid")
    code, separator, exchange = value.strip().upper().partition(".")
    if (
        separator != "."
        or exchange not in {"SH", "SZ", "BJ"}
        or len(code) != 6
        or not code.isdigit()
    ):
        raise BaoStockSnapshotError("industry_symbol_invalid")
    provider_code = None if exchange == "BJ" else f"{exchange.lower()}.{code}"
    return f"{code}.{exchange}", provider_code


def load_current_industries(
    symbols: Sequence[str],
    *,
    on_completed: Callable[[str, str | None], None] | None = None,
) -> dict[str, str]:
    """Return one bounded current BaoStock industry page.

    The caller owns the durable cursor.  Beijing-exchange symbols are accepted
    but skipped because BaoStock does not expose them through this interface.
    """

    requested = tuple(symbols)
    if not 1 <= len(requested) <= MAX_BATCH_SIZE:
        raise BaoStockSnapshotError("industry_batch_size_invalid")
    normalized = tuple(_normalise_symbol(symbol) for symbol in requested)
    if len({symbol for symbol, _ in normalized}) != len(normalized):
        raise BaoStockSnapshotError("industry_batch_duplicate_symbol")

    provider_output = io.StringIO()
    with contextlib.redirect_stdout(provider_output):
        login = bs.login()
    if login.error_code != "0":
        raise BaoStockSnapshotError(f"login_failed:{login.error_code}")

    industries: dict[str, str] = {}
    try:
        for requested_symbol, provider_code in normalized:
            if provider_code is None:
                if on_completed is not None:
                    on_completed(requested_symbol, None)
                continue
            query = bs.query_stock_industry(code=provider_code)
            if query.error_code != "0":
                raise BaoStockSnapshotError(
                    f"industry_query_failed:{requested_symbol}:{query.error_code}"
                )
            observed_industry: str | None = None
            while query.next():
                row: list[Any] = query.get_row_data()
                # BaoStock fields are updateDate, code, code_name, industry,
                # industryClassification. The update date is deliberately not
                # backdated into a PIT effective timestamp.
                if len(row) < 4:
                    raise BaoStockSnapshotError("industry_row_incomplete")
                row_symbol, _ = _normalise_symbol(
                    f"{str(row[1]).split('.')[-1]}.{str(row[1]).split('.')[0]}"
                )
                if row_symbol != requested_symbol:
                    raise BaoStockSnapshotError("industry_symbol_mismatch")
                industry = str(row[3] or "").strip()
                if not industry or len(industry) > 128:
                    continue
                previous = industries.setdefault(row_symbol, industry)
                if previous != industry:
                    raise BaoStockSnapshotError(f"industry_conflict:{row_symbol}")
                observed_industry = industry
            if on_completed is not None:
                on_completed(requested_symbol, observed_industry)
    finally:
        with contextlib.redirect_stdout(provider_output):
            bs.logout()
    return industries


def main() -> None:
    signal.alarm(MAX_SNAPSHOT_SECONDS)

    def emit_completed(symbol: str, industry: str | None) -> None:
        # One flushed record per completed symbol preserves factual progress if
        # a later BaoStock query hangs until the subprocess alarm fires.
        print(
            json.dumps(
                {"symbol": symbol, "industry": industry},
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    load_current_industries(sys.argv[1:], on_completed=emit_completed)


if __name__ == "__main__":
    main()
