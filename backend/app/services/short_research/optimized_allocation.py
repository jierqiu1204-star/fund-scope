from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import mean, pstdev
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfObservationPortfolioItem,
    EtfObservationPortfolioSnapshot,
    EtfOptimizedAllocationItem,
    EtfOptimizedAllocationSnapshot,
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.market_data import ASIA_SHANGHAI
from app.services.portfolio_allocation import (
    BLACK_LITTERMAN_METHOD,
    PORTFOLIO_CORRELATION_CLUSTER_CAP,
    PORTFOLIO_LAYER_DEFENSIVE,
    PORTFOLIO_LAYER_PRIMARY,
    PORTFOLIO_LAYER_SATELLITE,
    PORTFOLIO_MODE_CASH_WAIT,
    PORTFOLIO_RISK_BUDGET_VERSION,
    PORTFOLIO_RISK_FLAGS_FORBIDDEN,
    PORTFOLIO_SATELLITE_EXPOSURE_CAP,
    PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
    PORTFOLIO_SINGLE_WEIGHT_CAP,
    PORTFOLIO_THEME_EXPOSURE_CAP,
    BlackLittermanCandidate,
    MarketRiskStateResult,
    PortfolioRiskBudgetResult,
    apply_portfolio_risk_budget,
    black_litterman_covariance_summary,
    build_black_litterman_allocation,
    portfolio_asset_risk_cap,
    portfolio_risk_budget_manifest,
    proportional_capped_redistribution,
)
from app.services.portfolio_risk_shadow import (
    PortfolioRiskAssetInput,
    build_portfolio_risk_shadow,
)
from app.services.short_research.ranking_surfaces import (
    ACTIONABLE_CONTRACT_ID,
    actionable_rank_manifest,
    validate_rank_derived_action_context,
)
from app.services.short_research.snapshot_selector import (
    CanonicalSnapshotSelection,
    required_etf_snapshot_trade_date,
    resolve_current_etf_ranking_surface_snapshot,
)

OPTIMIZED_ALLOCATION_METHOD_SET = "stable_min_vol_risk_parity_black_litterman_v2_risk_budget"
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
    returns: tuple[float, ...] = ()
    dated_returns: tuple[tuple[date, float], ...] = ()
    clone_group_id: str | None = None
    asset_bucket: str | None = None
    adjusted_input_hash: str | None = None
    observation_label: str = ""
    entry_timing_label: str = ""
    evidence_confidence: float | None = None
    prior_weight: float | None = None
    prior_source: str | None = None
    data_reliability: str = "verified"
    liquidity_score: float | None = None
    market_regime: str | None = None
    validation_sample_count: int | None = None
    portfolio_layer: str = PORTFOLIO_LAYER_PRIMARY
    risk_flags: tuple[str, ...] = ()
    max_drawdown_60d: float | None = None
    display_eligible: bool = True


def _positive(value: float | None, default: float) -> float:
    if value is None or value <= 0:
        return default
    return float(value)


