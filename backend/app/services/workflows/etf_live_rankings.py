from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import ShortResearchSignalItem, TrackedPosition, TrackedPositionAlert
from app.schemas.etf_quotes import EtfLiveRankingItemOut, EtfLiveRankingListOut
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
    latest_signal_run,
)

TOP_SIGNAL_LIMIT = 20
SOURCE_TOP20_SIGNAL = "top20_signal"
SOURCE_SHORT_WATCH = "short_watch"
SOURCE_HIGH_WATCH = "high_watch"
SOURCE_TRACKED_POSITION = "tracked_position"
LIVE_LABEL_DATA_INSUFFICIENT = "数据不足"
LIVE_LABEL_DOWN_PERSISTENT = "跌破等待"
LIVE_LABEL_HEALTHY_PULLBACK = "健康回踩"
LIVE_LABEL_TREND_CONTINUATION = "趋势延续"
LIVE_LABEL_CHASE_WARNING = "冲高别追"
_INTRADAY_RANKING_DATA_INSUFFICIENT_REASON = "暂无新鲜盘中行情，暂不做盘中加分。"
TRACKING_STATE_ACTIVE = "我已持仓"
TRACKING_STATE_ALERT = "触发提醒"
TRACKING_STATE_WEB_ONLY = "仅网页提示"
ENTRY_STATE_CLOSED = "休市"
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


def _entry_timing(
    change_percent: float,
    conclusion: str | None,
) -> tuple[str, str, int]:
    if change_percent <= -2.5:
        return LIVE_LABEL_DOWN_PERSISTENT, "跌破后，偏离趋势，短线加分下调。", -5
    if change_percent <= -0.2:
        if conclusion in {CONCLUSION_WATCH, CONCLUSION_HIGH_WATCH}:
            return LIVE_LABEL_HEALTHY_PULLBACK, "回踩不宜过度下跌且有回归迹象，加分。", 2
        return LIVE_LABEL_DOWN_PERSISTENT, "下跌区间明显，继续观察，暂不加分。", -5
    if change_percent < 2.0:
        return LIVE_LABEL_TREND_CONTINUATION, "涨幅温和，趋势延续加分。", 1
    return LIVE_LABEL_CHASE_WARNING, "涨幅过快，短线追高风险较高，降分。", -3


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


async def _tracking_states_by_code(session: AsyncSession, user_id: int | None) -> dict[str, set[str]]:
    if user_id is None:
        return {}
    positions = (
        await session.scalars(
            select(TrackedPosition).where(
                TrackedPosition.user_id == user_id,
                TrackedPosition.asset_type == ASSET_TYPE_ETF,
                TrackedPosition.status == "active",
            )
        )
    ).all()
    states_by_code = {position.asset_code: {TRACKING_STATE_ACTIVE} for position in positions}
    position_code_by_id = {position.id: position.asset_code for position in positions}
    if not position_code_by_id:
        return states_by_code
    alerts = (
        await session.scalars(
            select(TrackedPositionAlert).where(
                TrackedPositionAlert.tracked_position_id.in_(list(position_code_by_id))
            ).order_by(
                TrackedPositionAlert.tracked_position_id.asc(),
                TrackedPositionAlert.created_at.desc(),
            )
        )
    ).all()
    seen_position_ids: set[int] = set()
    for alert in alerts:
        if alert.tracked_position_id in seen_position_ids:
            continue
        seen_position_ids.add(alert.tracked_position_id)
        code = position_code_by_id.get(alert.tracked_position_id)
        if code is None:
            continue
        states = states_by_code.setdefault(code, {TRACKING_STATE_ACTIVE})
        if alert.alert_type:
            states.add(TRACKING_STATE_ALERT)
        if alert.suppression_status in {"web_only", "suppressed"} or alert.email_status == "skipped":
            states.add(TRACKING_STATE_WEB_ONLY)
    return states_by_code


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
    elif quote_is_stale:
        labels.add(ENTRY_STATE_STALE)
    return {label for label in labels if label}


