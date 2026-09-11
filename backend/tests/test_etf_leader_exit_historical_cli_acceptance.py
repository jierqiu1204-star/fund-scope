"""Root acceptance for complete freezes, offline execution, and bounded workers."""

import json
import multiprocessing
import time
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from test_etf_leader_exit_historical_acceptance import END, FROZEN, START, _dataset

from app.services.strategy_lab.etf_leader_exit_historical_loader import (
    DATASET_NAMESPACE,
    dataset_from_json,
    dataset_to_json,
)
from scripts import run_etf_leader_exit_historical as cli


@pytest.fixture(autouse=True)
def _freeze_research_clock(monkeypatch):
    class ResearchClock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = FROZEN + timedelta(hours=1)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setattr(cli, "datetime", ResearchClock)


def _artifact_dataset(*, complete=True, omitted=False):
    data = _dataset()
    codes = [asset.asset_code for asset in data.assets]
    assets = data.assets[:-1] if omitted else data.assets
    return replace(data, assets=assets, source_hash="", source_manifest=(
        ("__config__", {"namespace": DATASET_NAMESPACE, "frozen_at": FROZEN.isoformat(),
                        "start_date": START.isoformat(), "end_date": END.isoformat(), "page_size": 16}),
        ("__pagination__", {"cursor": codes[-3], "last_code": codes[-1],
                            "next_cursor": None if complete else codes[-1],
                            "has_more": not complete, "load_complete": complete}),
        ("__universe__", {"codes": codes, "tradable_count": len(codes)}),
    ))


def _args(path, mode="--preflight"):
    return cli._arguments([mode, "--artifact", str(path), "--frozen-at", FROZEN.isoformat(),
                           "--start-date", START.isoformat(), "--end-date", END.isoformat()])


def _no_database():
    raise AssertionError("offline operation must not touch database settings")


async def test_existing_preflight_is_offline_and_does_not_rewrite_freeze(tmp_path, monkeypatch):
    path = tmp_path / "freeze.json"
    payload = dataset_to_json(_artifact_dataset())
    path.write_text(payload)
    monkeypatch.setattr(cli, "get_settings", _no_database)
    result = await cli._run(_args(path))
    assert result["status"] == "ready"
    assert result["asset_count"] == 18
    assert path.read_text() == payload


async def test_compare_is_structured_offline_and_preserves_frozen_artifact(tmp_path, monkeypatch):
    path = tmp_path / "freeze.json"
    payload = dataset_to_json(_artifact_dataset())
    path.write_text(payload)
    monkeypatch.setattr(cli, "get_settings", _no_database)
    result = await cli._run(_args(path, "--compare"))
    comparison = json.loads(json.dumps(result))["comparison"]
    assert len(comparison["policies"]) == 3
    assert all(isinstance(item["ledger"], dict) for item in comparison["policies"])
    assert comparison["formal_pit_credit"] == 0
    assert path.read_text() == payload


@pytest.mark.parametrize(("complete", "omitted"), [(False, False), (True, True)])
async def test_compare_rejects_incomplete_or_falsely_complete_denominator(tmp_path, monkeypatch, complete, omitted):
    path = tmp_path / "freeze.json"
    path.write_text(dataset_to_json(_artifact_dataset(complete=complete, omitted=omitted)))
    monkeypatch.setattr(cli, "get_settings", _no_database)
    with pytest.raises(ValueError):
        await cli._run(_args(path, "--compare"))


async def test_changed_cutoff_cannot_reuse_frozen_artifact(tmp_path, monkeypatch):
    path = tmp_path / "freeze.json"
    path.write_text(dataset_to_json(_artifact_dataset()))
    monkeypatch.setattr(cli, "get_settings", _no_database)
    arguments = _args(path)
    arguments.frozen_at += timedelta(minutes=1)
    with pytest.raises(ValueError):
        await cli._run(arguments)


async def test_future_cutoff_rejected_before_any_database_access(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", _no_database)
    arguments = _args(tmp_path / "absent.json")
    arguments.frozen_at += timedelta(days=365)
    with pytest.raises(ValueError):
        await cli._run(arguments)


def _large_worker(_artifact, result_queue):
    result_queue.put({"comparison": {"payload": "x" * 2_000_000}})


def _slow_worker(_artifact, _result_queue):
    time.sleep(10)


def test_worker_can_return_large_payload_without_queue_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_comparison_worker", _large_worker)
    result = cli._compare_with_timeout(tmp_path / "unused", 4)
    assert len(result["comparison"]["payload"]) == 2_000_000


def test_worker_timeout_leaves_no_orphan_process(tmp_path, monkeypatch):
    before = {process.pid for process in multiprocessing.active_children()}
    monkeypatch.setattr(cli, "_comparison_worker", _slow_worker)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        cli._compare_with_timeout(tmp_path / "unused", 0.1)
    assert time.monotonic() - start < 4
    assert {process.pid for process in multiprocessing.active_children()} <= before


def test_frozen_json_cannot_drop_hash_and_silently_become_a_new_dataset():
    raw = json.loads(dataset_to_json(_artifact_dataset()))
    raw.pop("source_hash")
    with pytest.raises(ValueError):
        dataset_from_json(json.dumps(raw))


async def test_manifest_cannot_claim_a_different_period_than_its_dataset(tmp_path, monkeypatch):
    data = _artifact_dataset()
    # Prices/calendar still cover the old start, but the formal ledger now starts a day later.
    changed = replace(data, start_date=START + timedelta(days=1), source_hash="")
    path = tmp_path / "freeze.json"
    path.write_text(dataset_to_json(changed))
    monkeypatch.setattr(cli, "get_settings", _no_database)
    with pytest.raises(ValueError):
        await cli._run(_args(path, "--compare"))
