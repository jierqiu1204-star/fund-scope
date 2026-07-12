from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    ShortResearchSignalItem,
    ShortResearchSignalRun,
)
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
    watchlist = await _build_research_watchlist(session)
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
            watched_count=0,
            total=0,
            signal_as_of_date=watchlist.signal_as_of_date,
            signal_status=watchlist.signal_status,
            latest_run=market_data.etf_watch_run_out(latest_run) if latest_run is not None else None,
            items=[],
            snapshot=snapshot_metadata(source_snapshot),
        )

    names = await market_data.etf_quote_name_map(session, watch_codes)
    latest_quotes = await market_data.latest_etf_quotes_by_code(session, watch_codes)
    keyword = q.strip().lower() if q else None
    now = datetime.now(ASIA_SHANGHAI).replace(tzinfo=None)
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
    selected_theme = theme.strip() if theme else None
    if selected_theme in {"", "all", "全部"}:
        selected_theme = None
    states_by_code = (
        await tracking_states_by_code(session, user_id=user_id, as_of_date=source_snapshot.as_of_date)
        if tracking_filters and source_snapshot is not None
        else {}
    )

    scored_rows: list[dict[str, Any]] = []
    for watch_item in watchlist.items:
        name = names.get(watch_item.etf_code)
        signal_item = signal_items_by_code.get(watch_item.etf_code)
        base_score = _float_or_none(signal_item.total_score) if signal_item is not None else None
        signal_metrics = dict(signal_item.metrics_json or {}) if signal_item is not None else {}
        signal_breakdown = dict(signal_item.score_breakdown_json or {}) if signal_item is not None else {}
        final_score_breakdown = signal_breakdown.get("final_score_v2")
        score_version = (
            signal_metrics.get("score_version")
            or signal_breakdown.get("score_version")
            or (final_score_breakdown.get("score_version") if isinstance(final_score_breakdown, dict) else None)
        )
        base_global_rank = (
            signal_item.global_rank if signal_item is not None and signal_item.global_rank is not None else watch_item.rank
        )
        conclusion = signal_item.conclusion if signal_item is not None else None
        quote = latest_quotes.get(watch_item.etf_code)
        daily_entry_timing_label, daily_entry_timing_reason = _daily_entry_timing(signal_item)

        score_source = "daily" if base_score is not None else "unavailable"
        live_total_score = base_score
        intraday_adjustment_score: float | None = None
        score_contribution_reasons: list[str] = []
        if base_score is not None:
            score_contribution_reasons.append(f"日线基础分 {base_score:.1f}")
        if state.status == "lunch_break":
            live_label = ENTRY_STATE_LUNCH_BREAK
            live_reason = "当前是午休时段，不产生新的盘中买点；页面展示上午最近行情，日线买点只作参考。"
            score_contribution_reasons.append("午休时段，仅使用日线基础分和上午最近行情展示。")
        elif not is_open or not market_data.is_fresh_etf_decision_quote(quote, now):
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

        scored_rows.append(
            {
                "etf_code": watch_item.etf_code,
                "etf_name": name,
                "base_global_rank": base_global_rank,
                "conclusion": conclusion,
                "base_score": base_score,
                "live_total_score": live_total_score,
                "intraday_adjustment_score": intraday_adjustment_score,
                "score_source": score_source,
                "score_version": str(score_version or "legacy") if signal_item is not None else None,
                "score_breakdown": signal_breakdown,
                "score_contribution_reasons": score_contribution_reasons,
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
        watched_count=len(watchlist.items),
        total=len(filtered_rows),
        signal_as_of_date=watchlist.signal_as_of_date,
        signal_status=watchlist.signal_status,
        latest_run=market_data.etf_watch_run_out(latest_run) if latest_run is not None else None,
        snapshot=snapshot_metadata(source_snapshot),
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
                live_entry_timing_label=row["live_entry_timing_label"],
                live_entry_timing_reason=row["live_entry_timing_reason"],
                daily_entry_timing_label=row["daily_entry_timing_label"],
                daily_entry_timing_reason=row["daily_entry_timing_reason"],
                quote=row["quote"],
            )
            for row in selected
        ],
    )
