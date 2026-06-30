from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from statistics import mean, median, pstdev
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import (
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
    EVIDENCE_STATUS_LEGACY,
    EVIDENCE_STATUS_SAME_CONTRACT,
    EVIDENCE_STATUS_WAITING,
    EXECUTION_MODEL_DAILY_CLOSE,
    FEE_MODEL_SIMPLE_RATE,
    build_evidence_summary,
    build_replay_contract,
)
from app.services.portfolio_allocation import (
    PORTFOLIO_LAYER_DEFENSIVE,
    PORTFOLIO_LAYER_PRIMARY,
    PORTFOLIO_LAYER_SATELLITE,
    PORTFOLIO_LAYER_WATCH_ONLY,
    PORTFOLIO_MODE_CASH_WAIT,
    PORTFOLIO_MODE_DEFENSIVE,
    PORTFOLIO_MODE_NEUTRAL,
    PORTFOLIO_MODE_RISK_ON,
    PORTFOLIO_SATELLITE_EXPOSURE_CAP,
    PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
    PORTFOLIO_SINGLE_WEIGHT_CAP,
)
from app.services.risk_alerts import (
    ALERT_EXIT_WATCH,
    ALERT_HARD_STOP,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_TREND_WEAKENING,
    ETF_TRAILING_GIVEBACK_MAX_PCT,
    ETF_TRAILING_GIVEBACK_MIN_PCT,
    ETF_TRAILING_GIVEBACK_VOL_MULTIPLIER,
    ETF_TRAILING_PROFIT_START_MAX_PCT,
    ETF_TRAILING_PROFIT_START_MIN_PCT,
    ETF_TRAILING_PROFIT_START_VOL_MULTIPLIER,
    HARD_STOP_LOSS_PCT,
)
from app.services.short_research.service import (
    CONCLUSION_INSUFFICIENT,
    CONCLUSION_REJECT,
    ComputedAsset,
    PricePoint,
    _cap_normalized_weights_by_caps,
    _metadata_from_etf_row,
    _portfolio_candidate_group,
    _portfolio_defensive_priority,
    _portfolio_defensive_reason,
    _portfolio_layer_cap,
    _portfolio_raw_weight,
    compute_asset_for_replay_from_series,
)

BACKTEST_RULE_VERSION = "etf_portfolio_backtest_v1"
BACKTEST_RANKING_VERSION = "short_research_daily_replay_v1"
BACKTEST_ALLOCATION_VERSION = "etf_portfolio_allocation_v3_layered"
BACKTEST_EXIT_RULE_VERSION = "risk_alerts_daily_v1"
STRATEGY_COMPARISON_RULE_VERSION = "etf_strategy_comparison_v1"
DEFAULT_BACKTEST_DAYS = 180
DEFAULT_BACKTEST_FEE_RATE = 0.001
DEFAULT_BACKTEST_INITIAL_CASH = 10000.0
MAX_BACKTEST_HOLDINGS = 6
MIN_WEIGHTABLE_HOLDINGS = 4
LABEL_HORIZONS = (1, 3, 5, 10)
BENCHMARK_CODES = ("510300", "159919", "510500", "512880", "588000")


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


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_money(value: float) -> float:
    return round(float(value), 2)


