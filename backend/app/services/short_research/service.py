from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from statistics import mean, median, pstdev
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import (
    ASSET_TYPE_ETF,
    ASSET_TYPE_FUND,
    DEFAULT_SHORT_RESEARCH_ASSETS,
    DEFAULT_SHORT_RESEARCH_ETF_CODES,
    DEFAULT_SHORT_RESEARCH_FUND_CODES,
    SHORT_RESEARCH_ASSET_BY_KEY,
    ShortResearchAsset,
    is_short_term_eligible_name,
)
from app.models.entities import (
    EtfDataHealth,
    EtfIntradayLatestQuote,
    EtfLabelOutcome,
    EtfLabelReplaySample,
    EtfObservationPortfolioItem,
    EtfObservationPortfolioSnapshot,
    EtfPriceHistory,
    EtfSignalValidationItem,
    EtfSignalValidationRun,
    EtfSyncCursor,
    EtfThemeExposure,
    Fund,
    FundNavHistory,
    JobRun,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
    utcnow,
)
from app.services.etf_research_evidence import (
    EVIDENCE_STATUS_LEGACY,
    EVIDENCE_STATUS_WAITING,
    build_allocation_contract,
    build_evidence_summary,
)
from app.services.jobs import sync_fund_nav_history
from app.services.portfolio_allocation import (
    PORTFOLIO_CORRELATION_MIN_POINTS,
    PORTFOLIO_ENTRY_TIMING_FORBIDDEN,
    PORTFOLIO_ENTRY_TIMING_OK,
    PORTFOLIO_HIGH_CORRELATION,
    PORTFOLIO_LAYER_DEFENSIVE,
    PORTFOLIO_LAYER_EXCLUDED,
    PORTFOLIO_LAYER_PRIMARY,
    PORTFOLIO_LAYER_SATELLITE,
    PORTFOLIO_LAYER_WATCH_ONLY,
    PORTFOLIO_MODE_CASH_WAIT,
    PORTFOLIO_MODE_DEFENSIVE,
    PORTFOLIO_MODE_NEUTRAL,
    PORTFOLIO_MODE_RISK_ON,
    PORTFOLIO_RISK_FLAGS_FORBIDDEN,
    PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT,
    PORTFOLIO_RISK_FLAGS_WATCH_ONLY,
    PORTFOLIO_SATELLITE_EXPOSURE_CAP,
    PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
    PORTFOLIO_SINGLE_WEIGHT_CAP,
    PORTFOLIO_THEME_EXPOSURE_CAP,
    PORTFOLIO_TOTAL_EXPOSURE_CAP,
)
from app.services.short_etf.data import sync_etf_price_history
from app.services.short_research.dynamic_thresholds import (
    ThresholdPricePoint,
    dynamic_threshold_context,
)
from app.services.short_research.factors import (
    FACTOR_PROFILE_UNAVAILABLE_VERSION,
    build_asset_factor_payload,
)
from app.services.short_research.optimized_allocation import (
    latest_optimized_allocation_snapshot,
    optimized_allocation_payload,
)
from app.services.short_research.ranking import (
    FINAL_SCORE_VERSION,
    RankingRecord,
    apply_final_score_limits,
    build_final_score_breakdowns,
)
from app.services.short_research.ranking_contract import scope_kind_for_filters
from app.services.short_research.sector_trends import build_sector_trend_payloads
from app.services.short_research.snapshot_selector import snapshot_metadata
from app.services.short_research.theme_catalysts import (
    build_asset_opportunity_payload,
    latest_theme_catalyst_snapshots_by_key,
)
from app.services.short_research.theme_taxonomy import (
    UNKNOWN_GROUP,
    UNKNOWN_THEME,
    classify_etf_theme,
    theme_coverage_summary,
)
from app.services.short_research.universe import refresh_etf_universe

RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_RUNNING = "running"

CONCLUSION_WATCH = "短线观察"
CONCLUSION_HIGH_WATCH = "高位观察"
CONCLUSION_CAUTION = "谨慎观察"
CONCLUSION_REJECT = "不适合短线"
CONCLUSION_INSUFFICIENT = "数据不足"
ENTRY_TIMING_TREND_CONTINUATION = "趋势延续"
ENTRY_TIMING_HEALTHY_PULLBACK = "健康回踩"
ENTRY_TIMING_CHASE_RISK = "冲高别追"
ENTRY_TIMING_BREAK_WAIT = "跌破等待"
ENTRY_TIMING_VOLUME_WEAKENING = "放量转弱"
ENTRY_TIMING_INSUFFICIENT = "数据不足"

STALE_DATA_DAYS = 7
MIN_AVERAGE_TURNOVER = 50_000_000
DEFAULT_ETF_SYNC_BATCH_SIZE = 100
DEFAULT_ETF_SYNC_MAX_BATCHES = 1
ETF_DAILY_SYNC_CURSOR_SCOPE = "short_research_daily"
ETF_JOB_FRESHNESS_WINDOWS = {
    "daily_etf_universe": timedelta(days=2),
    "daily_etf_theme_catalyst": timedelta(days=2),
    "daily_short_research_data": timedelta(days=2),
    "intraday_etf_cleanup": timedelta(days=2),
}
UNIVERSE_DEFAULT = "default"
UNIVERSE_ALL = "all"
UNIVERSE_ILLIQUID = "illiquid"
CHASE_RETURN_20D = 0.25
CHASE_RETURN_60D = 0.45
SURGE_RETURN_5D = 0.08
HIGH_DAILY_VOLATILITY_20D = 0.035
LARGE_DRAWDOWN_60D = -0.18
_PORTFOLIO_SINGLE_WEIGHT_CAP = PORTFOLIO_SINGLE_WEIGHT_CAP
_PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP = PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP
_PORTFOLIO_SATELLITE_EXPOSURE_CAP = PORTFOLIO_SATELLITE_EXPOSURE_CAP
_PORTFOLIO_TOTAL_EXPOSURE_CAP = PORTFOLIO_TOTAL_EXPOSURE_CAP
_PORTFOLIO_THEME_EXPOSURE_CAP = PORTFOLIO_THEME_EXPOSURE_CAP
_PORTFOLIO_HIGH_CORRELATION = PORTFOLIO_HIGH_CORRELATION
_PORTFOLIO_CORRELATION_MIN_POINTS = PORTFOLIO_CORRELATION_MIN_POINTS
_PORTFOLIO_ENTRY_TIMING_OK = PORTFOLIO_ENTRY_TIMING_OK
_PORTFOLIO_ENTRY_TIMING_FORBIDDEN = PORTFOLIO_ENTRY_TIMING_FORBIDDEN
_PORTFOLIO_RISK_FLAGS_FORBIDDEN = PORTFOLIO_RISK_FLAGS_FORBIDDEN
_PORTFOLIO_RISK_FLAGS_WATCH_ONLY = PORTFOLIO_RISK_FLAGS_WATCH_ONLY
_PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT = PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT
MARKET_REGIME_RISK_ON = "risk_on"
MARKET_REGIME_NEUTRAL = "neutral"
MARKET_REGIME_DEFENSIVE = "defensive"
MARKET_REGIME_CASH_WAIT = "cash_wait"


@dataclass(frozen=True)
class PricePoint:
    point_date: date
    value: float
    close: float | None = None
    nav: float | None = None
    turnover: float | None = None
    pct_change: float | None = None


@dataclass(frozen=True)
class ComputedAsset:
    metadata: ShortResearchAsset
    rank: int | None
    total_score: float
    conclusion: str
    latest_date: date | None
    latest_value: float | None
    usable_days: int
    sample_level: str
    metrics: dict[str, Any]
    score_breakdown: dict[str, Any]
    risk_flags: list[str]
    rationale: dict[str, Any]
    source_note: str
    entry_timing_label: str
    entry_timing_reason: str


@dataclass(frozen=True)
class EtfSyncSelection:
    codes: list[str]
    last_priority_code: str | None
    last_regular_code: str | None
    last_lane: str | None


def allowed_conclusions() -> set[str]:
    return {
        CONCLUSION_WATCH,
        CONCLUSION_HIGH_WATCH,
        CONCLUSION_CAUTION,
        CONCLUSION_REJECT,
        CONCLUSION_INSUFFICIENT,
    }


async def ensure_short_research_universe(session: AsyncSession) -> dict[str, int]:
    funds_created = 0
    etfs_created = 0
    for item in DEFAULT_SHORT_RESEARCH_ASSETS:
        eligible = is_short_term_eligible_name(item.name)
        if item.asset_type == ASSET_TYPE_FUND:
            existing = await session.scalar(select(Fund).where(Fund.code == item.code))
            if existing is None:
                session.add(
                    Fund(
                        code=item.code,
                        name=item.name,
                        category=item.category,
                        tracking_index_code=item.tracking_index_code,
                        target_allocation=0.0,
                        is_watchlist=eligible,
                    )
                )
                funds_created += 1
            else:
                existing.name = item.name
                existing.category = item.category
                existing.tracking_index_code = item.tracking_index_code
                existing.is_watchlist = eligible
            continue

        existing_etf = await session.scalar(select(TradableEtf).where(TradableEtf.code == item.code))
        if existing_etf is None:
            session.add(
                TradableEtf(
                    code=item.code,
                    name=item.name,
                    exchange=item.exchange or "",
                    theme_tags_json=list(item.theme_tags),
                    trading_rule_label=item.trading_rule_label,
                    asset_class=item.category,
                    is_short_term_eligible=eligible,
                    is_watchlist=eligible,
                )
            )
            etfs_created += 1
        else:
            existing_etf.name = item.name
            existing_etf.exchange = item.exchange or existing_etf.exchange
            if not existing_etf.theme_tags_json:
                existing_etf.theme_tags_json = list(item.theme_tags)
            existing_etf.trading_rule_label = item.trading_rule_label
            existing_etf.asset_class = item.category
            existing_etf.is_short_term_eligible = eligible
            existing_etf.is_watchlist = bool(existing_etf.is_watchlist or eligible)
        for theme in item.theme_tags:
            exposure = await session.scalar(
                select(EtfThemeExposure).where(
                    EtfThemeExposure.etf_code == item.code,
                    EtfThemeExposure.theme == theme,
                )
            )
            if exposure is None:
                session.add(EtfThemeExposure(etf_code=item.code, theme=theme, weight=1.0, source="short_research"))
    await session.commit()
    return {"funds_created": funds_created, "etfs_created": etfs_created}


def _metadata(asset_type: str, code: str, name: str | None = None) -> ShortResearchAsset:
    existing = SHORT_RESEARCH_ASSET_BY_KEY.get((asset_type, code))
    if existing is not None:
        return existing
    fallback_name = name or code
    if asset_type == ASSET_TYPE_ETF:
        return ShortResearchAsset(
            ASSET_TYPE_ETF,
            code,
            fallback_name,
            "etf",
            ("ETF",),
            "交易所 ETF",
            "T+1 股票 ETF",
            exchange="",
        )
    return ShortResearchAsset(
        ASSET_TYPE_FUND,
        code,
        fallback_name,
        "fund",
        ("基金",),
        "场外基金",
        "场外基金，按净值确认",
    )


def _metadata_from_etf_row(row: TradableEtf) -> ShortResearchAsset:
    profile = classify_etf_theme(
        code=row.code,
        name=row.name,
        asset_class=row.asset_class,
        theme_tags=list(row.theme_tags_json or []),
    )
    tags = tuple(
        dict.fromkeys(
            [
                *(row.theme_tags_json or ["ETF"]),
                profile.primary_theme,
                profile.theme_group,
                profile.asset_bucket,
                *profile.secondary_themes,
            ]
        )
    )
    tags = tuple(item for item in tags if item and item not in {UNKNOWN_GROUP})
    direction = "交易所 ETF"
    if row.asset_class == "broad_index":
        direction = "宽基指数 ETF"
    elif row.asset_class == "sector":
        direction = "行业主题 ETF"
    elif row.asset_class == "cross_border":
        direction = "跨境市场 ETF"
    elif row.asset_class == "bond":
        direction = "债券 ETF"
    elif row.asset_class == "commodity":
        direction = "商品 ETF"
    return ShortResearchAsset(
        ASSET_TYPE_ETF,
        row.code,
        row.name,
        row.asset_class or "etf",
        tags,
        direction,
        row.trading_rule_label or "证券账户 T+1 ETF",
        exchange=row.exchange,
    )


async def _metadata_for_code(session: AsyncSession, asset_type: str, code: str) -> ShortResearchAsset:
    if asset_type == ASSET_TYPE_ETF:
        row = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
        if row is not None:
            return _metadata_from_etf_row(row)
    static = SHORT_RESEARCH_ASSET_BY_KEY.get((asset_type, code))
    if static is not None:
        return static
    if asset_type == ASSET_TYPE_FUND:
        fund = await session.scalar(select(Fund).where(Fund.code == code))
        if fund is not None:
            return _metadata(asset_type, code, fund.name)
    return _metadata(asset_type, code)


async def _available_assets(
    session: AsyncSession,
    *,
    asset_type: str | None = None,
    codes: list[str] | None = None,
) -> list[ShortResearchAsset]:
    assets: list[ShortResearchAsset] = []
    if asset_type in (None, ASSET_TYPE_FUND):
        assets.extend(
            item
            for item in DEFAULT_SHORT_RESEARCH_ASSETS
            if item.asset_type == ASSET_TYPE_FUND
            and is_short_term_eligible_name(item.name)
            and (not codes or item.code in codes)
        )
    if asset_type in (None, ASSET_TYPE_ETF):
        query = select(TradableEtf).where(TradableEtf.is_short_term_eligible.is_(True))
        if codes:
            query = query.where(TradableEtf.code.in_(codes))
        rows = await session.scalars(query.order_by(TradableEtf.code.asc()))
        assets.extend(_metadata_from_etf_row(row) for row in rows.all())
    return assets


