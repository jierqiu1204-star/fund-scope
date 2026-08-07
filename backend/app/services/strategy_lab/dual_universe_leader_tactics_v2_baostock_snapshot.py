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
from typing import Any

import baostock as bs

MAX_SNAPSHOT_SECONDS = 15
MAX_INDUSTRY_ROWS = 6_000


class BaoStockSnapshotError(RuntimeError):
    """Raised when the synchronous provider snapshot is incomplete."""


def _normalise_symbol(value: object) -> str:
    if not isinstance(value, str):
        raise BaoStockSnapshotError("industry_symbol_invalid")
    prefix, separator, code = value.strip().partition(".")
    if separator != "." or prefix not in {"sh", "sz"} or len(code) != 6 or not code.isdigit():
        raise BaoStockSnapshotError("industry_symbol_invalid")
    return f"{code}.{prefix.upper()}"


def load_current_industries() -> dict[str, str]:
    """Return the current BaoStock industry map without historical inference."""

    provider_output = io.StringIO()
    with contextlib.redirect_stdout(provider_output):
        login = bs.login()
    if login.error_code != "0":
        raise BaoStockSnapshotError(f"login_failed:{login.error_code}")

    industries: dict[str, str] = {}
    try:
        query = bs.query_stock_industry()
        if query.error_code != "0":
            raise BaoStockSnapshotError(f"industry_query_failed:{query.error_code}")
        while query.next():
            row: list[Any] = query.get_row_data()
            # BaoStock fields are updateDate, code, code_name, industry,
            # industryClassification.  The update date is intentionally not
            # treated as a historical effective date: this is a current
            # snapshot whose visibility starts at the caller's receipt time.
            if len(row) < 4:
                raise BaoStockSnapshotError("industry_row_incomplete")
            symbol = _normalise_symbol(row[1])
            industry = str(row[3] or "").strip()
            if not industry or len(industry) > 128:
                continue
            previous = industries.setdefault(symbol, industry)
            if previous != industry:
                raise BaoStockSnapshotError(f"industry_conflict:{symbol}")
            if len(industries) > MAX_INDUSTRY_ROWS:
                raise BaoStockSnapshotError("industry_row_limit_exceeded")
    finally:
        with contextlib.redirect_stdout(provider_output):
            bs.logout()
    if not industries:
        raise BaoStockSnapshotError("industry_snapshot_empty")
    return industries


def main() -> None:
    signal.alarm(MAX_SNAPSHOT_SECONDS)
    print(json.dumps(load_current_industries(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
