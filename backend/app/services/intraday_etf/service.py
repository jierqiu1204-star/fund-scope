from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Any, cast
from zoneinfo import ZoneInfo

import akshare as ak
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfIntradayQuote,
    IntradayEtfWatchRun,
    ShortResearchSignalItem,
    TrackedPosition,
    TradableEtf,
)
from app.schemas.etf_quotes import (
    EtfIntradayQuoteOut,
    EtfLiveRankingItemOut,
    EtfLiveRankingListOut,
    IntradayEtfWatchItemOut,
    IntradayEtfWatchRunOut,
    IntradayEtfWatchStatusOut,
)
from app.services.short_research.service import (
    CONCLUSION_HIGH_WATCH,
    CONCLUSION_WATCH,
    latest_signal_run,
)

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
QUOTE_SOURCE_AKSHARE = "akshare"
QUOTE_FRESH_SECONDS = 180
WATCH_REFRESH_SECONDS = 60
PAGE_POLL_SECONDS = 30
TOP_SIGNAL_LIMIT = 20
LIVE_RANKING_LIMIT_DEFAULT = 50
LIVE_RANKING_LIMIT_MAX = 200
SOURCE_TOP20_SIGNAL = "top20_signal"
SOURCE_SHORT_WATCH = "short_watch"
SOURCE_HIGH_WATCH = "high_watch"
SOURCE_TRACKED_POSITION = "tracked_position"
LIVE_LABEL_DATA_INSUFFICIENT = "数据不足"
LIVE_LABEL_DOWN_PERSISTENT = "跌破等待"
LIVE_LABEL_HEALTHY_PULLBACK = "健康回踩"
LIVE_LABEL_TREND_CONTINUATION = "趋势延续"
LIVE_LABEL_CHASE_WARNING = "冲高别追"
_PROVIDER_FAILURE_COUNT = 0
_PROVIDER_BACKOFF_UNTIL: datetime | None = None
_INTRADAY_RANKING_DATA_INSUFFICIENT_REASON = "暂无新鲜盘中行情，暂不做盘中加分。"


@dataclass(frozen=True)
class MarketState:
    status: str
    session: str | None
    now: datetime


@dataclass
class WatchItem:
    etf_code: str
    rank: int | None = None
    sources: set[str] = field(default_factory=set)


@dataclass
class WatchlistResult:
    items: list[WatchItem]
    signal_run_id: int | None
    signal_as_of_date: date | None
    signal_status: str
    message: str


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        normalized = value.replace(",", "").replace("%", "").strip()
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _to_pagination(limit: int, offset: int) -> tuple[int, int]:
    safe_limit = max(1, min(limit, LIVE_RANKING_LIMIT_MAX))
    safe_offset = max(0, offset)
    return safe_limit, safe_offset


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _entry_timing(
    change_percent: float,
    conclusion: str | None,
) -> tuple[str, str, int]:
    if change_percent <= -2.5:
        return LIVE_LABEL_DOWN_PERSISTENT, "跌破后，偏离趋势，短线加分下调。", -5
    if change_percent <= -0.2:
        if conclusion in {CONCLUSION_WATCH, CONCLUSION_HIGH_WATCH}:
            return LIVE_LABEL_HEALTHY_PULLBACK, "回踩不宜过度下跌且有回归迹象，加分。", 2
        return LIVE_LABEL_DOWN_PERSISTENT, "下跌区间明显，继续观察，暂不加分。", -5
    if change_percent < 2.0:
        return LIVE_LABEL_TREND_CONTINUATION, "涨幅温和，趋势延续加分。", 1
    return LIVE_LABEL_CHASE_WARNING, "涨幅过快，短线追高风险较高，降分。", -3


@dataclass(frozen=True)
class NormalizedQuote:
    etf_code: str
    quote_time: datetime
    trade_date: date
    latest_price: float
    change_percent: float | None
    volume: float | None
    turnover: float | None
    bid_price: float | None
    ask_price: float | None
    iopv: float | None
    premium_discount_pct: float | None
    source: str
    raw: dict[str, Any]


def current_market_state(now: datetime | None = None) -> MarketState:
    local_now = (now or datetime.now(ASIA_SHANGHAI)).astimezone(ASIA_SHANGHAI)
    if local_now.weekday() >= 5:
        return MarketState("closed", None, local_now)
    current = local_now.time()
    if time(9, 30) <= current < time(11, 30):
        return MarketState("open", "morning", local_now)
    if time(13, 0) <= current < time(15, 0):
        return MarketState("open", "afternoon", local_now)
    return MarketState("closed", None, local_now)


