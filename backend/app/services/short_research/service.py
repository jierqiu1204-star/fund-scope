from __future__ import annotations

import os
from dataclasses import dataclass, replace
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
    EtfThemeExposure,
    Fund,
    FundNavHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TradableEtf,
    utcnow,
)
from app.services.jobs import sync_fund_nav_history
from app.services.portfolio_allocation import (
    PORTFOLIO_CORRELATION_MIN_POINTS,
    PORTFOLIO_ENTRY_TIMING_FORBIDDEN,
    PORTFOLIO_ENTRY_TIMING_OK,
    PORTFOLIO_HIGH_CORRELATION,
    PORTFOLIO_MODE_CASH_WAIT,
    PORTFOLIO_MODE_DEFENSIVE,
    PORTFOLIO_MODE_RISK_ON,
    PORTFOLIO_RISK_FLAGS_FORBIDDEN,
    PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT,
    PORTFOLIO_RISK_FLAGS_WATCH_ONLY,
    PORTFOLIO_SINGLE_WEIGHT_CAP,
    PORTFOLIO_THEME_EXPOSURE_CAP,
    PORTFOLIO_TOTAL_EXPOSURE_CAP,
)
from app.services.short_etf.data import sync_etf_price_history
from app.services.short_research.dynamic_thresholds import (
    ThresholdPricePoint,
    dynamic_threshold_context,
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
UNIVERSE_DEFAULT = "default"
UNIVERSE_ALL = "all"
UNIVERSE_ILLIQUID = "illiquid"
CHASE_RETURN_20D = 0.25
CHASE_RETURN_60D = 0.45
SURGE_RETURN_5D = 0.08
HIGH_DAILY_VOLATILITY_20D = 0.035
LARGE_DRAWDOWN_60D = -0.18
_PORTFOLIO_SINGLE_WEIGHT_CAP = PORTFOLIO_SINGLE_WEIGHT_CAP
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
            .limit(50)
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
        score_breakdown=dict(item.score_breakdown_json or {}),
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
    return [PricePoint(point_date=row.nav_date, value=row.nav, nav=row.nav) for row in rows.all() if row.nav > 0]


async def _etf_series(session: AsyncSession, code: str, as_of_date: date | None = None) -> list[PricePoint]:
    query = select(EtfPriceHistory).where(EtfPriceHistory.etf_code == code)
    if as_of_date is not None:
        query = query.where(EtfPriceHistory.trade_date <= as_of_date)
    rows = await session.scalars(query.order_by(EtfPriceHistory.trade_date.asc()))
    return [
        PricePoint(
            point_date=row.trade_date,
            value=row.close,
            close=row.close,
            turnover=row.turnover,
            pct_change=row.pct_change / 100,
        )
        for row in rows.all()
        if row.close > 0
    ]


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
    last_20 = series[-20:]
    last_60 = series[-60:]
    returns_20 = _daily_returns(last_20)
    return_5d = _window_return(series, 5)
    return_10d = _window_return(series, 10)
    return_20d = _window_return(series, 20)
    return_60d = _window_return(series, 60)
    volatility_20d = pstdev(returns_20) if len(returns_20) > 1 else None
    max_drawdown_60d = _max_drawdown(last_60)
    average_turnover_20d = None
    if metadata.asset_type == ASSET_TYPE_ETF:
        turnovers = [item.turnover for item in last_20 if item.turnover is not None]
        average_turnover_20d = mean(turnovers) if turnovers else None
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
    if usable_days < 20:
        total_score = min(total_score, 35.0)
    elif "数据滞后" in risk_flags:
        total_score = min(total_score, 55.0)
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
        "theme_profile": theme_profile.as_dict(),
        "dynamic_threshold_context": dynamic_context,
        "trend_score": trend_score,
        "risk_score": risk_score,
        "liquidity_score": liquidity_score,
        "total_score": total_score,
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


_LABEL_VALIDATION_WINDOWS = (1, 3, 5, 10)
_LABEL_VALIDATION_MAX_ASSETS = 120
_LABEL_VALIDATION_MIN_SAMPLES = 10
_LABEL_VALIDATION_RULE_VERSION = "label_validation_v1"
VALIDATION_MODE_FORWARD_LIVE = "forward_live"
VALIDATION_MODE_HISTORICAL_REPLAY = "historical_replay"
_LABEL_REPLAY_DEFAULT_DAYS = 180
_LABEL_REPLAY_MIN_SAMPLES = 30
_LABEL_REPLAY_SUFFICIENT_SAMPLES = 100
_LABEL_REPLAY_MAX_ASSETS = 300


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


def _price_points_from_rows(rows: list[EtfPriceHistory]) -> list[PricePoint]:
    return [
        PricePoint(
            point_date=row.trade_date,
            value=row.close,
            close=row.close,
            turnover=row.turnover,
            pct_change=row.pct_change / 100,
        )
        for row in rows
        if row.close > 0
    ]


def _completed_outcome_payload(
    rows: list[EtfPriceHistory],
    horizon_days: int,
) -> tuple[str, dict[str, Any]]:
    if len(rows) <= horizon_days:
        return "pending", {"exclusion_reason": "missing_future_price"}
    start = rows[0]
    end_row = rows[horizon_days]
    signal_price = float(start.close or 0.0)
    future_price = float(end_row.close or 0.0)
    if signal_price <= 0 or future_price <= 0:
        return "excluded", {"exclusion_reason": "invalid_price"}
    path_returns = [float(row.close or 0.0) / signal_price - 1.0 for row in rows[1 : horizon_days + 1] if row.close]
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


async def _label_outcome_summary(session: AsyncSession, as_of_date: date) -> dict[str, Any]:
    rows = list(
        (
            await session.scalars(
                select(EtfLabelOutcome)
                .where(EtfLabelOutcome.asset_type == ASSET_TYPE_ETF)
                .order_by(EtfLabelOutcome.signal_date.desc(), EtfLabelOutcome.id.desc())
            )
        ).all()
    )
    grouped: dict[tuple[str, str], dict[int, list[EtfLabelOutcome]]] = {}
    for row in rows:
        grouped.setdefault((row.label, row.entry_timing_label), {}).setdefault(row.horizon_days, []).append(row)
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


def _summarize_replay_bucket(
    rows: list[EtfLabelReplaySample],
    total_rows: int,
) -> dict[str, Any]:
    completed_rows = [item for item in rows if item.status == "completed" and item.forward_return is not None]
    excluded_count = sum(1 for item in rows if item.status == "excluded")
    if not completed_rows:
        return {
            "sample_count": 0,
            "excluded_count": excluded_count,
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
    returns = [float(item.forward_return or 0.0) for item in completed_rows]
    drawdowns = [float(item.adverse_drawdown or 0.0) for item in completed_rows]
    excursions = [float(item.favorable_excursion or 0.0) for item in completed_rows]
    latest_rows = sorted(completed_rows, key=lambda item: item.replay_date, reverse=True)
    recent_returns = [float(item.forward_return or 0.0) for item in latest_rows[: min(30, len(latest_rows))]]
    all_median = median(returns)
    recent_median = median(recent_returns) if recent_returns else None
    coverage = len(completed_rows) / total_rows if total_rows else 0.0
    confidence = _replay_validation_confidence(
        len(completed_rows),
        coverage=coverage,
        recent_median=recent_median,
        all_median=all_median,
    )
    exclusion_reasons: dict[str, int] = {}
    for item in rows:
        if item.exclusion_reason:
            exclusion_reasons[item.exclusion_reason] = exclusion_reasons.get(item.exclusion_reason, 0) + 1
    return {
        "sample_count": len(completed_rows),
        "excluded_count": excluded_count,
        "pending_count": 0,
        "coverage": round(coverage, 4),
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
        "validation_mode": VALIDATION_MODE_HISTORICAL_REPLAY,
        "price_source": "verified_daily_close",
    }


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
    entry_price = float(replay_row.close or 0.0) if replay_row.close else None
    forward_return: float | None = None
    adverse_drawdown: float | None = None
    favorable_excursion: float | None = None
    horizon_end_date: date | None = None
    if status == "completed" and entry_price and entry_price > 0:
        end_row = future_rows[-1]
        horizon_end_date = end_row.trade_date
        path_returns = [float(row.close or 0.0) / entry_price - 1.0 for row in future_rows if row.close and row.close > 0]
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
    max_assets: int = _LABEL_REPLAY_MAX_ASSETS,
) -> EtfSignalValidationRun:
    started_at = utcnow()
    horizons = list(_LABEL_VALIDATION_WINDOWS)
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
            "price_source": "verified_daily_close",
            "research_only": True,
        },
        summary_json={},
    )
    session.add(run)
    await session.flush()

    etfs = list(
        (
            await session.scalars(
                select(TradableEtf)
                .where(TradableEtf.is_short_term_eligible.is_(True))
                .order_by(TradableEtf.code.asc())
                .limit(max_assets)
            )
        ).all()
    )
    buckets: dict[tuple[str, str, int], list[EtfLabelReplaySample]] = {}
    completed_samples = 0
    excluded_samples = 0
    evaluated_assets = 0
    replay_start: date | None = None
    replay_end: date | None = None
    exclusion_reasons: dict[str, int] = {}

    for etf in etfs:
        rows = await _etf_price_rows_until(
            session,
            etf.code,
            from_date=date.today() - timedelta(days=max(days * 2 + 180, 540)),
        )
        rows = [row for row in rows if row.close and row.close > 0]
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
                buckets.setdefault((sample.label, sample.entry_timing_label, horizon), []).append(sample)
                session.add(sample)

    groups: list[dict[str, Any]] = []
    by_label: dict[tuple[str, str], dict[int, list[EtfLabelReplaySample]]] = {}
    for (label, entry_label, horizon), samples in buckets.items():
        by_label.setdefault((label, entry_label), {})[horizon] = samples
    for (label, entry_label), windows in sorted(by_label.items()):
        groups.append(
            {
                "label": label,
                "entry_timing_label": entry_label,
                "key": f"{label} / {entry_label}",
                "windows": {
                    str(window): _summarize_replay_bucket(windows.get(window, []), len(windows.get(window, [])))
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
                "rule_version": run.rule_version,
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


async def latest_forward_validation_evidence_by_label(session: AsyncSession) -> dict[tuple[str, str], dict[str, Any]]:
    forward_run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_FORWARD_LIVE)
    examples_by_label = await _recent_outcome_examples(session) if forward_run is not None else {}
    return await _validation_evidence_for_run(session, forward_run, examples_by_label=examples_by_label)


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
    summary = await _label_outcome_summary(session, source_run.as_of_date)
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
    candidates = [
        item
        for item in await _available_assets(session, asset_type=asset_type, codes=codes)
        if _matches_filters(item, asset_type=asset_type, theme=theme, codes=codes)
    ]
    computed = [await compute_asset(session, item, as_of_date=as_of_date) for item in candidates]
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
) -> tuple[ComputedAsset, list[dict[str, Any]], dict[str, str]]:
    await ensure_short_research_universe(session)
    metadata = SHORT_RESEARCH_ASSET_BY_KEY.get((asset_type, code))
    if metadata is None:
        if asset_type == ASSET_TYPE_FUND:
            fund = await session.scalar(select(Fund).where(Fund.code == code))
            if fund is None:
                raise ValueError("未找到这只基金")
            metadata = _metadata(asset_type, code, fund.name)
        elif asset_type == ASSET_TYPE_ETF:
            etf = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
            if etf is None:
                raise ValueError("未找到这只 ETF")
            metadata = _metadata(asset_type, code, etf.name)
        else:
            raise ValueError("资产类型只支持 fund 或 etf")
    computed = await compute_asset(session, metadata, as_of_date=as_of_date)
    series = await _series_for_asset(session, metadata, as_of_date or await latest_data_date(session))
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
    sections = {
        "投资方向": metadata.investment_direction,
        "为什么上榜": str(computed.rationale["key_reason"]),
        "今日买点": computed.entry_timing_reason,
        "主要风险": str(computed.rationale["risk_explanation"]),
        "反方提醒": str(computed.rationale["opposing_view"]),
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
            },
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
    theme_coverage = await theme_coverage_summary(session)
    return {
        "latest_data_date": latest,
        "signal_date": latest_run.as_of_date if latest_run else None,
        "theme_coverage": theme_coverage,
        "label_validation": label_validation if isinstance(label_validation, dict) else {},
        "label_validation_generated_at": label_validation_generated_at,
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
    }


async def data_health(session: AsyncSession) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    today = date.today()
    for metadata in await _available_assets(session):
        series = await _series_for_asset(session, metadata)
        latest = series[-1].point_date if series else None
        provider = None
        error_message = None
        if metadata.asset_type == ASSET_TYPE_ETF:
            etf_health = await session.scalar(select(EtfDataHealth).where(EtfDataHealth.etf_code == metadata.code))
            if etf_health is not None:
                provider = etf_health.provider
                error_message = etf_health.last_error_message
        is_stale = latest is None or (today - latest).days > STALE_DATA_DAYS
        rows.append(
            {
                "asset_type": metadata.asset_type,
                "code": metadata.code,
                "name": metadata.name,
                "status": "missing" if latest is None else "success",
                "latest_date": latest,
                "usable_days": len(series),
                "provider": provider,
                "source_note": "公开 ETF 日线数据" if metadata.asset_type == ASSET_TYPE_ETF else "公开基金净值数据",
                "last_error_message": error_message,
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
    tracked_rows = await session.scalars(
        select(TrackedPosition.asset_code).where(
            TrackedPosition.asset_type == ASSET_TYPE_ETF,
            TrackedPosition.status == "active",
            TrackedPosition.asset_code.in_(codes),
        )
    )
    tracked = list(dict.fromkeys(str(code) for code in tracked_rows.all()))
    prioritized = [*priority, *(code for code in tracked if code not in priority)]
    remaining = sorted(code_set - set(prioritized) - set(watchlist))
    return [
        *prioritized,
        *(code for code in watchlist if code not in prioritized),
        *remaining,
    ]


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
    etf_codes = await _prioritize_etf_sync_codes(session, etf_codes, priority_etf_codes)
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
        all_batches = _chunks(etf_codes, _etf_sync_batch_size())
        max_batches = len(all_batches) if sync_all_etfs or codes else _etf_sync_max_batches()
        batches = all_batches[:max_batches]
        for batch in batches:
            batch_result = await sync_etf_price_history(session, from_date, to_date, batch)
            etf_result["etfs"] += _result_count(batch_result, "etfs")
            etf_result["inserted"] += _result_count(batch_result, "inserted")
            etf_result["updated"] += _result_count(batch_result, "updated")
            etf_result["failed"] += _result_count(batch_result, "failed")
            etf_result["failures"].extend(batch_result.get("failures", []))
        etf_result["batches"] = len(batches)
        etf_result["batches_total"] = len(all_batches)
        etf_result["processed"] = sum(len(batch) for batch in batches)
        etf_result["skipped"] = max(0, len(etf_codes) - etf_result["processed"])
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


def _asset_with_validation_evidence(
    asset: ComputedAsset,
    evidence_by_label: dict[tuple[str, str], dict[str, Any]],
) -> ComputedAsset:
    evidence = evidence_by_label.get((asset.conclusion, asset.entry_timing_label))
    if not evidence:
        return asset
    metrics = dict(asset.metrics)
    metrics["validation_confidence"] = evidence.get("confidence", "insufficient")
    metrics["validation_sample_count"] = evidence.get("sample_count", 0)
    metrics["validation_median_return"] = evidence.get("median_return")
    metrics["validation_win_rate"] = evidence.get("win_rate")
    return replace(asset, metrics=metrics)


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
    validation_confidence = asset.metrics.get("validation_confidence")
    if validation_confidence == "limited":
        exposure -= 0.03
    elif validation_confidence == "insufficient":
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
    if asset.conclusion == CONCLUSION_HIGH_WATCH:
        return "趋势强但处于高位观察，先放入强势但别追，不给组合权重。"
    if asset.entry_timing_label == ENTRY_TIMING_CHASE_RISK:
        return f"今日买点为{ENTRY_TIMING_CHASE_RISK}，适合继续盯，不分配主组合权重。"
    watch_flags = [flag for flag in _PORTFOLIO_RISK_FLAGS_WATCH_ONLY if flag in asset.risk_flags]
    if watch_flags:
        return f"存在{'、'.join(watch_flags)}，强势但不适合追入。"
    if asset.conclusion != CONCLUSION_WATCH:
        return f"观察标签非短线观察（当前：{asset.conclusion}）。"
    if asset.entry_timing_label not in _PORTFOLIO_ENTRY_TIMING_OK:
        return f"今日买点不满足主组合筛选（当前：{asset.entry_timing_label}）。"
    return None


def _portfolio_candidate_group(asset: ComputedAsset) -> tuple[str, str | None]:
    exclusion_reason = _portfolio_exclusion_reason(asset)
    if exclusion_reason is not None:
        return "excluded", exclusion_reason
    watch_reason = _portfolio_watch_only_reason(asset)
    if watch_reason is not None:
        return "watch_only", watch_reason
    return "primary", None


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
    return (
        f"给 {target_weight * 100:.0f}% 观察权重：综合分 {asset.total_score:.1f}，"
        f"标签验证 {confidence}（样本 {sample_count}），20日成交额约 {turnover:.2f} 亿元；"
        f"按分数倾斜、波动率、回撤、成交额、主题和相关性约束归一到 ETF 账户资金 100%{cap_note}。"
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


def _portfolio_raw_weight(asset: ComputedAsset) -> tuple[float, dict[str, Any]]:
    score_component = max(0.01, float(asset.total_score or 0.0) / 100.0)
    volatility = max(0.006, float(asset.metrics.get("volatility_20d") or 0.025))
    drawdown = abs(min(0.0, float(asset.metrics.get("max_drawdown_60d") or 0.0)))
    turnover = max(1.0, float(asset.metrics.get("average_turnover_20d") or 0.0))
    liquidity_component = min(1.25, max(0.75, (turnover / 100_000_000) ** 0.2))
    drawdown_adjustment = max(0.55, 1.0 - drawdown * 1.5)
    risk_adjusted = score_component * liquidity_component * drawdown_adjustment / volatility
    return risk_adjusted, {
        "score_component": round(score_component, 4),
        "volatility_unit": round(volatility, 6),
        "drawdown_adjustment": round(drawdown_adjustment, 4),
        "liquidity_component": round(liquidity_component, 4),
        "raw_weight_score": round(risk_adjusted, 6),
        "data_reliability": "verified_or_alternate_provider",
    }


def _cap_normalized_weights(raw_weights: list[float], cap: float) -> list[float] | None:
    if not raw_weights or cap * len(raw_weights) < 1.0:
        return None
    remaining_indices = set(range(len(raw_weights)))
    weights = [0.0 for _item in raw_weights]
    remaining_weight = 1.0
    while remaining_indices:
        raw_total = sum(raw_weights[index] for index in remaining_indices)
        if raw_total <= 0:
            equal = remaining_weight / len(remaining_indices)
            for index in list(remaining_indices):
                weights[index] = min(cap, equal)
            break
        capped_this_round: list[int] = []
        for index in remaining_indices:
            proposed = remaining_weight * raw_weights[index] / raw_total
            if proposed > cap:
                weights[index] = cap
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
    return [round(weight / total, 4) for weight in weights]

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


def _observation_snapshot_is_usable(snapshot: EtfObservationPortfolioSnapshot) -> bool:
    summary = dict(snapshot.summary_json or {})
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
    rows = (
        await session.scalars(
            select(EtfObservationPortfolioItem)
            .where(EtfObservationPortfolioItem.snapshot_id == snapshot.id)
            .order_by(EtfObservationPortfolioItem.item_type.asc(), EtfObservationPortfolioItem.rank_order.asc())
        )
    ).all()
    primary = [_snapshot_item_out(row) for row in rows if row.item_type == "primary"]
    defensive = [_snapshot_item_out(row) for row in rows if row.item_type == "defensive"]
    watch_only = [_snapshot_item_out(row) for row in rows if row.item_type == "watch_only"]
    excluded = [_snapshot_item_out(row) for row in rows if row.item_type == "excluded"]
    summary = dict(snapshot.summary_json or {})
    weight_sum = round(sum(float(item.get("target_weight") or 0.0) for item in [*primary, *defensive]), 4)
    return {
        "snapshot_id": snapshot.id,
        "generated_at": snapshot.created_at,
        "as_of_date": snapshot.as_of_date,
        "asset_type": snapshot.asset_type,
        "items": primary,
        "defensive_items": defensive,
        "watch_only_items": watch_only,
        "excluded_items": excluded,
        "cash_weight": float(summary.get("cash_weight", round(max(0.0, 1.0 - weight_sum), 4))),
        "target_invested_weight": float(summary.get("target_invested_weight", _PORTFOLIO_TOTAL_EXPOSURE_CAP)),
        "weight_sum": float(summary.get("weight_sum", weight_sum)),
        "portfolio_mode": summary.get("portfolio_mode", PORTFOLIO_MODE_RISK_ON),
        "market_regime": summary.get("market_regime", MARKET_REGIME_RISK_ON),
        "risk_exposure_weight": float(summary.get("risk_exposure_weight", round(sum(float(item.get("target_weight") or 0.0) for item in primary), 4))),
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
    }


async def persist_observation_portfolio_snapshot(
    session: AsyncSession,
    portfolio: dict[str, Any],
    *,
    source_signal_run_id: int | None,
    validation_run_id: int | None,
) -> EtfObservationPortfolioSnapshot:
    summary = {
        "cash_weight": portfolio.get("cash_weight", 0.0),
        "target_invested_weight": portfolio.get("target_invested_weight", _PORTFOLIO_TOTAL_EXPOSURE_CAP),
        "weight_sum": portfolio.get("weight_sum", 0.0),
        "portfolio_mode": portfolio.get("portfolio_mode", PORTFOLIO_MODE_RISK_ON),
        "market_regime": portfolio.get("market_regime", MARKET_REGIME_RISK_ON),
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
    }
    snapshot = EtfObservationPortfolioSnapshot(
        status=RUN_STATUS_SUCCESS,
        source_signal_run_id=source_signal_run_id,
        validation_run_id=validation_run_id,
        as_of_date=portfolio.get("as_of_date") or date.today(),
        asset_type=ASSET_TYPE_ETF,
        config_json={
            "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
            "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
            "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
            "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
        },
        summary_json=summary,
    )
    session.add(snapshot)
    await session.flush()
    for item_type, key in (
        ("primary", "items"),
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
    portfolio = await etf_observation_portfolio(session, limit=limit, universe=UNIVERSE_DEFAULT, use_snapshot=False)
    return await persist_observation_portfolio_snapshot(
        session,
        portfolio,
        source_signal_run_id=signal_run.id if signal_run else None,
        validation_run_id=validation_run.id if validation_run else None,
    )


async def etf_observation_portfolio(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
    limit: int = 5,
    universe: str = UNIVERSE_DEFAULT,
    use_snapshot: bool = True,
) -> dict[str, Any]:
    if use_snapshot and universe == UNIVERSE_DEFAULT:
        snapshot = await latest_observation_portfolio_snapshot(session)
        if snapshot is not None and _observation_snapshot_is_usable(snapshot):
            return await observation_portfolio_from_snapshot(session, snapshot)
    run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    if run is None:
        return {
            "as_of_date": as_of_date or await latest_data_date(session) or date.today(),
            "asset_type": ASSET_TYPE_ETF,
            "items": [],
            "defensive_items": [],
            "watch_only_items": [],
            "excluded_items": [],
            "cash_weight": 1.0,
            "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
            "weight_sum": 0.0,
            "portfolio_mode": PORTFOLIO_MODE_CASH_WAIT,
            "market_regime": MARKET_REGIME_CASH_WAIT,
            "risk_exposure_weight": 0.0,
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
        }
    assets, _total = await cached_signal_assets(
        session,
        run,
        asset_type=ASSET_TYPE_ETF,
        sort="score",
        universe=universe,
        limit=max(50, min(limit * 10, 200)),
    )
    validation_by_label = await latest_forward_validation_evidence_by_label(session)
    if validation_by_label:
        assets = [_asset_with_validation_evidence(asset, validation_by_label) for asset in assets]
    primary_candidates: list[ComputedAsset] = []
    defensive_candidates: list[tuple[ComputedAsset, str | None]] = []
    watch_only_candidates: list[tuple[ComputedAsset, str | None]] = []
    watch_only_items: list[dict[str, Any]] = []
    excluded_items: list[dict[str, Any]] = []
    for asset in assets:
        group, reason = _portfolio_candidate_group(asset)
        if group == "primary":
            primary_candidates.append(asset)
            continue
        defensive_reason = _portfolio_defensive_reason(asset, reason)
        if defensive_reason is not None:
            defensive_candidates.append((asset, defensive_reason))
        elif group == "watch_only":
            watch_only_candidates.append((asset, reason))
        else:
            excluded_items.append(_portfolio_item(asset, target_weight=0.0, reason=reason))

    return_maps = await _portfolio_return_maps(
        session,
        [
            *primary_candidates,
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
        selected_item_types[asset.metadata.code] = "primary"
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
        selected_item_types[asset.metadata.code] = "defensive"
        for theme in asset_themes:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        if asset_returns:
            selected_return_maps[asset.metadata.code] = asset_returns

    for asset, original_reason in watch_only_candidates:
        watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=original_reason))

    raw_weight_rows = [_portfolio_raw_weight(asset) for asset in selected_assets]
    normalized_weights = _cap_normalized_weights(
        [row[0] for row in raw_weight_rows],
        _PORTFOLIO_SINGLE_WEIGHT_CAP,
    )
    items: list[dict[str, Any]] = []
    defensive_items: list[dict[str, Any]] = []
    unavailable_reason: str | None = None
    cash_reason: str | None = None
    if len(selected_assets) < 4:
        unavailable_reason = "进攻和防守候选都少于 4 只；单只 30% 上限下不能凑满 ETF 资金 100%。"
        cash_reason = "当前没有足够满足数据可靠性、流动性和风险约束的进攻/防守 ETF，建议等待。"
    elif normalized_weights is None:
        unavailable_reason = "组合约束不可行：单只 30% 上限和候选数量不足以归一到 100%。"
        cash_reason = "组合约束不可行，建议等待下一次数据更新。"
    else:
        for asset, target, raw_row in zip(selected_assets, normalized_weights, raw_weight_rows, strict=True):
            weight_reason = dict(raw_row[1])
            item_type = selected_item_types.get(asset.metadata.code, "primary")
            weight_reason.update(
                {
                    "final_weight": round(target, 4),
                    "single_cap_applied": target >= _PORTFOLIO_SINGLE_WEIGHT_CAP - 0.0001,
                    "theme_tags": _portfolio_theme_keys(asset) or list(asset.metadata.theme_tags[:2]),
                    "correlation_policy": "高相关候选转入观察组，主组合只保留分散后的候选。",
                    "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
                    "portfolio_item_type": item_type,
                }
            )
            fill_reason = selected_fill_reasons.get(asset.metadata.code)
            if fill_reason:
                weight_reason["weight_fill_reason"] = fill_reason
            portfolio_item = _portfolio_item(asset, target_weight=target, weight_reason=weight_reason)
            if item_type == "defensive":
                defensive_items.append(portfolio_item)
            else:
                items.append(portfolio_item)

    weight_sum = round(sum(float(item["target_weight"]) for item in [*items, *defensive_items]), 4)
    rounding_residual = 0.0 if items and abs(1.0 - weight_sum) <= 0.01 else (round(max(0.0, 1.0 - weight_sum), 4) if items else 0.0)
    if defensive_items:
        portfolio_mode = PORTFOLIO_MODE_DEFENSIVE
        market_regime = MARKET_REGIME_DEFENSIVE
    elif items:
        portfolio_mode = PORTFOLIO_MODE_RISK_ON
        market_regime = MARKET_REGIME_RISK_ON
    else:
        portfolio_mode = PORTFOLIO_MODE_CASH_WAIT
        market_regime = MARKET_REGIME_CASH_WAIT
        rounding_residual = 1.0
        cash_reason = cash_reason or "当前没有可用于配置的 ETF，建议等待。"
    risk_exposure_weight = round(sum(float(item["target_weight"]) for item in items), 4)
    defensive_weight = round(sum(float(item["target_weight"]) for item in defensive_items), 4)
    constraints_used = {
        "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
        "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
        "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
        "minimum_primary_count": 4,
        "weight_model": "score_tilted_inverse_volatility",
    }
    risk_summary = {
        "primary_count": len(items),
        "defensive_count": len(defensive_items),
        "watch_only_count": len(watch_only_items),
        "excluded_count": len(excluded_items),
        "high_volatility_count": sum(1 for item in [*items, *defensive_items] if "高波动" in item.get("risk_reasons", [])),
        "max_single_weight": max((float(item["target_weight"]) for item in [*items, *defensive_items]), default=0.0),
    }
    data_reliability_summary = {
        "eligible_candidates": len(primary_candidates),
        "selected_candidates": len(items),
        "selected_defensive_candidates": len(defensive_items),
        "watch_only_candidates": len(watch_only_items),
        "excluded_candidates": len(excluded_items),
        "weightable_candidates": len(selected_assets),
        "decision_reliability": "verified_or_alternate_provider",
    }
    note = "ETF 资金配置按你放进证券账户 ETF 的资金 100% 做研究参考，不代表你的总资产全仓。"
    if unavailable_reason:
        note = f"当前不建议动用 ETF 资金：{unavailable_reason}"
        items = []
        defensive_items = []
        weight_sum = 0.0
        risk_exposure_weight = 0.0
        defensive_weight = 0.0
        rounding_residual = 1.0
    elif defensive_items:
        note = "当前市场不适合全仓进攻，优先用防守 ETF 做资金配置参考。"
    elif watch_only_items or excluded_items:
        note = "高位/追高/数据不足资产会分到观察或等待分组，不进入主组合权重。" + note
    quote_time = await session.scalar(select(func.max(EtfIntradayLatestQuote.quote_time)))
    portfolio_generated_at = utcnow()
    data_as_of_time = quote_time or portfolio_generated_at
    return {
        "as_of_date": as_of_date or run.as_of_date,
        "data_as_of_time": data_as_of_time,
        "quote_time": quote_time,
        "daily_signal_date": run.as_of_date,
        "portfolio_generated_at": portfolio_generated_at,
        "asset_type": ASSET_TYPE_ETF,
        "items": items,
        "defensive_items": defensive_items,
        "watch_only_items": watch_only_items[:10],
        "excluded_items": excluded_items[:10],
        "cash_weight": rounding_residual,
        "target_invested_weight": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "weight_sum": weight_sum,
        "portfolio_mode": portfolio_mode,
        "market_regime": market_regime,
        "risk_exposure_weight": risk_exposure_weight,
        "defensive_weight": defensive_weight,
        "cash_reason": cash_reason,
        "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
        "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "constraint_summary": {
            **constraints_used,
            "primary_count": len(items),
            "defensive_count": len(defensive_items),
            "watch_only_count": len(watch_only_items),
            "excluded_count": len(excluded_items),
        },
        "constraints_used": constraints_used,
        "risk_summary": {
            **risk_summary,
            "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
            "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        },
        "data_reliability_summary": {
            **data_reliability_summary,
            "item_count": len(items),
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
            "若进攻和防守都不足，则保留现金等待。这是研究权重，不是交易指令。"
        ),
    }
