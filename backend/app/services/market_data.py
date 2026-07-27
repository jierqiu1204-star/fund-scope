from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfIntradayLatestQuote,
    EtfIntradayQuote,
    EtfPriceHistory,
    IntradayEtfWatchRun,
)
from app.models.entities import (
    EtfPointInTimeMembershipFact as EtfPointInTimeMembershipFactRow,
)
from app.schemas.etf_quotes import EtfIntradayQuoteOut, IntradayEtfWatchRunOut
from app.services.intraday_etf import service as intraday_quotes
from app.services.intraday_etf.exchange_calendar import is_trading_day

EtfQuoteRow = EtfIntradayLatestQuote | EtfIntradayQuote
ASIA_SHANGHAI = intraday_quotes.ASIA_SHANGHAI

_DECISION_ADJUSTED_PROVIDER_VERSIONS = {
    "eastmoney": "eastmoney.push2his.kline.hfq_v1",
    "efinance": "efinance.stock.get_quote_history.fqt2_v1",
    "tickflow": "tickflow.free.klines.backward_v1",
    "tencent": "tencent.ifzq.fqkline.hfq_v1",
}


def etf_decision_adjusted_provider_versions() -> tuple[tuple[str, str], ...]:
    """Return the immutable provider/version pairs accepted for ETF decisions."""

    return tuple(sorted(_DECISION_ADJUSTED_PROVIDER_VERSIONS.items()))


class MarketDataReadLimitExceededError(ValueError):
    pass


def etf_adjusted_price_provenance_issue(
    *,
    adjusted_value: Any,
    price_basis: str | None,
    data_provider: str | None,
    provider_version: str | None,
    source_timestamp: datetime | None,
    adjustment_version: str | None,
    data_cutoff: datetime,
) -> str | None:
    """Return the fail-closed reason that excludes an adjusted daily fact."""

    if price_basis != "total_return_adjusted":
        return "incompatible_research_price_basis"
    if (
        isinstance(adjusted_value, bool)
        or not isinstance(adjusted_value, int | float)
        or not math.isfinite(float(adjusted_value))
        or float(adjusted_value) <= 0
    ):
        return "invalid_research_adjusted_value"
    provider = str(data_provider or "").strip().lower()
    expected_version = _DECISION_ADJUSTED_PROVIDER_VERSIONS.get(provider)
    if (
        expected_version is None
        or provider_version != expected_version
        or adjustment_version != expected_version
    ):
        return "unsupported_adjusted_provider"
    if source_timestamp is None:
        return "missing_adjusted_price_provenance"
    cutoff_local = (
        data_cutoff.replace(tzinfo=ASIA_SHANGHAI)
        if data_cutoff.tzinfo is None
        else data_cutoff.astimezone(ASIA_SHANGHAI)
    )
    cutoff_utc = cutoff_local.astimezone(UTC).replace(tzinfo=None)
    source_utc = (
        source_timestamp
        if source_timestamp.tzinfo is None
        else source_timestamp.astimezone(UTC).replace(tzinfo=None)
    )
    return "source_after_data_cutoff" if source_utc > cutoff_utc else None


@dataclass(frozen=True)
class EtfPointInTimeMembershipFact:
    etf_code: str
    effective_from: date
    effective_to: date | None
    membership_state: str
    external_source_id: str
    provider: str
    provider_version: str
    observed_at: datetime
    evidence_hash: str
    raw_payload_hash: str
    fact_hash: str
    created_at: datetime


@dataclass(frozen=True)
class EtfAdjustedDailyFact:
    etf_code: str
    trade_date: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    volume: float
    raw_price_basis: str | None
    adjusted_close: float | None
    research_price_basis: str | None
    data_provider: str | None
    provider_version: str | None
    source_timestamp: datetime | None
    adjustment_version: str | None
    decision_eligible: bool | None
    decision_ineligibility_reason: str | None


@dataclass(frozen=True)
class EtfAdjustedSourceWatermark:
    row_count: int
    max_row_id: int | None
    max_source_timestamp: datetime | None


def is_etf_exchange_trading_day(value: date) -> bool:
    return is_trading_day(value)


async def etf_membership_facts_covering(
    session: AsyncSession,
    *,
    replay_date: date,
) -> tuple[EtfPointInTimeMembershipFact, ...]:
    """Return effective membership facts without consulting today's ETF flags."""

    rows = (
        await session.scalars(
            select(EtfPointInTimeMembershipFactRow)
            .where(
                EtfPointInTimeMembershipFactRow.effective_from <= replay_date,
                or_(
                    EtfPointInTimeMembershipFactRow.effective_to.is_(None),
                    EtfPointInTimeMembershipFactRow.effective_to >= replay_date,
                ),
            )
            .order_by(
                EtfPointInTimeMembershipFactRow.etf_code.asc(),
                EtfPointInTimeMembershipFactRow.effective_from.asc(),
                EtfPointInTimeMembershipFactRow.observed_at.asc(),
                EtfPointInTimeMembershipFactRow.id.asc(),
            )
        )
    ).all()
    return tuple(
        EtfPointInTimeMembershipFact(
            etf_code=row.etf_code,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            membership_state=row.membership_state,
            external_source_id=row.external_source_id,
            provider=row.provider,
            provider_version=row.provider_version,
            observed_at=row.observed_at,
            evidence_hash=row.evidence_hash,
            raw_payload_hash=row.raw_payload_hash,
            fact_hash=row.fact_hash,
            created_at=row.created_at,
        )
        for row in rows
    )