async def _create_backtest_run(
    session: AsyncSession,
    *,
    user_id: int | None,
    start_date: date,
    end_date: date,
    initial_cash: float,
    fee_rate: float,
    max_assets: int,
) -> EtfPortfolioBacktestRun:
    run = EtfPortfolioBacktestRun(
        user_id=user_id,
        status="running",
        start_date=start_date,
        end_date=end_date,
        asset_type=ASSET_TYPE_ETF,
        rule_version=BACKTEST_RULE_VERSION,
        ranking_version=BACKTEST_RANKING_VERSION,
        allocation_version=BACKTEST_ALLOCATION_VERSION,
        exit_rule_version=BACKTEST_EXIT_RULE_VERSION,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        config_json={
            "max_assets": max_assets,
            "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
            "min_holdings_for_full_exposure": MIN_WEIGHTABLE_HOLDINGS,
            "partial_allocation_allowed": True,
            "execution": "daily_close",
            "no_intraday_fill": True,
        },
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
        execution_model=EXECUTION_MODEL_DAILY_CLOSE,
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
    run.status = "failed"
    run.finished_at = utcnow()
    run.error_message = message
    await session.commit()
    await session.refresh(run)
    return run


async def latest_completed_backtest_run(session: AsyncSession) -> EtfPortfolioBacktestRun | None:
    return await session.scalar(
        select(EtfPortfolioBacktestRun)
        .where(
            EtfPortfolioBacktestRun.asset_type == ASSET_TYPE_ETF,
            EtfPortfolioBacktestRun.status == "success",
        )
        .order_by(EtfPortfolioBacktestRun.finished_at.desc(), EtfPortfolioBacktestRun.id.desc())
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
        if row.close <= 0:
            continue
        series[row.etf_code].append(
            PricePoint(
                point_date=row.trade_date,
                value=row.close,
                close=row.close,
                turnover=row.turnover,
                pct_change=row.pct_change / 100,
            )
        )
    return dict(series)


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


def _generate_target_weights(assets: list[ComputedAsset]) -> tuple[dict[str, float], str, dict[str, Any]]:
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

    raw_rows = [_portfolio_raw_weight(asset) for asset in selected]
    layer_caps = [_portfolio_layer_cap(item_types.get(asset.metadata.code, PORTFOLIO_LAYER_PRIMARY)) for asset in selected]
    target_exposure = min(1.0, sum(layer_caps))
    normalized = _cap_normalized_weights_by_caps(
        [row[0] for row in raw_rows],
        layer_caps,
        target_total=target_exposure,
    )
    if normalized is None:
        return (
            {},
            PORTFOLIO_MODE_CASH_WAIT,
            {
                "cash_reason": "单只 30% 上限和候选数量约束不可行，按现金等待处理。",
                "primary_count": len(primary),
                "satellite_count": len(satellite),
                "defensive_count": len(defensive),
                "target_exposure": 0.0,
                "cash_weight": 1.0,
            },
        )
    weights = {asset.metadata.code: weight for asset, weight in zip(selected, normalized, strict=True)}
    has_primary = any(item_types[code] == PORTFOLIO_LAYER_PRIMARY for code in weights)
    has_satellite = any(item_types[code] == PORTFOLIO_LAYER_SATELLITE for code in weights)
    has_defensive = any(item_types[code] == PORTFOLIO_LAYER_DEFENSIVE for code in weights)
    if has_defensive and not has_primary and not has_satellite:
        mode = PORTFOLIO_MODE_DEFENSIVE
    elif has_satellite or has_defensive:
        mode = PORTFOLIO_MODE_NEUTRAL
    else:
        mode = PORTFOLIO_MODE_RISK_ON
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
            "layer_caps": {asset.metadata.code: cap for asset, cap in zip(selected, layer_caps, strict=True)},
            "satellite_single_weight_cap": PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
            "satellite_exposure_cap": PORTFOLIO_SATELLITE_EXPOSURE_CAP,
            "target_exposure": round(sum(weights.values()), 4),
            "cash_weight": round(max(0.0, 1.0 - sum(weights.values())), 4),
            "cash_reason": "合格候选不足以用满资金，剩余现金等待。" if sum(weights.values()) < 0.999 else None,
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


def _risk_action(position: ReplayPosition, asset: ComputedAsset, price: float) -> tuple[str | None, float, dict[str, Any]]:
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
    context = {
        "profit_pct": round(profit_pct, 4),
        "max_profit_pct": round(position.max_profit_pct, 4),
        "profit_giveback_pct": round(profit_giveback, 4),
        "hard_stop_pct": round(hard_stop, 4),
        "profit_start_pct": round(profit_start, 4),
        "trailing_giveback_pct": round(giveback, 4),
        "trend_weakening": trend_weak,
    }
    if asset.conclusion in {CONCLUSION_REJECT, CONCLUSION_INSUFFICIENT}:
        return ALERT_EXIT_WATCH, 1.0, context
    if profit_pct <= hard_stop:
        return ALERT_HARD_STOP, 1.0, context
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
) -> tuple[ReplayTrade | None, float]:
    position = positions.get(code)
    if position is None or position.shares <= 0 or fraction <= 0:
        return None, 0.0
    shares = min(position.shares, position.shares * fraction)
    amount = shares * price
    fee = amount * fee_rate
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
) -> tuple[ReplayTrade | None, float]:
    if price <= 0 or amount <= 0:
        return None, 0.0
    fee = amount * fee_rate
    shares = amount / price
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
        new_cost = amount
        current.shares += shares
        current.avg_cost = (old_cost + new_cost) / current.shares if current.shares > 0 else price
    trade = ReplayTrade(
        trade_date=trade_date,
        code=metadata.code,
        name=metadata.name,
        side="buy",
        reason=reason,
        amount=_round_money(amount),
        shares=round(shares, 4),
        price=price,
        fee=_round_money(fee),
        realized_pnl=None,
        metadata={},
    )
    return trade, amount + fee


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
        trading_dates = _trading_dates(series_by_code, effective_start, effective_end)
        if len(trading_dates) < 30:
            raise ValueError("可用交易日少于 30 天，样本不足，暂不生成回测结论")

        cash = capital
        positions: dict[str, ReplayPosition] = {}
        high_watermark = capital
        total_fees = 0.0
        turnover = 0.0
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

        for date_index, trade_date in enumerate(trading_dates):
            assets = _build_daily_assets(metadata_by_code, series_by_code, trade_date)
            latest_assets = assets
            asset_by_code = {asset.metadata.code: asset for asset in assets}
            target_weights, portfolio_mode, portfolio_context = _generate_target_weights(assets)
            target_exposure = round(sum(float(weight) for weight in target_weights.values()), 4)
            last_target_exposure = target_exposure
            target_exposure_sum += target_exposure
            portfolio_mode_counts[portfolio_mode] += 1
            if target_exposure > 0 and first_signal_date is None:
                first_signal_date = trade_date
            if target_exposure < 0.999:
                partial_allocation_days += 1
            if target_exposure <= 0:
                full_cash_days += 1
                cash_wait_reason_counts[_cash_wait_reason_key(portfolio_context, assets)] += 1
            prices = {
                code: price
                for code, series in series_by_code.items()
                if (price := _price_on(series, trade_date)) is not None
            }

            for code in list(positions):
                price = prices.get(code)
                asset = asset_by_code.get(code)
                if price is None or asset is None:
                    continue
                alert_type, fraction, context = _risk_action(positions[code], asset, price)
                if alert_type is None:
                    continue
                trade, cash_delta = _sell_position(
                    positions,
                    code,
                    price=price,
                    fraction=fraction,
                    trade_date=trade_date,
                    reason=alert_type,
                    fee_rate=fee_rate,
                )
                if trade is not None:
                    cash += cash_delta
                    total_fees += trade.fee
                    turnover += trade.amount
                    realized_trades += 1
                    winning_trades += 1 if (trade.realized_pnl or 0.0) > 0 else 0
                    trade.metadata.update(context)
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

            current_equity = _equity(cash, positions, prices)
            for code, target_weight in sorted(target_weights.items(), key=lambda item: item[1], reverse=True):
                asset = asset_by_code.get(code)
                price = prices.get(code)
                if asset is None or price is None:
                    continue
                target_value = current_equity * target_weight
                current_position = positions.get(code)
                current_value = (current_position.shares * price) if current_position else 0.0
                delta = target_value - current_value
                if delta < -max(100.0, current_equity * 0.01):
                    fraction = min(1.0, abs(delta) / current_value) if current_value > 0 else 0.0
                    trade, cash_delta = _sell_position(
                        positions,
                        code,
                        price=price,
                        fraction=fraction,
                        trade_date=trade_date,
                        reason="target_rebalance",
                        fee_rate=fee_rate,
                    )
                    if trade is not None:
                        cash += cash_delta
                        total_fees += trade.fee
                        turnover += trade.amount
                        if trade.realized_pnl is not None:
                            realized_trades += 1
                            winning_trades += 1 if trade.realized_pnl > 0 else 0
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
                elif delta > max(100.0, current_equity * 0.01) and cash > 100:
                    buy_amount = min(delta, cash / (1 + fee_rate))
                    trade, cash_used = _buy_position(
                        positions,
                        asset.metadata,
                        price=price,
                        amount=buy_amount,
                        trade_date=trade_date,
                        reason="target_rebalance",
                        fee_rate=fee_rate,
                    )
                    if trade is not None:
                        cash -= cash_used
                        total_fees += trade.fee
                        turnover += trade.amount
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

            final_equity = _equity(cash, positions, prices)
            high_watermark = max(high_watermark, final_equity)
            drawdown = final_equity / high_watermark - 1.0 if high_watermark > 0 else 0.0
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
                current_price = _price_on(series, trade_date)
                if not current_price:
                    continue
                for horizon in LABEL_HORIZONS:
                    future_index = date_index + horizon
                    if future_index >= len(trading_dates):
                        continue
                    future_date = trading_dates[future_index]
                    future_price = _price_on(series, future_date)
                    if not future_price:
                        continue
                    window_dates = set(trading_dates[date_index + 1 : future_index + 1])
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
        metrics = {
            "cumulative_return": round(final_equity / capital - 1.0, 4),
            "max_drawdown": round(
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
            ),
            "trade_count": len(trade_rows),
            "sell_count": realized_trades,
            "win_rate": round(winning_trades / realized_trades, 4) if realized_trades else None,
            "turnover": round(turnover / capital, 4),
            "total_fees": _round_money(total_fees),
            "average_holding_days": round(mean([(trading_dates[-1] - item.entry_date).days for item in positions.values()]), 2)
            if positions
            else None,
            "average_target_exposure": round(average_target_exposure, 4),
            "last_target_exposure": round(last_target_exposure, 4),
            "partial_allocation_days": partial_allocation_days,
            "full_cash_days": full_cash_days,
            "portfolio_rule": "逐日重放 ETF 资金配置参考 + 日线风控",
        }
        caveats = [
            "这是历史日线回测，不代表未来收益。",
            "第一版不验证盘中分钟级买点，也不模拟券商盘口成交。",
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
            "warmup_days": (first_signal_date - trading_dates[0]).days if first_signal_date else len(trading_dates),
            "first_signal_date": first_signal_date.isoformat() if first_signal_date else None,
            "first_trade_date": first_trade_date.isoformat() if first_trade_date else None,
            "cash_wait_reason_counts": dict(cash_wait_reason_counts),
            "portfolio_mode_counts": dict(portfolio_mode_counts),
            "partial_allocation_days": partial_allocation_days,
            "full_cash_days": full_cash_days,
            "average_target_exposure": round(average_target_exposure, 4),
            "last_target_exposure": round(last_target_exposure, 4),
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
    positions = []
    if latest_date is not None:
        positions = (
            await session.scalars(
                select(EtfPortfolioBacktestPosition)
                .where(
                    EtfPortfolioBacktestPosition.run_id == run.id,
                    EtfPortfolioBacktestPosition.snapshot_date == latest_date,
                )
                .order_by(EtfPortfolioBacktestPosition.weight.desc())
            )
        ).all()
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


def backtest_summary_payload(run: EtfPortfolioBacktestRun) -> dict[str, Any]:
    replay_contract = dict((run.config_json or {}).get("replay_contract") or {})
    evidence_status = (
        EVIDENCE_STATUS_SAME_CONTRACT
        if replay_contract and run.status == "success"
        else EVIDENCE_STATUS_WAITING
        if replay_contract
        else EVIDENCE_STATUS_LEGACY
    )
    evidence_summary = build_evidence_summary(
        current_contract=replay_contract or None,
        validation_evidence={
            "contract_hash": replay_contract.get("contract_hash"),
            "sample_count": 20 if run.status == "success" and replay_contract else 0,
        }
        if replay_contract
        else None,
        backtest_metrics=dict(run.metrics_json or {}),
        caveats=list(run.caveats_json or []),
    )
    evidence_summary["evidence_status"] = evidence_status
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "initial_cash": run.initial_cash,
        "fee_rate": run.fee_rate,
        "metrics": dict(run.metrics_json or {}),
        "benchmark": dict(run.benchmark_json or {}),
        "data_coverage": dict(run.data_coverage_json or {}),
        "caveats": list(run.caveats_json or []),
        "replay_contract": replay_contract,
        "evidence_status": evidence_status,
        "evidence_summary": evidence_summary,
        "error_message": run.error_message,
    }


