from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite, sqrt
from statistics import mean, pstdev
from typing import Any, cast

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.etfs import DEFAULT_SHORT_ETFS
from app.models.entities import (
    EtfDataHealth,
    EtfIntradayQuote,
    EtfMetric,
    EtfPriceHistory,
    EtfThemeExposure,
    TradableEtf,
    utcnow,
)
from app.services.retry import retry_async

MIN_AVERAGE_TURNOVER = 50_000_000
CHASE_RETURN_20D = 0.25
CHASE_RETURN_60D = 0.45
SURGE_RETURN_5D = 0.08
HIGH_VOLATILITY_20D = 0.05
LARGE_DRAWDOWN_60D = -0.18
STALE_DATA_DAYS = 7
DEFAULT_SYNC_DELAY_SECONDS = 1.0
DEFAULT_PROVIDER_RETRIES = 2
DEFAULT_PROVIDER_RETRY_DELAY_SECONDS = 1.0

INELIGIBLE_NAME_KEYWORDS = ("一年持有", "持有期", "定开", "封闭", "封闭期")
PRICE_HISTORY_PROVIDER_NAMES = ("eastmoney", "efinance", "sina")
CLOSE_SNAPSHOT_MIN_TIME = time(14, 55)
RAW_PRICE_BASIS = "raw_ohlc"
TOTAL_RETURN_PRICE_BASIS = "total_return_adjusted"
EASTMONEY_HFQ_ADJUSTMENT_VERSION = "eastmoney.push2his.kline.hfq_v1"
EFINANCE_HFQ_ADJUSTMENT_VERSION = "efinance.stock.get_quote_history.fqt2_v1"
EASTMONEY_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_HISTORY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://quote.eastmoney.com/",
    "Connection": "close",
}

PriceHistoryRows = list[dict[str, float | str]]
PriceHistoryFetcher = Callable[[str, date, date], Awaitable[PriceHistoryRows]]


@dataclass(frozen=True)
class ProviderFetchResult:
    rows: list[dict[str, float | str]]
    provider: str
    fallback_used: bool
    primary_error: str | None = None


