from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import pstdev
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.defaults.short_research import (
    ASSET_TYPE_ETF,
    ASSET_TYPE_FUND,
    SHORT_RESEARCH_ASSET_BY_KEY,
)
from app.models.entities import (
    EtfPriceHistory,
    Fund,
    FundNavHistory,
    ShortResearchAdvisorReport,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    TradableEtf,
    User,
    utcnow,
)
from app.schemas.etf_quotes import DynamicExitThresholdsOut, TrackedEtfIntradaySnapshotOut
from app.schemas.tracked_positions import (
    TrackedPositionAlertOut,
    TrackedPositionChartPoint,
    TrackedPositionExitSignal,
    TrackedPositionSnapshot,
)
from app.services.intraday_etf.service import ASIA_SHANGHAI, is_quote_stale, latest_intraday_quote
from app.services.notifier import Notifier
from app.services.short_research.advisor import (
    ACTION_EXIT,
    EXIT_RISKS,
    conservative_action_for_item,
    latest_reports_by_asset,
)
from app.services.short_research.service import (
    ensure_short_research_universe,
    latest_signal_run,
    list_signal_items,
)

ACTIVE_STATUS = "active"
ORDER_BEFORE_15 = "before_15"
ORDER_AFTER_15 = "after_15"
ORDER_UNKNOWN = "unknown"
ALERT_EXIT_WATCH = "exit_watch"
ALERT_RISK_WARNING = "risk_warning"
ALERT_TAKE_PROFIT_WATCH = "take_profit_watch"
ALERT_TRAILING_TAKE_PROFIT = "trailing_take_profit"
ALERT_TREND_WEAKENING = "trend_weakening"
ALERT_HARD_STOP = "hard_stop"
EMAIL_ALERT_TYPES = {
    ALERT_EXIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_TREND_WEAKENING,
    ALERT_HARD_STOP,
}

TAKE_PROFIT_WATCH_PCT = 3.0
TRAILING_START_PROFIT_PCT = 5.0
TRAILING_GIVEBACK_POINTS = 2.5
TRAILING_GIVEBACK_RATIO = 0.35
HARD_STOP_LOSS_PCT = -4.0
TAKE_PROFIT_WATCH_COOLDOWN_DAYS = 3

TAKE_PROFIT_RISKS = {"追高风险", "连续大涨"}


@dataclass(frozen=True)
class PriceSnapshot:
    price: float
    price_date: date


@dataclass(frozen=True)
class AlertDecision:
    alert_type: str
    trigger_label: str
    reasons: list[str]
    risk_flags: list[str]
    advisor_summary: str | None
    signal_item: ShortResearchSignalItem
    advisor_report: ShortResearchAdvisorReport | None
    alert_level: str = "warning"
    alert_source: str = "daily_close"
    quote_time: datetime | None = None


@dataclass(frozen=True)
class PositionAnalysis:
    chart: list[TrackedPositionChartPoint]
    exit_signal: TrackedPositionExitSignal
    max_profit_pct: float | None
    profit_giveback_pct: float | None
    holding_days: int | None
    technical_metrics: dict[str, Any]
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None
    dynamic_thresholds: DynamicExitThresholdsOut | None = None


def email_configured(user: User, settings: Settings) -> bool:
    host = user.smtp_host or settings.smtp_host
    username = user.smtp_username or settings.smtp_username
    password = settings.smtp_password if user.smtp_password_ref == "env:SMTP_PASSWORD" else settings.smtp_password
    return bool(host and not host.endswith("example.com") and username and password)


def build_notifier_for_user(user: User, settings: Settings) -> Notifier:
    smtp_password = settings.smtp_password if user.smtp_password_ref == "env:SMTP_PASSWORD" else ""
    return Notifier(
        smtp_host=user.smtp_host or settings.smtp_host,
        smtp_port=user.smtp_port or settings.smtp_port,
        smtp_username=user.smtp_username or settings.smtp_username,
        smtp_password=smtp_password or settings.smtp_password,
        smtp_from=user.smtp_from or settings.smtp_from,
    )


async def resolve_asset_name(session: AsyncSession, asset_type: str, asset_code: str) -> str:
    await ensure_short_research_universe(session)
    metadata = SHORT_RESEARCH_ASSET_BY_KEY.get((asset_type, asset_code))
    if metadata is not None:
        return metadata.name
    if asset_type == ASSET_TYPE_FUND:
        fund = await session.get(Fund, asset_code)
        if fund is not None:
            return fund.name
    if asset_type == ASSET_TYPE_ETF:
        etf = await session.get(TradableEtf, asset_code)
        if etf is not None:
            return etf.name
    raise ValueError("只能追踪短线研究池里的基金或 ETF")


async def latest_price(
    session: AsyncSession,
    asset_type: str,
    asset_code: str,
    *,
    on_or_before: date | None = None,
    on_or_after: date | None = None,
) -> PriceSnapshot | None:
    if on_or_before is not None and on_or_after is not None:
        raise ValueError("on_or_before 和 on_or_after 不能同时使用")
    if asset_type == ASSET_TYPE_FUND:
        fund_query = select(FundNavHistory).where(FundNavHistory.fund_code == asset_code)
        if on_or_before is not None:
            fund_query = fund_query.where(FundNavHistory.nav_date <= on_or_before)
        if on_or_after is not None:
            fund_query = fund_query.where(FundNavHistory.nav_date >= on_or_after)
        order_by = FundNavHistory.nav_date.asc() if on_or_after is not None else FundNavHistory.nav_date.desc()
        row = await session.scalar(fund_query.order_by(order_by))
        return PriceSnapshot(row.nav, row.nav_date) if row is not None else None
    if asset_type == ASSET_TYPE_ETF:
        etf_query = select(EtfPriceHistory).where(EtfPriceHistory.etf_code == asset_code)
        if on_or_before is not None:
            etf_query = etf_query.where(EtfPriceHistory.trade_date <= on_or_before)
        if on_or_after is not None:
            etf_query = etf_query.where(EtfPriceHistory.trade_date >= on_or_after)
        order_by = EtfPriceHistory.trade_date.asc() if on_or_after is not None else EtfPriceHistory.trade_date.desc()
        row = await session.scalar(etf_query.order_by(order_by))
        return PriceSnapshot(row.close, row.trade_date) if row is not None else None
    raise ValueError("资产类型只支持 fund 或 etf")


