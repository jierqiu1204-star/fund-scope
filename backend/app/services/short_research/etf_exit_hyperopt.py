from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import product
from statistics import pstdev
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfExitHyperoptItem,
    EtfExitHyperoptRun,
    EtfPriceHistory,
    EtfThemeProfile,
    TradableEtf,
    utcnow,
)
from app.services.short_research.dynamic_thresholds import clamp

RULE_VERSION = "etf_exit_hyperopt_v1"
CALIBRATION_RULE_VERSION = "etf_exit_calibration_v1"
EXECUTION_MODEL_DAILY_CLOSE = "daily_close"
EXECUTION_MODEL_INTRADAY_ALERT = "intraday_alert"
OBJECTIVE_STABILITY_FIRST = "stability_first"
STATUS_CANDIDATE = "candidate"
STATUS_APPROVED = "approved"
STATUS_EXPIRED = "expired"
STATUS_EVIDENCE_INSUFFICIENT = "evidence_insufficient"
STATUS_REJECTED = "rejected"
CONCLUSION_CANDIDATE = "候选待确认"
CONCLUSION_INSUFFICIENT = "证据不足"
CONCLUSION_OVERFIT = "疑似过拟合"
CONCLUSION_REJECTED = "不建议采用"

DEFAULT_SEARCH_SPACE: dict[str, list[float | int]] = {
    "hard_stop_multiplier": [1.2, 1.5, 1.8],
    "profit_start_multiplier": [0.9, 1.1, 1.3],
    "trailing_giveback_multiplier": [0.5, 0.65, 0.8],
    "trend_confirm_days": [1, 2],
    "take_profit_watch_pct": [3.0, 4.0],
}

_BUCKET_LIMITS: dict[str, tuple[float, float]] = {
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
class HyperoptPricePoint:
    trade_date: date
    close: float


@dataclass(frozen=True)
class HyperoptIntradayPoint:
    quote_time: datetime
    price: float
    decision_eligible: bool = True


@dataclass(frozen=True)
class HyperoptSeries:
    code: str
    name: str
    asset_bucket: str
    theme_group: str
    points: list[HyperoptPricePoint]


def parameter_grid(search_space: dict[str, list[float | int]] | None = None) -> list[dict[str, float | int]]:
    space = search_space or DEFAULT_SEARCH_SPACE
    keys = list(space.keys())
    return [dict(zip(keys, values, strict=True)) for values in product(*(space[key] for key in keys))]


def volatility_unit_pct(points: list[HyperoptPricePoint], asset_bucket: str) -> float:
    closes = [point.close for point in points if point.close > 0]
    returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes)) if closes[index - 1] > 0]
    recent = returns[-20:]
    raw = pstdev(recent) * 100 if len(recent) > 1 else 1.2
    lower, upper = _BUCKET_LIMITS.get(asset_bucket, _BUCKET_LIMITS["unknown"])
    return clamp(raw, lower, upper)


def thresholds_from_params(
    params: dict[str, float | int],
    *,
    volatility_pct: float,
) -> dict[str, float]:
    return {
        "hard_stop_pct": -clamp(float(params["hard_stop_multiplier"]) * volatility_pct, 1.2, 7.0),
        "profit_start_pct": clamp(float(params["profit_start_multiplier"]) * volatility_pct, 2.0, 6.0),
        "trailing_giveback_pct": clamp(float(params["trailing_giveback_multiplier"]) * volatility_pct, 1.0, 4.0),
        "take_profit_watch_pct": clamp(float(params["take_profit_watch_pct"]), 2.0, 8.0),
    }


