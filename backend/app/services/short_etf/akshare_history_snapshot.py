"""Bounded subprocess entry point for one AKShare ETF history request.

AKShare exposes the Eastmoney ETF history endpoint through a synchronous
function.  Keeping that call in a short-lived child process lets the async
caller cancel or time out the request without leaving a worker thread behind.
"""

from __future__ import annotations

import json
import signal
import sys
from datetime import date
from typing import Any

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
SUBPROCESS_TIMEOUT_SECONDS = 20


def _frame_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or not hasattr(frame, "to_json"):
        raise ValueError("akshare_etf_history_frame_invalid")
    serialized = frame.to_json(
        orient="records",
        date_format="iso",
        force_ascii=False,
    )
    if not isinstance(serialized, str):
        raise ValueError("akshare_etf_history_records_invalid")
    try:
        records = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise ValueError("akshare_etf_history_records_invalid") from exc
    if not isinstance(records, list):
        raise ValueError("akshare_etf_history_records_invalid")
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("akshare_etf_history_record_invalid")
    return records


def _start_timeout_alarm() -> bool:
    if not hasattr(signal, "SIGALRM"):
        return False

    def _timeout_handler(_signum: int, _frame: object) -> None:
        raise TimeoutError("akshare_etf_history_timeout")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(SUBPROCESS_TIMEOUT_SECONDS)
    return True


def _stop_timeout_alarm(installed: bool) -> None:
    if installed:
        signal.alarm(0)


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 3:
        print("akshare_etf_history_requires_symbol_dates", file=sys.stderr)
        return 2
    symbol, start_date, end_date = args
    if not symbol:
        print("akshare_etf_history_arguments_invalid", file=sys.stderr)
        return 2
    try:
        if date.fromisoformat(start_date) > date.fromisoformat(end_date):
            raise ValueError("date_range_invalid")
    except ValueError as exc:
        print(f"akshare_etf_history_arguments_invalid:{exc}", file=sys.stderr)
        return 2

    alarm_installed = _start_timeout_alarm()
    try:
        try:
            import akshare as ak  # isolated so a stuck provider can be killed by the parent

            raw_frame = ak.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="",
            )
            hfq_frame = ak.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="hfq",
            )
            payload = json.dumps(
                {
                    "raw_rows": _frame_records(raw_frame),
                    "hfq_rows": _frame_records(hfq_frame),
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except TimeoutError:
            print("akshare_etf_history_timeout", file=sys.stderr)
            return 124
        except Exception as exc:  # noqa: BLE001
            # Keep provider URLs and proxy details out of the parent error.
            print(f"akshare_etf_history_failed:{type(exc).__name__}", file=sys.stderr)
            return 1
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            print("akshare_etf_history_response_too_large", file=sys.stderr)
            return 3
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.write(b"\n")
        sys.stdout.flush()
        return 0
    finally:
        _stop_timeout_alarm(alarm_installed)


if __name__ == "__main__":
    raise SystemExit(main())
