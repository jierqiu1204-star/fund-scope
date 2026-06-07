from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Fund,
    FundNavHistory,
    IndexValuationHistory,
    PaperPortfolio,
    StrategyDefinition,
    StrategyEquityCurve,
    StrategyOrder,
    StrategyPosition,
    StrategyRun,
    Transaction,
    utcnow,
)
from app.services.recommendations.constants import ASSET_TYPE_FUND
from app.services.recommendations.engine import generate_recommendations

RUN_STATUS_FAILED = "failed"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
STRATEGY_ACTIVE = "active"
STRATEGY_TYPE_DCA = "dca_baseline"
STRATEGY_TYPE_MOMENTUM = "momentum_rotation"
STRATEGY_TYPE_SCREENING = "screening"
RUN_TYPE_BACKTEST = "backtest"
RUN_TYPE_SCREENING = "screening"
RUN_TYPE_SIMULATION = "simulation_update"
PLATFORM_GENERIC = "generic"
PLATFORM_ALIPAY = "alipay"


ALIPAY_EXECUTION_MODEL: dict[str, Any] = {
    "cutoff_time": "15:00",
    "pricing": "same_nav_session_before_cutoff",
    "confirmation_lag_sessions": 1,
    "redemption_cash_lag_sessions": 1,
    "account_sync": "manual_import_only",
}


DEFAULT_CONFIGS: dict[str, dict[str, Any]] = {
    STRATEGY_TYPE_MOMENTUM: {
        "lookback_days": 60,
        "rebalance_frequency": "monthly",
        "top_n": 3,
        "max_pe_percentile": 80,
        "initial_cash": 100000,
        "fee_rate": 0.001,
    },
    STRATEGY_TYPE_DCA: {
        "monthly_amount": 1000,
        "day_of_month": 1,
        "target_asset_codes": [],
        "fee_rate": 0.001,
    },
    STRATEGY_TYPE_SCREENING: {},
}


@dataclass(frozen=True)
class SimulatedOrder:
    submitted_date: date | None
    trade_date: date
    confirmed_date: date | None
    asset_code: str
    side: str
    amount: float
    shares: float
    price: float
    fee: float
    status: str
    platform: str


@dataclass(frozen=True)
class SimulatedPosition:
    snapshot_date: date
    asset_code: str
    shares: float
    market_value: float
    weight: float


@dataclass(frozen=True)
class SimulatedEquityPoint:
    curve_date: date
    equity: float
    cash: float
    drawdown: float


@dataclass(frozen=True)
class SimulationResult:
    metrics: dict[str, Any]
    orders: list[SimulatedOrder]
    positions: list[SimulatedPosition]
    equity_curve: list[SimulatedEquityPoint]


def default_config(strategy_type: str, config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_CONFIGS.get(strategy_type, {}))
    merged.update(config)
    return merged


def _platform_profile(config: dict[str, Any]) -> str:
    platform = str(config.get("platform_profile") or config.get("platform") or PLATFORM_GENERIC).lower()
    return PLATFORM_ALIPAY if platform == PLATFORM_ALIPAY else PLATFORM_GENERIC


def _execution_model(platform_profile: str) -> dict[str, Any]:
    if platform_profile == PLATFORM_ALIPAY:
        return dict(ALIPAY_EXECUTION_MODEL)
    return {
        "pricing": "same_nav_session",
        "confirmation_lag_sessions": 0,
        "account_sync": "none",
    }


async def create_strategy(
    session: AsyncSession,
    *,
    name: str,
    strategy_type: str,
    asset_type: str,
    config: dict[str, Any],
) -> StrategyDefinition:
    if asset_type != ASSET_TYPE_FUND:
        raise ValueError("Strategy lab v1 only supports fund assets")
    if strategy_type not in DEFAULT_CONFIGS:
        raise ValueError(f"Unsupported strategy type: {strategy_type}")
    strategy = StrategyDefinition(
        name=name,
        strategy_type=strategy_type,
        asset_type=asset_type,
        status=STRATEGY_ACTIVE,
        config_json=default_config(strategy_type, config),
    )
    session.add(strategy)
    await session.commit()
    await session.refresh(strategy)
    return strategy


