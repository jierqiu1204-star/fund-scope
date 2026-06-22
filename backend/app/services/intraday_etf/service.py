from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Any, cast
from zoneinfo import ZoneInfo

import akshare as ak
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfIntradayDailySummary,
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


def _daily_entry_timing(signal_item: ShortResearchSignalItem | None) -> tuple[str, str]:
    if signal_item is None:
        return LIVE_LABEL_DATA_INSUFFICIENT, "缺少最新短线排序缓存，请先生成短线排序。"
    metrics = signal_item.metrics_json or {}
    rationale = signal_item.rationale_json or {}
    label = metrics.get("entry_timing_label") or rationale.get("entry_timing_label")
    reason = metrics.get("entry_timing_reason") or rationale.get("entry_timing_reason")
    return (
        str(label or LIVE_LABEL_DATA_INSUFFICIENT),
        str(reason or "短线排序缓存缺少日线买点，请重新生成短线排序。"),
    )


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


def _parse_quote_time(record: dict[str, Any], fallback: datetime | None = None) -> tuple[datetime, bool]:
    raw = _text(record, "行情时间", "更新时间", "time", "quote_time")
    if raw:
        normalized = raw.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ASIA_SHANGHAI).replace(tzinfo=None)
            return parsed, False
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if parsed.year == 1900:
                    base = (fallback or datetime.now(ASIA_SHANGHAI)).astimezone(ASIA_SHANGHAI)
                    quote_time = datetime.combine(base.date(), parsed.time()).replace(tzinfo=ASIA_SHANGHAI).replace(tzinfo=None)
                    return quote_time, False
                return parsed, False
            except ValueError:
                continue
    quote_time = (fallback or datetime.now(ASIA_SHANGHAI)).astimezone(ASIA_SHANGHAI).replace(tzinfo=None)
    return quote_time, True


def is_quote_time_fallback(quote: EtfIntradayQuote | None) -> bool:
    return bool(quote is not None and (quote.raw_json or {}).get("quote_time_is_fallback"))


