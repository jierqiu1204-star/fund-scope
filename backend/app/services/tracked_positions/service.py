from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
    TrackedPosition,
    TrackedPositionAlert,
    TradableEtf,
    User,
    utcnow,
)
from app.schemas.tracked_positions import (
    TrackedPositionAlertOut,
    TrackedPositionChartPoint,
    TrackedPositionSnapshot,
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
ALERT_EXIT_WATCH = "exit_watch"
ALERT_RISK_WARNING = "risk_warning"


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
) -> PriceSnapshot | None:
    if asset_type == ASSET_TYPE_FUND:
        fund_query = select(FundNavHistory).where(FundNavHistory.fund_code == asset_code)
        if on_or_before is not None:
            fund_query = fund_query.where(FundNavHistory.nav_date <= on_or_before)
        row = await session.scalar(fund_query.order_by(FundNavHistory.nav_date.desc()))
        return PriceSnapshot(row.nav, row.nav_date) if row is not None else None
    if asset_type == ASSET_TYPE_ETF:
        etf_query = select(EtfPriceHistory).where(EtfPriceHistory.etf_code == asset_code)
        if on_or_before is not None:
            etf_query = etf_query.where(EtfPriceHistory.trade_date <= on_or_before)
        row = await session.scalar(etf_query.order_by(EtfPriceHistory.trade_date.desc()))
        return PriceSnapshot(row.close, row.trade_date) if row is not None else None
    raise ValueError("资产类型只支持 fund 或 etf")


def _round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _estimate_snapshot(position: TrackedPosition, price: PriceSnapshot | None) -> TrackedPositionSnapshot:
    estimated_value: float | None = None
    estimated_pnl: float | None = None
    estimated_pnl_pct: float | None = None
    if price is not None and position.estimated_shares:
        estimated_value = position.estimated_shares * price.price
        estimated_pnl = estimated_value - position.buy_amount
        estimated_pnl_pct = estimated_pnl / position.buy_amount * 100 if position.buy_amount else None
    return TrackedPositionSnapshot(
        current_price=_round_or_none(price.price, 6) if price else None,
        current_price_date=price.price_date if price else None,
        estimated_value=_round_or_none(estimated_value),
        estimated_pnl=_round_or_none(estimated_pnl),
        estimated_pnl_pct=_round_or_none(estimated_pnl_pct),
    )


async def current_snapshot(session: AsyncSession, position: TrackedPosition) -> TrackedPositionSnapshot:
    snapshot = _estimate_snapshot(
        position,
        await latest_price(session, position.asset_type, position.asset_code),
    )
    run = await latest_signal_run(session)
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
    points: list[tuple[date, float]]
    if position.asset_type == ASSET_TYPE_FUND:
        fund_rows = (
            await session.scalars(
                select(FundNavHistory)
                .where(
                    FundNavHistory.fund_code == position.asset_code,
                    FundNavHistory.nav_date >= position.buy_date,
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
                    EtfPriceHistory.trade_date >= position.buy_date,
                )
                .order_by(EtfPriceHistory.trade_date.asc())
            )
        ).all()
        points = [(row.trade_date, row.close) for row in etf_rows]
    chart: list[TrackedPositionChartPoint] = []
    for point_date, price in points[-240:]:
        estimated_value = position.estimated_shares * price if position.estimated_shares else None
        pnl_pct = (
            (estimated_value - position.buy_amount) / position.buy_amount * 100
            if estimated_value is not None and position.buy_amount
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


async def create_position(
    session: AsyncSession,
    *,
    asset_type: str,
    asset_code: str,
    buy_amount: float,
    buy_date: date,
    note: str | None = None,
) -> TrackedPosition:
    asset_name = await resolve_asset_name(session, asset_type, asset_code)
    entry = await latest_price(session, asset_type, asset_code, on_or_before=buy_date)
    position = TrackedPosition(
        asset_type=asset_type,
        asset_code=asset_code,
        asset_name=asset_name,
        buy_date=buy_date,
        buy_amount=round(buy_amount, 2),
        entry_price=entry.price if entry else None,
        entry_price_date=entry.price_date if entry else None,
        estimated_shares=(buy_amount / entry.price) if entry and entry.price else None,
        status=ACTIVE_STATUS,
        note=note,
    )
    session.add(position)
    await session.commit()
    await session.refresh(position)
    return position


async def recalculate_entry(session: AsyncSession, position: TrackedPosition) -> None:
    entry = await latest_price(session, position.asset_type, position.asset_code, on_or_before=position.buy_date)
    position.entry_price = entry.price if entry else None
    position.entry_price_date = entry.price_date if entry else None
    position.estimated_shares = (position.buy_amount / entry.price) if entry and entry.price else None
    position.updated_at = utcnow()


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
        email_status=row.email_status,
        email_error_message=row.email_error_message,
        sent_at=row.sent_at,
        created_at=row.created_at,
    )


