from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import pstdev
from typing import Any, cast

from sqlalchemy import func, select
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
from app.services.intraday_etf.service import (
    ASIA_SHANGHAI,
    is_fresh_decision_quote,
    latest_intraday_quote,
)
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
    ALERT_TAKE_PROFIT_WATCH,
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

RELIABILITY_FRESH_INTRADAY = "fresh_intraday"
RELIABILITY_DAILY_CLOSE = "daily_close"
RELIABILITY_STALE_QUOTE = "stale_quote"
RELIABILITY_MISSING = "missing"


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
    signal_item: ShortResearchSignalItem | None
    advisor_report: ShortResearchAdvisorReport | None
    alert_level: str = "warning"
    alert_source: str = "daily_close"
    quote_time: datetime | None = None


@dataclass(frozen=True)
class SignalContext:
    run: ShortResearchSignalRun | None
    item: ShortResearchSignalItem | None
    report: ShortResearchAdvisorReport | None


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
    if quote is not None:
        spread_pct = None
        if quote.bid_price and quote.ask_price and quote.bid_price > 0:
            midpoint = (quote.bid_price + quote.ask_price) / 2
            spread_pct = (quote.ask_price - quote.bid_price) / midpoint * 100 if midpoint else None
        if is_fresh_decision_quote(quote):
            intraday = TrackedEtfIntradaySnapshotOut(
                current_price=round(quote.latest_price, 6),
                quote_time=quote.quote_time,
                trade_date=quote.trade_date,
                price_source="intraday_quote",
                reliability_level=RELIABILITY_FRESH_INTRADAY,
                email_eligible=True,
                email_eligibility_reason="新鲜盘中公开行情，可用于盘中提醒判断。",
                is_stale=False,
                freshness_status=quote.freshness_status,
                bid_price=quote.bid_price,
                ask_price=quote.ask_price,
                spread_pct=_round_or_none(spread_pct, 4),
                iopv=quote.iopv,
                premium_discount_pct=quote.premium_discount_pct,
                turnover=quote.turnover,
                source=quote.source,
                message="使用公开 ETF 盘中行情估算，仍可能和券商盘口存在延迟。",
            )
            return PriceSnapshot(quote.latest_price, quote.trade_date), intraday
        intraday = TrackedEtfIntradaySnapshotOut(
            current_price=round(quote.latest_price, 6),
            quote_time=quote.quote_time,
            trade_date=quote.trade_date,
            price_source="intraday_quote",
            reliability_level=RELIABILITY_STALE_QUOTE,
            email_eligible=False,
            email_eligibility_reason="最近盘中行情已滞后，只能用于网页估算，不能触发盘中邮件。",
            is_stale=True,
            freshness_status="stale",
            bid_price=quote.bid_price,
            ask_price=quote.ask_price,
            spread_pct=_round_or_none(spread_pct, 4),
            iopv=quote.iopv,
            premium_discount_pct=quote.premium_discount_pct,
            turnover=quote.turnover,
            source=quote.source,
            message="显示最近一次公开 ETF 盘中行情；行情已滞后，仅用于网页估算，不触发邮件。",
        )
        return PriceSnapshot(quote.latest_price, quote.trade_date), intraday

    daily = await latest_price(session, asset_type, asset_code)
    message = (
        "盘中行情缺失，暂用最近 ETF 日线收盘价估算；这不是盘中实时价格。"
        if daily is not None
        else "暂无可用盘中行情或日线收盘价，等待数据更新。"
    )
    intraday = TrackedEtfIntradaySnapshotOut(
        current_price=round(daily.price, 6) if daily else None,
        trade_date=daily.price_date if daily else None,
        price_source="daily_close" if daily else "unavailable",
        reliability_level=RELIABILITY_DAILY_CLOSE if daily else RELIABILITY_MISSING,
        email_eligible=False,
        email_eligibility_reason="非新鲜盘中行情，只能用于估算或收盘后复盘，不能触发盘中邮件。",
        is_stale=True,
        freshness_status="missing",
        message=message,
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
        if buy_date == local_today and is_fresh_decision_quote(quote):
            assert quote is not None
            return PriceSnapshot(quote.latest_price, quote.trade_date), quote.trade_date
        return None, None
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
        return "数据源：公开基金净值或 ETF 日线。"
    if snapshot.price_source == "daily_close":
        return "数据源：ETF 日线收盘价，不是盘中实时行情。"
    if snapshot.price_source == "intraday_quote" and snapshot.quote_time is not None:
        return f"数据源：公开 ETF 盘中行情，行情时间 {snapshot.quote_time:%Y-%m-%d %H:%M:%S}。"
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
    email_eligible: bool = False,
    email_eligibility_reason: str | None = None,
    data_reliability: str | None = None,
) -> TrackedPositionExitSignal:
    reason_list = reasons or []
    return TrackedPositionExitSignal(
        alert_type=alert_type,
        label=label,
        level=cast(Any, level),
        reason=reason_list[0] if reason_list else None,
        reasons=reason_list,
        email_eligible=email_eligible,
        email_eligibility_reason=email_eligibility_reason,
        data_reliability=data_reliability,
    )