def _metric_float(metrics: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = metrics.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    return None


def _candidate_input_hash(candidates: list[OptimizerCandidate]) -> str:
    return stable_contract_hash(
        {
            "schema_version": "optimizer_candidate_inputs_v1",
            "candidates": [
                asdict(candidate)
                for candidate in sorted(candidates, key=lambda item: item.code)
            ],
        }
    )


def cap_theme_weights(
    raw_weights: dict[str, float],
    theme_by_code: dict[str, str],
    *,
    single_cap: float = PORTFOLIO_SINGLE_WEIGHT_CAP,
    theme_cap: float = PORTFOLIO_THEME_EXPOSURE_CAP,
    target_total: float = 1.0,
) -> dict[str, float] | None:
    cleaned = {code: max(0.0, float(weight)) for code, weight in raw_weights.items() if weight > 0}
    if not cleaned:
        return None
    theme_members: dict[str, list[str]] = defaultdict(list)
    for code in cleaned:
        theme_members[theme_by_code.get(code) or "unknown"].append(code)
    allocation = proportional_capped_redistribution(
        cleaned,
        {code: single_cap for code in cleaned},
        group_caps={
            f"theme:{theme}": (members, theme_cap)
            for theme, members in theme_members.items()
        },
        target_total=target_total,
    )
    return allocation.weights or None


def _optimized_raw_weights(
    method: str,
    candidates: list[OptimizerCandidate],
) -> dict[str, float] | None:
    if len(candidates) < OPTIMIZED_ALLOCATION_MIN_ASSETS:
        return None
    if method == "equal_weight":
        return {candidate.code: 1.0 for candidate in candidates}
    if method == "minimum_volatility":
        return {
            candidate.code: 1.0 / (_positive(candidate.volatility, 0.03) ** 2)
            for candidate in candidates
        }
    if method == "risk_parity":
        return {
            candidate.code: 1.0 / _positive(candidate.volatility, 0.03)
            for candidate in candidates
        }
    return None


def optimized_method_weights(
    method: str,
    candidates: list[OptimizerCandidate],
) -> dict[str, float] | None:
    raw = _optimized_raw_weights(method, candidates)
    if raw is None:
        return None
    return cap_theme_weights(raw, {candidate.code: candidate.theme_group for candidate in candidates})


@dataclass(frozen=True)
class SourcePortfolioRiskPolicy:
    status: str
    market_risk: MarketRiskStateResult
    max_total_exposure: float
    source_allocation_contract_hash: str | None
    unavailable_reason: str | None = None


def _source_portfolio_risk_policy(
    snapshot: EtfObservationPortfolioSnapshot | None,
) -> SourcePortfolioRiskPolicy:
    manifest = portfolio_risk_budget_manifest()
    unavailable_market = MarketRiskStateResult(
        state=PORTFOLIO_MODE_CASH_WAIT,
        status="unavailable",
        metrics={},
        unavailable_reasons=("source_observation_risk_policy_unavailable",),
        contract_version=PORTFOLIO_RISK_BUDGET_VERSION,
        contract_hash=str(manifest["contract_hash"]),
    )
    if snapshot is None:
        return SourcePortfolioRiskPolicy(
            status="unavailable",
            market_risk=unavailable_market,
            max_total_exposure=0.0,
            source_allocation_contract_hash=None,
            unavailable_reason="同源观察组合快照不存在，不能推断优化组合风险状态。",
        )
    summary = dict(snapshot.summary_json or {})
    constraints = dict(summary.get("constraints_used") or summary.get("constraint_summary") or {})
    risk_summary = dict(summary.get("risk_summary") or {})
    allocation_contract = dict(summary.get("allocation_contract") or {})
    version = constraints.get("risk_budget_version")
    contract_hash = constraints.get("risk_budget_hash")
    state = str(summary.get("market_regime") or PORTFOLIO_MODE_CASH_WAIT)
    market_status = str(constraints.get("market_risk_status") or "unavailable")
    if (
        version != PORTFOLIO_RISK_BUDGET_VERSION
        or contract_hash != manifest["contract_hash"]
        or market_status != "ready"
    ):
        return SourcePortfolioRiskPolicy(
            status="unavailable",
            market_risk=unavailable_market,
            max_total_exposure=0.0,
            source_allocation_contract_hash=allocation_contract.get("contract_hash"),
            unavailable_reason="同源观察组合缺少当前版本的市场风险和现金约束证据。",
        )
    try:
        max_total_exposure = float(
            summary.get("target_invested_weight", 1.0 - float(summary.get("cash_weight", 1.0)))
        )
    except (TypeError, ValueError):
        max_total_exposure = -1.0
    if not math.isfinite(max_total_exposure) or not 0.0 <= max_total_exposure <= 1.0:
        return SourcePortfolioRiskPolicy(
            status="unavailable",
            market_risk=unavailable_market,
            max_total_exposure=0.0,
            source_allocation_contract_hash=allocation_contract.get("contract_hash"),
            unavailable_reason="同源观察组合的目标风险暴露无效。",
        )
    return SourcePortfolioRiskPolicy(
        status="ready",
        market_risk=MarketRiskStateResult(
            state=state,
            status="ready" if state != PORTFOLIO_MODE_CASH_WAIT else "unavailable",
            metrics=dict(risk_summary.get("market_state_metrics") or {}),
            unavailable_reasons=("source_market_state_cash_wait",)
            if state == PORTFOLIO_MODE_CASH_WAIT
            else (),
            contract_version=PORTFOLIO_RISK_BUDGET_VERSION,
            contract_hash=str(manifest["contract_hash"]),
        ),
        max_total_exposure=max_total_exposure,
        source_allocation_contract_hash=allocation_contract.get("contract_hash"),
    )


def _candidate_risk_budget(
    raw_weights: dict[str, float],
    candidates: list[OptimizerCandidate],
    source_policy: SourcePortfolioRiskPolicy,
) -> PortfolioRiskBudgetResult:
    by_code = {candidate.code: candidate for candidate in candidates}
    layers = {
        code: by_code[code].portfolio_layer
        for code in raw_weights
        if code in by_code
    }
    caps = {}
    for code, layer in layers.items():
        candidate = by_code[code]
        base_cap = (
            PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP
            if layer == PORTFOLIO_LAYER_SATELLITE
            else PORTFOLIO_SINGLE_WEIGHT_CAP
        )
        caps[code] = portfolio_asset_risk_cap(
            base_cap=base_cap,
            risk_flags=candidate.risk_flags,
            volatility_20d=candidate.volatility,
            max_drawdown_60d=candidate.max_drawdown_60d,
            display_eligible=candidate.display_eligible,
        )
    return apply_portfolio_risk_budget(
        raw_weights,
        layer_by_code=layers,
        theme_by_code={
            code: by_code[code].theme_group
            for code in raw_weights
            if code in by_code
        },
        returns_by_code={
            code: by_code[code].returns
            for code in raw_weights
            if code in by_code
        },
        market_risk=source_policy.market_risk,
        individual_caps=caps,
        max_total_exposure=source_policy.max_total_exposure,
    )


async def _current_canonical_etf_selection(session: AsyncSession) -> CanonicalSnapshotSelection:
    return await resolve_current_etf_ranking_surface_snapshot(
        session,
        required_trade_date=required_etf_snapshot_trade_date(),
        ranking_surface="actionable",
    )


async def latest_optimized_allocation_snapshot(session: AsyncSession) -> EtfOptimizedAllocationSnapshot | None:
    canonical_run = (await _current_canonical_etf_selection(session)).run
    if canonical_run is None:
        return None
    return cast(
        EtfOptimizedAllocationSnapshot | None,
        await session.scalar(
            select(EtfOptimizedAllocationSnapshot)
            .where(EtfOptimizedAllocationSnapshot.source_signal_run_id == canonical_run.id)
            .order_by(
                EtfOptimizedAllocationSnapshot.created_at.desc(),
                EtfOptimizedAllocationSnapshot.id.desc(),
            )
        ),
    )


async def optimized_allocation_payload(
    session: AsyncSession,
    snapshot: EtfOptimizedAllocationSnapshot | None,
    *,
    expected_source_signal_run_id: int | None,
) -> dict[str, Any] | None:
    if snapshot is not None and snapshot.source_signal_run_id != expected_source_signal_run_id:
        snapshot = None
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
        BLACK_LITTERMAN_METHOD: "Black-Litterman 对照",
    }
    summary = dict(snapshot.summary_json or {})
    for method_key, method_summary in (summary.get("methods") or {}).items():
        methods.setdefault(
            method_key,
            {
                "method": method_key,
                "label": labels.get(method_key, method_key),
                "status": str(method_summary.get("status") or "success"),
                "items": [],
                "weight_sum": 0.0,
                "summary": dict(method_summary),
                "unavailable_reason": method_summary.get("unavailable_reason"),
            },
        )
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
    for method in methods.values():
        method["summary"] = dict((summary.get("methods") or {}).get(method["method"]) or method["summary"] or {})
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


