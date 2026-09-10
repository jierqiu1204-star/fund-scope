from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfAdjustedPriceRevision,
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
from app.services.intraday_etf.exchange_calendar import (
    ExchangeCalendarUnavailableError as ExchangeCalendarUnavailableError,
)
from app.services.intraday_etf.exchange_calendar import (
    is_trading_day,
    next_trading_day,
)

EtfQuoteRow = EtfIntradayLatestQuote | EtfIntradayQuote
ASIA_SHANGHAI = intraday_quotes.ASIA_SHANGHAI

_DECISION_ADJUSTED_PROVIDER_VERSIONS = {
    "eastmoney": "eastmoney.push2his.kline.hfq_v1",
    "efinance": "efinance.stock.get_quote_history.fqt2_v1",
    "tencent": "tencent.ifzq.fqkline.hfq_turnover_yuan_v2",
    "tickflow": "tickflow.free.klines.backward_v1",
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
    turnover: float | None = None
    revision_hash: str | None = None
    first_seen_at: datetime | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True)
class EtfAdjustedSourceWatermark:
    row_count: int
    max_row_id: int | None
    max_source_timestamp: datetime | None


def is_etf_exchange_trading_day(value: date) -> bool:
    return is_trading_day(value)


def next_etf_exchange_trading_day(value: date) -> date:
    # Check the source year too: December in an unknown year must not jump into
    # the next year's supported calendar and masquerade as a known horizon.
    next_trading_day(date(value.year, 1, 1))
    return next_trading_day(value)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _compatible_adjusted_provider_pairs(
    pairs: tuple[tuple[str, str], ...] | None,
) -> tuple[tuple[str, str], ...]:
    source = pairs or etf_decision_adjusted_provider_versions()
    return tuple(
        sorted(
            {
                (str(provider).strip().lower(), str(version).strip())
                for provider, version in source
                if str(provider).strip() and str(version).strip()
            }
        )
    )


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
    decision_cutoff: datetime | None = None,
    compatible_provider_versions: tuple[tuple[str, str], ...] | None = None,
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
        EtfPriceHistory.turnover.label("turnover"),
        EtfPriceHistory.raw_price_basis.label("raw_price_basis"),
        EtfPriceHistory.research_adjusted_value.label("adjusted_close"),
        EtfPriceHistory.research_price_basis.label("research_price_basis"),
        EtfPriceHistory.data_provider.label("data_provider"),
        EtfPriceHistory.provider_version.label("provider_version"),
        EtfPriceHistory.source_timestamp.label("source_timestamp"),
        EtfPriceHistory.adjustment_version.label("adjustment_version"),
        EtfPriceHistory.decision_eligible.label("decision_eligible"),
        EtfPriceHistory.decision_ineligibility_reason.label("decision_ineligibility_reason"),
    )
    rows: list[Any] = []
    cutoff = _utc_naive(decision_cutoff) if decision_cutoff is not None else None
    accepted_pairs = _compatible_adjusted_provider_pairs(compatible_provider_versions)
    if cutoff is None:
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
    elif accepted_pairs:
        accepted_providers = tuple(sorted({provider for provider, _version in accepted_pairs}))
        provider_compatibility = func.lower(EtfAdjustedPriceRevision.data_provider).in_(
            accepted_providers
        )
        pair_compatibility = or_(
            *(
                and_(
                    func.lower(EtfAdjustedPriceRevision.data_provider) == provider,
                    EtfAdjustedPriceRevision.provider_version == version,
                    EtfAdjustedPriceRevision.adjustment_version == version,
                )
                for provider, version in accepted_pairs
            )
        )
        visible_revisions = (
            select(
                EtfAdjustedPriceRevision.etf_code.label("etf_code"),
                EtfAdjustedPriceRevision.trade_date.label("trade_date"),
                EtfAdjustedPriceRevision.open.label("raw_open"),
                EtfAdjustedPriceRevision.high.label("raw_high"),
                EtfAdjustedPriceRevision.low.label("raw_low"),
                EtfAdjustedPriceRevision.close.label("raw_close"),
                EtfAdjustedPriceRevision.volume.label("volume"),
                EtfAdjustedPriceRevision.turnover.label("turnover"),
                EtfAdjustedPriceRevision.raw_price_basis.label("raw_price_basis"),
                EtfAdjustedPriceRevision.research_adjusted_value.label("adjusted_close"),
                EtfAdjustedPriceRevision.research_price_basis.label("research_price_basis"),
                EtfAdjustedPriceRevision.data_provider.label("data_provider"),
                EtfAdjustedPriceRevision.provider_version.label("provider_version"),
                EtfAdjustedPriceRevision.source_timestamp.label("source_timestamp"),
                EtfAdjustedPriceRevision.adjustment_version.label("adjustment_version"),
                EtfAdjustedPriceRevision.decision_eligible.label("decision_eligible"),
                EtfAdjustedPriceRevision.decision_ineligibility_reason.label(
                    "decision_ineligibility_reason"
                ),
                EtfAdjustedPriceRevision.revision_hash.label("revision_hash"),
                EtfAdjustedPriceRevision.first_seen_at.label("first_seen_at"),
                EtfAdjustedPriceRevision.observed_at.label("observed_at"),
                EtfAdjustedPriceRevision.id.label("revision_id"),
                func.lower(EtfAdjustedPriceRevision.data_provider).label("provider_key"),
                case(
                    (
                        and_(
                            EtfAdjustedPriceRevision.decision_eligible.is_(True),
                            EtfAdjustedPriceRevision.research_price_basis
                            == "total_return_adjusted",
                            pair_compatibility,
                            EtfAdjustedPriceRevision.research_adjusted_value.is_not(None),
                            EtfAdjustedPriceRevision.source_timestamp.is_not(None),
                            EtfAdjustedPriceRevision.first_seen_at.is_not(None),
                            EtfAdjustedPriceRevision.observed_at.is_not(None),
                            EtfAdjustedPriceRevision.revision_hash.is_not(None),
                            EtfAdjustedPriceRevision.source_timestamp <= cutoff,
                        ),
                        0,
                    ),
                    else_=1,
                ).label("invalid_rank"),
                func.row_number()
                .over(
                    partition_by=(
                        EtfAdjustedPriceRevision.etf_code,
                        EtfAdjustedPriceRevision.trade_date,
                        func.lower(EtfAdjustedPriceRevision.data_provider),
                    ),
                    order_by=(
                        EtfAdjustedPriceRevision.first_seen_at.desc(),
                        EtfAdjustedPriceRevision.observed_at.desc(),
                        EtfAdjustedPriceRevision.id.desc(),
                    ),
                )
                .label("revision_rank"),
            )
            .where(
                EtfAdjustedPriceRevision.etf_code.in_(codes),
                EtfAdjustedPriceRevision.trade_date <= replay_date,
                EtfAdjustedPriceRevision.first_seen_at <= cutoff,
                EtfAdjustedPriceRevision.observed_at <= cutoff,
                provider_compatibility,
            )
            .subquery()
        )
        latest_by_provider_session = (
            select(visible_revisions)
            .where(visible_revisions.c.revision_rank == 1)
            .subquery()
        )
        ranked_provider_sessions = (
            select(
                latest_by_provider_session,
                func.row_number()
                .over(
                    partition_by=(
                        latest_by_provider_session.c.etf_code,
                        latest_by_provider_session.c.provider_key,
                    ),
                    order_by=(
                        latest_by_provider_session.c.trade_date.desc(),
                        latest_by_provider_session.c.revision_id.desc(),
                    ),
                )
                .label("provider_session_rank"),
            )
            .subquery()
        )
        provider_window = (
            select(ranked_provider_sessions)
            .where(ranked_provider_sessions.c.provider_session_rank <= rows_per_code)
            .subquery()
        )
        provider_stats = (
            select(
                provider_window.c.etf_code,
                provider_window.c.provider_key,
                func.count().label("session_count"),
                func.sum(provider_window.c.invalid_rank).label("invalid_count"),
                func.max(provider_window.c.trade_date).label("latest_trade_date"),
                func.max(provider_window.c.first_seen_at).label(
                    "latest_first_seen_at"
                ),
                func.max(provider_window.c.observed_at).label("latest_observed_at"),
            )
            .group_by(
                provider_window.c.etf_code,
                provider_window.c.provider_key,
            )
            .subquery()
        )
        ranked_providers = (
            select(
                provider_stats,
                func.row_number()
                .over(
                    partition_by=provider_stats.c.etf_code,
                    order_by=(
                        provider_stats.c.invalid_count.asc(),
                        provider_stats.c.latest_trade_date.desc(),
                        case(
                            (provider_stats.c.session_count >= rows_per_code, 0),
                            else_=1,
                        ).asc(),
                        provider_stats.c.session_count.desc(),
                        provider_stats.c.latest_first_seen_at.desc(),
                        provider_stats.c.latest_observed_at.desc(),
                        provider_stats.c.provider_key.asc(),
                    ),
                )
                .label("provider_rank"),
            )
            .subquery()
        )
        selected_provider = (
            select(ranked_providers)
            .where(ranked_providers.c.provider_rank == 1)
            .subquery()
        )
        selected_provider_rows = (
            select(provider_window)
            .join(
                selected_provider,
                and_(
                    provider_window.c.etf_code
                    == selected_provider.c.etf_code,
                    provider_window.c.provider_key
                    == selected_provider.c.provider_key,
                ),
            )
            .subquery()
        )
        ranked_sessions = select(
            selected_provider_rows,
            func.row_number()
            .over(
                partition_by=selected_provider_rows.c.etf_code,
                order_by=(
                    selected_provider_rows.c.trade_date.desc(),
                    selected_provider_rows.c.revision_id.desc(),
                ),
            )
            .label("session_rank"),
        ).subquery()
        result = await session.execute(
            select(ranked_sessions)
            .where(ranked_sessions.c.session_rank <= rows_per_code)
            .order_by(
                ranked_sessions.c.etf_code.asc(),
                ranked_sessions.c.trade_date.asc(),
                ranked_sessions.c.revision_id.asc(),
            )
        )
        rows.extend(result.mappings().all())
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
            turnover=row["turnover"],
            revision_hash=row.get("revision_hash"),
            first_seen_at=row.get("first_seen_at"),
            observed_at=row.get("observed_at"),
        )
        for row in rows
    )


