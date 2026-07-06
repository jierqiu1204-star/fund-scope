from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import pstdev
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfExitSignalCredibilityEvent,
    EtfExitSignalCredibilityItem,
    EtfExitSignalCredibilityRun,
    EtfIntradayQuote,
    EtfPriceHistory,
    EtfThemeProfile,
    ShortResearchSignalItem,
    TradableEtf,
    utcnow,
)
from app.services.short_research.dynamic_thresholds import clamp
from app.services.short_research.etf_exit_policy_validation import (
    evidence_status_for_policy,
    kpi_summary_for_item,
    policy_class_for_signal,
    policy_class_label,
    recommended_usage_for_policy,
    strong_conclusion_allowed,
)
from app.services.short_research.service import has_available_opportunity_score, latest_signal_run

SIGNAL_VERSION = "short_research_v1"
EXIT_RULE_VERSION = "risk_alerts_v1"
CREDIBILITY_RULE_VERSION = "etf_exit_signal_credibility_v1"
EXECUTION_INTRADAY = "intraday_alert"
EXECUTION_DAILY = "daily_close"
UNIVERSE_SCOPE_LATEST_OPPORTUNITY_TOP = "latest_opportunity_top"

EXIT_SIGNALS = (
    "hard_stop",
    "trailing_take_profit",
    "trend_weakening",
    "take_profit_watch",
    "exit_watch",
)
FORWARD_WINDOWS = (1, 3, 5, 10, 20)
MIN_SIGNAL_SAMPLES = 8
EVENT_SAMPLE_LIMIT = 5

_BUCKET_VOL_LIMITS: dict[str, tuple[float, float]] = {
    "bond": (0.3, 1.2),
    "money": (0.1, 0.6),
    "broad_base": (0.6, 2.5),
    "dividend": (0.6, 2.2),
    "equity": (0.8, 3.5),
    "cross_border": (1.0, 4.5),
    "commodity": (1.0, 4.0),
    "unknown": (0.8, 3.0),
}


@dataclass(frozen=True)
class CredibilityPricePoint:
    trade_date: date
    price: float
    quote_time: datetime | None = None


@dataclass(frozen=True)
class CredibilitySeries:
    code: str
    name: str
    asset_bucket: str
    theme_group: str
    points: list[CredibilityPricePoint]


@dataclass(frozen=True)
class CredibilityUniverse:
    codes: list[str]
    metadata: dict[str, Any]
    score_by_code: dict[str, float]


@dataclass(frozen=True)
class ExitSignalEvent:
    code: str
    name: str
    asset_bucket: str
    theme_group: str
    signal_type: str
    point_index: int
    signal_date: date
    signal_time: datetime | None
    signal_price: float
    thresholds: dict[str, float]
    context: dict[str, Any]
    windows: dict[int, dict[str, Any]]