def is_short_term_eligible_name(name: str) -> bool:
    return not any(keyword in name for keyword in INELIGIBLE_NAME_KEYWORDS)


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _number(record: Any, *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return float(value)
    return default


def _optional_number(record: Any, *keys: str) -> float | None:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return float(value)
    return None


def _research_price_fields(row: dict[str, Any], *, provider: str, source_timestamp: datetime) -> dict[str, Any]:
    adjusted_value = _optional_number(row, "research_adjusted_value", "adjusted_close")
    price_basis = str(row.get("research_price_basis") or "")
    adjustment_version = str(row.get("adjustment_version") or "")
    provider_version = str(row.get("provider_version") or "")
    ineligibility_reason = None
    if adjusted_value is None or not isfinite(adjusted_value) or adjusted_value <= 0:
        ineligibility_reason = "missing_total_return_provenance"
    elif price_basis != TOTAL_RETURN_PRICE_BASIS:
        ineligibility_reason = "incompatible_research_price_basis"
    elif not adjustment_version:
        ineligibility_reason = "missing_adjustment_version"
    elif not provider_version:
        ineligibility_reason = "missing_provider_version"
    if ineligibility_reason is not None:
        return {
            "raw_price_basis": str(row.get("raw_price_basis") or RAW_PRICE_BASIS),
            "research_adjusted_value": None,
            "research_price_basis": None,
            "data_provider": provider,
            "provider_version": provider_version or None,
            "source_timestamp": source_timestamp,
            "adjustment_version": adjustment_version or None,
            "decision_eligible": False,
            "decision_ineligibility_reason": ineligibility_reason,
        }
    return {
        "raw_price_basis": str(row.get("raw_price_basis") or RAW_PRICE_BASIS),
        "research_adjusted_value": adjusted_value,
        "research_price_basis": price_basis,
        "data_provider": provider,
        "provider_version": provider_version,
        "source_timestamp": source_timestamp,
        "adjustment_version": adjustment_version,
        "decision_eligible": True,
        "decision_ineligibility_reason": None,
    }


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _sync_delay_seconds() -> float:
    return _float_env("SHORT_ETF_SYNC_DELAY_SECONDS", DEFAULT_SYNC_DELAY_SECONDS)


def _provider_retries() -> int:
    return _int_env("SHORT_ETF_PROVIDER_RETRIES", DEFAULT_PROVIDER_RETRIES)


def _provider_retry_delay_seconds() -> float:
    return _float_env("SHORT_ETF_PROVIDER_RETRY_DELAY_SECONDS", DEFAULT_PROVIDER_RETRY_DELAY_SECONDS)


def parse_etf_history_frame(frame: Any, from_date: date, to_date: date) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for _, record in frame.iterrows():
        trade_date = _parse_date(record.get("日期") or record.get("trade_date"))
        if not from_date <= trade_date <= to_date:
            continue
        rows.append(
            {
                "date": trade_date.isoformat(),
                "open": _number(record, "开盘", "open"),
                "high": _number(record, "最高", "high"),
                "low": _number(record, "最低", "low"),
                "close": _number(record, "收盘", "close"),
                "volume": _number(record, "成交量", "volume"),
                "turnover": _number(record, "成交额", "turnover"),
                "pct_change": _number(record, "涨跌幅", "pct_change"),
            }
        )
    return rows


def _attach_hfq_research_prices(
    raw_rows: PriceHistoryRows,
    hfq_rows: PriceHistoryRows,
    adjustment_version: str,
) -> PriceHistoryRows:
    adjusted_by_date = {
        str(row["date"]): adjusted_close
        for row in hfq_rows
        if (adjusted_close := _optional_number(row, "close")) is not None
        and isfinite(adjusted_close)
        and adjusted_close > 0
    }
    for row in raw_rows:
        adjusted_close = adjusted_by_date.get(str(row["date"]))
        if adjusted_close is None:
            continue
        row.update(
            {
                "research_adjusted_value": adjusted_close,
                "research_price_basis": TOTAL_RETURN_PRICE_BASIS,
                "adjustment_version": adjustment_version,
                "provider_version": adjustment_version,
            }
        )
    return raw_rows


def parse_eastmoney_history_payload(
    payload: Any,
    from_date: date,
    to_date: date,
) -> PriceHistoryRows:
    data = payload.get("data") if isinstance(payload, dict) else None
    klines = data.get("klines") if isinstance(data, dict) else None
    if not isinstance(klines, list):
        return []
    rows: PriceHistoryRows = []
    for item in klines:
        fields = str(item).split(",")
        if len(fields) < 11:
            raise ValueError("东方财富返回了不完整的 ETF 日线数据")
        trade_date = _parse_date(fields[0])
        if not from_date <= trade_date <= to_date:
            continue
        rows.append(
            {
                "date": trade_date.isoformat(),
                "open": float(fields[1]),
                "close": float(fields[2]),
                "high": float(fields[3]),
                "low": float(fields[4]),
                "volume": float(fields[5]),
                "turnover": float(fields[6]),
                "pct_change": float(fields[8]),
            }
        )
    return rows


def _eastmoney_market_id(code: str) -> int:
    return 1 if code.startswith(("5", "6")) else 0


async def fetch_eastmoney_etf_price_history(
    code: str,
    from_date: date,
    to_date: date,
) -> PriceHistoryRows:
    async def fetch_primary() -> PriceHistoryRows:
        common_params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "invt": "2",
            "klt": "101",
            "beg": from_date.strftime("%Y%m%d"),
            "end": to_date.strftime("%Y%m%d"),
            "secid": f"{_eastmoney_market_id(code)}.{code}",
        }
        async with httpx.AsyncClient(timeout=20, headers=EASTMONEY_HISTORY_HEADERS) as client:
            raw_response = await client.get(
                EASTMONEY_HISTORY_URL,
                params={**common_params, "fqt": "0"},
            )
            raw_response.raise_for_status()
            hfq_response = await client.get(
                EASTMONEY_HISTORY_URL,
                params={**common_params, "fqt": "2"},
            )
            hfq_response.raise_for_status()
        return _attach_hfq_research_prices(
            parse_eastmoney_history_payload(raw_response.json(), from_date, to_date),
            parse_eastmoney_history_payload(hfq_response.json(), from_date, to_date),
            EASTMONEY_HFQ_ADJUSTMENT_VERSION,
        )

    return await retry_async(
        "fetch_eastmoney_etf_price_history",
        fetch_primary,
        retries=_provider_retries(),
        base_delay=_provider_retry_delay_seconds(),
    )


