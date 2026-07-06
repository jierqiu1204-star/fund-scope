from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.defaults.short_research import ASSET_TYPE_ETF, ASSET_TYPE_FUND
from app.models.entities import EtfPriceHistory
from app.services.etf_exit_calibration import run_etf_exit_hyperopt
from app.services.llm import LLMClient
from app.services.short_etf.data import sync_etf_price_history_from_intraday_snapshot
from app.services.short_research.advisor import run_advisor_generation
from app.services.short_research.backtest import (
    run_etf_portfolio_backtest,
    run_etf_strategy_comparison_backtest,
)
from app.services.short_research.etf_exit_credibility import run_etf_exit_credibility
from app.services.short_research.healthcheck import run_etf_strategy_healthcheck
from app.services.short_research.optimized_allocation import run_etf_optimized_allocation
from app.services.short_research.service import (
    run_etf_label_historical_replay,
    run_etf_observation_portfolio_optimization,
    run_etf_score_bucket_validation,
    run_etf_signal_validation,
    run_signal_generation,
)
from app.services.short_research.theme_catalysts import refresh_theme_catalyst_snapshots
from app.services.short_research.theme_taxonomy import refresh_etf_theme_profiles
from app.services.short_research.universe import refresh_etf_universe
from app.services.workflows.short_research_data import (
    sync_short_research_data_with_tracking_priority as sync_short_research_data,
)

SHORT_RESEARCH_DAILY_ASSET_TYPES = [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
ETF_HISTORY_BACKFILL_ALLOWED_DAYS = (365, 730, 1095)


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
    changed_count = _count(snapshot_result, "inserted") + _count(snapshot_result, "updated")
    deferred_count = _count(snapshot_result, "missing") + _count(snapshot_result, "skipped_too_early")
    if changed_count > 0:
        return {
            "from_date": today.isoformat(),
            "to_date": today.isoformat(),
            "asset_type": ASSET_TYPE_ETF,
            "etf": snapshot_result,
            "snapshot": snapshot_result,
            "asset_count": _count(snapshot_result, "etfs"),
            "failed": 0,
            "source": "intraday_snapshot_partial" if needs_history_provider else "intraday_snapshot",
            "needs_history_provider": needs_history_provider,
            "history_provider_deferred": needs_history_provider,
            "deferred_history_provider_count": deferred_count if needs_history_provider else 0,
        }

    return {
        "from_date": today.isoformat(),
        "to_date": today.isoformat(),
        "asset_type": ASSET_TYPE_ETF,
        "etf": snapshot_result,
        "snapshot": snapshot_result,
        "asset_count": _count(snapshot_result, "etfs"),
        "failed": 0,
        "source": "intraday_snapshot_unavailable",
        "needs_history_provider": True,
        "history_provider_deferred": True,
        "deferred_history_provider_count": deferred_count,
    }


async def _etf_price_history_coverage(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            select(
                func.count(EtfPriceHistory.id),
                func.count(func.distinct(EtfPriceHistory.etf_code)),
                func.min(EtfPriceHistory.trade_date),
                func.max(EtfPriceHistory.trade_date),
            )
        )
    ).one()
    return {
        "rows": int(row[0] or 0),
        "etfs": int(row[1] or 0),
        "earliest_trade_date": row[2].isoformat() if row[2] else None,
        "latest_trade_date": row[3].isoformat() if row[3] else None,
    }


async def etf_history_backfill_job(session: AsyncSession, *, days: int = 730) -> dict[str, Any]:
    backfill_days = days if days in ETF_HISTORY_BACKFILL_ALLOWED_DAYS else 730
    today = date.today()
    from_date = today - timedelta(days=backfill_days)
    result = await sync_short_research_data(
        session,
        from_date=from_date,
        to_date=today,
        asset_type=ASSET_TYPE_ETF,
        sync_all_etfs=True,
    )
    return {
        "from_date": from_date.isoformat(),
        "to_date": today.isoformat(),
        "days": backfill_days,
        "asset_type": ASSET_TYPE_ETF,
        "etf": result,
        "asset_count": _count(result, "asset_count"),
        "failed": _count(result, "failed"),
        "coverage": await _etf_price_history_coverage(session),
        "source": "history_provider_long_backfill",
    }


async def daily_etf_universe_job(session: AsyncSession) -> dict[str, Any]:
    universe = await refresh_etf_universe(session)
    taxonomy = await refresh_etf_theme_profiles(session)
    return {**universe, "universe": universe, "taxonomy": taxonomy}


async def daily_etf_taxonomy_job(session: AsyncSession) -> dict[str, Any]:
    return await refresh_etf_theme_profiles(session)


