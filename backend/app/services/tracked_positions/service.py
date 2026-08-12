from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from statistics import pstdev
from typing import Any, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.market_data import (
    DECISION_ELIGIBLE_RELIABILITIES,
    RELIABILITY_STALE,
    RELIABILITY_UNAVAILABLE,
)
from app.defaults.short_research import (
    ASSET_TYPE_ETF,
    ASSET_TYPE_FUND,
    SHORT_RESEARCH_ASSET_BY_KEY,
)
from app.models.entities import (
    EtfExitHyperoptItem,
    EtfExitHyperoptRun,
    EtfObservationPortfolioItem,
    EtfObservationPortfolioSnapshot,
    EtfPriceHistory,
    Fund,
    FundNavHistory,
    ShortResearchAdvisorReport,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionActionExecution,
    TrackedPositionAlert,
    TrackedPositionAlertAudit,
    TradableEtf,
    User,
    utcnow,
)
from app.schemas.etf_quotes import DynamicExitThresholdsOut, TrackedEtfIntradaySnapshotOut
from app.schemas.tracked_positions import (
    TrackedPositionAlertAuditOut,
    TrackedPositionAlertOut,
    TrackedPositionChartPoint,
    TrackedPositionExitSignal,
    TrackedPositionSnapshot,
)
from app.services.etf_exit_calibration import (
    EXECUTION_MODEL_INTRADAY_ALERT,
    OBJECTIVE_STABILITY_FIRST,
    calibration_contract_hash,
    search_space_for_execution_model,
)
from app.services.market_data import (
    ASIA_SHANGHAI,
    latest_etf_quotes_by_code,
    recent_decision_eligible_etf_adjusted_facts,
    recent_decision_eligible_etf_turnovers_by_code,
)
from app.services.market_data import (
    etf_quote_consensus_status as quote_consensus_status,
)
from app.services.market_data import (
    etf_quote_decision_limitation_reason as quote_decision_limitation_reason,
)
from app.services.market_data import (
    etf_quote_fresh_provider_count as quote_fresh_provider_count,
)
from app.services.market_data import (
    etf_quote_price_diff_abs as quote_price_diff_abs,
)
from app.services.market_data import (
    etf_quote_price_diff_pct as quote_price_diff_pct,
)
from app.services.market_data import (
    etf_quote_provider_count as quote_provider_count,
)
from app.services.market_data import (
    etf_quote_reliability as quote_reliability,
)
from app.services.market_data import (
    is_etf_quote_stale as is_quote_stale,
)
from app.services.market_data import (
    is_etf_quote_time_fallback as is_quote_time_fallback,
)
from app.services.market_data import (
    is_fresh_etf_decision_quote as is_fresh_decision_quote,
)
from app.services.market_data import (
    latest_intraday_etf_quote as latest_intraday_quote,
)
from app.services.notifier import Notifier
from app.services.risk_alerts import (
    ACTION_CLASS_ACTIONABLE_EXIT,
    ACTION_CLASS_DATA_WAITING,
    ACTION_CLASS_GUARD_ONLY,
    ACTION_CLASS_NONE,
    ACTION_CLASS_SOFT_WATCH,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_EXIT_WATCH,
    ALERT_HARD_STOP,
    ALERT_MA5_CLOSE_BREAK_EXIT,
    ALERT_RISK_WARNING,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_TREND_WEAKENING,
    DEFAULT_ETF_TRADING_CAPITAL,
    EMAIL_ALERT_TYPES,
    ETF_DYNAMIC_THRESHOLD_VERSION,
    ETF_PROFIT_PROTECTION_STATE_KEY,
    HARD_STOP_LOSS_PCT,
    TAKE_PROFIT_WATCH_COOLDOWN_DAYS,
    TAKE_PROFIT_WATCH_PCT,
    TRAILING_GIVEBACK_POINTS,
    TRAILING_GIVEBACK_RATIO,
    TRAILING_START_PROFIT_PCT,
    AlertDecision,
    EtfLiquidityCapacityAssessment,
    LongProfitProtectionDecision,
    PositionSizingRecommendation,
    assess_etf_liquidity_capacity,
    calculate_position_sizing,
    derive_etf_profit_thresholds,
    evaluate_exit_execution_evidence,
    evaluate_long_profit_protection,
    evaluate_reentry_state,
)
from app.services.short_research.advisor import (
    ACTION_EXIT,
    EXIT_RISKS,
    conservative_action_for_item,
    latest_reports_by_asset,
)
from app.services.short_research.dynamic_thresholds import (
    ThresholdPricePoint,
    dynamic_threshold_context,
)
from app.services.short_research.service import (
    ensure_short_research_universe,
    latest_signal_run,
    list_signal_items,
)
from app.services.short_research.theme_taxonomy import classify_etf_theme
from app.services.tracked_positions.exposure_repository import (
    ExposureMutationCommand,
    ExposureMutationIntent,
    ExposureMutationSource,
    InitializeTrackedPositionCommand,
    apply_exposure_mutation,
    initialize_tracked_position,
)
from app.services.tracked_positions.ma5_close_break import (
    MA5_CLOSE_BREAK_STATE_KEY,
    AdjustedMa5CloseBreakEvidence,
    load_adjusted_ma5_close_break_evidence,
)
from app.services.tracked_positions.owner_risk import (
    EtfOwnerRiskContext,
    apply_owner_risk_guard,
    build_owner_etf_risk_context,
)

ACTIVE_STATUS = "active"
ORDER_BEFORE_15 = "before_15"
ORDER_AFTER_15 = "after_15"
ORDER_UNKNOWN = "unknown"

TAKE_PROFIT_RISKS = {"追高风险", "连续大涨"}

RELIABILITY_FRESH_INTRADAY = "single_fresh"
RELIABILITY_DAILY_CLOSE = RELIABILITY_STALE
RELIABILITY_STALE_QUOTE = RELIABILITY_STALE
RELIABILITY_MISSING = RELIABILITY_UNAVAILABLE
MAX_TRACKED_ETF_LIQUIDITY_CODES = 100
MA5_CLOSE_BREAK_ALERT_SOURCE = "total_return_adjusted_daily_close"


async def batch_etf_liquidity_inputs(
    session: AsyncSession,
    positions: list[TrackedPosition],
) -> tuple[dict[str, tuple[float, ...]], dict[str, Any]]:
    """Load bounded liquidity inputs once for a tracked-position run."""

    codes = list(
        dict.fromkeys(
            position.asset_code for position in positions if position.asset_type == ASSET_TYPE_ETF
        )
    )[:MAX_TRACKED_ETF_LIQUIDITY_CODES]
    if not codes:
        return {}, {}
    turnovers = await recent_decision_eligible_etf_turnovers_by_code(
        session,
        codes,
        lookback_sessions=20,
        max_codes=MAX_TRACKED_ETF_LIQUIDITY_CODES,
    )
    quotes = await latest_etf_quotes_by_code(session, codes)
    return turnovers, quotes


def etf_liquidity_capacity_for_position(
    position: TrackedPosition,
    snapshot: TrackedPositionSnapshot,
    sizing: PositionSizingRecommendation,
    *,
    daily_turnovers: tuple[float, ...] = (),
    quote: Any | None = None,
) -> EtfLiquidityCapacityAssessment | None:
    if position.asset_type != ASSET_TYPE_ETF:
        return None
    if sizing.action in {"add", "reentry_candidate", "no_add"}:
        side = "buy"
    elif sizing.action in {"trim", "reduce", "exit"}:
        side = "sell"
    else:
        return None
    trade_amount = (
        sizing.recommended_trade_amount
        if side == "buy"
        else sizing.recommended_trade_amount or snapshot.estimated_value
    )
    raw_quote = dict(getattr(quote, "raw_json", None) or {})
    limit_state = raw_quote.get("limit_state")
    return assess_etf_liquidity_capacity(
        side=side,
        trade_amount=trade_amount,
        daily_turnovers=daily_turnovers,
        quote_eligible=is_fresh_decision_quote(quote),
        bid_price=getattr(quote, "bid_price", None),
        ask_price=getattr(quote, "ask_price", None),
        premium_discount_pct=getattr(quote, "premium_discount_pct", None),
        limit_state=str(limit_state) if limit_state else None,
    )


def apply_etf_liquidity_capacity_guard(
    sizing: PositionSizingRecommendation,
    capacity: EtfLiquidityCapacityAssessment | None,
) -> PositionSizingRecommendation:
    if capacity is None:
        return sizing
    capacity_context = capacity.as_context()
    reason_suffix = (
        "流动性容量：" + "、".join(capacity.reason_codes) if capacity.reason_codes else None
    )
    if sizing.action in {"add", "reentry_candidate"} and not capacity.entry_allowed:
        return replace(
            sizing,
            action="no_add",
            label="暂停加仓",
            target_account_weight=sizing.current_account_weight,
            reason=reason_suffix or "当前流动性容量不足，暂停增加风险。",
            action_class=ACTION_CLASS_GUARD_ONLY,
            liquidity_capacity=capacity_context,
        )
    if sizing.action in {"trim", "reduce", "exit"} and capacity.status != "ready":
        return replace(
            sizing,
            reason="；".join(
                item
                for item in (
                    sizing.reason,
                    reason_suffix or "退出容量暂不可用，注意分批和跳空风险。",
                )
                if item
            ),
            liquidity_capacity=capacity_context,
        )
    return replace(sizing, liquidity_capacity=capacity_context)


async def active_tracked_etf_codes(
    session: AsyncSession, codes: list[str] | None = None
) -> list[str]:
    stmt = select(TrackedPosition.asset_code).where(
        TrackedPosition.asset_type == ASSET_TYPE_ETF,
        TrackedPosition.status == ACTIVE_STATUS,
    )
    if codes:
        stmt = stmt.where(TrackedPosition.asset_code.in_(codes))
    rows = await session.scalars(stmt)
    return list(dict.fromkeys(str(code) for code in rows.all()))


@dataclass(frozen=True)
class PriceSnapshot:
    price: float
    price_date: date


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
    profit_protection: LongProfitProtectionDecision | None = None
    ma5_close_break: AdjustedMa5CloseBreakEvidence | None = None


@dataclass(frozen=True)
class PreparedAlertEvaluation:
    decision: AlertDecision | None
    signal_date: date | None
    analysis: PositionAnalysis | None
    data_reason_code: str


def email_configured(user: User, settings: Settings) -> bool:
    host = user.smtp_host or settings.smtp_host
    username = user.smtp_username or settings.smtp_username
    password = (
        settings.smtp_password
        if user.smtp_password_ref == "env:SMTP_PASSWORD"
        else settings.smtp_password
    )
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
        order_by = (
            FundNavHistory.nav_date.asc()
            if on_or_after is not None
            else FundNavHistory.nav_date.desc()
        )
        row = await session.scalar(fund_query.order_by(order_by))
        return PriceSnapshot(row.nav, row.nav_date) if row is not None else None
    if asset_type == ASSET_TYPE_ETF:
        etf_query = select(EtfPriceHistory).where(EtfPriceHistory.etf_code == asset_code)
        if on_or_before is not None:
            etf_query = etf_query.where(EtfPriceHistory.trade_date <= on_or_before)
        if on_or_after is not None:
            etf_query = etf_query.where(EtfPriceHistory.trade_date >= on_or_after)
        order_by = (
            EtfPriceHistory.trade_date.asc()
            if on_or_after is not None
            else EtfPriceHistory.trade_date.desc()
        )
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
        decision_eligible = is_fresh_decision_quote(quote)
        limitation_reason = None if decision_eligible else quote_decision_limitation_reason(quote)
        reliability_level = quote_reliability(quote)
        if is_quote_time_fallback(quote) or is_quote_stale(quote.quote_time):
            reliability_level = RELIABILITY_STALE
        intraday = TrackedEtfIntradaySnapshotOut(
            current_price=round(quote.latest_price, 6),
            quote_time=quote.quote_time,
            trade_date=quote.trade_date,
            price_source="intraday_quote",
            reliability_level=reliability_level,
            email_eligible=decision_eligible,
            email_eligibility_reason=(
                "多源校验通过的新鲜盘中公开行情，可用于盘中提醒判断。"
                if decision_eligible
                else limitation_reason or "盘中行情不可用于邮件提醒。"
            ),
            is_stale=not decision_eligible,
            freshness_status=quote.freshness_status if decision_eligible else "display_only",
            bid_price=quote.bid_price,
            ask_price=quote.ask_price,
            spread_pct=_round_or_none(spread_pct, 4),
            iopv=quote.iopv,
            premium_discount_pct=quote.premium_discount_pct,
            turnover=quote.turnover,
            source=quote.source,
            message=(
                "使用多源校验后的公开 ETF 盘中行情估算，仍可能和券商盘口存在延迟。"
                if decision_eligible
                else f"显示最近公开 ETF 盘中行情；{limitation_reason or '仅用于网页估算，不触发邮件。'}"
            ),
            consensus_status=quote_consensus_status(quote),
            quote_reliability=reliability_level,
            decision_eligible=decision_eligible,
            provider_count=quote_provider_count(quote),
            fresh_provider_count=quote_fresh_provider_count(quote),
            price_diff_abs=quote_price_diff_abs(quote),
            price_diff_pct=quote_price_diff_pct(quote),
            limitation_reason=limitation_reason,
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
        consensus_status="unavailable",
        quote_reliability=RELIABILITY_UNAVAILABLE,
        decision_eligible=False,
        provider_count=0,
        fresh_provider_count=0,
        limitation_reason="暂无盘中行情。",
    )
    return daily, intraday