def _sina_symbol(code: str) -> str:
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def parse_sina_history_payload(payload: str, from_date: date, to_date: date) -> list[dict[str, float | str]]:
    start = payload.find("[")
    end = payload.rfind("]")
    if start < 0 or end < start:
        raise ValueError("新浪财经未返回可解析的 ETF 日线数据")
    raw_rows = json.loads(payload[start : end + 1])
    parsed_rows = sorted(raw_rows, key=lambda record: _parse_date(record.get("day") or record.get("date")))
    rows: list[dict[str, float | str]] = []
    previous_close: float | None = None
    for record in parsed_rows:
        trade_date = _parse_date(record.get("day") or record.get("date"))
        close = _number(record, "close", "收盘")
        pct_change = _optional_number(record, "pct_change", "percent", "涨跌幅")
        if pct_change is None:
            pct_change = close / previous_close * 100 - 100 if previous_close else 0.0
        previous_close = close
        if not from_date <= trade_date <= to_date:
            continue
        volume = _number(record, "volume", "成交量")
        turnover = _optional_number(record, "amount", "turnover", "成交额")
        rows.append(
            {
                "date": trade_date.isoformat(),
                "open": _number(record, "open", "开盘"),
                "high": _number(record, "high", "最高"),
                "low": _number(record, "low", "最低"),
                "close": close,
                "volume": volume,
                "turnover": turnover if turnover is not None else volume * close,
                "pct_change": pct_change,
            }
        )
    return rows


async def fetch_efinance_etf_price_history(code: str, from_date: date, to_date: date) -> list[dict[str, float | str]]:
    async def fetch_backup() -> list[dict[str, float | str]]:
        import efinance as ef  # type: ignore[import-untyped]

        common_kwargs = {
            "beg": from_date.strftime("%Y%m%d"),
            "end": to_date.strftime("%Y%m%d"),
            "suppress_error": False,
        }
        raw_frame = await asyncio.to_thread(
            ef.stock.get_quote_history,
            code,
            **common_kwargs,
            fqt=0,
        )
        hfq_frame = await asyncio.to_thread(
            ef.stock.get_quote_history,
            code,
            **common_kwargs,
            fqt=2,
        )
        return _attach_hfq_research_prices(
            parse_etf_history_frame(raw_frame, from_date, to_date),
            parse_etf_history_frame(hfq_frame, from_date, to_date),
            EFINANCE_HFQ_ADJUSTMENT_VERSION,
        )

    return await retry_async(
        "fetch_efinance_etf_price_history",
        fetch_backup,
        retries=_provider_retries(),
        base_delay=_provider_retry_delay_seconds(),
    )


async def fetch_sina_etf_price_history(code: str, from_date: date, to_date: date) -> list[dict[str, float | str]]:
    async def fetch_backup() -> list[dict[str, float | str]]:
        async with httpx.AsyncClient(
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                ),
                "Referer": "https://finance.sina.com.cn/",
            },
        ) as client:
            response = await client.get(
                "https://quotes.sina.cn/cn/api/jsonp.php/=/CN_MarketDataService.getKLineData",
                params={"symbol": _sina_symbol(code), "scale": 240, "ma": "no", "datalen": 1023},
            )
            response.raise_for_status()
        return parse_sina_history_payload(response.text, from_date, to_date)

    return await retry_async(
        "fetch_sina_etf_price_history",
        fetch_backup,
        retries=_provider_retries(),
        base_delay=_provider_retry_delay_seconds(),
    )


