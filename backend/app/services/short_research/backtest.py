from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import (
    EtfIntradayQuote,
    EtfPortfolioBacktestEquityCurve,
    EtfPortfolioBacktestLabelSummary,
    EtfPortfolioBacktestPosition,
    EtfPortfolioBacktestRun,
    EtfPortfolioBacktestTrade,
    EtfPriceHistory,
    TradableEtf,
    User,
    utcnow,
)
from app.services.etf_research_evidence import (
    ALLOCATION_CONTRACT_VERSION,
    EVIDENCE_STATUS_LEGACY,
    EVIDENCE_STATUS_SAME_CONTRACT,
    EVIDENCE_STATUS_WAITING,
    EXECUTION_MODEL_INTRADAY_ALERT,
    EXIT_ACTION_CONTRACT_VERSION,
    FEE_MODEL_SIMPLE_RATE,
    REPLAY_CONTRACT_VERSION,
    build_evidence_summary,
    build_replay_contract,
    stable_contract_hash,
)
from app.services.intraday_etf.service import (
    DISPLAY_ONLY_CONSENSUS,
    is_quote_time_fallback,
    quote_consensus_status,
    quote_decision_eligible_flag,
)
from app.services.portfolio_allocation import (
    PORTFOLIO_CORRELATION_CLUSTER_CAP,
    PORTFOLIO_LAYER_DEFENSIVE,
    PORTFOLIO_LAYER_PRIMARY,
    PORTFOLIO_LAYER_SATELLITE,
    PORTFOLIO_LAYER_WATCH_ONLY,
    PORTFOLIO_MODE_CASH_WAIT,
    PORTFOLIO_RISK_BUDGET_VERSION,
    PORTFOLIO_SATELLITE_EXPOSURE_CAP,
    PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
    PORTFOLIO_SINGLE_WEIGHT_CAP,
    apply_portfolio_risk_budget,
    classify_portfolio_market_risk,
    portfolio_risk_budget_manifest,
)
from app.services.portfolio_risk_shadow import (
    PortfolioRiskAssetInput,
    build_portfolio_risk_shadow,
)
from app.services.risk_alerts import (
    ALERT_EXIT_WATCH,
    ALERT_HARD_STOP,
    ALERT_MA5_CLOSE_BREAK_EXIT,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_TREND_WEAKENING,
    ETF_ENTRY_MAX_ADV_PARTICIPATION,
    ETF_ENTRY_STRESS_MAX_ADV_PARTICIPATION,
    ETF_EXIT_NORMAL_ADV_PARTICIPATION,
    ETF_EXIT_STRESS_ADV_PARTICIPATION,
    ETF_LIQUIDITY_STRESS_TURNOVER_MULTIPLIER,
    ETF_TRAILING_GIVEBACK_MAX_PCT,
    ETF_TRAILING_GIVEBACK_MIN_PCT,
    ETF_TRAILING_GIVEBACK_VOL_MULTIPLIER,
    ETF_TRAILING_PROFIT_START_MAX_PCT,
    ETF_TRAILING_PROFIT_START_MIN_PCT,
    ETF_TRAILING_PROFIT_START_VOL_MULTIPLIER,
    HARD_STOP_LOSS_PCT,
    etf_liquidity_capacity_manifest,
)
from app.services.short_research.service import (
    CONCLUSION_INSUFFICIENT,
    CONCLUSION_REJECT,
    ComputedAsset,
    PricePoint,
    _metadata_from_etf_row,
    _portfolio_candidate_group,
    _portfolio_defensive_priority,
    _portfolio_defensive_reason,
    _portfolio_exposure_for_asset,
    _portfolio_layer_cap,
    _portfolio_market_risk_observations,
    _portfolio_raw_weight,
    _portfolio_theme_keys,
    _research_adjusted_value,
    compute_asset_for_replay_from_series,
)
from app.services.strategy_lab.etf_action_replay import (
    DailyExecutionBar,
    ExecutionResolution,
    ExecutionStatus,
    select_adjusted_open_fill,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
)

BACKTEST_RULE_VERSION = "etf_portfolio_backtest_v2"
BACKTEST_RANKING_VERSION = "short_research_daily_replay_v1"
BACKTEST_ALLOCATION_VERSION = ALLOCATION_CONTRACT_VERSION
BACKTEST_EXIT_RULE_VERSION = "risk_alerts_daily_v2_adjusted_ma5_close_break"
INTRADAY_BACKTEST_EXIT_RULE_VERSION = "risk_alerts_intraday_v1"
DEFAULT_BACKTEST_DAYS = 180
EXECUTION_MODEL_DAILY_ADJUSTED_OPEN = "adjusted_open_next_eligible_v1"
EXECUTION_RISK_CONTRACT_VERSION = "etf_backtest_execution_risk_v2"
DEFAULT_BACKTEST_FEE_RATE = RANKING_FEE_BPS_PER_SIDE / 10_000
STRESS_SLIPPAGE_BPS_PER_SIDE = 20
ETF_LOT_SIZE = 100
DEFAULT_BACKTEST_INITIAL_CASH = 10000.0
DEFAULT_INTRADAY_EXECUTION_DELAY_MINUTES = 3
MAX_BACKTEST_HOLDINGS = 6
MIN_WEIGHTABLE_HOLDINGS = 4
LABEL_HORIZONS = (1, 3, 5, 10)
BENCHMARK_CODES = ("510300", "159919", "510500", "512880", "588000")
CURRENT_ACTION_TARGET_SEMANTICS = "absolute_exposure_baseline"
CURRENT_ACTION_EVENT_SOURCE = "unique_action_decision"
LEGACY_ACTION_TARGET_SEMANTICS = "legacy_current_position"
LEGACY_ACTION_EVENT_SOURCE = "repeated_risk_evaluation"
LEGACY_BACKTEST_ROLE = "legacy_diagnostic"


@dataclass
class ReplayPosition:
    code: str
    name: str
    shares: float
    avg_cost: float
    entry_date: date
    max_profit_pct: float = 0.0


@dataclass(frozen=True)
class ReplayTrade:
    trade_date: date
    code: str
    name: str
    side: str
    reason: str
    amount: float
    shares: float
    price: float
    fee: float
    realized_pnl: float | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PendingDailyOrder:
    order_id: int
    signal_date: date
    code: str
    name: str
    side: str
    reason: str
    signal_price: float
    requested_amount: float = 0.0
    requested_fraction: float = 0.0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DailyOrderAttempt:
    status: ExecutionStatus
    trade: ReplayTrade | None = None
    cash_delta: float = 0.0
    reason: str | None = None
    execution_evidence: dict[str, Any] | None = None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_money(value: float) -> float:
    return round(float(value), 2)