def is_quote_stale(quote_time: datetime | None, now: datetime | None = None) -> bool:
    if quote_time is None:
        return True
    effective_now = now or datetime.now(ASIA_SHANGHAI).replace(tzinfo=None)
    return effective_now - quote_time > timedelta(seconds=QUOTE_FRESH_SECONDS)


def _number(record: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = record.get(key)
        if value in (None, "", "-", "--"):
            continue
        try:
            return float(str(value).replace(",", "").replace("%", ""))
        except ValueError:
            continue
    return None


def _text(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _parse_quote_time(record: dict[str, Any], fallback: datetime | None = None) -> datetime:
    raw = _text(record, "行情时间", "更新时间", "time", "quote_time")
    if raw:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if parsed.year == 1900:
                    base = (fallback or datetime.now(ASIA_SHANGHAI)).astimezone(ASIA_SHANGHAI)
                    return datetime.combine(base.date(), parsed.time()).replace(tzinfo=ASIA_SHANGHAI).replace(tzinfo=None)
                return parsed
            except ValueError:
                continue
    return (fallback or datetime.now(ASIA_SHANGHAI)).astimezone(ASIA_SHANGHAI).replace(tzinfo=None)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, int | str | bool):
        return value
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except (TypeError, ValueError):
            pass
    return str(value)


def _json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in record.items()}


def normalize_spot_record(record: dict[str, Any], *, fallback_time: datetime | None = None) -> NormalizedQuote | None:
    code = _text(record, "代码", "code", "symbol")
    price = _number(record, "最新价", "现价", "最新", "price", "latest_price")
    if not code or price is None or price <= 0:
        return None
    quote_time = _parse_quote_time(record, fallback=fallback_time)
    return NormalizedQuote(
        etf_code=code[-6:],
        quote_time=quote_time,
        trade_date=quote_time.date(),
        latest_price=price,
        change_percent=_number(record, "涨跌幅", "涨幅", "change_percent", "pct_change"),
        volume=_number(record, "成交量", "volume"),
        turnover=_number(record, "成交额", "turnover", "amount"),
        bid_price=_number(record, "买一", "买入", "bid", "bid_price"),
        ask_price=_number(record, "卖一", "卖出", "ask", "ask_price"),
        iopv=_number(record, "IOPV", "iopv"),
        premium_discount_pct=_number(record, "折价率", "溢价率", "折溢价率", "premium_discount_pct"),
        source=QUOTE_SOURCE_AKSHARE,
        raw=_json_safe_record(record),
    )


async def fetch_spot_quotes(fetcher: Any | None = None) -> dict[str, NormalizedQuote]:
    global _PROVIDER_BACKOFF_UNTIL, _PROVIDER_FAILURE_COUNT
    now = datetime.now(ASIA_SHANGHAI)
    use_default_provider = fetcher is None
    if use_default_provider and _PROVIDER_BACKOFF_UNTIL is not None and now < _PROVIDER_BACKOFF_UNTIL:
        wait_seconds = int((_PROVIDER_BACKOFF_UNTIL - now).total_seconds())
        raise RuntimeError(f"ETF 行情源正在退避，约 {wait_seconds} 秒后再试；本次使用最近缓存。")
    effective_fetcher = fetcher or ak.fund_etf_spot_em
    try:
        frame = await asyncio.to_thread(effective_fetcher)
    except Exception as exc:  # noqa: BLE001
        backoff_seconds = 0
        if use_default_provider:
            _PROVIDER_FAILURE_COUNT += 1
            backoff_seconds = min(300, 30 * (2 ** min(_PROVIDER_FAILURE_COUNT - 1, 4)))
            _PROVIDER_BACKOFF_UNTIL = now + timedelta(seconds=backoff_seconds)
        raise RuntimeError(
            f"ETF 行情源请求失败，已退避 {backoff_seconds} 秒；本次使用最近缓存。原始错误：{exc}"
        ) from exc
    if use_default_provider:
        _PROVIDER_FAILURE_COUNT = 0
        _PROVIDER_BACKOFF_UNTIL = None
    quotes: dict[str, NormalizedQuote] = {}
    for _, row in frame.iterrows():
        quote = normalize_spot_record(dict(row), fallback_time=now)
        if quote is not None:
            quotes[quote.etf_code] = quote
    return quotes


async def latest_intraday_quote(session: AsyncSession, etf_code: str) -> EtfIntradayQuote | None:
    return cast(
        EtfIntradayQuote | None,
        await session.scalar(
            select(EtfIntradayQuote)
            .where(EtfIntradayQuote.etf_code == etf_code)
            .order_by(EtfIntradayQuote.quote_time.desc(), EtfIntradayQuote.id.desc())
        ),
    )