async def fetch_etf_price_history(code: str, from_date: date, to_date: date) -> list[dict[str, float | str]]:
    return (await fetch_etf_price_history_with_provider(code, from_date, to_date)).rows


async def fetch_etf_price_history_with_provider(
    code: str,
    from_date: date,
    to_date: date,
) -> ProviderFetchResult:
    provider_errors: list[str] = []
    for index, (provider, label, fetcher) in enumerate(_price_history_provider_sequence()):
        try:
            rows = await fetcher(code, from_date, to_date)
            if not rows:
                raise ValueError(f"{label} 未返回可用 ETF 日线数据")
            return ProviderFetchResult(
                rows=rows,
                provider=provider,
                fallback_used=index > 0,
                primary_error=provider_errors[0] if provider_errors else None,
            )
        except Exception as exc:  # noqa: BLE001
            provider_errors.append(f"{label}: {exc}")
    raise RuntimeError("; ".join(provider_errors))


def _price_history_provider_sequence() -> list[tuple[str, str, PriceHistoryFetcher]]:
    return [
        ("eastmoney", "东方财富", fetch_eastmoney_etf_price_history),
        ("efinance", "efinance", fetch_efinance_etf_price_history),
        ("sina", "新浪财经", fetch_sina_etf_price_history),
    ]


async def ensure_default_etf_universe(session: AsyncSession) -> int:
    created = 0
    for item in DEFAULT_SHORT_ETFS:
        existing = await session.scalar(select(TradableEtf).where(TradableEtf.code == item.code))
        eligible = is_short_term_eligible_name(item.name)
        if existing is None:
            session.add(
                TradableEtf(
                    code=item.code,
                    name=item.name,
                    exchange=item.exchange,
                    theme_tags_json=list(item.theme_tags),
                    trading_rule_label=item.trading_rule_label,
                    asset_class=item.asset_class,
                    is_short_term_eligible=eligible,
                    is_watchlist=True,
                )
            )
            created += 1
        else:
            existing.name = item.name
            existing.exchange = item.exchange
            existing.theme_tags_json = list(item.theme_tags)
            existing.trading_rule_label = item.trading_rule_label
            existing.asset_class = item.asset_class
            existing.is_short_term_eligible = eligible
            existing.is_watchlist = True
        for theme in item.theme_tags:
            exposure = await session.scalar(
                select(EtfThemeExposure).where(
                    EtfThemeExposure.etf_code == item.code,
                    EtfThemeExposure.theme == theme,
                )
            )
            if exposure is None:
                session.add(EtfThemeExposure(etf_code=item.code, theme=theme, weight=1.0, source="default"))
    await session.commit()
    return created


async def list_short_etfs(session: AsyncSession, codes: list[str] | None = None) -> list[TradableEtf]:
    await ensure_default_etf_universe(session)
    query = select(TradableEtf).where(
        TradableEtf.is_watchlist.is_(True),
        TradableEtf.is_short_term_eligible.is_(True),
    )
    if codes:
        query = query.where(TradableEtf.code.in_(codes))
    rows = await session.scalars(query.order_by(TradableEtf.code.asc()))
    return list(rows.all())


def _return(prices: list[EtfPriceHistory], lookback: int) -> float | None:
    if len(prices) <= lookback:
        return None
    start = prices[-lookback - 1].close
    end = prices[-1].close
    return end / start - 1 if start else None


def _max_drawdown(prices: list[EtfPriceHistory]) -> float | None:
    if not prices:
        return None
    peak = prices[0].close
    worst = 0.0
    for item in prices:
        peak = max(peak, item.close)
        if peak:
            worst = min(worst, item.close / peak - 1)
    return worst


def _trend_score(return_5d: float | None, return_20d: float | None, return_60d: float | None) -> float:
    weighted = (
        max(min(return_5d or 0.0, 0.12), -0.12) * 180
        + max(min(return_20d or 0.0, 0.30), -0.30) * 130
        + max(min(return_60d or 0.0, 0.60), -0.60) * 70
    )
    return round(max(0.0, min(100.0, 50.0 + weighted)), 2)


