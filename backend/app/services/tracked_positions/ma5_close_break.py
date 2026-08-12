from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfPriceHistory, TrackedPosition
from app.services.intraday_etf.exchange_calendar import (
    ExchangeCalendarUnavailableError,
    next_trading_day,
)
from app.services.market_data import (
    ASIA_SHANGHAI,
    etf_adjusted_price_provenance_issue,
    is_etf_exchange_trading_day,
)

MA5_CLOSE_BREAK_RULE_VERSION = "adjusted_ma5_close_break_v1"
MA5_CLOSE_BREAK_STATE_KEY = "ma5_close_break_state"
_REQUIRED_SESSIONS = 5
_RECOVERY_SESSIONS = 2


@dataclass(frozen=True)
class AdjustedMa5CloseBreakEvidence:
    status: str
    reason_code: str
    trade_date: date | None = None
    adjusted_close: float | None = None
    adjusted_ma5: float | None = None
    condition_met: bool | None = None
    observation_is_new: bool = False
    should_alert: bool = False
    earliest_execution_date: date | None = None
    data_provider: str | None = None
    provider_version: str | None = None
    adjustment_version: str | None = None
    source_timestamp: datetime | None = None
    state_update: dict[str, Any] | None = None

    @property
    def decision_eligible(self) -> bool:
        return self.status == "eligible"

    def as_context(self) -> dict[str, Any]:
        return {
            "rule_version": MA5_CLOSE_BREAK_RULE_VERSION,
            "status": self.status,
            "reason_code": self.reason_code,
            "decision_eligible": self.decision_eligible,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "adjusted_close": self.adjusted_close,
            "adjusted_ma5": self.adjusted_ma5,
            "price_basis": "total_return_adjusted" if self.decision_eligible else None,
            "condition_met": self.condition_met,
            "observation_is_new": self.observation_is_new,
            "should_alert": self.should_alert,
            "earliest_execution_date": (
                self.earliest_execution_date.isoformat() if self.earliest_execution_date else None
            ),
            "data_provider": self.data_provider,
            "provider_version": self.provider_version,
            "adjustment_version": self.adjustment_version,
            "source_timestamp": (
                self.source_timestamp.isoformat() if self.source_timestamp else None
            ),
            "rearm_rule": "adjusted_close_at_or_above_adjusted_ma5_for_2_sessions",
            "intraday_trigger_allowed": False,
        }


def _as_shanghai(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(ASIA_SHANGHAI)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).astimezone(ASIA_SHANGHAI)
    return value.astimezone(ASIA_SHANGHAI)


def _latest_completed_session(now: datetime) -> date:
    candidate = now.date()
    if now.timetz().replace(tzinfo=None) < time(15, 10):
        candidate -= timedelta(days=1)
    for _ in range(14):
        if is_etf_exchange_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError("latest completed ETF session is unavailable")


def _trading_sessions_through(latest: date, count: int) -> list[date]:
    sessions: list[date] = []
    candidate = latest
    for _ in range(30):
        if is_etf_exchange_trading_day(candidate):
            sessions.append(candidate)
            if len(sessions) == count:
                return sessions
        candidate -= timedelta(days=1)
    return sessions


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _next_state(
    position: TrackedPosition,
    *,
    trade_date: date,
    condition_met: bool,
) -> tuple[bool, bool, dict[str, Any]]:
    raw = dict((position.exit_state_json or {}).get(MA5_CLOSE_BREAK_STATE_KEY) or {})
    phase = str(raw.get("phase") or "armed")
    if phase not in {"armed", "firing", "recovering"}:
        phase = "armed"
    recovery_count = max(0, int(raw.get("recovery_count") or 0))
    last_observed = _parse_date(raw.get("last_observed_trade_date"))
    observation_is_new = last_observed != trade_date
    should_alert = False

    if observation_is_new:
        if condition_met:
            should_alert = phase == "armed"
            phase = "firing"
            recovery_count = 0
        elif phase in {"firing", "recovering"}:
            recovery_count += 1
            if recovery_count >= _RECOVERY_SESSIONS:
                phase = "armed"
                recovery_count = 0
            else:
                phase = "recovering"
        else:
            phase = "armed"
            recovery_count = 0

    state_update = {
        "rule_version": MA5_CLOSE_BREAK_RULE_VERSION,
        "phase": phase,
        "last_observed_trade_date": trade_date.isoformat(),
        "last_condition_met": condition_met,
        "recovery_count": recovery_count,
        "last_signal_date": (
            trade_date.isoformat() if should_alert else raw.get("last_signal_date")
        ),
    }
    return observation_is_new, should_alert, state_update