def _alert_type_label(alert_type: str) -> str:
    return {
        ALERT_EXIT_WATCH: "卖出/减仓提醒",
        ALERT_TAKE_PROFIT_WATCH: "止盈观察提醒",
        ALERT_TRAILING_TAKE_PROFIT: "卖出/减仓提醒",
        ALERT_TREND_WEAKENING: "卖出/减仓提醒",
        ALERT_HARD_STOP: "止损提醒",
    }.get(alert_type, "网页风险提示")


def _should_send_email(alert_type: str) -> bool:
    return alert_type in EMAIL_ALERT_TYPES


def _is_fresh_intraday_snapshot(snapshot: TrackedEtfIntradaySnapshotOut | None) -> bool:
    return bool(
        snapshot is not None
        and snapshot.price_source == "intraday_quote"
        and snapshot.reliability_level == RELIABILITY_FRESH_INTRADAY
        and not snapshot.is_stale
        and snapshot.current_price is not None
    )


def _data_reliability_for_position(
    position: TrackedPosition,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
) -> str:
    if position.asset_type == ASSET_TYPE_FUND:
        return "daily_nav"
    if intraday_snapshot is None:
        return RELIABILITY_DAILY_CLOSE
    return intraday_snapshot.reliability_level


def _annotate_exit_signal_email_eligibility(
    position: TrackedPosition,
    signal: TrackedPositionExitSignal,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
) -> TrackedPositionExitSignal:
    reliability = signal.data_reliability or _data_reliability_for_position(position, intraday_snapshot)
    signal.data_reliability = reliability
    if signal.alert_type is None:
        signal.email_eligible = False
        signal.email_eligibility_reason = "暂无明确持仓处理信号。"
        return signal
    if not _should_send_email(signal.alert_type):
        signal.email_eligible = False
        signal.email_eligibility_reason = "数据质量或结构提示只在网页展示，不发送邮件。"
        return signal
    if position.asset_type == ASSET_TYPE_ETF and not _is_fresh_intraday_snapshot(intraday_snapshot):
        signal.email_eligible = False
        signal.email_eligibility_reason = "当前不是新鲜盘中行情，盘中邮件不会用日线兜底或旧行情触发。"
        return signal
    signal.email_eligible = True
    signal.email_eligibility_reason = "满足当前数据口径下的邮件提醒条件。"
    return signal


