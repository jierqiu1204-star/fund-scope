from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.short_etf import akshare_history_snapshot, data


def _record(close: float) -> dict[str, object]:
    return {
        "日期": "2026-07-10",
        "开盘": close,
        "最高": close,
        "最低": close,
        "收盘": close,
        "成交量": 100.0,
        "成交额": 200.0,
        "涨跌幅": 0.0,
    }


class _CompletedProcess:
    def __init__(self, payload: dict[str, object]) -> None:
        self.returncode = 0
        self.stdout = json.dumps(payload).encode()
        self.stderr = b""
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return int(self.returncode)


class _HangingProcess:
    returncode = None

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = -9
        return -9


def test_akshare_frame_records_use_native_json_serialization() -> None:
    class _Frame:
        def to_json(
            self,
            *,
            orient: str,
            date_format: str,
            force_ascii: bool,
        ) -> str:
            assert orient == "records"
            assert date_format == "iso"
            assert force_ascii is False
            return '[{"日期":"2026-07-10","收盘":null}]'

    assert akshare_history_snapshot._frame_records(_Frame()) == [
        {"日期": "2026-07-10", "收盘": None}
    ]


def test_akshare_snapshot_requests_raw_and_hfq_in_one_child(monkeypatch, capsys) -> None:
    calls: list[str] = []

    class _Frame:
        def __init__(self, close: float) -> None:
            self.close = close

        def to_json(
            self,
            *,
            orient: str,
            date_format: str,
            force_ascii: bool,
        ) -> str:
            assert orient == "records"
            assert date_format == "iso"
            assert force_ascii is False
            return json.dumps([_record(self.close)])

    def fetch(**kwargs: object) -> _Frame:
        adjust = str(kwargs["adjust"])
        calls.append(adjust)
        return _Frame(1.0 if adjust == "" else 2.0)

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_etf_hist_em=fetch),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "akshare-history-snapshot",
            "159605",
            "2026-07-10",
            "2026-07-10",
        ],
    )

    assert akshare_history_snapshot.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == ["", "hfq"]
    assert payload["raw_rows"][0]["收盘"] == 1.0
    assert payload["hfq_rows"][0]["收盘"] == 2.0


@pytest.mark.asyncio
async def test_akshare_history_fetches_raw_then_hfq_and_aligns_by_date(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    async def spawn(*args: object, **kwargs: object) -> _CompletedProcess:
        calls.append(args)
        assert kwargs["stdout"] is asyncio.subprocess.PIPE
        assert kwargs["stderr"] is asyncio.subprocess.PIPE
        return _CompletedProcess(
            {
                "raw_rows": [_record(1.0)],
                "hfq_rows": [_record(2.0)],
            }
        )

    monkeypatch.setattr(data.asyncio, "create_subprocess_exec", spawn)

    rows = await data.fetch_eastmoney_etf_price_history_akshare_once(
        "159605", date(2026, 7, 10), date(2026, 7, 10)
    )

    assert [call[0] for call in calls] == [sys.executable]
    assert [call[3:] for call in calls] == [
        ("159605", "2026-07-10", "2026-07-10"),
    ]
    assert rows[0]["close"] == 1.0
    assert rows[0]["research_adjusted_value"] == 2.0
    assert rows[0]["research_price_basis"] == data.TOTAL_RETURN_PRICE_BASIS
    assert rows[0]["adjustment_version"] == data.EASTMONEY_HFQ_ADJUSTMENT_VERSION
    assert rows[0]["provider_version"] == data.EASTMONEY_HFQ_ADJUSTMENT_VERSION


def test_akshare_history_parser_rejects_incomplete_raw_ohlcv() -> None:
    record = _record(1.0)
    del record["成交额"]
    with pytest.raises(ValueError, match="etf_history_record_incomplete"):
        data.parse_etf_history_records(
            [record],
            date(2026, 7, 10),
            date(2026, 7, 10),
            require_complete=True,
        )


@pytest.mark.asyncio
async def test_akshare_history_timeout_kills_child(monkeypatch) -> None:
    process = _HangingProcess()

    async def spawn(*_args: object, **_kwargs: object) -> _HangingProcess:
        return process

    monkeypatch.setattr(data.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(data, "ETF_HISTORY_PROVIDER_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError, match="eastmoney_akshare_history_timeout"):
        await data._fetch_akshare_history_records(
            "159605", date(2026, 7, 10), date(2026, 7, 10)
        )
    assert process.killed is True


@pytest.mark.asyncio
async def test_akshare_history_external_cancellation_kills_child(monkeypatch) -> None:
    process = _HangingProcess()

    async def spawn(*_args: object, **_kwargs: object) -> _HangingProcess:
        return process

    monkeypatch.setattr(data.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        data._fetch_akshare_history_records(
            "159605", date(2026, 7, 10), date(2026, 7, 10)
        )
    )
    await asyncio.wait_for(process.started.wait(), timeout=0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True
