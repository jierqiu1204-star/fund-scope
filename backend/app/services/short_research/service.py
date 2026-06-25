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
from app.services.short_etf.data import sync_etf_price_history
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
_PORTFOLIO_SINGLE_WEIGHT_CAP = 0.30
_PORTFOLIO_TOTAL_EXPOSURE_CAP = 0.60
_PORTFOLIO_THEME_EXPOSURE_CAP = 0.35
_PORTFOLIO_HIGH_CORRELATION = 0.85
_PORTFOLIO_CORRELATION_MIN_POINTS = 40
_PORTFOLIO_ENTRY_TIMING_OK = (
    ENTRY_TIMING_HEALTHY_PULLBACK,
    ENTRY_TIMING_TREND_CONTINUATION,
)
_PORTFOLIO_ENTRY_TIMING_FORBIDDEN = (
    ENTRY_TIMING_CHASE_RISK,
    ENTRY_TIMING_BREAK_WAIT,
    ENTRY_TIMING_VOLUME_WEAKENING,
    ENTRY_TIMING_INSUFFICIENT,
)
_PORTFOLIO_RISK_FLAGS_FORBIDDEN = (
    "数据不足",
    "数据滞后",
    "流动性不足",
)
_PORTFOLIO_RISK_FLAGS_WATCH_ONLY = (
    "追高风险",
    "连续大涨",
)
_PORTFOLIO_RISK_FLAGS_REDUCE_WEIGHT = (
    "高波动",
    "回撤较大",
)


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
    tags = tuple(row.theme_tags_json or ["ETF"])
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

    if today_return < 0 and (heavy_volume or below_ma20):
        reason = (
            f"今天 {_format_percent(today_return)}，价格已低于20日线或成交额明显放大，"
            "短线结构转弱，先等待新信号。"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_VOLUME_WEAKENING, "entry_timing_reason": reason}
    if today_return <= -0.025 or below_ma5_ma10 or ((return_5d or 0.0) < 0 and not near_or_above_ma10):
        reason = (
            f"今天 {_format_percent(today_return)}，最新价已跌破5日线和10日线附近，"
            "短线趋势开始变弱，适合先等待。"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_BREAK_WAIT, "entry_timing_reason": reason}
    if today_return >= 0.025 and ((return_20d or 0.0) >= 0.10 or (return_60d or 0.0) >= 0.25 or (distance_to_ma5 or 0.0) >= 0.035):
        reason = (
            f"今天 {_format_percent(today_return)}，且近20日 {_format_percent(return_20d)}、"
            f"近60日 {_format_percent(return_60d)} 已经不低，追高风险上升。"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_CHASE_RISK, "entry_timing_reason": reason}
    if positive_trend and -0.015 <= today_return <= -0.002 and near_or_above_ma10 and not heavy_volume:
        reason = (
            f"近5/20/60日仍为正，今天 {_format_percent(today_return)}，"
            f"仍在10日线附近或上方，属于健康回踩，适合继续观察。"
        )
        return {**base, "entry_timing_label": ENTRY_TIMING_HEALTHY_PULLBACK, "entry_timing_reason": reason}
    if positive_trend and today_return > -0.015 and near_or_above_ma10:
        reason = (
            f"近5/20/60日仍为正，今天 {_format_percent(today_return)}，"
            "价格仍在10日线附近或上方，趋势暂未破坏。"
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

    risk_flags: list[str] = []
    if usable_days < 20:
        risk_flags.append("数据不足")
    elif usable_days < 60:
        risk_flags.append("样本很短")
    elif usable_days < 120:
        risk_flags.append("短样本")
    if latest_date is None or (as_of_date - latest_date).days > STALE_DATA_DAYS:
        risk_flags.append("数据滞后")
    if (return_20d or 0.0) > CHASE_RETURN_20D or (return_60d or 0.0) > CHASE_RETURN_60D:
        risk_flags.append("追高风险")
    if (return_5d or 0.0) > SURGE_RETURN_5D:
        risk_flags.append("连续大涨")
    if volatility_20d is not None and volatility_20d > HIGH_DAILY_VOLATILITY_20D:
        risk_flags.append("高波动")
    if max_drawdown_60d is not None and max_drawdown_60d < LARGE_DRAWDOWN_60D:
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


def _forward_drawdown(series: list[PricePoint]) -> float | None:
    if len(series) < 2:
        return None
    return _max_drawdown(series)


def _validation_confidence(sample_count: int) -> str:
    if sample_count >= 30:
        return "sufficient"
    if sample_count >= _LABEL_VALIDATION_MIN_SAMPLES:
        return "limited"
    return "insufficient"


def _summarize_validation_samples(samples: list[dict[str, float]], total_samples: int) -> dict[str, Any]:
    sample_count = len(samples)
    excluded_count = max(0, total_samples - sample_count)
    if not samples:
        return {
            "sample_count": 0,
            "excluded_count": excluded_count,
            "coverage": 0.0,
            "avg_return": None,
            "median_return": None,
            "max_drawdown": None,
            "worst_forward_drawdown": None,
            "win_rate": None,
            "confidence": "insufficient",
            "insufficient_sample": True,
        }
    returns = [item["return"] for item in samples]
    drawdowns = [item["drawdown"] for item in samples]
    confidence = _validation_confidence(sample_count)
    return {
        "sample_count": sample_count,
        "excluded_count": excluded_count,
        "coverage": round(sample_count / total_samples, 4) if total_samples else 0.0,
        "avg_return": round(mean(returns), 6),
        "median_return": round(median(returns), 6),
        "max_drawdown": round(min(drawdowns), 6),
        "worst_forward_drawdown": round(min(drawdowns), 6),
        "win_rate": round(sum(1 for item in returns if item > 0) / sample_count, 4),
        "confidence": confidence,
        "insufficient_sample": confidence == "insufficient",
    }


async def _label_validation_summary(
    session: AsyncSession,
    assets: list[ComputedAsset],
    as_of_date: date,
) -> dict[str, Any]:
    etf_assets = [item for item in assets if item.metadata.asset_type == ASSET_TYPE_ETF][:_LABEL_VALIDATION_MAX_ASSETS]
    if not etf_assets:
        return {}
    grouped: dict[tuple[str, str], dict[int, list[dict[str, float]]]] = {}
    total_by_group: dict[tuple[str, str], dict[int, int]] = {}
    exclusion_reasons: dict[tuple[str, str], dict[int, dict[str, int]]] = {}
    evaluated_assets = 0
    max_window = max(_LABEL_VALIDATION_WINDOWS)
    for asset in etf_assets:
        series = await _etf_series(session, asset.metadata.code, as_of_date)
        if len(series) <= 60 + max_window:
            continue
        evaluated_assets += 1
        for index in range(60, len(series)):
            history = series[: index + 1]
            point_date = history[-1].point_date
            metrics = _score_metrics(asset.metadata, history, point_date)
            conclusion = _conclusion(metrics)
            entry_label = str(metrics.get("entry_timing_label") or ENTRY_TIMING_INSUFFICIENT)
            key = (conclusion, entry_label)
            grouped.setdefault(key, {window: [] for window in _LABEL_VALIDATION_WINDOWS})
            total_by_group.setdefault(key, {window: 0 for window in _LABEL_VALIDATION_WINDOWS})
            exclusion_reasons.setdefault(key, {window: {} for window in _LABEL_VALIDATION_WINDOWS})
            for window in _LABEL_VALIDATION_WINDOWS:
                total_by_group[key][window] += 1
                future_index = index + window
                if future_index >= len(series):
                    reasons = exclusion_reasons[key][window]
                    reasons["missing_future_price"] = reasons.get("missing_future_price", 0) + 1
                    continue
                if series[index].value <= 0 or series[future_index].value <= 0:
                    reasons = exclusion_reasons[key][window]
                    reasons["invalid_price"] = reasons.get("invalid_price", 0) + 1
                    continue
                window_series = series[index : future_index + 1]
                drawdown = _forward_drawdown(window_series)
                if drawdown is None:
                    reasons = exclusion_reasons[key][window]
                    reasons["insufficient_window"] = reasons.get("insufficient_window", 0) + 1
                    continue
                grouped[key][window].append(
                    {
                        "return": series[future_index].value / series[index].value - 1.0,
                        "drawdown": drawdown,
                    }
                )
    groups: list[dict[str, Any]] = []
    for (conclusion, entry_label), windows in sorted(grouped.items()):
        key = (conclusion, entry_label)
        window_summary = {
            str(window): {
                **_summarize_validation_samples(windows[window], total_by_group[key][window]),
                "exclusion_reasons": exclusion_reasons[key][window],
            }
            for window in _LABEL_VALIDATION_WINDOWS
        }
        groups.append(
            {
                "label": conclusion,
                "entry_timing_label": entry_label,
                "key": f"{conclusion} / {entry_label}",
                "windows": window_summary,
            }
        )
    return {
        "generated_at": utcnow().isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "asset_type": ASSET_TYPE_ETF,
        "rule_version": _LABEL_VALIDATION_RULE_VERSION,
        "asset_count": len(etf_assets),
        "evaluated_asset_count": evaluated_assets,
        "windows": list(_LABEL_VALIDATION_WINDOWS),
        "min_sample_count": _LABEL_VALIDATION_MIN_SAMPLES,
        "sufficient_sample_count": 30,
        "sample_policy": "最多取当前 ETF 排序前 120 只，按历史日线重新计算标签后验证未来 1/3/5/10 个交易日表现。",
        "groups": groups,
    }


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
                    "worst_forward_drawdown": metrics.get("worst_forward_drawdown") or metrics.get("max_drawdown"),
                    "confidence": str(metrics.get("confidence") or "insufficient"),
                    "metrics": metrics,
                }
            )
    return items


