from __future__ import annotations

import math
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    authorize_snapshot_publication,
    utcnow,
)
from app.services.short_research.canonical_publication import (
    CanonicalPublicationError,
    build_provider_health_seal,
    canonical_publication_draft_from_run,
    register_canonical_publication,
)
from app.services.short_research.coverage_policy import (
    ETF_COMPLETE_SCORE_COVERAGE,
    ETF_DAILY_DECISION_MIN_COVERAGE,
    ETF_READINESS_POLICY_VERSION,
    evaluate_etf_readiness,
    evaluate_persisted_etf_readiness,
)
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_contract import (
    build_ranking_contract,
    canonical_hash,
)
from app.services.short_research.ranking_quality import (
    canonical_research_eligibility_policy,
    evaluate_canonical_research_eligibility,
)
from app.services.short_research.ranking_surfaces import (
    DUAL_RANKING_RULE_VERSION,
    actionable_rank_manifest,
)
from app.services.short_research.service import ComputedAsset, compute_etf_snapshot_assets
from app.services.short_research.snapshot_materialization import (
    SnapshotMaterializationError,
    _json_safe,
    _normalized_cutoff,
)
from app.services.short_research.snapshot_publication import (
    SnapshotPublicationError,
    _verify_snapshot_draft_seal,
    build_etf_coverage_barrier,
    build_snapshot_draft_seal,
)
from app.services.short_research.theme_heat import theme_heat_summary
from app.services.short_research.universe import build_point_in_time_universe_snapshot


def _finite_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    score = float(value)
    return score if math.isfinite(score) else None


def _observation_evidence(
    *,
    code: str,
    asset: ComputedAsset | None,
    reasons: list[str],
    decision_data_eligible: bool,
    price_basis: str,
) -> dict[str, Any]:
    metrics = asset.metrics if asset is not None else {}
    theme_profile = metrics.get("theme_profile")
    theme_profile = theme_profile if isinstance(theme_profile, dict) else {}
    return {
        "asset_code": code,
        "reasons": sorted(set(reasons)),
        "evidence": {
            "eligible_adjusted_sessions": asset.usable_days if asset is not None else 0,
            "price_basis": price_basis,
            "decision_data_eligible": decision_data_eligible,
            "average_turnover_20d": _finite_score(
                metrics.get("average_turnover_20d")
            ),
            "taxonomy_bucket": metrics.get("ranking_asset_bucket"),
            "taxonomy_source": theme_profile.get("classification_source"),
            "taxonomy_confidence": theme_profile.get("classification_confidence"),
            "research_score": _finite_score(metrics.get("research_score")),
            "research_score_eligible": metrics.get("research_score_eligible") is True,
            "latest_data_date": (
                asset.latest_date.isoformat()
                if asset is not None and asset.latest_date is not None
                else None
            ),
        },
    }


