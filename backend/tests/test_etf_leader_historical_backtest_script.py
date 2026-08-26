from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    HistoricalLeaderEvent,
)
from scripts.run_etf_leader_historical_backtest import (
    ARTIFACT_SCHEMA_VERSION,
    _fold_dates,
    _init_store,
    _initialize_source,
    _load_events,
    _meta,
    _persist_date_events,
    _policy_sort_key,
    _set_meta,
)


def test_old_checkpoint_keeps_market_data_but_rebuilds_events(tmp_path) -> None:
    path = tmp_path / "checkpoint.sqlite3"
    store = sqlite3.connect(path)
    store.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    store.execute(
        "INSERT INTO meta(key,value) VALUES('artifact_schema_version','leader_historical_backtest_checkpoint_v1')"
    )
    store.execute("CREATE TABLE events (legacy TEXT)")
    store.execute(
        "CREATE TABLE evaluated_dates (signal_date TEXT PRIMARY KEY, event_count INTEGER, candidate_count INTEGER)"
    )
    store.execute("INSERT INTO evaluated_dates VALUES('2026-08-01',1,1)")
    store.commit()
    store.close()

    migrated = _init_store(path)
    try:
        version = migrated.execute(
            "SELECT value FROM meta WHERE key='artifact_schema_version'"
        ).fetchone()[0]
        columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(events)").fetchall()
        }
        evaluated = migrated.execute("SELECT count(*) FROM evaluated_dates").fetchone()[0]
    finally:
        migrated.close()

    assert version == ARTIFACT_SCHEMA_VERSION
    assert {"trade_status", "exit_signal_date", "exit_reason"} <= columns
    assert evaluated == 0


def test_checkpoint_round_trips_unique_lifecycle_event(tmp_path) -> None:
    store = _init_store(tmp_path / "checkpoint.sqlite3")
    event = HistoricalLeaderEvent(
        signal_date=date(2026, 8, 1),
        candidate_id="leader_breakout_proxy_v1",
        asset_code="510300",
        name="沪深300ETF",
        peer_group="宽基",
        score=0.9,
        feature_hash="a" * 64,
        trade_status="closed",
        confirmation_date=date(2026, 8, 2),
        entry_date=date(2026, 8, 3),
        entry_price=10.0,
        exit_signal_date=date(2026, 8, 4),
        exit_date=date(2026, 8, 5),
        exit_price=10.5,
        exit_reason="leader_tactics_ma5_exit",
        holding_sessions=2,
        gross_return=0.05,
        net_return=0.05,
        peer_net_return=0.01,
        net_excess_return=0.04,
        entry_quality_state="disciplined",
        entry_quality_reason_codes=(),
        next_session_confirmation_state="confirmed",
        next_session_confirmation_reason_codes=(),
    )
    try:
        _persist_date_events(store, event.signal_date, (event,))
        loaded = _load_events(store)
    finally:
        store.close()

    assert loaded == (event,)


def test_policy_screen_prefers_stable_folds_then_simpler_cooldown() -> None:
    common = {
        "screen_eligible": True,
        "positive_fold_count": 2,
        "median_fold_mean_return": 0.01,
        "worst_fold_mean_return": -0.01,
        "event_series_max_drawdown": -0.08,
        "take_profit_return": 0.03,
    }
    rows = [
        {**common, "cooldown_sessions": 3},
        {**common, "cooldown_sessions": 0},
        {
            **common,
            "cooldown_sessions": 1,
            "positive_fold_count": 3,
            "median_fold_mean_return": 0.005,
        },
    ]

    ranked = sorted(rows, key=_policy_sort_key)

    assert ranked[0]["cooldown_sessions"] == 1
    assert ranked[1]["cooldown_sessions"] == 0