def _matches_signal_config(
    run: ShortResearchSignalRun,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> bool:
    config = run.config_json or {}
    if asset_type is not None and config.get("asset_type") != asset_type:
        return False
    if theme is not None and config.get("theme") != theme:
        return False
    if codes is not None and sorted(config.get("codes") or []) != sorted(codes):
        return False
    return True


async def _latest_signal_run(
    session: AsyncSession,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> ShortResearchSignalRun | None:
    rows = (
        await session.scalars(
            select(ShortResearchSignalRun)
            .where(ShortResearchSignalRun.status == RUN_STATUS_SUCCESS)
            .order_by(
                ShortResearchSignalRun.as_of_date.desc(),
                ShortResearchSignalRun.finished_at.desc(),
                ShortResearchSignalRun.id.desc(),
            )
        )
    ).all()
    if asset_type is None and theme is None and codes is None:
        return cast(ShortResearchSignalRun | None, rows[0] if rows else None)
    for run in rows:
        if _matches_signal_config(run, asset_type=asset_type, theme=theme, codes=codes):
            return run
        config = run.config_json or {}
        if asset_type is not None and config.get("asset_type") is None and theme is None:
            item_query = select(ShortResearchSignalItem.id).where(
                ShortResearchSignalItem.run_id == run.id,
                ShortResearchSignalItem.asset_type == asset_type,
            )
            if codes:
                item_query = item_query.where(ShortResearchSignalItem.asset_code.in_(codes))
            legacy_item_id = await session.scalar(item_query.limit(1))
            if legacy_item_id is not None:
                return run
    return None


async def latest_signal_run(
    session: AsyncSession,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> ShortResearchSignalRun | None:
    return await _latest_signal_run(session, asset_type=asset_type, theme=theme, codes=codes)


async def list_signal_items(session: AsyncSession, run_id: int) -> list[ShortResearchSignalItem]:
    rows = await session.scalars(
        select(ShortResearchSignalItem)
        .where(ShortResearchSignalItem.run_id == run_id)
        .order_by(ShortResearchSignalItem.rank.asc(), ShortResearchSignalItem.asset_type.asc())
    )
    return list(rows.all())


def _date_metric(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _float_metric(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int_metric(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def has_unavailable_theme_catalyst(metrics: Mapping[str, Any]) -> bool:
    status = str(metrics.get("catalyst_status") or "").lower()
    if status == "unavailable":
        return True
    limitations = metrics.get("catalyst_limitations") or []
    if isinstance(limitations, list | tuple):
        if any("主题催化数据不可用" in str(item) for item in limitations):
            return True
    summary = str(metrics.get("catalyst_summary") or "")
    return "暂无可用于评分的主题催化事件" in summary


def has_available_opportunity_score(metrics: Mapping[str, Any]) -> bool:
    if not isinstance(metrics.get("opportunity_score"), int | float):
        return False
    factor_profile_version = str(metrics.get("factor_profile_version") or "")
    if (
        factor_profile_version
        and factor_profile_version != FACTOR_PROFILE_UNAVAILABLE_VERSION
        and isinstance(metrics.get("factor_profile_score"), int | float)
    ):
        return True
    if not has_unavailable_theme_catalyst(metrics):
        return True
    return str(metrics.get("sector_trend_status") or "") == "success" and isinstance(
        metrics.get("sector_trend_score"),
        int | float,
    )


def _final_decision_score_from_breakdown(
    score_breakdown: Mapping[str, Any] | None,
    fallback: float | int | None,
) -> float | None:
    final_breakdown = (score_breakdown or {}).get("final_score_v2")
    if isinstance(final_breakdown, Mapping):
        final_score = final_breakdown.get("final_score")
        if isinstance(final_score, int | float):
            return float(final_score)
    if isinstance(fallback, int | float):
        return float(fallback)
    return None


def _final_decision_score(asset: ComputedAsset) -> float | None:
    return _final_decision_score_from_breakdown(asset.score_breakdown, asset.total_score)


async def _metadata_map_for_signal_items(
    session: AsyncSession,
    items: list[ShortResearchSignalItem],
) -> dict[tuple[str, str], ShortResearchAsset]:
    by_key: dict[tuple[str, str], ShortResearchAsset] = {}
    missing_etf_codes: list[str] = []
    missing_fund_codes: list[str] = []
    for item in items:
        key = (item.asset_type, item.asset_code)
        static = SHORT_RESEARCH_ASSET_BY_KEY.get(key)
        if static is not None:
            by_key[key] = static
        elif item.asset_type == ASSET_TYPE_ETF:
            missing_etf_codes.append(item.asset_code)
        elif item.asset_type == ASSET_TYPE_FUND:
            missing_fund_codes.append(item.asset_code)
    if missing_etf_codes:
        rows = await session.scalars(select(TradableEtf).where(TradableEtf.code.in_(set(missing_etf_codes))))
        for row in rows.all():
            by_key[(ASSET_TYPE_ETF, row.code)] = _metadata_from_etf_row(row)
    if missing_fund_codes:
        rows = await session.scalars(select(Fund).where(Fund.code.in_(set(missing_fund_codes))))
        for row in rows.all():
            by_key[(ASSET_TYPE_FUND, row.code)] = _metadata(ASSET_TYPE_FUND, row.code, row.name)
    return by_key


def _cached_asset_from_signal_item(
    item: ShortResearchSignalItem,
    metadata: ShortResearchAsset,
    *,
    as_of_date: date | None,
) -> ComputedAsset:
    metrics = dict(item.metrics_json or {})
    rationale = dict(item.rationale_json or {})
    entry_timing_label = str(
        metrics.get("entry_timing_label")
        or rationale.get("entry_timing_label")
        or ENTRY_TIMING_INSUFFICIENT
    )
    entry_timing_reason = str(
        metrics.get("entry_timing_reason")
        or rationale.get("entry_timing_reason")
        or "这条排序缓存缺少今日买点维度，请重新生成短线排序。"
    )
    metrics.setdefault("entry_timing_label", entry_timing_label)
    metrics.setdefault("entry_timing_reason", entry_timing_reason)
    rationale.setdefault("entry_timing_label", entry_timing_label)
    rationale.setdefault("entry_timing_reason", entry_timing_reason)
    score_breakdown = dict(item.score_breakdown_json or {})
    final_score_breakdown = score_breakdown.get("final_score_v2")
    score_version = (
        metrics.get("score_version")
        or score_breakdown.get("score_version")
        or (final_score_breakdown.get("score_version") if isinstance(final_score_breakdown, dict) else None)
    )
    if not score_version:
        score_version = "legacy"
        metrics["score_version"] = score_version
        metrics["score_confidence"] = "legacy"
        score_breakdown["score_version"] = score_version
        score_breakdown["final_score_v2"] = {
            "score_version": score_version,
            "final_score": float(item.total_score),
            "confidence": "legacy",
            "components": {},
            "limitation_reasons": ["旧口径排序缓存，请重新生成短线排序获得新评分拆解。"],
        }
    latest_date = _date_metric(metrics.pop("latest_date", None)) or as_of_date
    latest_value = _float_metric(metrics.pop("latest_value", None))
    usable_days = _int_metric(metrics.pop("usable_days", None)) or 0
    sample_level = str(metrics.pop("sample_level", "") or _sample_level(usable_days))
    source_note = str(
        metrics.pop(
            "source_note",
            "公开 ETF 日线数据" if item.asset_type == ASSET_TYPE_ETF else "公开基金净值数据",
        )
    )
    return ComputedAsset(
        metadata=metadata,
        rank=item.rank,
        total_score=float(item.total_score),
        conclusion=item.conclusion,
        latest_date=latest_date,
        latest_value=latest_value,
        usable_days=usable_days,
        sample_level=sample_level,
        metrics=metrics,
        score_breakdown=score_breakdown,
        risk_flags=list(item.risk_flags_json or []),
        rationale=rationale,
        source_note=source_note,
        entry_timing_label=entry_timing_label,
        entry_timing_reason=entry_timing_reason,
    )


async def cached_signal_assets(
    session: AsyncSession,
    run: ShortResearchSignalRun,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
    q: str | None = None,
    sort: str = "score",
    universe: str = UNIVERSE_DEFAULT,
    limit: int | None = None,
    offset: int = 0,
    observation_labels: set[str] | None = None,
    entry_labels: set[str] | None = None,
) -> tuple[list[ComputedAsset], int]:
    if universe not in {UNIVERSE_DEFAULT, UNIVERSE_ALL, UNIVERSE_ILLIQUID}:
        raise ValueError("ETF universe 只支持 default、all、illiquid")
    query = select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run.id)
    if asset_type is not None:
        query = query.where(ShortResearchSignalItem.asset_type == asset_type)
    if codes:
        query = query.where(ShortResearchSignalItem.asset_code.in_(codes))
    rows = await session.scalars(query.order_by(ShortResearchSignalItem.rank.asc()))
    items = list(rows.all())
    metadata_by_key = await _metadata_map_for_signal_items(session, items)
    code_set = set(codes or [])
    keyword = q.strip().lower() if q else None
    observation_filters = observation_labels or set()
    entry_filters = entry_labels or set()
    assets: list[ComputedAsset] = []
    for item in items:
        metadata = metadata_by_key.get((item.asset_type, item.asset_code)) or _metadata(
            item.asset_type,
            item.asset_code,
        )
        if not _matches_filters(metadata, asset_type=asset_type, theme=theme, codes=list(code_set) if code_set else None):
            continue
        if keyword and keyword not in metadata.code.lower() and keyword not in metadata.name.lower():
            continue
        asset = _cached_asset_from_signal_item(item, metadata, as_of_date=run.as_of_date)
        if observation_filters and asset.conclusion not in observation_filters:
            continue
        if entry_filters and asset.entry_timing_label not in entry_filters:
            continue
        if item.asset_type == ASSET_TYPE_ETF and universe == UNIVERSE_DEFAULT and not bool(
            asset.metrics.get("default_display_eligible", True)
        ):
            continue
        if item.asset_type == ASSET_TYPE_ETF and universe == UNIVERSE_ILLIQUID and bool(
            asset.metrics.get("default_display_eligible", True)
        ):
            continue
        assets.append(asset)
    assets.sort(key=lambda asset: _sort_key(asset, sort), reverse=True)
    ranked = [replace(asset, rank=index) for index, asset in enumerate(assets, start=1)]
    total = len(ranked)
    if offset:
        ranked = ranked[offset:]
    if limit is not None:
        ranked = ranked[:limit]
    return ranked, total


async def latest_data_date(session: AsyncSession) -> date | None:
    latest_fund = await session.scalar(select(func.max(FundNavHistory.nav_date)))
    latest_etf = await session.scalar(select(func.max(EtfPriceHistory.trade_date)))
    dates = [item for item in [latest_fund, latest_etf] if item is not None]
    return max(dates) if dates else None


async def _fund_series(session: AsyncSession, code: str, as_of_date: date | None = None) -> list[PricePoint]:
    query = select(FundNavHistory).where(FundNavHistory.fund_code == code)
    if as_of_date is not None:
        query = query.where(FundNavHistory.nav_date <= as_of_date)
    rows = await session.scalars(query.order_by(FundNavHistory.nav_date.asc()))
    return [
        PricePoint(point_date=row.nav_date, value=row.accumulated_nav, nav=row.nav)
        for row in rows.all()
        if row.accumulated_nav > 0
    ]


def _research_adjusted_value(row: EtfPriceHistory) -> float | None:
    value = row.research_adjusted_value
    if (
        row.decision_eligible is not True
        or row.research_price_basis != "total_return_adjusted"
        or value is None
        or not math.isfinite(value)
        or value <= 0
    ):
        return None
    return float(value)


async def _etf_series(session: AsyncSession, code: str, as_of_date: date | None = None) -> list[PricePoint]:
    query = select(EtfPriceHistory).where(EtfPriceHistory.etf_code == code)
    if as_of_date is not None:
        query = query.where(EtfPriceHistory.trade_date <= as_of_date)
    rows = await session.scalars(query.order_by(EtfPriceHistory.trade_date.asc()))
    series: list[PricePoint] = []
    for row in rows.all():
        research_value = _research_adjusted_value(row)
        if research_value is None:
            continue
        series.append(
            PricePoint(
                point_date=row.trade_date,
                value=research_value,
                close=row.close,
                turnover=row.turnover,
                pct_change=row.pct_change / 100,
            )
        )
    return series


async def _series_for_asset(
    session: AsyncSession,
    metadata: ShortResearchAsset,
    as_of_date: date | None = None,
) -> list[PricePoint]:
    if metadata.asset_type == ASSET_TYPE_ETF:
        return await _etf_series(session, metadata.code, as_of_date)
    return await _fund_series(session, metadata.code, as_of_date)


def _window_return(series: list[PricePoint], lookback: int) -> float | None:
    if len(series) <= lookback:
        return None
    start = series[-lookback - 1].value
    end = series[-1].value
    return end / start - 1 if start else None


def _drawdown_series(series: list[PricePoint]) -> list[float]:
    peak = 0.0
    values: list[float] = []
    for item in series:
        peak = max(peak, item.value)
        values.append(item.value / peak - 1 if peak else 0.0)
    return values


def _max_drawdown(series: list[PricePoint]) -> float | None:
    if not series:
        return None
    return min(_drawdown_series(series))


def _daily_returns(series: list[PricePoint]) -> list[float]:
    returns: list[float] = []
    for index, item in enumerate(series[1:], start=1):
        previous = series[index - 1].value
        if previous:
            returns.append(item.value / previous - 1)
    return returns


def _sample_level(usable_days: int) -> str:
    if usable_days < 20:
        return "不足 20 个可用交易日，不能形成短线观察结论"
    if usable_days < 60:
        return "20-59 个可用交易日，样本很短"
    if usable_days < 120:
        return "60-119 个可用交易日，短样本"
    if usable_days < 250:
        return "120 个可用交易日以上，可做短线观察"
    return "250 个可用交易日以上，历史背景较充分"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{value * 100:.2f}%"


def _mean_value(points: list[PricePoint]) -> float | None:
    values = [item.value for item in points if item.value > 0]
    return mean(values) if values else None


def _distance_to_average(current: float | None, average: float | None) -> float | None:
    if current is None or average is None or average <= 0:
        return None
    return current / average - 1


def _latest_day_return(series: list[PricePoint]) -> float | None:
    if not series:
        return None
    latest = series[-1]
    if latest.pct_change is not None:
        return latest.pct_change
    if len(series) < 2:
        return None
    previous = series[-2].value
    return latest.value / previous - 1 if previous else None


def _pullback_from_high(series: list[PricePoint], window: int) -> float | None:
    recent = series[-window:]
    if not recent:
        return None
    high = max(item.value for item in recent)
    latest = recent[-1].value
    return latest / high - 1 if high else None


def _entry_timing_metrics(
    metadata: ShortResearchAsset,
    series: list[PricePoint],
    as_of_date: date,
    *,
    return_5d: float | None,
    return_20d: float | None,
    return_60d: float | None,
    average_turnover_20d: float | None,
    dynamic_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest = series[-1] if series else None
    latest_value = latest.value if latest else None
    latest_date = latest.point_date if latest else None
    ma5 = _mean_value(series[-5:])
    ma10 = _mean_value(series[-10:])
    ma20 = _mean_value(series[-20:])
    today_return = _latest_day_return(series)
    distance_to_ma5 = _distance_to_average(latest_value, ma5)
    distance_to_ma10 = _distance_to_average(latest_value, ma10)
    distance_to_ma20 = _distance_to_average(latest_value, ma20)
    pullback_5d = _pullback_from_high(series, 5)
    pullback_20d = _pullback_from_high(series, 20)
    volume_ratio_20d = None
    if metadata.asset_type == ASSET_TYPE_ETF and latest and latest.turnover is not None and average_turnover_20d:
        volume_ratio_20d = latest.turnover / average_turnover_20d

    base = {
        "today_return_pct": today_return,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "distance_to_ma5_pct": distance_to_ma5,
        "distance_to_ma10_pct": distance_to_ma10,
        "pullback_from_5d_high_pct": pullback_5d,
        "pullback_from_20d_high_pct": pullback_20d,
        "volume_ratio_20d": volume_ratio_20d,
        "dynamic_threshold_context": dynamic_context or {},
    }
    if latest is None or len(series) < 20:
        reason = "公开历史不足 20 个可用交易日，今天不做买点判断。"
        return {**base, "entry_timing_label": ENTRY_TIMING_INSUFFICIENT, "entry_timing_reason": reason}
    if latest_date is None or (as_of_date - latest_date).days > STALE_DATA_DAYS:
        reason = "最新公开数据已经滞后，今天不做买点判断。"
        return {**base, "entry_timing_label": ENTRY_TIMING_INSUFFICIENT, "entry_timing_reason": reason}
    if today_return is None or ma5 is None or ma10 is None or ma20 is None:
        reason = "缺少今天涨跌或均线数据，今天不做买点判断。"
        return {**base, "entry_timing_label": ENTRY_TIMING_INSUFFICIENT, "entry_timing_reason": reason}

    positive_trend = (return_5d or 0.0) > 0 and (return_20d or 0.0) > 0 and (return_60d or 0.0) > 0
    thresholds = dict((dynamic_context or {}).get("thresholds") or {})
    drop_wait = float(thresholds.get("drop_wait", -0.025))
    healthy_pullback_min = float(thresholds.get("healthy_pullback_min", -0.015))
    healthy_pullback_max = float(thresholds.get("healthy_pullback_max", -0.002))
    chase_daily = float(thresholds.get("chase_daily", 0.025))
    ma_overextension = float(thresholds.get("ma_overextension", 0.035))
    return_20_chase = float(thresholds.get("return_20_chase", 0.10))
    return_60_chase = float(thresholds.get("return_60_chase", 0.25))
    premium_state = str((dynamic_context or {}).get("premium_state") or "unavailable")
    threshold_note = str((dynamic_context or {}).get("reason") or "使用固定保守阈值。")
    heavy_volume = volume_ratio_20d is not None and volume_ratio_20d >= 1.5
    below_ma5_ma10 = (
        latest_value is not None
        and distance_to_ma5 is not None
        and distance_to_ma10 is not None
        and distance_to_ma5 < 0
        and distance_to_ma10 < 0
    )
    below_ma20 = distance_to_ma20 is not None and distance_to_ma20 < 0
    near_or_above_ma10 = distance_to_ma10 is not None and distance_to_ma10 >= -0.005

    if premium_state in {"high", "extreme"}:
        reason = f"折溢价状态为 {premium_state}，结构风险偏高；{threshold_note}"
        return {**base, "entry_timing_label": ENTRY_TIMING_CHASE_RISK, "entry_timing_reason": reason}
    if today_return < 0 and (heavy_volume or below_ma20):
        reason = (
            f"今天 {_format_percent(today_return)}，价格已低于20日线或成交额明显放大，"
            f"短线结构转弱，先等待新信号。{threshold_note}"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_VOLUME_WEAKENING, "entry_timing_reason": reason}
    if today_return <= drop_wait or below_ma5_ma10 or ((return_5d or 0.0) < 0 and not near_or_above_ma10):
        reason = (
            f"今天 {_format_percent(today_return)}，已触及动态等待线 {_format_percent(drop_wait)} "
            f"或跌破5/10日线附近，短线趋势开始变弱，适合先等待。{threshold_note}"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_BREAK_WAIT, "entry_timing_reason": reason}
    if today_return >= chase_daily and (
        (return_20d or 0.0) >= return_20_chase
        or (return_60d or 0.0) >= return_60_chase
        or (distance_to_ma5 or 0.0) >= ma_overextension
    ):
        reason = (
            f"今天 {_format_percent(today_return)} 已高于动态冲高线 {_format_percent(chase_daily)}，"
            f"近20日 {_format_percent(return_20d)}、近60日 {_format_percent(return_60d)} 处于偏热区间。{threshold_note}"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_CHASE_RISK, "entry_timing_reason": reason}
    if positive_trend and healthy_pullback_min <= today_return <= healthy_pullback_max and near_or_above_ma10 and not heavy_volume:
        reason = (
            f"近5/20/60日仍为正，今天 {_format_percent(today_return)}，"
            f"处在动态健康回踩区间 {_format_percent(healthy_pullback_min)} 到 {_format_percent(healthy_pullback_max)}，"
            f"仍在10日线附近或上方，适合继续观察。{threshold_note}"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_HEALTHY_PULLBACK, "entry_timing_reason": reason}
    if positive_trend and today_return > healthy_pullback_min and near_or_above_ma10:
        reason = (
            f"近5/20/60日仍为正，今天 {_format_percent(today_return)}，"
            f"未跌破动态等待线 {_format_percent(drop_wait)}，价格仍在10日线附近或上方。{threshold_note}"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_TREND_CONTINUATION, "entry_timing_reason": reason}

    reason = (
        f"今天 {_format_percent(today_return)}，趋势条件不够清晰，"
        "短线买点需要等待更多确认。"
    )
    return {**base, "entry_timing_label": ENTRY_TIMING_BREAK_WAIT, "entry_timing_reason": reason}


def _risk_text(flags: list[str]) -> str:
    if not flags:
        return "暂未触发主要风险标签。"
    explanations = {
        "数据不足": "可用历史太短，分数稳定性不足。",
        "样本很短": "只有较短历史，容易受单一行情影响。",
        "短样本": "历史背景仍偏短，需要继续观察。",
        "数据滞后": "最新公开数据不够新，短线信号可能失效。",
        "追高风险": "近 20 日或 60 日涨幅已经偏高，继续上冲的不确定性更大。",
        "连续大涨": "近 5 日上行较快，情绪可能偏热。",
        "高波动": "最近日线波动偏大，短期净值或价格可能快速反复。",
        "回撤较大": "最近阶段从高点回落幅度较大。",
        "流动性不足": "成交活跃度偏弱，ETF 短线跟踪的摩擦可能更高。",
    }
    return "；".join(f"{flag}：{explanations.get(flag, '需要额外谨慎。')}" for flag in flags)


def _score_metrics(metadata: ShortResearchAsset, series: list[PricePoint], as_of_date: date) -> dict[str, Any]:
    usable_days = len(series)
    latest_date = series[-1].point_date if series else None
    latest_value = series[-1].value if series else None
    volatility_points = series[-21:]
    last_60 = series[-60:]
    returns_20 = _daily_returns(volatility_points)
    return_5d = _window_return(series, 5)
    return_10d = _window_return(series, 10)
    return_20d = _window_return(series, 20)
    return_60d = _window_return(series, 60)
    volatility_20d = pstdev(returns_20) if len(returns_20) == 20 else None
    max_drawdown_60d = _max_drawdown(last_60)
    average_turnover_20d = None
    average_turnover_60d = None
    turnover_20_count = 0
    turnover_60_count = 0
    if metadata.asset_type == ASSET_TYPE_ETF:
        turnovers_20d = [item.turnover for item in series[-20:] if item.turnover is not None]
        turnovers_60d = [item.turnover for item in last_60 if item.turnover is not None]
        turnover_20_count = len(turnovers_20d)
        turnover_60_count = len(turnovers_60d)
        average_turnover_20d = mean(turnovers_20d) if turnover_20_count == 20 else None
        average_turnover_60d = mean(turnovers_60d) if turnover_60_count == 60 else None
    theme_profile = classify_etf_theme(
        code=metadata.code,
        name=metadata.name,
        asset_class=metadata.category,
        theme_tags=list(metadata.theme_tags),
    )
    ma5 = _mean_value(series[-5:])
    distance_to_ma5 = _distance_to_average(latest_value, ma5)
    threshold_points = [
        ThresholdPricePoint(value=item.value, pct_change=item.pct_change)
        for item in series
        if item.value > 0
    ]
    dynamic_context = dynamic_threshold_context(
        asset_bucket=theme_profile.asset_bucket,
        theme_group=theme_profile.theme_group,
        points=threshold_points,
        today_return=_latest_day_return(series),
        return_5d=return_5d,
        return_20d=return_20d,
        return_60d=return_60d,
        volatility_20d=volatility_20d,
        max_drawdown_60d=max_drawdown_60d,
        distance_to_ma5=distance_to_ma5,
    )
    dynamic_thresholds = dict(dynamic_context.get("thresholds") or {})
    return_5_surge = float(dynamic_thresholds.get("return_5_surge", SURGE_RETURN_5D))
    return_20_chase = float(dynamic_thresholds.get("return_20_chase", CHASE_RETURN_20D))
    return_60_chase = float(dynamic_thresholds.get("return_60_chase", CHASE_RETURN_60D))
    high_volatility = float(dynamic_thresholds.get("high_volatility", HIGH_DAILY_VOLATILITY_20D))
    large_drawdown = float(dynamic_thresholds.get("large_drawdown", LARGE_DRAWDOWN_60D))

    risk_flags: list[str] = []
    if usable_days < 20:
        risk_flags.append("数据不足")
    elif usable_days < 60:
        risk_flags.append("样本很短")
    elif usable_days < 120:
        risk_flags.append("短样本")
    if latest_date is None or (as_of_date - latest_date).days > STALE_DATA_DAYS:
        risk_flags.append("数据滞后")
    if (return_20d or 0.0) > return_20_chase or (return_60d or 0.0) > return_60_chase:
        risk_flags.append("追高风险")
    if (return_5d or 0.0) > return_5_surge:
        risk_flags.append("连续大涨")
    if volatility_20d is not None and volatility_20d > high_volatility:
        risk_flags.append("高波动")
    if max_drawdown_60d is not None and max_drawdown_60d < large_drawdown:
        risk_flags.append("回撤较大")
    if metadata.asset_type == ASSET_TYPE_ETF and (
        average_turnover_20d is None or average_turnover_20d < MIN_AVERAGE_TURNOVER
    ):
        risk_flags.append("流动性不足")

    trend_score = round(
        max(
            0.0,
            min(
                100.0,
                50.0
                + max(min(return_5d or 0.0, 0.12), -0.12) * 150
                + max(min(return_10d or 0.0, 0.18), -0.18) * 110
                + max(min(return_20d or 0.0, 0.30), -0.30) * 85
                + max(min(return_60d or 0.0, 0.60), -0.60) * 35,
            ),
        ),
        2,
    )
    risk_score = 100.0
    risk_penalties = {
        "数据不足": 55,
        "样本很短": 18,
        "短样本": 8,
        "数据滞后": 35,
        "追高风险": 22,
        "连续大涨": 12,
        "高波动": 18,
        "回撤较大": 18,
        "流动性不足": 35,
    }
    for flag in risk_flags:
        risk_score -= risk_penalties.get(flag, 0)
    risk_score = round(max(0.0, risk_score), 2)
    liquidity_score = 70.0
    if metadata.asset_type == ASSET_TYPE_ETF:
        liquidity_score = round(max(0.0, min(100.0, (average_turnover_20d or 0.0) / 1_000_000)), 2)
    total_score = round(trend_score * 0.55 + risk_score * 0.30 + liquidity_score * 0.15, 2)
    total_score, score_limitations = apply_final_score_limits(total_score, risk_flags=risk_flags)
    entry_timing = _entry_timing_metrics(
        metadata,
        series,
        as_of_date,
        return_5d=return_5d,
        return_20d=return_20d,
        return_60d=return_60d,
        average_turnover_20d=average_turnover_20d,
        dynamic_context=dynamic_context,
    )

    return {
        "usable_days": usable_days,
        "latest_date": latest_date,
        "latest_value": latest_value,
        "return_5d": return_5d,
        "return_10d": return_10d,
        "return_20d": return_20d,
        "return_60d": return_60d,
        "volatility_20d": volatility_20d,
        "max_drawdown_60d": max_drawdown_60d,
        "average_turnover_20d": average_turnover_20d,
        "average_turnover_60d": average_turnover_60d,
        "effective_windows": {
            "return_5d": {"close_count": min(len(series), 6), "required_close_count": 6},
            "return_10d": {"close_count": min(len(series), 11), "required_close_count": 11},
            "return_20d": {"close_count": min(len(series), 21), "required_close_count": 21},
            "return_60d": {"close_count": min(len(series), 61), "required_close_count": 61},
            "volatility_20d": {
                "close_count": len(volatility_points),
                "return_count": len(returns_20),
                "required_close_count": 21,
                "required_return_count": 20,
            },
            "max_drawdown_60d": {"close_count": len(last_60), "required_close_count": 60},
            "average_turnover_20d": {"observation_count": turnover_20_count, "required_count": 20},
            "average_turnover_60d": {"observation_count": turnover_60_count, "required_count": 60},
        },
        "theme_profile": theme_profile.as_dict(),
        "dynamic_threshold_context": dynamic_context,
        "trend_score": trend_score,
        "risk_score": risk_score,
        "liquidity_score": liquidity_score,
        "total_score": total_score,
        "score_limitations": score_limitations,
        "risk_flags": risk_flags,
        **entry_timing,
    }


def _conclusion(metrics: dict[str, Any]) -> str:
    risk_flags = list(metrics["risk_flags"])
    total_score = float(metrics["total_score"])
    if "数据不足" in risk_flags or "数据滞后" in risk_flags:
        return CONCLUSION_INSUFFICIENT
    if "流动性不足" in risk_flags:
        return CONCLUSION_REJECT
    if {"追高风险", "连续大涨", "高波动"}.intersection(risk_flags) and total_score >= 65:
        return CONCLUSION_HIGH_WATCH
    if total_score >= 72:
        return CONCLUSION_WATCH
    if total_score >= 50:
        return CONCLUSION_CAUTION
    return CONCLUSION_REJECT


def _label_meaning(conclusion: str) -> str:
    return {
        CONCLUSION_WATCH: "进入观察清单，表示趋势和风险条件相对更好，但不是交易指令。",
        CONCLUSION_HIGH_WATCH: "分数不低但风险也被触发，重点防止追高和波动。",
        CONCLUSION_CAUTION: "条件不够突出，适合继续看图和等待更多数据。",
        CONCLUSION_REJECT: "短线条件不适合，通常是流动性、风险或趋势条件较弱。",
        CONCLUSION_INSUFFICIENT: "数据不足或滞后，不能形成有效短线结论。",
    }.get(conclusion, "研究标签只表示观察状态，不是交易指令。")


_LABEL_VALIDATION_WINDOWS = (1, 3, 5, 10)
_LABEL_VALIDATION_MAX_ASSETS = 120
_LABEL_VALIDATION_MIN_SAMPLES = 10
_LABEL_VALIDATION_RULE_VERSION = "label_validation_v1"
VALIDATION_MODE_FORWARD_LIVE = "forward_live"
VALIDATION_MODE_HISTORICAL_REPLAY = "historical_replay"
VALIDATION_MODE_SCORE_BUCKET_REPLAY = "score_bucket_replay"
_LABEL_REPLAY_DEFAULT_DAYS = 180
_LABEL_REPLAY_DEFAULT_BATCH_SIZE = 25
_LABEL_REPLAY_MIN_SAMPLES = 30
_LABEL_REPLAY_SUFFICIENT_SAMPLES = 100
_LABEL_REPLAY_SCOPE_ALL_ELIGIBLE = "all_eligible"
_LABEL_REPLAY_SCOPE_LIMITED = "eligible_limited"
_SCORE_BUCKET_VALIDATION_RULE_VERSION = "score_bucket_replay_v1"
_SCORE_BUCKET_DEFAULT_DAYS = 180
_SCORE_BUCKET_DEFAULT_TOP_N = (5, 10, 20, 50)
_SCORE_BUCKET_SCORE_BASIS = "opportunity"
_SCORE_BUCKET_BASELINE = "all_scored"
_SCORE_BUCKET_SCORE_VERSION = "final_score_v3"
_SCORE_BUCKET_SCORE_FIELD = "ranking_score"
_SCORE_BUCKET_PRICE_BASIS = "total_return_adjusted"


def _forward_drawdown(series: list[PricePoint]) -> float | None:
    if len(series) < 2:
        return None
    return _max_drawdown(series)


def _validation_confidence(sample_count: int, *, recent_median: float | None = None, all_median: float | None = None) -> str:
    if sample_count >= 50:
        if recent_median is not None and all_median is not None and recent_median < min(0.0, all_median - 0.01):
            return "recent_weakening"
        return "sufficient"
    if sample_count >= 20:
        return "limited"
    return "insufficient"


def _validation_confidence_label(confidence: str) -> str:
    return {
        "sufficient": "样本充足",
        "limited": "样本有限",
        "recent_weakening": "近期走弱",
        "insufficient": "样本不足",
    }.get(confidence, "样本不足")


def _replay_validation_confidence(
    sample_count: int,
    *,
    coverage: float,
    recent_median: float | None = None,
    all_median: float | None = None,
) -> str:
    if sample_count < _LABEL_REPLAY_MIN_SAMPLES or coverage < 0.6:
        return "insufficient"
    if recent_median is not None and all_median is not None and recent_median < min(0.0, all_median - 0.01):
        return "recent_weakening"
    if sample_count >= _LABEL_REPLAY_SUFFICIENT_SAMPLES and coverage >= 0.8:
        return "sufficient"
    return "limited"


def _outcome_entry_timing(item: ShortResearchSignalItem) -> str:
    metrics = dict(item.metrics_json or {})
    rationale = dict(item.rationale_json or {})
    return str(metrics.get("entry_timing_label") or rationale.get("entry_timing_label") or ENTRY_TIMING_INSUFFICIENT)


def _signal_item_decision_eligible(item: ShortResearchSignalItem) -> tuple[bool, str | None]:
    metrics = dict(item.metrics_json or {})
    if metrics.get("default_display_eligible") is False:
        return False, "display_only_or_unqualified"
    reliability = str(metrics.get("data_reliability") or metrics.get("price_source") or "verified")
    if reliability in {"estimated", "stale", "unavailable", "display_only"}:
        return False, f"unreliable_{reliability}"
    if item.conclusion == CONCLUSION_INSUFFICIENT:
        return False, "insufficient_signal_data"
    return True, None


async def _etf_price_rows_until(
    session: AsyncSession,
    code: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[EtfPriceHistory]:
    query = select(EtfPriceHistory).where(EtfPriceHistory.etf_code == code)
    if from_date is not None:
        query = query.where(EtfPriceHistory.trade_date >= from_date)
    if to_date is not None:
        query = query.where(EtfPriceHistory.trade_date <= to_date)
    rows = await session.scalars(query.order_by(EtfPriceHistory.trade_date.asc()))
    return list(rows.all())


async def _etf_price_rows_from(
    session: AsyncSession,
    code: str,
    signal_date: date,
) -> list[EtfPriceHistory]:
    return list(
        (
            await session.scalars(
                select(EtfPriceHistory)
                .where(EtfPriceHistory.etf_code == code, EtfPriceHistory.trade_date >= signal_date)
                .order_by(EtfPriceHistory.trade_date.asc())
            )
        ).all()
    )


async def _etf_price_rows_by_code_from(
    session: AsyncSession,
    codes: list[str],
    signal_date: date,
    *,
    max_horizon: int,
) -> dict[str, list[EtfPriceHistory]]:
    if not codes:
        return {}
    by_code: dict[str, list[EtfPriceHistory]] = {code: [] for code in codes}
    cutoff = signal_date + timedelta(days=max_horizon * 4 + 14)
    for index in range(0, len(codes), 400):
        chunk = codes[index : index + 400]
        rows = (
            await session.scalars(
                select(EtfPriceHistory)
                .where(
                    EtfPriceHistory.etf_code.in_(chunk),
                    EtfPriceHistory.trade_date >= signal_date,
                    EtfPriceHistory.trade_date <= cutoff,
                )
                .order_by(EtfPriceHistory.etf_code.asc(), EtfPriceHistory.trade_date.asc())
            )
        ).all()
        for row in rows:
            by_code.setdefault(row.etf_code, []).append(row)
    return by_code


def _price_points_from_rows(rows: list[EtfPriceHistory]) -> list[PricePoint]:
    points: list[PricePoint] = []
    for row in rows:
        research_value = _research_adjusted_value(row)
        if research_value is None:
            continue
        points.append(
            PricePoint(
                point_date=row.trade_date,
                value=research_value,
                close=row.close,
                turnover=row.turnover,
                pct_change=row.pct_change / 100,
            )
        )
    return points


def _completed_outcome_payload(
    rows: list[EtfPriceHistory],
    horizon_days: int,
) -> tuple[str, dict[str, Any]]:
    if len(rows) <= horizon_days:
        return "pending", {"exclusion_reason": "missing_future_price"}
    start = rows[0]
    end_row = rows[horizon_days]
    signal_price = _research_adjusted_value(start)
    future_price = _research_adjusted_value(end_row)
    if signal_price is None or future_price is None:
        return "excluded", {"exclusion_reason": "missing_research_price_provenance"}
    path_returns = [
        value / signal_price - 1.0
        for row in rows[1 : horizon_days + 1]
        if (value := _research_adjusted_value(row)) is not None
    ]
    if len(path_returns) < horizon_days:
        return "excluded", {"exclusion_reason": "invalid_window_price"}
    return "completed", {
        "signal_price": signal_price,
        "future_price": future_price,
        "future_date": end_row.trade_date.isoformat(),
        "forward_return": future_price / signal_price - 1.0,
        "adverse_drawdown": min(path_returns),
        "favorable_excursion": max(path_returns),
    }


async def review_etf_label_outcomes(
    session: AsyncSession,
    *,
    source_run: ShortResearchSignalRun | None = None,
    max_signal_items: int = 2000,
) -> dict[str, Any]:
    stmt = (
        select(ShortResearchSignalItem, ShortResearchSignalRun)
        .join(ShortResearchSignalRun, ShortResearchSignalRun.id == ShortResearchSignalItem.run_id)
        .where(ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF)
        .order_by(ShortResearchSignalRun.as_of_date.desc(), ShortResearchSignalRun.id.desc(), ShortResearchSignalItem.rank.asc())
        .limit(max_signal_items)
    )
    if source_run is not None:
        stmt = stmt.where(ShortResearchSignalItem.run_id == source_run.id)
    rows = (await session.execute(stmt)).all()
    processed = 0
    completed = 0
    pending = 0
    excluded = 0
    now = utcnow()
    for signal_item, signal_run in rows:
        processed += 1
        eligible, exclusion_reason = _signal_item_decision_eligible(signal_item)
        price_rows = [] if not eligible else await _etf_price_rows_from(session, signal_item.asset_code, signal_run.as_of_date)
        for horizon in _LABEL_VALIDATION_WINDOWS:
            existing = await session.scalar(
                select(EtfLabelOutcome).where(
                    EtfLabelOutcome.signal_item_id == signal_item.id,
                    EtfLabelOutcome.horizon_days == horizon,
                )
            )
            if existing is not None and existing.status == "completed":
                completed += 1
                continue
            if not eligible:
                status = "excluded"
                payload: dict[str, Any] = {"exclusion_reason": exclusion_reason or "unreliable_signal"}
            elif not price_rows:
                status = "pending"
                payload = {"exclusion_reason": "missing_signal_price"}
            else:
                status, payload = _completed_outcome_payload(price_rows, horizon)
            if status == "completed":
                completed += 1
            elif status == "pending":
                pending += 1
            else:
                excluded += 1
            target = existing or EtfLabelOutcome(
                signal_item_id=signal_item.id,
                signal_run_id=signal_item.run_id,
                asset_type=signal_item.asset_type,
                asset_code=signal_item.asset_code,
                label=signal_item.conclusion,
                entry_timing_label=_outcome_entry_timing(signal_item),
                rule_version=_LABEL_VALIDATION_RULE_VERSION,
                signal_date=signal_run.as_of_date,
                horizon_days=horizon,
                created_at=now,
            )
            target.signal_price = payload.get("signal_price")
            target.forward_return = payload.get("forward_return")
            target.adverse_drawdown = payload.get("adverse_drawdown")
            target.favorable_excursion = payload.get("favorable_excursion")
            target.status = status
            target.exclusion_reason = payload.get("exclusion_reason")
            target.metrics_json = {
                "future_price": payload.get("future_price"),
                "future_date": payload.get("future_date"),
                "stored_signal_context": True,
                "signal_rule_version": (signal_run.config_json or {}).get("rule_version") or "short_research_signal_v1",
                "signal_date": signal_run.as_of_date.isoformat(),
                "data_reliability": (signal_item.metrics_json or {}).get("data_reliability", "verified"),
            }
            target.updated_at = now
            session.add(target)
    await session.flush()
    return {
        "processed_signal_items": processed,
        "completed_outcomes": completed,
        "pending_outcomes": pending,
        "excluded_outcomes": excluded,
    }


def _summarize_outcome_rows(rows: list[EtfLabelOutcome], total_rows: int) -> dict[str, Any]:
    completed_rows = [item for item in rows if item.status == "completed" and item.forward_return is not None]
    excluded_count = sum(1 for item in rows if item.status == "excluded")
    pending_count = sum(1 for item in rows if item.status == "pending")
    if not completed_rows:
        return {
            "sample_count": 0,
            "excluded_count": excluded_count,
            "pending_count": pending_count,
            "coverage": 0.0,
            "avg_return": None,
            "median_return": None,
            "worst_forward_drawdown": None,
            "favorable_excursion_median": None,
            "win_rate": None,
            "confidence": "insufficient",
            "confidence_label": "样本不足",
            "insufficient_sample": True,
        }
    returns = [float(item.forward_return or 0.0) for item in completed_rows]
    drawdowns = [float(item.adverse_drawdown or 0.0) for item in completed_rows]
    excursions = [float(item.favorable_excursion or 0.0) for item in completed_rows]
    recent_returns = returns[: min(10, len(returns))]
    all_median = median(returns)
    recent_median = median(recent_returns) if recent_returns else None
    confidence = _validation_confidence(len(completed_rows), recent_median=recent_median, all_median=all_median)
    exclusion_reasons: dict[str, int] = {}
    for item in rows:
        if item.exclusion_reason:
            exclusion_reasons[item.exclusion_reason] = exclusion_reasons.get(item.exclusion_reason, 0) + 1
    return {
        "sample_count": len(completed_rows),
        "excluded_count": excluded_count,
        "pending_count": pending_count,
        "coverage": round(len(completed_rows) / total_rows, 4) if total_rows else 0.0,
        "avg_return": round(mean(returns), 6),
        "median_return": round(all_median, 6),
        "worst_forward_drawdown": round(min(drawdowns), 6),
        "favorable_excursion_median": round(median(excursions), 6),
        "win_rate": round(sum(1 for item in returns if item > 0) / len(returns), 4),
        "confidence": confidence,
        "confidence_label": _validation_confidence_label(confidence),
        "insufficient_sample": confidence == "insufficient",
        "recent_median_return": round(recent_median, 6) if recent_median is not None else None,
        "exclusion_reasons": exclusion_reasons,
    }


async def _label_outcome_summary(
    session: AsyncSession,
    as_of_date: date,
    *,
    source_signal_run_id: int | None = None,
) -> dict[str, Any]:
    filters = [EtfLabelOutcome.asset_type == ASSET_TYPE_ETF]
    if source_signal_run_id is not None:
        filters.append(EtfLabelOutcome.signal_run_id == source_signal_run_id)
    rows = list(
        (
            await session.scalars(
                select(EtfLabelOutcome)
                .where(*filters)
                .order_by(EtfLabelOutcome.signal_date.desc(), EtfLabelOutcome.id.desc())
            )
        ).all()
    )
    grouped: dict[tuple[str, str], dict[int, list[EtfLabelOutcome]]] = {}
    contract_groups: dict[str, int] = {}
    for row in rows:
        grouped.setdefault((row.label, row.entry_timing_label), {}).setdefault(row.horizon_days, []).append(row)
        metrics = dict(row.metrics_json or {})
        group_key = " / ".join(
            [
                row.label,
                row.entry_timing_label,
                str(metrics.get("signal_rule_version") or row.rule_version),
                str(metrics.get("data_reliability") or "unknown"),
                row.signal_date.isoformat() if row.signal_date else "unknown_date",
            ]
        )
        contract_groups[group_key] = contract_groups.get(group_key, 0) + 1
    groups: list[dict[str, Any]] = []
    for (label, entry_label), windows in sorted(grouped.items()):
        window_summary = {
            str(window): _summarize_outcome_rows(windows.get(window, []), len(windows.get(window, [])))
            for window in _LABEL_VALIDATION_WINDOWS
        }
        groups.append(
            {
                "label": label,
                "entry_timing_label": entry_label,
                "key": f"{label} / {entry_label}",
                "windows": window_summary,
            }
        )
    return {
        "generated_at": utcnow().isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "asset_type": ASSET_TYPE_ETF,
        "rule_version": _LABEL_VALIDATION_RULE_VERSION,
        "outcome_source": "stored_signal_items",
        "asset_count": len({row.asset_code for row in rows}),
        "evaluated_asset_count": len({row.asset_code for row in rows if row.status == "completed"}),
        "windows": list(_LABEL_VALIDATION_WINDOWS),
        "min_sample_count": _LABEL_VALIDATION_MIN_SAMPLES,
        "contract_group_count": len(contract_groups),
        "contract_groups": contract_groups,
        "sufficient_sample_count": 50,
        "sample_policy": "读取已保存短线排序 signal item，等未来真实 ETF 日线收盘价出现后补 1/3/5/10 个交易日结果；不回放当前规则。",
        "groups": groups,
    }


async def _label_validation_summary(
    session: AsyncSession,
    _assets: list[ComputedAsset],
    as_of_date: date,
) -> dict[str, Any]:
    return await _label_outcome_summary(session, as_of_date)


@dataclass
class _ReplayBucketStats:
    total_count: int = 0
    excluded_count: int = 0
    returns: list[float] = field(default_factory=list)
    drawdowns: list[float] = field(default_factory=list)
    excursions: list[float] = field(default_factory=list)
    recent_returns: list[tuple[date, float]] = field(default_factory=list)
    exclusion_reasons: dict[str, int] = field(default_factory=dict)

    def add(self, sample: EtfLabelReplaySample) -> None:
        self.total_count += 1
        if sample.status == "completed" and sample.forward_return is not None:
            forward_return = float(sample.forward_return)
            self.returns.append(forward_return)
            self.drawdowns.append(float(sample.adverse_drawdown or 0.0))
            self.excursions.append(float(sample.favorable_excursion or 0.0))
            self.recent_returns.append((sample.replay_date, forward_return))
            self.recent_returns.sort(key=lambda item: item[0], reverse=True)
            del self.recent_returns[30:]
            return

        self.excluded_count += 1
        if sample.exclusion_reason:
            self.exclusion_reasons[sample.exclusion_reason] = self.exclusion_reasons.get(sample.exclusion_reason, 0) + 1

    def summary(self) -> dict[str, Any]:
        if not self.returns:
            return {
                "sample_count": 0,
                "excluded_count": self.excluded_count,
                "pending_count": 0,
                "coverage": 0.0,
                "avg_return": None,
                "median_return": None,
                "worst_forward_drawdown": None,
                "favorable_excursion_median": None,
                "win_rate": None,
                "confidence": "insufficient",
                "confidence_label": "样本不足",
                "insufficient_sample": True,
                "validation_mode": VALIDATION_MODE_HISTORICAL_REPLAY,
            }

        recent_return_values = [item[1] for item in self.recent_returns]
        all_median = median(self.returns)
        recent_median = median(recent_return_values) if recent_return_values else None
        coverage = len(self.returns) / self.total_count if self.total_count else 0.0
        confidence = _replay_validation_confidence(
            len(self.returns),
            coverage=coverage,
            recent_median=recent_median,
            all_median=all_median,
        )
        return {
            "sample_count": len(self.returns),
            "excluded_count": self.excluded_count,
            "pending_count": 0,
            "coverage": round(coverage, 4),
            "avg_return": round(mean(self.returns), 6),
            "median_return": round(all_median, 6),
            "worst_forward_drawdown": round(min(self.drawdowns), 6),
            "favorable_excursion_median": round(median(self.excursions), 6),
            "win_rate": round(sum(1 for item in self.returns if item > 0) / len(self.returns), 4),
            "confidence": confidence,
            "confidence_label": _validation_confidence_label(confidence),
            "insufficient_sample": confidence == "insufficient",
            "recent_median_return": round(recent_median, 6) if recent_median is not None else None,
            "exclusion_reasons": self.exclusion_reasons,
            "validation_mode": VALIDATION_MODE_HISTORICAL_REPLAY,
            "price_source": "verified_daily_close",
        }


@dataclass
class _ScoreBucketStats:
    total_count: int = 0
    excluded_count: int = 0
    pending_count: int = 0
    returns: list[float] = field(default_factory=list)
    drawdowns: list[float] = field(default_factory=list)
    excursions: list[float] = field(default_factory=list)
    recent_returns: list[tuple[date, float]] = field(default_factory=list)
    exclusion_reasons: dict[str, int] = field(default_factory=dict)

    def add(self, *, signal_date: date, status: str, payload: dict[str, Any]) -> None:
        self.total_count += 1
        if status == "completed" and isinstance(payload.get("forward_return"), int | float):
            forward_return = float(payload["forward_return"])
            self.returns.append(forward_return)
            self.drawdowns.append(float(payload.get("adverse_drawdown") or 0.0))
            self.excursions.append(float(payload.get("favorable_excursion") or 0.0))
            self.recent_returns.append((signal_date, forward_return))
            self.recent_returns.sort(key=lambda item: item[0], reverse=True)
            del self.recent_returns[30:]
            return

        if status == "pending":
            self.pending_count += 1
        else:
            self.excluded_count += 1
        reason = payload.get("exclusion_reason")
        if reason:
            key = str(reason)
            self.exclusion_reasons[key] = self.exclusion_reasons.get(key, 0) + 1

    def summary(self) -> dict[str, Any]:
        if not self.returns:
            return {
                "sample_count": 0,
                "excluded_count": self.excluded_count,
                "pending_count": self.pending_count,
                "coverage": 0.0,
                "avg_return": None,
                "median_return": None,
                "worst_forward_drawdown": None,
                "favorable_excursion_median": None,
                "win_rate": None,
                "confidence": "insufficient",
                "confidence_label": "样本不足",
                "insufficient_sample": True,
                "validation_mode": VALIDATION_MODE_SCORE_BUCKET_REPLAY,
                "price_source": "verified_daily_close",
                "exclusion_reasons": self.exclusion_reasons,
            }

        recent_return_values = [item[1] for item in self.recent_returns]
        all_median = median(self.returns)
        recent_median = median(recent_return_values) if recent_return_values else None
        coverage = len(self.returns) / self.total_count if self.total_count else 0.0
        confidence = _replay_validation_confidence(
            len(self.returns),
            coverage=coverage,
            recent_median=recent_median,
            all_median=all_median,
        )
        return {
            "sample_count": len(self.returns),
            "excluded_count": self.excluded_count,
            "pending_count": self.pending_count,
            "coverage": round(coverage, 4),
            "avg_return": round(mean(self.returns), 6),
            "median_return": round(all_median, 6),
            "worst_forward_drawdown": round(min(self.drawdowns), 6),
            "favorable_excursion_median": round(median(self.excursions), 6),
            "win_rate": round(sum(1 for item in self.returns if item > 0) / len(self.returns), 4),
            "confidence": confidence,
            "confidence_label": _validation_confidence_label(confidence),
            "insufficient_sample": confidence == "insufficient",
            "recent_median_return": round(recent_median, 6) if recent_median is not None else None,
            "exclusion_reasons": self.exclusion_reasons,
            "validation_mode": VALIDATION_MODE_SCORE_BUCKET_REPLAY,
            "price_source": "verified_daily_close",
        }


def _append_unique_codes(target: list[str], codes: list[str]) -> None:
    seen = set(target)
    for code in codes:
        if code in seen:
            continue
        target.append(code)
        seen.add(code)


def _replay_sample(
    *,
    validation_run_id: int,
    metadata: ShortResearchAsset,
    metrics: dict[str, Any],
    label: str,
    replay_row: EtfPriceHistory,
    horizon: int,
    future_rows: list[EtfPriceHistory],
    status: str,
    exclusion_reason: str | None = None,
) -> EtfLabelReplaySample:
    entry_price = _research_adjusted_value(replay_row)
    forward_return: float | None = None
    adverse_drawdown: float | None = None
    favorable_excursion: float | None = None
    horizon_end_date: date | None = None
    if status == "completed" and entry_price is None:
        status = "excluded"
        exclusion_reason = "missing_research_price_provenance"
    elif status == "completed" and entry_price > 0:
        end_row = future_rows[-1]
        horizon_end_date = end_row.trade_date
        path_returns = [
            value / entry_price - 1.0
            for row in future_rows
            if (value := _research_adjusted_value(row)) is not None
        ]
        if len(path_returns) == horizon:
            forward_return = path_returns[-1]
            adverse_drawdown = min(path_returns)
            favorable_excursion = max(path_returns)
        else:
            status = "excluded"
            exclusion_reason = "invalid_future_window_price"
    return EtfLabelReplaySample(
        validation_run_id=validation_run_id,
        asset_type=ASSET_TYPE_ETF,
        asset_code=metadata.code,
        asset_name=metadata.name,
        label=label,
        entry_timing_label=str(metrics.get("entry_timing_label") or ENTRY_TIMING_INSUFFICIENT),
        rule_version=_LABEL_VALIDATION_RULE_VERSION,
        replay_date=replay_row.trade_date,
        entry_price=entry_price,
        horizon_days=horizon,
        horizon_end_date=horizon_end_date,
        forward_return=forward_return,
        adverse_drawdown=adverse_drawdown,
        favorable_excursion=favorable_excursion,
        status=status,
        exclusion_reason=exclusion_reason,
        metrics_json={
            "score": metrics.get("total_score"),
            "score_breakdown": {
                "trend_score": metrics.get("trend_score"),
                "risk_score": metrics.get("risk_score"),
                "liquidity_score": metrics.get("liquidity_score"),
            },
            "risk_flags": list(metrics.get("risk_flags") or []),
            "entry_timing_reason": metrics.get("entry_timing_reason"),
            "data_reliability": metrics.get("data_reliability", "verified"),
            "price_source": "verified_daily_close",
            "no_lookahead_cutoff": replay_row.trade_date.isoformat(),
        },
    )


async def run_etf_label_historical_replay(
    session: AsyncSession,
    *,
    days: int = _LABEL_REPLAY_DEFAULT_DAYS,
    max_assets: int | None = None,
    batch_size: int = _LABEL_REPLAY_DEFAULT_BATCH_SIZE,
) -> EtfSignalValidationRun:
    started_at = utcnow()
    horizons = list(_LABEL_VALIDATION_WINDOWS)
    effective_batch_size = max(1, batch_size)
    universe_scope = _LABEL_REPLAY_SCOPE_ALL_ELIGIBLE if max_assets is None else _LABEL_REPLAY_SCOPE_LIMITED
    run = EtfSignalValidationRun(
        status=RUN_STATUS_RUNNING,
        started_at=started_at,
        as_of_date=date.today(),
        asset_type=ASSET_TYPE_ETF,
        validation_mode=VALIDATION_MODE_HISTORICAL_REPLAY,
        rule_version=_LABEL_VALIDATION_RULE_VERSION,
        config_json={
            "validation_mode": VALIDATION_MODE_HISTORICAL_REPLAY,
            "windows": horizons,
            "days": days,
            "max_assets": max_assets,
            "batch_size": effective_batch_size,
            "universe_scope": universe_scope,
            "price_source": "verified_daily_close",
            "research_only": True,
        },
        summary_json={},
    )
    session.add(run)
    await session.flush()
    await session.commit()

    etf_stmt = (
        select(TradableEtf)
        .where(TradableEtf.is_short_term_eligible.is_(True))
        .order_by(TradableEtf.code.asc())
    )
    if max_assets is not None:
        etf_stmt = etf_stmt.limit(max_assets)
    etfs = list((await session.scalars(etf_stmt)).all())
    bucket_stats: dict[tuple[str, str, int], _ReplayBucketStats] = {}
    completed_samples = 0
    excluded_samples = 0
    evaluated_assets = 0
    processed_assets = 0
    replay_start: date | None = None
    replay_end: date | None = None
    exclusion_reasons: dict[str, int] = {}

    def progress_summary() -> dict[str, Any]:
        percent = round(processed_assets / len(etfs), 4) if etfs else 1.0
        return {
            "validation_mode": VALIDATION_MODE_HISTORICAL_REPLAY,
            "generated_at": utcnow().isoformat(),
            "status": RUN_STATUS_RUNNING,
            "asset_type": ASSET_TYPE_ETF,
            "rule_version": _LABEL_VALIDATION_RULE_VERSION,
            "outcome_source": VALIDATION_MODE_HISTORICAL_REPLAY,
            "price_source": "verified_daily_close",
            "asset_count": len(etfs),
            "evaluated_asset_count": evaluated_assets,
            "processed_asset_count": processed_assets,
            "progress": {
                "processed_assets": processed_assets,
                "total_assets": len(etfs),
                "batch_size": effective_batch_size,
                "percent": percent,
            },
            "universe_scope": universe_scope,
            "max_assets": max_assets,
            "batch_size": effective_batch_size,
            "completed_samples": completed_samples,
            "excluded_samples": excluded_samples,
            "exclusion_reasons": exclusion_reasons,
            "windows": horizons,
        }

    for processed_assets, etf in enumerate(etfs, start=1):
        rows = await _etf_price_rows_until(
            session,
            etf.code,
            from_date=date.today() - timedelta(days=max(days * 2 + 180, 540)),
        )
        rows = [row for row in rows if _research_adjusted_value(row) is not None]
        if not rows:
            continue
        evaluated_assets += 1
        metadata = _metadata_from_etf_row(etf)
        replay_indices = range(max(0, len(rows) - days), len(rows))
        for index in replay_indices:
            replay_row = rows[index]
            replay_start = replay_row.trade_date if replay_start is None else min(replay_start, replay_row.trade_date)
            replay_end = replay_row.trade_date if replay_end is None else max(replay_end, replay_row.trade_date)
            series = _price_points_from_rows(rows[: index + 1])
            if len(series) < 20:
                metrics = {
                    "entry_timing_label": ENTRY_TIMING_INSUFFICIENT,
                    "entry_timing_reason": "回放日之前可用日线不足，不能生成无未来函数标签。",
                    "risk_flags": ["数据不足"],
                    "total_score": 0.0,
                }
                label = CONCLUSION_INSUFFICIENT
                eligible = False
                base_exclusion = "no_lookahead_insufficient_history"
            else:
                metrics = _score_metrics(metadata, series, replay_row.trade_date)
                label = _conclusion(metrics)
                eligible = label not in {CONCLUSION_INSUFFICIENT, CONCLUSION_REJECT}
                base_exclusion = None if eligible else "unqualified_replay_label"
            for horizon in horizons:
                if not eligible:
                    status = "excluded"
                    future_rows: list[EtfPriceHistory] = []
                    exclusion_reason = base_exclusion
                elif index + horizon >= len(rows):
                    status = "excluded"
                    future_rows = []
                    exclusion_reason = "missing_future_price"
                else:
                    future_rows = rows[index + 1 : index + horizon + 1]
                    status = "completed"
                    exclusion_reason = None
                sample = _replay_sample(
                    validation_run_id=run.id,
                    metadata=metadata,
                    metrics=metrics,
                    label=label,
                    replay_row=replay_row,
                    horizon=horizon,
                    future_rows=future_rows,
                    status=status,
                    exclusion_reason=exclusion_reason,
                )
                if sample.status == "completed":
                    completed_samples += 1
                else:
                    excluded_samples += 1
                    if sample.exclusion_reason:
                        exclusion_reasons[sample.exclusion_reason] = exclusion_reasons.get(sample.exclusion_reason, 0) + 1
                bucket_stats.setdefault((sample.label, sample.entry_timing_label, horizon), _ReplayBucketStats()).add(sample)
                session.add(sample)

        if processed_assets % effective_batch_size == 0:
            run.summary_json = progress_summary()
            await session.commit()

    groups: list[dict[str, Any]] = []
    by_label: dict[tuple[str, str], dict[int, _ReplayBucketStats]] = {}
    for (label, entry_label, horizon), stats in bucket_stats.items():
        by_label.setdefault((label, entry_label), {})[horizon] = stats
    for (label, entry_label), windows in sorted(by_label.items()):
        groups.append(
            {
                "label": label,
                "entry_timing_label": entry_label,
                "key": f"{label} / {entry_label}",
                "windows": {
                    str(window): windows.get(window, _ReplayBucketStats()).summary()
                    for window in horizons
                },
            }
        )
    summary = {
        "validation_mode": VALIDATION_MODE_HISTORICAL_REPLAY,
        "generated_at": utcnow().isoformat(),
        "as_of_date": (replay_end or date.today()).isoformat(),
        "replay_start_date": replay_start.isoformat() if replay_start else None,
        "replay_end_date": replay_end.isoformat() if replay_end else None,
        "asset_type": ASSET_TYPE_ETF,
        "rule_version": _LABEL_VALIDATION_RULE_VERSION,
        "outcome_source": VALIDATION_MODE_HISTORICAL_REPLAY,
        "price_source": "verified_daily_close",
        "asset_count": len(etfs),
        "evaluated_asset_count": evaluated_assets,
        "processed_asset_count": processed_assets,
        "universe_scope": universe_scope,
        "max_assets": max_assets,
        "batch_size": effective_batch_size,
        "progress": {
            "processed_assets": processed_assets,
            "total_assets": len(etfs),
            "batch_size": effective_batch_size,
            "percent": 1.0,
        },
        "universe_source": "current_tradable_etfs_short_term_eligible",
        "universe_bias_note": "历史回放基于当前仍可用的 ETF 池，可能存在幸存者偏差。",
        "completed_samples": completed_samples,
        "excluded_samples": excluded_samples,
        "exclusion_reasons": exclusion_reasons,
        "windows": horizons,
        "min_sample_count": _LABEL_REPLAY_MIN_SAMPLES,
        "sufficient_sample_count": _LABEL_REPLAY_SUFFICIENT_SAMPLES,
        "sample_policy": (
            "历史回放只使用回放日及以前的 ETF 日线收盘价重建标签；"
            "后续收益为 close-to-close 统计，不代表可成交收益，不参与实时排序、组合或邮件提醒。"
        ),
        "groups": groups,
    }
    run.status = RUN_STATUS_SUCCESS
    run.finished_at = utcnow()
    run.as_of_date = replay_end or date.today()
    run.summary_json = summary
    for item in _validation_items_from_summary(summary):
        session.add(
            EtfSignalValidationItem(
                run_id=run.id,
                label=item["label"],
                entry_timing_label=item["entry_timing_label"],
                horizon_days=item["horizon_days"],
                sample_count=item["sample_count"],
                excluded_count=item["excluded_count"],
                avg_return=item["avg_return"],
                median_return=item["median_return"],
                win_rate=item["win_rate"],
                worst_forward_drawdown=item["worst_forward_drawdown"],
                confidence=item["confidence"],
                metrics_json=item["metrics"],
            )
        )
    await session.commit()
    await session.refresh(run)
    return run


async def _latest_etf_signal_runs_by_date(
    session: AsyncSession,
    *,
    from_date: date,
) -> list[ShortResearchSignalRun]:
    etf_run_ids = (
        select(ShortResearchSignalItem.run_id)
        .where(ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF)
        .distinct()
    )
    rows = (
        await session.scalars(
            select(ShortResearchSignalRun)
            .where(
                ShortResearchSignalRun.status == RUN_STATUS_SUCCESS,
                ShortResearchSignalRun.publication_state == "published",
                ShortResearchSignalRun.scope_kind == "full",
                ShortResearchSignalRun.score_version == _SCORE_BUCKET_SCORE_VERSION,
                ShortResearchSignalRun.score_field == _SCORE_BUCKET_SCORE_FIELD,
                ShortResearchSignalRun.price_basis == _SCORE_BUCKET_PRICE_BASIS,
                ShortResearchSignalRun.as_of_trade_date >= from_date,
                ShortResearchSignalRun.id.in_(etf_run_ids),
            )
            .order_by(
                ShortResearchSignalRun.as_of_trade_date.desc(),
                ShortResearchSignalRun.published_at.desc(),
                ShortResearchSignalRun.id.desc(),
            )
        )
    ).all()
    by_date: dict[date, ShortResearchSignalRun] = {}
    for run in rows:
        assert run.as_of_trade_date is not None
        by_date.setdefault(run.as_of_trade_date, run)
    return list(by_date.values())


async def _score_bucket_signal_items(
    session: AsyncSession,
    run: ShortResearchSignalRun,
) -> tuple[list[tuple[ShortResearchSignalItem, float]], list[dict[str, str]]]:
    rows = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(
                ShortResearchSignalItem.run_id == run.id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
            )
            .order_by(ShortResearchSignalItem.rank.asc(), ShortResearchSignalItem.asset_code.asc())
        )
    ).all()
    scored: list[tuple[ShortResearchSignalItem, float]] = []
    exclusions: list[dict[str, str]] = []
    signal_date = (run.as_of_trade_date or run.as_of_date).isoformat()
    for item in rows:
        score = item.ranking_score
        if score is None:
            breakdown = (item.score_breakdown_json or {}).get(_SCORE_BUCKET_SCORE_VERSION)
            declared_score = breakdown.get(_SCORE_BUCKET_SCORE_FIELD) if isinstance(breakdown, Mapping) else None
            reason = (
                "non_finite_ranking_score"
                if isinstance(declared_score, int | float) and not math.isfinite(declared_score)
                else "missing_ranking_score"
            )
            exclusions.append(
                {
                    "key": reason,
                    "asset_code": item.asset_code,
                    "signal_date": signal_date,
                }
            )
            continue
        if not math.isfinite(score):
            exclusions.append(
                {
                    "key": "non_finite_ranking_score",
                    "asset_code": item.asset_code,
                    "signal_date": signal_date,
                }
            )
            continue
        if item.score_eligible is not True:
            exclusions.append(
                {
                    "key": "ineligible_ranking_score",
                    "asset_code": item.asset_code,
                    "signal_date": signal_date,
                }
            )
            continue
        scored.append((item, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0].global_rank or pair[0].rank or 999999, pair[0].asset_code))
    return scored, exclusions


def _stable_score_bucket_exclusions(
    excluded_codes: dict[str, list[str]],
    excluded_items: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, list[str]], dict[str, list[dict[str, str]]]]:
    return (
        {key: sorted(codes) for key, codes in sorted(excluded_codes.items())},
        {
            key: sorted(items, key=lambda item: (item["signal_date"], item["asset_code"]))
            for key, items in sorted(excluded_items.items())
        },
    )


def _score_bucket_group_specs(top_n: list[int]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "label": _SCORE_BUCKET_BASELINE,
            "entry_timing_label": "baseline",
            "group_type": "baseline",
            "start": 0,
            "end": None,
        }
    ]
    for value in top_n:
        specs.append(
            {
                "label": f"Top {value}",
                "entry_timing_label": "cumulative",
                "group_type": "cumulative",
                "start": 0,
                "end": value,
            }
        )
    marginal_bounds = [(1, 5), (6, 10), (11, 20), (21, 50)]
    for start_rank, end_rank in marginal_bounds:
        specs.append(
            {
                "label": f"{start_rank}-{end_rank}",
                "entry_timing_label": "marginal",
                "group_type": "marginal",
                "start": start_rank - 1,
                "end": end_rank,
            }
        )
    return specs


async def run_etf_score_bucket_validation(
    session: AsyncSession,
    *,
    days: int = _SCORE_BUCKET_DEFAULT_DAYS,
    score_basis: str = _SCORE_BUCKET_SCORE_BASIS,
    top_n: list[int] | None = None,
) -> EtfSignalValidationRun:
    started_at = utcnow()
    horizons = list(_LABEL_VALIDATION_WINDOWS)
    requested_top_n = sorted(set(top_n or list(_SCORE_BUCKET_DEFAULT_TOP_N)))
    config = {
        "validation_mode": VALIDATION_MODE_SCORE_BUCKET_REPLAY,
        "days": days,
        "score_basis": score_basis,
        "score_field": _SCORE_BUCKET_SCORE_FIELD,
        "score_version": _SCORE_BUCKET_SCORE_VERSION,
        "score_meaning": "综合排名最终分",
        "top_n": requested_top_n,
        "windows": horizons,
        "baseline": _SCORE_BUCKET_BASELINE,
        "price_source": "verified_daily_close",
        "research_only": True,
    }
    run = EtfSignalValidationRun(
        status=RUN_STATUS_RUNNING,
        started_at=started_at,
        as_of_date=date.today(),
        asset_type=ASSET_TYPE_ETF,
        validation_mode=VALIDATION_MODE_SCORE_BUCKET_REPLAY,
        rule_version=_SCORE_BUCKET_VALIDATION_RULE_VERSION,
        config_json=config,
        summary_json={},
    )
    session.add(run)
    await session.flush()

    if score_basis != _SCORE_BUCKET_SCORE_BASIS:
        run.status = RUN_STATUS_FAILED
        run.finished_at = utcnow()
        run.error_message = "score_basis 只支持 opportunity（综合关注最终决策分）。"
        run.summary_json = {**config, "status": RUN_STATUS_FAILED, "groups": []}
        await session.commit()
        await session.refresh(run)
        return run

    source_runs = await _latest_etf_signal_runs_by_date(
        session,
        from_date=date.today() - timedelta(days=days),
    )
    if not source_runs:
        run.status = RUN_STATUS_FAILED
        run.finished_at = utcnow()
        run.error_message = "暂无历史 ETF 短线排序快照，无法做综合关注分层验证。"
        run.summary_json = {
            **config,
            "status": RUN_STATUS_FAILED,
            "generated_at": utcnow().isoformat(),
            "unavailable_reason": "waiting_signal_generation",
            "groups": [],
        }
        await session.commit()
        await session.refresh(run)
        return run

    group_specs = _score_bucket_group_specs(requested_top_n)
    bucket_stats: dict[tuple[str, str, int], _ScoreBucketStats] = {}
    selected_codes_by_group: dict[tuple[str, str], list[str]] = {
        (spec["label"], spec["entry_timing_label"]): [] for spec in group_specs
    }
    excluded_codes: dict[str, list[str]] = {"unavailable_final_decision_score": []}
    excluded_items: dict[str, list[dict[str, str]]] = {}
    source_signal_run_ids: list[int] = []
    source_dates: list[str] = []
    scored_item_count = 0
    excluded_unavailable_score_count = 0
    completed_samples = 0
    excluded_samples = 0
    pending_samples = 0
    replay_start: date | None = None
    replay_end: date | None = None
    max_horizon = max(horizons)

    for source_run in source_runs:
        scored_items, score_exclusions = await _score_bucket_signal_items(session, source_run)
        if score_exclusions:
            excluded_unavailable_score_count += len(score_exclusions)
            _append_unique_codes(
                excluded_codes["unavailable_final_decision_score"],
                [item["asset_code"] for item in score_exclusions],
            )
            for item in score_exclusions:
                key = item["key"]
                _append_unique_codes(excluded_codes.setdefault(key, []), [item["asset_code"]])
                bucket = excluded_items.setdefault(key, [])
                if item not in bucket:
                    bucket.append(item)
        if not scored_items:
            continue
        source_signal_run_ids.append(source_run.id)
        source_dates.append(source_run.as_of_date.isoformat())
        scored_item_count += len(scored_items)
        replay_start = source_run.as_of_date if replay_start is None else min(replay_start, source_run.as_of_date)
        replay_end = source_run.as_of_date if replay_end is None else max(replay_end, source_run.as_of_date)
        rows_by_code = await _etf_price_rows_by_code_from(
            session,
            [item.asset_code for item, _score in scored_items],
            source_run.as_of_date,
            max_horizon=max_horizon,
        )
        for spec in group_specs:
            group_items = scored_items[spec["start"] : spec["end"]]
            group_key = (spec["label"], spec["entry_timing_label"])
            _append_unique_codes(
                selected_codes_by_group[group_key],
                [item.asset_code for item, _score in group_items],
            )
            for item, score in group_items:
                rows = rows_by_code.get(item.asset_code, [])
                for horizon in horizons:
                    if not rows or rows[0].trade_date != source_run.as_of_date:
                        status = "pending"
                        payload = {"exclusion_reason": "missing_signal_price"}
                    else:
                        status, payload = _completed_outcome_payload(rows, horizon)
                    if status == "completed":
                        completed_samples += 1
                    elif status == "pending":
                        pending_samples += 1
                    else:
                        excluded_samples += 1
                    metrics = {
                        **payload,
                        "score_basis": score_basis,
                        "final_decision_score": score,
                        "source_signal_run_id": source_run.id,
                        "source_signal_as_of_date": source_run.as_of_date.isoformat(),
                        "asset_code": item.asset_code,
                        "ranking_sort": "opportunity",
                    }
                    bucket_stats.setdefault(
                        (spec["label"], spec["entry_timing_label"], horizon),
                        _ScoreBucketStats(),
                    ).add(signal_date=source_run.as_of_date, status=status, payload=metrics)

    if not source_signal_run_ids:
        stable_excluded_codes, stable_excluded_items = _stable_score_bucket_exclusions(excluded_codes, excluded_items)
        run.status = RUN_STATUS_FAILED
        run.finished_at = utcnow()
        run.error_message = "历史 ETF signal run 没有可用真实综合关注分。"
        run.summary_json = {
            **config,
            "status": RUN_STATUS_FAILED,
            "generated_at": utcnow().isoformat(),
            "unavailable_reason": "no_available_final_decision_score",
            "source_signal_run_count": len(source_runs),
            "excluded_unavailable_score_count": excluded_unavailable_score_count,
            "excluded_codes": stable_excluded_codes,
            "excluded_items": stable_excluded_items,
            "groups": [],
        }
        await session.commit()
        await session.refresh(run)
        return run

    groups: list[dict[str, Any]] = []
    for spec in group_specs:
        label = spec["label"]
        entry_label = spec["entry_timing_label"]
        group_key = (label, entry_label)
        groups.append(
            {
                "label": label,
                "entry_timing_label": entry_label,
                "key": f"{label} / {entry_label}",
                "group_type": spec["group_type"],
                "score_basis": score_basis,
                "ranking_sort": "opportunity",
                "rank_start": None if spec["group_type"] == "baseline" else spec["start"] + 1,
                "rank_end": spec["end"],
                "selected_codes": selected_codes_by_group.get(group_key, []),
                "selected_count": len(selected_codes_by_group.get(group_key, [])),
                "windows": {
                    str(window): bucket_stats.get(
                        (label, entry_label, window),
                        _ScoreBucketStats(),
                    ).summary()
                    for window in horizons
                },
            }
        )

    excluded_codes, excluded_items = _stable_score_bucket_exclusions(excluded_codes, excluded_items)

    summary = {
        **config,
        "status": RUN_STATUS_SUCCESS,
        "generated_at": utcnow().isoformat(),
        "as_of_date": (replay_end or date.today()).isoformat(),
        "replay_start_date": replay_start.isoformat() if replay_start else None,
        "replay_end_date": replay_end.isoformat() if replay_end else None,
        "source_signal_run_count": len(source_signal_run_ids),
        "source_signal_run_ids": source_signal_run_ids,
        "source_signal_as_of_dates": source_dates,
        "ranking_sort": "opportunity",
        "requested_top_n": requested_top_n,
        "top_n": requested_top_n,
        "baseline": _SCORE_BUCKET_BASELINE,
        "scored_item_count": scored_item_count,
        "excluded_unavailable_score_count": excluded_unavailable_score_count,
        "excluded_codes": excluded_codes,
        "excluded_items": excluded_items,
        "completed_samples": completed_samples,
        "excluded_samples": excluded_samples,
        "pending_samples": pending_samples,
        "sample_policy": (
            "每天只取已发布的全范围 final_score_v3 ETF 快照，按其声明的 ranking_score 排序；"
            "缺失、非有限或不具备决策资格的 ranking_score 均排除，不回退 total_score；"
            "未来收益只读取 signal 日期之后的已保存 ETF 日线。"
        ),
        "research_only": True,
        "no_trade_instruction": True,
        "groups": groups,
    }
    run.status = RUN_STATUS_SUCCESS
    run.finished_at = utcnow()
    run.as_of_date = replay_end or date.today()
    run.source_signal_run_id = source_signal_run_ids[0]
    run.summary_json = summary
    for item in _validation_items_from_summary(summary):
        session.add(
            EtfSignalValidationItem(
                run_id=run.id,
                label=item["label"],
                entry_timing_label=item["entry_timing_label"],
                horizon_days=item["horizon_days"],
                sample_count=item["sample_count"],
                excluded_count=item["excluded_count"],
                avg_return=item["avg_return"],
                median_return=item["median_return"],
                win_rate=item["win_rate"],
                worst_forward_drawdown=item["worst_forward_drawdown"],
                confidence=item["confidence"],
                metrics_json=item["metrics"],
            )
        )
    await session.commit()
    await session.refresh(run)
    return run


def _validation_items_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in summary.get("groups", []):
        if not isinstance(group, dict):
            continue
        windows = group.get("windows", {})
        if not isinstance(windows, dict):
            continue
        for raw_window, metrics in windows.items():
            if not isinstance(metrics, dict):
                continue
            items.append(
                {
                    "label": str(group.get("label") or ""),
                    "entry_timing_label": str(group.get("entry_timing_label") or ""),
                    "horizon_days": int(raw_window),
                    "sample_count": int(metrics.get("sample_count") or 0),
                    "excluded_count": int(metrics.get("excluded_count") or 0),
                    "avg_return": metrics.get("avg_return"),
                    "median_return": metrics.get("median_return"),
                    "win_rate": metrics.get("win_rate"),
                    "worst_forward_drawdown": metrics.get("worst_forward_drawdown"),
                    "confidence": str(metrics.get("confidence") or "insufficient"),
                    "metrics": metrics,
                }
            )
    return items


async def latest_signal_validation_run(
    session: AsyncSession,
    *,
    validation_mode: str | None = None,
) -> EtfSignalValidationRun | None:
    query = select(EtfSignalValidationRun).where(EtfSignalValidationRun.asset_type == ASSET_TYPE_ETF)
    if validation_mode is not None:
        query = query.where(EtfSignalValidationRun.validation_mode == validation_mode)
    return cast(
        EtfSignalValidationRun | None,
        await session.scalar(
            query.order_by(EtfSignalValidationRun.as_of_date.desc(), EtfSignalValidationRun.id.desc())
        ),
    )


async def _recent_outcome_examples(session: AsyncSession) -> dict[tuple[str, str], list[dict[str, Any]]]:
    rows = list(
        (
            await session.scalars(
                select(EtfLabelOutcome)
                .where(EtfLabelOutcome.status == "completed", EtfLabelOutcome.horizon_days == 5)
                .order_by(EtfLabelOutcome.signal_date.desc(), EtfLabelOutcome.id.desc())
                .limit(200)
            )
        ).all()
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row.label, row.entry_timing_label)
        bucket = grouped.setdefault(key, [])
        if len(bucket) >= 5:
            continue
        bucket.append(
            {
                "asset_code": row.asset_code,
                "signal_date": row.signal_date.isoformat(),
                "horizon_days": row.horizon_days,
                "forward_return": row.forward_return,
                "adverse_drawdown": row.adverse_drawdown,
                "favorable_excursion": row.favorable_excursion,
            }
        )
    return grouped


async def _validation_evidence_for_run(
    session: AsyncSession,
    run: EtfSignalValidationRun | None,
    *,
    examples_by_label: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if run is None:
        return {}
    rows = (
        await session.scalars(
            select(EtfSignalValidationItem).where(EtfSignalValidationItem.run_id == run.id)
        )
    ).all()
    examples_by_label = examples_by_label or {}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.label, row.entry_timing_label)
        validation_mode = run.validation_mode or VALIDATION_MODE_FORWARD_LIVE
        outcome_source = validation_mode
        evidence = grouped.setdefault(
            key,
            {
                "run_id": run.id,
                "as_of_date": run.as_of_date.isoformat(),
                "source_ranking_contract_hash": run.source_ranking_contract_hash,
                "source_score_version": run.source_score_version,
                "source_score_field": run.source_score_field,
                "source_rule_version": run.source_rule_version,
                "source_universe_snapshot_hash": run.source_universe_snapshot_hash,
                "price_basis": run.price_basis,
                "validation_rule_version": run.rule_version,
                "validation_mode": validation_mode,
                "outcome_source": outcome_source,
                "confidence": "insufficient",
                "confidence_label": "样本不足",
                "horizons": {},
                "recent_examples": examples_by_label.get(key, []) if validation_mode == VALIDATION_MODE_FORWARD_LIVE else [],
            },
        )
        metrics = dict(row.metrics_json or {})
        horizon = {
            "sample_count": row.sample_count,
            "excluded_count": row.excluded_count,
            "pending_count": metrics.get("pending_count"),
            "coverage": metrics.get("coverage"),
            "avg_return": row.avg_return,
            "median_return": row.median_return,
            "win_rate": row.win_rate,
            "worst_forward_drawdown": row.worst_forward_drawdown,
            "favorable_excursion_median": metrics.get("favorable_excursion_median"),
            "confidence": row.confidence,
            "confidence_label": metrics.get("confidence_label") or _validation_confidence_label(row.confidence),
            "exclusion_reasons": metrics.get("exclusion_reasons", {}),
        }
        evidence["horizons"][str(row.horizon_days)] = horizon
        if row.horizon_days == 5:
            evidence.update(
                {
                    "sample_count": row.sample_count,
                    "win_rate": row.win_rate,
                    "median_return": row.median_return,
                    "confidence": row.confidence,
                    "confidence_label": horizon["confidence_label"],
                }
            )
        elif "sample_count" not in evidence:
            evidence.update(
                {
                    "sample_count": row.sample_count,
                    "win_rate": row.win_rate,
                    "median_return": row.median_return,
                    "confidence": row.confidence,
                    "confidence_label": horizon["confidence_label"],
                }
            )
    for evidence in grouped.values():
        horizons = evidence.get("horizons", {})
        five_day = horizons.get("5") if isinstance(horizons, dict) else None
        one_day = horizons.get("1") if isinstance(horizons, dict) else None
        excluded_total = 0
        pending_total = 0
        sample_total = 0
        exclusion_summary: dict[str, int] = {}
        if isinstance(horizons, dict):
            for horizon in horizons.values():
                if not isinstance(horizon, dict):
                    continue
                sample_total += int(horizon.get("sample_count") or 0)
                excluded_total += int(horizon.get("excluded_count") or 0)
                pending_total += int(horizon.get("pending_count") or 0)
                reasons = horizon.get("exclusion_reasons")
                if isinstance(reasons, dict):
                    for key, count in reasons.items():
                        exclusion_summary[str(key)] = exclusion_summary.get(str(key), 0) + int(count or 0)
        degradation_warning = None
        if isinstance(five_day, dict) and five_day.get("median_return") is not None:
            if float(five_day["median_return"] or 0.0) < 0 or float(five_day.get("win_rate") or 0.0) < 0.45:
                degradation_warning = "近5日历史验收样本中位收益或胜率偏弱，标签有效性需要降级观察。"
        if degradation_warning is None and isinstance(one_day, dict) and one_day.get("median_return") is not None:
            if float(one_day["median_return"] or 0.0) < 0 and evidence.get("confidence") == "sufficient":
                degradation_warning = "近1日历史验收出现转弱迹象，短线标签需要结合当日走势复核。"
        evidence["freshness"] = {
            "as_of_date": run.as_of_date.isoformat(),
            "generated_at": run.created_at.isoformat(),
            "rule_version": run.rule_version,
            "validation_mode": run.validation_mode or VALIDATION_MODE_FORWARD_LIVE,
        }
        evidence["sample_quality"] = {
            "sample_count_total": sample_total,
            "excluded_count_total": excluded_total,
            "pending_count_total": pending_total,
            "exclusion_reasons": exclusion_summary,
        }
        evidence["degradation_warning"] = degradation_warning
    return grouped


async def latest_validation_evidence_by_label(session: AsyncSession) -> dict[tuple[str, str], dict[str, Any]]:
    historical_run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_HISTORICAL_REPLAY)
    forward_run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_FORWARD_LIVE)
    examples_by_label = await _recent_outcome_examples(session) if forward_run is not None else {}
    historical = await _validation_evidence_for_run(session, historical_run)
    forward = await _validation_evidence_for_run(session, forward_run, examples_by_label=examples_by_label)
    keys = set(historical) | set(forward)
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for key in keys:
        primary = dict(historical.get(key) or forward.get(key) or {})
        tracks: dict[str, Any] = {}
        if key in historical:
            tracks[VALIDATION_MODE_HISTORICAL_REPLAY] = historical[key]
        if key in forward:
            tracks[VALIDATION_MODE_FORWARD_LIVE] = forward[key]
        primary["evidence_tracks"] = tracks
        primary["historical_replay"] = tracks.get(VALIDATION_MODE_HISTORICAL_REPLAY)
        primary["forward_live"] = tracks.get(VALIDATION_MODE_FORWARD_LIVE)
        if VALIDATION_MODE_HISTORICAL_REPLAY in tracks and VALIDATION_MODE_FORWARD_LIVE not in tracks:
            primary["degradation_warning"] = (
                primary.get("degradation_warning")
                or "真实前瞻样本仍在积累，历史回放只能作为研究参考。"
            )
        merged[key] = primary
    return merged