def _liquidity_score(average_turnover: float | None) -> float:
    if average_turnover is None:
        return 0.0
    return round(max(0.0, min(100.0, average_turnover / 1_000_000)), 2)


def _risk_score(flags: list[str]) -> float:
    score = 100.0
    if "追高风险" in flags:
        score -= 35
    if "连续大涨" in flags:
        score -= 25
    if "流动性不足" in flags:
        score -= 40
    if "高波动" in flags:
        score -= 20
    if "回撤较大" in flags:
        score -= 20
    if "数据不足" in flags:
        score -= 30
    if "数据滞后" in flags:
        score -= 30
    return round(max(0.0, score), 2)


async def compute_etf_metric(session: AsyncSession, code: str, as_of_date: date) -> EtfMetric | None:
    prices = (
        await session.scalars(
            select(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == code, EtfPriceHistory.trade_date <= as_of_date)
            .order_by(EtfPriceHistory.trade_date.asc())
        )
    ).all()
    if len(prices) < 6:
        return None
    price_list = list(prices)
    return_5d = _return(price_list, 5)
    return_20d = _return(price_list, 20)
    return_60d = _return(price_list, 60)
    last_20 = price_list[-20:]
    last_60 = price_list[-60:]
    average_turnover_20d = mean([item.turnover for item in last_20]) if last_20 else None
    pct_changes = [item.pct_change / 100 for item in last_20]
    volatility_20d = pstdev(pct_changes) * sqrt(252) if len(pct_changes) > 1 else None
    max_drawdown_60d = _max_drawdown(last_60)

    risk_flags: list[str] = []
    if len(price_list) < 61:
        risk_flags.append("数据不足")
    if (return_20d or 0) > CHASE_RETURN_20D or (return_60d or 0) > CHASE_RETURN_60D:
        risk_flags.append("追高风险")
    if (return_5d or 0) > SURGE_RETURN_5D:
        risk_flags.append("连续大涨")
    if average_turnover_20d is None or average_turnover_20d < MIN_AVERAGE_TURNOVER:
        risk_flags.append("流动性不足")
    if volatility_20d is not None and volatility_20d > HIGH_VOLATILITY_20D:
        risk_flags.append("高波动")
    if max_drawdown_60d is not None and max_drawdown_60d < LARGE_DRAWDOWN_60D:
        risk_flags.append("回撤较大")

    metric = await session.scalar(
        select(EtfMetric).where(EtfMetric.etf_code == code, EtfMetric.metric_date == as_of_date)
    )
    if metric is None:
        metric = EtfMetric(etf_code=code, metric_date=as_of_date)
        session.add(metric)
    metric.return_5d = return_5d
    metric.return_20d = return_20d
    metric.return_60d = return_60d
    metric.average_turnover_20d = average_turnover_20d
    metric.volatility_20d = volatility_20d
    metric.max_drawdown_60d = max_drawdown_60d
    metric.trend_score = _trend_score(return_5d, return_20d, return_60d)
    metric.liquidity_score = _liquidity_score(average_turnover_20d)
    metric.risk_score = _risk_score(risk_flags)
    metric.risk_flags_json = risk_flags
    await session.commit()
    await session.refresh(metric)
    return metric


async def upsert_etf_data_health_success(
    session: AsyncSession,
    *,
    etf_code: str,
    provider: str,
    latest_price_date: date | None,
    row_count: int,
    fallback_error: str | None = None,
) -> EtfDataHealth:
    now = utcnow()
    health = await session.scalar(select(EtfDataHealth).where(EtfDataHealth.etf_code == etf_code))
    if health is None:
        health = EtfDataHealth(etf_code=etf_code)
        session.add(health)
    health.status = "success"
    health.provider = provider
    health.latest_price_date = latest_price_date
    health.successful_rows = row_count
    health.last_error_message = fallback_error
    health.consecutive_failures = 0
    health.last_attempted_at = now
    health.last_success_at = now
    health.updated_at = now
    await session.flush()
    return health