def test_fold_dates_are_contiguous_and_use_confirmed_signals() -> None:
    event = HistoricalLeaderEvent(
        signal_date=date(2026, 1, 1),
        candidate_id="leader_breakout_proxy_v1",
        asset_code="510300",
        name="沪深300ETF",
        peer_group="宽基",
        score=0.9,
        feature_hash="a" * 64,
        trade_status="closed",
        confirmation_date=date(2026, 1, 2),
        entry_date=date(2026, 1, 3),
        entry_price=10.0,
        exit_signal_date=date(2026, 1, 4),
        exit_date=date(2026, 1, 5),
        exit_price=10.5,
        exit_reason="leader_tactics_ma5_exit",
        holding_sessions=2,
        gross_return=0.05,
        net_return=0.05,
        peer_net_return=0.01,
        net_excess_return=0.04,
        entry_quality_state="disciplined",
        entry_quality_reason_codes=(),
        next_session_confirmation_state="confirmed",
        next_session_confirmation_reason_codes=(),
    )
    events = tuple(
        replace(
            event,
            signal_date=date(2026, 1, day),
            feature_hash=f"{day:064d}",
        )
        for day in range(1, 7)
    )

    assert _fold_dates(events) == (
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 3), date(2026, 1, 4)),
        (date(2026, 1, 5), date(2026, 1, 6)),
    )


@pytest.mark.asyncio
async def test_source_uses_latest_published_full_scope_snapshot(tmp_path) -> None:
    store = _init_store(tmp_path / "checkpoint.sqlite3")
    run = SimpleNamespace(
        id=149,
        publication_state="published",
        as_of_trade_date=date(2026, 8, 24),
        ranking_contract_hash="ranking-hash",
        input_snapshot_hash="input-hash",
    )
    session = SimpleNamespace(scalar=AsyncMock(side_effect=(run, 1441)))
    try:
        await _initialize_source(store, session)

        assert _meta(store, "source_run_id") == "149"
        assert _meta(store, "source_signal_date") == "2026-08-24"
        assert _meta(store, "source_ranked_asset_count") == "1441"
        assert _meta(store, "stage") == "load"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_completed_checkpoint_rebases_to_newer_source(tmp_path) -> None:
    store = _init_store(tmp_path / "checkpoint.sqlite3")
    with store:
        _set_meta(store, "source_run_id", 121)
        _set_meta(store, "source_signal_date", "2026-07-31")
        _set_meta(store, "stage", "complete")
        store.execute(
            "INSERT INTO assets VALUES(?,?,?,?,?,?,?)",
            ("510300", "沪深300ETF", "宽基", "510300", 0.8, "available", None),
        )
        store.execute(
            "INSERT INTO evaluated_dates VALUES(?,?,?)",
            ("2026-07-29", 1, 1),
        )
    run = SimpleNamespace(
        id=149,
        publication_state="published",
        as_of_trade_date=date(2026, 8, 24),
        ranking_contract_hash="new-ranking-hash",
        input_snapshot_hash="new-input-hash",
    )
    session = SimpleNamespace(scalar=AsyncMock(side_effect=(run, 1441)))
    try:
        await _initialize_source(store, session)

        assert _meta(store, "source_run_id") == "149"
        assert store.execute("SELECT count(*) FROM assets").fetchone()[0] == 0
        assert store.execute("SELECT count(*) FROM evaluated_dates").fetchone()[0] == 0
        assert _meta(store, "stage") == "load"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_partial_checkpoint_keeps_frozen_source(tmp_path) -> None:
    store = _init_store(tmp_path / "checkpoint.sqlite3")
    session = SimpleNamespace(scalar=AsyncMock())
    with store:
        _set_meta(store, "source_run_id", 121)
        _set_meta(store, "stage", "evaluate")
        store.execute(
            "INSERT INTO evaluated_dates VALUES(?,?,?)",
            ("2026-07-29", 1, 1),
        )
    try:
        await _initialize_source(store, session)

        assert _meta(store, "source_run_id") == "121"
        session.scalar.assert_not_awaited()
    finally:
        store.close()
