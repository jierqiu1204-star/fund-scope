from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import DatabaseManager
from app.models.entities import (
    EtfFactorExperimentEvidence,
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_evidence import persist_factor_evidence
from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    HISTORICAL_BACKTEST_CONTRACT_HASH,
    LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
    LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_BACKTEST_NOT_PIT,
    LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
    LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
    HistoricalLeaderAsset,
    HistoricalLeaderBar,
    HistoricalLeaderEvent,
    build_historical_backtest_evidence,
    eligible_historical_signal_dates,
    evaluate_historical_signal_date,
    stable_event_payloads,
    summarize_historical_events,
)
from app.services.strategy_lab.etf_leader_tactics_historical_proxy import (
    LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
)

DEFAULT_ARTIFACT = Path(
    "/app/data/etf-leader-tactics-artifacts/historical-backtest-v1.sqlite3"
)
UNCLASSIFIED_PEER_GROUPS = {
    "unknown",
    "other",
    "unclassified",
    "未知",
    "其他",
    "未分类",
}
FORBIDDEN_PROVIDERS = {"sina", "efinance"}
ARTIFACT_SCHEMA_VERSION = "leader_historical_backtest_checkpoint_v2"

EVENTS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS events (
        signal_date TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        asset_code TEXT NOT NULL,
        name TEXT,
        peer_group TEXT NOT NULL,
        score REAL NOT NULL,
        feature_hash TEXT NOT NULL,
        trade_status TEXT NOT NULL,
        confirmation_date TEXT,
        entry_date TEXT,
        entry_price REAL,
        exit_signal_date TEXT,
        exit_date TEXT,
        exit_price REAL,
        exit_reason TEXT,
        holding_sessions INTEGER,
        gross_return REAL,
        net_return REAL,
        peer_net_return REAL,
        net_excess_return REAL,
        entry_quality_state TEXT,
        entry_quality_reason_codes TEXT NOT NULL,
        next_session_confirmation_state TEXT,
        next_session_confirmation_reason_codes TEXT NOT NULL,
        PRIMARY KEY(signal_date, candidate_id, asset_code)
    )
