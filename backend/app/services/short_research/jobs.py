from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.defaults.short_research import ASSET_TYPE_ETF, ASSET_TYPE_FUND
from app.models.entities import EtfPriceHistory, EtfSyncCursor, EtfThemeProfile, JobRun, utcnow
from app.services.etf_exit_calibration import run_etf_exit_hyperopt
from app.services.llm import LLMClient
from app.services.market_data import ASIA_SHANGHAI, is_etf_exchange_trading_day
from app.services.short_etf.bounded_history_sync import (
    BoundedHistorySyncRequest,
    run_bounded_history_sync_slice,
)
from app.services.short_etf.data import sync_etf_price_history_from_intraday_snapshot
from app.services.short_research.advisor import run_advisor_generation
from app.services.short_research.backtest import run_etf_portfolio_backtest
from app.services.short_research.coverage_policy import (
    ETF_DAILY_DECISION_MIN_COVERAGE,
    ETF_SCORE_PUBLICATION_MIN_COVERAGE,
)
from app.services.short_research.etf_exit_credibility import run_etf_exit_credibility
from app.services.short_research.etf_identity_facts import (
    MAX_IDENTITY_FACT_SLICE_SECONDS,
    IdentityFactIngestionRequest,
    IdentityFactProviderPage,
    TaxonomyFactInput,
    identity_fact_coverage_at_cutoff,
    run_identity_fact_ingestion_slice,
    try_acquire_identity_fact_worker_lock,
)
from app.services.short_research.etf_tracked_underlying import (
    EASTMONEY_TRACKED_UNDERLYING_PROVIDER_VERSION,
    EtfTrackedUnderlyingProviderError,
    fetch_eastmoney_tracked_underlying_page,
)
from app.services.short_research.healthcheck import run_etf_strategy_healthcheck
from app.services.short_research.history_readiness import (
    SCORE_WARMUP_SCOPE,
    SCORE_WARMUP_SESSIONS,
    derived_replay_depth_sessions,
    history_depth_scope,
)
from app.services.short_research.optimized_allocation import run_etf_optimized_allocation
from app.services.short_research.service import (
    run_etf_label_historical_replay,
    run_etf_observation_portfolio_optimization,
    run_etf_signal_validation,
    run_signal_generation,
)
from app.services.short_research.snapshot_publication import (
    SnapshotPublicationError,
    build_etf_coverage_barrier,
)
from app.services.short_research.snapshot_selector import (
    resolve_current_etf_ranking_surface_snapshot,
)
from app.services.short_research.theme_catalysts import refresh_theme_catalyst_snapshots
from app.services.short_research.theme_taxonomy import refresh_etf_theme_profiles
from app.services.short_research.universe import refresh_etf_universe
from app.services.strategy_lab.etf_score_bucket_validation import (
    run_registered_etf_score_bucket_validation as run_etf_score_bucket_validation,
)
from app.services.workflows.etf_daily_research import (
    etf_source_availability_cutoff,
    generate_and_publish_etf_snapshot,
)
from app.services.workflows.etf_history_readiness import (
    current_etf_history_contract_hash,
    read_etf_history_readiness,
)
from app.services.workflows.etf_point_in_time_capture import (
    run_scheduled_production_pit_capture,
)
from app.services.workflows.etf_publish_readiness import (
    run_post_close_etf_publication_readiness,
)
from app.services.workflows.etf_research_history_sync import (
    run_post_publication_etf_research_history_slice,
)
from app.services.workflows.short_research_data import (
    sync_short_research_data_with_tracking_priority as sync_short_research_data,
)