def simulate_exit_rule(
    points: list[HyperoptPricePoint],
    params: dict[str, float | int],
    *,
    asset_bucket: str = "unknown",
) -> dict[str, Any]:
    valid = [point for point in points if point.close > 0]
    if len(valid) < 20:
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": None,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
        }

    vol_pct = volatility_unit_pct(valid, asset_bucket)
    thresholds = thresholds_from_params(params, volatility_pct=vol_pct)
    trend_confirm_days = max(1, int(params["trend_confirm_days"]))
    entry_price = valid[0].close
    peak_price = entry_price
    equity = 1.0
    peak_equity = 1.0
    max_drawdown = 0.0
    trade_returns: list[float] = []
    alert_count = 0
    false_exit_count = 0
    protected_exit_count = 0
    watch_alert_active = False
    negative_streak = 0
    index = 1

    while index < len(valid):
        current = valid[index].close
        previous = valid[index - 1].close
        if previous > 0 and current < previous:
            negative_streak += 1
        else:
            negative_streak = 0
        peak_price = max(peak_price, current)
        profit_pct = (current / entry_price - 1.0) * 100
        peak_profit_pct = (peak_price / entry_price - 1.0) * 100
        giveback_pct = peak_profit_pct - profit_pct
        in_trade_equity = equity * (current / entry_price)
        peak_equity = max(peak_equity, in_trade_equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, in_trade_equity / peak_equity - 1.0)

        exit_reason: str | None = None
        if profit_pct <= thresholds["hard_stop_pct"]:
            exit_reason = "hard_stop"
        elif (
            peak_profit_pct >= thresholds["profit_start_pct"]
            and giveback_pct >= thresholds["trailing_giveback_pct"]
        ):
            exit_reason = "trailing_take_profit"
        elif negative_streak >= trend_confirm_days and profit_pct < 0:
            exit_reason = "trend_weakening"
        elif profit_pct >= thresholds["take_profit_watch_pct"] and not watch_alert_active:
            alert_count += 1
            watch_alert_active = True

        if exit_reason is not None:
            realized = current / entry_price - 1.0
            trade_returns.append(realized)
            alert_count += 1
            future = valid[index + 1 : index + 6]
            if future:
                max_future_return = max(point.close / current - 1.0 for point in future)
                min_future_return = min(point.close / current - 1.0 for point in future)
                if max_future_return * 100 >= max(1.0, vol_pct):
                    false_exit_count += 1
                if min_future_return * 100 <= -max(1.0, vol_pct):
                    protected_exit_count += 1
            equity *= 1.0 + realized
            peak_equity = max(peak_equity, equity)
            index += 1
            if index >= len(valid):
                break
            entry_price = valid[index].close
            peak_price = entry_price
            watch_alert_active = False
            negative_streak = 0
        index += 1

    final_return = valid[-1].close / entry_price - 1.0
    total_return = equity * (1.0 + final_return) - 1.0
    trade_count = len(trade_returns)
    win_rate = sum(1 for value in trade_returns if value > 0) / trade_count if trade_count else None
    avg_trade_return = sum(trade_returns) / trade_count if trade_count else None
    turnover = trade_count / max(len(valid) / 20, 1)
    return {
        "sample_count": 1,
        "trade_count": trade_count,
        "alert_count": alert_count,
        "win_rate": win_rate,
        "total_return": total_return,
        "avg_trade_return": avg_trade_return,
        "max_drawdown": max_drawdown,
        "turnover": turnover,
        "unfilled_count": 0,
        "false_exit_count": false_exit_count,
        "protected_exit_count": protected_exit_count,
        "missed_upside_rate": false_exit_count / trade_count if trade_count else None,
        "protected_exit_rate": protected_exit_count / trade_count if trade_count else None,
        "volatility_unit_pct": vol_pct,
        "thresholds": thresholds,
    }


