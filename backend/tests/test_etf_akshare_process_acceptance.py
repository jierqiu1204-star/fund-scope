"""Exercise the real child-process boundary without making network requests."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import date
from pathlib import Path

import pytest

from app.services.short_etf import data
from app.services.short_etf.akshare_history_snapshot import _frame_records


@pytest.mark.asyncio
async def test_recorded_real_akshare_frames_preserve_raw_prices_and_adjusted_basis(monkeypatch):
    import pandas as pd

    fixture = json.loads((Path(__file__).parent / "fixtures" / "akshare_etf_510300_20260911.json").read_text())

    async def recorded_frames(*_args):
        return tuple(_frame_records(pd.DataFrame(fixture[key])) for key in ("raw_rows", "hfq_rows"))

    monkeypatch.setattr(data, "_fetch_akshare_history_records", recorded_frames)
    rows = await data.fetch_eastmoney_etf_price_history_akshare_once(
        fixture["code"], date(2026, 9, 9), date(2026, 9, 10)
    )
    assert len(rows) == 2
    for actual, raw, adjusted in zip(rows, fixture["raw_rows"], fixture["hfq_rows"], strict=True):
        assert actual["date"] == raw["日期"] == adjusted["日期"]
        for field, source in (("open", "开盘"), ("high", "最高"), ("low", "最低"), ("close", "收盘"), ("volume", "成交量"), ("turnover", "成交额"), ("pct_change", "涨跌幅")):
            assert actual[field] == raw[source]
        assert actual["research_adjusted_value"] == adjusted["收盘"]
        assert actual["provider_version"] == data.EASTMONEY_HFQ_ADJUSTMENT_VERSION


def _fake_provider(tmp_path, monkeypatch, *, hang=False):
    trace = tmp_path / "calls.jsonl"
    monkeypatch.setenv("ETF_REUSE_ACCEPTANCE_TRACE", str(trace))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(tmp_path), str(Path.cwd()))))
    (tmp_path / "akshare.py").write_text(
        "import json, os, time\n"
        "class Frame:\n"
        " def __init__(self, rows): self.rows = rows\n"
        " def to_json(self, **kwargs): return json.dumps(self.rows)\n"
        "def fund_etf_hist_em(**kwargs):\n"
        " with open(os.environ['ETF_REUSE_ACCEPTANCE_TRACE'], 'a') as f:\n"
        "  f.write(json.dumps({'pid': os.getpid(), **kwargs}) + '\\n')\n"
        + (" while True: time.sleep(1)\n" if hang else "")
        + " adjusted = kwargs['adjust'] == 'hfq'\n"
        " rows = []\n"
        " for day in ('2026-09-08', '2026-09-09', '2026-09-10'):\n"
        "  if adjusted and day == '2026-09-09': continue\n"
        "  rows.append({'日期': day, '开盘': 1.2, '最高': 1.4, '最低': 1.1,\n"
        "   '收盘': 2.5 if adjusted else 1.25, '成交量': 1234,\n"
        "   '成交额': 154250, '涨跌幅': 3.0 if adjusted else 1.0})\n"
        " return Frame(rows)\n"
    )
    return trace


@pytest.mark.asyncio
async def test_real_child_fetches_both_price_bases_once_without_filling_missing_hfq(tmp_path, monkeypatch):
    trace = _fake_provider(tmp_path, monkeypatch)
    rows = await data.fetch_eastmoney_etf_price_history_akshare_once(
        "510300", date(2026, 9, 9), date(2026, 9, 10)
    )
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(calls) == 2
    assert len({call["pid"] for call in calls}) == 1
    assert [call["adjust"] for call in calls] == ["", "hfq"]
    assert all(call["symbol"] == "510300" for call in calls)
    assert [row["date"] for row in rows] == ["2026-09-09", "2026-09-10"]
    assert rows[0].get("research_adjusted_value") is None
    assert rows[1]["close"] == 1.25
    assert rows[1]["research_adjusted_value"] == 2.5
    assert rows[1]["pct_change"] == 1.0
    assert rows[1]["volume"] == 1234
    assert rows[1]["turnover"] == 154250
    assert rows[1]["provider_version"] == data.EASTMONEY_HFQ_ADJUSTMENT_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [True, False])
async def test_real_provider_process_is_reaped_after_cancellation_or_timeout(tmp_path, monkeypatch, cancel):
    trace = _fake_provider(tmp_path, monkeypatch, hang=True)
    monkeypatch.setattr(data, "ETF_HISTORY_PROVIDER_TIMEOUT_SECONDS", 3 if cancel else 0.6)
    started = time.monotonic()
    task = asyncio.create_task(data.fetch_eastmoney_etf_price_history_akshare_once(
        "510300", date(2026, 9, 9), date(2026, 9, 10)
    ))
    async with asyncio.timeout(3):
        while not trace.exists():  # noqa: ASYNC110 - the OS child signals readiness through a file
            await asyncio.sleep(0.01)
        pid = json.loads(trace.read_text().splitlines()[0])["pid"]
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
    assert time.monotonic() - started < 3
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
