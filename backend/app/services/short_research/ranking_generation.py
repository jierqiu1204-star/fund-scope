from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.final_score_v3 import FinalScoreV3Result
from app.services.short_research.ranking_surfaces import (
    ActionableFieldEvidence,
    ActionableRankResult,
    evaluate_actionable_rank,
    history_confidence_tier,
)


@dataclass(frozen=True)
class RankingSurfaceCandidate:
    asset_code: str
    eligible_sessions: int
    research_score: float | None
    research_contract_id: str
    research_score_field: str
    research_manifest_hash: str
    final_score_v3: FinalScoreV3Result | None
    actionable_fields: Mapping[str, ActionableFieldEvidence]
    research_as_of_date: date
    actionable_as_of_date: date | None
    product_type: str = "etf"
    cap_violation: bool = False
    non_finite_reject: bool = False


@dataclass(frozen=True)
class RankingSurfaceRow:
    asset_code: str
    rank: int
    score: float
    contract_id: str
    score_field: str
    contract_hash: str
    as_of_date: date
    history_tier: str
    actionable_evidence: ActionableRankResult | None = None


@dataclass(frozen=True)
class DualRankingSurfaces:
    research_rows: tuple[RankingSurfaceRow, ...]
    actionable_rows: tuple[RankingSurfaceRow, ...]
    research_exclusions: Mapping[str, tuple[str, ...]]
    actionable_exclusions: Mapping[str, tuple[str, ...]]
    rejection_summary: Mapping[str, Mapping[str, int]]


def _research_exclusion_reasons(candidate: RankingSurfaceCandidate) -> tuple[str, ...]:
    manifest = daily_reconstructable_manifest()
    reasons: list[str] = []
    if candidate.eligible_sessions < manifest.required_bar_count:
        reasons.append("history:insufficient_61_eligible_adjusted_sessions")
    if not manifest.matches_identity(
        contract_id=candidate.research_contract_id,
        score_field=candidate.research_score_field,
        manifest_hash=candidate.research_manifest_hash,
    ):
        reasons.append("research_contract:identity_mismatch")
    score = candidate.research_score
    if (
        score is None
        or isinstance(score, bool)
        or not isinstance(score, int | float)
        or not math.isfinite(float(score))
    ):
        reasons.append("research_score:unavailable_or_non_finite")
    return tuple(sorted(set(reasons)))


def _rank_rows(rows: list[RankingSurfaceRow]) -> tuple[RankingSurfaceRow, ...]:
    ordered = sorted(rows, key=lambda row: (-row.score, row.asset_code))
    return tuple(
        RankingSurfaceRow(
            asset_code=row.asset_code,
            rank=rank,
            score=row.score,
            contract_id=row.contract_id,
            score_field=row.score_field,
            contract_hash=row.contract_hash,
            as_of_date=row.as_of_date,
            history_tier=row.history_tier,
            actionable_evidence=row.actionable_evidence,
        )
        for rank, row in enumerate(ordered, start=1)
    )


def _summary(exclusions: Mapping[str, tuple[str, ...]]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(reason for reasons in exclusions.values() for reason in reasons).items()
        )
    )


def generate_dual_ranking_surfaces(
    candidates: Sequence[RankingSurfaceCandidate],
) -> DualRankingSurfaces:
    research_manifest = daily_reconstructable_manifest()
    research_rows: list[RankingSurfaceRow] = []
    actionable_rows: list[RankingSurfaceRow] = []
    research_exclusions: dict[str, tuple[str, ...]] = {}
    actionable_exclusions: dict[str, tuple[str, ...]] = {}

    for candidate in sorted(candidates, key=lambda item: item.asset_code):
        research_reasons = _research_exclusion_reasons(candidate)
        if research_reasons:
            research_exclusions[candidate.asset_code] = research_reasons
        else:
            assert candidate.research_score is not None
            tier = history_confidence_tier(candidate.eligible_sessions)
            assert tier is not None
            research_rows.append(
                RankingSurfaceRow(
                    asset_code=candidate.asset_code,
                    rank=0,
                    score=float(candidate.research_score),
                    contract_id=research_manifest.contract_id,
                    score_field=research_manifest.score_field,
                    contract_hash=research_manifest.manifest_hash,
                    as_of_date=candidate.research_as_of_date,
                    history_tier=tier,
                )
            )

        if candidate.final_score_v3 is None:
            actionable_exclusions[candidate.asset_code] = ("final_score_v3:missing",)
            continue
        actionable = evaluate_actionable_rank(
            candidate.final_score_v3,
            eligible_sessions=candidate.eligible_sessions,
            field_evidence=candidate.actionable_fields,
            product_type=candidate.product_type,
            cap_violation=candidate.cap_violation,
            non_finite_reject=candidate.non_finite_reject,
        )
        action_reasons = list(actionable.exclusion_reasons)
        if research_reasons:
            action_reasons.append("research_surface:unavailable")
        if candidate.actionable_as_of_date != candidate.research_as_of_date:
            action_reasons.append("as_of_context:mismatch")
        if action_reasons:
            actionable_exclusions[candidate.asset_code] = tuple(sorted(set(action_reasons)))
            continue
        assert actionable.actionable_score is not None
        assert actionable.history_tier is not None
        actionable_rows.append(
            RankingSurfaceRow(
                asset_code=candidate.asset_code,
                rank=0,
                score=actionable.actionable_score,
                contract_id=actionable.contract_id,
                score_field=actionable.score_field,
                contract_hash=actionable.manifest_hash,
                as_of_date=candidate.research_as_of_date,
                history_tier=actionable.history_tier,
                actionable_evidence=actionable,
            )
        )

    return DualRankingSurfaces(
        research_rows=_rank_rows(research_rows),
        actionable_rows=_rank_rows(actionable_rows),
        research_exclusions=research_exclusions,
        actionable_exclusions=actionable_exclusions,
        rejection_summary={
            "research": _summary(research_exclusions),
            "actionable": _summary(actionable_exclusions),
        },
    )