def tracking_start_date(position: TrackedPosition) -> date:
    if position.confirmed_nav_date is not None:
        return position.confirmed_nav_date
    if (
        position.order_time_bucket in {ORDER_BEFORE_15, ORDER_AFTER_15}
        and position.entry_price_date is not None
    ):
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
        entry = await latest_price(
            session, asset_type, asset_code, on_or_after=buy_date + timedelta(days=1)
        )
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
        source = (
            "confirmed_shares_entry_price"
            if position.confirmed_shares is not None
            else "estimated_shares_entry_price"
        )
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
        return (
            f"数据源：{snapshot.price_source}，行情时间 {snapshot.quote_time:%Y-%m-%d %H:%M:%S}。"
        )
    return f"数据源：{snapshot.price_source}。"


async def _approved_calibration_thresholds(
    session: AsyncSession,
    *,
    bucket_candidates: list[tuple[str, str]],
    volatility_unit_pct: float,
) -> dict[str, Any] | None:
    if not bucket_candidates:
        return None
    expected_contract_hash = calibration_contract_hash(
        search_space=search_space_for_execution_model(EXECUTION_MODEL_INTRADAY_ALERT),
        objective=OBJECTIVE_STABILITY_FIRST,
        execution_model=EXECUTION_MODEL_INTRADAY_ALERT,
    )
    conditions = [
        and_(
            EtfExitHyperoptItem.bucket_type == bucket_type,
            EtfExitHyperoptItem.bucket_key == bucket_key,
        )
        for bucket_type, bucket_key in bucket_candidates
    ]
    rows = (
        await session.execute(
            select(EtfExitHyperoptItem, EtfExitHyperoptRun)
            .join(EtfExitHyperoptRun, EtfExitHyperoptRun.id == EtfExitHyperoptItem.run_id)
            .where(
                EtfExitHyperoptItem.status == "approved",
                EtfExitHyperoptItem.approved_at.is_not(None),
                EtfExitHyperoptRun.execution_model == EXECUTION_MODEL_INTRADAY_ALERT,
                EtfExitHyperoptRun.contract_hash == expected_contract_hash,
                or_(*conditions),
            )
            .order_by(
                EtfExitHyperoptItem.score.desc(),
                EtfExitHyperoptItem.approved_at.desc(),
                EtfExitHyperoptItem.id.desc(),
            )
            .limit(20)
        )
    ).all()
    row: tuple[EtfExitHyperoptItem, EtfExitHyperoptRun] | None = None
    coverage_status = None
    for candidate_item, candidate_run in rows:
        summary = dict(candidate_run.summary_json or {})
        if summary.get("sampled"):
            continue
        if int(summary.get("final_optimized_count", 0) or 0) <= 0:
            continue
        if int(summary.get("enough_intraday_history_count", 0) or 0) <= 0:
            continue
        row = (candidate_item, candidate_run)
        coverage_status = "full_universe"
        break
    if row is None:
        return None

    item, run = row
    params = dict(item.parameter_json or {})
    hard_stop_multiplier = float(params.get("hard_stop_multiplier") or 1.5)
    profit_start_multiplier = float(params.get("profit_start_multiplier") or 1.1)
    trailing_giveback_multiplier = float(params.get("trailing_giveback_multiplier") or 0.65)
    return {
        "thresholds": {
            "hard_stop_pct": -_clamp(hard_stop_multiplier * volatility_unit_pct, 1.2, 7.0),
            "profit_start_pct": _clamp(profit_start_multiplier * volatility_unit_pct, 2.0, 6.0),
            "trailing_giveback_pct": _clamp(
                trailing_giveback_multiplier * volatility_unit_pct, 1.0, 4.0
            ),
        },
        "run_id": run.id,
        "candidate_id": item.id,
        "bucket_key": f"{item.bucket_type}:{item.bucket_key}",
        "calibration_version": run.calibration_rule_version or run.rule_version,
        "execution_model": run.execution_model,
        "contract_hash": run.contract_hash,
        "coverage_status": coverage_status,
    }


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
            value for value in [realized_vol_pct, drawdown_unit_pct, 1.5] if value is not None
        )
        ma5 = _mean_or_none(prices[-5:]) if len(prices) >= 5 else None
        ma10 = _mean_or_none(prices[-10:]) if len(prices) >= 10 else None
        return_5d_pct = (
            ((prices[-1] / prices[-6] - 1.0) * 100) if len(prices) >= 6 and prices[-6] else None
        )
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
            profit_start_pct=_round_or_none(
                max(TRAILING_START_PROFIT_PCT, 1.5 * volatility_unit_pct)
            ),
            trailing_giveback_pct=_round_or_none(_clamp(0.85 * volatility_unit_pct, 1.2, 3.5)),
            trend_weakening=trend_weakening,
            liquidity_warnings=warnings,
            structure_warnings=[],
        )
    if position.asset_type != ASSET_TYPE_ETF:
        return None
    etf = await session.scalar(select(TradableEtf).where(TradableEtf.code == position.asset_code))
    theme_profile = classify_etf_theme(
        code=position.asset_code,
        name=etf.name if etf is not None else position.asset_code,
        asset_class=etf.asset_class if etf is not None else None,
        theme_tags=list(etf.theme_tags_json or []) if etf is not None else [],
    )
    risk_cutoff = chart[-1].date if chart else date.today()
    adjusted_facts = await recent_decision_eligible_etf_adjusted_facts(
        session,
        etf_code=position.asset_code,
        on_or_before=risk_cutoff,
        lookback_sessions=120,
    )
    closes = [float(row.adjusted_close) for row in adjusted_facts if row.adjusted_close]
    returns = [
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
        if closes[index - 1]
    ][-20:]
    realized_vol = pstdev(returns) if len(returns) >= 8 else None
    return_5d = closes[-1] / closes[-6] - 1.0 if len(closes) >= 6 and closes[-6] else None
    return_20d = closes[-1] / closes[-21] - 1.0 if len(closes) >= 21 and closes[-21] else None
    return_60d = closes[-1] / closes[-61] - 1.0 if len(closes) >= 61 and closes[-61] else None
    max_drawdown_60d = None
    recent_60 = closes[-60:]
    if recent_60:
        peak = recent_60[0]
        drawdown = 0.0
        for close in recent_60:
            peak = max(peak, close)
            if peak:
                drawdown = min(drawdown, close / peak - 1.0)
        max_drawdown_60d = drawdown
    adjusted_points: list[ThresholdPricePoint] = []
    for row in adjusted_facts:
        adjusted_close = float(row.adjusted_close or 0.0)
        adjustment_factor = (
            adjusted_close / float(row.raw_close) if row.raw_close and row.raw_close > 0 else None
        )
        adjusted_points.append(
            ThresholdPricePoint(
                value=adjusted_close,
                high=float(row.raw_high) * adjustment_factor
                if adjustment_factor is not None and row.raw_high > 0
                else None,
                low=float(row.raw_low) * adjustment_factor
                if adjustment_factor is not None and row.raw_low > 0
                else None,
                pct_change=None,
            )
        )
    risk_data_eligible = len(adjusted_points) >= 30
    threshold_context: dict[str, Any] = {}
    if risk_data_eligible:
        threshold_context = dynamic_threshold_context(
            asset_bucket=theme_profile.asset_bucket,
            theme_group=theme_profile.theme_group,
            points=adjusted_points,
            today_return=returns[-1] if returns else None,
            return_5d=return_5d,
            return_20d=return_20d,
            return_60d=return_60d,
            volatility_20d=realized_vol,
            max_drawdown_60d=max_drawdown_60d,
            distance_to_ma5=None,
            premium_discount_pct=(
                intraday_snapshot.premium_discount_pct if intraday_snapshot else None
            ),
            holding_state={
                "position_id": position.id,
                "asset_code": position.asset_code,
                "tracking_start_date": tracking_start_date(position).isoformat()
                if tracking_start_date(position)
                else None,
            },
        )
    persisted_protection = dict(
        (position.exit_state_json or {}).get(ETF_PROFIT_PROTECTION_STATE_KEY) or {}
    )
    profit_thresholds = (
        derive_etf_profit_thresholds(
            asset_bucket=theme_profile.asset_bucket,
            atr_pct=threshold_context.get("atr_style_20d_pct"),
            realized_volatility_pct=threshold_context.get("realized_vol_20d_pct"),
            median_abs_return_pct=threshold_context.get("median_abs_return_60d_pct"),
            persisted_entry_risk_unit_pct=persisted_protection.get("entry_risk_unit_pct"),
        )
        if risk_data_eligible
        else None
    )
    risk_data_eligible = risk_data_eligible and profit_thresholds is not None
    thresholds = dict(threshold_context.get("thresholds") or {})
    volatility_unit_pct = (
        profit_thresholds.entry_risk_unit_pct if profit_thresholds is not None else None
    )
    current_volatility_unit_pct = (
        profit_thresholds.current_risk_unit_pct if profit_thresholds is not None else None
    )
    hard_stop_pct = float(
        thresholds.get("hard_stop_pct", HARD_STOP_LOSS_PCT)
    )
    profit_start_pct = (
        profit_thresholds.profit_start_pct if profit_thresholds is not None else None
    )
    trailing_giveback_pct = (
        profit_thresholds.trailing_giveback_pct if profit_thresholds is not None else None
    )
    if volatility_unit_pct is None:
        volatility_bucket = "unavailable"
    elif volatility_unit_pct < 1.0:
        volatility_bucket = "low_volatility"
    elif volatility_unit_pct < 2.5:
        volatility_bucket = "mid_volatility"
    else:
        volatility_bucket = "high_volatility"
    approved_calibration = (
        await _approved_calibration_thresholds(
            session,
            bucket_candidates=[
                ("asset_bucket", theme_profile.asset_bucket or "unknown"),
                ("theme_group", theme_profile.theme_group or "unknown"),
                ("volatility", volatility_bucket),
                ("all", "all"),
            ],
            volatility_unit_pct=volatility_unit_pct,
        )
        if volatility_unit_pct is not None
        else None
    )
    threshold_source = "rule_dynamic_v2" if risk_data_eligible else "data_waiting"
    threshold_rule_version = ETF_DYNAMIC_THRESHOLD_VERSION
    calibration_run_id = None
    calibration_candidate_id = None
    calibration_bucket_key = None
    calibration_version = None
    calibration_execution_model = None
    calibration_contract_hash_value = None
    calibration_coverage_status = None
    if approved_calibration is not None:
        approved_thresholds = dict(approved_calibration["thresholds"])
        hard_stop_pct = float(approved_thresholds["hard_stop_pct"])
        profit_start_pct = float(approved_thresholds["profit_start_pct"])
        trailing_giveback_pct = float(approved_thresholds["trailing_giveback_pct"])
        threshold_source = "approved_calibration"
        threshold_rule_version = "approved_etf_exit_calibration"
        calibration_run_id = int(approved_calibration["run_id"])
        calibration_candidate_id = int(approved_calibration["candidate_id"])
        calibration_bucket_key = str(approved_calibration["bucket_key"])
        calibration_version = str(approved_calibration["calibration_version"])
        calibration_execution_model = str(approved_calibration["execution_model"])
        calibration_contract_hash_value = str(approved_calibration["contract_hash"])
        calibration_coverage_status = str(approved_calibration["coverage_status"])

    prices = [point.price for point in chart]
    ma5 = _mean_or_none(prices[-5:]) if len(prices) >= 5 else None
    ma10 = _mean_or_none(prices[-10:]) if len(prices) >= 10 else None
    return_5d_pct = (
        ((prices[-1] / prices[-6] - 1.0) * 100) if len(prices) >= 6 and prices[-6] else None
    )
    current_price = (
        prices[-1] if prices else intraday_snapshot.current_price if intraday_snapshot else None
    )
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
            liquidity_warnings.append(
                f"买卖价差约 {intraday_snapshot.spread_pct:.2f}%，成交成本可能变高。"
            )
        if intraday_snapshot.turnover is not None and intraday_snapshot.turnover < 30_000_000:
            liquidity_warnings.append(
                f"当前成交额约 {intraday_snapshot.turnover / 10_000:.0f} 万元，短线流动性偏弱。"
            )
        if (
            intraday_snapshot.premium_discount_pct is not None
            and abs(intraday_snapshot.premium_discount_pct) >= 0.8
        ):
            structure_warnings.append(
                f"折溢价约 {intraday_snapshot.premium_discount_pct:.2f}%，价格可能偏离基金净值。"
            )
        if intraday_snapshot.iopv is None:
            structure_warnings.append("暂无 IOPV，无法判断盘中价格相对净值是否偏贵。")

    if not risk_data_eligible:
        structure_warnings.append(
            f"只有 {len(adjusted_points)} 个可决策复权交易日，少于动态止盈要求的 30 日；不使用原始价格补算。"
        )
    threshold_reason = (
        str(threshold_context.get("reason") or "")
        if risk_data_eligible
        else "可决策 total-return-adjusted 日线不足，ETF 动态止盈等待数据。"
    )
    if profit_thresholds is not None:
        threshold_reason = (
            f"{threshold_reason} 止盈风险单位采用 ATR、实现波动率和绝对收益中位数的稳健中位数；"
            f"持仓风险单位冻结为 {profit_thresholds.entry_risk_unit_pct:.2f}%。"
        ).strip()

    return DynamicExitThresholdsOut(
        threshold_source=threshold_source,
        rule_version=threshold_rule_version,
        threshold_mode="dynamic" if risk_data_eligible else "data_waiting",
        asset_bucket=theme_profile.asset_bucket,
        calibration_run_id=calibration_run_id,
        calibration_candidate_id=calibration_candidate_id,
        calibration_bucket_key=calibration_bucket_key,
        calibration_version=calibration_version,
        calibration_execution_model=calibration_execution_model,
        calibration_contract_hash=calibration_contract_hash_value,
        calibration_coverage_status=calibration_coverage_status,
        volatility_unit_pct=_round_or_none(volatility_unit_pct),
        current_volatility_unit_pct=_round_or_none(current_volatility_unit_pct),
        entry_risk_unit_pct=_round_or_none(volatility_unit_pct),
        risk_data_eligible=risk_data_eligible,
        risk_data_reason_code=(
            "decision_eligible" if risk_data_eligible else "insufficient_adjusted_history"
        ),
        risk_price_basis="total_return_adjusted",
        risk_sample_count=len(adjusted_points),
        hard_stop_pct=_round_or_none(hard_stop_pct),
        profit_start_pct=_round_or_none(profit_start_pct),
        trailing_giveback_pct=_round_or_none(trailing_giveback_pct),
        trend_weakening=trend_weakening,
        explanation=[threshold_reason],
        liquidity_warnings=liquidity_warnings,
        structure_warnings=structure_warnings,
    )