def _decision_email_data_eligible(
    position: TrackedPosition,
    decision: AlertDecision,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
    *,
    evaluation_mode: str,
) -> bool:
    if not _should_send_email(decision.alert_type):
        return True
    if evaluation_mode != "intraday":
        return True
    if position.asset_type != ASSET_TYPE_ETF:
        return True
    return _is_fresh_intraday_snapshot(intraday_snapshot)


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
        exit_signal = _exit_signal(
            reasons=["等待公开净值或 ETF 日线数据，暂不能计算卖出/减仓提醒。"],
            data_reliability=RELIABILITY_MISSING if position.asset_type == ASSET_TYPE_ETF else "unavailable",
        )
        return PositionAnalysis(
            chart=[],
            exit_signal=_annotate_exit_signal_email_eligibility(position, exit_signal, intraday_snapshot),
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
        exit_signal = _exit_signal(
            reasons=["缺少买入净值或估算份额，暂不能计算卖出/减仓提醒。"],
            data_reliability=RELIABILITY_MISSING if position.asset_type == ASSET_TYPE_ETF else "unavailable",
        )
        return PositionAnalysis(
            chart=chart,
            exit_signal=_annotate_exit_signal_email_eligibility(position, exit_signal, intraday_snapshot),
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
    trend_distances = []
    if ma5:
        trend_distances.append((current_point.price / ma5 - 1.0) * 100)
    if ma10:
        trend_distances.append((current_point.price / ma10 - 1.0) * 100)
    trend_weakening_distance_pct = min(trend_distances) if trend_distances else None
    distance_to_hard_stop_pct = current_pnl_pct - hard_stop_pct
    distance_to_profit_start_pct = current_pnl_pct - take_profit_watch_threshold
    distance_to_trailing_giveback_pct = (
        trailing_threshold - profit_giveback_pct if trailing_threshold is not None else None
    )
    threshold_explanation = [
        f"规则版本：{dynamic_thresholds.rule_version if dynamic_thresholds else 'fixed_exit_v1'}。",
        f"硬止损线 {hard_stop_pct:.2f}%，当前距离硬止损线 {distance_to_hard_stop_pct:.2f} 个百分点。",
        f"止盈观察线 {take_profit_watch_threshold:.2f}%，当前距离止盈观察线 {distance_to_profit_start_pct:.2f} 个百分点。",
    ]
    if dynamic_thresholds and dynamic_thresholds.volatility_unit_pct is not None:
        threshold_explanation.append(
            f"动态线参考近阶段波动/回撤，波动单位约 {dynamic_thresholds.volatility_unit_pct:.2f}%。"
        )
    if trailing_threshold is not None and distance_to_trailing_giveback_pct is not None:
        threshold_explanation.append(
            f"移动止盈回吐线 {trailing_threshold:.2f} 个百分点，距离触发还有 {distance_to_trailing_giveback_pct:.2f} 个百分点。"
        )
    if dynamic_thresholds is not None:
        dynamic_thresholds = dynamic_thresholds.model_copy(
            update={
                "distance_to_hard_stop_pct": _round_or_none(distance_to_hard_stop_pct),
                "distance_to_profit_start_pct": _round_or_none(distance_to_profit_start_pct),
                "distance_to_trailing_giveback_pct": _round_or_none(distance_to_trailing_giveback_pct),
                "trend_weakening_distance_pct": _round_or_none(trend_weakening_distance_pct),
                "explanation": threshold_explanation,
            }
        )
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
        "threshold_source": dynamic_thresholds.threshold_source if dynamic_thresholds else "fixed_rule",
        "threshold_rule_version": dynamic_thresholds.rule_version if dynamic_thresholds else "fixed_exit_v1",
        "distance_to_hard_stop_pct": _round_or_none(distance_to_hard_stop_pct),
        "distance_to_profit_start_pct": _round_or_none(distance_to_profit_start_pct),
        "distance_to_trailing_giveback_pct": _round_or_none(distance_to_trailing_giveback_pct),
        "trend_weakening_distance_pct": _round_or_none(trend_weakening_distance_pct),
        "threshold_explanation": threshold_explanation,
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
    elif current_pnl_pct >= take_profit_watch_threshold:
        risk_text = (
            "、".join(sorted(risk_flags.intersection(TAKE_PROFIT_RISKS)))
            or ("高位观察" if current_label == "高位观察" else "达到动态止盈观察线")
        )
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
            f"动态移动止盈回吐线为 {trailing_threshold:.2f} 个百分点，距离触发还有 {distance_to_trailing_giveback_pct:.2f} 个百分点；当前价 {current_point.price:.4f}；{source_message}",
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

    exit_signal = _annotate_exit_signal_email_eligibility(position, exit_signal, intraday_snapshot)

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


def _estimate_snapshot(
    position: TrackedPosition,
    price: PriceSnapshot | None,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None,
) -> TrackedPositionSnapshot:
    estimated_value: float | None = None
    estimated_pnl: float | None = None
    estimated_pnl_pct: float | None = None
    cost_basis, _source = cost_basis_for_position(position)
    if price is not None and position.estimated_shares:
        estimated_value = position.estimated_shares * price.price
        estimated_pnl = estimated_value - cost_basis if cost_basis is not None else None
        estimated_pnl_pct = estimated_pnl / cost_basis * 100 if estimated_pnl is not None and cost_basis else None
    if position.asset_type == ASSET_TYPE_ETF:
        price_source = intraday_snapshot.price_source if intraday_snapshot is not None else "unavailable"
        decision_eligible = bool(intraday_snapshot is not None and intraday_snapshot.email_eligible)
        data_reliability = (
            "verified"
            if decision_eligible
            else intraday_snapshot.reliability_level
            if intraday_snapshot is not None
            else "unavailable"
        )
        display_only_reason = None if decision_eligible else (
            intraday_snapshot.email_eligibility_reason if intraday_snapshot is not None else "暂无可用价格，不能触发邮件或计算持仓处理。"
        )
    else:
        price_source = "daily_nav" if price is not None else "unavailable"
        data_reliability = "verified" if price is not None else "unavailable"
        decision_eligible = price is not None
        display_only_reason = None if decision_eligible else "暂无公开净值，不能触发邮件或计算持仓处理。"
    return TrackedPositionSnapshot(
        current_price=_round_or_none(price.price, 6) if price else None,
        current_price_date=price.price_date if price else None,
        estimated_value=_round_or_none(estimated_value),
        estimated_pnl=_round_or_none(estimated_pnl),
        estimated_pnl_pct=_round_or_none(estimated_pnl_pct),
        data_reliability=data_reliability,
        price_source=price_source,
        decision_eligible=decision_eligible,
        display_only_reason=display_only_reason,
    )


async def current_snapshot(
    session: AsyncSession,
    position: TrackedPosition,
    *,
    item: ShortResearchSignalItem | None = None,
    report: ShortResearchAdvisorReport | None = None,
    signal_context_loaded: bool = False,
) -> TrackedPositionSnapshot:
    current_price, intraday = await latest_tracking_price(session, position.asset_type, position.asset_code)
    snapshot = _estimate_snapshot(position, current_price, intraday)
    if not signal_context_loaded:
        _run, item, report = await latest_signal_context(session, position)
    if item is None:
        return snapshot
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
        if is_fresh_decision_quote(quote) and quote is not None and quote.trade_date >= start_date:
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


def merge_exit_state(position: TrackedPosition, analysis: PositionAnalysis) -> None:
    state = dict(position.exit_state_json or {})
    if analysis.max_profit_pct is not None:
        previous_max = state.get("max_profit_pct")
        if previous_max is None or analysis.max_profit_pct > float(previous_max):
            state["max_profit_pct"] = analysis.max_profit_pct
            state["max_profit_recorded_at"] = utcnow().isoformat()
    if analysis.profit_giveback_pct is not None:
        state["profit_giveback_pct"] = analysis.profit_giveback_pct
    if analysis.holding_days is not None:
        state["holding_days"] = analysis.holding_days
    if analysis.dynamic_thresholds is not None:
        state["dynamic_thresholds"] = analysis.dynamic_thresholds.model_dump(mode="json")
    if analysis.intraday_snapshot is not None:
        state["latest_price_source"] = analysis.intraday_snapshot.price_source
        if analysis.intraday_snapshot.quote_time is not None:
            state["latest_quote_time"] = analysis.intraday_snapshot.quote_time.isoformat()
    state["updated_at"] = utcnow().isoformat()
    position.exit_state_json = state

async def create_position(
    session: AsyncSession,
    *,
    asset_type: str,
    asset_code: str,
    user_id: int,
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


async def latest_alerts_for_positions(
    session: AsyncSession,
    position_ids: list[int],
) -> dict[int, TrackedPositionAlert]:
    ids = list(dict.fromkeys(position_ids))
    if not ids:
        return {}
    ranked = (
        select(
            TrackedPositionAlert.id.label("alert_id"),
            TrackedPositionAlert.tracked_position_id.label("position_id"),
            func.row_number()
            .over(
                partition_by=TrackedPositionAlert.tracked_position_id,
                order_by=(TrackedPositionAlert.alert_date.desc(), TrackedPositionAlert.id.desc()),
            )
            .label("rank"),
        )
        .where(TrackedPositionAlert.tracked_position_id.in_(ids))
        .subquery()
    )
    rows = (
        await session.scalars(
            select(TrackedPositionAlert)
            .join(ranked, ranked.c.alert_id == TrackedPositionAlert.id)
            .where(ranked.c.rank == 1)
        )
    ).all()
    return {row.tracked_position_id: row for row in rows}


async def recent_intraday_alerts_for_positions(
    session: AsyncSession,
    position_ids: list[int],
    *,
    limit: int = 5,
) -> dict[int, list[TrackedPositionAlert]]:
    ids = list(dict.fromkeys(position_ids))
    if not ids:
        return {}
    ranked = (
        select(
            TrackedPositionAlert.id.label("alert_id"),
            TrackedPositionAlert.tracked_position_id.label("position_id"),
            func.row_number()
            .over(
                partition_by=TrackedPositionAlert.tracked_position_id,
                order_by=(TrackedPositionAlert.created_at.desc(), TrackedPositionAlert.id.desc()),
            )
            .label("rank"),
        )
        .where(
            TrackedPositionAlert.tracked_position_id.in_(ids),
            TrackedPositionAlert.alert_source == "intraday_quote",
        )
        .subquery()
    )
    rows = (
        await session.scalars(
            select(TrackedPositionAlert)
            .join(ranked, ranked.c.alert_id == TrackedPositionAlert.id)
            .where(ranked.c.rank <= limit)
            .order_by(
                TrackedPositionAlert.tracked_position_id.asc(),
                TrackedPositionAlert.created_at.desc(),
                TrackedPositionAlert.id.desc(),
            )
        )
    ).all()
    grouped: dict[int, list[TrackedPositionAlert]] = {position_id: [] for position_id in ids}
    for row in rows:
        grouped.setdefault(row.tracked_position_id, []).append(row)
    return grouped


async def latest_signal_contexts(
    session: AsyncSession,
    positions: list[TrackedPosition],
) -> dict[int, SignalContext]:
    contexts = {position.id: SignalContext(None, None, None) for position in positions}
    by_type: dict[str, list[TrackedPosition]] = {}
    for position in positions:
        by_type.setdefault(position.asset_type, []).append(position)
    for asset_type, rows in by_type.items():
        run = await latest_signal_run(session, asset_type=asset_type)
        if run is None:
            continue
        codes = list(dict.fromkeys(row.asset_code for row in rows))
        signal_items = (
            await session.scalars(
                select(ShortResearchSignalItem).where(
                    ShortResearchSignalItem.run_id == run.id,
                    ShortResearchSignalItem.asset_type == asset_type,
                    ShortResearchSignalItem.asset_code.in_(codes),
                )
            )
        ).all()
        item_by_code = {item.asset_code: item for item in signal_items}
        advisor_rows = (
            await session.scalars(
                select(ShortResearchAdvisorReport).where(
                    ShortResearchAdvisorReport.signal_run_id == run.id,
                    ShortResearchAdvisorReport.asset_type == asset_type,
                    ShortResearchAdvisorReport.asset_code.in_(codes),
                )
            )
        ).all()
        report_by_code = {report.asset_code: report for report in advisor_rows}
        for position in rows:
            contexts[position.id] = SignalContext(
                run,
                item_by_code.get(position.asset_code),
                report_by_code.get(position.asset_code),
            )
    return contexts


def _alert_threshold_context(
    position: TrackedPosition,
    alert: TrackedPositionAlert | None,
    decision: AlertDecision,
) -> dict[str, Any]:
    state = dict(position.exit_state_json or {})
    dynamic_thresholds = dict(state.get("dynamic_thresholds") or {})
    context = {
        "threshold_mode": dynamic_thresholds.get("threshold_source", "fixed_rule"),
        "rule_version": dynamic_thresholds.get("rule_version", "fixed_exit_v1"),
        "hard_stop_pct": dynamic_thresholds.get("hard_stop_pct"),
        "profit_start_pct": dynamic_thresholds.get("profit_start_pct"),
        "trailing_giveback_pct": dynamic_thresholds.get("trailing_giveback_pct"),
        "volatility_unit_pct": dynamic_thresholds.get("volatility_unit_pct"),
        "distance_to_hard_stop_pct": dynamic_thresholds.get("distance_to_hard_stop_pct"),
        "distance_to_profit_start_pct": dynamic_thresholds.get("distance_to_profit_start_pct"),
        "distance_to_trailing_giveback_pct": dynamic_thresholds.get("distance_to_trailing_giveback_pct"),
        "max_profit_pct": state.get("max_profit_pct"),
        "profit_giveback_pct": state.get("profit_giveback_pct"),
        "holding_days": state.get("holding_days"),
        "explanation": dynamic_thresholds.get("explanation") or [],
        "alert_type": decision.alert_type,
        "alert_source": decision.alert_source,
    }
    if alert is not None:
        context.update(
            {
                "current_price": alert.current_price,
                "current_price_date": alert.current_price_date.isoformat() if alert.current_price_date else None,
                "estimated_pnl_pct": alert.estimated_pnl_pct,
            }
        )
    return context

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
        threshold_context=dict(row.threshold_context_json or {}),
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
    analysis = await position_analysis(session, position, item=item)
    merge_exit_state(position, analysis)
    technical_signal = analysis.exit_signal if analysis.exit_signal.alert_type is not None else None
    if run is None and technical_signal is None:
        return None, None

    if item is None and technical_signal is not None:
        trigger_label = technical_signal.label
    elif item is not None and report is not None:
        trigger_label = report.action_label
    elif item is not None:
        trigger_label = conservative_action_for_item(item, is_held=True)
    else:
        trigger_label = ""
    risk_flags = list(item.risk_flags_json or []) if item is not None else []
    exit_risks = sorted(set(risk_flags).intersection(EXIT_RISKS))
    alert_source = analysis.intraday_snapshot.price_source if analysis.intraday_snapshot else "daily_close"
    quote_time = analysis.intraday_snapshot.quote_time if analysis.intraday_snapshot else None
    if analysis.intraday_snapshot and analysis.intraday_snapshot.trade_date is not None:
        alert_date = analysis.intraday_snapshot.trade_date
    elif analysis.chart:
        alert_date = analysis.chart[-1].date
    elif run is not None:
        alert_date = run.as_of_date
    else:
        alert_date = date.today()
    alert_level = "warning"

    if technical_signal is not None and technical_signal.alert_type == ALERT_HARD_STOP:
        alert_type = ALERT_HARD_STOP
        trigger_label = technical_signal.label
        reasons = list(technical_signal.reasons)
        alert_level = technical_signal.level
    elif item is None:
        if technical_signal is None:
            return None, alert_date
        alert_type = cast(str, technical_signal.alert_type)
        trigger_label = technical_signal.label
        reasons = [
            *technical_signal.reasons,
            "该资产未进入最新短线榜单上下文，本提醒只基于你的持仓价格和动态线计算。",
        ]
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
    *,
    evaluation_mode: str = "daily",
) -> tuple[TrackedPositionAlert | None, str]:
    decision, signal_date = await evaluate_alert_decision_v2(session, position)
    if decision is None or signal_date is None:
        if session.is_modified(position, include_collections=False):
            await session.commit()
        return None, "no_signal"

    current, intraday_snapshot = await latest_tracking_price(session, position.asset_type, position.asset_code)
    snapshot = _estimate_snapshot(position, current)
    is_intraday_alert = position.asset_type == ASSET_TYPE_ETF and decision.alert_source == "intraday_quote"

    if not _decision_email_data_eligible(
        position,
        decision,
        intraday_snapshot,
        evaluation_mode=evaluation_mode,
    ):
        if session.is_modified(position, include_collections=False):
            await session.commit()
        return None, "data_ineligible"

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
            return recent_intraday, "suppressed"
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

    if decision.alert_type == ALERT_TAKE_PROFIT_WATCH:
        recent_take_profit = await session.scalar(
            select(TrackedPositionAlert)
            .where(
                TrackedPositionAlert.tracked_position_id == position.id,
                TrackedPositionAlert.alert_type == ALERT_TAKE_PROFIT_WATCH,
                TrackedPositionAlert.alert_date >= signal_date - timedelta(days=TAKE_PROFIT_WATCH_COOLDOWN_DAYS),
                TrackedPositionAlert.email_status == "sent",
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
    alert.threshold_context_json = _alert_threshold_context(position, alert, decision)
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
        "current_label": decision.signal_item.conclusion if decision.signal_item is not None else "未进入最新榜单",
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
        "threshold_context": _alert_threshold_context(position, alert, decision),
    }