async def latest_label_validation_summary(session: AsyncSession) -> dict[str, Any]:
    historical_run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_HISTORICAL_REPLAY)
    forward_run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_FORWARD_LIVE)
    if historical_run is None and forward_run is None:
        return {}
    primary = historical_run or forward_run
    assert primary is not None
    summary = dict(primary.summary_json or {})
    summary["primary_validation_mode"] = primary.validation_mode or VALIDATION_MODE_FORWARD_LIVE
    tracks: dict[str, Any] = {}
    if historical_run is not None:
        tracks[VALIDATION_MODE_HISTORICAL_REPLAY] = {
            "run_id": historical_run.id,
            "as_of_date": historical_run.as_of_date.isoformat(),
            "generated_at": historical_run.created_at.isoformat(),
            "summary": dict(historical_run.summary_json or {}),
        }
    if forward_run is not None:
        tracks[VALIDATION_MODE_FORWARD_LIVE] = {
            "run_id": forward_run.id,
            "as_of_date": forward_run.as_of_date.isoformat(),
            "generated_at": forward_run.created_at.isoformat(),
            "summary": dict(forward_run.summary_json or {}),
        }
    summary["evidence_tracks"] = tracks
    return summary


async def latest_score_bucket_validation_summary(session: AsyncSession) -> dict[str, Any]:
    run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_SCORE_BUCKET_REPLAY)
    if run is None:
        return {}
    summary = dict(run.summary_json or {})
    summary["run_id"] = run.id
    summary["validation_mode"] = run.validation_mode or VALIDATION_MODE_SCORE_BUCKET_REPLAY
    summary["rule_version"] = run.rule_version
    summary["generated_at"] = summary.get("generated_at") or run.created_at.isoformat()
    summary["created_at"] = run.created_at.isoformat()
    return summary