def _series_price_map(series_by_code: dict[str, list[PricePoint]]) -> dict[str, dict[date, float]]:
    return {code: {point.point_date: point.value for point in series} for code, series in series_by_code.items()}


def _positive_momentum_assets(assets: list[ComputedAsset], *, limit: int = 4) -> list[ComputedAsset]:
    filtered = [
        asset
        for asset in assets
        if isinstance(asset.metrics.get("return_20d"), (int, float))
        and float(asset.metrics.get("return_20d") or 0.0) > 0
        and asset.conclusion not in {CONCLUSION_REJECT, CONCLUSION_INSUFFICIENT}
    ]
    return sorted(filtered, key=lambda item: (float(item.metrics.get("return_20d") or 0.0), item.total_score), reverse=True)[
        :limit
    ]


def _comparison_target_weights(strategy_key: str, assets: list[ComputedAsset]) -> tuple[dict[str, float], str]:
    if strategy_key == "current_workbench":
        weights, mode, _context = _generate_target_weights(assets)
        return weights, mode
    if strategy_key == "equal_weight_benchmark":
        benchmark_assets = [asset for asset in assets if asset.metadata.code in BENCHMARK_CODES][:4]
        selected = benchmark_assets or assets[:4]
        if not selected:
            return {}, PORTFOLIO_MODE_CASH_WAIT
        weight = round(min(1.0 / len(selected), PORTFOLIO_SINGLE_WEIGHT_CAP), 4)
        return {asset.metadata.code: weight for asset in selected}, PORTFOLIO_MODE_RISK_ON
    selected = _positive_momentum_assets(assets, limit=4)
    if strategy_key == "momentum_regime_cash_filter":
        broad_returns = [
            float(asset.metrics.get("return_20d") or 0.0)
            for asset in assets
            if asset.metadata.code in BENCHMARK_CODES and isinstance(asset.metrics.get("return_20d"), (int, float))
        ]
        if broad_returns and mean(broad_returns) <= 0:
            return {}, PORTFOLIO_MODE_CASH_WAIT
    if not selected:
        return {}, PORTFOLIO_MODE_CASH_WAIT
    if strategy_key == "momentum_volatility_weighted" or strategy_key == "momentum_regime_cash_filter":
        raw = [1.0 / max(0.006, float(asset.metrics.get("volatility_20d") or 0.025)) for asset in selected]
        weights = _cap_normalized_weights_by_caps(
            raw,
            [PORTFOLIO_SINGLE_WEIGHT_CAP for _asset in selected],
            target_total=min(1.0, len(selected) * PORTFOLIO_SINGLE_WEIGHT_CAP),
        )
        return (
            {asset.metadata.code: weight for asset, weight in zip(selected, weights or [], strict=False)},
            PORTFOLIO_MODE_RISK_ON,
        )
    weight = round(min(1.0 / len(selected), PORTFOLIO_SINGLE_WEIGHT_CAP), 4)
    return {asset.metadata.code: weight for asset in selected}, PORTFOLIO_MODE_RISK_ON