async def _latest_observation_snapshot(
    session: AsyncSession,
    *,
    source_signal_run_id: int,
) -> EtfObservationPortfolioSnapshot | None:
    return cast(
        EtfObservationPortfolioSnapshot | None,
        await session.scalar(
            select(EtfObservationPortfolioSnapshot)
            .where(EtfObservationPortfolioSnapshot.source_signal_run_id == source_signal_run_id)
            .order_by(
                EtfObservationPortfolioSnapshot.as_of_date.desc(),
                EtfObservationPortfolioSnapshot.id.desc(),
            )
        ),
    )


async def _observation_snapshot_weights(
    session: AsyncSession,
    snapshot: EtfObservationPortfolioSnapshot | None,
) -> dict[str, float] | None:
    if snapshot is None:
        return None
    rows = (
        await session.execute(
            select(
                EtfObservationPortfolioItem.asset_code,
                EtfObservationPortfolioItem.target_weight,
            ).where(
                EtfObservationPortfolioItem.snapshot_id == snapshot.id,
                EtfObservationPortfolioItem.target_weight > 0,
            )
        )
    ).all()
    return {str(code): float(weight) for code, weight in rows}


def _source_cutoff_utc(signal_run: ShortResearchSignalRun) -> datetime | None:
    cutoff = signal_run.data_cutoff
    if cutoff is None:
        return None
    local = (
        cutoff.replace(tzinfo=ASIA_SHANGHAI)
        if cutoff.tzinfo is None
        else cutoff.astimezone(ASIA_SHANGHAI)
    )
    return local.astimezone(UTC).replace(tzinfo=None)