async def materialize_dual_ranking_snapshot(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
    source_availability_cutoff: datetime | None = None,
    batch_progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> ShortResearchSignalRun:
    market_cutoff = _normalized_cutoff(decision_cutoff)
    data_cutoff = _normalized_cutoff(source_availability_cutoff or decision_cutoff)
    if market_cutoff.date() != trade_date or data_cutoff.date() != trade_date:
        raise ValueError("decision cutoff must match trade date")

    universe = await build_point_in_time_universe_snapshot(session, as_of_date=trade_date)
    barrier = await build_etf_coverage_barrier(
        session,
        as_of_trade_date=trade_date,
        data_cutoff=data_cutoff,
    )
    expected_codes = [str(member["asset_code"]) for member in universe.members]
    if expected_codes != barrier.expected_codes:
        raise SnapshotMaterializationError("point-in-time universe changed during materialization")

    batch_audit: list[dict[str, Any]] = []
    assets = await compute_etf_snapshot_assets(
        session,
        codes=list(barrier.included_codes),
        as_of_date=trade_date,
        decision_cutoff=market_cutoff,
        data_cutoff=data_cutoff,
        batch_audit=batch_audit,
        batch_progress_callback=batch_progress_callback,
    )
    assets_by_code = {asset.metadata.code: asset for asset in assets}
    research_manifest = daily_reconstructable_manifest()
    actionable_manifest = actionable_rank_manifest()
    decision_code_set = set(barrier.included_codes)
    decision_exclusions = {
        str(item["asset_code"]): str(item["reason"])
        for item in barrier.excluded
    }

    research: list[tuple[ComputedAsset, float]] = []
    actionable: list[tuple[ComputedAsset, float]] = []
    score_ready_codes: list[str] = []
    score_excluded: list[dict[str, Any]] = []
    research_excluded: list[dict[str, Any]] = []
    actionable_excluded: list[dict[str, Any]] = []
    for code in expected_codes:
        asset = assets_by_code.get(code)
        score_reasons: list[str] = []
        quality_reasons: list[str] = []
        if code not in decision_code_set:
            score_reasons.append(
                decision_exclusions.get(code, "decision_data_ineligible")
            )
        elif asset is None:
            score_reasons.append("missing_computed_asset")
        else:
            score = _finite_score(asset.metrics.get("research_score"))
            if asset.usable_days < research_manifest.required_bar_count:
                score_reasons.append(
                    "history:insufficient_61_eligible_adjusted_sessions"
                )
            if asset.metrics.get("research_score_eligible") is not True or score is None:
                score_reasons.append(
                    str(
                        asset.metrics.get("research_score_unavailable_reason")
                        or "research_score_unavailable"
                    )
                )
            if (
                asset.metrics.get("research_contract_id") != research_manifest.contract_id
                or asset.metrics.get("research_score_field") != research_manifest.score_field
                or asset.metrics.get("research_contract_hash") != research_manifest.manifest_hash
            ):
                score_reasons.append("research_contract_identity_mismatch")
            theme_profile = asset.metrics.get("theme_profile")
            taxonomy_evidence_valid = (
                isinstance(theme_profile, dict)
                and str(theme_profile.get("classification_source") or "") != "unknown"
                and str(theme_profile.get("classification_confidence") or "")
                not in {"", "unknown"}
            )
            quality = evaluate_canonical_research_eligibility(
                eligible_sessions=int(asset.usable_days),
                price_basis=research_manifest.price_basis,
                decision_data_eligible=code in decision_code_set,
                finite_adjusted_inputs=(
                    score is not None
                    and asset.metrics.get("research_score_eligible") is True
                ),
                average_turnover_20d=asset.metrics.get("average_turnover_20d"),
                taxonomy_bucket=asset.metrics.get("ranking_asset_bucket"),
                taxonomy_evidence_valid=taxonomy_evidence_valid,
                default_display_eligible=(
                    asset.metrics.get("default_display_eligible") is True
                ),
            )
            quality_reasons.extend(quality.reasons)
        score_reasons = sorted(set(score_reasons))
        if score_reasons:
            score_excluded.append(
                _observation_evidence(
                    code=code,
                    asset=asset,
                    reasons=score_reasons,
                    decision_data_eligible=code in decision_code_set,
                    price_basis=research_manifest.price_basis,
                )
            )
        else:
            score_ready_codes.append(code)

        research_reasons = sorted(set([*score_reasons, *quality_reasons]))
        if research_reasons:
            research_excluded.append(
                _observation_evidence(
                    code=code,
                    asset=asset,
                    reasons=research_reasons,
                    decision_data_eligible=code in decision_code_set,
                    price_basis=research_manifest.price_basis,
                )
            )
        else:
            assert asset is not None
            score = _finite_score(asset.metrics.get("research_score"))
            assert score is not None
            research.append((asset, score))

        action_reasons: list[str] = []
        if research_reasons:
            action_reasons.append("research_surface_unavailable")
        if asset is None:
            action_reasons.append("missing_computed_asset")
        else:
            action_score = _finite_score(asset.metrics.get("actionable_score"))
            declared_reasons = asset.metrics.get("actionable_exclusion_reasons")
            if isinstance(declared_reasons, list):
                action_reasons.extend(str(reason) for reason in declared_reasons if reason)
            if asset.metrics.get("actionable_eligible") is not True or action_score is None:
                if not action_reasons:
                    action_reasons.append("actionable_rank_unavailable")
            if (
                asset.metrics.get("actionable_contract_id") != actionable_manifest.contract_id
                or asset.metrics.get("actionable_score_field") != actionable_manifest.score_field
                or asset.metrics.get("actionable_contract_hash") != actionable_manifest.manifest_hash
            ):
                action_reasons.append("actionable_contract_identity_mismatch")
        if action_reasons:
            actionable_excluded.append(
                {"asset_code": code, "reasons": sorted(set(action_reasons))}
            )
        else:
            assert asset is not None
            action_score = _finite_score(asset.metrics.get("actionable_score"))
            assert action_score is not None
            actionable.append((asset, action_score))

    research.sort(key=lambda item: (-item[1], item[0].metadata.code))
    actionable.sort(key=lambda item: (-item[1], item[0].metadata.code))
    expected_count = len(expected_codes)
    score_coverage_ratio = (
        len(score_ready_codes) / expected_count if expected_count else 0.0
    )
    canonical_research_ratio = (
        len(research) / expected_count if expected_count else 0.0
    )
    readiness = evaluate_etf_readiness(
        daily_coverage_ratio=barrier.coverage_ratio,
        warmup_coverage_ratio=score_coverage_ratio,
    )
    if readiness.state == "blocked":
        raise SnapshotMaterializationError(
            "ETF readiness is blocked; no target-date ranking may materialize"
        )
    if readiness.state == "degraded":
        actionable = []
        actionable_excluded = [
            {
                "asset_code": code,
                "reasons": ["provisional_research_only"],
            }
            for code in expected_codes
        ]
    provider_health_identity = build_provider_health_seal(
        candidate_evidence=[
            {
                "asset_code": asset.metadata.code,
                "field_statuses": dict(
                    asset.metrics.get("actionable_field_statuses") or {}
                ),
                "source_times": dict(
                    asset.metrics.get("actionable_source_times") or {}
                ),
            }
            for asset, _score in research
        ],
        manifest=actionable_manifest,
        trade_date=trade_date,
        decision_cutoff=market_cutoff,
    )
    provider_health_reason = (
        str(provider_health_identity.get("unavailable_reason") or "") or None
    )
    if (
        readiness.state == "complete"
        and provider_health_identity.get("state") != "compatible"
    ):
        exclusion_by_code = {
            str(item["asset_code"]): set(item.get("reasons") or [])
            for item in actionable_excluded
        }
        for asset, _score in research:
            exclusion_by_code.setdefault(asset.metadata.code, set()).add(
                provider_health_reason or "provider_health_seal_incompatible"
            )
        actionable = []
        actionable_excluded = [
            {
                "asset_code": code,
                "reasons": sorted(exclusion_by_code[code]),
            }
            for code in sorted(exclusion_by_code)
        ]
    actionable_rank_by_code = {
        asset.metadata.code: rank
        for rank, (asset, _score) in enumerate(actionable, start=1)
    }
    diversified_position_by_code = {
        asset.metadata.code: position
        for position, (asset, _score) in enumerate(
            (
                item
                for item in research
                if item[0].metrics.get("diversified_representative") is True
            ),
            start=1,
        )
    }
    theme_counts = Counter(
        str(
            (asset.metrics.get("theme_profile") or {}).get("theme_group")
            if isinstance(asset.metrics.get("theme_profile"), dict)
            else "unknown"
        )
        for asset, _score in research
    )
    clone_group_counts = Counter(
        str(asset.metrics.get("clone_group_id"))
        for asset, _score in research
        if asset.metrics.get("clone_group_id")
    )
    tracked_underlying_coverage = max(
        (
            float(asset.metrics.get("tracked_underlying_coverage") or 0.0)
            for asset, _score in research
        ),
        default=0.0,
    )
    concentration_evidence = {
        "clone_policy_active": any(
            asset.metrics.get("clone_policy_active") is True
            for asset, _score in research
        ),
        "tracked_underlying_coverage": round(
            tracked_underlying_coverage,
            6,
        ),
        "unresolved_underlying_count": sum(
            not bool(str(asset.metrics.get("tracked_underlying_id") or "").strip())
            for asset, _score in research
        ),
        "clone_group_count": len(clone_group_counts),
        "repeated_clone_group_count": sum(
            count > 1 for count in clone_group_counts.values()
        ),
        "diversified_representative_count": len(diversified_position_by_code),
        "theme_counts": dict(sorted(theme_counts.items())),
    }
    input_snapshot = {
        "trade_date": trade_date,
        "decision_cutoff": market_cutoff,
        "source_availability_cutoff": data_cutoff,
        "replay_visibility_cutoff": None,
        "assets": [
            {
                "asset_code": code,
                "history_digest": (
                    assets_by_code[code].metrics.get("v3_adjusted_price_history_digest")
                    if code in assets_by_code
                    else None
                ),
                "research_score": (
                    assets_by_code[code].metrics.get("research_score")
                    if code in assets_by_code
                    else None
                ),
                "actionable_score": (
                    assets_by_code[code].metrics.get("actionable_score")
                    if code in assets_by_code
                    else None
                ),
                "actionable_field_statuses": (
                    assets_by_code[code].metrics.get("actionable_field_statuses")
                    if code in assets_by_code
                    else None
                ),
                "actionable_source_times": (
                    assets_by_code[code].metrics.get("actionable_source_times")
                    if code in assets_by_code
                    else None
                ),
                "canonical_eligibility": (
                    {
                        "policy": canonical_research_eligibility_policy(),
                        "default_display_eligible": assets_by_code[code].metrics.get(
                            "default_display_eligible"
                        ),
                        "average_turnover_20d": assets_by_code[code].metrics.get(
                            "average_turnover_20d"
                        ),
                        "taxonomy_bucket": assets_by_code[code].metrics.get(
                            "ranking_asset_bucket"
                        ),
                        "theme_profile": assets_by_code[code].metrics.get(
                            "theme_profile"
                        ),
                    }
                    if code in assets_by_code
                    else None
                ),
            }
            for code in expected_codes
        ],
    }
    surface_manifest = {
        "research": research_manifest.canonical_payload(),
        "research_hash": research_manifest.manifest_hash,
        "actionable": actionable_manifest.canonical_payload(),
        "actionable_hash": actionable_manifest.manifest_hash,
        "readiness_policy": {
            "policy_version": readiness.policy_version,
            "daily_coverage_threshold": ETF_DAILY_DECISION_MIN_COVERAGE,
            "warmup_coverage_threshold": ETF_COMPLETE_SCORE_COVERAGE,
            "warmup_coverage_kind": "daily_reconstructable_score_eligible",
        },
        "canonical_research_eligibility": canonical_research_eligibility_policy(),
        "provider_health_policy": provider_health_identity.get("policy", {}),
    }
    identity = build_ranking_contract(
        score_version=research_manifest.contract_id,
        rule_version=DUAL_RANKING_RULE_VERSION,
        component_manifest=surface_manifest,
        scope={"scope_kind": "full", "asset_type": "etf", "theme": None, "codes": []},
        universe_snapshot=universe.members,
        input_snapshot=input_snapshot,
        price_basis=research_manifest.price_basis,
        data_cutoff=data_cutoff,
        reliability_policy={
            "research": "decision_eligible_total_return_adjusted_daily",
            "actionable": "all_actionable_rank_v1_gates_must_pass",
            "fallback": "none",
        },
    )
    surface_group_hash = canonical_hash(surface_manifest)
    idempotency_key = canonical_hash(
        {
            "trade_date": trade_date,
            "scope_hash": identity["scope_hash"],
            "input_snapshot_hash": identity["input_snapshot_hash"],
            "ranking_contract_hash": identity["ranking_contract_hash"],
            "surface_group_hash": surface_group_hash,
            "provider_health_seal_hash": provider_health_identity["hash"],
        }
    )
    existing = await session.scalar(
        select(ShortResearchSignalRun).where(
            ShortResearchSignalRun.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        existing_items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == existing.id)
                .order_by(
                    ShortResearchSignalItem.global_rank.asc(),
                    ShortResearchSignalItem.asset_code.asc(),
                )
            )
        ).all()
        if (
            existing.score_version != research_manifest.contract_id
            or existing.ranking_contract_hash != identity["ranking_contract_hash"]
            or len(existing_items) != len(research)
            or (existing.summary_json or {}).get("draft_seal")
            != build_snapshot_draft_seal(existing, existing_items)
        ):
            raise SnapshotMaterializationError("idempotent dual ranking snapshot is incomplete")
        return existing

    actionable_ratio = len(actionable) / expected_count if expected_count else 0.0
    actionable_blocker_counts = dict(
        sorted(
            Counter(
                reason
                for item in actionable_excluded
                for reason in item["reasons"]
            ).items()
        )
    )
    now = utcnow()
    run = ShortResearchSignalRun(
        status="success",
        started_at=now,
        finished_at=now,
        as_of_date=trade_date,
        config_json={
            "asset_type": "etf",
            "generation_mode": "dual_ranking_surfaces",
            "surface_group_hash": surface_group_hash,
            "market_decision_cutoff": market_cutoff.isoformat(),
            "source_availability_cutoff": data_cutoff.isoformat(),
            "replay_visibility_cutoff": None,
            "readiness_policy_version": readiness.policy_version,
            "readiness_state": readiness.state,
        },
        summary_json={
            "item_count": len(research),
            "fund_count": 0,
            "etf_count": len(research),
            "research_only": True,
            "readiness_policy": readiness.to_dict(),
            "readiness_policy_version": readiness.policy_version,
            "readiness_state": readiness.state,
            "snapshot_state": (
                "provisional"
                if readiness.state == "degraded"
                else "complete_candidate"
            ),
            "surface_availability_state": (
                "provisional"
                if readiness.state == "degraded"
                else "research_complete_actionable_available"
                if actionable
                else "research_complete_actionable_unavailable"
            ),
            "unavailable_reason": (
                "history_depth_61_coverage_below_90pct"
                if readiness.state == "degraded"
                else provider_health_reason
                if not actionable
                and provider_health_identity.get("state") != "compatible"
                else None
            ),
            "cutoff_provenance": {
                "market_decision_cutoff": market_cutoff.isoformat(),
                "data_receipt_cutoff": data_cutoff.isoformat(),
                "replay_visibility_cutoff": None,
            },
            "surface_group_hash": surface_group_hash,
            "provider_health_identity": provider_health_identity,
            "score_coverage": {
                "coverage_kind": "daily_reconstructable_score_eligible",
                "expected_count": expected_count,
                "eligible_count": len(score_ready_codes),
                "coverage_ratio": round(score_coverage_ratio, 6),
                "eligible_code_hash": canonical_hash(sorted(score_ready_codes)),
                "excluded_count": len(score_excluded),
                "exclusion_reason_counts": dict(
                    sorted(
                        Counter(
                            reason
                            for item in score_excluded
                            for reason in item["reasons"]
                        ).items()
                    )
                ),
            },
            "ranking_surfaces": {
                "research": {
                    "contract_id": research_manifest.contract_id,
                    "score_field": research_manifest.score_field,
                    "contract_hash": research_manifest.manifest_hash,
                    "eligible_count": len(research),
                    "coverage_ratio": round(canonical_research_ratio, 6),
                    "excluded": research_excluded,
                },
                "actionable": {
                    "contract_id": actionable_manifest.contract_id,
                    "score_field": actionable_manifest.score_field,
                    "contract_hash": actionable_manifest.manifest_hash,
                    "eligible_count": len(actionable),
                    "coverage_ratio": round(actionable_ratio, 6),
                    "excluded": actionable_excluded,
                    "blocker_counts": actionable_blocker_counts,
                },
            },
            "canonical_research_eligibility": canonical_research_eligibility_policy(),
            "concentration_evidence": concentration_evidence,
            "observation_only": {
                "count": len(research_excluded),
                "items": research_excluded,
            },
            "theme_heat": theme_heat_summary(
                (asset for asset, _score in research),
                scores_by_code={
                    asset.metadata.code: score for asset, score in research
                },
            ),
            "input_snapshot": _json_safe(input_snapshot),
            "non_finite_reject_count": sum(
                any("non_finite" in reason for reason in item["reasons"])
                for item in [*research_excluded, *actionable_excluded]
            ),
            "cap_violation_count": sum(
                any("cap_violation" in reason for reason in item["reasons"])
                for item in actionable_excluded
            ),
            "batch_execution": {
                "batch_size_limit": 20,
                "batch_count": len(batch_audit),
                "peak_batch_size": max(
                    (int(item["batch_size"]) for item in batch_audit),
                    default=0,
                ),
                "batches": batch_audit,
            },
        },
        scope_kind="full",
        scope_hash=str(identity["scope_hash"]),
        universe_snapshot_hash=str(identity["universe_snapshot_hash"]),
        input_snapshot_hash=str(identity["input_snapshot_hash"]),
        score_version=research_manifest.contract_id,
        rule_version=DUAL_RANKING_RULE_VERSION,
        ranking_contract_hash=str(identity["ranking_contract_hash"]),
        score_field=research_manifest.score_field,
        data_cutoff=data_cutoff,
        as_of_trade_date=trade_date,
        price_basis=research_manifest.price_basis,
        expected_item_count=expected_count,
        decision_data_item_count=len(barrier.included_codes),
        decision_data_coverage_ratio=round(
            len(barrier.included_codes) / expected_count if expected_count else 0.0,
            6,
        ),
        eligible_item_count=len(score_ready_codes),
        coverage_ratio=round(score_coverage_ratio, 6),
        publication_state="unpublished",
        idempotency_key=idempotency_key,
    )
    session.add(run)
    await session.flush()

    actionable_score_by_code = {
        asset.metadata.code: score for asset, score in actionable
    }
    for research_rank, (asset, research_score) in enumerate(research, start=1):
        code = asset.metadata.code
        actionable_rank = actionable_rank_by_code.get(code)
        actionable_score = actionable_score_by_code.get(code)
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type="etf",
                asset_code=code,
                rank=research_rank,
                global_rank=research_rank,
                total_score=research_score,
                ranking_score=research_score,
                score_eligible=True,
                conclusion=asset.conclusion,
                score_breakdown_json=_json_safe(
                    {
                        **asset.score_breakdown,
                        "ranking_surfaces": {
                            "research": {
                                "rank": research_rank,
                                "score": research_score,
                                "contract_id": research_manifest.contract_id,
                                "score_field": research_manifest.score_field,
                                "contract_hash": research_manifest.manifest_hash,
                            },
                            "actionable": {
                                "rank": actionable_rank,
                                "score": actionable_score,
                                "eligible": actionable_rank is not None,
                                "contract_id": actionable_manifest.contract_id,
                                "score_field": actionable_manifest.score_field,
                                "contract_hash": actionable_manifest.manifest_hash,
                            },
                        },
                    }
                ),
                risk_flags_json=list(asset.risk_flags),
                rationale_json=_json_safe(asset.rationale),
                metrics_json=_json_safe(
                    {
                        **asset.metrics,
                        "ranking_surface": "research",
                        "canonical_research_rank": research_rank,
                        "observation_only": False,
                        "research_rank": research_rank,
                        "research_score": research_score,
                        "peer_diagnostics": {
                            "asset_bucket": asset.metrics.get(
                                "ranking_asset_bucket"
                            ),
                            "metric_peer_counts": asset.metrics.get(
                                "v3_metric_peer_counts",
                                {},
                            ),
                        },
                        "diversified_presentation_position": (
                            diversified_position_by_code.get(code)
                        ),
                        "diversified_representative": asset.metrics.get(
                            "diversified_representative"
                        ),
                        "actionable_rank": actionable_rank,
                        "actionable_score": actionable_score,
                        "actionable_eligible": actionable_rank is not None,
                        "actionable_exclusion_reasons": (
                            ["provisional_research_only"]
                            if readiness.state == "degraded"
                            else asset.metrics.get(
                                "actionable_exclusion_reasons",
                                [],
                            )
                        ),
                        "actionable_as_of_date": trade_date,
                        "latest_date": asset.latest_date,
                        "latest_value": asset.latest_value,
                        "usable_days": asset.usable_days,
                        "sample_level": asset.sample_level,
                        "source_note": asset.source_note,
                    }
                ),
            )
        )
        if research_rank % 20 == 0:
            await session.flush()
    await session.flush()
    sealed_items = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(ShortResearchSignalItem.run_id == run.id)
            .order_by(
                ShortResearchSignalItem.global_rank.asc(),
                ShortResearchSignalItem.asset_code.asc(),
            )
        )
    ).all()
    run.summary_json = {
        **run.summary_json,
        "draft_seal": build_snapshot_draft_seal(run, sealed_items),
    }
    await session.flush()
    return run


