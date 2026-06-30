from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfDataHealth,
    EtfObservationPortfolioSnapshot,
    EtfOptimizedAllocationItem,
    EtfOptimizedAllocationSnapshot,
    EtfPriceHistory,
    EtfThemeProfile,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.portfolio_allocation import (
    PORTFOLIO_RISK_FLAGS_FORBIDDEN,
    PORTFOLIO_SINGLE_WEIGHT_CAP,
    PORTFOLIO_THEME_EXPOSURE_CAP,
)

OPTIMIZED_ALLOCATION_METHOD_SET = "stable_min_vol_risk_parity_v1"
OPTIMIZED_ALLOCATION_MIN_ASSETS = 4
OPTIMIZED_ALLOCATION_MIN_HISTORY_DAYS = 60
OPTIMIZED_ALLOCATION_LOOKBACK_DAYS = 120


@dataclass(frozen=True)
class OptimizerCandidate:
    code: str
    name: str
    score: float
    theme_group: str
    data_date: date | None
    expected_return: float | None
    volatility: float | None


def _positive(value: float | None, default: float) -> float:
    if value is None or value <= 0:
        return default
    return float(value)


def cap_theme_weights(
    raw_weights: dict[str, float],
    theme_by_code: dict[str, str],
    *,
    single_cap: float = PORTFOLIO_SINGLE_WEIGHT_CAP,
    theme_cap: float = PORTFOLIO_THEME_EXPOSURE_CAP,
    target_total: float = 1.0,
) -> dict[str, float] | None:
    cleaned = {code: max(0.0, float(weight)) for code, weight in raw_weights.items() if weight > 0}
    if len(cleaned) * single_cap + 1e-9 < target_total:
        return None
    theme_capacity: dict[str, float] = defaultdict(float)
    for code in cleaned:
        theme_capacity[theme_by_code.get(code) or "unknown"] += single_cap
    if sum(min(theme_cap, capacity) for capacity in theme_capacity.values()) + 1e-9 < target_total:
        return None

    weights = dict.fromkeys(cleaned, 0.0)
    theme_used: dict[str, float] = defaultdict(float)
    remaining = target_total
    ranked = sorted(cleaned.items(), key=lambda item: item[1], reverse=True)
    while remaining > 1e-6:
        changed = False
        for code, _raw in ranked:
            if remaining <= 1e-6:
                break
            theme = theme_by_code.get(code) or "unknown"
            capacity = min(single_cap - weights[code], theme_cap - theme_used[theme], remaining)
            if capacity <= 1e-6:
                continue
            step = min(capacity, remaining)
            weights[code] += step
            theme_used[theme] += step
            remaining -= step
            changed = True
        if not changed:
            break
    if remaining > 1e-4:
        return None
    return {code: round(weight, 6) for code, weight in weights.items() if weight > 1e-6}


def optimized_method_weights(
    method: str,
    candidates: list[OptimizerCandidate],
) -> dict[str, float] | None:
    if len(candidates) < OPTIMIZED_ALLOCATION_MIN_ASSETS:
        return None
    if method == "equal_weight":
        raw = {candidate.code: 1.0 for candidate in candidates}
    elif method == "minimum_volatility":
        raw = {
            candidate.code: 1.0 / (_positive(candidate.volatility, 0.03) ** 2)
            for candidate in candidates
        }
    elif method == "risk_parity":
        raw = {candidate.code: 1.0 / _positive(candidate.volatility, 0.03) for candidate in candidates}
    else:
        return None
    return cap_theme_weights(raw, {candidate.code: candidate.theme_group for candidate in candidates})


async def latest_optimized_allocation_snapshot(session: AsyncSession) -> EtfOptimizedAllocationSnapshot | None:
    return await session.scalar(
        select(EtfOptimizedAllocationSnapshot).order_by(
            EtfOptimizedAllocationSnapshot.created_at.desc(),
            EtfOptimizedAllocationSnapshot.id.desc(),
        )
    )