"""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continue the bounded ETF leader historical proxy backtest."
    )
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--page-size", type=int, default=120)
    parser.add_argument("--runtime-seconds", type=float, default=38.0)
    parser.add_argument("--code-version", required=True)
    return parser.parse_args()


def _require_server_database(database_url: str) -> None:
    url = make_url(database_url)
    host = (url.host or "").lower()
    if url.get_backend_name() == "sqlite" or host in {
        "",
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise RuntimeError(
            "historical backtest requires the server database; local targets are refused"
        )


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _init_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = sqlite3.connect(path)
    store.execute("PRAGMA journal_mode=WAL")
    store.execute("PRAGMA synchronous=NORMAL")
    store.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    store.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            asset_code TEXT PRIMARY KEY,
            name TEXT,
            peer_group TEXT,
            clone_group TEXT NOT NULL,
            baseline_score REAL,
            status TEXT NOT NULL,
            exclusion_reason TEXT
        )
        """
    )
    store.execute(
        """
        CREATE TABLE IF NOT EXISTS bars (
            asset_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            adjusted_open REAL NOT NULL,
            adjusted_high REAL NOT NULL,
            adjusted_low REAL NOT NULL,
            adjusted_close REAL NOT NULL,
            volume REAL NOT NULL,
            turnover REAL NOT NULL,
            PRIMARY KEY(asset_code, trade_date)
        )
        """
    )
    store.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluated_dates (
            signal_date TEXT PRIMARY KEY,
            event_count INTEGER NOT NULL,
            candidate_count INTEGER NOT NULL
        )
        """
    )
    store.execute(EVENTS_TABLE_SQL)
    store.execute(
        "CREATE INDEX IF NOT EXISTS ix_leader_backtest_bars_code_date ON bars(asset_code, trade_date)"
    )
    existing = store.execute(
        "SELECT value FROM meta WHERE key='artifact_schema_version'"
    ).fetchone()
    if existing is not None and existing[0] != ARTIFACT_SCHEMA_VERSION:
        with store:
            store.execute("DROP TABLE events")
            store.execute(EVENTS_TABLE_SQL)
            store.execute("DELETE FROM evaluated_dates")
            _set_meta(
                store,
                "stage",
                "evaluate" if _meta(store, "load_complete") == "true" else "load",
            )
    with store:
        store.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('artifact_schema_version',?)",
            (ARTIFACT_SCHEMA_VERSION,),
        )
        store.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('contract_hash',?)",
            (HISTORICAL_BACKTEST_CONTRACT_HASH,),
        )
    return store


def _meta(store: sqlite3.Connection, key: str) -> str | None:
    row = store.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row is not None else None


def _set_meta(store: sqlite3.Connection, key: str, value: object) -> None:
    store.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value))
    )


async def _initialize_source(store: sqlite3.Connection, session: Any) -> None:
    if _meta(store, "source_run_id") is not None:
        return
    proxy_row = await session.scalar(
        select(EtfFactorExperimentEvidence)
        .where(
            EtfFactorExperimentEvidence.experiment_family
            == LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY
        )
        .order_by(
            EtfFactorExperimentEvidence.created_at.desc(),
            EtfFactorExperimentEvidence.id.desc(),
        )
        .limit(1)
    )
    source_run_id = None
    if proxy_row is not None:
        source_run_id = _mapping(proxy_row.report_json).get("signal_run_id")
    run = await session.get(ShortResearchSignalRun, int(source_run_id)) if source_run_id else None
    if run is None:
        run = await session.scalar(
            select(ShortResearchSignalRun)
            .where(
                ShortResearchSignalRun.publication_state == "published",
                ShortResearchSignalRun.scope_kind == "all",
                ShortResearchSignalRun.price_basis == "total_return_adjusted",
            )
            .order_by(
                ShortResearchSignalRun.as_of_trade_date.desc(),
                ShortResearchSignalRun.id.desc(),
            )
            .limit(1)
        )
    if (
        run is None
        or run.publication_state != "published"
        or run.as_of_trade_date is None
        or not run.ranking_contract_hash
        or not run.input_snapshot_hash
    ):
        raise RuntimeError("sealed_source_run_unavailable")
    source_count = await session.scalar(
        select(func.count(ShortResearchSignalItem.id)).where(
            ShortResearchSignalItem.run_id == run.id,
            ShortResearchSignalItem.asset_type == "etf",
            ShortResearchSignalItem.score_eligible.is_(True),
        )
    )
    with store:
        _set_meta(store, "source_run_id", run.id)
        _set_meta(store, "source_signal_date", run.as_of_trade_date.isoformat())
        _set_meta(store, "source_ranking_contract_hash", run.ranking_contract_hash)
        _set_meta(store, "source_input_snapshot_hash", run.input_snapshot_hash)
        _set_meta(store, "source_ranked_asset_count", int(source_count or 0))
        _set_meta(store, "stage", "load")


def _asset_metadata(item: Any, etf: Any) -> tuple[Any, ...]:
    metrics = _mapping(item.metrics_json)
    rationale = _mapping(item.rationale_json)
    profile = _mapping(metrics.get("theme_profile") or rationale.get("theme_profile"))
    v3 = _mapping(metrics.get("v3_input_values"))
    tracked = _text(
        metrics.get("tracked_underlying_id"), v3.get("tracked_underlying_id")
    )
    peer_group = _text(
        metrics.get("theme_group"),
        v3.get("theme_group"),
        profile.get("theme_group"),
        profile.get("primary_theme"),
        tracked,
    )
    reason = None
    if peer_group is None:
        reason = "current_vintage_peer_group_unavailable"
    elif peer_group.lower() in UNCLASSIFIED_PEER_GROUPS:
        reason = "unclassified_peer_group"
    return (
        item.asset_code,
        etf.name,
        peer_group,
        tracked or item.asset_code,
        _finite(item.ranking_score),
        "available" if reason is None else "excluded",
        reason,
    )


async def _load_page(
    store: sqlite3.Connection, session: Any, *, page_size: int
) -> dict[str, Any]:
    source_run_id = int(_meta(store, "source_run_id") or 0)
    source_date = date.fromisoformat(str(_meta(store, "source_signal_date")))
    last_code = _meta(store, "load_last_code") or ""
    source_rows = (
        await session.execute(
            select(ShortResearchSignalItem, TradableEtf)
            .join(TradableEtf, TradableEtf.code == ShortResearchSignalItem.asset_code)
            .where(
                ShortResearchSignalItem.run_id == source_run_id,
                ShortResearchSignalItem.asset_type == "etf",
                ShortResearchSignalItem.score_eligible.is_(True),
                ShortResearchSignalItem.asset_code > last_code,
            )
            .order_by(ShortResearchSignalItem.asset_code.asc())
            .limit(page_size)
        )
    ).all()
    if not source_rows:
        with store:
            _set_meta(store, "stage", "evaluate")
            _set_meta(store, "load_complete", "true")
        return {"status": "load_complete", "processed": _asset_count(store)}

    codes = tuple(item.asset_code for item, _etf in source_rows)
    history_rows = (
        await session.execute(
            select(
                EtfPriceHistory.etf_code,
                EtfPriceHistory.trade_date,
                EtfPriceHistory.open,
                EtfPriceHistory.high,
                EtfPriceHistory.low,
                EtfPriceHistory.close,
                EtfPriceHistory.volume,
                EtfPriceHistory.turnover,
                EtfPriceHistory.research_adjusted_value,
                EtfPriceHistory.data_provider,
                EtfPriceHistory.provider_version,
                EtfPriceHistory.adjustment_version,
            )
            .where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date <= source_date,
                EtfPriceHistory.decision_eligible.is_(True),
                EtfPriceHistory.research_price_basis == "total_return_adjusted",
                func.lower(func.coalesce(EtfPriceHistory.data_provider, "")).not_in(
                    tuple(FORBIDDEN_PROVIDERS)
                ),
            )
            .order_by(EtfPriceHistory.etf_code, EtfPriceHistory.trade_date)
        )
    ).all()
    valid_bars: list[tuple[Any, ...]] = []
    invalid_by_code: Counter[str] = Counter()
    for row in history_rows:
        raw_close = _finite(row.close)
        adjusted_close = _finite(row.research_adjusted_value)
        raw_open = _finite(row.open)
        raw_high = _finite(row.high)
        raw_low = _finite(row.low)
        volume = _finite(row.volume)
        turnover = _finite(row.turnover)
        if (
            raw_close is None
            or raw_close <= 0
            or adjusted_close is None
            or adjusted_close <= 0
            or raw_open is None
            or raw_high is None
            or raw_low is None
            or volume is None
            or turnover is None
            or volume < 0
            or turnover < 0
            or not row.data_provider
            or not row.provider_version
            or not row.adjustment_version
        ):
            invalid_by_code[str(row.etf_code)] += 1
            continue
        factor = adjusted_close / raw_close
        adjusted_open = raw_open * factor
        adjusted_high = raw_high * factor
        adjusted_low = raw_low * factor
        if adjusted_high < max(adjusted_open, adjusted_close) or adjusted_low > min(
            adjusted_open, adjusted_close
        ):
            invalid_by_code[str(row.etf_code)] += 1
            continue
        valid_bars.append(
            (
                str(row.etf_code),
                row.trade_date.isoformat(),
                adjusted_open,
                adjusted_high,
                adjusted_low,
                adjusted_close,
                volume,
                turnover,
            )
        )
    with store:
        store.executemany(
            """
            INSERT OR REPLACE INTO assets(
                asset_code,name,peer_group,clone_group,baseline_score,status,exclusion_reason
            ) VALUES(?,?,?,?,?,?,?)
            """,
            [_asset_metadata(item, etf) for item, etf in source_rows],
        )
        store.executemany(
            """
            INSERT OR REPLACE INTO bars(
                asset_code,trade_date,adjusted_open,adjusted_high,adjusted_low,
                adjusted_close,volume,turnover
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            valid_bars,
        )
        _set_meta(store, "load_last_code", source_rows[-1][0].asset_code)
        invalid_total = int(_meta(store, "invalid_adjusted_row_count") or 0)
        _set_meta(
            store,
            "invalid_adjusted_row_count",
            invalid_total + sum(invalid_by_code.values()),
        )
    return {
        "status": "partial_load",
        "processed": _asset_count(store),
        "last_code": source_rows[-1][0].asset_code,
        "page_size": len(source_rows),
        "valid_bar_rows": len(valid_bars),
        "invalid_bar_rows": sum(invalid_by_code.values()),
    }


