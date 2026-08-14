"""Point-in-time, shadow-only late-day turnaround strategy.

This module is deliberately self-contained.  It operates on immutable inputs and
does not know about downstream integrations, persistence, or network data.  The
strategy is intended for research only:

    14:30--14:50 Shanghai time -> buy the first eligible quote within two minutes
    next trading day 10:00--10:10 -> sell the first eligible quote (frozen
    benchmark exit; live tracked-position mail uses its separate T+1 policy)

The implementation is conservative about malformed data and always fails closed
with a stable reason instead of raising an application-level exception.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import isfinite
from statistics import median
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from app.services.etf_research_evidence import stable_contract_hash

SHANGHAI = ZoneInfo("Asia/Shanghai")
POLICY_MODE = "shadow_only"
PRODUCTION_MUTATION_ALLOWED = False
STRATEGY_VERSION = "late_day_turnaround_shadow.v2"


OK = "ok"
INVALID_INPUT = "invalid_input"
INVALID_CONFIG = "invalid_config"
INVALID_BAR = "invalid_bar"
INVALID_QUOTE = "invalid_quote"
DECISION_WINDOW_CLOSED = "decision_window_closed"
INSUFFICIENT_BARS = "insufficient_bars"
NOT_FIRST_DAY_RED = "not_first_day_red"
MA5_NOT_TURNING_UP = "ma5_not_turning_up"
LOW_LIQUIDITY = "low_liquidity"
GAIN_TOO_HIGH = "gain_too_high"
MA_DEVIATION_TOO_HIGH = "ma_deviation_too_high"
INVALID_LIQUIDITY_BASELINE = "invalid_liquidity_baseline"
INVALID_TOP_N = "invalid_top_n"
INPUT_LIMIT_EXCEEDED = "input_limit_exceeded"
SIGNAL_UNAVAILABLE = "signal_unavailable"
MISSING_BUY_QUOTE = "missing_buy_quote"
MISSING_SELL_QUOTE = "missing_sell_quote"
STALE_LATEST_BAR = "stale_latest_bar"
QUOTE_CODE_MISMATCH = "quote_code_mismatch"
MISSING_TRADING_CALENDAR = "missing_trading_calendar"


@dataclass(frozen=True, slots=True)
class Config:
    """Stable strategy and execution parameters.

    ``fee_bps`` and ``slippage_bps`` are the symmetric defaults.  The explicit
    buy/sell fields can override them for a more realistic cost model.
    """

    min_cumulative_amount: float = 50_000_000.0
    max_gain_pct: float = 7.0
    max_ma_deviation_pct: float = 2.0
    min_amount_ratio: float = 0.8
    max_screen_snapshots: int = 128
    top_n: int = 10
    fee_bps: float = 5.0
    slippage_bps: float = 5.0
    buy_fee_bps: float | None = None
    sell_fee_bps: float | None = None
    buy_slippage_bps: float | None = None
    sell_slippage_bps: float | None = None
    sell_tax_bps: float = 10.0


CONTRACT_PAYLOAD = {
    "schema_version": STRATEGY_VERSION,
    "policy_mode": POLICY_MODE,
    "production_mutation_allowed": False,
    "signal_formula": (
        "current_price > today_open and current_price > previous_close and "
        "previous_close <= previous_open"
    ),
    "ma_formula": (
        "current_ma5=mean(close[-5:]); previous_ma5=mean(close[-6:-1]); "
        "prior_ma5=mean(close[-7:-2]); current_ma5 > previous_ma5 and "
        "previous_ma5 <= prior_ma5 and latest.close > current_ma5"
    ),
    "decision_window": ["14:30", "14:50"],
    "bar_minutes": 10,
    "minimum_closed_bars": 7,
    "input_limits": {
        "bars": 32,
        "snapshots": 128,
        "quotes": 512,
        "trading_dates": 512,
        "top_n": 10,
    },
    "default_config": {
        "min_cumulative_amount": 50_000_000.0,
        "max_gain_pct": 7.0,
        "max_ma_deviation_pct": 2.0,
        "min_amount_ratio": 0.8,
        "max_screen_snapshots": 128,
        "top_n": 10,
        "fee_bps": 5.0,
        "slippage_bps": 5.0,
        "buy_fee_bps": None,
        "sell_fee_bps": None,
        "buy_slippage_bps": None,
        "sell_slippage_bps": None,
        "sell_tax_bps": 10.0,
    },
    "execution": {
        "entry_delay_minutes": 0,
        "entry_max_latency_minutes": 2,
        "entry_deadline": "14:55",
        "exit_window": ["10:00", "10:10"],
        "exit_role": "fixed_research_benchmark",
        "calendar": "explicit_next_trading_session",
        "t_plus_one": True,
    },
}
CONTRACT_HASH = stable_contract_hash(CONTRACT_PAYLOAD)


@dataclass(frozen=True, slots=True)
class ClosedBar10m:
    """A closed ten-minute bar observed at ``observed_at``."""

    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass(frozen=True, slots=True)
class PITSnapshot:
    """Point-in-time snapshot available at one decision timestamp."""

    asset_code: str
    signal_date: date
    decision_at: datetime
    today_open: float
    previous_close: float
    current_price: float
    previous_open: float
    cumulative_amount: float
    bars: Sequence[ClosedBar10m] = field(default_factory=tuple)

    @property
    def today_cumulative_amount(self) -> float:
        """Readable alias for callers that use the market-data field name."""

        return self.cumulative_amount


@dataclass(frozen=True, slots=True)
class SignalResult:
    available: bool
    reason: str
    asset_code: str = ""
    signal_date: date | None = None
    decision_at: datetime | None = None
    score: float | None = None
    ma5: float | None = None
    gain_pct: float | None = None
    ma_deviation_pct: float | None = None
    amount_ratio: float | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionQuote:
    observed_at: datetime
    price: float
    asset_code: str = ""
    source: str = ""

    @property
    def quote_time(self) -> datetime:
        return self.observed_at


@dataclass(frozen=True, slots=True)
class TradeResult:
    available: bool
    reason: str
    asset_code: str = ""
    signal_date: date | None = None
    buy_quote: ExecutionQuote | None = None
    sell_quote: ExecutionQuote | None = None
    gross_return_pct: float | None = None
    net_return_pct: float | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def net_return(self) -> float | None:
        return self.net_return_pct


# Friendly aliases keep the contract easy to consume without creating another
# representation of the data.
Bar10m = ClosedBar10m
TenMinuteBar = ClosedBar10m


def _base_provenance(**values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "policy_mode": POLICY_MODE,
        "production_mutation_allowed": PRODUCTION_MUTATION_ALLOWED,
        "strategy_version": STRATEGY_VERSION,
        "contract_hash": CONTRACT_HASH,
    }
    result.update(values)
    return result


def _immutable_provenance(**values: Any) -> Mapping[str, Any]:
    return MappingProxyType(_base_provenance(**values))


def _unavailable_signal(
    reason: str,
    snapshot: PITSnapshot | None = None,
    **provenance: Any,
) -> SignalResult:
    return SignalResult(
        available=False,
        reason=reason,
        asset_code=snapshot.asset_code if snapshot is not None else "",
        signal_date=snapshot.signal_date if snapshot is not None else None,
        decision_at=snapshot.decision_at if snapshot is not None else None,
        provenance=_immutable_provenance(**provenance),
    )


def _unavailable_trade(
    reason: str,
    signal: SignalResult | None = None,
    **provenance: Any,
) -> TradeResult:
    return TradeResult(
        available=False,
        reason=reason,
        asset_code=signal.asset_code if signal is not None else "",
        signal_date=signal.signal_date if signal is not None else None,
        provenance=_immutable_provenance(**provenance),
    )


def _is_number(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _is_positive(value: Any) -> bool:
    return _is_number(value) and float(value) > 0.0


def _is_nonnegative(value: Any) -> bool:
    return _is_number(value) and float(value) >= 0.0


def _aware(value: Any) -> bool:
    try:
        return isinstance(value, datetime) and value.utcoffset() is not None
    except (TypeError, ValueError, OverflowError):
        return False


def _shanghai(value: datetime) -> datetime:
    return value.astimezone(SHANGHAI)


def _valid_asset_code(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 6
        and value != "000000"
        and all("0" <= character <= "9" for character in value)
    )


def _valid_config(config: Config) -> bool:
    if not isinstance(config, Config):
        return False
    nonnegative = (
        config.min_cumulative_amount,
        config.max_gain_pct,
        config.max_ma_deviation_pct,
        config.min_amount_ratio,
        config.fee_bps,
        config.slippage_bps,
        config.sell_tax_bps,
    )
    if any(not _is_nonnegative(item) for item in nonnegative):
        return False
    if not isinstance(config.max_screen_snapshots, int) or isinstance(
        config.max_screen_snapshots, bool
    ):
        return False
    if not 1 <= config.max_screen_snapshots <= 128:
        return False
    if not isinstance(config.top_n, int) or isinstance(config.top_n, bool):
        return False
    if not 1 <= config.top_n <= 10:
        return False

    optional_costs = (
        config.buy_fee_bps,
        config.sell_fee_bps,
        config.buy_slippage_bps,
        config.sell_slippage_bps,
    )
    if any(item is not None and not _is_nonnegative(item) for item in optional_costs):
        return False

    buy_fee = config.buy_fee_bps if config.buy_fee_bps is not None else config.fee_bps
    sell_fee = config.sell_fee_bps if config.sell_fee_bps is not None else config.fee_bps
    buy_slippage = (
        config.buy_slippage_bps
        if config.buy_slippage_bps is not None
        else config.slippage_bps
    )
    sell_slippage = (
        config.sell_slippage_bps
        if config.sell_slippage_bps is not None
        else config.slippage_bps
    )
    costs = tuple(
        float(item)
        for item in (
            buy_fee,
            sell_fee,
            buy_slippage,
            sell_slippage,
            config.sell_tax_bps,
        )
    )
    if any(item >= 10_000.0 for item in costs):
        return False
    buy_fee, sell_fee, buy_slippage, sell_slippage, sell_tax = costs
    return (
        buy_fee + buy_slippage < 10_000.0
        and sell_fee + sell_slippage < 10_000.0
        and sell_fee + sell_tax < 10_000.0
        and sell_fee + sell_slippage + sell_tax < 10_000.0
    )


def _valid_bar(bar: ClosedBar10m) -> bool:
    if not isinstance(bar, ClosedBar10m) or not _aware(bar.observed_at):
        return False
    ohlc = (bar.open, bar.high, bar.low, bar.close)
    if not all(_is_positive(value) for value in ohlc):
        return False
    try:
        open_price, high, low, close = (float(value) for value in ohlc)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        high >= max(open_price, close)
        and low <= min(open_price, close)
        and high >= low
        and _is_nonnegative(bar.volume)
        and _is_nonnegative(bar.amount)
    )


def _validate_snapshot(
    snapshot: PITSnapshot,
) -> tuple[bool, str, list[ClosedBar10m], int]:
    if not isinstance(snapshot, PITSnapshot):
        return False, INVALID_INPUT, [], 0
    if not _valid_asset_code(snapshot.asset_code):
        return False, INVALID_INPUT, [], 0
    if not isinstance(snapshot.signal_date, date) or isinstance(
        snapshot.signal_date, datetime
    ):
        return False, INVALID_INPUT, [], 0
    if not _aware(snapshot.decision_at):
        return False, INVALID_INPUT, [], 0
    decision_local = _shanghai(snapshot.decision_at)
    if decision_local.date() != snapshot.signal_date:
        return False, INVALID_INPUT, [], 0
    if not time(14, 30) <= decision_local.time() <= time(14, 50):
        return False, DECISION_WINDOW_CLOSED, [], 0
    if not all(
        _is_positive(value)
        for value in (
            snapshot.today_open,
            snapshot.previous_close,
            snapshot.current_price,
            snapshot.previous_open,
        )
    ) or not _is_nonnegative(snapshot.cumulative_amount):
        return False, INVALID_INPUT, [], 0

    try:
        iterator = iter(snapshot.bars)
    except (TypeError, ValueError, OverflowError):
        return False, INVALID_BAR, [], 0

    eligible: list[ClosedBar10m] = []
    seen_times: set[datetime] = set()
    ignored_future = 0
    try:
        for index, bar in enumerate(iterator, start=1):
            if index > 32:
                return False, INPUT_LIMIT_EXCEEDED, [], ignored_future
            if not isinstance(bar, ClosedBar10m) or not _aware(bar.observed_at):
                return False, INVALID_BAR, [], ignored_future
            if bar.observed_at > snapshot.decision_at:
                ignored_future += 1
                continue
            bar_local = _shanghai(bar.observed_at)
            if bar_local.date() != snapshot.signal_date:
                return False, INVALID_BAR, [], ignored_future
            if bar_local in seen_times or not _valid_bar(bar):
                return False, INVALID_BAR, [], ignored_future
            seen_times.add(bar_local)
            eligible.append(bar)
    except Exception:
        return False, INVALID_BAR, [], ignored_future

    eligible.sort(key=lambda item: item.observed_at)
    return True, OK, eligible, ignored_future


def evaluate_snapshot(
    snapshot: PITSnapshot,
    config: Config = Config(),
) -> SignalResult:
    """Evaluate one PIT snapshot and return a fail-closed signal result."""

    try:
        if not _valid_config(config):
            return _unavailable_signal(INVALID_CONFIG)
        valid, reason, bars, ignored_future = _validate_snapshot(snapshot)
        if not valid:
            return _unavailable_signal(
                reason,
                snapshot if isinstance(snapshot, PITSnapshot) else None,
                ignored_future_bar_count=ignored_future,
            )
        common = {"ignored_future_bar_count": ignored_future}
        if bars and (snapshot.decision_at - bars[-1].observed_at).total_seconds() > 600.0:
            return _unavailable_signal(STALE_LATEST_BAR, snapshot, **common)
        if len(bars) < 7:
            return _unavailable_signal(INSUFFICIENT_BARS, snapshot, **common)
        if not (
            snapshot.current_price > snapshot.today_open
            and snapshot.current_price > snapshot.previous_close
            and snapshot.previous_close <= snapshot.previous_open
        ):
            return _unavailable_signal(NOT_FIRST_DAY_RED, snapshot, **common)

        latest = bars[-1]
        current_ma5 = sum(item.close for item in bars[-5:]) / 5.0
        previous_ma5 = sum(item.close for item in bars[-6:-1]) / 5.0
        prior_ma5 = sum(item.close for item in bars[-7:-2]) / 5.0
        if not (
            current_ma5 > previous_ma5
            and previous_ma5 <= prior_ma5
            and latest.close > current_ma5
        ):
            return _unavailable_signal(
                MA5_NOT_TURNING_UP,
                snapshot,
                current_ma5=current_ma5,
                previous_ma5=previous_ma5,
                prior_ma5=prior_ma5,
                **common,
            )

        gain_pct = (snapshot.current_price / snapshot.previous_close - 1.0) * 100.0
        ma_deviation_pct = abs(latest.close - current_ma5) / current_ma5 * 100.0
        if snapshot.cumulative_amount < config.min_cumulative_amount:
            return _unavailable_signal(
                LOW_LIQUIDITY,
                snapshot,
                current_ma5=current_ma5,
                gain_pct=gain_pct,
                ma_deviation_pct=ma_deviation_pct,
                **common,
            )
        if gain_pct > config.max_gain_pct:
            return _unavailable_signal(
                GAIN_TOO_HIGH,
                snapshot,
                current_ma5=current_ma5,
                gain_pct=gain_pct,
                ma_deviation_pct=ma_deviation_pct,
                **common,
            )
        if ma_deviation_pct > config.max_ma_deviation_pct:
            return _unavailable_signal(
                MA_DEVIATION_TOO_HIGH,
                snapshot,
                current_ma5=current_ma5,
                gain_pct=gain_pct,
                ma_deviation_pct=ma_deviation_pct,
                **common,
            )

        previous_amounts = [item.amount for item in bars[-21:-1]]
        if not previous_amounts:
            return _unavailable_signal(
                INVALID_LIQUIDITY_BASELINE,
                snapshot,
                current_ma5=current_ma5,
                gain_pct=gain_pct,
                ma_deviation_pct=ma_deviation_pct,
                **common,
            )
        baseline = median(previous_amounts)
        if not _is_positive(baseline):
            return _unavailable_signal(
                INVALID_LIQUIDITY_BASELINE,
                snapshot,
                current_ma5=current_ma5,
                gain_pct=gain_pct,
                ma_deviation_pct=ma_deviation_pct,
                **common,
            )
        amount_ratio = latest.amount / baseline
        if amount_ratio < config.min_amount_ratio:
            return _unavailable_signal(
                LOW_LIQUIDITY,
                snapshot,
                current_ma5=current_ma5,
                gain_pct=gain_pct,
                ma_deviation_pct=ma_deviation_pct,
                amount_ratio=amount_ratio,
                **common,
            )

        slope = (current_ma5 - previous_ma5) / previous_ma5
        distance = (latest.close - current_ma5) / current_ma5
        score = round(10_000.0 * slope + 1_000.0 * distance + min(amount_ratio, 3.0), 10)
        if not _is_number(score):
            return _unavailable_signal(INVALID_INPUT, snapshot, **common)
        return SignalResult(
            available=True,
            reason=OK,
            asset_code=snapshot.asset_code,
            signal_date=snapshot.signal_date,
            decision_at=snapshot.decision_at,
            score=float(score),
            ma5=float(current_ma5),
            gain_pct=float(gain_pct),
            ma_deviation_pct=float(ma_deviation_pct),
            amount_ratio=float(amount_ratio),
            provenance=_immutable_provenance(
                latest_observed_at=latest.observed_at,
                current_ma5=current_ma5,
                previous_ma5=previous_ma5,
                prior_ma5=prior_ma5,
                baseline_amount=baseline,
                ignored_future_bar_count=ignored_future,
            ),
        )
    except (TypeError, ValueError, OverflowError, ArithmeticError):
        return _unavailable_signal(
            INVALID_INPUT,
            snapshot if isinstance(snapshot, PITSnapshot) else None,
        )


def screen_snapshots(
    snapshots: Iterable[PITSnapshot],
    config: Config = Config(),
    top_n: int | None = None,
) -> tuple[SignalResult, ...]:
    """Screen a bounded snapshot stream and return deterministic top-N signals."""

    try:
        if not _valid_config(config):
            return (_unavailable_signal(INVALID_CONFIG),)
        requested = config.top_n if top_n is None else top_n
        if not isinstance(requested, int) or isinstance(requested, bool) or not 1 <= requested <= 10:
            return (_unavailable_signal(INVALID_TOP_N),)
        bounded: list[PITSnapshot] = []
        for snapshot in snapshots:
            bounded.append(snapshot)
            if len(bounded) > config.max_screen_snapshots:
                return (_unavailable_signal(INPUT_LIMIT_EXCEEDED),)
        candidates = [
            result
            for result in (evaluate_snapshot(item, config) for item in bounded)
            if result.available
        ]
        candidates.sort(key=lambda item: (-float(item.score or 0.0), item.asset_code))
        return tuple(candidates[:requested])
    except (TypeError, ValueError, OverflowError, ArithmeticError):
        return (_unavailable_signal(INVALID_INPUT),)


def _valid_quote(quote: ExecutionQuote) -> bool:
    return (
        isinstance(quote, ExecutionQuote)
        and _aware(quote.observed_at)
        and _is_positive(quote.price)
    )


def _valid_backtest_signal(signal: object) -> bool:
    if type(signal) is not SignalResult:
        return False
    if signal.available is not True or signal.reason != OK:
        return False
    if not _valid_asset_code(signal.asset_code):
        return False
    if not isinstance(signal.signal_date, date) or isinstance(
        signal.signal_date, datetime
    ):
        return False
    if not _aware(signal.decision_at):
        return False
    if _shanghai(signal.decision_at).date() != signal.signal_date:
        return False
    provenance = signal.provenance
    if not isinstance(provenance, Mapping):
        return False
    try:
        return (
            provenance.get("contract_hash") == CONTRACT_HASH
            and provenance.get("strategy_version") == STRATEGY_VERSION
            and provenance.get("policy_mode") == POLICY_MODE
            and provenance.get("production_mutation_allowed")
            is PRODUCTION_MUTATION_ALLOWED
        )
    except Exception:
        return False


def _effective_costs(config: Config) -> tuple[float, float, float, float, float]:
    buy_fee = config.buy_fee_bps if config.buy_fee_bps is not None else config.fee_bps
    sell_fee = config.sell_fee_bps if config.sell_fee_bps is not None else config.fee_bps
    buy_slippage = (
        config.buy_slippage_bps
        if config.buy_slippage_bps is not None
        else config.slippage_bps
    )
    sell_slippage = (
        config.sell_slippage_bps
        if config.sell_slippage_bps is not None
        else config.slippage_bps
    )
    return (
        float(buy_fee),
        float(sell_fee),
        float(buy_slippage),
        float(sell_slippage),
        float(config.sell_tax_bps),
    )


def backtest_trade(
    signal: SignalResult,
    quotes: Iterable[ExecutionQuote],
    config: Config = Config(),
    trading_dates: Iterable[date] | None = None,
) -> TradeResult:
    """Execute the fixed PIT/T+1 quote selection and calculate net return."""

    try:
        if not _valid_config(config):
            return _unavailable_trade(INVALID_CONFIG, signal)
        if not _valid_backtest_signal(signal):
            return _unavailable_trade(
                SIGNAL_UNAVAILABLE,
                signal if isinstance(signal, SignalResult) else None,
            )
        if trading_dates is None:
            return _unavailable_trade(MISSING_TRADING_CALENDAR, signal)

        try:
            quote_iterator = iter(quotes)
        except (TypeError, ValueError, OverflowError):
            return _unavailable_trade(INVALID_INPUT, signal)

        raw_quotes: list[ExecutionQuote] = []
        try:
            for index, quote in enumerate(quote_iterator, start=1):
                if index > 512:
                    return _unavailable_trade(INPUT_LIMIT_EXCEEDED, signal)
                if not _valid_quote(quote):
                    return _unavailable_trade(INVALID_QUOTE, signal)
                if (
                    not _valid_asset_code(quote.asset_code)
                    or quote.asset_code != signal.asset_code
                ):
                    return _unavailable_trade(QUOTE_CODE_MISMATCH, signal)
                raw_quotes.append(quote)
        except Exception:
            return _unavailable_trade(INVALID_INPUT, signal)

        dates_seen: set[date] = set()
        try:
            for index, item in enumerate(trading_dates, start=1):
                if index > 512:
                    return _unavailable_trade(INPUT_LIMIT_EXCEEDED, signal)
                if not isinstance(item, date) or isinstance(item, datetime):
                    return _unavailable_trade(INVALID_INPUT, signal)
                if item > signal.signal_date:
                    dates_seen.add(item)
        except Exception:
            return _unavailable_trade(INVALID_INPUT, signal)
        dates = sorted(dates_seen)
        if not dates:
            return _unavailable_trade(
                MISSING_SELL_QUOTE,
                signal,
                trading_calendar="empty",
            )
        sell_date = dates[0]

        signal_date = signal.signal_date
        decision_local = _shanghai(signal.decision_at)
        session_deadline = datetime.combine(signal_date, time(14, 55), tzinfo=SHANGHAI)
        buy_deadline = min(session_deadline, decision_local + timedelta(minutes=2))
        buy_start = decision_local
        buy_candidates = [
            quote
            for quote in raw_quotes
            if quote.observed_at >= buy_start
            and _shanghai(quote.observed_at).date() == signal_date
            and _shanghai(quote.observed_at) <= buy_deadline
        ]
        if not buy_candidates:
            return _unavailable_trade(MISSING_BUY_QUOTE, signal)
        buy_quote = min(buy_candidates, key=lambda item: item.observed_at)

        sell_start = datetime.combine(sell_date, time(10, 0), tzinfo=SHANGHAI)
        sell_deadline = datetime.combine(sell_date, time(10, 10), tzinfo=SHANGHAI)
        sell_candidates = [
            quote
            for quote in raw_quotes
            if _shanghai(quote.observed_at).date() == sell_date
            and sell_start <= quote.observed_at <= sell_deadline
        ]
        if not sell_candidates:
            return _unavailable_trade(
                MISSING_SELL_QUOTE,
                signal,
                t_plus_one_date=sell_date,
            )
        sell_quote = min(sell_candidates, key=lambda item: item.observed_at)

        buy_fee, sell_fee, buy_slippage, sell_slippage, sell_tax = _effective_costs(
            config
        )
        buy_execution = buy_quote.price * (1.0 + buy_slippage / 10_000.0)
        sell_execution = sell_quote.price * (1.0 - sell_slippage / 10_000.0)
        buy_cost = buy_execution * (1.0 + buy_fee / 10_000.0)
        sell_proceeds = sell_execution * (
            1.0 - (sell_fee + sell_tax) / 10_000.0
        )
        gross_return_pct = (sell_quote.price / buy_quote.price - 1.0) * 100.0
        net_return_pct = (sell_proceeds / buy_cost - 1.0) * 100.0
        if not _is_number(gross_return_pct) or not _is_number(net_return_pct):
            return _unavailable_trade(INVALID_INPUT, signal)
        return TradeResult(
            available=True,
            reason=OK,
            asset_code=signal.asset_code,
            signal_date=signal.signal_date,
            buy_quote=buy_quote,
            sell_quote=sell_quote,
            gross_return_pct=float(gross_return_pct),
            net_return_pct=float(net_return_pct),
            provenance=_immutable_provenance(
                buy_observed_at=buy_quote.observed_at,
                sell_observed_at=sell_quote.observed_at,
                t_plus_one_date=sell_date,
                buy_fee_bps=buy_fee,
                sell_fee_bps=sell_fee,
                buy_slippage_bps=buy_slippage,
                sell_slippage_bps=sell_slippage,
                sell_tax_bps=sell_tax,
                buy_execution_price=buy_execution,
                sell_execution_price=sell_execution,
                buy_source=buy_quote.source,
                sell_source=sell_quote.source,
            ),
        )
    except (TypeError, ValueError, OverflowError, ArithmeticError):
        return _unavailable_trade(
            INVALID_INPUT,
            signal if isinstance(signal, SignalResult) else None,
        )


def backtest_snapshot(
    snapshot: PITSnapshot,
    quotes: Iterable[ExecutionQuote],
    config: Config = Config(),
    trading_dates: Iterable[date] | None = None,
) -> TradeResult:
    """Evaluate a snapshot and, only when eligible, run its quote backtest."""

    signal = evaluate_snapshot(snapshot, config)
    return backtest_trade(signal, quotes, config, trading_dates)


# Explicit names make the pure research entry points discoverable to callers.
evaluate = evaluate_snapshot
screen = screen_snapshots
simulate_trade = backtest_trade
run_shadow_backtest = backtest_snapshot


__all__ = [
    "Bar10m",
    "ClosedBar10m",
    "Config",
    "CONTRACT_HASH",
    "CONTRACT_PAYLOAD",
    "ExecutionQuote",
    "PITSnapshot",
    "PRODUCTION_MUTATION_ALLOWED",
    "POLICY_MODE",
    "SignalResult",
    "STRATEGY_VERSION",
    "TradeResult",
    "evaluate",
    "evaluate_snapshot",
    "backtest_snapshot",
    "backtest_trade",
    "run_shadow_backtest",
    "screen",
    "screen_snapshots",
    "simulate_trade",
    "TenMinuteBar",
]
