from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)
OPEN_POLL_SECONDS = 30

# Shanghai Stock Exchange 2026 holiday schedule (SSE announcement 2025-12-22).
_SSE_HOLIDAYS_2026 = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 2),
        *[date(2026, 2, day) for day in range(16, 24)],
        date(2026, 4, 6),
        *[date(2026, 5, day) for day in range(1, 6)],
        date(2026, 6, 19),
        date(2026, 9, 25),
        *[date(2026, 10, day) for day in range(1, 8)],
    }
)
_HOLIDAYS_BY_YEAR = {2026: _SSE_HOLIDAYS_2026}


class ExchangeCalendarUnavailableError(ValueError):
    """Raised when a future trading day cannot be derived from a verified calendar."""


def localize_exchange_time(value: datetime | None = None) -> datetime:
    current = value or datetime.now(ASIA_SHANGHAI)
    if current.tzinfo is None:
        return current.replace(tzinfo=ASIA_SHANGHAI)
    return current.astimezone(ASIA_SHANGHAI)


def is_trading_day(value: date) -> bool:
    holidays = _HOLIDAYS_BY_YEAR.get(value.year)
    return holidays is not None and value.weekday() < 5 and value not in holidays


def next_trading_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.year in _HOLIDAYS_BY_YEAR:
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    raise ExchangeCalendarUnavailableError(f"exchange calendar is unavailable for {candidate.year}")


def market_session(value: datetime | None = None) -> tuple[str, str | None]:
    local = localize_exchange_time(value)
    if not is_trading_day(local.date()):
        return "closed", None
    current = local.time()
    if MORNING_OPEN <= current < MORNING_CLOSE:
        return "open", "morning"
    if MORNING_CLOSE <= current < AFTERNOON_OPEN:
        return "lunch_break", "lunch"
    if AFTERNOON_OPEN <= current < AFTERNOON_CLOSE:
        return "open", "afternoon"
    return "closed", None


def next_poll_seconds(value: datetime | None = None) -> int:
    local = localize_exchange_time(value)
    status, _session = market_session(local)
    if status == "open":
        return OPEN_POLL_SECONDS
    if status == "lunch_break":
        boundary = datetime.combine(local.date(), AFTERNOON_OPEN, tzinfo=ASIA_SHANGHAI)
    elif is_trading_day(local.date()) and local.time() < MORNING_OPEN:
        boundary = datetime.combine(local.date(), MORNING_OPEN, tzinfo=ASIA_SHANGHAI)
    else:
        try:
            boundary = datetime.combine(next_trading_day(local.date()), MORNING_OPEN, tzinfo=ASIA_SHANGHAI)
        except ExchangeCalendarUnavailableError:
            boundary = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=ASIA_SHANGHAI)
    return max(1, int((boundary - local).total_seconds()))