async def daily_etf_theme_catalyst_job(session: AsyncSession) -> dict[str, Any]:
    return await refresh_theme_catalyst_snapshots(session)


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


async def etf_label_historical_replay_job(
    session: AsyncSession,
    *,
    days: int = 180,
    max_assets: int | None = None,
    batch_size: int = 25,
) -> dict[str, Any]:
    run = await run_etf_label_historical_replay(
        session,
        days=days,
        max_assets=max_assets,
        batch_size=batch_size,
    )
    summary = dict(run.summary_json or {})
    return {
        "run_id": run.id,
        "status": run.status,
        "validation_mode": run.validation_mode,
        "as_of_date": run.as_of_date.isoformat(),
        "rule_version": run.rule_version,
        "days": days,
        "max_assets": max_assets,
        "batch_size": summary.get("batch_size", batch_size),
        "universe_scope": summary.get("universe_scope"),
        "replay_start_date": summary.get("replay_start_date"),
        "replay_end_date": summary.get("replay_end_date"),
        "processed_etfs": summary.get("asset_count", 0),
        "evaluated_etfs": summary.get("evaluated_asset_count", 0),
        "completed_samples": summary.get("completed_samples", 0),
        "excluded_samples": summary.get("excluded_samples", 0),
        "groups": len(summary.get("groups", [])),
    }


async def etf_score_bucket_validation_job(
    session: AsyncSession,
    *,
    days: int = 180,
    score_basis: str = "opportunity",
    top_n: list[int] | None = None,
) -> dict[str, Any]:
    requested_top_n = top_n or [5, 10, 20, 50]
    run = await run_etf_score_bucket_validation(
        session,
        days=days,
        score_basis=score_basis,
        top_n=requested_top_n,
    )
    summary = dict(run.summary_json or {})
    return {
        "run_id": run.id,
        "status": run.status,
        "validation_mode": run.validation_mode,
        "as_of_date": run.as_of_date.isoformat(),
        "rule_version": run.rule_version,
        "days": days,
        "score_basis": summary.get("score_basis", score_basis),
        "top_n": summary.get("top_n", requested_top_n),
        "baseline": summary.get("baseline"),
        "source_signal_runs": summary.get("source_signal_run_count", 0),
        "scored_items": summary.get("scored_item_count", 0),
        "excluded_unavailable_score_count": summary.get("excluded_unavailable_score_count", 0),
        "completed_samples": summary.get("completed_samples", 0),
        "excluded_samples": summary.get("excluded_samples", 0),
        "pending_samples": summary.get("pending_samples", 0),
        "groups": len(summary.get("groups", [])),
    }


async def etf_portfolio_backtest_job(
    session: AsyncSession,
    *,
    days: int = 180,
    max_assets: int = 180,
) -> dict[str, Any]:
    run = await run_etf_portfolio_backtest(session, days=days, max_assets=max_assets)
    return {
        "run_id": run.id,
        "status": run.status,
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "metrics": dict(run.metrics_json or {}),
        "data_coverage": dict(run.data_coverage_json or {}),
        "error_message": run.error_message,
    }


async def etf_strategy_comparison_backtest_job(
    session: AsyncSession,
    *,
    days: int = 180,
    max_assets: int = 180,
) -> dict[str, Any]:
    run = await run_etf_strategy_comparison_backtest(session, days=days, max_assets=max_assets)
    return {
        "run_id": run.id,
        "status": run.status,
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "best_strategy": (run.metrics_json or {}).get("best_strategy"),
        "strategy_count": len((run.metrics_json or {}).get("strategies") or []),
        "data_coverage": dict(run.data_coverage_json or {}),
        "error_message": run.error_message,
    }


async def etf_strategy_healthcheck_job(session: AsyncSession) -> dict[str, Any]:
    snapshot = await run_etf_strategy_healthcheck(session)
    summary = dict(snapshot.summary_json or {})
    return {
        "snapshot_id": snapshot.id,
        "status": snapshot.status,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "conclusion": snapshot.conclusion,
        "evidence_status": snapshot.evidence_status,
        "daily_close_evidence_status": summary.get("daily_close_evidence_status"),
        "intraday_alert_evidence_status": summary.get("intraday_alert_evidence_status"),
        "backtest_evidence_status": summary.get("backtest_evidence_status"),
        "weak_label_count": int(summary.get("weak_label_count", 0)),
        "weak_theme_count": len(summary.get("weak_themes") or []),
        "weak_market_regime_count": len(summary.get("weak_market_regimes") or []),
        "full_window_days": summary.get("full_window_days"),
        "recent_window_days": summary.get("recent_window_days"),
    }


