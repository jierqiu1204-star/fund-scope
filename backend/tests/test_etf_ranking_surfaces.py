from __future__ import annotations

from dataclasses import replace

from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.final_score_v3 import FinalScoreV3Result
from app.services.short_research.ranking_surfaces import (
    MANDATORY_ACTIONABLE_FIELDS,
    ActionableFieldEvidence,
    ProductFieldPolicy,
    actionable_product_field_policy,
    actionable_rank_manifest,
    evaluate_actionable_rank,
    history_confidence_tier,
)


def _final_score(**overrides: object) -> FinalScoreV3Result:
    values: dict[str, object] = {
        "asset_bucket": "broad-equity",
        "ranking_score": 77.125,
        "score_eligible": True,
        "component_scores": {"technical": 75.0, "structure": 80.0},
        "missing_by_component": {},
        "metric_peer_counts": {"return_20d": 20},
        "limitation_reasons": (),
    }
    values.update(overrides)
    return FinalScoreV3Result(**values)  # type: ignore[arg-type]


def _available_fields() -> dict[str, ActionableFieldEvidence]:
    return {
        field: ActionableFieldEvidence(
            status="available",
            source_time="2026-07-17T14:50:00+08:00",
        )
        for field in MANDATORY_ACTIONABLE_FIELDS
    }


def test_research_and_actionable_contract_identities_are_distinct_and_frozen() -> None:
    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    payload = actionable.canonical_payload()

    assert research.contract_id == "daily_reconstructable_v1"
    assert research.score_field == "research_score"
    assert actionable.contract_id == "actionable_rank_v1"
    assert actionable.score_field == "actionable_score"
    assert actionable.eligibility_policy_version == "actionable_eligibility_policy_v1"
    assert actionable.manifest_hash != research.manifest_hash
    assert payload["score_source"]["contract_id"] == "final_score_v3"
    assert payload["score_source"]["score_field"] == "ranking_score"
    assert payload["score_source"]["transform"] == "identity_no_recalculation"
    assert "future_outcome" in payload["forbidden_input_domains"]
    assert "intraday" in research.canonical_payload()["forbidden_input_domains"]
    assert actionable.matches_identity(
        contract_id="actionable_rank_v1",
        score_field="actionable_score",
        manifest_hash=actionable.manifest_hash,
    )
    assert not actionable.matches_identity(
        contract_id=research.contract_id,
        score_field=research.score_field,
        manifest_hash=research.manifest_hash,
    )


def test_actionable_wrapper_copies_final_score_without_changing_v3_result() -> None:
    final_score = _final_score()
    before = replace(final_score)

    result = evaluate_actionable_rank(
        final_score,
        eligible_sessions=120,
        field_evidence=_available_fields(),
    )

    assert result.actionable_eligible is True
    assert result.actionable_score == 77.125
    assert result.exclusion_reasons == ()
    assert final_score == before
    assert result.referenced_score_contract_id == "final_score_v3"


def test_actionable_wrapper_fails_closed_for_history_score_cap_and_non_finite() -> None:
    result = evaluate_actionable_rank(
        _final_score(
            ranking_score=float("nan"),
            component_scores={"technical": float("inf")},
        ),
        eligible_sessions=119,
        field_evidence=_available_fields(),
        cap_violation=True,
        non_finite_reject=True,
    )

    assert result.actionable_eligible is False
    assert result.actionable_score is None
    assert "history:insufficient_120_eligible_adjusted_sessions" in result.exclusion_reasons
    assert "final_score_v3:unavailable_or_non_finite" in result.exclusion_reasons
    assert "final_score_v3.technical:non_finite" in result.exclusion_reasons
    assert "final_score_v3:cap_violation" in result.exclusion_reasons
    assert "final_score_v3:non_finite_reject" in result.exclusion_reasons


def test_history_confidence_tiers_use_point_in_time_eligible_session_count() -> None:
    assert history_confidence_tier(60) is None
    assert history_confidence_tier(61) == "provisional_short_history"
    assert history_confidence_tier(119) == "provisional_short_history"
    assert history_confidence_tier(120) == "standard_history"
    assert history_confidence_tier(249) == "standard_history"
    assert history_confidence_tier(250) == "full_history_context"


def test_product_field_policy_distinguishes_missing_stale_and_not_applicable() -> None:
    fields = _available_fields()
    fields["bid"] = ActionableFieldEvidence(status="missing")
    fields["ask"] = ActionableFieldEvidence(status="stale")
    fields["iopv"] = ActionableFieldEvidence(status="not_applicable")

    result = evaluate_actionable_rank(
        _final_score(),
        eligible_sessions=250,
        field_evidence=fields,
    )

    assert result.actionable_eligible is False
    assert "bid:missing" in result.exclusion_reasons
    assert "ask:stale" in result.exclusion_reasons
    assert "iopv:not_applicable_not_permitted" in result.exclusion_reasons
    assert result.product_field_policy_id == actionable_product_field_policy().policy_id


def test_not_applicable_requires_an_explicit_versioned_product_declaration() -> None:
    policy = ProductFieldPolicy(
        policy_id="declared_cash_etf_fields_v1",
        mandatory_fields=MANDATORY_ACTIONABLE_FIELDS,
        not_applicable_fields_by_product_type={"cash_etf": ("iopv",)},
    )
    fields = _available_fields()
    fields["iopv"] = ActionableFieldEvidence(status="not_applicable")

    accepted = evaluate_actionable_rank(
        _final_score(),
        eligible_sessions=250,
        field_evidence=fields,
        product_type="cash_etf",
        product_policy=policy,
    )
    rejected = evaluate_actionable_rank(
        _final_score(),
        eligible_sessions=250,
        field_evidence=fields,
        product_type="etf",
        product_policy=policy,
    )

    assert accepted.actionable_eligible is True
    assert accepted.field_statuses["iopv"] == "not_applicable"
    assert accepted.product_field_policy_hash == policy.policy_hash
    assert rejected.actionable_eligible is False
    assert "iopv:not_applicable_not_permitted" in rejected.exclusion_reasons
