from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import optional_approved_user, require_approved_user
from app.core.db import get_db_session
from app.models.entities import (
    EtfSignalValidationItem,
    EtfSignalValidationRun,
    EtfSignalValidationSourceEvent,
    ShortResearchSignalRun,
    User,
)
from app.schemas.etf_evidence import EtfEvidenceOverviewOut
from app.schemas.short_research import (
    EtfExitCredibilityRequest,
    EtfExitCredibilityRunOut,
    EtfExitHyperoptRequest,
    EtfExitHyperoptRunOut,
    EtfOptimizedAllocationOut,
    EtfPortfolioBacktestDetailOut,
    EtfPortfolioBacktestListOut,
    EtfPortfolioBacktestRequest,
    EtfRankingSnapshotMetadataOut,
    EtfSignalValidationItemOut,
    EtfSignalValidationRunOut,
    EtfSignalValidationSourceEventOut,
    EtfStrategyComparisonOut,
    EtfStrategyComparisonRequest,
    EtfStrategyHealthcheckOut,
    ShortResearchAdvisorReportOut,
    ShortResearchAdvisorRunRequest,
    ShortResearchAssetDetailOut,
    ShortResearchAssetListOut,
    ShortResearchAssetOut,
    ShortResearchChartPointOut,
    ShortResearchDataSyncRequest,
    ShortResearchObservationPortfolioOut,
    ShortResearchSignalRunOut,
    ShortResearchSignalRunRequest,
    ShortResearchStatusOut,
)
from app.services.etf_catalyst_shadow.evidence import catalyst_shadow_contexts
from app.services.etf_exit_calibration import (
    etf_exit_hyperopt_payload,
    latest_etf_exit_hyperopt_run,
    run_etf_exit_hyperopt,
)
from app.services.etf_research_evidence import (
    EVIDENCE_STATUS_WAITING,
    build_evidence_summary,
    build_research_signal_contract,
)
from app.services.short_research.advisor import latest_reports_by_asset, run_advisor_generation
from app.services.short_research.backtest import (
    backtest_detail_payload,
    backtest_summary_payload,
    get_backtest_run,
    latest_strategy_comparison_run,
    list_backtest_runs,
    run_etf_intraday_alert_backtest,
    run_etf_portfolio_backtest,
    run_etf_strategy_comparison_backtest,
    strategy_comparison_payload,
)
from app.services.short_research.etf_exit_credibility import (
    etf_exit_credibility_payload,
    latest_etf_exit_credibility_run,
    run_etf_exit_credibility,
)
from app.services.short_research.healthcheck import (
    healthcheck_payload,
    latest_healthcheck_snapshot,
    run_etf_strategy_healthcheck,
)
from app.services.short_research.optimized_allocation import (
    latest_optimized_allocation_snapshot,
    optimized_allocation_payload,
    run_etf_optimized_allocation,
)
from app.services.short_research.ranking_surfaces import DUAL_RANKING_RULE_VERSION
from app.services.short_research.service import (
    VALIDATION_MODE_FORWARD_LIVE,
    VALIDATION_MODE_HISTORICAL_REPLAY,
    VALIDATION_MODE_SCORE_BUCKET_REPLAY,
    ComputedAsset,
    cached_signal_assets,
    current_etf_ranking_surface_selection,
    etf_observation_portfolio,
    get_asset_detail,
    has_available_opportunity_score,
    has_unavailable_theme_catalyst,
    latest_signal_run,
    latest_signal_validation_run,
    latest_validation_evidence_by_label,
    run_etf_label_historical_replay,
    run_etf_signal_validation,
    run_signal_generation,
    status_summary,
    sync_short_research_data,
)
from app.services.short_research.snapshot_selector import snapshot_metadata
from app.services.strategy_lab.etf_evidence_overview import (
    build_etf_evidence_overview,
)
from app.services.strategy_lab.etf_ranking_validation import (
    RankingValidationContractError,
)
from app.services.strategy_lab.etf_score_bucket_validation import (
    run_registered_etf_score_bucket_validation as run_etf_score_bucket_validation,
)
from app.services.strategy_lab.etf_validation_manifest import (
    validate_attached_validation_source_manifest,
)
from app.services.workflows.tracking_filters import (
    tracking_states_by_code,
    validate_tracking_states,
)

router = APIRouter(prefix="/api/short-research", tags=["short-research"])


@router.get("/evidence/etf/latest", response_model=EtfEvidenceOverviewOut)
async def get_latest_etf_evidence_overview(
    session: AsyncSession = Depends(get_db_session),
) -> EtfEvidenceOverviewOut:
    """Return provenance-separated ETF evidence without mutating research state."""

    payload = await build_etf_evidence_overview(session)
    return EtfEvidenceOverviewOut.model_validate(payload)


def _csv_values(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _asset_catalyst_theme_ids(asset: ComputedAsset) -> tuple[str, ...]:
    metrics = dict(asset.metrics or {})
    profile = metrics.get("theme_profile")
    profile = profile if isinstance(profile, dict) else {}
    values = [
        profile.get("primary_theme"),
        *(profile.get("secondary_themes") or []),
        metrics.get("primary_theme"),
        metrics.get("theme_group"),
        *asset.metadata.theme_tags,
    ]
    return tuple(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if value is not None and str(value).strip()
        )
    )