def _exit_signal(
    *,
    alert_type: str | None = None,
    label: str = "暂无卖出/减仓提醒",
    level: str = "none",
    action_class: str = ACTION_CLASS_NONE,
    guard_state: str | None = None,
    guard_reasons: list[str] | None = None,
    threshold_context: dict[str, Any] | None = None,
    approved_for_live: bool = False,
    no_alert_reason: str | None = None,
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
        action_class=cast(Any, action_class),
        guard_state=guard_state,
        guard_reasons=guard_reasons or [],
        threshold_context=threshold_context or {},
        approved_for_live=approved_for_live,
        no_alert_reason=no_alert_reason,
        reason=reason_list[0] if reason_list else None,
        reasons=reason_list,
        email_eligible=email_eligible,
        email_eligibility_reason=email_eligibility_reason,
        data_reliability=data_reliability,
    )


def _ma5_close_break_metrics(
    evidence: AdjustedMa5CloseBreakEvidence | None,
) -> dict[str, Any]:
    if evidence is None:
        return {
            "ma5_close_break_decision_eligible": False,
            "ma5_close_break_reason_code": "not_applicable",
        }
    return {
        "ma5_close_break_decision_eligible": evidence.decision_eligible,
        "ma5_close_break_reason_code": evidence.reason_code,
        "ma5_close_break_condition_met": evidence.condition_met,
        "ma5_close_break_observation_new": evidence.observation_is_new,
        "ma5_close_break_should_alert": evidence.should_alert,
        "ma5_close_break": evidence.as_context(),
    }


def _ma5_close_break_exit_signal(
    evidence: AdjustedMa5CloseBreakEvidence | None,
) -> TrackedPositionExitSignal | None:
    if evidence is None or not evidence.should_alert:
        return None
    assert evidence.trade_date is not None
    assert evidence.adjusted_close is not None
    assert evidence.adjusted_ma5 is not None
    earliest = (
        evidence.earliest_execution_date.isoformat()
        if evidence.earliest_execution_date is not None
        else "下一合格交易日"
    )
    context = evidence.as_context()
    return _exit_signal(
        alert_type=ALERT_MA5_CLOSE_BREAK_EXIT,
        label="收盘跌破五日线退出提醒",
        level="urgent",
        action_class=ACTION_CLASS_ACTIONABLE_EXIT,
        reasons=[
            (
                f"{evidence.trade_date.isoformat()} 复权收盘价 "
                f"{evidence.adjusted_close:.4f} 低于同日复权 MA5 "
                f"{evidence.adjusted_ma5:.4f}。"
            ),
            f"信号仅按收盘确认，不使用盘中最低价；最早可执行日为 {earliest}。",
        ],
        threshold_context={"ma5_close_break": context},
        data_reliability=MA5_CLOSE_BREAK_ALERT_SOURCE,
    )


def _alert_type_label(alert_type: str) -> str:
    return {
        ALERT_EXIT_WATCH: "卖出/减仓提醒",
        ALERT_TAKE_PROFIT_WATCH: "止盈观察提醒",
        ALERT_TRAILING_TAKE_PROFIT: "盈利回吐提醒 / 卖出减仓提醒",
        ALERT_TREND_WEAKENING: "趋势警戒",
        ALERT_CONFIRMED_TREND_WEAKENING: "确认趋势转弱提醒",
        ALERT_HARD_STOP: "止损提醒",
        ALERT_MA5_CLOSE_BREAK_EXIT: "收盘跌破五日线退出提醒",
    }.get(alert_type, "网页风险提示")


def _should_send_email(alert_type: str) -> bool:
    return alert_type in EMAIL_ALERT_TYPES


def _action_class_for_alert_type(alert_type: str | None) -> str:
    if alert_type in {
        ALERT_HARD_STOP,
        ALERT_MA5_CLOSE_BREAK_EXIT,
        ALERT_TRAILING_TAKE_PROFIT,
        ALERT_CONFIRMED_TREND_WEAKENING,
        ALERT_EXIT_WATCH,
    }:
        return ACTION_CLASS_ACTIONABLE_EXIT
    if alert_type == ALERT_TAKE_PROFIT_WATCH:
        return ACTION_CLASS_SOFT_WATCH
    if alert_type in {ALERT_TREND_WEAKENING, ALERT_RISK_WARNING}:
        return ACTION_CLASS_GUARD_ONLY
    return ACTION_CLASS_NONE


def _intraday_snapshot_metadata_eligible(
    snapshot: TrackedEtfIntradaySnapshotOut | None,
) -> bool:
    return bool(
        snapshot is not None
        and snapshot.price_source == "intraday_quote"
        and snapshot.reliability_level in DECISION_ELIGIBLE_RELIABILITIES
        and not snapshot.is_stale
        and snapshot.email_eligible
        and snapshot.decision_eligible
        and snapshot.current_price is not None
    )


def _exit_execution_context(
    position: TrackedPosition,
    snapshot: TrackedEtfIntradaySnapshotOut | None,
) -> dict[str, Any]:
    state = dict(position.exit_state_json or {})
    thresholds = dict(state.get("dynamic_thresholds") or {})
    evidence = evaluate_exit_execution_evidence(
        signal_price=snapshot.current_price if snapshot is not None else None,
        bid_price=snapshot.bid_price if snapshot is not None else None,
        ask_price=snapshot.ask_price if snapshot is not None else None,
        entry_price=position.entry_price,
        hard_stop_pct=thresholds.get("hard_stop_pct"),
        quote_eligible=_intraday_snapshot_metadata_eligible(snapshot),
    )
    return evidence.as_context()


def _is_fresh_intraday_snapshot(snapshot: TrackedEtfIntradaySnapshotOut | None) -> bool:
    if not _intraday_snapshot_metadata_eligible(snapshot):
        return False
    assert snapshot is not None
    evidence = evaluate_exit_execution_evidence(
        signal_price=snapshot.current_price,
        bid_price=snapshot.bid_price,
        ask_price=snapshot.ask_price,
        entry_price=None,
        hard_stop_pct=None,
        quote_eligible=True,
    )
    return evidence.status == "observable"


def _data_reliability_for_position(
    position: TrackedPosition,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
) -> str:
    if position.asset_type == ASSET_TYPE_FUND:
        return "daily_nav"
    if intraday_snapshot is None:
        return RELIABILITY_DAILY_CLOSE
    return intraday_snapshot.reliability_level


def _ma5_daily_signal_eligible(position: TrackedPosition) -> bool:
    state = dict(position.exit_state_json or {})
    threshold_context = dict(state.get("exit_signal_threshold_context") or {})
    evidence = dict(threshold_context.get("ma5_close_break") or {})
    return bool(
        evidence.get("decision_eligible") is True
        and evidence.get("price_basis") == "total_return_adjusted"
        and evidence.get("condition_met") is True
        and evidence.get("trade_date")
        and evidence.get("should_alert") is True
    )


def _annotate_exit_signal_email_eligibility(
    position: TrackedPosition,
    signal: TrackedPositionExitSignal,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
) -> TrackedPositionExitSignal:
    reliability = signal.data_reliability or _data_reliability_for_position(
        position, intraday_snapshot
    )
    signal.data_reliability = reliability
    if signal.alert_type is None:
        signal.email_eligible = False
        signal.email_eligibility_reason = "暂无明确持仓处理信号。"
        return signal
    if signal.action_class in {ACTION_CLASS_GUARD_ONLY, ACTION_CLASS_DATA_WAITING}:
        signal.email_eligible = False
        signal.email_eligibility_reason = (
            signal.email_eligibility_reason or "这是风险警戒或等待数据状态，仅在网页展示。"
        )
        return signal
    if not _should_send_email(signal.alert_type):
        signal.email_eligible = False
        signal.email_eligibility_reason = "数据质量或结构提示只在网页展示，不发送邮件。"
        return signal
    ma5_context = dict(signal.threshold_context.get("ma5_close_break") or {})
    if (
        signal.alert_type in {ALERT_HARD_STOP, ALERT_MA5_CLOSE_BREAK_EXIT}
        and ma5_context.get("decision_eligible") is True
        and ma5_context.get("should_alert") is True
    ):
        signal.email_eligible = True
        signal.email_eligibility_reason = "使用同一交易日的可决策复权收盘价与复权 MA5，按收盘确认。"
        return signal
    if position.asset_type == ASSET_TYPE_ETF and signal.alert_type in {
        ALERT_TRAILING_TAKE_PROFIT,
        ALERT_TAKE_PROFIT_WATCH,
    }:
        protection_context = dict(signal.threshold_context.get("profit_protection") or {})
        if (
            signal.threshold_context.get("risk_data_eligible") is not True
            or signal.threshold_context.get("risk_price_basis")
            != "total_return_adjusted"
            or protection_context.get("data_eligible") is not True
        ):
            signal.email_eligible = False
            signal.email_eligibility_reason = (
                "动态止盈缺少可决策复权日线证据，仅在网页展示，不使用原始价格触发邮件。"
            )
            return signal
    if position.asset_type == ASSET_TYPE_ETF and not _is_fresh_intraday_snapshot(intraday_snapshot):
        signal.email_eligible = False
        signal.email_eligibility_reason = (
            "当前不是新鲜盘中行情，盘中邮件不会用日线兜底或旧行情触发。"
        )
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
    if position.asset_type != ASSET_TYPE_ETF:
        return True
    if decision.alert_type == ALERT_MA5_CLOSE_BREAK_EXIT:
        return _ma5_daily_signal_eligible(position)
    if decision.alert_type == ALERT_HARD_STOP and _ma5_daily_signal_eligible(position):
        return True
    if decision.alert_type in {ALERT_TRAILING_TAKE_PROFIT, ALERT_TAKE_PROFIT_WATCH}:
        protection = dict(
            (position.exit_state_json or {}).get(ETF_PROFIT_PROTECTION_STATE_KEY) or {}
        )
        if (
            protection.get("data_eligible") is not True
            or protection.get("risk_price_basis") != "total_return_adjusted"
        ):
            return False
    return _is_fresh_intraday_snapshot(intraday_snapshot)