async def load_adjusted_ma5_close_break_evidence(
    session: AsyncSession,
    position: TrackedPosition,
    *,
    now: datetime | None = None,
) -> AdjustedMa5CloseBreakEvidence:
    local_now = _as_shanghai(now)
    try:
        completed_session = _latest_completed_session(local_now)
    except ValueError:
        return AdjustedMa5CloseBreakEvidence(
            status="unavailable",
            reason_code="exchange_calendar_unavailable",
        )
    if completed_session < position.buy_date:
        return AdjustedMa5CloseBreakEvidence(
            status="unavailable",
            reason_code="position_not_open_at_completed_session",
        )

    rows = list(
        (
            await session.scalars(
                select(EtfPriceHistory)
                .where(
                    EtfPriceHistory.etf_code == position.asset_code,
                    EtfPriceHistory.trade_date <= completed_session,
                )
                .order_by(EtfPriceHistory.trade_date.desc(), EtfPriceHistory.id.desc())
                .limit(_REQUIRED_SESSIONS)
            )
        ).all()
    )
    expected_dates = _trading_sessions_through(completed_session, _REQUIRED_SESSIONS)
    if len(rows) != _REQUIRED_SESSIONS or [row.trade_date for row in rows] != expected_dates:
        return AdjustedMa5CloseBreakEvidence(
            status="unavailable",
            reason_code="ma5_adjusted_session_gap",
            trade_date=rows[0].trade_date if rows else None,
        )

    adjusted_values: list[float] = []
    series_identity: tuple[str, str | None, str | None] | None = None
    for row in rows:
        if row.decision_eligible is not True:
            return AdjustedMa5CloseBreakEvidence(
                status="unavailable",
                reason_code=row.decision_ineligibility_reason
                or "adjusted_close_not_decision_eligible",
                trade_date=row.trade_date,
            )
        issue = etf_adjusted_price_provenance_issue(
            adjusted_value=row.research_adjusted_value,
            price_basis=row.research_price_basis,
            data_provider=row.data_provider,
            provider_version=row.provider_version,
            source_timestamp=row.source_timestamp,
            adjustment_version=row.adjustment_version,
            data_cutoff=local_now,
        )
        if issue is not None:
            return AdjustedMa5CloseBreakEvidence(
                status="unavailable",
                reason_code=issue,
                trade_date=row.trade_date,
            )
        current_series_identity = (
            str(row.data_provider or "").strip().lower(),
            row.provider_version,
            row.adjustment_version,
        )
        if series_identity is None:
            series_identity = current_series_identity
        elif current_series_identity != series_identity:
            return AdjustedMa5CloseBreakEvidence(
                status="unavailable",
                reason_code="mixed_adjusted_series_provenance",
                trade_date=row.trade_date,
            )
        assert row.research_adjusted_value is not None
        adjusted_values.append(float(row.research_adjusted_value))

    latest = rows[0]
    assert latest.source_timestamp is not None
    latest_source_utc = (
        latest.source_timestamp.replace(tzinfo=UTC)
        if latest.source_timestamp.tzinfo is None
        else latest.source_timestamp.astimezone(UTC)
    )
    session_close_utc = datetime.combine(
        completed_session,
        time(15, 0),
        tzinfo=ASIA_SHANGHAI,
    ).astimezone(UTC)
    if latest_source_utc < session_close_utc:
        return AdjustedMa5CloseBreakEvidence(
            status="unavailable",
            reason_code="adjusted_close_observed_before_session_close",
            trade_date=completed_session,
        )
    adjusted_close = adjusted_values[0]
    adjusted_ma5 = sum(adjusted_values) / _REQUIRED_SESSIONS
    condition_met = adjusted_close < adjusted_ma5
    observation_is_new, should_alert, state_update = _next_state(
        position,
        trade_date=completed_session,
        condition_met=condition_met,
    )
    try:
        earliest_execution_date = next_trading_day(completed_session)
    except ExchangeCalendarUnavailableError:
        earliest_execution_date = None

    return AdjustedMa5CloseBreakEvidence(
        status="eligible",
        reason_code="eligible_total_return_adjusted_close",
        trade_date=completed_session,
        adjusted_close=round(adjusted_close, 8),
        adjusted_ma5=round(adjusted_ma5, 8),
        condition_met=condition_met,
        observation_is_new=observation_is_new,
        should_alert=should_alert,
        earliest_execution_date=earliest_execution_date,
        data_provider=latest.data_provider,
        provider_version=latest.provider_version,
        adjustment_version=latest.adjustment_version,
        source_timestamp=latest.source_timestamp,
        state_update=state_update,
    )