async def upsert_etf_data_health_failure(
    session: AsyncSession,
    *,
    etf_code: str,
    error_message: str,
) -> EtfDataHealth:
    now = utcnow()
    health = await session.scalar(select(EtfDataHealth).where(EtfDataHealth.etf_code == etf_code))
    if health is None:
        health = EtfDataHealth(etf_code=etf_code, successful_rows=0, consecutive_failures=0)
        session.add(health)
    health.status = "failed"
    health.last_error_message = error_message
    health.consecutive_failures = int(health.consecutive_failures or 0) + 1
    health.last_attempted_at = now
    health.updated_at = now
    await session.flush()
    return health


async def latest_etf_price(session: AsyncSession, code: str, on_or_before: date) -> EtfPriceHistory | None:
    return cast(
        EtfPriceHistory | None,
        await session.scalar(
            select(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == code, EtfPriceHistory.trade_date <= on_or_before)
            .order_by(EtfPriceHistory.trade_date.desc())
        ),
    )


async def latest_price_date(session: AsyncSession) -> date | None:
    return cast(date | None, await session.scalar(select(func.max(EtfPriceHistory.trade_date))))


async def sync_etf_price_history(
    session: AsyncSession,
    from_date: date,
    to_date: date,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    etfs = await list_short_etfs(session, codes)
    inserted = 0
    updated = 0
    failures: list[dict[str, str]] = []
    fallback_used = 0
    provider_counts: dict[str, int] = {provider: 0 for provider in PRICE_HISTORY_PROVIDER_NAMES}
    sync_delay = _sync_delay_seconds()
    for index, etf in enumerate(etfs):
        try:
            result = await fetch_etf_price_history_with_provider(etf.code, from_date, to_date)
            rows = result.rows
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": etf.code, "error": str(exc)})
            await upsert_etf_data_health_failure(session, etf_code=etf.code, error_message=str(exc))
            await session.commit()
        else:
            provider_counts[result.provider] = provider_counts.get(result.provider, 0) + 1
            if result.fallback_used:
                fallback_used += 1
            source_timestamp = utcnow()
            for row in rows:
                trade_date = date.fromisoformat(str(row["date"]))
                values = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "turnover": float(row["turnover"]),
                    "pct_change": float(row["pct_change"]),
                    **_research_price_fields(row, provider=result.provider, source_timestamp=source_timestamp),
                }
                existing = await session.scalar(
                    select(EtfPriceHistory).where(
                        EtfPriceHistory.etf_code == etf.code,
                        EtfPriceHistory.trade_date == trade_date,
                    )
                )
                if existing is None:
                    session.add(
                        EtfPriceHistory(
                            etf_code=etf.code,
                            trade_date=trade_date,
                            **values,
                        )
                    )
                    inserted += 1
                elif existing.decision_eligible is True and values["decision_eligible"] is not True:
                    continue
                else:
                    for field, value in values.items():
                        setattr(existing, field, value)
                    updated += 1
            latest_row_date = max((date.fromisoformat(str(row["date"])) for row in rows), default=None)
            await upsert_etf_data_health_success(
                session,
                etf_code=etf.code,
                provider=result.provider,
                latest_price_date=latest_row_date,
                row_count=len(rows),
                fallback_error=result.primary_error,
            )
            await session.commit()
            await compute_etf_metric(session, etf.code, to_date)
        if sync_delay > 0 and index < len(etfs) - 1:
            await asyncio.sleep(sync_delay)
    return {
        "etfs": len(etfs),
        "inserted": inserted,
        "updated": updated,
        "failed": len(failures),
        "fallback_used": fallback_used,
        "provider_counts": provider_counts,
        "sync_delay_seconds": sync_delay,
        "failures": failures,
    }