async def evaluate_alert_decision(
    session: AsyncSession,
    position: TrackedPosition,
) -> tuple[AlertDecision | None, date | None]:
    run = await latest_signal_run(session)
    if run is None:
        return None, None
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
        return None, run.as_of_date
    reports = await latest_reports_by_asset(session, run.id)
    report = reports.get((position.asset_type, position.asset_code))
    trigger_label = report.action_label if report is not None else conservative_action_for_item(item, is_held=True)
    risk_flags = list(item.risk_flags_json or [])
    exit_risks = sorted(set(risk_flags).intersection(EXIT_RISKS))
    if trigger_label != ACTION_EXIT and not exit_risks:
        return None, run.as_of_date

    if item.conclusion in {"不适合短线", "数据不足"}:
        alert_type = ALERT_EXIT_WATCH
        reasons = [f"短线研究标签变为“{item.conclusion}”，不再适合作为短线持有观察对象。"]
    elif exit_risks:
        alert_type = ALERT_RISK_WARNING
        reasons = [f"触发明显风险标签：{'、'.join(exit_risks)}。"]
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


async def create_alert_if_needed(
    session: AsyncSession,
    position: TrackedPosition,
    settings: Settings,
) -> tuple[TrackedPositionAlert | None, str]:
    decision, signal_date = await evaluate_alert_decision(session, position)
    if decision is None or signal_date is None:
        return None, "no_signal"
    existing = await session.scalar(
        select(TrackedPositionAlert).where(
            TrackedPositionAlert.tracked_position_id == position.id,
            TrackedPositionAlert.alert_date == signal_date,
            TrackedPositionAlert.alert_type == decision.alert_type,
        )
    )
    if existing is not None:
        return existing, "deduplicated"

    current = await latest_price(session, position.asset_type, position.asset_code)
    snapshot = _estimate_snapshot(position, current)
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
        email_status="pending",
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)

    user = await session.get(User, 1)
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
    title_prefix = "退出观察提醒" if alert.alert_type == ALERT_EXIT_WATCH else "风险提醒"
    return {
        "title": f"FundScope {title_prefix}：{position.asset_name}",
        "alert_title": title_prefix,
        "asset_name": position.asset_name,
        "asset_code": position.asset_code,
        "asset_type": "ETF" if position.asset_type == ASSET_TYPE_ETF else "基金",
        "buy_date": position.buy_date.isoformat(),
        "buy_amount": position.buy_amount,
        "entry_price": position.entry_price,
        "entry_price_date": position.entry_price_date.isoformat() if position.entry_price_date else None,
        "current_price": alert.current_price,
        "current_price_date": alert.current_price_date.isoformat() if alert.current_price_date else None,
        "estimated_value": alert.estimated_value,
        "estimated_pnl": alert.estimated_pnl,
        "estimated_pnl_pct": alert.estimated_pnl_pct,
        "trigger_label": alert.trigger_label,
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