async def etf_optimized_allocation_job(session: AsyncSession) -> dict[str, Any]:
    snapshot = await run_etf_optimized_allocation(session)
    summary = dict(snapshot.summary_json or {})
    data_window = dict(snapshot.data_window_json or {})
    black_litterman = dict((summary.get("methods") or {}).get("black_litterman") or {})
    return {
        "snapshot_id": snapshot.id,
        "status": snapshot.status,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "method_set": snapshot.method_set,
        "unavailable_reason": snapshot.unavailable_reason,
        "method_count": int(summary.get("method_count", 0)),
        "eligible_count": int(data_window.get("candidate_count", 0)),
        "black_litterman_status": black_litterman.get("status"),
        "black_litterman_unavailable_reason": black_litterman.get("unavailable_reason"),
        "black_litterman_candidate_count": black_litterman.get("candidate_count"),
    }


async def etf_exit_hyperopt_job(
    session: AsyncSession,
    *,
    days: int = 730,
    max_assets: int | None = None,
    execution_model: str = "intraday_alert",
    manual_delay_minutes: int = 3,
    universe_scope: str = "all_eligible",
    batch_size: int = 100,
) -> dict[str, Any]:
    run = await run_etf_exit_hyperopt(
        session,
        days=days,
        max_assets=max_assets,
        execution_model=execution_model,
        manual_delay_minutes=manual_delay_minutes,
        universe_scope=universe_scope,
        batch_size=batch_size,
    )
    summary = dict(run.summary_json or {})
    coverage = dict(summary.get("coverage") or summary.get("coverage_funnel") or {})
    return {
        "run_id": run.id,
        "status": run.status,
        "as_of_date": run.as_of_date.isoformat(),
        "objective": run.objective,
        "rule_version": run.rule_version,
        "execution_model": run.execution_model,
        "asset_count": int(summary.get("asset_count", 0)),
        "bucket_count": int(summary.get("bucket_count", 0)),
        "candidate_count": int(summary.get("candidate_count", 0)),
        "rejected_count": int(summary.get("rejected_count", 0)),
        "evidence_insufficient_count": int(summary.get("evidence_insufficient_count", 0)),
        "coverage": coverage,
        "sampled": bool(summary.get("sampled", False)),
        "universe_scope": summary.get("universe_scope"),
        "max_assets": summary.get("max_assets"),
        "batch_size": int(summary.get("batch_size", batch_size)),
        "final_optimized_count": int(summary.get("final_optimized_count", 0)),
        "enough_daily_history_count": int(summary.get("enough_daily_history_count", 0)),
        "enough_intraday_history_count": int(summary.get("enough_intraday_history_count", 0)),
        "manual_delay_minutes": int(summary.get("manual_delay_minutes", manual_delay_minutes)),
        "calibration_rule_version": summary.get("calibration_rule_version"),
        "policy_validation_version": summary.get("policy_validation_version"),
        "contract_hash": summary.get("contract_hash"),
        "auto_applied": False,
        "research_only": True,
        "error_message": run.error_message,
    }


async def etf_exit_signal_credibility_job(
    session: AsyncSession,
    *,
    days: int = 730,
    max_assets: int = 50,
    execution_model: str = "intraday_alert",
    universe_scope: str = "latest_opportunity_top",
) -> dict[str, Any]:
    run = await run_etf_exit_credibility(
        session,
        days=days,
        max_assets=max_assets,
        execution_model=execution_model,
        universe_scope=universe_scope,
    )
    summary = dict(run.summary_json or {})
    return {
        "run_id": run.id,
        "status": run.status,
        "as_of_date": run.as_of_date.isoformat(),
        "execution_model": run.execution_model,
        "signal_version": run.signal_version,
        "exit_rule_version": run.exit_rule_version,
        "contract_hash": run.contract_hash,
        "evidence_status": run.evidence_status,
        "universe_scope": summary.get("universe_scope"),
        "ranking_sort": summary.get("ranking_sort"),
        "requested_top_n": summary.get("requested_top_n"),
        "source_signal_run_id": summary.get("source_signal_run_id"),
        "selected_codes": summary.get("selected_codes", []),
        "asset_count": int(summary.get("asset_count", 0)),
        "event_count": int(summary.get("event_count", 0)),
        "verified_signal_count": int(summary.get("verified_signal_count", 0)),
        "insufficiency_reasons": list(run.insufficiency_reasons_json or []),
        "research_only": True,
        "auto_applied": False,
        "email_sent": False,
        "tracked_position_mutated": False,
        "error_message": run.error_message,
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
        "satellite_count": constraint_summary.get("satellite_count", 0),
        "defensive_count": constraint_summary.get("defensive_count", 0),
        "watch_only_count": constraint_summary.get("watch_only_count", 0),
        "excluded_count": constraint_summary.get("excluded_count", 0),
    }