async def sync_etf_price_history_from_intraday_snapshot(
    session: AsyncSession,
    *,
    trade_date: date,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    etfs = await list_short_etfs(session, codes)
    target_codes = [etf.code for etf in etfs]
    if not target_codes:
        return {
            "etfs": 0,
            "inserted": 0,
            "updated": 0,
            "missing": 0,
            "skipped_too_early": 0,
            "quote_rows": 0,
            "needs_history_provider": False,
        }

    quote_rows = (
        await session.scalars(
            select(EtfIntradayQuote)
            .where(
                EtfIntradayQuote.trade_date == trade_date,
                EtfIntradayQuote.etf_code.in_(target_codes),
            )
            .order_by(
                EtfIntradayQuote.etf_code.asc(),
                EtfIntradayQuote.quote_time.asc(),
                EtfIntradayQuote.id.asc(),
            )
        )
    ).all()
    grouped: dict[str, list[EtfIntradayQuote]] = {}
    for quote in quote_rows:
        grouped.setdefault(quote.etf_code, []).append(quote)

    inserted = 0
    updated = 0
    missing = 0
    skipped_too_early = 0
    skipped_display_only = 0
    skipped_codes: list[str] = []
    for code in target_codes:
        quotes = grouped.get(code)
        if not quotes:
            missing += 1
            skipped_codes.append(code)
            continue

        skipped_display_only += 1
        skipped_codes.append(code)

    await session.commit()

    return {
        "etfs": len(target_codes),
        "inserted": inserted,
        "updated": updated,
        "missing": missing,
        "skipped_too_early": skipped_too_early,
        "skipped_display_only": skipped_display_only,
        "skipped_codes": skipped_codes[:50],
        "quote_rows": len(quote_rows),
        "provider": "intraday_snapshot",
        "needs_history_provider": bool(missing or skipped_too_early or skipped_display_only),
    }

async def list_etf_data_health(session: AsyncSession) -> list[tuple[TradableEtf, EtfDataHealth | None, bool]]:
    etfs = await list_short_etfs(session)
    today = date.today()
    rows: list[tuple[TradableEtf, EtfDataHealth | None, bool]] = []
    for etf in etfs:
        health = await session.scalar(select(EtfDataHealth).where(EtfDataHealth.etf_code == etf.code))
        latest_price = await latest_etf_price(session, etf.code, date.max)
        latest_date = health.latest_price_date if health else None
        if latest_date is None and latest_price is not None:
            latest_date = latest_price.trade_date
        is_stale = latest_date is None or (today - latest_date).days > STALE_DATA_DAYS
        rows.append((etf, health, is_stale))
    return rows


async def data_status_summary(session: AsyncSession) -> dict[str, Any]:
    rows = await list_etf_data_health(session)
    latest_dates = [
        health.latest_price_date
        for _, health, _ in rows
        if health is not None and health.latest_price_date is not None
    ]
    return {
        "etfs": len(rows),
        "healthy": sum(1 for _, health, stale in rows if health is not None and health.status == "success" and not stale),
        "failed": sum(1 for _, health, _ in rows if health is not None and health.status == "failed"),
        "stale": sum(1 for _, _, stale in rows if stale),
        "fallback_used": sum(
            1
            for _, health, _ in rows
            if health is not None and health.provider is not None and health.provider != "eastmoney"
        ),
        "latest_price_date": max(latest_dates).isoformat() if latest_dates else None,
    }


async def retry_failed_or_stale_etf_data(session: AsyncSession, days: int = 90) -> dict[str, Any]:
    rows = await list_etf_data_health(session)
    codes = [
        etf.code
        for etf, health, stale in rows
        if health is not None and (stale or health.status == "failed")
    ]
    if not codes:
        return {
            "retried": 0,
            "etfs": 0,
            "inserted": 0,
            "updated": 0,
            "failed": 0,
            "fallback_used": 0,
            "provider_counts": {provider: 0 for provider in PRICE_HISTORY_PROVIDER_NAMES},
            "sync_delay_seconds": _sync_delay_seconds(),
            "failures": [],
        }
    today = date.today()
    result = await sync_etf_price_history(session, today - timedelta(days=days), today, codes)
    return {"retried": len(codes), **result}