def contract_hash(*, execution_model: str, data_window: dict[str, Any]) -> str:
    payload = {
        "signal_version": SIGNAL_VERSION,
        "exit_rule_version": EXIT_RULE_VERSION,
        "credibility_rule_version": CREDIBILITY_RULE_VERSION,
        "execution_model": execution_model,
        "data_window": data_window,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def volatility_unit_pct(points: list[CredibilityPricePoint], asset_bucket: str) -> float:
    prices = [point.price for point in points if point.price > 0]
    returns = [prices[index] / prices[index - 1] - 1.0 for index in range(1, len(prices)) if prices[index - 1] > 0]
    recent = returns[-20:]
    raw = pstdev(recent) * 100 if len(recent) > 1 else 1.2
    lower, upper = _BUCKET_VOL_LIMITS.get(asset_bucket, _BUCKET_VOL_LIMITS["unknown"])
    return clamp(raw, lower, upper)


def thresholds_for_points(points: list[CredibilityPricePoint], asset_bucket: str) -> dict[str, float]:
    volatility_pct = volatility_unit_pct(points, asset_bucket)
    return {
        "volatility_unit_pct": round(volatility_pct, 4),
        "hard_stop_pct": -clamp(1.5 * volatility_pct, 1.2, 5.5),
        "profit_start_pct": clamp(1.1 * volatility_pct, 3.0, 4.0),
        "trailing_giveback_pct": clamp(0.65 * volatility_pct, 1.8, 2.5),
        "take_profit_watch_pct": max(3.0, clamp(1.1 * volatility_pct, 3.0, 4.0)),
    }


def _forward_window(
    points: list[CredibilityPricePoint],
    index: int,
    horizon_days: int,
) -> dict[str, Any]:
    signal_price = points[index].price
    future_trade_dates: list[date] = []
    future_points: list[CredibilityPricePoint] = []
    for point in points[index + 1 :]:
        if point.trade_date not in future_trade_dates:
            future_trade_dates.append(point.trade_date)
        if len(future_trade_dates) > horizon_days:
            break
        future_points.append(point)

    if not future_points or len(future_trade_dates) < horizon_days or signal_price <= 0:
        return {
            "outcome": "insufficient_future_window",
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
        }

    final_price = future_points[-1].price
    returns = [point.price / signal_price - 1.0 for point in future_points if point.price > 0]
    return {
        "outcome": "neutral",
        "forward_return": final_price / signal_price - 1.0,
        "max_favorable_return": max(returns) if returns else None,
        "max_adverse_return": min(returns) if returns else None,
    }


def classify_outcome(signal_type: str, window: dict[str, Any], volatility_pct: float) -> str:
    if window.get("outcome") == "insufficient_future_window":
        return "insufficient_future_window"

    favorable = float(window.get("max_favorable_return") or 0.0) * 100
    adverse = float(window.get("max_adverse_return") or 0.0) * 100
    forward_return = float(window.get("forward_return") or 0.0) * 100
    material_move = max(1.2, volatility_pct)

    if signal_type in {"hard_stop", "trend_weakening", "exit_watch"}:
        if adverse <= -material_move and forward_return <= 0:
            return "success_avoid_loss"
        if favorable >= material_move and forward_return > 0:
            return "false_stop"
        return "neutral"

    if adverse <= -material_move:
        return "success_avoid_loss"
    if favorable >= material_move:
        return "sold_too_early"
    return "neutral"


def _event_windows(
    points: list[CredibilityPricePoint],
    index: int,
    signal_type: str,
    volatility_pct: float,
) -> dict[int, dict[str, Any]]:
    windows: dict[int, dict[str, Any]] = {}
    for horizon in FORWARD_WINDOWS:
        window = _forward_window(points, index, horizon)
        outcome = classify_outcome(signal_type, window, volatility_pct)
        windows[horizon] = {**window, "outcome": outcome}
    return windows


def generate_exit_events(series: CredibilitySeries) -> list[ExitSignalEvent]:
    points = [point for point in series.points if point.price > 0]
    if len(points) < 30:
        return []

    events: list[ExitSignalEvent] = []
    cooldown_until: dict[str, int] = {}
    watch_alert_active = False
    for index in range(20, len(points) - 1):
        recent = points[max(0, index - 20) : index + 1]
        entry_price = recent[0].price
        current = points[index].price
        previous = points[index - 1].price
        if entry_price <= 0 or previous <= 0:
            continue

        thresholds = thresholds_for_points(recent, series.asset_bucket)
        profit_pct = (current / entry_price - 1.0) * 100
        peak_price = max(point.price for point in recent)
        peak_profit_pct = (peak_price / entry_price - 1.0) * 100
        giveback_pct = peak_profit_pct - profit_pct
        five_return_pct = (current / points[max(0, index - 5)].price - 1.0) * 100 if points[max(0, index - 5)].price > 0 else 0.0
        ma10 = sum(point.price for point in recent[-10:]) / min(len(recent), 10)
        signal_type: str | None = None

        if profit_pct <= thresholds["hard_stop_pct"]:
            signal_type = "hard_stop"
        elif (
            peak_profit_pct >= thresholds["profit_start_pct"]
            and giveback_pct >= thresholds["trailing_giveback_pct"]
        ):
            signal_type = "trailing_take_profit"
        elif current < previous and current < ma10 and five_return_pct < 0:
            signal_type = "trend_weakening"
        elif profit_pct >= thresholds["take_profit_watch_pct"] and not watch_alert_active:
            signal_type = "take_profit_watch"
            watch_alert_active = True
        elif current < ma10 and five_return_pct <= -thresholds["volatility_unit_pct"]:
            signal_type = "exit_watch"

        if signal_type is None:
            if profit_pct < thresholds["take_profit_watch_pct"] * 0.5:
                watch_alert_active = False
            continue
        if cooldown_until.get(signal_type, -1) > index:
            continue

        volatility_pct = thresholds["volatility_unit_pct"]
        events.append(
            ExitSignalEvent(
                code=series.code,
                name=series.name,
                asset_bucket=series.asset_bucket,
                theme_group=series.theme_group,
                signal_type=signal_type,
                point_index=index,
                signal_date=points[index].trade_date,
                signal_time=points[index].quote_time,
                signal_price=current,
                thresholds=thresholds,
                context={
                    "profit_pct": round(profit_pct, 4),
                    "peak_profit_pct": round(peak_profit_pct, 4),
                    "giveback_pct": round(giveback_pct, 4),
                    "five_return_pct": round(five_return_pct, 4),
                    "ma10": round(ma10, 6),
                    "credibility_rule_version": CREDIBILITY_RULE_VERSION,
                },
                windows=_event_windows(points, index, signal_type, volatility_pct),
            )
        )
        cooldown_until[signal_type] = index + max(FORWARD_WINDOWS)
    return events


def _primary_window(event: ExitSignalEvent) -> dict[str, Any]:
    return event.windows.get(5) or next(iter(event.windows.values()))


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize_events(events: list[ExitSignalEvent], *, group_type: str, group_key: str, signal_type: str) -> dict[str, Any]:
    primary = [_primary_window(event) for event in events]
    scored = [item for item in primary if item.get("outcome") != "insufficient_future_window"]
    sample_count = len(scored)
    success_count = sum(1 for item in scored if item.get("outcome") == "success_avoid_loss")
    false_count = sum(1 for item in scored if item.get("outcome") == "false_stop")
    sold_early_count = sum(1 for item in scored if item.get("outcome") == "sold_too_early")
    adverse = [float(item["max_adverse_return"]) for item in scored if item.get("max_adverse_return") is not None]
    favorable = [float(item["max_favorable_return"]) for item in scored if item.get("max_favorable_return") is not None]
    forward = [float(item["forward_return"]) for item in scored if item.get("forward_return") is not None]
    evidence_level = "样本不足"
    if sample_count >= MIN_SIGNAL_SAMPLES * 3:
        evidence_level = "样本较充分"
    elif sample_count >= MIN_SIGNAL_SAMPLES:
        evidence_level = "样本有限"

    return {
        "signal_type": signal_type,
        "group_type": group_type,
        "group_key": group_key,
        "evidence_level": evidence_level,
        "sample_count": sample_count,
        "success_avoidance_rate": _rate(success_count, sample_count),
        "false_stop_rate": _rate(false_count, sample_count),
        "sold_too_early_rate": _rate(sold_early_count, sample_count),
        "avg_avoided_drawdown": sum(adverse) / len(adverse) if adverse else None,
        "avg_missed_upside": sum(favorable) / len(favorable) if favorable else None,
        "avg_forward_return": sum(forward) / len(forward) if forward else None,
        "metrics": {
            "window_days": 5,
            "forward_windows": list(FORWARD_WINDOWS),
            "success_count": success_count,
            "false_stop_count": false_count,
            "sold_too_early_count": sold_early_count,
            "insufficient_future_window_count": len(primary) - sample_count,
            "raw_event_count": len(events),
        },
    }


def _group_events(events: list[ExitSignalEvent]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for signal in EXIT_SIGNALS:
        signal_events = [event for event in events if event.signal_type == signal]
        summaries.append(summarize_events(signal_events, group_type="signal", group_key=signal, signal_type=signal))

    theme_keys = sorted({event.theme_group for event in events if event.theme_group and event.theme_group != "unknown"})
    for theme in theme_keys:
        theme_events = [event for event in events if event.theme_group == theme]
        if len(theme_events) < MIN_SIGNAL_SAMPLES:
            continue
        for signal in EXIT_SIGNALS:
            signal_events = [event for event in theme_events if event.signal_type == signal]
            if len(signal_events) >= MIN_SIGNAL_SAMPLES:
                summaries.append(
                    summarize_events(signal_events, group_type="theme_group", group_key=theme, signal_type=signal)
                )
    return summaries


class CredibilityUniverseUnavailableError(ValueError):
    pass


async def _latest_opportunity_universe(
    session: AsyncSession,
    *,
    max_assets: int,
) -> CredibilityUniverse:
    source_run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    if source_run is None:
        raise CredibilityUniverseUnavailableError("等待信号生成：没有最新成功 ETF 信号 run。")

    items = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(
                ShortResearchSignalItem.run_id == source_run.id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
            )
            .order_by(ShortResearchSignalItem.rank.asc(), ShortResearchSignalItem.asset_code.asc())
        )
    ).all()
    ranked: list[tuple[float, str]] = []
    unavailable_count = 0
    for item in items:
        metrics = dict(item.metrics_json or {})
        score = metrics.get("opportunity_score")
        if not isinstance(score, int | float) or not has_available_opportunity_score(metrics):
            unavailable_count += 1
            continue
        ranked.append((float(score), item.asset_code))

    ranked.sort(key=lambda row: (-row[0], row[1]))
    selected = ranked[:max_assets]
    if not selected:
        raise CredibilityUniverseUnavailableError(
            f"等待信号生成：最新 ETF 信号 run {source_run.id} 没有可用综合关注评分。"
        )

    codes = [code for _score, code in selected]
    score_by_code = {code: score for score, code in selected}
    metadata = {
        "universe_scope": UNIVERSE_SCOPE_LATEST_OPPORTUNITY_TOP,
        "ranking_sort": "opportunity",
        "requested_top_n": max_assets,
        "source_signal_run_id": source_run.id,
        "source_signal_as_of_date": source_run.as_of_date.isoformat(),
        "source_signal_item_count": len(items),
        "selected_codes": codes,
        "selected_count": len(codes),
        "excluded_unavailable_opportunity_count": unavailable_count,
    }
    return CredibilityUniverse(codes=codes, metadata=metadata, score_by_code=score_by_code)