async def run_etf_signal_validation(session: AsyncSession) -> EtfSignalValidationRun:
    started_at = utcnow()
    source_run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    if source_run is None:
        run = EtfSignalValidationRun(
            status=RUN_STATUS_FAILED,
            started_at=started_at,
            finished_at=utcnow(),
            as_of_date=date.today(),
            asset_type=ASSET_TYPE_ETF,
            validation_mode=VALIDATION_MODE_FORWARD_LIVE,
            rule_version=_LABEL_VALIDATION_RULE_VERSION,
            config_json={"windows": list(_LABEL_VALIDATION_WINDOWS)},
            summary_json={},
            error_message="暂无 ETF 短线排序快照，无法做标签有效性验证。",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run
    outcome_status = await review_etf_label_outcomes(session, source_run=source_run)
    summary = await _label_outcome_summary(
        session,
        source_run.as_of_date,
        source_signal_run_id=source_run.id,
    )
    summary.update(outcome_status)
    run = EtfSignalValidationRun(
        status=RUN_STATUS_SUCCESS,
        started_at=started_at,
        finished_at=utcnow(),
        as_of_date=source_run.as_of_date,
        source_signal_run_id=source_run.id,
        asset_type=ASSET_TYPE_ETF,
        validation_mode=VALIDATION_MODE_FORWARD_LIVE,
        rule_version=_LABEL_VALIDATION_RULE_VERSION,
        config_json={
            "windows": list(_LABEL_VALIDATION_WINDOWS),
            "max_signal_items": 2000,
            "min_sample_count": _LABEL_VALIDATION_MIN_SAMPLES,
            "validation_mode": VALIDATION_MODE_FORWARD_LIVE,
            "outcome_source": "stored_signal_items",
        },
        summary_json=summary,
    )
    session.add(run)
    await session.flush()
    for item in _validation_items_from_summary(summary):
        session.add(
            EtfSignalValidationItem(
                run_id=run.id,
                label=item["label"],
                entry_timing_label=item["entry_timing_label"],
                horizon_days=item["horizon_days"],
                sample_count=item["sample_count"],
                excluded_count=item["excluded_count"],
                avg_return=item["avg_return"],
                median_return=item["median_return"],
                win_rate=item["win_rate"],
                worst_forward_drawdown=item["worst_forward_drawdown"],
                confidence=item["confidence"],
                metrics_json=item["metrics"],
            )
        )
    await session.commit()
    await session.refresh(run)
    return run

def _rationale(metadata: ShortResearchAsset, metrics: dict[str, Any], conclusion: str) -> dict[str, Any]:
    trend_evidence = [
        f"近 5 日 {_format_percent(metrics['return_5d'])}",
        f"近 10 日 {_format_percent(metrics['return_10d'])}",
        f"近 20 日 {_format_percent(metrics['return_20d'])}",
        f"近 60 日 {_format_percent(metrics['return_60d'])}",
    ]
    risk_evidence = [
        f"20 日波动 {_format_percent(metrics['volatility_20d'])}",
        f"60 日最大回撤 {_format_percent(metrics['max_drawdown_60d'])}",
    ]
    if metadata.asset_type == ASSET_TYPE_ETF:
        turnover = metrics["average_turnover_20d"]
        risk_evidence.append(f"20 日平均成交额 {turnover / 100_000_000:.2f} 亿元" if turnover else "20 日平均成交额 暂无")
    sample_level = _sample_level(int(metrics["usable_days"]))
    reason = (
        f"{conclusion}：综合分 {float(metrics['total_score']):.1f}，"
        f"趋势分 {float(metrics['trend_score']):.1f}，风险分 {float(metrics['risk_score']):.1f}。"
        f"核心依据是 {trend_evidence[0]}、{trend_evidence[2]}，样本状态为 {sample_level}。"
    )
    opposing_view = (
        "反方提醒：短线排序依赖近期数据，市场风格切换时可能很快失效，"
        "需要结合风险标签和图表位置继续观察。"
    )
    label_meanings = {
        CONCLUSION_WATCH: "进入观察清单，表示趋势和风险条件相对更好，但不是交易指令。",
        CONCLUSION_HIGH_WATCH: "分数不低但风险也被触发，重点防止追高和波动。",
        CONCLUSION_CAUTION: "条件不够突出，适合继续看图和等待更多数据。",
        CONCLUSION_REJECT: "短线条件不适合，通常是流动性、风险或趋势条件较弱。",
        CONCLUSION_INSUFFICIENT: "数据不足或滞后，不能形成有效短线结论。",
    }
    return {
        "key_reason": reason,
        "trend_evidence": trend_evidence,
        "risk_evidence": risk_evidence,
        "risk_explanation": _risk_text(list(metrics["risk_flags"])),
        "investment_direction": metadata.investment_direction,
        "theme_profile": metrics.get("theme_profile") or {},
        "dynamic_threshold_context": metrics.get("dynamic_threshold_context") or {},
        "opposing_view": opposing_view,
        "sample_note": sample_level,
        "label_meaning": label_meanings[conclusion],
        "entry_timing_label": metrics["entry_timing_label"],
        "entry_timing_reason": metrics["entry_timing_reason"],
        "research_only": True,
        "no_trade_instruction": True,
    }


def _data_quality_from_reasons(reasons: list[str]) -> float:
    if not reasons:
        return 100.0
    score = 100.0
    for reason in reasons:
        if "历史" in reason:
            score -= 35
        elif "成交额" in reason or "流动性" in reason:
            score -= 35
        elif "滞后" in reason:
            score -= 40
        elif "失败" in reason:
            score -= 25
        else:
            score -= 15
    return round(max(0.0, score), 2)


async def _quality_gate_reasons(
    session: AsyncSession,
    asset: ComputedAsset,
    as_of_date: date,
) -> list[str]:
    if asset.metadata.asset_type != ASSET_TYPE_ETF:
        return []
    reasons: list[str] = []
    if asset.usable_days < 20:
        reasons.append("可用历史少于 20 个交易日")
    if asset.latest_date is None:
        reasons.append("暂无最新价格数据")
    elif (as_of_date - asset.latest_date).days > STALE_DATA_DAYS:
        reasons.append("数据滞后")
    turnover = asset.metrics.get("average_turnover_20d")
    if turnover is None or float(turnover) < MIN_AVERAGE_TURNOVER:
        reasons.append("近 20 日平均成交额偏低，流动性不足")
    health = await session.scalar(select(EtfDataHealth).where(EtfDataHealth.etf_code == asset.metadata.code))
    if health is not None and int(health.consecutive_failures or 0) >= 3:
        reasons.append("数据源连续同步失败")
    return reasons


async def _with_quality_metrics(
    session: AsyncSession,
    asset: ComputedAsset,
    as_of_date: date,
) -> ComputedAsset:
    reasons = await _quality_gate_reasons(session, asset, as_of_date)
    data_quality_score = _data_quality_from_reasons(reasons)
    metrics = {
        **asset.metrics,
        "data_quality_score": data_quality_score,
        "default_display_eligible": not reasons,
        "default_exclusion_reasons": reasons,
    }
    score_breakdown = dict(asset.score_breakdown)
    score_breakdown["data_quality"] = {"score": data_quality_score, "weight": 0.10}
    adjusted_score = asset.total_score
    if asset.metadata.asset_type == ASSET_TYPE_ETF:
        adjusted_score = round(asset.total_score * 0.90 + data_quality_score * 0.10, 2)
    return replace(
        asset,
        total_score=adjusted_score,
        metrics=metrics,
        score_breakdown=score_breakdown,
    )


def _with_final_score_v2(assets: list[ComputedAsset]) -> list[ComputedAsset]:
    etf_assets = [asset for asset in assets if asset.metadata.asset_type == ASSET_TYPE_ETF]
    if not etf_assets:
        return assets
    score_by_code = build_final_score_breakdowns(
        [
            RankingRecord(
                code=asset.metadata.code,
                metrics=asset.metrics,
                risk_flags=asset.risk_flags,
            )
            for asset in etf_assets
        ]
    )
    updated: list[ComputedAsset] = []
    for asset in assets:
        final_breakdown = score_by_code.get(asset.metadata.code)
        if asset.metadata.asset_type != ASSET_TYPE_ETF or not final_breakdown:
            updated.append(asset)
            continue
        final_score = float(final_breakdown["final_score"])
        metrics = {
            **asset.metrics,
            "total_score": final_score,
            "score_version": FINAL_SCORE_VERSION,
            "score_confidence": final_breakdown.get("confidence"),
            "score_limitation_reasons": final_breakdown.get("limitation_reasons", []),
        }
        reliability_component = (final_breakdown.get("components") or {}).get("data_reliability") or {}
        if isinstance(reliability_component, dict):
            metrics["data_reliability"] = reliability_component.get("reliability")
        conclusion = _conclusion({**metrics, "risk_flags": asset.risk_flags, "total_score": final_score})
        score_breakdown = {
            **asset.score_breakdown,
            "score_version": FINAL_SCORE_VERSION,
            "final_score_v2": final_breakdown,
        }
        rationale = {
            **asset.rationale,
            "key_reason": (
                f"{conclusion}：最终分 {final_score:.1f}，"
                "由横截面分位、动态阈值、标签历史有效性、数据可信度、流动性/折溢价共同生成。"
            ),
            "label_meaning": _label_meaning(conclusion),
            "score_version": FINAL_SCORE_VERSION,
        }
        updated.append(
            replace(
                asset,
                total_score=final_score,
                conclusion=conclusion,
                metrics=metrics,
                score_breakdown=score_breakdown,
                rationale=rationale,
            )
        )
    return updated


def _with_sector_trend_scores(assets: list[ComputedAsset]) -> list[ComputedAsset]:
    etf_assets = [asset for asset in assets if asset.metadata.asset_type == ASSET_TYPE_ETF]
    if not etf_assets:
        return assets
    payloads = build_sector_trend_payloads(etf_assets)
    updated: list[ComputedAsset] = []
    for asset in assets:
        if asset.metadata.asset_type != ASSET_TYPE_ETF:
            updated.append(asset)
            continue
        payload = payloads.get(asset.metadata.code)
        if not payload:
            updated.append(asset)
            continue
        metrics = {**asset.metrics, **dict(payload["metrics"])}
        breakdown = dict(payload["breakdown"])
        updated.append(
            replace(
                asset,
                metrics=metrics,
                score_breakdown={**asset.score_breakdown, "sector_trend_v1": breakdown},
                rationale={
                    **asset.rationale,
                    "sector_trend_summary": metrics.get("sector_trend_summary"),
                },
            )
        )
    return updated


async def _with_opportunity_scores(
    session: AsyncSession,
    assets: list[ComputedAsset],
    as_of_date: date,
) -> list[ComputedAsset]:
    etf_assets = [asset for asset in assets if asset.metadata.asset_type == ASSET_TYPE_ETF]
    if not etf_assets:
        return assets
    snapshots = await latest_theme_catalyst_snapshots_by_key(session, as_of_date=as_of_date)
    updated: list[ComputedAsset] = []
    for asset in assets:
        if asset.metadata.asset_type != ASSET_TYPE_ETF:
            updated.append(asset)
            continue
        payload = build_asset_opportunity_payload(
            asset_name=asset.metadata.name,
            theme_tags=list(asset.metadata.theme_tags),
            metrics=asset.metrics,
            risk_flags=asset.risk_flags,
            technical_score=asset.total_score,
            snapshots_by_key=snapshots,
        )
        opportunity_metrics = dict(payload["metrics"])
        combined_metrics = {**asset.metrics, **opportunity_metrics}
        combined_score_breakdown = {**asset.score_breakdown, "opportunity_score_v2": payload["breakdown"]}
        factor_payload = build_asset_factor_payload(
            asset_name=asset.metadata.name,
            theme_tags=list(asset.metadata.theme_tags),
            metrics=combined_metrics,
            risk_flags=asset.risk_flags,
            technical_score=asset.total_score,
            score_breakdown=combined_score_breakdown,
            as_of_date=as_of_date,
        )
        factor_metrics = dict(factor_payload["metrics"])
        factor_score = factor_metrics.get("factor_profile_score")
        if isinstance(factor_score, int | float):
            factor_metrics["opportunity_score"] = round(float(factor_score), 2)
            factor_metrics["opportunity_score_version"] = str(factor_metrics["factor_profile_version"])
        catalyst_summary = str(opportunity_metrics.get("catalyst_summary") or "")
        opportunity_label = str(opportunity_metrics.get("opportunity_label") or "")
        updated.append(
            replace(
                asset,
                metrics={**combined_metrics, **factor_metrics},
                score_breakdown={**combined_score_breakdown, "factor_profile_v1": factor_payload["breakdown"]},
                rationale={
                    **asset.rationale,
                    "catalyst_summary": catalyst_summary,
                    "opportunity_label": opportunity_label,
                    "opportunity_meaning": "综合关注分只用于研究观察，不覆盖买点、数据可信度或风险标签；缺失因子不会用中性分补位。",
                },
            )
        )
    return updated


async def compute_asset(
    session: AsyncSession,
    metadata: ShortResearchAsset,
    *,
    as_of_date: date | None = None,
    rank: int | None = None,
) -> ComputedAsset:
    effective_date = as_of_date or await latest_data_date(session) or date.today()
    series = await _series_for_asset(session, metadata, effective_date)
    metrics = _score_metrics(metadata, series, effective_date)
    conclusion = _conclusion(metrics)
    source_note = "公开 ETF 日线数据" if metadata.asset_type == ASSET_TYPE_ETF else "公开基金净值数据"
    score_breakdown = {
        "trend": {"score": metrics["trend_score"], "weight": 0.55},
        "risk": {"score": metrics["risk_score"], "weight": 0.30},
        "liquidity": {"score": metrics["liquidity_score"], "weight": 0.15},
        "metrics": {
            key: metrics[key]
            for key in (
                "return_5d",
                "return_10d",
                "return_20d",
                "return_60d",
                "volatility_20d",
                "max_drawdown_60d",
                "average_turnover_20d",
                "today_return_pct",
                "ma5",
                "ma10",
                "ma20",
                "distance_to_ma5_pct",
                "distance_to_ma10_pct",
                "pullback_from_5d_high_pct",
                "pullback_from_20d_high_pct",
                "volume_ratio_20d",
                "entry_timing_label",
                "entry_timing_reason",
                "theme_profile",
                "dynamic_threshold_context",
            )
        },
    }
    computed = ComputedAsset(
        metadata=metadata,
        rank=rank,
        total_score=float(metrics["total_score"]),
        conclusion=conclusion,
        latest_date=cast(date | None, metrics["latest_date"]),
        latest_value=cast(float | None, metrics["latest_value"]),
        usable_days=int(metrics["usable_days"]),
        sample_level=_sample_level(int(metrics["usable_days"])),
        metrics={
            key: metrics[key]
            for key in (
                "return_5d",
                "return_10d",
                "return_20d",
                "return_60d",
                "volatility_20d",
                "max_drawdown_60d",
                "average_turnover_20d",
                "today_return_pct",
                "ma5",
                "ma10",
                "ma20",
                "distance_to_ma5_pct",
                "distance_to_ma10_pct",
                "pullback_from_5d_high_pct",
                "pullback_from_20d_high_pct",
                "volume_ratio_20d",
                "entry_timing_label",
                "entry_timing_reason",
                "theme_profile",
                "dynamic_threshold_context",
            )
        },
        score_breakdown=score_breakdown,
        risk_flags=list(metrics["risk_flags"]),
        rationale=_rationale(metadata, metrics, conclusion),
        source_note=source_note,
        entry_timing_label=str(metrics["entry_timing_label"]),
        entry_timing_reason=str(metrics["entry_timing_reason"]),
    )
    return await _with_quality_metrics(session, computed, effective_date)


def compute_asset_for_replay_from_series(
    metadata: ShortResearchAsset,
    *,
    series: list[PricePoint],
    as_of_date: date,
    rank: int | None = None,
) -> ComputedAsset:
    metrics = _score_metrics(metadata, series, as_of_date)
    conclusion = _conclusion(metrics)
    source_note = "历史 ETF 日线数据" if metadata.asset_type == ASSET_TYPE_ETF else "历史基金净值数据"
    metric_keys = (
        "return_5d",
        "return_10d",
        "return_20d",
        "return_60d",
        "volatility_20d",
        "max_drawdown_60d",
        "average_turnover_20d",
        "today_return_pct",
        "ma5",
        "ma10",
        "ma20",
        "distance_to_ma5_pct",
        "distance_to_ma10_pct",
        "pullback_from_5d_high_pct",
        "pullback_from_20d_high_pct",
        "volume_ratio_20d",
        "entry_timing_label",
        "entry_timing_reason",
        "theme_profile",
        "dynamic_threshold_context",
    )
    replay_metrics = {key: metrics[key] for key in metric_keys}
    replay_metrics.update(
        {
            "data_quality_score": 100.0,
            "default_display_eligible": not any(
                flag in metrics["risk_flags"] for flag in ("数据不足", "数据滞后", "流动性不足")
            ),
            "default_exclusion_reasons": [
                flag for flag in metrics["risk_flags"] if flag in {"数据不足", "数据滞后", "流动性不足"}
            ],
            "replay_as_of_date": as_of_date,
        }
    )
    score_breakdown = {
        "trend": {"score": metrics["trend_score"], "weight": 0.55},
        "risk": {"score": metrics["risk_score"], "weight": 0.30},
        "liquidity": {"score": metrics["liquidity_score"], "weight": 0.15},
        "data_quality": {"score": 100.0, "weight": 0.0},
        "metrics": replay_metrics,
    }
    return ComputedAsset(
        metadata=metadata,
        rank=rank,
        total_score=float(metrics["total_score"]),
        conclusion=conclusion,
        latest_date=cast(date | None, metrics["latest_date"]),
        latest_value=cast(float | None, metrics["latest_value"]),
        usable_days=int(metrics["usable_days"]),
        sample_level=_sample_level(int(metrics["usable_days"])),
        metrics=replay_metrics,
        score_breakdown=score_breakdown,
        risk_flags=list(metrics["risk_flags"]),
        rationale=_rationale(metadata, metrics, conclusion),
        source_note=source_note,
        entry_timing_label=str(metrics["entry_timing_label"]),
        entry_timing_reason=str(metrics["entry_timing_reason"]),
    )


async def compute_asset_for_replay(
    session: AsyncSession,
    metadata: ShortResearchAsset,
    *,
    as_of_date: date,
    rank: int | None = None,
) -> ComputedAsset:
    series = await _series_for_asset(session, metadata, as_of_date)
    return compute_asset_for_replay_from_series(
        metadata,
        series=series,
        as_of_date=as_of_date,
        rank=rank,
    )


def _matches_filters(
    metadata: ShortResearchAsset,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> bool:
    if asset_type and metadata.asset_type != asset_type:
        return False
    if theme and theme not in metadata.theme_tags:
        return False
    if codes and metadata.code not in codes:
        return False
    return True


def _sort_key(asset: ComputedAsset, sort: str) -> tuple[float, str]:
    metrics = asset.metrics
    if sort == "opportunity":
        final_score = _final_decision_score(asset)
        if final_score is None:
            return (-999.0, asset.metadata.code)
        return (final_score, asset.metadata.code)
    if sort == "return_5d":
        return (float(metrics.get("return_5d") or -999), asset.metadata.code)
    if sort == "return_20d":
        return (float(metrics.get("return_20d") or -999), asset.metadata.code)
    if sort == "drawdown_low":
        return (-(abs(float(metrics.get("max_drawdown_60d") or 0.0))), asset.metadata.code)
    if sort == "liquidity":
        return (float(metrics.get("average_turnover_20d") or 0.0), asset.metadata.code)
    if sort == "risk_low":
        return (float(asset.score_breakdown["risk"]["score"]), asset.metadata.code)
    return (asset.total_score, asset.metadata.code)


def _dedupe_etf_candidates(assets: list[ComputedAsset]) -> list[ComputedAsset]:
    best_by_key: dict[str, ComputedAsset] = {}
    for asset in assets:
        normalized_name = (
            asset.metadata.name.replace("交易型开放式指数证券投资基金", "")
            .replace("ETF", "")
            .replace("联接", "")
            .replace("基金", "")
        )
        key = normalized_name or asset.metadata.investment_direction or asset.metadata.code
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = asset
            continue
        current_liquidity = float(current.metrics.get("average_turnover_20d") or 0)
        asset_liquidity = float(asset.metrics.get("average_turnover_20d") or 0)
        if (asset_liquidity, asset.total_score) > (current_liquidity, current.total_score):
            best_by_key[key] = asset
    return list(best_by_key.values())


async def list_computed_assets(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
    sort: str = "score",
    universe: str = UNIVERSE_DEFAULT,
) -> list[ComputedAsset]:
    await ensure_short_research_universe(session)
    effective_date = as_of_date or await latest_data_date(session) or date.today()
    candidates = [
        item
        for item in await _available_assets(session, asset_type=asset_type, codes=codes)
        if _matches_filters(item, asset_type=asset_type, theme=theme, codes=codes)
    ]
    computed = [await compute_asset(session, item, as_of_date=effective_date) for item in candidates]
    if asset_type == ASSET_TYPE_ETF or any(item.metadata.asset_type == ASSET_TYPE_ETF for item in computed):
        computed = _with_final_score_v2(computed)
        computed = _with_sector_trend_scores(computed)
        computed = await _with_opportunity_scores(session, computed, effective_date)
    if asset_type == ASSET_TYPE_ETF and universe == UNIVERSE_DEFAULT and not codes:
        computed = [item for item in computed if bool(item.metrics.get("default_display_eligible"))]
        computed = _dedupe_etf_candidates(computed)
    elif asset_type == ASSET_TYPE_ETF and universe not in {UNIVERSE_DEFAULT, UNIVERSE_ALL, UNIVERSE_ILLIQUID}:
        raise ValueError("ETF universe 只支持 default、all、illiquid")
    computed.sort(key=lambda item: _sort_key(item, sort), reverse=True)
    return [
        replace(
            item,
            rank=index,
        )
        for index, item in enumerate(computed, start=1)
    ]


async def get_asset_detail(
    session: AsyncSession,
    asset_type: str,
    code: str,
    *,
    as_of_date: date | None = None,
    source_run: ShortResearchSignalRun | None = None,
) -> tuple[ComputedAsset, list[dict[str, Any]], dict[str, str]]:
    await ensure_short_research_universe(session)
    if asset_type == ASSET_TYPE_ETF:
        if SHORT_RESEARCH_ASSET_BY_KEY.get((asset_type, code)) is None:
            etf = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
            if etf is None:
                raise ValueError("未找到这只 ETF")
        run = source_run or await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
        if run is None:
            raise ValueError("等待 ETF 信号生成后再查看当前评分")
        cached_assets, _total = await cached_signal_assets(
            session,
            run,
            asset_type=ASSET_TYPE_ETF,
            codes=[code],
            sort="score",
            universe=UNIVERSE_ALL,
            limit=1,
        )
        if not cached_assets:
            raise ValueError("等待 ETF 信号生成后再查看当前评分")
        computed = cached_assets[0]
    else:
        metadata = SHORT_RESEARCH_ASSET_BY_KEY.get((asset_type, code))
        if metadata is None:
            if asset_type == ASSET_TYPE_FUND:
                fund = await session.scalar(select(Fund).where(Fund.code == code))
                if fund is None:
                    raise ValueError("未找到这只基金")
                metadata = _metadata(asset_type, code, fund.name)
            else:
                raise ValueError("资产类型只支持 fund 或 etf")
        computed = await compute_asset(session, metadata, as_of_date=as_of_date)
    detail_date = as_of_date or computed.latest_date or await latest_data_date(session)
    series = await _series_for_asset(session, computed.metadata, detail_date)
    recent = series[-240:]
    drawdowns = _drawdown_series(recent)
    chart = [
        {
            "date": item.point_date,
            "value": round(item.value, 6),
            "close": item.close,
            "nav": item.nav,
            "drawdown": round(drawdowns[index], 6) if index < len(drawdowns) else 0.0,
            "turnover": item.turnover,
        }
        for index, item in enumerate(recent)
    ]
    key_reason = str(
        computed.rationale.get("key_reason")
        or computed.rationale.get("entry_timing_reason")
        or "使用最新短线信号缓存展示当前评分。"
    )
    risk_explanation = str(computed.rationale.get("risk_explanation") or "风险说明请结合买点、回撤和数据质量查看。")
    opposing_view = str(computed.rationale.get("opposing_view") or "暂无额外反方说明。")
    sections = {
        "投资方向": computed.metadata.investment_direction,
        "为什么上榜": key_reason,
        "今日买点": computed.entry_timing_reason,
        "主要风险": risk_explanation,
        "反方提醒": opposing_view,
        "数据说明": f"{computed.source_note}，最新日期 {computed.latest_date.isoformat() if computed.latest_date else '暂无'}。",
    }
    return computed, chart, sections


async def run_signal_generation(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> ShortResearchSignalRun:
    effective_date = as_of_date or await latest_data_date(session) or date.today()
    run = ShortResearchSignalRun(
        status=RUN_STATUS_RUNNING,
        as_of_date=effective_date,
        scope_kind=scope_kind_for_filters(theme=theme, codes=codes),
        config_json={"asset_type": asset_type, "theme": theme, "codes": codes or [], "language": "research_only"},
        summary_json={},
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    try:
        assets = await list_computed_assets(
            session,
            as_of_date=effective_date,
            asset_type=asset_type,
            theme=theme,
            codes=codes,
            sort="score",
            universe=UNIVERSE_ALL if asset_type == ASSET_TYPE_ETF else UNIVERSE_DEFAULT,
        )
        has_etf_assets = any(asset.metadata.asset_type == ASSET_TYPE_ETF for asset in assets)
        for asset in assets:
            cached_metrics = {
                **asset.metrics,
                "latest_date": asset.latest_date.isoformat() if asset.latest_date else None,
                "latest_value": asset.latest_value,
                "usable_days": asset.usable_days,
                "sample_level": asset.sample_level,
                "source_note": asset.source_note,
            }
            session.add(
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type=asset.metadata.asset_type,
                    asset_code=asset.metadata.code,
                    rank=asset.rank or 0,
                    total_score=round(asset.total_score, 2),
                    conclusion=asset.conclusion,
                    score_breakdown_json=asset.score_breakdown,
                    risk_flags_json=asset.risk_flags,
                    rationale_json=asset.rationale,
                    metrics_json=cached_metrics,
                )
            )
        conclusion_counts: dict[str, int] = {}
        for asset in assets:
            conclusion_counts[asset.conclusion] = conclusion_counts.get(asset.conclusion, 0) + 1
        label_validation = await _label_validation_summary(session, assets, effective_date)
        run.status = RUN_STATUS_SUCCESS
        run.finished_at = utcnow()
        run.summary_json = {
            "item_count": len(assets),
            "fund_count": sum(1 for item in assets if item.metadata.asset_type == ASSET_TYPE_FUND),
            "etf_count": sum(1 for item in assets if item.metadata.asset_type == ASSET_TYPE_ETF),
            "conclusion_counts": conclusion_counts,
            "research_only": True,
            "experiment": {
                "rule_version": _LABEL_VALIDATION_RULE_VERSION,
                "label_validation_windows": list(_LABEL_VALIDATION_WINDOWS),
                "portfolio_single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
                "score_version": FINAL_SCORE_VERSION if has_etf_assets else "legacy",
            },
            "score_version": FINAL_SCORE_VERSION if has_etf_assets else "legacy",
            "label_validation": label_validation,
        }
        await session.commit()
        await session.refresh(run)
        return run
    except Exception as exc:  # noqa: BLE001
        run.status = RUN_STATUS_FAILED
        run.finished_at = utcnow()
        run.error_message = str(exc)
        await session.commit()
        raise


async def signal_run_items_as_assets(session: AsyncSession, run: ShortResearchSignalRun) -> list[ComputedAsset]:
    cached_assets, _total = await cached_signal_assets(session, run, sort="score", universe=UNIVERSE_ALL)
    return cached_assets


async def scheduled_etf_job_freshness(session: AsyncSession) -> dict[str, dict[str, Any]]:
    job_names = tuple(ETF_JOB_FRESHNESS_WINDOWS)
    rows = (
        await session.scalars(
            select(JobRun)
            .where(JobRun.job_name.in_(job_names))
            .order_by(JobRun.job_name.asc(), JobRun.started_at.desc(), JobRun.id.desc())
        )
    ).all()
    latest_by_name: dict[str, JobRun] = {}
    for row in rows:
        latest_by_name.setdefault(row.job_name, row)

    now = utcnow()
    freshness: dict[str, dict[str, Any]] = {}
    for job_name, max_age in ETF_JOB_FRESHNESS_WINDOWS.items():
        row = latest_by_name.get(job_name)
        if row is None:
            freshness[job_name] = {"status": "waiting", "finished_at": None, "last_job_status": None}
            continue
        if row.status == "running":
            status = "running"
        elif row.status != "success":
            status = "degraded" if row.status in {"partial", "skipped"} else "failed"
        elif row.finished_at is None or now - row.finished_at > max_age:
            status = "stale"
        else:
            status = "fresh"
        freshness[job_name] = {
            "status": status,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "last_job_status": row.status,
        }
    return freshness


async def status_summary(session: AsyncSession, *, include_health: bool = False) -> dict[str, Any]:
    await ensure_short_research_universe(session)
    latest_run = await latest_signal_run(session)
    latest_etf_run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    latest = await latest_data_date(session)
    etf_total = int(await session.scalar(select(func.count()).select_from(TradableEtf)) or 0)
    etf_eligible = int(
        await session.scalar(
            select(func.count()).select_from(TradableEtf).where(TradableEtf.is_short_term_eligible.is_(True))
        )
        or 0
    )
    etf_default_display_count = 0
    if latest_etf_run is not None:
        latest_etf_items = await list_signal_items(session, latest_etf_run.id)
        etf_default_display_count = sum(
            1
            for item in latest_etf_items
            if item.asset_type == ASSET_TYPE_ETF and bool((item.metrics_json or {}).get("default_display_eligible", True))
        )
    etf_failed = int(
        await session.scalar(
            select(func.count()).select_from(EtfDataHealth).where(EtfDataHealth.status == "failed")
        )
        or 0
    )
    stale_cutoff = date.today() - timedelta(days=STALE_DATA_DAYS)
    fresh_etf_count = int(
        await session.scalar(
            select(func.count())
            .select_from(EtfDataHealth)
            .join(TradableEtf, TradableEtf.code == EtfDataHealth.etf_code)
            .where(
                TradableEtf.is_short_term_eligible.is_(True),
                EtfDataHealth.status == "success",
                EtfDataHealth.latest_price_date >= stale_cutoff,
            )
        )
        or 0
    )
    etf_data_stale_count = max(0, etf_eligible - fresh_etf_count)
    fund_priced_count = int(
        await session.scalar(select(func.count(func.distinct(FundNavHistory.fund_code)))) or 0
    )
    etf_priced_count = int(
        await session.scalar(
            select(func.count(func.distinct(EtfDataHealth.etf_code))).where(EtfDataHealth.successful_rows > 0)
        )
        or 0
    )
    if latest_run is not None:
        items = await list_signal_items(session, latest_run.id)
        observable_count = sum(1 for item in items if item.conclusion == CONCLUSION_WATCH)
        high_risk_count = sum(1 for item in items if item.conclusion == CONCLUSION_HIGH_WATCH)
    else:
        observable_count = 0
        high_risk_count = 0
    health = await data_health(session) if include_health else []
    if include_health:
        etf_data_stale_count = sum(
            1 for item in health if item["asset_type"] == ASSET_TYPE_ETF and item["is_stale"]
        )
        priced_asset_count = sum(1 for item in health if item["usable_days"] > 0)
        data_issue_count = sum(1 for item in health if item["status"] != "success" or item["is_stale"])
    else:
        priced_asset_count = fund_priced_count + etf_priced_count
        data_issue_count = etf_data_stale_count + etf_failed
    label_validation = await latest_label_validation_summary(session)
    label_validation_generated_at = None
    if isinstance(label_validation, dict) and label_validation.get("generated_at"):
        try:
            label_validation_generated_at = datetime.fromisoformat(str(label_validation["generated_at"]))
        except ValueError:
            label_validation_generated_at = None
    score_bucket_validation = await latest_score_bucket_validation_summary(session)
    score_bucket_validation_generated_at = None
    if isinstance(score_bucket_validation, dict) and score_bucket_validation.get("generated_at"):
        try:
            score_bucket_validation_generated_at = datetime.fromisoformat(str(score_bucket_validation["generated_at"]))
        except ValueError:
            score_bucket_validation_generated_at = None
    theme_coverage = await theme_coverage_summary(session)
    return {
        "latest_data_date": latest,
        "signal_date": latest_run.as_of_date if latest_run else None,
        "theme_coverage": theme_coverage,
        "label_validation": label_validation if isinstance(label_validation, dict) else {},
        "label_validation_generated_at": label_validation_generated_at,
        "score_bucket_validation": score_bucket_validation if isinstance(score_bucket_validation, dict) else {},
        "score_bucket_validation_generated_at": score_bucket_validation_generated_at,
        "asset_count": len(DEFAULT_SHORT_RESEARCH_ASSETS),
        "fund_count": len(DEFAULT_SHORT_RESEARCH_FUND_CODES),
        "etf_count": len(DEFAULT_SHORT_RESEARCH_ETF_CODES),
        "etf_total_count": etf_total,
        "etf_eligible_count": etf_eligible,
        "etf_default_display_count": etf_default_display_count,
        "etf_data_stale_count": etf_data_stale_count,
        "etf_failed_count": etf_failed,
        "priced_asset_count": priced_asset_count,
        "observable_count": observable_count,
        "high_risk_count": high_risk_count,
        "data_issue_count": data_issue_count,
        "data_health": health,
        "job_freshness": await scheduled_etf_job_freshness(session),
    }


async def _latest_etf_sync_state(session: AsyncSession) -> tuple[str, int]:
    run = await session.scalar(
        select(JobRun)
        .where(JobRun.job_name == "daily_short_research_data")
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(1)
    )
    if run is None:
        return "waiting", 0
    details = dict(run.details_json or {})
    etf_details = details.get("etf")
    etf_result = etf_details.get("etfs") if isinstance(etf_details, dict) else None
    deferred = _result_count(etf_result, "skipped") if isinstance(etf_result, dict) else 0
    if run.status == "partial" and deferred:
        return "deferred", deferred
    return run.status, deferred


async def data_health(session: AsyncSession) -> list[dict[str, Any]]:
    metadata_items = await _available_assets(session)
    etf_codes = [item.code for item in metadata_items if item.asset_type == ASSET_TYPE_ETF]
    raw_by_code: dict[str, EtfPriceHistory] = {}
    research_by_code: dict[str, EtfPriceHistory] = {}
    research_count_by_code: dict[str, int] = {}
    health_by_code: dict[str, EtfDataHealth] = {}
    if etf_codes:
        raw_dates = (
            select(
                EtfPriceHistory.etf_code.label("etf_code"),
                func.max(EtfPriceHistory.trade_date).label("trade_date"),
            )
            .where(EtfPriceHistory.etf_code.in_(etf_codes))
            .group_by(EtfPriceHistory.etf_code)
            .subquery()
        )
        raw_rows = await session.scalars(
            select(EtfPriceHistory).join(
                raw_dates,
                (EtfPriceHistory.etf_code == raw_dates.c.etf_code)
                & (EtfPriceHistory.trade_date == raw_dates.c.trade_date),
            )
        )
        raw_by_code = {row.etf_code: row for row in raw_rows.all()}

        research_filter = (
            EtfPriceHistory.etf_code.in_(etf_codes),
            EtfPriceHistory.decision_eligible.is_(True),
            EtfPriceHistory.research_price_basis == "total_return_adjusted",
            EtfPriceHistory.research_adjusted_value.is_not(None),
        )
        research_dates = (
            select(
                EtfPriceHistory.etf_code.label("etf_code"),
                func.max(EtfPriceHistory.trade_date).label("trade_date"),
            )
            .where(*research_filter)
            .group_by(EtfPriceHistory.etf_code)
            .subquery()
        )
        research_rows = await session.scalars(
            select(EtfPriceHistory).join(
                research_dates,
                (EtfPriceHistory.etf_code == research_dates.c.etf_code)
                & (EtfPriceHistory.trade_date == research_dates.c.trade_date),
            )
        )
        research_by_code = {row.etf_code: row for row in research_rows.all()}
        research_counts = await session.execute(
            select(EtfPriceHistory.etf_code, func.count())
            .where(*research_filter)
            .group_by(EtfPriceHistory.etf_code)
        )
        research_count_by_code = {str(code): int(count) for code, count in research_counts}
        health_rows = await session.scalars(select(EtfDataHealth).where(EtfDataHealth.etf_code.in_(etf_codes)))
        health_by_code = {row.etf_code: row for row in health_rows.all()}

    sync_state, deferred_count = await _latest_etf_sync_state(session)
    rows: list[dict[str, Any]] = []
    today = date.today()
    for metadata in metadata_items:
        if metadata.asset_type != ASSET_TYPE_ETF:
            series = await _series_for_asset(session, metadata)
            latest = series[-1].point_date if series else None
            rows.append(
                {
                    "asset_type": metadata.asset_type,
                    "code": metadata.code,
                    "name": metadata.name,
                    "status": "missing" if latest is None else "success",
                    "latest_date": latest,
                    "raw_latest_date": latest,
                    "research_latest_date": latest,
                    "usable_days": len(series),
                    "provider": None,
                    "provider_version": None,
                    "research_price_basis": "accumulated_nav",
                    "source_timestamp": None,
                    "decision_eligible": None,
                    "decision_ineligibility_reason": None,
                    "sync_state": "not_applicable",
                    "sync_deferred_count": 0,
                    "issue_details": [] if latest else ["missing_accumulated_nav"],
                    "source_note": "公开基金净值数据",
                    "last_error_message": None,
                    "is_stale": latest is None or (today - latest).days > STALE_DATA_DAYS,
                }
            )
            continue

        raw = raw_by_code.get(metadata.code)
        research = research_by_code.get(metadata.code)
        etf_health = health_by_code.get(metadata.code)
        raw_latest_date = raw.trade_date if raw else None
        research_latest_date = research.trade_date if research else None
        is_stale = research_latest_date is None or (today - research_latest_date).days > STALE_DATA_DAYS
        issues: list[str] = []
        if raw is None:
            issues.append("missing_raw_daily_price")
        if research is None:
            issues.append("missing_research_price_provenance")
        if raw is not None and raw.decision_eligible is False and raw.decision_ineligibility_reason:
            issues.append(raw.decision_ineligibility_reason)
        if is_stale and research_latest_date is not None:
            issues.append("stale_research_price")
        if etf_health and etf_health.last_error_message:
            issues.append(etf_health.last_error_message)
        if sync_state == "deferred":
            issues.append("daily_sync_deferred")

        if etf_health and etf_health.status == "failed":
            status = "failed"
        elif raw is None:
            status = "missing"
        elif research is None:
            status = "unavailable"
        elif is_stale:
            status = "stale"
        else:
            status = "success"
        rows.append(
            {
                "asset_type": metadata.asset_type,
                "code": metadata.code,
                "name": metadata.name,
                "status": status,
                "latest_date": research_latest_date,
                "raw_latest_date": raw_latest_date,
                "research_latest_date": research_latest_date,
                "usable_days": research_count_by_code.get(metadata.code, 0),
                "provider": (raw.data_provider if raw else None) or (etf_health.provider if etf_health else None),
                "provider_version": raw.provider_version if raw else None,
                "research_price_basis": research.research_price_basis if research else None,
                "source_timestamp": raw.source_timestamp if raw else None,
                "decision_eligible": raw.decision_eligible if raw else None,
                "decision_ineligibility_reason": raw.decision_ineligibility_reason if raw else None,
                "sync_state": sync_state,
                "sync_deferred_count": deferred_count,
                "issue_details": issues,
                "source_note": "公开 ETF 原始日线及复权研究数据",
                "last_error_message": etf_health.last_error_message if etf_health else None,
                "is_stale": is_stale,
            }
        )
    return rows


def _result_count(result: dict[str, Any], key: str) -> int:
    value = result.get(key, 0)
    if isinstance(value, int | float | str):
        return int(value)
    return 0


def _etf_sync_batch_size() -> int:
    try:
        return max(1, int(os.getenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", str(DEFAULT_ETF_SYNC_BATCH_SIZE))))
    except ValueError:
        return DEFAULT_ETF_SYNC_BATCH_SIZE


def _etf_sync_max_batches() -> int:
    try:
        return max(
            1,
            int(os.getenv("SHORT_RESEARCH_ETF_SYNC_MAX_BATCHES", str(DEFAULT_ETF_SYNC_MAX_BATCHES))),
        )
    except ValueError:
        return DEFAULT_ETF_SYNC_MAX_BATCHES


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


async def _dynamic_etf_codes(session: AsyncSession, codes: list[str] | None = None) -> list[str]:
    query = select(TradableEtf).where(TradableEtf.is_short_term_eligible.is_(True))
    if codes:
        query = query.where(TradableEtf.code.in_(codes))
    rows = await session.scalars(query.order_by(TradableEtf.code.asc()))
    return [row.code for row in rows.all()]


async def _prioritize_etf_sync_codes(
    session: AsyncSession,
    codes: list[str],
    priority_codes: list[str] | None = None,
) -> list[str]:
    if not codes:
        return []
    code_set = set(codes)
    priority = list(dict.fromkeys(str(code) for code in (priority_codes or []) if str(code) in code_set))
    watchlist_rows = await session.scalars(
        select(TradableEtf.code).where(
            TradableEtf.code.in_(codes),
            TradableEtf.is_watchlist.is_(True),
        )
    )
    watchlist = list(dict.fromkeys(str(code) for code in watchlist_rows.all()))
    remaining = sorted(code_set - set(priority) - set(watchlist))
    return [
        *priority,
        *(code for code in watchlist if code not in priority),
        *remaining,
    ]


def _rotate_sync_codes(codes: list[str], last_code: str | None) -> list[str]:
    if not last_code or last_code not in codes:
        return codes
    index = codes.index(last_code)
    return [*codes[index + 1 :], *codes[: index + 1]]


async def _select_bounded_etf_sync_codes(
    session: AsyncSession,
    *,
    codes: list[str],
    priority_codes: list[str] | None,
    to_date: date,
    limit: int,
) -> EtfSyncSelection:
    if not codes or limit < 1:
        return EtfSyncSelection([], None, None, None)

    code_set = set(codes)
    priority_set = {str(code) for code in (priority_codes or []) if str(code) in code_set}
    rows = await session.execute(
        select(TradableEtf.code, TradableEtf.is_watchlist).where(TradableEtf.code.in_(code_set))
    )
    default_display_codes = {str(code) for code, is_watchlist in rows if is_watchlist} - priority_set
    latest_rows = await session.execute(
        select(EtfPriceHistory.etf_code, func.max(EtfPriceHistory.trade_date))
        .where(EtfPriceHistory.etf_code.in_(code_set))
        .group_by(EtfPriceHistory.etf_code)
    )
    latest_by_code = {str(code): latest_date for code, latest_date in latest_rows}

    def freshness(code: str) -> int:
        latest_date = latest_by_code.get(code)
        if latest_date is None:
            return 0
        return 1 if latest_date < to_date else 2

    priority = sorted(
        priority_set | default_display_codes,
        key=lambda code: (freshness(code), 0 if code in priority_set else 1, code),
    )
    regular = sorted(code_set - set(priority), key=lambda code: (freshness(code), code))
    cursor = await session.get(EtfSyncCursor, ETF_DAILY_SYNC_CURSOR_SCOPE)
    priority_codes_rotated = _rotate_sync_codes(priority, cursor.last_priority_code if cursor else None)
    regular_codes_rotated = _rotate_sync_codes(regular, cursor.last_regular_code if cursor else None)

    selected_priority: list[str] = []
    selected_regular: list[str] = []
    if priority_codes_rotated and regular_codes_rotated and limit == 1:
        if cursor and cursor.last_lane == "priority":
            selected_regular = regular_codes_rotated[:1]
        else:
            selected_priority = priority_codes_rotated[:1]
    elif priority_codes_rotated and regular_codes_rotated:
        priority_slots = limit // 2
        selected_priority = priority_codes_rotated[:priority_slots]
        selected_regular = regular_codes_rotated[: limit - len(selected_priority)]
        remaining = limit - len(selected_priority) - len(selected_regular)
        if remaining:
            selected_priority.extend(priority_codes_rotated[len(selected_priority) : len(selected_priority) + remaining])
    else:
        selected_priority = priority_codes_rotated[:limit]
        selected_regular = regular_codes_rotated[: limit - len(selected_priority)]

    last_priority_code = selected_priority[-1] if selected_priority else (cursor.last_priority_code if cursor else None)
    last_regular_code = selected_regular[-1] if selected_regular else (cursor.last_regular_code if cursor else None)
    last_lane = "regular" if selected_regular else "priority" if selected_priority else None
    return EtfSyncSelection(
        codes=[*selected_priority, *selected_regular],
        last_priority_code=last_priority_code,
        last_regular_code=last_regular_code,
        last_lane=last_lane,
    )


async def _persist_etf_sync_cursor(session: AsyncSession, selection: EtfSyncSelection) -> None:
    if not selection.codes:
        return
    cursor = await session.get(EtfSyncCursor, ETF_DAILY_SYNC_CURSOR_SCOPE)
    if cursor is None:
        cursor = EtfSyncCursor(scope=ETF_DAILY_SYNC_CURSOR_SCOPE)
        session.add(cursor)
    cursor.last_priority_code = selection.last_priority_code
    cursor.last_regular_code = selection.last_regular_code
    cursor.last_lane = selection.last_lane
    await session.commit()


async def sync_short_research_data(
    session: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    asset_type: str | None = None,
    codes: list[str] | None = None,
    sync_all_etfs: bool = False,
    priority_etf_codes: list[str] | None = None,
) -> dict[str, Any]:
    await ensure_short_research_universe(session)
    fund_codes = [
        item.code
        for item in DEFAULT_SHORT_RESEARCH_ASSETS
        if item.asset_type == ASSET_TYPE_FUND
        and _matches_filters(item, asset_type=asset_type, codes=codes)
        and is_short_term_eligible_name(item.name)
    ]
    etf_codes = await _dynamic_etf_codes(session, codes) if asset_type in (None, ASSET_TYPE_ETF) else []
    bounded_etf_sync = bool(etf_codes) and not sync_all_etfs and not codes
    sync_selection: EtfSyncSelection | None = None
    fund_result = {"funds": 0, "rows_inserted": 0, "rows_updated": 0, "failed": 0, "failures": []}
    etf_result: dict[str, Any] = {
        "etfs": 0,
        "inserted": 0,
        "updated": 0,
        "failed": 0,
        "failures": [],
        "batches": 0,
        "batches_total": 0,
        "batch_size": _etf_sync_batch_size(),
        "max_batches": None if sync_all_etfs or codes else _etf_sync_max_batches(),
        "total_candidates": len(etf_codes),
        "processed": 0,
        "skipped": 0,
    }
    if fund_codes:
        fund_result = await sync_fund_nav_history(session, from_date, to_date, fund_codes)
    if etf_codes:
        batch_size = _etf_sync_batch_size()
        if bounded_etf_sync:
            sync_selection = await _select_bounded_etf_sync_codes(
                session,
                codes=etf_codes,
                priority_codes=priority_etf_codes,
                to_date=to_date,
                limit=batch_size * _etf_sync_max_batches(),
            )
            batches = _chunks(sync_selection.codes, batch_size)
            batches_total = len(_chunks(etf_codes, batch_size))
        else:
            prioritized_codes = await _prioritize_etf_sync_codes(session, etf_codes, priority_etf_codes)
            all_batches = _chunks(prioritized_codes, batch_size)
            batches = all_batches
            batches_total = len(all_batches)
        for batch in batches:
            batch_result = await sync_etf_price_history(session, from_date, to_date, batch)
            etf_result["etfs"] += _result_count(batch_result, "etfs")
            etf_result["inserted"] += _result_count(batch_result, "inserted")
            etf_result["updated"] += _result_count(batch_result, "updated")
            etf_result["failed"] += _result_count(batch_result, "failed")
            etf_result["failures"].extend(batch_result.get("failures", []))
        etf_result["batches"] = len(batches)
        etf_result["batches_total"] = batches_total
        etf_result["processed"] = sum(len(batch) for batch in batches)
        etf_result["skipped"] = max(0, len(etf_codes) - etf_result["processed"])
        if sync_selection is not None:
            await _persist_etf_sync_cursor(session, sync_selection)
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "funds": fund_result,
        "etfs": etf_result,
        "asset_count": len(fund_codes) + len(etf_codes),
        "failed": _result_count(fund_result, "failed") + _result_count(etf_result, "failed"),
    }


async def refresh_dynamic_etf_universe(session: AsyncSession) -> dict[str, Any]:
    return await refresh_etf_universe(session)


def _portfolio_exposure_for_asset(asset: ComputedAsset) -> float:
    exposure = _PORTFOLIO_SINGLE_WEIGHT_CAP
    if any(flag in asset.risk_flags for flag in _PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT):
        exposure -= 0.05
    if (asset.metrics.get("volatility_20d") or 0.0) > 0.025:
        exposure -= 0.03
    if (asset.metrics.get("max_drawdown_60d") or 0.0) < -0.12:
        exposure -= 0.03
    if not asset.metrics.get("default_display_eligible", False):
        exposure -= 0.05
    return float(max(0.05, min(_PORTFOLIO_SINGLE_WEIGHT_CAP, exposure)))


def _portfolio_exclusion_reason(asset: ComputedAsset) -> str | None:
    if not asset.metrics.get("default_display_eligible", True):
        reasons = asset.metrics.get("default_exclusion_reasons") or ["数据质量未通过默认精选门槛"]
        return "数据质量未通过：" + "、".join(str(item) for item in reasons)
    forbidden = [flag for flag in _PORTFOLIO_RISK_FLAGS_FORBIDDEN if flag in asset.risk_flags]
    if forbidden:
        return f"数据或流动性不足：{'、'.join(forbidden)}"
    if asset.conclusion in {CONCLUSION_REJECT, CONCLUSION_INSUFFICIENT}:
        return f"观察标签不适合短线组合（当前：{asset.conclusion}）"
    if asset.entry_timing_label in {
        ENTRY_TIMING_BREAK_WAIT,
        ENTRY_TIMING_VOLUME_WEAKENING,
        ENTRY_TIMING_INSUFFICIENT,
    }:
        return f"今日买点需要等待（当前：{asset.entry_timing_label}）"
    return None


def _portfolio_watch_only_reason(asset: ComputedAsset) -> str | None:
    if asset.entry_timing_label == ENTRY_TIMING_CHASE_RISK:
        return f"今日买点为{ENTRY_TIMING_CHASE_RISK}，适合继续盯，不分配主组合权重。"
    watch_flags = [flag for flag in _PORTFOLIO_RISK_FLAGS_WATCH_ONLY if flag in asset.risk_flags]
    if watch_flags:
        return f"存在{'、'.join(watch_flags)}，强势但不适合追入。"
    if asset.conclusion == CONCLUSION_HIGH_WATCH:
        if asset.entry_timing_label in _PORTFOLIO_ENTRY_TIMING_OK:
            return None
        return "趋势强但处于高位观察，但买点不健康，先放入强势但别追，不给组合权重。"
    if asset.conclusion != CONCLUSION_WATCH:
        return f"观察标签非短线观察（当前：{asset.conclusion}）。"
    if asset.entry_timing_label not in _PORTFOLIO_ENTRY_TIMING_OK:
        return f"今日买点不满足主组合筛选（当前：{asset.entry_timing_label}）。"
    return None


def _portfolio_candidate_group(asset: ComputedAsset) -> tuple[str, str | None]:
    exclusion_reason = _portfolio_exclusion_reason(asset)
    if exclusion_reason is not None:
        return PORTFOLIO_LAYER_EXCLUDED, exclusion_reason
    if asset.conclusion == CONCLUSION_HIGH_WATCH and asset.entry_timing_label in _PORTFOLIO_ENTRY_TIMING_OK:
        return (
            PORTFOLIO_LAYER_SATELLITE,
            "高位观察但买点仍为健康回踩/趋势延续，只进入小仓观察层并降低权重。",
        )
    watch_reason = _portfolio_watch_only_reason(asset)
    if watch_reason is not None:
        return PORTFOLIO_LAYER_WATCH_ONLY, watch_reason
    return PORTFOLIO_LAYER_PRIMARY, None


def _portfolio_fill_priority(asset: ComputedAsset) -> int:
    tags = set(asset.metadata.theme_tags)
    name = asset.metadata.name
    asset_class = asset.metadata.category or ""
    if asset_class == "broad" or "宽基" in tags or "宽基" in name:
        return 0
    if asset_class in {"bond", "commodity"} or any(key in name for key in ("国债", "债", "黄金", "红利")):
        return 1
    if tags.intersection({"红利", "金融", "银行", "消费"}):
        return 2
    return 9


def _portfolio_defensive_priority(asset: ComputedAsset) -> int:
    tags = set(asset.metadata.theme_tags)
    name = asset.metadata.name
    asset_class = asset.metadata.category or ""
    if asset_class in {"cash", "money"} or any(key in name for key in ("货币", "现金", "短融")):
        return 0
    if asset_class == "bond" or "债券" in tags or any(key in name for key in ("国债", "政金债", "债券", "债ETF", "可转债")):
        return 1
    if asset_class == "commodity" or "黄金" in tags or "黄金" in name:
        return 2
    if "红利" in tags or "红利" in name:
        return 3
    if asset_class == "broad" or "宽基" in tags or "宽基" in name:
        volatility = float(asset.metrics.get("volatility_20d") or 1.0)
        drawdown = float(asset.metrics.get("max_drawdown_60d") or -1.0)
        if volatility <= 0.025 and drawdown >= -0.10:
            return 4
    return 99


def _portfolio_theme_keys(asset: ComputedAsset) -> list[str]:
    profile = dict(asset.metrics.get("theme_profile") or asset.rationale.get("theme_profile") or {})
    keys = [
        str(profile.get("theme_group") or "").strip(),
        str(profile.get("primary_theme") or "").strip(),
    ]
    filtered = [
        item
        for item in dict.fromkeys(keys)
        if item and item not in {UNKNOWN_GROUP, UNKNOWN_THEME}
    ]
    if filtered:
        return filtered
    return [
        item
        for item in dict.fromkeys(asset.metadata.theme_tags[:2])
        if item and item not in {UNKNOWN_GROUP, UNKNOWN_THEME}
    ]


def _portfolio_defensive_reason(asset: ComputedAsset, original_reason: str | None) -> str | None:
    if not asset.metrics.get("default_display_eligible", True):
        return None
    if asset.conclusion in {CONCLUSION_REJECT, CONCLUSION_INSUFFICIENT}:
        return None
    if any(flag in asset.risk_flags for flag in _PORTFOLIO_RISK_FLAGS_FORBIDDEN):
        return None
    if _portfolio_defensive_priority(asset) >= 99:
        return None
    if asset.entry_timing_label in {ENTRY_TIMING_VOLUME_WEAKENING, ENTRY_TIMING_INSUFFICIENT}:
        return None
    if asset.entry_timing_label == ENTRY_TIMING_BREAK_WAIT:
        today_return = float(asset.metrics.get("today_return_pct") or 0.0)
        drawdown = float(asset.metrics.get("max_drawdown_60d") or 0.0)
        if today_return <= -0.025 or drawdown <= -0.12:
            return None
    base = "进攻候选不足；该 ETF 属于货币/债券/黄金/红利/低波动宽基等防守候选，用于降低风险暴露。"
    if original_reason:
        return f"{base}原分组原因：{original_reason}"
    return base


def _portfolio_fill_reason(asset: ComputedAsset, original_reason: str | None) -> str | None:
    if not asset.metrics.get("default_display_eligible", True):
        return None
    if asset.entry_timing_label not in _PORTFOLIO_ENTRY_TIMING_OK:
        return None
    if any(flag in asset.risk_flags for flag in _PORTFOLIO_RISK_FLAGS_FORBIDDEN):
        return None
    if _portfolio_fill_priority(asset) > 2:
        return None
    base = "主组合高分候选不足 4 只；用数据可靠、流动性合格、非冲高别追的宽基/防守候选补足 ETF 账户资金 100%。"
    if original_reason:
        return f"{base}原分组原因：{original_reason}"
    return base

def _portfolio_decision_factors(
    asset: ComputedAsset,
    *,
    target_weight: float,
    reason: str | None,
    weight_reason: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "score": round(asset.total_score, 2),
        "validation_confidence": asset.metrics.get("validation_confidence"),
        "validation_sample_count": int(asset.metrics.get("validation_sample_count") or 0),
        "volatility_20d": asset.metrics.get("volatility_20d"),
        "max_drawdown_60d": asset.metrics.get("max_drawdown_60d"),
        "average_turnover_20d": asset.metrics.get("average_turnover_20d"),
        "entry_timing_label": asset.entry_timing_label,
        "risk_flags": list(asset.risk_flags),
        "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
        "target_weight": round(target_weight, 4),
        "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "exclusion_reason": reason,
        "weight_reason": weight_reason or {},
    }


def _portfolio_weight_explanation(
    asset: ComputedAsset,
    *,
    target_weight: float,
    weight_reason: dict[str, Any] | None = None,
) -> str | None:
    if target_weight <= 0:
        return None
    confidence = asset.metrics.get("validation_confidence") or "未验证"
    sample_count = int(asset.metrics.get("validation_sample_count") or 0)
    turnover = float(asset.metrics.get("average_turnover_20d") or 0.0) / 100_000_000
    cap_note = "，触及单只30%上限" if weight_reason and weight_reason.get("single_cap_applied") else ""
    target_total = float((weight_reason or {}).get("portfolio_target_weight") or _PORTFOLIO_TOTAL_EXPOSURE_CAP)
    cash_wait = float((weight_reason or {}).get("cash_wait_weight") or 0.0)
    exposure_note = (
        f"归一到当前合格 ETF 权重 {target_total * 100:.0f}%，剩余资金 {cash_wait * 100:.0f}% 等待"
        if cash_wait > 0.0001
        else "归一到 ETF 账户资金 100%"
    )
    return (
        f"给 {target_weight * 100:.0f}% 观察权重：综合分 {asset.total_score:.1f}，"
        f"标签验证 {confidence}（样本 {sample_count}），20日成交额约 {turnover:.2f} 亿元；"
        f"按分数倾斜、波动率、回撤、成交额、主题和相关性约束{exposure_note}{cap_note}。"
    )


def _portfolio_exclusion_explanation(reason: str | None) -> str | None:
    if reason is None:
        return None
    return f"未分配主组合权重：{reason}"


def _portfolio_item(
    asset: ComputedAsset,
    *,
    target_weight: float,
    reason: str | None = None,
    weight_reason: dict[str, Any] | None = None,
) -> dict[str, Any]:
    risk_reasons = asset.risk_flags or ["暂未触发主要风险标签"]
    if reason:
        risk_reasons = [reason, *risk_reasons]
    risk_reasons = [
        *risk_reasons,
        f"今日买点：{asset.entry_timing_label}",
        f"买点原因：{asset.entry_timing_reason}",
    ]
    validation_confidence = asset.metrics.get("validation_confidence")
    if validation_confidence:
        risk_reasons.append(
            f"标签验证：{validation_confidence}，样本 {int(asset.metrics.get('validation_sample_count') or 0)}"
        )
    weight_explanation = _portfolio_weight_explanation(
        asset,
        target_weight=target_weight,
        weight_reason=weight_reason,
    )
    exclusion_explanation = _portfolio_exclusion_explanation(reason)
    decision_factors = _portfolio_decision_factors(
        asset,
        target_weight=target_weight,
        reason=reason,
        weight_reason=weight_reason,
    )
    theme_profile = dict(asset.metrics.get("theme_profile") or asset.rationale.get("theme_profile") or {})
    metrics = {
        **asset.metrics,
        "portfolio_theme_keys": _portfolio_theme_keys(asset),
        "portfolio_weight_explanation": weight_explanation,
        "portfolio_exclusion_explanation": exclusion_explanation,
        "portfolio_decision_factors": decision_factors,
        "portfolio_weight_reason": weight_reason or {},
    }
    return {
        "asset_type": asset.metadata.asset_type,
        "code": asset.metadata.code,
        "name": asset.metadata.name,
        "target_weight": round(target_weight, 4),
        "score": round(asset.total_score, 2),
        "conclusion": asset.conclusion,
        "entry_timing_label": asset.entry_timing_label,
        "primary_theme": theme_profile.get("primary_theme"),
        "theme_group": theme_profile.get("theme_group"),
        "data_date": asset.latest_date,
        "evidence": [
            f"综合分 {asset.total_score:.1f}",
            f"近20日收益 {_format_percent(asset.metrics.get('return_20d'))}",
            f"近20日平均成交额 {float(asset.metrics.get('average_turnover_20d') or 0) / 100_000_000:.2f} 亿元",
            f"今日买点 {asset.entry_timing_label}：{asset.entry_timing_reason}",
            f"买点原因：{asset.entry_timing_reason}",
        ],
        "risk_reasons": risk_reasons,
        "weight_explanation": weight_explanation,
        "exclusion_explanation": exclusion_explanation,
        "decision_factors": decision_factors,
        "weight_reason_json": weight_reason or {},
        "metrics": metrics,
    }


def _portfolio_layer_cap(item_type: str) -> float:
    if item_type == PORTFOLIO_LAYER_SATELLITE:
        return _PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP
    return _PORTFOLIO_SINGLE_WEIGHT_CAP


def _portfolio_raw_weight(asset: ComputedAsset) -> tuple[float, dict[str, Any]]:
    score_component = max(0.01, float(asset.total_score or 0.0) / 100.0)
    entry_multiplier = 1.0
    if asset.entry_timing_label == ENTRY_TIMING_HEALTHY_PULLBACK:
        entry_multiplier = 1.08
    elif asset.entry_timing_label == ENTRY_TIMING_TREND_CONTINUATION:
        entry_multiplier = 1.0
    label_multiplier = 0.68 if asset.conclusion == CONCLUSION_HIGH_WATCH else 1.0
    volatility = max(0.006, float(asset.metrics.get("volatility_20d") or 0.025))
    drawdown = abs(min(0.0, float(asset.metrics.get("max_drawdown_60d") or 0.0)))
    turnover = max(1.0, float(asset.metrics.get("average_turnover_20d") or 0.0))
    liquidity_component = min(1.25, max(0.75, (turnover / 100_000_000) ** 0.2))
    drawdown_adjustment = max(0.55, 1.0 - drawdown * 1.5)
    risk_adjusted = score_component * entry_multiplier * label_multiplier * liquidity_component * drawdown_adjustment / volatility
    return risk_adjusted, {
        "score_component": round(score_component, 4),
        "entry_timing_multiplier": round(entry_multiplier, 4),
        "label_multiplier": round(label_multiplier, 4),
        "volatility_unit": round(volatility, 6),
        "drawdown_adjustment": round(drawdown_adjustment, 4),
        "liquidity_component": round(liquidity_component, 4),
        "raw_weight_score": round(risk_adjusted, 6),
        "data_reliability": "verified_or_alternate_provider",
    }


def _cap_normalized_weights(
    raw_weights: list[float],
    cap: float,
    *,
    target_total: float = 1.0,
) -> list[float] | None:
    return _cap_normalized_weights_by_caps(raw_weights, [cap for _item in raw_weights], target_total=target_total)


def _cap_normalized_weights_by_caps(
    raw_weights: list[float],
    caps: list[float],
    *,
    target_total: float = 1.0,
) -> list[float] | None:
    if not raw_weights:
        return None
    if len(raw_weights) != len(caps):
        raise ValueError("raw_weights and caps length mismatch")
    target = min(max(target_total, 0.0), 1.0)
    if target <= 0:
        return [0.0 for _item in raw_weights]
    if sum(caps) + 1e-9 < target:
        return None
    remaining_indices = set(range(len(raw_weights)))
    weights = [0.0 for _item in raw_weights]
    remaining_weight = target
    while remaining_indices:
        raw_total = sum(raw_weights[index] for index in remaining_indices)
        if raw_total <= 0:
            equal = remaining_weight / len(remaining_indices)
            for index in list(remaining_indices):
                weights[index] = min(caps[index], equal)
            break
        capped_this_round: list[int] = []
        for index in remaining_indices:
            proposed = remaining_weight * raw_weights[index] / raw_total
            if proposed > caps[index]:
                weights[index] = caps[index]
                capped_this_round.append(index)
        if not capped_this_round:
            for index in remaining_indices:
                weights[index] = remaining_weight * raw_weights[index] / raw_total
            break
        for index in capped_this_round:
            remaining_indices.remove(index)
            remaining_weight -= weights[index]
    total = sum(weights)
    if total <= 0:
        return None
    if abs(total - target) > 0.0001:
        scale = target / total
        weights = [weight * scale for weight in weights]
    return [round(weight, 4) for weight in weights]

def _series_return_by_date(series: list[PricePoint], window: int = 60) -> dict[date, float]:
    recent = series[-(window + 1) :]
    returns: dict[date, float] = {}
    for previous, current in zip(recent, recent[1:], strict=False):
        if previous.value > 0:
            returns[current.point_date] = current.value / previous.value - 1.0
    return returns


def _correlation(left: dict[date, float], right: dict[date, float]) -> float | None:
    common_dates = sorted(set(left).intersection(right))
    if len(common_dates) < _PORTFOLIO_CORRELATION_MIN_POINTS:
        return None
    left_values = [left[item] for item in common_dates]
    right_values = [right[item] for item in common_dates]
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    numerator = sum((left_item - left_mean) * (right_item - right_mean) for left_item, right_item in zip(left_values, right_values, strict=True))
    left_denominator = sum((item - left_mean) ** 2 for item in left_values) ** 0.5
    right_denominator = sum((item - right_mean) ** 2 for item in right_values) ** 0.5
    if left_denominator == 0 or right_denominator == 0:
        return None
    return float(numerator / (left_denominator * right_denominator))


async def _portfolio_return_maps(
    session: AsyncSession,
    assets: list[ComputedAsset],
    as_of_date: date | None,
) -> dict[str, dict[date, float]]:
    result: dict[str, dict[date, float]] = {}
    for asset in assets:
        series = await _etf_series(session, asset.metadata.code, as_of_date)
        returns = _series_return_by_date(series)
        if len(returns) >= _PORTFOLIO_CORRELATION_MIN_POINTS:
            result[asset.metadata.code] = returns
    return result


async def latest_observation_portfolio_snapshot(
    session: AsyncSession,
) -> EtfObservationPortfolioSnapshot | None:
    return cast(
        EtfObservationPortfolioSnapshot | None,
        await session.scalar(
            select(EtfObservationPortfolioSnapshot)
            .where(EtfObservationPortfolioSnapshot.asset_type == ASSET_TYPE_ETF)
            .order_by(
                EtfObservationPortfolioSnapshot.as_of_date.desc(),
                EtfObservationPortfolioSnapshot.id.desc(),
            )
        ),
    )


def _snapshot_item_out(row: EtfObservationPortfolioItem) -> dict[str, Any]:
    metrics = dict(row.metrics_json or {})
    decision_factors = metrics.get("portfolio_decision_factors")
    if not isinstance(decision_factors, dict):
        decision_factors = {}
    weight_reason = metrics.get("portfolio_weight_reason")
    if not isinstance(weight_reason, dict):
        weight_reason = {}
    return {
        "asset_type": ASSET_TYPE_ETF,
        "code": row.asset_code,
        "name": row.asset_name,
        "target_weight": row.target_weight,
        "score": row.score,
        "conclusion": row.conclusion,
        "entry_timing_label": row.entry_timing_label,
        "data_date": row.data_date,
        "evidence": list(row.evidence_json or []),
        "risk_reasons": list(row.risk_reasons_json or []),
        "item_type": row.item_type,
        "exclusion_reason": row.exclusion_reason,
        "weight_explanation": metrics.get("portfolio_weight_explanation"),
        "exclusion_explanation": metrics.get("portfolio_exclusion_explanation"),
        "decision_factors": decision_factors,
        "weight_reason_json": weight_reason,
        "metrics": metrics,
    }


def _json_time(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _allocation_contract_from_portfolio(
    portfolio: dict[str, Any],
    *,
    portfolio_run_id: int | None = None,
    source_signal_run_id: int | None = None,
) -> dict[str, Any]:
    target_weights: dict[str, float] = {}
    for key in ("items", "satellite_items", "defensive_items"):
        for item in portfolio.get(key, []):
            code = str(item.get("code") or "")
            if code:
                target_weights[code] = float(item.get("target_weight") or 0.0)
    return build_allocation_contract(
        portfolio_run_id=portfolio_run_id,
        source_signal_run_id=source_signal_run_id,
        portfolio_mode=str(portfolio.get("portfolio_mode") or PORTFOLIO_MODE_RISK_ON),
        market_regime=str(portfolio.get("market_regime") or MARKET_REGIME_RISK_ON),
        target_weights=target_weights,
        allocation_layers={
            "primary_weight": portfolio.get("primary_weight", 0.0),
            "satellite_weight": portfolio.get("satellite_weight", 0.0),
            "risk_exposure_weight": portfolio.get("risk_exposure_weight", 0.0),
            "defensive_weight": portfolio.get("defensive_weight", 0.0),
            "cash_weight": portfolio.get("cash_weight", 0.0),
        },
        constraints=dict(portfolio.get("constraints_used") or portfolio.get("constraint_summary") or {}),
        data_as_of_time=portfolio.get("data_as_of_time") or portfolio.get("quote_time") or portfolio.get("daily_signal_date"),
    )


def _observation_snapshot_is_usable(snapshot: EtfObservationPortfolioSnapshot) -> bool:
    summary = dict(snapshot.summary_json or {})
    if summary.get("allocation_contract"):
        return True
    if summary.get("portfolio_mode") == PORTFOLIO_MODE_CASH_WAIT:
        return True
    try:
        cash_weight = float(summary.get("cash_weight", 1.0))
        target_weight = float(summary.get("target_invested_weight", 0.0))
        weight_sum = float(summary.get("weight_sum", 0.0))
    except (TypeError, ValueError):
        return False
    return cash_weight <= 0.0001 and target_weight >= 0.999 and weight_sum >= 0.999


async def observation_portfolio_from_snapshot(
    session: AsyncSession,
    snapshot: EtfObservationPortfolioSnapshot,
) -> dict[str, Any]:
    source_run = (
        await session.get(ShortResearchSignalRun, snapshot.source_signal_run_id)
        if snapshot.source_signal_run_id is not None
        else None
    )
    rows = (
        await session.scalars(
            select(EtfObservationPortfolioItem)
            .where(EtfObservationPortfolioItem.snapshot_id == snapshot.id)
            .order_by(EtfObservationPortfolioItem.item_type.asc(), EtfObservationPortfolioItem.rank_order.asc())
        )
    ).all()
    primary = [_snapshot_item_out(row) for row in rows if row.item_type == "primary"]
    satellite = [_snapshot_item_out(row) for row in rows if row.item_type == "satellite"]
    defensive = [_snapshot_item_out(row) for row in rows if row.item_type == "defensive"]
    watch_only = [_snapshot_item_out(row) for row in rows if row.item_type == "watch_only"]
    excluded = [_snapshot_item_out(row) for row in rows if row.item_type == "excluded"]
    summary = dict(snapshot.summary_json or {})
    weighted_items = [*primary, *satellite, *defensive]
    weight_sum = round(sum(float(item.get("target_weight") or 0.0) for item in weighted_items), 4)
    allocation_contract = dict(summary.get("allocation_contract") or {})
    evidence_status = EVIDENCE_STATUS_WAITING if allocation_contract else EVIDENCE_STATUS_LEGACY
    return {
        "snapshot_id": snapshot.id,
        "generated_at": snapshot.created_at,
        "as_of_date": snapshot.as_of_date,
        "asset_type": snapshot.asset_type,
        "items": primary,
        "satellite_items": satellite,
        "defensive_items": defensive,
        "watch_only_items": watch_only,
        "excluded_items": excluded,
        "cash_weight": float(summary.get("cash_weight", round(max(0.0, 1.0 - weight_sum), 4))),
        "target_invested_weight": float(summary.get("target_invested_weight", _PORTFOLIO_TOTAL_EXPOSURE_CAP)),
        "weight_sum": float(summary.get("weight_sum", weight_sum)),
        "portfolio_mode": summary.get("portfolio_mode", PORTFOLIO_MODE_RISK_ON),
        "market_regime": summary.get("market_regime", MARKET_REGIME_RISK_ON),
        "primary_weight": float(summary.get("primary_weight", round(sum(float(item.get("target_weight") or 0.0) for item in primary), 4))),
        "satellite_weight": float(summary.get("satellite_weight", round(sum(float(item.get("target_weight") or 0.0) for item in satellite), 4))),
        "risk_exposure_weight": float(
            summary.get(
                "risk_exposure_weight",
                round(sum(float(item.get("target_weight") or 0.0) for item in [*primary, *satellite]), 4),
            )
        ),
        "defensive_weight": float(summary.get("defensive_weight", round(sum(float(item.get("target_weight") or 0.0) for item in defensive), 4))),
        "cash_reason": summary.get("cash_reason"),
        "single_weight_cap": summary.get("single_weight_cap", _PORTFOLIO_SINGLE_WEIGHT_CAP),
        "total_exposure_cap": summary.get("total_exposure_cap", _PORTFOLIO_TOTAL_EXPOSURE_CAP),
        "constraint_summary": summary.get("constraint_summary", {}),
        "constraints_used": summary.get("constraints_used", summary.get("constraint_summary", {})),
        "risk_summary": summary.get("risk_summary", {}),
        "data_reliability_summary": summary.get("data_reliability_summary", {}),
        "unavailable_reason": summary.get("unavailable_reason"),
        "research_only": True,
        "no_trade_instruction": True,
        "note": str(summary.get("note") or "观察组合只用于手动研究参考，不连接券商、不自动下单。"),
        "methodology": str(summary.get("methodology") or "按评分、波动、回撤、流动性、主题和相关性约束生成。"),
        "data_as_of_time": summary.get("data_as_of_time"),
        "quote_time": summary.get("quote_time"),
        "daily_signal_date": summary.get("daily_signal_date"),
        "portfolio_generated_at": summary.get("portfolio_generated_at") or snapshot.created_at,
        "weight_fill_reason": summary.get("weight_fill_reason"),
        "allocation_contract": allocation_contract,
        "evidence_status": evidence_status,
        "evidence_summary": build_evidence_summary(
            current_contract=allocation_contract or None,
            validation_evidence=None if allocation_contract else {"sample_count": 0},
            caveats=["组合证据只说明当前权重口径是否有同源历史验证，不构成买卖指令。"],
        ),
        "source_ranking_snapshot": snapshot_metadata(source_run),
    }


async def persist_observation_portfolio_snapshot(
    session: AsyncSession,
    portfolio: dict[str, Any],
    *,
    source_signal_run_id: int | None,
    validation_run_id: int | None,
) -> EtfObservationPortfolioSnapshot:
    allocation_contract = _allocation_contract_from_portfolio(
        portfolio,
        source_signal_run_id=source_signal_run_id,
    )
    summary = {
        "cash_weight": portfolio.get("cash_weight", 0.0),
        "target_invested_weight": portfolio.get("target_invested_weight", _PORTFOLIO_TOTAL_EXPOSURE_CAP),
        "weight_sum": portfolio.get("weight_sum", 0.0),
        "portfolio_mode": portfolio.get("portfolio_mode", PORTFOLIO_MODE_RISK_ON),
        "market_regime": portfolio.get("market_regime", MARKET_REGIME_RISK_ON),
        "primary_weight": portfolio.get("primary_weight", 0.0),
        "satellite_weight": portfolio.get("satellite_weight", 0.0),
        "risk_exposure_weight": portfolio.get("risk_exposure_weight", 0.0),
        "defensive_weight": portfolio.get("defensive_weight", 0.0),
        "cash_reason": portfolio.get("cash_reason"),
        "single_weight_cap": portfolio.get("single_weight_cap"),
        "total_exposure_cap": portfolio.get("total_exposure_cap"),
        "constraint_summary": portfolio.get("constraint_summary", {}),
        "constraints_used": portfolio.get("constraints_used", {}),
        "risk_summary": portfolio.get("risk_summary", {}),
        "data_reliability_summary": portfolio.get("data_reliability_summary", {}),
        "unavailable_reason": portfolio.get("unavailable_reason"),
        "note": portfolio.get("note", ""),
        "methodology": portfolio.get("methodology", ""),
        "data_as_of_time": _json_time(portfolio.get("data_as_of_time")),
        "quote_time": _json_time(portfolio.get("quote_time")),
        "daily_signal_date": _json_time(portfolio.get("daily_signal_date")),
        "portfolio_generated_at": _json_time(portfolio.get("portfolio_generated_at")),
        "weight_fill_reason": portfolio.get("weight_fill_reason"),
        "allocation_contract": allocation_contract,
    }
    snapshot = EtfObservationPortfolioSnapshot(
        status=RUN_STATUS_SUCCESS,
        source_signal_run_id=source_signal_run_id,
        validation_run_id=validation_run_id,
        as_of_date=portfolio.get("as_of_date") or date.today(),
        asset_type=ASSET_TYPE_ETF,
        config_json={
            "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
            "satellite_single_weight_cap": _PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
            "satellite_exposure_cap": _PORTFOLIO_SATELLITE_EXPOSURE_CAP,
            "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
            "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
            "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
        },
        summary_json=summary,
    )
    session.add(snapshot)
    await session.flush()
    summary["allocation_contract"] = _allocation_contract_from_portfolio(
        portfolio,
        portfolio_run_id=snapshot.id,
        source_signal_run_id=source_signal_run_id,
    )
    snapshot.summary_json = summary
    for item_type, key in (
        ("primary", "items"),
        ("satellite", "satellite_items"),
        ("defensive", "defensive_items"),
        ("watch_only", "watch_only_items"),
        ("excluded", "excluded_items"),
    ):
        for index, item in enumerate(portfolio.get(key, []), start=1):
            risk_reasons = list(item.get("risk_reasons") or [])
            session.add(
                EtfObservationPortfolioItem(
                    snapshot_id=snapshot.id,
                    item_type=item_type,
                    rank_order=index,
                    asset_code=str(item.get("code") or ""),
                    asset_name=str(item.get("name") or ""),
                    target_weight=float(item.get("target_weight") or 0.0),
                    score=float(item.get("score") or 0.0),
                    conclusion=str(item.get("conclusion") or ""),
                    entry_timing_label=item.get("entry_timing_label"),
                    data_date=item.get("data_date"),
                    evidence_json=list(item.get("evidence") or []),
                    risk_reasons_json=risk_reasons,
                    exclusion_reason=(risk_reasons[0] if item_type != "primary" and risk_reasons else None),
                    metrics_json=dict(item.get("metrics") or {}),
                )
            )
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def run_etf_observation_portfolio_optimization(
    session: AsyncSession,
    *,
    limit: int = 5,
) -> EtfObservationPortfolioSnapshot:
    signal_run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    validation_run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_FORWARD_LIVE)
    portfolio = await etf_observation_portfolio(
        session,
        limit=limit,
        universe=UNIVERSE_DEFAULT,
        use_snapshot=False,
        include_optimized=False,
        source_run=signal_run,
    )
    return await persist_observation_portfolio_snapshot(
        session,
        portfolio,
        source_signal_run_id=signal_run.id if signal_run else None,
        validation_run_id=validation_run.id if validation_run else None,
    )


async def _attach_optimized_allocation(session: AsyncSession, portfolio: dict[str, Any]) -> dict[str, Any]:
    snapshot = await latest_optimized_allocation_snapshot(session)
    portfolio["optimized_allocation"] = await optimized_allocation_payload(session, snapshot)
    return portfolio


async def etf_observation_portfolio(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
    limit: int = 5,
    universe: str = UNIVERSE_DEFAULT,
    use_snapshot: bool = True,
    include_optimized: bool = True,
    source_run: ShortResearchSignalRun | None = None,
) -> dict[str, Any]:
    if use_snapshot and source_run is None and universe == UNIVERSE_DEFAULT:
        snapshot = await latest_observation_portfolio_snapshot(session)
        if snapshot is not None and _observation_snapshot_is_usable(snapshot):
            portfolio = await observation_portfolio_from_snapshot(session, snapshot)
            return await _attach_optimized_allocation(session, portfolio) if include_optimized else portfolio
    run = source_run or await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    if run is None:
        portfolio = {
            "as_of_date": as_of_date or await latest_data_date(session) or date.today(),
            "asset_type": ASSET_TYPE_ETF,
            "items": [],
            "satellite_items": [],
            "defensive_items": [],
            "watch_only_items": [],
            "excluded_items": [],
            "cash_weight": 1.0,
            "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
            "weight_sum": 0.0,
            "portfolio_mode": PORTFOLIO_MODE_CASH_WAIT,
            "market_regime": MARKET_REGIME_CASH_WAIT,
            "risk_exposure_weight": 0.0,
            "primary_weight": 0.0,
            "satellite_weight": 0.0,
            "defensive_weight": 0.0,
            "cash_reason": "暂无 ETF 排序快照，先生成短线排序后再查看资金配置。",
            "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
            "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
            "constraint_summary": {
                "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
                "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
                "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
                "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
            },
            "constraints_used": {
                "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
                "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
                "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
                "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
            },
            "risk_summary": {},
            "data_reliability_summary": {},
            "unavailable_reason": "暂无 ETF 排序快照，先生成短线排序后再查看观察组合。",
            "research_only": True,
            "no_trade_instruction": True,
            "note": "暂无 ETF 排序快照，先生成短线排序后再查看观察组合。",
            "methodology": "按最新 ETF 短线排序生成研究参考；当前没有可用排序快照。",
            "allocation_contract": {},
            "evidence_status": EVIDENCE_STATUS_WAITING,
            "evidence_summary": build_evidence_summary(
                current_contract=None,
                caveats=["没有 ETF 排序快照，因此没有可验证的组合证据。"],
            ),
            "source_ranking_snapshot": snapshot_metadata(None),
        }
        return await _attach_optimized_allocation(session, portfolio) if include_optimized else portfolio
    assets, _total = await cached_signal_assets(
        session,
        run,
        asset_type=ASSET_TYPE_ETF,
        sort="score",
        universe=universe,
        limit=max(50, min(limit * 10, 200)),
    )
    primary_candidates: list[ComputedAsset] = []
    satellite_candidates: list[tuple[ComputedAsset, str | None]] = []
    defensive_candidates: list[tuple[ComputedAsset, str | None]] = []
    watch_only_candidates: list[tuple[ComputedAsset, str | None]] = []
    watch_only_items: list[dict[str, Any]] = []
    excluded_items: list[dict[str, Any]] = []
    for asset in assets:
        group, reason = _portfolio_candidate_group(asset)
        if group == PORTFOLIO_LAYER_PRIMARY:
            primary_candidates.append(asset)
            continue
        if group == PORTFOLIO_LAYER_SATELLITE:
            satellite_candidates.append((asset, reason))
            continue
        defensive_reason = _portfolio_defensive_reason(asset, reason)
        if defensive_reason is not None:
            defensive_candidates.append((asset, defensive_reason))
        elif group == PORTFOLIO_LAYER_WATCH_ONLY:
            watch_only_candidates.append((asset, reason))
        else:
            excluded_items.append(_portfolio_item(asset, target_weight=0.0, reason=reason))

    return_maps = await _portfolio_return_maps(
        session,
        [
            *primary_candidates,
            *[asset for asset, _reason in satellite_candidates],
            *[asset for asset, _reason in defensive_candidates],
            *[asset for asset, _reason in watch_only_candidates],
        ],
        run.as_of_date,
    )
    selected_assets: list[ComputedAsset] = []
    selected_return_maps: dict[str, dict[date, float]] = {}
    selected_fill_reasons: dict[str, str] = {}
    selected_item_types: dict[str, str] = {}
    theme_counts: dict[str, int] = {}
    selected_limit = max(4, min(limit, 10))
    for asset in primary_candidates:
        if len(selected_assets) >= selected_limit:
            break
        selection_reason: str | None = None
        asset_themes = _portfolio_theme_keys(asset) or ["ETF"]
        if any(theme_counts.get(theme, 0) >= 2 for theme in asset_themes):
            selection_reason = "同主题 ETF 已有足够候选，按主题上限转入观察。"
        asset_returns = return_maps.get(asset.metadata.code)
        if selection_reason is None and asset_returns:
            for selected_code, selected_returns in selected_return_maps.items():
                correlation = _correlation(asset_returns, selected_returns)
                if correlation is not None and correlation >= _PORTFOLIO_HIGH_CORRELATION:
                    selection_reason = f"与已选 ETF {selected_code} 近60日相关性约 {correlation:.2f}，为避免重复押注转入观察。"
                    break
        if selection_reason is not None:
            watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=selection_reason))
            continue
        selected_assets.append(asset)
        selected_item_types[asset.metadata.code] = PORTFOLIO_LAYER_PRIMARY
        for theme in asset_themes:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        if asset_returns:
            selected_return_maps[asset.metadata.code] = asset_returns

    satellite_selected = 0
    satellite_limit = max(1, int(_PORTFOLIO_SATELLITE_EXPOSURE_CAP / _PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP))
    for asset, original_reason in satellite_candidates:
        if len(selected_assets) >= selected_limit or satellite_selected >= satellite_limit:
            watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=original_reason))
            continue
        selection_reason = None
        asset_themes = _portfolio_theme_keys(asset) or ["ETF"]
        if any(theme_counts.get(theme, 0) >= 2 for theme in asset_themes):
            selection_reason = "同主题 ETF 已有足够候选，高位观察资产转入观察组。"
        asset_returns = return_maps.get(asset.metadata.code)
        if selection_reason is None and asset_returns:
            for selected_code, selected_returns in selected_return_maps.items():
                correlation = _correlation(asset_returns, selected_returns)
                if correlation is not None and correlation >= _PORTFOLIO_HIGH_CORRELATION:
                    selection_reason = f"与已选 ETF {selected_code} 近60日相关性约 {correlation:.2f}，高位观察资产转入观察组。"
                    break
        if selection_reason is not None:
            watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=selection_reason))
            continue
        selected_assets.append(asset)
        selected_fill_reasons[asset.metadata.code] = original_reason or "高位观察但买点仍健康，仅小仓观察。"
        selected_item_types[asset.metadata.code] = PORTFOLIO_LAYER_SATELLITE
        satellite_selected += 1
        for theme in asset_themes:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        if asset_returns:
            selected_return_maps[asset.metadata.code] = asset_returns

    for asset, original_reason in sorted(
        defensive_candidates,
        key=lambda row: (_portfolio_defensive_priority(row[0]), -float(row[0].total_score or 0.0)),
    ):
        if len(selected_assets) >= 4:
            watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=original_reason))
            continue
        fill_reason = _portfolio_defensive_reason(asset, original_reason)
        if fill_reason is None:
            watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=original_reason))
            continue
        selection_reason = None
        asset_themes = _portfolio_theme_keys(asset) or ["ETF"]
        if any(theme_counts.get(theme, 0) >= 2 for theme in asset_themes):
            selection_reason = "补位候选同主题已有足够权重，继续留在观察组。"
        asset_returns = return_maps.get(asset.metadata.code)
        if selection_reason is None and asset_returns:
            for selected_code, selected_returns in selected_return_maps.items():
                correlation = _correlation(asset_returns, selected_returns)
                if correlation is not None and correlation >= _PORTFOLIO_HIGH_CORRELATION:
                    selection_reason = f"补位候选与已选 ETF {selected_code} 近60日相关性约 {correlation:.2f}，继续留在观察组。"
                    break
        if selection_reason is not None:
            watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=selection_reason))
            continue
        selected_assets.append(asset)
        selected_fill_reasons[asset.metadata.code] = fill_reason
        selected_item_types[asset.metadata.code] = PORTFOLIO_LAYER_DEFENSIVE
        for theme in asset_themes:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        if asset_returns:
            selected_return_maps[asset.metadata.code] = asset_returns

    for asset, original_reason in watch_only_candidates:
        watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=original_reason))

    raw_weight_rows = [_portfolio_raw_weight(asset) for asset in selected_assets]
    layer_caps = [
        _portfolio_layer_cap(selected_item_types.get(asset.metadata.code, PORTFOLIO_LAYER_PRIMARY))
        for asset in selected_assets
    ]
    target_exposure = min(
        _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        sum(layer_caps),
    )
    normalized_weights = _cap_normalized_weights_by_caps(
        [row[0] for row in raw_weight_rows],
        layer_caps,
        target_total=target_exposure,
    )
    items: list[dict[str, Any]] = []
    satellite_items: list[dict[str, Any]] = []
    defensive_items: list[dict[str, Any]] = []
    unavailable_reason: str | None = None
    cash_reason: str | None = None
    if not selected_assets:
        unavailable_reason = "当前没有满足数据可靠性、流动性和风险约束的进攻/防守 ETF。"
        cash_reason = "当前没有足够满足条件的 ETF，建议等待，不硬凑标的。"
    elif normalized_weights is None:
        unavailable_reason = "组合约束不可行：单只 30% 上限和候选数量无法形成有效仓位。"
        cash_reason = "组合约束不可行，建议等待下一次数据更新。"
    else:
        for asset, target, raw_row in zip(selected_assets, normalized_weights, raw_weight_rows, strict=True):
            weight_reason = dict(raw_row[1])
            item_type = selected_item_types.get(asset.metadata.code, "primary")
            weight_reason.update(
                {
                    "final_weight": round(target, 4),
                    "single_cap_applied": target >= _portfolio_layer_cap(item_type) - 0.0001,
                    "single_weight_cap": _portfolio_layer_cap(item_type),
                    "satellite_exposure_cap": _PORTFOLIO_SATELLITE_EXPOSURE_CAP,
                    "theme_tags": _portfolio_theme_keys(asset) or list(asset.metadata.theme_tags[:2]),
                    "correlation_policy": "高相关候选转入观察组，主组合只保留分散后的候选。",
                    "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
                    "portfolio_target_weight": round(target_exposure, 4),
                    "cash_wait_weight": round(max(0.0, _PORTFOLIO_TOTAL_EXPOSURE_CAP - target_exposure), 4),
                    "portfolio_item_type": item_type,
                }
            )
            fill_reason = selected_fill_reasons.get(asset.metadata.code)
            if fill_reason:
                weight_reason["weight_fill_reason"] = fill_reason
            portfolio_item = _portfolio_item(asset, target_weight=target, weight_reason=weight_reason)
            if item_type == PORTFOLIO_LAYER_DEFENSIVE:
                defensive_items.append(portfolio_item)
            elif item_type == PORTFOLIO_LAYER_SATELLITE:
                satellite_items.append(portfolio_item)
            else:
                items.append(portfolio_item)

    weighted_items = [*items, *satellite_items, *defensive_items]
    weight_sum = round(sum(float(item["target_weight"]) for item in weighted_items), 4)
    rounding_residual = round(max(0.0, 1.0 - weight_sum), 4) if weighted_items else 0.0
    if defensive_items and not items and not satellite_items:
        portfolio_mode = PORTFOLIO_MODE_DEFENSIVE
        market_regime = MARKET_REGIME_DEFENSIVE
    elif satellite_items or defensive_items:
        portfolio_mode = PORTFOLIO_MODE_NEUTRAL
        market_regime = MARKET_REGIME_NEUTRAL
    elif items:
        portfolio_mode = PORTFOLIO_MODE_RISK_ON
        market_regime = MARKET_REGIME_RISK_ON
    else:
        portfolio_mode = PORTFOLIO_MODE_CASH_WAIT
        market_regime = MARKET_REGIME_CASH_WAIT
        rounding_residual = 1.0
        cash_reason = cash_reason or "当前没有可用于配置的 ETF，建议等待。"
    if rounding_residual > 0.0001 and portfolio_mode != PORTFOLIO_MODE_CASH_WAIT:
        cash_reason = cash_reason or "满足条件的 ETF 不足以用满账户资金；剩余资金等待，不硬凑标的。"
    primary_weight = round(sum(float(item["target_weight"]) for item in items), 4)
    satellite_weight = round(sum(float(item["target_weight"]) for item in satellite_items), 4)
    risk_exposure_weight = round(primary_weight + satellite_weight, 4)
    defensive_weight = round(sum(float(item["target_weight"]) for item in defensive_items), 4)
    constraints_used = {
        "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
        "satellite_single_weight_cap": _PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
        "satellite_exposure_cap": _PORTFOLIO_SATELLITE_EXPOSURE_CAP,
        "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
        "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
        "minimum_full_exposure_holdings": 4,
        "partial_allocation_allowed": True,
        "weight_model": "score_tilted_inverse_volatility",
    }
    risk_summary = {
        "primary_count": len(items),
        "satellite_count": len(satellite_items),
        "defensive_count": len(defensive_items),
        "watch_only_count": len(watch_only_items),
        "excluded_count": len(excluded_items),
        "high_volatility_count": sum(1 for item in weighted_items if "高波动" in item.get("risk_reasons", [])),
        "max_single_weight": max((float(item["target_weight"]) for item in weighted_items), default=0.0),
    }
    data_reliability_summary = {
        "eligible_candidates": len(primary_candidates),
        "eligible_satellite_candidates": len(satellite_candidates),
        "selected_candidates": len(items),
        "selected_satellite_candidates": len(satellite_items),
        "selected_defensive_candidates": len(defensive_items),
        "watch_only_candidates": len(watch_only_items),
        "excluded_candidates": len(excluded_items),
        "weightable_candidates": len(selected_assets),
        "decision_reliability": "verified_or_alternate_provider",
    }
    note = "ETF 资金配置按你放进证券账户 ETF 的资金最多 100% 做研究参考，不代表必须时时满仓。"
    if unavailable_reason:
        note = f"当前不建议动用 ETF 资金：{unavailable_reason}"
        items = []
        satellite_items = []
        defensive_items = []
        weight_sum = 0.0
        primary_weight = 0.0
        risk_exposure_weight = 0.0
        satellite_weight = 0.0
        defensive_weight = 0.0
        rounding_residual = 1.0
    elif portfolio_mode == PORTFOLIO_MODE_DEFENSIVE:
        note = "当前市场不适合全仓进攻，优先用防守 ETF 做资金配置参考；未用满的资金继续等待。"
    elif portfolio_mode == PORTFOLIO_MODE_NEUTRAL:
        note = "当前主配置不足，采用主配置、小仓观察和防守资产混合；小仓观察不是强买信号。"
    elif watch_only_items or excluded_items:
        note = "高位/追高/数据不足资产会分到观察或等待分组，不进入主组合权重。" + note
    elif rounding_residual > 0.0001:
        note = "当前只给满足条件 ETF 分配部分仓位；剩余资金等待，不硬凑标的。"
    quote_time = await session.scalar(select(func.max(EtfIntradayLatestQuote.quote_time)))
    portfolio_generated_at = utcnow()
    data_as_of_time = quote_time or portfolio_generated_at
    portfolio = {
        "as_of_date": as_of_date or run.as_of_date,
        "data_as_of_time": data_as_of_time,
        "quote_time": quote_time,
        "daily_signal_date": run.as_of_date,
        "portfolio_generated_at": portfolio_generated_at,
        "asset_type": ASSET_TYPE_ETF,
        "items": items,
        "satellite_items": satellite_items,
        "defensive_items": defensive_items,
        "watch_only_items": watch_only_items[:10],
        "excluded_items": excluded_items[:10],
        "cash_weight": rounding_residual,
        "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "weight_sum": weight_sum,
        "portfolio_mode": portfolio_mode,
        "market_regime": market_regime,
        "risk_exposure_weight": risk_exposure_weight,
        "primary_weight": primary_weight,
        "satellite_weight": satellite_weight,
        "defensive_weight": defensive_weight,
        "cash_reason": cash_reason,
        "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
        "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "constraint_summary": {
            **constraints_used,
            "primary_count": len(items),
            "satellite_count": len(satellite_items),
            "defensive_count": len(defensive_items),
            "watch_only_count": len(watch_only_items),
            "excluded_count": len(excluded_items),
        },
        "constraints_used": constraints_used,
        "risk_summary": {
            **risk_summary,
            "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
            "satellite_single_weight_cap": _PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
            "satellite_exposure_cap": _PORTFOLIO_SATELLITE_EXPOSURE_CAP,
            "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        },
        "data_reliability_summary": {
            **data_reliability_summary,
            "item_count": len(items),
            "satellite_item_count": len(satellite_items),
            "defensive_item_count": len(defensive_items),
            "watch_only_item_count": len(watch_only_items[:10]),
            "excluded_item_count": len(excluded_items[:10]),
        },
        "unavailable_reason": unavailable_reason,
        "research_only": True,
        "no_trade_instruction": True,
        "note": note,
        "methodology": (
            "先生成进攻候选；进攻不足时再用货币、债券、黄金、红利或低波动宽基 ETF 做防守候选；"
            "若候选不足以用满资金，则部分配置、剩余现金等待；进攻和防守都不足时才全现金等待。这是研究权重，不是交易指令。"
        ),
    }
    allocation_contract = _allocation_contract_from_portfolio(portfolio, source_signal_run_id=run.id)
    portfolio["allocation_contract"] = allocation_contract
    portfolio["evidence_status"] = EVIDENCE_STATUS_WAITING
    portfolio["evidence_summary"] = build_evidence_summary(
        current_contract=allocation_contract,
        caveats=["当前组合权重仍在等待同源历史回放验证。"],
    )
    portfolio["source_ranking_snapshot"] = snapshot_metadata(run)
    return await _attach_optimized_allocation(session, portfolio) if include_optimized else portfolio
