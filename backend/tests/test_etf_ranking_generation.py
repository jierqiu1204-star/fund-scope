from __future__ import annotations

from dataclasses import replace
from datetime import date
from time import perf_counter

from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.final_score_v3 import FinalScoreV3Result
from app.services.short_research.ranking_contract import canonical_hash
from app.services.short_research.ranking_generation import (
    RankingSurfaceCandidate,
    generate_dual_ranking_surfaces,
)
from app.services.short_research.ranking_surfaces import (
    MANDATORY_ACTIONABLE_FIELDS,
    ActionableFieldEvidence,
)

AS_OF = date(2026, 7, 17)


def _final_score(score: float = 80.0) -> FinalScoreV3Result:
    return FinalScoreV3Result(
        asset_bucket="broad-equity",
        ranking_score=score,
        score_eligible=True,
        component_scores={"technical": score},
        missing_by_component={},
        metric_peer_counts={"return_20d": 20},
        limitation_reasons=(),
    )


def _fields(
    *,
    status: str = "available",
) -> dict[str, ActionableFieldEvidence]:
    return {
        field: ActionableFieldEvidence(status=status)  # type: ignore[arg-type]
        for field in MANDATORY_ACTIONABLE_FIELDS
    }


def _candidate(
    code: str,
    *,
    sessions: int = 120,
    research_score: float | None = 70.0,
    final_score: FinalScoreV3Result | None = None,
    fields: dict[str, ActionableFieldEvidence] | None = None,
) -> RankingSurfaceCandidate:
    manifest = daily_reconstructable_manifest()
    return RankingSurfaceCandidate(
        asset_code=code,
        eligible_sessions=sessions,
        research_score=research_score,
        research_contract_id=manifest.contract_id,
        research_score_field=manifest.score_field,
        research_manifest_hash=manifest.manifest_hash,
        final_score_v3=final_score or _final_score(),
        actionable_fields=fields or _fields(),
        research_as_of_date=AS_OF,
        actionable_as_of_date=AS_OF,
    )


def test_61_sessions_produce_research_row_and_fewer_fail_closed() -> None:
    surfaces = generate_dual_ranking_surfaces(
        [
            _candidate("510061", sessions=61),
            _candidate("510060", sessions=60),
        ]
    )

    assert [(row.asset_code, row.history_tier) for row in surfaces.research_rows] == [
        ("510061", "provisional_short_history")
    ]
    assert surfaces.research_exclusions["510060"] == (
        "history:insufficient_61_eligible_adjusted_sessions",
    )
    assert "510061" in surfaces.actionable_exclusions


def test_missing_intraday_preserves_research_but_excludes_action_with_field_reasons() -> None:
    fields = _fields()
    fields["bid"] = ActionableFieldEvidence(status="missing")
    fields["iopv"] = ActionableFieldEvidence(status="stale")

    surfaces = generate_dual_ranking_surfaces([_candidate("510300", sessions=250, fields=fields)])

    assert [row.asset_code for row in surfaces.research_rows] == ["510300"]
    assert surfaces.actionable_rows == ()
    assert surfaces.actionable_exclusions["510300"] == ("bid:missing", "iopv:stale")


def test_low_turnover_and_unknown_taxonomy_remain_observation_only() -> None:
    surfaces = generate_dual_ranking_surfaces(
        [
            replace(
                _candidate("510001", research_score=99.0),
                average_turnover_20d=49_999_999.0,
                default_display_eligible=False,
            ),
            replace(
                _candidate("510002", research_score=98.0),
                taxonomy_bucket="unknown",
                taxonomy_evidence_valid=False,
            ),
            _candidate("510003", research_score=70.0),
        ]
    )

    assert [row.asset_code for row in surfaces.research_rows] == [
        "510001",
        "510002",
        "510003",
    ]
    assert surfaces.research_exclusions == {}
    assert surfaces.research_quality_reasons["510001"] == ("absolute_tradability_below_threshold",)
    assert surfaces.research_quality_reasons["510002"] == ("taxonomy_bucket_unresolved",)
    assert surfaces.research_rows[0].observation_only is True
    assert surfaces.research_rows[1].observation_only is True
    assert surfaces.research_rows[2].observation_only is False
    assert "absolute_tradability_below_threshold" in (surfaces.actionable_exclusions["510001"])
    assert "taxonomy_bucket_unresolved" in surfaces.actionable_exclusions["510002"]
    assert "research_surface:unavailable" not in (surfaces.actionable_exclusions["510001"])


