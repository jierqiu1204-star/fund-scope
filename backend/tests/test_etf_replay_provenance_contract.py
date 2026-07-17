from __future__ import annotations

from datetime import datetime

import pytest

from app.services import etf_research_evidence as evidence


@pytest.mark.parametrize(
    (
        "ranking_source",
        "signal_compatibility",
        "action_compatibility",
        "policy_mode",
        "notification_provenance",
        "execution_provenance",
    ),
    [
        (
            "research_replay",
            "same_replay_contract",
            "same_contract",
            "policy_shadow",
            "shadow_eligible",
            "simulated_execution",
        ),
        (
            "production_published",
            "same_production_contract",
            "same_contract",
            "production_live",
            "smtp_accepted_live",
            "none",
        ),
        (
            "research_replay",
            "mismatch",
            "legacy",
            "policy_shadow",
            "not_attempted",
            "none",
        ),
    ],
)
def test_replay_provenance_dimensions_are_typed_and_orthogonal(
    ranking_source: str,
    signal_compatibility: str,
    action_compatibility: str,
    policy_mode: str,
    notification_provenance: str,
    execution_provenance: str,
) -> None:
    contract = evidence.ReplayEvidenceProvenanceContract(
        ranking_source_kind=evidence.RankingSourceKind(ranking_source),
        signal_contract_compatibility=evidence.SignalContractCompatibility(
            signal_compatibility
        ),
        action_policy_contract_compatibility=evidence.ActionPolicyContractCompatibility(
            action_compatibility
        ),
        policy_mode=evidence.PolicyMode(policy_mode),
        notification_provenance=evidence.NotificationProvenance(
            notification_provenance
        ),
        execution_provenance=evidence.ReplayExecutionProvenance(execution_provenance),
    )

    payload = contract.to_dict()

    assert payload["ranking_source_kind"] == ranking_source
    assert payload["signal_contract_compatibility"] == signal_compatibility
    assert payload["action_policy_contract_compatibility"] == action_compatibility
    assert payload["policy_mode"] == policy_mode
    assert payload["notification_provenance"] == notification_provenance
    assert payload["execution_provenance"] == execution_provenance
    assert payload["contract_hash"] == evidence.stable_contract_hash(
        {key: value for key, value in payload.items() if key != "contract_hash"}
    )


def test_replay_provenance_serialization_is_stable() -> None:
    first = evidence.ReplayEvidenceProvenanceContract(
        ranking_source_kind=evidence.RankingSourceKind.RESEARCH_REPLAY,
        signal_contract_compatibility=(
            evidence.SignalContractCompatibility.SAME_REPLAY_CONTRACT
        ),
        action_policy_contract_compatibility=(
            evidence.ActionPolicyContractCompatibility.SAME_CONTRACT
        ),
        policy_mode=evidence.PolicyMode.POLICY_SHADOW,
        notification_provenance=evidence.NotificationProvenance.SHADOW_ELIGIBLE,
        execution_provenance=(
            evidence.ReplayExecutionProvenance.SIMULATED_EXECUTION
        ),
    )
    second = evidence.ReplayEvidenceProvenanceContract(
        execution_provenance=(
            evidence.ReplayExecutionProvenance.SIMULATED_EXECUTION
        ),
        notification_provenance=evidence.NotificationProvenance.SHADOW_ELIGIBLE,
        policy_mode=evidence.PolicyMode.POLICY_SHADOW,
        action_policy_contract_compatibility=(
            evidence.ActionPolicyContractCompatibility.SAME_CONTRACT
        ),
        signal_contract_compatibility=(
            evidence.SignalContractCompatibility.SAME_REPLAY_CONTRACT
        ),
        ranking_source_kind=evidence.RankingSourceKind.RESEARCH_REPLAY,
    )

    assert first.canonical_json() == second.canonical_json()
    assert first.to_dict() == second.to_dict()


@pytest.mark.parametrize(
    ("receipt_id", "receipt_timestamp"),
    [
        (None, None),
        ("provider-receipt-1", None),
        (None, datetime(2026, 7, 15, 9, 1)),
        ("   ", datetime(2026, 7, 15, 9, 1)),
    ],
)
def test_provider_delivered_live_requires_receipt_id_and_timestamp(
    receipt_id: str | None,
    receipt_timestamp: datetime | None,
) -> None:
    with pytest.raises(ValueError, match="provider receipt"):
        evidence.ReplayEvidenceProvenanceContract(
            ranking_source_kind=evidence.RankingSourceKind.PRODUCTION_PUBLISHED,
            signal_contract_compatibility=(
                evidence.SignalContractCompatibility.SAME_PRODUCTION_CONTRACT
            ),
            action_policy_contract_compatibility=(
                evidence.ActionPolicyContractCompatibility.SAME_CONTRACT
            ),
            policy_mode=evidence.PolicyMode.PRODUCTION_LIVE,
            notification_provenance=(
                evidence.NotificationProvenance.PROVIDER_DELIVERED_LIVE
            ),
            execution_provenance=evidence.ReplayExecutionProvenance.NONE,
            provider_receipt_id=receipt_id,
            provider_receipt_timestamp=receipt_timestamp,
        )