async def list_strategies(session: AsyncSession) -> list[StrategyDefinition]:
    rows = await session.scalars(
        select(StrategyDefinition).order_by(StrategyDefinition.created_at.desc(), StrategyDefinition.id.desc())
    )
    return list(rows.all())


async def get_strategy(session: AsyncSession, strategy_id: int) -> StrategyDefinition | None:
    return cast(
        StrategyDefinition | None,
        await session.scalar(select(StrategyDefinition).where(StrategyDefinition.id == strategy_id)),
    )


async def _fund_codes(session: AsyncSession, config: dict[str, Any]) -> list[str]:
    configured = config.get("asset_codes") or config.get("target_asset_codes")
    if configured:
        return [str(code) for code in configured]
    rows = await session.scalars(select(Fund.code).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc()))
    return list(rows.all())


async def _nav_map(
    session: AsyncSession,
    asset_codes: Iterable[str],
    start_date: date,
    end_date: date,
) -> dict[str, dict[date, float]]:
    rows = (
        await session.scalars(
            select(FundNavHistory)
            .where(
                FundNavHistory.fund_code.in_(list(asset_codes)),
                FundNavHistory.nav_date >= start_date,
                FundNavHistory.nav_date <= end_date,
            )
            .order_by(FundNavHistory.nav_date.asc())
        )
    ).all()
    navs: dict[str, dict[date, float]] = {}
    for row in rows:
        navs.setdefault(row.fund_code, {})[row.nav_date] = float(row.nav)
    return navs


def _latest_price(prices: dict[date, float], on_or_before: date) -> tuple[date, float] | None:
    eligible = [price_date for price_date in prices if price_date <= on_or_before]
    if not eligible:
        return None
    price_date = max(eligible)
    return price_date, prices[price_date]


def _shared_dates(navs: dict[str, dict[date, float]], start_date: date, end_date: date) -> list[date]:
    dates = sorted({nav_date for prices in navs.values() for nav_date in prices})
    return [nav_date for nav_date in dates if start_date <= nav_date <= end_date]


def _next_available_session(dates: list[date], current: date, lag_sessions: int) -> date:
    if lag_sessions <= 0:
        return current
    future_dates = [item for item in dates if item > current]
    if len(future_dates) >= lag_sessions:
        return future_dates[lag_sessions - 1]
    return current


def _order_confirmation_date(dates: list[date], current: date, platform_profile: str) -> date:
    if platform_profile == PLATFORM_ALIPAY:
        return _next_available_session(dates, current, ALIPAY_EXECUTION_MODEL["confirmation_lag_sessions"])
    return current


def _simulated_order(
    *,
    dates: list[date],
    platform_profile: str,
    current: date,
    asset_code: str,
    side: str,
    amount: float,
    shares: float,
    price: float,
    fee: float,
) -> SimulatedOrder:
    return SimulatedOrder(
        submitted_date=current,
        trade_date=current,
        confirmed_date=_order_confirmation_date(dates, current, platform_profile),
        asset_code=asset_code,
        side=side,
        amount=round(amount, 2),
        shares=round(shares, 6),
        price=price,
        fee=round(fee, 2),
        status="confirmed",
        platform=platform_profile,
    )


def _is_monthly_rebalance(current: date, last_rebalance: date | None) -> bool:
    return last_rebalance is None or current.month != last_rebalance.month or current.year != last_rebalance.year


async def _passes_valuation_filter(
    session: AsyncSession,
    asset_code: str,
    current: date,
    max_pe_percentile: float,
) -> bool:
    fund = await session.scalar(select(Fund).where(Fund.code == asset_code))
    if fund is None or fund.tracking_index_code is None:
        return True
    row = await session.scalar(
        select(IndexValuationHistory)
        .where(
            IndexValuationHistory.index_code == fund.tracking_index_code,
            IndexValuationHistory.valuation_date <= current,
        )
        .order_by(IndexValuationHistory.valuation_date.desc())
    )
    if row is None or row.pe_percentile is None:
        return True
    return float(row.pe_percentile) <= max_pe_percentile