async def _catalyst_shadow_by_asset_code(
    session: AsyncSession,
    assets: list[ComputedAsset],
    *,
    as_of_date: date,
) -> dict[str, dict[str, Any]]:
    themes_by_code = {
        asset.metadata.code: _asset_catalyst_theme_ids(asset)
        for asset in assets
        if asset.metadata.asset_type == "etf"
    }
    all_theme_ids = tuple(
        dict.fromkeys(
            theme_id
            for theme_ids in themes_by_code.values()
            for theme_id in theme_ids
        )
    )
    contexts = await catalyst_shadow_contexts(
        session,
        theme_ids=all_theme_ids,
        as_of_date=as_of_date,
    )
    result: dict[str, dict[str, Any]] = {}
    for code, theme_ids in themes_by_code.items():
        themes = [contexts[theme_id] for theme_id in theme_ids if theme_id in contexts]
        states = [
            str(theme["snapshot"]["coverage_state"])
            for theme in themes
            if isinstance(theme.get("snapshot"), dict)
        ]
        if "active" in states:
            coverage_state = "active"
        elif "unavailable" in states or not states:
            coverage_state = "unavailable"
        elif "observed_none" in states:
            coverage_state = "observed_none"
        else:
            coverage_state = "not_applicable"
        result[code] = {
            "coverage_state": coverage_state,
            "themes": themes,
            "limitations": (
                ["没有已完成的缓存催化影子快照。"]
                if not themes
                else sorted(
                    {
                        limitation
                        for theme in themes
                        for limitation in theme.get("limitations", [])
                    }
                )
            ),
            "shadow_only": True,
            "ranking_weight": 0,
            "changes_research_rank": False,
            "changes_actionable_rank": False,
            "changes_allocation": False,
            "changes_tracked_position_action": False,
            "changes_email_trigger": False,
        }
    return result


def _advisor_report_out(report: Any | None) -> ShortResearchAdvisorReportOut | None:
    if report is None:
        return None
    return ShortResearchAdvisorReportOut(
        id=report.id,
        status=report.status,
        action_label=report.action_label,
        plain_summary=report.plain_summary,
        opportunity=list(report.opportunity_json),
        risks=list(report.risks_json),
        opposing_view=report.opposing_view,
        watch_conditions=list(report.watch_conditions_json),
        holding_note=report.holding_note,
        data_limitations=report.data_limitations,
        model_name=report.model_name,
        prompt_version=report.prompt_version,
        source=report.source,
        generated_at=report.generated_at,
    )