async def optimized_allocation_payload(
    session: AsyncSession,
    snapshot: EtfOptimizedAllocationSnapshot | None,
) -> dict[str, Any] | None:
    if snapshot is None:
        return {
            "id": None,
            "status": "waiting",
            "as_of_date": None,
            "generated_at": None,
            "method_set": OPTIMIZED_ALLOCATION_METHOD_SET,
            "data_window": {},
            "constraints": {
                "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
                "theme_exposure_cap": PORTFOLIO_THEME_EXPOSURE_CAP,
            },
            "summary": {},
            "unavailable_reason": "等待生成优化组合快照。",
            "methods": [],
            "research_only": True,
            "no_trade_instruction": True,
        }
    rows = (
        await session.scalars(
            select(EtfOptimizedAllocationItem)
            .where(EtfOptimizedAllocationItem.snapshot_id == snapshot.id)
            .order_by(EtfOptimizedAllocationItem.method.asc(), EtfOptimizedAllocationItem.target_weight.desc())
        )
    ).all()
    methods: dict[str, dict[str, Any]] = {}
    labels = {
        "equal_weight": "等权基线",
        "minimum_volatility": "最小波动",
        "risk_parity": "风险平价近似",
    }
    for row in rows:
        bucket = methods.setdefault(
            row.method,
            {
                "method": row.method,
                "label": labels.get(row.method, row.method),
                "status": "success",
                "items": [],
                "weight_sum": 0.0,
                "summary": {},
                "unavailable_reason": None,
            },
        )
        bucket["items"].append(
            {
                "method": row.method,
                "code": row.asset_code,
                "name": row.asset_name,
                "target_weight": row.target_weight,
                "expected_return": row.expected_return,
                "volatility": row.volatility,
                "theme_group": row.theme_group,
                "data_date": row.data_date,
                "explanation": row.explanation,
                "metrics": dict(row.metrics_json or {}),
            }
        )
        bucket["weight_sum"] = round(float(bucket["weight_sum"]) + float(row.target_weight or 0.0), 6)
    summary = dict(snapshot.summary_json or {})
    for method in methods.values():
        method["summary"] = dict((summary.get("methods") or {}).get(method["method"]) or {})
    return {
        "id": snapshot.id,
        "status": snapshot.status,
        "as_of_date": snapshot.as_of_date,
        "generated_at": snapshot.created_at,
        "method_set": snapshot.method_set,
        "evidence_contract_hash": snapshot.evidence_contract_hash,
        "data_window": dict(snapshot.data_window_json or {}),
        "constraints": dict(snapshot.constraints_json or {}),
        "summary": summary,
        "unavailable_reason": snapshot.unavailable_reason,
        "methods": list(methods.values()),
        "research_only": True,
        "no_trade_instruction": True,
    }


async def _latest_observation_snapshot(session: AsyncSession) -> EtfObservationPortfolioSnapshot | None:
    return await session.scalar(
        select(EtfObservationPortfolioSnapshot).order_by(
            EtfObservationPortfolioSnapshot.as_of_date.desc(),
            EtfObservationPortfolioSnapshot.id.desc(),
        )
    )


async def _eligible_candidates(session: AsyncSession, signal_run: ShortResearchSignalRun) -> list[OptimizerCandidate]:
    signal_rows = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(ShortResearchSignalItem.run_id == signal_run.id, ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF)
            .order_by(ShortResearchSignalItem.rank.asc())
            .limit(120)
        )
    ).all()
    codes = [row.asset_code for row in signal_rows]
    if not codes:
        return []
    etfs = {
        row.code: row
        for row in (
            await session.scalars(select(TradableEtf).where(TradableEtf.code.in_(codes)))
        ).all()
    }
    health = {
        row.etf_code: row
        for row in (
            await session.scalars(select(EtfDataHealth).where(EtfDataHealth.etf_code.in_(codes)))
        ).all()
    }
    themes = {
        row.etf_code: row
        for row in (
            await session.scalars(select(EtfThemeProfile).where(EtfThemeProfile.etf_code.in_(codes)))
        ).all()
    }
    start_date = signal_run.as_of_date - timedelta(days=OPTIMIZED_ALLOCATION_LOOKBACK_DAYS * 2)
    price_rows = (
        await session.scalars(
            select(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code.in_(codes), EtfPriceHistory.trade_date >= start_date)
            .order_by(EtfPriceHistory.etf_code.asc(), EtfPriceHistory.trade_date.asc())
        )
    ).all()
    closes: dict[str, list[EtfPriceHistory]] = defaultdict(list)
    for row in price_rows:
        closes[row.etf_code].append(row)

    candidates: list[OptimizerCandidate] = []
    for signal in signal_rows:
        risk_flags = set(signal.risk_flags_json or [])
        if risk_flags & set(PORTFOLIO_RISK_FLAGS_FORBIDDEN):
            continue
        data_health = health.get(signal.asset_code)
        if data_health and data_health.status in {"failed", "stale", "unavailable"}:
            continue
        rows = closes.get(signal.asset_code, [])
        if len(rows) < OPTIMIZED_ALLOCATION_MIN_HISTORY_DAYS:
            continue
        returns = [
            rows[index].close / rows[index - 1].close - 1.0
            for index in range(1, len(rows))
            if rows[index - 1].close > 0
        ]
        if len(returns) < OPTIMIZED_ALLOCATION_MIN_HISTORY_DAYS - 1:
            continue
        profile = themes.get(signal.asset_code)
        metrics = dict(signal.metrics_json or {})
        candidates.append(
            OptimizerCandidate(
                code=signal.asset_code,
                name=etfs.get(signal.asset_code).name if signal.asset_code in etfs else signal.asset_code,
                score=float(signal.total_score or 0.0),
                theme_group=profile.theme_group if profile else str(metrics.get("theme_group") or "unknown"),
                data_date=rows[-1].trade_date,
                expected_return=mean(returns[-60:]) if returns[-60:] else None,
                volatility=pstdev(returns[-60:]) if len(returns[-60:]) > 1 else None,
            )
        )
    return candidates