async def _eligible_candidates(session: AsyncSession, signal_run: ShortResearchSignalRun) -> list[OptimizerCandidate]:
    stored_rows = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(
                ShortResearchSignalItem.run_id == signal_run.id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
            )
        )
    ).all()
    actionable_manifest = actionable_rank_manifest()
    actionable_rows: list[tuple[int, float, ShortResearchSignalItem]] = []
    action_date = signal_run.as_of_trade_date or signal_run.as_of_date
    for row in stored_rows:
        metrics = dict(row.metrics_json or {})
        rank = metrics.get("actionable_rank")
        score = metrics.get("actionable_score")
        action_decision = validate_rank_derived_action_context(
            metrics,
            required_as_of_date=action_date,
            required_contract_hash=actionable_manifest.manifest_hash,
        )
        if (
            not action_decision.allowed
            or not isinstance(rank, int)
            or rank <= 0
            or isinstance(score, bool)
            or not isinstance(score, int | float)
            or not math.isfinite(float(score))
        ):
            continue
        actionable_rows.append((rank, float(score), row))
    actionable_rows.sort(key=lambda item: (item[0], item[2].asset_code))
    actionable_rows = actionable_rows[:120]
    signal_rows = [row for _rank, _score, row in actionable_rows]
    actionable_score_by_code = {
        row.asset_code: score for _rank, score, row in actionable_rows
    }
    codes = [row.asset_code for row in signal_rows]
    if not codes:
        return []
    source_cutoff = _source_cutoff_utc(signal_run)
    if source_cutoff is None:
        return []
    etfs = {
        row.code: row
        for row in (
            await session.scalars(select(TradableEtf).where(TradableEtf.code.in_(codes)))
        ).all()
    }
    signal_date = signal_run.as_of_trade_date or signal_run.as_of_date
    start_date = signal_date - timedelta(days=OPTIMIZED_ALLOCATION_LOOKBACK_DAYS * 2)
    price_rows = (
        await session.scalars(
            select(EtfPriceHistory)
            .where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date >= start_date,
                EtfPriceHistory.trade_date <= signal_date,
                EtfPriceHistory.decision_eligible.is_(True),
                EtfPriceHistory.research_price_basis == "total_return_adjusted",
                EtfPriceHistory.research_adjusted_value.is_not(None),
                EtfPriceHistory.data_provider.is_not(None),
                EtfPriceHistory.provider_version.is_not(None),
                EtfPriceHistory.source_timestamp.is_not(None),
                EtfPriceHistory.source_timestamp <= source_cutoff,
                EtfPriceHistory.adjustment_version.is_not(None),
            )
            .order_by(EtfPriceHistory.etf_code.asc(), EtfPriceHistory.trade_date.asc())
        )
    ).all()
    closes: dict[str, list[EtfPriceHistory]] = defaultdict(list)
    for row in price_rows:
        closes[row.etf_code].append(row)

    candidates: list[OptimizerCandidate] = []
    for signal in signal_rows:
        actionable_score = actionable_score_by_code[signal.asset_code]
        risk_flags = set(signal.risk_flags_json or [])
        if risk_flags & set(PORTFOLIO_RISK_FLAGS_FORBIDDEN):
            continue
        metrics = dict(signal.metrics_json or {})
        data_reliability = str(
            metrics.get("market_data_reliability")
            or metrics.get("data_reliability")
            or "unavailable"
        )
        if data_reliability in {"failed", "stale", "unavailable"}:
            continue
        rows = [
            row
            for row in closes.get(signal.asset_code, [])
            if row.research_adjusted_value is not None
            and math.isfinite(float(row.research_adjusted_value))
            and row.research_adjusted_value > 0
        ]
        if len(rows) < OPTIMIZED_ALLOCATION_MIN_HISTORY_DAYS:
            continue
        adjusted_values = [float(row.research_adjusted_value) for row in rows if row.research_adjusted_value is not None]
        returns = [
            adjusted_values[index] / adjusted_values[index - 1] - 1.0
            for index in range(1, len(adjusted_values))
        ]
        if len(returns) < OPTIMIZED_ALLOCATION_MIN_HISTORY_DAYS - 1:
            continue
        dated_returns = tuple(
            (rows[index].trade_date, returns[index - 1])
            for index in range(1, len(rows))
        )[-OPTIMIZED_ALLOCATION_LOOKBACK_DAYS:]
        adjusted_input_hash = stable_contract_hash(
            {
                "schema_version": "optimizer_adjusted_history_v1",
                "asset_code": signal.asset_code,
                "rows": [
                    {
                        "trade_date": row.trade_date,
                        "research_adjusted_value": row.research_adjusted_value,
                        "research_price_basis": row.research_price_basis,
                        "data_provider": row.data_provider,
                        "provider_version": row.provider_version,
                        "source_timestamp": row.source_timestamp,
                        "adjustment_version": row.adjustment_version,
                        "decision_eligible": row.decision_eligible,
                    }
                    for row in rows
                ],
            }
        )
        rationale = dict(signal.rationale_json or {})
        entry_timing_label = str(
            metrics.get("entry_timing_label")
            or rationale.get("entry_timing_label")
            or rationale.get("entry_timing")
            or ""
        )
        theme_profile = metrics.get("theme_profile")
        frozen_theme_group = str(
            metrics.get("theme_group")
            or (
                theme_profile.get("theme_group")
                if isinstance(theme_profile, dict)
                else ""
            )
            or "unknown"
        )
        liquidity_score = _metric_float(
            metrics,
            "avg_turnover_20d",
            "turnover_20d",
            "turnover",
            "latest_turnover",
            "amount_20d",
        )
        evidence_confidence = _metric_float(metrics, "evidence_confidence", "validation_confidence_score")
        sample_count_value = metrics.get("validation_sample_count") or metrics.get("sample_count")
        try:
            validation_sample_count = int(sample_count_value) if sample_count_value is not None else None
        except (TypeError, ValueError):
            validation_sample_count = None
        etf = etfs.get(signal.asset_code)
        asset_class = str(etf.asset_class or "") if etf is not None else ""
        if asset_class in {"bond", "commodity", "cash", "money", "dividend"}:
            portfolio_layer = PORTFOLIO_LAYER_DEFENSIVE
        elif signal.conclusion == "高位观察":
            portfolio_layer = PORTFOLIO_LAYER_SATELLITE
        else:
            portfolio_layer = PORTFOLIO_LAYER_PRIMARY
        max_drawdown_value = metrics.get("max_drawdown_60d")
        try:
            max_drawdown_60d = float(max_drawdown_value)
        except (TypeError, ValueError):
            max_drawdown_60d = None
        if max_drawdown_60d is not None and not math.isfinite(max_drawdown_60d):
            max_drawdown_60d = None
        candidates.append(
            OptimizerCandidate(
                code=signal.asset_code,
                name=etf.name if etf is not None else signal.asset_code,
                score=actionable_score,
                theme_group=frozen_theme_group,
                data_date=rows[-1].trade_date,
                expected_return=mean(returns[-60:]) if returns[-60:] else None,
                volatility=pstdev(returns[-60:]) if len(returns[-60:]) > 1 else None,
                returns=tuple(returns[-OPTIMIZED_ALLOCATION_LOOKBACK_DAYS:]),
                dated_returns=dated_returns,
                clone_group_id=(
                    str(metrics.get("clone_group_id"))
                    if metrics.get("clone_group_id")
                    else (
                        f"underlying:{metrics.get('tracked_underlying_id')}"
                        if metrics.get("tracked_underlying_id")
                        else None
                    )
                ),
                asset_bucket=(
                    "bond"
                    if asset_class == "bond"
                    else "money"
                    if asset_class in {"cash", "money"}
                    else "commodity"
                    if asset_class == "commodity"
                    else "cross_border"
                    if asset_class == "cross_border"
                    else "equity"
                ),
                adjusted_input_hash=adjusted_input_hash,
                observation_label=signal.conclusion or "",
                entry_timing_label=entry_timing_label,
                evidence_confidence=evidence_confidence,
                prior_weight=liquidity_score,
                prior_source="liquidity_proxy" if liquidity_score else None,
                data_reliability=data_reliability,
                liquidity_score=liquidity_score,
                market_regime=str(metrics.get("market_regime") or ""),
                validation_sample_count=validation_sample_count,
                portfolio_layer=portfolio_layer,
                risk_flags=tuple(sorted(str(flag) for flag in risk_flags)),
                max_drawdown_60d=max_drawdown_60d,
                display_eligible=bool(metrics.get("default_display_eligible", False)),
            )
        )
    return candidates