async def latest_quotes_by_code(session: AsyncSession, codes: list[str]) -> dict[str, EtfIntradayQuote]:
    if not codes:
        return {}
    quotes: dict[str, EtfIntradayQuote] = {}
    rows = (
        await session.scalars(
            select(EtfIntradayQuote)
            .where(EtfIntradayQuote.etf_code.in_(codes))
            .order_by(EtfIntradayQuote.etf_code.asc(), EtfIntradayQuote.quote_time.desc(), EtfIntradayQuote.id.desc())
        )
    ).all()
    for row in rows:
        quotes.setdefault(row.etf_code, row)
    return quotes


async def latest_watch_run(session: AsyncSession) -> IntradayEtfWatchRun | None:
    return cast(
        IntradayEtfWatchRun | None,
        await session.scalar(
            select(IntradayEtfWatchRun).order_by(
                IntradayEtfWatchRun.started_at.desc(),
                IntradayEtfWatchRun.id.desc(),
            )
        ),
    )


async def build_watchlist(session: AsyncSession, *, top_limit: int = TOP_SIGNAL_LIMIT) -> WatchlistResult:
    watch_map: dict[str, WatchItem] = {}
    run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    signal_status = "missing"
    message = "尚未生成 ETF 短线评分，当前盯盘已追踪 ETF。"
    signal_run_id: int | None = None
    signal_as_of_date: date | None = None
    if run is not None:
        signal_status = "ready"
        signal_run_id = run.id
        signal_as_of_date = run.as_of_date
        message = "使用最新 ETF 短线评分前 20、短线观察/高位观察和已追踪 ETF 盯盘。"
        signal_items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(
                    ShortResearchSignalItem.run_id == run.id,
                    ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
                )
                .order_by(ShortResearchSignalItem.rank.asc())
            )
        ).all()
        for item in signal_items:
            sources: set[str] = set()
            if item.rank is not None and item.rank <= top_limit:
                sources.add(SOURCE_TOP20_SIGNAL)
            if item.conclusion == CONCLUSION_WATCH:
                sources.add(SOURCE_SHORT_WATCH)
            if item.conclusion == CONCLUSION_HIGH_WATCH:
                sources.add(SOURCE_HIGH_WATCH)
            if sources:
                watch_map[item.asset_code] = WatchItem(etf_code=item.asset_code, rank=item.rank, sources=sources)

    tracked_rows = (
        await session.scalars(
            select(TrackedPosition).where(
                TrackedPosition.asset_type == ASSET_TYPE_ETF,
                TrackedPosition.status == "active",
            )
        )
    ).all()
    for position in tracked_rows:
        watch_item = watch_map.setdefault(position.asset_code, WatchItem(etf_code=position.asset_code))
        watch_item.sources.add(SOURCE_TRACKED_POSITION)

    items = sorted(watch_map.values(), key=lambda item: (item.rank is None, item.rank or 9999, item.etf_code))
    return WatchlistResult(items, signal_run_id, signal_as_of_date, signal_status, message)