def simulate_intraday_exit_rule(
    intraday_points: list[HyperoptIntradayPoint],
    params: dict[str, float | int],
    *,
    asset_bucket: str = "unknown",
    daily_points: list[HyperoptPricePoint] | None = None,
    allow_daily_fallback: bool = False,
) -> dict[str, Any]:
    eligible_points = [
        point
        for point in sorted(intraday_points, key=lambda item: item.quote_time)
        if point.decision_eligible and point.price > 0
    ]
    if not eligible_points:
        if allow_daily_fallback and daily_points:
            result = simulate_exit_rule(daily_points, params, asset_bucket=asset_bucket)
            result.update(
                {
                    "execution_model": EXECUTION_MODEL_DAILY_CLOSE,
                    "source_reliability": "verified_daily_close",
                    "missing_intraday_evidence_count": 0,
                    "no_lookahead_exclusions": [],
                }
            )
            return result
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": None,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
            "execution_model": EXECUTION_MODEL_INTRADAY_ALERT,
            "source_reliability": "unavailable",
            "missing_intraday_evidence_count": 1,
            "no_lookahead_exclusions": ["缺少可决策盘中行情，未用日线收盘价替代。"],
        }

    replay_points = [
        HyperoptPricePoint(point.quote_time.date(), point.price)
        for point in eligible_points
    ]
    result = simulate_exit_rule(replay_points, params, asset_bucket=asset_bucket)
    result.update(
        {
            "execution_model": EXECUTION_MODEL_INTRADAY_ALERT,
            "source_reliability": "verified_intraday",
            "missing_intraday_evidence_count": 0,
            "no_lookahead_exclusions": [],
        }
    )
    return result


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [item for item in results if item.get("sample_count")]
    if not usable:
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": None,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
        }
    sample_count = sum(int(item.get("sample_count") or 0) for item in usable)
    trade_count = sum(int(item.get("trade_count") or 0) for item in usable)
    alert_count = sum(int(item.get("alert_count") or 0) for item in usable)
    unfilled_count = sum(int(item.get("unfilled_count") or 0) for item in usable)
    false_exit_count = sum(int(item.get("false_exit_count") or 0) for item in usable)
    protected_exit_count = sum(int(item.get("protected_exit_count") or 0) for item in usable)
    total_returns = [float(item["total_return"]) for item in usable if item.get("total_return") is not None]
    avg_trade_returns = [
        float(item["avg_trade_return"]) for item in usable if item.get("avg_trade_return") is not None
    ]
    drawdowns = [float(item["max_drawdown"]) for item in usable if item.get("max_drawdown") is not None]
    turnovers = [float(item["turnover"]) for item in usable if item.get("turnover") is not None]
    win_numerators = [
        float(item["win_rate"]) * int(item.get("trade_count") or 0)
        for item in usable
        if item.get("win_rate") is not None and int(item.get("trade_count") or 0) > 0
    ]
    return {
        "sample_count": sample_count,
        "trade_count": trade_count,
        "alert_count": alert_count,
        "win_rate": sum(win_numerators) / trade_count if trade_count and win_numerators else None,
        "total_return": sum(total_returns) / len(total_returns) if total_returns else None,
        "avg_trade_return": sum(avg_trade_returns) / len(avg_trade_returns) if avg_trade_returns else None,
        "max_drawdown": min(drawdowns) if drawdowns else None,
        "turnover": sum(turnovers) / len(turnovers) if turnovers else None,
        "unfilled_count": unfilled_count,
        "false_exit_count": false_exit_count,
        "protected_exit_count": protected_exit_count,
        "missed_upside_rate": false_exit_count / trade_count if trade_count else None,
        "protected_exit_rate": protected_exit_count / trade_count if trade_count else None,
    }


def stability_score(train_metrics: dict[str, Any], oos_metrics: dict[str, Any]) -> float:
    oos_return = float(oos_metrics.get("total_return") or 0.0)
    oos_drawdown = abs(float(oos_metrics.get("max_drawdown") or 0.0))
    train_return = float(train_metrics.get("total_return") or 0.0)
    trade_count = int(oos_metrics.get("trade_count") or 0)
    alert_count = int(oos_metrics.get("alert_count") or 0)
    sample_count = max(int(oos_metrics.get("sample_count") or 0), 1)
    turnover = float(oos_metrics.get("turnover") or 0.0)
    missed_upside_rate = float(oos_metrics.get("missed_upside_rate") or 0.0)
    protected_exit_rate = float(oos_metrics.get("protected_exit_rate") or 0.0)
    overfit_penalty = max(0.0, train_return - oos_return - 0.08) * 100
    sparse_penalty = 18.0 if trade_count < 3 else 0.0
    return round(
        100
        + oos_return * 160
        - oos_drawdown * 260
        - turnover * 5
        - (alert_count / sample_count) * 3
        - missed_upside_rate * 18
        + protected_exit_rate * 8
        - overfit_penalty
        - sparse_penalty,
        4,
    )


def classify_hyperopt_candidate(train_metrics: dict[str, Any], oos_metrics: dict[str, Any]) -> tuple[str, str]:
    sample_count = int(oos_metrics.get("sample_count") or 0)
    trade_count = int(oos_metrics.get("trade_count") or 0)
    train_return = train_metrics.get("total_return")
    oos_return = oos_metrics.get("total_return")
    train_drawdown = train_metrics.get("max_drawdown")
    oos_drawdown = oos_metrics.get("max_drawdown")
    if sample_count < 3 or trade_count < 2:
        return STATUS_EVIDENCE_INSUFFICIENT, CONCLUSION_INSUFFICIENT
    if train_return is not None and oos_return is not None and float(oos_return) < float(train_return) - 0.12:
        return STATUS_REJECTED, CONCLUSION_OVERFIT
    if train_drawdown is not None and oos_drawdown is not None and float(oos_drawdown) < float(train_drawdown) - 0.08:
        return STATUS_REJECTED, CONCLUSION_OVERFIT
    if oos_return is not None and float(oos_return) < -0.03:
        return STATUS_REJECTED, CONCLUSION_REJECTED
    if oos_drawdown is not None and float(oos_drawdown) < -0.12:
        return STATUS_REJECTED, CONCLUSION_REJECTED
    return STATUS_CANDIDATE, CONCLUSION_CANDIDATE