async def etf_adjusted_daily_facts_on_or_before(
    session: AsyncSession,
    *,
    etf_codes: tuple[str, ...],
    replay_date: date,
    rows_per_code: int,
    max_source_rows: int,
) -> tuple[EtfAdjustedDailyFact, ...]:
    """Read a deterministically ordered, cutoff-bounded daily input page."""

    if rows_per_code < 1 or max_source_rows < 1:
        raise ValueError("rows_per_code and max_source_rows must be positive")
    codes = tuple(sorted(set(etf_codes)))
    if not codes:
        return ()
    if len(codes) * rows_per_code > max_source_rows:
        raise MarketDataReadLimitExceededError(
            "ETF point-in-time code page exceeds the bounded source-row budget"
        )

    columns = (
        EtfPriceHistory.etf_code.label("etf_code"),
        EtfPriceHistory.trade_date.label("trade_date"),
        EtfPriceHistory.open.label("raw_open"),
        EtfPriceHistory.high.label("raw_high"),
        EtfPriceHistory.low.label("raw_low"),
        EtfPriceHistory.close.label("raw_close"),
        EtfPriceHistory.volume.label("volume"),
        EtfPriceHistory.raw_price_basis.label("raw_price_basis"),
        EtfPriceHistory.research_adjusted_value.label("adjusted_close"),
        EtfPriceHistory.research_price_basis.label("research_price_basis"),
        EtfPriceHistory.data_provider.label("data_provider"),
        EtfPriceHistory.provider_version.label("provider_version"),
        EtfPriceHistory.source_timestamp.label("source_timestamp"),
        EtfPriceHistory.adjustment_version.label("adjustment_version"),
        EtfPriceHistory.decision_eligible.label("decision_eligible"),
        EtfPriceHistory.decision_ineligibility_reason.label(
            "decision_ineligibility_reason"
        ),
    )
    rows: list[Any] = []
    for code in codes:
        result = await session.execute(
            select(*columns)
            .where(
                EtfPriceHistory.etf_code == code,
                EtfPriceHistory.trade_date <= replay_date,
            )
            .order_by(EtfPriceHistory.trade_date.desc(), EtfPriceHistory.id.desc())
            .limit(rows_per_code)
        )
        rows.extend(reversed(result.mappings().all()))
    return tuple(
        EtfAdjustedDailyFact(
            etf_code=row["etf_code"],
            trade_date=row["trade_date"],
            raw_open=row["raw_open"],
            raw_high=row["raw_high"],
            raw_low=row["raw_low"],
            raw_close=row["raw_close"],
            volume=row["volume"],
            raw_price_basis=row["raw_price_basis"],
            adjusted_close=row["adjusted_close"],
            research_price_basis=row["research_price_basis"],
            data_provider=row["data_provider"],
            provider_version=row["provider_version"],
            source_timestamp=row["source_timestamp"],
            adjustment_version=row["adjustment_version"],
            decision_eligible=row["decision_eligible"],
            decision_ineligibility_reason=row["decision_ineligibility_reason"],
        )
        for row in rows
    )


async def etf_adjusted_source_watermark(
    session: AsyncSession,
    *,
    etf_codes: tuple[str, ...],
    replay_date: date,
) -> EtfAdjustedSourceWatermark:
    """Return a cheap revision watermark without loading or sorting history rows."""

    codes = tuple(sorted(set(etf_codes)))
    if not codes:
        return EtfAdjustedSourceWatermark(0, None, None)
    row = (
        await session.execute(
            select(
                func.count(EtfPriceHistory.id),
                func.max(EtfPriceHistory.id),
                func.max(EtfPriceHistory.source_timestamp),
            ).where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date <= replay_date,
            )
        )
    ).one()
    return EtfAdjustedSourceWatermark(
        row_count=int(row[0] or 0),
        max_row_id=int(row[1]) if row[1] is not None else None,
        max_source_timestamp=row[2],
    )


async def latest_etf_quotes_by_code(
    session: AsyncSession,
    codes: list[str],
) -> dict[str, EtfQuoteRow]:
    return await intraday_quotes.latest_quotes_by_code(session, codes)


async def etf_quotes_at_decision_cutoff(
    session: AsyncSession,
    codes: list[str],
    *,
    trade_date: date,
    decision_cutoff: datetime,
) -> dict[str, EtfIntradayQuote]:
    return await intraday_quotes.quotes_at_or_before_cutoff(
        session,
        codes,
        trade_date=trade_date,
        decision_cutoff=decision_cutoff,
    )


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