async def _build_research_watchlist(session: AsyncSession) -> WatchlistResult:
    watchlist = await build_watchlist(session)
    watch_map = {item.etf_code: item for item in watchlist.items}
    run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    signal_status = watchlist.signal_status
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

    tracked_rows = (
        await session.scalars(
            select(TrackedPosition).where(
                TrackedPosition.asset_type == ASSET_TYPE_ETF,
                TrackedPosition.status == "active",
            )
        )
    ).all()
    for position in tracked_rows:
        watch_item = watch_map.setdefault(position.asset_code, WatchItem(etf_code=position.asset_code, sources={SOURCE_ALL_ETF}))
        watch_item.sources.add(SOURCE_TRACKED_POSITION)

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
    state = intraday_quotes.current_market_state()
    watchlist = await _build_research_watchlist(session)
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
            watched_count=0,
            total=0,
            signal_as_of_date=watchlist.signal_as_of_date,
            signal_status=watchlist.signal_status,
            latest_run=market_data.etf_watch_run_out(latest_run) if latest_run is not None else None,
            items=[],
        )

    names = await market_data.etf_quote_name_map(session, watch_codes)
    latest_quotes = await market_data.latest_etf_quotes_by_code(session, watch_codes)
    keyword = q.strip().lower() if q else None
    now = datetime.now(ASIA_SHANGHAI).replace(tzinfo=None)
    signal_run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    signal_items_by_code: dict[str, ShortResearchSignalItem] = {}
    if signal_run is not None:
        signal_rows = await session.scalars(
            select(ShortResearchSignalItem).where(
                ShortResearchSignalItem.run_id == signal_run.id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
                ShortResearchSignalItem.asset_code.in_(watch_codes),
            )
        )
        signal_items_by_code = {item.asset_code: item for item in signal_rows.all()}
    selected_theme = theme.strip() if theme else None
    if selected_theme in {"", "all", "全部"}:
        selected_theme = None
    tracking_states_by_code = await _tracking_states_by_code(session, user_id) if tracking_filters else {}

    scored_rows: list[dict[str, Any]] = []
    for watch_item in watchlist.items:
        name = names.get(watch_item.etf_code)
        if keyword and keyword not in watch_item.etf_code.lower() and keyword not in (name or "").lower():
            continue
        signal_item = signal_items_by_code.get(watch_item.etf_code)
        if selected_theme and selected_theme not in _signal_item_theme_values(signal_item):
            continue
        base_score = _float_or_none(signal_item.total_score) if signal_item is not None else None
        base_rank = watch_item.rank
        conclusion = signal_item.conclusion if signal_item is not None else None
        if observation_filters and (conclusion is None or conclusion not in observation_filters):
            continue
        quote = latest_quotes.get(watch_item.etf_code)
        daily_entry_timing_label, daily_entry_timing_reason = _daily_entry_timing(signal_item)

        score_source = "daily" if base_score is not None else "unavailable"
        live_total_score = base_score
        intraday_adjustment_score: float | None = None
        score_contribution_reasons: list[str] = []
        if base_score is not None:
            score_contribution_reasons.append(f"日线基础分 {base_score:.1f}")
        if not is_open or not market_data.is_fresh_etf_decision_quote(quote, now):
            live_label = LIVE_LABEL_DATA_INSUFFICIENT
            live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
            score_contribution_reasons.append("休市或行情不新鲜，仅使用日线基础分。")
        else:
            assert quote is not None
            if quote.change_percent is None:
                live_label = LIVE_LABEL_DATA_INSUFFICIENT
                live_reason = _INTRADAY_RANKING_DATA_INSUFFICIENT_REASON
                score_contribution_reasons.append("缺少盘中涨跌幅，实时调整不可用。")
            else:
                score_source = "intraday"
                live_label, live_reason, adjustment = _entry_timing(quote.change_percent, conclusion)
                intraday_adjustment_score = float(adjustment)
                score_contribution_reasons.append(f"盘中涨跌 {quote.change_percent:+.2f}%：{adjustment:+.1f} 分。")
                live_total_score = _clamp_score(base_score + adjustment) if base_score is not None else None
                avg_turnover_20d = (
                    _float_or_none((signal_item.metrics_json or {}).get("average_turnover_20d")) if signal_item else None
                )
                if (
                    live_total_score is not None
                    and quote.turnover is not None
                    and avg_turnover_20d is not None
                    and avg_turnover_20d > 0
                ):
                    if quote.turnover >= avg_turnover_20d * 0.7:
                        live_total_score = _clamp_score(live_total_score + 1)
                        intraday_adjustment_score = (intraday_adjustment_score or 0.0) + 1.0
                        score_contribution_reasons.append("盘中成交额接近近期均值，流动性加 1 分。")
                    elif quote.turnover < avg_turnover_20d * 0.1:
                        live_total_score = _clamp_score(live_total_score - 2)
                        intraday_adjustment_score = (intraday_adjustment_score or 0.0) - 2.0
                        score_contribution_reasons.append("盘中成交额明显偏低，流动性扣 2 分。")

        if entry_filters and not (
            entry_filters
            & _entry_filter_labels(
                live_label=live_label,
                daily_label=daily_entry_timing_label,
                is_open=is_open,
                quote_is_stale=market_data.is_etf_quote_stale(quote.quote_time if quote is not None else None, now),
            )
        ):
            continue
        if tracking_filters and not (tracking_filters & tracking_states_by_code.get(watch_item.etf_code, set())):
            continue

        scored_rows.append(
            {
                "etf_code": watch_item.etf_code,
                "etf_name": name,
                "base_rank": base_rank,
                "conclusion": conclusion,
                "base_score": base_score,
                "live_total_score": live_total_score,
                "intraday_adjustment_score": intraday_adjustment_score,
                "score_source": score_source,
                "score_contribution_reasons": score_contribution_reasons,
                "live_entry_timing_label": live_label,
                "live_entry_timing_reason": live_reason,
                "daily_entry_timing_label": daily_entry_timing_label,
                "daily_entry_timing_reason": daily_entry_timing_reason,
                "sources": sorted(watch_item.sources),
                "quote": market_data.etf_quote_out(quote, etf_name=name, now=now) if quote is not None else None,
            }
        )

    scored_rows.sort(
        key=lambda row: (
            row["live_total_score"] is None,
            -(row["live_total_score"] or 0.0),
            row["etf_code"],
        )
    )

    ranked_rows = []
    for index, row in enumerate(scored_rows, start=1):
        row["live_rank"] = index
        row["rank_change"] = row["base_rank"] - index if row["base_rank"] is not None else None
        ranked_rows.append(row)

    selected = ranked_rows[safe_offset : safe_offset + safe_limit]
    return EtfLiveRankingListOut(
        market_status=state.status,
        market_session=state.session,
        message=watchlist.message if is_open else f"{watchlist.message} 当前休市，页面不会自动刷新盘中行情。",
        quote_refresh_seconds=WATCH_REFRESH_SECONDS if is_open else 0,
        page_poll_seconds=PAGE_POLL_SECONDS if is_open else 0,
        watched_count=len(watchlist.items),
        total=len(ranked_rows),
        signal_as_of_date=watchlist.signal_as_of_date,
        signal_status=watchlist.signal_status,
        latest_run=market_data.etf_watch_run_out(latest_run) if latest_run is not None else None,
        items=[
            EtfLiveRankingItemOut(
                etf_code=row["etf_code"],
                etf_name=row["etf_name"],
                base_rank=row["base_rank"],
                live_rank=row["live_rank"],
                rank_change=row["rank_change"],
                sources=row["sources"],
                conclusion=row["conclusion"],
                base_score=row["base_score"],
                live_total_score=row["live_total_score"],
                intraday_adjustment_score=row["intraday_adjustment_score"],
                score_source=row["score_source"],
                score_contribution_reasons=row["score_contribution_reasons"],
                live_entry_timing_label=row["live_entry_timing_label"],
                live_entry_timing_reason=row["live_entry_timing_reason"],
                daily_entry_timing_label=row["daily_entry_timing_label"],
                daily_entry_timing_reason=row["daily_entry_timing_reason"],
                quote=row["quote"],
            )
            for row in selected
        ],
    )