def _asset_count(store: sqlite3.Connection) -> int:
    return int(store.execute("SELECT count(*) FROM assets").fetchone()[0])


def _load_assets(store: sqlite3.Connection) -> tuple[HistoricalLeaderAsset, ...]:
    bars: dict[str, list[HistoricalLeaderBar]] = defaultdict(list)
    for row in store.execute(
        """
        SELECT asset_code,trade_date,adjusted_open,adjusted_high,adjusted_low,
               adjusted_close,volume,turnover
        FROM bars ORDER BY asset_code,trade_date
        """
    ):
        bars[str(row[0])].append(
            HistoricalLeaderBar(
                trade_date=date.fromisoformat(str(row[1])),
                adjusted_open=float(row[2]),
                adjusted_high=float(row[3]),
                adjusted_low=float(row[4]),
                adjusted_close=float(row[5]),
                volume=float(row[6]),
                turnover=float(row[7]),
            )
        )
    return tuple(
        HistoricalLeaderAsset(
            asset_code=str(row[0]),
            name=str(row[1]) if row[1] is not None else None,
            peer_group=str(row[2]),
            clone_group=str(row[3]),
            baseline_score=float(row[4]) if row[4] is not None else None,
            bars=tuple(bars.get(str(row[0]), ())),
        )
        for row in store.execute(
            """
            SELECT asset_code,name,peer_group,clone_group,baseline_score
            FROM assets WHERE status='available' ORDER BY asset_code
            """
        )
        if row[2]
    )