async def live_rankings(
    session: AsyncSession,
    *,
    limit: int = LIVE_RANKING_LIMIT_DEFAULT,
    offset: int = 0,
    q: str | None = None,
) -> EtfLiveRankingListOut:
    safe_limit, safe_offset = _to_pagination(limit, offset)
    state = current_market_state()
    watchlist = await build_watchlist(session)
    watch_codes = [item.etf_code for item in watchlist.items]
    latest_run = await latest_watch_run(session)
    is_open = state.status == "open"
    if not watch_codes:
        return EtfLiveRankingListOut(
            market_status=state.status,
            market_session=state.session,
            message=watchlist.message if is_open else f"{watchlist.message} 当前休市，页面不会自动刷新盘中行情。",
            quote_refresh_seconds=WATCH_REFRESH_SECONDS if is_open else 0,
            page_poll_seconds=PAGE_POLL_SECONDS if is_open else 0,
            watched_count=0,
            total=0,
            signal_as_of_date=watchlist.signal_as_of_date,
            signal_status=watchlist.signal_status,
            latest_run=watch_run_out(latest_run) if latest_run is not None else None,
            items=[],
        )

    names = await quote_name_map(session, watch_codes)
    latest_quotes = await latest_quotes_by_code(session, watch_codes)
    keyword = q.strip().lower() if q else None
    now = datetime.now(ASIA_SHANGHAI).replace(tzinfo=None)
    signal_run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    signal_items_by_code: dict[str, ShortResearchSignalItem] = {}
    if signal_run is not None:
        signal_rows = await session.scalars(
            select(ShortResearchSignalItem).where(
                ShortResearchSignalItem.run_id == signal_run.id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
                ShortResearchSignalItem.asset_code.in_(watch_codes),
            )
        )
        signal_items_by_code = {item.asset_code: item for item in signal_rows.all()}

    scored_rows: list[dict[str, Any]] = []
    for watch_item in watchlist.items:
        name = names.get(watch_item.etf_code)
        if keyword and keyword not in watch_item.etf_code.lower() and keyword not in (name or "").lower():
            continue
        signal_item = signal_items_by_code.get(watch_item.etf_code)
        base_score = _float_or_none(signal_item.total_score) if signal_item is not None else None
        base_rank = watch_item.rank
        conclusion = signal_item.conclusion if signal_item is not None else None
        quote = latest_quotes.get(watch_item.etf_code)

        if quote is None or is_quote_stale(quote.quote_time, now):
            live_total_score = _clamp_score(base_score - 8) if base_score is not None else None
            live_label = LIVE_LABEL_DATA_INSUFFICIENT
            live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
        else:
            if quote.change_percent is None:
                live_total_score = _clamp_score(base_score - 8) if base_score is not None else None
                live_label = LIVE_LABEL_DATA_INSUFFICIENT
                live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
            else:
                live_label, live_reason, adjustment = _entry_timing(quote.change_percent, conclusion)
                live_total_score = _clamp_score(base_score + adjustment) if base_score is not None else None
                avg_turnover_20d = (
                    _float_or_none((signal_item.metrics_json or {}).get("average_turnover_20d")) if signal_item else None
                )
                if (
                    live_total_score is not None
                    and quote.turnover is not None
                    and avg_turnover_20d is not None
                    and avg_turnover_20d > 0
                ):
                    if quote.turnover >= avg_turnover_20d * 0.7:
                        live_total_score = _clamp_score(live_total_score + 1)
                    elif quote.turnover < avg_turnover_20d * 0.1:
                        live_total_score = _clamp_score(live_total_score - 2)

        scored_rows.append(
            {
                "etf_code": watch_item.etf_code,
                "etf_name": name,
                "base_rank": base_rank,
                "conclusion": conclusion,
                "base_score": base_score,
                "live_total_score": live_total_score,
                "live_entry_timing_label": live_label,
                "live_entry_timing_reason": live_reason,
                "sources": sorted(watch_item.sources),
                "quote": quote_out(quote, etf_name=name, now=now) if quote is not None else None,
            }
        )

    scored_rows.sort(
        key=lambda row: (
            row["live_total_score"] is None,
            -(row["live_total_score"] or 0.0),
            row["etf_code"],
        )
    )

    ranked_rows = []
    for index, row in enumerate(scored_rows, start=1):
        row["live_rank"] = index
        row["rank_change"] = row["base_rank"] - index if row["base_rank"] is not None else None
        ranked_rows.append(row)

    selected = ranked_rows[safe_offset : safe_offset + safe_limit]
    return EtfLiveRankingListOut(
        market_status=state.status,
        market_session=state.session,
        message=watchlist.message if is_open else f"{watchlist.message} 当前休市，页面不会自动刷新盘中行情。",
        quote_refresh_seconds=WATCH_REFRESH_SECONDS if is_open else 0,
        page_poll_seconds=PAGE_POLL_SECONDS if is_open else 0,
        watched_count=len(watchlist.items),
        total=len(ranked_rows),
        signal_as_of_date=watchlist.signal_as_of_date,
        signal_status=watchlist.signal_status,
        latest_run=watch_run_out(latest_run) if latest_run is not None else None,
        items=[
            EtfLiveRankingItemOut(
                etf_code=row["etf_code"],
                etf_name=row["etf_name"],
                base_rank=row["base_rank"],
                live_rank=row["live_rank"],
                rank_change=row["rank_change"],
                sources=row["sources"],
                conclusion=row["conclusion"],
                base_score=row["base_score"],
                live_total_score=row["live_total_score"],
                live_entry_timing_label=row["live_entry_timing_label"],
                live_entry_timing_reason=row["live_entry_timing_reason"],
                quote=row["quote"],
            )
            for row in selected
        ],
    )


