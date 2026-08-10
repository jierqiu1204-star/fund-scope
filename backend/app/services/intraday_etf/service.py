from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Any, cast

import akshare as ak
import httpx
from sqlalchemy import delete, exists, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.market_data import RELIABILITY_STALE, quote_reliability_from_consensus
from app.models.entities import (
    EtfIntradayCleanupCheckpoint,
    EtfIntradayDailySummary,
    EtfIntradayLatestQuote,
    EtfIntradayQuote,
    EtfIntradayQuoteEvidenceRef,
    IntradayEtfWatchRun,
    TradableEtf,
    utcnow,
)
from app.schemas.etf_quotes import (
    EtfIntradayQuoteOut,
    IntradayEtfWatchItemOut,
    IntradayEtfWatchRunOut,
    IntradayEtfWatchStatusOut,
)
from app.services.intraday_etf.exchange_calendar import (
    ASIA_SHANGHAI,
    localize_exchange_time,
    market_session,
    next_poll_seconds,
)

QUOTE_SOURCE_AKSHARE = "akshare"
QUOTE_SOURCE_EASTMONEY = "eastmoney"
QUOTE_FRESH_SECONDS = 180
QUOTE_PROVIDER_TIMEOUT_SECONDS = 4.0
AKSHARE_PROVIDER_TIMEOUT_SECONDS = 30.0
EASTMONEY_PROVIDER_TIMEOUT_SECONDS = 8.0
EASTMONEY_PROVIDER_TOTAL_TIMEOUT_SECONDS = 20.0
QUOTE_PRICE_DIFF_PCT_TOLERANCE = 0.003
QUOTE_PRICE_DIFF_ABS_TOLERANCE = 0.003
PREMIUM_DIFF_BPS_TOLERANCE = 30.0
PREMIUM_CONSENSUS_MULTI_PROVIDER_SCORE = 100
PREMIUM_CONSENSUS_SINGLE_PROVIDER_SCORE = 60
EASTMONEY_PAGE_SIZE = 5000
EASTMONEY_MAX_PAGES = 30
EASTMONEY_RETRY_ATTEMPTS = 3
EASTMONEY_REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
WATCH_REFRESH_SECONDS = 60
PAGE_POLL_SECONDS = 30
TOP_SIGNAL_LIMIT = 20
LIVE_RANKING_LIMIT_DEFAULT = 50
LIVE_RANKING_LIMIT_MAX = 200
INTRADAY_CLEANUP_BATCH_SIZE = 2_000
INTRADAY_CLEANUP_BATCH_SIZE_MAX = 5_000
INTRADAY_CLEANUP_GROUP_SCAN_LIMIT = 64
INTRADAY_CLEANUP_PROTECTED_ROW_ALLOWANCE = 1_000
INTRADAY_CLEANUP_ADVISORY_LOCK_KEY = 2_026_081_001
POSTGRES_DISTINCT_ON_MIN_CODES = 100
SOURCE_ALL_ETF = "all_etf"
_PROVIDER_FAILURE_COUNT = 0
_PROVIDER_BACKOFF_UNTIL: datetime | None = None
CONSENSUS_CONSISTENT = "consistent"
CONSENSUS_SINGLE_PROVIDER = "single_provider"
CONSENSUS_DIVERGED = "diverged"
CONSENSUS_STALE = "stale"
CONSENSUS_UNAVAILABLE = "unavailable"
DISPLAY_ONLY_CONSENSUS = {CONSENSUS_DIVERGED, CONSENSUS_STALE, CONSENSUS_UNAVAILABLE}
QuoteRow = EtfIntradayQuote | EtfIntradayLatestQuote


@dataclass(frozen=True)
class MarketState:
    status: str
    session: str | None
    now: datetime
    next_poll_seconds: int = 0


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


@dataclass(frozen=True)
class ProviderQuoteResult:
    provider: str
    quotes: dict[str, NormalizedQuote]
    error: str | None = None
    elapsed_ms: int | None = None


@dataclass(frozen=True)
class EastmoneyEtfSpotRows:
    rows: tuple[dict[str, Any], ...]
    expected_total: int | None
    error: str | None
    elapsed_ms: int

    @property
    def complete(self) -> bool:
        return bool(
            self.error is None
            and self.expected_total is not None
            and self.expected_total > 0
            and len(self.rows) == self.expected_total
        )


@dataclass(frozen=True)
class SpotQuoteFetchResult:
    quotes: dict[str, NormalizedQuote]
    provider_results: list[ProviderQuoteResult]
    consensus_counts: dict[str, int]


def current_market_state(now: datetime | None = None) -> MarketState:
    local_now = localize_exchange_time(now)
    status, session = market_session(local_now)
    return MarketState(status, session, local_now, next_poll_seconds(local_now))


def is_quote_stale(quote_time: datetime | None, now: datetime | None = None) -> bool:
    if quote_time is None:
        return True
    effective_now = localize_exchange_time(now)
    effective_quote_time = localize_exchange_time(quote_time)
    if effective_quote_time.date() != effective_now.date():
        return True
    if market_session(effective_now)[0] == "lunch_break":
        return False
    return effective_now - effective_quote_time > timedelta(seconds=QUOTE_FRESH_SECONDS)


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


def _quote_raw(quote: QuoteRow | None) -> dict[str, Any]:
    if quote is None:
        return {}
    raw = quote.raw_json or {}
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}
    return dict(raw) if isinstance(raw, dict) else {}


def is_quote_time_fallback(quote: QuoteRow | None) -> bool:
    return bool(quote is not None and _quote_raw(quote).get("quote_time_is_fallback"))


def quote_consensus_status(quote: QuoteRow | None) -> str:
    if quote is None:
        return CONSENSUS_UNAVAILABLE
    return str(_quote_raw(quote).get("consensus_status") or CONSENSUS_SINGLE_PROVIDER)