def test_provider_delivered_live_serializes_receipt_facts() -> None:
    receipt_timestamp = datetime(2026, 7, 15, 9, 1, 2)
    payload = evidence.ReplayEvidenceProvenanceContract(
        ranking_source_kind=evidence.RankingSourceKind.PRODUCTION_PUBLISHED,
        signal_contract_compatibility=(
            evidence.SignalContractCompatibility.SAME_PRODUCTION_CONTRACT
        ),
        action_policy_contract_compatibility=(
            evidence.ActionPolicyContractCompatibility.SAME_CONTRACT
        ),
        policy_mode=evidence.PolicyMode.PRODUCTION_LIVE,
        notification_provenance=(
            evidence.NotificationProvenance.PROVIDER_DELIVERED_LIVE
        ),
        execution_provenance=evidence.ReplayExecutionProvenance.NONE,
        provider_receipt_id="provider-receipt-1",
        provider_receipt_timestamp=receipt_timestamp,
    ).to_dict()

    assert payload["provider_receipt_id"] == "provider-receipt-1"
    assert payload["provider_receipt_timestamp"] == receipt_timestamp.isoformat()


def test_replay_provenance_rejects_untyped_dimension_values() -> None:
    with pytest.raises(ValueError, match="registered provenance enum"):
        evidence.ReplayEvidenceProvenanceContract(  # type: ignore[arg-type]
            ranking_source_kind="research_replay",
            signal_contract_compatibility=(
                evidence.SignalContractCompatibility.SAME_REPLAY_CONTRACT
            ),
            action_policy_contract_compatibility=(
                evidence.ActionPolicyContractCompatibility.SAME_CONTRACT
            ),
            policy_mode=evidence.PolicyMode.POLICY_SHADOW,
            notification_provenance=evidence.NotificationProvenance.SHADOW_ELIGIBLE,
            execution_provenance=(
                evidence.ReplayExecutionProvenance.SIMULATED_EXECUTION
            ),
        )


@pytest.mark.parametrize(
    ("ranking_source", "signal_compatibility"),
    [
        ("research_replay", "same_production_contract"),
        ("production_published", "same_replay_contract"),
    ],
)
def test_replay_provenance_rejects_cross_source_contract_claims(
    ranking_source: str,
    signal_compatibility: str,
) -> None:
    with pytest.raises(ValueError, match="ranking source.*signal contract"):
        evidence.ReplayEvidenceProvenanceContract(
            ranking_source_kind=evidence.RankingSourceKind(ranking_source),
            signal_contract_compatibility=evidence.SignalContractCompatibility(
                signal_compatibility
            ),
            action_policy_contract_compatibility=(
                evidence.ActionPolicyContractCompatibility.SAME_CONTRACT
            ),
            policy_mode=evidence.PolicyMode.POLICY_SHADOW,
            notification_provenance=evidence.NotificationProvenance.NOT_ATTEMPTED,
            execution_provenance=evidence.ReplayExecutionProvenance.NONE,
        )


def test_shared_future_prices_cannot_merge_ranking_source_evidence() -> None:
    shared_future_price_hash = "future-adjusted-prices-shared-by-both-sources"
    production_evidence = {
        "ranking_source_kind": "production_published",
        "future_price_input_hash": shared_future_price_hash,
        "sample_count": 12,
        "coverage": 0.75,
        "confidence_interval": [-0.02, 0.04],
        "evidence_status": "样本不足",
    }
    replay_evidence = {
        "ranking_source_kind": "research_replay",
        "future_price_input_hash": shared_future_price_hash,
        "sample_count": 30,
        "coverage": 0.9,
        "confidence_interval": [0.01, 0.05],
        "evidence_status": "同源已验证",
    }

    with pytest.raises(ValueError, match="ranking sources cannot be merged"):
        evidence.require_single_validation_ranking_source(
            [production_evidence, replay_evidence]
        )

    assert production_evidence["sample_count"] == 12
    assert production_evidence["coverage"] == 0.75
    assert production_evidence["confidence_interval"] == [-0.02, 0.04]
    assert production_evidence["evidence_status"] == "样本不足"
    assert replay_evidence["sample_count"] == 30
    assert replay_evidence["coverage"] == 0.9
    assert replay_evidence["confidence_interval"] == [0.01, 0.05]
    assert replay_evidence["evidence_status"] == "同源已验证"


def test_validation_evidence_from_one_ranking_source_is_merge_compatible() -> None:
    source = evidence.require_single_validation_ranking_source(
        [
            {"ranking_source_kind": "research_replay", "sample_count": 10},
            {"ranking_source_kind": "research_replay", "sample_count": 20},
        ]
    )

    assert source is evidence.RankingSourceKind.RESEARCH_REPLAY
