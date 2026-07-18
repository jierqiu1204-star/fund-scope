from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.core.config import Settings
from app.models.entities import (
    DatabaseInstanceIdentity,
    DatabaseInstanceIdentityImmutableError,
    EtfSignalValidationRun,
    JobRun,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    authorize_snapshot_publication,
)
from app.services.intraday_etf.jobs import _decision_quote_summary
from app.services.strategy_lab.etf_validation_manifest import (
    attach_validation_source_manifest,
    build_production_validation_source_cohort,
    validate_attached_validation_source_manifest,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash
from app.services.workflows import etf_readiness_attestation as readiness_module
from app.services.workflows.etf_readiness_attestation import (
    DatabaseIdentityProvisioningError,
    _formal_return_evidence,
    _intraday_quote_evidence,
    build_attested_etf_readiness_report,
    build_bounded_attested_etf_readiness_report,
    provision_database_instance_identity,
    verify_readiness_observation,
)

INSTANCE_UUID = "12345678-1234-5678-1234-567812345678"


def _settings(**overrides: str) -> Settings:
    values = {
        "readiness_expected_database_instance_uuid": INSTANCE_UUID,
        "readiness_expected_environment": "production",
        "readiness_deploy_artifact": "fundscope@sha256:fixture",
        "readiness_expected_schema_head": "20260717_000051",
        "readiness_attestation_key_id": "vps-readiness-key-v1",
        "readiness_attestation_secret": "fixture-secret-not-for-production",
    }
    values.update(overrides)
    return Settings(**values)


async def _seed_schema_head(session) -> None:
    await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
    await session.execute(
        text("INSERT INTO alembic_version (version_num) VALUES ('20260717_000051')")
    )


def _fixture_hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


async def _seed_complete_readiness_evidence(session) -> None:
    target_date = date(2026, 7, 17)
    signal_run = ShortResearchSignalRun(
        status="success",
        started_at=datetime(2026, 7, 17, 15, 30),
        finished_at=datetime(2026, 7, 17, 15, 35),
        as_of_date=target_date,
        as_of_trade_date=target_date,
        scope_kind="full",
        scope_hash=_fixture_hash("scope"),
        universe_snapshot_hash=_fixture_hash("universe"),
        input_snapshot_hash=_fixture_hash("input"),
        score_version="final_score_v3",
        score_field="ranking_score",
        rule_version="final_score_v3_rule_v2",
        ranking_contract_hash=_fixture_hash("contract"),
        data_cutoff=datetime(2026, 7, 17, 15, 0),
        price_basis="total_return_adjusted",
        expected_item_count=1,
        decision_data_item_count=1,
        decision_data_coverage_ratio=1.0,
        eligible_item_count=1,
        coverage_ratio=1.0,
        idempotency_key="readiness-real-job-shape",
    )
    session.add(signal_run)
    await session.flush()
    session.add(
        ShortResearchSignalItem(
            run_id=signal_run.id,
            asset_type="etf",
            asset_code="510001",
            rank=1,
            global_rank=1,
            total_score=90.0,
            ranking_score=90.0,
            score_eligible=True,
            conclusion="短线观察",
        )
    )
    await session.flush()
    with authorize_snapshot_publication(session.sync_session, run_id=signal_run.id):
        signal_run.publication_state = "published"
        signal_run.published_at = datetime(2026, 7, 17, 15, 35)
        await session.flush()

    validation_run = EtfSignalValidationRun(
        status="running",
        as_of_date=target_date,
        validation_mode="score_bucket_replay",
        rule_version="score_bucket_replay_v2",
    )
    session.add(validation_run)
    await session.flush()
    cohort = await build_production_validation_source_cohort(
        session,
        source_runs=(signal_run,),
    )
    await attach_validation_source_manifest(session, validation_run, cohort)
    validation_run.status = "success"
    session.add(
        JobRun(
            job_name="intraday_etf_watch",
            status="success",
            started_at=datetime(2026, 7, 17, 14, 50),
            finished_at=datetime(2026, 7, 17, 14, 51),
            details_json=_real_intraday_job_result(
                {
                    "510001": {
                        "decision_eligible": True,
                        "quote_freshness": "fresh",
                        "source": "eastmoney",
                        "quote_time": "2026-07-17T14:50:00",
                        "quote_time_is_fallback": False,
                        "consensus_status": "consistent",
                    }
                }
            ),
        )
    )
    await session.commit()


def _real_intraday_job_result(
    quote_audit: dict[str, dict[str, object]],
    *,
    status: str = "success",
) -> dict[str, object]:
    return {
        "status": status,
        "watched_count": len(quote_audit),
        "updated_quote_count": len(quote_audit),
        "details": {
            "provider_health": [
                {
                    "provider": "eastmoney",
                    "status": "success",
                    "quote_count": len(quote_audit),
                }
            ],
            "quote_audit": quote_audit,
            "quote_audit_count": len(quote_audit),
        },
    }


def test_intraday_quote_evidence_reads_real_job_result_shape() -> None:
    job_result = _real_intraday_job_result(
        {
            code: {
                "decision_eligible": True,
                "quote_freshness": "fresh",
                "quote_time": f"2026-07-17T14:50:0{index}",
                "quote_time_is_fallback": False,
                "consensus_status": "consistent",
            }
            for index, code in enumerate(("510001", "510002", "510003"))
        }
    )

    evidence = _intraday_quote_evidence(
        job_status="success",
        job_result=job_result,
        target_date=date(2026, 7, 17),
    )

    assert evidence == {
        "business_status": "success",
        "expected_count": 3,
        "covered_count": 3,
        "coverage_ratio": 1.0,
        "quote_time": "2026-07-17T14:50:02",
        "provider_health": [
            {
                "provider": "eastmoney",
                "status": "success",
                "quote_count": 3,
            }
        ],
    }


def test_intraday_quote_evidence_rejects_raw_counts_fallback_and_display_only() -> None:
    raw_only = _intraday_quote_evidence(
        job_status="success",
        job_result={
            "watch_count": 3,
            "updated_code_count": 3,
            "quote_time": "2026-07-17T14:50:00",
        },
        target_date=date(2026, 7, 17),
    )
    ineligible = _intraday_quote_evidence(
        job_status="success",
        job_result=_real_intraday_job_result(
            {
                "510001": {
                    "decision_eligible": True,
                    "quote_freshness": "fresh",
                    "quote_time": "2026-07-17T14:50:00",
                    "quote_time_is_fallback": True,
                    "consensus_status": "consistent",
                },
                "510002": {
                    "decision_eligible": False,
                    "quote_freshness": "display_only",
                    "quote_time": "2026-07-17T14:50:00",
                    "quote_time_is_fallback": False,
                    "consensus_status": "diverged",
                },
                "510003": {
                    "decision_eligible": True,
                    "quote_freshness": "fresh",
                    "quote_time": "2026-07-16T14:50:00",
                    "quote_time_is_fallback": False,
                    "consensus_status": "consistent",
                },
            }
        ),
        target_date=date(2026, 7, 17),
    )
    degraded = _intraday_quote_evidence(
        job_status="success",
        job_result=_real_intraday_job_result(
            {
                "510001": {
                    "decision_eligible": True,
                    "quote_freshness": "fresh",
                    "quote_time": "2026-07-17T14:50:00",
                    "quote_time_is_fallback": False,
                    "consensus_status": "consistent",
                }
            },
            status="degraded",
        ),
        target_date=date(2026, 7, 17),
    )

    assert raw_only["covered_count"] == 0
    assert raw_only["expected_count"] == 0
    assert ineligible["covered_count"] == 0
    assert ineligible["expected_count"] == 3
    assert degraded["covered_count"] == 0


def test_full_quote_audit_summary_is_not_limited_to_50() -> None:
    quote_audit = {
        f"{510000 + index:06d}": {
            "decision_eligible": True,
            "quote_freshness": "fresh",
            "quote_time": f"2026-07-17T14:50:{index % 60:02d}",
            "quote_time_is_fallback": False,
            "consensus_status": "consistent",
        }
        for index in range(100)
    }
    summary = _decision_quote_summary(quote_audit)
    job_result = _real_intraday_job_result(dict(list(quote_audit.items())[:50]))
    job_result["watched_count"] = 100
    job_result["details"]["decision_quote_summary"] = summary

    evidence = _intraday_quote_evidence(
        job_status="success",
        job_result=job_result,
        target_date=date(2026, 7, 17),
    )

    assert summary["covered_count_by_trade_date"] == {"2026-07-17": 100}
    assert evidence["expected_count"] == 100
    assert evidence["covered_count"] == 100
    assert evidence["coverage_ratio"] == 1.0


@pytest.mark.asyncio
async def test_readiness_requires_real_job_shape_and_canonical_manifest(
    app,
    monkeypatch,
) -> None:
    async def passing_history(*_args, **_kwargs):
        return {
            "contract_hash": _fixture_hash("contract"),
            "history_publication_gate_passed": True,
            "blockers": [],
        }

    monkeypatch.setattr(
        readiness_module,
        "read_etf_history_readiness",
        passing_history,
    )
    async with app.state.db.session() as session:
        await _seed_schema_head(session)
        await provision_database_instance_identity(
            session,
            declared_environment="production",
            deploy_artifact="fundscope@sha256:fixture",
            attestation_key_id="vps-readiness-key-v1",
            instance_uuid=INSTANCE_UUID,
        )
        await _seed_complete_readiness_evidence(session)

        report = await build_attested_etf_readiness_report(
            session,
            settings=_settings(),
            target_date=date(2026, 7, 17),
            observed_at=datetime(2026, 7, 17, 16, 0),
        )

        async def mismatched_manifest(*_args, **_kwargs):
            return SimpleNamespace(
                events=(
                    SimpleNamespace(
                        ranking_contract_hash=_fixture_hash("old-contract")
                    ),
                )
            )

        monkeypatch.setattr(
            readiness_module,
            "validate_attached_validation_source_manifest",
            mismatched_manifest,
        )
        mismatched_report = await build_attested_etf_readiness_report(
            session,
            settings=_settings(),
            target_date=date(2026, 7, 17),
            observed_at=datetime(2026, 7, 17, 16, 0),
        )
        monkeypatch.setattr(
            readiness_module,
            "validate_attached_validation_source_manifest",
            validate_attached_validation_source_manifest,
        )
        await session.execute(
            text(
                "UPDATE etf_signal_validation_source_events "
                "SET source_event_hash = :tampered_hash"
            ),
            {"tampered_hash": _fixture_hash("tampered")},
        )
        await session.commit()
        session.expire_all()
        tampered_report = await build_attested_etf_readiness_report(
            session,
            settings=_settings(),
            target_date=date(2026, 7, 17),
            observed_at=datetime(2026, 7, 17, 16, 0),
        )

    assert report["production_attested"] is True
    assert report["intraday_quote_coverage"] == {
        "expected_count": 1,
        "covered_count": 1,
        "coverage_ratio": 1.0,
        "quote_time": "2026-07-17T14:50:00",
    }
    assert report["provider_health"][0]["provider"] == "eastmoney"
    assert mismatched_report["production_attested"] is False
    assert "registered_production_manifest_invalid" in {
        blocker["key"] for blocker in mismatched_report["blockers"]
    }
    assert tampered_report["production_attested"] is False
    assert "registered_production_manifest_invalid" in {
        blocker["key"] for blocker in tampered_report["blockers"]
    }


def test_formal_return_evidence_uses_only_the_registered_primary_endpoint() -> None:
    evidence = _formal_return_evidence(
        EtfSignalValidationRun(id=7, as_of_date=date(2026, 7, 17)),
        {
            "groups": [
                {
                    "windows": {
                        "5": {
                            "endpoint_type": "primary",
                            "calculation_status": "success",
                            "sample_sufficiency": "insufficient",
                            "compatible_signal_date_count": 12,
                            "completed_signal_date_count": 10,
                            "non_overlapping_signal_date_count": 10,
                            "paired_sample_count": 9,
                            "paired_required_count": 20,
                            "paired_coverage": 0.9,
                            "sufficiency_reasons": ["insufficient_paired_dates"],
                        }
                    }
                }
            ]
        },
    )

    assert evidence == {
        "validation_run_id": 7,
        "registered": True,
        "calculation_status": "success",
        "statistical_sufficiency": "insufficient",
        "compatible_signal_date_count": 12,
        "completed_signal_date_count": 10,
        "non_overlapping_signal_date_count": 10,
        "paired_sample_count": 9,
        "paired_required_count": 20,
        "paired_coverage": 0.9,
        "sufficiency_reasons": ["insufficient_paired_dates"],
    }


@pytest.mark.asyncio
async def test_database_identity_is_singleton_and_immutable(app) -> None:
    async with app.state.db.session() as session:
        identity = await provision_database_instance_identity(
            session,
            declared_environment="production",
            deploy_artifact="fundscope@sha256:fixture",
            attestation_key_id="vps-readiness-key-v1",
            instance_uuid=INSTANCE_UUID,
            creation_metadata={"provisioner": "test"},
        )
        await session.commit()

        same = await provision_database_instance_identity(
            session,
            declared_environment="production",
            deploy_artifact="fundscope@sha256:fixture",
            attestation_key_id="vps-readiness-key-v1",
            instance_uuid=INSTANCE_UUID,
        )
        with pytest.raises(DatabaseIdentityProvisioningError, match="immutable"):
            await provision_database_instance_identity(
                session,
                declared_environment="acceptance",
                deploy_artifact="fundscope@sha256:fixture",
                attestation_key_id="vps-readiness-key-v1",
                instance_uuid=INSTANCE_UUID,
            )
        count = await session.scalar(
            select(func.count()).select_from(DatabaseInstanceIdentity)
        )
        assert same.id == identity.id == 1
        assert count == 1

        same.instance_uuid = "87654321-4321-8765-4321-876543218765"
        with pytest.raises(DatabaseInstanceIdentityImmutableError, match="immutable"):
            await session.commit()
        await session.rollback()

@pytest.mark.asyncio
async def test_signed_readiness_fails_closed_without_real_data_gates(app) -> None:
    async with app.state.db.session() as session:
        await _seed_schema_head(session)
        await provision_database_instance_identity(
            session,
            declared_environment="production",
            deploy_artifact="fundscope@sha256:fixture",
            attestation_key_id="vps-readiness-key-v1",
            instance_uuid=INSTANCE_UUID,
        )
        await session.commit()

        report = await build_attested_etf_readiness_report(
            session,
            settings=_settings(),
            target_date=date(2026, 7, 17),
            observed_at=datetime(2026, 7, 17, 16, 0),
        )
        bounded_report = await build_bounded_attested_etf_readiness_report(
            session,
            settings=_settings(),
            target_date=date(2026, 7, 17),
            observed_at=datetime(2026, 7, 17, 16, 0),
        )

    assert report["read_only"] is True
    assert report["environment_identity"]["database_instance_uuid"] == INSTANCE_UUID
    assert report["environment_identity"]["schema_head"] == "20260717_000051"
    assert set(report) >= {
        "observed_at",
        "trade_date",
        "read_only",
        "ranking_contract_hash",
        "source_manifest",
        "publication",
        "history_readiness",
        "formal_return_evidence",
        "provider_health",
        "intraday_quote_coverage",
        "component_availability",
        "cap_violations",
        "non_finite_rejects",
        "rank_churn",
        "validation_exclusions",
    }
    assert set(report["history_readiness"]) >= {
        "daily_freshness",
        "history_depth_61",
        "contract_depth",
        "telemetry_depth_180",
        "historical_production_snapshots",
    }
    assert report["formal_return_evidence"] == {
        "validation_run_id": None,
        "registered": False,
        "calculation_status": "unavailable",
        "statistical_sufficiency": "unavailable",
        "compatible_signal_date_count": 0,
        "completed_signal_date_count": 0,
        "non_overlapping_signal_date_count": 0,
        "paired_sample_count": 0,
        "paired_required_count": 20,
        "paired_coverage": 0.0,
        "sufficiency_reasons": ["registered_primary_endpoint_missing"],
    }
    assert all(
        set(blocker) == {"key", "observed", "threshold"}
        for blocker in report["blockers"]
    )
    assert report["attestation"]["verified"] is True
    assert verify_readiness_observation(
        report,
        secret="fixture-secret-not-for-production",
    )
    assert bounded_report["limits"]["observed_sql_statements"] <= 25
    assert verify_readiness_observation(
        bounded_report,
        secret="fixture-secret-not-for-production",
    )
    assert report["production_attested"] is False
    assert {item["key"] for item in report["blockers"]} >= {
        "current_day_published_snapshot_missing",
        "daily_freshness_coverage_below_95pct",
        "history_depth_61_coverage_below_95pct",
        "intraday_quote_evidence_missing",
        "registered_production_manifest_missing",
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "fixture-secret-not-for-production" not in serialized


@pytest.mark.asyncio
async def test_historical_target_date_cannot_be_rollout_attested(app) -> None:
    async with app.state.db.session() as session:
        await _seed_schema_head(session)
        await provision_database_instance_identity(
            session,
            declared_environment="production",
            deploy_artifact="fundscope@sha256:fixture",
            attestation_key_id="vps-readiness-key-v1",
            instance_uuid=INSTANCE_UUID,
        )
        await session.commit()

        report = await build_attested_etf_readiness_report(
            session,
            settings=_settings(),
            target_date=date(2026, 7, 16),
            observed_at=datetime(2026, 7, 17, 16, 0),
        )

    assert report["production_attested"] is False
    assert "target_trade_date_mismatch" in {
        blocker["key"] for blocker in report["blockers"]
    }


@pytest.mark.asyncio
async def test_identity_mismatch_and_repeated_readiness_calls_do_not_inflate_sessions(app) -> None:
    async with app.state.db.session() as session:
        await _seed_schema_head(session)
        await provision_database_instance_identity(
            session,
            declared_environment="production",
            deploy_artifact="fundscope@sha256:fixture",
            attestation_key_id="vps-readiness-key-v1",
            instance_uuid=INSTANCE_UUID,
        )
        await session.commit()
        before = await session.scalar(
            select(func.count()).select_from(DatabaseInstanceIdentity)
        )

        settings = _settings(
            readiness_expected_database_instance_uuid=(
                "87654321-4321-8765-4321-876543218765"
            )
        )
        first = await build_attested_etf_readiness_report(
            session,
            settings=settings,
            target_date=date(2026, 7, 17),
        )
        second = await build_attested_etf_readiness_report(
            session,
            settings=settings,
            target_date=date(2026, 7, 17),
        )
        after = await session.scalar(
            select(func.count()).select_from(DatabaseInstanceIdentity)
        )

    assert first["production_attested"] is False
    assert second["production_attested"] is False
    assert "database_instance_uuid_mismatch" in {
        item["key"] for item in first["blockers"]
    }
    assert before == after == 1