def _finite_positive(value: float | None) -> bool:
    return bool(
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _rate_from_bps(value: int | float) -> float:
    return float(value) / 10_000


def _slipped_price(reference_price: float, *, side: str, slippage_bps: int) -> float:
    if side not in {"buy", "sell"}:
        raise ValueError("execution side must be buy or sell")
    direction = 1.0 if side == "buy" else -1.0
    return reference_price * (1.0 + direction * _rate_from_bps(slippage_bps))


def _round_down_to_lot(*, gross_limit: float, price: float, lot_size: int = ETF_LOT_SIZE) -> float:
    if not _finite_positive(gross_limit) or not _finite_positive(price) or lot_size < 1:
        return 0.0
    lots = math.floor(float(gross_limit) / float(price) / lot_size)
    return float(lots * lot_size)


def _execution_cost_evidence(
    *,
    side: str,
    signal_date: date,
    fill_date: date,
    signal_price: float,
    reference_price: float,
    shares: float,
    signal_to_fill_sessions: int,
    deferred_reasons: tuple[str, ...],
    price_basis: str,
    execution_time: datetime | None = None,
    spread_pct: float | None = None,
    spread_observed: bool = False,
    median_turnover_20d: float | None = None,
    requested_notional: float | None = None,
) -> dict[str, Any]:
    if side not in {"buy", "sell"}:
        raise ValueError("execution side must be buy or sell")
    invalid_fields = [
        name
        for name, value in (
            ("signal_price", signal_price),
            ("reference_price", reference_price),
            ("shares", shares),
        )
        if not _finite_positive(value)
    ]
    if invalid_fields:
        raise ValueError(f"execution evidence requires positive finite values: {','.join(invalid_fields)}")
    reference_notional = float(reference_price) * float(shares)
    fee = reference_notional * _rate_from_bps(RANKING_FEE_BPS_PER_SIDE)
    base_slippage = reference_notional * _rate_from_bps(RANKING_SLIPPAGE_BPS_PER_SIDE)
    stress_slippage = reference_notional * _rate_from_bps(STRESS_SLIPPAGE_BPS_PER_SIDE)
    spread_cost = (
        reference_notional * abs(float(spread_pct)) / 2
        if spread_observed and spread_pct is not None and math.isfinite(spread_pct)
        else None
    )
    # Bid/ask is the executable reference itself for intraday fills; reporting the
    # half-spread again in total cost would double count it.
    base_total = fee + base_slippage
    stress_total = fee + stress_slippage
    adv = float(median_turnover_20d) if _finite_positive(median_turnover_20d) else None
    requested = (
        float(requested_notional)
        if _finite_positive(requested_notional)
        else reference_notional
    )
    base_participation = reference_notional / adv if adv is not None else None
    requested_participation = requested / adv if adv is not None else None
    stress_participation = (
        requested / (adv * ETF_LIQUIDITY_STRESS_TURNOVER_MULTIPLIER)
        if adv is not None
        else None
    )
    fill_capped = requested > reference_notional + max(0.01, requested * 1e-9)
    capacity_manifest = etf_liquidity_capacity_manifest()
    capacity_stress = {
        "status": "unavailable" if adv is None else ("capped" if fill_capped else "ready"),
        "contract_version": capacity_manifest["version"],
        "contract_hash": capacity_manifest["contract_hash"],
        "median_turnover_20d": adv,
        "requested_notional": round(requested, 2),
        "filled_notional": round(reference_notional, 2),
        "unfilled_notional": round(max(requested - reference_notional, 0.0), 2),
        "fill_capped": fill_capped,
        "base_adv_participation": base_participation,
        "requested_adv_participation": requested_participation,
        "stress_adv_participation": stress_participation,
        "entry_capacity_exceeded": (
            side == "buy"
            and requested_participation is not None
            and requested_participation > ETF_ENTRY_MAX_ADV_PARTICIPATION
        ),
        "stress_entry_capacity_exceeded": (
            side == "buy"
            and stress_participation is not None
            and stress_participation > ETF_ENTRY_STRESS_MAX_ADV_PARTICIPATION
        ),
        "normal_exit_days": (
            requested / (adv * ETF_EXIT_NORMAL_ADV_PARTICIPATION)
            if side == "sell" and adv is not None
            else None
        ),
        "stress_exit_days": (
            requested
            / (
                adv
                * ETF_LIQUIDITY_STRESS_TURNOVER_MULTIPLIER
                * ETF_EXIT_STRESS_ADV_PARTICIPATION
            )
            if side == "sell" and adv is not None
            else None
        ),
        "simulation_only": True,
    }
    return {
        "contract_version": EXECUTION_RISK_CONTRACT_VERSION,
        "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "execution_provenance": "simulated_fill_base",
        "simulated_not_observed": True,
        "side": side,
        "signal_date": signal_date.isoformat(),
        "fill_date": fill_date.isoformat(),
        "execution_time": execution_time.isoformat() if execution_time else None,
        "price_basis": price_basis,
        "signal_price": float(signal_price),
        "reference_price": float(reference_price),
        "signal_to_fill_gap_return": float(reference_price) / float(signal_price) - 1.0,
        "signal_to_fill_trading_sessions": signal_to_fill_sessions,
        "deferred_reasons": list(deferred_reasons),
        "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
        "base_slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        "stress_slippage_bps_per_side": STRESS_SLIPPAGE_BPS_PER_SIDE,
        "spread_pct": spread_pct if spread_observed else None,
        "spread_cost": spread_cost,
        "spread_evidence": "observed_bid_ask" if spread_observed else "modeled_sensitivity_not_observed",
        "liquidity_capacity_stress": capacity_stress,
        "base": {
            "provenance": "simulated_fill_base",
            "fill_price": _slipped_price(
                float(reference_price),
                side=side,
                slippage_bps=RANKING_SLIPPAGE_BPS_PER_SIDE,
            ),
            "fee": fee,
            "slippage_cost": base_slippage,
            "spread_cost": spread_cost,
            "total_cost": base_total,
        },
        "stress": {
            "provenance": "simulated_fill_stress",
            "fill_price": _slipped_price(
                float(reference_price),
                side=side,
                slippage_bps=STRESS_SLIPPAGE_BPS_PER_SIDE,
            ),
            "fee": fee,
            "slippage_cost": stress_slippage,
            "spread_cost": spread_cost,
            "total_cost": stress_total,
        },
    }


def _daily_execution_resolution(
    *,
    signal_date: date,
    through_date: date,
    bars: tuple[DailyExecutionBar, ...] | list[DailyExecutionBar],
) -> ExecutionResolution:
    visible_bars = tuple(bar for bar in bars if bar.session_date <= through_date)
    return select_adjusted_open_fill(signal_session=signal_date, bars=visible_bars)


def _intraday_execution_price(quote: EtfIntradayQuote | None, *, side: str) -> tuple[float | None, str | None]:
    if side not in {"buy", "sell"}:
        raise ValueError("execution side must be buy or sell")
    if quote is None:
        return None, "missing_quote"
    if quote.quote_time is None or quote.trade_date is None or quote.quote_time.date() != quote.trade_date:
        return None, "invalid_quote_time"
    if quote.freshness_status not in {"fresh", "historical_replay"}:
        return None, "stale_or_display_only_quote"
    if (
        not quote_decision_eligible_flag(quote)
        or is_quote_time_fallback(quote)
        or quote_consensus_status(quote) in DISPLAY_ONLY_CONSENSUS
    ):
        return None, "decision_ineligible_quote"
    if not _finite_positive(quote.volume):
        return None, "zero_or_missing_volume"
    if not _finite_positive(quote.bid_price) or not _finite_positive(quote.ask_price):
        return None, "missing_executable_book"
    bid = float(quote.bid_price)
    ask = float(quote.ask_price)
    if bid > ask:
        return None, "crossed_book"
    return (ask if side == "buy" else bid), None


def _first_eligible_intraday_quote(
    quotes: list[EtfIntradayQuote],
    *,
    side: str,
) -> EtfIntradayQuote | None:
    for quote in sorted(quotes, key=lambda item: item.quote_time or datetime.max):
        price, _reason = _intraday_execution_price(quote, side=side)
        if price is not None:
            return quote
    return None


def _intraday_spread_fraction(quote: EtfIntradayQuote) -> float | None:
    if not _finite_positive(quote.bid_price) or not _finite_positive(quote.ask_price):
        return None
    bid = float(quote.bid_price)
    ask = float(quote.ask_price)
    midpoint = (bid + ask) / 2
    return (ask - bid) / midpoint if midpoint > 0 and bid <= ask else None


async def _create_backtest_run(
    session: AsyncSession,
    *,
    user_id: int | None,
    start_date: date,
    end_date: date,
    initial_cash: float,
    fee_rate: float,
    max_assets: int,
    execution_model: str = EXECUTION_MODEL_DAILY_ADJUSTED_OPEN,
    execution_label: str = "next_eligible_adjusted_open",
    exit_rule_version: str = BACKTEST_EXIT_RULE_VERSION,
    execution_delay_minutes: int | None = None,
) -> EtfPortfolioBacktestRun:
    risk_budget_manifest = portfolio_risk_budget_manifest()
    liquidity_capacity_manifest = etf_liquidity_capacity_manifest()
    config: dict[str, Any] = {
        "max_assets": max_assets,
        "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
        "min_holdings_for_full_exposure": MIN_WEIGHTABLE_HOLDINGS,
        "partial_allocation_allowed": True,
        "correlation_cluster_cap": PORTFOLIO_CORRELATION_CLUSTER_CAP,
        "risk_budget_version": PORTFOLIO_RISK_BUDGET_VERSION,
        "risk_budget_hash": risk_budget_manifest["contract_hash"],
        "execution": execution_label,
        "run_role": LEGACY_BACKTEST_ROLE,
        "action_lifecycle_version": exit_rule_version,
        "target_semantics": LEGACY_ACTION_TARGET_SEMANTICS,
        "action_event_source": LEGACY_ACTION_EVENT_SOURCE,
        "research_only": True,
        "promotion_eligible": False,
        "requested_fee_rate": fee_rate,
        "execution_risk_contract_version": EXECUTION_RISK_CONTRACT_VERSION,
        "liquidity_capacity_contract_version": liquidity_capacity_manifest["version"],
        "liquidity_capacity_contract_hash": liquidity_capacity_manifest["contract_hash"],
        "capacity_unavailable_policy": "exclude_entry_without_fallback",
        "capacity_constrained_exit_policy": "partial_fill_at_normal_adv_limit",
        "execution_cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
        "base_slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
        "stress_slippage_bps_per_side": STRESS_SLIPPAGE_BPS_PER_SIDE,
        "lot_size": ETF_LOT_SIZE,
        "simulated_not_observed": True,
    }
    if execution_model == EXECUTION_MODEL_DAILY_ADJUSTED_OPEN:
        config.update(
            no_intraday_fill=True,
            base_fill_field="next_eligible_total_return_adjusted_open",
            same_day_close_fill_allowed=False,
            unavailable_execution_policy="defer_without_fallback",
        )
    if execution_delay_minutes is not None:
        config["execution_delay_minutes"] = execution_delay_minutes
    run = EtfPortfolioBacktestRun(
        user_id=user_id,
        status="running",
        start_date=start_date,
        end_date=end_date,
        asset_type=ASSET_TYPE_ETF,
        rule_version=BACKTEST_RULE_VERSION,
        ranking_version=BACKTEST_RANKING_VERSION,
        allocation_version=BACKTEST_ALLOCATION_VERSION,
        exit_rule_version=exit_rule_version,
        initial_cash=initial_cash,
        fee_rate=DEFAULT_BACKTEST_FEE_RATE,
        config_json=config,
        metrics_json={},
        benchmark_json={},
        data_coverage_json={},
        caveats_json=[],
    )
    session.add(run)
    await session.flush()
    replay_contract = build_replay_contract(
        replay_run_id=run.id,
        signal_rule_version=BACKTEST_RANKING_VERSION,
        allocation_version=BACKTEST_ALLOCATION_VERSION,
        action_lifecycle_version=exit_rule_version,
        target_semantics=LEGACY_ACTION_TARGET_SEMANTICS,
        action_event_source=LEGACY_ACTION_EVENT_SOURCE,
        execution_model=execution_model,
        fee_model=FEE_MODEL_SIMPLE_RATE,
        start_date=start_date,
        end_date=end_date,
        data_cutoff=end_date,
    )
    run.config_json = {
        **dict(run.config_json or {}),
        "replay_contract": replay_contract,
        "contract_hash": replay_contract["contract_hash"],
        "evidence_only": True,
    }
    return run


async def mark_backtest_failed(
    session: AsyncSession,
    run: EtfPortfolioBacktestRun,
    message: str,
) -> EtfPortfolioBacktestRun:
    run_id = run.id
    await session.rollback()
    persisted = await session.get(EtfPortfolioBacktestRun, run_id)
    if persisted is None:
        raise LookupError("ETF backtest run disappeared before failure recording")
    persisted.status = "failed"
    persisted.finished_at = utcnow()
    persisted.error_message = message
    await session.commit()
    await session.refresh(persisted)
    return persisted


async def latest_completed_backtest_run(session: AsyncSession) -> EtfPortfolioBacktestRun | None:
    return cast(
        EtfPortfolioBacktestRun | None,
        await session.scalar(
        select(EtfPortfolioBacktestRun)
        .where(
            EtfPortfolioBacktestRun.asset_type == ASSET_TYPE_ETF,
            EtfPortfolioBacktestRun.status == "success",
        )
        .order_by(EtfPortfolioBacktestRun.finished_at.desc(), EtfPortfolioBacktestRun.id.desc())
        ),
    )


async def list_backtest_runs(session: AsyncSession, *, limit: int = 10) -> list[EtfPortfolioBacktestRun]:
    rows = await session.scalars(
        select(EtfPortfolioBacktestRun)
        .where(EtfPortfolioBacktestRun.asset_type == ASSET_TYPE_ETF)
        .order_by(EtfPortfolioBacktestRun.started_at.desc(), EtfPortfolioBacktestRun.id.desc())
        .limit(limit)
    )
    return list(rows.all())


async def get_backtest_run(session: AsyncSession, run_id: int) -> EtfPortfolioBacktestRun | None:
    return await session.get(EtfPortfolioBacktestRun, run_id)


async def _load_etf_universe(
    session: AsyncSession,
    *,
    max_assets: int,
) -> list[ShortResearchAsset]:
    rows = (
        await session.scalars(
            select(TradableEtf)
            .where(TradableEtf.is_short_term_eligible.is_(True))
            .order_by(TradableEtf.is_watchlist.desc(), TradableEtf.code.asc())
            .limit(max_assets)
        )
    ).all()
    return [_metadata_from_etf_row(row) for row in rows]


async def _load_price_series(
    session: AsyncSession,
    *,
    codes: list[str],
    from_date: date,
    to_date: date,
) -> dict[str, list[PricePoint]]:
    rows = (
        await session.scalars(
            select(EtfPriceHistory)
            .where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date >= from_date,
                EtfPriceHistory.trade_date <= to_date,
            )
            .order_by(EtfPriceHistory.etf_code.asc(), EtfPriceHistory.trade_date.asc())
        )
    ).all()
    series: dict[str, list[PricePoint]] = defaultdict(list)
    for row in rows:
        research_value = _research_adjusted_value(row)
        if research_value is None:
            continue
        series[row.etf_code].append(
            PricePoint(
                point_date=row.trade_date,
                value=research_value,
                close=row.close,
                turnover=row.turnover,
                pct_change=row.pct_change / 100,
            )
        )
    return dict(series)


async def _load_daily_execution_bars(
    session: AsyncSession,
    *,
    codes: list[str],
    from_date: date,
    to_date: date,
) -> dict[str, tuple[DailyExecutionBar, ...]]:
    rows = (
        await session.scalars(
            select(EtfPriceHistory)
            .where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date >= from_date,
                EtfPriceHistory.trade_date <= to_date,
            )
            .order_by(EtfPriceHistory.etf_code.asc(), EtfPriceHistory.trade_date.asc())
        )
    ).all()
    grouped: dict[str, list[DailyExecutionBar]] = defaultdict(list)
    turnover_windows: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        adjusted_close = _research_adjusted_value(row)
        if (
            adjusted_close is not None
            and row.decision_eligible is True
            and _finite_positive(row.turnover)
        ):
            turnover_windows[row.etf_code].append(float(row.turnover))
        recent_turnovers = turnover_windows[row.etf_code][-20:]
        adjustment_factor = (
            float(adjusted_close) / float(row.close)
            if adjusted_close is not None and _finite_positive(row.close)
            else None
        )
        finite_ohlc = all(_finite_positive(value) for value in (row.open, row.high, row.low, row.close))
        limit_locked = bool(
            finite_ohlc
            and math.isclose(float(row.open), float(row.high), rel_tol=0.0, abs_tol=1e-12)
            and math.isclose(float(row.open), float(row.low), rel_tol=0.0, abs_tol=1e-12)
            and math.isclose(float(row.open), float(row.close), rel_tol=0.0, abs_tol=1e-12)
        )
        grouped[row.etf_code].append(
            DailyExecutionBar(
                session_date=row.trade_date,
                raw_open=float(row.open) if _finite_positive(row.open) else None,
                adjustment_factor=adjustment_factor,
                volume=float(row.volume) if row.volume is not None else None,
                suspended=False,
                limit_locked=limit_locked,
                # A daily one-price bar cannot prove queue-side liquidity.
                demonstrably_tradable=False,
                raw_high=float(row.high) if _finite_positive(row.high) else None,
                raw_low=float(row.low) if _finite_positive(row.low) else None,
                raw_close=float(row.close) if _finite_positive(row.close) else None,
                median_turnover_20d=(
                    float(median(recent_turnovers))
                    if len(recent_turnovers) >= 10
                    else None
                ),
            )
        )
    return {code: tuple(items) for code, items in grouped.items()}