def _comparison_metrics(
    equity_curve: list[dict[str, Any]],
    *,
    initial_cash: float,
    turnover: float,
    total_fees: float,
    trade_count: int,
    cash_wait_days: int,
) -> dict[str, Any]:
    if not equity_curve:
        return {}
    returns = [
        equity_curve[index]["equity"] / equity_curve[index - 1]["equity"] - 1.0
        for index in range(1, len(equity_curve))
        if equity_curve[index - 1]["equity"] > 0
    ]
    final_equity = float(equity_curve[-1]["equity"])
    max_drawdown = min(float(item.get("drawdown") or 0.0) for item in equity_curve)
    volatility = pstdev(returns) * (252**0.5) if len(returns) >= 2 else None
    cumulative_return = final_equity / initial_cash - 1.0
    return {
        "cumulative_return": round(cumulative_return, 4),
        "final_equity": _round_money(final_equity),
        "max_drawdown": round(max_drawdown, 4),
        "annualized_volatility": round(volatility, 4) if volatility is not None else None,
        "return_drawdown_ratio": round(cumulative_return / abs(max_drawdown), 4) if max_drawdown < 0 else None,
        "turnover": round(turnover / initial_cash, 4),
        "total_fees": _round_money(total_fees),
        "trade_count": trade_count,
        "cash_wait_days": cash_wait_days,
        "trading_days": len(equity_curve),
    }