async def _momentum_targets(
    session: AsyncSession,
    navs: dict[str, dict[date, float]],
    current: date,
    config: dict[str, Any],
) -> list[str]:
    lookback_days = int(config["lookback_days"])
    max_pe_percentile = float(config["max_pe_percentile"])
    momentum: list[tuple[str, float]] = []
    for asset_code, prices in navs.items():
        latest = _latest_price(prices, current)
        previous = _latest_price(prices, current - timedelta(days=lookback_days))
        if latest is None or previous is None or previous[1] <= 0:
            continue
        if not await _passes_valuation_filter(session, asset_code, current, max_pe_percentile):
            continue
        momentum.append((asset_code, latest[1] / previous[1] - 1.0))
    momentum.sort(key=lambda item: (-item[1], item[0]))
    return [asset_code for asset_code, _ in momentum[: int(config["top_n"])]]


def _equity(cash: float, holdings: dict[str, float], navs: dict[str, dict[date, float]], current: date) -> float:
    total = cash
    for asset_code, shares in holdings.items():
        latest = _latest_price(navs.get(asset_code, {}), current)
        if latest is not None:
            total += shares * latest[1]
    return total


def _append_snapshot(
    *,
    positions: list[SimulatedPosition],
    equity_curve: list[SimulatedEquityPoint],
    navs: dict[str, dict[date, float]],
    holdings: dict[str, float],
    current: date,
    cash: float,
    peak: float,
) -> float:
    equity = _equity(cash, holdings, navs, current)
    next_peak = max(peak, equity)
    drawdown = 0.0 if next_peak <= 0 else equity / next_peak - 1.0
    equity_curve.append(
        SimulatedEquityPoint(
            curve_date=current,
            equity=round(equity, 2),
            cash=round(cash, 2),
            drawdown=round(drawdown, 6),
        )
    )
    for asset_code, shares in holdings.items():
        latest = _latest_price(navs.get(asset_code, {}), current)
        if latest is None or shares <= 0:
            continue
        market_value = shares * latest[1]
        positions.append(
            SimulatedPosition(
                snapshot_date=current,
                asset_code=asset_code,
                shares=round(shares, 6),
                market_value=round(market_value, 2),
                weight=round(0.0 if equity <= 0 else market_value / equity, 6),
            )
        )
    return next_peak


def _daily_returns(equity_curve: list[SimulatedEquityPoint]) -> list[float]:
    returns: list[float] = []
    for index in range(1, len(equity_curve)):
        previous = equity_curve[index - 1].equity
        current = equity_curve[index].equity
        if previous > 0:
            returns.append(current / previous - 1.0)
    return returns


