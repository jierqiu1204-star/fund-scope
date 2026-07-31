"""Exploratory adjusted-MA5 lifecycle for sealed leader research entries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from statistics import fmean
from typing import Literal

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    LEADER_CANDIDATE_IDS,
    MA5_EXIT_POLICY_ID,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    ForwardAdjustedClose,
)

MA5_MAX_COMPARISON_SESSIONS = 10
INTRADAY_T_POLICY_STATE = "unavailable_no_executable_pit_model"


@dataclass(frozen=True)
class SealedLeaderEntry:
    candidate_id: str
    asset_code: str
    signal_date: date
    source_sample_hash: str

    def validate(self) -> None:
        if self.candidate_id not in LEADER_CANDIDATE_IDS:
            raise ValueError("sealed entry candidate is outside the frozen registry")
        if not self.asset_code.strip() or len(self.source_sample_hash) != 64:
            raise ValueError("sealed entry identity is incomplete")


@dataclass(frozen=True)
class LeaderMa5PolicyResult:
    candidate_id: str
    asset_code: str
    signal_date: date
    entry_session: date | None
    trigger_session: date | None
    exit_session: date | None
    exit_reason: str | None
    status: Literal["completed", "unavailable"]
    unavailable_reason: str | None
    ma5_gross_return: float | None
    ma5_net_return: float | None
    fixed_5_session_net_return: float | None
    fixed_10_session_net_return: float | None
    fee_bps_per_side: int
    slippage_bps_per_side: int
    cost_contract_hash: str
    policy_id: str
    policy_mode: Literal["policy_shadow"]
    execution_provenance: Literal["simulated_execution"]
    notification_provenance: Literal["none"]
    production_mutation_allowed: Literal[False]
    intraday_t_policy_state: str
    result_hash: str


def _net_return(entry: float, exit_price: float) -> tuple[float, float]:
    fee = RANKING_FEE_BPS_PER_SIDE / 10_000
    slippage = RANKING_SLIPPAGE_BPS_PER_SIDE / 10_000
    gross = exit_price / entry - 1.0
    net = (
        exit_price
        * (1.0 - fee)
        * (1.0 - slippage)
        / (entry * (1.0 + fee) * (1.0 + slippage))
        - 1.0
    )
    return gross, net


def _result(**values) -> LeaderMa5PolicyResult:
    draft = LeaderMa5PolicyResult(**values, result_hash="pending")
    payload = asdict(draft)
    payload.pop("result_hash")
    return LeaderMa5PolicyResult(**values, result_hash=stable_contract_hash(payload))


def evaluate_ma5_exit_proxy(
    entries: Sequence[SealedLeaderEntry],
    *,
    trading_sessions: Sequence[date],
    adjusted_closes_by_code: Mapping[str, Sequence[ForwardAdjustedClose]],
) -> tuple[LeaderMa5PolicyResult, ...]:
    """Compare next-close MA5 exit with fixed five/ten-session research holds."""

    calendar = tuple(trading_sessions)
    if calendar != tuple(sorted(set(calendar))):
        raise ValueError("trading sessions must be unique and chronological")
    output: list[LeaderMa5PolicyResult] = []
    common = {
        "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
        "slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "policy_id": MA5_EXIT_POLICY_ID,
        "policy_mode": "policy_shadow",
        "execution_provenance": "simulated_execution",
        "notification_provenance": "none",
        "production_mutation_allowed": False,
        "intraday_t_policy_state": INTRADAY_T_POLICY_STATE,
    }
    for entry in sorted(entries, key=lambda item: (item.signal_date, item.asset_code)):
        entry.validate()
        try:
            signal_index = calendar.index(entry.signal_date)
        except ValueError:
            signal_index = -1
        rows = adjusted_closes_by_code.get(entry.asset_code, ())
        closes = {item.session_date: item.adjusted_close for item in rows}
        entry_index = signal_index + 1
        entry_session = (
            calendar[entry_index]
            if signal_index >= 0 and entry_index < len(calendar)
            else None
        )
        entry_price = closes.get(entry_session) if entry_session is not None else None
        unavailable_reason: str | None = None
        if signal_index < 0:
            unavailable_reason = "signal_date_outside_calendar"
        elif entry_session is None or entry_price is None:
            unavailable_reason = "missing_adjusted_entry_price"
        fixed_sessions = {
            horizon: (
                calendar[entry_index + horizon]
                if entry_index + horizon < len(calendar)
                else None
            )
            for horizon in (5, 10)
        }
        fixed_prices = {
            horizon: closes.get(session) if session is not None else None
            for horizon, session in fixed_sessions.items()
        }
        if unavailable_reason is None and fixed_prices[10] is None:
            unavailable_reason = "future_window_pending"
        trigger_session: date | None = None
        exit_session: date | None = None
        exit_reason: str | None = None
        if unavailable_reason is None:
            for observation_index in range(
                entry_index,
                min(entry_index + MA5_MAX_COMPARISON_SESSIONS, len(calendar) - 1),
            ):
                observation_session = calendar[observation_index]
                history_sessions = calendar[max(0, observation_index - 4) : observation_index + 1]
                history = [closes.get(session) for session in history_sessions]
                if len(history) < 5 or any(value is None for value in history):
                    continue
                observation_close = closes.get(observation_session)
                assert observation_close is not None
                adjusted_ma5 = fmean(float(value) for value in history if value is not None)
                if observation_close < adjusted_ma5:
                    trigger_session = observation_session
                    exit_session = calendar[observation_index + 1]
                    exit_reason = "adjusted_close_below_same_session_adjusted_ma5"
                    break
            if exit_session is None:
                exit_session = fixed_sessions[10]
                exit_reason = "maximum_10_session_comparison_window"
            if exit_session is None or closes.get(exit_session) is None:
                unavailable_reason = "missing_future_exit_price"

        if unavailable_reason is not None or entry_price is None:
            output.append(
                _result(
                    candidate_id=entry.candidate_id,
                    asset_code=entry.asset_code,
                    signal_date=entry.signal_date,
                    entry_session=entry_session,
                    trigger_session=trigger_session,
                    exit_session=exit_session,
                    exit_reason=exit_reason,
                    status="unavailable",
                    unavailable_reason=unavailable_reason,
                    ma5_gross_return=None,
                    ma5_net_return=None,
                    fixed_5_session_net_return=None,
                    fixed_10_session_net_return=None,
                    **common,
                )
            )
            continue
        assert exit_session is not None
        ma5_gross, ma5_net = _net_return(entry_price, closes[exit_session])
        fixed_net: dict[int, float | None] = {}
        for horizon in (5, 10):
            price = fixed_prices[horizon]
            fixed_net[horizon] = (
                _net_return(entry_price, price)[1] if price is not None else None
            )
        output.append(
            _result(
                candidate_id=entry.candidate_id,
                asset_code=entry.asset_code,
                signal_date=entry.signal_date,
                entry_session=entry_session,
                trigger_session=trigger_session,
                exit_session=exit_session,
                exit_reason=exit_reason,
                status="completed",
                unavailable_reason=None,
                ma5_gross_return=ma5_gross,
                ma5_net_return=ma5_net,
                fixed_5_session_net_return=fixed_net[5],
                fixed_10_session_net_return=fixed_net[10],
                **common,
            )
        )
    return tuple(output)