async def recent_decision_eligible_etf_adjusted_facts(
    session: AsyncSession,
    *,
    etf_code: str,
    on_or_before: date,
    lookback_sessions: int = 120,
) -> tuple[EtfAdjustedDailyFact, ...]:
    """Return one bounded ETF adjusted-history page for live risk decisions.

    The projection may contain raw-only or stale-provider rows, so this facade
    revalidates the explicit adjusted-price provenance before exposing facts to
    Position Tracking or Risk Alert consumers. It never requests a provider.
    """

    code = str(etf_code).strip()
    if not code:
        return ()
    if lookback_sessions < 1 or lookback_sessions > 120:
        raise MarketDataReadLimitExceededError(
            "ETF adjusted-history lookback must be between 1 and 120 sessions"
        )
    source_limit = min(240, lookback_sessions * 2)
    facts = await etf_adjusted_daily_facts_on_or_before(
        session,
        etf_codes=(code,),
        replay_date=on_or_before,
        rows_per_code=source_limit,
        max_source_rows=source_limit,
    )
    cutoff = datetime.now(ASIA_SHANGHAI)
    eligible = [
        fact
        for fact in facts
        if fact.decision_eligible is True
        and fact.research_price_basis == "total_return_adjusted"
        and etf_adjusted_price_provenance_issue(
            adjusted_value=fact.adjusted_close,
            price_basis=fact.research_price_basis,
            data_provider=fact.data_provider,
            provider_version=fact.provider_version,
            source_timestamp=fact.source_timestamp,
            adjustment_version=fact.adjustment_version,
            data_cutoff=cutoff,
        )
        is None
    ]
    return tuple(eligible[-lookback_sessions:])