def _split_points(points: list[HyperoptPricePoint]) -> tuple[list[HyperoptPricePoint], list[HyperoptPricePoint]]:
    split_index = max(20, int(len(points) * 0.7))
    return points[:split_index], points[split_index:]


def evaluate_parameter_set(series: list[HyperoptSeries], params: dict[str, float | int]) -> tuple[dict[str, Any], dict[str, Any]]:
    train_results: list[dict[str, Any]] = []
    oos_results: list[dict[str, Any]] = []
    for item in series:
        train_points, oos_points = _split_points(item.points)
        train_results.append(simulate_exit_rule(train_points, params, asset_bucket=item.asset_bucket))
        if len(oos_points) >= 20:
            oos_results.append(simulate_exit_rule(oos_points, params, asset_bucket=item.asset_bucket))
    return aggregate_metrics(train_results), aggregate_metrics(oos_results)


def rolling_validation_metrics(
    series: list[HyperoptSeries],
    params: dict[str, float | int],
    *,
    window_size: int = 60,
    step_size: int = 20,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in series:
        points = item.points
        if len(points) < window_size:
            continue
        for start in range(0, len(points) - window_size + 1, step_size):
            window = points[start : start + window_size]
            results.append(simulate_exit_rule(window, params, asset_bucket=item.asset_bucket))

    usable = [row for row in results if row.get("total_return") is not None]
    if not usable:
        return {
            "window_count": 0,
            "stable_window_rate": None,
            "worst_window_return": None,
            "worst_window_drawdown": None,
            "avg_window_return": None,
        }

    returns = [float(row["total_return"]) for row in usable]
    drawdowns = [float(row["max_drawdown"]) for row in usable if row.get("max_drawdown") is not None]
    stable_count = sum(
        1
        for row in usable
        if float(row.get("total_return") or 0.0) >= -0.02
        and float(row.get("max_drawdown") or 0.0) >= -0.08
    )
    return {
        "window_count": len(usable),
        "stable_window_rate": stable_count / len(usable),
        "worst_window_return": min(returns),
        "worst_window_drawdown": min(drawdowns) if drawdowns else None,
        "avg_window_return": sum(returns) / len(returns),
    }


def confidence_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    sample_count = int(metrics.get("sample_count") or 0)
    trade_count = int(metrics.get("trade_count") or 0)
    raw_win_rate = metrics.get("win_rate")
    prior_sample = 20
    prior_win_rate = 0.5
    if raw_win_rate is None or trade_count <= 0:
        shrunk_win_rate = None
    else:
        shrunk_win_rate = (float(raw_win_rate) * trade_count + prior_win_rate * prior_sample) / (
            trade_count + prior_sample
        )
    evidence_score = min(1.0, sample_count / 20) * 0.5 + min(1.0, trade_count / 12) * 0.5
    if evidence_score >= 0.8:
        level = "较充分"
    elif evidence_score >= 0.45:
        level = "一般"
    else:
        level = "样本不足"
    return {
        "level": level,
        "evidence_score": round(evidence_score, 4),
        "shrunk_win_rate": shrunk_win_rate,
        "sample_count": sample_count,
        "trade_count": trade_count,
        "prior_win_rate": prior_win_rate,
        "prior_sample": prior_sample,
    }


def calibration_contract_hash(*, search_space: dict[str, list[float | int]], objective: str) -> str:
    payload = {
        "rule_version": RULE_VERSION,
        "calibration_rule_version": CALIBRATION_RULE_VERSION,
        "execution_model": EXECUTION_MODEL_DAILY_CLOSE,
        "objective": objective,
        "search_space": search_space,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def best_candidate_for_bucket(series: list[HyperoptSeries]) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for params in parameter_grid():
        train_metrics, oos_metrics = evaluate_parameter_set(series, params)
        score = stability_score(train_metrics, oos_metrics)
        status, conclusion = classify_hyperopt_candidate(train_metrics, oos_metrics)
        candidate = {
            "params": params,
            "train_metrics": train_metrics,
            "out_of_sample_metrics": oos_metrics,
            "score": score,
            "status": status,
            "conclusion": conclusion,
            "sample_count": int(oos_metrics.get("sample_count") or 0),
            "trade_count": int(oos_metrics.get("trade_count") or 0),
        }
        if best is None or score > float(best["score"]):
            best = candidate
    assert best is not None
    best["rolling_metrics"] = rolling_validation_metrics(series, best["params"])
    best["confidence"] = confidence_summary(best["out_of_sample_metrics"])
    best["source_reliability"] = "verified_daily_close"
    return best


async def _load_series(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    max_assets: int,
) -> list[HyperoptSeries]:
    etfs = (
        await session.execute(
            select(TradableEtf, EtfThemeProfile)
            .outerjoin(EtfThemeProfile, EtfThemeProfile.etf_code == TradableEtf.code)
            .where(TradableEtf.is_short_term_eligible.is_(True))
            .order_by(TradableEtf.code.asc())
            .limit(max_assets)
        )
    ).all()
    codes = [row[0].code for row in etfs]
    if not codes:
        return []
    history_rows = (
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
    history_by_code: dict[str, list[HyperoptPricePoint]] = {}
    for row in history_rows:
        history_by_code.setdefault(row.etf_code, []).append(HyperoptPricePoint(row.trade_date, float(row.close)))

    series: list[HyperoptSeries] = []
    for etf, profile in etfs:
        points = history_by_code.get(etf.code, [])
        if len(points) < 40:
            continue
        series.append(
            HyperoptSeries(
                code=etf.code,
                name=etf.name,
                asset_bucket=(profile.asset_bucket if profile else etf.asset_class) or "unknown",
                theme_group=(profile.theme_group if profile else "unknown") or "unknown",
                points=points,
            )
        )
    return series


def _bucket_series(series: list[HyperoptSeries]) -> dict[tuple[str, str], list[HyperoptSeries]]:
    buckets: dict[tuple[str, str], list[HyperoptSeries]] = {("all", "all"): list(series)}
    for item in series:
        buckets.setdefault(("asset_bucket", item.asset_bucket or "unknown"), []).append(item)
        buckets.setdefault(("theme_group", item.theme_group or "unknown"), []).append(item)
        vol = volatility_unit_pct(item.points, item.asset_bucket)
        if vol < 1.0:
            volatility_bucket = "low_volatility"
        elif vol < 2.5:
            volatility_bucket = "mid_volatility"
        else:
            volatility_bucket = "high_volatility"
        buckets.setdefault(("volatility", volatility_bucket), []).append(item)
    return {key: value for key, value in buckets.items() if key == ("all", "all") or len(value) >= 3}


async def run_etf_exit_hyperopt(
    session: AsyncSession,
    *,
    days: int = 730,
    max_assets: int = 300,
    objective: str = OBJECTIVE_STABILITY_FIRST,
) -> EtfExitHyperoptRun:
    latest_date = await session.scalar(select(EtfPriceHistory.trade_date).order_by(desc(EtfPriceHistory.trade_date)).limit(1))
    as_of_date = latest_date or date.today()
    start_date = as_of_date - timedelta(days=days)
    train_cutoff = start_date + timedelta(days=int(days * 0.7))
    run = EtfExitHyperoptRun(
        status="running",
        started_at=utcnow(),
        as_of_date=as_of_date,
        objective=objective,
        rule_version=RULE_VERSION,
        calibration_rule_version=CALIBRATION_RULE_VERSION,
        execution_model=EXECUTION_MODEL_DAILY_CLOSE,
        contract_hash=calibration_contract_hash(search_space=DEFAULT_SEARCH_SPACE, objective=objective),
        data_cutoff=utcnow(),
        train_range_json={"start_date": start_date.isoformat(), "end_date": train_cutoff.isoformat()},
        out_of_sample_range_json={"start_date": train_cutoff.isoformat(), "end_date": as_of_date.isoformat()},
        search_space_json=DEFAULT_SEARCH_SPACE,
        bucket_summary_json={},
        summary_json={},
        created_at=utcnow(),
    )
    session.add(run)
    await session.flush()

    try:
        series = await _load_series(session, start_date=start_date, end_date=as_of_date, max_assets=max_assets)
        buckets = _bucket_series(series)
        item_count = 0
        candidate_count = 0
        rejected_count = 0
        evidence_insufficient_count = 0
        bucket_summary: dict[str, int] = {}
        for (bucket_type, bucket_key), bucket_items in buckets.items():
            best = best_candidate_for_bucket(bucket_items)
            if best["status"] == STATUS_CANDIDATE:
                candidate_count += 1
            elif best["status"] == STATUS_EVIDENCE_INSUFFICIENT:
                evidence_insufficient_count += 1
            else:
                rejected_count += 1
            item_count += 1
            bucket_summary[bucket_type] = bucket_summary.get(bucket_type, 0) + 1
            session.add(
                EtfExitHyperoptItem(
                    run_id=run.id,
                    bucket_type=bucket_type,
                    bucket_key=bucket_key,
                    status=best["status"],
                    conclusion=best["conclusion"],
                    parameter_json=best["params"],
                    train_metrics_json=best["train_metrics"],
                    out_of_sample_metrics_json=best["out_of_sample_metrics"],
                    rolling_metrics_json=best["rolling_metrics"],
                    confidence_json=best["confidence"],
                    source_reliability=best["source_reliability"],
                    score=best["score"],
                    sample_count=best["sample_count"],
                    trade_count=best["trade_count"],
                    created_at=utcnow(),
                )
            )
        run.status = "success"
        run.finished_at = utcnow()
        run.bucket_summary_json = bucket_summary
        run.summary_json = {
            "objective": objective,
            "rule_version": RULE_VERSION,
            "calibration_rule_version": CALIBRATION_RULE_VERSION,
            "execution_model": EXECUTION_MODEL_DAILY_CLOSE,
            "contract_hash": run.contract_hash,
            "asset_count": len(series),
            "bucket_count": item_count,
            "candidate_count": candidate_count,
            "rejected_count": rejected_count,
            "evidence_insufficient_count": evidence_insufficient_count,
            "parameter_count": len(parameter_grid()),
            "auto_applied": False,
            "research_only": True,
            "no_trade_instruction": True,
        }
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_message = str(exc)
        run.summary_json = {"objective": objective, "research_only": True, "auto_applied": False}
        raise
    finally:
        await session.commit()
        await session.refresh(run)
    return run


async def latest_etf_exit_hyperopt_run(session: AsyncSession) -> EtfExitHyperoptRun | None:
    return await session.scalar(
        select(EtfExitHyperoptRun).order_by(desc(EtfExitHyperoptRun.finished_at), desc(EtfExitHyperoptRun.id)).limit(1)
    )


async def etf_exit_hyperopt_payload(session: AsyncSession, run: EtfExitHyperoptRun) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(EtfExitHyperoptItem)
            .where(EtfExitHyperoptItem.run_id == run.id)
            .order_by(EtfExitHyperoptItem.score.desc(), EtfExitHyperoptItem.id.asc())
        )
    ).all()
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "as_of_date": run.as_of_date,
        "objective": run.objective,
        "rule_version": run.rule_version,
        "calibration_rule_version": run.calibration_rule_version,
        "execution_model": run.execution_model,
        "contract_hash": run.contract_hash,
        "data_cutoff": run.data_cutoff,
        "train_range": dict(run.train_range_json or {}),
        "out_of_sample_range": dict(run.out_of_sample_range_json or {}),
        "search_space": dict(run.search_space_json or {}),
        "bucket_summary": dict(run.bucket_summary_json or {}),
        "summary": dict(run.summary_json or {}),
        "error_message": run.error_message,
        "research_only": True,
        "no_trade_instruction": True,
        "items": [
            {
                "id": item.id,
                "bucket_type": item.bucket_type,
                "bucket_key": item.bucket_key,
                "status": item.status,
                "conclusion": item.conclusion,
                "parameters": dict(item.parameter_json or {}),
                "train_metrics": dict(item.train_metrics_json or {}),
                "out_of_sample_metrics": dict(item.out_of_sample_metrics_json or {}),
                "rolling_metrics": dict(item.rolling_metrics_json or {}),
                "confidence": dict(item.confidence_json or {}),
                "source_reliability": item.source_reliability,
                "score": item.score,
                "sample_count": item.sample_count,
                "trade_count": item.trade_count,
                "approved_at": item.approved_at,
                "created_at": item.created_at,
            }
            for item in rows
        ],
    }