async def publish_dual_ranking_snapshot(
    session: AsyncSession,
    *,
    run_id: int,
) -> ShortResearchSignalRun:
    research_manifest = daily_reconstructable_manifest()
    actionable_manifest = actionable_rank_manifest()
    async with session.begin():
        run = await session.scalar(
            select(ShortResearchSignalRun)
            .where(ShortResearchSignalRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise SnapshotPublicationError("snapshot run does not exist")
        if run.publication_state == "published":
            return run
        if (
            run.status != "success"
            or run.scope_kind != "full"
            or run.score_version != research_manifest.contract_id
            or run.rule_version != DUAL_RANKING_RULE_VERSION
            or run.score_field != research_manifest.score_field
            or run.price_basis != research_manifest.price_basis
        ):
            raise SnapshotPublicationError("dual ranking snapshot identity mismatch")
        summary = run.summary_json or {}
        policy_payload = summary.get("readiness_policy")
        policy_version = (
            policy_payload.get("policy_version")
            if isinstance(policy_payload, dict)
            else summary.get("readiness_policy_version")
        )
        readiness = evaluate_persisted_etf_readiness(
            policy_version=policy_version if isinstance(policy_version, str) else None,
            daily_coverage_ratio=run.decision_data_coverage_ratio,
            warmup_coverage_ratio=run.coverage_ratio,
        )
        if (
            not readiness.complete_publication_allowed
            or summary.get("readiness_state") != "complete"
        ):
            raise SnapshotPublicationError(
                "dual ranking publication requires 95 percent daily and "
                "90 percent warmup readiness"
            )
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run.id)
                .order_by(
                    ShortResearchSignalItem.global_rank.asc(),
                    ShortResearchSignalItem.asset_code.asc(),
                )
                .with_for_update()
            )
        ).all()
        if not items:
            raise SnapshotPublicationError("research surface is empty or incomplete")
        if policy_version == ETF_READINESS_POLICY_VERSION:
            score_coverage = summary.get("score_coverage")
            if (
                not isinstance(score_coverage, dict)
                or score_coverage.get("coverage_kind")
                != "daily_reconstructable_score_eligible"
                or score_coverage.get("expected_count") != run.expected_item_count
                or score_coverage.get("eligible_count") != run.eligible_item_count
                or score_coverage.get("coverage_ratio")
                != round(float(run.coverage_ratio or 0.0), 6)
                or run.eligible_item_count is None
                or run.eligible_item_count < len(items)
                or summary.get("item_count") != len(items)
            ):
                raise SnapshotPublicationError(
                    "score-ready and canonical research coverage are inconsistent"
                )
        elif run.eligible_item_count != len(items):
            raise SnapshotPublicationError("research surface is empty or incomplete")
        if [item.global_rank for item in items] != list(range(1, len(items) + 1)):
            raise SnapshotPublicationError("research ranks are not contiguous")
        actionable_ranks: list[int] = []
        for item in items:
            metrics = item.metrics_json or {}
            if (
                item.asset_type != "etf"
                or item.score_eligible is not True
                or _finite_score(item.ranking_score) is None
                or metrics.get("research_contract_id") != research_manifest.contract_id
                or metrics.get("research_contract_hash") != research_manifest.manifest_hash
                or metrics.get("actionable_contract_id") != actionable_manifest.contract_id
                or metrics.get("actionable_contract_hash") != actionable_manifest.manifest_hash
            ):
                raise SnapshotPublicationError("ranking surface item identity mismatch")
            action_rank = metrics.get("actionable_rank")
            action_score = _finite_score(metrics.get("actionable_score"))
            if action_rank is None:
                if metrics.get("actionable_eligible") is True or action_score is not None:
                    raise SnapshotPublicationError("ineligible action row carries actionable score")
                continue
            if (
                not isinstance(action_rank, int)
                or action_rank <= 0
                or action_score is None
                or metrics.get("actionable_eligible") is not True
            ):
                raise SnapshotPublicationError("actionable rank is incomplete")
            actionable_ranks.append(action_rank)
        if sorted(actionable_ranks) != list(range(1, len(actionable_ranks) + 1)):
            raise SnapshotPublicationError("actionable ranks are not contiguous")
        _verify_snapshot_draft_seal(run, items)
        try:
            publication_draft = canonical_publication_draft_from_run(
                run,
                actionable_manifest=actionable_manifest,
            )
        except CanonicalPublicationError as exc:
            raise SnapshotPublicationError(str(exc)) from exc
        if actionable_ranks and not publication_draft.actionable_available:
            raise SnapshotPublicationError(
                "actionable rows require a compatible provider-health seal"
            )
        try:
            registration = await register_canonical_publication(
                session,
                run=run,
                actionable_manifest=actionable_manifest,
            )
        except CanonicalPublicationError as exc:
            raise SnapshotPublicationError(str(exc)) from exc
        if registration.winner_run_id != run.id:
            winner = await session.get(
                ShortResearchSignalRun,
                registration.winner_run_id,
            )
            if winner is None or winner.publication_state != "published":
                raise SnapshotPublicationError(
                    "canonical publication winner is not available"
                )
            return winner
        run.summary_json = {
            **summary,
            "publication_evidence": {
                "identity_version": "etf_canonical_publication_v1",
                "publication_registry_id": registration.registry.id,
                "publication_identity": registration.registry.publication_identity_hash,
                "canonical_slot_hash": registration.registry.canonical_slot_hash,
                "supersedes_publication_id": (
                    registration.registry.supersedes_publication_id
                ),
                "duplicate_publication": not registration.created,
                "is_current": registration.registry.is_current,
            },
        }
        run.summary_json = {
            **run.summary_json,
            "draft_seal": build_snapshot_draft_seal(run, items),
        }
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = utcnow()
            await session.flush()
    return run