async def _load_daily_series(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    codes: list[str],
) -> tuple[list[CredibilitySeries], datetime | None]:
    etf_rows = (
        await session.execute(
            select(TradableEtf, EtfThemeProfile)
            .outerjoin(EtfThemeProfile, EtfThemeProfile.etf_code == TradableEtf.code)
            .where(
                TradableEtf.is_short_term_eligible.is_(True),
                TradableEtf.code.in_(codes),
            )
            .order_by(TradableEtf.code.asc())
        )
    ).all()
    row_by_code = {row[0].code: row for row in etf_rows}
    ordered_rows = [row_by_code[code] for code in codes if code in row_by_code]
    if not ordered_rows:
        return [], None
    price_rows = (
        await session.scalars(
            select(EtfPriceHistory)
            .where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date >= start_date,
                EtfPriceHistory.trade_date <= end_date,
            )
            .order_by(EtfPriceHistory.etf_code.asc(), EtfPriceHistory.trade_date.asc())
        )
    ).all()
    by_code: dict[str, list[CredibilityPricePoint]] = {}
    for row in price_rows:
        by_code.setdefault(row.etf_code, []).append(
            CredibilityPricePoint(
                trade_date=row.trade_date,
                price=float(row.close),
                quote_time=datetime.combine(row.trade_date, time(hour=15)),
            )
        )
    series = _series_from_rows(ordered_rows, by_code)
    cutoff = datetime.combine(end_date, time(hour=15)) if series else None
    return series, cutoff


