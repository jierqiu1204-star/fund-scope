"""Contract tests for the pure late-day turnaround shadow strategy."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta
from math import inf, nan
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.late_day_turnaround_shadow import (
    CONTRACT_HASH,
    CONTRACT_PAYLOAD,
    POLICY_MODE,
    STRATEGY_VERSION,
    ClosedBar10m,
    Config,
    ExecutionQuote,
    PITSnapshot,
    SignalResult,
    backtest_trade,
    evaluate_snapshot,
    screen_snapshots,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SIGNAL_DATE = date(2026, 8, 10)
DECISION_AT = datetime(2026, 8, 10, 14, 30, tzinfo=SHANGHAI)


def _bars(
    *,
    latest_close: float = 10.22,
    latest_amount: float = 1_000_000.0,
    future: bool = False,
    start_at: datetime = datetime(2026, 8, 10, 13, 30, tzinfo=SHANGHAI),
) -> tuple[ClosedBar10m, ...]:
    closes = (10.20, 10.20, 10.00, 10.00, 10.00, 10.10, latest_close)
    bars = tuple(
        ClosedBar10m(
            observed_at=start_at + timedelta(minutes=10 * index),
            open=close,
            high=close * 1.002,
            low=close * 0.998,
            close=close,
            volume=100_000.0,
            amount=latest_amount if index == 6 else 1_000_000.0,
        )
        for index, close in enumerate(closes)
    )
    if future:
        bars += (
            ClosedBar10m(
                observed_at=datetime(2026, 8, 10, 15, 0, tzinfo=SHANGHAI),
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=nan,
                amount=nan,
            ),
        )
    return bars


def _snapshot(**changes: object) -> PITSnapshot:
    snapshot = PITSnapshot(
        asset_code="600000",
        signal_date=SIGNAL_DATE,
        decision_at=DECISION_AT,
        today_open=10.00,
        previous_close=9.80,
        current_price=10.30,
        previous_open=10.00,
        cumulative_amount=60_000_000.0,
        bars=_bars(),
    )
    return replace(snapshot, **changes)


def _bars_of_count(count: int) -> tuple[ClosedBar10m, ...]:
    return tuple(
        ClosedBar10m(
            observed_at=datetime(2026, 8, 10, 9, 10, tzinfo=SHANGHAI)
            + timedelta(minutes=10 * index),
            open=10.00,
            high=10.02,
            low=9.98,
            close=10.00,
            volume=100_000.0,
            amount=1_000_000.0,
        )
        for index in range(count)
    )


def test_success_has_frozen_contract_and_provenance() -> None:
    result = evaluate_snapshot(_snapshot())

    assert result.available is True
    assert result.reason == "ok"
    assert result.score is not None
    assert result.provenance["policy_mode"] == POLICY_MODE == "shadow_only"
    assert result.provenance["production_mutation_allowed"] is False
    assert result.provenance["strategy_version"] == STRATEGY_VERSION
    assert result.provenance["contract_hash"] == CONTRACT_HASH
    assert result.provenance["ignored_future_bar_count"] == 0
    with pytest.raises(FrozenInstanceError):
        result.reason = "changed"  # type: ignore[misc]


def test_contract_hash_matches_payload() -> None:
    assert CONTRACT_HASH == stable_contract_hash(CONTRACT_PAYLOAD)


def test_first_day_red_failure_is_stable() -> None:
    result = evaluate_snapshot(_snapshot(previous_close=10.10))

    assert result.available is False
    assert result.reason == "not_first_day_red"


def test_ma5_failure_is_stable() -> None:
    result = evaluate_snapshot(_snapshot(bars=_bars(latest_close=10.00)))

    assert result.available is False
    assert result.reason == "ma5_not_turning_up"


def test_future_bars_are_ignored_and_counted() -> None:
    normal = evaluate_snapshot(_snapshot())
    with_future = evaluate_snapshot(_snapshot(bars=_bars(future=True)))

    assert with_future.available is normal.available
    assert with_future.reason == normal.reason
    assert with_future.score == normal.score
    assert with_future.provenance["ignored_future_bar_count"] == 1


@pytest.mark.parametrize(
    ("bars", "reason"),
    (
        (
            replace(
                _bars()[0],
                observed_at=datetime(2026, 8, 9, 14, 20, tzinfo=SHANGHAI),
            ),
            "invalid_bar",
        ),
        (_bars() + (_bars()[-1],), "invalid_bar"),
        (replace(_bars()[0], high=9.90, low=10.10), "invalid_bar"),
        (_bars_of_count(33), "input_limit_exceeded"),
    ),
)
def test_malformed_or_oversized_bar_history_fails_closed(
    bars: object, reason: str
) -> None:
    result = evaluate_snapshot(_snapshot(bars=bars))

    assert result.available is False
    assert result.reason == reason


def test_stale_latest_bar_is_rejected() -> None:
    stale_bars = tuple(
        replace(bar, observed_at=bar.observed_at - timedelta(minutes=20))
        for bar in _bars()
    )

    result = evaluate_snapshot(_snapshot(bars=stale_bars))

    assert result.available is False
    assert result.reason == "stale_latest_bar"


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"cumulative_amount": 49_999_999.0}, "low_liquidity"),
        ({"current_price": 10.60}, "gain_too_high"),
        ({"bars": _bars(latest_close=10.80)}, "ma_deviation_too_high"),
        (
            {"bars": _bars(latest_amount=100_000.0)},
            "low_liquidity",
        ),
    ),
)
def test_default_filters(changes: dict[str, object], reason: str) -> None:
    result = evaluate_snapshot(_snapshot(**changes))

    assert result.available is False
    assert result.reason == reason


def test_nan_is_rejected_without_raising() -> None:
    assert evaluate_snapshot(_snapshot(current_price=nan)).reason == "invalid_input"
    assert evaluate_snapshot(_snapshot(bars=_bars(latest_close=nan))).reason == "invalid_bar"


def test_decision_window_and_bar_history_are_bounded() -> None:
    before_window = evaluate_snapshot(
        _snapshot(decision_at=datetime(2026, 8, 10, 14, 29, 59, tzinfo=SHANGHAI))
    )
    short_history = evaluate_snapshot(_snapshot(bars=_bars()[:6]))

    assert before_window.reason == "decision_window_closed"
    assert short_history.reason == "insufficient_bars"


def test_screen_is_deterministically_sorted_and_fails_closed_at_limit() -> None:
    first = _snapshot(asset_code="600001")
    second = replace(first, asset_code="600000")
    assert [item.asset_code for item in screen_snapshots([first, second], top_n=2)] == [
        "600000",
        "600001",
    ]
    assert screen_snapshots([first], top_n=0)[0].reason == "invalid_top_n"
    too_many = screen_snapshots([first] * 129)
    assert too_many[0].reason == "input_limit_exceeded"


def _signal() -> SignalResult:
    return evaluate_snapshot(_snapshot())


def _quotes() -> tuple[ExecutionQuote, ...]:
    return (
        ExecutionQuote(
            observed_at=datetime(2026, 8, 10, 14, 40, tzinfo=SHANGHAI),
            price=10.40,
            asset_code="600000",
            source="quote-provider-buy",
        ),
        ExecutionQuote(
            observed_at=datetime(2026, 8, 10, 14, 50, tzinfo=SHANGHAI),
            price=10.60,
            asset_code="600000",
            source="quote-provider-buy-later",
        ),
        ExecutionQuote(
            observed_at=datetime(2026, 8, 11, 10, 5, tzinfo=SHANGHAI),
            price=10.80,
            asset_code="600000",
            source="quote-provider-sell",
        ),
        ExecutionQuote(
            observed_at=datetime(2026, 8, 11, 10, 10, tzinfo=SHANGHAI),
            price=11.20,
            asset_code="600000",
            source="quote-provider-sell-later",
        ),
        ExecutionQuote(
            observed_at=datetime(2026, 8, 11, 12, 0, tzinfo=SHANGHAI),
            price=12.50,
            asset_code="600000",
            source="quote-provider-sell-noon",
        ),
    )


def test_backtest_uses_first_quote_and_fixed_t_plus_one_10am_exit() -> None:
    result = backtest_trade(
        _signal(),
        _quotes(),
        Config(fee_bps=0, slippage_bps=0, sell_tax_bps=0),
        trading_dates=(date(2026, 8, 11),),
    )

    assert result.available is True
    assert result.buy_quote is not None
    assert result.sell_quote is not None
    assert result.buy_quote.observed_at.time() == time(14, 40)
    assert result.sell_quote.observed_at.time() == time(10, 5)
    assert result.net_return_pct == pytest.approx((10.80 / 10.40 - 1) * 100)


def test_fees_slippage_and_tax_reduce_net_return() -> None:
    no_cost = backtest_trade(
        _signal(),
        _quotes(),
        Config(fee_bps=0, slippage_bps=0, sell_tax_bps=0),
        trading_dates=(date(2026, 8, 11),),
    )
    with_cost = backtest_trade(
        _signal(), _quotes(), trading_dates=(date(2026, 8, 11),)
    )

    assert no_cost.net_return_pct is not None
    assert with_cost.net_return_pct is not None
    assert with_cost.net_return_pct < no_cost.net_return_pct
    assert with_cost.provenance["buy_fee_bps"] == 5.0


def test_backtest_requires_explicit_trading_calendar() -> None:
    result = backtest_trade(_signal(), _quotes())

    assert result.available is False
    assert result.reason == "missing_trading_calendar"


@pytest.mark.parametrize("quotes, reason", (((), "missing_buy_quote"),))
def test_missing_buy_quote_is_unavailable(quotes: tuple[object, ...], reason: str) -> None:
    result = backtest_trade(
        _signal(), quotes, trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == reason


def test_missing_sell_quote_is_unavailable() -> None:
    buy_only = _quotes()[:2]
    result = backtest_trade(
        _signal(), buy_only, trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == "missing_sell_quote"


def test_invalid_quote_and_aware_time_are_rejected() -> None:
    invalid = (ExecutionQuote(observed_at=datetime(2026, 8, 10, 14, 40), price=10.4),)
    assert (
        backtest_trade(
            _signal(), invalid, trading_dates=(date(2026, 8, 11),)
        ).reason
        == "invalid_quote"
    )
    assert evaluate_snapshot(_snapshot(decision_at=datetime(2026, 8, 10, 14, 30))).reason == "invalid_input"


def test_infinite_quote_price_is_rejected() -> None:
    invalid = replace(_quotes()[0], price=inf)

    result = backtest_trade(
        _signal(), (invalid,), trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == "invalid_quote"


def test_quote_code_mismatch_is_rejected() -> None:
    mismatched = tuple(replace(quote, asset_code="600001") for quote in _quotes())

    result = backtest_trade(
        _signal(), mismatched, trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == "quote_code_mismatch"


def test_quote_input_is_bounded_at_513_items() -> None:
    quotes = _quotes() * 103

    result = backtest_trade(
        _signal(), quotes[:513], trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == "input_limit_exceeded"


def test_guarded_unbounded_quote_stream_is_not_fully_consumed() -> None:
    def guarded_quotes() -> object:
        for index in range(513):
            yield _quotes()[index % len(_quotes())]
        raise AssertionError("quote iterator was consumed past its bound")

    result = backtest_trade(
        _signal(), guarded_quotes(), trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == "input_limit_exceeded"


def test_fake_signal_result_contract_is_rejected() -> None:
    fake_signal = SimpleNamespace(
        available=True,
        asset_code="600000",
        signal_date=SIGNAL_DATE,
        decision_at=DECISION_AT,
    )

    result = backtest_trade(
        fake_signal, _quotes(), trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == "signal_unavailable"


@pytest.mark.parametrize(
    "config",
    (
        Config(buy_fee_bps=10_000.0),
        Config(sell_fee_bps=9_995.0, sell_tax_bps=5.0),
    ),
)
def test_invalid_fee_boundary_or_composition_fails_closed(config: Config) -> None:
    result = backtest_trade(
        _signal(), _quotes(), config, trading_dates=(date(2026, 8, 11),)
    )

    assert result.available is False
    assert result.reason == "invalid_config"


def test_success_provenance_includes_buy_and_sell_sources() -> None:
    result = backtest_trade(
        _signal(),
        _quotes(),
        Config(fee_bps=0, slippage_bps=0, sell_tax_bps=0),
        trading_dates=(date(2026, 8, 11),),
    )

    assert result.available is True
    assert result.provenance["buy_source"] == "quote-provider-buy"
    assert result.provenance["sell_source"] == "quote-provider-sell"


def test_custom_trading_calendar_selects_next_provided_date() -> None:
    result = backtest_trade(
        _signal(),
        _quotes(),
        Config(fee_bps=0, slippage_bps=0, sell_tax_bps=0),
        trading_dates=(date(2026, 8, 12),),
    )

    assert result.available is False
    assert result.reason == "missing_sell_quote"
    assert result.provenance["t_plus_one_date"] == date(2026, 8, 12)


def test_trading_calendar_is_bounded_at_513_items() -> None:
    consumed = 0

    def guarded_trading_dates() -> object:
        nonlocal consumed
        for index in range(513):
            consumed += 1
            yield SIGNAL_DATE + timedelta(days=index + 1)
        raise AssertionError("trading calendar was consumed past its bound")

    result = backtest_trade(
        _signal(), _quotes(), trading_dates=guarded_trading_dates()
    )

    assert result.available is False
    assert result.reason == "input_limit_exceeded"
    assert consumed == 513


@pytest.mark.parametrize(
    "invalid_date",
    (
        datetime(2026, 8, 11, tzinfo=SHANGHAI),
        "2026-08-11",
    ),
)
def test_invalid_trading_calendar_item_fails_closed(invalid_date: object) -> None:
    result = backtest_trade(
        _signal(),
        _quotes(),
        trading_dates=(date(2026, 8, 11), invalid_date),
    )

    assert result.available is False
    assert result.reason == "invalid_input"


def test_ast_keeps_the_strategy_domain_isolated() -> None:
    path = Path(__file__).parents[1] / "app/services/strategy_lab/late_day_turnaround_shadow.py"
    module_source = path.read_text(encoding="utf-8")
    forbidden = (
        "risk_alerts",
        "tracked_positions",
        "notifier",
        "notification",
        "scheduler",
    )
    assert all(token not in module_source for token in forbidden)