async def latest_signal_validation_run(session: AsyncSession) -> EtfSignalValidationRun | None:
    return cast(
        EtfSignalValidationRun | None,
        await session.scalar(
            select(EtfSignalValidationRun)
            .where(EtfSignalValidationRun.asset_type == ASSET_TYPE_ETF)
            .order_by(EtfSignalValidationRun.as_of_date.desc(), EtfSignalValidationRun.id.desc())
        ),
    )


async def latest_validation_evidence_by_label(session: AsyncSession) -> dict[tuple[str, str], dict[str, Any]]:
    run = await latest_signal_validation_run(session)
    if run is None:
        return {}
    rows = (
        await session.scalars(
            select(EtfSignalValidationItem).where(EtfSignalValidationItem.run_id == run.id)
        )
    ).all()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.label, row.entry_timing_label)
        evidence = grouped.setdefault(
            key,
            {
                "run_id": run.id,
                "as_of_date": run.as_of_date.isoformat(),
                "rule_version": run.rule_version,
                "confidence": "insufficient",
                "horizons": {},
            },
        )
        metrics = dict(row.metrics_json or {})
        horizon = {
            "sample_count": row.sample_count,
            "excluded_count": row.excluded_count,
            "coverage": metrics.get("coverage"),
            "avg_return": row.avg_return,
            "median_return": row.median_return,
            "win_rate": row.win_rate,
            "worst_forward_drawdown": row.worst_forward_drawdown,
            "confidence": row.confidence,
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
                }
            )
        elif "sample_count" not in evidence:
            evidence.update(
                {
                    "sample_count": row.sample_count,
                    "win_rate": row.win_rate,
                    "median_return": row.median_return,
                    "confidence": row.confidence,
                }
            )
    for evidence in grouped.values():
        horizons = evidence.get("horizons", {})
        five_day = horizons.get("5") if isinstance(horizons, dict) else None
        one_day = horizons.get("1") if isinstance(horizons, dict) else None
        excluded_total = 0
        sample_total = 0
        exclusion_summary: dict[str, int] = {}
        if isinstance(horizons, dict):
            for horizon in horizons.values():
                if not isinstance(horizon, dict):
                    continue
                sample_total += int(horizon.get("sample_count") or 0)
                excluded_total += int(horizon.get("excluded_count") or 0)
                reasons = horizon.get("exclusion_reasons")
                if isinstance(reasons, dict):
                    for key, count in reasons.items():
                        exclusion_summary[str(key)] = exclusion_summary.get(str(key), 0) + int(count or 0)
        degradation_warning = None
        if isinstance(five_day, dict) and five_day.get("median_return") is not None:
            if float(five_day["median_return"] or 0.0) < 0 or float(five_day.get("win_rate") or 0.0) < 0.45:
                degradation_warning = "近5日验证样本中位收益或胜率偏弱，标签有效性需要降级观察。"
        if degradation_warning is None and isinstance(one_day, dict) and one_day.get("median_return") is not None:
            if float(one_day["median_return"] or 0.0) < 0 and evidence.get("confidence") == "sufficient":
                degradation_warning = "近1日验证出现转弱迹象，短线标签需要结合当日走势复核。"
        evidence["freshness"] = {
            "as_of_date": run.as_of_date.isoformat(),
            "generated_at": run.created_at.isoformat(),
            "rule_version": run.rule_version,
        }
        evidence["sample_quality"] = {
            "sample_count_total": sample_total,
            "excluded_count_total": excluded_total,
            "exclusion_reasons": exclusion_summary,
        }
        evidence["degradation_warning"] = degradation_warning
    return grouped


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
            rule_version=_LABEL_VALIDATION_RULE_VERSION,
            config_json={"windows": list(_LABEL_VALIDATION_WINDOWS)},
            summary_json={},
            error_message="暂无 ETF 短线排序快照，无法做标签有效性验证。",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run
    assets, _total = await cached_signal_assets(
        session,
        source_run,
        asset_type=ASSET_TYPE_ETF,
        sort="score",
        universe=UNIVERSE_ALL,
        limit=_LABEL_VALIDATION_MAX_ASSETS,
    )
    summary = await _label_validation_summary(session, assets, source_run.as_of_date)
    run = EtfSignalValidationRun(
        status=RUN_STATUS_SUCCESS,
        started_at=started_at,
        finished_at=utcnow(),
        as_of_date=source_run.as_of_date,
        source_signal_run_id=source_run.id,
        asset_type=ASSET_TYPE_ETF,
        rule_version=_LABEL_VALIDATION_RULE_VERSION,
        config_json={
            "windows": list(_LABEL_VALIDATION_WINDOWS),
            "max_assets": _LABEL_VALIDATION_MAX_ASSETS,
            "min_sample_count": _LABEL_VALIDATION_MIN_SAMPLES,
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
    label_validation = (latest_etf_run.summary_json or {}).get("label_validation") if latest_etf_run else {}
    label_validation_generated_at = None
    if isinstance(label_validation, dict) and label_validation.get("generated_at"):
        try:
            label_validation_generated_at = datetime.fromisoformat(str(label_validation["generated_at"]))
        except ValueError:
            label_validation_generated_at = None
    return {
        "latest_data_date": latest,
        "signal_date": latest_run.as_of_date if latest_run else None,
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


async def _prioritize_etf_sync_codes(session: AsyncSession, codes: list[str]) -> list[str]:
    if not codes:
        return []
    code_set = set(codes)
    tracked_rows = await session.scalars(
        select(TrackedPosition.asset_code).where(
            TrackedPosition.asset_type == ASSET_TYPE_ETF,
            TrackedPosition.status == "active",
            TrackedPosition.asset_code.in_(codes),
        )
    )
    tracked = list(dict.fromkeys(str(code) for code in tracked_rows.all()))
    watchlist_rows = await session.scalars(
        select(TradableEtf.code).where(
            TradableEtf.code.in_(codes),
            TradableEtf.is_watchlist.is_(True),
        )
    )
    watchlist = list(dict.fromkeys(str(code) for code in watchlist_rows.all()))
    remaining = sorted(code_set - set(tracked) - set(watchlist))
    return [*tracked, *(code for code in watchlist if code not in tracked), *remaining]


async def sync_short_research_data(
    session: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    asset_type: str | None = None,
    codes: list[str] | None = None,
    sync_all_etfs: bool = False,
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
    etf_codes = await _prioritize_etf_sync_codes(session, etf_codes)
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


def _portfolio_decision_factors(asset: ComputedAsset, *, target_weight: float, reason: str | None) -> dict[str, Any]:
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
        "exclusion_reason": reason,
    }


def _portfolio_weight_explanation(asset: ComputedAsset, *, target_weight: float) -> str | None:
    if target_weight <= 0:
        return None
    confidence = asset.metrics.get("validation_confidence") or "未验证"
    sample_count = int(asset.metrics.get("validation_sample_count") or 0)
    turnover = float(asset.metrics.get("average_turnover_20d") or 0.0) / 100_000_000
    return (
        f"给 {target_weight * 100:.0f}% 观察权重：综合分 {asset.total_score:.1f}，"
        f"标签验证 {confidence}（样本 {sample_count}），20日成交额约 {turnover:.2f} 亿元；"
        "同时受单只30%、总仓位、主题集中度、波动和回撤约束。"
    )


def _portfolio_exclusion_explanation(reason: str | None) -> str | None:
    if reason is None:
        return None
    return f"未分配主组合权重：{reason}"


def _portfolio_item(asset: ComputedAsset, *, target_weight: float, reason: str | None = None) -> dict[str, Any]:
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
    weight_explanation = _portfolio_weight_explanation(asset, target_weight=target_weight)
    exclusion_explanation = _portfolio_exclusion_explanation(reason)
    decision_factors = _portfolio_decision_factors(asset, target_weight=target_weight, reason=reason)
    metrics = {
        **asset.metrics,
        "portfolio_weight_explanation": weight_explanation,
        "portfolio_exclusion_explanation": exclusion_explanation,
        "portfolio_decision_factors": decision_factors,
    }
    return {
        "asset_type": asset.metadata.asset_type,
        "code": asset.metadata.code,
        "name": asset.metadata.name,
        "target_weight": round(target_weight, 4),
        "score": round(asset.total_score, 2),
        "conclusion": asset.conclusion,
        "entry_timing_label": asset.entry_timing_label,
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
        "metrics": metrics,
    }


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
        "metrics": metrics,
    }


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
    watch_only = [_snapshot_item_out(row) for row in rows if row.item_type == "watch_only"]
    excluded = [_snapshot_item_out(row) for row in rows if row.item_type == "excluded"]
    summary = dict(snapshot.summary_json or {})
    return {
        "snapshot_id": snapshot.id,
        "generated_at": snapshot.created_at,
        "as_of_date": snapshot.as_of_date,
        "asset_type": snapshot.asset_type,
        "items": primary,
        "watch_only_items": watch_only,
        "excluded_items": excluded,
        "cash_weight": float(summary.get("cash_weight", max(0.0, 1.0 - sum(item["target_weight"] for item in primary)))),
        "single_weight_cap": summary.get("single_weight_cap", _PORTFOLIO_SINGLE_WEIGHT_CAP),
        "total_exposure_cap": summary.get("total_exposure_cap", _PORTFOLIO_TOTAL_EXPOSURE_CAP),
        "constraint_summary": summary.get("constraint_summary", {}),
        "research_only": True,
        "no_trade_instruction": True,
        "note": str(summary.get("note") or "观察组合只用于手动研究参考，不连接券商、不自动下单。"),
        "methodology": str(summary.get("methodology") or "按评分、波动、回撤、流动性、主题和相关性约束生成。"),
    }


async def persist_observation_portfolio_snapshot(
    session: AsyncSession,
    portfolio: dict[str, Any],
    *,
    source_signal_run_id: int | None,
    validation_run_id: int | None,
) -> EtfObservationPortfolioSnapshot:
    summary = {
        "cash_weight": portfolio.get("cash_weight", 1.0),
        "single_weight_cap": portfolio.get("single_weight_cap"),
        "total_exposure_cap": portfolio.get("total_exposure_cap"),
        "constraint_summary": portfolio.get("constraint_summary", {}),
        "note": portfolio.get("note", ""),
        "methodology": portfolio.get("methodology", ""),
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
    validation_run = await latest_signal_validation_run(session)
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
        if snapshot is not None:
            return await observation_portfolio_from_snapshot(session, snapshot)
    run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    if run is None:
        return {
            "as_of_date": as_of_date or await latest_data_date(session) or date.today(),
            "asset_type": ASSET_TYPE_ETF,
            "items": [],
            "watch_only_items": [],
            "excluded_items": [],
            "cash_weight": 1.0,
            "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
            "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
            "constraint_summary": {
                "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
                "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
                "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
                "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
            },
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
    validation_by_label = await latest_validation_evidence_by_label(session)
    if validation_by_label:
        assets = [_asset_with_validation_evidence(asset, validation_by_label) for asset in assets]
    primary_candidates: list[ComputedAsset] = []
    watch_only_items: list[dict[str, Any]] = []
    excluded_items: list[dict[str, Any]] = []
    for asset in assets:
        group, reason = _portfolio_candidate_group(asset)
        if group == "primary":
            primary_candidates.append(asset)
        elif group == "watch_only":
            watch_only_items.append(_portfolio_item(asset, target_weight=0.0, reason=reason))
        else:
            excluded_items.append(_portfolio_item(asset, target_weight=0.0, reason=reason))

    return_maps = await _portfolio_return_maps(session, primary_candidates, run.as_of_date)
    items: list[dict[str, Any]] = []
    total_weight = 0.0
    theme_weights: dict[str, float] = {}
    selected_return_maps: dict[str, dict[date, float]] = {}
    max_total_exposure = _PORTFOLIO_TOTAL_EXPOSURE_CAP
    selected_limit = max(1, min(limit, 10))
    for asset in primary_candidates:
        if len(items) >= selected_limit or total_weight >= max_total_exposure:
            break
        selection_reason: str | None = None
        asset_themes = list(asset.metadata.theme_tags[:2]) or ["ETF"]
        if any(theme_weights.get(theme, 0.0) >= _PORTFOLIO_THEME_EXPOSURE_CAP for theme in asset_themes):
            selection_reason = "同主题 ETF 已有足够观察权重，避免集中在单一方向。"
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

        target = min(_portfolio_exposure_for_asset(asset), max_total_exposure - total_weight)
        for theme in asset_themes:
            remaining_theme_room = _PORTFOLIO_THEME_EXPOSURE_CAP - theme_weights.get(theme, 0.0)
            target = min(target, max(0.0, remaining_theme_room))
        if target <= 0:
            watch_only_items.append(
                _portfolio_item(asset, target_weight=0.0, reason="主题集中度约束已满，暂不分配主组合权重。")
            )
            continue
        total_weight += target
        for theme in asset_themes:
            theme_weights[theme] = theme_weights.get(theme, 0.0) + target
        if asset_returns:
            selected_return_maps[asset.metadata.code] = asset_returns
        items.append(_portfolio_item(asset, target_weight=target))

    note = "观察组合只用于手动研究参考，不连接券商、不自动下单。"
    if not items:
        note = "当前没有同时满足短线观察和健康买点的 ETF，强势但高位的只适合继续观察，不给组合权重。"
    elif watch_only_items or excluded_items:
        note = "高位/追高/数据不足资产会分到观察或等待分组，不进入主组合权重。" + note
    return {
        "as_of_date": as_of_date or run.as_of_date,
        "asset_type": ASSET_TYPE_ETF,
        "items": items,
        "watch_only_items": watch_only_items[:10],
        "excluded_items": excluded_items[:10],
        "cash_weight": round(max(0.0, 1.0 - sum(item["target_weight"] for item in items)), 4),
        "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
        "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
        "constraint_summary": {
            "single_weight_cap": _PORTFOLIO_SINGLE_WEIGHT_CAP,
            "total_exposure_cap": _PORTFOLIO_TOTAL_EXPOSURE_CAP,
            "theme_exposure_cap": _PORTFOLIO_THEME_EXPOSURE_CAP,
            "high_correlation_threshold": _PORTFOLIO_HIGH_CORRELATION,
            "primary_count": len(items),
            "watch_only_count": len(watch_only_items),
            "excluded_count": len(excluded_items),
        },
        "research_only": True,
        "no_trade_instruction": True,
        "note": note,
        "methodology": (
            "先过滤短线观察且买点为健康回踩/趋势延续的 ETF，再按单只30%上限、总仓位上限、"
            "高波动/回撤降权、同主题集中度和近60日相关性做简化组合约束；这不是收益最优模型。"
        ),
    }