SHORT_RESEARCH_DAILY_ASSET_TYPES = [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
ETF_HISTORY_BACKFILL_ALLOWED_DAYS = (365, 730, 1095)
ETF_CANONICAL_MIN_COVERAGE = 0.95
ETF_TAXONOMY_FACT_RULE_VERSION = "etf_theme_taxonomy_fact_v2"
ETF_TRACKED_UNDERLYING_FACT_SCOPE_PREFIX = (
    f"etf_tracked_underlying_facts:{EASTMONEY_TRACKED_UNDERLYING_PROVIDER_VERSION}"
)
ETF_TRACKED_UNDERLYING_FACT_SCOPE = ETF_TRACKED_UNDERLYING_FACT_SCOPE_PREFIX
ETF_TRACKED_UNDERLYING_SWEEP_LANE = "identity_facts"


def _underlying_sweep_marker(kind: str, sweep_date: date) -> str:
    return f"{kind}:{sweep_date.isoformat()}"


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
        from_date = today if asset_type == ASSET_TYPE_ETF else today - timedelta(days=120)
        results[asset_type] = await sync_short_research_data(
            session,
            from_date=from_date,
            to_date=today,
            asset_type=asset_type,
        )
    asset_count = sum(_count(item, "asset_count") for item in results.values())
    failed_count = sum(_count(item, "failed") for item in results.values())
    result: dict[str, Any] = {
        "from_date": (today - timedelta(days=120)).isoformat(),
        "etf_from_date": today.isoformat(),
        "to_date": today.isoformat(),
        "asset_types": SHORT_RESEARCH_DAILY_ASSET_TYPES,
        "fund": results[ASSET_TYPE_FUND],
        "etf": results[ASSET_TYPE_ETF],
        "asset_count": asset_count,
        "failed": failed_count,
    }
    if failed_count:
        result["job_status"] = "partial" if failed_count < asset_count else "failed"
        result["job_message"] = "daily data provider returned incomplete results"
    elif _count(results[ASSET_TYPE_ETF].get("etfs", {}), "skipped"):
        result["job_status"] = "partial"
        result["job_message"] = "bounded ETF daily sync deferred remaining candidates"
    return result


async def post_close_etf_data_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    snapshot_result = await sync_etf_price_history_from_intraday_snapshot(session, trade_date=today)
    needs_history_provider = bool(snapshot_result.get("needs_history_provider"))
    changed_count = _count(snapshot_result, "inserted") + _count(snapshot_result, "updated")
    deferred_count = _count(snapshot_result, "missing") + _count(snapshot_result, "skipped_too_early")
    if changed_count > 0:
        result = {
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
        if needs_history_provider:
            result["job_status"] = "partial"
            result["job_message"] = "official daily history remains deferred"
        return result

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
        "job_status": "skipped",
        "job_message": "intraday quotes cannot produce official daily history",
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
    today = datetime.now(ASIA_SHANGHAI).date()
    from_date = today - timedelta(days=backfill_days)
    horizons = (1, 3, 5, 10)
    contract_hash = current_etf_history_contract_hash(horizons=horizons)
    readiness = await read_etf_history_readiness(
        session,
        target_date=today,
        horizons=horizons,
    )
    universe = readiness.get("universe") or {}
    eligible_codes = tuple(str(code) for code in (universe.get("codes") or ()))
    universe_hash = str(universe.get("snapshot_hash") or "")
    daily_ratio = float(readiness["daily_freshness"]["coverage_ratio"])
    warmup_ratio = float(readiness["history_depth_61"]["coverage_ratio"])
    if not eligible_codes or len(universe_hash) != 64:
        return {
            "from_date": from_date.isoformat(),
            "to_date": today.isoformat(),
            "days": backfill_days,
            "asset_type": ASSET_TYPE_ETF,
            "asset_count": 0,
            "failed": 0,
            "etf": {
                "status": "skipped",
                "stop_reason": "point_in_time_universe_unavailable",
                "attempted_codes": [],
                "completed_codes": [],
                "exclusions": [],
            },
            "coverage": await _etf_price_history_coverage(session),
            "source": "history_provider_bounded_continuation",
            "lane_scope": SCORE_WARMUP_SCOPE,
            "required_sessions": SCORE_WARMUP_SESSIONS,
            "readiness_before": {
                "daily_freshness_coverage_ratio": daily_ratio,
                "history_depth_61_coverage_ratio": warmup_ratio,
            },
        }
    if eligible_codes and daily_ratio < ETF_DAILY_DECISION_MIN_COVERAGE:
        return {
            "from_date": from_date.isoformat(),
            "to_date": today.isoformat(),
            "days": backfill_days,
            "asset_type": ASSET_TYPE_ETF,
            "asset_count": len(eligible_codes),
            "failed": 0,
            "etf": {
                "status": "skipped",
                "stop_reason": "daily_freshness_below_publication_priority",
                "attempted_codes": [],
                "completed_codes": [],
                "exclusions": [],
            },
            "coverage": await _etf_price_history_coverage(session),
            "source": "history_provider_bounded_continuation",
            "lane_scope": SCORE_WARMUP_SCOPE,
            "required_sessions": SCORE_WARMUP_SESSIONS,
            "readiness_before": {
                "daily_freshness_coverage_ratio": daily_ratio,
                "history_depth_61_coverage_ratio": warmup_ratio,
            },
        }
    if warmup_ratio < ETF_SCORE_PUBLICATION_MIN_COVERAGE:
        lane_scope = SCORE_WARMUP_SCOPE
        required_sessions = SCORE_WARMUP_SESSIONS
    else:
        lane_scope = history_depth_scope(contract_hash)
        required_sessions = derived_replay_depth_sessions(horizons=horizons)
    result = await run_bounded_history_sync_slice(
        session,
        request=BoundedHistorySyncRequest(
            scope=lane_scope,
            contract_hash=contract_hash,
            universe_hash=universe_hash,
            eligible_codes=eligible_codes,
            from_date=from_date,
            to_date=today,
            required_sessions=required_sessions,
        ),
    )
    return {
        "from_date": from_date.isoformat(),
        "to_date": today.isoformat(),
        "days": backfill_days,
        "asset_type": ASSET_TYPE_ETF,
        "etf": {
            "status": result.status,
            "stop_reason": result.stop_reason,
            "attempted_codes": list(result.attempted_codes),
            "completed_codes": list(result.completed_codes),
            "exclusions": [list(item) for item in result.exclusions],
            "fetched_rows": result.fetched_rows,
            "persisted_rows": result.persisted_rows,
            "inserted_rows": result.inserted_rows,
            "updated_rows": result.updated_rows,
            "unchanged_rows": result.unchanged_rows,
            "excluded_rows": result.excluded_rows,
            "max_page_rows": result.max_page_rows,
            "elapsed_seconds": result.elapsed_seconds,
            "peak_rss_bytes": result.peak_rss_bytes,
            "sql_statements": result.sql_statements,
            "max_page_sql_statements": result.max_page_sql_statements,
            "retries": result.retries,
            "last_durable_checkpoint": result.last_durable_checkpoint,
        },
        "asset_count": len(eligible_codes),
        "failed": len(result.exclusions),
        "coverage": await _etf_price_history_coverage(session),
        "source": "history_provider_bounded_continuation",
        "lane_scope": lane_scope,
        "required_sessions": required_sessions,
        "readiness_before": {
            "daily_freshness_coverage_ratio": daily_ratio,
            "history_depth_61_coverage_ratio": warmup_ratio,
        },
    }


async def daily_etf_universe_job(session: AsyncSession) -> dict[str, Any]:
    universe = await refresh_etf_universe(session)
    taxonomy = await refresh_etf_theme_profiles(session)
    result = {
        **universe,
        "as_of_date": date.today().isoformat(),
        "universe": universe,
        "taxonomy": taxonomy,
    }
    if universe.get("authoritative") is not True:
        result["job_status"] = "failed"
        result["job_message"] = "ETF universe discovery is not authoritative"
    return result


async def daily_etf_taxonomy_job(session: AsyncSession) -> dict[str, Any]:
    return await refresh_etf_theme_profiles(session)


async def etf_taxonomy_fact_ingestion_job(
    session: AsyncSession,
) -> dict[str, Any]:
    """Advance one bounded page from current taxonomy into immutable PIT facts."""

    async def fetch_page(codes: tuple[str, ...]) -> IdentityFactProviderPage:
        profiles = list(
            (
                await session.scalars(
                    select(EtfThemeProfile).where(
                        EtfThemeProfile.etf_code.in_(codes),
                    )
                )
            ).all()
        )
        records = []
        for profile in profiles:
            raw_payload = {
                "asset_bucket": profile.asset_bucket,
                "theme_group": profile.theme_group,
                "primary_theme": profile.primary_theme,
                "secondary_themes": list(profile.secondary_themes_json or []),
                "classification_source": profile.classification_source,
                "classification_confidence": profile.classification_confidence,
                "classification_reason": profile.classification_reason or "",
            }
            records.append(
                TaxonomyFactInput(
                    etf_code=profile.etf_code,
                    external_source_id=(
                        f"{ETF_TAXONOMY_FACT_RULE_VERSION}:"
                        f"{profile.etf_code}:{profile.updated_at.isoformat()}"
                    ),
                    source=profile.classification_source,
                    provider_version=ETF_TAXONOMY_FACT_RULE_VERSION,
                    observed_at=profile.updated_at,
                    confidence=profile.classification_confidence,
                    rule_version=ETF_TAXONOMY_FACT_RULE_VERSION,
                    asset_bucket=profile.asset_bucket,
                    theme_group=profile.theme_group,
                    primary_theme=profile.primary_theme,
                    secondary_themes=list(profile.secondary_themes_json or []),
                    classification_source=profile.classification_source,
                    classification_reason=profile.classification_reason or "未提供分类说明",
                    raw_payload=raw_payload,
                )
            )
        return IdentityFactProviderPage(taxonomy_records=tuple(records))

    result = await run_identity_fact_ingestion_slice(
        session,
        request=IdentityFactIngestionRequest(
            scope=(
                f"etf_taxonomy_facts:{ETF_TAXONOMY_FACT_RULE_VERSION}:{date.today().isoformat()}"
            ),
            target_page_size=20,
            estimated_seconds_per_etf=0.1,
        ),
        fetch_page=fetch_page,
    )
    await session.commit()
    persisted = result.persisted or None
    return {
        "status": result.status,
        # JobRunner persists this as the business status.  A completed slice
        # is the normal scheduler success; provider failures and bounded
        # partial pages must remain visible to operations.
        "job_status": (
            result.status if result.status in {"failed", "partial", "skipped"} else None
        ),
        "job_message": (
            "ETF taxonomy fact ingestion failed"
            if result.status == "failed"
            else None
        ),
        "stop_reason": result.stop_reason,
        "selected_count": len(result.selected_codes),
        "cursor_before": result.cursor_before,
        "cursor_after": result.cursor_after,
        "has_more": result.has_more,
        "elapsed_seconds": result.elapsed_seconds,
        "taxonomy_facts_inserted": (
            persisted.taxonomy_facts_inserted if persisted is not None else 0
        ),
        "taxonomy_facts_existing": (
            persisted.taxonomy_facts_existing if persisted is not None else 0
        ),
        "error_summary": result.error_summary,
    }


async def etf_tracked_underlying_ingestion_job(
    session: AsyncSession,
) -> dict[str, Any]:
    try:
        async with asyncio.timeout(MAX_IDENTITY_FACT_SLICE_SECONDS):
            return await _run_etf_tracked_underlying_ingestion_job(session)
    except TimeoutError:
        await session.rollback()
        return {
            "status": "partial",
            "job_status": "partial",
            "stop_reason": "slice_timeout",
            "selected_count": 0,
            "cursor_before": None,
            "cursor_after": None,
            "has_more": True,
            "elapsed_seconds": MAX_IDENTITY_FACT_SLICE_SECONDS,
            "underlying_facts_inserted": 0,
            "underlying_facts_existing": 0,
            "unresolved_count": 0,
            "provider_failure_count": 0,
            "provider_failures": [],
            "coverage": None,
            "error_summary": "ETF tracked-underlying ingestion exceeded 55 seconds",
        }


async def _run_etf_tracked_underlying_ingestion_job(
    session: AsyncSession,
) -> dict[str, Any]:
    """Advance one bounded page of explicit ETF tracked-underlying facts."""

    if not await try_acquire_identity_fact_worker_lock(session):
        await session.rollback()
        return {
            "status": "skipped",
            "job_status": "skipped",
            "stop_reason": "identity_fact_worker_lock_busy",
            "selected_count": 0,
            "underlying_facts_inserted": 0,
            "underlying_facts_existing": 0,
            "unresolved_count": 0,
            "provider_failure_count": 0,
            "provider_failures": [],
            "coverage": None,
            "error_summary": None,
        }

    scope = ETF_TRACKED_UNDERLYING_FACT_SCOPE
    sweep_date = date.today()
    cursor = await session.get(EtfSyncCursor, scope)
    done_marker = _underlying_sweep_marker("done", sweep_date)
    retry_marker = _underlying_sweep_marker("retry", sweep_date)
    if cursor is not None and cursor.last_lane == done_marker:
        coverage = await identity_fact_coverage_at_cutoff(session, cutoff=utcnow())
        coverage_payload = coverage.as_dict()
        coverage_payload["cutoff"] = coverage.cutoff.isoformat()
        await session.commit()
        return {
            "status": "complete",
            "stop_reason": "daily_sweep_complete",
            "selected_count": 0,
            "cursor_before": cursor.last_regular_code,
            "cursor_after": cursor.last_regular_code,
            "has_more": False,
            "elapsed_seconds": 0.0,
            "underlying_facts_inserted": 0,
            "underlying_facts_existing": 0,
            "unresolved_count": 0,
            "provider_failure_count": 0,
            "provider_failures": [],
            "coverage": coverage_payload,
            "error_summary": None,
        }
    if (
        cursor is not None
        and cursor.last_lane is not None
        and cursor.last_lane.startswith(("done:", "retry:"))
        and cursor.last_lane.rsplit(":", 1)[-1] != sweep_date.isoformat()
        and (
            cursor.last_lane.startswith("done:")
            or cursor.last_regular_code is None
        )
    ):
        # A completed or failed sweep from an earlier day starts from the
        # head today. An incomplete sweep without a marker continues where it
        # stopped, so a full universe cannot starve at the tail.
        cursor.last_regular_code = None
        cursor.last_lane = ETF_TRACKED_UNDERLYING_SWEEP_LANE
        await session.flush()

    provider_page: IdentityFactProviderPage | None = None

    async def fetch_page(codes: tuple[str, ...]) -> IdentityFactProviderPage:
        nonlocal provider_page
        try:
            provider_page = await fetch_eastmoney_tracked_underlying_page(codes)
        except EtfTrackedUnderlyingProviderError as exc:
            provider_page = IdentityFactProviderPage(
                provider_errors=exc.failures,
            )
            raise
        return provider_page

    result = await run_identity_fact_ingestion_slice(
        session,
        request=IdentityFactIngestionRequest(
            # Keep one persistent cursor across dates; the lane marker resets
            # only after a completed daily sweep.
            scope=scope,
            target_page_size=20,
            # The adapter is serial and each request has an 8-second bound;
            # reserve enough time for persistence and coverage so a slow page
            # cannot consume the whole 55-second slice.
            estimated_seconds_per_etf=8.0,
            commit_reserve_seconds=5.0,
        ),
        fetch_page=fetch_page,
    )
    persisted = result.persisted
    coverage = await identity_fact_coverage_at_cutoff(session, cutoff=utcnow())
    coverage_payload = coverage.as_dict()
    coverage_payload["cutoff"] = coverage.cutoff.isoformat()
    provider_failures = tuple(provider_page.provider_errors if provider_page else ())
    provider_records = tuple(provider_page.underlying_records if provider_page else ())
    status = result.status
    stop_reason = result.stop_reason
    error_summary = result.error_summary
    if provider_failures and result.persisted is not None:
        # Persist valid records and advance the bounded page while exposing
        # each malformed/unavailable code for the next daily retry.  A failed
        # code is never represented as a fabricated unresolved identity.
        status = "partial" if provider_records else "failed"
        stop_reason = "provider_partial_failure" if provider_records else "provider_fetch_failed"
        failure_text = "; ".join(f"{code}:{reason}" for code, reason in provider_failures)
        error_summary = failure_text[:160]
    cursor_after = await session.get(EtfSyncCursor, scope)
    had_retry_marker = cursor_after is not None and cursor_after.last_lane == retry_marker
    if status == "complete":
        if cursor_after is None:
            cursor_after = EtfSyncCursor(scope=scope)
            session.add(cursor_after)
        cursor_after.last_regular_code = None
        cursor_after.last_lane = done_marker
        cursor_after.updated_at = utcnow()
    elif provider_failures or status == "failed" or had_retry_marker:
        if cursor_after is None:
            cursor_after = EtfSyncCursor(scope=scope)
            session.add(cursor_after)
        if (provider_failures or status == "failed") and not result.has_more:
            # A failed final page still completes the ordinal sweep. Clear the
            # cursor so the next daily retry starts at the head; an incomplete
            # failed page retains its last code and continues across days.
            cursor_after.last_regular_code = None
        cursor_after.last_lane = retry_marker
        cursor_after.updated_at = utcnow()
    elif cursor_after is not None:
        cursor_after.last_lane = ETF_TRACKED_UNDERLYING_SWEEP_LANE
        cursor_after.updated_at = utcnow()
    await session.commit()
    unresolved_count = (
        sum(
            record.identity_state == "unresolved"
            for record in provider_records
        )
        if persisted is not None
        else 0
    )
    return {
        "status": status,
        # JobRunner persists this as the business status. A completed slice
        # is the normal scheduler success; provider failures and bounded
        # partial pages must remain visible to operations.
        "job_status": (
            status if status in {"failed", "partial", "skipped"} else None
        ),
        "job_message": (
            "ETF tracked-underlying provider fetch failed"
            if status == "failed"
            else None
        ),
        "stop_reason": stop_reason,
        "selected_count": len(result.selected_codes),
        "cursor_before": result.cursor_before,
        "cursor_after": (
            cursor_after.last_regular_code
            if cursor_after is not None
            else result.cursor_after
        ),
        "has_more": result.has_more,
        "elapsed_seconds": result.elapsed_seconds,
        "underlying_facts_inserted": (
            persisted.underlying_facts_inserted if persisted is not None else 0
        ),
        "underlying_facts_existing": (
            persisted.underlying_facts_existing if persisted is not None else 0
        ),
        "unresolved_count": unresolved_count,
        "provider_failure_count": (
            len(provider_failures)
            if provider_failures
            else len(result.selected_codes)
            if result.stop_reason == "provider_fetch_failed"
            else 0
        ),
        "provider_failures": [
            {"etf_code": code, "reason": reason} for code, reason in provider_failures
        ],
        "coverage": coverage_payload,
        "error_summary": error_summary,
    }


async def daily_short_research_fund_data_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    result = await sync_short_research_data(
        session,
        from_date=today - timedelta(days=120),
        to_date=today,
        asset_type=ASSET_TYPE_FUND,
    )
    return {
        "from_date": (today - timedelta(days=120)).isoformat(),
        "to_date": today.isoformat(),
        "asset_types": [ASSET_TYPE_FUND],
        "fund": result,
        "asset_count": _count(result, "asset_count"),
        "failed": _count(result, "failed"),
    }


async def daily_etf_theme_catalyst_job(session: AsyncSession) -> dict[str, Any]:
    return await refresh_theme_catalyst_snapshots(session)


async def daily_short_research_signals_job(session: AsyncSession) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {
        ASSET_TYPE_FUND: _signal_result(
            await run_signal_generation(session, asset_type=ASSET_TYPE_FUND)
        ),
        ASSET_TYPE_ETF: {
            "asset_type": ASSET_TYPE_ETF,
            "status": "delegated_to_canonical_v3",
            "items": 0,
            "funds": 0,
            "etfs": 0,
        },
    }
    return {
        "asset_types": SHORT_RESEARCH_DAILY_ASSET_TYPES,
        "fund": results[ASSET_TYPE_FUND],
        "etf": results[ASSET_TYPE_ETF],
        "items": sum(_count(item, "items") for item in results.values()),
        "funds": sum(_count(item, "funds") for item in results.values()),
        "etfs": sum(_count(item, "etfs") for item in results.values()),
    }


def post_close_etf_decision_context(now: datetime | None = None) -> tuple[date, datetime] | None:
    local_now = now or datetime.now(ASIA_SHANGHAI)
    if local_now.tzinfo is not None:
        local_now = local_now.astimezone(ASIA_SHANGHAI)
    trade_date = local_now.date()
    if not is_etf_exchange_trading_day(trade_date) or local_now.time() < time(15, 0):
        return None
    return trade_date, datetime.combine(trade_date, time(15, 0))


def publication_readiness_decision_context(
    now: datetime | None = None,
) -> tuple[date, datetime] | None:
    context = post_close_etf_decision_context(now)
    if context is None:
        return None
    local_now = now or datetime.now(ASIA_SHANGHAI)
    if local_now.tzinfo is not None:
        local_now = local_now.astimezone(ASIA_SHANGHAI)
    if not time(15, 15) <= local_now.time() <= time(22, 55):
        return None
    return context


async def _latest_authoritative_etf_universe_refresh(
    session: AsyncSession,
    *,
    trade_date: date,
) -> tuple[bool, str | None]:
    run = await session.scalar(
        select(JobRun)
        .where(JobRun.job_name == "daily_etf_universe")
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(1)
    )
    if run is None:
        return False, "missing_daily_universe_refresh"
    details = dict(run.details_json or {})
    universe = details.get("universe")
    universe_details = universe if isinstance(universe, dict) else details
    observed_date = details.get("as_of_date") or universe_details.get("as_of_date")
    if observed_date != trade_date.isoformat():
        return False, "stale_daily_universe_refresh"
    if run.status != "success" or universe_details.get("authoritative") is not True:
        return False, str(universe_details.get("discovery_error") or "universe_not_authoritative")
    return True, None


def _waiting_etf_publication(
    *,
    trade_date: date,
    reason: str,
    coverage: dict[str, Any] | None = None,
    sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "asset_type": ASSET_TYPE_ETF,
        "as_of_date": trade_date.isoformat(),
        "status": "waiting",
        "publication_state": "not_run",
        "reason": reason,
        "job_status": "partial",
        "job_message": reason,
    }
    if coverage is not None:
        result["coverage"] = coverage
    if sync is not None:
        result["sync"] = sync
    return result


async def _publish_etf_snapshot_at_gate(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
    source_availability_cutoff: datetime,
    coverage: dict[str, Any],
    sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        run = await generate_and_publish_etf_snapshot(
            session,
            trade_date=trade_date,
            decision_cutoff=decision_cutoff,
            source_availability_cutoff=source_availability_cutoff,
        )
    except SnapshotPublicationError as exc:
        await session.rollback()
        return _waiting_etf_publication(
            trade_date=trade_date,
            reason=f"score_coverage_or_publication_gate_failed: {exc}",
            coverage=coverage,
            sync=sync,
        )
    result = _signal_result(run)
    return {
        "asset_type": ASSET_TYPE_ETF,
        "publication_state": run.publication_state,
        "coverage": coverage,
        **({"sync": sync} if sync is not None else {}),
        "etf": result,
        **result,
    }


async def post_close_etf_signals_job(session: AsyncSession) -> dict[str, Any]:
    context = post_close_etf_decision_context()
    if context is None:
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "publication_state": "not_run",
            "reason": "no_completed_trading_session",
        }
    trade_date, decision_cutoff = context
    authoritative, universe_error = await _latest_authoritative_etf_universe_refresh(
        session,
        trade_date=trade_date,
    )
    if not authoritative:
        return _waiting_etf_publication(
            trade_date=trade_date,
            reason="universe_not_authoritative",
            sync={"error": universe_error},
        )
    current = await resolve_current_etf_ranking_surface_snapshot(
        session,
        required_trade_date=trade_date,
        ranking_surface="research",
    )
    if current.state == "ready" and current.run is not None:
        result = _signal_result(current.run)
        return {
            "asset_type": ASSET_TYPE_ETF,
            "publication_state": "published",
            "already_published": True,
            "etf": result,
            **result,
        }
    source_cutoff = etf_source_availability_cutoff(trade_date)
    coverage = await build_etf_coverage_barrier(
        session,
        as_of_trade_date=trade_date,
        data_cutoff=source_cutoff,
    )
    coverage_payload = coverage.to_dict()
    if not coverage.expected_codes or coverage.coverage_ratio < ETF_CANONICAL_MIN_COVERAGE:
        return _waiting_etf_publication(
            trade_date=trade_date,
            reason="adjusted_price_coverage_below_publication_gate",
            coverage=coverage_payload,
        )
    return await _publish_etf_snapshot_at_gate(
        session,
        trade_date=trade_date,
        decision_cutoff=decision_cutoff,
        source_availability_cutoff=source_cutoff,
        coverage=coverage_payload,
    )


async def post_close_etf_adjusted_sync_job(session: AsyncSession) -> dict[str, Any]:
    context = publication_readiness_decision_context()
    if context is None:
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "publication_state": "not_run",
            "reason": "no_completed_trading_session",
        }
    trade_date, decision_cutoff = context
    return await run_post_close_etf_publication_readiness(
        session,
        trade_date=trade_date,
        decision_cutoff=decision_cutoff,
    )


async def production_etf_pit_capture_job(
    session: AsyncSession,
    settings: Settings,
) -> dict[str, Any]:
    context = publication_readiness_decision_context()
    if context is None:
        return {
            "status": "skipped",
            "reason": "pit_no_completed_trading_session",
            "research_only": True,
            "production_mutation_allowed": False,
        }
    trade_date, _decision_cutoff = context
    code_version = (
        settings.etf_pit_code_version.strip()
        or settings.readiness_deploy_artifact.strip()
    )
    return await run_scheduled_production_pit_capture(
        session,
        trade_date=trade_date,
        enabled=settings.etf_pit_capture_enabled,
        code_version=code_version,
        artifact_dir=settings.etf_pit_artifact_dir,
    )


async def post_publication_etf_research_history_job(
    session: AsyncSession,
) -> dict[str, Any]:
    return await run_post_publication_etf_research_history_slice(session)


async def etf_research_input_history_sync_job(
    session: AsyncSession,
) -> dict[str, Any]:
    return await run_post_publication_etf_research_history_slice(
        session,
        input_repair=True,
    )


async def daily_short_research_advisor_job(
    session: AsyncSession,
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    effective_settings = settings or get_settings()
    results: dict[str, dict[str, Any]] = {}
    for asset_type in SHORT_RESEARCH_DAILY_ASSET_TYPES:
        try:
            results[asset_type] = await run_advisor_generation(
                session,
                effective_settings,
                llm_client=llm_client,
                asset_type=asset_type,
            )
        except ValueError as exc:
            if asset_type != ASSET_TYPE_ETF or "canonical ETF 排名快照不可用" not in str(exc):
                raise
            results[asset_type] = {
                "asset_type": ASSET_TYPE_ETF,
                "status": "waiting",
                "selected": 0,
                "succeeded": 0,
                "failed": 0,
                "reason": "canonical_etf_snapshot_unavailable",
            }
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
        "protection_guard_version": summary.get("protection_guard_version"),
        "policy_class": summary.get("policy_class"),
        "approved_for_live": bool(summary.get("approved_for_live", False)),
        "approval_status": summary.get("approval_status"),
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
