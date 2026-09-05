"""Point-in-time forward outcomes for frozen ETF ranking candidates."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

from app.services.tracked_positions.lifecycle import stable_contract_hash

from .etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
    RankingCandidateSelection,
)

FORWARD_HORIZONS = (1, 3, 5, 10)
SUPPORTED_FORWARD_HORIZONS = (*FORWARD_HORIZONS, 20)
FORWARD_EXECUTION_MODEL = "t_plus_one_adjusted_close_v1"
_BPS_DENOMINATOR = 10_000.0
FACTUAL_CUTOFF_VALID_COST_PROVENANCE = "factual_cutoff_valid"
FROZEN_CONSERVATIVE_COST_PROVENANCE = "frozen_conservative_fallback"
NOT_EVALUATED_COST_PROVENANCE = "not_evaluated"
FORWARD_COST_PROVENANCE_CONTRACT_HASH = stable_contract_hash(
    {
        "contract_id": "ranking_forward_execution_cost_provenance_v1",
        "factual_inputs": (
            "bid_ask_or_declared_spread",
            "liquidity_notional",
            "declared_liquidity_cost_bps_per_side",
            "quote_time",
            "available_at",
            "execution_cutoff",
            "provider",
            "source_hash",
        ),
        "factual_formula": (
            "fee_bps_per_side_plus_half_spread_bps_plus_declared_liquidity_cost"
        ),
        "fallback": {
            "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
            "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
            "slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        },
        "no_intraday_reconstruction": True,
    }
)

PURE_MOMENTUM_CONTROL_ID = "positive_adjusted_return_20_session_top10"
PURE_MOMENTUM_LOOKBACK_SESSIONS = 20
PURE_MOMENTUM_TARGET_WEIGHT = 0.10
PURE_MOMENTUM_CONTROL_CONTRACT_HASH = stable_contract_hash(
    {
        "contract_id": "positive_adjusted_return_20_session_top10_v1",
        "lookback_sessions": PURE_MOMENTUM_LOOKBACK_SESSIONS,
        "eligibility": "positive_return_and_caller_declared_common_support",
        "ordering": ("adjusted_return_desc", "asset_code_asc"),
        "top_n": 10,
        "target_weight_per_asset": PURE_MOMENTUM_TARGET_WEIGHT,
        "unfilled_weight": "cash",
        "promotion_candidate": False,
    }
)
CONTINUOUS_RANKING_EXECUTION_MODEL = (
    "t_plus_one_adjusted_close_continuous_cash_share_v1"
)


@dataclass(frozen=True)
class PureMomentumControlSelection:
    signal_date: date
    selected_asset_codes: tuple[str, ...]
    momentum_returns: tuple[tuple[str, float], ...]
    target_weights: tuple[tuple[str, float], ...]
    cash_target_weight: float
    requested_asset_count: int
    priced_asset_count: int
    positive_asset_count: int
    coverage_ratio: float
    exclusions: tuple[tuple[str, str], ...]
    contract_hash: str
    input_hash: str
    selection_hash: str


@dataclass(frozen=True)
class RankingPortfolioTarget:
    signal_date: date
    target_weights: tuple[tuple[str, float], ...]
    source_hash: str
    target_hash: str


@dataclass(frozen=True)
class RankingPortfolioCostPolicy:
    scenario: Literal["base", "stress"]
    fee_bps_per_side: float
    slippage_bps_per_side: float
    provenance: str
    contract_hash: str


@dataclass(frozen=True)
class RankingPortfolioPoint:
    session_date: date
    pre_rebalance_net_value: float
    post_rebalance_net_value: float
    pre_rebalance_gross_value: float
    post_rebalance_gross_value: float
    net_cash: float
    gross_cash: float
    net_holdings: tuple[tuple[str, float], ...]
    gross_holdings: tuple[tuple[str, float], ...]
    target_hash: str | None
    net_trade_notional: float
    gross_trade_notional: float
    transaction_cost: float
    order_count: int


@dataclass(frozen=True)
class RankingPortfolioUnavailableInterval:
    start_session: date
    end_session: date
    asset_codes: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class RankingPortfolioLedger:
    status: Literal["completed", "unavailable"]
    execution_model: str
    cost_scenario: str
    cost_contract_hash: str
    cost_provenance: str
    fee_bps_per_side: float
    slippage_bps_per_side: float
    initial_capital: float
    points: tuple[RankingPortfolioPoint, ...]
    net_return: float | None
    gross_return: float | None
    net_maximum_drawdown: float | None
    gross_maximum_drawdown: float | None
    turnover: float
    total_transaction_cost: float
    rebalance_count: int
    order_count: int
    final_net_cash: float | None
    final_gross_cash: float | None
    unavailable_intervals: tuple[RankingPortfolioUnavailableInterval, ...]
    market_data_hash: str
    input_hash: str
    ledger_hash: str


def _portfolio_cost_policy(
    scenario: Literal["base", "stress"],
    *,
    slippage_bps_per_side: float,
) -> RankingPortfolioCostPolicy:
    payload = {
        "contract_id": f"ranking_continuous_actual_trade_cost_{scenario}_v1",
        "fee_bps_per_side": float(RANKING_FEE_BPS_PER_SIDE),
        "slippage_bps_per_side": slippage_bps_per_side,
        "application": "actual_trade_notional_each_side",
        "source_cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "parameter_selection_allowed": False,
    }
    return RankingPortfolioCostPolicy(
        scenario=scenario,
        fee_bps_per_side=float(RANKING_FEE_BPS_PER_SIDE),
        slippage_bps_per_side=slippage_bps_per_side,
        provenance="frozen_research_cost_policy",
        contract_hash=stable_contract_hash(payload),
    )


RANKING_PORTFOLIO_BASE_COST_POLICY = _portfolio_cost_policy(
    "base",
    slippage_bps_per_side=float(RANKING_SLIPPAGE_BPS_PER_SIDE),
)
RANKING_PORTFOLIO_STRESS_COST_POLICY = _portfolio_cost_policy(
    "stress",
    slippage_bps_per_side=10.0,
)


class ForwardOutcomeContractError(ValueError):
    """Raised when a forward-outcome input violates the frozen contract."""


@dataclass(frozen=True)
class ForwardAdjustedClose:
    """One decision-eligible total-return-adjusted close observation."""

    asset_code: str
    session_date: date
    adjusted_close: float
    price_basis: str
    decision_eligible: bool
    provider: str
    adjustment_version: str
    source_hash: str

    def __post_init__(self) -> None:
        if not self.asset_code.strip():
            raise ForwardOutcomeContractError("adjusted close asset code is required")
        if self.price_basis != "total_return_adjusted":
            raise ForwardOutcomeContractError(
                "forward outcomes require total-return-adjusted prices"
            )
        if self.decision_eligible is not True:
            raise ForwardOutcomeContractError(
                "forward outcomes require decision-eligible adjusted prices"
            )
        if (
            isinstance(self.adjusted_close, bool)
            or not math.isfinite(self.adjusted_close)
            or self.adjusted_close <= 0.0
        ):
            raise ForwardOutcomeContractError(
                "adjusted close must be finite and positive"
            )
        if not self.provider.strip() or not self.adjustment_version.strip():
            raise ForwardOutcomeContractError(
                "adjusted close provider and adjustment version are required"
            )
        if not self.source_hash.strip():
            raise ForwardOutcomeContractError("adjusted close source hash is required")


@dataclass(frozen=True)
class ForwardExecutionCostEvidence:
    """Factual execution evidence available at an individual trade cutoff.

    This is deliberately a research input rather than a quote reconstruction
    adapter.  A missing, stale, fallback-timestamped, or later-visible fact is
    retained as an explicit fallback reason instead of being filled from a
    current quote or adjusted close.
    """

    asset_code: str
    execution_session: date
    provider: str
    source_hash: str
    quote_time: datetime | None = None
    available_at: datetime | None = None
    execution_cutoff: datetime | None = None
    bid: float | None = None
    ask: float | None = None
    quoted_spread_bps: float | None = None
    liquidity_notional: float | None = None
    liquidity_cost_bps_per_side: float | None = None
    quote_time_is_fallback: bool = False
    decision_eligible: bool = True

    @property
    def evidence_hash(self) -> str:
        return stable_contract_hash(asdict(self))


@dataclass(frozen=True)
class RankingForwardOutcome:
    replay_run_key: str
    replay_date: date
    candidate_id: str
    candidate_manifest_hash: str
    candidate_registry_hash: str
    source_selection_hash: str
    asset_code: str
    horizon_sessions: int
    status: Literal["completed", "excluded", "pending"]
    entry_session: date | None
    exit_session: date | None
    entry_adjusted_close: float | None
    exit_adjusted_close: float | None
    gross_return: float | None
    net_return: float | None
    fee_bps_per_side: float
    slippage_bps_per_side: float
    round_trip_cost_bps: float
    cost_contract_hash: str
    cost_provenance: Literal[
        "factual_cutoff_valid",
        "frozen_conservative_fallback",
        "not_evaluated",
    ]
    cost_provenance_contract_hash: str
    cost_evidence: tuple[ForwardExecutionCostEvidence, ...]
    cost_input_hashes: tuple[str, ...]
    cost_source_hashes: tuple[str, ...]
    cost_unavailable_reasons: tuple[str, ...]
    entry_slippage_bps_per_side: float | None
    exit_slippage_bps_per_side: float | None
    entry_factual_spread_bps: float | None
    exit_factual_spread_bps: float | None
    entry_liquidity_cost_bps_per_side: float | None
    exit_liquidity_cost_bps_per_side: float | None
    exclusion_reason: str | None
    missing_leg: Literal["entry", "exit"] | None
    input_hash: str
    outcome_hash: str


@dataclass(frozen=True)
class RankingForwardOutcomeStatusCount:
    horizon_sessions: int
    requested_count: int
    completed_count: int
    pending_count: int
    excluded_count: int


@dataclass(frozen=True)
class RankingForwardOutcomeBundle:
    replay_run_key: str
    replay_date: date
    candidate_id: str
    candidate_manifest_hash: str
    candidate_registry_hash: str
    source_selection_hash: str
    execution_model: str
    horizons: tuple[int, ...]
    fee_bps_per_side: float
    slippage_bps_per_side: float
    round_trip_cost_bps: float
    cost_contract_hash: str
    cost_provenance_contract_hash: str
    cost_provenance_counts: tuple[tuple[str, int], ...]
    cost_unavailable_reason_counts: tuple[tuple[str, int], ...]
    cost_input_hashes: tuple[str, ...]
    execution_cost_evidence_hash: str
    requested_outcome_count: int
    completed_outcome_count: int
    pending_outcome_count: int
    excluded_outcome_count: int
    status_counts_by_horizon: tuple[RankingForwardOutcomeStatusCount, ...]
    outcomes: tuple[RankingForwardOutcome, ...]
    input_hash: str
    bundle_hash: str


def _validate_selection(selection: RankingCandidateSelection) -> None:
    selection_payload = asdict(selection)
    selection_payload.pop("selection_hash")
    if (
        not selection.replay_run_key.strip()
        or not selection.candidate_id.strip()
        or not selection.candidate_manifest_hash.strip()
        or not selection.candidate_registry_hash.strip()
        or not selection.source_ranking_event_hash.strip()
        or selection.selection_hash
        != stable_contract_hash(selection_payload)
    ):
        raise ForwardOutcomeContractError("ranking candidate selection is invalid")
    if len(selection.selected_asset_codes) != len(
        set(selection.selected_asset_codes)
    ) or any(not code.strip() for code in selection.selected_asset_codes):
        raise ForwardOutcomeContractError(
            "ranking candidate selection contains invalid assets"
        )


def _validate_sessions(
    trading_sessions: Sequence[date],
    *,
    signal_date: date,
) -> tuple[date, ...]:
    sessions = tuple(trading_sessions)
    if not sessions or sessions != tuple(sorted(sessions)):
        raise ForwardOutcomeContractError(
            "trading sessions must be non-empty and increasing"
        )
    if len(sessions) != len(set(sessions)):
        raise ForwardOutcomeContractError("trading sessions contain duplicates")
    if signal_date not in sessions:
        raise ForwardOutcomeContractError(
            "selection date is absent from the trading-session calendar"
        )
    return sessions


def _validate_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(horizons)
    if not values or len(values) != len(set(values)):
        raise ForwardOutcomeContractError(
            "forward horizons must be a non-empty unique frozen subset"
        )
    if any(value not in SUPPORTED_FORWARD_HORIZONS for value in values):
        raise ForwardOutcomeContractError(
            "forward horizons must use the frozen supported 1/3/5/10/20-session set"
        )
    return tuple(value for value in SUPPORTED_FORWARD_HORIZONS if value in set(values))


def _index_adjusted_closes(
    rows: Iterable[ForwardAdjustedClose],
    *,
    trading_sessions: tuple[date, ...],
) -> tuple[
    dict[tuple[str, date], ForwardAdjustedClose],
    tuple[ForwardAdjustedClose, ...],
]:
    by_key: dict[tuple[str, date], ForwardAdjustedClose] = {}
    valid_sessions = set(trading_sessions)
    for row in rows:
        if row.session_date not in valid_sessions:
            raise ForwardOutcomeContractError(
                "adjusted close is outside the trading-session calendar"
            )
        key = (row.asset_code, row.session_date)
        if key in by_key:
            raise ForwardOutcomeContractError("duplicate adjusted close observation")
        by_key[key] = row
    ordered = tuple(
        sorted(by_key.values(), key=lambda row: (row.asset_code, row.session_date))
    )
    return by_key, ordered


def select_positive_momentum_top10(
    *,
    signal_date: date,
    eligible_asset_codes: Iterable[str],
    trading_sessions: Sequence[date],
    adjusted_closes: Iterable[ForwardAdjustedClose],
) -> PureMomentumControlSelection:
    """Build the frozen 20-session positive-momentum diagnostic control."""

    sessions = _validate_sessions(trading_sessions, signal_date=signal_date)
    codes = tuple(eligible_asset_codes)
    if (
        len(codes) != len(set(codes))
        or any(not isinstance(code, str) or not code.strip() for code in codes)
    ):
        raise ForwardOutcomeContractError(
            "momentum-control eligible assets must be unique non-empty codes"
        )
    closes, ordered_closes = _index_adjusted_closes(
        adjusted_closes,
        trading_sessions=sessions,
    )
    signal_index = sessions.index(signal_date)
    lookback_date = (
        sessions[signal_index - PURE_MOMENTUM_LOOKBACK_SESSIONS]
        if signal_index >= PURE_MOMENTUM_LOOKBACK_SESSIONS
        else None
    )
    momentum: list[tuple[str, float]] = []
    exclusions: list[tuple[str, str]] = []
    priced_count = 0
    for code in sorted(codes):
        if lookback_date is None:
            exclusions.append((code, "insufficient_20_session_history"))
            continue
        start = closes.get((code, lookback_date))
        end = closes.get((code, signal_date))
        if start is None or end is None:
            exclusions.append((code, "missing_adjusted_momentum_price"))
            continue
        priced_count += 1
        value = end.adjusted_close / start.adjusted_close - 1.0
        if value <= 0.0:
            exclusions.append((code, "non_positive_20_session_momentum"))
            continue
        momentum.append((code, value))
    momentum.sort(key=lambda item: (-item[1], item[0]))
    selected = tuple(code for code, _value in momentum[:10])
    input_hash = stable_contract_hash(
        {
            "contract_hash": PURE_MOMENTUM_CONTROL_CONTRACT_HASH,
            "signal_date": signal_date,
            "lookback_date": lookback_date,
            "eligible_asset_codes": tuple(sorted(codes)),
            "trading_sessions": sessions,
            "adjusted_closes": tuple(asdict(row) for row in ordered_closes),
        }
    )
    draft = PureMomentumControlSelection(
        signal_date=signal_date,
        selected_asset_codes=selected,
        momentum_returns=tuple(momentum),
        target_weights=tuple(
            (code, PURE_MOMENTUM_TARGET_WEIGHT) for code in selected
        ),
        cash_target_weight=1.0 - len(selected) * PURE_MOMENTUM_TARGET_WEIGHT,
        requested_asset_count=len(codes),
        priced_asset_count=priced_count,
        positive_asset_count=len(momentum),
        coverage_ratio=priced_count / len(codes) if codes else 0.0,
        exclusions=tuple(sorted(exclusions)),
        contract_hash=PURE_MOMENTUM_CONTROL_CONTRACT_HASH,
        input_hash=input_hash,
        selection_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("selection_hash")
    return replace(draft, selection_hash=stable_contract_hash(payload))


def freeze_ranking_portfolio_target(
    *,
    signal_date: date,
    target_weights: Mapping[str, float] | Iterable[tuple[str, float]],
    source_hash: str,
) -> RankingPortfolioTarget:
    """Freeze one signal-date target without inventing weights for empty slots."""

    items = tuple(
        target_weights.items()
        if isinstance(target_weights, Mapping)
        else target_weights
    )
    if len(items) != len({code for code, _weight in items}):
        raise ForwardOutcomeContractError("portfolio target contains duplicate assets")
    normalized: list[tuple[str, float]] = []
    for code, weight in items:
        if not isinstance(code, str) or not code.strip():
            raise ForwardOutcomeContractError("portfolio target asset code is required")
        if (
            isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or float(weight) < 0.0
        ):
            raise ForwardOutcomeContractError(
                "portfolio target weights must be finite and non-negative"
            )
        if float(weight) > 0.0:
            normalized.append((code, float(weight)))
    normalized.sort()
    if sum(weight for _code, weight in normalized) > 1.0 + 1e-12:
        raise ForwardOutcomeContractError("portfolio target weights exceed capital")
    if len(source_hash) != 64:
        raise ForwardOutcomeContractError("portfolio target source hash is required")
    payload = {
        "schema_version": "ranking_continuous_portfolio_target_v1",
        "signal_date": signal_date,
        "target_weights": tuple(normalized),
        "source_hash": source_hash,
    }
    return RankingPortfolioTarget(
        signal_date=signal_date,
        target_weights=tuple(normalized),
        source_hash=source_hash,
        target_hash=stable_contract_hash(payload),
    )


def _account_value(
    cash: float,
    holdings: Mapping[str, float],
    prices: Mapping[str, float],
) -> float:
    return cash + sum(quantity * prices[code] for code, quantity in holdings.items())


def _rebalance_account(
    *,
    cash: float,
    holdings: Mapping[str, float],
    prices: Mapping[str, float],
    target_weights: Mapping[str, float],
    cost_rate: float,
) -> tuple[float, dict[str, float], float, float, int]:
    """Sell first, then cash-limit buys; charge only executed notional."""

    next_holdings = dict(holdings)
    pre_value = _account_value(cash, next_holdings, prices)
    target_values = {
        code: pre_value * weight for code, weight in target_weights.items()
    }
    traded = 0.0
    costs = 0.0
    orders = 0
    for code in sorted(next_holdings):
        current_value = next_holdings[code] * prices[code]
        sell_notional = current_value - target_values.get(code, 0.0)
        if sell_notional <= 1e-15:
            continue
        next_holdings[code] -= sell_notional / prices[code]
        cash += sell_notional * (1.0 - cost_rate)
        traded += sell_notional
        costs += sell_notional * cost_rate
        orders += 1
    buy_needs = {
        code: max(
            0.0,
            target_value - next_holdings.get(code, 0.0) * prices[code],
        )
        for code, target_value in target_values.items()
    }
    total_buy_need = sum(buy_needs.values())
    affordable = cash / (1.0 + cost_rate) if cost_rate >= 0.0 else cash
    buy_scale = min(1.0, affordable / total_buy_need) if total_buy_need else 0.0
    for code in sorted(buy_needs):
        buy_notional = buy_needs[code] * buy_scale
        if buy_notional <= 1e-15:
            continue
        next_holdings[code] = (
            next_holdings.get(code, 0.0) + buy_notional / prices[code]
        )
        cash -= buy_notional * (1.0 + cost_rate)
        traded += buy_notional
        costs += buy_notional * cost_rate
        orders += 1
    next_holdings = {
        code: quantity
        for code, quantity in next_holdings.items()
        if quantity > 1e-15
    }
    if cash < -1e-12:
        raise ForwardOutcomeContractError("portfolio rebalance overdraws cash")
    return max(cash, 0.0), next_holdings, traded, costs, orders


def _capital_maximum_drawdown(values: Sequence[float]) -> float:
    peak = values[0]
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak)
    return maximum


def calculate_continuous_ranking_portfolio(
    *,
    trading_sessions: Sequence[date],
    adjusted_closes: Iterable[ForwardAdjustedClose],
    targets: Iterable[RankingPortfolioTarget],
    cost_policy: RankingPortfolioCostPolicy = RANKING_PORTFOLIO_BASE_COST_POLICY,
    initial_capital: float = 1.0,
    required_signal_dates: Iterable[date] = (),
) -> RankingPortfolioLedger:
    """Run one deterministic cash/share account and its zero-cost companion."""

    sessions = tuple(trading_sessions)
    if (
        not sessions
        or sessions != tuple(sorted(set(sessions)))
        or isinstance(initial_capital, bool)
        or not math.isfinite(initial_capital)
        or initial_capital <= 0.0
    ):
        raise ForwardOutcomeContractError(
            "continuous portfolio requires ordered sessions and positive capital"
        )
    if cost_policy not in {
        RANKING_PORTFOLIO_BASE_COST_POLICY,
        RANKING_PORTFOLIO_STRESS_COST_POLICY,
    }:
        raise ForwardOutcomeContractError("continuous portfolio cost policy is not frozen")
    closes, ordered_closes = _index_adjusted_closes(
        adjusted_closes,
        trading_sessions=sessions,
    )
    target_values = tuple(targets)
    if len(target_values) != len({item.signal_date for item in target_values}):
        raise ForwardOutcomeContractError("duplicate portfolio target signal date")
    execution_targets: dict[date, RankingPortfolioTarget] = {}
    required_dates = tuple(sorted(set(required_signal_dates)))
    if any(item not in sessions for item in required_dates):
        raise ForwardOutcomeContractError("required signal date is outside the calendar")
    missing_execution_dates = {
        sessions[sessions.index(item) + 1]
        for item in required_dates
        if item not in {target.signal_date for target in target_values}
        and sessions.index(item) + 1 < len(sessions)
    }
    for target in target_values:
        canonical = freeze_ranking_portfolio_target(
            signal_date=target.signal_date,
            target_weights=target.target_weights,
            source_hash=target.source_hash,
        )
        if canonical != target:
            raise ForwardOutcomeContractError("portfolio target hash is invalid")
        try:
            signal_index = sessions.index(target.signal_date)
        except ValueError as exc:
            raise ForwardOutcomeContractError(
                "portfolio target signal date is outside the trading calendar"
            ) from exc
        if signal_index + 1 < len(sessions):
            execution_targets[sessions[signal_index + 1]] = target
    market_data_hash = stable_contract_hash(
        {
            "trading_sessions": sessions,
            "adjusted_closes": tuple(asdict(row) for row in ordered_closes),
        }
    )
    input_hash = stable_contract_hash(
        {
            "execution_model": CONTINUOUS_RANKING_EXECUTION_MODEL,
            "market_data_hash": market_data_hash,
            "targets": tuple(
                asdict(item) for item in sorted(target_values, key=lambda item: item.signal_date)
            ),
            "cost_policy": asdict(cost_policy),
            "initial_capital": initial_capital,
            "required_signal_dates": required_dates,
        }
    )
    net_cash = gross_cash = float(initial_capital)
    net_holdings: dict[str, float] = {}
    gross_holdings: dict[str, float] = {}
    points: list[RankingPortfolioPoint] = []
    total_turnover = 0.0
    total_cost = 0.0
    total_orders = 0
    rebalances = 0
    unavailable: list[RankingPortfolioUnavailableInterval] = []
    cost_rate = (
        cost_policy.fee_bps_per_side + cost_policy.slippage_bps_per_side
    ) / _BPS_DENOMINATOR
    for session_date in sessions:
        target = execution_targets.get(session_date)
        needed_codes = set(net_holdings) | set(gross_holdings)
        if target is not None:
            needed_codes.update(code for code, _weight in target.target_weights)
        missing = tuple(
            sorted(code for code in needed_codes if (code, session_date) not in closes)
        )
        if missing:
            unavailable.append(
                RankingPortfolioUnavailableInterval(
                    start_session=session_date,
                    end_session=sessions[-1],
                    asset_codes=missing,
                    reason="missing_decision_eligible_adjusted_valuation",
                )
            )
            break
        prices = {
            code: closes[(code, session_date)].adjusted_close for code in needed_codes
        }
        pre_net = _account_value(net_cash, net_holdings, prices)
        pre_gross = _account_value(gross_cash, gross_holdings, prices)
        net_trade = gross_trade = transaction_cost = 0.0
        order_count = 0
        if target is not None:
            weights = dict(target.target_weights)
            net_cash, net_holdings, net_trade, transaction_cost, order_count = (
                _rebalance_account(
                    cash=net_cash,
                    holdings=net_holdings,
                    prices=prices,
                    target_weights=weights,
                    cost_rate=cost_rate,
                )
            )
            gross_cash, gross_holdings, gross_trade, _ignored_cost, _ignored_orders = (
                _rebalance_account(
                    cash=gross_cash,
                    holdings=gross_holdings,
                    prices=prices,
                    target_weights=weights,
                    cost_rate=0.0,
                )
            )
            total_turnover += net_trade
            total_cost += transaction_cost
            total_orders += order_count
            rebalances += 1
        post_net = _account_value(net_cash, net_holdings, prices)
        post_gross = _account_value(gross_cash, gross_holdings, prices)
        points.append(
            RankingPortfolioPoint(
                session_date=session_date,
                pre_rebalance_net_value=pre_net,
                post_rebalance_net_value=post_net,
                pre_rebalance_gross_value=pre_gross,
                post_rebalance_gross_value=post_gross,
                net_cash=net_cash,
                gross_cash=gross_cash,
                net_holdings=tuple(sorted(net_holdings.items())),
                gross_holdings=tuple(sorted(gross_holdings.items())),
                target_hash=target.target_hash if target is not None else None,
                net_trade_notional=net_trade,
                gross_trade_notional=gross_trade,
                transaction_cost=transaction_cost,
                order_count=order_count,
            )
        )
        if session_date in missing_execution_dates:
            # The pre-trade value remains observable at this boundary, but the
            # account cannot invent a hold decision for a missing daily signal.
            unavailable.append(
                RankingPortfolioUnavailableInterval(
                    start_session=session_date,
                    end_session=sessions[-1],
                    asset_codes=(),
                    reason="missing_daily_ranking_target",
                )
            )
            break
    complete = not unavailable
    net_path = [initial_capital]
    gross_path = [initial_capital]
    for point in points:
        net_path.extend(
            (point.pre_rebalance_net_value, point.post_rebalance_net_value)
        )
        gross_path.extend(
            (point.pre_rebalance_gross_value, point.post_rebalance_gross_value)
        )
    final_net = points[-1].post_rebalance_net_value if points else initial_capital
    final_gross = points[-1].post_rebalance_gross_value if points else initial_capital
    draft = RankingPortfolioLedger(
        status="completed" if complete else "unavailable",
        execution_model=CONTINUOUS_RANKING_EXECUTION_MODEL,
        cost_scenario=cost_policy.scenario,
        cost_contract_hash=cost_policy.contract_hash,
        cost_provenance=cost_policy.provenance,
        fee_bps_per_side=cost_policy.fee_bps_per_side,
        slippage_bps_per_side=cost_policy.slippage_bps_per_side,
        initial_capital=initial_capital,
        points=tuple(points),
        net_return=final_net / initial_capital - 1.0 if complete else None,
        gross_return=final_gross / initial_capital - 1.0 if complete else None,
        net_maximum_drawdown=(
            _capital_maximum_drawdown(net_path) if complete else None
        ),
        gross_maximum_drawdown=(
            _capital_maximum_drawdown(gross_path) if complete else None
        ),
        turnover=total_turnover / initial_capital,
        total_transaction_cost=total_cost,
        rebalance_count=rebalances,
        order_count=total_orders,
        final_net_cash=net_cash if complete else None,
        final_gross_cash=gross_cash if complete else None,
        unavailable_intervals=tuple(unavailable),
        market_data_hash=market_data_hash,
        input_hash=input_hash,
        ledger_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("ledger_hash")
    return replace(draft, ledger_hash=stable_contract_hash(payload))


def _outcome_payload(outcome: RankingForwardOutcome) -> dict[str, Any]:
    payload = asdict(outcome)
    payload.pop("outcome_hash")
    return payload


def _finite_nonnegative(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def _cutoff_valid(
    value: datetime | None,
    cutoff: datetime | None,
) -> bool:
    if value is None or cutoff is None:
        return False
    if (value.tzinfo is None) != (cutoff.tzinfo is None):
        return False
    try:
        return value <= cutoff
    except TypeError:
        return False


def _cost_evidence_reasons(
    evidence: ForwardExecutionCostEvidence,
) -> tuple[float | None, tuple[str, ...]]:
    """Return factual spread/liquidity inputs or stable unavailable reasons."""

    reasons: list[str] = []
    if not evidence.provider.strip():
        reasons.append("missing_provider")
    if not evidence.source_hash.strip():
        reasons.append("missing_source_hash")
    if evidence.quote_time_is_fallback:
        reasons.append("quote_time_fallback")
    if evidence.decision_eligible is not True:
        reasons.append("not_decision_eligible")
    if evidence.quote_time is None:
        reasons.append("missing_quote_time")
    elif not _cutoff_valid(evidence.quote_time, evidence.execution_cutoff):
        reasons.append("quote_time_after_execution_cutoff")
    elif evidence.quote_time.date() != evidence.execution_session:
        reasons.append("quote_time_outside_execution_session")
    if evidence.available_at is None:
        reasons.append("missing_available_at")
    elif not _cutoff_valid(evidence.available_at, evidence.execution_cutoff):
        reasons.append("available_after_execution_cutoff")

    spread_bps: float | None = None
    bid = _finite_nonnegative(evidence.bid)
    ask = _finite_nonnegative(evidence.ask)
    declared_spread = _finite_nonnegative(evidence.quoted_spread_bps)
    if evidence.bid is not None or evidence.ask is not None:
        if bid is None or ask is None or bid <= 0.0 or ask <= bid:
            reasons.append("invalid_bid_ask")
        else:
            spread_bps = (ask - bid) / ((ask + bid) / 2.0) * _BPS_DENOMINATOR
            if (
                declared_spread is not None
                and not math.isclose(
                    spread_bps,
                    declared_spread,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                reasons.append("conflicting_spread_inputs")
    elif declared_spread is not None:
        spread_bps = declared_spread
    else:
        reasons.append("missing_spread")

    liquidity_notional = _finite_nonnegative(evidence.liquidity_notional)
    liquidity_cost = _finite_nonnegative(evidence.liquidity_cost_bps_per_side)
    if liquidity_notional is None or liquidity_notional <= 0.0:
        reasons.append("missing_or_invalid_liquidity_notional")
    if liquidity_cost is None:
        reasons.append("missing_or_invalid_liquidity_cost")
    if reasons:
        return None, tuple(sorted(set(reasons)))
    assert spread_bps is not None and liquidity_cost is not None
    return spread_bps / 2.0 + liquidity_cost, ()


def _index_execution_cost_evidence(
    rows: Iterable[ForwardExecutionCostEvidence],
    *,
    trading_sessions: tuple[date, ...],
) -> tuple[
    dict[tuple[str, date], ForwardExecutionCostEvidence],
    tuple[ForwardExecutionCostEvidence, ...],
]:
    by_key: dict[tuple[str, date], ForwardExecutionCostEvidence] = {}
    valid_sessions = set(trading_sessions)
    for row in rows:
        if not row.asset_code.strip():
            raise ForwardOutcomeContractError(
                "execution cost evidence asset code is required"
            )
        if row.execution_session not in valid_sessions:
            raise ForwardOutcomeContractError(
                "execution cost evidence is outside the trading-session calendar"
            )
        key = (row.asset_code, row.execution_session)
        if key in by_key:
            raise ForwardOutcomeContractError("duplicate execution cost evidence")
        by_key[key] = row
    return by_key, tuple(
        sorted(
            by_key.values(),
            key=lambda row: (row.asset_code, row.execution_session),
        )
    )


def _execution_cost_decision(
    *,
    asset_code: str,
    entry_session: date,
    exit_session: date,
    evidence_by_key: dict[tuple[str, date], ForwardExecutionCostEvidence],
) -> dict[str, Any]:
    facts = (
        ("entry", entry_session),
        ("exit", exit_session),
    )
    slippages: dict[str, float] = {}
    spreads: dict[str, float] = {}
    liquidity_costs: dict[str, float] = {}
    input_hashes: list[str] = []
    source_hashes: list[str] = []
    cost_evidence: list[ForwardExecutionCostEvidence] = []
    unavailable_reasons: list[str] = []
    for leg, session_date in facts:
        evidence = evidence_by_key.get((asset_code, session_date))
        if evidence is None:
            unavailable_reasons.append(f"{leg}_cost_evidence_missing")
            continue
        cost_evidence.append(evidence)
        input_hashes.append(evidence.evidence_hash)
        if evidence.source_hash.strip():
            source_hashes.append(evidence.source_hash)
        slippage, reasons = _cost_evidence_reasons(evidence)
        if reasons:
            unavailable_reasons.extend(f"{leg}_{reason}" for reason in reasons)
            continue
        assert slippage is not None
        slippages[leg] = slippage
        if evidence.bid is not None and evidence.ask is not None:
            bid = _finite_nonnegative(evidence.bid)
            ask = _finite_nonnegative(evidence.ask)
            assert bid is not None and ask is not None
            spreads[leg] = (ask - bid) / ((ask + bid) / 2.0) * _BPS_DENOMINATOR
        else:
            declared_spread = _finite_nonnegative(evidence.quoted_spread_bps)
            assert declared_spread is not None
            spreads[leg] = declared_spread
        liquidity_cost = _finite_nonnegative(evidence.liquidity_cost_bps_per_side)
        assert liquidity_cost is not None
        liquidity_costs[leg] = liquidity_cost
    if not unavailable_reasons and len(slippages) == len(facts):
        return {
            "cost_provenance": FACTUAL_CUTOFF_VALID_COST_PROVENANCE,
            "entry_slippage_bps_per_side": slippages["entry"],
            "exit_slippage_bps_per_side": slippages["exit"],
            "entry_factual_spread_bps": spreads["entry"],
            "exit_factual_spread_bps": spreads["exit"],
            "entry_liquidity_cost_bps_per_side": liquidity_costs["entry"],
            "exit_liquidity_cost_bps_per_side": liquidity_costs["exit"],
            "cost_evidence": tuple(cost_evidence),
            "cost_input_hashes": tuple(input_hashes),
            "cost_source_hashes": tuple(source_hashes),
            "cost_unavailable_reasons": (),
        }
    return {
        "cost_provenance": FROZEN_CONSERVATIVE_COST_PROVENANCE,
        "entry_slippage_bps_per_side": float(RANKING_SLIPPAGE_BPS_PER_SIDE),
        "exit_slippage_bps_per_side": float(RANKING_SLIPPAGE_BPS_PER_SIDE),
        "entry_factual_spread_bps": None,
        "exit_factual_spread_bps": None,
        "entry_liquidity_cost_bps_per_side": None,
        "exit_liquidity_cost_bps_per_side": None,
        "cost_evidence": tuple(cost_evidence),
        "cost_input_hashes": tuple(input_hashes),
        "cost_source_hashes": tuple(source_hashes),
        "cost_unavailable_reasons": tuple(sorted(set(unavailable_reasons))),
    }


def _not_evaluated_cost_fields() -> dict[str, Any]:
    return {
        "cost_provenance": NOT_EVALUATED_COST_PROVENANCE,
        "cost_evidence": (),
        "cost_input_hashes": (),
        "cost_source_hashes": (),
        "cost_unavailable_reasons": ("outcome_not_completed",),
        "entry_slippage_bps_per_side": None,
        "exit_slippage_bps_per_side": None,
        "entry_factual_spread_bps": None,
        "exit_factual_spread_bps": None,
        "entry_liquidity_cost_bps_per_side": None,
        "exit_liquidity_cost_bps_per_side": None,
    }


def _outcome(
    *,
    selection: RankingCandidateSelection,
    asset_code: str,
    horizon: int,
    sessions: tuple[date, ...],
    signal_index: int,
    closes: dict[tuple[str, date], ForwardAdjustedClose],
    execution_cost_evidence: dict[
        tuple[str, date], ForwardExecutionCostEvidence
    ],
    input_hash: str,
) -> RankingForwardOutcome:
    entry_index = signal_index + 1
    exit_index = entry_index + horizon
    common: dict[str, Any] = {
        "replay_run_key": selection.replay_run_key,
        "replay_date": selection.replay_date,
        "candidate_id": selection.candidate_id,
        "candidate_manifest_hash": selection.candidate_manifest_hash,
        "candidate_registry_hash": selection.candidate_registry_hash,
        "source_selection_hash": selection.selection_hash,
        "asset_code": asset_code,
        "horizon_sessions": horizon,
        "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
        "slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        "round_trip_cost_bps": 2
        * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
        "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "cost_provenance_contract_hash": FORWARD_COST_PROVENANCE_CONTRACT_HASH,
        "input_hash": input_hash,
        "outcome_hash": "pending",
    }
    if entry_index >= len(sessions) or exit_index >= len(sessions):
        draft = RankingForwardOutcome(
            **common,
            status="pending",
            entry_session=(
                sessions[entry_index] if entry_index < len(sessions) else None
            ),
            exit_session=None,
            entry_adjusted_close=None,
            exit_adjusted_close=None,
            gross_return=None,
            net_return=None,
            **_not_evaluated_cost_fields(),
            exclusion_reason="future_window_pending",
            missing_leg=None,
        )
        return replace(
            draft,
            outcome_hash=stable_contract_hash(_outcome_payload(draft)),
        )

    entry_session = sessions[entry_index]
    exit_session = sessions[exit_index]
    entry = closes.get((asset_code, entry_session))
    exit_row = closes.get((asset_code, exit_session))
    missing_leg: Literal["entry", "exit"] | None = None
    if entry is None:
        missing_leg = "entry"
    elif exit_row is None:
        missing_leg = "exit"
    if missing_leg is not None:
        draft = RankingForwardOutcome(
            **common,
            status="excluded",
            entry_session=entry_session,
            exit_session=exit_session,
            entry_adjusted_close=(entry.adjusted_close if entry is not None else None),
            exit_adjusted_close=(
                exit_row.adjusted_close if exit_row is not None else None
            ),
            gross_return=None,
            net_return=None,
            **_not_evaluated_cost_fields(),
            exclusion_reason="missing_adjusted_entry_or_exit",
            missing_leg=missing_leg,
        )
        return replace(
            draft,
            outcome_hash=stable_contract_hash(_outcome_payload(draft)),
        )

    assert entry is not None and exit_row is not None
    cost_fields = _execution_cost_decision(
        asset_code=asset_code,
        entry_session=entry_session,
        exit_session=exit_session,
        evidence_by_key=execution_cost_evidence,
    )
    entry_slippage_bps = float(cost_fields["entry_slippage_bps_per_side"])
    exit_slippage_bps = float(cost_fields["exit_slippage_bps_per_side"])
    fee = RANKING_FEE_BPS_PER_SIDE / _BPS_DENOMINATOR
    entry_slippage = entry_slippage_bps / _BPS_DENOMINATOR
    exit_slippage = exit_slippage_bps / _BPS_DENOMINATOR
    gross_return = exit_row.adjusted_close / entry.adjusted_close - 1.0
    net_return = (
        exit_row.adjusted_close
        * (1.0 - exit_slippage)
        * (1.0 - fee)
        / (entry.adjusted_close * (1.0 + entry_slippage) * (1.0 + fee))
        - 1.0
    )
    completed_common = {
        **common,
        "slippage_bps_per_side": (entry_slippage_bps + exit_slippage_bps) / 2.0,
        "round_trip_cost_bps": (
            2.0 * RANKING_FEE_BPS_PER_SIDE
            + entry_slippage_bps
            + exit_slippage_bps
        ),
    }
    draft = RankingForwardOutcome(
        **completed_common,
        status="completed",
        entry_session=entry_session,
        exit_session=exit_session,
        entry_adjusted_close=entry.adjusted_close,
        exit_adjusted_close=exit_row.adjusted_close,
        gross_return=gross_return,
        net_return=net_return,
        **cost_fields,
        exclusion_reason=None,
        missing_leg=None,
    )
    return replace(
        draft,
        outcome_hash=stable_contract_hash(_outcome_payload(draft)),
    )


def calculate_ranking_forward_outcomes(
    *,
    selection: RankingCandidateSelection,
    trading_sessions: Sequence[date],
    adjusted_closes: Iterable[ForwardAdjustedClose],
    execution_cost_evidence: Iterable[ForwardExecutionCostEvidence] = (),
    horizons: Iterable[int] = FORWARD_HORIZONS,
) -> RankingForwardOutcomeBundle:
    """Calculate immutable research outcomes without writing production state."""

    _validate_selection(selection)
    sessions = _validate_sessions(
        trading_sessions,
        signal_date=selection.replay_date,
    )
    frozen_horizons = _validate_horizons(horizons)
    closes_by_key, ordered_closes = _index_adjusted_closes(
        adjusted_closes,
        trading_sessions=sessions,
    )
    cost_evidence_by_key, ordered_cost_evidence = _index_execution_cost_evidence(
        execution_cost_evidence,
        trading_sessions=sessions,
    )
    input_hash = stable_contract_hash(
        {
            "contract_id": "ranking_forward_outcome_input_v1",
            "selection_hash": selection.selection_hash,
            "trading_sessions": sessions,
            "horizons": frozen_horizons,
            "adjusted_closes": tuple(asdict(row) for row in ordered_closes),
            "execution_cost_evidence": tuple(
                asdict(row) for row in ordered_cost_evidence
            ),
            "execution_model": FORWARD_EXECUTION_MODEL,
            "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
            "cost_provenance_contract_hash": FORWARD_COST_PROVENANCE_CONTRACT_HASH,
        }
    )
    signal_index = sessions.index(selection.replay_date)
    outcomes = tuple(
        _outcome(
            selection=selection,
            asset_code=asset_code,
            horizon=horizon,
            sessions=sessions,
            signal_index=signal_index,
            closes=closes_by_key,
            execution_cost_evidence=cost_evidence_by_key,
            input_hash=input_hash,
        )
        for asset_code in selection.selected_asset_codes
        for horizon in frozen_horizons
    )
    status_counts_by_horizon = tuple(
        RankingForwardOutcomeStatusCount(
            horizon_sessions=horizon,
            requested_count=sum(
                outcome.horizon_sessions == horizon for outcome in outcomes
            ),
            completed_count=sum(
                outcome.horizon_sessions == horizon and outcome.status == "completed"
                for outcome in outcomes
            ),
            pending_count=sum(
                outcome.horizon_sessions == horizon and outcome.status == "pending"
                for outcome in outcomes
            ),
            excluded_count=sum(
                outcome.horizon_sessions == horizon and outcome.status == "excluded"
                for outcome in outcomes
            ),
        )
        for horizon in frozen_horizons
    )
    provenance_counts = Counter(outcome.cost_provenance for outcome in outcomes)
    unavailable_reason_counts = Counter(
        reason
        for outcome in outcomes
        for reason in outcome.cost_unavailable_reasons
    )
    cost_input_hashes = tuple(
        sorted(
            {
                input_hash
                for outcome in outcomes
                for input_hash in outcome.cost_input_hashes
            }
        )
    )
    evidence_hash = stable_contract_hash(
        {
            "contract_hash": FORWARD_COST_PROVENANCE_CONTRACT_HASH,
            "evidence": tuple(asdict(row) for row in ordered_cost_evidence),
        }
    )
    draft = RankingForwardOutcomeBundle(
        replay_run_key=selection.replay_run_key,
        replay_date=selection.replay_date,
        candidate_id=selection.candidate_id,
        candidate_manifest_hash=selection.candidate_manifest_hash,
        candidate_registry_hash=selection.candidate_registry_hash,
        source_selection_hash=selection.selection_hash,
        execution_model=FORWARD_EXECUTION_MODEL,
        horizons=frozen_horizons,
        fee_bps_per_side=RANKING_FEE_BPS_PER_SIDE,
        slippage_bps_per_side=RANKING_SLIPPAGE_BPS_PER_SIDE,
        round_trip_cost_bps=2
        * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        cost_provenance_contract_hash=FORWARD_COST_PROVENANCE_CONTRACT_HASH,
        cost_provenance_counts=tuple(sorted(provenance_counts.items())),
        cost_unavailable_reason_counts=tuple(
            sorted(unavailable_reason_counts.items())
        ),
        cost_input_hashes=cost_input_hashes,
        execution_cost_evidence_hash=evidence_hash,
        requested_outcome_count=len(outcomes),
        completed_outcome_count=sum(
            outcome.status == "completed" for outcome in outcomes
        ),
        pending_outcome_count=sum(
            outcome.status == "pending" for outcome in outcomes
        ),
        excluded_outcome_count=sum(
            outcome.status == "excluded" for outcome in outcomes
        ),
        status_counts_by_horizon=status_counts_by_horizon,
        outcomes=outcomes,
        input_hash=input_hash,
        bundle_hash="pending",
    )
    bundle_payload = asdict(draft)
    bundle_payload.pop("bundle_hash")
    return replace(
        draft,
        bundle_hash=stable_contract_hash(bundle_payload),
    )