def test_raw_or_ineligible_decision_data_cannot_enter_research_rank() -> None:
    surfaces = generate_dual_ranking_surfaces(
        [
            replace(_candidate("510001"), price_basis="sina_raw_close"),
            replace(_candidate("510002"), price_basis="efinance_display_only"),
            replace(_candidate("510003"), price_basis="estimated_adjusted_close"),
            replace(_candidate("510004"), decision_data_eligible=False),
            replace(_candidate("510005"), finite_adjusted_inputs=False),
        ]
    )

    assert surfaces.research_rows == ()
    assert surfaces.research_exclusions["510001"] == ("price_basis:decision_ineligible",)
    assert surfaces.research_exclusions["510002"] == ("price_basis:decision_ineligible",)
    assert surfaces.research_exclusions["510003"] == ("price_basis:decision_ineligible",)
    assert surfaces.research_exclusions["510004"] == ("decision_data:ineligible",)
    assert surfaces.research_exclusions["510005"] == ("adjusted_inputs:missing_or_non_finite",)


def test_surfaces_rank_independently_and_break_ties_by_code() -> None:
    surfaces = generate_dual_ranking_surfaces(
        [
            _candidate("510003", research_score=90.0, final_score=_final_score(70.0)),
            _candidate("510002", research_score=80.0, final_score=_final_score(90.0)),
            _candidate("510001", research_score=80.0, final_score=_final_score(90.0)),
        ]
    )

    assert [(row.asset_code, row.rank) for row in surfaces.research_rows] == [
        ("510003", 1),
        ("510001", 2),
        ("510002", 3),
    ]
    assert [(row.asset_code, row.rank) for row in surfaces.actionable_rows] == [
        ("510001", 1),
        ("510002", 2),
        ("510003", 3),
    ]


def test_non_finite_cap_as_of_and_contract_hash_fail_closed() -> None:
    bad_hash = replace(_candidate("510001"), research_manifest_hash="0" * 64)
    non_finite = _candidate("510002", research_score=float("nan"))
    cap = replace(_candidate("510003"), cap_violation=True)
    as_of_mismatch = replace(
        _candidate("510004"),
        actionable_as_of_date=date(2026, 7, 16),
    )

    surfaces = generate_dual_ranking_surfaces([bad_hash, non_finite, cap, as_of_mismatch])

    assert "research_contract:identity_mismatch" in surfaces.research_exclusions["510001"]
    assert "research_score:unavailable_or_non_finite" in surfaces.research_exclusions["510002"]
    assert "final_score_v3:cap_violation" in surfaces.actionable_exclusions["510003"]
    assert surfaces.actionable_exclusions["510004"] == ("as_of_context:mismatch",)


def test_production_shaped_shadow_is_bounded_and_deterministic() -> None:
    candidates = [
        replace(
            _candidate(
                f"51{index:04d}",
                sessions=250,
                research_score=50.0 + (index % 100) / 10,
                final_score=_final_score(40.0 + (index % 80) / 10),
            ),
            cap_violation=index % 4 == 0,
        )
        for index in range(1405)
    ]
    started = perf_counter()
    first = generate_dual_ranking_surfaces(candidates)
    latency_seconds = perf_counter() - started
    second = generate_dual_ranking_surfaces(list(reversed(candidates)))
    first_hash = canonical_hash(
        {
            "research": [
                (row.asset_code, row.rank, row.score, row.contract_hash)
                for row in first.research_rows
            ],
            "actionable": [
                (row.asset_code, row.rank, row.score, row.contract_hash)
                for row in first.actionable_rows
            ],
            "exclusions": first.actionable_exclusions,
        }
    )
    second_hash = canonical_hash(
        {
            "research": [
                (row.asset_code, row.rank, row.score, row.contract_hash)
                for row in second.research_rows
            ],
            "actionable": [
                (row.asset_code, row.rank, row.score, row.contract_hash)
                for row in second.actionable_rows
            ],
            "exclusions": second.actionable_exclusions,
        }
    )
    assert len(first.research_rows) == 1405
    assert len(first.actionable_rows) == 1053
    assert len(first.actionable_exclusions) == 352
    assert first.rejection_summary["actionable"] == {"final_score_v3:cap_violation": 352}
    assert first_hash == second_hash
    assert first_hash == "8711b8b98b300d831260b433e25e33587eaa8c6da8e824e9edd9760506b14bc2"
    assert latency_seconds < 5.0