async def _load_intraday_quotes(
    session: AsyncSession,
    *,
    codes: list[str],
    from_date: date,
    to_date: date,
) -> dict[str, dict[date, list[EtfIntradayQuote]]]:
    rows = (
        await session.scalars(
            select(EtfIntradayQuote)
            .where(
                EtfIntradayQuote.etf_code.in_(codes),
                EtfIntradayQuote.trade_date >= from_date,
                EtfIntradayQuote.trade_date <= to_date,
                EtfIntradayQuote.latest_price > 0,
            )
            .order_by(EtfIntradayQuote.etf_code.asc(), EtfIntradayQuote.trade_date.asc(), EtfIntradayQuote.quote_time.asc())
        )
    ).all()
    grouped: dict[str, dict[date, list[EtfIntradayQuote]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.quote_time is None or row.trade_date is None:
            continue
        grouped[row.etf_code][row.trade_date].append(row)
    return {code: dict(by_date) for code, by_date in grouped.items()}


def _first_execution_quote_after(
    quotes: list[EtfIntradayQuote],
    signal_time: datetime,
    *,
    delay_minutes: int = DEFAULT_INTRADAY_EXECUTION_DELAY_MINUTES,
    side: str = "sell",
) -> EtfIntradayQuote | None:
    earliest_execution_time = signal_time + timedelta(minutes=delay_minutes)
    for quote in sorted(quotes, key=lambda item: item.quote_time or datetime.max):
        execution_price, _reason = _intraday_execution_price(quote, side=side)
        if quote.quote_time is not None and quote.quote_time >= earliest_execution_time and execution_price is not None:
            return quote
    return None


def _slice_until(series: list[PricePoint], as_of_date: date) -> list[PricePoint]:
    return [item for item in series if item.point_date <= as_of_date]


def _price_on(series: list[PricePoint], trade_date: date) -> float | None:
    for item in reversed(series):
        if item.point_date == trade_date:
            return item.value
        if item.point_date < trade_date:
            return None
    return None


def _trading_dates(series_by_code: dict[str, list[PricePoint]], start_date: date, end_date: date) -> list[date]:
    dates = {
        item.point_date
        for series in series_by_code.values()
        for item in series
        if start_date <= item.point_date <= end_date
    }
    return sorted(dates)


def _previous_date(dates: list[date], current_date: date) -> date | None:
    previous: date | None = None
    for item in dates:
        if item >= current_date:
            return previous
        previous = item
    return previous


def _build_daily_assets(
    metadata_by_code: dict[str, ShortResearchAsset],
    series_by_code: dict[str, list[PricePoint]],
    trade_date: date,
) -> list[ComputedAsset]:
    assets: list[ComputedAsset] = []
    for code, metadata in metadata_by_code.items():
        series = _slice_until(series_by_code.get(code, []), trade_date)
        if not series:
            continue
        asset = compute_asset_for_replay_from_series(
            metadata,
            series=series,
            as_of_date=trade_date,
        )
        assets.append(asset)
    assets.sort(key=lambda item: (item.total_score, item.metadata.code), reverse=True)
    return [replace(asset, rank=index) for index, asset in enumerate(assets, start=1)]


def _risk_return_maps_for_date(
    series_by_code: Mapping[str, list[PricePoint]],
    signal_date: date,
) -> dict[str, dict[date, float]]:
    result: dict[str, dict[date, float]] = {}
    for code, series in series_by_code.items():
        visible = [point for point in series if point.point_date <= signal_date][-61:]
        returns = {
            current.point_date: current.value / previous.value - 1.0
            for previous, current in zip(visible, visible[1:], strict=False)
            if previous.value > 0
        }
        if returns:
            result[code] = returns
    return result


def _generate_target_weights(
    assets: list[ComputedAsset],
    *,
    return_maps: Mapping[str, Mapping[date, float]] | None = None,
    previous_weights: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], str, dict[str, Any]]:
    primary: list[ComputedAsset] = []
    satellite: list[tuple[ComputedAsset, str | None]] = []
    defensive: list[tuple[ComputedAsset, str]] = []
    excluded = 0
    watch_only = 0
    for asset in assets:
        group, reason = _portfolio_candidate_group(asset)
        if group == PORTFOLIO_LAYER_PRIMARY:
            primary.append(asset)
            continue
        if group == PORTFOLIO_LAYER_SATELLITE:
            satellite.append((asset, reason))
            continue
        defensive_reason = _portfolio_defensive_reason(asset, reason)
        if defensive_reason is not None:
            defensive.append((asset, defensive_reason))
            continue
        if group == PORTFOLIO_LAYER_WATCH_ONLY:
            watch_only += 1
        else:
            excluded += 1

    selected = primary[:MAX_BACKTEST_HOLDINGS]
    item_types = {asset.metadata.code: PORTFOLIO_LAYER_PRIMARY for asset in selected}
    satellite_limit = max(1, int(PORTFOLIO_SATELLITE_EXPOSURE_CAP / PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP))
    satellite_selected = 0
    for asset, _reason in satellite:
        if len(selected) >= MAX_BACKTEST_HOLDINGS or satellite_selected >= satellite_limit:
            watch_only += 1
            continue
        if asset.metadata.code in item_types:
            continue
        selected.append(asset)
        item_types[asset.metadata.code] = PORTFOLIO_LAYER_SATELLITE
        satellite_selected += 1
    if len(selected) < MIN_WEIGHTABLE_HOLDINGS:
        for asset, _reason in sorted(
            defensive,
            key=lambda row: (_portfolio_defensive_priority(row[0]), -row[0].total_score),
        ):
            if asset.metadata.code in item_types:
                continue
            selected.append(asset)
            item_types[asset.metadata.code] = PORTFOLIO_LAYER_DEFENSIVE
            if len(selected) >= MIN_WEIGHTABLE_HOLDINGS:
                break

    if not selected:
        return (
            {},
            PORTFOLIO_MODE_CASH_WAIT,
            {
                "cash_reason": "没有满足进攻或防守约束的 ETF，按现金等待处理。",
                "primary_count": len(primary),
                "satellite_count": len(satellite),
                "defensive_count": len(defensive),
                "watch_only_count": watch_only,
                "excluded_count": excluded,
                "target_exposure": 0.0,
                "cash_weight": 1.0,
            },
        )

    market_risk = classify_portfolio_market_risk(
        _portfolio_market_risk_observations(assets)
    )
    raw_rows = {
        asset.metadata.code: _portfolio_raw_weight(asset)
        for asset in selected
    }
    individual_caps = {
        asset.metadata.code: min(
            _portfolio_layer_cap(
                item_types.get(asset.metadata.code, PORTFOLIO_LAYER_PRIMARY)
            ),
            _portfolio_exposure_for_asset(asset),
        )
        for asset in selected
    }
    risk_budget = apply_portfolio_risk_budget(
        {code: row[0] for code, row in raw_rows.items()},
        layer_by_code=item_types,
        theme_by_code={
            asset.metadata.code: (_portfolio_theme_keys(asset) or ["unknown"])[0]
            for asset in selected
        },
        returns_by_code=dict(return_maps or {}),
        market_risk=market_risk,
        individual_caps=individual_caps,
    )
    if risk_budget.status != "ready":
        return (
            {},
            PORTFOLIO_MODE_CASH_WAIT,
            {
                "cash_reason": "组合风险证据不足，按现金等待处理。",
                "primary_count": len(primary),
                "satellite_count": len(satellite),
                "defensive_count": len(defensive),
                "target_exposure": 0.0,
                "cash_weight": 1.0,
                "risk_budget_version": risk_budget.contract_version,
                "risk_budget_hash": risk_budget.contract_hash,
                "risk_budget_status": risk_budget.status,
                "unavailable_reasons": list(risk_budget.unavailable_reasons),
                "risk_summary": risk_budget.metrics,
            },
        )
    weights = risk_budget.weights
    mode = risk_budget.market_state
    risk_returns = dict(return_maps or {})
    large_returns = risk_returns.get("510300") or risk_returns.get("510310") or {}
    small_returns = risk_returns.get("512100") or risk_returns.get("159845") or {}
    growth_returns = risk_returns.get("159915") or risk_returns.get("159949") or {}

    def spread_returns(
        left: Mapping[date, float],
        right: Mapping[date, float],
    ) -> dict[date, float]:
        return {
            point_date: float(left[point_date]) - float(right[point_date])
            for point_date in sorted(set(left) & set(right))
        }

    factor_returns = {
        "market": dict(large_returns),
        "small_vs_large": spread_returns(small_returns, large_returns),
        "growth_vs_large": spread_returns(growth_returns, large_returns),
    }
    selected_by_code = {asset.metadata.code: asset for asset in selected}
    risk_shadow = build_portfolio_risk_shadow(
        tuple(
            PortfolioRiskAssetInput(
                code=code,
                weight=float(weight),
                clone_group_id=selected_by_code[code].metadata.tracking_index_code,
                theme_group=(
                    (_portfolio_theme_keys(selected_by_code[code]) or ["unknown"])[0]
                ),
                asset_bucket={
                    "broad_index": "broad_base",
                    "cross_border": "cross_border",
                    "commodity": "commodity",
                    "bond": "bond",
                    "money": "money",
                }.get(selected_by_code[code].metadata.category, "equity"),
            )
            for code, weight in sorted(weights.items())
        ),
        risk_returns,
        factor_returns,
        previous_weights=previous_weights,
    )
    return (
        weights,
        mode,
        {
            "primary_count": len(primary),
            "satellite_count": len(satellite),
            "defensive_count": len(defensive),
            "watch_only_count": watch_only,
            "excluded_count": excluded,
            "selected_codes": list(weights),
            "selected_item_types": item_types,
            "layer_caps": individual_caps,
            "satellite_single_weight_cap": PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
            "satellite_exposure_cap": PORTFOLIO_SATELLITE_EXPOSURE_CAP,
            "target_exposure": round(sum(weights.values()), 4),
            "cash_weight": risk_budget.cash_weight,
            "cash_reason": "风险预算保留部分现金。" if risk_budget.cash_weight > 0.0001 else None,
            "risk_budget_version": risk_budget.contract_version,
            "risk_budget_hash": risk_budget.contract_hash,
            "risk_budget_status": risk_budget.status,
            "risk_summary": risk_budget.metrics,
            "portfolio_risk_shadow_v1": risk_shadow.as_dict(),
            "binding_constraints": list(risk_budget.binding_constraints),
            "constraints": risk_budget.constraints,
        },
    )


def _cash_wait_reason_key(portfolio_context: dict[str, Any], assets: list[ComputedAsset]) -> str:
    if not assets:
        return "no_price_data"
    max_usable_days = max((asset.usable_days for asset in assets), default=0)
    if max_usable_days < 60:
        return "data_warmup"
    primary_count = int(portfolio_context.get("primary_count") or 0)
    defensive_count = int(portfolio_context.get("defensive_count") or 0)
    if primary_count + defensive_count == 0:
        return "risk_filters"
    return "qualified_candidate_shortage"


def _trend_weakening(metrics: dict[str, Any]) -> bool:
    latest = metrics.get("latest_value")
    ma5 = metrics.get("ma5")
    ma10 = metrics.get("ma10")
    return_5d = metrics.get("return_5d")
    return bool(
        isinstance(latest, (int, float))
        and isinstance(ma5, (int, float))
        and isinstance(ma10, (int, float))
        and isinstance(return_5d, (int, float))
        and latest < ma5
        and latest < ma10
        and return_5d < 0
    )


def _risk_action(
    position: ReplayPosition,
    asset: ComputedAsset,
    price: float,
    *,
    close_only: bool = False,
) -> tuple[str | None, float, dict[str, Any]]:
    profit_pct = (price / position.avg_cost - 1.0) * 100 if position.avg_cost > 0 else 0.0
    position.max_profit_pct = max(position.max_profit_pct, profit_pct)
    volatility_unit = max(1.5, float(asset.metrics.get("volatility_20d") or 0.025) * 100)
    hard_stop = -min(max(1.5 * volatility_unit, abs(HARD_STOP_LOSS_PCT)), 4.5)
    profit_start = _clamp(
        ETF_TRAILING_PROFIT_START_VOL_MULTIPLIER * volatility_unit,
        ETF_TRAILING_PROFIT_START_MIN_PCT,
        ETF_TRAILING_PROFIT_START_MAX_PCT,
    )
    giveback = _clamp(
        ETF_TRAILING_GIVEBACK_VOL_MULTIPLIER * volatility_unit,
        ETF_TRAILING_GIVEBACK_MIN_PCT,
        ETF_TRAILING_GIVEBACK_MAX_PCT,
    )
    profit_giveback = position.max_profit_pct - profit_pct
    trend_weak = _trend_weakening(asset.metrics)
    adjusted_close = asset.metrics.get("latest_value", asset.latest_value)
    adjusted_ma5 = asset.metrics.get("ma5")
    ma5_close_break = bool(
        close_only
        and isinstance(adjusted_close, (int, float))
        and isinstance(adjusted_ma5, (int, float))
        and math.isfinite(float(adjusted_close))
        and math.isfinite(float(adjusted_ma5))
        and float(adjusted_close) < float(adjusted_ma5)
    )
    context = {
        "profit_pct": round(profit_pct, 4),
        "max_profit_pct": round(position.max_profit_pct, 4),
        "profit_giveback_pct": round(profit_giveback, 4),
        "hard_stop_pct": round(hard_stop, 4),
        "profit_start_pct": round(profit_start, 4),
        "trailing_giveback_pct": round(giveback, 4),
        "trend_weakening": trend_weak,
        "ma5_close_break_condition_met": ma5_close_break,
        "ma5_close_break_price_basis": "total_return_adjusted" if close_only else None,
        "ma5_close_break_intraday_trigger_allowed": False,
    }
    if asset.conclusion == CONCLUSION_INSUFFICIENT:
        context.update(data_state="data_waiting", reason_code="ranking_data_insufficient")
        return None, 0.0, context
    if asset.conclusion == CONCLUSION_REJECT:
        return ALERT_EXIT_WATCH, 1.0, context
    if profit_pct <= hard_stop:
        return ALERT_HARD_STOP, 1.0, context
    if ma5_close_break:
        return ALERT_MA5_CLOSE_BREAK_EXIT, 1.0, context
    if position.max_profit_pct >= profit_start and profit_giveback >= giveback:
        return ALERT_TRAILING_TAKE_PROFIT, 1.0 if trend_weak else 0.5, context
    if trend_weak:
        return ALERT_TREND_WEAKENING, 0.5, context
    if profit_pct >= max(3.0, profit_start):
        return ALERT_TAKE_PROFIT_WATCH, 0.3, context
    return None, 0.0, context


def _sell_position(
    positions: dict[str, ReplayPosition],
    code: str,
    *,
    price: float,
    fraction: float,
    trade_date: date,
    reason: str,
    fee_rate: float,
    fee_reference_price: float | None = None,
    lot_size: int | None = None,
) -> tuple[ReplayTrade | None, float]:
    position = positions.get(code)
    if position is None or position.shares <= 0 or fraction <= 0:
        return None, 0.0
    requested_shares = min(position.shares, position.shares * fraction)
    if lot_size is not None and fraction < 1.0 - 1e-12:
        shares = float(math.floor(requested_shares / lot_size) * lot_size)
    else:
        shares = requested_shares
    if shares <= 0:
        return None, 0.0
    amount = shares * price
    fee_basis = shares * (fee_reference_price if _finite_positive(fee_reference_price) else price)
    fee = fee_basis * fee_rate
    realized = shares * (price - position.avg_cost) - fee
    trade = ReplayTrade(
        trade_date=trade_date,
        code=code,
        name=position.name,
        side="sell",
        reason=reason,
        amount=_round_money(amount),
        shares=round(shares, 4),
        price=price,
        fee=_round_money(fee),
        realized_pnl=_round_money(realized),
        metadata={"fraction": round(fraction, 4)},
    )
    position.shares -= shares
    if position.shares <= 1e-8:
        positions.pop(code, None)
    return trade, amount - fee


def _buy_position(
    positions: dict[str, ReplayPosition],
    metadata: ShortResearchAsset,
    *,
    price: float,
    amount: float,
    trade_date: date,
    reason: str,
    fee_rate: float,
    lot_size: int = ETF_LOT_SIZE,
    fee_reference_price: float | None = None,
) -> tuple[ReplayTrade | None, float]:
    if price <= 0 or amount <= 0:
        return None, 0.0
    shares = _round_down_to_lot(gross_limit=amount, price=price, lot_size=lot_size)
    if shares <= 0:
        return None, 0.0
    gross = shares * price
    fee_basis = shares * (fee_reference_price if _finite_positive(fee_reference_price) else price)
    fee = fee_basis * fee_rate
    current = positions.get(metadata.code)
    if current is None:
        positions[metadata.code] = ReplayPosition(
            code=metadata.code,
            name=metadata.name,
            shares=shares,
            avg_cost=price,
            entry_date=trade_date,
        )
    else:
        old_cost = current.shares * current.avg_cost
        new_cost = gross
        current.shares += shares
        current.avg_cost = (old_cost + new_cost) / current.shares if current.shares > 0 else price
    trade = ReplayTrade(
        trade_date=trade_date,
        code=metadata.code,
        name=metadata.name,
        side="buy",
        reason=reason,
        amount=_round_money(gross),
        shares=round(shares, 4),
        price=price,
        fee=_round_money(fee),
        realized_pnl=None,
        metadata={},
    )
    return trade, gross + fee


def _attempt_daily_order(
    order: PendingDailyOrder,
    *,
    through_date: date,
    bars: tuple[DailyExecutionBar, ...],
    positions: dict[str, ReplayPosition],
    metadata: ShortResearchAsset | None,
    cash: float,
) -> DailyOrderAttempt:
    resolution = _daily_execution_resolution(
        signal_date=order.signal_date,
        through_date=through_date,
        bars=bars,
    )
    if resolution.status is ExecutionStatus.DEFERRED:
        reason = (
            resolution.deferred_sessions[-1].reason
            if resolution.deferred_sessions
            else "missing_execution_bar"
        )
        return DailyOrderAttempt(status=ExecutionStatus.DEFERRED, reason=reason)
    if resolution.status is ExecutionStatus.REJECTED:
        return DailyOrderAttempt(
            status=ExecutionStatus.REJECTED,
            reason=resolution.rejection_reason or "execution_rejected",
        )
    if resolution.fill is None:
        return DailyOrderAttempt(status=ExecutionStatus.REJECTED, reason="missing_fill_evidence")

    reference_price = resolution.fill.normalized_execution_price
    median_turnover_20d = resolution.fill.median_turnover_20d
    if not _finite_positive(median_turnover_20d):
        return DailyOrderAttempt(
            status=ExecutionStatus.REJECTED,
            reason="liquidity_capacity_turnover_unavailable",
        )
    adv = float(median_turnover_20d)
    base_fill_price = _slipped_price(
        reference_price,
        side=order.side,
        slippage_bps=RANKING_SLIPPAGE_BPS_PER_SIDE,
    )
    if order.side == "buy":
        if metadata is None:
            return DailyOrderAttempt(status=ExecutionStatus.REJECTED, reason="missing_asset_metadata")
        requested_notional = min(
            order.requested_amount,
            cash / (1.0 + DEFAULT_BACKTEST_FEE_RATE),
        )
        gross_limit = min(
            requested_notional,
            adv * ETF_ENTRY_MAX_ADV_PARTICIPATION,
        )
        trade, cash_used = _buy_position(
            positions,
            metadata,
            price=base_fill_price,
            amount=gross_limit,
            trade_date=resolution.fill.session_date,
            reason=order.reason,
            fee_rate=DEFAULT_BACKTEST_FEE_RATE,
            lot_size=ETF_LOT_SIZE,
            fee_reference_price=reference_price,
        )
        cash_delta = -cash_used
        missing_trade_reason = "insufficient_cash_for_one_lot"
    else:
        current = positions.get(order.code)
        if current is None or current.shares <= 0:
            return DailyOrderAttempt(
                status=ExecutionStatus.REJECTED,
                reason="position_missing_or_target_satisfied",
            )
        requested_shares = min(
            current.shares,
            current.shares * order.requested_fraction,
        )
        requested_notional = requested_shares * reference_price
        capacity_fraction = min(
            order.requested_fraction,
            (adv * ETF_EXIT_NORMAL_ADV_PARTICIPATION)
            / (current.shares * reference_price),
        )
        trade, proceeds = _sell_position(
            positions,
            order.code,
            price=base_fill_price,
            fraction=capacity_fraction,
            trade_date=resolution.fill.session_date,
            reason=order.reason,
            fee_rate=DEFAULT_BACKTEST_FEE_RATE,
            fee_reference_price=reference_price,
            lot_size=ETF_LOT_SIZE,
        )
        cash_delta = proceeds
        missing_trade_reason = "position_missing_or_target_satisfied"
    if trade is None:
        return DailyOrderAttempt(status=ExecutionStatus.REJECTED, reason=missing_trade_reason)

    evidence = _execution_cost_evidence(
        side=order.side,
        signal_date=order.signal_date,
        fill_date=resolution.fill.session_date,
        signal_price=order.signal_price,
        reference_price=reference_price,
        shares=trade.shares,
        signal_to_fill_sessions=resolution.fill.signal_to_fill_trading_sessions,
        deferred_reasons=tuple(item.reason for item in resolution.deferred_sessions),
        price_basis="total_return_adjusted_open",
        median_turnover_20d=median_turnover_20d,
        requested_notional=requested_notional,
    )
    trade.metadata.update(order.metadata or {})
    trade.metadata["execution_evidence"] = evidence
    return DailyOrderAttempt(
        status=ExecutionStatus.FILLED,
        trade=trade,
        cash_delta=cash_delta,
        execution_evidence=evidence,
    )


def _equity(cash: float, positions: dict[str, ReplayPosition], prices: dict[str, float]) -> float:
    return cash + sum(position.shares * prices.get(code, 0.0) for code, position in positions.items())


def _benchmark(
    series_by_code: dict[str, list[PricePoint]],
    trading_dates: list[date],
    initial_cash: float,
) -> dict[str, Any]:
    if not trading_dates:
        return {"cash_wait_return": 0.0}
    for code in BENCHMARK_CODES:
        series = series_by_code.get(code) or []
        start = _price_on(series, trading_dates[0])
        end = _price_on(series, trading_dates[-1])
        if start and end:
            return {
                "cash_wait_return": 0.0,
                "buy_hold_code": code,
                "buy_hold_return": round(end / start - 1.0, 4),
                "buy_hold_equity": _round_money(initial_cash * end / start),
                "selection_reason": "优先选择有完整覆盖的宽基或代表性 ETF 做买入持有对照。",
            }
    return {"cash_wait_return": 0.0, "buy_hold_return": None, "selection_reason": "缺少可用宽基对照数据。"}


def _benchmark_equity(
    benchmark: dict[str, Any],
    series_by_code: dict[str, list[PricePoint]],
    first_date: date,
    trade_date: date,
    initial_cash: float,
) -> float | None:
    code = benchmark.get("buy_hold_code")
    if not code:
        return None
    series = series_by_code.get(str(code)) or []
    start = _price_on(series, first_date)
    current = _price_on(series, trade_date)
    if not start or not current:
        return None
    return _round_money(initial_cash * current / start)


def _label_summaries(
    label_samples: dict[tuple[str, str, int], list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for (label, entry_label, horizon), samples in sorted(label_samples.items()):
        returns = [item[0] for item in samples]
        drawdowns = [item[1] for item in samples]
        count = len(returns)
        confidence = "insufficient"
        if count >= 50:
            confidence = "medium"
        if count >= 120:
            confidence = "higher"
        summaries.append(
            {
                "label": label,
                "entry_timing_label": entry_label,
                "horizon_days": horizon,
                "sample_count": count,
                "avg_return": round(mean(returns), 4) if returns else None,
                "median_return": round(median(returns), 4) if returns else None,
                "win_rate": round(sum(1 for item in returns if item > 0) / count, 4) if count else None,
                "worst_forward_drawdown": round(min(drawdowns), 4) if drawdowns else None,
                "confidence": confidence,
                "metrics": {"note": "样本少时只能观察，不能证明标签稳定有效。"},
            }
        )
    return summaries


async def run_etf_portfolio_backtest(
    session: AsyncSession,
    *,
    user: User | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    days: int = DEFAULT_BACKTEST_DAYS,
    initial_cash: float | None = None,
    fee_rate: float = DEFAULT_BACKTEST_FEE_RATE,
    max_assets: int = 180,
) -> EtfPortfolioBacktestRun:
    effective_end = end_date or await session.scalar(select(func.max(EtfPriceHistory.trade_date))) or date.today()
    effective_start = start_date or (effective_end - timedelta(days=days))
    if effective_start >= effective_end:
        raise ValueError("回测开始日期必须早于结束日期")
    capital = float(initial_cash or (user.etf_trading_capital if user else DEFAULT_BACKTEST_INITIAL_CASH) or DEFAULT_BACKTEST_INITIAL_CASH)
    run = await _create_backtest_run(
        session,
        user_id=user.id if user else None,
        start_date=effective_start,
        end_date=effective_end,
        initial_cash=capital,
        fee_rate=fee_rate,
        max_assets=max_assets,
    )
    await session.commit()
    try:
        metadata = await _load_etf_universe(session, max_assets=max_assets)
        if len(metadata) < 20:
            raise ValueError("ETF 历史池太小，无法做有意义的组合回测")
        metadata_by_code = {item.code: item for item in metadata}
        lookback_start = effective_start - timedelta(days=260)
        series_by_code = await _load_price_series(
            session,
            codes=list(metadata_by_code),
            from_date=lookback_start,
            to_date=effective_end,
        )
        execution_bars_by_code = await _load_daily_execution_bars(
            session,
            codes=list(metadata_by_code),
            from_date=effective_start,
            to_date=effective_end,
        )
        all_trading_dates = _trading_dates(series_by_code, lookback_start, effective_end)
        all_trading_date_index = {item: index for index, item in enumerate(all_trading_dates)}
        trading_dates = [
            item
            for item in all_trading_dates
            if effective_start <= item <= effective_end and _previous_date(all_trading_dates, item) is not None
        ]
        if len(trading_dates) < 30:
            raise ValueError("可用交易日少于 30 天，样本不足，暂不生成回测结论")

        cash = capital
        positions: dict[str, ReplayPosition] = {}
        high_watermark = capital
        total_fees = 0.0
        turnover = 0.0
        total_base_slippage = 0.0
        total_stress_slippage = 0.0
        total_stress_incremental_cost = 0.0
        stress_high_watermark = capital
        stress_max_drawdown = 0.0
        realized_trades = 0
        winning_trades = 0
        trade_rows: list[EtfPortfolioBacktestTrade] = []
        label_samples: dict[tuple[str, str, int], list[tuple[float, float]]] = defaultdict(list)
        latest_assets: list[ComputedAsset] = []
        benchmark = _benchmark(series_by_code, trading_dates, capital)
        first_signal_date: date | None = None
        cash_wait_reason_counts: dict[str, int] = defaultdict(int)
        portfolio_mode_counts: dict[str, int] = defaultdict(int)
        target_exposure_sum = 0.0
        last_target_exposure = 0.0
        partial_allocation_days = 0
        full_cash_days = 0
        pending_orders: dict[str, PendingDailyOrder] = {}
        order_sequence = 0
        execution_deferred_counts: dict[str, int] = defaultdict(int)
        execution_excluded_counts: dict[str, int] = defaultdict(int)
        liquidity_capacity_status_counts: dict[str, int] = defaultdict(int)
        entry_capacity_exceeded_count = 0
        stress_entry_capacity_exceeded_count = 0
        max_stress_exit_days: float | None = None
        last_portfolio_context: dict[str, Any] = {}
        previous_target_weights: dict[str, float] = {}

        for date_index, trade_date in enumerate(trading_dates):
            signal_date = _previous_date(all_trading_dates, trade_date)
            if signal_date is None:
                continue
            assets = _build_daily_assets(metadata_by_code, series_by_code, signal_date)
            latest_assets = assets
            asset_by_code = {asset.metadata.code: asset for asset in assets}
            target_weights, portfolio_mode, portfolio_context = _generate_target_weights(
                assets,
                return_maps=_risk_return_maps_for_date(series_by_code, signal_date),
                previous_weights=previous_target_weights,
            )
            previous_target_weights = dict(target_weights)
            last_portfolio_context = portfolio_context
            target_exposure = round(sum(float(weight) for weight in target_weights.values()), 4)
            last_target_exposure = target_exposure
            target_exposure_sum += target_exposure
            portfolio_mode_counts[portfolio_mode] += 1
            if target_exposure > 0 and first_signal_date is None:
                first_signal_date = signal_date
            if target_exposure < 0.999:
                partial_allocation_days += 1
            if target_exposure <= 0:
                full_cash_days += 1
                cash_wait_reason_counts[_cash_wait_reason_key(portfolio_context, assets)] += 1
            signal_prices = {
                code: price
                for code, series in series_by_code.items()
                if (price := _price_on(series, signal_date)) is not None
            }

            risk_signal_codes: set[str] = set()
            for code in list(positions):
                price = signal_prices.get(code)
                asset = asset_by_code.get(code)
                if price is None or asset is None:
                    continue
                alert_type, fraction, context = _risk_action(positions[code], asset, price, close_only=True)
                if alert_type is None:
                    continue
                risk_signal_codes.add(code)
                target_weights.pop(code, None)
                if pending_orders.get(code) is not None and pending_orders[code].side == "buy":
                    pending_orders.pop(code, None)
                if code in pending_orders:
                    continue
                order_sequence += 1
                pending_orders[code] = PendingDailyOrder(
                    order_id=order_sequence,
                    signal_date=signal_date,
                    code=code,
                    name=positions[code].name,
                    side="sell",
                    reason=alert_type,
                    signal_price=price,
                    requested_fraction=fraction,
                    metadata=context,
                )

            current_equity = _equity(cash, positions, signal_prices)
            for code, target_weight in sorted(target_weights.items(), key=lambda item: item[1], reverse=True):
                asset = asset_by_code.get(code)
                price = signal_prices.get(code)
                if asset is None or price is None or code in pending_orders or code in risk_signal_codes:
                    continue
                target_value = current_equity * target_weight
                current_position = positions.get(code)
                current_value = (current_position.shares * price) if current_position else 0.0
                delta = target_value - current_value
                if delta < -max(100.0, current_equity * 0.01):
                    fraction = min(1.0, abs(delta) / current_value) if current_value > 0 else 0.0
                    order_sequence += 1
                    pending_orders[code] = PendingDailyOrder(
                        order_id=order_sequence,
                        signal_date=signal_date,
                        code=code,
                        name=asset.metadata.name,
                        side="sell",
                        reason="target_rebalance",
                        signal_price=price,
                        requested_fraction=fraction,
                    )
                elif delta > max(100.0, current_equity * 0.01) and cash > 100:
                    order_sequence += 1
                    pending_orders[code] = PendingDailyOrder(
                        order_id=order_sequence,
                        signal_date=signal_date,
                        code=code,
                        name=asset.metadata.name,
                        side="buy",
                        reason="target_rebalance",
                        signal_price=price,
                        requested_amount=min(delta, cash / (1 + DEFAULT_BACKTEST_FEE_RATE)),
                    )

            for code, order in sorted(
                list(pending_orders.items()),
                key=lambda item: (0 if item[1].side == "sell" else 1, item[1].signal_date, item[1].order_id),
            ):
                attempt = _attempt_daily_order(
                    order,
                    through_date=trade_date,
                    bars=execution_bars_by_code.get(code, ()),
                    positions=positions,
                    metadata=metadata_by_code.get(code),
                    cash=cash,
                )
                if attempt.status is ExecutionStatus.DEFERRED:
                    execution_deferred_counts[attempt.reason or "execution_deferred"] += 1
                    continue
                pending_orders.pop(code, None)
                if attempt.status is ExecutionStatus.REJECTED or attempt.trade is None:
                    execution_excluded_counts[attempt.reason or "execution_rejected"] += 1
                    continue
                trade = attempt.trade
                cash += attempt.cash_delta
                total_fees += trade.fee
                turnover += trade.amount
                if trade.realized_pnl is not None:
                    realized_trades += 1
                    winning_trades += 1 if trade.realized_pnl > 0 else 0
                evidence = attempt.execution_evidence or {}
                liquidity_capacity = dict(
                    evidence.get("liquidity_capacity_stress") or {}
                )
                liquidity_capacity_status_counts[
                    str(liquidity_capacity.get("status") or "unavailable")
                ] += 1
                entry_capacity_exceeded_count += int(
                    liquidity_capacity.get("entry_capacity_exceeded") is True
                )
                stress_entry_capacity_exceeded_count += int(
                    liquidity_capacity.get("stress_entry_capacity_exceeded") is True
                )
                stress_exit_days = liquidity_capacity.get("stress_exit_days")
                if isinstance(stress_exit_days, int | float) and not isinstance(
                    stress_exit_days,
                    bool,
                ) and math.isfinite(float(stress_exit_days)):
                    max_stress_exit_days = max(
                        max_stress_exit_days or 0.0,
                        float(stress_exit_days),
                    )
                base_cost = dict(evidence.get("base") or {})
                stress_cost = dict(evidence.get("stress") or {})
                total_base_slippage += float(base_cost.get("slippage_cost") or 0.0)
                total_stress_slippage += float(stress_cost.get("slippage_cost") or 0.0)
                total_stress_incremental_cost += max(
                    float(stress_cost.get("total_cost") or 0.0)
                    - float(base_cost.get("total_cost") or 0.0),
                    0.0,
                )
                trade_rows.append(
                    EtfPortfolioBacktestTrade(
                        run_id=run.id,
                        trade_date=trade.trade_date,
                        etf_code=trade.code,
                        etf_name=trade.name,
                        side=trade.side,
                        reason=trade.reason,
                        amount=trade.amount,
                        shares=trade.shares,
                        price=trade.price,
                        fee=trade.fee,
                        realized_pnl=trade.realized_pnl,
                        metadata_json=trade.metadata,
                    )
                )

            prices = {
                code: price
                for code, series in series_by_code.items()
                if (price := _price_on(series, trade_date)) is not None
            }

            final_equity = _equity(cash, positions, prices)
            high_watermark = max(high_watermark, final_equity)
            drawdown = final_equity / high_watermark - 1.0 if high_watermark > 0 else 0.0
            stress_equity = max(final_equity - total_stress_incremental_cost, 0.0)
            stress_high_watermark = max(stress_high_watermark, stress_equity)
            stress_drawdown = stress_equity / stress_high_watermark - 1.0 if stress_high_watermark > 0 else 0.0
            stress_max_drawdown = min(stress_max_drawdown, stress_drawdown)
            session.add(
                EtfPortfolioBacktestEquityCurve(
                    run_id=run.id,
                    curve_date=trade_date,
                    equity=_round_money(final_equity),
                    cash=_round_money(cash),
                    drawdown=round(drawdown, 4),
                    benchmark_equity=_benchmark_equity(benchmark, series_by_code, trading_dates[0], trade_date, capital),
                    portfolio_mode=portfolio_mode,
                )
            )
            for code, position in positions.items():
                price = prices.get(code)
                if price is None:
                    continue
                market_value = position.shares * price
                session.add(
                    EtfPortfolioBacktestPosition(
                        run_id=run.id,
                        snapshot_date=trade_date,
                        etf_code=code,
                        etf_name=position.name,
                        shares=round(position.shares, 4),
                        price=price,
                        market_value=_round_money(market_value),
                        weight=round(market_value / final_equity, 4) if final_equity > 0 else 0.0,
                        cost_basis=round(position.avg_cost, 4),
                        unrealized_pnl=_round_money(position.shares * (price - position.avg_cost)),
                        metadata_json={"entry_date": position.entry_date.isoformat(), "max_profit_pct": round(position.max_profit_pct, 4)},
                    )
                )

            for code in target_weights:
                asset = asset_by_code.get(code)
                series = series_by_code.get(code) or []
                if asset is None:
                    continue
                current_price = _price_on(series, signal_date)
                if not current_price:
                    continue
                signal_index = all_trading_date_index[signal_date]
                for horizon in LABEL_HORIZONS:
                    future_index = signal_index + horizon
                    if future_index >= len(all_trading_dates):
                        continue
                    future_date = all_trading_dates[future_index]
                    future_price = _price_on(series, future_date)
                    if not future_price:
                        continue
                    window_dates = set(all_trading_dates[signal_index + 1 : future_index + 1])
                    window_prices = [item.value for item in series if item.point_date in window_dates]
                    forward_return = future_price / current_price - 1.0
                    worst_drawdown = min((price / current_price - 1.0 for price in window_prices), default=0.0)
                    label_samples[(asset.conclusion, asset.entry_timing_label, horizon)].append(
                        (forward_return, worst_drawdown)
                    )

            if date_index % 20 == 0:
                await session.flush()

        session.add_all(trade_rows)
        label_summaries = _label_summaries(label_samples)
        for item in label_summaries:
            session.add(
                EtfPortfolioBacktestLabelSummary(
                    run_id=run.id,
                    label=item["label"],
                    entry_timing_label=item["entry_timing_label"],
                    horizon_days=item["horizon_days"],
                    sample_count=item["sample_count"],
                    avg_return=item["avg_return"],
                    median_return=item["median_return"],
                    win_rate=item["win_rate"],
                    worst_forward_drawdown=item["worst_forward_drawdown"],
                    confidence=item["confidence"],
                    metrics_json=item["metrics"],
                )
            )
        final_equity = _equity(cash, positions, {code: _price_on(series, trading_dates[-1]) or 0.0 for code, series in series_by_code.items()})
        first_trade_date = min((row.trade_date for row in trade_rows), default=None)
        average_target_exposure = target_exposure_sum / len(trading_dates) if trading_dates else 0.0
        base_max_drawdown = round(
            min(
                (
                    row.drawdown
                    for row in (
                        await session.scalars(
                            select(EtfPortfolioBacktestEquityCurve).where(
                                EtfPortfolioBacktestEquityCurve.run_id == run.id
                            )
                        )
                    ).all()
                ),
                default=0.0,
            ),
            4,
        )
        stress_final_equity = max(final_equity - total_stress_incremental_cost, 0.0)
        base_total_cost = total_fees + total_base_slippage
        stress_total_cost = total_fees + total_stress_slippage
        action_cycle_unavailable = {
            "status": "unavailable",
            "reason": "portfolio_replay_has_no_observed_action_cycle_counterfactual",
        }
        execution_scenarios = {
            "contract_version": EXECUTION_RISK_CONTRACT_VERSION,
            "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
            "simulated_not_observed": True,
            "spread_evidence": "modeled_sensitivity_not_observed",
            "base": {
                "provenance": "simulated_fill_base",
                "net_return": round(final_equity / capital - 1.0, 4),
                "max_drawdown": base_max_drawdown,
                "turnover": round(turnover / capital, 4),
                "fee": _round_money(total_fees),
                "slippage_cost": _round_money(total_base_slippage),
                "spread_cost": None,
                "total_cost": _round_money(base_total_cost),
                "action_cycle_benefit": action_cycle_unavailable,
            },
            "stress": {
                "provenance": "simulated_fill_stress",
                "same_action_path_sensitivity": True,
                "net_return": round(stress_final_equity / capital - 1.0, 4),
                "max_drawdown": round(stress_max_drawdown, 4),
                "turnover": round(turnover / capital, 4),
                "fee": _round_money(total_fees),
                "slippage_cost": _round_money(total_stress_slippage),
                "spread_cost": None,
                "total_cost": _round_money(stress_total_cost),
                "action_cycle_benefit": action_cycle_unavailable,
            },
            "liquidity_capacity_stress": {
                "status_counts": dict(liquidity_capacity_status_counts),
                "entry_capacity_exceeded_count": entry_capacity_exceeded_count,
                "stress_entry_capacity_exceeded_count": (
                    stress_entry_capacity_exceeded_count
                ),
                "max_stress_exit_days": max_stress_exit_days,
                "blocked_exit_sessions": int(
                    execution_deferred_counts.get(
                        "limit_lock_without_demonstrable_liquidity",
                        0,
                    )
                ),
                "simulation_only": True,
            },
        }
        metrics = {
            "cumulative_return": round(final_equity / capital - 1.0, 4),
            "max_drawdown": base_max_drawdown,
            "trade_count": len(trade_rows),
            "sell_count": realized_trades,
            "win_rate": round(winning_trades / realized_trades, 4) if realized_trades else None,
            "turnover": round(turnover / capital, 4),
            "total_fees": _round_money(total_fees),
            "execution_scenarios": execution_scenarios,
            "pending_order_count": len(pending_orders),
            "execution_deferred_counts": dict(execution_deferred_counts),
            "execution_excluded_counts": dict(execution_excluded_counts),
            "liquidity_capacity_status_counts": dict(liquidity_capacity_status_counts),
            "entry_capacity_exceeded_count": entry_capacity_exceeded_count,
            "stress_entry_capacity_exceeded_count": stress_entry_capacity_exceeded_count,
            "max_stress_exit_days": max_stress_exit_days,
            "average_holding_days": round(mean([(trading_dates[-1] - item.entry_date).days for item in positions.values()]), 2)
            if positions
            else None,
            "average_target_exposure": round(average_target_exposure, 4),
            "last_target_exposure": round(last_target_exposure, 4),
            "partial_allocation_days": partial_allocation_days,
            "full_cash_days": full_cash_days,
            "portfolio_rule": "前一交易日信号 + 下一合格交易日复权开盘成交 + 日线风控",
            "portfolio_risk_budget": last_portfolio_context,
        }
        caveats = [
            "这是历史日线回测，不代表未来收益。",
            "信号最早在下一合格交易日复权开盘模拟成交；停牌、零量、缺价或不可证明成交时不使用收盘价兜底。",
            "日线历史没有可验证 bid/ask，价差和盘中路径只标记为未观察敏感性；压力情景沿用同一动作路径。",
            "回测不连接券商，不会自动买卖。",
        ]
        if len(trading_dates) < 120:
            caveats.append("样本交易日少于 120 天，只能观察，不能下结论。")
            caveats.append("如需更长历史，请先在后台任务运行 ETF 长历史日线回填，再重新生成回测。")
        data_coverage = {
            "requested_start_date": effective_start.isoformat(),
            "requested_end_date": effective_end.isoformat(),
            "lookback_start_date": lookback_start.isoformat(),
            "asset_count": len(metadata),
            "priced_asset_count": len(series_by_code),
            "trading_days": len(trading_dates),
            "start_date": trading_dates[0].isoformat(),
            "end_date": trading_dates[-1].isoformat(),
            "effective_start_date": trading_dates[0].isoformat(),
            "effective_end_date": trading_dates[-1].isoformat(),
            "latest_replay_asset_count": len(latest_assets),
            "sample_insufficient": len(trading_dates) < 120,
            "warmup_days": max(0, (first_signal_date - trading_dates[0]).days) if first_signal_date else len(trading_dates),
            "first_signal_date": first_signal_date.isoformat() if first_signal_date else None,
            "first_trade_date": first_trade_date.isoformat() if first_trade_date else None,
            "cash_wait_reason_counts": dict(cash_wait_reason_counts),
            "portfolio_mode_counts": dict(portfolio_mode_counts),
            "partial_allocation_days": partial_allocation_days,
            "full_cash_days": full_cash_days,
            "average_target_exposure": round(average_target_exposure, 4),
            "last_target_exposure": round(last_target_exposure, 4),
            "pending_order_count": len(pending_orders),
            "execution_deferred_counts": dict(execution_deferred_counts),
            "execution_excluded_counts": dict(execution_excluded_counts),
            "liquidity_capacity_status_counts": dict(liquidity_capacity_status_counts),
            "entry_capacity_exceeded_count": entry_capacity_exceeded_count,
            "stress_entry_capacity_exceeded_count": stress_entry_capacity_exceeded_count,
            "max_stress_exit_days": max_stress_exit_days,
            "execution_model": EXECUTION_MODEL_DAILY_ADJUSTED_OPEN,
            "simulated_not_observed": True,
            "portfolio_risk_budget": last_portfolio_context,
        }
        run.status = "success"
        run.finished_at = utcnow()
        run.metrics_json = metrics
        run.benchmark_json = benchmark
        run.data_coverage_json = data_coverage
        run.caveats_json = caveats
        await session.commit()
        await session.refresh(run)
        return run
    except Exception as exc:
        message = str(exc) or "ETF 组合回测失败，请检查历史日线数据"
        return await mark_backtest_failed(session, run, message)


async def run_etf_intraday_alert_backtest(
    session: AsyncSession,
    *,
    user: User | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    days: int = DEFAULT_BACKTEST_DAYS,
    initial_cash: float | None = None,
    fee_rate: float = DEFAULT_BACKTEST_FEE_RATE,
    max_assets: int = 180,
    execution_delay_minutes: int = DEFAULT_INTRADAY_EXECUTION_DELAY_MINUTES,
) -> EtfPortfolioBacktestRun:
    latest_intraday_date = await session.scalar(select(func.max(EtfIntradayQuote.trade_date)))
    effective_end = end_date or latest_intraday_date or date.today()
    effective_start = start_date or (effective_end - timedelta(days=days))
    if effective_start >= effective_end:
        raise ValueError("回测开始日期必须早于结束日期")
    capital = float(initial_cash or (user.etf_trading_capital if user else DEFAULT_BACKTEST_INITIAL_CASH) or DEFAULT_BACKTEST_INITIAL_CASH)
    run = await _create_backtest_run(
        session,
        user_id=user.id if user else None,
        start_date=effective_start,
        end_date=effective_end,
        initial_cash=capital,
        fee_rate=fee_rate,
        max_assets=max_assets,
        execution_model=EXECUTION_MODEL_INTRADAY_ALERT,
        execution_label="intraday_alert",
        exit_rule_version=INTRADAY_BACKTEST_EXIT_RULE_VERSION,
        execution_delay_minutes=execution_delay_minutes,
    )
    await session.commit()
    try:
        metadata = await _load_etf_universe(session, max_assets=max_assets)
        if len(metadata) < 20:
            raise ValueError("ETF 历史池太小，无法做有意义的盘中提醒回测")
        metadata_by_code = {item.code: item for item in metadata}
        lookback_start = effective_start - timedelta(days=260)
        series_by_code = await _load_price_series(
            session,
            codes=list(metadata_by_code),
            from_date=lookback_start,
            to_date=effective_end,
        )
        daily_dates = _trading_dates(series_by_code, lookback_start, effective_end)
        intraday_by_code = await _load_intraday_quotes(
            session,
            codes=list(metadata_by_code),
            from_date=effective_start,
            to_date=effective_end,
        )
        quote_days = sorted(
            {
                trade_date
                for by_date in intraday_by_code.values()
                for trade_date, quotes in by_date.items()
                if quotes and effective_start <= trade_date <= effective_end
            }
        )
        trading_dates = [item for item in quote_days if _previous_date(daily_dates, item) is not None]
        if not trading_dates:
            raise ValueError("盘中历史不足，无法生成盘中提醒执行回测")
        benchmark_dates = [item for item in daily_dates if trading_dates[0] <= item <= trading_dates[-1]]

        cash = capital
        positions: dict[str, ReplayPosition] = {}
        high_watermark = capital
        total_fees = 0.0
        turnover = 0.0
        total_base_slippage = 0.0
        total_stress_slippage = 0.0
        total_stress_incremental_cost = 0.0
        total_observed_spread_cost = 0.0
        stress_high_watermark = capital
        stress_max_drawdown = 0.0
        realized_trades = 0
        winning_trades = 0
        unfilled_alert_count = 0
        execution_excluded_counts: dict[str, int] = defaultdict(int)
        trade_rows: list[EtfPortfolioBacktestTrade] = []
        benchmark = _benchmark(series_by_code, benchmark_dates or trading_dates, capital)
        portfolio_mode_counts: dict[str, int] = defaultdict(int)
        cash_wait_reason_counts: dict[str, int] = defaultdict(int)
        target_exposure_sum = 0.0
        partial_allocation_days = 0
        full_cash_days = 0
        first_signal_date: date | None = None
        first_trade_date: date | None = None
        last_target_exposure = 0.0
        last_intraday_prices: dict[str, float] = {}
        latest_assets: list[ComputedAsset] = []
        last_portfolio_context: dict[str, Any] = {}

        for trade_date in trading_dates:
            signal_date = _previous_date(daily_dates, trade_date)
            if signal_date is None:
                continue
            assets = _build_daily_assets(metadata_by_code, series_by_code, signal_date)
            latest_assets = assets
            asset_by_code = {asset.metadata.code: asset for asset in assets}
            target_weights, portfolio_mode, portfolio_context = _generate_target_weights(
                assets,
                return_maps=_risk_return_maps_for_date(series_by_code, signal_date),
            )
            last_portfolio_context = portfolio_context
            target_exposure = round(sum(float(weight) for weight in target_weights.values()), 4)
            last_target_exposure = target_exposure
            target_exposure_sum += target_exposure
            portfolio_mode_counts[portfolio_mode] += 1
            if target_exposure > 0 and first_signal_date is None:
                first_signal_date = signal_date
            if target_exposure < 0.999:
                partial_allocation_days += 1
            if target_exposure <= 0:
                full_cash_days += 1
                cash_wait_reason_counts[_cash_wait_reason_key(portfolio_context, assets)] += 1

            day_quotes_by_code = {
                code: intraday_by_code.get(code, {}).get(trade_date, [])
                for code in metadata_by_code
                if intraday_by_code.get(code, {}).get(trade_date)
            }
            opening_buy_quotes = {
                code: quote
                for code, quotes in day_quotes_by_code.items()
                if (quote := _first_eligible_intraday_quote(quotes, side="buy")) is not None
            }
            opening_sell_quotes = {
                code: quote
                for code, quotes in day_quotes_by_code.items()
                if (quote := _first_eligible_intraday_quote(quotes, side="sell")) is not None
            }
            opening_mark_prices = {
                code: quote.latest_price
                for code, quote in {**opening_buy_quotes, **opening_sell_quotes}.items()
                if _finite_positive(quote.latest_price)
            }
            last_intraday_prices.update(opening_mark_prices)
            current_equity = _equity(cash, positions, last_intraday_prices)

            for code in list(positions):
                if code in target_weights:
                    continue
                quote = opening_sell_quotes.get(code)
                reference_price, reason = _intraday_execution_price(quote, side="sell")
                if quote is None or reference_price is None:
                    execution_excluded_counts[reason or "missing_executable_sell_quote"] += 1
                    continue
                signal_price = _price_on(series_by_code.get(code, []), signal_date)
                if not _finite_positive(signal_price):
                    execution_excluded_counts["missing_signal_close"] += 1
                    continue
                price = _slipped_price(
                    reference_price,
                    side="sell",
                    slippage_bps=RANKING_SLIPPAGE_BPS_PER_SIDE,
                )
                trade, cash_delta = _sell_position(
                    positions,
                    code,
                    price=price,
                    fraction=1.0,
                    trade_date=trade_date,
                    reason="target_rebalance",
                    fee_rate=DEFAULT_BACKTEST_FEE_RATE,
                    fee_reference_price=reference_price,
                    lot_size=ETF_LOT_SIZE,
                )
                if trade is None:
                    continue
                cash += cash_delta
                total_fees += trade.fee
                turnover += trade.amount
                realized_trades += 1
                winning_trades += 1 if (trade.realized_pnl or 0.0) > 0 else 0
                spread_pct = _intraday_spread_fraction(quote)
                evidence = _execution_cost_evidence(
                    side="sell",
                    signal_date=signal_date,
                    fill_date=trade_date,
                    signal_price=float(signal_price),
                    reference_price=reference_price,
                    shares=trade.shares,
                    signal_to_fill_sessions=1,
                    deferred_reasons=(),
                    price_basis="observed_sell_bid",
                    execution_time=quote.quote_time,
                    spread_pct=spread_pct,
                    spread_observed=spread_pct is not None,
                )
                trade.metadata.update(
                    {
                        "execution_model": "intraday_alert",
                        "execution_time": quote.quote_time.isoformat() if quote.quote_time else None,
                        "execution_price": price,
                        "execution_delay_minutes": 0,
                        "signal_date": signal_date.isoformat(),
                        "source": quote.source,
                        "execution_evidence": evidence,
                    }
                )
                total_base_slippage += float(evidence["base"]["slippage_cost"])
                total_stress_slippage += float(evidence["stress"]["slippage_cost"])
                total_observed_spread_cost += float(evidence.get("spread_cost") or 0.0)
                total_stress_incremental_cost += float(evidence["stress"]["total_cost"]) - float(
                    evidence["base"]["total_cost"]
                )
                first_trade_date = first_trade_date or trade_date
                trade_rows.append(
                    EtfPortfolioBacktestTrade(
                        run_id=run.id,
                        trade_date=trade.trade_date,
                        etf_code=trade.code,
                        etf_name=trade.name,
                        side=trade.side,
                        reason=trade.reason,
                        amount=trade.amount,
                        shares=trade.shares,
                        price=trade.price,
                        fee=trade.fee,
                        realized_pnl=trade.realized_pnl,
                        metadata_json=trade.metadata,
                    )
                )

            current_equity = _equity(cash, positions, last_intraday_prices)
            for code, target_weight in sorted(target_weights.items(), key=lambda item: item[1], reverse=True):
                asset = asset_by_code.get(code)
                mark_price = opening_mark_prices.get(code)
                signal_price = _price_on(series_by_code.get(code, []), signal_date)
                if asset is None or not _finite_positive(mark_price) or not _finite_positive(signal_price):
                    execution_excluded_counts["missing_signal_or_mark_price"] += 1
                    continue
                target_value = current_equity * target_weight
                current_position = positions.get(code)
                current_value = (current_position.shares * float(mark_price)) if current_position else 0.0
                delta = target_value - current_value
                if delta < -max(100.0, current_equity * 0.01):
                    side = "sell"
                    quote = opening_sell_quotes.get(code)
                    reference_price, reason = _intraday_execution_price(quote, side=side)
                    if quote is None or reference_price is None:
                        execution_excluded_counts[reason or "missing_executable_sell_quote"] += 1
                        continue
                    price = _slipped_price(
                        reference_price,
                        side=side,
                        slippage_bps=RANKING_SLIPPAGE_BPS_PER_SIDE,
                    )
                    fraction = min(1.0, abs(delta) / current_value) if current_value > 0 else 0.0
                    trade, cash_delta = _sell_position(
                        positions,
                        code,
                        price=price,
                        fraction=fraction,
                        trade_date=trade_date,
                        reason="target_rebalance",
                        fee_rate=DEFAULT_BACKTEST_FEE_RATE,
                        fee_reference_price=reference_price,
                        lot_size=ETF_LOT_SIZE,
                    )
                    if trade is None:
                        continue
                    cash += cash_delta
                    total_fees += trade.fee
                    turnover += trade.amount
                    if trade.realized_pnl is not None:
                        realized_trades += 1
                        winning_trades += 1 if trade.realized_pnl > 0 else 0
                elif delta > max(100.0, current_equity * 0.01) and cash > 100:
                    side = "buy"
                    quote = opening_buy_quotes.get(code)
                    reference_price, reason = _intraday_execution_price(quote, side=side)
                    if quote is None or reference_price is None:
                        execution_excluded_counts[reason or "missing_executable_buy_quote"] += 1
                        continue
                    price = _slipped_price(
                        reference_price,
                        side=side,
                        slippage_bps=RANKING_SLIPPAGE_BPS_PER_SIDE,
                    )
                    buy_amount = min(delta, cash / (1 + DEFAULT_BACKTEST_FEE_RATE))
                    trade, cash_used = _buy_position(
                        positions,
                        asset.metadata,
                        price=price,
                        amount=buy_amount,
                        trade_date=trade_date,
                        reason="target_rebalance",
                        fee_rate=DEFAULT_BACKTEST_FEE_RATE,
                        lot_size=ETF_LOT_SIZE,
                        fee_reference_price=reference_price,
                    )
                    if trade is None:
                        continue
                    cash -= cash_used
                    total_fees += trade.fee
                    turnover += trade.amount
                else:
                    continue
                spread_pct = _intraday_spread_fraction(quote)
                evidence = _execution_cost_evidence(
                    side=side,
                    signal_date=signal_date,
                    fill_date=trade_date,
                    signal_price=float(signal_price),
                    reference_price=reference_price,
                    shares=trade.shares,
                    signal_to_fill_sessions=1,
                    deferred_reasons=(),
                    price_basis="observed_buy_ask" if side == "buy" else "observed_sell_bid",
                    execution_time=quote.quote_time,
                    spread_pct=spread_pct,
                    spread_observed=spread_pct is not None,
                )
                trade.metadata.update(
                    {
                        "execution_model": "intraday_alert",
                        "execution_time": quote.quote_time.isoformat() if quote.quote_time else None,
                        "execution_price": price,
                        "execution_delay_minutes": 0,
                        "signal_date": signal_date.isoformat(),
                        "source": quote.source,
                        "execution_evidence": evidence,
                    }
                )
                total_base_slippage += float(evidence["base"]["slippage_cost"])
                total_stress_slippage += float(evidence["stress"]["slippage_cost"])
                total_observed_spread_cost += float(evidence.get("spread_cost") or 0.0)
                total_stress_incremental_cost += float(evidence["stress"]["total_cost"]) - float(
                    evidence["base"]["total_cost"]
                )
                first_trade_date = first_trade_date or trade_date
                trade_rows.append(
                    EtfPortfolioBacktestTrade(
                        run_id=run.id,
                        trade_date=trade.trade_date,
                        etf_code=trade.code,
                        etf_name=trade.name,
                        side=trade.side,
                        reason=trade.reason,
                        amount=trade.amount,
                        shares=trade.shares,
                        price=trade.price,
                        fee=trade.fee,
                        realized_pnl=trade.realized_pnl,
                        metadata_json=trade.metadata,
                    )
                )

            attempted_alerts: set[tuple[str, str]] = set()
            day_events = sorted(
                (
                    (quote.quote_time, code, quote)
                    for code, quotes in day_quotes_by_code.items()
                    for quote in quotes
                    if quote.quote_time is not None
                    and _intraday_execution_price(quote, side="sell")[0] is not None
                ),
                key=lambda item: item[0],
            )
            for signal_time, code, quote in day_events:
                last_intraday_prices[code] = quote.latest_price
                position = positions.get(code)
                asset = asset_by_code.get(code)
                if position is None or asset is None:
                    continue
                alert_type, fraction, context = _risk_action(position, asset, quote.latest_price)
                if alert_type is None:
                    continue
                alert_key = (code, alert_type)
                if alert_key in attempted_alerts:
                    continue
                attempted_alerts.add(alert_key)
                execution_quote = _first_execution_quote_after(
                    day_quotes_by_code.get(code, []),
                    signal_time,
                    delay_minutes=execution_delay_minutes,
                    side="sell",
                )
                if execution_quote is None:
                    unfilled_alert_count += 1
                    execution_excluded_counts["missing_eligible_delayed_sell_quote"] += 1
                    continue
                reference_price, reason = _intraday_execution_price(execution_quote, side="sell")
                if reference_price is None:
                    unfilled_alert_count += 1
                    execution_excluded_counts[reason or "missing_executable_sell_quote"] += 1
                    continue
                execution_price = _slipped_price(
                    reference_price,
                    side="sell",
                    slippage_bps=RANKING_SLIPPAGE_BPS_PER_SIDE,
                )
                trade, cash_delta = _sell_position(
                    positions,
                    code,
                    price=execution_price,
                    fraction=fraction,
                    trade_date=trade_date,
                    reason=alert_type,
                    fee_rate=DEFAULT_BACKTEST_FEE_RATE,
                    fee_reference_price=reference_price,
                    lot_size=ETF_LOT_SIZE,
                )
                if trade is None:
                    continue
                last_intraday_prices[code] = execution_quote.latest_price
                cash += cash_delta
                total_fees += trade.fee
                turnover += trade.amount
                realized_trades += 1
                winning_trades += 1 if (trade.realized_pnl or 0.0) > 0 else 0
                delay = (
                    (execution_quote.quote_time - signal_time).total_seconds() / 60
                    if execution_quote.quote_time is not None
                    else None
                )
                spread_pct = _intraday_spread_fraction(execution_quote)
                evidence = _execution_cost_evidence(
                    side="sell",
                    signal_date=trade_date,
                    fill_date=trade_date,
                    signal_price=quote.latest_price,
                    reference_price=reference_price,
                    shares=trade.shares,
                    signal_to_fill_sessions=0,
                    deferred_reasons=(),
                    price_basis="observed_sell_bid",
                    execution_time=execution_quote.quote_time,
                    spread_pct=spread_pct,
                    spread_observed=spread_pct is not None,
                )
                trade.metadata.update(
                    {
                        **context,
                        "execution_model": "intraday_alert",
                        "signal_time": signal_time.isoformat(),
                        "signal_price": quote.latest_price,
                        "execution_time": execution_quote.quote_time.isoformat() if execution_quote.quote_time else None,
                        "execution_price": execution_price,
                        "execution_delay_minutes": round(delay, 2) if delay is not None else None,
                        "configured_execution_delay_minutes": execution_delay_minutes,
                        "quote_source": quote.source,
                        "execution_evidence": evidence,
                    }
                )
                total_base_slippage += float(evidence["base"]["slippage_cost"])
                total_stress_slippage += float(evidence["stress"]["slippage_cost"])
                total_observed_spread_cost += float(evidence.get("spread_cost") or 0.0)
                total_stress_incremental_cost += float(evidence["stress"]["total_cost"]) - float(
                    evidence["base"]["total_cost"]
                )
                first_trade_date = first_trade_date or trade_date
                trade_rows.append(
                    EtfPortfolioBacktestTrade(
                        run_id=run.id,
                        trade_date=trade.trade_date,
                        etf_code=trade.code,
                        etf_name=trade.name,
                        side=trade.side,
                        reason=trade.reason,
                        amount=trade.amount,
                        shares=trade.shares,
                        price=trade.price,
                        fee=trade.fee,
                        realized_pnl=trade.realized_pnl,
                        metadata_json=trade.metadata,
                    )
                )
                target_weights.pop(code, None)

            final_equity = _equity(cash, positions, last_intraday_prices)
            high_watermark = max(high_watermark, final_equity)
            drawdown = final_equity / high_watermark - 1.0 if high_watermark > 0 else 0.0
            stress_equity = max(final_equity - total_stress_incremental_cost, 0.0)
            stress_high_watermark = max(stress_high_watermark, stress_equity)
            stress_drawdown = stress_equity / stress_high_watermark - 1.0 if stress_high_watermark > 0 else 0.0
            stress_max_drawdown = min(stress_max_drawdown, stress_drawdown)
            session.add(
                EtfPortfolioBacktestEquityCurve(
                    run_id=run.id,
                    curve_date=trade_date,
                    equity=_round_money(final_equity),
                    cash=_round_money(cash),
                    drawdown=round(drawdown, 4),
                    benchmark_equity=_benchmark_equity(
                        benchmark,
                        series_by_code,
                        (benchmark_dates or trading_dates)[0],
                        signal_date,
                        capital,
                    ),
                    portfolio_mode=portfolio_mode,
                )
            )
            for code, position in positions.items():
                price = last_intraday_prices.get(code)
                if price is None:
                    continue
                market_value = position.shares * price
                session.add(
                    EtfPortfolioBacktestPosition(
                        run_id=run.id,
                        snapshot_date=trade_date,
                        etf_code=code,
                        etf_name=position.name,
                        shares=round(position.shares, 4),
                        price=price,
                        market_value=_round_money(market_value),
                        weight=round(market_value / final_equity, 4) if final_equity > 0 else 0.0,
                        cost_basis=round(position.avg_cost, 4),
                        unrealized_pnl=_round_money(position.shares * (price - position.avg_cost)),
                        metadata_json={
                            "entry_date": position.entry_date.isoformat(),
                            "max_profit_pct": round(position.max_profit_pct, 4),
                            "execution_model": "intraday_alert",
                        },
                    )
                )

            if len(trade_rows) % 40 == 0:
                await session.flush()

        session.add_all(trade_rows)
        final_equity = _equity(cash, positions, last_intraday_prices)
        curve_rows = (
            await session.scalars(
                select(EtfPortfolioBacktestEquityCurve).where(EtfPortfolioBacktestEquityCurve.run_id == run.id)
            )
        ).all()
        average_target_exposure = target_exposure_sum / len(trading_dates) if trading_dates else 0.0
        quote_count = sum(len(quotes) for by_date in intraday_by_code.values() for quotes in by_date.values())
        quote_times = [
            quote.quote_time
            for by_date in intraday_by_code.values()
            for quotes in by_date.values()
            for quote in quotes
            if quote.quote_time is not None
        ]
        base_max_drawdown = round(min((row.drawdown for row in curve_rows), default=0.0), 4)
        stress_final_equity = max(final_equity - total_stress_incremental_cost, 0.0)
        action_cycle_unavailable = {
            "status": "unavailable",
            "reason": "portfolio_replay_has_no_observed_action_cycle_counterfactual",
        }
        execution_scenarios = {
            "contract_version": EXECUTION_RISK_CONTRACT_VERSION,
            "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
            "simulated_not_observed": True,
            "spread_evidence": "observed_bid_ask",
            "base": {
                "provenance": "simulated_fill_base",
                "net_return": round(final_equity / capital - 1.0, 4),
                "max_drawdown": base_max_drawdown,
                "turnover": round(turnover / capital, 4),
                "fee": _round_money(total_fees),
                "slippage_cost": _round_money(total_base_slippage),
                "spread_cost": _round_money(total_observed_spread_cost),
                "total_cost": _round_money(total_fees + total_base_slippage),
                "action_cycle_benefit": action_cycle_unavailable,
            },
            "stress": {
                "provenance": "simulated_fill_stress",
                "same_action_path_sensitivity": True,
                "net_return": round(stress_final_equity / capital - 1.0, 4),
                "max_drawdown": round(stress_max_drawdown, 4),
                "turnover": round(turnover / capital, 4),
                "fee": _round_money(total_fees),
                "slippage_cost": _round_money(total_stress_slippage),
                "spread_cost": _round_money(total_observed_spread_cost),
                "total_cost": _round_money(total_fees + total_stress_slippage),
                "action_cycle_benefit": action_cycle_unavailable,
            },
        }
        metrics = {
            "execution_model": "intraday_alert",
            "cumulative_return": round(final_equity / capital - 1.0, 4),
            "max_drawdown": base_max_drawdown,
            "trade_count": len(trade_rows),
            "sell_count": realized_trades,
            "win_rate": round(winning_trades / realized_trades, 4) if realized_trades else None,
            "turnover": round(turnover / capital, 4),
            "total_fees": _round_money(total_fees),
            "execution_scenarios": execution_scenarios,
            "execution_excluded_counts": dict(execution_excluded_counts),
            "unfilled_alert_count": unfilled_alert_count,
            "average_target_exposure": round(average_target_exposure, 4),
            "last_target_exposure": round(last_target_exposure, 4),
            "partial_allocation_days": partial_allocation_days,
            "full_cash_days": full_cash_days,
            "portfolio_rule": "上一交易日 ETF 资金配置参考 + 次日盘中成交 + 盘中提醒执行",
            "portfolio_risk_budget": last_portfolio_context,
        }
        data_limitation_notes = [
            "盘中提醒回测只覆盖系统已保存的 ETF 盘中行情历史。",
            "只有显式 decision_eligible 且具备有效 ask/bid 的历史盘口可以成交；缺少后续合格行情时标记未成交。",
            "买入使用 ask、卖出使用 bid，并分别报告费用、观察价差、基础滑点和压力滑点，不使用 latest_price 或收盘价兜底。",
        ]
        data_coverage = {
            "requested_start_date": effective_start.isoformat(),
            "requested_end_date": effective_end.isoformat(),
            "lookback_start_date": lookback_start.isoformat(),
            "asset_count": len(metadata),
            "priced_asset_count": len(series_by_code),
            "intraday_asset_count": len(intraday_by_code),
            "intraday_quote_count": quote_count,
            "intraday_trade_days": len(trading_dates),
            "intraday_coverage": {
                "quote_count": quote_count,
                "trade_days": len(trading_dates),
                "first_quote_time": min(quote_times).isoformat() if quote_times else None,
                "latest_quote_time": max(quote_times).isoformat() if quote_times else None,
            },
            "start_date": trading_dates[0].isoformat(),
            "end_date": trading_dates[-1].isoformat(),
            "effective_start_date": trading_dates[0].isoformat(),
            "effective_end_date": trading_dates[-1].isoformat(),
            "latest_replay_asset_count": len(latest_assets),
            "first_signal_date": first_signal_date.isoformat() if first_signal_date else None,
            "first_trade_date": first_trade_date.isoformat() if first_trade_date else None,
            "cash_wait_reason_counts": dict(cash_wait_reason_counts),
            "portfolio_mode_counts": dict(portfolio_mode_counts),
            "partial_allocation_days": partial_allocation_days,
            "full_cash_days": full_cash_days,
            "average_target_exposure": round(average_target_exposure, 4),
            "last_target_exposure": round(last_target_exposure, 4),
            "execution_delay_minutes": execution_delay_minutes,
            "unfilled_alert_count": unfilled_alert_count,
            "execution_excluded_counts": dict(execution_excluded_counts),
            "portfolio_risk_budget": last_portfolio_context,
            "simulated_not_observed": True,
            "data_limitation_notes": data_limitation_notes,
        }
        caveats = [
            "这是盘中提醒执行回测，更接近邮件提醒后手动操作的链路，但仍不代表未来收益。",
            "回测不会写真实提醒表，不会发送邮件，也不会连接券商。",
            "盘中历史不足的日期不会回退为日线收盘模拟。",
            *data_limitation_notes,
        ]
        run.status = "success"
        run.finished_at = utcnow()
        run.metrics_json = metrics
        run.benchmark_json = benchmark
        run.data_coverage_json = data_coverage
        run.caveats_json = caveats
        await session.commit()
        await session.refresh(run)
        return run
    except Exception as exc:
        message = str(exc) or "ETF 盘中提醒执行回测失败，请检查盘中行情历史"
        return await mark_backtest_failed(session, run, message)


async def backtest_detail_payload(session: AsyncSession, run: EtfPortfolioBacktestRun) -> dict[str, Any]:
    curve = (
        await session.scalars(
            select(EtfPortfolioBacktestEquityCurve)
            .where(EtfPortfolioBacktestEquityCurve.run_id == run.id)
            .order_by(EtfPortfolioBacktestEquityCurve.curve_date.asc())
        )
    ).all()
    trades = (
        await session.scalars(
            select(EtfPortfolioBacktestTrade)
            .where(EtfPortfolioBacktestTrade.run_id == run.id)
            .order_by(EtfPortfolioBacktestTrade.trade_date.desc(), EtfPortfolioBacktestTrade.id.desc())
            .limit(80)
        )
    ).all()
    latest_date = await session.scalar(
        select(func.max(EtfPortfolioBacktestPosition.snapshot_date)).where(
            EtfPortfolioBacktestPosition.run_id == run.id
        )
    )
    positions: list[EtfPortfolioBacktestPosition] = []
    if latest_date is not None:
        positions = list(
            (
                await session.scalars(
                    select(EtfPortfolioBacktestPosition)
                    .where(
                        EtfPortfolioBacktestPosition.run_id == run.id,
                        EtfPortfolioBacktestPosition.snapshot_date == latest_date,
                    )
                    .order_by(EtfPortfolioBacktestPosition.weight.desc())
                )
            ).all()
        )
    labels = (
        await session.scalars(
            select(EtfPortfolioBacktestLabelSummary)
            .where(EtfPortfolioBacktestLabelSummary.run_id == run.id)
            .order_by(
                EtfPortfolioBacktestLabelSummary.label.asc(),
                EtfPortfolioBacktestLabelSummary.entry_timing_label.asc(),
                EtfPortfolioBacktestLabelSummary.horizon_days.asc(),
            )
        )
    ).all()
    return {
        **backtest_summary_payload(run),
        "equity_curve": [
            {
                "date": item.curve_date,
                "equity": item.equity,
                "cash": item.cash,
                "drawdown": item.drawdown,
                "benchmark_equity": item.benchmark_equity,
                "portfolio_mode": item.portfolio_mode,
            }
            for item in curve
        ],
        "trades": [
            {
                "id": item.id,
                "trade_date": item.trade_date,
                "etf_code": item.etf_code,
                "etf_name": item.etf_name,
                "side": item.side,
                "reason": item.reason,
                "amount": item.amount,
                "shares": item.shares,
                "price": item.price,
                "fee": item.fee,
                "realized_pnl": item.realized_pnl,
                "metadata": dict(item.metadata_json or {}),
            }
            for item in trades
        ],
        "latest_positions": [
            {
                "snapshot_date": item.snapshot_date,
                "etf_code": item.etf_code,
                "etf_name": item.etf_name,
                "shares": item.shares,
                "price": item.price,
                "market_value": item.market_value,
                "weight": item.weight,
                "cost_basis": item.cost_basis,
                "unrealized_pnl": item.unrealized_pnl,
                "metadata": dict(item.metadata_json or {}),
            }
            for item in positions
        ],
        "label_summaries": [
            {
                "label": item.label,
                "entry_timing_label": item.entry_timing_label,
                "horizon_days": item.horizon_days,
                "sample_count": item.sample_count,
                "avg_return": item.avg_return,
                "median_return": item.median_return,
                "win_rate": item.win_rate,
                "worst_forward_drawdown": item.worst_forward_drawdown,
                "confidence": item.confidence,
                "metrics": dict(item.metrics_json or {}),
            }
            for item in labels
        ],
    }


def backtest_uses_current_action_contract(run: EtfPortfolioBacktestRun) -> bool:
    replay_contract = dict((run.config_json or {}).get("replay_contract") or {})
    claimed_hash = replay_contract.pop("contract_hash", None)
    contract_hash_valid = bool(
        isinstance(claimed_hash, str)
        and claimed_hash
        and stable_contract_hash(replay_contract) == claimed_hash
    )
    return bool(
        contract_hash_valid
        and replay_contract.get("replay_contract_version") == REPLAY_CONTRACT_VERSION
        and replay_contract.get("action_lifecycle_version") == EXIT_ACTION_CONTRACT_VERSION
        and replay_contract.get("target_semantics") == CURRENT_ACTION_TARGET_SEMANTICS
        and replay_contract.get("action_event_source") == CURRENT_ACTION_EVENT_SOURCE
    )


def classify_backtest_evidence_status(run: EtfPortfolioBacktestRun) -> str:
    replay_contract = dict((run.config_json or {}).get("replay_contract") or {})
    if not replay_contract or not backtest_uses_current_action_contract(run):
        return EVIDENCE_STATUS_LEGACY
    if run.status != "success":
        return EVIDENCE_STATUS_WAITING
    return EVIDENCE_STATUS_SAME_CONTRACT


def _backtest_evidence_dimensions(
    run: EtfPortfolioBacktestRun,
    *,
    execution_model: str | None,
) -> dict[str, Any]:
    config = dict(run.config_json or {})
    metrics = dict(run.metrics_json or {})
    coverage = dict(run.data_coverage_json or {})
    is_intraday = execution_model == EXECUTION_MODEL_INTRADAY_ALERT
    is_next_open = execution_model == EXECUTION_MODEL_DAILY_ADJUSTED_OPEN
    current_action_contract = backtest_uses_current_action_contract(run)
    policy_semantics = (
        CURRENT_ACTION_TARGET_SEMANTICS
        if current_action_contract
        else LEGACY_ACTION_TARGET_SEMANTICS
    )

    action_metrics = metrics.get("action_evidence")
    if not isinstance(action_metrics, dict):
        action_metrics = {}
    notification_metrics = metrics.get("notification_evidence")
    if not isinstance(notification_metrics, dict):
        notification_metrics = {}

    def sample_count(source: dict[str, Any]) -> int | None:
        value = source.get("sample_count")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    action_samples = sample_count(action_metrics)
    notification_samples = sample_count(notification_metrics)
    quote_count = coverage.get("intraday_quote_count")
    has_intraday_quotes = (
        isinstance(quote_count, int)
        and not isinstance(quote_count, bool)
        and quote_count > 0
    )
    notes = coverage.get("data_limitation_notes")
    if not isinstance(notes, list) or not all(isinstance(item, str) for item in notes):
        notes = [
            "盘中模型只使用已保存的历史快照，不能证明完整盘口、SMTP 送达或用户真实执行。"
            if is_intraday
            else "日线模型不能验证盘中硬止损、bid/ask、IOPV 或邮件触达后的真实执行。"
        ]
    else:
        notes = list(notes)
    if not current_action_contract:
        notes.append("旧相对仓位动作语义仅供诊断，不可作为当前 v2 动作策略证据。")

    action_status = (
        "available" if action_samples else "not_computed"
    ) if current_action_contract else "legacy_diagnostic"
    notification_status = (
        "available" if notification_samples else "not_computed"
    ) if current_action_contract else "legacy_diagnostic"

    return {
        "action_evidence": {
            "scenario_label": "动作建议完全执行情景",
            "status": action_status,
            "sample_count": action_samples,
            "execution_provenance": "simulated_not_observed",
            "observed_user_execution": False,
            "policy_semantics": policy_semantics,
            "research_only": True,
            "promotion_eligible": False,
        },
        "notification_evidence": {
            "scenario_label": "仅 SMTP 已接受邮件被执行敏感性",
            "status": notification_status,
            "sample_count": notification_samples,
            "smtp_semantics": "accepted_not_delivered",
            "execution_provenance": "simulated_not_observed",
            "observed_user_execution": False,
        },
        "execution_evidence": {
            "model": execution_model,
            "label": (
                "盘中历史快照模拟"
                if is_intraday
                else "下一合格交易日复权开盘模拟"
                if is_next_open
                else "旧日线收盘模拟"
            ),
            "base_fill_field": config.get("base_fill_field")
            or (
                "stored_intraday_quote_after_fixed_delay"
                if is_intraday
                else "next_eligible_total_return_adjusted_open"
                if is_next_open
                else "legacy_daily_close"
            ),
            "execution_delay_minutes": config.get("execution_delay_minutes")
            if is_intraday
            else None,
            "execution_provenance": "simulated_not_observed",
            "observed_user_execution": False,
        },
        "coverage_evidence": {
            "asset_count": coverage.get("asset_count"),
            "priced_asset_count": coverage.get("priced_asset_count"),
            "intraday_asset_count": coverage.get("intraday_asset_count"),
            "trading_days": coverage.get("intraday_trade_days")
            if is_intraday
            else coverage.get("trading_days"),
            "intraday_quote_count": quote_count if is_intraday else None,
            "start_date": coverage.get("start_date"),
            "end_date": coverage.get("end_date"),
        },
        "time_resolution_limitations": {
            "resolution": "stored_intraday_snapshots" if is_intraday else "daily_bars",
            "intraday_trigger_replayed": is_intraday and has_intraday_quotes,
            "bid_ask_iopv_verified": False,
            "smtp_delivery_verified": False,
            "user_execution_verified": False,
            "notes": notes,
        },
    }


def backtest_summary_payload(run: EtfPortfolioBacktestRun) -> dict[str, Any]:
    replay_contract = dict((run.config_json or {}).get("replay_contract") or {})
    execution_model = replay_contract.get("execution_model") or (run.config_json or {}).get("execution")
    evidence_status = classify_backtest_evidence_status(run)
    current_action_contract = backtest_uses_current_action_contract(run)
    metrics = dict(run.metrics_json or {})
    action_metrics = metrics.get("action_evidence")
    action_sample_count = (
        action_metrics.get("sample_count")
        if isinstance(action_metrics, dict)
        and isinstance(action_metrics.get("sample_count"), int)
        and not isinstance(action_metrics.get("sample_count"), bool)
        and action_metrics.get("sample_count", -1) >= 0
        else 0
    )
    caveats = list(run.caveats_json or [])
    if not current_action_contract:
        caveats.append("该回测使用旧相对仓位动作语义，仅供诊断，不可证明当前 v2 动作策略。")
    evidence_summary = build_evidence_summary(
        current_contract=replay_contract if current_action_contract else None,
        validation_evidence={
            "contract_hash": replay_contract.get("contract_hash"),
            "sample_count": action_sample_count,
        }
        if current_action_contract
        else None,
        backtest_metrics=metrics,
        caveats=caveats,
    )
    evidence_dimensions = _backtest_evidence_dimensions(
        run,
        execution_model=str(execution_model) if execution_model else None,
    )
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "initial_cash": run.initial_cash,
        "fee_rate": run.fee_rate,
        "metrics": metrics,
        "benchmark": dict(run.benchmark_json or {}),
        "data_coverage": dict(run.data_coverage_json or {}),
        "caveats": caveats,
        "execution_model": str(execution_model) if execution_model else None,
        "replay_contract": replay_contract,
        "evidence_status": evidence_status,
        "evidence_summary": evidence_summary,
        "research_only": True,
        "promotion_eligible": False,
        **evidence_dimensions,
        "error_message": run.error_message,
    }