async def _load_intraday_series(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    codes: list[str],
) -> tuple[list[CredibilitySeries], datetime | None]:
    etf_rows = (
        await session.execute(
            select(TradableEtf, EtfThemeProfile)
            .outerjoin(EtfThemeProfile, EtfThemeProfile.etf_code == TradableEtf.code)
            .where(
                TradableEtf.is_short_term_eligible.is_(True),
                TradableEtf.code.in_(codes),
            )
            .order_by(TradableEtf.code.asc())
        )
    ).all()
    row_by_code = {row[0].code: row for row in etf_rows}
    ordered_rows = [row_by_code[code] for code in codes if code in row_by_code]
    if not ordered_rows:
        return [], None
    quote_rows = (
        await session.scalars(
            select(EtfIntradayQuote)
            .where(
                EtfIntradayQuote.etf_code.in_(codes),
                EtfIntradayQuote.trade_date >= start_date,
                EtfIntradayQuote.trade_date <= end_date,
                EtfIntradayQuote.freshness_status == "fresh",
            )
            .order_by(EtfIntradayQuote.etf_code.asc(), EtfIntradayQuote.quote_time.asc())
        )
    ).all()
    by_code: dict[str, list[CredibilityPricePoint]] = {}
    cutoff: datetime | None = None
    for row in quote_rows:
        point = CredibilityPricePoint(
            trade_date=row.trade_date,
            price=float(row.latest_price),
            quote_time=row.quote_time,
        )
        by_code.setdefault(row.etf_code, []).append(point)
        cutoff = row.quote_time if cutoff is None or row.quote_time > cutoff else cutoff
    return _series_from_rows(ordered_rows, by_code), cutoff