def _simulate_comparison_strategy(
    strategy_key: str,
    *,
    metadata_by_code: dict[str, ShortResearchAsset],
    series_by_code: dict[str, list[PricePoint]],
    trading_dates: list[date],
    initial_cash: float,
    fee_rate: float,
) -> dict[str, Any]:
    price_map = _series_price_map(series_by_code)
    equity = initial_cash
    high_watermark = initial_cash
    previous_weights: dict[str, float] = {}
    total_fees = 0.0
    turnover = 0.0
    trade_count = 0
    cash_wait_days = 0
    curve: list[dict[str, Any]] = []
    for index, trade_date in enumerate(trading_dates):
        if index > 0:
            previous_date = trading_dates[index - 1]
            day_return = 0.0
            for code, weight in previous_weights.items():
                previous_price = price_map.get(code, {}).get(previous_date)
                current_price = price_map.get(code, {}).get(trade_date)
                if previous_price and current_price:
                    day_return += weight * (current_price / previous_price - 1.0)
            equity *= 1.0 + day_return
        assets = _build_daily_assets(metadata_by_code, series_by_code, trade_date)
        target_weights, portfolio_mode = _comparison_target_weights(strategy_key, assets)
        weight_change = sum(abs(target_weights.get(code, 0.0) - previous_weights.get(code, 0.0)) for code in set(target_weights) | set(previous_weights))
        if weight_change > 0.0001:
            fee = equity * weight_change * fee_rate
            equity -= fee
            total_fees += fee
            turnover += equity * weight_change
            trade_count += 1
        previous_weights = target_weights
        if not target_weights:
            cash_wait_days += 1
        high_watermark = max(high_watermark, equity)
        drawdown = equity / high_watermark - 1.0 if high_watermark > 0 else 0.0
        curve.append(
            {
                "date": trade_date.isoformat(),
                "equity": _round_money(equity),
                "drawdown": round(drawdown, 4),
                "cash_weight": round(max(0.0, 1.0 - sum(target_weights.values())), 4),
                "portfolio_mode": portfolio_mode,
            }
        )
    return {
        "strategy_key": strategy_key,
        "strategy_label": {
            "current_workbench": "当前 ETF 工作台策略",
            "momentum_top_n": "动量 Top N 等权",
            "momentum_volatility_weighted": "动量 + 波动率权重",
            "momentum_regime_cash_filter": "动量 + 大盘过滤",
            "equal_weight_benchmark": "宽基等权对照",
        }.get(strategy_key, strategy_key),
        "metrics": _comparison_metrics(
            curve,
            initial_cash=initial_cash,
            turnover=turnover,
            total_fees=total_fees,
            trade_count=trade_count,
            cash_wait_days=cash_wait_days,
        ),
        "equity_curve": curve[:: max(1, len(curve) // 120)] if len(curve) > 120 else curve,
        "caveats": ["策略对照使用日线收盘价复盘，不模拟盘中成交和券商盘口。"],
    }


async def run_etf_strategy_comparison_backtest(
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
    capital = float(initial_cash or (user.etf_trading_capital if user else DEFAULT_BACKTEST_INITIAL_CASH) or DEFAULT_BACKTEST_INITIAL_CASH)
    run = EtfPortfolioBacktestRun(
        user_id=user.id if user else None,
        status="running",
        start_date=effective_start,
        end_date=effective_end,
        asset_type=ASSET_TYPE_ETF,
        rule_version=STRATEGY_COMPARISON_RULE_VERSION,
        ranking_version=BACKTEST_RANKING_VERSION,
        allocation_version=BACKTEST_ALLOCATION_VERSION,
        exit_rule_version=BACKTEST_EXIT_RULE_VERSION,
        initial_cash=capital,
        fee_rate=fee_rate,
        config_json={"run_kind": "strategy_comparison", "max_assets": max_assets},
        metrics_json={},
        benchmark_json={},
        data_coverage_json={},
        caveats_json=[],
    )
    session.add(run)
    await session.commit()
    try:
        metadata = await _load_etf_universe(session, max_assets=max_assets)
        if len(metadata) < 20:
            raise ValueError("ETF 历史池太小，无法做策略对照")
        metadata_by_code = {item.code: item for item in metadata}
        series_by_code = await _load_price_series(
            session,
            codes=list(metadata_by_code),
            from_date=effective_start - timedelta(days=260),
            to_date=effective_end,
        )
        trading_dates = _trading_dates(series_by_code, effective_start, effective_end)
        if len(trading_dates) < 30:
            raise ValueError("可用交易日少于 30 天，暂不生成策略对照")
        strategies = [
            _simulate_comparison_strategy(
                strategy_key,
                metadata_by_code=metadata_by_code,
                series_by_code=series_by_code,
                trading_dates=trading_dates,
                initial_cash=capital,
                fee_rate=fee_rate,
            )
            for strategy_key in (
                "current_workbench",
                "momentum_top_n",
                "momentum_volatility_weighted",
                "momentum_regime_cash_filter",
                "equal_weight_benchmark",
            )
        ]
        best = max(
            strategies,
            key=lambda item: float((item.get("metrics") or {}).get("return_drawdown_ratio") or -999.0),
        )
        run.status = "success"
        run.finished_at = utcnow()
        run.metrics_json = {
            "run_kind": "strategy_comparison",
            "strategy_count": len(strategies),
            "best_strategy": best["strategy_key"],
            "strategies": strategies,
        }
        run.data_coverage_json = {
            "start_date": trading_dates[0].isoformat(),
            "end_date": trading_dates[-1].isoformat(),
            "trading_days": len(trading_dates),
            "asset_count": len(metadata),
            "priced_asset_count": len(series_by_code),
        }
        run.caveats_json = [
            "策略对照只用于比较不同规则在同一历史数据中的表现，不代表未来收益。",
            "当前工作台策略和页面组合使用同一目标权重生成逻辑。",
        ]
        await session.commit()
        await session.refresh(run)
        return run
    except Exception as exc:
        return await mark_backtest_failed(session, run, str(exc) or "ETF 策略对照失败")


async def latest_strategy_comparison_run(session: AsyncSession) -> EtfPortfolioBacktestRun | None:
    return await session.scalar(
        select(EtfPortfolioBacktestRun)
        .where(EtfPortfolioBacktestRun.asset_type == ASSET_TYPE_ETF, EtfPortfolioBacktestRun.rule_version == STRATEGY_COMPARISON_RULE_VERSION)
        .order_by(EtfPortfolioBacktestRun.finished_at.desc(), EtfPortfolioBacktestRun.id.desc())
    )


def strategy_comparison_payload(run: EtfPortfolioBacktestRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "initial_cash": run.initial_cash,
        "fee_rate": run.fee_rate,
        "data_coverage": dict(run.data_coverage_json or {}),
        "caveats": list(run.caveats_json or []),
        "strategies": list((run.metrics_json or {}).get("strategies") or []),
        "best_strategy": (run.metrics_json or {}).get("best_strategy"),
        "error_message": run.error_message,
    }