async def etf_adjusted_source_watermark(
    session: AsyncSession,
    *,
    etf_codes: tuple[str, ...],
    replay_date: date,
    decision_cutoff: datetime | None = None,
    compatible_provider_versions: tuple[tuple[str, str], ...] | None = None,
) -> EtfAdjustedSourceWatermark:
    """Return a cheap revision watermark without loading or sorting history rows."""

    codes = tuple(sorted(set(etf_codes)))
    if not codes:
        return EtfAdjustedSourceWatermark(0, None, None)
    cutoff = _utc_naive(decision_cutoff) if decision_cutoff is not None else None
    row: Any
    if cutoff is None:
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
    else:
        accepted_pairs = _compatible_adjusted_provider_pairs(compatible_provider_versions)
        if not accepted_pairs:
            return EtfAdjustedSourceWatermark(0, None, None)
        provider_compatibility = or_(
            *(
                and_(
                    func.lower(EtfAdjustedPriceRevision.data_provider) == provider,
                    EtfAdjustedPriceRevision.provider_version == version,
                    EtfAdjustedPriceRevision.adjustment_version == version,
                )
                for provider, version in accepted_pairs
            )
        )
        row = (
            await session.execute(
                select(
                    func.count(EtfAdjustedPriceRevision.id),
                    func.max(EtfAdjustedPriceRevision.id),
                    func.max(EtfAdjustedPriceRevision.first_seen_at),
                ).where(
                    EtfAdjustedPriceRevision.etf_code.in_(codes),
                    EtfAdjustedPriceRevision.trade_date <= replay_date,
                    EtfAdjustedPriceRevision.first_seen_at <= cutoff,
                    EtfAdjustedPriceRevision.observed_at <= cutoff,
                    EtfAdjustedPriceRevision.decision_eligible.is_(True),
                    EtfAdjustedPriceRevision.research_price_basis == "total_return_adjusted",
                    provider_compatibility,
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


async def recent_decision_eligible_etf_turnovers_by_code(
    session: AsyncSession,
    codes: list[str],
    *,
    lookback_sessions: int = 20,
    max_codes: int = 100,
) -> dict[str, tuple[float, ...]]:
    """Load bounded, decision-eligible ETF turnover histories in one query."""

    unique_codes = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    if len(unique_codes) > max_codes:
        raise MarketDataReadLimitExceededError(
            f"ETF turnover batch exceeds {max_codes} codes"
        )
    if not unique_codes:
        return {}
    if lookback_sessions < 1 or lookback_sessions > 60:
        raise MarketDataReadLimitExceededError(
            "ETF turnover lookback must be between 1 and 60 sessions"
        )
    row_number = func.row_number().over(
        partition_by=EtfPriceHistory.etf_code,
        order_by=(EtfPriceHistory.trade_date.desc(), EtfPriceHistory.id.desc()),
    )
    ranked = (
        select(
            EtfPriceHistory.etf_code.label("etf_code"),
            EtfPriceHistory.trade_date.label("trade_date"),
            EtfPriceHistory.turnover.label("turnover"),
            row_number.label("row_number"),
        )
        .where(
            EtfPriceHistory.etf_code.in_(unique_codes),
            EtfPriceHistory.decision_eligible.is_(True),
            EtfPriceHistory.research_price_basis == "total_return_adjusted",
            EtfPriceHistory.turnover.is_not(None),
            EtfPriceHistory.turnover > 0,
        )
        .subquery()
    )
    rows = (
        await session.execute(
            select(ranked.c.etf_code, ranked.c.trade_date, ranked.c.turnover)
            .where(ranked.c.row_number <= lookback_sessions)
            .order_by(ranked.c.etf_code, ranked.c.trade_date)
        )
    ).all()
    result: dict[str, list[float]] = {code: [] for code in unique_codes}
    for code, _trade_date, turnover in rows:
        if isinstance(turnover, bool) or not isinstance(turnover, int | float):
            continue
        value = float(turnover)
        if math.isfinite(value) and value > 0:
            result.setdefault(str(code), []).append(value)
    return {code: tuple(values) for code, values in result.items()}


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