async def run_etf_optimized_allocation(session: AsyncSession) -> EtfOptimizedAllocationSnapshot:
    signal_run = await session.scalar(
        select(ShortResearchSignalRun)
        .where(ShortResearchSignalRun.status == "success")
        .order_by(ShortResearchSignalRun.as_of_date.desc(), ShortResearchSignalRun.id.desc())
    )
    observation_snapshot = await _latest_observation_snapshot(session)
    if signal_run is None:
        snapshot = EtfOptimizedAllocationSnapshot(
            status="unavailable",
            source_signal_run_id=None,
            observation_portfolio_snapshot_id=observation_snapshot.id if observation_snapshot else None,
            as_of_date=date.today(),
            method_set=OPTIMIZED_ALLOCATION_METHOD_SET,
            data_window_json={},
            constraints_json={
                "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
                "theme_exposure_cap": PORTFOLIO_THEME_EXPOSURE_CAP,
            },
            summary_json={"method_count": 0, "research_only": True},
            unavailable_reason="暂无 ETF 短线排序快照，不能生成优化组合。",
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    candidates = await _eligible_candidates(session, signal_run)
    constraints = {
        "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
        "theme_exposure_cap": PORTFOLIO_THEME_EXPOSURE_CAP,
        "min_assets": OPTIMIZED_ALLOCATION_MIN_ASSETS,
        "min_history_days": OPTIMIZED_ALLOCATION_MIN_HISTORY_DAYS,
    }
    contract_hash = stable_contract_hash(
        {
            "method_set": OPTIMIZED_ALLOCATION_METHOD_SET,
            "source_signal_run_id": signal_run.id,
            "as_of_date": signal_run.as_of_date.isoformat(),
            "constraints": constraints,
        }
    )
    methods = ("equal_weight", "minimum_volatility", "risk_parity")
    method_weights: dict[str, dict[str, float]] = {}
    for method in methods:
        weights = optimized_method_weights(method, candidates)
        if weights:
            method_weights[method] = weights

    unavailable_reason = None
    if not method_weights:
        unavailable_reason = (
            f"满足数据可靠性、历史长度和约束的 ETF 只有 {len(candidates)} 只，暂不能生成优化权重。"
        )
    latest_dates = [candidate.data_date for candidate in candidates if candidate.data_date is not None]
    data_window = {
        "as_of_date": signal_run.as_of_date.isoformat(),
        "source_signal_run_id": signal_run.id,
        "candidate_count": len(candidates),
        "latest_data_date": max(latest_dates).isoformat() if latest_dates else None,
        "lookback_days": OPTIMIZED_ALLOCATION_LOOKBACK_DAYS,
    }
    summary_methods: dict[str, Any] = {}
    for method, weights in method_weights.items():
        summary_methods[method] = {
            "weight_sum": round(sum(weights.values()), 6),
            "asset_count": len(weights),
            "max_single_weight": max(weights.values()) if weights else 0.0,
        }
    snapshot = EtfOptimizedAllocationSnapshot(
        status="success" if method_weights else "unavailable",
        source_signal_run_id=signal_run.id,
        observation_portfolio_snapshot_id=observation_snapshot.id if observation_snapshot else None,
        as_of_date=signal_run.as_of_date,
        method_set=OPTIMIZED_ALLOCATION_METHOD_SET,
        evidence_contract_hash=contract_hash,
        data_window_json=data_window,
        constraints_json=constraints,
        summary_json={
            "method_count": len(method_weights),
            "methods": summary_methods,
            "research_only": True,
            "no_trade_instruction": True,
        },
        unavailable_reason=unavailable_reason,
    )
    session.add(snapshot)
    await session.flush()
    by_code = {candidate.code: candidate for candidate in candidates}
    for method, weights in method_weights.items():
        for code, weight in weights.items():
            candidate = by_code[code]
            session.add(
                EtfOptimizedAllocationItem(
                    snapshot_id=snapshot.id,
                    method=method,
                    asset_code=code,
                    asset_name=candidate.name,
                    target_weight=weight,
                    expected_return=candidate.expected_return,
                    volatility=candidate.volatility,
                    theme_group=candidate.theme_group,
                    data_date=candidate.data_date,
                    explanation=f"{method} 按波动和约束生成，仅作规则组合对照。",
                    metrics_json={
                        "score": candidate.score,
                        "expected_return": candidate.expected_return,
                        "volatility": candidate.volatility,
                    },
                )
            )
    await session.commit()
    await session.refresh(snapshot)
    return snapshot
