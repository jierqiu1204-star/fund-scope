from __future__ import annotations

from datetime import date, datetime

from app.api.routes.short_research import _asset_out
from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.schemas.short_research import EtfRankingSnapshotMetadataOut
from app.services.short_research.ranking_quality import (
    canonical_research_eligibility_policy,
)
from app.services.short_research.service import ComputedAsset


def test_etf_asset_api_exposes_non_aliasing_quality_and_identity_fields() -> None:
    asset = ComputedAsset(
        metadata=ShortResearchAsset(
            asset_type=ASSET_TYPE_ETF,
            code="510300",
            name="沪深300ETF",
            category="broad_index",
            theme_tags=("宽基",),
            investment_direction="fixture",
            trading_rule_label="T+1",
            exchange="SH",
        ),
        rank=1,
        total_score=88.0,
        conclusion="观察",
        latest_date=date(2026, 8, 1),
        latest_value=4.0,
        usable_days=250,
        sample_level="完整",
        metrics={
            "ranking_surface": "research",
            "research_rank": 1,
            "research_score": 88.0,
            "research_score_eligible": True,
            "research_quality_eligible": False,
            "research_quality_reasons": ["absolute_tradability_below_threshold"],
            "research_contract_hash": "a" * 64,
            "actionable_rank": 2,
            "actionable_score": 86.0,
            "actionable_eligible": True,
            "actionable_contract_hash": "b" * 64,
            "canonical_research_rank": 1,
            "observation_only": True,
            "history_confidence_tier": "established_120_plus",
            "default_display_eligible": True,
            "average_turnover_20d": 800_000_000.0,
            "ranking_asset_bucket": "broad-equity",
            "tracked_underlying_id": "CSI-000300",
            "tracked_underlying_coverage": 0.95,
            "underlying_evidence": {
                "source": "authoritative_index_registry",
                "observed_at": "2026-08-01T09:30:00",
                "rule_version": "identity-rules-v1",
                "evidence_hash": "c" * 64,
            },
            "clone_group_id": "CSI-000300",
            "clone_policy_active": True,
            "diversified_representative": True,
            "diversified_presentation_position": 1,
            "peer_diagnostics": {
                "asset_bucket": "broad-equity",
                "metric_peer_counts": {"return_20d": 100},
            },
            "theme_profile": {
                "theme_group": "broad_base",
                "primary_theme": "宽基",
                "secondary_themes": [],
                "classification_source": "authoritative",
                "classification_confidence": "high",
                "classification_reason": "fixture",
            },
        },
        score_breakdown={},
        risk_flags=[],
        rationale={},
        source_note="fixture",
        entry_timing_label="观察",
        entry_timing_reason="fixture",
    )

    payload = _asset_out(asset)

    assert payload.canonical_research_rank == 1
    assert payload.actionable_rank == 2
    assert payload.observation_only is True
    assert payload.research_quality_eligible is False
    assert payload.research_quality_reasons == ["absolute_tradability_below_threshold"]
    assert payload.tradability_eligible is True
    assert payload.taxonomy_bucket == "broad-equity"
    assert payload.tracked_underlying_id == "CSI-000300"
    assert payload.underlying_evidence["rule_version"] == "identity-rules-v1"
    assert payload.clone_group_id == "CSI-000300"
    assert payload.diversified_presentation_position == 1
    assert payload.peer_diagnostics["metric_peer_counts"] == {"return_20d": 100}


def test_snapshot_api_keeps_provider_publication_pit_cost_and_concentration_separate() -> None:
    payload = EtfRankingSnapshotMetadataOut(
        ranking_surface="research",
        snapshot_state="complete",
        market_decision_cutoff=datetime(2026, 8, 1, 15, 0),
        provider_health_identity={"seal_hash": "a" * 64},
        quality_evidence={"canonical_count": 100},
        publication_evidence={"publication_identity": "b" * 64},
        pit_evidence={"factual_sessions": 3},
        cost_evidence={"policy": "factual_or_conservative_v1"},
        concentration_evidence={"clone_group_count": 80},
    )

    assert payload.provider_health_identity != payload.publication_evidence
    assert payload.pit_evidence["factual_sessions"] == 3
    assert payload.cost_evidence["policy"] == "factual_or_conservative_v1"
    assert payload.concentration_evidence["clone_group_count"] == 80


def test_canonical_quality_contract_versions_observation_only_reasons() -> None:
    contract = canonical_research_eligibility_policy()

    assert contract["policy_version"] == "etf_canonical_research_eligibility_v2"
    assert contract["observation_only_state_contract_version"] == "etf_observation_only_state_v2"
    assert contract["stable_exclusion_reason_semantics"] == ("etf_quality_exclusion_reasons_v2")
    assert contract["eligibility_layers"]["research"] == ("same_as_score_eligibility")
