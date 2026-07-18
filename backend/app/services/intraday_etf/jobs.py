from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import IntradayEtfWatchRun, utcnow
from app.services.intraday_etf.service import (
    ASIA_SHANGHAI,
    build_watchlist,
    current_market_state,
    fetch_spot_quotes_with_metadata,
    is_fresh_decision_quote,
    is_quote_time_fallback,
    latest_quotes_by_code,
    persist_quotes,
    quote_consensus_status,
    quote_decision_limitation_reason,
    quote_fresh_provider_count,
    quote_price_diff_abs,
    quote_price_diff_pct,
    quote_provider_count,
    summarize_and_cleanup_intraday_quotes,
)


async def intraday_etf_watch_job(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    run_type: str = "scheduled",
    force: bool = False,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    started = utcnow()
    market_state = current_market_state()
    watchlist = await build_watchlist(session)
    run = IntradayEtfWatchRun(
        run_type=run_type,
        status="running",
        started_at=started,
        market_session=market_state.session,
        watched_count=len(watchlist.items),
        details_json={
            "signal_run_id": watchlist.signal_run_id,
            "signal_as_of_date": watchlist.signal_as_of_date.isoformat() if watchlist.signal_as_of_date else None,
            "signal_status": watchlist.signal_status,
            "watchlist_source_counts": _watchlist_source_counts(watchlist.items),
            "watchlist_codes": _sample_codes([item.etf_code for item in watchlist.items]),
        },
    )
    session.add(run)
    await session.commit()
    run_id = run.id
    try:
        if market_state.status != "open" and not force:
            run.status = "skipped"
            run.skipped_reason = "当前不在 A 股 ETF 交易时段，已跳过外部实时行情请求。"
            run.finished_at = utcnow()
            await session.commit()
            return _result(run)

        if not watchlist.items:
            run.status = "skipped"
            run.skipped_reason = "没有需要盘中盯盘的 ETF。"
            run.finished_at = utcnow()
            await session.commit()
            return _result(run)

        watch_codes = {item.etf_code for item in watchlist.items}
        fetch_result = await fetch_spot_quotes_with_metadata(fetcher)
        quotes = fetch_result.quotes
        provider_health = [
            {
                "provider": result.provider,
                "quote_count": len(result.quotes),
                "status": "failed" if result.error else "success",
                "error": result.error,
                "elapsed_ms": result.elapsed_ms,
            }
            for result in fetch_result.provider_results
        ]
        provider_errors = [str(item["error"]) for item in provider_health if item.get("error")]
        provider_error = "; ".join(provider_errors) if provider_errors and not quotes else None
        updated = await persist_quotes(session, watchlist, quotes)
        updated_codes = sorted(set(quotes).intersection(watch_codes))
        missing_watch_codes = sorted(watch_codes - set(quotes))
        latest_quotes = await latest_quotes_by_code(session, [item.etf_code for item in watchlist.items])
        local_now = datetime.now(ASIA_SHANGHAI).replace(tzinfo=None)
        quote_audit = _quote_audit_for_watchlist(watch_codes, latest_quotes, local_now)
        stale = sum(1 for item in quote_audit.values() if item["quote_freshness"] == "stale")
        run.status = "degraded" if provider_error else "success"
        run.updated_quote_count = updated
        run.stale_quote_count = stale
        run.error_message = provider_error
        run.finished_at = utcnow()
        run.details_json = {
            **dict(run.details_json or {}),
            "provider_returned": len(quotes),
            "provider_health": provider_health,
            "provider_errors": provider_errors,
            "consensus_counts": fetch_result.consensus_counts,
            "updated_code_count": len(updated_codes),
            "updated_codes": _sample_codes(updated_codes),
            "missing_watch_count": len(missing_watch_codes),
            "missing_watch_codes": _sample_codes(missing_watch_codes),
            "provider_error": provider_error,
            "quote_audit": _sample_mapping(quote_audit),
            "quote_audit_count": len(quote_audit),
            "decision_quote_summary": _decision_quote_summary(quote_audit),
        }
        await session.commit()
        return _result(run)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        failed_run = await session.get(IntradayEtfWatchRun, run_id)
        if failed_run is None:
            raise
        failed_run.status = "failed"
        failed_run.error_message = str(exc)
        failed_run.finished_at = utcnow()
        await session.commit()
        raise


def _quote_audit_for_watchlist(watch_codes: set[str], latest_quotes: dict[str, Any], local_now: datetime) -> dict[str, Any]:
    audit: dict[str, Any] = {}
    for code in sorted(watch_codes):
        quote = latest_quotes.get(code)
        if quote is None:
            audit[code] = {
                "decision_eligible": False,
                "quote_freshness": "unavailable",
                "source": None,
                "quote_time": None,
                "consensus_status": "unavailable",
                "provider_count": 0,
                "fresh_provider_count": 0,
                "price_diff_abs": None,
                "price_diff_pct": None,
                "limitation_reason": "暂无盘中行情。",
            }
            continue
        decision_eligible = is_fresh_decision_quote(quote, local_now)
        if decision_eligible:
            freshness = "fresh"
        elif local_now - quote.quote_time > timedelta(minutes=3):
            freshness = "stale"
        else:
            freshness = getattr(quote, "freshness_status", None) or "display_only"
        audit[code] = {
            "decision_eligible": decision_eligible,
            "quote_freshness": freshness,
            "source": quote.source,
            "quote_time": quote.quote_time.isoformat() if quote.quote_time else None,
            "quote_time_is_fallback": is_quote_time_fallback(quote),
            "consensus_status": quote_consensus_status(quote),
            "provider_count": quote_provider_count(quote),
            "fresh_provider_count": quote_fresh_provider_count(quote),
            "price_diff_abs": quote_price_diff_abs(quote),
            "price_diff_pct": quote_price_diff_pct(quote),
            "limitation_reason": None if decision_eligible else quote_decision_limitation_reason(quote, local_now),
        }
    return audit


def _decision_quote_summary(quote_audit: dict[str, Any]) -> dict[str, Any]:
    covered_count_by_trade_date: dict[str, int] = {}
    latest_quote_time_by_trade_date: dict[str, str] = {}
    for raw_evidence in quote_audit.values():
        if not isinstance(raw_evidence, dict):
            continue
        if (
            raw_evidence.get("decision_eligible") is not True
            or raw_evidence.get("quote_time_is_fallback") is not False
            or raw_evidence.get("quote_freshness") != "fresh"
            or raw_evidence.get("consensus_status")
            in {"diverged", "stale", "unavailable"}
        ):
            continue
        raw_quote_time = raw_evidence.get("quote_time")
        if not isinstance(raw_quote_time, str):
            continue
        try:
            quote_time = datetime.fromisoformat(
                raw_quote_time.strip().replace("Z", "+00:00")
            )
        except ValueError:
            continue
        trade_date = quote_time.date().isoformat()
        covered_count_by_trade_date[trade_date] = (
            covered_count_by_trade_date.get(trade_date, 0) + 1
        )
        normalized_time = quote_time.isoformat()
        previous_time = latest_quote_time_by_trade_date.get(trade_date)
        if previous_time is None or normalized_time > previous_time:
            latest_quote_time_by_trade_date[trade_date] = normalized_time
    return {
        "covered_count_by_trade_date": covered_count_by_trade_date,
        "latest_quote_time_by_trade_date": latest_quote_time_by_trade_date,
    }


def _watchlist_source_counts(items: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for source in item.sources:
            counts[source] = counts.get(source, 0) + 1
    return counts


def _sample_codes(codes: list[str], *, limit: int = 50) -> list[str]:
    sorted_codes = sorted(codes)
    return sorted_codes if len(sorted_codes) <= limit else sorted_codes[:limit]


def _sample_mapping(values: dict[str, Any], *, limit: int = 50) -> dict[str, Any]:
    if len(values) <= limit:
        return values
    return {key: values[key] for key in sorted(values)[:limit]}


async def intraday_etf_cleanup_job(
    session: AsyncSession,
    *,
    retention_trading_days: int = 60,
) -> dict[str, Any]:
    return await summarize_and_cleanup_intraday_quotes(
        session,
        retention_trading_days=retention_trading_days,
    )

def _result(run: IntradayEtfWatchRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "run_type": run.run_type,
        "status": run.status,
        "market_session": run.market_session,
        "watched_count": run.watched_count,
        "updated_quote_count": run.updated_quote_count,
        "stale_quote_count": run.stale_quote_count,
        "alert_count": run.alert_count,
        "email_sent_count": run.email_sent_count,
        "suppressed_count": run.suppressed_count,
        "skipped_reason": run.skipped_reason,
        "error_message": run.error_message,
        "details": dict(run.details_json or {}),
    }

