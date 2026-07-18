"""Read-only, environment-attested ETF readiness projection."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import (
    DatabaseInstanceIdentity,
    EtfSignalValidationRun,
    JobRun,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    utcnow,
)
from app.services.short_research.snapshot_selector import (
    required_etf_snapshot_trade_date,
)
from app.services.strategy_lab.etf_ranking_validation import (
    RankingValidationContractError,
)
from app.services.strategy_lab.etf_validation_manifest import (
    validate_attached_validation_source_manifest,
)
from app.services.workflows.etf_history_readiness import read_etf_history_readiness

READINESS_REPORT_VERSION = "etf_readiness_attestation_v1"
READINESS_ENDPOINT_DEADLINE_SECONDS = 10.0
READINESS_STATEMENT_TIMEOUT_SECONDS = 2.0
READINESS_MAX_SQL_STATEMENTS = 25
READINESS_MAX_ROWS_PER_STATEMENT = 5_000
_ALLOWED_ENVIRONMENTS = {"local", "test", "acceptance", "production"}


class DatabaseIdentityProvisioningError(ValueError):
    pass


class ReadinessQueryLimitError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _signature(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_readiness_observation(report: dict[str, Any], *, secret: str) -> bool:
    attestation = report.get("attestation")
    if not isinstance(attestation, dict) or not secret:
        return False
    signature = attestation.get("signature")
    if not isinstance(signature, str):
        return False
    unsigned = {**report, "attestation": {**attestation, "signature": None, "verified": False}}
    expected = _signature(unsigned, secret)
    return hmac.compare_digest(signature, expected)


def _sign_readiness_observation(report: dict[str, Any], *, secret: str) -> None:
    report["attestation"]["signature"] = None
    report["attestation"]["verified"] = False
    if not secret:
        report["production_attested"] = False
        return
    report["attestation"]["signature"] = _signature(report, secret)
    report["attestation"]["verified"] = verify_readiness_observation(
        report,
        secret=secret,
    )
    report["production_attested"] = bool(
        report["production_attested"]
        and report["attestation"]["verified"] is True
    )


async def provision_database_instance_identity(
    session: AsyncSession,
    *,
    declared_environment: str,
    deploy_artifact: str,
    attestation_key_id: str,
    instance_uuid: str | None = None,
    creation_metadata: dict[str, Any] | None = None,
) -> DatabaseInstanceIdentity:
    environment = declared_environment.strip().lower()
    if environment not in _ALLOWED_ENVIRONMENTS:
        raise DatabaseIdentityProvisioningError("declared environment is invalid")
    if not deploy_artifact.strip() or not attestation_key_id.strip():
        raise DatabaseIdentityProvisioningError(
            "deploy artifact and attestation key id are required"
        )
    frozen_uuid = str(uuid.UUID(instance_uuid)) if instance_uuid else str(uuid.uuid4())
    existing = await session.get(DatabaseInstanceIdentity, 1)
    if existing is not None:
        frozen = (
            existing.instance_uuid,
            existing.declared_environment,
            existing.provisioned_by_deploy,
            existing.attestation_key_id,
        )
        requested = (
            frozen_uuid,
            environment,
            deploy_artifact.strip(),
            attestation_key_id.strip(),
        )
        if requested != frozen:
            raise DatabaseIdentityProvisioningError(
                "database instance identity is immutable and already provisioned"
            )
        return existing
    identity = DatabaseInstanceIdentity(
        id=1,
        instance_uuid=frozen_uuid,
        declared_environment=environment,
        provisioned_by_deploy=deploy_artifact.strip(),
        attestation_key_id=attestation_key_id.strip(),
        creation_metadata_json=dict(creation_metadata or {}),
    )
    session.add(identity)
    await session.flush()
    return identity


async def _schema_head(session: AsyncSession) -> str | None:
    connection = await session.connection()
    has_table = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).has_table("alembic_version")
    )
    if not has_table:
        return None
    return await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))


def _blocker(key: str, observed: Any, threshold: Any) -> dict[str, Any]:
    return {"key": key, "observed": observed, "threshold": threshold}


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _intraday_quote_evidence(
    *,
    job_status: str | None,
    job_result: dict[str, Any],
    target_date: date,
) -> dict[str, Any]:
    details = _safe_mapping(job_result.get("details"))
    quote_audit = _safe_mapping(details.get("quote_audit"))
    business_status = str(job_result.get("status") or "")
    expected_count = (
        int(job_result["watched_count"])
        if isinstance(job_result.get("watched_count"), int)
        and not isinstance(job_result.get("watched_count"), bool)
        and int(job_result["watched_count"]) > 0
        else 0
    )
    target_date_key = target_date.isoformat()
    decision_summary = _safe_mapping(details.get("decision_quote_summary"))
    covered_by_date = _safe_mapping(
        decision_summary.get("covered_count_by_trade_date")
    )
    latest_by_date = _safe_mapping(
        decision_summary.get("latest_quote_time_by_trade_date")
    )
    eligible_times: list[datetime] = []
    if job_status == "success" and business_status == "success":
        for raw_evidence in quote_audit.values():
            evidence = _safe_mapping(raw_evidence)
            if (
                evidence.get("decision_eligible") is not True
                or evidence.get("quote_time_is_fallback") is not False
                or evidence.get("quote_freshness") != "fresh"
                or evidence.get("consensus_status")
                in {"diverged", "stale", "unavailable"}
            ):
                continue
            raw_quote_time = evidence.get("quote_time")
            if not isinstance(raw_quote_time, str):
                continue
            try:
                quote_time = datetime.fromisoformat(
                    raw_quote_time.strip().replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if quote_time.date() == target_date:
                eligible_times.append(quote_time)
    summary_count = covered_by_date.get(target_date_key)
    summary_quote_time = latest_by_date.get(target_date_key)
    summary_is_valid = (
        isinstance(summary_count, int)
        and not isinstance(summary_count, bool)
        and 0 <= summary_count <= expected_count
        and isinstance(summary_quote_time, str)
    )
    if (
        job_status == "success"
        and business_status == "success"
        and summary_is_valid
    ):
        try:
            parsed_summary_time = datetime.fromisoformat(
                summary_quote_time.strip().replace("Z", "+00:00")
            )
        except ValueError:
            summary_is_valid = False
        else:
            summary_is_valid = parsed_summary_time.date() == target_date
    if summary_is_valid:
        covered_count = int(summary_count)
        quote_time_value = parsed_summary_time.isoformat()
    else:
        covered_count = len(eligible_times)
        quote_time_value = (
            max(eligible_times).isoformat() if eligible_times else None
        )
    return {
        "business_status": business_status or None,
        "expected_count": expected_count,
        "covered_count": covered_count,
        "coverage_ratio": (
            covered_count / expected_count if expected_count else 0.0
        ),
        "quote_time": quote_time_value,
        "provider_health": (
            list(details["provider_health"])
            if isinstance(details.get("provider_health"), list)
            else []
        ),
    }


def _formal_return_evidence(
    validation_run: EtfSignalValidationRun | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    unavailable = {
        "validation_run_id": validation_run.id if validation_run else None,
        "registered": validation_run is not None,
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
    groups = summary.get("groups")
    if not isinstance(groups, list):
        return unavailable
    for group in groups:
        if not isinstance(group, dict):
            continue
        windows = group.get("windows")
        if not isinstance(windows, dict):
            continue
        metrics = windows.get("5")
        if not isinstance(metrics, dict) or metrics.get("endpoint_type") != "primary":
            continue
        return {
            "validation_run_id": validation_run.id if validation_run else None,
            "registered": validation_run is not None,
            "calculation_status": metrics.get("calculation_status") or "unavailable",
            "statistical_sufficiency": (
                metrics.get("sample_sufficiency")
                or metrics.get("statistical_sufficiency")
                or "unavailable"
            ),
            "compatible_signal_date_count": int(
                metrics.get("compatible_signal_date_count") or 0
            ),
            "completed_signal_date_count": int(
                metrics.get("completed_signal_date_count") or 0
            ),
            "non_overlapping_signal_date_count": int(
                metrics.get("non_overlapping_signal_date_count") or 0
            ),
            "paired_sample_count": int(metrics.get("paired_sample_count") or 0),
            "paired_required_count": int(
                metrics.get("paired_required_count")
                or metrics.get("required_signal_date_count")
                or 20
            ),
            "paired_coverage": float(metrics.get("paired_coverage") or 0.0),
            "sufficiency_reasons": list(metrics.get("sufficiency_reasons") or []),
        }
    return unavailable


def _schema_unavailable_report(
    *,
    settings: Settings,
    effective_date: date,
    observation_time: datetime,
    schema_head: str | None,
    identity_table_exists: bool,
) -> dict[str, Any]:
    expected_schema_head = settings.readiness_expected_schema_head.strip()
    blockers = [
        _blocker(
            "readiness_schema_unavailable",
            schema_head,
            expected_schema_head or "configured schema head",
        )
    ]
    if not identity_table_exists:
        blockers.append(
            _blocker(
                "database_instance_identity_table_missing",
                False,
                True,
            )
        )
    report: dict[str, Any] = {
        "report_version": READINESS_REPORT_VERSION,
        "observed_at": observation_time.isoformat(),
        "trade_date": effective_date.isoformat(),
        "read_only": True,
        "environment_identity": {
            "database_instance_uuid": None,
            "declared_environment": None,
            "provisioned_at": None,
            "provisioned_by_deploy": None,
            "attestation_key_id": None,
            "schema_head": schema_head,
            "deploy_artifact": settings.readiness_deploy_artifact.strip() or None,
        },
        "ranking_contract_hash": None,
        "source_manifest": {
            "validation_run_id": None,
            "source_kind": None,
            "manifest_hash": None,
            "source_event_count": None,
        },
        "publication": {
            "history_gate_passed": False,
            "decision_data_coverage_ratio": None,
            "score_coverage_ratio": None,
            "snapshot_age_seconds": None,
            "signal_run_id": None,
            "signal_trade_date": None,
        },
        "history_readiness": {
            "status": "unavailable",
            "blockers": [item["key"] for item in blockers],
        },
        "formal_return_evidence": _formal_return_evidence(None, {}),
        "provider_health": [],
        "intraday_quote_coverage": {
            "expected_count": 0,
            "covered_count": 0,
            "coverage_ratio": 0.0,
            "quote_time": None,
        },
        "component_availability": {},
        "cap_violations": [],
        "non_finite_rejects": 0,
        "rank_churn": None,
        "validation_exclusions": {},
        "limits": {
            "max_sql_statements": READINESS_MAX_SQL_STATEMENTS,
            "max_rows_per_statement": READINESS_MAX_ROWS_PER_STATEMENT,
            "statement_timeout_seconds": READINESS_STATEMENT_TIMEOUT_SECONDS,
            "endpoint_deadline_seconds": READINESS_ENDPOINT_DEADLINE_SECONDS,
        },
        "blockers": blockers,
        "production_attested": False,
        "attestation": {
            "algorithm": "HMAC-SHA256",
            "key_id": settings.readiness_attestation_key_id.strip() or None,
            "signature": None,
            "verified": False,
            "threat_model": (
                "Prevents accidental environment attribution; it does not prevent an "
                "authorized operator cloning identity and signing material together."
            ),
        },
    }
    _sign_readiness_observation(
        report,
        secret=settings.readiness_attestation_secret,
    )
    return report


async def build_attested_etf_readiness_report(
    session: AsyncSession,
    *,
    settings: Settings,
    target_date: date | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observation_time = observed_at or utcnow()
    effective_date = target_date or observation_time.date()
    schema_head = await _schema_head(session)
    identity = await session.get(DatabaseInstanceIdentity, 1)
    history = await read_etf_history_readiness(
        session,
        target_date=effective_date,
    )
    has_etf_item = (
        select(ShortResearchSignalItem.id)
        .where(
            ShortResearchSignalItem.run_id == ShortResearchSignalRun.id,
            ShortResearchSignalItem.asset_type == "etf",
        )
        .exists()
    )
    has_non_etf_item = (
        select(ShortResearchSignalItem.id)
        .where(
            ShortResearchSignalItem.run_id == ShortResearchSignalRun.id,
            ShortResearchSignalItem.asset_type != "etf",
        )
        .exists()
    )
    signal_run = await session.scalar(
        select(ShortResearchSignalRun)
        .where(
            ShortResearchSignalRun.status == "success",
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.scope_kind == "full",
            ShortResearchSignalRun.score_version == "final_score_v3",
            ShortResearchSignalRun.rule_version == "final_score_v3_rule_v2",
            ShortResearchSignalRun.score_field == "ranking_score",
            ShortResearchSignalRun.price_basis == "total_return_adjusted",
            ShortResearchSignalRun.as_of_trade_date == effective_date,
            ShortResearchSignalRun.ranking_contract_hash.is_not(None),
            ShortResearchSignalRun.universe_snapshot_hash.is_not(None),
            ShortResearchSignalRun.input_snapshot_hash.is_not(None),
            ShortResearchSignalRun.decision_data_coverage_ratio >= 0.95,
            ShortResearchSignalRun.coverage_ratio >= 0.95,
            has_etf_item,
            ~has_non_etf_item,
        )
        .order_by(
            ShortResearchSignalRun.as_of_trade_date.desc(),
            ShortResearchSignalRun.id.desc(),
        )
        .limit(1)
    )
    validation_run = (
        await session.scalar(
            select(EtfSignalValidationRun)
            .where(
                EtfSignalValidationRun.status == "success",
                EtfSignalValidationRun.ranking_source_kind == "production_published",
                EtfSignalValidationRun.source_manifest_hash.is_not(None),
                EtfSignalValidationRun.source_event_count > 0,
                EtfSignalValidationRun.source_ranking_contract_hash
                == signal_run.ranking_contract_hash,
            )
            .order_by(EtfSignalValidationRun.id.desc())
            .limit(1)
        )
        if signal_run is not None
        else None
    )
    production_manifest_valid = False
    if validation_run is not None:
        try:
            production_cohort = await validate_attached_validation_source_manifest(
                session,
                validation_run,
            )
        except RankingValidationContractError:
            pass
        else:
            signal_contract_hash = (
                signal_run.ranking_contract_hash if signal_run is not None else None
            )
            production_manifest_valid = bool(
                signal_contract_hash
                and validation_run.source_ranking_contract_hash
                == signal_contract_hash
                and all(
                    event.ranking_contract_hash == signal_contract_hash
                    for event in production_cohort.events
                )
            )
    intraday_run = await session.scalar(
        select(JobRun)
        .where(JobRun.job_name == "intraday_etf_watch")
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    signal_summary = _safe_mapping(signal_run.summary_json if signal_run else None)
    validation_summary = _safe_mapping(
        validation_run.summary_json if validation_run else None
    )
    intraday_job_result = _safe_mapping(
        intraday_run.details_json if intraday_run else None
    )
    intraday_evidence = _intraday_quote_evidence(
        job_status=intraday_run.status if intraday_run else None,
        job_result=intraday_job_result,
        target_date=effective_date,
    )
    snapshot_time = (
        signal_run.published_at or signal_run.finished_at or signal_run.started_at
        if signal_run is not None
        else None
    )
    snapshot_age_seconds = (
        max(0.0, (observation_time - snapshot_time).total_seconds())
        if snapshot_time is not None
        else None
    )
    expected_quote_count = int(intraday_evidence["expected_count"])
    updated_quote_count = int(intraday_evidence["covered_count"])
    blockers: list[dict[str, Any]] = []
    expected_uuid = settings.readiness_expected_database_instance_uuid.strip()
    expected_environment = settings.readiness_expected_environment.strip().lower()
    expected_deploy = settings.readiness_deploy_artifact.strip()
    expected_key_id = settings.readiness_attestation_key_id.strip()
    expected_schema_head = settings.readiness_expected_schema_head.strip()
    secret = settings.readiness_attestation_secret
    if identity is None:
        blockers.append(_blocker("database_instance_identity_missing", None, "provisioned"))
    else:
        if not expected_uuid or identity.instance_uuid != expected_uuid:
            blockers.append(
                _blocker(
                    "database_instance_uuid_mismatch",
                    identity.instance_uuid,
                    expected_uuid or "configured expected UUID",
                )
            )
        if not expected_environment or identity.declared_environment != expected_environment:
            blockers.append(
                _blocker(
                    "declared_environment_mismatch",
                    identity.declared_environment,
                    expected_environment or "configured environment",
                )
            )
        if not expected_deploy or identity.provisioned_by_deploy != expected_deploy:
            blockers.append(
                _blocker(
                    "deploy_artifact_mismatch",
                    identity.provisioned_by_deploy,
                    expected_deploy or "configured deploy artifact",
                )
            )
        if not expected_key_id or identity.attestation_key_id != expected_key_id:
            blockers.append(
                _blocker(
                    "attestation_key_id_mismatch",
                    identity.attestation_key_id,
                    expected_key_id or "configured key id",
                )
            )
    if not expected_schema_head or schema_head != expected_schema_head:
        blockers.append(
            _blocker(
                "schema_head_mismatch",
                schema_head,
                expected_schema_head or "configured schema head",
            )
        )
    if not secret:
        blockers.append(_blocker("attestation_secret_missing", False, True))
    expected_trade_date = required_etf_snapshot_trade_date(observation_time)
    if effective_date != expected_trade_date:
        blockers.append(
            _blocker(
                "target_trade_date_mismatch",
                effective_date.isoformat(),
                expected_trade_date.isoformat(),
            )
        )
    if signal_run is None:
        blockers.append(
            _blocker(
                "current_day_published_snapshot_missing",
                None,
                effective_date.isoformat(),
            )
        )
    if validation_run is None:
        blockers.append(
            _blocker(
                "registered_production_manifest_missing",
                None,
                "complete current-contract manifest",
            )
        )
    elif not production_manifest_valid:
        blockers.append(
            _blocker(
                "registered_production_manifest_invalid",
                validation_run.id,
                "canonical manifest validation",
            )
        )
    quote_time = intraday_evidence["quote_time"]
    quote_trade_date: date | None = None
    if isinstance(quote_time, str):
        try:
            quote_trade_date = date.fromisoformat(quote_time[:10])
        except ValueError:
            quote_trade_date = None
    if intraday_run is None or quote_trade_date is None:
        blockers.append(
            _blocker(
                "intraday_quote_evidence_missing",
                None,
                effective_date.isoformat(),
            )
        )
    elif quote_trade_date != effective_date:
        blockers.append(
            _blocker(
                "intraday_quote_trade_date_mismatch",
                quote_trade_date.isoformat(),
                effective_date.isoformat(),
            )
        )
    elif expected_quote_count <= 0 or updated_quote_count / expected_quote_count < 0.95:
        blockers.append(
            _blocker(
                "intraday_quote_coverage_below_95pct",
                (
                    updated_quote_count / expected_quote_count
                    if expected_quote_count
                    else 0.0
                ),
                0.95,
            )
        )
    for history_blocker in history.get("blockers", []):
        blockers.append(_blocker(str(history_blocker), False, True))

    report: dict[str, Any] = {
        "report_version": READINESS_REPORT_VERSION,
        "observed_at": observation_time.isoformat(),
        "trade_date": effective_date.isoformat(),
        "read_only": True,
        "environment_identity": {
            "database_instance_uuid": identity.instance_uuid if identity else None,
            "declared_environment": identity.declared_environment if identity else None,
            "provisioned_at": identity.provisioned_at.isoformat() if identity else None,
            "provisioned_by_deploy": identity.provisioned_by_deploy if identity else None,
            "attestation_key_id": identity.attestation_key_id if identity else None,
            "schema_head": schema_head,
            "deploy_artifact": expected_deploy or None,
        },
        "ranking_contract_hash": history.get("contract_hash"),
        "source_manifest": {
            "validation_run_id": validation_run.id if validation_run else None,
            "source_kind": validation_run.ranking_source_kind if validation_run else None,
            "manifest_hash": validation_run.source_manifest_hash if validation_run else None,
            "source_event_count": validation_run.source_event_count if validation_run else None,
        },
        "publication": {
            "history_gate_passed": history.get("history_publication_gate_passed"),
            "decision_data_coverage_ratio": (
                signal_run.decision_data_coverage_ratio if signal_run else None
            ),
            "score_coverage_ratio": signal_run.coverage_ratio if signal_run else None,
            "snapshot_age_seconds": snapshot_age_seconds,
            "signal_run_id": signal_run.id if signal_run else None,
            "signal_trade_date": (
                (signal_run.as_of_trade_date or signal_run.as_of_date).isoformat()
                if signal_run
                else None
            ),
        },
        "history_readiness": history,
        "formal_return_evidence": _formal_return_evidence(
            validation_run,
            validation_summary,
        ),
        "provider_health": intraday_evidence["provider_health"],
        "intraday_quote_coverage": {
            "expected_count": expected_quote_count,
            "covered_count": updated_quote_count,
            "coverage_ratio": (
                updated_quote_count / expected_quote_count
                if expected_quote_count
                else 0.0
            ),
            "quote_time": quote_time,
        },
        "component_availability": signal_summary.get("component_availability") or {},
        "cap_violations": signal_summary.get("cap_violations") or [],
        "non_finite_rejects": int(signal_summary.get("non_finite_reject_count") or 0),
        "rank_churn": validation_summary.get("rank_churn"),
        "validation_exclusions": validation_summary.get("excluded_items") or {},
        "limits": {
            "max_sql_statements": READINESS_MAX_SQL_STATEMENTS,
            "max_rows_per_statement": READINESS_MAX_ROWS_PER_STATEMENT,
            "statement_timeout_seconds": READINESS_STATEMENT_TIMEOUT_SECONDS,
            "endpoint_deadline_seconds": READINESS_ENDPOINT_DEADLINE_SECONDS,
        },
        "blockers": blockers,
        "production_attested": not blockers and expected_environment == "production",
        "attestation": {
            "algorithm": "HMAC-SHA256",
            "key_id": expected_key_id or None,
            "signature": None,
            "verified": False,
            "threat_model": (
                "Prevents accidental environment attribution; it does not prevent an "
                "authorized operator cloning identity and signing material together."
            ),
        },
    }
    _sign_readiness_observation(report, secret=secret)
    return report


async def build_bounded_attested_etf_readiness_report(
    session: AsyncSession,
    *,
    settings: Settings,
    target_date: date | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    connection = await session.connection()
    sync_connection = connection.sync_connection
    statement_count = 0

    def count_statement(*_args: Any) -> None:
        nonlocal statement_count
        statement_count += 1
        if statement_count > READINESS_MAX_SQL_STATEMENTS:
            raise ReadinessQueryLimitError(
                f"readiness query exceeded {READINESS_MAX_SQL_STATEMENTS} statements"
            )

    event.listen(sync_connection, "before_cursor_execute", count_statement)
    try:
        async with asyncio.timeout(READINESS_ENDPOINT_DEADLINE_SECONDS):
            if connection.dialect.name == "postgresql":
                await session.execute(
                    text(
                        "SET LOCAL statement_timeout = "
                        f"'{int(READINESS_STATEMENT_TIMEOUT_SECONDS * 1000)}ms'"
                    )
                )
            observation_time = observed_at or utcnow()
            effective_date = target_date or observation_time.date()
            schema_head = await _schema_head(session)
            identity_table_exists = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).has_table(
                    "database_instance_identity"
                )
            )
            expected_schema_head = settings.readiness_expected_schema_head.strip()
            if (
                not expected_schema_head
                or schema_head != expected_schema_head
                or not identity_table_exists
            ):
                report = _schema_unavailable_report(
                    settings=settings,
                    effective_date=effective_date,
                    observation_time=observation_time,
                    schema_head=schema_head,
                    identity_table_exists=identity_table_exists,
                )
            else:
                report = await build_attested_etf_readiness_report(
                    session,
                    settings=settings,
                    target_date=effective_date,
                    observed_at=observation_time,
                )
    finally:
        event.remove(sync_connection, "before_cursor_execute", count_statement)

    report["limits"]["observed_sql_statements"] = statement_count
    _sign_readiness_observation(
        report,
        secret=settings.readiness_attestation_secret,
    )
    return report


async def readiness_observation_count(session: AsyncSession) -> int:
    """Read-only reports are not persisted and therefore cannot inflate sessions."""

    return int(
        await session.scalar(select(func.count()).select_from(DatabaseInstanceIdentity))
        or 0
    )


__all__ = [
    "DatabaseIdentityProvisioningError",
    "READINESS_ENDPOINT_DEADLINE_SECONDS",
    "build_attested_etf_readiness_report",
    "provision_database_instance_identity",
    "readiness_observation_count",
    "verify_readiness_observation",
]
