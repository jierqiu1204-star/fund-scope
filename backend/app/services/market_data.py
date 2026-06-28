from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfIntradayLatestQuote, EtfIntradayQuote, IntradayEtfWatchRun
from app.schemas.etf_quotes import EtfIntradayQuoteOut, IntradayEtfWatchRunOut
from app.services.intraday_etf import service as intraday_quotes

EtfQuoteRow = EtfIntradayLatestQuote | EtfIntradayQuote
ASIA_SHANGHAI = intraday_quotes.ASIA_SHANGHAI


async def latest_etf_quotes_by_code(
    session: AsyncSession,
    codes: list[str],
) -> dict[str, EtfQuoteRow]:
    return await intraday_quotes.latest_quotes_by_code(session, codes)


async def latest_intraday_etf_quote(session: AsyncSession, code: str) -> EtfQuoteRow | None:
    return await intraday_quotes.latest_intraday_quote(session, code)


async def latest_etf_watch_run(session: AsyncSession) -> IntradayEtfWatchRun | None:
    return await intraday_quotes.latest_watch_run(session)


async def etf_quote_name_map(session: AsyncSession, codes: list[str]) -> dict[str, str]:
    return await intraday_quotes.quote_name_map(session, codes)


def etf_quote_out(
    quote: EtfQuoteRow,
    *,
    etf_name: str | None = None,
    now: datetime | None = None,
) -> EtfIntradayQuoteOut:
    return intraday_quotes.quote_out(quote, etf_name=etf_name, now=now)


def etf_watch_run_out(row: IntradayEtfWatchRun) -> IntradayEtfWatchRunOut:
    return intraday_quotes.watch_run_out(row)


def is_fresh_etf_decision_quote(
    quote: EtfQuoteRow | None,
    now: datetime | None = None,
) -> bool:
    return intraday_quotes.is_fresh_decision_quote(quote, now)


def is_etf_quote_stale(quote_time: datetime | None, now: datetime | None = None) -> bool:
    return intraday_quotes.is_quote_stale(quote_time, now)


def is_etf_quote_time_fallback(quote: EtfQuoteRow | None) -> bool:
    return intraday_quotes.is_quote_time_fallback(quote)


def etf_quote_consensus_status(quote: EtfQuoteRow | None) -> str:
    return intraday_quotes.quote_consensus_status(quote)


def etf_quote_decision_limitation_reason(
    quote: EtfQuoteRow | None,
    now: datetime | None = None,
) -> str | None:
    return intraday_quotes.quote_decision_limitation_reason(quote, now)


def etf_quote_fresh_provider_count(quote: EtfQuoteRow | None) -> int:
    return intraday_quotes.quote_fresh_provider_count(quote)


def etf_quote_price_diff_abs(quote: EtfQuoteRow | None) -> float | None:
    return intraday_quotes.quote_price_diff_abs(quote)


def etf_quote_price_diff_pct(quote: EtfQuoteRow | None) -> float | None:
    return intraday_quotes.quote_price_diff_pct(quote)


def etf_quote_provider_count(quote: EtfQuoteRow | None) -> int:
    return intraday_quotes.quote_provider_count(quote)


def etf_quote_reliability(quote: EtfQuoteRow | None) -> str:
    return intraday_quotes.quote_reliability(quote)