def _persist_date_events(
    store: sqlite3.Connection,
    signal_date: date,
    events: tuple[HistoricalLeaderEvent, ...],
) -> None:
    with store:
        store.executemany(
            """
            INSERT OR REPLACE INTO events(
                signal_date,candidate_id,asset_code,name,peer_group,score,feature_hash,
                trade_status,confirmation_date,entry_date,entry_price,exit_signal_date,
                exit_date,exit_price,exit_reason,holding_sessions,gross_return,net_return,
                peer_net_return,net_excess_return,entry_quality_state,
                entry_quality_reason_codes,next_session_confirmation_state,
                next_session_confirmation_reason_codes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    event.signal_date.isoformat(),
                    event.candidate_id,
                    event.asset_code,
                    event.name,
                    event.peer_group,
                    event.score,
                    event.feature_hash,
                    event.trade_status,
                    event.confirmation_date.isoformat()
                    if event.confirmation_date
                    else None,
                    event.entry_date.isoformat() if event.entry_date else None,
                    event.entry_price,
                    event.exit_signal_date.isoformat()
                    if event.exit_signal_date
                    else None,
                    event.exit_date.isoformat() if event.exit_date else None,
                    event.exit_price,
                    event.exit_reason,
                    event.holding_sessions,
                    event.gross_return,
                    event.net_return,
                    event.peer_net_return,
                    event.net_excess_return,
                    event.entry_quality_state,
                    json.dumps(event.entry_quality_reason_codes),
                    event.next_session_confirmation_state,
                    json.dumps(event.next_session_confirmation_reason_codes),
                )
                for event in events
            ],
        )
        store.execute(
            """
            INSERT OR REPLACE INTO evaluated_dates(signal_date,event_count,candidate_count)
            VALUES(?,?,?)
            """,
            (
                signal_date.isoformat(),
                len(events),
                len({(item.candidate_id, item.asset_code) for item in events}),
            ),
        )


def _evaluate_until_deadline(
    store: sqlite3.Connection, *, deadline: float
) -> dict[str, Any]:
    assets = _load_assets(store)
    dates = eligible_historical_signal_dates(assets)
    completed = {
        str(row[0]) for row in store.execute("SELECT signal_date FROM evaluated_dates")
    }
    pending = [item for item in dates if item.isoformat() not in completed]
    processed = 0
    event_count = 0
    for signal_date in pending:
        if processed and time.monotonic() >= deadline:
            break
        events = evaluate_historical_signal_date(assets, signal_date)
        _persist_date_events(store, signal_date, events)
        processed += 1
        event_count += len(events)
    evaluated = int(
        store.execute("SELECT count(*) FROM evaluated_dates").fetchone()[0]
    )
    if evaluated == len(dates):
        with store:
            _set_meta(store, "stage", "finalize")
            _set_meta(store, "evaluation_complete", "true")
    return {
        "status": "evaluation_complete" if evaluated == len(dates) else "partial_evaluate",
        "eligible_signal_dates": len(dates),
        "evaluated_signal_dates": evaluated,
        "processed_this_run": processed,
        "events_this_run": event_count,
    }


def _load_events(store: sqlite3.Connection) -> tuple[HistoricalLeaderEvent, ...]:
    return tuple(
        HistoricalLeaderEvent(
            signal_date=date.fromisoformat(str(row[0])),
            candidate_id=str(row[1]),
            asset_code=str(row[2]),
            name=str(row[3]) if row[3] is not None else None,
            peer_group=str(row[4]),
            score=float(row[5]),
            feature_hash=str(row[6]),
            trade_status=str(row[7]),
            confirmation_date=date.fromisoformat(str(row[8])) if row[8] else None,
            entry_date=date.fromisoformat(str(row[9])) if row[9] else None,
            entry_price=float(row[10]) if row[10] is not None else None,
            exit_signal_date=date.fromisoformat(str(row[11])) if row[11] else None,
            exit_date=date.fromisoformat(str(row[12])) if row[12] else None,
            exit_price=float(row[13]) if row[13] is not None else None,
            exit_reason=str(row[14]) if row[14] else None,
            holding_sessions=int(row[15]) if row[15] is not None else None,
            gross_return=float(row[16]) if row[16] is not None else None,
            net_return=float(row[17]) if row[17] is not None else None,
            peer_net_return=float(row[18]) if row[18] is not None else None,
            net_excess_return=float(row[19]) if row[19] is not None else None,
            entry_quality_state=str(row[20]) if row[20] else None,
            entry_quality_reason_codes=tuple(json.loads(str(row[21]))),
            next_session_confirmation_state=str(row[22]) if row[22] else None,
            next_session_confirmation_reason_codes=tuple(json.loads(str(row[23]))),
        )
        for row in store.execute(
            """
            SELECT signal_date,candidate_id,asset_code,name,peer_group,score,feature_hash,
                   trade_status,confirmation_date,entry_date,entry_price,exit_signal_date,
                   exit_date,exit_price,exit_reason,holding_sessions,gross_return,net_return,
                   peer_net_return,net_excess_return,entry_quality_state,
                   entry_quality_reason_codes,next_session_confirmation_state,
                   next_session_confirmation_reason_codes
            FROM events ORDER BY signal_date,candidate_id,asset_code
            """
        )
    )


def _history_coverage(store: sqlite3.Connection) -> dict[str, int]:
    rows = store.execute(
        """
        SELECT a.asset_code, count(b.trade_date)
        FROM assets a LEFT JOIN bars b ON b.asset_code=a.asset_code
        WHERE a.status='available'
        GROUP BY a.asset_code
        """
    ).fetchall()
    counts = [int(row[1]) for row in rows]
    return {
        "classified_asset_count": len(counts),
        "history_120_asset_count": sum(value >= 120 for value in counts),
        "history_180_asset_count": sum(value >= 180 for value in counts),
        "history_252_asset_count": sum(value >= 252 for value in counts),
        "history_300_asset_count": sum(value >= 300 for value in counts),
    }


def _build_report(store: sqlite3.Connection, events: tuple[HistoricalLeaderEvent, ...]) -> dict[str, Any]:
    coverage = {
        "source_ranked_asset_count": int(_meta(store, "source_ranked_asset_count") or 0),
        "loaded_asset_count": _asset_count(store),
        **_history_coverage(store),
        "eligible_signal_date_count": int(
            store.execute("SELECT count(*) FROM evaluated_dates").fetchone()[0]
        ),
        "event_count": len(events),
        "unique_candidate_signal_count": len(
            {(item.signal_date, item.candidate_id, item.asset_code) for item in events}
        ),
        "zero_candidate_signal_date_count": int(
            store.execute(
                "SELECT count(*) FROM evaluated_dates WHERE candidate_count=0"
            ).fetchone()[0]
        ),
    }
    exclusions = Counter(
        {
            str(row[0]): int(row[1])
            for row in store.execute(
                """
                SELECT exclusion_reason,count(*) FROM assets
                WHERE status!='available' GROUP BY exclusion_reason
                """
            )
            if row[0]
        }
    )
    exclusions["invalid_adjusted_rows"] = int(
        _meta(store, "invalid_adjusted_row_count") or 0
    )
    exclusions["classified_below_120_sessions"] = (
        coverage["classified_asset_count"] - coverage["history_120_asset_count"]
    )
    exclusions["classified_below_180_sessions"] = (
        coverage["classified_asset_count"] - coverage["history_180_asset_count"]
    )
    event_payloads = stable_event_payloads(events)
    dates = sorted({item.signal_date for item in events})
    report: dict[str, Any] = {
        "schema_version": LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
        "report_kind": LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
        "experiment_family": LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
        "evidence_mode": LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
        "status": "insufficient_data",
        "unavailable_reason": LEADER_HISTORICAL_BACKTEST_NOT_PIT,
        "contract_hash": HISTORICAL_BACKTEST_CONTRACT_HASH,
        "source_signal_run_id": int(_meta(store, "source_run_id") or 0),
        "source_signal_date": _meta(store, "source_signal_date"),
        "source_ranking_contract_hash": _meta(
            store, "source_ranking_contract_hash"
        ),
        "source_input_snapshot_hash": _meta(store, "source_input_snapshot_hash"),
        "ranking_source_kind": "research_replay",
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "signal_timing": "signal_at_T_close_confirm_at_T_plus_1_close",
        "entry_timing": "enter_at_T_plus_2_adjusted_open",
        "exit_signal_timing": "evaluate_email_exit_policy_at_each_daily_close",
        "exit_execution_timing": "exit_at_next_session_adjusted_open",
        "first_signal_date": dates[0].isoformat() if dates else None,
        "last_signal_date": dates[-1].isoformat() if dates else None,
        "coverage": coverage,
        "exclusion_counts": dict(sorted(exclusions.items())),
        "aggregates": list(summarize_historical_events(events)),
        "events_hash": stable_contract_hash(event_payloads),
        "sample_events": [
            {
                **item,
                "signal_date": item["signal_date"].isoformat(),
                "confirmation_date": (
                    item["confirmation_date"].isoformat()
                    if item["confirmation_date"]
                    else None
                ),
                "entry_date": (
                    item["entry_date"].isoformat() if item["entry_date"] else None
                ),
                "exit_signal_date": (
                    item["exit_signal_date"].isoformat()
                    if item["exit_signal_date"]
                    else None
                ),
                "exit_date": (
                    item["exit_date"].isoformat() if item["exit_date"] else None
                ),
            }
            for item in event_payloads[-40:]
        ],
        "promotion_gate_credit": {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        },
        "limitations": [
            "current-vintage ETF membership and peer taxonomy create survivorship/lookahead bias",
            "historical sector technical scores are unavailable and use a frozen neutral-50 date-local proxy",
            "overlapping event-series returns are not a capital-constrained portfolio equity curve",
            "historical proxy results cannot count toward PIT promotion gates",
            "research only; no ranking, position, alert, email, or execution mutation",
        ],
        "research_only": True,
        "production_mutation_allowed": False,
    }
    report["manifest_hash"] = stable_contract_hash(report)
    return report


async def _finalize(
    store: sqlite3.Connection,
    session: Any,
    *,
    code_version: str,
    artifact: Path,
) -> dict[str, Any]:
    events = _load_events(store)
    report = _build_report(store, events)
    evidence = build_historical_backtest_evidence(report, code_version=code_version)
    row = await persist_factor_evidence(session, evidence)
    result_path = artifact.with_suffix(".json")
    result_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    with store:
        _set_meta(store, "stage", "complete")
        _set_meta(store, "evidence_id", row.id)
        _set_meta(store, "manifest_hash", report["manifest_hash"])
    return {
        "status": "complete",
        "evidence_id": row.id,
        "manifest_hash": report["manifest_hash"],
        "result_path": str(result_path),
        "coverage": report["coverage"],
        "aggregates": report["aggregates"],
        "production_mutation_allowed": False,
    }


async def _run(arguments: argparse.Namespace) -> None:
    if not 1 <= arguments.page_size <= 200:
        raise ValueError("page size must be in [1, 200]")
    if not 5 <= arguments.runtime_seconds <= 42:
        raise ValueError("runtime seconds must be in [5, 42]")
    database_url = get_settings().database_url
    _require_server_database(database_url)
    store = _init_store(arguments.artifact)
    database = DatabaseManager(database_url)
    started = time.monotonic()
    try:
        async with database.session() as session:
            await _initialize_source(store, session)
            stage = _meta(store, "stage") or "load"
            if stage == "load":
                result = await _load_page(
                    store, session, page_size=arguments.page_size
                )
            elif stage == "evaluate":
                result = _evaluate_until_deadline(
                    store, deadline=started + arguments.runtime_seconds
                )
            elif stage == "finalize":
                result = await _finalize(
                    store,
                    session,
                    code_version=arguments.code_version,
                    artifact=arguments.artifact,
                )
            else:
                result = {
                    "status": "complete",
                    "evidence_id": int(_meta(store, "evidence_id") or 0),
                    "manifest_hash": _meta(store, "manifest_hash"),
                    "production_mutation_allowed": False,
                }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        store.close()
        await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(_run(_arguments()), timeout=47.0))
