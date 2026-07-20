from __future__ import annotations

import math
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
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_contract import (
    build_ranking_contract,
    canonical_hash,
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
from app.services.short_research.universe import build_point_in_time_universe_snapshot


def _finite_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    score = float(value)
    return score if math.isfinite(score) else None


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
    research_excluded: list[dict[str, Any]] = []
    actionable_excluded: list[dict[str, Any]] = []
    for code in expected_codes:
        asset = assets_by_code.get(code)
        research_reasons: list[str] = []
        if code not in decision_code_set:
            research_reasons.append(decision_exclusions.get(code, "decision_data_ineligible"))
        elif asset is None:
            research_reasons.append("missing_computed_asset")
        else:
            score = _finite_score(asset.metrics.get("research_score"))
            if asset.metrics.get("research_score_eligible") is not True or score is None:
                research_reasons.append(
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
                research_reasons.append("research_contract_identity_mismatch")
        if research_reasons:
            research_excluded.append(
                {"asset_code": code, "reasons": sorted(set(research_reasons))}
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
    actionable_rank_by_code = {
        asset.metadata.code: rank
        for rank, (asset, _score) in enumerate(actionable, start=1)
    }
    input_snapshot = {
        "trade_date": trade_date,
        "decision_cutoff": market_cutoff,
        "source_availability_cutoff": data_cutoff,
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
            }
            for code in expected_codes
        ],
    }
    surface_manifest = {
        "research": research_manifest.canonical_payload(),
        "research_hash": research_manifest.manifest_hash,
        "actionable": actionable_manifest.canonical_payload(),
        "actionable_hash": actionable_manifest.manifest_hash,
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

    expected_count = len(expected_codes)
    research_ratio = len(research) / expected_count if expected_count else 0.0
    actionable_ratio = len(actionable) / expected_count if expected_count else 0.0
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
        },
        summary_json={
            "item_count": len(research),
            "fund_count": 0,
            "etf_count": len(research),
            "research_only": True,
            "surface_group_hash": surface_group_hash,
            "ranking_surfaces": {
                "research": {
                    "contract_id": research_manifest.contract_id,
                    "score_field": research_manifest.score_field,
                    "contract_hash": research_manifest.manifest_hash,
                    "eligible_count": len(research),
                    "coverage_ratio": round(research_ratio, 6),
                    "excluded": research_excluded,
                },
                "actionable": {
                    "contract_id": actionable_manifest.contract_id,
                    "score_field": actionable_manifest.score_field,
                    "contract_hash": actionable_manifest.manifest_hash,
                    "eligible_count": len(actionable),
                    "coverage_ratio": round(actionable_ratio, 6),
                    "excluded": actionable_excluded,
                },
            },
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
        eligible_item_count=len(research),
        coverage_ratio=round(research_ratio, 6),
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
                        "research_rank": research_rank,
                        "research_score": research_score,
                        "actionable_rank": actionable_rank,
                        "actionable_score": actionable_score,
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
        if not items or run.eligible_item_count != len(items):
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
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = utcnow()
            await session.flush()
    return run