def _asset_out(
    asset: ComputedAsset,
    advisor_report: Any | None = None,
    *,
    signal_run: ShortResearchSignalRun | None = None,
    validation_evidence: dict[str, Any] | None = None,
    observation_portfolio: dict[str, Any] | None = None,
    catalyst_shadow: dict[str, Any] | None = None,
    provisional_research: bool = False,
) -> ShortResearchAssetOut:
    metrics = dict(asset.metrics or {})
    if provisional_research and asset.metadata.asset_type == "etf":
        metrics.update(
            {
                "actionable_rank": None,
                "actionable_score": None,
                "actionable_eligible": False,
                "actionable_exclusion_reasons": ["provisional_research_only"],
                "actionable_field_statuses": {},
                "actionable_source_times": {},
            }
        )
    catalyst_unavailable = asset.metadata.asset_type == "etf" and has_unavailable_theme_catalyst(metrics)
    opportunity_score = metrics.get("opportunity_score") if has_available_opportunity_score(metrics) else None
    opportunity_label = str(metrics.get("opportunity_label")) if metrics.get("opportunity_label") else None
    if opportunity_score is None and catalyst_unavailable and opportunity_label not in {"等待数据"}:
        opportunity_label = "暂无主题辅助"
    sector_trend_score = metrics.get("sector_trend_score")
    theme_profile = dict(
        metrics.get("theme_profile")
        or asset.rationale.get("theme_profile")
        or {}
    )
    signal_contract: dict[str, Any] = {}
    evidence_summary: dict[str, Any] = {}
    evidence_status = EVIDENCE_STATUS_WAITING
    if asset.metadata.asset_type == "etf":
        signal_rule_version = None
        if signal_run is not None:
            signal_rule_version = (signal_run.config_json or {}).get("rule_version") or (
                signal_run.summary_json or {}
            ).get("rule_version")
        source_data_time = (
            metrics.get("quote_time")
            or metrics.get("data_as_of_time")
            or metrics.get("latest_quote_time")
            or asset.latest_date
        )
        signal_contract = build_research_signal_contract(
            asset_type=asset.metadata.asset_type,
            asset_code=asset.metadata.code,
            signal_run_id=signal_run.id if signal_run else None,
            signal_date=signal_run.as_of_date if signal_run else asset.latest_date,
            score=asset.total_score,
            observation_label=asset.conclusion,
            entry_timing_label=asset.entry_timing_label,
            data_reliability=str(metrics.get("data_reliability") or metrics.get("score_source") or "verified"),
            source_data_time=source_data_time,
            rule_version=str(signal_rule_version) if signal_rule_version else "short_research_signal_v1",
            ranking_contract_hash=signal_run.ranking_contract_hash if signal_run else None,
            score_version=signal_run.score_version if signal_run else None,
            score_field=signal_run.score_field if signal_run else None,
            universe_snapshot_hash=signal_run.universe_snapshot_hash if signal_run else None,
            price_basis=signal_run.price_basis if signal_run else None,
        )
        evidence_summary = build_evidence_summary(
            current_contract=signal_contract,
            validation_evidence=validation_evidence,
            caveats=["研究证据只用于复盘标签有效性，不代表未来收益。"],
        )
        evidence_status = str(evidence_summary.get("evidence_status") or EVIDENCE_STATUS_WAITING)
    return ShortResearchAssetOut(
        asset_type=asset.metadata.asset_type,
        code=asset.metadata.code,
        name=asset.metadata.name,
        rank=asset.rank,
        ranking_score=round(asset.ranking_score, 4) if asset.ranking_score is not None else None,
        score_eligible=asset.score_eligible,
        global_rank=asset.global_rank if asset.global_rank is not None else asset.rank,
        filtered_position=asset.filtered_position,
        ranking_surface=(
            str(metrics["ranking_surface"])
            if metrics.get("ranking_surface") in {"research", "actionable"}
            else None
        ),
        research_rank=int(metrics["research_rank"])
        if isinstance(metrics.get("research_rank"), int)
        else None,
        research_score=float(metrics["research_score"])
        if isinstance(metrics.get("research_score"), int | float)
        else None,
        research_eligible=(
            metrics.get("research_score_eligible")
            if isinstance(metrics.get("research_score_eligible"), bool)
            else None
        ),
        research_contract_hash=(
            str(metrics["research_contract_hash"])
            if metrics.get("research_contract_hash")
            else None
        ),
        actionable_rank=int(metrics["actionable_rank"])
        if isinstance(metrics.get("actionable_rank"), int)
        else None,
        actionable_score=float(metrics["actionable_score"])
        if isinstance(metrics.get("actionable_score"), int | float)
        else None,
        actionable_eligible=(
            metrics.get("actionable_eligible")
            if isinstance(metrics.get("actionable_eligible"), bool)
            else None
        ),
        actionable_contract_hash=(
            str(metrics["actionable_contract_hash"])
            if metrics.get("actionable_contract_hash")
            else None
        ),
        actionable_exclusion_reasons=list(
            metrics.get("actionable_exclusion_reasons") or []
        ),
        actionable_field_statuses=dict(
            metrics.get("actionable_field_statuses") or {}
        ),
        actionable_source_times=dict(metrics.get("actionable_source_times") or {}),
        history_confidence_tier=(
            str(metrics["history_confidence_tier"])
            if metrics.get("history_confidence_tier")
            else None
        ),
        total_score=round(asset.total_score, 2),
        technical_score=round(float(metrics["technical_score"]), 2) if isinstance(metrics.get("technical_score"), (int, float)) else None,
        opportunity_score=round(float(opportunity_score), 2)
        if isinstance(opportunity_score, int | float)
        else None,
        opportunity_label=opportunity_label,
        opportunity_score_version=str(metrics.get("opportunity_score_version"))
        if metrics.get("opportunity_score_version")
        else None,
        sector_trend_score=round(float(sector_trend_score), 2)
        if isinstance(sector_trend_score, int | float)
        else None,
        sector_trend_label=str(metrics.get("sector_trend_label")) if metrics.get("sector_trend_label") else None,
        sector_trend_summary=str(metrics.get("sector_trend_summary")) if metrics.get("sector_trend_summary") else None,
        sector_trend_reason=str(metrics.get("sector_trend_reason")) if metrics.get("sector_trend_reason") else None,
        sector_trend_status=str(metrics.get("sector_trend_status")) if metrics.get("sector_trend_status") else None,
        sector_peer_count=int(metrics["sector_peer_count"]) if isinstance(metrics.get("sector_peer_count"), int | float) else None,
        catalyst_score=(
            None
            if asset.metadata.asset_type == "etf"
            else round(float(metrics["catalyst_score"]), 2)
            if isinstance(metrics.get("catalyst_score"), (int, float))
            else None
        ),
        sentiment_heat_score=(
            None
            if asset.metadata.asset_type == "etf"
            else round(float(metrics["sentiment_heat_score"]), 2)
            if isinstance(metrics.get("sentiment_heat_score"), (int, float))
            else None
        ),
        catalyst_summary=(
            None
            if asset.metadata.asset_type == "etf"
            else str(metrics.get("catalyst_summary"))
            if metrics.get("catalyst_summary")
            else None
        ),
        catalyst_events=(
            []
            if asset.metadata.asset_type == "etf"
            else list(metrics.get("catalyst_events") or [])
        ),
        catalyst_limitations=(
            ["旧主题催化评分已停用；请查看独立的催化影子证据。"]
            if asset.metadata.asset_type == "etf"
            else list(metrics.get("catalyst_limitations") or [])
        ),
        catalyst_shadow=dict(catalyst_shadow or {}),
        factor_profile_version=str(metrics.get("factor_profile_version"))
        if metrics.get("factor_profile_version")
        else None,
        factor_profile_status=str(metrics.get("factor_profile_status"))
        if metrics.get("factor_profile_status")
        else None,
        factor_profile_score=round(float(metrics["factor_profile_score"]), 2)
        if isinstance(metrics.get("factor_profile_score"), int | float)
        else None,
        factor_group_scores=dict(metrics.get("factor_group_scores") or {}),
        factor_scores=list(metrics.get("factor_scores") or []),
        factor_availability=dict(metrics.get("factor_availability") or {}),
        risk_gates=list(metrics.get("risk_gates") or []),
        opportunity_breakdown=dict(metrics.get("opportunity_breakdown") or {}),
        conclusion=asset.conclusion,
        entry_timing_label=asset.entry_timing_label,
        entry_timing_reason=asset.entry_timing_reason,
        theme_tags=list(asset.metadata.theme_tags),
        theme_group=theme_profile.get("theme_group"),
        primary_theme=theme_profile.get("primary_theme"),
        secondary_themes=list(theme_profile.get("secondary_themes") or []),
        classification_source=theme_profile.get("classification_source"),
        classification_confidence=theme_profile.get("classification_confidence"),
        classification_reason=theme_profile.get("classification_reason"),
        theme_profile=theme_profile,
        investment_direction=asset.metadata.investment_direction,
        trading_rule_label=asset.metadata.trading_rule_label,
        latest_date=asset.latest_date,
        latest_value=asset.latest_value,
        usable_days=asset.usable_days,
        sample_level=asset.sample_level,
        metrics=metrics,
        score_breakdown=asset.score_breakdown,
        risk_flags=asset.risk_flags,
        rationale=asset.rationale,
        source_note=asset.source_note,
        advisor_report=_advisor_report_out(advisor_report),
        validation_evidence=validation_evidence or {},
        observation_portfolio=observation_portfolio or {},
        research_signal_contract=signal_contract,
        evidence_status=evidence_status,
        evidence_summary=evidence_summary,
    )