def is_fresh_decision_quote(quote: EtfIntradayQuote | None, now: datetime | None = None) -> bool:
    return bool(quote is not None and not is_quote_time_fallback(quote) and not is_quote_stale(quote.quote_time, now))


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
    quote_time, quote_time_is_fallback = _parse_quote_time(record, fallback=fallback_time)
    raw = _json_safe_record(record)
    raw["quote_time_is_fallback"] = quote_time_is_fallback
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
        raw=raw,
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
    unique_codes = list(dict.fromkeys(code for code in codes if code))
    if not unique_codes:
        return {}

    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        values_sql = ", ".join(f"(:code_{index})" for index in range(len(unique_codes)))
        params = {f"code_{index}": code for index, code in enumerate(unique_codes)}
        stmt = text(
            f"""
            WITH watch_codes(etf_code) AS (VALUES {values_sql})
            SELECT q.*
            FROM watch_codes w
            JOIN LATERAL (
                SELECT *
                FROM etf_intraday_quotes q
                WHERE q.etf_code = w.etf_code
                ORDER BY q.quote_time DESC, q.id DESC
                LIMIT 1
            ) q ON TRUE
            """
        )
        rows = (await session.scalars(select(EtfIntradayQuote).from_statement(stmt).params(**params))).all()
        return {row.etf_code: row for row in rows}

    ranked = (
        select(
            EtfIntradayQuote.id.label("quote_id"),
            func.row_number()
            .over(
                partition_by=EtfIntradayQuote.etf_code,
                order_by=(EtfIntradayQuote.quote_time.desc(), EtfIntradayQuote.id.desc()),
            )
            .label("rank"),
        )
        .where(EtfIntradayQuote.etf_code.in_(unique_codes))
        .subquery()
    )
    rows = (
        await session.scalars(
            select(EtfIntradayQuote)
            .join(ranked, ranked.c.quote_id == EtfIntradayQuote.id)
            .where(ranked.c.rank == 1)
        )
    ).all()
    return {row.etf_code: row for row in rows}

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
        daily_entry_timing_label, daily_entry_timing_reason = _daily_entry_timing(signal_item)

        score_source = "daily" if base_score is not None else "unavailable"
        live_total_score = base_score
        intraday_adjustment_score: float | None = None
        score_contribution_reasons: list[str] = []
        if base_score is not None:
            score_contribution_reasons.append(f"日线基础分 {base_score:.1f}")
        if not is_open or not is_fresh_decision_quote(quote, now):
            live_label = LIVE_LABEL_DATA_INSUFFICIENT
            live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
            score_contribution_reasons.append("休市或行情不新鲜，仅使用日线基础分。")
        else:
            assert quote is not None
            if quote.change_percent is None:
                live_label = LIVE_LABEL_DATA_INSUFFICIENT
                live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
                score_contribution_reasons.append("缺少盘中涨跌幅，实时调整不可用。")
            else:
                score_source = "intraday"
                live_label, live_reason, adjustment = _entry_timing(quote.change_percent, conclusion)
                intraday_adjustment_score = float(adjustment)
                score_contribution_reasons.append(f"盘中涨跌 {quote.change_percent:+.2f}%：{adjustment:+.1f} 分。")
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
                        intraday_adjustment_score = (intraday_adjustment_score or 0.0) + 1.0
                        score_contribution_reasons.append("盘中成交额接近近期均值，流动性加 1 分。")
                    elif quote.turnover < avg_turnover_20d * 0.1:
                        live_total_score = _clamp_score(live_total_score - 2)
                        intraday_adjustment_score = (intraday_adjustment_score or 0.0) - 2.0
                        score_contribution_reasons.append("盘中成交额明显偏低，流动性扣 2 分。")

        scored_rows.append(
            {
                "etf_code": watch_item.etf_code,
                "etf_name": name,
                "base_rank": base_rank,
                "conclusion": conclusion,
                "base_score": base_score,
                "live_total_score": live_total_score,
                "intraday_adjustment_score": intraday_adjustment_score,
                "score_source": score_source,
                "score_contribution_reasons": score_contribution_reasons,
                "live_entry_timing_label": live_label,
                "live_entry_timing_reason": live_reason,
                "daily_entry_timing_label": daily_entry_timing_label,
                "daily_entry_timing_reason": daily_entry_timing_reason,
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
                intraday_adjustment_score=row["intraday_adjustment_score"],
                score_source=row["score_source"],
                score_contribution_reasons=row["score_contribution_reasons"],
                live_entry_timing_label=row["live_entry_timing_label"],
                live_entry_timing_reason=row["live_entry_timing_reason"],
                daily_entry_timing_label=row["daily_entry_timing_label"],
                daily_entry_timing_reason=row["daily_entry_timing_reason"],
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
    stale = is_quote_stale(quote.quote_time, now) or is_quote_time_fallback(quote)
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
        quote_time_is_fallback=is_quote_time_fallback(quote),
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


async def summarize_and_cleanup_intraday_quotes(
    session: AsyncSession,
    *,
    retention_trading_days: int = 60,
) -> dict[str, Any]:
    safe_days = max(1, retention_trading_days)
    trade_dates = (
        await session.scalars(
            select(EtfIntradayQuote.trade_date)
            .distinct()
            .order_by(EtfIntradayQuote.trade_date.desc())
        )
    ).all()
    if len(trade_dates) <= safe_days:
        return {
            "retention_trading_days": safe_days,
            "cutoff_date": None,
            "summarized_groups": 0,
            "deleted_rows": 0,
            "message": "盘中明细未超过保留窗口，无需清理。",
        }

    cutoff_date = trade_dates[safe_days - 1]
    group_rows = (
        await session.execute(
            select(
                EtfIntradayQuote.etf_code.label("etf_code"),
                EtfIntradayQuote.trade_date.label("trade_date"),
                func.count(EtfIntradayQuote.id).label("quote_count"),
                func.min(EtfIntradayQuote.quote_time).label("first_quote_time"),
                func.max(EtfIntradayQuote.quote_time).label("last_quote_time"),
                func.min(EtfIntradayQuote.latest_price).label("low_price"),
                func.max(EtfIntradayQuote.latest_price).label("high_price"),
                func.sum(EtfIntradayQuote.volume).label("total_volume"),
                func.sum(EtfIntradayQuote.turnover).label("total_turnover"),
            )
            .where(EtfIntradayQuote.trade_date < cutoff_date)
            .group_by(EtfIntradayQuote.etf_code, EtfIntradayQuote.trade_date)
        )
    ).all()

    summarized = 0
    for row in group_rows:
        first_quote = await session.scalar(
            select(EtfIntradayQuote)
            .where(
                EtfIntradayQuote.etf_code == row.etf_code,
                EtfIntradayQuote.trade_date == row.trade_date,
            )
            .order_by(EtfIntradayQuote.quote_time.asc(), EtfIntradayQuote.id.asc())
        )
        last_quote = await session.scalar(
            select(EtfIntradayQuote)
            .where(
                EtfIntradayQuote.etf_code == row.etf_code,
                EtfIntradayQuote.trade_date == row.trade_date,
            )
            .order_by(EtfIntradayQuote.quote_time.desc(), EtfIntradayQuote.id.desc())
        )
        existing = await session.scalar(
            select(EtfIntradayDailySummary).where(
                EtfIntradayDailySummary.etf_code == row.etf_code,
                EtfIntradayDailySummary.trade_date == row.trade_date,
            )
        )
        summary = existing or EtfIntradayDailySummary(etf_code=row.etf_code, trade_date=row.trade_date)
        summary.quote_count = int(row.quote_count or 0)
        summary.first_quote_time = row.first_quote_time
        summary.last_quote_time = row.last_quote_time
        summary.open_price = first_quote.latest_price if first_quote is not None else None
        summary.close_price = last_quote.latest_price if last_quote is not None else None
        summary.low_price = float(row.low_price) if row.low_price is not None else None
        summary.high_price = float(row.high_price) if row.high_price is not None else None
        summary.total_volume = float(row.total_volume) if row.total_volume is not None else None
        summary.total_turnover = float(row.total_turnover) if row.total_turnover is not None else None
        summary.source = last_quote.source if last_quote is not None else None
        summary.summary_json = {
            "retention_trading_days": safe_days,
            "source": "raw_intraday_cleanup",
            "display_only": True,
        }
        if existing is None:
            session.add(summary)
        summarized += 1

    delete_result = await session.execute(delete(EtfIntradayQuote).where(EtfIntradayQuote.trade_date < cutoff_date))
    deleted_rows = int(getattr(delete_result, "rowcount", 0) or 0)
    await session.commit()
    return {
        "retention_trading_days": safe_days,
        "cutoff_date": cutoff_date.isoformat(),
        "summarized_groups": summarized,
        "deleted_rows": deleted_rows,
        "message": "已汇总并清理超过保留窗口的 ETF 盘中明细。",
    }

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