def quote_reliability(quote: QuoteRow | None) -> str:
    return quote_reliability_from_consensus(quote_consensus_status(quote))


def quote_decision_eligible_flag(quote: QuoteRow | None) -> bool:
    if quote is None:
        return False
    raw = _quote_raw(quote)
    value = raw.get("decision_eligible")
    return True if value is None else bool(value)


def quote_provider_count(quote: QuoteRow | None) -> int:
    raw = _quote_raw(quote)
    value = raw.get("provider_count")
    return int(value) if isinstance(value, int | float) else (1 if quote is not None else 0)


def quote_fresh_provider_count(quote: QuoteRow | None) -> int:
    raw = _quote_raw(quote)
    value = raw.get("fresh_provider_count")
    return int(value) if isinstance(value, int | float) else (1 if quote is not None and not is_quote_time_fallback(quote) else 0)


def quote_price_diff_abs(quote: QuoteRow | None) -> float | None:
    return _float_or_none(_quote_raw(quote).get("price_diff_abs"))


def quote_price_diff_pct(quote: QuoteRow | None) -> float | None:
    return _float_or_none(_quote_raw(quote).get("price_diff_pct"))


def quote_decision_limitation_reason(quote: QuoteRow | None, now: datetime | None = None) -> str | None:
    if quote is None:
        return "暂无盘中行情。"
    raw = _quote_raw(quote)
    reason = raw.get("decision_ineligible_reason")
    if reason:
        return str(reason)
    if is_quote_time_fallback(quote):
        return "行情时间来自服务器兜底，只能网页参考。"
    if is_quote_stale(quote.quote_time, now):
        return "盘中行情已滞后，只能网页参考。"
    status = quote_consensus_status(quote)
    if status == CONSENSUS_DIVERGED:
        return "多行情源价格分歧，只能网页参考。"
    if status == CONSENSUS_STALE:
        return "多行情源没有新鲜可决策报价。"
    if status == CONSENSUS_UNAVAILABLE:
        return "多行情源当前不可用。"
    return None


def is_fresh_decision_quote(quote: QuoteRow | None, now: datetime | None = None) -> bool:
    if quote is None:
        return False
    return bool(
        quote_decision_eligible_flag(quote)
        and quote_consensus_status(quote) not in DISPLAY_ONLY_CONSENSUS
        and not is_quote_time_fallback(quote)
        and not is_quote_stale(quote.quote_time, now)
    )


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


def normalize_spot_record(record: dict[str, Any], *, fallback_time: datetime | None = None, source: str = QUOTE_SOURCE_AKSHARE) -> NormalizedQuote | None:
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
        iopv=_number(record, "IOPV", "IOPV实时估值", "iopv"),
        premium_discount_pct=_number(
            record,
            "基金折价率",
            "折价率",
            "溢价率",
            "折溢价率",
            "premium_discount_pct",
        ),
        source=source,
        raw=raw,
    )


def _elapsed_ms(started: datetime) -> int:
    return max(0, int((datetime.now(ASIA_SHANGHAI) - started).total_seconds() * 1000))


def _quote_field_completeness(quote: NormalizedQuote) -> int:
    fields = (
        quote.change_percent,
        quote.volume,
        quote.turnover,
        quote.bid_price,
        quote.ask_price,
        quote.iopv,
        quote.premium_discount_pct,
    )
    return sum(1 for value in fields if value is not None)


def _normalized_quote_summary(quote: NormalizedQuote) -> dict[str, Any]:
    premium_discount_bps = _quote_premium_discount_bps(quote)
    return {
        "provider": quote.source,
        "quote_time": quote.quote_time.isoformat(),
        "latest_price": quote.latest_price,
        "change_percent": quote.change_percent,
        "volume": quote.volume,
        "turnover": quote.turnover,
        "bid_price": quote.bid_price,
        "ask_price": quote.ask_price,
        "iopv": quote.iopv,
        "premium_discount_pct": quote.premium_discount_pct,
        "premium_discount_bps": premium_discount_bps,
        "quote_time_is_fallback": bool(quote.raw.get("quote_time_is_fallback")),
        "field_completeness": _quote_field_completeness(quote),
    }


def _quote_premium_discount_bps(quote: NormalizedQuote) -> float | None:
    if quote.iopv is None or not isfinite(quote.iopv) or quote.iopv <= 0:
        return None
    premium_bps = (quote.latest_price / quote.iopv - 1.0) * 10_000
    return premium_bps if isfinite(premium_bps) else None


def _premium_consensus(
    fresh_quotes: list[NormalizedQuote],
    *,
    price_consensus_status: str,
) -> tuple[str, int | None, int, float | None, str | None]:
    premium_by_provider = {
        quote.source: premium_bps
        for quote in fresh_quotes
        if (premium_bps := _quote_premium_discount_bps(quote)) is not None
    }
    provider_count = len(premium_by_provider)
    if price_consensus_status == CONSENSUS_DIVERGED:
        return CONSENSUS_DIVERGED, None, provider_count, None, "price_provider_diverged"
    if not fresh_quotes:
        return CONSENSUS_STALE, None, 0, None, "no_fresh_provider_quote"
    if provider_count == 0:
        return CONSENSUS_UNAVAILABLE, None, 0, None, "missing_provider_iopv"
    if provider_count == 1:
        return (
            CONSENSUS_SINGLE_PROVIDER,
            PREMIUM_CONSENSUS_SINGLE_PROVIDER_SCORE,
            1,
            None,
            None,
        )
    values = list(premium_by_provider.values())
    dispersion_bps = max(values) - min(values)
    if dispersion_bps > PREMIUM_DIFF_BPS_TOLERANCE:
        return CONSENSUS_DIVERGED, None, provider_count, dispersion_bps, "premium_provider_diverged"
    return (
        CONSENSUS_CONSISTENT,
        PREMIUM_CONSENSUS_MULTI_PROVIDER_SCORE,
        provider_count,
        dispersion_bps,
        None,
    )


