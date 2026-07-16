from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun, utcnow
from app.services.market_data import ASIA_SHANGHAI
from app.services.short_research.ranking_contract import (
    build_ranking_contract,
    canonical_hash,
    final_score_v3_contract,
    final_score_v3_manifest,
    ranking_strategy_semantics,
)
from app.services.short_research.service import ComputedAsset, compute_etf_snapshot_assets
from app.services.short_research.snapshot_publication import (
    build_etf_coverage_barrier,
    build_snapshot_draft_seal,
)
from app.services.short_research.universe import build_point_in_time_universe_snapshot


class SnapshotMaterializationError(ValueError):
    pass


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _normalized_cutoff(decision_cutoff: datetime) -> datetime:
    if decision_cutoff.tzinfo is None:
        return decision_cutoff
    return decision_cutoff.astimezone(ASIA_SHANGHAI).replace(tzinfo=None)


def _compact_input(asset: ComputedAsset | None, required_keys: set[str]) -> dict[str, Any] | None:
    if asset is None:
        return None
    persisted = asset.metrics.get("v3_input_values")
    if isinstance(persisted, Mapping):
        values = {str(key): persisted.get(key) for key in sorted(persisted)}
    else:
        values = {key: asset.metrics.get(key) for key in sorted(required_keys)}
    return {str(key): _json_safe(value) for key, value in values.items()}


def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _input_evidence(asset: ComputedAsset | None, required_keys: set[str]) -> dict[str, Any]:
    values = _compact_input(asset, required_keys)
    values_hash = canonical_hash(values)
    if asset is None:
        final_bucket = None
        ranking_bucket = None
        profile_version = None
        history_digest = None
    else:
        shadow = asset.score_breakdown.get("final_score_v3_shadow")
        final_bucket = shadow.get("asset_bucket") if isinstance(shadow, Mapping) else None
        ranking_bucket = asset.metrics.get("ranking_asset_bucket")
        profile_version = asset.metrics.get("ranking_profile_version")
        history_digest = asset.metrics.get("v3_adjusted_price_history_digest")
    bucket_payload = {
        "asset_bucket": str(final_bucket or ranking_bucket) if final_bucket or ranking_bucket else None,
        "ranking_asset_bucket": str(ranking_bucket) if ranking_bucket else None,
        "profile_version": str(profile_version) if profile_version else None,
        "source": "final_score_v3_shadow.asset_bucket" if final_bucket else "ranking_asset_bucket",
    }
    usable_history_digest = history_digest if _is_sha256_digest(history_digest) else None
    return {
        "input_values": values,
        "input_values_hash": values_hash,
        "adjusted_price_history_provenance": {
            "digest": usable_history_digest,
            "status": "sealed_digest" if usable_history_digest else "unavailable_not_emitted",
            "fallback_input_values_hash": None if usable_history_digest else values_hash,
        },
        "final_bucket_evidence": {
            **bucket_payload,
            "evidence_hash": canonical_hash(bucket_payload),
        },
    }


def _score_exclusion_reasons(asset: ComputedAsset | None) -> list[str]:
    if asset is None:
        return ["missing_computed_asset"]
    raw_score = asset.metrics.get("v3_ranking_score")
    if isinstance(raw_score, bool) or raw_score is None or not isinstance(raw_score, int | float):
        declared = asset.metrics.get("v3_score_limitation_reasons")
        if isinstance(declared, list) and declared:
            return sorted({str(reason) for reason in declared if str(reason)})
        return ["missing_v3_ranking_score"]
    if not math.isfinite(float(raw_score)):
        return ["non_finite_v3_ranking_score"]
    if asset.metrics.get("v3_score_eligible") is not True:
        declared = asset.metrics.get("v3_score_limitation_reasons")
        if isinstance(declared, list) and declared:
            return sorted({str(reason) for reason in declared if str(reason)})
        return ["v3_score_ineligible"]
    return []


