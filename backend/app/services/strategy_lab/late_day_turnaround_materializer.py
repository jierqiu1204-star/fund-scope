"""PIT adapters and bounded materialization for late-day turnaround research."""

from __future__ import annotations

import json
import time as monotonic_time
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.late_day_turnaround_shadow import (
    ClosedBar10m,
    Config,
    PITSnapshot,
    evaluate_snapshot,
)
from app.services.strategy_lab.late_day_turnaround_storage import (
    INCOMPLETE_DECLARED_COVERAGE,
    JOB_TIMEOUT,
    MINUTE_DATA_UNAVAILABLE,
    Universe,
    persist_run,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
WORK_BUDGET_SECONDS = 52.0
MAX_UNIVERSE_SIZE = 6_000
MAX_QUOTES = 200_000
FORMAL_COVERAGE_GATE = 1.0
PROXY_LIMIT = 100


def _aware_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def _finite_positive(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _raw_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _session_open(raw: dict[str, Any]) -> float | None:
    for key in ("今开", "开盘", "open", "open_price", "today_open"):
        value = raw.get(key)
        if _finite_positive(value):
            return float(value)
    return None


def _bucket_start(value: datetime) -> datetime | None:
    local = _aware_shanghai(value)
    local_time = local.time()
    if time(9, 30) <= local_time < time(11, 30):
        segment = local.replace(hour=9, minute=30, second=0, microsecond=0)
    elif time(13, 0) <= local_time < time(15, 0):
        segment = local.replace(hour=13, minute=0, second=0, microsecond=0)
    else:
        return None
    minutes = int((local - segment).total_seconds() // 60)
    return segment + timedelta(minutes=(minutes // 10) * 10)


def _aggregate_quote_rows(
    rows: list[dict[str, Any]], *, decision_at: datetime, normalization_factor: float
) -> tuple[ClosedBar10m, ...]:
    groups: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        quote_time = row["quote_time"]
        if isinstance(quote_time, str):
            quote_time = datetime.fromisoformat(quote_time)
        quote_time = _aware_shanghai(quote_time)
        bucket_start = _bucket_start(quote_time)
        if bucket_start is None or bucket_start + timedelta(minutes=10) > decision_at:
            continue
        if not _finite_positive(row.get("latest_price")):
            continue
        row["quote_time"] = quote_time
        groups[bucket_start].append(row)

    bars: list[ClosedBar10m] = []
    prior_cumulative_volume = 0.0
    prior_cumulative_amount = 0.0
    for bucket_start in sorted(groups):
        bucket_rows = sorted(groups[bucket_start], key=lambda row: row["quote_time"])
        prices = [float(row["latest_price"]) * normalization_factor for row in bucket_rows]
        last = bucket_rows[-1]
        cumulative_volume = float(last.get("volume") or prior_cumulative_volume)
        cumulative_amount = float(last.get("turnover") or prior_cumulative_amount)
        volume = max(0.0, cumulative_volume - prior_cumulative_volume)
        amount = max(0.0, cumulative_amount - prior_cumulative_amount)
        prior_cumulative_volume = max(prior_cumulative_volume, cumulative_volume)
        prior_cumulative_amount = max(prior_cumulative_amount, cumulative_amount)
        bars.append(
            ClosedBar10m(
                observed_at=bucket_start + timedelta(minutes=10),
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume=volume,
                amount=amount,
            )
        )
    return tuple(bars[1:][-32:])


async def _etf_universe(session: AsyncSession) -> list[tuple[str, str]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT code, name FROM tradable_etfs
                WHERE is_short_term_eligible = :eligible
                ORDER BY code
                LIMIT :limit
                """
            ),
            {"eligible": True, "limit": MAX_UNIVERSE_SIZE + 1},
        )
    ).all()
    return [(str(code)[-6:], str(name)) for code, name in rows]


async def _ashare_universe(
    session: AsyncSession, *, signal_date: date, cutoff: datetime
) -> list[tuple[str, str]]:
    row = (
        await session.execute(
            text(
                """
                SELECT status, expected_count, completed_count, details_json
                FROM late_day_turnaround_capture_checkpoints
                WHERE universe = 'ashare'
                  AND trade_date = :signal_date
                  AND checkpoint_at = :cutoff
                  AND provider = 'eastmoney_5m'
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {
                "signal_date": signal_date,
                "cutoff": cutoff.replace(tzinfo=None),
            },
        )
    ).mappings().first()
    if (
        row is None
        or row["status"] != "complete"
        or int(row["expected_count"] or 0) != int(row["completed_count"] or 0)
    ):
        return []
    details = row["details_json"]
    if isinstance(details, str):
        details = json.loads(details)
    declared = details.get("declared_pool", []) if isinstance(details, dict) else []
    result = [
        (str(item.get("asset_code") or "")[-6:], str(item.get("asset_name") or ""))
        for item in declared
        if isinstance(item, dict)
    ]
    return [(code, name or code) for code, name in result if len(code) == 6]


async def _ashare_proxy_universe(
    session: AsyncSession, *, signal_date: date, cutoff: datetime
) -> list[tuple[str, str]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT asset_code, asset_name
                FROM ashare_research_universe_snapshots
                WHERE snapshot_date = (
                    SELECT MAX(snapshot_date)
                    FROM ashare_research_universe_snapshots
                    WHERE snapshot_date <= :signal_date AND received_at <= :cutoff
                )
                  AND received_at <= :cutoff
                  AND listing_state = 'listed'
                  AND exclusion_reason IS NULL
                ORDER BY asset_code
                LIMIT :limit
                """
            ),
            {
                "signal_date": signal_date,
                "cutoff": cutoff.replace(tzinfo=None),
                "limit": MAX_UNIVERSE_SIZE + 1,
            },
        )
    ).all()
    return [(str(code)[-6:], str(name)) for code, name in rows]


async def _latest_etf_daily(
    session: AsyncSession, *, codes: list[str], signal_date: date
) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(codes), 128):
        page = codes[offset : offset + 128]
        params: dict[str, Any] = {"signal_date": signal_date, "row_limit": 1}
        placeholders: list[str] = []
        for index, code in enumerate(page):
            key = f"code_{index}"
            params[key] = code
            placeholders.append(f":{key}")
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT * FROM (
                        SELECT etf_code, trade_date, open, close,
                               research_adjusted_value, research_price_basis,
                               data_provider, provider_version, adjustment_version,
                               decision_eligible,
                               ROW_NUMBER() OVER (
                                   PARTITION BY etf_code ORDER BY trade_date DESC, id DESC
                               ) AS row_rank
                        FROM etf_price_history
                        WHERE etf_code IN ({','.join(placeholders)})
                          AND trade_date < :signal_date
                    ) ranked
                    WHERE row_rank <= :row_limit
                    """
                ),
                params,
            )
        ).mappings().all()
        for row in rows:
            result[str(row["etf_code"])[-6:]] = dict(row)
    return result


async def _etf_observations(
    session: AsyncSession,
    *,
    universe: list[tuple[str, str]],
    decision_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    codes = [code for code, _name in universe]
    daily = await _latest_etf_daily(session, codes=codes, signal_date=decision_at.date())
    quote_rows = (
        await session.execute(
            text(
                """
                SELECT etf_code, quote_time, latest_price, volume, turnover,
                       source, freshness_status, raw_json, created_at
                FROM etf_intraday_quotes
                WHERE trade_date = :trade_date
                  AND quote_time >= :window_start
                  AND quote_time <= :decision_at
                  AND created_at <= :decision_at
                ORDER BY etf_code, quote_time, id
                LIMIT :limit
                """
            ),
            {
                "trade_date": decision_at.date(),
                "window_start": (decision_at - timedelta(minutes=100)).replace(tzinfo=None),
                "decision_at": decision_at.replace(tzinfo=None),
                "limit": MAX_QUOTES + 1,
            },
        )
    ).mappings().all()
    if len(quote_rows) > MAX_QUOTES:
        return [], {"status": "unavailable", "reason": "input_budget_exceeded"}
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sources: Counter[str] = Counter()
    for raw_row in quote_rows:
        row = dict(raw_row)
        code = str(row["etf_code"])[-6:]
        by_code[code].append(row)
        sources[str(row.get("source") or "unknown")] += 1

    observations: list[dict[str, Any]] = []
    for code, name in universe:
        daily_row = daily.get(code)
        quotes = by_code.get(code, [])
        reason: str | None = None
        if daily_row is None:
            reason = "missing_prior_adjusted_daily_fact"
        elif daily_row.get("decision_eligible") is not True:
            reason = "prior_daily_fact_not_decision_eligible"
        elif daily_row.get("research_price_basis") != "total_return_adjusted":
            reason = "incompatible_price_basis"
        elif not quotes:
            reason = MINUTE_DATA_UNAVAILABLE
        if reason is not None:
            observations.append(
                {
                    "asset_code": code,
                    "asset_name": name,
                    "observation_kind": "formal_exclusion",
                    "available": False,
                    "reason": reason,
                    "provenance": {"universe": "etf"},
                }
            )
            continue
        assert daily_row is not None
        raw_close = float(daily_row["close"])
        adjusted_close = float(daily_row["research_adjusted_value"])
        factor = adjusted_close / raw_close if raw_close > 0 else 0.0
        open_price = _session_open(_raw_json(quotes[0].get("raw_json")))
        bars = _aggregate_quote_rows(quotes, decision_at=decision_at, normalization_factor=factor)
        if not _finite_positive(factor) or open_price is None:
            reason = "missing_session_open_or_normalization"
            result = None
        else:
            snapshot = PITSnapshot(
                asset_code=code,
                signal_date=decision_at.date(),
                decision_at=decision_at,
                today_open=open_price * factor,
                previous_close=adjusted_close,
                current_price=float(quotes[-1]["latest_price"]) * factor,
                previous_open=float(daily_row["open"]) * factor,
                cumulative_amount=float(quotes[-1].get("turnover") or 0.0),
                bars=bars,
            )
            result = evaluate_snapshot(snapshot, Config())
            reason = result.reason
        observations.append(
            {
                "asset_code": code,
                "asset_name": name,
                "observation_kind": (
                    "formal_candidate" if result is not None and result.available else "formal_exclusion"
                ),
                "available": bool(result is not None and result.available),
                "reason": reason,
                "score": result.score if result is not None else None,
                "ma5": result.ma5 if result is not None else None,
                "gain_pct": result.gain_pct if result is not None else None,
                "ma_deviation_pct": result.ma_deviation_pct if result is not None else None,
                "amount_ratio": result.amount_ratio if result is not None else None,
                "provenance": {
                    "universe": "etf",
                    "daily_provider": daily_row.get("data_provider"),
                    "adjustment_version": daily_row.get("adjustment_version"),
                    "quote_sources": sorted({str(row.get("source")) for row in quotes}),
                    "bar_count": len(bars),
                },
            }
        )
    return observations, {"status": "ok", "sources": dict(sources), "quote_rows": len(quote_rows)}


async def _daily_proxy_observations(
    session: AsyncSession,
    *,
    universe: list[tuple[str, str]],
    decision_at: datetime,
) -> list[dict[str, Any]]:
    """Return a bounded, explicitly non-actionable daily approximation."""

    names = dict(universe)
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    codes = list(names)
    for offset in range(0, len(codes), 512):
        page = codes[offset : offset + 512]
        params: dict[str, Any] = {
            "signal_date": decision_at.date(),
            "cutoff": decision_at.replace(tzinfo=None),
            "eligible": True,
            "historical_only": False,
        }
        placeholders: list[str] = []
        for index, code in enumerate(page):
            key = f"proxy_code_{index}"
            params[key] = code
            placeholders.append(f":{key}")
        page_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT asset_code, trade_date, adjusted_open, adjusted_close, amount
                    FROM (
                        SELECT ranked_revisions.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY asset_code ORDER BY trade_date DESC
                               ) AS history_rank
                        FROM (
                            SELECT facts.*,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY asset_code, trade_date
                                       ORDER BY received_at DESC, revision_id DESC, id DESC
                                   ) AS revision_rank
                            FROM ashare_adjusted_price_facts facts
                            WHERE asset_code IN ({','.join(placeholders)})
                              AND trade_date < :signal_date
                              AND received_at <= :cutoff
                              AND decision_eligible = :eligible
                              AND historical_research_only = :historical_only
                              AND price_basis = 'total_return_adjusted'
                        ) ranked_revisions
                        WHERE revision_rank = 1
                    ) history
                    WHERE history_rank <= 7
                    ORDER BY asset_code, trade_date
                    """
                ),
                params,
            )
        ).mappings().all()
        for row in page_rows:
            code = str(row["asset_code"])[-6:]
            if code in names:
                by_code[code].append(dict(row))
    candidates: list[dict[str, Any]] = []
    for code, history in by_code.items():
        if len(history) < 7:
            continue
        closes = [float(row["adjusted_close"]) for row in history]
        latest = history[-1]
        previous = history[-2]
        current_ma5 = sum(closes[-5:]) / 5
        previous_ma5 = sum(closes[-6:-1]) / 5
        prior_ma5 = sum(closes[-7:-2]) / 5
        if not (
            float(latest["adjusted_close"]) > float(latest["adjusted_open"])
            and float(previous["adjusted_close"]) <= float(previous["adjusted_open"])
            and current_ma5 > previous_ma5
            and previous_ma5 <= prior_ma5
            and closes[-1] > current_ma5
        ):
            continue
        score = (current_ma5 / previous_ma5 - 1.0) * 10_000 + min(
            float(latest["amount"]) / 100_000_000.0, 10.0
        )
        candidates.append(
            {
                "asset_code": code,
                "asset_name": names[code],
                "observation_kind": "daily_proxy_watchlist",
                "available": False,
                "reason": "daily_proxy_not_live_signal",
                "score": round(score, 8),
                "ma5": current_ma5,
                "gain_pct": None,
                "ma_deviation_pct": abs(closes[-1] - current_ma5) / current_ma5 * 100,
                "amount_ratio": None,
                "provenance": {
                    "historical_live_signal_eligible": False,
                    "formal_candidate": False,
                    "source": "ashare_adjusted_price_facts",
                },
            }
        )
    candidates.sort(key=lambda row: (-float(row["score"]), str(row["asset_code"])))
    return candidates[:PROXY_LIMIT]


async def _evaluate_ashare_formal(
    session: AsyncSession,
    *,
    universe: list[tuple[str, str]],
    decision_at: datetime,
) -> list[dict[str, Any]]:
    codes = [code for code, _name in universe]
    if not codes:
        return []
    params: dict[str, Any] = {
        "trade_date": decision_at.date(),
        "decision_at": decision_at.replace(tzinfo=None),
        "eligible": True,
        "historical_only": False,
    }
    placeholders: list[str] = []
    for index, code in enumerate(codes):
        key = f"ashare_code_{index}"
        params[key] = code
        placeholders.append(f":{key}")
    code_sql = ",".join(placeholders)
    daily_rows = (
        await session.execute(
            text(
                f"""
                SELECT asset_code, trade_date, adjusted_open, adjusted_close,
                       provider, adjustment_version, received_at
                FROM (
                    SELECT facts.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code
                               ORDER BY trade_date DESC, received_at DESC,
                                        revision_id DESC, id DESC
                           ) AS row_rank
                    FROM ashare_adjusted_price_facts facts
                    WHERE asset_code IN ({code_sql})
                      AND trade_date < :trade_date
                      AND received_at <= :decision_at
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
    daily = {str(row["asset_code"])[-6:]: dict(row) for row in daily_rows}
    intraday_rows = (
        await session.execute(
            text(
                f"""
                SELECT asset_code, bar_start, bar_end, raw_open, raw_high,
                       raw_low, raw_close, volume, amount, provider,
                       source_timestamp, received_at, normalization_factor,
                       normalization_identity
                FROM ashare_intraday_10m_facts
                WHERE asset_code IN ({code_sql})
                  AND trade_date = :trade_date
                  AND bar_end <= :decision_at
                  AND received_at <= :decision_at
                  AND decision_eligible = :eligible
                ORDER BY asset_code, bar_end, id
                """
            ),
            params,
        )
    ).mappings().all()
    bars_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in intraday_rows:
        bars_by_code[str(row["asset_code"])[-6:]].append(dict(row))

    observations: list[dict[str, Any]] = []
    for code, name in universe:
        daily_row = daily.get(code)
        raw_bars = bars_by_code.get(code, [])
        identities = {str(row["normalization_identity"]) for row in raw_bars}
        factors = {float(row["normalization_factor"]) for row in raw_bars}
        if daily_row is None:
            result = None
            reason = "missing_prior_adjusted_daily_fact"
            bars: tuple[ClosedBar10m, ...] = ()
        elif len(identities) != 1 or len(factors) != 1:
            result = None
            reason = "incompatible_price_basis"
            bars = ()
        else:
            factor = next(iter(factors))
            parsed_bars: list[ClosedBar10m] = []
            for row in raw_bars:
                bar_end = row["bar_end"]
                if isinstance(bar_end, str):
                    bar_end = datetime.fromisoformat(bar_end)
                parsed_bars.append(
                    ClosedBar10m(
                        observed_at=_aware_shanghai(bar_end),
                        open=float(row["raw_open"]) * factor,
                        high=float(row["raw_high"]) * factor,
                        low=float(row["raw_low"]) * factor,
                        close=float(row["raw_close"]) * factor,
                        volume=float(row["volume"]),
                        amount=float(row["amount"]),
                    )
                )
            bars = tuple(parsed_bars[-32:])
            if not bars:
                result = None
                reason = MINUTE_DATA_UNAVAILABLE
            else:
                snapshot = PITSnapshot(
                    asset_code=code,
                    signal_date=decision_at.date(),
                    decision_at=decision_at,
                    today_open=bars[0].open,
                    previous_close=float(daily_row["adjusted_close"]),
                    current_price=bars[-1].close,
                    previous_open=float(daily_row["adjusted_open"]),
                    cumulative_amount=sum(bar.amount for bar in bars),
                    bars=bars,
                )
                result = evaluate_snapshot(snapshot, Config())
                reason = result.reason
        observations.append(
            {
                "asset_code": code,
                "asset_name": name,
                "observation_kind": (
                    "formal_candidate"
                    if result is not None and result.available
                    else "formal_exclusion"
                ),
                "available": bool(result is not None and result.available),
                "reason": reason,
                "score": result.score if result is not None else None,
                "ma5": result.ma5 if result is not None else None,
                "gain_pct": result.gain_pct if result is not None else None,
                "ma_deviation_pct": (
                    result.ma_deviation_pct if result is not None else None
                ),
                "amount_ratio": result.amount_ratio if result is not None else None,
                "provenance": {
                    "universe": "ashare",
                    "daily_provider": (
                        daily_row.get("provider") if daily_row is not None else None
                    ),
                    "adjustment_version": (
                        daily_row.get("adjustment_version")
                        if daily_row is not None
                        else None
                    ),
                    "intraday_provider": (
                        raw_bars[-1].get("provider") if raw_bars else None
                    ),
                    "normalization_identity": (
                        next(iter(identities)) if len(identities) == 1 else None
                    ),
                    "bar_count": len(bars),
                },
            }
        )
    return observations


async def _ashare_observations(
    session: AsyncSession,
    *,
    universe: list[tuple[str, str]],
    decision_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    params: dict[str, Any] = {
        "trade_date": decision_at.date(),
        "decision_at": decision_at.replace(tzinfo=None),
        "eligible": True,
    }
    placeholders: list[str] = []
    for index, (code, _name) in enumerate(universe):
        key = f"coverage_code_{index}"
        params[key] = code
        placeholders.append(f":{key}")
    code_sql = ",".join(placeholders) or "NULL"
    coverage_row = (
        await session.execute(
            text(
                f"""
                SELECT COUNT(DISTINCT asset_code) AS covered,
                       COUNT(*) AS bar_count,
                       MAX(received_at) AS latest_received_at
                FROM ashare_intraday_10m_facts
                WHERE asset_code IN ({code_sql})
                  AND trade_date = :trade_date
                  AND bar_end <= :decision_at
                  AND received_at <= :decision_at
                  AND decision_eligible = :eligible
                """
            ),
            params,
        )
    ).mappings().one()
    expected = len(universe)
    covered = int(coverage_row["covered"] or 0)
    health = {
        "status": "ok" if expected and covered == expected else "incomplete",
        "covered_assets": covered,
        "expected_assets": expected,
        "bar_count": int(coverage_row["bar_count"] or 0),
        "latest_received_at": (
            coverage_row["latest_received_at"].isoformat()
            if hasattr(coverage_row["latest_received_at"], "isoformat")
            else coverage_row["latest_received_at"]
        ),
    }
    if expected == 0 or covered / expected < FORMAL_COVERAGE_GATE:
        proxies = await _daily_proxy_observations(
            session, universe=universe, decision_at=decision_at
        )
        reason = MINUTE_DATA_UNAVAILABLE if covered == 0 else INCOMPLETE_DECLARED_COVERAGE
        return proxies, health, reason

    observations = await _evaluate_ashare_formal(
        session, universe=universe, decision_at=decision_at
    )
    return observations, health, None


async def materialize_late_day_turnaround(
    session: AsyncSession,
    *,
    universe: Universe,
    decision_at: datetime,
    work_budget_seconds: float = WORK_BUDGET_SECONDS,
) -> dict[str, Any]:
    started = monotonic_time.monotonic()
    cutoff = _aware_shanghai(decision_at).replace(second=0, microsecond=0)
    if cutoff.time() not in {time(14, 30), time(14, 40), time(14, 50)}:
        return {"status": "skipped", "reason": "outside_materialization_checkpoint"}
    if universe == "etf":
        declared = await _etf_universe(session)
    else:
        declared = await _ashare_universe(
            session, signal_date=cutoff.date(), cutoff=cutoff
        )
    if len(declared) > MAX_UNIVERSE_SIZE:
        return {"status": "unavailable", "reason": "input_budget_exceeded"}

    unavailable_reason: str | None = None
    if universe == "etf":
        observations, provider_health = await _etf_observations(
            session, universe=declared, decision_at=cutoff
        )
        status = "complete" if len(observations) == len(declared) else "unavailable"
        if status != "complete":
            unavailable_reason = INCOMPLETE_DECLARED_COVERAGE
    else:
        if not declared:
            declared = await _ashare_proxy_universe(
                session, signal_date=cutoff.date(), cutoff=cutoff
            )
            observations = await _daily_proxy_observations(
                session, universe=declared, decision_at=cutoff
            )
            provider_health = {"status": "minute_capture_not_completed"}
            unavailable_reason = MINUTE_DATA_UNAVAILABLE
        else:
            observations, provider_health, unavailable_reason = await _ashare_observations(
                session, universe=declared, decision_at=cutoff
            )
        status = "unavailable" if unavailable_reason else "complete"

    elapsed = monotonic_time.monotonic() - started
    if elapsed > work_budget_seconds:
        status = "unavailable"
        unavailable_reason = JOB_TIMEOUT
        observations = []
        provider_health = {**provider_health, "elapsed_seconds": round(elapsed, 4)}
    manifest_hash = await persist_run(
        session,
        universe=universe,
        decision_at=cutoff,
        status=status,
        universe_codes=[code for code, _name in declared],
        observations=observations,
        expected_count=len(declared),
        provider_health=provider_health,
        unavailable_reason=unavailable_reason,
    )
    return {
        "status": status,
        "universe": universe,
        "manifest_hash": manifest_hash,
        "expected_count": len(declared),
        "observation_count": len(observations),
        "unavailable_reason": unavailable_reason,
        "elapsed_seconds": round(monotonic_time.monotonic() - started, 4),
        "research_only": True,
        "production_mutation_allowed": False,
    }


__all__ = ["materialize_late_day_turnaround"]