def _series_from_rows(
    etf_rows: list[tuple[TradableEtf, EtfThemeProfile | None]],
    by_code: dict[str, list[CredibilityPricePoint]],
) -> list[CredibilitySeries]:
    series: list[CredibilitySeries] = []
    for etf, profile in etf_rows:
        points = by_code.get(etf.code, [])
        if len(points) < 30:
            continue
        series.append(
            CredibilitySeries(
                code=etf.code,
                name=etf.name,
                asset_bucket=(profile.asset_bucket if profile else etf.asset_class) or "unknown",
                theme_group=(profile.theme_group if profile else "unknown") or "unknown",
                points=points,
            )
        )
    return series


async def run_etf_exit_credibility(
    session: AsyncSession,
    *,
    days: int = 730,
    max_assets: int = 50,
    execution_model: str = EXECUTION_INTRADAY,
    universe_scope: str = UNIVERSE_SCOPE_LATEST_OPPORTUNITY_TOP,
) -> EtfExitSignalCredibilityRun:
    if execution_model not in {EXECUTION_INTRADAY, EXECUTION_DAILY}:
        raise ValueError("execution_model 只支持 intraday_alert 或 daily_close")
    if universe_scope != UNIVERSE_SCOPE_LATEST_OPPORTUNITY_TOP:
        raise ValueError("universe_scope 只支持 latest_opportunity_top")

    latest_date = await session.scalar(
        select(EtfPriceHistory.trade_date).order_by(desc(EtfPriceHistory.trade_date)).limit(1)
    )
    if execution_model == EXECUTION_INTRADAY:
        latest_date = await session.scalar(
            select(EtfIntradayQuote.trade_date).order_by(desc(EtfIntradayQuote.trade_date)).limit(1)
        ) or latest_date
    as_of_date = latest_date or date.today()
    start_date = as_of_date - timedelta(days=days)
    data_window = {
        "start_date": start_date.isoformat(),
        "end_date": as_of_date.isoformat(),
        "days": days,
        "universe_scope": universe_scope,
        "ranking_sort": "opportunity",
        "requested_top_n": max_assets,
    }
    run = EtfExitSignalCredibilityRun(
        status="running",
        started_at=utcnow(),
        as_of_date=as_of_date,
        execution_model=execution_model,
        signal_version=SIGNAL_VERSION,
        exit_rule_version=EXIT_RULE_VERSION,
        contract_hash=contract_hash(execution_model=execution_model, data_window=data_window),
        evidence_status="等待验证",
        data_window_json=data_window,
        summary_json={},
        insufficiency_reasons_json=[],
        created_at=utcnow(),
    )
    session.add(run)
    await session.flush()

    try:
        universe = await _latest_opportunity_universe(session, max_assets=max_assets)
        data_window = {**data_window, **universe.metadata}
        run.data_window_json = data_window
        run.contract_hash = contract_hash(execution_model=execution_model, data_window=data_window)

        if execution_model == EXECUTION_INTRADAY:
            series, cutoff = await _load_intraday_series(
                session, start_date=start_date, end_date=as_of_date, codes=universe.codes
            )
        else:
            series, cutoff = await _load_daily_series(
                session, start_date=start_date, end_date=as_of_date, codes=universe.codes
            )
        events = [event for item in series for event in generate_exit_events(item)]
        summaries = _group_events(events)
        item_by_key: dict[tuple[str, str, str], EtfExitSignalCredibilityItem] = {}
        for summary in summaries:
            item = EtfExitSignalCredibilityItem(
                run_id=run.id,
                signal_type=summary["signal_type"],
                group_type=summary["group_type"],
                group_key=summary["group_key"],
                evidence_level=summary["evidence_level"],
                sample_count=summary["sample_count"],
                success_avoidance_rate=summary["success_avoidance_rate"],
                false_stop_rate=summary["false_stop_rate"],
                sold_too_early_rate=summary["sold_too_early_rate"],
                avg_avoided_drawdown=summary["avg_avoided_drawdown"],
                avg_missed_upside=summary["avg_missed_upside"],
                avg_forward_return=summary["avg_forward_return"],
                metrics_json=summary["metrics"],
                created_at=utcnow(),
            )
            session.add(item)
            await session.flush()
            item_by_key[(item.group_type, item.group_key, item.signal_type)] = item

        sample_events = sorted(events, key=lambda event: event.signal_date, reverse=True)[: EVENT_SAMPLE_LIMIT * len(EXIT_SIGNALS)]
        for event in sample_events:
            item = item_by_key.get(("signal", event.signal_type, event.signal_type))
            primary = _primary_window(event)
            session.add(
                EtfExitSignalCredibilityEvent(
                    run_id=run.id,
                    item_id=item.id if item else None,
                    etf_code=event.code,
                    etf_name=event.name,
                    signal_type=event.signal_type,
                    signal_time=event.signal_time,
                    signal_date=event.signal_date,
                    signal_price=event.signal_price,
                    outcome=str(primary.get("outcome")),
                    forward_window_days=5,
                    forward_return=primary.get("forward_return"),
                    max_favorable_return=primary.get("max_favorable_return"),
                    max_adverse_return=primary.get("max_adverse_return"),
                    context_json={
                        **event.context,
                        "universe_scope": universe.metadata["universe_scope"],
                        "source_signal_run_id": universe.metadata["source_signal_run_id"],
                        "ranking_sort": universe.metadata["ranking_sort"],
                        "requested_top_n": universe.metadata["requested_top_n"],
                        "source_opportunity_score": universe.score_by_code.get(event.code),
                        "asset_bucket": event.asset_bucket,
                        "theme_group": event.theme_group,
                        "thresholds": event.thresholds,
                        "windows": event.windows,
                    },
                    created_at=utcnow(),
                )
            )

        insufficient_reasons: list[str] = []
        if execution_model == EXECUTION_INTRADAY and not series:
            insufficient_reasons.append("缺少盘中历史行情，不能用日线收盘价代替盘中邮件证据")
        if not events:
            insufficient_reasons.append("样本不足，未生成可评价的理论退出事件")
        signal_sample_counts = {
            signal: sum(1 for event in events if event.signal_type == signal) for signal in EXIT_SIGNALS
        }
        verified_signals = sum(1 for value in signal_sample_counts.values() if value >= MIN_SIGNAL_SAMPLES)
        run.status = "success"
        run.finished_at = utcnow()
        run.data_cutoff = cutoff
        run.evidence_status = "同源已验证" if verified_signals else "样本不足"
        run.insufficiency_reasons_json = insufficient_reasons
        run.summary_json = {
            **universe.metadata,
            "execution_model": execution_model,
            "signal_version": SIGNAL_VERSION,
            "exit_rule_version": EXIT_RULE_VERSION,
            "credibility_rule_version": CREDIBILITY_RULE_VERSION,
            "asset_count": len(series),
            "event_count": len(events),
            "signal_sample_counts": signal_sample_counts,
            "verified_signal_count": verified_signals,
            "research_only": True,
            "no_trade_instruction": True,
            "no_email_sent": True,
            "no_tracked_position_mutation": True,
        }
    except CredibilityUniverseUnavailableError as exc:
        message = str(exc)
        run.status = "failed"
        run.finished_at = utcnow()
        run.evidence_status = "等待信号生成"
        run.error_message = message
        run.insufficiency_reasons_json = [message]
        run.summary_json = {
            "execution_model": execution_model,
            "universe_scope": universe_scope,
            "ranking_sort": "opportunity",
            "requested_top_n": max_assets,
            "asset_count": 0,
            "event_count": 0,
            "research_only": True,
            "no_trade_instruction": True,
            "no_email_sent": True,
            "no_tracked_position_mutation": True,
        }
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_message = str(exc)
        run.summary_json = {"execution_model": execution_model, "research_only": True, "no_email_sent": True}
        raise
    finally:
        await session.commit()
        await session.refresh(run)
    return run


