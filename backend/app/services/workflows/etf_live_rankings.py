from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfIntradayQuote,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
)
from app.schemas.etf_quotes import EtfLiveRankingItemOut, EtfLiveRankingListOut
from app.schemas.short_research import EtfRankingSnapshotMetadataOut
from app.services import market_data
from app.services.intraday_etf import service as intraday_quotes
from app.services.intraday_etf.service import (
    ASIA_SHANGHAI,
    LIVE_RANKING_LIMIT_DEFAULT,
    LIVE_RANKING_LIMIT_MAX,
    PAGE_POLL_SECONDS,
    SOURCE_ALL_ETF,
    WATCH_REFRESH_SECONDS,
    WatchItem,
    WatchlistResult,
    build_watchlist,
)
from app.services.short_research.service import (
    CONCLUSION_HIGH_WATCH,
    CONCLUSION_WATCH,
    current_etf_snapshot_selection,
)
from app.services.short_research.snapshot_selector import snapshot_metadata
from app.services.workflows.tracking_filters import (
    tracking_states_by_code,
    validate_tracking_states,
)

TOP_SIGNAL_LIMIT = 20
SOURCE_TOP20_SIGNAL = "top20_signal"
SOURCE_SHORT_WATCH = "short_watch"
SOURCE_HIGH_WATCH = "high_watch"
LIVE_LABEL_DATA_INSUFFICIENT = "数据不足"
LIVE_LABEL_DOWN_PERSISTENT = "跌破等待"
LIVE_LABEL_HEALTHY_PULLBACK = "健康回踩"
LIVE_LABEL_TREND_CONTINUATION = "趋势延续"
LIVE_LABEL_CHASE_WARNING = "冲高别追"
_INTRADAY_RANKING_DATA_INSUFFICIENT_REASON = "暂无新鲜盘中行情，暂不做盘中加分。"
_INTRADAY_BASE_PRICE_BASIS = "total_return_adjusted"
_INTRADAY_BASE_SCORE_FIELD = "ranking_score"
_INTRADAY_BASE_MIN_COVERAGE = 0.95
_INTRADAY_ADJUSTMENT_VERSION = "intraday_adjustment_v2"
_MIN_SAME_TIME_ACTIVITY_SAMPLES = 5
_PRICE_MOVE_THRESHOLDS = {
    "bond": (0.25, 1.0, 1.6),
    "money": (0.25, 1.0, 1.6),
    "broad_base": (0.25, 1.1, 1.8),
    "equity": (0.25, 1.2, 1.8),
    "commodity": (0.25, 1.2, 1.8),
    "cross_border": (0.25, 1.3, 2.0),
}
ENTRY_STATE_CLOSED = "休市"
ENTRY_STATE_LUNCH_BREAK = "午休"
ENTRY_STATE_STALE = "行情滞后"


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        normalized = value.replace(",", "").replace("%", "").strip()
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _to_pagination(limit: int, offset: int) -> tuple[int, int]:
    safe_limit = max(1, min(limit, LIVE_RANKING_LIMIT_MAX))
    safe_offset = max(0, offset)
    return safe_limit, safe_offset


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _live_scope_hash(codes: list[str], score_version: str) -> str:
    payload = json.dumps(
        {"codes": sorted(set(codes)), "score_version": score_version},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_finite_score(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _eligible_intraday_base(
    snapshot: ShortResearchSignalRun | None,
    item: ShortResearchSignalItem | None,
    quote_trade_date: date | None,
) -> tuple[float | None, str | None]:
    if snapshot is None:
        return None, "缺少日线综合排序基座。"
    if snapshot.status != "success" or snapshot.publication_state != "published":
        return None, "日线基座尚未发布。"
    if snapshot.scope_kind != "full":
        return None, "日线基座不是完整 ETF 排名。"
    if not snapshot.score_version or not snapshot.ranking_contract_hash:
        return None, "日线基座缺少评分契约。"
    if snapshot.price_basis != _INTRADAY_BASE_PRICE_BASIS:
        return None, "日线基座价格口径不兼容。"
    if snapshot.score_field != _INTRADAY_BASE_SCORE_FIELD:
        return None, "日线基座未声明当前最终分字段。"
    if snapshot.coverage_ratio is None or snapshot.coverage_ratio < _INTRADAY_BASE_MIN_COVERAGE:
        return None, "日线基座覆盖率不足。"
    if snapshot.as_of_trade_date is None or snapshot.as_of_trade_date != quote_trade_date:
        return None, "日线基座不是当前交易日，盘中综合分不可用。"
    score = item.ranking_score if item is not None else None
    if item is None or item.score_eligible is not True or score is None or not _is_finite_score(score):
        return None, "日线基座缺少可用最终分。"
    return float(score), None


def _volatility_price_adjustment(
    change_percent: float,
    conclusion: str | None,
    asset_bucket: str,
    volatility_20d: float | None,
) -> tuple[str, str, float | None]:
    thresholds = _PRICE_MOVE_THRESHOLDS.get(asset_bucket)
    if thresholds is None:
        return LIVE_LABEL_DATA_INSUFFICIENT, "缺少同类资产桶，盘中涨跌调整不可用。", None
    if not _is_finite_score(volatility_20d) or volatility_20d is None or volatility_20d <= 0:
        return LIVE_LABEL_DATA_INSUFFICIENT, "缺少有限的 20 日波动率，盘中涨跌调整不可用。", None
    normalized_move = change_percent / (volatility_20d * 100)
    pullback, chase, breakdown = thresholds
    if normalized_move <= -breakdown:
        return (
            LIVE_LABEL_DOWN_PERSISTENT,
            f"按 {_INTRADAY_ADJUSTMENT_VERSION} 波动率归一化后跌破趋势，短线加分下调。",
            -5.0,
        )
    if normalized_move <= -pullback:
        if conclusion in {CONCLUSION_WATCH, CONCLUSION_HIGH_WATCH}:
            return LIVE_LABEL_HEALTHY_PULLBACK, "按波动率归一化后属于健康回踩，加分。", 2.0
        return LIVE_LABEL_DOWN_PERSISTENT, "按波动率归一化后下跌明显，继续观察。", -5.0
    if normalized_move < chase:
        return LIVE_LABEL_TREND_CONTINUATION, "按波动率归一化后涨幅温和，趋势延续加分。", 1.0
    return LIVE_LABEL_CHASE_WARNING, "按波动率归一化后涨幅偏快，短线追高风险较高。", -3.0


def _same_time_activity_adjustment(
    current_turnover: float | None,
    historical_turnovers: list[float],
) -> tuple[float | None, str]:
    valid_history = [value for value in historical_turnovers if _is_finite_score(value) and value > 0]
    if not _is_finite_score(current_turnover) or current_turnover is None or current_turnover <= 0:
        return None, "缺少有限的当前累计成交额，活跃度调整不可用。"
    if len(valid_history) < _MIN_SAME_TIME_ACTIVITY_SAMPLES:
        return None, f"同刻成交历史少于 {_MIN_SAME_TIME_ACTIVITY_SAMPLES} 个交易日，活跃度调整不可用。"
    reference = float(median(valid_history))
    ratio = current_turnover / reference
    if ratio >= 1.25:
        return 1.0, "当前累计成交额高于同刻历史中位数，活跃度加 1 分。"
    if ratio <= 0.75:
        return -1.0, "当前累计成交额低于同刻历史中位数，活跃度扣 1 分。"
    return 0.0, "当前累计成交额与同刻历史相近，活跃度不调整。"


def _component_status(
    available: bool,
    reason: str | None = None,
    *,
    adjustment: float | None = None,
    value: float | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {"status": "available" if available else "unavailable", "reason": reason}
    if adjustment is not None:
        status["adjustment"] = adjustment
    if value is not None:
        status["value"] = value
    return status


def _quote_component_status(quote: Any, now: datetime) -> dict[str, dict[str, Any]]:
    if quote is None:
        unavailable = _component_status(False, "暂无盘中行情。")
        return {
            "price": unavailable,
            "timestamp": unavailable,
            "consensus": unavailable,
            "premium_discount": unavailable,
            "spread": unavailable,
        }

    price = _float_or_none(quote.latest_price)
    price_status = _component_status(
        _is_finite_score(price) and price is not None and price > 0,
        None if _is_finite_score(price) and price is not None and price > 0 else "最新价非有限或不为正数。",
        value=price,
    )
    timestamp_eligible = not market_data.is_etf_quote_stale(quote.quote_time, now) and not market_data.is_etf_quote_time_fallback(
        quote
    )
    timestamp_status = _component_status(
        timestamp_eligible,
        None if timestamp_eligible else market_data.etf_quote_decision_limitation_reason(quote, now),
    )
    consensus_eligible = market_data.is_fresh_etf_decision_quote(quote, now)
    consensus_status = _component_status(
        consensus_eligible,
        None if consensus_eligible else market_data.etf_quote_decision_limitation_reason(quote, now),
    )

    premium = _float_or_none(quote.premium_discount_pct)
    if premium is None:
        iopv = _float_or_none(quote.iopv)
        if _is_finite_score(iopv) and iopv is not None and iopv > 0 and price is not None and price > 0:
            premium = (price / iopv - 1) * 100
    premium_available = consensus_eligible and _is_finite_score(premium)
    premium_adjustment = -1.0 if premium_available and premium is not None and abs(premium) >= 1.0 else 0.0
    premium_status = _component_status(
        premium_available,
        None if premium_available else "缺少可决策的有限溢折价。",
        adjustment=premium_adjustment if premium_available else None,
        value=premium if premium_available else None,
    )

    bid = _float_or_none(quote.bid_price)
    ask = _float_or_none(quote.ask_price)
    spread_bps: float | None = None
    if bid is not None and ask is not None:
        midpoint = (bid + ask) / 2
        if midpoint > 0:
            spread_bps = ((ask - bid) / midpoint) * 10_000
    spread_available = consensus_eligible and _is_finite_score(spread_bps) and spread_bps is not None and spread_bps >= 0
    spread_adjustment = -1.0 if spread_available and spread_bps is not None and spread_bps > 50 else 0.0
    spread_status = _component_status(
        spread_available,
        None if spread_available else "缺少可决策的有限买卖价差。",
        adjustment=spread_adjustment if spread_available else None,
        value=spread_bps if spread_available else None,
    )
    return {
        "price": price_status,
        "timestamp": timestamp_status,
        "consensus": consensus_status,
        "premium_discount": premium_status,
        "spread": spread_status,
    }


def _exchange_minute(value: datetime) -> tuple[str, int] | None:
    local = value.astimezone(ASIA_SHANGHAI) if value.tzinfo is not None else value
    minute = local.hour * 60 + local.minute
    if 9 * 60 + 30 <= minute < 11 * 60 + 30:
        return "morning", minute - (9 * 60 + 30)
    if 13 * 60 <= minute < 15 * 60:
        return "afternoon", minute - 13 * 60
    return None


async def _same_time_turnover_history(
    session: AsyncSession,
    codes: list[str],
    now: datetime,
) -> dict[str, list[float]]:
    target_minute = _exchange_minute(now)
    if target_minute is None or not codes:
        return {}
    rows = (
        await session.scalars(
            select(EtfIntradayQuote).where(
                EtfIntradayQuote.etf_code.in_(codes),
                EtfIntradayQuote.trade_date < now.date(),
                EtfIntradayQuote.trade_date >= now.date() - timedelta(days=90),
            )
        )
    ).all()
    by_code_and_date: dict[tuple[str, date], float] = {}
    for row in rows:
        if _exchange_minute(row.quote_time) != target_minute or not _is_finite_score(row.turnover):
            continue
        turnover = row.turnover
        if turnover is None:
            continue
        by_code_and_date[(row.etf_code, row.trade_date)] = float(turnover)
    result: dict[str, list[float]] = {}
    for (code, _trade_date), turnover in by_code_and_date.items():
        result.setdefault(code, []).append(turnover)
    return result


def _daily_entry_timing(signal_item: ShortResearchSignalItem | None) -> tuple[str, str]:
    if signal_item is None:
        return LIVE_LABEL_DATA_INSUFFICIENT, "缺少最新短线排序缓存，请先生成短线排序。"
    metrics = signal_item.metrics_json or {}
    rationale = signal_item.rationale_json or {}
    label = metrics.get("entry_timing_label") or rationale.get("entry_timing_label")
    reason = metrics.get("entry_timing_reason") or rationale.get("entry_timing_reason")
    return (
        str(label or LIVE_LABEL_DATA_INSUFFICIENT),
        str(reason or "短线排序缓存缺少日线买点，请重新生成短线排序。"),
    )


def _signal_item_theme_values(signal_item: ShortResearchSignalItem | None) -> set[str]:
    if signal_item is None:
        return set()
    metrics = dict(signal_item.metrics_json or {})
    profile = dict(metrics.get("theme_profile") or {})
    values = {
        str(profile.get("theme_group") or ""),
        str(profile.get("primary_theme") or ""),
    }
    values.update(str(item) for item in profile.get("secondary_themes") or [])
    return {item for item in values if item}


def _entry_filter_labels(
    *,
    live_label: str,
    daily_label: str,
    is_open: bool,
    quote_is_stale: bool,
) -> set[str]:
    labels = {live_label, daily_label}
    if not is_open:
        labels.add(ENTRY_STATE_CLOSED)
        if live_label == ENTRY_STATE_LUNCH_BREAK:
            labels.add(ENTRY_STATE_LUNCH_BREAK)
    elif quote_is_stale:
        labels.add(ENTRY_STATE_STALE)
    return {label for label in labels if label}


async def _build_research_watchlist(session: AsyncSession, *, now: datetime | None = None) -> WatchlistResult:
    watchlist = await build_watchlist(session)
    watch_map = {item.etf_code: item for item in watchlist.items}
    selection = await current_etf_snapshot_selection(session, now=now)
    run = selection.run
    signal_status = selection.state
    message = watchlist.message
    signal_run_id: int | None = None
    signal_as_of_date: date | None = None
    if run is not None:
        signal_status = "ready"
        signal_run_id = run.id
        signal_as_of_date = run.as_of_date
        message = "使用全部可交易 ETF 盘中行情；实时榜单优先显示最新评分前 20、短线观察、高位观察和已追踪 ETF。"
        signal_items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(
                    ShortResearchSignalItem.run_id == run.id,
                    ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
                )
                .order_by(ShortResearchSignalItem.rank.asc())
            )
        ).all()
        for item in signal_items:
            watch_item = watch_map.setdefault(item.asset_code, WatchItem(etf_code=item.asset_code, sources={SOURCE_ALL_ETF}))
            watch_item.rank = item.rank
            if item.rank is not None and item.rank <= TOP_SIGNAL_LIMIT:
                watch_item.sources.add(SOURCE_TOP20_SIGNAL)
            if item.conclusion == CONCLUSION_WATCH:
                watch_item.sources.add(SOURCE_SHORT_WATCH)
            if item.conclusion == CONCLUSION_HIGH_WATCH:
                watch_item.sources.add(SOURCE_HIGH_WATCH)

    items = sorted(watch_map.values(), key=lambda item: (item.rank is None, item.rank or 9999, item.etf_code))
    return WatchlistResult(items, signal_run_id, signal_as_of_date, signal_status, message)


async def live_rankings(
    session: AsyncSession,
    *,
    limit: int = LIVE_RANKING_LIMIT_DEFAULT,
    offset: int = 0,
    q: str | None = None,
    theme: str | None = None,
    observation_labels: set[str] | None = None,
    entry_labels: set[str] | None = None,
    tracking_states: set[str] | None = None,
    user_id: int | None = None,
) -> EtfLiveRankingListOut:
    safe_limit, safe_offset = _to_pagination(limit, offset)
    observation_filters = observation_labels or set()
    entry_filters = entry_labels or set()
    tracking_filters = tracking_states or set()
    validate_tracking_states(tracking_filters)
    if tracking_filters and user_id is None:
        raise ValueError("持仓筛选需要登录")
    state = intraday_quotes.current_market_state()
    watchlist = await _build_research_watchlist(session, now=state.now)
    source_snapshot = (
        await session.get(ShortResearchSignalRun, watchlist.signal_run_id)
        if watchlist.signal_run_id is not None
        else None
    )
    watch_codes = [item.etf_code for item in watchlist.items]
    latest_run = await market_data.latest_etf_watch_run(session)
    is_open = state.status == "open"
    if not watch_codes:
        return EtfLiveRankingListOut(
            market_status=state.status,
            market_session=state.session,
            message=watchlist.message if is_open else f"{watchlist.message} 当前休市，页面不会自动刷新盘中行情。",
            quote_refresh_seconds=WATCH_REFRESH_SECONDS if is_open else 0,
            page_poll_seconds=PAGE_POLL_SECONDS if is_open else 0,
            next_poll_seconds=state.next_poll_seconds,
            watched_count=0,
            total=0,
            signal_as_of_date=watchlist.signal_as_of_date,
            signal_status=watchlist.signal_status,
            latest_run=market_data.etf_watch_run_out(latest_run) if latest_run is not None else None,
            items=[],
            snapshot=EtfRankingSnapshotMetadataOut.model_validate(
                snapshot_metadata(source_snapshot, selection_state=watchlist.signal_status)
            ),
        )

    names = await market_data.etf_quote_name_map(session, watch_codes)
    latest_quotes = await market_data.latest_etf_quotes_by_code(session, watch_codes)
    keyword = q.strip().lower() if q else None
    now = state.now
    signal_items_by_code: dict[str, ShortResearchSignalItem] = {}
    if watchlist.signal_run_id is not None:
        signal_rows = await session.scalars(
            select(ShortResearchSignalItem).where(
                ShortResearchSignalItem.run_id == watchlist.signal_run_id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
                ShortResearchSignalItem.asset_code.in_(watch_codes),
            )
        )
        signal_items_by_code = {item.asset_code: item for item in signal_rows.all()}
    scope_score_version = str(source_snapshot.score_version or "legacy") if source_snapshot is not None else "unavailable"
    live_scope_hash = _live_scope_hash(watch_codes, scope_score_version)
    base_scope_hash = _live_scope_hash(list(signal_items_by_code), scope_score_version)
    same_time_turnovers = await _same_time_turnover_history(session, watch_codes, state.now) if is_open else {}
    selected_theme = theme.strip() if theme else None
    if selected_theme in {"", "all", "全部"}:
        selected_theme = None
    states_by_code = (
        await tracking_states_by_code(session, user_id=user_id, as_of_date=source_snapshot.as_of_date)
        if tracking_filters and user_id is not None and source_snapshot is not None
        else {}
    )

    scored_rows: list[dict[str, Any]] = []
    for watch_item in watchlist.items:
        name = names.get(watch_item.etf_code)
        signal_item = signal_items_by_code.get(watch_item.etf_code)
        reference_base_score = (
            _float_or_none(signal_item.ranking_score)
            if signal_item is not None and signal_item.score_eligible is True
            else None
        )
        signal_metrics = dict(signal_item.metrics_json or {}) if signal_item is not None else {}
        signal_breakdown = dict(signal_item.score_breakdown_json or {}) if signal_item is not None else {}
        final_score_breakdown = signal_breakdown.get("final_score_v3") or signal_breakdown.get("final_score_v2")
        item_score_version = (
            signal_metrics.get("score_version")
            or signal_breakdown.get("score_version")
            or (final_score_breakdown.get("score_version") if isinstance(final_score_breakdown, dict) else None)
        )
        score_version = item_score_version or (source_snapshot.score_version if source_snapshot is not None else None)
        base_global_rank = (
            signal_item.global_rank if signal_item is not None and signal_item.global_rank is not None else watch_item.rank
        )
        conclusion = signal_item.conclusion if signal_item is not None else None
        quote = latest_quotes.get(watch_item.etf_code)
        component_status = _quote_component_status(quote, now)
        intraday_base_score, base_limitation = _eligible_intraday_base(
            source_snapshot,
            signal_item,
            quote.trade_date if quote is not None else None,
        )
        daily_entry_timing_label, daily_entry_timing_reason = _daily_entry_timing(signal_item)

        score_source = "daily" if reference_base_score is not None else "unavailable"
        live_total_score = reference_base_score
        intraday_adjustment_score: float | None = None
        score_contribution_reasons: list[str] = []
        if reference_base_score is not None:
            score_contribution_reasons.append(f"日线基础分 {reference_base_score:.1f}")
        if state.status == "lunch_break":
            live_label = ENTRY_STATE_LUNCH_BREAK
            live_reason = "当前是午休时段，不产生新的盘中买点；页面展示上午最近行情，日线买点只作参考。"
            score_contribution_reasons.append("午休时段，仅使用日线基础分和上午最近行情展示。")
        elif (
            not is_open
            or not market_data.is_fresh_etf_decision_quote(quote, now)
            or component_status["price"]["status"] != "available"
            or component_status["timestamp"]["status"] != "available"
        ):
            live_label = LIVE_LABEL_DATA_INSUFFICIENT
            live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
            score_contribution_reasons.append("休市或行情不新鲜，仅使用日线基础分。")
        elif intraday_base_score is None:
            score_source = "unavailable"
            live_total_score = None
            live_label = LIVE_LABEL_DATA_INSUFFICIENT
            live_reason = "盘中行情可展示，但日线综合排序基座不兼容或已过期。"
            score_contribution_reasons.append(base_limitation or "日线基座不可用。")
        else:
            assert quote is not None
            score_source = "intraday"
            change_percent = _float_or_none(quote.change_percent)
            if change_percent is None:
                live_label = LIVE_LABEL_DATA_INSUFFICIENT
                live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
                price_adjustment = None
                score_contribution_reasons.append("缺少有限的盘中涨跌幅，涨跌调整不可用。")
            else:
                live_label, live_reason, price_adjustment = _volatility_price_adjustment(
                    change_percent,
                    conclusion,
                    str(signal_metrics.get("ranking_asset_bucket") or "unknown"),
                    _float_or_none(signal_metrics.get("realized_volatility_20d"))
                    or _float_or_none(signal_metrics.get("volatility_20d")),
                )
                if price_adjustment is None:
                    score_contribution_reasons.append(live_reason)
                else:
                    score_contribution_reasons.append(
                        f"盘中涨跌 {change_percent:+.2f}%：{price_adjustment:+.1f} 分（{_INTRADAY_ADJUSTMENT_VERSION}）。"
                    )
            activity_adjustment, activity_reason = _same_time_activity_adjustment(
                quote.turnover,
                same_time_turnovers.get(watch_item.etf_code, []),
            )
            component_status["activity"] = _component_status(
                activity_adjustment is not None,
                None if activity_adjustment is not None else activity_reason,
                adjustment=activity_adjustment,
            )
            score_contribution_reasons.append(activity_reason)
            component_status["price"]["adjustment"] = price_adjustment
            structure_adjustments = [
                component_status["premium_discount"].get("adjustment"),
                component_status["spread"].get("adjustment"),
            ]
            adjustments = [
                value
                for value in (price_adjustment, activity_adjustment, *structure_adjustments)
                if value is not None
            ]
            for component_name in ("premium_discount", "spread"):
                adjustment = component_status[component_name].get("adjustment")
                if adjustment:
                    score_contribution_reasons.append(f"{component_name} 结构调整 {adjustment:+.1f} 分。")
            intraday_adjustment_score = sum(adjustments) if adjustments else None
            live_total_score = _clamp_score(intraday_base_score + sum(adjustments))

        if "activity" not in component_status:
            component_status["activity"] = _component_status(False, "当前不是可计算盘中活跃度的时段。")

        scored_rows.append(
            {
                "etf_code": watch_item.etf_code,
                "etf_name": name,
                "base_global_rank": base_global_rank,
                "conclusion": conclusion,
                "base_score": reference_base_score,
                "live_total_score": live_total_score,
                "intraday_adjustment_score": intraday_adjustment_score,
                "score_source": score_source,
                "score_version": str(score_version or "legacy") if signal_item is not None else None,
                "item_score_version": str(item_score_version) if item_score_version is not None else None,
                "score_breakdown": signal_breakdown,
                "score_contribution_reasons": score_contribution_reasons,
                "intraday_component_status": component_status,
                "live_entry_timing_label": live_label,
                "live_entry_timing_reason": live_reason,
                "daily_entry_timing_label": daily_entry_timing_label,
                "daily_entry_timing_reason": daily_entry_timing_reason,
                "sources": sorted(watch_item.sources),
                "quote": market_data.etf_quote_out(quote, etf_name=name, now=now) if quote is not None else None,
                "theme_values": _signal_item_theme_values(signal_item),
                "tracking_states": states_by_code.get(watch_item.etf_code, set()),
                "keyword_matches": not keyword
                or keyword in watch_item.etf_code.lower()
                or keyword in (name or "").lower(),
            }
        )

    for row in scored_rows:
        row["live_rankable"] = _is_finite_score(row["live_total_score"]) and (
            not is_open or row["score_source"] == "intraday"
        )
    scored_rows.sort(
        key=lambda row: (
            not row["live_rankable"],
            -float(row["live_total_score"]) if row["live_rankable"] else 0.0,
            row["base_global_rank"] is None,
            row["base_global_rank"] or 0,
            row["etf_code"],
        )
    )

    live_scope_rank = 0
    for row in scored_rows:
        if row["live_rankable"]:
            live_scope_rank += 1
            row["live_scope_rank"] = live_scope_rank
            row["rank_scope"] = "live_scope"
        else:
            row["live_scope_rank"] = None
            row["rank_scope"] = "unranked"
        row["rank_change"] = (
            row["base_global_rank"] - row["live_scope_rank"]
            if (
                row["base_global_rank"] is not None
                and row["live_scope_rank"] is not None
                and base_scope_hash == live_scope_hash
                and row["score_version"] == scope_score_version
                and row["item_score_version"] == scope_score_version
            )
            else None
        )

    filtered_rows = []
    for row in scored_rows:
        if not row["keyword_matches"]:
            continue
        if selected_theme and selected_theme not in row["theme_values"]:
            continue
        if observation_filters and row["conclusion"] not in observation_filters:
            continue
        entry_filter_values = _entry_filter_labels(
            live_label=row["live_entry_timing_label"],
            daily_label=row["daily_entry_timing_label"],
            is_open=is_open,
            quote_is_stale=market_data.is_etf_quote_stale(
                row["quote"].quote_time if row["quote"] is not None else None,
                now,
            ),
        )
        if entry_filters and not (entry_filters & entry_filter_values):
            continue
        if tracking_filters and not (tracking_filters & row["tracking_states"]):
            continue
        filtered_rows.append(row)
    for index, row in enumerate(filtered_rows, start=1):
        row["filtered_position"] = index

    selected = filtered_rows[safe_offset : safe_offset + safe_limit]
    return EtfLiveRankingListOut(
        market_status=state.status,
        market_session=state.session,
        message=watchlist.message if is_open else f"{watchlist.message} 当前休市，页面不会自动刷新盘中行情。",
        quote_refresh_seconds=WATCH_REFRESH_SECONDS if is_open else 0,
        page_poll_seconds=PAGE_POLL_SECONDS if is_open else 0,
        next_poll_seconds=state.next_poll_seconds,
        watched_count=len(watchlist.items),
        total=len(filtered_rows),
        signal_as_of_date=watchlist.signal_as_of_date,
        signal_status=watchlist.signal_status,
        latest_run=market_data.etf_watch_run_out(latest_run) if latest_run is not None else None,
        snapshot=EtfRankingSnapshotMetadataOut.model_validate(
            snapshot_metadata(source_snapshot, selection_state=watchlist.signal_status)
        ),
        live_scope_hash=live_scope_hash,
        items=[
            EtfLiveRankingItemOut(
                etf_code=row["etf_code"],
                etf_name=row["etf_name"],
                base_rank=row["base_global_rank"],
                live_rank=row["live_scope_rank"],
                base_global_rank=row["base_global_rank"],
                live_scope_rank=row["live_scope_rank"],
                filtered_position=row["filtered_position"],
                rank_scope=row["rank_scope"],
                rank_change=row["rank_change"],
                sources=row["sources"],
                conclusion=row["conclusion"],
                base_score=row["base_score"],
                live_total_score=row["live_total_score"],
                intraday_adjustment_score=row["intraday_adjustment_score"],
                score_source=row["score_source"],
                score_version=row["score_version"],
                score_breakdown=row["score_breakdown"],
                score_contribution_reasons=row["score_contribution_reasons"],
                intraday_component_status=row["intraday_component_status"],
                live_entry_timing_label=row["live_entry_timing_label"],
                live_entry_timing_reason=row["live_entry_timing_reason"],
                daily_entry_timing_label=row["daily_entry_timing_label"],
                daily_entry_timing_reason=row["daily_entry_timing_reason"],
                quote=row["quote"],
            )
            for row in selected
        ],
    )