def _web_only_message(alert_type: str) -> str:
    if alert_type == ALERT_RISK_WARNING:
        return "仅网页提示：这是数据质量或盘中结构提示，不是明确卖出/减仓信号。"
    if alert_type == ALERT_TREND_WEAKENING:
        return "仅网页提示：趋势转弱当前只是风险警戒，未被亏损、回吐或榜单转弱确认。"
    if alert_type == ALERT_TAKE_PROFIT_WATCH:
        return "仅网页提示：止盈观察用于提醒你关注利润，不是明确卖出/减仓信号。"
    return "仅网页提示：不是明确卖出/减仓信号。"


def _populate_etf_profit_protection_chart(
    chart: list[TrackedPositionChartPoint],
    *,
    profit_start_pct: float | None,
    trailing_giveback_pct: float | None,
    data_eligible: bool,
    effective_current_stop_pct: float | None,
) -> None:
    """Replay the V2 line forward without backfilling today's stop into history."""

    running_high: float | None = None
    running_stop: float | None = None
    armed = False
    for point in chart:
        if point.estimated_pnl_pct is None:
            point.trailing_stop_pnl_pct = None
            continue
        running_high = max(running_high, point.estimated_pnl_pct) if running_high is not None else point.estimated_pnl_pct
        decision = evaluate_long_profit_protection(
            current_profit_pct=point.estimated_pnl_pct,
            observed_high_water_profit_pct=running_high,
            profit_start_pct=profit_start_pct,
            trailing_giveback_pct=trailing_giveback_pct,
            data_eligible=data_eligible,
            persisted_high_water_profit_pct=running_high,
            persisted_trailing_stop_pnl_pct=running_stop,
            persisted_armed=armed,
        )
        armed = decision.state in {"armed", "triggered"}
        running_high = decision.high_water_profit_pct
        running_stop = decision.trailing_stop_pnl_pct
        point.trailing_stop_pnl_pct = _round_or_none(running_stop)
    if chart and effective_current_stop_pct is not None:
        # A persisted high may predate the bounded chart. Expose it only at the
        # current point instead of pretending it existed on every visible date.
        chart[-1].trailing_stop_pnl_pct = _round_or_none(
            max(chart[-1].trailing_stop_pnl_pct or effective_current_stop_pct, effective_current_stop_pct)
        )