def _theme_heat_summary(assets: list[ComputedAsset]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if asset.metadata.asset_type != "etf":
            continue
        profile = dict(asset.metrics.get("theme_profile") or asset.rationale.get("theme_profile") or {})
        primary_theme = str(profile.get("primary_theme") or "").strip() or "未分类"
        if primary_theme == "未分类":
            primary_theme = str(profile.get("theme_group") or "未分类")
        bucket = groups.setdefault(
            primary_theme,
            {
                "theme": primary_theme,
                "count": 0,
                "score_sum": 0.0,
                "change_sum": 0.0,
                "change_count": 0,
                "top_asset": None,
                "top_score": 0.0,
            },
        )
        bucket["count"] += 1
        bucket["score_sum"] += float(asset.total_score)
        today_return = asset.metrics.get("today_return_pct")
        if isinstance(today_return, (int, float)):
            bucket["change_sum"] += float(today_return)
            bucket["change_count"] += 1
        if float(asset.total_score) >= float(bucket["top_score"]):
            bucket["top_score"] = round(float(asset.total_score), 2)
            bucket["top_asset"] = {"code": asset.metadata.code, "name": asset.metadata.name}
    result: list[dict[str, Any]] = []
    for item in groups.values():
        count = max(int(item["count"]), 1)
        change_count = int(item["change_count"])
        result.append(
            {
                "theme": item["theme"],
                "count": count,
                "avg_score": round(float(item["score_sum"]) / count, 2),
                "avg_today_return": round(float(item["change_sum"]) / change_count, 4) if change_count else None,
                "top_score": item["top_score"],
                "top_asset": item["top_asset"],
            }
        )
    return sorted(result, key=lambda item: (item["avg_score"], item["count"]), reverse=True)[:12]


def _validation_for_asset(asset: ComputedAsset, evidence_by_label: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    return evidence_by_label.get((asset.conclusion, asset.entry_timing_label), {})


def _portfolio_contexts(portfolio: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for section, status in (
        ("items", "included"),
        ("satellite_items", "satellite"),
        ("defensive_items", "defensive"),
        ("watch_only_items", "watch_only"),
        ("excluded_items", "excluded"),
    ):
        for item in portfolio.get(section, []):
            code = str(item.get("code") or "")
            if not code:
                continue
            decision_factors = item.get("decision_factors")
            if not isinstance(decision_factors, dict):
                decision_factors = {}
            result[code] = {
                "status": status,
                "target_weight": item.get("target_weight", 0.0),
                "evidence": list(item.get("evidence") or []),
                "risk_reasons": list(item.get("risk_reasons") or []),
                "exclusion_reason": item.get("exclusion_reason"),
                "weight_explanation": item.get("weight_explanation"),
                "exclusion_explanation": item.get("exclusion_explanation"),
                "decision_factors": decision_factors,
                "snapshot_id": portfolio.get("snapshot_id"),
                "generated_at": portfolio.get("generated_at"),
            }
    return result


async def _validation_run_out(session: AsyncSession, run: EtfSignalValidationRun) -> EtfSignalValidationRunOut:
    source_snapshot = (
        await session.get(ShortResearchSignalRun, run.source_signal_run_id)
        if run.source_signal_run_id is not None
        else None
    )
    rows = (
        await session.scalars(
            select(EtfSignalValidationItem)
            .where(EtfSignalValidationItem.run_id == run.id)
            .order_by(
                EtfSignalValidationItem.label.asc(),
                EtfSignalValidationItem.entry_timing_label.asc(),
                EtfSignalValidationItem.horizon_days.asc(),
            )
        )
    ).all()
    ranking_source_kind: Literal["production_published", "research_replay"] | None
    if run.ranking_source_kind == "production_published":
        ranking_source_kind = "production_published"
    elif run.ranking_source_kind == "research_replay":
        ranking_source_kind = "research_replay"
    else:
        ranking_source_kind = None
    source_events: list[EtfSignalValidationSourceEvent] = []
    if run.status == "success" and ranking_source_kind is not None:
        try:
            await validate_attached_validation_source_manifest(session, run)
        except RankingValidationContractError:
            ranking_source_kind = None
        else:
            source_events = list(
                (
                    await session.scalars(
                        select(EtfSignalValidationSourceEvent)
                        .where(
                            EtfSignalValidationSourceEvent.validation_run_id == run.id
                        )
                        .order_by(EtfSignalValidationSourceEvent.event_order)
                    )
                ).all()
            )
    return EtfSignalValidationRunOut(
        id=run.id,
        status=run.status,
        as_of_date=run.as_of_date,
        source_signal_run_id=run.source_signal_run_id,
        validation_mode=run.validation_mode or VALIDATION_MODE_FORWARD_LIVE,
        ranking_source_kind=ranking_source_kind,
        source_replay_run_key=(
            run.source_replay_run_key
            if ranking_source_kind == "research_replay"
            else None
        ),
        source_manifest_hash=(
            run.source_manifest_hash if ranking_source_kind is not None else None
        ),
        source_event_count=(
            run.source_event_count if ranking_source_kind is not None else None
        ),
        source_events=[
            EtfSignalValidationSourceEventOut(
                event_order=event.event_order,
                source_date=event.source_date,
                ranking_source_kind=ranking_source_kind,
                source_signal_run_id=event.source_signal_run_id,
                source_replay_run_key=event.source_replay_run_key,
                source_replay_contract_hash=event.source_replay_contract_hash,
                source_event_hash=event.source_event_hash,
                ranking_contract_hash=event.ranking_contract_hash,
                scope_hash=event.scope_hash,
                universe_snapshot_hash=event.universe_snapshot_hash,
                input_snapshot_hash=event.input_snapshot_hash,
                availability_cutoff=event.availability_cutoff,
                immutable_hash=event.immutable_hash,
            )
            for event in source_events
        ],
        rule_version=run.rule_version,
        source_ranking_contract_hash=run.source_ranking_contract_hash,
        source_scope_kind=run.source_scope_kind,
        source_scope_hash=run.source_scope_hash,
        source_universe_snapshot_hash=run.source_universe_snapshot_hash,
        source_input_snapshot_hash=run.source_input_snapshot_hash,
        source_score_field=run.source_score_field,
        source_score_version=run.source_score_version,
        source_rule_version=run.source_rule_version,
        price_basis=run.price_basis,
        execution_model=run.execution_model,
        data_cutoff=run.data_cutoff,
        source_ranking_snapshot=EtfRankingSnapshotMetadataOut.model_validate(snapshot_metadata(source_snapshot)),
        summary=dict(run.summary_json or {}),
        created_at=run.created_at,
        items=[
            EtfSignalValidationItemOut(
                label=row.label,
                entry_timing_label=row.entry_timing_label,
                horizon_days=row.horizon_days,
                sample_count=row.sample_count,
                excluded_count=row.excluded_count,
                avg_return=row.avg_return,
                median_return=row.median_return,
                win_rate=row.win_rate,
                worst_forward_drawdown=row.worst_forward_drawdown,
                confidence=row.confidence,
                metrics=dict(row.metrics_json or {}),
            )
            for row in rows
        ],
    )


async def _signal_run_out(
    session: AsyncSession,
    run: ShortResearchSignalRun,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> ShortResearchSignalRunOut:
    assets, _total = await cached_signal_assets(
        session,
        run,
        asset_type=asset_type,
        theme=theme,
        codes=codes,
        sort="score",
        universe="all",
    )
    advisor_reports = await latest_reports_by_asset(session, run.id)
    summary = dict(run.summary_json or {})
    if asset_type is not None or theme is not None or codes is not None:
        summary["item_count"] = len(assets)
        summary["fund_count"] = sum(1 for item in assets if item.metadata.asset_type == "fund")
        summary["etf_count"] = sum(1 for item in assets if item.metadata.asset_type == "etf")
    else:
        summary.setdefault("item_count", len(assets))
        summary.setdefault("fund_count", sum(1 for item in assets if item.metadata.asset_type == "fund"))
        summary.setdefault("etf_count", sum(1 for item in assets if item.metadata.asset_type == "etf"))
    return ShortResearchSignalRunOut(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        as_of_date=run.as_of_date,
        config=run.config_json,
        summary=summary,
        error_message=run.error_message,
        scope_kind=run.scope_kind,
        scope_hash=run.scope_hash,
        universe_snapshot_hash=run.universe_snapshot_hash,
        input_snapshot_hash=run.input_snapshot_hash,
        score_version=run.score_version,
        rule_version=run.rule_version,
        ranking_contract_hash=run.ranking_contract_hash,
        score_field=run.score_field,
        data_cutoff=run.data_cutoff,
        as_of_trade_date=run.as_of_trade_date,
        price_basis=run.price_basis,
        expected_item_count=run.expected_item_count,
        decision_data_item_count=run.decision_data_item_count,
        decision_data_coverage_ratio=run.decision_data_coverage_ratio,
        eligible_item_count=run.eligible_item_count,
        coverage_ratio=run.coverage_ratio,
        publication_state=run.publication_state,
        published_at=run.published_at,
        idempotency_key=run.idempotency_key,
        items=[
            _asset_out(
                item,
                advisor_reports.get((item.metadata.asset_type, item.metadata.code)),
                signal_run=run,
            )
            for item in assets
        ],
    )


@router.get("/status", response_model=ShortResearchStatusOut)
async def get_short_research_status(
    include_health: bool = Query(default=False),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await status_summary(session, include_health=include_health)


def _ranking_surface_snapshot_metadata(
    run: ShortResearchSignalRun | None,
    *,
    ranking_surface: Literal["research", "actionable"],
    selection_state: str | None = None,
) -> dict[str, Any]:
    metadata = {
        **snapshot_metadata(run, selection_state=selection_state),
        "ranking_surface": ranking_surface,
    }
    if run is None or run.rule_version != DUAL_RANKING_RULE_VERSION:
        return metadata
    surfaces = (run.summary_json or {}).get("ranking_surfaces")
    surface = surfaces.get(ranking_surface) if isinstance(surfaces, dict) else None
    if not isinstance(surface, dict):
        return metadata
    eligible_count = int(surface.get("eligible_count") or 0)
    coverage_ratio = float(surface.get("coverage_ratio") or 0.0)
    return {
        **metadata,
        "score_version": surface.get("contract_id"),
        "score_field": surface.get("score_field"),
        "ranking_contract_hash": surface.get("contract_hash"),
        "score_eligible_item_count": eligible_count,
        "score_coverage_ratio": coverage_ratio,
        "coverage_ratio": coverage_ratio,
    }


@router.get("/assets", response_model=ShortResearchAssetListOut)
async def list_short_research_assets(
    asset_type: str | None = Query(default=None),
    theme: str | None = Query(default=None),
    sort: str = Query(default="score"),
    q: str | None = Query(default=None),
    universe: str = Query(default="default"),
    observation_labels: str | None = Query(default=None),
    entry_labels: str | None = Query(default=None),
    tracking_states: str | None = Query(default=None),
    ranking_surface: Literal["research", "actionable"] = Query(default="research"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    user: User | None = Depends(optional_approved_user),
) -> ShortResearchAssetListOut:
    try:
        tracking_filters = _csv_values(tracking_states)
        validate_tracking_states(tracking_filters)
        if tracking_filters and user is None:
            raise HTTPException(status_code=401, detail="持仓筛选需要登录")
        if tracking_filters and asset_type == "fund":
            raise ValueError("持仓筛选仅支持 ETF")
        selection = (
            await current_etf_ranking_surface_selection(
                session,
                ranking_surface=ranking_surface,
            )
            if asset_type == "etf"
            else None
        )
        run = selection.run if selection is not None else await latest_signal_run(
            session,
            asset_type=asset_type,
            theme=theme,
        )
        if run is None and theme is not None and asset_type != "etf":
            run = await latest_signal_run(session, asset_type=asset_type)
        if run is None:
            return ShortResearchAssetListOut(
                items=[],
                total=0,
                snapshot=EtfRankingSnapshotMetadataOut.model_validate(
                    _ranking_surface_snapshot_metadata(
                        None,
                        ranking_surface=ranking_surface,
                        selection_state=selection.state if selection is not None else None,
                    )
                    if asset_type == "etf"
                    else snapshot_metadata(None)
                ),
                ranking_surface=ranking_surface if asset_type == "etf" else None,
            )
        assets, total = await cached_signal_assets(
            session,
            run,
            asset_type=asset_type,
            theme=theme,
            q=q,
            sort=sort,
            universe=universe,
            limit=None if tracking_filters else limit,
            offset=0 if tracking_filters else offset,
            observation_labels=_csv_values(observation_labels),
            entry_labels=_csv_values(entry_labels),
            ranking_surface=ranking_surface if asset_type == "etf" else None,
        )
        if tracking_filters:
            assert user is not None
            states_by_code = await tracking_states_by_code(session, user_id=user.id, as_of_date=run.as_of_date)
            assets = [
                asset
                for asset in assets
                if tracking_filters & states_by_code.get(asset.metadata.code, set())
            ]
            assets = [replace(asset, filtered_position=index) for index, asset in enumerate(assets, start=1)]
            total = len(assets)
            assets = assets[offset : offset + limit]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    advisor_reports = await latest_reports_by_asset(session, run.id) if run is not None else {}
    validation_by_label = await latest_validation_evidence_by_label(session) if asset_type in {None, "etf"} else {}
    actionable_selection = (
        await current_etf_ranking_surface_selection(
            session,
            ranking_surface="actionable",
        )
        if asset_type in {None, "etf"}
        else None
    )
    portfolio_context_by_code = (
        _portfolio_contexts(
            await etf_observation_portfolio(
                session,
                source_run=(
                    actionable_selection.run
                    if actionable_selection is not None
                    else None
                ),
            )
        )
        if asset_type in {None, "etf"}
        else {}
    )
    theme_heat: list[dict[str, Any]] = []
    if asset_type in {None, "etf"}:
        heat_assets, _heat_total = await cached_signal_assets(
            session,
            run,
            asset_type="etf",
            sort=sort,
            universe=universe,
            limit=2000,
            offset=0,
            ranking_surface=(
                "research"
                if (
                    run.score_version == "daily_reconstructable_v1"
                    and run.rule_version == DUAL_RANKING_RULE_VERSION
                )
                else None
            ),
        )
        theme_heat = _theme_heat_summary(heat_assets)
    catalyst_shadow_by_code = await _catalyst_shadow_by_asset_code(
        session,
        assets,
        as_of_date=run.as_of_date,
    )
    return ShortResearchAssetListOut(
        items=[
            _asset_out(
                item,
                advisor_reports.get((item.metadata.asset_type, item.metadata.code)),
                signal_run=run,
                validation_evidence=_validation_for_asset(item, validation_by_label),
                observation_portfolio=portfolio_context_by_code.get(item.metadata.code, {}),
                catalyst_shadow=catalyst_shadow_by_code.get(
                    item.metadata.code,
                    {},
                ),
                provisional_research=(
                    selection is not None and selection.state == "provisional"
                ),
            )
            for item in assets
        ],
        total=total,
        generated_at=run.finished_at or run.started_at,
        as_of_date=run.as_of_date,
        theme_heat=theme_heat,
        snapshot=EtfRankingSnapshotMetadataOut.model_validate(
            _ranking_surface_snapshot_metadata(
                run,
                ranking_surface=ranking_surface,
                selection_state=(
                    selection.state if selection is not None else None
                ),
            )
            if asset_type == "etf"
            else snapshot_metadata(run)
        ),
        ranking_surface=ranking_surface if asset_type == "etf" else None,
    )


@router.get("/observation-portfolio", response_model=ShortResearchObservationPortfolioOut)
async def get_short_research_observation_portfolio(
    asset_type: str = Query(default="etf"),
    limit: int = Query(default=5, ge=1, le=10),
    universe: str = Query(default="default"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if asset_type != "etf":
        raise HTTPException(status_code=400, detail="观察组合第一版只支持场内 ETF")
    return await etf_observation_portfolio(session, limit=limit, universe=universe)


@router.post("/etf-strategy-healthcheck/run", response_model=EtfStrategyHealthcheckOut)
async def run_etf_strategy_healthcheck_endpoint(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    snapshot = await run_etf_strategy_healthcheck(session)
    result = await healthcheck_payload(session, snapshot)
    if result is None:
        raise HTTPException(status_code=500, detail="策略健康检查结果不可用")
    return result


@router.get("/etf-strategy-healthcheck/latest", response_model=EtfStrategyHealthcheckOut | None)
async def get_latest_etf_strategy_healthcheck(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any] | None:
    snapshot = await latest_healthcheck_snapshot(session)
    if snapshot is None:
        return None
    return await healthcheck_payload(session, snapshot)


@router.post("/etf-optimized-allocation/run", response_model=EtfOptimizedAllocationOut)
async def run_etf_optimized_allocation_endpoint(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    snapshot = await run_etf_optimized_allocation(session)
    result = await optimized_allocation_payload(
        session,
        snapshot,
        expected_source_signal_run_id=snapshot.source_signal_run_id,
    )
    if result is None:
        raise HTTPException(status_code=500, detail="ETF 优化配置结果不可用")
    return result


@router.get("/etf-optimized-allocation/latest", response_model=EtfOptimizedAllocationOut | None)
async def get_latest_etf_optimized_allocation(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any] | None:
    snapshot = await latest_optimized_allocation_snapshot(session)
    if snapshot is None:
        return None
    return await optimized_allocation_payload(
        session,
        snapshot,
        expected_source_signal_run_id=snapshot.source_signal_run_id,
    )


@router.post("/etf-backtests", response_model=EtfPortfolioBacktestDetailOut)
async def start_etf_portfolio_backtest(
    payload: EtfPortfolioBacktestRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    payload = payload or EtfPortfolioBacktestRequest()
    run_factory = run_etf_intraday_alert_backtest if payload.execution_model == "intraday_alert" else run_etf_portfolio_backtest
    run = await run_factory(
        session,
        user=user,
        start_date=payload.start_date,
        end_date=payload.end_date,
        days=payload.days,
        initial_cash=payload.initial_cash,
        fee_rate=payload.fee_rate,
        max_assets=payload.max_assets,
    )
    return await backtest_detail_payload(session, run)


@router.get("/etf-backtests", response_model=EtfPortfolioBacktestListOut)
async def list_etf_portfolio_backtests(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    runs = await list_backtest_runs(session, limit=limit)
    return {"items": [backtest_summary_payload(run) for run in runs]}


@router.get("/etf-backtests/{run_id}", response_model=EtfPortfolioBacktestDetailOut)
async def get_etf_portfolio_backtest(
    run_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    run = await get_backtest_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="未找到 ETF 组合回测记录")
    return await backtest_detail_payload(session, run)


@router.post("/etf-strategy-comparisons", response_model=EtfStrategyComparisonOut)
async def start_etf_strategy_comparison(
    payload: EtfStrategyComparisonRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    payload = payload or EtfStrategyComparisonRequest()
    run = await run_etf_strategy_comparison_backtest(
        session,
        user=user,
        start_date=payload.start_date,
        end_date=payload.end_date,
        days=payload.days,
        initial_cash=payload.initial_cash,
        fee_rate=payload.fee_rate,
        max_assets=payload.max_assets,
    )
    return strategy_comparison_payload(run)


@router.get("/etf-strategy-comparisons/latest", response_model=EtfStrategyComparisonOut | None)
async def get_latest_etf_strategy_comparison(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any] | None:
    run = await latest_strategy_comparison_run(session)
    if run is None:
        return None
    return strategy_comparison_payload(run)


@router.get("/etf-strategy-comparisons/{run_id}", response_model=EtfStrategyComparisonOut)
async def get_etf_strategy_comparison(
    run_id: int,
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    run = await get_backtest_run(session, run_id)
    if run is None or run.rule_version != "etf_strategy_comparison_v1":
        raise HTTPException(status_code=404, detail="未找到 ETF 策略对照记录")
    return strategy_comparison_payload(run)


@router.post("/etf-exit-hyperopt/run", response_model=EtfExitHyperoptRunOut)
async def run_etf_exit_hyperopt_endpoint(
    payload: EtfExitHyperoptRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    payload = payload or EtfExitHyperoptRequest()
    run = await run_etf_exit_hyperopt(
        session,
        days=payload.days,
        max_assets=payload.max_assets,
        objective=payload.objective,
        execution_model=payload.execution_model,
        manual_delay_minutes=payload.manual_delay_minutes,
        universe_scope=payload.universe_scope,
        batch_size=payload.batch_size,
    )
    return await etf_exit_hyperopt_payload(session, run)


@router.get("/etf-exit-hyperopt/latest", response_model=EtfExitHyperoptRunOut | None)
async def get_latest_etf_exit_hyperopt(
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any] | None:
    run = await latest_etf_exit_hyperopt_run(session)
    if run is None:
        return None
    return await etf_exit_hyperopt_payload(session, run)


@router.post("/etf-exit-credibility/run", response_model=EtfExitCredibilityRunOut)
async def run_etf_exit_credibility_endpoint(
    payload: EtfExitCredibilityRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any]:
    payload = payload or EtfExitCredibilityRequest()
    run = await run_etf_exit_credibility(
        session,
        days=payload.days,
        max_assets=payload.max_assets,
        execution_model=payload.execution_model,
        universe_scope=payload.universe_scope,
    )
    return await etf_exit_credibility_payload(session, run)


@router.get("/etf-exit-credibility/latest", response_model=EtfExitCredibilityRunOut | None)
async def get_latest_etf_exit_credibility(
    execution_model: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_approved_user),
) -> dict[str, Any] | None:
    run = await latest_etf_exit_credibility_run(session, execution_model=execution_model)
    if run is None:
        return None
    return await etf_exit_credibility_payload(session, run)


@router.post("/validation/run", response_model=EtfSignalValidationRunOut)
async def run_short_research_validation(
    validation_mode: str = Query(default=VALIDATION_MODE_FORWARD_LIVE),
    days: int = Query(default=180, ge=30, le=730),
    max_assets: int | None = Query(default=None, ge=1, le=2000),
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut:
    if validation_mode == VALIDATION_MODE_HISTORICAL_REPLAY:
        run = await run_etf_label_historical_replay(session, days=days, max_assets=max_assets)
    elif validation_mode == VALIDATION_MODE_SCORE_BUCKET_REPLAY:
        run = await run_etf_score_bucket_validation(session, days=days)
    elif validation_mode == VALIDATION_MODE_FORWARD_LIVE:
        run = await run_etf_signal_validation(session)
    else:
        raise HTTPException(
            status_code=400,
            detail="validation_mode 只支持 forward_live、historical_replay 或 score_bucket_replay",
        )
    return await _validation_run_out(session, run)


@router.post("/validation/historical-replay/run", response_model=EtfSignalValidationRunOut)
async def run_short_research_historical_replay(
    days: int = Query(default=180, ge=30, le=730),
    max_assets: int | None = Query(default=None, ge=1, le=2000),
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut:
    run = await run_etf_label_historical_replay(session, days=days, max_assets=max_assets)
    return await _validation_run_out(session, run)


@router.post("/validation/score-buckets/run", response_model=EtfSignalValidationRunOut)
async def run_short_research_score_bucket_validation(
    days: int = Query(default=180, ge=30, le=730),
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut:
    run = await run_etf_score_bucket_validation(session, days=days)
    return await _validation_run_out(session, run)


@router.get("/validation/latest", response_model=EtfSignalValidationRunOut | None)
async def get_latest_short_research_validation(
    validation_mode: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut | None:
    run = await latest_signal_validation_run(session, validation_mode=validation_mode)
    if run is None:
        return None
    return await _validation_run_out(session, run)


@router.get("/validation/historical-replay/latest", response_model=EtfSignalValidationRunOut | None)
async def get_latest_short_research_historical_replay(
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut | None:
    run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_HISTORICAL_REPLAY)
    if run is None:
        return None
    return await _validation_run_out(session, run)


@router.get("/validation/score-buckets/latest", response_model=EtfSignalValidationRunOut | None)
async def get_latest_short_research_score_bucket_validation(
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut | None:
    run = await latest_signal_validation_run(session, validation_mode=VALIDATION_MODE_SCORE_BUCKET_REPLAY)
    if run is None:
        return None
    return await _validation_run_out(session, run)


@router.get("/validation", response_model=list[EtfSignalValidationRunOut])
async def list_short_research_validations(
    limit: int = Query(default=10, ge=1, le=50),
    validation_mode: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> list[EtfSignalValidationRunOut]:
    query = select(EtfSignalValidationRun)
    if validation_mode is not None:
        query = query.where(EtfSignalValidationRun.validation_mode == validation_mode)
    runs = (
        await session.scalars(
            query.order_by(EtfSignalValidationRun.as_of_date.desc(), EtfSignalValidationRun.id.desc())
            .limit(limit)
        )
    ).all()
    return [await _validation_run_out(session, run) for run in runs]


@router.get("/assets/{asset_type}/{code}", response_model=ShortResearchAssetDetailOut)
async def get_short_research_asset_detail(
    asset_type: str,
    code: str,
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchAssetDetailOut:
    run = None
    if asset_type == "etf":
        selection = await current_etf_ranking_surface_selection(
            session,
            ranking_surface="research",
        )
        if selection.run is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "当前 ETF 综合排名快照不可用",
                    "snapshot_state": selection.state,
                },
            )
        run = selection.run
    try:
        asset, chart, sections = await get_asset_detail(session, asset_type, code, source_run=run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    advisor_reports = await latest_reports_by_asset(session, run.id) if run is not None else {}
    validation_by_label = await latest_validation_evidence_by_label(session) if asset.metadata.asset_type == "etf" else {}
    portfolio_context_by_code = (
        _portfolio_contexts(await etf_observation_portfolio(session, source_run=run))
        if asset.metadata.asset_type == "etf"
        else {}
    )
    catalyst_shadow_by_code = await _catalyst_shadow_by_asset_code(
        session,
        [asset],
        as_of_date=run.as_of_date if run is not None else asset.latest_date or date.today(),
    )
    return ShortResearchAssetDetailOut(
        asset=_asset_out(
            asset,
            advisor_reports.get((asset.metadata.asset_type, asset.metadata.code)),
            signal_run=run,
            validation_evidence=_validation_for_asset(asset, validation_by_label),
            observation_portfolio=portfolio_context_by_code.get(asset.metadata.code, {}),
            catalyst_shadow=catalyst_shadow_by_code.get(asset.metadata.code, {}),
        ),
        chart=[
            ShortResearchChartPointOut(
                date=item["date"],
                value=item["value"],
                close=item["close"],
                nav=item["nav"],
                drawdown=item["drawdown"],
                turnover=item["turnover"],
            )
            for item in chart
        ],
        return_windows={
            "return_5d": asset.metrics.get("return_5d"),
            "return_10d": asset.metrics.get("return_10d"),
            "return_20d": asset.metrics.get("return_20d"),
            "return_60d": asset.metrics.get("return_60d"),
        },
        explanation_sections=sections,
        snapshot=EtfRankingSnapshotMetadataOut.model_validate(snapshot_metadata(run)),
    )


@router.post("/data/sync")
async def run_short_research_data_sync(
    payload: ShortResearchDataSyncRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    to_date = payload.to_date or date.today()
    from_date = payload.from_date or (to_date - timedelta(days=payload.days))
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return await sync_short_research_data(
        session,
        from_date=from_date,
        to_date=to_date,
        asset_type=payload.asset_type,
        codes=payload.codes,
    )


@router.post("/signals/run", response_model=ShortResearchSignalRunOut)
async def run_short_research_signals(
    payload: ShortResearchSignalRunRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchSignalRunOut:
    if payload.asset_type != "fund":
        raise HTTPException(
            status_code=409,
            detail="ETF 综合排名仅由 canonical v3 工作流发布；当前接口只允许生成基金研究信号。",
        )
    run = await run_signal_generation(
        session,
        as_of_date=payload.as_of_date,
        asset_type=payload.asset_type,
        theme=payload.theme,
        codes=payload.codes,
    )
    return await _signal_run_out(
        session,
        run,
        asset_type=payload.asset_type,
        theme=payload.theme,
        codes=payload.codes,
    )


@router.post("/advisor/run")
async def run_short_research_advisor(
    request: Request,
    payload: ShortResearchAdvisorRunRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    payload = payload or ShortResearchAdvisorRunRequest()
    try:
        return await run_advisor_generation(
            session,
            request.app.state.settings,
            asset_type=payload.asset_type,
            theme=payload.theme,
            codes=payload.codes,
            as_of_date=payload.as_of_date,
            source_signal_run_id=payload.source_signal_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/signals/latest", response_model=ShortResearchSignalRunOut | None)
async def get_latest_short_research_signals(
    asset_type: str | None = Query(default=None),
    theme: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchSignalRunOut | None:
    run = await latest_signal_run(session, asset_type=asset_type, theme=theme)
    if run is None:
        return None
    return await _signal_run_out(session, run, asset_type=asset_type, theme=theme)
