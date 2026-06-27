from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.defaults.short_research import ASSET_TYPE_ETF, ASSET_TYPE_FUND
from app.services.llm import LLMClient
from app.services.short_etf.data import sync_etf_price_history_from_intraday_snapshot
from app.services.short_research.advisor import run_advisor_generation
from app.services.short_research.service import (
    run_etf_observation_portfolio_optimization,
    run_etf_signal_validation,
    run_signal_generation,
    sync_short_research_data,
)
from app.services.short_research.universe import refresh_etf_universe

SHORT_RESEARCH_DAILY_ASSET_TYPES = [ASSET_TYPE_FUND, ASSET_TYPE_ETF]


def _count(value: Any, key: str) -> int:
    raw = value.get(key, 0) if isinstance(value, dict) else 0
    if isinstance(raw, int | float | str):
        return int(raw)
    return 0


def _signal_result(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status,
        "as_of_date": run.as_of_date.isoformat(),
        "items": int(run.summary_json.get("item_count", 0)),
        "funds": int(run.summary_json.get("fund_count", 0)),
        "etfs": int(run.summary_json.get("etf_count", 0)),
        "conclusion_counts": run.summary_json.get("conclusion_counts", {}),
    }


async def daily_short_research_data_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    results: dict[str, dict[str, Any]] = {}
    for asset_type in SHORT_RESEARCH_DAILY_ASSET_TYPES:
        results[asset_type] = await sync_short_research_data(
            session,
            from_date=today - timedelta(days=120),
            to_date=today,
            asset_type=asset_type,
        )
    return {
        "from_date": (today - timedelta(days=120)).isoformat(),
        "to_date": today.isoformat(),
        "asset_types": SHORT_RESEARCH_DAILY_ASSET_TYPES,
        "fund": results[ASSET_TYPE_FUND],
        "etf": results[ASSET_TYPE_ETF],
        "asset_count": sum(_count(item, "asset_count") for item in results.values()),
        "failed": sum(_count(item, "failed") for item in results.values()),
    }


async def post_close_etf_data_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    snapshot_result = await sync_etf_price_history_from_intraday_snapshot(session, trade_date=today)
    needs_history_provider = bool(snapshot_result.get("needs_history_provider"))
    if _count(snapshot_result, "inserted") + _count(snapshot_result, "updated") > 0:
        if needs_history_provider:
            history_result = await sync_short_research_data(
                session,
                from_date=today - timedelta(days=120),
                to_date=today,
                asset_type=ASSET_TYPE_ETF,
                sync_all_etfs=True,
            )
            return {
                "from_date": (today - timedelta(days=120)).isoformat(),
                "to_date": today.isoformat(),
                "asset_type": ASSET_TYPE_ETF,
                "etf": history_result,
                "snapshot": snapshot_result,
                "history_provider": history_result,
                "asset_count": _count(history_result, "asset_count"),
                "failed": _count(history_result, "failed"),
                "source": "intraday_snapshot_plus_history_provider",
            }
        return {
            "from_date": today.isoformat(),
            "to_date": today.isoformat(),
            "asset_type": ASSET_TYPE_ETF,
            "etf": snapshot_result,
            "asset_count": _count(snapshot_result, "etfs"),
            "failed": 0,
            "source": "intraday_snapshot",
        }

    result = await sync_short_research_data(
        session,
        from_date=today - timedelta(days=120),
        to_date=today,
        asset_type=ASSET_TYPE_ETF,
        sync_all_etfs=True,
    )
    return {
        "from_date": (today - timedelta(days=120)).isoformat(),
        "to_date": today.isoformat(),
        "asset_type": ASSET_TYPE_ETF,
        "etf": result,
        "asset_count": _count(result, "asset_count"),
        "failed": _count(result, "failed"),
        "source": "history_provider",
    }


async def daily_etf_universe_job(session: AsyncSession) -> dict[str, Any]:
    return await refresh_etf_universe(session)


async def daily_short_research_signals_job(session: AsyncSession) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    for asset_type in SHORT_RESEARCH_DAILY_ASSET_TYPES:
        results[asset_type] = _signal_result(await run_signal_generation(session, asset_type=asset_type))
    return {
        "asset_types": SHORT_RESEARCH_DAILY_ASSET_TYPES,
        "fund": results[ASSET_TYPE_FUND],
        "etf": results[ASSET_TYPE_ETF],
        "items": sum(_count(item, "items") for item in results.values()),
        "funds": sum(_count(item, "funds") for item in results.values()),
        "etfs": sum(_count(item, "etfs") for item in results.values()),
    }


async def post_close_etf_signals_job(session: AsyncSession) -> dict[str, Any]:
    run = await run_signal_generation(session, asset_type=ASSET_TYPE_ETF)
    result = _signal_result(run)
    return {
        "asset_type": ASSET_TYPE_ETF,
        "etf": result,
        **result,
    }


async def daily_short_research_advisor_job(
    session: AsyncSession,
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    effective_settings = settings or get_settings()
    results: dict[str, dict[str, Any]] = {}
    for asset_type in SHORT_RESEARCH_DAILY_ASSET_TYPES:
        results[asset_type] = await run_advisor_generation(
            session,
            effective_settings,
            llm_client=llm_client,
            asset_type=asset_type,
        )
    return {
        "asset_types": SHORT_RESEARCH_DAILY_ASSET_TYPES,
        "fund": results[ASSET_TYPE_FUND],
        "etf": results[ASSET_TYPE_ETF],
        "selected": sum(_count(item, "selected") for item in results.values()),
        "succeeded": sum(_count(item, "succeeded") for item in results.values()),
        "failed": sum(_count(item, "failed") for item in results.values()),
    }


async def daily_etf_signal_validation_job(session: AsyncSession) -> dict[str, Any]:
    run = await run_etf_signal_validation(session)
    summary = dict(run.summary_json or {})
    return {
        "run_id": run.id,
        "status": run.status,
        "as_of_date": run.as_of_date.isoformat(),
        "rule_version": run.rule_version,
        "items": len(summary.get("groups", [])),
        "outcome_source": summary.get("outcome_source"),
        "pending_outcomes": summary.get("pending_count", 0),
        "excluded_outcomes": summary.get("excluded_count", 0),
    }


async def post_close_etf_label_outcome_review_job(session: AsyncSession) -> dict[str, Any]:
    return await daily_etf_signal_validation_job(session)


async def daily_etf_label_outcome_review_job(session: AsyncSession) -> dict[str, Any]:
    return await daily_etf_signal_validation_job(session)


async def daily_etf_observation_portfolio_job(session: AsyncSession) -> dict[str, Any]:
    return await post_close_etf_observation_portfolio_job(session)


async def post_close_etf_observation_portfolio_job(session: AsyncSession) -> dict[str, Any]:
    snapshot = await run_etf_observation_portfolio_optimization(session)
    summary = dict(snapshot.summary_json or {})
    constraint_summary = dict(summary.get("constraint_summary") or {})
    return {
        "snapshot_id": snapshot.id,
        "status": snapshot.status,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "cash_weight": summary.get("cash_weight"),
        "weight_sum": summary.get("weight_sum"),
        "unavailable_reason": summary.get("unavailable_reason"),
        "primary_count": constraint_summary.get("primary_count", 0),
        "watch_only_count": constraint_summary.get("watch_only_count", 0),
        "excluded_count": constraint_summary.get("excluded_count", 0),
    }