def _provider_status_summary(result: ProviderQuoteResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "quote_count": len(result.quotes),
        "status": "failed" if result.error else "success",
        "error": result.error,
        "elapsed_ms": result.elapsed_ms,
    }


def _provider_quote_sort_key(quote: NormalizedQuote) -> tuple[int, datetime, int]:
    fallback_penalty = 0 if quote.raw.get("quote_time_is_fallback") else 1
    return (fallback_penalty, quote.quote_time, _quote_field_completeness(quote))


def _consensus_counts() -> dict[str, int]:
    return {
        CONSENSUS_CONSISTENT: 0,
        CONSENSUS_SINGLE_PROVIDER: 0,
        CONSENSUS_DIVERGED: 0,
        CONSENSUS_STALE: 0,
        CONSENSUS_UNAVAILABLE: 0,
    }


def select_consensus_quotes(
    provider_results: list[ProviderQuoteResult],
    *,
    now: datetime | None = None,
) -> SpotQuoteFetchResult:
    local_now = (now or datetime.now(ASIA_SHANGHAI)).replace(tzinfo=None)
    counts = _consensus_counts()
    by_code: dict[str, list[NormalizedQuote]] = {}
    provider_status = [_provider_status_summary(result) for result in provider_results]
    for result in provider_results:
        for code, quote in result.quotes.items():
            by_code.setdefault(code, []).append(quote)

    selected: dict[str, NormalizedQuote] = {}
    for code, quotes in by_code.items():
        valid_quotes = [quote for quote in quotes if quote.latest_price > 0]
        if not valid_quotes:
            counts[CONSENSUS_UNAVAILABLE] += 1
            continue
        fresh_quotes = [
            quote
            for quote in valid_quotes
            if not quote.raw.get("quote_time_is_fallback") and not is_quote_stale(quote.quote_time, local_now)
        ]
        chosen = max(fresh_quotes or valid_quotes, key=_provider_quote_sort_key)
        price_values = [quote.latest_price for quote in fresh_quotes]
        price_diff_abs = max(price_values) - min(price_values) if len(price_values) >= 2 else None
        price_diff_pct = (price_diff_abs / min(price_values) * 100) if price_diff_abs is not None and min(price_values) > 0 else None
        reason: str | None = None
        if not fresh_quotes:
            status = CONSENSUS_STALE
            decision_eligible = False
            reason = "多行情源没有新鲜且带真实时间的 ETF 盘中行情，只能网页参考。"
        elif len(fresh_quotes) == 1:
            status = CONSENSUS_SINGLE_PROVIDER
            decision_eligible = True
        else:
            diverged = bool(
                price_diff_abs is not None
                and price_diff_pct is not None
                and (
                    price_diff_abs > QUOTE_PRICE_DIFF_ABS_TOLERANCE
                    or price_diff_pct / 100 > QUOTE_PRICE_DIFF_PCT_TOLERANCE
                )
            )
            status = CONSENSUS_DIVERGED if diverged else CONSENSUS_CONSISTENT
            decision_eligible = not diverged
            if diverged:
                reason = f"多行情源价格分歧：最大价差约 {price_diff_abs:.4f}，占 {price_diff_pct:.2f}%，仅网页参考。"
        counts[status] += 1
        (
            premium_consensus_status,
            premium_provider_consensus,
            premium_provider_count,
            premium_dispersion_bps,
            premium_consensus_reason,
        ) = _premium_consensus(fresh_quotes, price_consensus_status=status)
        provider_quotes = [_normalized_quote_summary(quote) for quote in valid_quotes]
        raw = dict(chosen.raw)
        raw.update(
            {
                "selected_source": chosen.source,
                "primary_provider": chosen.source,
                "provider_count": len(valid_quotes),
                "fresh_provider_count": len(fresh_quotes),
                "provider_quotes": provider_quotes,
                "provider_status": provider_status,
                "consensus_status": status,
                "price_diff_abs": round(price_diff_abs, 6) if price_diff_abs is not None else None,
                "price_diff_pct": round(price_diff_pct, 4) if price_diff_pct is not None else None,
                "premium_consensus_status": premium_consensus_status,
                "premium_provider_consensus": premium_provider_consensus,
                "premium_provider_count": premium_provider_count,
                "premium_dispersion_bps": (
                    round(premium_dispersion_bps, 6) if premium_dispersion_bps is not None else None
                ),
                "premium_consensus_reason": premium_consensus_reason,
                "decision_eligible": decision_eligible,
                "decision_ineligible_reason": reason,
            }
        )
        selected[code] = replace(chosen, raw=raw)
    return SpotQuoteFetchResult(selected, provider_results, counts)


