"""Exchange-session planner for reproducible non-overlapping validation dates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfPriceHistory, ShortResearchSignalRun

from .etf_ranking_validation import (
    PRODUCTION_RULE_VERSION,
    PRODUCTION_SCORE_FIELD,
    PRODUCTION_SCORE_VERSION,
    TOTAL_RETURN_ADJUSTED,
)


def theoretical_session_span(
    *,
    horizon_sessions: int,
    required_dates: int,
) -> int:
    if horizon_sessions <= 0 or required_dates <= 0:
        raise ValueError("horizon_sessions and required_dates must be positive")
    return required_dates * (horizon_sessions + 2)


@dataclass(frozen=True)
class ValidationSourceDatePlan:
    status: str
    reason: str | None
    horizon_sessions: int
    required_dates: int
    theoretical_session_span: int
    searched_session_span: int
    retention_cap_sessions: int
    query_count: int
    compatible_count: int
    completed_count: int
    pending_count: int
    overlapping_count: int
    non_overlapping_count: int
    excluded_count: int
    source_date_shortfall: int
    compatible_source_dates: tuple[date, ...]
    selected_source_dates: tuple[date, ...]


def _summarize(
    *,
    trading_sessions: tuple[date, ...],
    compatible_source_dates: set[date],
    horizon_sessions: int,
    required_dates: int,
    theoretical_span: int,
    searched_span: int,
    retention_cap: int,
    query_count: int,
    at_retention_cap: bool,
) -> ValidationSourceDatePlan:
    session_indexes = {session: index for index, session in enumerate(trading_sessions)}
    excluded_count = sum(day not in session_indexes for day in compatible_source_dates)
    compatible = tuple(
        sorted(day for day in compatible_source_dates if day in session_indexes)
    )
    completed: list[date] = []
    pending: list[date] = []
    for signal_date in compatible:
        signal_index = session_indexes[signal_date]
        if signal_index + horizon_sessions + 1 < len(trading_sessions):
            completed.append(signal_date)
        else:
            pending.append(signal_date)

    independent_descending: list[date] = []
    next_signal_index = len(trading_sessions)
    overlapping_count = 0
    for signal_date in reversed(completed):
        signal_index = session_indexes[signal_date]
        exit_index = signal_index + horizon_sessions + 1
        if exit_index >= next_signal_index:
            overlapping_count += 1
            continue
        independent_descending.append(signal_date)
        next_signal_index = signal_index

    independent = list(reversed(independent_descending))
    non_overlapping_count = len(independent)
    shortfall = max(required_dates - non_overlapping_count, 0)
    status = "ready" if shortfall == 0 else "insufficient"
    reason = None
    if status == "insufficient" and at_retention_cap:
        reason = "sparse_compatible_sources_at_retention_cap"
    elif status == "insufficient":
        reason = "compatible_source_search_incomplete"
    return ValidationSourceDatePlan(
        status=status,
        reason=reason,
        horizon_sessions=horizon_sessions,
        required_dates=required_dates,
        theoretical_session_span=theoretical_span,
        searched_session_span=searched_span,
        retention_cap_sessions=retention_cap,
        query_count=query_count,
        compatible_count=len(compatible),
        completed_count=len(completed),
        pending_count=len(pending),
        overlapping_count=overlapping_count,
        non_overlapping_count=non_overlapping_count,
        excluded_count=excluded_count,
        source_date_shortfall=shortfall,
        compatible_source_dates=compatible,
        selected_source_dates=tuple(independent[:required_dates]),
    )


async def expand_validation_source_search(
    *,
    trading_sessions: Sequence[date],
    load_compatible_source_dates: Callable[
        [date, date], Awaitable[Sequence[date]]
    ],
    horizon_sessions: int,
    required_dates: int,
    retention_cap_sessions: int,
    expansion_page_sessions: int = 40,
) -> ValidationSourceDatePlan:
    sessions = tuple(trading_sessions)
    if not sessions or sessions != tuple(sorted(set(sessions))):
        raise ValueError("trading_sessions must be non-empty, unique, and ordered")
    if retention_cap_sessions <= 0 or expansion_page_sessions <= 0:
        raise ValueError("retention and expansion caps must be positive")
    theoretical_span = theoretical_session_span(
        horizon_sessions=horizon_sessions,
        required_dates=required_dates,
    )
    retention_cap = min(retention_cap_sessions, len(sessions))
    searched_span = min(theoretical_span, retention_cap)
    oldest_allowed_index = len(sessions) - retention_cap
    start_index = len(sessions) - searched_span
    end_index = len(sessions) - 1
    compatible_dates: set[date] = set()
    query_count = 0

    while True:
        loaded = await load_compatible_source_dates(
            sessions[start_index],
            sessions[end_index],
        )
        compatible_dates.update(loaded)
        query_count += 1
        at_retention_cap = start_index == oldest_allowed_index
        plan = _summarize(
            trading_sessions=sessions,
            compatible_source_dates=compatible_dates,
            horizon_sessions=horizon_sessions,
            required_dates=required_dates,
            theoretical_span=theoretical_span,
            searched_span=len(sessions) - start_index,
            retention_cap=retention_cap,
            query_count=query_count,
            at_retention_cap=at_retention_cap,
        )
        if plan.status == "ready" or at_retention_cap:
            return plan
        previous_start_index = start_index
        start_index = max(
            oldest_allowed_index,
            start_index - expansion_page_sessions,
        )
        end_index = previous_start_index - 1


@dataclass(frozen=True)
class ProductionValidationSourcePlan:
    date_plan: ValidationSourceDatePlan
    source_runs: tuple[ShortResearchSignalRun, ...]


async def plan_production_validation_sources(
    session: AsyncSession,
    *,
    as_of_date: date,
    horizon_sessions: int = 10,
    required_dates: int = 20,
    retention_cap_sessions: int = 300,
    expansion_page_sessions: int = 40,
) -> ProductionValidationSourcePlan:
    raw_sessions = list(
        (
            await session.scalars(
                select(EtfPriceHistory.trade_date)
                .where(
                    EtfPriceHistory.trade_date <= as_of_date,
                    EtfPriceHistory.decision_eligible.is_(True),
                    EtfPriceHistory.research_price_basis == TOTAL_RETURN_ADJUSTED,
                    EtfPriceHistory.research_adjusted_value.is_not(None),
                )
                .distinct()
                .order_by(desc(EtfPriceHistory.trade_date))
                .limit(retention_cap_sessions)
            )
        ).all()
    )
    sessions = tuple(reversed(raw_sessions))
    if not sessions:
        raise ValueError("missing_adjusted_trading_sessions")
    source_runs_by_date: dict[date, ShortResearchSignalRun] = {}

    async def load_compatible_source_dates(
        start_date: date,
        end_date: date,
    ) -> tuple[date, ...]:
        rows = list(
            (
                await session.scalars(
                    select(ShortResearchSignalRun)
                    .where(
                        ShortResearchSignalRun.status == "success",
                        ShortResearchSignalRun.publication_state == "published",
                        ShortResearchSignalRun.scope_kind == "full",
                        ShortResearchSignalRun.score_version
                        == PRODUCTION_SCORE_VERSION,
                        ShortResearchSignalRun.score_field == PRODUCTION_SCORE_FIELD,
                        ShortResearchSignalRun.rule_version == PRODUCTION_RULE_VERSION,
                        ShortResearchSignalRun.price_basis == TOTAL_RETURN_ADJUSTED,
                        ShortResearchSignalRun.ranking_contract_hash.is_not(None),
                        ShortResearchSignalRun.scope_hash.is_not(None),
                        ShortResearchSignalRun.universe_snapshot_hash.is_not(None),
                        ShortResearchSignalRun.input_snapshot_hash.is_not(None),
                        ShortResearchSignalRun.data_cutoff.is_not(None),
                        ShortResearchSignalRun.idempotency_key.is_not(None),
                        ShortResearchSignalRun.expected_item_count > 0,
                        ShortResearchSignalRun.decision_data_coverage_ratio >= 0.95,
                        ShortResearchSignalRun.coverage_ratio >= 0.95,
                        ShortResearchSignalRun.as_of_trade_date >= start_date,
                        ShortResearchSignalRun.as_of_trade_date <= end_date,
                    )
                    .order_by(
                        ShortResearchSignalRun.as_of_trade_date.desc(),
                        ShortResearchSignalRun.published_at.desc(),
                        ShortResearchSignalRun.id.desc(),
                    )
                )
            ).all()
        )
        dates: list[date] = []
        for source_run in rows:
            source_date = source_run.as_of_trade_date
            if source_date is None or source_date in source_runs_by_date:
                continue
            source_runs_by_date[source_date] = source_run
            dates.append(source_date)
        return tuple(dates)

    date_plan = await expand_validation_source_search(
        trading_sessions=sessions,
        load_compatible_source_dates=load_compatible_source_dates,
        horizon_sessions=horizon_sessions,
        required_dates=required_dates,
        retention_cap_sessions=retention_cap_sessions,
        expansion_page_sessions=expansion_page_sessions,
    )
    return ProductionValidationSourcePlan(
        date_plan=date_plan,
        source_runs=tuple(
            source_runs_by_date[source_date]
            for source_date in date_plan.compatible_source_dates
        ),
    )