async def latest_tracking_price(
    session: AsyncSession,
    asset_type: str,
    asset_code: str,
) -> tuple[PriceSnapshot | None, TrackedEtfIntradaySnapshotOut | None]:
    if asset_type != ASSET_TYPE_ETF:
        return await latest_price(session, asset_type, asset_code), None
    quote = await latest_intraday_quote(session, asset_code)
    if quote is not None and not is_quote_stale(quote.quote_time):
        spread_pct = None
        if quote.bid_price and quote.ask_price and quote.bid_price > 0:
            midpoint = (quote.bid_price + quote.ask_price) / 2
            spread_pct = (quote.ask_price - quote.bid_price) / midpoint * 100 if midpoint else None
        intraday = TrackedEtfIntradaySnapshotOut(
            current_price=round(quote.latest_price, 6),
            quote_time=quote.quote_time,
            trade_date=quote.trade_date,
            price_source="intraday_quote",
            is_stale=False,
            freshness_status=quote.freshness_status,
            bid_price=quote.bid_price,
            ask_price=quote.ask_price,
            spread_pct=_round_or_none(spread_pct, 4),
            iopv=quote.iopv,
            premium_discount_pct=quote.premium_discount_pct,
            turnover=quote.turnover,
            source=quote.source,
            message="使用公开 ETF 盘中行情估算。",
        )
        return PriceSnapshot(quote.latest_price, quote.trade_date), intraday
    daily = await latest_price(session, asset_type, asset_code)
    intraday = TrackedEtfIntradaySnapshotOut(
        current_price=round(daily.price, 6) if daily else None,
        trade_date=daily.price_date if daily else None,
        price_source="daily_close" if daily else "unavailable",
        is_stale=True,
        freshness_status="stale" if quote is not None else "missing",
        quote_time=quote.quote_time if quote is not None else None,
        bid_price=quote.bid_price if quote is not None else None,
        ask_price=quote.ask_price if quote is not None else None,
        iopv=quote.iopv if quote is not None else None,
        premium_discount_pct=quote.premium_discount_pct if quote is not None else None,
        turnover=quote.turnover if quote is not None else None,
        source=quote.source if quote is not None else None,
        message="盘中行情缺失或滞后，暂用最近日线收盘价估算。",
    )
    return daily, intraday


def tracking_start_date(position: TrackedPosition) -> date:
    if position.confirmed_nav_date is not None:
        return position.confirmed_nav_date
    if position.order_time_bucket in {ORDER_BEFORE_15, ORDER_AFTER_15} and position.entry_price_date is not None:
        return position.entry_price_date
    return position.buy_date


async def resolve_entry_price(
    session: AsyncSession,
    *,
    asset_type: str,
    asset_code: str,
    buy_date: date,
    order_time_bucket: str,
    confirmed_nav_date: date | None,
    confirmed_nav: float | None,
) -> tuple[PriceSnapshot | None, date | None]:
    if confirmed_nav_date is not None:
        if confirmed_nav is not None:
            return PriceSnapshot(confirmed_nav, confirmed_nav_date), confirmed_nav_date
        entry = await latest_price(session, asset_type, asset_code, on_or_after=confirmed_nav_date)
        return entry, entry.price_date if entry is not None else confirmed_nav_date
    if asset_type == ASSET_TYPE_ETF and confirmed_nav is not None:
        return PriceSnapshot(confirmed_nav, buy_date), buy_date
    if asset_type == ASSET_TYPE_ETF:
        local_today = datetime.now(ASIA_SHANGHAI).date()
        quote = await latest_intraday_quote(session, asset_code)
        if buy_date == local_today and quote is not None and not is_quote_stale(quote.quote_time):
            return PriceSnapshot(quote.latest_price, quote.trade_date), quote.trade_date
        entry = await latest_price(session, asset_type, asset_code, on_or_before=buy_date)
        return entry, entry.price_date if entry is not None else None
    if order_time_bucket == ORDER_AFTER_15:
        entry = await latest_price(session, asset_type, asset_code, on_or_after=buy_date + timedelta(days=1))
        return entry, entry.price_date if entry is not None else None
    if order_time_bucket == ORDER_BEFORE_15:
        entry = await latest_price(session, asset_type, asset_code, on_or_after=buy_date)
        return entry, entry.price_date if entry is not None else None
    entry = await latest_price(session, asset_type, asset_code, on_or_before=buy_date)
    return entry, None


def estimated_shares_for_position(
    *,
    buy_amount: float,
    confirmed_shares: float | None,
    entry: PriceSnapshot | None,
) -> float | None:
    if confirmed_shares is not None:
        return confirmed_shares
    return (buy_amount / entry.price) if entry and entry.price else None


def _round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def cost_basis_for_position(position: TrackedPosition) -> tuple[float | None, str | None]:
    if position.estimated_shares and position.entry_price:
        source = "confirmed_shares_entry_price" if position.confirmed_shares is not None else "estimated_shares_entry_price"
        return position.estimated_shares * position.entry_price, source
    if position.buy_amount:
        return position.buy_amount, "buy_amount_estimate"
    return None, None