async def quote_name_map(session: AsyncSession, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    rows = await session.scalars(select(TradableEtf).where(TradableEtf.code.in_(codes)))
    return {row.code: row.name for row in rows.all()}


def quote_out(
    quote: EtfIntradayQuote,
    *,
    etf_name: str | None = None,
    now: datetime | None = None,
) -> EtfIntradayQuoteOut:
    stale = is_quote_stale(quote.quote_time, now)
    return EtfIntradayQuoteOut(
        etf_code=quote.etf_code,
        etf_name=etf_name,
        quote_time=quote.quote_time,
        trade_date=quote.trade_date,
        latest_price=quote.latest_price,
        change_percent=quote.change_percent,
        volume=quote.volume,
        turnover=quote.turnover,
        bid_price=quote.bid_price,
        ask_price=quote.ask_price,
        iopv=quote.iopv,
        premium_discount_pct=quote.premium_discount_pct,
        source=quote.source,
        freshness_status="stale" if stale else quote.freshness_status,
        is_stale=stale,
    )


def watch_run_out(row: IntradayEtfWatchRun) -> IntradayEtfWatchRunOut:
    return IntradayEtfWatchRunOut(
        id=row.id,
        run_type=row.run_type,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        market_session=row.market_session,
        watched_count=row.watched_count,
        updated_quote_count=row.updated_quote_count,
        stale_quote_count=row.stale_quote_count,
        alert_count=row.alert_count,
        email_sent_count=row.email_sent_count,
        suppressed_count=row.suppressed_count,
        skipped_reason=row.skipped_reason,
        error_message=row.error_message,
        details=dict(row.details_json or {}),
    )


async def persist_quotes(
    session: AsyncSession,
    watchlist: WatchlistResult,
    quotes: dict[str, NormalizedQuote],
) -> int:
    updated = 0
    for item in watchlist.items:
        quote = quotes.get(item.etf_code)
        if quote is None:
            continue
        existing = await session.scalar(
            select(EtfIntradayQuote).where(
                EtfIntradayQuote.etf_code == quote.etf_code,
                EtfIntradayQuote.quote_time == quote.quote_time,
            )
        )
        if existing is None:
            session.add(
                EtfIntradayQuote(
                    etf_code=quote.etf_code,
                    quote_time=quote.quote_time,
                    trade_date=quote.trade_date,
                    latest_price=quote.latest_price,
                    change_percent=quote.change_percent,
                    volume=quote.volume,
                    turnover=quote.turnover,
                    bid_price=quote.bid_price,
                    ask_price=quote.ask_price,
                    iopv=quote.iopv,
                    premium_discount_pct=quote.premium_discount_pct,
                    source=quote.source,
                    freshness_status="fresh",
                    raw_json=quote.raw,
                )
            )
        else:
            existing.latest_price = quote.latest_price
            existing.change_percent = quote.change_percent
            existing.volume = quote.volume
            existing.turnover = quote.turnover
            existing.bid_price = quote.bid_price
            existing.ask_price = quote.ask_price
            existing.iopv = quote.iopv
            existing.premium_discount_pct = quote.premium_discount_pct
            existing.raw_json = quote.raw
            existing.freshness_status = "fresh"
        updated += 1
    await session.commit()
    return updated


async def watch_status(session: AsyncSession) -> IntradayEtfWatchStatusOut:
    state = current_market_state()
    watchlist = await build_watchlist(session)
    codes = [item.etf_code for item in watchlist.items]
    names = await quote_name_map(session, codes)
    quotes = await latest_quotes_by_code(session, codes)
    latest_run = await latest_watch_run(session)
    now = datetime.now(ASIA_SHANGHAI).replace(tzinfo=None)
    is_open = state.status == "open"
    return IntradayEtfWatchStatusOut(
        market_status=state.status,
        market_session=state.session,
        message=watchlist.message if is_open else f"{watchlist.message} 当前休市，页面不会自动刷新盘中行情。",
        quote_refresh_seconds=WATCH_REFRESH_SECONDS if is_open else 0,
        page_poll_seconds=PAGE_POLL_SECONDS if is_open else 0,
        watched_count=len(watchlist.items),
        top20_signal_run_id=watchlist.signal_run_id,
        signal_as_of_date=watchlist.signal_as_of_date,
        signal_status=watchlist.signal_status,
        latest_run=watch_run_out(latest_run) if latest_run is not None else None,
        items=[
            IntradayEtfWatchItemOut(
                etf_code=item.etf_code,
                etf_name=names.get(item.etf_code),
                rank=item.rank,
                sources=sorted(item.sources),
                quote=quote_out(quotes[item.etf_code], etf_name=names.get(item.etf_code), now=now)
                if item.etf_code in quotes
                else None,
            )
            for item in watchlist.items
        ],
    )