def _performance_analysis(
    position: TrackedPosition,
    chart: list[TrackedPositionChartPoint],
    item: ShortResearchSignalItem | None,
    *,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None,
    dynamic_thresholds: DynamicExitThresholdsOut | None = None,
    ma5_close_break: AdjustedMa5CloseBreakEvidence | None = None,
) -> PositionAnalysis:
    start_date = tracking_start_date(position)
    ma5_signal = _ma5_close_break_exit_signal(ma5_close_break)
    ma5_metrics = _ma5_close_break_metrics(ma5_close_break)
    if not chart:
        exit_signal = ma5_signal or _exit_signal(
            reasons=["等待公开净值或 ETF 日线数据，暂不能计算卖出/减仓提醒。"],
            action_class=ACTION_CLASS_DATA_WAITING,
            no_alert_reason="等待公开净值或 ETF 日线数据。",
            data_reliability=RELIABILITY_MISSING
            if position.asset_type == ASSET_TYPE_ETF
            else "unavailable",
        )
        return PositionAnalysis(
            chart=[],
            exit_signal=_annotate_exit_signal_email_eligibility(
                position, exit_signal, intraday_snapshot
            ),
            max_profit_pct=None,
            profit_giveback_pct=None,
            holding_days=None,
            technical_metrics={"data_status": "数据不足", **ma5_metrics},
            intraday_snapshot=intraday_snapshot,
            dynamic_thresholds=dynamic_thresholds,
            ma5_close_break=ma5_close_break,
        )

    current_point = chart[-1]
    current_pnl_pct = current_point.estimated_pnl_pct
    pnl_points = [point for point in chart if point.estimated_pnl_pct is not None]
    if current_pnl_pct is None or not pnl_points:
        exit_signal = ma5_signal or _exit_signal(
            reasons=["缺少买入净值或估算份额，暂不能计算卖出/减仓提醒。"],
            action_class=ACTION_CLASS_DATA_WAITING,
            no_alert_reason="缺少买入净值或估算份额。",
            data_reliability=RELIABILITY_MISSING
            if position.asset_type == ASSET_TYPE_ETF
            else "unavailable",
        )
        return PositionAnalysis(
            chart=chart,
            exit_signal=_annotate_exit_signal_email_eligibility(
                position, exit_signal, intraday_snapshot
            ),
            max_profit_pct=None,
            profit_giveback_pct=None,
            holding_days=(current_point.date - start_date).days,
            technical_metrics={"data_status": "等待买入净值", **ma5_metrics},
            intraday_snapshot=intraday_snapshot,
            dynamic_thresholds=dynamic_thresholds,
            ma5_close_break=ma5_close_break,
        )

    high_point = max(pnl_points, key=lambda point: cast(float, point.estimated_pnl_pct))
    observed_max_profit_pct = cast(float, high_point.estimated_pnl_pct)
    prices = [point.price for point in chart]
    ma5 = _mean_or_none(prices[-5:]) if len(prices) >= 5 else None
    ma10 = _mean_or_none(prices[-10:]) if len(prices) >= 10 else None
    return_5d_pct = (
        ((prices[-1] / prices[-6] - 1.0) * 100) if len(prices) >= 6 and prices[-6] else None
    )
    dynamic_hard_stop_pct = dynamic_thresholds.hard_stop_pct if dynamic_thresholds else None
    dynamic_profit_start_pct = dynamic_thresholds.profit_start_pct if dynamic_thresholds else None
    dynamic_trailing_giveback_pct = (
        dynamic_thresholds.trailing_giveback_pct if dynamic_thresholds else None
    )
    hard_stop_pct = (
        dynamic_hard_stop_pct if dynamic_hard_stop_pct is not None else HARD_STOP_LOSS_PCT
    )
    profit_protection: LongProfitProtectionDecision | None = None
    if position.asset_type == ASSET_TYPE_ETF:
        profit_start_pct = dynamic_profit_start_pct
        profit_data_eligible = bool(
            dynamic_thresholds and dynamic_thresholds.risk_data_eligible is True
        )
        persisted_protection = dict(
            (position.exit_state_json or {}).get(ETF_PROFIT_PROTECTION_STATE_KEY) or {}
        )
        profit_protection = evaluate_long_profit_protection(
            current_profit_pct=current_pnl_pct,
            observed_high_water_profit_pct=observed_max_profit_pct,
            profit_start_pct=profit_start_pct,
            trailing_giveback_pct=dynamic_trailing_giveback_pct,
            data_eligible=profit_data_eligible,
            persisted_high_water_profit_pct=persisted_protection.get(
                "high_water_profit_pct"
            ),
            persisted_trailing_stop_pnl_pct=persisted_protection.get(
                "trailing_stop_pnl_pct"
            ),
            persisted_armed=persisted_protection.get("state") in {"armed", "triggered"},
        )
        max_profit_pct = profit_protection.high_water_profit_pct
        profit_giveback_pct = profit_protection.profit_giveback_pct
        trailing_threshold = (
            profit_protection.trailing_giveback_pct
            if profit_protection.state in {"armed", "triggered"}
            else None
        )
        trailing_stop_pnl_pct = profit_protection.trailing_stop_pnl_pct
        take_profit_watch_threshold = profit_start_pct if profit_data_eligible else None
    else:
        profit_data_eligible = True
        max_profit_pct = observed_max_profit_pct
        profit_giveback_pct = max(0.0, max_profit_pct - current_pnl_pct)
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
        take_profit_watch_threshold = TAKE_PROFIT_WATCH_PCT
        trailing_stop_pnl_pct = (
            max_profit_pct - trailing_threshold if trailing_threshold is not None else None
        )

    for point in chart:
        point.is_entry = point.date == chart[0].date
        point.is_high = point.date == high_point.date
        point.is_current = point.date == current_point.date
    if position.asset_type == ASSET_TYPE_ETF:
        _populate_etf_profit_protection_chart(
            chart,
            profit_start_pct=profit_start_pct,
            trailing_giveback_pct=dynamic_trailing_giveback_pct,
            data_eligible=profit_data_eligible,
            effective_current_stop_pct=trailing_stop_pnl_pct,
        )
    else:
        for point in chart:
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
    trend_weakening = (
        dynamic_thresholds.trend_weakening if dynamic_thresholds else rule_trend_weakening
    )
    trend_confirmed_by_loss = current_pnl_pct <= min(-1.0, hard_stop_pct / 2)
    trend_confirmed_by_giveback = trailing_threshold is not None and profit_giveback_pct >= max(
        1.0, trailing_threshold * 0.75
    )
    trend_confirmed_by_ranking = current_label == "不适合短线"
    confirmed_trend_weakening = bool(
        trend_weakening
        and (trend_confirmed_by_loss or trend_confirmed_by_giveback or trend_confirmed_by_ranking)
    )
    trend_distances = []
    if ma5:
        trend_distances.append((current_point.price / ma5 - 1.0) * 100)
    if ma10:
        trend_distances.append((current_point.price / ma10 - 1.0) * 100)
    trend_weakening_distance_pct = min(trend_distances) if trend_distances else None
    distance_to_hard_stop_pct = current_pnl_pct - hard_stop_pct
    distance_to_profit_start_pct = (
        current_pnl_pct - take_profit_watch_threshold
        if take_profit_watch_threshold is not None
        else None
    )
    distance_to_trailing_giveback_pct = (
        profit_protection.distance_to_trailing_stop_pct
        if profit_protection is not None
        else trailing_threshold - profit_giveback_pct
        if trailing_threshold is not None
        else None
    )
    threshold_explanation = [
        item
        for item in (dynamic_thresholds.explanation if dynamic_thresholds else [])
        if item
    ]
    threshold_explanation.extend(
        [
            f"规则版本：{dynamic_thresholds.rule_version if dynamic_thresholds else 'fixed_exit_v1'}。",
            f"硬止损线 {hard_stop_pct:.2f}%，当前距离硬止损线 {distance_to_hard_stop_pct:.2f} 个百分点。",
        ]
    )
    if take_profit_watch_threshold is not None and distance_to_profit_start_pct is not None:
        threshold_explanation.append(
            f"止盈启动线 {take_profit_watch_threshold:.2f}%，当前距离启动线 {distance_to_profit_start_pct:.2f} 个百分点。"
        )
    elif position.asset_type == ASSET_TYPE_ETF:
        threshold_explanation.append("动态止盈等待至少30个可决策复权交易日，不使用原始价格补算。")
    if dynamic_thresholds and dynamic_thresholds.volatility_unit_pct is not None:
        threshold_explanation.append(
            f"动态线参考近阶段波动/回撤，波动单位约 {dynamic_thresholds.volatility_unit_pct:.2f}%。"
        )
    if trailing_threshold is not None and distance_to_trailing_giveback_pct is not None:
        threshold_explanation.append(
            f"允许从高点回吐 {trailing_threshold:.2f} 个百分点，实际保护线为 {trailing_stop_pnl_pct:.2f}%，当前距离保护线 {distance_to_trailing_giveback_pct:.2f} 个百分点。"
        )
    if dynamic_thresholds is not None:
        dynamic_thresholds = dynamic_thresholds.model_copy(
            update={
                "distance_to_hard_stop_pct": _round_or_none(distance_to_hard_stop_pct),
                "distance_to_profit_start_pct": _round_or_none(distance_to_profit_start_pct),
                "distance_to_trailing_giveback_pct": _round_or_none(
                    distance_to_trailing_giveback_pct
                ),
                "trend_weakening_distance_pct": _round_or_none(trend_weakening_distance_pct),
                "profit_protection_state": (
                    profit_protection.state if profit_protection is not None else None
                ),
                "high_water_profit_pct": _round_or_none(max_profit_pct),
                "trailing_stop_pnl_pct": _round_or_none(trailing_stop_pnl_pct),
                "distance_to_trailing_stop_pct": _round_or_none(
                    distance_to_trailing_giveback_pct
                ),
                "explanation": threshold_explanation,
            }
        )
    source_message = _quote_source_message(intraday_snapshot)
    ma5_text = f"{ma5:.4f}" if ma5 is not None else "暂无"
    ma10_text = f"{ma10:.4f}" if ma10 is not None else "暂无"
    return_5d_text = f"{return_5d_pct:.2f}%" if return_5d_pct is not None else "暂无"

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
        "confirmed_trend_weakening": confirmed_trend_weakening,
        "trend_guard_only": bool(trend_weakening and not confirmed_trend_weakening),
        "trend_confirmation_reasons": [
            reason
            for reason, enabled in [
                ("当前亏损已确认趋势风险", trend_confirmed_by_loss),
                ("盈利回吐已确认趋势风险", trend_confirmed_by_giveback),
                ("最新榜单标签已转弱", trend_confirmed_by_ranking),
            ]
            if enabled
        ],
        "threshold_source": dynamic_thresholds.threshold_source
        if dynamic_thresholds
        else "fixed_rule",
        "threshold_rule_version": dynamic_thresholds.rule_version
        if dynamic_thresholds
        else "fixed_exit_v1",
        "distance_to_hard_stop_pct": _round_or_none(distance_to_hard_stop_pct),
        "distance_to_profit_start_pct": _round_or_none(distance_to_profit_start_pct),
        "distance_to_trailing_giveback_pct": _round_or_none(distance_to_trailing_giveback_pct),
        "trend_weakening_distance_pct": _round_or_none(trend_weakening_distance_pct),
        "threshold_explanation": threshold_explanation,
        "price_source": intraday_snapshot.price_source if intraday_snapshot else "daily_close",
        "profit_protection": (
            profit_protection.as_context() if profit_protection is not None else None
        ),
        **ma5_metrics,
    }

    if current_pnl_pct <= hard_stop_pct:
        exit_signal = _exit_signal(
            alert_type=ALERT_HARD_STOP,
            label="硬止损提醒",
            level="urgent",
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            reasons=[f"当前估算亏损 {current_pnl_pct:.2f}%，已达到 -4% 的硬止损检查线。"],
        )
    elif ma5_signal is not None:
        exit_signal = ma5_signal
    elif (
        profit_protection is not None
        and profit_protection.triggered
        or profit_protection is None
        and trailing_threshold is not None
        and profit_giveback_pct >= trailing_threshold
    ):
        exit_signal = _exit_signal(
            alert_type=ALERT_TRAILING_TAKE_PROFIT,
            label="盈利回吐提醒",
            level="warning",
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            reasons=[
                f"最高盈利 {max_profit_pct:.2f}%，当前盈利 {current_pnl_pct:.2f}%，已从高点回吐 {profit_giveback_pct:.2f} 个百分点。",
                f"移动止盈阈值为 {trailing_threshold:.2f} 个百分点，建议人工考虑卖出或减仓。",
            ],
        )
    elif confirmed_trend_weakening:
        exit_signal = _exit_signal(
            alert_type=ALERT_CONFIRMED_TREND_WEAKENING,
            label="确认趋势转弱提醒",
            level="warning",
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            reasons=[
                f"最新价格 {current_point.price:.4f} 已低于短均线，5 日均线 {ma5_text}，10 日均线 {ma10_text}。",
                f"近 5 日收益 {return_5d_text}，且亏损、回吐或榜单转弱已确认趋势风险。",
            ],
        )
    elif trend_weakening:
        guard_reasons = [
            f"最新价格 {current_point.price:.4f} 已低于短均线，近 5 日收益 {return_5d_text}。",
            "趋势转弱当前只是风险警戒：可用于暂不加仓或降低关注强度，不单独触发卖出邮件。",
        ]
        exit_signal = _exit_signal(
            alert_type=ALERT_TREND_WEAKENING,
            label="趋势警戒（仅网页）",
            level="watch",
            action_class=ACTION_CLASS_GUARD_ONLY,
            guard_state="trend_weakening_unconfirmed",
            guard_reasons=guard_reasons,
            no_alert_reason="趋势转弱未被亏损、盈利回吐或榜单转弱确认。",
            reasons=guard_reasons,
        )
    elif (
        take_profit_watch_threshold is not None
        and current_pnl_pct >= take_profit_watch_threshold
    ):
        risk_text = "、".join(sorted(risk_flags.intersection(TAKE_PROFIT_RISKS))) or (
            "高位观察" if current_label == "高位观察" else "达到动态止盈观察线"
        )
        exit_signal = _exit_signal(
            alert_type=ALERT_TAKE_PROFIT_WATCH,
            label="止盈观察提醒",
            level="watch",
            action_class=ACTION_CLASS_SOFT_WATCH,
            reasons=[
                f"当前估算盈利 {current_pnl_pct:.2f}%，且触发 {risk_text}，说明利润已有但追高风险也在上升。",
                "这不是立即卖出指令，只是提醒你别贪最高点，可以开始考虑止盈或减仓。",
            ],
        )
    else:
        exit_signal = _exit_signal(
            reasons=["暂无卖出/减仓提醒；继续按每日公开数据观察。"],
            no_alert_reason="阈值未触发。",
        )

    if exit_signal.alert_type == ALERT_HARD_STOP:
        exit_signal.label = "硬止损提醒"
        exit_signal.reasons = [
            f"当前估算亏损 {abs(current_pnl_pct):.2f}%（盈亏 {current_pnl_pct:.2f}%），已触及硬止损线 {hard_stop_pct:.2f}%。",
            f"当前价 {current_point.price:.4f}；{source_message}",
        ]
        if (
            ma5_close_break is not None
            and ma5_close_break.condition_met is True
            and ma5_close_break.adjusted_close is not None
            and ma5_close_break.adjusted_ma5 is not None
        ):
            exit_signal.reasons.append(
                f"同时满足复权收盘破位：{ma5_close_break.adjusted_close:.4f} < 复权 MA5 {ma5_close_break.adjusted_ma5:.4f}。"
            )
        exit_signal.reason = exit_signal.reasons[0]
    elif exit_signal.alert_type == ALERT_TRAILING_TAKE_PROFIT and trailing_threshold is not None:
        exit_signal.label = "盈利回吐提醒"
        exit_signal.reasons = [
            f"最高盈利 {max_profit_pct:.2f}%，当前盈利 {current_pnl_pct:.2f}%，已从高点回吐 {profit_giveback_pct:.2f} 个百分点。",
            f"动态启动线 {profit_start_pct:.2f}%，动态回吐线 {trailing_threshold:.2f} 个百分点；当前价 {current_point.price:.4f}；{source_message}",
        ]
        exit_signal.reason = exit_signal.reasons[0]
    elif exit_signal.alert_type == ALERT_CONFIRMED_TREND_WEAKENING:
        exit_signal.label = "确认趋势转弱提醒"
        exit_signal.reasons = [
            f"最新价 {current_point.price:.4f} 已低于短均线，5 日均线 {ma5_text}，10 日均线 {ma10_text}。",
            f"近 5 日收益 {return_5d_text}，且亏损、回吐或榜单转弱已确认；{source_message}",
        ]
        exit_signal.reason = exit_signal.reasons[0]
    elif exit_signal.alert_type == ALERT_TAKE_PROFIT_WATCH:
        exit_signal.label = "止盈观察提醒"
        exit_signal.reasons = [
            f"当前估算盈利 {current_pnl_pct:.2f}%，已达到止盈观察启动线 {take_profit_watch_threshold:.2f}%。",
            f"当前价 {current_point.price:.4f}；{source_message} 这不是卖出指令，只提醒你检查是否需要止盈或减仓。",
        ]
        exit_signal.reason = exit_signal.reasons[0]
    elif (
        position.asset_type == ASSET_TYPE_ETF
        and dynamic_thresholds
        and not exit_signal.alert_type
        and (dynamic_thresholds.liquidity_warnings or dynamic_thresholds.structure_warnings)
    ):
        warnings = [*dynamic_thresholds.liquidity_warnings, *dynamic_thresholds.structure_warnings]
        exit_signal = _exit_signal(
            alert_type=ALERT_RISK_WARNING,
            label="盘中结构风险提醒",
            level="watch",
            action_class=ACTION_CLASS_GUARD_ONLY,
            guard_state="data_quality_warning",
            guard_reasons=warnings,
            reasons=warnings,
        )

    exit_signal.threshold_context = {
        "hard_stop_pct": _round_or_none(hard_stop_pct),
        "profit_start_pct": _round_or_none(profit_start_pct),
        "take_profit_watch_threshold_pct": _round_or_none(take_profit_watch_threshold),
        "trailing_giveback_pct": _round_or_none(trailing_threshold),
        "current_pnl_pct": _round_or_none(current_pnl_pct),
        "max_profit_pct": _round_or_none(max_profit_pct),
        "profit_giveback_pct": _round_or_none(profit_giveback_pct),
        "trend_weakening": bool(trend_weakening),
        "confirmed_trend_weakening": confirmed_trend_weakening,
        "threshold_source": dynamic_thresholds.threshold_source
        if dynamic_thresholds
        else "fixed_rule",
        "approved_for_live": bool(
            dynamic_thresholds and dynamic_thresholds.calibration_candidate_id
        ),
        "risk_data_eligible": bool(
            dynamic_thresholds and dynamic_thresholds.risk_data_eligible is True
        ),
        "risk_data_reason_code": (
            dynamic_thresholds.risk_data_reason_code if dynamic_thresholds else None
        ),
        "risk_price_basis": dynamic_thresholds.risk_price_basis if dynamic_thresholds else None,
        "profit_protection": (
            profit_protection.as_context() if profit_protection is not None else None
        ),
        "ma5_close_break": (ma5_close_break.as_context() if ma5_close_break is not None else None),
    }
    exit_signal.approved_for_live = bool(
        dynamic_thresholds and dynamic_thresholds.calibration_candidate_id
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
        profit_protection=profit_protection,
        ma5_close_break=ma5_close_break,
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
        estimated_pnl_pct = (
            estimated_pnl / cost_basis * 100 if estimated_pnl is not None and cost_basis else None
        )
    if position.asset_type == ASSET_TYPE_ETF:
        price_source = (
            intraday_snapshot.price_source if intraday_snapshot is not None else "unavailable"
        )
        decision_eligible = bool(intraday_snapshot is not None and intraday_snapshot.email_eligible)
        data_reliability = (
            "verified"
            if decision_eligible
            else intraday_snapshot.reliability_level
            if intraday_snapshot is not None
            else "unavailable"
        )
        display_only_reason = (
            None
            if decision_eligible
            else (
                intraday_snapshot.email_eligibility_reason
                if intraday_snapshot is not None
                else "暂无可用价格，不能触发邮件或计算持仓处理。"
            )
        )
    else:
        price_source = "daily_nav" if price is not None else "unavailable"
        data_reliability = "verified" if price is not None else "unavailable"
        decision_eligible = price is not None
        display_only_reason = (
            None if decision_eligible else "暂无公开净值，不能触发邮件或计算持仓处理。"
        )
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
    current_price, intraday = await latest_tracking_price(
        session, position.asset_type, position.asset_code
    )
    snapshot = _estimate_snapshot(position, current_price, intraday)
    if not signal_context_loaded:
        _run, item, report = await latest_signal_context(session, position)
    if item is None:
        return snapshot
    snapshot.current_label = item.conclusion
    snapshot.advisor_label = (
        report.action_label
        if report is not None
        else conservative_action_for_item(item, is_held=True)
    )
    snapshot.risk_flags = list(item.risk_flags_json or [])
    snapshot.explanation = (
        report.plain_summary
        if report is not None
        else str((item.rationale_json or {}).get("key_reason", ""))
    )
    return snapshot


async def position_chart(
    session: AsyncSession, position: TrackedPosition
) -> list[TrackedPositionChartPoint]:
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
) -> tuple[
    ShortResearchSignalRun | None, ShortResearchSignalItem | None, ShortResearchAdvisorReport | None
]:
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
    now: datetime | None = None,
    include_daily_close_rules: bool = True,
) -> PositionAnalysis:
    chart = await position_chart(session, position)
    intraday_snapshot = None
    dynamic_thresholds = None
    ma5_close_break = None
    if position.asset_type == ASSET_TYPE_ETF:
        _price, intraday_snapshot = await latest_tracking_price(
            session, position.asset_type, position.asset_code
        )
        if include_daily_close_rules:
            ma5_close_break = await load_adjusted_ma5_close_break_evidence(
                session, position, now=now
            )
    dynamic_thresholds = await dynamic_thresholds_for_position(
        session, position, chart, intraday_snapshot
    )
    return _performance_analysis(
        position,
        chart,
        item,
        intraday_snapshot=intraday_snapshot,
        dynamic_thresholds=dynamic_thresholds,
        ma5_close_break=ma5_close_break,
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
    if analysis.profit_protection is not None:
        previous = dict(state.get(ETF_PROFIT_PROTECTION_STATE_KEY) or {})
        protection = analysis.profit_protection.as_context()
        previous_high = previous.get("high_water_profit_pct")
        if isinstance(previous_high, int | float):
            protection["high_water_profit_pct"] = max(
                float(previous_high), analysis.profit_protection.high_water_profit_pct
            )
        previous_stop = previous.get("trailing_stop_pnl_pct")
        current_stop = analysis.profit_protection.trailing_stop_pnl_pct
        if isinstance(previous_stop, int | float):
            protection["trailing_stop_pnl_pct"] = (
                max(float(previous_stop), current_stop)
                if current_stop is not None
                else float(previous_stop)
            )
        if (
            analysis.profit_protection.state == "data_waiting"
            and previous.get("state") in {"armed", "triggered"}
        ):
            protection["state"] = "armed"
            protection["triggered"] = False
        protection["data_state"] = (
            "eligible" if analysis.profit_protection.data_eligible else "data_waiting"
        )
        thresholds = analysis.dynamic_thresholds
        if thresholds is not None:
            protection.update(
                {
                    "asset_bucket": thresholds.asset_bucket,
                    "entry_risk_unit_pct": thresholds.entry_risk_unit_pct
                    if thresholds.entry_risk_unit_pct is not None
                    else previous.get("entry_risk_unit_pct"),
                    "current_risk_unit_pct": thresholds.current_volatility_unit_pct,
                    "risk_data_eligible": thresholds.risk_data_eligible is True,
                    "risk_data_reason_code": thresholds.risk_data_reason_code,
                    "risk_price_basis": thresholds.risk_price_basis,
                    "risk_sample_count": thresholds.risk_sample_count,
                }
            )
        protection["updated_at"] = utcnow().isoformat()
        state[ETF_PROFIT_PROTECTION_STATE_KEY] = protection
    state["action_class"] = analysis.exit_signal.action_class
    state["guard_state"] = analysis.exit_signal.guard_state
    state["guard_reasons"] = analysis.exit_signal.guard_reasons
    state["no_alert_reason"] = analysis.exit_signal.no_alert_reason
    state["exit_signal_threshold_context"] = analysis.exit_signal.threshold_context
    if analysis.intraday_snapshot is not None:
        state["latest_price_source"] = analysis.intraday_snapshot.price_source
        if analysis.intraday_snapshot.quote_time is not None:
            state["latest_quote_time"] = analysis.intraday_snapshot.quote_time.isoformat()
    if analysis.ma5_close_break is not None and analysis.ma5_close_break.state_update is not None:
        state[MA5_CLOSE_BREAK_STATE_KEY] = dict(analysis.ma5_close_break.state_update)
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
    now = utcnow()
    position = await initialize_tracked_position(
        session,
        InitializeTrackedPositionCommand(
            owner_id=user_id,
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
            note=note,
            occurred_at=now,
            request_id=f"create:{asset_type}:{asset_code}:{now.isoformat()}",
        ),
    )
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
    next_entry_price = entry.price if entry else None
    next_entry_price_date = entry.price_date if entry else None
    next_estimated_shares = estimated_shares_for_position(
        buy_amount=position.buy_amount,
        confirmed_shares=position.confirmed_shares,
        entry=entry,
    )
    after = (
        effective_confirmed_nav_date,
        next_entry_price,
        next_entry_price_date,
        next_estimated_shares,
    )
    if after == before:
        return False
    now = utcnow()
    await apply_exposure_mutation(
        session,
        ExposureMutationCommand(
            owner_id=position.user_id,
            position_id=position.id,
            expected_exit_state_version=position.exit_state_version,
            intent=ExposureMutationIntent.SYSTEM_ESTIMATE,
            source=ExposureMutationSource.RECALCULATION,
            occurred_at=now,
            request_id=f"recalculate:{position.id}:{position.exit_state_version}",
            new_estimated_shares=next_estimated_shares,
            new_confirmed_nav_date=effective_confirmed_nav_date,
            new_entry_price=next_entry_price,
            new_entry_price_date=next_entry_price_date,
        ),
    )
    return True


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


async def _latest_etf_observation_target(
    session: AsyncSession,
    asset_code: str,
) -> EtfObservationPortfolioItem | None:
    snapshot = await session.scalar(
        select(EtfObservationPortfolioSnapshot)
        .where(
            EtfObservationPortfolioSnapshot.asset_type == ASSET_TYPE_ETF,
            EtfObservationPortfolioSnapshot.status == "success",
        )
        .order_by(
            EtfObservationPortfolioSnapshot.created_at.desc(),
            EtfObservationPortfolioSnapshot.id.desc(),
        )
        .limit(1)
    )
    if snapshot is None:
        return None
    return cast(
        EtfObservationPortfolioItem | None,
        await session.scalar(
            select(EtfObservationPortfolioItem)
            .where(
                EtfObservationPortfolioItem.snapshot_id == snapshot.id,
                EtfObservationPortfolioItem.asset_code == asset_code,
                EtfObservationPortfolioItem.item_type == "primary",
                EtfObservationPortfolioItem.target_weight > 0,
            )
            .order_by(
                EtfObservationPortfolioItem.target_weight.desc(),
                EtfObservationPortfolioItem.rank_order.asc(),
            )
            .limit(1)
        ),
    )


def _rank_bucket(rank_order: int | None) -> str | None:
    if rank_order is None or rank_order <= 0:
        return None
    if rank_order <= 5:
        return "top5"
    if rank_order <= 10:
        return "top10"
    if rank_order <= 20:
        return "top20"
    if rank_order <= 50:
        return "top50"
    return "outside"


async def latest_owner_confirmed_exit_execution_context(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
) -> tuple[str | None, date | None]:
    latest = (
        await session.execute(
            select(TrackedPositionActionExecution, TrackedPositionActionDecision)
            .join(
                TrackedPositionActionDecision,
                TrackedPositionActionDecision.id
                == TrackedPositionActionExecution.action_decision_id,
            )
            .where(
                TrackedPositionActionExecution.user_id == owner_id,
                TrackedPositionActionExecution.tracked_position_id == position_id,
                TrackedPositionActionExecution.execution_provenance == "owner_confirmed",
            )
            .order_by(
                TrackedPositionActionExecution.executed_at.desc(),
                TrackedPositionActionExecution.id.desc(),
            )
            .limit(1)
        )
    ).first()
    if latest is None:
        return None, None
    latest_execution, latest_action = latest
    if latest_action.target_remaining_fraction >= 1.0:
        return None, None
    first_fill_at = await session.scalar(
        select(func.min(TrackedPositionActionExecution.executed_at))
        .join(
            TrackedPositionActionDecision,
            TrackedPositionActionDecision.id == TrackedPositionActionExecution.action_decision_id,
        )
        .where(
            TrackedPositionActionExecution.user_id == owner_id,
            TrackedPositionActionExecution.tracked_position_id == position_id,
            TrackedPositionActionExecution.execution_provenance == "owner_confirmed",
            TrackedPositionActionDecision.position_episode_id == latest_action.position_episode_id,
            TrackedPositionActionDecision.action_cycle_id == latest_action.action_cycle_id,
        )
    )
    if first_fill_at is None:
        return None, None
    last_action = "exit" if latest_execution.resulting_normalized_quantity <= 1e-8 else "reduce"
    return last_action, first_fill_at.date()


def _exit_state_trend_weakening(position: TrackedPosition) -> bool:
    state = dict(position.exit_state_json or {})
    thresholds = dict(state.get("dynamic_thresholds") or {})
    return bool(thresholds.get("trend_weakening"))


async def position_sizing_recommendation(
    session: AsyncSession,
    user: User,
    position: TrackedPosition,
    snapshot: TrackedPositionSnapshot,
    exit_signal: TrackedPositionExitSignal | None = None,
    *,
    alert_type: str | None = None,
    trend_weakening: bool | None = None,
    owner_risk_context: EtfOwnerRiskContext | None = None,
    risk_counters: dict[str, int] | None = None,
) -> PositionSizingRecommendation:
    target_weight = None
    entry_timing_label = None
    target = None
    effective_alert_type: str | None
    if alert_type is not None:
        effective_alert_type = alert_type
    elif exit_signal is not None:
        effective_alert_type = exit_signal.alert_type
    else:
        effective_alert_type = None
    if position.asset_type == ASSET_TYPE_ETF and effective_alert_type is None:
        target = await _latest_etf_observation_target(session, position.asset_code)
        if target is not None:
            target_weight = target.target_weight
            entry_timing_label = target.entry_timing_label
    lifecycle_state = dict(position.exit_state_json or {})
    exposure_baseline = dict(lifecycle_state.get("exposure_baseline") or {})
    baseline_quantity_value = exposure_baseline.get("normalized_quantity")
    exposure_baseline_quantity = (
        float(baseline_quantity_value)
        if isinstance(baseline_quantity_value, (int, float))
        else None
    )
    recommendation = calculate_position_sizing(
        asset_type=position.asset_type,
        alert_type=effective_alert_type,
        current_market_value=snapshot.estimated_value,
        current_price=snapshot.current_price,
        etf_trading_capital=getattr(user, "etf_trading_capital", DEFAULT_ETF_TRADING_CAPITAL),
        capital_confirmed=getattr(user, "etf_trading_capital_confirmed_at", None) is not None,
        allow_full_exit=bool(getattr(user, "allow_full_exit", True)),
        target_portfolio_weight=target_weight,
        entry_timing_label=entry_timing_label,
        trend_weakening=_exit_state_trend_weakening(position)
        if trend_weakening is None
        else trend_weakening,
        exposure_baseline_quantity=exposure_baseline_quantity,
    )
    if (
        position.asset_type == ASSET_TYPE_ETF
        and effective_alert_type is None
        and recommendation.action == "hold"
    ):
        last_action, last_action_date = await latest_owner_confirmed_exit_execution_context(
            session,
            owner_id=position.user_id,
            position_id=position.id,
        )
        reentry = evaluate_reentry_state(
            last_action=last_action,
            last_action_date=last_action_date,
            today=date.today(),
            ranking_bucket=_rank_bucket(target.rank_order if target is not None else None),
            entry_timing_label=entry_timing_label,
            theme_trend=(target.metrics_json or {}).get("theme_trend")
            if target is not None
            else None,
            data_reliability=snapshot.data_reliability,
        )
        if reentry.state != "not_applicable":
            recommendation = replace(
                recommendation,
                action=reentry.action,
                label=reentry.label,
                reentry_state=reentry.state,
                reentry_reason=reentry.reason,
                reentry_rule_version=reentry.rule_version,
            )
    if position.asset_type == ASSET_TYPE_ETF:
        effective_owner_risk = owner_risk_context or await build_owner_etf_risk_context(
            session,
            user,
            now=utcnow(),
        )
        guarded = apply_owner_risk_guard(recommendation, effective_owner_risk)
        if (
            risk_counters is not None
            and recommendation.action in {"add", "reentry_candidate"}
            and guarded.action == "no_add"
        ):
            risk_counters["owner_risk_blocked_adds"] = (
                risk_counters.get("owner_risk_blocked_adds", 0) + 1
            )
        return guarded
    return recommendation


def _alert_threshold_context(
    position: TrackedPosition,
    alert: TrackedPositionAlert | None,
    decision: AlertDecision,
    position_sizing: PositionSizingRecommendation | None = None,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None,
) -> dict[str, Any]:
    state = dict(position.exit_state_json or {})
    dynamic_thresholds = dict(state.get("dynamic_thresholds") or {})
    profit_protection = dict(state.get(ETF_PROFIT_PROTECTION_STATE_KEY) or {})
    signal_thresholds = dict(state.get("exit_signal_threshold_context") or {})
    context = {
        "threshold_mode": dynamic_thresholds.get("threshold_source", "fixed_rule"),
        "rule_version": dynamic_thresholds.get("rule_version", "fixed_exit_v1"),
        "hard_stop_pct": dynamic_thresholds.get("hard_stop_pct"),
        "profit_start_pct": dynamic_thresholds.get("profit_start_pct"),
        "trailing_giveback_pct": dynamic_thresholds.get("trailing_giveback_pct"),
        "volatility_unit_pct": dynamic_thresholds.get("volatility_unit_pct"),
        "distance_to_hard_stop_pct": dynamic_thresholds.get("distance_to_hard_stop_pct"),
        "distance_to_profit_start_pct": dynamic_thresholds.get("distance_to_profit_start_pct"),
        "distance_to_trailing_giveback_pct": dynamic_thresholds.get(
            "distance_to_trailing_giveback_pct"
        ),
        "calibration_execution_model": dynamic_thresholds.get("calibration_execution_model"),
        "calibration_contract_hash": dynamic_thresholds.get("calibration_contract_hash"),
        "calibration_coverage_status": dynamic_thresholds.get("calibration_coverage_status"),
        "max_profit_pct": state.get("max_profit_pct"),
        "profit_giveback_pct": state.get("profit_giveback_pct"),
        "holding_days": state.get("holding_days"),
        "profit_protection": profit_protection,
        "risk_data_eligible": profit_protection.get("risk_data_eligible"),
        "risk_data_reason_code": profit_protection.get("risk_data_reason_code"),
        "risk_price_basis": profit_protection.get("risk_price_basis"),
        "explanation": dynamic_thresholds.get("explanation") or [],
        "alert_type": decision.alert_type,
        "alert_source": decision.alert_source,
        "action_class": _action_class_for_alert_type(decision.alert_type),
        "guard_state": state.get("guard_state"),
        "guard_reasons": state.get("guard_reasons") or [],
        "no_alert_reason": state.get("no_alert_reason"),
        "execution_risk": _exit_execution_context(position, intraday_snapshot),
        "ma5_close_break": signal_thresholds.get("ma5_close_break"),
    }
    if position_sizing is not None:
        context["position_sizing"] = position_sizing.as_context()
    if alert is not None:
        context.update(
            {
                "current_price": alert.current_price,
                "current_price_date": alert.current_price_date.isoformat()
                if alert.current_price_date
                else None,
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


def _legacy_alert_audit_outcome(row: TrackedPositionAlert) -> str:
    if row.email_status == "sent":
        return "sent"
    if row.email_status == "failed":
        return "failed"
    if row.suppression_status in {"web_only", "suppressed"}:
        return str(row.suppression_status)
    if row.email_status == "skipped":
        return "skipped"
    return row.email_status or "recorded"


def _audit_summary(
    *,
    outcome: str,
    alert_type: str,
    smtp_error_message: str | None = None,
    duplicate_reason: str | None = None,
    cooldown_reason: str | None = None,
) -> str:
    if outcome == "sent":
        return "已发送邮件提醒。"
    if outcome == "failed":
        return f"邮件发送失败：{smtp_error_message or '未知错误'}。"
    if outcome == "web_only":
        return _web_only_message(alert_type)
    if outcome == "suppressed":
        return duplicate_reason or "同类盘中提醒处于冷却期，本次不重复发邮件。"
    if outcome == "data_ineligible":
        return "当前数据不可用于邮件决策，仅在网页展示，不发送卖出/减仓邮件。"
    if outcome == "skipped":
        return cooldown_reason or smtp_error_message or "本次跳过邮件发送。"
    return "已记录提醒评估结果。"


def _quote_freshness_for_audit(
    position: TrackedPosition,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
    *,
    evaluation_mode: str,
) -> str:
    if position.asset_type == ASSET_TYPE_FUND:
        return "daily_nav"
    if intraday_snapshot is None:
        return "missing_intraday" if evaluation_mode == "intraday" else "daily_close"
    if _is_fresh_intraday_snapshot(intraday_snapshot):
        return RELIABILITY_FRESH_INTRADAY
    if intraday_snapshot.reliability_level:
        return intraday_snapshot.reliability_level
    return RELIABILITY_STALE_QUOTE if intraday_snapshot.is_stale else "intraday_display_only"


def _audit_decision_context(
    position: TrackedPosition,
    decision: AlertDecision,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None,
    *,
    evaluation_mode: str,
    outcome: str,
) -> dict[str, Any]:
    email_data_eligible = _decision_email_data_eligible(
        position,
        decision,
        intraday_snapshot,
        evaluation_mode=evaluation_mode,
    )
    email_eligible = _should_send_email(decision.alert_type) and email_data_eligible
    email_eligibility_reason = None
    if not _should_send_email(decision.alert_type):
        email_eligibility_reason = _web_only_message(decision.alert_type)
    elif not email_data_eligible:
        if decision.alert_type == ALERT_MA5_CLOSE_BREAK_EXIT:
            email_eligibility_reason = "可决策复权收盘或同日复权 MA5 证据不可用。"
        else:
            email_eligibility_reason = (
                "盘中提醒必须使用新鲜盘中行情；旧行情、日线价和估算价不会触发邮件。"
            )
    return {
        "evaluation_mode": evaluation_mode,
        "outcome": outcome,
        "action_class": _action_class_for_alert_type(decision.alert_type),
        "guard_state": dict(position.exit_state_json or {}).get("guard_state"),
        "email_eligible": email_eligible,
        "email_eligibility_reason": email_eligibility_reason,
        "alert_level": decision.alert_level,
        "reasons": list(decision.reasons),
        "risk_flags": list(decision.risk_flags),
        "data_reliability": (
            MA5_CLOSE_BREAK_ALERT_SOURCE
            if decision.alert_source == MA5_CLOSE_BREAK_ALERT_SOURCE
            else _data_reliability_for_position(position, intraday_snapshot)
        ),
        "quote_time": decision.quote_time.isoformat() if decision.quote_time else None,
        "consensus_status": intraday_snapshot.consensus_status if intraday_snapshot else None,
        "provider_count": intraday_snapshot.provider_count if intraday_snapshot else 0,
        "fresh_provider_count": intraday_snapshot.fresh_provider_count if intraday_snapshot else 0,
        "price_diff_abs": intraday_snapshot.price_diff_abs if intraday_snapshot else None,
        "price_diff_pct": intraday_snapshot.price_diff_pct if intraday_snapshot else None,
        "limitation_reason": intraday_snapshot.limitation_reason if intraday_snapshot else None,
        "execution_risk": _exit_execution_context(position, intraday_snapshot),
    }


def alert_audit_out(row: TrackedPositionAlertAudit) -> TrackedPositionAlertAuditOut:
    return TrackedPositionAlertAuditOut(
        id=row.id,
        tracked_position_id=row.tracked_position_id,
        tracked_position_alert_id=row.tracked_position_alert_id,
        outcome=row.outcome,
        signal_type=row.signal_type,
        alert_date=row.alert_date,
        alert_type=row.alert_type,
        trigger_label=row.trigger_label,
        data_source=row.data_source,
        quote_freshness=row.quote_freshness,
        threshold_context=dict(row.threshold_context_json or {}),
        decision_context=dict(row.decision_context_json or {}),
        recipient=row.recipient,
        duplicate_reason=row.duplicate_reason,
        cooldown_reason=row.cooldown_reason,
        smtp_result=row.smtp_result,
        smtp_error_message=row.smtp_error_message,
        quote_time=row.quote_time,
        created_at=row.created_at,
        audit_summary=_audit_summary(
            outcome=row.outcome,
            alert_type=row.alert_type,
            smtp_error_message=row.smtp_error_message,
            duplicate_reason=row.duplicate_reason,
            cooldown_reason=row.cooldown_reason,
        ),
    )


def legacy_alert_audit_out(row: TrackedPositionAlert) -> TrackedPositionAlertAuditOut:
    outcome = _legacy_alert_audit_outcome(row)
    threshold_context = dict(row.threshold_context_json or {})
    cooldown_reason = None
    duplicate_reason = None
    if row.suppression_status == "suppressed":
        duplicate_reason = "同一 ETF 同一盘中信号 30 分钟内不重复发。"
    elif row.email_status == "skipped" and row.alert_type == ALERT_TAKE_PROFIT_WATCH:
        cooldown_reason = "止盈观察只看真正发过邮件的记录，3 天内不重复发。"
    return TrackedPositionAlertAuditOut(
        id=row.id,
        tracked_position_id=row.tracked_position_id,
        tracked_position_alert_id=row.id,
        outcome=outcome,
        signal_type=row.alert_type,
        alert_date=row.alert_date,
        alert_type=row.alert_type,
        trigger_label=row.trigger_label,
        data_source=row.alert_source or "unknown",
        quote_freshness=str(threshold_context.get("data_reliability") or "unknown"),
        threshold_context=threshold_context,
        decision_context={
            "legacy_alert_row": True,
            "email_status": row.email_status,
            "suppression_status": row.suppression_status,
            "risk_flags": list(row.risk_flags_json or []),
            "reasons": list(row.reasons_json or []),
        },
        recipient=None,
        duplicate_reason=duplicate_reason,
        cooldown_reason=cooldown_reason,
        smtp_result=row.email_status if row.email_status in {"sent", "failed", "skipped"} else None,
        smtp_error_message=row.email_error_message,
        quote_time=row.quote_time,
        created_at=row.created_at,
        audit_summary=_audit_summary(
            outcome=outcome,
            alert_type=row.alert_type,
            smtp_error_message=row.email_error_message,
            duplicate_reason=duplicate_reason,
            cooldown_reason=cooldown_reason,
        ),
    )


async def _record_alert_audit(
    session: AsyncSession,
    position: TrackedPosition,
    decision: AlertDecision,
    signal_date: date,
    *,
    outcome: str,
    evaluation_mode: str,
    alert: TrackedPositionAlert | None = None,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None,
    duplicate_reason: str | None = None,
    cooldown_reason: str | None = None,
    smtp_result: str | None = None,
    smtp_error_message: str | None = None,
    recipient: str | None = None,
    dedupe_minutes: int | None = None,
    position_sizing: PositionSizingRecommendation | None = None,
) -> TrackedPositionAlertAudit:
    if dedupe_minutes is not None:
        existing = await session.scalar(
            select(TrackedPositionAlertAudit)
            .where(
                TrackedPositionAlertAudit.tracked_position_id == position.id,
                TrackedPositionAlertAudit.alert_type == decision.alert_type,
                TrackedPositionAlertAudit.outcome == outcome,
                TrackedPositionAlertAudit.created_at
                >= utcnow() - timedelta(minutes=dedupe_minutes),
            )
            .order_by(
                TrackedPositionAlertAudit.created_at.desc(), TrackedPositionAlertAudit.id.desc()
            )
        )
        if existing is not None:
            return existing
    data_source = (
        decision.alert_source
        if decision.alert_source == MA5_CLOSE_BREAK_ALERT_SOURCE
        else intraday_snapshot.price_source
        if intraday_snapshot
        else decision.alert_source or "unknown"
    )
    row = TrackedPositionAlertAudit(
        tracked_position_id=position.id,
        tracked_position_alert_id=alert.id if alert is not None else None,
        outcome=outcome,
        signal_type=decision.alert_type,
        alert_date=signal_date,
        alert_type=decision.alert_type,
        trigger_label=decision.trigger_label,
        data_source=data_source,
        quote_freshness=(
            MA5_CLOSE_BREAK_ALERT_SOURCE
            if decision.alert_source == MA5_CLOSE_BREAK_ALERT_SOURCE
            else _quote_freshness_for_audit(
                position,
                intraday_snapshot,
                evaluation_mode=evaluation_mode,
            )
        ),
        threshold_context_json=_alert_threshold_context(
            position,
            alert,
            decision,
            position_sizing,
            intraday_snapshot,
        ),
        decision_context_json=_audit_decision_context(
            position,
            decision,
            intraday_snapshot,
            evaluation_mode=evaluation_mode,
            outcome=outcome,
        ),
        recipient=recipient,
        duplicate_reason=duplicate_reason,
        cooldown_reason=cooldown_reason,
        smtp_result=smtp_result,
        smtp_error_message=smtp_error_message,
        quote_time=decision.quote_time
        or (intraday_snapshot.quote_time if intraday_snapshot else None),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def evaluate_alert_decision(
    session: AsyncSession,
    position: TrackedPosition,
) -> tuple[AlertDecision | None, date | None]:
    prepared = await prepare_alert_evaluation(session, position)
    return prepared.decision, prepared.signal_date


async def prepare_alert_evaluation(
    session: AsyncSession,
    position: TrackedPosition,
    *,
    evaluation_mode: str = "daily",
) -> PreparedAlertEvaluation:
    run, item, report = await latest_signal_context(session, position)
    analysis = await position_analysis(
        session,
        position,
        item=item,
        include_daily_close_rules=evaluation_mode != "intraday",
    )
    merge_exit_state(position, analysis)
    technical_signal = analysis.exit_signal if analysis.exit_signal.alert_type is not None else None
    if (
        item is not None
        and item.conclusion == "数据不足"
        and (
            technical_signal is None
            or technical_signal.alert_type not in {ALERT_HARD_STOP, ALERT_MA5_CLOSE_BREAK_EXIT}
        )
    ):
        state = dict(position.exit_state_json or {})
        state["evaluation_data_outcome"] = {
            "state": "data_waiting",
            "reason_code": "ranking_data_insufficient",
        }
        position.exit_state_json = state
        return PreparedAlertEvaluation(
            decision=None,
            signal_date=run.as_of_date if run is not None else date.today(),
            analysis=analysis,
            data_reason_code="ranking_data_insufficient",
        )
    if run is None and technical_signal is None:
        return PreparedAlertEvaluation(
            decision=None,
            signal_date=None,
            analysis=analysis,
            data_reason_code="no_signal_context",
        )

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
    if (
        technical_signal is not None
        and technical_signal.alert_type in {ALERT_HARD_STOP, ALERT_MA5_CLOSE_BREAK_EXIT}
        and analysis.ma5_close_break is not None
        and analysis.ma5_close_break.trade_date is not None
        and analysis.ma5_close_break.should_alert
    ):
        alert_source = MA5_CLOSE_BREAK_ALERT_SOURCE
        quote_time = None
        alert_date = analysis.ma5_close_break.trade_date
    else:
        alert_source = (
            analysis.intraday_snapshot.price_source if analysis.intraday_snapshot else "daily_close"
        )
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

    if technical_signal is not None and technical_signal.alert_type in {
        ALERT_HARD_STOP,
        ALERT_MA5_CLOSE_BREAK_EXIT,
    }:
        alert_type = cast(str, technical_signal.alert_type)
        trigger_label = technical_signal.label
        reasons = list(technical_signal.reasons)
        alert_level = technical_signal.level
    elif item is None:
        if technical_signal is None:
            return PreparedAlertEvaluation(
                decision=None,
                signal_date=alert_date,
                analysis=analysis,
                data_reason_code="threshold_not_triggered",
            )
        alert_type = cast(str, technical_signal.alert_type)
        trigger_label = technical_signal.label
        reasons = [
            *technical_signal.reasons,
            "该资产未进入最新短线榜单上下文，本提醒只基于你的持仓风控规则计算。",
        ]
        alert_level = technical_signal.level
    elif item.conclusion == "不适合短线":
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
        return PreparedAlertEvaluation(
            decision=None,
            signal_date=alert_date,
            analysis=analysis,
            data_reason_code="threshold_not_triggered",
        )
    else:
        alert_type = ALERT_EXIT_WATCH
        reasons = ["保守规则把这笔持仓标记为“退出观察”，建议人工检查是否卖出或减仓。"]

    if report is not None and report.plain_summary:
        reasons.append(report.plain_summary)
    return PreparedAlertEvaluation(
        decision=AlertDecision(
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
        signal_date=alert_date,
        analysis=analysis,
        data_reason_code="prepared_analysis",
    )


async def evaluate_alert_decision_v2(
    session: AsyncSession,
    position: TrackedPosition,
) -> tuple[AlertDecision | None, date | None]:
    prepared = await prepare_alert_evaluation(session, position)
    return prepared.decision, prepared.signal_date


async def create_alert_if_needed(
    session: AsyncSession,
    position: TrackedPosition,
    settings: Settings,
    *,
    evaluation_mode: str = "daily",
    prepared_evaluation: PreparedAlertEvaluation | None = None,
    liquidity_turnovers: tuple[float, ...] = (),
    liquidity_quote: Any | None = None,
    liquidity_inputs_loaded: bool = False,
    owner_risk_context: EtfOwnerRiskContext | None = None,
    risk_counters: dict[str, int] | None = None,
) -> tuple[TrackedPositionAlert | None, str]:
    prepared = prepared_evaluation or await prepare_alert_evaluation(
        session, position, evaluation_mode=evaluation_mode
    )
    decision, signal_date = prepared.decision, prepared.signal_date
    if decision is None or signal_date is None:
        if session.is_modified(position, include_collections=False):
            await session.commit()
        return None, "no_signal"

    if position.asset_type == ASSET_TYPE_ETF and prepared.analysis is not None:
        intraday_snapshot = prepared.analysis.intraday_snapshot
        current = (
            PriceSnapshot(intraday_snapshot.current_price, intraday_snapshot.trade_date)
            if intraday_snapshot is not None
            and intraday_snapshot.current_price is not None
            and intraday_snapshot.trade_date is not None
            else PriceSnapshot(
                prepared.analysis.chart[-1].price,
                prepared.analysis.chart[-1].date,
            )
            if prepared.analysis.chart
            else None
        )
    else:
        current, intraday_snapshot = await latest_tracking_price(
            session,
            position.asset_type,
            position.asset_code,
        )
    snapshot = _estimate_snapshot(position, current, intraday_snapshot)
    user = await session.get(User, position.user_id)
    assert user is not None
    position_sizing = await position_sizing_recommendation(
        session,
        user,
        position,
        snapshot,
        alert_type=decision.alert_type,
        owner_risk_context=owner_risk_context,
        risk_counters=risk_counters,
    )
    if not liquidity_inputs_loaded and position.asset_type == ASSET_TYPE_ETF:
        turnover_by_code, quote_by_code = await batch_etf_liquidity_inputs(
            session,
            [position],
        )
        liquidity_turnovers = turnover_by_code.get(position.asset_code, ())
        liquidity_quote = quote_by_code.get(position.asset_code)
    capacity = etf_liquidity_capacity_for_position(
        position,
        snapshot,
        position_sizing,
        daily_turnovers=liquidity_turnovers,
        quote=liquidity_quote,
    )
    if risk_counters is not None and capacity is not None:
        status_key = f"liquidity_capacity_{capacity.status}"
        risk_counters[status_key] = risk_counters.get(status_key, 0) + 1
        if capacity.side == "buy" and not capacity.entry_allowed:
            risk_counters["liquidity_blocked_adds"] = (
                risk_counters.get("liquidity_blocked_adds", 0) + 1
            )
        if capacity.side == "sell" and capacity.status != "ready":
            risk_counters["liquidity_stressed_exits"] = (
                risk_counters.get("liquidity_stressed_exits", 0) + 1
            )
    position_sizing = apply_etf_liquidity_capacity_guard(position_sizing, capacity)
    is_intraday_alert = (
        position.asset_type == ASSET_TYPE_ETF and decision.alert_source == "intraday_quote"
    )

    if not _decision_email_data_eligible(
        position,
        decision,
        intraday_snapshot,
        evaluation_mode=evaluation_mode,
    ):
        if session.is_modified(position, include_collections=False):
            await session.commit()
        await _record_alert_audit(
            session,
            position,
            decision,
            signal_date,
            outcome="data_ineligible",
            evaluation_mode=evaluation_mode,
            intraday_snapshot=intraday_snapshot,
            cooldown_reason="当前行情不是新鲜盘中行情，盘中邮件不会用日线兜底或旧行情触发。",
            dedupe_minutes=30,
            position_sizing=position_sizing,
        )
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
            await _record_alert_audit(
                session,
                position,
                decision,
                signal_date,
                outcome="suppressed",
                evaluation_mode=evaluation_mode,
                alert=recent_intraday,
                intraday_snapshot=intraday_snapshot,
                duplicate_reason="同一 ETF 同一盘中信号 30 分钟内不重复发。",
                dedupe_minutes=30,
                position_sizing=position_sizing,
            )
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
            await _record_alert_audit(
                session,
                position,
                decision,
                signal_date,
                outcome="suppressed",
                evaluation_mode=evaluation_mode,
                alert=existing,
                intraday_snapshot=intraday_snapshot,
                duplicate_reason="同一持仓同一天同类提醒已存在，本次不重复记录邮件。",
                dedupe_minutes=1440,
                position_sizing=position_sizing,
            )
            return existing, "deduplicated"

    if decision.alert_type == ALERT_TAKE_PROFIT_WATCH:
        recent_take_profit = await session.scalar(
            select(TrackedPositionAlert)
            .where(
                TrackedPositionAlert.tracked_position_id == position.id,
                TrackedPositionAlert.alert_type == ALERT_TAKE_PROFIT_WATCH,
                TrackedPositionAlert.alert_date
                >= signal_date - timedelta(days=TAKE_PROFIT_WATCH_COOLDOWN_DAYS),
                TrackedPositionAlert.email_status == "sent",
            )
            .order_by(TrackedPositionAlert.alert_date.desc(), TrackedPositionAlert.id.desc())
        )
        if recent_take_profit is not None:
            await _record_alert_audit(
                session,
                position,
                decision,
                signal_date,
                outcome="skipped",
                evaluation_mode=evaluation_mode,
                alert=recent_take_profit,
                intraday_snapshot=intraday_snapshot,
                cooldown_reason="止盈观察只看真正发过邮件的记录，3 天内不重复发。",
                dedupe_minutes=1440,
                position_sizing=position_sizing,
            )
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
        alert_source=(
            decision.alert_source
            if decision.alert_source == MA5_CLOSE_BREAK_ALERT_SOURCE
            else intraday_snapshot.price_source
            if intraday_snapshot
            else decision.alert_source
        ),
        suppression_status="sent_or_pending",
        email_status="pending",
    )
    alert.threshold_context_json = _alert_threshold_context(
        position,
        alert,
        decision,
        position_sizing,
        intraday_snapshot,
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
        await _record_alert_audit(
            session,
            position,
            decision,
            signal_date,
            outcome="web_only",
            evaluation_mode=evaluation_mode,
            alert=alert,
            intraday_snapshot=intraday_snapshot,
            smtp_result="skipped",
            position_sizing=position_sizing,
        )
        return alert, "web_only"

    user = await session.get(User, position.user_id)
    assert user is not None
    if not email_configured(user, settings):
        alert.email_status = "skipped"
        alert.email_error_message = "邮件通道未配置或未通过测试"
        await session.commit()
        await session.refresh(alert)
        await _record_alert_audit(
            session,
            position,
            decision,
            signal_date,
            outcome="skipped",
            evaluation_mode=evaluation_mode,
            alert=alert,
            intraday_snapshot=intraday_snapshot,
            smtp_result="skipped",
            smtp_error_message=alert.email_error_message,
            recipient=user.recipient_email,
            position_sizing=position_sizing,
        )
        return alert, "email_skipped"

    notifier = build_notifier_for_user(user, settings)
    try:
        await notifier.send_template(
            session,
            recipient=user.recipient_email,
            template_name="tracked_position_alert.html.j2",
            payload=_email_payload(
                position,
                alert,
                decision,
                position_sizing,
                intraday_snapshot,
            ),
        )
        alert.email_status = "sent"
        alert.sent_at = utcnow()
        await session.commit()
        await session.refresh(alert)
        await _record_alert_audit(
            session,
            position,
            decision,
            signal_date,
            outcome="sent",
            evaluation_mode=evaluation_mode,
            alert=alert,
            intraday_snapshot=intraday_snapshot,
            smtp_result="sent",
            recipient=user.recipient_email,
            position_sizing=position_sizing,
        )
        return alert, "email_sent"
    except Exception as exc:  # noqa: BLE001
        alert.email_status = "failed"
        alert.email_error_message = str(exc)
        await session.commit()
        await session.refresh(alert)
        await _record_alert_audit(
            session,
            position,
            decision,
            signal_date,
            outcome="failed",
            evaluation_mode=evaluation_mode,
            alert=alert,
            intraday_snapshot=intraday_snapshot,
            smtp_result="failed",
            smtp_error_message=str(exc),
            recipient=user.recipient_email,
            position_sizing=position_sizing,
        )
        return alert, "email_failed"


def _email_payload(
    position: TrackedPosition,
    alert: TrackedPositionAlert,
    decision: AlertDecision,
    position_sizing: PositionSizingRecommendation | None = None,
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None,
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
        "confirmed_nav_date": position.confirmed_nav_date.isoformat()
        if position.confirmed_nav_date
        else None,
        "confirmed_nav": position.confirmed_nav,
        "confirmed_shares": position.confirmed_shares,
        "buy_amount": position.buy_amount,
        "entry_price": position.entry_price,
        "entry_price_date": position.entry_price_date.isoformat()
        if position.entry_price_date
        else None,
        "current_price": alert.current_price,
        "current_price_date": alert.current_price_date.isoformat()
        if alert.current_price_date
        else None,
        "estimated_value": alert.estimated_value,
        "estimated_pnl": alert.estimated_pnl,
        "estimated_pnl_pct": alert.estimated_pnl_pct,
        "trigger_label": alert.trigger_label,
        "alert_level": alert.alert_level,
        "alert_source": alert.alert_source,
        "quote_time": alert.quote_time.isoformat() if alert.quote_time else None,
        "current_label": decision.signal_item.conclusion
        if decision.signal_item is not None
        else "未进入最新榜单",
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
        "position_sizing": position_sizing.as_context() if position_sizing is not None else None,
        "threshold_context": _alert_threshold_context(
            position,
            alert,
            decision,
            position_sizing,
            intraday_snapshot,
        ),
    }
