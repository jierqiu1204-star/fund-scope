from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date, timedelta
from statistics import mean, pstdev
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
    EtfPriceHistory,
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
        run.status = RUN_STATUS_SUCCESS
        run.finished_at = utcnow()
        run.summary_json = {
            "item_count": len(assets),
            "fund_count": sum(1 for item in assets if item.metadata.asset_type == ASSET_TYPE_FUND),
            "etf_count": sum(1 for item in assets if item.metadata.asset_type == ASSET_TYPE_ETF),
            "conclusion_counts": conclusion_counts,
            "research_only": True,
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
    items = await list_signal_items(session, run.id)
    assets: list[ComputedAsset] = []
    for item in items:
        metadata = await _metadata_for_code(session, item.asset_type, item.asset_code)
        fresh = await compute_asset(session, metadata, as_of_date=run.as_of_date, rank=item.rank)
        assets.append(
            ComputedAsset(
                metadata=metadata,
                rank=item.rank,
                total_score=item.total_score,
                conclusion=item.conclusion,
                latest_date=fresh.latest_date,
                latest_value=fresh.latest_value,
                usable_days=fresh.usable_days,
                sample_level=fresh.sample_level,
                metrics=item.metrics_json,
                score_breakdown=item.score_breakdown_json,
                risk_flags=item.risk_flags_json,
                rationale=item.rationale_json,
                source_note="公开 ETF 日线数据" if item.asset_type == ASSET_TYPE_ETF else "公开基金净值数据",
                entry_timing_label=str(
                    (item.metrics_json or {}).get("entry_timing_label")
                    or (item.rationale_json or {}).get("entry_timing_label")
                    or fresh.entry_timing_label
                ),
                entry_timing_reason=str(
                    (item.metrics_json or {}).get("entry_timing_reason")
                    or (item.rationale_json or {}).get("entry_timing_reason")
                    or fresh.entry_timing_reason
                ),
            )
        )
    return assets


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
    return {
        "latest_data_date": latest,
        "signal_date": latest_run.as_of_date if latest_run else None,
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
        "max_batches": None if codes else _etf_sync_max_batches(),
        "total_candidates": len(etf_codes),
        "processed": 0,
        "skipped": 0,
    }
    if fund_codes:
        fund_result = await sync_fund_nav_history(session, from_date, to_date, fund_codes)
    if etf_codes:
        all_batches = _chunks(etf_codes, _etf_sync_batch_size())
        max_batches = len(all_batches) if codes else _etf_sync_max_batches()
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


def _portfolio_exposure_for_asset(asset: ComputedAsset) -> float:
    exposure = 0.25
    if "杩介珮椋庨櫓" in asset.risk_flags or "追高风险" in asset.risk_flags:
        exposure -= 0.05
    if "楂樻尝鍔?" in asset.risk_flags or "高波动" in asset.risk_flags:
        exposure -= 0.05
    if not asset.metrics.get("default_display_eligible", False):
        exposure -= 0.10
    return max(0.05, min(0.30, exposure))


async def etf_observation_portfolio(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
    limit: int = 5,
    universe: str = UNIVERSE_DEFAULT,
) -> dict[str, Any]:
    run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    if run is None:
        return {
            "as_of_date": as_of_date or await latest_data_date(session) or date.today(),
            "asset_type": ASSET_TYPE_ETF,
            "items": [],
            "cash_weight": 1.0,
            "research_only": True,
            "no_trade_instruction": True,
            "note": "暂无 ETF 排序快照，先生成短线排序后再查看观察组合。",
        }
    assets, _total = await cached_signal_assets(
        session,
        run,
        asset_type=ASSET_TYPE_ETF,
        sort="score",
        universe=universe,
        limit=max(1, min(limit, 10)),
    )
    selected = assets[: max(1, min(limit, 10))]
    items: list[dict[str, Any]] = []
    total_weight = 0.0
    max_total_exposure = 0.75
    high_risk_count = sum(1 for item in selected if item.conclusion == CONCLUSION_HIGH_WATCH or item.risk_flags)
    if high_risk_count >= 2:
        max_total_exposure = 0.55
    for asset in selected:
        if total_weight >= max_total_exposure:
            break
        target = min(_portfolio_exposure_for_asset(asset), max_total_exposure - total_weight)
        if target <= 0:
            continue
        total_weight += target
        items.append(
            {
                "asset_type": asset.metadata.asset_type,
                "code": asset.metadata.code,
                "name": asset.metadata.name,
                "target_weight": round(target, 4),
                "score": round(asset.total_score, 2),
                "conclusion": asset.conclusion,
                "data_date": asset.latest_date,
                "evidence": [
                    f"综合分 {asset.total_score:.1f}",
                    f"近20日收益 {_format_percent(asset.metrics.get('return_20d'))}",
                    f"近20日平均成交额 {float(asset.metrics.get('average_turnover_20d') or 0) / 100_000_000:.2f} 亿元",
                ],
                "risk_reasons": asset.risk_flags or ["暂未触发主要风险标签"],
            }
        )
    return {
        "as_of_date": as_of_date or run.as_of_date,
        "asset_type": ASSET_TYPE_ETF,
        "items": items,
        "cash_weight": round(max(0.0, 1.0 - sum(item["target_weight"] for item in items)), 4),
        "research_only": True,
        "no_trade_instruction": True,
        "note": "观察组合只用于手动研究参考，不连接券商、不自动下单。",
    }