async def latest_etf_exit_credibility_run(
    session: AsyncSession,
    *,
    execution_model: str | None = None,
) -> EtfExitSignalCredibilityRun | None:
    query = select(EtfExitSignalCredibilityRun)
    if execution_model is not None:
        query = query.where(EtfExitSignalCredibilityRun.execution_model == execution_model)
    return await session.scalar(
        query.order_by(
            desc(EtfExitSignalCredibilityRun.finished_at),
            desc(EtfExitSignalCredibilityRun.id),
        ).limit(1)
    )


async def etf_exit_credibility_payload(
    session: AsyncSession,
    run: EtfExitSignalCredibilityRun,
) -> dict[str, Any]:
    items = (
        await session.scalars(
            select(EtfExitSignalCredibilityItem)
            .where(EtfExitSignalCredibilityItem.run_id == run.id)
            .order_by(
                EtfExitSignalCredibilityItem.group_type.asc(),
                EtfExitSignalCredibilityItem.signal_type.asc(),
                EtfExitSignalCredibilityItem.sample_count.desc(),
            )
        )
    ).all()
    events = (
        await session.scalars(
            select(EtfExitSignalCredibilityEvent)
            .where(EtfExitSignalCredibilityEvent.run_id == run.id)
            .order_by(EtfExitSignalCredibilityEvent.signal_date.desc(), EtfExitSignalCredibilityEvent.id.desc())
        )
    ).all()
    events_by_item: dict[int, list[EtfExitSignalCredibilityEvent]] = {}
    for event in events:
        if event.item_id is not None:
            events_by_item.setdefault(event.item_id, []).append(event)

    def item_payload(item: EtfExitSignalCredibilityItem) -> dict[str, Any]:
        policy_class = policy_class_for_signal(item.signal_type)
        evidence_status = evidence_status_for_policy(
            sample_count=item.sample_count,
            evidence_level=item.evidence_level,
            run_evidence_status=run.evidence_status,
            research_only=True,
            min_samples=MIN_SIGNAL_SAMPLES,
        )
        kpi_summary = kpi_summary_for_item(
            signal_type=item.signal_type,
            sample_count=item.sample_count,
            success_avoidance_rate=item.success_avoidance_rate,
            false_stop_rate=item.false_stop_rate,
            sold_too_early_rate=item.sold_too_early_rate,
            avg_avoided_drawdown=item.avg_avoided_drawdown,
            avg_missed_upside=item.avg_missed_upside,
            avg_forward_return=item.avg_forward_return,
            metrics=dict(item.metrics_json or {}),
        )
        return {
            "id": item.id,
            "signal_type": item.signal_type,
            "group_type": item.group_type,
            "group_key": item.group_key,
            "policy_class": policy_class,
            "policy_class_label": policy_class_label(policy_class),
            "evidence_status": evidence_status,
            "recommended_usage": recommended_usage_for_policy(policy_class),
            "strong_conclusion_allowed": strong_conclusion_allowed(
                sample_count=item.sample_count,
                evidence_level=item.evidence_level,
                run_evidence_status=run.evidence_status,
                min_samples=MIN_SIGNAL_SAMPLES,
            ),
            "is_live_rule_evidence": False,
            "evidence_level": item.evidence_level,
            "sample_count": item.sample_count,
            "success_avoidance_rate": item.success_avoidance_rate,
            "false_stop_rate": item.false_stop_rate,
            "sold_too_early_rate": item.sold_too_early_rate,
            "avg_avoided_drawdown": item.avg_avoided_drawdown,
            "avg_missed_upside": item.avg_missed_upside,
            "avg_forward_return": item.avg_forward_return,
            "metrics": dict(item.metrics_json or {}),
            "kpi_summary": kpi_summary,
            "events": [
                {
                    "id": event.id,
                    "etf_code": event.etf_code,
                    "etf_name": event.etf_name,
                    "signal_type": event.signal_type,
                    "signal_time": event.signal_time,
                    "signal_date": event.signal_date,
                    "signal_price": event.signal_price,
                    "outcome": event.outcome,
                    "forward_window_days": event.forward_window_days,
                    "forward_return": event.forward_return,
                    "max_favorable_return": event.max_favorable_return,
                    "max_adverse_return": event.max_adverse_return,
                    "context": dict(event.context_json or {}),
                }
                for event in events_by_item.get(item.id, [])[:EVENT_SAMPLE_LIMIT]
            ],
            "created_at": item.created_at,
        }

    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "as_of_date": run.as_of_date,
        "execution_model": run.execution_model,
        "signal_version": run.signal_version,
        "exit_rule_version": run.exit_rule_version,
        "contract_hash": run.contract_hash,
        "evidence_status": run.evidence_status,
        "data_cutoff": run.data_cutoff,
        "data_window": dict(run.data_window_json or {}),
        "summary": dict(run.summary_json or {}),
        "insufficiency_reasons": list(run.insufficiency_reasons_json or []),
        "error_message": run.error_message,
        "research_only": True,
        "no_trade_instruction": True,
        "items": [item_payload(item) for item in items],
    }
