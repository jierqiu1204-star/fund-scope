from __future__ import annotations

import sqlite3
from datetime import date

from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    HistoricalLeaderEvent,
)
from scripts.run_etf_leader_historical_backtest import (
    ARTIFACT_SCHEMA_VERSION,
    _init_store,
    _load_events,
    _persist_date_events,
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