def _mean_or_none(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _quote_source_message(snapshot: TrackedEtfIntradaySnapshotOut | None) -> str:
    if snapshot is None:
        return "数据源：公开日线。"
    if snapshot.quote_time is not None:
        return f"数据源：{snapshot.price_source}，行情时间 {snapshot.quote_time:%Y-%m-%d %H:%M:%S}。"
    return f"数据源：{snapshot.price_source}。"


async def dynamic_thresholds_for_position(
    session: AsyncSession,
    position: TrackedPosition,
    chart: list[TrackedPositionChartPoint],
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
) -> DynamicExitThresholdsOut | None:
    if position.asset_type == ASSET_TYPE_FUND:
        prices = [point.price for point in chart if point.price]
        returns = [
            prices[index] / prices[index - 1] - 1.0
            for index in range(1, len(prices))
            if prices[index - 1]
        ][-20:]
        realized_vol_pct = pstdev(returns) * 100 if len(returns) >= 5 else None
        peak = prices[0] if prices else None
        max_drawdown_pct = 0.0
        if peak is not None:
            for price in prices:
                peak = max(peak, price)
                if peak:
                    max_drawdown_pct = min(max_drawdown_pct, (price / peak - 1.0) * 100)
        drawdown_unit_pct = abs(max_drawdown_pct) / 3 if len(prices) >= 5 else None
        volatility_unit_pct = max(
            value
            for value in [realized_vol_pct, drawdown_unit_pct, 1.5]
            if value is not None
        )
        ma5 = _mean_or_none(prices[-5:]) if len(prices) >= 5 else None
        ma10 = _mean_or_none(prices[-10:]) if len(prices) >= 10 else None
        return_5d_pct = ((prices[-1] / prices[-6] - 1.0) * 100) if len(prices) >= 6 and prices[-6] else None
        trend_weakening = (
            len(prices) > 0
            and ma5 is not None
            and ma10 is not None
            and return_5d_pct is not None
            and prices[-1] < ma5
            and prices[-1] < ma10
            and return_5d_pct < 0
        )
        warnings = ["净值样本偏少，动态阈值使用保守下限。"] if len(returns) < 5 else []
        return DynamicExitThresholdsOut(
            volatility_unit_pct=_round_or_none(volatility_unit_pct),
            hard_stop_pct=_round_or_none(-_clamp(1.35 * volatility_unit_pct, 2.0, 6.5)),
            profit_start_pct=_round_or_none(max(TRAILING_START_PROFIT_PCT, 1.5 * volatility_unit_pct)),
            trailing_giveback_pct=_round_or_none(_clamp(0.85 * volatility_unit_pct, 1.2, 3.5)),
            trend_weakening=trend_weakening,
            liquidity_warnings=warnings,
            structure_warnings=[],
        )
    if position.asset_type != ASSET_TYPE_ETF:
        return None
    rows = (
        await session.scalars(
            select(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == position.asset_code)
            .order_by(EtfPriceHistory.trade_date.desc())
            .limit(25)
        )
    ).all()
    ordered = list(reversed(rows))
    closes = [row.close for row in ordered if row.close]
    returns = [
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
        if closes[index - 1]
    ][-20:]
    realized_vol_pct = pstdev(returns) * 100 if len(returns) >= 8 else None
    atr_values: list[float] = []
    for index, row in enumerate(ordered):
        previous_close = ordered[index - 1].close if index > 0 else row.close
        if not previous_close:
            continue
        true_range = max(row.high - row.low, abs(row.high - previous_close), abs(row.low - previous_close))
        atr_values.append(true_range / previous_close * 100)
    atr_pct = _mean_or_none(atr_values[-20:]) if len(atr_values) >= 8 else None
    volatility_unit_pct = max(
        value
        for value in [realized_vol_pct, atr_pct, 2.0]
        if value is not None
    )
    hard_stop_pct = -_clamp(1.5 * volatility_unit_pct, 1.2, 4.5)
    profit_start_pct = max(1.5 * volatility_unit_pct, 1.0)
    trailing_giveback_pct = _clamp(0.9 * volatility_unit_pct, 0.8, 2.5)

    prices = [point.price for point in chart]
    ma5 = _mean_or_none(prices[-5:]) if len(prices) >= 5 else None
    ma10 = _mean_or_none(prices[-10:]) if len(prices) >= 10 else None
    return_5d_pct = ((prices[-1] / prices[-6] - 1.0) * 100) if len(prices) >= 6 and prices[-6] else None
    current_price = prices[-1] if prices else intraday_snapshot.current_price if intraday_snapshot else None
    intraday_average_proxy = None
    if intraday_snapshot and intraday_snapshot.turnover and intraday_snapshot.turnover > 0:
        # AKShare 的成交量单位可能随接口变化，这里只把它作为“盘中均价代理”，不作为精确 VWAP。
        raw_volume = None
        quote = await latest_intraday_quote(session, position.asset_code)
        if quote is not None and quote.volume and quote.volume > 0:
            raw_volume = quote.volume
        if raw_volume:
            intraday_average_proxy = intraday_snapshot.turnover / raw_volume
    trend_weakening = (
        current_price is not None
        and ma5 is not None
        and ma10 is not None
        and return_5d_pct is not None
        and current_price < ma5
        and current_price < ma10
        and return_5d_pct < 0
        and (intraday_average_proxy is None or current_price < intraday_average_proxy)
    )

    liquidity_warnings: list[str] = []
    structure_warnings: list[str] = []
    if intraday_snapshot is not None:
        if intraday_snapshot.is_stale:
            liquidity_warnings.append("盘中行情已滞后，不能当成实时价格。")
        if intraday_snapshot.spread_pct is not None and intraday_snapshot.spread_pct >= 0.3:
            liquidity_warnings.append(f"买卖价差约 {intraday_snapshot.spread_pct:.2f}%，成交成本可能变高。")
        if intraday_snapshot.turnover is not None and intraday_snapshot.turnover < 30_000_000:
            liquidity_warnings.append(f"当前成交额约 {intraday_snapshot.turnover / 10_000:.0f} 万元，短线流动性偏弱。")
        if intraday_snapshot.premium_discount_pct is not None and abs(intraday_snapshot.premium_discount_pct) >= 0.8:
            structure_warnings.append(f"折溢价约 {intraday_snapshot.premium_discount_pct:.2f}%，价格可能偏离基金净值。")
        if intraday_snapshot.iopv is None:
            structure_warnings.append("暂无 IOPV，无法判断盘中价格相对净值是否偏贵。")

    return DynamicExitThresholdsOut(
        volatility_unit_pct=_round_or_none(volatility_unit_pct),
        hard_stop_pct=_round_or_none(hard_stop_pct),
        profit_start_pct=_round_or_none(profit_start_pct),
        trailing_giveback_pct=_round_or_none(trailing_giveback_pct),
        trend_weakening=trend_weakening,
        liquidity_warnings=liquidity_warnings,
        structure_warnings=structure_warnings,
    )


def _exit_signal(
    *,
    alert_type: str | None = None,
    label: str = "暂无卖出/减仓提醒",
    level: str = "none",
    reasons: list[str] | None = None,
) -> TrackedPositionExitSignal:
    reason_list = reasons or []
    return TrackedPositionExitSignal(
        alert_type=alert_type,
        label=label,
        level=cast(Any, level),
        reason=reason_list[0] if reason_list else None,
        reasons=reason_list,
    )


def _alert_type_label(alert_type: str) -> str:
    return {
        ALERT_EXIT_WATCH: "卖出/减仓提醒",
        ALERT_TRAILING_TAKE_PROFIT: "卖出/减仓提醒",
        ALERT_TREND_WEAKENING: "卖出/减仓提醒",
        ALERT_HARD_STOP: "止损提醒",
    }.get(alert_type, "网页风险提示")


def _should_send_email(alert_type: str) -> bool:
    return alert_type in EMAIL_ALERT_TYPES


def _web_only_message(alert_type: str) -> str:
    if alert_type == ALERT_RISK_WARNING:
        return "仅网页提示：这是数据质量或盘中结构提示，不是明确卖出/减仓信号。"
    if alert_type == ALERT_TAKE_PROFIT_WATCH:
        return "仅网页提示：止盈观察用于提醒你关注利润，不是明确卖出/减仓信号。"
    return "仅网页提示：不是明确卖出/减仓信号。"


def _performance_analysis(
    position: TrackedPosition,
    chart: list[TrackedPositionChartPoint],
    item: ShortResearchSignalItem | None,
    *,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None,
    dynamic_thresholds: DynamicExitThresholdsOut | None = None,
) -> PositionAnalysis:
    start_date = tracking_start_date(position)
    if not chart:
        return PositionAnalysis(
            chart=[],
            exit_signal=_exit_signal(reasons=["等待公开净值或 ETF 日线数据，暂不能计算卖出/减仓提醒。"]),
            max_profit_pct=None,
            profit_giveback_pct=None,
            holding_days=None,
            technical_metrics={"data_status": "数据不足"},
            intraday_snapshot=intraday_snapshot,
            dynamic_thresholds=dynamic_thresholds,
        )

    current_point = chart[-1]
    current_pnl_pct = current_point.estimated_pnl_pct
    pnl_points = [point for point in chart if point.estimated_pnl_pct is not None]
    if current_pnl_pct is None or not pnl_points:
        return PositionAnalysis(
            chart=chart,
            exit_signal=_exit_signal(reasons=["缺少买入净值或估算份额，暂不能计算卖出/减仓提醒。"]),
            max_profit_pct=None,
            profit_giveback_pct=None,
            holding_days=(current_point.date - start_date).days,
            technical_metrics={"data_status": "等待买入净值"},
            intraday_snapshot=intraday_snapshot,
            dynamic_thresholds=dynamic_thresholds,
        )

    high_point = max(pnl_points, key=lambda point: cast(float, point.estimated_pnl_pct))
    max_profit_pct = cast(float, high_point.estimated_pnl_pct)
    profit_giveback_pct = max(0.0, max_profit_pct - current_pnl_pct)
    prices = [point.price for point in chart]
    ma5 = _mean_or_none(prices[-5:]) if len(prices) >= 5 else None
    ma10 = _mean_or_none(prices[-10:]) if len(prices) >= 10 else None
    return_5d_pct = ((prices[-1] / prices[-6] - 1.0) * 100) if len(prices) >= 6 and prices[-6] else None
    dynamic_hard_stop_pct = dynamic_thresholds.hard_stop_pct if dynamic_thresholds else None
    dynamic_profit_start_pct = dynamic_thresholds.profit_start_pct if dynamic_thresholds else None
    dynamic_trailing_giveback_pct = dynamic_thresholds.trailing_giveback_pct if dynamic_thresholds else None
    hard_stop_pct = dynamic_hard_stop_pct if dynamic_hard_stop_pct is not None else HARD_STOP_LOSS_PCT
    profit_start_pct = (
        dynamic_profit_start_pct
        if dynamic_profit_start_pct is not None
        else TRAILING_START_PROFIT_PCT
    )
    if max_profit_pct >= profit_start_pct:
        trailing_threshold = (
            dynamic_trailing_giveback_pct
            if dynamic_trailing_giveback_pct is not None
            else min(TRAILING_GIVEBACK_POINTS, max_profit_pct * TRAILING_GIVEBACK_RATIO)
        )
    else:
        trailing_threshold = None
    take_profit_watch_threshold = (
        max(TAKE_PROFIT_WATCH_PCT, profit_start_pct)
        if position.asset_type == ASSET_TYPE_ETF
        else TAKE_PROFIT_WATCH_PCT
    )
    trailing_stop_pnl_pct = max_profit_pct - trailing_threshold if trailing_threshold is not None else None

    for point in chart:
        point.is_entry = point.date == chart[0].date
        point.is_high = point.date == high_point.date
        point.is_current = point.date == current_point.date
        point.trailing_stop_pnl_pct = _round_or_none(trailing_stop_pnl_pct)

    risk_flags = set(item.risk_flags_json or []) if item is not None else set()
    current_label = item.conclusion if item is not None else None
    rule_trend_weakening = (
        ma5 is not None
        and ma10 is not None
        and return_5d_pct is not None
        and current_point.price < ma5
        and current_point.price < ma10
        and return_5d_pct < 0
    )
    trend_weakening = dynamic_thresholds.trend_weakening if dynamic_thresholds else rule_trend_weakening
    source_message = _quote_source_message(intraday_snapshot)

    technical_metrics: dict[str, Any] = {
        "current_pnl_pct": _round_or_none(current_pnl_pct),
        "max_profit_pct": _round_or_none(max_profit_pct),
        "max_profit_date": high_point.date.isoformat(),
        "profit_giveback_pct": _round_or_none(profit_giveback_pct),
        "ma5": _round_or_none(ma5, 6),
        "ma10": _round_or_none(ma10, 6),
        "return_5d_pct": _round_or_none(return_5d_pct),
        "hard_stop_pct": _round_or_none(hard_stop_pct),
        "profit_start_pct": _round_or_none(profit_start_pct),
        "trailing_threshold_pct": _round_or_none(trailing_threshold),
        "trailing_stop_pnl_pct": _round_or_none(trailing_stop_pnl_pct),
        "trend_weakening": trend_weakening,
        "price_source": intraday_snapshot.price_source if intraday_snapshot else "daily_close",
    }

    if current_pnl_pct <= hard_stop_pct:
        exit_signal = _exit_signal(
            alert_type=ALERT_HARD_STOP,
            label="硬止损提醒",
            level="urgent",
            reasons=[f"当前估算亏损 {current_pnl_pct:.2f}%，已达到 -4% 的硬止损检查线。"],
        )
    elif trailing_threshold is not None and profit_giveback_pct >= trailing_threshold:
        exit_signal = _exit_signal(
            alert_type=ALERT_TRAILING_TAKE_PROFIT,
            label="移动止盈提醒",
            level="warning",
            reasons=[
                f"最高盈利 {max_profit_pct:.2f}%，当前盈利 {current_pnl_pct:.2f}%，已从高点回吐 {profit_giveback_pct:.2f} 个百分点。",
                f"移动止盈阈值为 {trailing_threshold:.2f} 个百分点，建议人工考虑卖出或减仓。",
            ],
        )
    elif trend_weakening:
        exit_signal = _exit_signal(
            alert_type=ALERT_TREND_WEAKENING,
            label="趋势转弱提醒",
            level="warning",
            reasons=[
                f"最新价格 {current_point.price:.4f} 已同时低于 5 日均线 {ma5:.4f} 和 10 日均线 {ma10:.4f}。",
                f"近 5 日收益 {return_5d_pct:.2f}%，上涨趋势开始转弱。",
            ],
        )
    elif current_pnl_pct >= take_profit_watch_threshold and (
        current_label == "高位观察" or bool(risk_flags.intersection(TAKE_PROFIT_RISKS))
    ):
        risk_text = "、".join(sorted(risk_flags.intersection(TAKE_PROFIT_RISKS))) or "高位观察"
        exit_signal = _exit_signal(
            alert_type=ALERT_TAKE_PROFIT_WATCH,
            label="止盈观察提醒",
            level="watch",
            reasons=[
                f"当前估算盈利 {current_pnl_pct:.2f}%，且触发 {risk_text}，说明利润已有但追高风险也在上升。",
                "这不是立即卖出指令，只是提醒你别贪最高点，可以开始考虑止盈或减仓。",
            ],
        )
    else:
        exit_signal = _exit_signal(
            reasons=["暂无卖出/减仓提醒；继续按每日公开数据观察。"],
        )

    if exit_signal.alert_type == ALERT_HARD_STOP:
        exit_signal.label = "硬止损提醒"
        exit_signal.reasons = [
            f"当前估算亏损 {abs(current_pnl_pct):.2f}%（盈亏 {current_pnl_pct:.2f}%），已触及硬止损线 {hard_stop_pct:.2f}%。",
            f"当前价 {current_point.price:.4f}；{source_message}",
        ]
        exit_signal.reason = exit_signal.reasons[0]
    elif exit_signal.alert_type == ALERT_TRAILING_TAKE_PROFIT and trailing_threshold is not None:
        exit_signal.label = "移动止盈提醒"
        exit_signal.reasons = [
            f"最高盈利 {max_profit_pct:.2f}%，当前盈利 {current_pnl_pct:.2f}%，已从高点回吐 {profit_giveback_pct:.2f} 个百分点。",
            f"动态移动止盈回吐线为 {trailing_threshold:.2f} 个百分点，当前价 {current_point.price:.4f}；{source_message}",
        ]
        exit_signal.reason = exit_signal.reasons[0]
    elif exit_signal.alert_type == ALERT_TREND_WEAKENING:
        exit_signal.label = "趋势转弱提醒"
        exit_signal.reasons = [
            f"最新价 {current_point.price:.4f} 已低于 5 日均线 {ma5:.4f} 和 10 日均线 {ma10:.4f}。",
            f"近 5 日收益 {return_5d_pct:.2f}%，趋势开始转弱；{source_message}",
        ]
        exit_signal.reason = exit_signal.reasons[0]
    elif exit_signal.alert_type == ALERT_TAKE_PROFIT_WATCH:
        exit_signal.label = "止盈观察提醒"
        exit_signal.reasons = [
            f"当前估算盈利 {current_pnl_pct:.2f}%，已达到止盈观察启动线 {take_profit_watch_threshold:.2f}%。",
            f"当前价 {current_point.price:.4f}；{source_message} 这不是卖出指令，只提醒你检查是否需要止盈或减仓。",
        ]
        exit_signal.reason = exit_signal.reasons[0]
    elif position.asset_type == ASSET_TYPE_ETF and dynamic_thresholds and not exit_signal.alert_type and (
        dynamic_thresholds.liquidity_warnings or dynamic_thresholds.structure_warnings
    ):
        warnings = [*dynamic_thresholds.liquidity_warnings, *dynamic_thresholds.structure_warnings]
        exit_signal = _exit_signal(
            alert_type=ALERT_RISK_WARNING,
            label="盘中结构风险提醒",
            level="watch",
            reasons=warnings,
        )

    return PositionAnalysis(
        chart=chart,
        exit_signal=exit_signal,
        max_profit_pct=_round_or_none(max_profit_pct),
        profit_giveback_pct=_round_or_none(profit_giveback_pct),
        holding_days=(current_point.date - start_date).days,
        technical_metrics=technical_metrics,
        intraday_snapshot=intraday_snapshot,
        dynamic_thresholds=dynamic_thresholds,
    )


def _estimate_snapshot(position: TrackedPosition, price: PriceSnapshot | None) -> TrackedPositionSnapshot:
    estimated_value: float | None = None
    estimated_pnl: float | None = None
    estimated_pnl_pct: float | None = None
    cost_basis, _source = cost_basis_for_position(position)
    if price is not None and position.estimated_shares:
        estimated_value = position.estimated_shares * price.price
        estimated_pnl = estimated_value - cost_basis if cost_basis is not None else None
        estimated_pnl_pct = estimated_pnl / cost_basis * 100 if estimated_pnl is not None and cost_basis else None
    return TrackedPositionSnapshot(
        current_price=_round_or_none(price.price, 6) if price else None,
        current_price_date=price.price_date if price else None,
        estimated_value=_round_or_none(estimated_value),
        estimated_pnl=_round_or_none(estimated_pnl),
        estimated_pnl_pct=_round_or_none(estimated_pnl_pct),
    )


async def current_snapshot(session: AsyncSession, position: TrackedPosition) -> TrackedPositionSnapshot:
    current_price, _intraday = await latest_tracking_price(session, position.asset_type, position.asset_code)
    snapshot = _estimate_snapshot(position, current_price)
    run = await latest_signal_run(session, asset_type=position.asset_type)
    if run is None:
        return snapshot
    items = await list_signal_items(session, run.id)
    item = next(
        (
            row
            for row in items
            if row.asset_type == position.asset_type and row.asset_code == position.asset_code
        ),
        None,
    )
    if item is None:
        return snapshot
    reports = await latest_reports_by_asset(session, run.id)
    report = reports.get((position.asset_type, position.asset_code))
    snapshot.current_label = item.conclusion
    snapshot.advisor_label = report.action_label if report is not None else conservative_action_for_item(item, is_held=True)
    snapshot.risk_flags = list(item.risk_flags_json or [])
    snapshot.explanation = report.plain_summary if report is not None else str((item.rationale_json or {}).get("key_reason", ""))
    return snapshot


async def position_chart(session: AsyncSession, position: TrackedPosition) -> list[TrackedPositionChartPoint]:
    start_date = tracking_start_date(position)
    points: list[tuple[date, float]]
    if position.asset_type == ASSET_TYPE_FUND:
        fund_rows = (
            await session.scalars(
                select(FundNavHistory)
                .where(
                    FundNavHistory.fund_code == position.asset_code,
                    FundNavHistory.nav_date >= start_date,
                )
                .order_by(FundNavHistory.nav_date.asc())
            )
        ).all()
        points = [(row.nav_date, row.nav) for row in fund_rows]
    else:
        etf_rows = (
            await session.scalars(
                select(EtfPriceHistory)
                .where(
                    EtfPriceHistory.etf_code == position.asset_code,
                    EtfPriceHistory.trade_date >= start_date,
                )
                .order_by(EtfPriceHistory.trade_date.asc())
            )
        ).all()
        points = [(row.trade_date, row.close) for row in etf_rows]
        quote = await latest_intraday_quote(session, position.asset_code)
        if quote is not None and not is_quote_stale(quote.quote_time) and quote.trade_date >= start_date:
            if points and points[-1][0] == quote.trade_date:
                points[-1] = (quote.trade_date, quote.latest_price)
            else:
                points.append((quote.trade_date, quote.latest_price))
    chart: list[TrackedPositionChartPoint] = []
    cost_basis, _source = cost_basis_for_position(position)
    for point_date, price in points[-240:]:
        estimated_value = position.estimated_shares * price if position.estimated_shares else None
        pnl_pct = (
            (estimated_value - cost_basis) / cost_basis * 100
            if estimated_value is not None and cost_basis
            else None
        )
        chart.append(
            TrackedPositionChartPoint(
                date=point_date,
                price=round(price, 6),
                estimated_value=_round_or_none(estimated_value),
                estimated_pnl_pct=_round_or_none(pnl_pct),
            )
        )
    return chart


async def latest_signal_context(
    session: AsyncSession,
    position: TrackedPosition,
) -> tuple[ShortResearchSignalRun | None, ShortResearchSignalItem | None, ShortResearchAdvisorReport | None]:
    run = await latest_signal_run(session, asset_type=position.asset_type)
    if run is None:
        return None, None, None
    items = await list_signal_items(session, run.id)
    item = next(
        (
            row
            for row in items
            if row.asset_type == position.asset_type and row.asset_code == position.asset_code
        ),
        None,
    )
    if item is None:
        return run, None, None
    reports = await latest_reports_by_asset(session, run.id)
    return run, item, reports.get((position.asset_type, position.asset_code))


async def position_analysis(
    session: AsyncSession,
    position: TrackedPosition,
    *,
    item: ShortResearchSignalItem | None = None,
) -> PositionAnalysis:
    chart = await position_chart(session, position)
    intraday_snapshot = None
    dynamic_thresholds = None
    if position.asset_type == ASSET_TYPE_ETF:
        _price, intraday_snapshot = await latest_tracking_price(session, position.asset_type, position.asset_code)
    dynamic_thresholds = await dynamic_thresholds_for_position(session, position, chart, intraday_snapshot)
    return _performance_analysis(
        position,
        chart,
        item,
        intraday_snapshot=intraday_snapshot,
        dynamic_thresholds=dynamic_thresholds,
    )


async def create_position(
    session: AsyncSession,
    *,
    asset_type: str,
    asset_code: str,
    user_id: int = 1,
    buy_amount: float,
    buy_date: date,
    order_time_bucket: str = ORDER_UNKNOWN,
    confirmed_nav_date: date | None = None,
    confirmed_nav: float | None = None,
    confirmed_shares: float | None = None,
    note: str | None = None,
) -> TrackedPosition:
    asset_name = await resolve_asset_name(session, asset_type, asset_code)
    entry, effective_confirmed_nav_date = await resolve_entry_price(
        session,
        asset_type=asset_type,
        asset_code=asset_code,
        buy_date=buy_date,
        order_time_bucket=order_time_bucket,
        confirmed_nav_date=confirmed_nav_date,
        confirmed_nav=confirmed_nav,
    )
    position = TrackedPosition(
        user_id=user_id,
        asset_type=asset_type,
        asset_code=asset_code,
        asset_name=asset_name,
        buy_date=buy_date,
        order_time_bucket=order_time_bucket,
        confirmed_nav_date=effective_confirmed_nav_date,
        confirmed_nav=confirmed_nav,
        confirmed_shares=confirmed_shares,
        buy_amount=round(buy_amount, 2),
        entry_price=entry.price if entry else None,
        entry_price_date=entry.price_date if entry else None,
        estimated_shares=estimated_shares_for_position(
            buy_amount=buy_amount,
            confirmed_shares=confirmed_shares,
            entry=entry,
        ),
        status=ACTIVE_STATUS,
        note=note,
    )
    session.add(position)
    await session.commit()
    await session.refresh(position)
    return position


async def recalculate_entry(session: AsyncSession, position: TrackedPosition) -> bool:
    before = (
        position.confirmed_nav_date,
        position.entry_price,
        position.entry_price_date,
        position.estimated_shares,
    )
    entry, effective_confirmed_nav_date = await resolve_entry_price(
        session,
        asset_type=position.asset_type,
        asset_code=position.asset_code,
        buy_date=position.buy_date,
        order_time_bucket=position.order_time_bucket,
        confirmed_nav_date=position.confirmed_nav_date,
        confirmed_nav=position.confirmed_nav,
    )
    position.confirmed_nav_date = effective_confirmed_nav_date
    position.entry_price = entry.price if entry else None
    position.entry_price_date = entry.price_date if entry else None
    position.estimated_shares = estimated_shares_for_position(
        buy_amount=position.buy_amount,
        confirmed_shares=position.confirmed_shares,
        entry=entry,
    )
    after = (
        position.confirmed_nav_date,
        position.entry_price,
        position.entry_price_date,
        position.estimated_shares,
    )
    if after != before:
        position.updated_at = utcnow()
        return True
    return False


async def refresh_entry_if_waiting(session: AsyncSession, position: TrackedPosition) -> bool:
    if position.estimated_shares is not None:
        return False
    changed = await recalculate_entry(session, position)
    if not changed:
        return False
    await session.commit()
    await session.refresh(position)
    return position.estimated_shares is not None


async def latest_alert_for_position(
    session: AsyncSession,
    position_id: int,
) -> TrackedPositionAlert | None:
    return cast(
        TrackedPositionAlert | None,
        await session.scalar(
            select(TrackedPositionAlert)
            .where(TrackedPositionAlert.tracked_position_id == position_id)
            .order_by(TrackedPositionAlert.alert_date.desc(), TrackedPositionAlert.id.desc())
        ),
    )


async def recent_intraday_alerts_for_position(
    session: AsyncSession,
    position_id: int,
    *,
    limit: int = 5,
) -> list[TrackedPositionAlert]:
    result = await session.scalars(
        select(TrackedPositionAlert)
        .where(
            TrackedPositionAlert.tracked_position_id == position_id,
            TrackedPositionAlert.alert_source == "intraday_quote",
        )
        .order_by(TrackedPositionAlert.created_at.desc(), TrackedPositionAlert.id.desc())
        .limit(limit)
    )
    return list(result.all())


def alert_out(row: TrackedPositionAlert) -> TrackedPositionAlertOut:
    return TrackedPositionAlertOut(
        id=row.id,
        tracked_position_id=row.tracked_position_id,
        alert_date=row.alert_date,
        alert_type=row.alert_type,
        trigger_label=row.trigger_label,
        current_price=row.current_price,
        current_price_date=row.current_price_date,
        estimated_value=row.estimated_value,
        estimated_pnl=row.estimated_pnl,
        estimated_pnl_pct=row.estimated_pnl_pct,
        reasons=list(row.reasons_json or []),
        risk_flags=list(row.risk_flags_json or []),
        advisor_summary=row.advisor_summary,
        alert_level=row.alert_level,
        quote_time=row.quote_time,
        alert_source=row.alert_source,
        suppression_status=row.suppression_status,
        email_status=row.email_status,
        email_error_message=row.email_error_message,
        sent_at=row.sent_at,
        created_at=row.created_at,
    )


async def evaluate_alert_decision(
    session: AsyncSession,
    position: TrackedPosition,
) -> tuple[AlertDecision | None, date | None]:
    run, item, report = await latest_signal_context(session, position)
    if run is None:
        return None, None
    if item is None:
        return None, run.as_of_date
    trigger_label = report.action_label if report is not None else conservative_action_for_item(item, is_held=True)
    risk_flags = list(item.risk_flags_json or [])
    exit_risks = sorted(set(risk_flags).intersection(EXIT_RISKS))
    analysis = await position_analysis(session, position, item=item)
    technical_signal = analysis.exit_signal if analysis.exit_signal.alert_type is not None else None

    if item.conclusion in {"不适合短线", "数据不足"}:
        alert_type = ALERT_EXIT_WATCH
        reasons = [f"短线研究标签变为“{item.conclusion}”，不再适合作为短线持有观察对象。"]
    elif exit_risks:
        alert_type = ALERT_RISK_WARNING
        reasons = [f"触发明显风险标签：{'、'.join(exit_risks)}。"]
    elif technical_signal is not None:
        alert_type = cast(str, technical_signal.alert_type)
        reasons = list(technical_signal.reasons)
        trigger_label = technical_signal.label
    elif trigger_label != ACTION_EXIT:
        return None, run.as_of_date
    else:
        alert_type = ALERT_EXIT_WATCH
        reasons = ["保守规则把这笔持仓标记为“退出观察”。"]
    if report is not None and report.plain_summary:
        reasons.append(report.plain_summary)
    return (
        AlertDecision(
            alert_type=alert_type,
            trigger_label=trigger_label,
            reasons=reasons,
            risk_flags=risk_flags,
            advisor_summary=report.plain_summary if report is not None else None,
            signal_item=item,
            advisor_report=report,
        ),
        run.as_of_date,
    )


async def evaluate_alert_decision_v2(
    session: AsyncSession,
    position: TrackedPosition,
) -> tuple[AlertDecision | None, date | None]:
    run, item, report = await latest_signal_context(session, position)
    if run is None:
        return None, None
    if item is None:
        return None, run.as_of_date

    trigger_label = report.action_label if report is not None else conservative_action_for_item(item, is_held=True)
    risk_flags = list(item.risk_flags_json or [])
    exit_risks = sorted(set(risk_flags).intersection(EXIT_RISKS))
    analysis = await position_analysis(session, position, item=item)
    technical_signal = analysis.exit_signal if analysis.exit_signal.alert_type is not None else None
    alert_source = analysis.intraday_snapshot.price_source if analysis.intraday_snapshot else "daily_close"
    quote_time = analysis.intraday_snapshot.quote_time if analysis.intraday_snapshot else None
    alert_date = (
        analysis.intraday_snapshot.trade_date
        if analysis.intraday_snapshot and analysis.intraday_snapshot.trade_date is not None
        else run.as_of_date
    )
    alert_level = "warning"

    if technical_signal is not None and technical_signal.alert_type == ALERT_HARD_STOP:
        alert_type = ALERT_HARD_STOP
        trigger_label = technical_signal.label
        reasons = list(technical_signal.reasons)
        alert_level = technical_signal.level
    elif item.conclusion in {"不适合短线", "数据不足"}:
        alert_type = ALERT_EXIT_WATCH
        reasons = [f"短线研究标签变为“{item.conclusion}”，不再适合作为短线持有观察对象。"]
    elif exit_risks:
        alert_type = ALERT_RISK_WARNING
        reasons = [f"触发明显风险标签：{'、'.join(exit_risks)}。"]
    elif technical_signal is not None:
        alert_type = cast(str, technical_signal.alert_type)
        trigger_label = technical_signal.label
        reasons = list(technical_signal.reasons)
        alert_level = technical_signal.level
    elif trigger_label != ACTION_EXIT:
        return None, alert_date
    else:
        alert_type = ALERT_EXIT_WATCH
        reasons = ["保守规则把这笔持仓标记为“退出观察”，建议人工检查是否卖出或减仓。"]

    if report is not None and report.plain_summary:
        reasons.append(report.plain_summary)
    return (
        AlertDecision(
            alert_type=alert_type,
            trigger_label=trigger_label,
            reasons=reasons,
            risk_flags=risk_flags,
            advisor_summary=report.plain_summary if report is not None else None,
            signal_item=item,
            advisor_report=report,
            alert_level=alert_level,
            alert_source=alert_source,
            quote_time=quote_time,
        ),
        alert_date,
    )


async def create_alert_if_needed(
    session: AsyncSession,
    position: TrackedPosition,
    settings: Settings,
) -> tuple[TrackedPositionAlert | None, str]:
    decision, signal_date = await evaluate_alert_decision_v2(session, position)
    if decision is None or signal_date is None:
        return None, "no_signal"

    current, intraday_snapshot = await latest_tracking_price(session, position.asset_type, position.asset_code)
    snapshot = _estimate_snapshot(position, current)
    is_intraday_alert = position.asset_type == ASSET_TYPE_ETF and decision.alert_source == "intraday_quote"

    if is_intraday_alert:
        recent_intraday = await session.scalar(
            select(TrackedPositionAlert)
            .where(
                TrackedPositionAlert.tracked_position_id == position.id,
                TrackedPositionAlert.alert_type == decision.alert_type,
                TrackedPositionAlert.alert_source == "intraday_quote",
                TrackedPositionAlert.created_at >= utcnow() - timedelta(minutes=30),
            )
            .order_by(TrackedPositionAlert.created_at.desc(), TrackedPositionAlert.id.desc())
        )
        escalated_hard_stop = (
            decision.alert_type == ALERT_HARD_STOP
            and recent_intraday is not None
            and snapshot.estimated_pnl_pct is not None
            and recent_intraday.estimated_pnl_pct is not None
            and snapshot.estimated_pnl_pct <= recent_intraday.estimated_pnl_pct - 1.0
            and decision.quote_time != recent_intraday.quote_time
        )
        if recent_intraday is not None and not escalated_hard_stop:
            suppressed = TrackedPositionAlert(
                tracked_position_id=position.id,
                alert_date=signal_date,
                alert_type=decision.alert_type,
                trigger_label=decision.trigger_label,
                current_price=snapshot.current_price,
                current_price_date=snapshot.current_price_date,
                estimated_value=snapshot.estimated_value,
                estimated_pnl=snapshot.estimated_pnl,
                estimated_pnl_pct=snapshot.estimated_pnl_pct,
                reasons_json=decision.reasons,
                risk_flags_json=decision.risk_flags,
                advisor_summary=decision.advisor_summary,
                alert_level=decision.alert_level,
                quote_time=decision.quote_time,
                alert_source=decision.alert_source,
                suppression_status="suppressed",
                email_status="skipped",
                email_error_message="同一 ETF 同类盘中提醒 30 分钟内只发一次。",
            )
            session.add(suppressed)
            await session.commit()
            await session.refresh(suppressed)
            return suppressed, "suppressed"
    else:
        existing = await session.scalar(
            select(TrackedPositionAlert).where(
                TrackedPositionAlert.tracked_position_id == position.id,
                TrackedPositionAlert.alert_date == signal_date,
                TrackedPositionAlert.alert_type == decision.alert_type,
            )
        )
        if existing is not None:
            return existing, "deduplicated"

    if decision.alert_type == ALERT_TAKE_PROFIT_WATCH and not is_intraday_alert:
        recent_take_profit = await session.scalar(
            select(TrackedPositionAlert)
            .where(
                TrackedPositionAlert.tracked_position_id == position.id,
                TrackedPositionAlert.alert_type == ALERT_TAKE_PROFIT_WATCH,
                TrackedPositionAlert.alert_date >= signal_date - timedelta(days=TAKE_PROFIT_WATCH_COOLDOWN_DAYS),
            )
            .order_by(TrackedPositionAlert.alert_date.desc(), TrackedPositionAlert.id.desc())
        )
        if recent_take_profit is not None:
            return recent_take_profit, "deduplicated"

    alert = TrackedPositionAlert(
        tracked_position_id=position.id,
        alert_date=signal_date,
        alert_type=decision.alert_type,
        trigger_label=decision.trigger_label,
        current_price=snapshot.current_price,
        current_price_date=snapshot.current_price_date,
        estimated_value=snapshot.estimated_value,
        estimated_pnl=snapshot.estimated_pnl,
        estimated_pnl_pct=snapshot.estimated_pnl_pct,
        reasons_json=decision.reasons,
        risk_flags_json=decision.risk_flags,
        advisor_summary=decision.advisor_summary,
        alert_level=decision.alert_level,
        quote_time=decision.quote_time,
        alert_source=intraday_snapshot.price_source if intraday_snapshot else decision.alert_source,
        suppression_status="sent_or_pending",
        email_status="pending",
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)

    if not _should_send_email(alert.alert_type):
        alert.suppression_status = "web_only"
        alert.email_status = "skipped"
        alert.email_error_message = _web_only_message(alert.alert_type)
        await session.commit()
        await session.refresh(alert)
        return alert, "web_only"

    user = await session.get(User, position.user_id)
    assert user is not None
    if not email_configured(user, settings):
        alert.email_status = "skipped"
        alert.email_error_message = "邮件通道未配置或未通过测试"
        await session.commit()
        return alert, "email_skipped"

    notifier = build_notifier_for_user(user, settings)
    try:
        await notifier.send_template(
            session,
            recipient=user.recipient_email,
            template_name="tracked_position_alert.html.j2",
            payload=_email_payload(position, alert, decision),
        )
        alert.email_status = "sent"
        alert.sent_at = utcnow()
        await session.commit()
        return alert, "email_sent"
    except Exception as exc:  # noqa: BLE001
        alert.email_status = "failed"
        alert.email_error_message = str(exc)
        await session.commit()
        return alert, "email_failed"


def _email_payload(
    position: TrackedPosition,
    alert: TrackedPositionAlert,
    decision: AlertDecision,
) -> dict[str, Any]:
    title_prefix = _alert_type_label(alert.alert_type)
    order_time_label = {
        ORDER_BEFORE_15: "15:00 前",
        ORDER_AFTER_15: "15:00 后",
        ORDER_UNKNOWN: "未填写",
    }.get(position.order_time_bucket, "未填写")
    return {
        "title": f"FundScope {title_prefix}：{position.asset_name}",
        "alert_title": title_prefix,
        "asset_name": position.asset_name,
        "asset_code": position.asset_code,
        "asset_type": "ETF" if position.asset_type == ASSET_TYPE_ETF else "基金",
        "buy_date": position.buy_date.isoformat(),
        "order_time_label": order_time_label,
        "confirmed_nav_date": position.confirmed_nav_date.isoformat() if position.confirmed_nav_date else None,
        "confirmed_nav": position.confirmed_nav,
        "confirmed_shares": position.confirmed_shares,
        "buy_amount": position.buy_amount,
        "entry_price": position.entry_price,
        "entry_price_date": position.entry_price_date.isoformat() if position.entry_price_date else None,
        "current_price": alert.current_price,
        "current_price_date": alert.current_price_date.isoformat() if alert.current_price_date else None,
        "estimated_value": alert.estimated_value,
        "estimated_pnl": alert.estimated_pnl,
        "estimated_pnl_pct": alert.estimated_pnl_pct,
        "trigger_label": alert.trigger_label,
        "alert_level": alert.alert_level,
        "alert_source": alert.alert_source,
        "quote_time": alert.quote_time.isoformat() if alert.quote_time else None,
        "current_label": decision.signal_item.conclusion,
        "reasons": list(alert.reasons_json or []),
        "risk_flags": list(alert.risk_flags_json or []),
        "watch_conditions": (
            list(decision.advisor_report.watch_conditions_json or [])
            if decision.advisor_report is not None
            else []
        ),
        "data_limitations": (
            decision.advisor_report.data_limitations
            if decision.advisor_report is not None
            else "结果基于公开净值或 ETF 日线数据，不连接支付宝或券商。"
        ),
    }
