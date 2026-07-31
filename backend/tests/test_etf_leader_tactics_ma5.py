from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

from app.services.strategy_lab.etf_leader_tactics_ma5 import (
    INTRADAY_T_POLICY_STATE,
    SealedLeaderEntry,
    evaluate_ma5_exit_proxy,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    LEADER_BREAKOUT_CANDIDATE,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    ForwardAdjustedClose,
)


def _calendar() -> tuple[date, ...]:
    return tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(20))


def _entry() -> SealedLeaderEntry:
    return SealedLeaderEntry(
        candidate_id=LEADER_BREAKOUT_CANDIDATE,
        asset_code="510001",
        signal_date=_calendar()[5],
        source_sample_hash="a" * 64,
    )


def _rows(*, drop_index: int | None = 8, count: int = 20):
    rows: list[ForwardAdjustedClose] = []
    for index, session_date in enumerate(_calendar()[:count]):
        close = 100.0 + index
        if drop_index is not None and index == drop_index:
            close = 90.0
        rows.append(
            ForwardAdjustedClose(
                asset_code="510001",
                session_date=session_date,
                adjusted_close=close,
                price_basis="total_return_adjusted",
                decision_eligible=True,
                provider="eastmoney",
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                source_hash=f"row-{index}",
            )
        )
    return rows


def test_ma5_observes_same_session_and_exits_at_next_eligible_close() -> None:
    result = evaluate_ma5_exit_proxy(
        (_entry(),),
        trading_sessions=_calendar(),
        adjusted_closes_by_code={"510001": _rows()},
    )[0]

    assert result.entry_session == _calendar()[6]
    assert result.trigger_session == _calendar()[8]
    assert result.exit_session == _calendar()[9]
    assert result.exit_reason == "adjusted_close_below_same_session_adjusted_ma5"
    assert result.status == "completed"
    assert result.ma5_gross_return is not None
    assert result.ma5_net_return is not None
    assert result.ma5_net_return < result.ma5_gross_return
    assert result.fixed_5_session_net_return is not None
    assert result.fixed_10_session_net_return is not None


def test_ma5_without_trigger_stops_at_frozen_ten_session_window() -> None:
    result = evaluate_ma5_exit_proxy(
        (_entry(),),
        trading_sessions=_calendar(),
        adjusted_closes_by_code={"510001": _rows(drop_index=None)},
    )[0]

    assert result.trigger_session is None
    assert result.exit_session == _calendar()[16]
    assert result.exit_reason == "maximum_10_session_comparison_window"


def test_ma5_missing_future_price_is_unavailable_without_fill_fabrication() -> None:
    result = evaluate_ma5_exit_proxy(
        (_entry(),),
        trading_sessions=_calendar(),
        adjusted_closes_by_code={"510001": _rows(drop_index=None, count=12)},
    )[0]

    assert result.status == "unavailable"
    assert result.unavailable_reason == "future_window_pending"
    assert result.ma5_net_return is None


def test_ma5_policy_is_shadow_only_and_intraday_t_stays_unavailable() -> None:
    result = evaluate_ma5_exit_proxy(
        (_entry(),),
        trading_sessions=_calendar(),
        adjusted_closes_by_code={"510001": _rows()},
    )[0]

    assert result.policy_mode == "policy_shadow"
    assert result.notification_provenance == "none"
    assert result.execution_provenance == "simulated_execution"
    assert result.production_mutation_allowed is False
    assert result.intraday_t_policy_state == INTRADAY_T_POLICY_STATE

    module = Path(
        "app/services/strategy_lab/etf_leader_tactics_ma5.py"
    ).read_text(encoding="utf-8")
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(module))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        forbidden in name
        for name in imported
        for forbidden in (
            "risk_alerts",
            "tracked_positions",
            "notifier",
            "notification",
        )
    )