async def run_etf_optimized_allocation(session: AsyncSession) -> EtfOptimizedAllocationSnapshot:
    canonical_selection = await _current_canonical_etf_selection(session)
    signal_run = canonical_selection.run
    if signal_run is None:
        snapshot = EtfOptimizedAllocationSnapshot(
            status="waiting",
            source_signal_run_id=None,
            observation_portfolio_snapshot_id=None,
            as_of_date=required_etf_snapshot_trade_date(),
            method_set=OPTIMIZED_ALLOCATION_METHOD_SET,
            data_window_json={},
            constraints_json={
                "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
                "theme_exposure_cap": PORTFOLIO_THEME_EXPOSURE_CAP,
            },
            summary_json={"method_count": 0, "research_only": True},
            unavailable_reason=(
                f"当前 canonical ETF 排名快照不可用（{canonical_selection.state}），"
                "等待发布后再生成优化组合。"
            ),
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    observation_snapshot = await _latest_observation_snapshot(
        session,
        source_signal_run_id=signal_run.id,
    )
    source_risk_policy = _source_portfolio_risk_policy(observation_snapshot)
    risk_budget_manifest = portfolio_risk_budget_manifest()
    candidates = await _eligible_candidates(session, signal_run)
    candidate_input_hash = _candidate_input_hash(candidates)
    constraints = {
        "single_weight_cap": PORTFOLIO_SINGLE_WEIGHT_CAP,
        "satellite_single_weight_cap": PORTFOLIO_SATELLITE_SINGLE_WEIGHT_CAP,
        "satellite_exposure_cap": PORTFOLIO_SATELLITE_EXPOSURE_CAP,
        "theme_exposure_cap": PORTFOLIO_THEME_EXPOSURE_CAP,
        "correlation_cluster_cap": PORTFOLIO_CORRELATION_CLUSTER_CAP,
        "min_assets": OPTIMIZED_ALLOCATION_MIN_ASSETS,
        "min_history_days": OPTIMIZED_ALLOCATION_MIN_HISTORY_DAYS,
        "risk_budget_version": PORTFOLIO_RISK_BUDGET_VERSION,
        "risk_budget_hash": risk_budget_manifest["contract_hash"],
        "source_market_state": source_risk_policy.market_risk.state,
        "source_target_exposure": source_risk_policy.max_total_exposure,
    }
    contract_hash = stable_contract_hash(
        {
            "method_set": OPTIMIZED_ALLOCATION_METHOD_SET,
            "source_signal_run_id": signal_run.id,
            "as_of_trade_date": (
                signal_run.as_of_trade_date or signal_run.as_of_date
            ).isoformat(),
            "source_data_cutoff": signal_run.data_cutoff,
            "source_input_snapshot_hash": signal_run.input_snapshot_hash,
            "source_ranking_contract_id": ACTIONABLE_CONTRACT_ID,
            "source_ranking_contract_hash": actionable_rank_manifest().manifest_hash,
            "price_basis": signal_run.price_basis,
            "candidate_input_hash": candidate_input_hash,
            "source_allocation_contract_hash": (
                source_risk_policy.source_allocation_contract_hash
            ),
            "constraints": constraints,
        }
    )
    methods = ("equal_weight", "minimum_volatility", "risk_parity")
    method_weights: dict[str, dict[str, float]] = {}
    method_budgets: dict[str, PortfolioRiskBudgetResult] = {}
    if source_risk_policy.status == "ready":
        for method in methods:
            raw_weights = _optimized_raw_weights(method, candidates)
            if raw_weights is None:
                continue
            budget = _candidate_risk_budget(raw_weights, candidates, source_risk_policy)
            method_budgets[method] = budget
            if budget.status == "ready" and budget.weights:
                method_weights[method] = budget.weights
    bl_candidates = [
        BlackLittermanCandidate(
            code=candidate.code,
            name=candidate.name,
            theme_group=candidate.theme_group,
            score=candidate.score,
            observation_label=candidate.observation_label,
            entry_timing_label=candidate.entry_timing_label,
            returns=candidate.returns,
            expected_return=candidate.expected_return,
            volatility=candidate.volatility,
            prior_weight=candidate.prior_weight,
            prior_source=candidate.prior_source,
            evidence_confidence=candidate.evidence_confidence,
            data_reliability=candidate.data_reliability,
            liquidity_score=candidate.liquidity_score,
            market_regime=candidate.market_regime,
            validation_sample_count=candidate.validation_sample_count,
        )
        for candidate in candidates
    ]
    black_litterman_result = build_black_litterman_allocation(bl_candidates)
    if black_litterman_result.status == "success" and source_risk_policy.status == "ready":
        black_litterman_raw = {
            item.code: item.target_weight for item in black_litterman_result.items
        }
        black_litterman_budget = _candidate_risk_budget(
            black_litterman_raw,
            candidates,
            source_risk_policy,
        )
        method_budgets[BLACK_LITTERMAN_METHOD] = black_litterman_budget
        if black_litterman_budget.status == "ready" and black_litterman_budget.weights:
            method_weights[BLACK_LITTERMAN_METHOD] = black_litterman_budget.weights

    unavailable_reason = source_risk_policy.unavailable_reason
    if unavailable_reason is None and not method_weights:
        unavailable_reason = (
            f"满足数据可靠性、历史长度和约束的 ETF 只有 {len(candidates)} 只，暂不能生成优化权重。"
        )
    latest_dates = [candidate.data_date for candidate in candidates if candidate.data_date is not None]
    data_window = {
        "as_of_date": signal_run.as_of_date.isoformat(),
        "source_signal_run_id": signal_run.id,
        "source_data_cutoff": (
            signal_run.data_cutoff.isoformat() if signal_run.data_cutoff else None
        ),
        "source_input_snapshot_hash": signal_run.input_snapshot_hash,
        "source_ranking_contract_id": ACTIONABLE_CONTRACT_ID,
        "source_ranking_contract_hash": actionable_rank_manifest().manifest_hash,
        "candidate_input_hash": candidate_input_hash,
        "candidate_count": len(candidates),
        "latest_data_date": max(latest_dates).isoformat() if latest_dates else None,
        "lookback_days": OPTIMIZED_ALLOCATION_LOOKBACK_DAYS,
        "covariance": black_litterman_covariance_summary(bl_candidates),
        "source_observation_portfolio_snapshot_id": (
            observation_snapshot.id if observation_snapshot else None
        ),
        "source_allocation_contract_hash": (
            source_risk_policy.source_allocation_contract_hash
        ),
        "risk_budget_version": PORTFOLIO_RISK_BUDGET_VERSION,
        "risk_budget_hash": risk_budget_manifest["contract_hash"],
    }
    summary_methods: dict[str, Any] = {}
    candidate_by_code = {candidate.code: candidate for candidate in candidates}
    dated_return_maps = {
        candidate.code: dict(candidate.dated_returns)
        for candidate in candidates
    }
    previous_weights = await _observation_snapshot_weights(session, observation_snapshot)
    for method, weights in method_weights.items():
        budget = method_budgets[method]
        risk_shadow = build_portfolio_risk_shadow(
            [
                PortfolioRiskAssetInput(
                    code=code,
                    weight=float(weight),
                    clone_group_id=candidate_by_code[code].clone_group_id,
                    theme_group=candidate_by_code[code].theme_group,
                    asset_bucket=candidate_by_code[code].asset_bucket,
                )
                for code, weight in sorted(weights.items())
            ],
            dated_return_maps,
            {},
            previous_weights=previous_weights,
        )
        summary_methods[method] = {
            "status": "success",
            "weight_sum": round(sum(weights.values()), 6),
            "cash_weight": budget.cash_weight,
            "asset_count": len(weights),
            "max_single_weight": max(weights.values()) if weights else 0.0,
            "market_state": budget.market_state,
            "risk_metrics": budget.metrics,
            "binding_constraints": list(budget.binding_constraints),
            "risk_budget_version": budget.contract_version,
            "risk_budget_hash": budget.contract_hash,
            "risk_shadow": risk_shadow.as_dict(),
        }
    for method, budget in method_budgets.items():
        if method in summary_methods:
            continue
        summary_methods[method] = {
            "status": "unavailable",
            "weight_sum": 0.0,
            "cash_weight": 1.0,
            "asset_count": 0,
            "unavailable_reason": "、".join(budget.unavailable_reasons),
            "risk_metrics": budget.metrics,
            "risk_budget_version": budget.contract_version,
            "risk_budget_hash": budget.contract_hash,
        }
    summary_methods[BLACK_LITTERMAN_METHOD] = {
        **black_litterman_result.summary,
        **summary_methods.get(BLACK_LITTERMAN_METHOD, {}),
        "status": summary_methods.get(BLACK_LITTERMAN_METHOD, {}).get(
            "status",
            black_litterman_result.status,
        ),
        "weight_sum": round(sum(method_weights.get(BLACK_LITTERMAN_METHOD, {}).values()), 6),
        "asset_count": len(method_weights.get(BLACK_LITTERMAN_METHOD, {})),
        "unavailable_reason": summary_methods.get(BLACK_LITTERMAN_METHOD, {}).get(
            "unavailable_reason",
            black_litterman_result.unavailable_reason,
        ),
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
            "black_litterman": black_litterman_result.summary,
            "black_litterman_excluded_items": list(black_litterman_result.excluded_items),
            "source_risk_policy": {
                "status": source_risk_policy.status,
                "market_state": source_risk_policy.market_risk.state,
                "target_exposure": source_risk_policy.max_total_exposure,
                "allocation_contract_hash": (
                    source_risk_policy.source_allocation_contract_hash
                ),
                "risk_budget_version": PORTFOLIO_RISK_BUDGET_VERSION,
                "risk_budget_hash": risk_budget_manifest["contract_hash"],
                "unavailable_reason": source_risk_policy.unavailable_reason,
            },
            "research_only": True,
            "no_trade_instruction": True,
        },
        unavailable_reason=unavailable_reason,
    )
    session.add(snapshot)
    await session.flush()
    by_code = {candidate.code: candidate for candidate in candidates}
    black_litterman_by_code = {item.code: item for item in black_litterman_result.items}
    for method, weights in method_weights.items():
        for code, weight in weights.items():
            candidate = by_code[code]
            bl_item = black_litterman_by_code.get(code) if method == BLACK_LITTERMAN_METHOD else None
            session.add(
                EtfOptimizedAllocationItem(
                    snapshot_id=snapshot.id,
                    method=method,
                    asset_code=code,
                    asset_name=candidate.name,
                    target_weight=weight,
                    expected_return=bl_item.posterior_return if bl_item else candidate.expected_return,
                    volatility=bl_item.volatility if bl_item else candidate.volatility,
                    theme_group=candidate.theme_group,
                    data_date=candidate.data_date,
                    explanation=(
                        bl_item.explanation
                        if bl_item
                        else f"{method} 按波动和约束生成，仅作规则组合对照。"
                    ),
                    metrics_json={
                        "score": candidate.score,
                        "expected_return": candidate.expected_return,
                        "volatility": candidate.volatility,
                        "adjusted_input_hash": candidate.adjusted_input_hash,
                        "observation_label": candidate.observation_label,
                        "entry_timing_label": candidate.entry_timing_label,
                        "portfolio_layer": candidate.portfolio_layer,
                        "risk_budget_version": PORTFOLIO_RISK_BUDGET_VERSION,
                        "risk_budget_hash": risk_budget_manifest["contract_hash"],
                        **(
                            {
                                "prior_weight": bl_item.prior_weight,
                                "prior_source": bl_item.prior_source,
                                "prior_return": bl_item.prior_return,
                                "view_return": bl_item.view_return,
                                "posterior_return": bl_item.posterior_return,
                                "confidence": bl_item.confidence,
                                "black_litterman": bl_item.metrics,
                            }
                            if bl_item
                            else {}
                        ),
                    },
                )
            )
    await session.commit()
    await session.refresh(snapshot)
    return snapshot
