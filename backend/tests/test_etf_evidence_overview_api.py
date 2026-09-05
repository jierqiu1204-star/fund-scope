from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, insert, select

from app.models.entities import (
    EtfFactorExperimentEvidence,
    ShortResearchSignalRun,
)
from app.services.short_research.daily_reconstructable import (
    PRICE_BASIS,
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import DUAL_RANKING_RULE_VERSION


async def _evidence_row_count(app) -> int:
    async with app.state.db.session() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(EtfFactorExperimentEvidence)
            )
            or 0
        )


@pytest.mark.anyio
async def test_latest_etf_evidence_is_read_only_and_keeps_surfaces_separate(
    app,
    client,
) -> None:
    before = await _evidence_row_count(app)

    response = await client.get("/api/short-research/evidence/etf/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "etf_evidence_overview_v1"
    assert payload["research_only"] is True
    assert payload["production_mutation_allowed"] is False
    assert payload["surfaces"]["production_ranking"] == {
        "status": "unavailable",
        "unavailable_reason": "no_production_published_snapshot",
        "ranking_source_kind": "production_published",
        "policy_mode": "production_live",
        "data_cutoff": None,
        "manifest_hash": None,
        "ranking_contract_hash": None,
        "run_reference": {},
        "coverage": {},
        "exclusions": {"count": 0, "reason_counts": {}},
        "primary_metric": None,
        "exploratory_metrics": [],
        "costs": {},
        "notification_provenance": None,
        "execution_provenance": None,
        "provider_receipt": None,
        "limitations": [],
    }
    assert payload["surfaces"]["research_replay"]["unavailable_reason"] == (
        "research_replay_not_materialized"
    )
    assert payload["surfaces"]["policy_shadow"]["unavailable_reason"] == (
        "no_complete_policy_shadow"
    )
    assert payload["surfaces"]["provider_delivery"]["unavailable_reason"] == (
        "provider_delivery_unconfirmed"
    )
    assert payload["surfaces"]["confirmed_execution"]["execution_provenance"] == (
        "user_confirmed"
    )
    assert await _evidence_row_count(app) == before


@pytest.mark.anyio
async def test_latest_etf_evidence_exposes_only_qualified_production_snapshot(
    app,
    client,
) -> None:
    contract = daily_reconstructable_manifest()
    cutoff = datetime(2026, 7, 24, 15, 1)
    async with app.state.db.session() as session:
        await session.execute(
            insert(ShortResearchSignalRun).values(
                status="success",
                as_of_date=date(2026, 7, 24),
                finished_at=cutoff,
                as_of_trade_date=date(2026, 7, 24),
                published_at=cutoff,
                publication_state="published",
                scope_kind="full",
                scope_hash="scope-hash",
                universe_snapshot_hash="universe-hash",
                input_snapshot_hash="input-hash",
                score_version=contract.contract_id,
                score_field=contract.score_field,
                rule_version=DUAL_RANKING_RULE_VERSION,
                ranking_contract_hash=contract.manifest_hash,
                data_cutoff=cutoff,
                price_basis=PRICE_BASIS,
                expected_item_count=1_400,
                decision_data_item_count=1_360,
                decision_data_coverage_ratio=1_360 / 1_400,
                eligible_item_count=1_260,
                coverage_ratio=1_260 / 1_400,
                idempotency_key="published-dual-ranking-2026-07-24",
                summary_json={
                    "readiness_policy_version": "etf_readiness_policy_v2",
                    "readiness_state": "complete",
                },
            )
        )
        await session.commit()

    response = await client.get("/api/short-research/evidence/etf/latest")

    assert response.status_code == 200
    production = response.json()["surfaces"]["production_ranking"]
    assert production["status"] == "available"
    assert production["unavailable_reason"] is None
    assert production["ranking_source_kind"] == "production_published"
    assert production["manifest_hash"]
    assert production["coverage"]["decision_data_coverage_ratio"] >= 0.95
    assert production["coverage"]["score_coverage_ratio"] == 0.90
    assert production["coverage"]["minimum_decision_data_ratio"] == 0.95
    assert production["coverage"]["minimum_score_ratio"] == 0.90


@pytest.mark.anyio
async def test_latest_etf_evidence_does_not_present_shadow_as_live_delivery(
    app,
    client,
) -> None:
    async with app.state.db.session() as session:
        session.add(
            EtfFactorExperimentEvidence(
                manifest_hash="manifest-hash",
                ranking_contract_hash="ranking-contract-hash",
                code_version="test-code",
                evidence_hash="evidence-hash",
                promotion_state="insufficient_data",
                costs_json={
                    "fee_bps_per_side": 5.0,
                    "slippage_bps_per_side": 5.0,
                },
                report_json={
                    "schema_version": "etf_point_in_time_research_evidence_v1",
                    "ranking_source_kind": "production_published",
                    "data_cutoff": "2026-07-24T15:00:00",
                    "coverage": {
                        "eligible_point_in_time_sessions": 61,
                        "independent_primary_dates": 12,
                    },
                    "primary_metric": {
                        "label": "top10_5_session_paired_net_excess",
                        "value": 0.001,
                        "sample_count": 12,
                        "confidence_interval": [-0.002, 0.004],
                        "primary": True,
                    },
                    "exploratory_metrics": [
                        {
                            "label": "top5_3_session_paired_net_excess",
                            "value": 0.002,
                            "sample_count": 12,
                            "primary": False,
                        }
                    ],
                    "primary_diagnostics": {
                        "candidate_results": {
                            "daily_core_top10": {
                                "endpoint": {
                                    "mean_paired_net_excess": 0.0,
                                    "independent_dates": ["2026-07-01"],
                                    "bootstrap_confidence_interval": [0.0, 0.0],
                                    "coverage_ratio": 1.0,
                                    "average_turnover": 0.2,
                                    "candidate_maximum_drawdown": 0.01,
                                }
                            },
                            "daily_core_top10_hysteresis": {
                                "endpoint": {
                                    "mean_paired_net_excess": 0.001,
                                    "independent_dates": ["2026-07-01"],
                                    "bootstrap_confidence_interval": [-0.002, 0.004],
                                    "coverage_ratio": 1.0,
                                    "average_turnover": 0.1,
                                    "candidate_maximum_drawdown": 0.009,
                                }
                            },
                        }
                    },
                    "policy_shadow": {
                        "status": "insufficient_data",
                        "unavailable_reason": "insufficient_independent_dates",
                        "ranking_source_kind": "research_replay",
                        "policy_mode": "policy_shadow",
                        "notification_provenance": "shadow_eligible",
                        "execution_provenance": "simulated_execution",
                    },
                },
            )
        )
        await session.commit()

    response = await client.get("/api/short-research/evidence/etf/latest")

    assert response.status_code == 200
    surfaces = response.json()["surfaces"]
    assert surfaces["research_replay"]["status"] == "insufficient_data"
    assert surfaces["research_replay"]["ranking_source_kind"] == (
        "production_published"
    )
    assert [
        item["label"]
        for item in surfaces["research_replay"]["exploratory_metrics"]
    ] == [
        "daily_core_top10",
        "daily_core_top10_hysteresis",
        "top5_3_session_paired_net_excess",
    ]
    diagnostics = surfaces["research_replay"]["exploratory_metrics"]
    assert diagnostics[0]["diagnostic"] is True
    assert diagnostics[1]["confidence_interval"] == [-0.002, 0.004]
    assert diagnostics[2]["primary"] is False
    assert surfaces["policy_shadow"]["notification_provenance"] == "shadow_eligible"
    assert surfaces["policy_shadow"]["execution_provenance"] == "simulated_execution"
    assert surfaces["live_notification"]["status"] == "unavailable"
    assert surfaces["provider_delivery"]["status"] == "unavailable"
    assert surfaces["confirmed_execution"]["status"] == "unavailable"