async def materialize_final_score_v3_snapshot(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
    source_availability_cutoff: datetime | None = None,
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

    assets = await compute_etf_snapshot_assets(
        session,
        codes=list(barrier.included_codes),
        as_of_date=trade_date,
        decision_cutoff=market_cutoff,
        data_cutoff=data_cutoff,
    )
    assets_by_code = {asset.metadata.code: asset for asset in assets}
    decision_code_set = set(barrier.included_codes)
    decision_exclusion_by_code = {
        str(item["asset_code"]): str(item["reason"])
        for item in barrier.excluded
    }

    eligible: list[tuple[ComputedAsset, float]] = []
    score_excluded: list[dict[str, Any]] = []
    for code in expected_codes:
        asset = assets_by_code.get(code)
        if code not in decision_code_set:
            reasons = [decision_exclusion_by_code.get(code, "decision_data_ineligible")]
        else:
            reasons = _score_exclusion_reasons(asset)
        if reasons:
            score_excluded.append({"asset_code": code, "reasons": reasons})
            continue
        assert asset is not None
        eligible.append((asset, float(asset.metrics["v3_ranking_score"])))
    eligible.sort(key=lambda item: (-item[1], item[0].metadata.code))

    manifest = final_score_v3_manifest()
    required_keys = {
        key
        for component in manifest.components.values()
        for key in component.required_inputs
    }
    required_keys.update(
        {
            "component_reliability",
            "component_source_dates",
            "distance_to_ma20",
            "quality_gate_rejected",
            "risk_flags",
            "theme_group",
            "tracked_underlying_id",
        }
    )
    input_snapshot = {
        "trade_date": trade_date,
        "decision_cutoff": market_cutoff,
        "source_availability_cutoff": data_cutoff,
        "assets": [
            {
                "asset_code": code,
                "decision_data_eligible": code in decision_code_set,
                **_input_evidence(assets_by_code.get(code), required_keys),
            }
            for code in expected_codes
        ],
    }
    contract_document = final_score_v3_contract()
    calculation = contract_document["calculation"]
    reliability = contract_document["reliability"]
    identity = build_ranking_contract(
        score_version=str(contract_document["contract_id"]),
        rule_version=str(contract_document["rule_version"]),
        component_manifest=ranking_strategy_semantics(contract_document),
        scope={"scope_kind": "full", "asset_type": "etf", "theme": None, "codes": []},
        universe_snapshot=universe.members,
        input_snapshot=input_snapshot,
        price_basis=str(calculation["price_basis"]),
        data_cutoff=data_cutoff,
        reliability_policy=dict(reliability),
    )
    idempotency_key = canonical_hash(
        {
            "trade_date": trade_date,
            "scope_hash": identity["scope_hash"],
            "input_snapshot_hash": identity["input_snapshot_hash"],
            "ranking_contract_hash": identity["ranking_contract_hash"],
        }
    )
    expected_count = len(expected_codes)
    decision_count = len(barrier.included_codes)
    eligible_count = len(eligible)
    decision_ratio = decision_count / expected_count if expected_count else 0.0
    score_ratio = eligible_count / expected_count if expected_count else 0.0
    existing = await session.scalar(
        select(ShortResearchSignalRun).where(ShortResearchSignalRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        expected_identity = {
            "status": "success",
            "scope_kind": "full",
            "scope_hash": str(identity["scope_hash"]),
            "universe_snapshot_hash": str(identity["universe_snapshot_hash"]),
            "input_snapshot_hash": str(identity["input_snapshot_hash"]),
            "score_version": str(contract_document["contract_id"]),
            "rule_version": str(contract_document["rule_version"]),
            "ranking_contract_hash": str(identity["ranking_contract_hash"]),
            "score_field": "ranking_score",
            "data_cutoff": data_cutoff,
            "as_of_trade_date": trade_date,
            "price_basis": str(calculation["price_basis"]),
            "expected_item_count": expected_count,
            "decision_data_item_count": decision_count,
            "decision_data_coverage_ratio": round(decision_ratio, 6),
            "eligible_item_count": eligible_count,
            "coverage_ratio": round(score_ratio, 6),
        }
        if any(getattr(existing, field) != value for field, value in expected_identity.items()):
            raise SnapshotMaterializationError("idempotent snapshot identity does not match")
        existing_items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == existing.id)
                .order_by(ShortResearchSignalItem.global_rank.asc())
            )
        ).all()
        expected_items = [
            (asset.metadata.code, rank, score)
            for rank, (asset, score) in enumerate(eligible, start=1)
        ]
        persisted_items = [
            (item.asset_code, item.global_rank, item.ranking_score)
            for item in existing_items
            if item.score_eligible is True
            and item.ranking_score is not None
            and math.isfinite(item.ranking_score)
        ]
        coverage = (existing.summary_json or {}).get("coverage")
        decision_data = coverage.get("decision_data") if isinstance(coverage, Mapping) else None
        score = coverage.get("score") if isinstance(coverage, Mapping) else None
        summary_is_complete = (
            (existing.summary_json or {}).get("item_count") == eligible_count
            and isinstance(decision_data, Mapping)
            and decision_data.get("expected_codes") == expected_codes
            and decision_data.get("included_codes") == barrier.included_codes
            and isinstance(score, Mapping)
            and score.get("eligible_codes") == [asset.metadata.code for asset, _score in eligible]
            and (existing.summary_json or {}).get("draft_seal")
            == build_snapshot_draft_seal(existing, existing_items)
        )
        if (
            len(persisted_items) != len(existing_items)
            or persisted_items != expected_items
            or not summary_is_complete
        ):
            raise SnapshotMaterializationError("idempotent snapshot is incomplete")
        return existing

    now = utcnow()
    run = ShortResearchSignalRun(
        status="success",
        started_at=now,
        finished_at=now,
        as_of_date=trade_date,
        config_json={
            "asset_type": "etf",
            "theme": None,
            "codes": [],
            "generation_mode": "final_score_v3",
            "language": "research_only",
            "market_decision_cutoff": market_cutoff.isoformat(),
            "source_availability_cutoff": data_cutoff.isoformat(),
        },
        summary_json={
            "item_count": eligible_count,
            "fund_count": 0,
            "etf_count": eligible_count,
            "research_only": True,
            "score_version": str(contract_document["contract_id"]),
            "rule_version": str(contract_document["rule_version"]),
            "decision_cutoff": market_cutoff.isoformat(),
            "source_availability_cutoff": data_cutoff.isoformat(),
            "coverage": {
                "decision_data": barrier.to_dict(),
                "score": {
                    "expected_count": expected_count,
                    "eligible_count": eligible_count,
                    "eligible_codes": [asset.metadata.code for asset, _score in eligible],
                    "coverage_ratio": round(score_ratio, 6),
                    "excluded": score_excluded,
                },
            },
            "input_snapshot": _json_safe(input_snapshot),
            "non_finite_reject_count": sum(
                "non_finite_v3_ranking_score" in item["reasons"] for item in score_excluded
            ),
        },
        scope_kind="full",
        scope_hash=str(identity["scope_hash"]),
        universe_snapshot_hash=str(identity["universe_snapshot_hash"]),
        input_snapshot_hash=str(identity["input_snapshot_hash"]),
        score_version=str(contract_document["contract_id"]),
        rule_version=str(contract_document["rule_version"]),
        ranking_contract_hash=str(identity["ranking_contract_hash"]),
        score_field="ranking_score",
        data_cutoff=data_cutoff,
        as_of_trade_date=trade_date,
        price_basis=str(calculation["price_basis"]),
        expected_item_count=expected_count,
        decision_data_item_count=decision_count,
        decision_data_coverage_ratio=round(decision_ratio, 6),
        eligible_item_count=eligible_count,
        coverage_ratio=round(score_ratio, 6),
        publication_state="unpublished",
        idempotency_key=idempotency_key,
    )
    session.add(run)
    await session.flush()

    for rank, (asset, score) in enumerate(eligible, start=1):
        shadow_breakdown = asset.score_breakdown.get("final_score_v3_shadow")
        canonical_breakdown = dict(shadow_breakdown) if isinstance(shadow_breakdown, Mapping) else {}
        score_breakdown = {
            key: value
            for key, value in asset.score_breakdown.items()
            if key != "final_score_v3_shadow"
        }
        score_breakdown["final_score_v3"] = {
            **canonical_breakdown,
            "score_version": str(contract_document["contract_id"]),
            "rule_version": str(contract_document["rule_version"]),
            "ranking_contract_hash": str(identity["ranking_contract_hash"]),
            "ranking_score": score,
            "score_eligible": True,
        }
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type="etf",
                asset_code=asset.metadata.code,
                rank=rank,
                global_rank=rank,
                total_score=score,
                ranking_score=score,
                score_eligible=True,
                conclusion=str(asset.metrics.get("v3_observation_label") or asset.conclusion),
                score_breakdown_json=_json_safe(score_breakdown),
                risk_flags_json=list(asset.risk_flags),
                rationale_json=_json_safe(
                    {
                        **asset.rationale,
                        "v3_observation_explanation": asset.metrics.get("v3_observation_explanation"),
                    }
                ),
                metrics_json=_json_safe(
                    {
                        **asset.metrics,
                        "score_version": str(contract_document["contract_id"]),
                        "ranking_score": score,
                        "score_eligible": True,
                        "ranking_contract_hash": str(identity["ranking_contract_hash"]),
                        "latest_date": asset.latest_date,
                        "latest_value": asset.latest_value,
                        "usable_days": asset.usable_days,
                        "sample_level": asset.sample_level,
                        "source_note": asset.source_note,
                    }
                ),
            )
        )
        if rank % 20 == 0:
            await session.flush()
    await session.flush()
    sealed_items = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(ShortResearchSignalItem.run_id == run.id)
            .order_by(ShortResearchSignalItem.global_rank.asc(), ShortResearchSignalItem.asset_code.asc())
        )
    ).all()
    run.summary_json = {
        **run.summary_json,
        "draft_seal": build_snapshot_draft_seal(run, sealed_items),
    }
    await session.flush()
    return run