def _metrics(starting_equity: float, equity_curve: list[SimulatedEquityPoint], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ending_equity = equity_curve[-1].equity if equity_curve else starting_equity
    returns = _daily_returns(equity_curve)
    average = sum(returns) / len(returns) if returns else 0.0
    variance = sum((item - average) ** 2 for item in returns) / len(returns) if returns else 0.0
    metrics = {
        "starting_equity": round(starting_equity, 2),
        "ending_equity": round(ending_equity, 2),
        "total_return": round(ending_equity / starting_equity - 1.0, 6) if starting_equity > 0 else 0.0,
        "max_drawdown": min((point.drawdown for point in equity_curve), default=0.0),
        "annualized_volatility": round(sqrt(variance) * sqrt(252), 6) if returns else 0.0,
    }
    if extra:
        metrics.update(extra)
    return metrics


async def _simulate_momentum(
    session: AsyncSession,
    config: dict[str, Any],
    start_date: date,
    end_date: date,
) -> SimulationResult:
    asset_codes = await _fund_codes(session, config)
    navs = await _nav_map(
        session,
        asset_codes,
        start_date - timedelta(days=int(config["lookback_days"]) + 7),
        end_date,
    )
    dates = _shared_dates(navs, start_date, end_date)
    cash = float(config["initial_cash"])
    starting_cash = cash
    fee_rate = float(config["fee_rate"])
    platform_profile = _platform_profile(config)
    holdings: dict[str, float] = {}
    orders: list[SimulatedOrder] = []
    positions: list[SimulatedPosition] = []
    equity_curve: list[SimulatedEquityPoint] = []
    last_rebalance: date | None = None
    peak = cash

    for current in dates:
        if _is_monthly_rebalance(current, last_rebalance):
            targets = await _momentum_targets(session, navs, current, config)
            current_equity = _equity(cash, holdings, navs, current)
            for asset_code, shares in list(holdings.items()):
                if asset_code in targets or shares <= 0:
                    continue
                latest = _latest_price(navs.get(asset_code, {}), current)
                if latest is None:
                    continue
                price = latest[1]
                amount = shares * price
                fee = amount * fee_rate
                cash += amount - fee
                holdings[asset_code] = 0.0
                orders.append(
                    _simulated_order(
                        dates=dates,
                        platform_profile=platform_profile,
                        current=current,
                        asset_code=asset_code,
                        side="sell",
                        amount=amount,
                        shares=shares,
                        price=price,
                        fee=fee,
                    )
                )
            if targets:
                target_value = current_equity / len(targets)
                for asset_code in targets:
                    latest = _latest_price(navs.get(asset_code, {}), current)
                    if latest is None:
                        continue
                    price = latest[1]
                    current_value = holdings.get(asset_code, 0.0) * price
                    buy_amount = max(target_value - current_value, 0.0)
                    if buy_amount <= 0 or cash <= 0:
                        continue
                    buy_amount = min(buy_amount, cash)
                    fee = buy_amount * fee_rate
                    shares = (buy_amount - fee) / price
                    cash -= buy_amount
                    holdings[asset_code] = holdings.get(asset_code, 0.0) + shares
                    orders.append(
                        _simulated_order(
                            dates=dates,
                            platform_profile=platform_profile,
                            current=current,
                            asset_code=asset_code,
                            side="buy",
                            amount=buy_amount,
                            shares=shares,
                            price=price,
                            fee=fee,
                        )
                    )
            last_rebalance = current
        peak = _append_snapshot(
            positions=positions,
            equity_curve=equity_curve,
            navs=navs,
            holdings=holdings,
            current=current,
            cash=cash,
            peak=peak,
        )

    return SimulationResult(
        _metrics(
            starting_cash,
            equity_curve,
            {"platform_profile": platform_profile, "execution_model": _execution_model(platform_profile)},
        ),
        orders,
        positions,
        equity_curve,
    )


async def _simulate_dca(
    session: AsyncSession,
    config: dict[str, Any],
    start_date: date,
    end_date: date,
) -> SimulationResult:
    asset_codes = await _fund_codes(session, config)
    navs = await _nav_map(session, asset_codes, start_date, end_date)
    dates = _shared_dates(navs, start_date, end_date)
    fee_rate = float(config["fee_rate"])
    monthly_amount = float(config["monthly_amount"])
    day_of_month = int(config["day_of_month"])
    platform_profile = _platform_profile(config)
    cash = 0.0
    contributions = 0.0
    holdings: dict[str, float] = {}
    orders: list[SimulatedOrder] = []
    positions: list[SimulatedPosition] = []
    equity_curve: list[SimulatedEquityPoint] = []
    bought_months: set[tuple[int, int]] = set()
    peak = 0.0

    for current in dates:
        month_key = (current.year, current.month)
        if current.day >= day_of_month and month_key not in bought_months and asset_codes:
            bought_months.add(month_key)
            cash += monthly_amount
            contributions += monthly_amount
            priced_assets = [
                (asset_code, latest[1])
                for asset_code in asset_codes
                if (latest := _latest_price(navs.get(asset_code, {}), current)) is not None
            ]
            amount_per_asset = monthly_amount / len(priced_assets) if priced_assets else 0.0
            for asset_code, price in priced_assets:
                fee = amount_per_asset * fee_rate
                shares = (amount_per_asset - fee) / price
                cash -= amount_per_asset
                holdings[asset_code] = holdings.get(asset_code, 0.0) + shares
                orders.append(
                    _simulated_order(
                        dates=dates,
                        platform_profile=platform_profile,
                        current=current,
                        asset_code=asset_code,
                        side="buy",
                        amount=amount_per_asset,
                        shares=shares,
                        price=price,
                        fee=fee,
                    )
                )
        peak = _append_snapshot(
            positions=positions,
            equity_curve=equity_curve,
            navs=navs,
            holdings=holdings,
            current=current,
            cash=cash,
            peak=peak,
        )

    return SimulationResult(
        _metrics(
            max(contributions, 1.0),
            equity_curve,
            {
                "contributions": round(contributions, 2),
                "platform_profile": platform_profile,
                "execution_model": _execution_model(platform_profile),
            },
        ),
        orders,
        positions,
        equity_curve,
    )


async def _simulate(
    session: AsyncSession,
    strategy: StrategyDefinition,
    start_date: date,
    end_date: date,
) -> SimulationResult:
    if start_date > end_date:
        raise ValueError("start_date must be before or equal to end_date")
    config = strategy.config_json
    if strategy.strategy_type == STRATEGY_TYPE_MOMENTUM:
        return await _simulate_momentum(session, config, start_date, end_date)
    if strategy.strategy_type == STRATEGY_TYPE_DCA:
        return await _simulate_dca(session, config, start_date, end_date)
    raise ValueError(f"Strategy type does not support portfolio simulation: {strategy.strategy_type}")


async def simulate_strategy_config(
    session: AsyncSession,
    *,
    strategy_type: str,
    asset_type: str,
    config: dict[str, Any],
    start_date: date,
    end_date: date,
) -> SimulationResult:
    if asset_type != ASSET_TYPE_FUND:
        raise ValueError("Strategy lab v1 only supports fund assets")
    if strategy_type not in DEFAULT_CONFIGS:
        raise ValueError(f"Unsupported strategy type: {strategy_type}")
    strategy = StrategyDefinition(
        name="strategy evaluation",
        strategy_type=strategy_type,
        asset_type=asset_type,
        status=STRATEGY_ACTIVE,
        config_json=default_config(strategy_type, config),
    )
    return await _simulate(session, strategy, start_date, end_date)


async def _persist_simulation(
    session: AsyncSession,
    strategy: StrategyDefinition,
    *,
    run_type: str,
    start_date: date,
    end_date: date,
    simulation: SimulationResult,
) -> StrategyRun:
    run = StrategyRun(
        strategy_id=strategy.id,
        run_type=run_type,
        status=RUN_STATUS_SUCCESS,
        started_at=utcnow(),
        finished_at=utcnow(),
        as_of_date=end_date,
        date_range_json={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        metrics_json=simulation.metrics,
    )
    session.add(run)
    await session.flush()
    session.add_all(
        [
            StrategyOrder(
                run_id=run.id,
                submitted_date=order.submitted_date,
                trade_date=order.trade_date,
                confirmed_date=order.confirmed_date,
                asset_code=order.asset_code,
                side=order.side,
                amount=order.amount,
                shares=order.shares,
                price=order.price,
                fee=order.fee,
                status=order.status,
                platform=order.platform,
            )
            for order in simulation.orders
        ]
    )
    session.add_all(
        [
            StrategyPosition(
                run_id=run.id,
                snapshot_date=position.snapshot_date,
                asset_code=position.asset_code,
                shares=position.shares,
                market_value=position.market_value,
                weight=position.weight,
            )
            for position in simulation.positions
        ]
    )
    session.add_all(
        [
            StrategyEquityCurve(
                run_id=run.id,
                curve_date=point.curve_date,
                equity=point.equity,
                cash=point.cash,
                drawdown=point.drawdown,
            )
            for point in simulation.equity_curve
        ]
    )
    await session.commit()
    await session.refresh(run)
    return run


async def run_backtest(
    session: AsyncSession,
    strategy: StrategyDefinition,
    *,
    start_date: date,
    end_date: date,
) -> StrategyRun:
    if strategy.strategy_type == STRATEGY_TYPE_SCREENING:
        recommendation_run = await generate_recommendations(session, ASSET_TYPE_FUND, end_date)
        run = StrategyRun(
            strategy_id=strategy.id,
            run_type=RUN_TYPE_SCREENING,
            status=RUN_STATUS_SUCCESS,
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=end_date,
            date_range_json={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            metrics_json={"recommendation_run_id": recommendation_run.id, "asset_type": ASSET_TYPE_FUND},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run

    simulation = await _simulate(session, strategy, start_date, end_date)
    return await _persist_simulation(
        session,
        strategy,
        run_type=RUN_TYPE_BACKTEST,
        start_date=start_date,
        end_date=end_date,
        simulation=simulation,
    )


async def get_run(session: AsyncSession, run_id: int) -> StrategyRun | None:
    return cast(StrategyRun | None, await session.scalar(select(StrategyRun).where(StrategyRun.id == run_id)))


async def start_paper_portfolio(
    session: AsyncSession,
    strategy: StrategyDefinition,
    *,
    name: str,
    started_at: date,
) -> PaperPortfolio:
    cash = float(strategy.config_json.get("initial_cash", 0.0))
    paper = PaperPortfolio(
        strategy_id=strategy.id,
        name=name,
        status=STRATEGY_ACTIVE,
        started_at=started_at,
        cash=cash,
        latest_equity=cash,
    )
    session.add(paper)
    await session.commit()
    await session.refresh(paper)
    return paper


async def get_paper_portfolio(session: AsyncSession, paper_id: int) -> PaperPortfolio | None:
    return cast(
        PaperPortfolio | None,
        await session.scalar(select(PaperPortfolio).where(PaperPortfolio.id == paper_id)),
    )


async def run_paper_update(
    session: AsyncSession,
    paper: PaperPortfolio,
    *,
    as_of_date: date,
) -> StrategyRun:
    strategy = await get_strategy(session, paper.strategy_id)
    if strategy is None:
        raise ValueError("Paper portfolio strategy is missing")
    simulation = await _simulate(session, strategy, paper.started_at, as_of_date)
    run = await _persist_simulation(
        session,
        strategy,
        run_type=RUN_TYPE_SIMULATION,
        start_date=paper.started_at,
        end_date=as_of_date,
        simulation=simulation,
    )
    paper.cash = simulation.equity_curve[-1].cash if simulation.equity_curve else paper.cash
    paper.latest_equity = simulation.metrics["ending_equity"]
    paper.updated_at = utcnow()
    await session.commit()
    await session.refresh(paper)
    return run


def _aggregate_actual_shares(transactions: Iterable[Transaction]) -> dict[str, float]:
    shares_by_code: dict[str, float] = {}
    for transaction in sorted(transactions, key=lambda item: (item.traded_at, item.id)):
        current = shares_by_code.get(transaction.fund_code, 0.0)
        if transaction.action == "buy":
            shares_by_code[transaction.fund_code] = current + transaction.shares
        else:
            shares_by_code[transaction.fund_code] = current - transaction.shares
    return {asset_code: shares for asset_code, shares in shares_by_code.items() if abs(shares) > 1e-9}


async def _latest_nav(session: AsyncSession, asset_code: str, as_of_date: date) -> float:
    row = await session.scalar(
        select(FundNavHistory)
        .where(FundNavHistory.fund_code == asset_code, FundNavHistory.nav_date <= as_of_date)
        .order_by(FundNavHistory.nav_date.desc())
    )
    return 0.0 if row is None else float(row.nav)


async def reconcile_paper_portfolio(session: AsyncSession, paper: PaperPortfolio) -> dict[str, Any]:
    strategy = await get_strategy(session, paper.strategy_id)
    latest_run = await latest_run_for_paper(session, paper)
    if strategy is None:
        raise ValueError("Paper portfolio strategy is missing")
    if latest_run is None:
        raise ValueError("Paper portfolio has no simulation run")

    snapshot_date = await session.scalar(
        select(func.max(StrategyPosition.snapshot_date)).where(StrategyPosition.run_id == latest_run.id)
    )
    if snapshot_date is None:
        raise ValueError("Paper portfolio has no simulated positions")

    paper_positions = (
        await session.scalars(
            select(StrategyPosition)
            .where(
                StrategyPosition.run_id == latest_run.id,
                StrategyPosition.snapshot_date == snapshot_date,
            )
            .order_by(StrategyPosition.asset_code.asc())
        )
    ).all()
    paper_by_code = {position.asset_code: position for position in paper_positions}
    transactions = (await session.scalars(select(Transaction).order_by(Transaction.traded_at.asc(), Transaction.id.asc()))).all()
    actual_shares_by_code = _aggregate_actual_shares(transactions)
    asset_codes = sorted(set(paper_by_code) | set(actual_shares_by_code))

    items: list[dict[str, float | str]] = []
    actual_equity = 0.0
    paper_market_total = 0.0
    for asset_code in asset_codes:
        paper_position = paper_by_code.get(asset_code)
        paper_shares = 0.0 if paper_position is None else float(paper_position.shares)
        paper_market_value = 0.0 if paper_position is None else float(paper_position.market_value)
        actual_shares = actual_shares_by_code.get(asset_code, 0.0)
        nav = await _latest_nav(session, asset_code, snapshot_date)
        actual_market_value = actual_shares * nav
        actual_equity += actual_market_value
        paper_market_total += paper_market_value
        items.append(
            {
                "asset_code": asset_code,
                "paper_shares": round(paper_shares, 6),
                "actual_shares": round(actual_shares, 6),
                "share_diff": round(paper_shares - actual_shares, 6),
                "paper_market_value": round(paper_market_value, 2),
                "actual_market_value": round(actual_market_value, 2),
                "market_value_diff": round(paper_market_value - actual_market_value, 2),
            }
        )

    paper_equity = float(latest_run.metrics_json.get("ending_equity", paper.latest_equity))
    if paper_equity <= 0 and paper_market_total > 0:
        paper_equity = paper_market_total
    return {
        "paper_id": paper.id,
        "strategy_id": paper.strategy_id,
        "as_of_date": snapshot_date,
        "platform_profile": _platform_profile(strategy.config_json),
        "paper_equity": round(paper_equity, 2),
        "actual_equity": round(actual_equity, 2),
        "equity_diff": round(paper_equity - actual_equity, 2),
        "items": items,
        "note": "Actual side is built from manually imported transactions only; Alipay accounts are not connected.",
    }


async def latest_run_for_strategy(session: AsyncSession, strategy_id: int) -> StrategyRun | None:
    return cast(
        StrategyRun | None,
        await session.scalar(
            select(StrategyRun)
            .where(StrategyRun.strategy_id == strategy_id)
            .order_by(StrategyRun.started_at.desc(), StrategyRun.id.desc())
        ),
    )


async def latest_run_for_paper(session: AsyncSession, paper: PaperPortfolio) -> StrategyRun | None:
    rows = (
        await session.scalars(
            select(StrategyRun)
            .where(
                StrategyRun.strategy_id == paper.strategy_id,
                StrategyRun.run_type == RUN_TYPE_SIMULATION,
            )
            .order_by(StrategyRun.started_at.desc(), StrategyRun.id.desc())
        )
    ).all()
    paper_start = paper.started_at.isoformat()
    for run in rows:
        if run.date_range_json.get("start_date") == paper_start:
            return run
    return None