async def _fetch_akshare_provider(fetcher: Any | None = None) -> ProviderQuoteResult:
    global _PROVIDER_BACKOFF_UNTIL, _PROVIDER_FAILURE_COUNT
    started = datetime.now(ASIA_SHANGHAI)
    now = started
    use_default_provider = fetcher is None
    if use_default_provider and _PROVIDER_BACKOFF_UNTIL is not None:
        if now < _PROVIDER_BACKOFF_UNTIL:
            wait_seconds = max(1, int((_PROVIDER_BACKOFF_UNTIL - now).total_seconds()) + 1)
            return ProviderQuoteResult(
                QUOTE_SOURCE_AKSHARE,
                {},
                f"ETF 行情源正在退避，约 {wait_seconds} 秒后再试。",
                _elapsed_ms(started),
            )
        _PROVIDER_BACKOFF_UNTIL = None
    effective_fetcher = fetcher or ak.fund_etf_spot_em
    try:
        frame = await asyncio.wait_for(asyncio.to_thread(effective_fetcher), timeout=AKSHARE_PROVIDER_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        error_detail = str(exc) or exc.__class__.__name__
        if use_default_provider:
            _PROVIDER_FAILURE_COUNT += 1
            backoff_seconds = min(300, 30 * (2 ** min(_PROVIDER_FAILURE_COUNT - 1, 4)))
            _PROVIDER_BACKOFF_UNTIL = now + timedelta(seconds=backoff_seconds)
            error = f"AKShare ETF 行情源请求失败，已退避 {backoff_seconds} 秒。原始错误：{error_detail}"
        else:
            error = f"AKShare ETF 行情源请求失败：{error_detail}"
        return ProviderQuoteResult(QUOTE_SOURCE_AKSHARE, {}, error, _elapsed_ms(started))
    if use_default_provider:
        _PROVIDER_FAILURE_COUNT = 0
        _PROVIDER_BACKOFF_UNTIL = None
    quotes: dict[str, NormalizedQuote] = {}
    for _, row in frame.iterrows():
        quote = normalize_spot_record(dict(row), fallback_time=now, source=QUOTE_SOURCE_AKSHARE)
        if quote is not None:
            quotes[quote.etf_code] = quote
    return ProviderQuoteResult(QUOTE_SOURCE_AKSHARE, quotes, elapsed_ms=_elapsed_ms(started))


def _eastmoney_quote_time(value: Any) -> datetime | None:
    raw = _float_or_none(value)
    if raw is None or raw <= 0:
        return None
    return datetime.fromtimestamp(int(raw), tz=ASIA_SHANGHAI).replace(tzinfo=None)


async def fetch_eastmoney_etf_spot_rows() -> EastmoneyEtfSpotRows:
    started = datetime.now(ASIA_SHANGHAI)
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    base_params = {
        "pn": "1",
        "pz": str(EASTMONEY_PAGE_SIZE),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
        "fields": "f12,f13,f14,f2,f3,f5,f6,f124,f26",
    }
    rows: list[dict[str, Any]] = []
    expected_total: int | None = None

    async def fetch_all_pages() -> None:
        nonlocal expected_total
        timeout = httpx.Timeout(EASTMONEY_PROVIDER_TIMEOUT_SECONDS, connect=QUOTE_PROVIDER_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(
            timeout=timeout,
            headers=EASTMONEY_REQUEST_HEADERS,
            follow_redirects=True,
        ) as client:
            for page in range(1, EASTMONEY_MAX_PAGES + 1):
                params = dict(base_params)
                params["pn"] = str(page)
                response = None
                for attempt in range(EASTMONEY_RETRY_ATTEMPTS):
                    try:
                        response = await client.get(url, params=params)
                        response.raise_for_status()
                        break
                    except Exception:
                        if attempt == EASTMONEY_RETRY_ATTEMPTS - 1:
                            raise
                if response is None:
                    break
                payload = response.json()
                data = ((payload or {}).get("data") or {})
                page_rows = data.get("diff") or []
                if not isinstance(page_rows, list) or not page_rows:
                    break
                rows.extend(row for row in page_rows if isinstance(row, dict))
                if expected_total is None:
                    total_raw = _float_or_none(data.get("total"))
                    expected_total = int(total_raw) if total_raw is not None and total_raw > 0 else None
                if expected_total is None or len(rows) >= expected_total:
                    break

    try:
        async with asyncio.timeout(EASTMONEY_PROVIDER_TOTAL_TIMEOUT_SECONDS):
            await fetch_all_pages()
    except TimeoutError:
        return EastmoneyEtfSpotRows(
            rows=tuple(rows),
            expected_total=expected_total,
            error=f"Eastmoney ETF spot request timeout after {EASTMONEY_PROVIDER_TOTAL_TIMEOUT_SECONDS:g}s",
            elapsed_ms=_elapsed_ms(started),
        )
    except Exception as exc:  # noqa: BLE001
        return EastmoneyEtfSpotRows(
            rows=tuple(rows),
            expected_total=expected_total,
            error=f"东方财富 ETF 行情源请求失败：{exc}",
            elapsed_ms=_elapsed_ms(started),
        )
    return EastmoneyEtfSpotRows(
        rows=tuple(rows),
        expected_total=expected_total,
        error=None,
        elapsed_ms=_elapsed_ms(started),
    )


async def _fetch_eastmoney_provider() -> ProviderQuoteResult:
    result = await fetch_eastmoney_etf_spot_rows()
    if result.error is not None and not result.rows:
        return ProviderQuoteResult(QUOTE_SOURCE_EASTMONEY, {}, result.error, result.elapsed_ms)
    quotes: dict[str, NormalizedQuote] = {}
    now = datetime.now(ASIA_SHANGHAI)
    for row in result.rows:
        quote_time = _eastmoney_quote_time(row.get("f124"))
        record = {
            "code": row.get("f12"),
            "name": row.get("f14"),
            "latest_price": row.get("f2"),
            "change_percent": row.get("f3"),
            "volume": row.get("f5"),
            "turnover": row.get("f6"),
            "provider_timestamp": row.get("f124"),
        }
        if quote_time is not None:
            record["quote_time"] = quote_time.isoformat(sep=" ")
        quote = normalize_spot_record(record, fallback_time=now, source=QUOTE_SOURCE_EASTMONEY)
        if quote is not None:
            quotes[quote.etf_code] = quote
    error = result.error
    if error is None and not result.complete:
        error = (
            "东方财富 ETF 行情源返回不完整："
            f"expected={result.expected_total}, received={len(result.rows)}"
        )
    return ProviderQuoteResult(QUOTE_SOURCE_EASTMONEY, quotes, error, result.elapsed_ms)

async def fetch_spot_quotes_with_metadata(fetcher: Any | None = None) -> SpotQuoteFetchResult:
    if fetcher is not None:
        results = [await _fetch_akshare_provider(fetcher)]
    else:
        results = list(await asyncio.gather(_fetch_akshare_provider(), _fetch_eastmoney_provider()))
    return select_consensus_quotes(results)


async def fetch_spot_quotes(fetcher: Any | None = None) -> dict[str, NormalizedQuote]:
    return (await fetch_spot_quotes_with_metadata(fetcher)).quotes


async def latest_intraday_quote(session: AsyncSession, etf_code: str) -> QuoteRow | None:
    latest = await session.get(EtfIntradayLatestQuote, etf_code)
    if latest is not None:
        return latest
    return cast(
        EtfIntradayQuote | None,
        await session.scalar(
            select(EtfIntradayQuote)
            .where(EtfIntradayQuote.etf_code == etf_code)
            .order_by(EtfIntradayQuote.quote_time.desc(), EtfIntradayQuote.id.desc())
        ),
    )


def _postgres_distinct_on_historical_quotes_stmt(
    codes: list[str],
    *,
    trade_date: date,
    decision_cutoff: datetime | None,
    captured_cutoff: datetime | None,
) -> Any:
    filters: list[Any] = [
        EtfIntradayQuote.etf_code.in_(codes),
        EtfIntradayQuote.trade_date == trade_date,
    ]
    if decision_cutoff is not None:
        filters.append(EtfIntradayQuote.quote_time <= decision_cutoff)
    if captured_cutoff is not None:
        filters.append(EtfIntradayQuote.created_at <= captured_cutoff)
    return (
        select(EtfIntradayQuote)
        .where(*filters)
        .distinct(EtfIntradayQuote.etf_code)
        .order_by(
            EtfIntradayQuote.etf_code,
            EtfIntradayQuote.quote_time.desc(),
            EtfIntradayQuote.id.desc(),
        )
    )


async def _latest_historical_quotes_by_code(
    session: AsyncSession,
    codes: list[str],
    *,
    trade_date: date | None = None,
    decision_cutoff: datetime | None = None,
    captured_cutoff: datetime | None = None,
) -> dict[str, EtfIntradayQuote]:
    unique_codes = list(dict.fromkeys(code for code in codes if code))
    if not unique_codes:
        return {}

    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        if (
            trade_date is not None
            and len(unique_codes) >= POSTGRES_DISTINCT_ON_MIN_CODES
        ):
            rows = (
                await session.scalars(
                    _postgres_distinct_on_historical_quotes_stmt(
                        unique_codes,
                        trade_date=trade_date,
                        decision_cutoff=decision_cutoff,
                        captured_cutoff=captured_cutoff,
                    )
                )
            ).all()
            return {row.etf_code: row for row in rows}

        values_sql = ", ".join(f"(:code_{index})" for index in range(len(unique_codes)))
        params: dict[str, Any] = {f"code_{index}": code for index, code in enumerate(unique_codes)}
        cutoff_filters = ""
        if trade_date is not None:
            cutoff_filters += " AND q.trade_date = :trade_date"
            params["trade_date"] = trade_date
        if decision_cutoff is not None:
            cutoff_filters += " AND q.quote_time <= :decision_cutoff"
            params["decision_cutoff"] = decision_cutoff
        if captured_cutoff is not None:
            cutoff_filters += " AND q.created_at <= :captured_cutoff"
            params["captured_cutoff"] = captured_cutoff
        stmt = text(
            f"""
            WITH watch_codes(etf_code) AS (VALUES {values_sql})
            SELECT q.*
            FROM watch_codes w
            JOIN LATERAL (
                SELECT *
                FROM etf_intraday_quotes q
                WHERE q.etf_code = w.etf_code
                  {cutoff_filters}
                ORDER BY q.quote_time DESC, q.id DESC
                LIMIT 1
            ) q ON TRUE
            """
        )
        rows = (await session.scalars(select(EtfIntradayQuote).from_statement(stmt).params(**params))).all()
        return {row.etf_code: row for row in rows}

    filters: list[Any] = [EtfIntradayQuote.etf_code.in_(unique_codes)]
    if trade_date is not None:
        filters.append(EtfIntradayQuote.trade_date == trade_date)
    if decision_cutoff is not None:
        filters.append(EtfIntradayQuote.quote_time <= decision_cutoff)
    if captured_cutoff is not None:
        filters.append(EtfIntradayQuote.created_at <= captured_cutoff)
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
        .where(*filters)
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


async def quotes_at_or_before_cutoff(
    session: AsyncSession,
    codes: list[str],
    *,
    trade_date: date,
    decision_cutoff: datetime,
) -> dict[str, EtfIntradayQuote]:
    market_cutoff = (
        decision_cutoff.replace(tzinfo=None)
        if decision_cutoff.tzinfo is None
        else decision_cutoff.astimezone(ASIA_SHANGHAI).replace(tzinfo=None)
    )
    captured_cutoff = (
        decision_cutoff.replace(tzinfo=ASIA_SHANGHAI)
        if decision_cutoff.tzinfo is None
        else decision_cutoff.astimezone(ASIA_SHANGHAI)
    ).astimezone(UTC).replace(tzinfo=None)
    return await _latest_historical_quotes_by_code(
        session,
        codes,
        trade_date=trade_date,
        decision_cutoff=market_cutoff,
        captured_cutoff=captured_cutoff,
    )


async def latest_quotes_by_code(
    session: AsyncSession,
    codes: list[str],
) -> dict[str, EtfIntradayLatestQuote | EtfIntradayQuote]:
    unique_codes = list(dict.fromkeys(code for code in codes if code))
    if not unique_codes:
        return {}

    latest_rows = (
        await session.scalars(
            select(EtfIntradayLatestQuote).where(EtfIntradayLatestQuote.etf_code.in_(unique_codes))
        )
    ).all()
    result: dict[str, EtfIntradayLatestQuote | EtfIntradayQuote] = {row.etf_code: row for row in latest_rows}
    missing_codes = [code for code in unique_codes if code not in result]
    if missing_codes:
        result.update(await _latest_historical_quotes_by_code(session, missing_codes))
    return result


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
    eligible_codes = (
        await session.scalars(
            select(TradableEtf.code)
            .where(TradableEtf.is_short_term_eligible.is_(True))
            .order_by(TradableEtf.code.asc())
        )
    ).all()
    watch_map: dict[str, WatchItem] = {
        code: WatchItem(etf_code=code, sources={SOURCE_ALL_ETF})
        for code in eligible_codes
    }
    items = sorted(watch_map.values(), key=lambda item: (item.rank is None, item.rank or 9999, item.etf_code))
    return WatchlistResult(
        items,
        None,
        None,
        "all_etf",
        "使用全部可交易 ETF 盘中行情；研究筛选和用户持仓由上层服务编排。",
    )


async def quote_name_map(session: AsyncSession, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    rows = await session.scalars(select(TradableEtf).where(TradableEtf.code.in_(codes)))
    return {row.code: row.name for row in rows.all()}


def quote_out(
    quote: QuoteRow,
    *,
    etf_name: str | None = None,
    now: datetime | None = None,
) -> EtfIntradayQuoteOut:
    stale = is_quote_stale(quote.quote_time, now) or is_quote_time_fallback(quote)
    decision_eligible = is_fresh_decision_quote(quote, now)
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
        consensus_status=quote_consensus_status(quote),
        quote_reliability=RELIABILITY_STALE if stale else quote_reliability(quote),
        decision_eligible=decision_eligible,
        provider_count=quote_provider_count(quote),
        fresh_provider_count=quote_fresh_provider_count(quote),
        price_diff_abs=quote_price_diff_abs(quote),
        price_diff_pct=quote_price_diff_pct(quote),
        limitation_reason=None if decision_eligible else quote_decision_limitation_reason(quote, now),
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


_QUOTE_DB_FIELDS = (
    "etf_code",
    "quote_time",
    "trade_date",
    "latest_price",
    "change_percent",
    "volume",
    "turnover",
    "bid_price",
    "ask_price",
    "iopv",
    "premium_discount_pct",
    "source",
    "freshness_status",
    "raw_json",
)


def _quote_freshness_status(quote: NormalizedQuote) -> str:
    if quote.raw.get("consensus_status") == CONSENSUS_STALE:
        return "stale"
    return "fresh" if quote.raw.get("decision_eligible", True) else "display_only"


def _quote_record(quote: NormalizedQuote) -> dict[str, Any]:
    return {
        "etf_code": quote.etf_code,
        "quote_time": quote.quote_time,
        "trade_date": quote.trade_date,
        "latest_price": quote.latest_price,
        "change_percent": quote.change_percent,
        "volume": quote.volume,
        "turnover": quote.turnover,
        "bid_price": quote.bid_price,
        "ask_price": quote.ask_price,
        "iopv": quote.iopv,
        "premium_discount_pct": quote.premium_discount_pct,
        "source": quote.source,
        "freshness_status": _quote_freshness_status(quote),
        "raw_json": quote.raw,
    }


def _apply_quote_record(row: EtfIntradayQuote | EtfIntradayLatestQuote, record: dict[str, Any]) -> None:
    for field_name in _QUOTE_DB_FIELDS:
        setattr(row, field_name, record[field_name])


async def _persist_quotes_postgresql(session: AsyncSession, records: list[dict[str, Any]]) -> None:
    now = utcnow()
    history_rows = [{**_postgres_quote_record(record), "created_at": now} for record in records]
    latest_rows = [{**_postgres_quote_record(record), "created_at": now, "updated_at": now} for record in records]

    history_insert = pg_insert(EtfIntradayQuote).values(history_rows)
    await session.execute(
        history_insert.on_conflict_do_nothing(
            index_elements=["etf_code", "quote_time"],
        )
    )

    latest_insert = pg_insert(EtfIntradayLatestQuote).values(latest_rows)
    latest_update = {
        field_name: getattr(latest_insert.excluded, field_name)
        for field_name in _QUOTE_DB_FIELDS
        if field_name != "etf_code"
    }
    latest_update["updated_at"] = now
    await session.execute(
        latest_insert.on_conflict_do_update(
            index_elements=["etf_code"],
            set_=latest_update,
            where=latest_insert.excluded.quote_time > EtfIntradayLatestQuote.quote_time,
        )
    )


def _postgres_quote_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "raw_json": json.dumps(record["raw_json"], ensure_ascii=False, separators=(",", ":")),
    }


async def _persist_quotes_row_by_row(session: AsyncSession, records: list[dict[str, Any]]) -> None:
    for record in records:
        existing = await session.scalar(
            select(EtfIntradayQuote).where(
                EtfIntradayQuote.etf_code == record["etf_code"],
                EtfIntradayQuote.quote_time == record["quote_time"],
            )
        )
        if existing is None:
            session.add(EtfIntradayQuote(**record))

        latest = await session.get(EtfIntradayLatestQuote, record["etf_code"])
        if latest is None:
            session.add(EtfIntradayLatestQuote(**record))
        elif record["quote_time"] > latest.quote_time:
            _apply_quote_record(latest, record)


async def persist_quotes(
    session: AsyncSession,
    watchlist: WatchlistResult,
    quotes: dict[str, NormalizedQuote],
) -> int:
    records: list[dict[str, Any]] = []
    for item in watchlist.items:
        quote = quotes.get(item.etf_code)
        if quote is not None:
            records.append(_quote_record(quote))
    if not records:
        await session.commit()
        return 0

    if session.get_bind().dialect.name == "postgresql":
        await _persist_quotes_postgresql(session, records)
    else:
        await _persist_quotes_row_by_row(session, records)
    await session.commit()
    return len(records)


async def summarize_and_cleanup_intraday_quotes(
    session: AsyncSession,
    *,
    retention_trading_days: int = 60,
    batch_size: int = INTRADAY_CLEANUP_BATCH_SIZE,
    evidence_seal_complete: bool = False,
) -> dict[str, Any]:
    safe_days = max(1, retention_trading_days)
    safe_batch_size = max(1, min(INTRADAY_CLEANUP_BATCH_SIZE_MAX, batch_size))
    trade_dates = (
        await session.scalars(
            select(EtfIntradayQuote.trade_date)
            .distinct()
            .order_by(EtfIntradayQuote.trade_date.desc())
            .limit(safe_days + 1)
        )
    ).all()
    if len(trade_dates) <= safe_days:
        return {
            "retention_trading_days": safe_days,
            "cutoff_date": None,
            "summarized_groups": 0,
            "deleted_rows": 0,
            "batch_size": safe_batch_size,
            "message": "盘中明细未超过保留窗口，无需清理。",
        }

    cutoff_date = trade_dates[safe_days - 1]
    if not evidence_seal_complete:
        return {
            "job_status": "partial",
            "job_message": "盘中决策证据尚未完成封存，清理保持关闭。",
            "retention_trading_days": safe_days,
            "cutoff_date": cutoff_date.isoformat(),
            "summarized_groups": 0,
            "deleted_rows": 0,
            "batch_size": safe_batch_size,
            "unavailable_reason": "intraday_quote_evidence_seal_incomplete",
            "message": "盘中决策证据尚未完成封存，未删除任何明细。",
        }

    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET LOCAL statement_timeout = '45s'"))
        await session.execute(text("SET LOCAL lock_timeout = '2s'"))
        lock_acquired = bool(
            await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_key)").bindparams(
                    lock_key=INTRADAY_CLEANUP_ADVISORY_LOCK_KEY
                )
            )
        )
        if not lock_acquired:
            await session.rollback()
            return {
                "job_status": "partial",
                "job_message": "另一个盘中明细清理切片正在运行。",
                "retention_trading_days": safe_days,
                "cutoff_date": cutoff_date.isoformat(),
                "summarized_groups": 0,
                "deleted_rows": 0,
                "batch_size": safe_batch_size,
                "unavailable_reason": "intraday_cleanup_lock_busy",
                "message": "清理锁被占用，本次安全跳过。",
            }

    protected_ref_exists = exists(
        select(EtfIntradayQuoteEvidenceRef.id).where(
            EtfIntradayQuoteEvidenceRef.quote_id == EtfIntradayQuote.id
        )
    )
    candidate_groups = (
        await session.execute(
            select(
                EtfIntradayQuote.etf_code.label("etf_code"),
                EtfIntradayQuote.trade_date.label("trade_date"),
                func.count(EtfIntradayQuote.id).label("unprotected_count"),
            )
            .where(
                EtfIntradayQuote.trade_date < cutoff_date,
                ~protected_ref_exists,
            )
            .group_by(EtfIntradayQuote.etf_code, EtfIntradayQuote.trade_date)
            .order_by(
                EtfIntradayQuote.trade_date.asc(),
                EtfIntradayQuote.etf_code.asc(),
            )
            .limit(INTRADAY_CLEANUP_GROUP_SCAN_LIMIT)
        )
    ).all()
    if not candidate_groups:
        checkpoint = await session.get(EtfIntradayCleanupCheckpoint, 1)
        if checkpoint is None:
            checkpoint = EtfIntradayCleanupCheckpoint(id=1)
            session.add(checkpoint)
        checkpoint.cutoff_date = cutoff_date
        checkpoint.status = "complete"
        checkpoint.details_json = {
            "retention_trading_days": safe_days,
            "batch_size": safe_batch_size,
            "message": "no_unprotected_rows_before_cutoff",
        }
        checkpoint.updated_at = utcnow()
        await session.commit()
        return {
            "retention_trading_days": safe_days,
            "cutoff_date": cutoff_date.isoformat(),
            "summarized_groups": 0,
            "deleted_rows": 0,
            "batch_size": safe_batch_size,
            "message": "超过保留窗口的明细均受证据保护，无可清理行。",
        }

    selected_groups: list[tuple[str, date]] = []
    selected_unprotected_count = 0
    for row in candidate_groups:
        group_count = int(row.unprotected_count or 0)
        if group_count <= 0:
            continue
        if group_count > safe_batch_size and not selected_groups:
            await session.rollback()
            return {
                "job_status": "partial",
                "job_message": "最旧 ETF 日内分组超过单批上限。",
                "retention_trading_days": safe_days,
                "cutoff_date": cutoff_date.isoformat(),
                "summarized_groups": 0,
                "deleted_rows": 0,
                "batch_size": safe_batch_size,
                "oversized_group": {
                    "asset_code": row.etf_code,
                    "trade_date": row.trade_date.isoformat(),
                    "row_count": group_count,
                },
                "message": "发现异常超大日内分组，本次未删除。",
            }
        if selected_unprotected_count + group_count > safe_batch_size:
            break
        selected_groups.append((row.etf_code, row.trade_date))
        selected_unprotected_count += group_count

    group_filter = tuple_(
        EtfIntradayQuote.etf_code,
        EtfIntradayQuote.trade_date,
    ).in_(selected_groups)
    quote_rows = (
        await session.execute(
            select(
                EtfIntradayQuote.id,
                EtfIntradayQuote.etf_code,
                EtfIntradayQuote.trade_date,
                EtfIntradayQuote.quote_time,
                EtfIntradayQuote.latest_price,
                EtfIntradayQuote.volume,
                EtfIntradayQuote.turnover,
                EtfIntradayQuote.source,
            )
            .where(group_filter)
            .order_by(
                EtfIntradayQuote.etf_code.asc(),
                EtfIntradayQuote.trade_date.asc(),
                EtfIntradayQuote.quote_time.asc(),
                EtfIntradayQuote.id.asc(),
            )
            .limit(safe_batch_size + INTRADAY_CLEANUP_PROTECTED_ROW_ALLOWANCE + 1)
        )
    ).all()
    if len(quote_rows) > safe_batch_size + INTRADAY_CLEANUP_PROTECTED_ROW_ALLOWANCE:
        await session.rollback()
        return {
            "job_status": "partial",
            "job_message": "受保护行使日内分组超过内存上限。",
            "retention_trading_days": safe_days,
            "cutoff_date": cutoff_date.isoformat(),
            "summarized_groups": 0,
            "deleted_rows": 0,
            "batch_size": safe_batch_size,
            "unavailable_reason": "intraday_cleanup_group_memory_bound_exceeded",
            "message": "日内分组超过内存上限，本次未删除。",
        }

    rows_by_group: dict[tuple[str, date], list[Any]] = {}
    for row in quote_rows:
        rows_by_group.setdefault((row.etf_code, row.trade_date), []).append(row)
    existing_summaries = (
        await session.scalars(
            select(EtfIntradayDailySummary).where(
                tuple_(
                    EtfIntradayDailySummary.etf_code,
                    EtfIntradayDailySummary.trade_date,
                ).in_(selected_groups)
            )
        )
    ).all()
    summary_by_group = {
        (row.etf_code, row.trade_date): row for row in existing_summaries
    }
    for group_key in selected_groups:
        group_quotes = rows_by_group[group_key]
        first_quote = group_quotes[0]
        last_quote = group_quotes[-1]
        prices = [
            float(row.latest_price)
            for row in group_quotes
            if isfinite(float(row.latest_price)) and float(row.latest_price) > 0
        ]
        if len(prices) != len(group_quotes):
            await session.rollback()
            return {
                "job_status": "partial",
                "job_message": "日内分组包含非有限或非正价格。",
                "retention_trading_days": safe_days,
                "cutoff_date": cutoff_date.isoformat(),
                "summarized_groups": 0,
                "deleted_rows": 0,
                "batch_size": safe_batch_size,
                "unavailable_reason": "intraday_cleanup_invalid_price",
                "blocked_group": {
                    "asset_code": group_key[0],
                    "trade_date": group_key[1].isoformat(),
                },
                "message": "价格证据异常，本次未删除任何明细。",
            }
        volume_values = [
            float(row.volume)
            for row in group_quotes
            if row.volume is not None and isfinite(float(row.volume))
        ]
        turnover_values = [
            float(row.turnover)
            for row in group_quotes
            if row.turnover is not None and isfinite(float(row.turnover))
        ]
        existing = summary_by_group.get(group_key)
        summary = existing or EtfIntradayDailySummary(
            etf_code=group_key[0],
            trade_date=group_key[1],
        )
        summary.quote_count = len(group_quotes)
        summary.first_quote_time = first_quote.quote_time
        summary.last_quote_time = last_quote.quote_time
        summary.open_price = float(first_quote.latest_price)
        summary.close_price = float(last_quote.latest_price)
        summary.low_price = min(prices)
        summary.high_price = max(prices)
        summary.total_volume = max(volume_values) if volume_values else None
        summary.total_turnover = max(turnover_values) if turnover_values else None
        summary.source = last_quote.source
        summary.summary_json = {
            "retention_trading_days": safe_days,
            "source": "raw_intraday_cleanup",
            "display_only": True,
            "volume_semantics": "max_cumulative_snapshot",
            "turnover_semantics": "max_cumulative_snapshot",
            "decision_evidence_source": False,
        }
        if existing is None:
            session.add(summary)

    delete_result = await session.execute(
        delete(EtfIntradayQuote).where(
            EtfIntradayQuote.trade_date < cutoff_date,
            group_filter,
            ~exists(
                select(EtfIntradayQuoteEvidenceRef.id).where(
                    EtfIntradayQuoteEvidenceRef.quote_id == EtfIntradayQuote.id
                )
            ),
        )
    )
    deleted_rows = int(getattr(delete_result, "rowcount", 0) or 0)
    checkpoint = await session.get(EtfIntradayCleanupCheckpoint, 1)
    if checkpoint is None:
        checkpoint = EtfIntradayCleanupCheckpoint(id=1)
        session.add(checkpoint)
    checkpoint.cutoff_date = cutoff_date
    checkpoint.last_trade_date = selected_groups[-1][1]
    checkpoint.last_etf_code = selected_groups[-1][0]
    checkpoint.status = "advanced"
    checkpoint.deleted_rows_total = int(checkpoint.deleted_rows_total or 0) + deleted_rows
    checkpoint.summarized_groups_total = int(
        checkpoint.summarized_groups_total or 0
    ) + len(selected_groups)
    checkpoint.details_json = {
        "retention_trading_days": safe_days,
        "batch_size": safe_batch_size,
        "selected_unprotected_count": selected_unprotected_count,
        "selected_group_count": len(selected_groups),
        "protected_rows_loaded": len(quote_rows) - selected_unprotected_count,
    }
    checkpoint.updated_at = utcnow()
    await session.commit()
    return {
        "retention_trading_days": safe_days,
        "cutoff_date": cutoff_date.isoformat(),
        "summarized_groups": len(selected_groups),
        "deleted_rows": deleted_rows,
        "batch_size": safe_batch_size,
        "checkpoint": {
            "last_trade_date": selected_groups[-1][1].isoformat(),
            "last_etf_code": selected_groups[-1][0],
        },
        "message": "已完成一个有界 ETF 盘中明细清理切片。",
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
