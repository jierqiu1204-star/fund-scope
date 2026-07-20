from __future__ import annotations

from datetime import date

from app.services.etf_research_evidence import (
    EVIDENCE_STATUS_SAME_CONTRACT,
    EVIDENCE_STATUS_VERSION_MISMATCH,
    build_replay_contract,
    classify_evidence_status,
)
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import (
    actionable_rank_manifest,
    validate_rank_derived_action_context,
)


def test_research_evidence_cannot_validate_actionable_rank() -> None:
    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    current = {
        "ranking_surface": "actionable",
        "contract_hash": actionable.manifest_hash,
    }

    assert (
        classify_evidence_status(
            current,
            {
                "ranking_surface": "research",
                "contract_hash": research.manifest_hash,
                "sample_count": 100,
            },
        )
        == EVIDENCE_STATUS_VERSION_MISMATCH
    )
    assert (
        classify_evidence_status(
            current,
            {
                "ranking_surface": "actionable",
                "contract_hash": actionable.manifest_hash,
                "sample_count": 100,
            },
        )
        == EVIDENCE_STATUS_SAME_CONTRACT
    )


def test_replay_contract_keeps_warmup_range_separate_from_replay_range() -> None:
    contract = build_replay_contract(
        replay_run_id=7,
        signal_rule_version="dual_ranking_surfaces_v1",
        allocation_version="allocation_v1",
        action_lifecycle_version="action_v1",
        target_semantics="observation",
        action_event_source="actionable_rank_v1",
        execution_model="daily_close_v1",
        fee_model="simple_fee_rate_v1",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        warmup_start_date=date(2026, 1, 2),
        warmup_end_date=date(2026, 3, 31),
        ranking_surface="actionable",
        data_cutoff=date(2026, 6, 30),
    )

    assert contract["date_range"] == {
        "start_date": "2026-04-01",
        "end_date": "2026-06-30",
    }
    assert contract["warmup_range"] == {
        "start_date": "2026-01-02",
        "end_date": "2026-03-31",
    }
    assert contract["ranking_surface"] == "actionable"


def test_rank_derived_candidate_persists_exact_fail_closed_reasons() -> None:
    decision = validate_rank_derived_action_context(
        {
            "actionable_contract_id": "daily_reconstructable_v1",
            "actionable_contract_hash": daily_reconstructable_manifest().manifest_hash,
            "actionable_as_of_date": "2026-07-16",
            "actionable_eligible": False,
            "actionable_rank": None,
            "actionable_score": None,
        },
        required_as_of_date=date(2026, 7, 17),
    )

    assert decision.allowed is False
    assert decision.suppression_reasons == (
        "actionable_as_of_date_mismatch",
        "actionable_contract_hash_mismatch",
        "actionable_contract_id_mismatch",
        "actionable_rank_ineligible",
        "actionable_rank_missing",
        "actionable_score_missing_or_non_finite",
    )
