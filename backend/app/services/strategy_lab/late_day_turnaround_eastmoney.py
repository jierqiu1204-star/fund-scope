"""Guarded Eastmoney A-share intraday capture for a bounded declared pool."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SHANGHAI = ZoneInfo("Asia/Shanghai")
SNAPSHOT_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DECLARED_POOL = 20
MAX_CONCURRENCY = 4
REQUEST_TIMEOUT_SECONDS = 6.0


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    code: str
    name: str
    latest: float
    today_open: float
    previous_close: float
    amount: float


@dataclass(frozen=True, slots=True)
class FiveMinuteBar:
    started_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


def _declared_pool_from_details(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, dict) or not isinstance(value.get("declared_pool"), list):
        return []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in value["declared_pool"]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("asset_code") or "")[-6:]
        if len(code) != 6 or not code.isdigit() or code in seen:
            continue
        seen.add(code)
        result.append((code, str(item.get("asset_name") or code)))
    return result[:MAX_DECLARED_POOL]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _secid(code: str) -> str:
    return f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"


def _payload(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("eastmoney_response_too_large")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("eastmoney_invalid_payload")
    return payload


async def _market_snapshot(client: httpx.AsyncClient) -> list[SnapshotRow]:
    response = await client.get(
        SNAPSHOT_URL,
        params={
            "pn": 1,
            "pz": 6000,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f6",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f2,f6,f12,f14,f17,f18",
        },
    )
    diff = (_payload(response).get("data") or {}).get("diff") or []
    if not isinstance(diff, list) or len(diff) > 6000:
        raise ValueError("eastmoney_invalid_market_snapshot")
    rows: list[SnapshotRow] = []
    for item in diff:
        if not isinstance(item, dict):
            continue
        code = str(item.get("f12") or "")[-6:]
        latest = _number(item.get("f2"))
        today_open = _number(item.get("f17"))
        previous_close = _number(item.get("f18"))
        amount = _number(item.get("f6"))
        if (
            len(code) != 6
            or latest is None
            or today_open is None
            or previous_close is None
            or amount is None
            or min(latest, today_open, previous_close) <= 0
            or amount < 0
        ):
            continue
        rows.append(
            SnapshotRow(
                code=code,
                name=str(item.get("f14") or code),
                latest=latest,
                today_open=today_open,
                previous_close=previous_close,
                amount=amount,
            )
        )
    return rows


async def _five_minute_bars(
    client: httpx.AsyncClient,
    *,
    code: str,
    trade_date: date,
    cutoff: datetime,
) -> list[FiveMinuteBar]:
    response = await client.get(
        KLINE_URL,
        params={
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": 5,
            "fqt": 0,
            "secid": _secid(code),
            "beg": trade_date.strftime("%Y%m%d"),
            "end": trade_date.strftime("%Y%m%d"),
        },
    )
    klines = (_payload(response).get("data") or {}).get("klines") or []
    if not isinstance(klines, list) or len(klines) > 64:
        raise ValueError("eastmoney_invalid_kline_payload")
    rows: list[FiveMinuteBar] = []
    for raw in klines:
        fields = str(raw).split(",")
        if len(fields) < 7:
            continue
        try:
            ended_at = datetime.strptime(fields[0], "%Y-%m-%d %H:%M").replace(
                tzinfo=SHANGHAI
            )
        except ValueError:
            continue
        # Eastmoney five-minute klines are labelled by interval end.
        if ended_at.date() != trade_date or ended_at > cutoff:
            continue
        started_at = ended_at - timedelta(minutes=5)
        values = [_number(value) for value in fields[1:7]]
        if any(value is None for value in values):
            continue
        open_price, close, high, low, volume, amount = (float(value) for value in values)
        if (
            min(open_price, close, high, low) <= 0
            or high < max(open_price, close)
            or low > min(open_price, close)
            or volume < 0
            or amount < 0
        ):
            continue
        rows.append(
            FiveMinuteBar(
                started_at=started_at,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
            )
        )
    return rows


def _aggregate_pairs(rows: list[FiveMinuteBar]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row.started_at)
    result: list[dict[str, Any]] = []
    for index in range(0, len(ordered) - 1, 2):
        first, second = ordered[index], ordered[index + 1]
        if second.started_at - first.started_at != timedelta(minutes=5):
            continue
        result.append(
            {
                "bar_start": first.started_at,
                "bar_end": second.started_at + timedelta(minutes=5),
                "raw_open": first.open,
                "raw_high": max(first.high, second.high),
                "raw_low": min(first.low, second.low),
                "raw_close": second.close,
                "volume": first.volume + second.volume,
                "amount": first.amount + second.amount,
            }
        )
    return result


async def _normalization_factors(
    session: AsyncSession,
    *,
    snapshots: list[SnapshotRow],
    trade_date: date,
    cutoff: datetime,
) -> dict[str, tuple[float, str]]:
    if not snapshots:
        return {}
    params: dict[str, Any] = {
        "trade_date": trade_date,
        "cutoff": cutoff.replace(tzinfo=None),
        "eligible": True,
        "historical_only": False,
    }
    placeholders: list[str] = []
    for index, row in enumerate(snapshots):
        key = f"code_{index}"
        params[key] = row.code
        placeholders.append(f":{key}")
    rows = (
        await session.execute(
            text(
                f"""
                SELECT asset_code, trade_date, adjusted_close, adjustment_version
                FROM (
                    SELECT facts.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code ORDER BY trade_date DESC,
                                   received_at DESC, revision_id DESC, id DESC
                           ) AS row_rank
                    FROM ashare_adjusted_price_facts facts
                    WHERE asset_code IN ({','.join(placeholders)})
                      AND trade_date < :trade_date
                      AND received_at <= :cutoff
                      AND decision_eligible = :eligible
                      AND historical_research_only = :historical_only
                      AND price_basis = 'total_return_adjusted'
                ) ranked
                WHERE row_rank = 1
                """
            ),
            params,
        )
    ).mappings().all()
    daily = {str(row["asset_code"])[-6:]: dict(row) for row in rows}
    factors: dict[str, tuple[float, str]] = {}
    for snapshot in snapshots:
        fact = daily.get(snapshot.code)
        if fact is None or snapshot.previous_close <= 0:
            continue
        factor = float(fact["adjusted_close"]) / snapshot.previous_close
        if not isfinite(factor) or factor <= 0:
            continue
        identity = sha256(
            json.dumps(
                {
                    "code": snapshot.code,
                    "daily_trade_date": str(fact["trade_date"]),
                    "adjustment_version": fact["adjustment_version"],
                    "raw_previous_close": snapshot.previous_close,
                    "adjusted_previous_close": fact["adjusted_close"],
                    "factor": factor,
                },
                sort_keys=True,
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        factors[snapshot.code] = (factor, identity)
    return factors


async def capture_bounded_ashare_pool(
    session: AsyncSession,
    *,
    decision_at: datetime,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Capture a full-market gate then factual five-minute bars for at most 20 names."""

    cutoff = decision_at.astimezone(SHANGHAI)
    checkpoint = (
        await session.execute(
            text(
                """
                SELECT status, expected_count, completed_count, failed_count,
                       manifest_hash, details_json
                FROM late_day_turnaround_capture_checkpoints
                WHERE universe = 'ashare'
                  AND trade_date = :trade_date
                  AND checkpoint_at = :checkpoint_at
                  AND provider = 'eastmoney_5m'
                """
            ),
            {
                "trade_date": cutoff.date(),
                "checkpoint_at": cutoff.replace(tzinfo=None),
            },
        )
    ).mappings().first()
    if checkpoint is not None and checkpoint["status"] == "complete":
        return {
            "status": "complete",
            "expected_count": int(checkpoint["expected_count"]),
            "completed_count": int(checkpoint["completed_count"]),
            "failed_count": int(checkpoint["failed_count"]),
            "manifest_hash": str(checkpoint["manifest_hash"]),
            "research_only": True,
            "production_mutation_allowed": False,
            "provider_work_skipped": True,
        }
    resume_pool = _declared_pool_from_details(
        checkpoint["details_json"] if checkpoint is not None else None
    )
    owned_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": "FundScope/late-day-shadow"},
        )
    try:
        universe_rows = (
            await session.execute(
                text(
                    """
                    SELECT asset_code FROM ashare_research_universe_snapshots
                    WHERE snapshot_date = (
                        SELECT MAX(snapshot_date) FROM ashare_research_universe_snapshots
                        WHERE snapshot_date <= :trade_date AND received_at <= :cutoff
                    )
                      AND received_at <= :cutoff
                      AND listing_state = 'listed'
                      AND exclusion_reason IS NULL
                    """
                ),
                {"trade_date": cutoff.date(), "cutoff": cutoff.replace(tzinfo=None)},
            )
        ).all()
        authoritative = {str(row[0])[-6:] for row in universe_rows}
        market_rows = await _market_snapshot(client)
        market_by_code = {row.code: row for row in market_rows if row.code in authoritative}
        failures: dict[str, str] = {}
        if resume_pool:
            declared_pool = resume_pool
            declared = []
            for code, _name in declared_pool:
                snapshot = market_by_code.get(code)
                if snapshot is None:
                    failures[code] = "declared_asset_missing_from_market_snapshot"
                else:
                    declared.append(snapshot)
        else:
            snapshots = [
                row
                for row in market_by_code.values()
                if row.latest > row.today_open
                and row.latest > row.previous_close
                and (row.latest / row.previous_close - 1.0) * 100 <= 7.0
                and row.amount >= 50_000_000.0
            ]
            snapshots.sort(key=lambda row: (-row.amount, row.code))
            declared = snapshots[:MAX_DECLARED_POOL]
            declared_pool = [(row.code, row.name) for row in declared]
        expected_count = len(declared_pool)
        factors = await _normalization_factors(
            session,
            snapshots=declared,
            trade_date=cutoff.date(),
            cutoff=cutoff,
        )
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def load(row: SnapshotRow) -> tuple[SnapshotRow, list[FiveMinuteBar] | None, str | None]:
            try:
                async with semaphore:
                    bars = await _five_minute_bars(
                        client,
                        code=row.code,
                        trade_date=cutoff.date(),
                        cutoff=cutoff,
                    )
                return row, bars, None
            except (httpx.HTTPError, ValueError) as exc:
                return row, None, f"{type(exc).__name__}:{str(exc)[:160]}"

        loaded = await asyncio.gather(*(load(row) for row in declared))
        completed = 0
        received_at = datetime.now(SHANGHAI)
        capture_eligible = received_at <= cutoff
        for snapshot, five_minute, error in loaded:
            factor_identity = factors.get(snapshot.code)
            bars = _aggregate_pairs(five_minute or [])
            if error or factor_identity is None or len(bars) < 7:
                failures[snapshot.code] = error or "normalization_or_bar_coverage_unavailable"
                continue
            factor, identity = factor_identity
            for bar in bars:
                content_hash = sha256(
                    json.dumps(
                        {"code": snapshot.code, **bar, "factor": factor},
                        default=str,
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                await session.execute(
                    text(
                        """
                        INSERT INTO ashare_intraday_10m_facts
                            (asset_code, trade_date, bar_start, bar_end, raw_open,
                             raw_high, raw_low, raw_close, volume, amount, provider,
                             source_timestamp, received_at, normalization_factor,
                             normalization_identity, decision_eligible, content_hash)
                        VALUES
                            (:asset_code, :trade_date, :bar_start, :bar_end, :raw_open,
                             :raw_high, :raw_low, :raw_close, :volume, :amount, :provider,
                             :source_timestamp, :received_at, :factor, :identity,
                             :eligible, :content_hash)
                        ON CONFLICT(content_hash) DO NOTHING
                        """
                    ),
                    {
                        "asset_code": snapshot.code,
                        "trade_date": cutoff.date(),
                        **{key: value.replace(tzinfo=None) if isinstance(value, datetime) else value for key, value in bar.items()},
                        "provider": "eastmoney_5m",
                        "source_timestamp": received_at.replace(tzinfo=None),
                        "received_at": received_at.replace(tzinfo=None),
                        "factor": factor,
                        "identity": identity,
                        "eligible": capture_eligible,
                        "content_hash": content_hash,
                    },
                )
            if not capture_eligible:
                failures[snapshot.code] = "received_after_cutoff"
                continue
            completed += 1

        manifest_hash = sha256(
            json.dumps(
                {
                    "decision_at": cutoff.isoformat(),
                    "declared": [code for code, _name in declared_pool],
                    "completed": completed,
                    "failures": failures,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        await session.execute(
            text(
                """
                INSERT INTO late_day_turnaround_capture_checkpoints
                    (universe, trade_date, checkpoint_at, provider, status,
                     cursor_asset_code, expected_count, completed_count, failed_count,
                     manifest_hash, error_summary, details_json, updated_at)
                VALUES
                    ('ashare', :trade_date, :checkpoint_at, :provider, :status,
                     NULL, :expected_count, :completed_count, :failed_count,
                     :manifest_hash, :error_summary, :details, :updated_at)
                ON CONFLICT(universe, trade_date, checkpoint_at, provider)
                DO UPDATE SET status = EXCLUDED.status,
                    expected_count = EXCLUDED.expected_count,
                    completed_count = EXCLUDED.completed_count,
                    failed_count = EXCLUDED.failed_count,
                    manifest_hash = EXCLUDED.manifest_hash,
                    error_summary = EXCLUDED.error_summary,
                    details_json = EXCLUDED.details_json,
                    updated_at = EXCLUDED.updated_at
                WHERE late_day_turnaround_capture_checkpoints.status <> 'complete'
                """
            ),
            {
                "trade_date": cutoff.date(),
                "checkpoint_at": cutoff.replace(tzinfo=None),
                "provider": "eastmoney_5m",
                "status": "complete" if completed == expected_count else "incomplete",
                "expected_count": expected_count,
                "completed_count": completed,
                "failed_count": len(failures),
                "manifest_hash": manifest_hash,
                "error_summary": ";".join(f"{key}:{value}" for key, value in failures.items())[:500] or None,
                "details": json.dumps(
                    {
                        "declared_pool": [
                            {"asset_code": code, "asset_name": name}
                            for code, name in declared_pool
                        ],
                        "provider_calls": len(declared) + 1,
                        "max_concurrency": MAX_CONCURRENCY,
                        "response_cap_bytes": MAX_RESPONSE_BYTES,
                    },
                    sort_keys=True,
                ),
                "updated_at": datetime.now(UTC).replace(tzinfo=None),
            },
        )
        await session.commit()
        return {
            "status": "complete" if completed == expected_count else "incomplete",
            "expected_count": expected_count,
            "completed_count": completed,
            "failed_count": len(failures),
            "manifest_hash": manifest_hash,
            "research_only": True,
            "production_mutation_allowed": False,
        }
    finally:
        if owned_client:
            await client.aclose()


__all__ = [
    "MAX_CONCURRENCY",
    "MAX_DECLARED_POOL",
    "MAX_RESPONSE_BYTES",
    "capture_bounded_ashare_pool",
]
