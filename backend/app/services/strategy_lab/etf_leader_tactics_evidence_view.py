"""Fail-closed read projection for the latest leader-tactics evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.etf_leader_tactics_evidence import (
    LEADER_EVIDENCE_SCHEMA_VERSION,
    latest_leader_factor_evidence,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_EXPERIMENT_FAMILY,
    LEADER_HYPOTHESIS_REGISTRY,
)

LEADER_EVIDENCE_VIEW_SCHEMA_VERSION = "etf_leader_tactics_evidence_view_v1"
LEADER_EVIDENCE_API_DISABLED = "leader_tactics_evidence_api_disabled"
LEADER_EVIDENCE_NOT_MATERIALIZED = "leader_tactics_evidence_not_materialized"
LEADER_REGISTRY_MISSING = "leader_hypothesis_registry_missing_or_incompatible"
LEADER_PIT_INPUT_MISSING = "leader_point_in_time_input_missing"
LEADER_COHORT_SPARSE = "leader_candidate_cohort_below_top10"
LEADER_OUTCOMES_PENDING = "leader_forward_outcomes_pending"
LEADER_DATES_INSUFFICIENT = "leader_eligible_pit_dates_below_252"
LEADER_EVIDENCE_INCOMPATIBLE = "leader_evidence_incompatible"
LEADER_EVIDENCE_INSUFFICIENT = "leader_evidence_insufficient_data"

_VALID_STATUSES = {
    "insufficient_data",
    "unconfirmed",
    "rejected",
    "eligible_for_v4_proposal",
}
_PIT_INPUT_REASONS = {
    "missing_historical_peer_mapping",
    "historical_peer_mapping_not_visible",
    "adjusted_turnover_unavailable",
    "leader_capture_source_incomplete",
    "missing_decision_eligible_adjusted_price",
}


def _source_registry() -> dict[str, Any]:
    return {
        "version": LEADER_HYPOTHESIS_REGISTRY.version,
        "interpretation_version": (
            LEADER_HYPOTHESIS_REGISTRY.interpretation_version
        ),
        "registry_hash": LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        "articles": [
            {
                "article_id": item.article_id,
                "title": item.title,
                "source_url": item.source_url,
                "published_at": item.published_at.isoformat(),
                "captured_content_hash": item.captured_content_hash,
            }
            for item in LEADER_HYPOTHESIS_REGISTRY.articles
        ],
        "statements": [
            {
                "statement_id": item.statement_id,
                "disclosure_state": item.disclosure_state,
                "proxy_id": item.proxy_id,
                "interpretation": item.interpretation,
                "limitation": item.limitation,
            }
            for item in LEADER_HYPOTHESIS_REGISTRY.statements
        ],
        "non_equivalence_notice": (
            LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice
        ),
    }


def _candidate_registry() -> dict[str, Any]:
    return {
        "registry_hash": FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "formula": item.formula,
                "required_history_sessions": item.required_history_sessions,
                "missing_value_rule": item.missing_value_rule,
                "manifest_hash": item.manifest_hash,
            }
            for item in FROZEN_LEADER_CANDIDATE_REGISTRY.candidates
        ],
    }


def _base_view(reason: str) -> dict[str, Any]:
    return {
        "schema_version": LEADER_EVIDENCE_VIEW_SCHEMA_VERSION,
        "experiment_family": LEADER_EXPERIMENT_FAMILY,
        "status": "insufficient_data",
        "unavailable_reason": reason,
        "generated_at": None,
        "data_cutoff": None,
        "manifest_hash": None,
        "factor_manifest_hash": None,
        "source_snapshot_hash": None,
        "universe_manifest_hash": None,
        "input_snapshot_hash": None,
        "feature_panel_hashes": [],
        "hypothesis_registry": _source_registry(),
        "candidate_registry": _candidate_registry(),
        "ranking_source_kind": "research_replay",
        "policy_mode": "none",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "coverage": {},
        "exclusion_counts": {},
        "primary_metrics": [],
        "exploratory_metrics": [],
        "diagnostics": {},
        "holdout": {},
        "ma5_policy_shadow": [],
        "candidate_decisions": [],
        "costs": {},
        "limitations": [
            LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice,
            "研究代理不代表原作者专有信号、正式排名、实盘邮件或确认成交。",
        ],
        "research_only": True,
        "production_mutation_allowed": False,
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def _finite_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    if not numeric.is_integer() or numeric < 0:
        return None
    return int(numeric)


def _insufficient_reason(report: Mapping[str, Any]) -> str:
    exclusions = _mapping(report.get("exclusion_counts"))
    if any(_finite_int(exclusions.get(reason)) for reason in _PIT_INPUT_REASONS):
        return LEADER_PIT_INPUT_MISSING
    if _finite_int(exclusions.get("insufficient_candidate_cohort")):
        return LEADER_COHORT_SPARSE
    if _finite_int(exclusions.get("future_window_pending")):
        return LEADER_OUTCOMES_PENDING
    coverage = _mapping(report.get("coverage"))
    sessions = next(
        (
            _finite_int(coverage.get(key))
            for key in (
                "eligible_point_in_time_sessions",
                "eligible_pit_dates",
                "complete_top10_dates",
            )
            if _finite_int(coverage.get(key)) is not None
        ),
        None,
    )
    if sessions is not None and sessions < 252:
        return LEADER_DATES_INSUFFICIENT
    return LEADER_EVIDENCE_INSUFFICIENT


def _report_is_compatible(row: Any, report: Mapping[str, Any]) -> bool:
    hypothesis = _mapping(report.get("hypothesis_registry"))
    candidates = _mapping(report.get("candidate_registry"))
    status = report.get("status")
    return (
        row.experiment_family == LEADER_EXPERIMENT_FAMILY
        and row.hypothesis_registry_hash
        == LEADER_HYPOTHESIS_REGISTRY.registry_hash
        and report.get("schema_version") == LEADER_EVIDENCE_SCHEMA_VERSION
        and report.get("experiment_family") == LEADER_EXPERIMENT_FAMILY
        and report.get("manifest_hash") == row.manifest_hash
        and hypothesis.get("registry_hash")
        == LEADER_HYPOTHESIS_REGISTRY.registry_hash
        and candidates.get("registry_hash")
        == FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash
        and status in _VALID_STATUSES
        and row.promotion_state == status
        and report.get("ranking_source_kind") == "research_replay"
        and report.get("policy_mode") in {"none", "policy_shadow"}
        and report.get("notification_provenance") in {"none", "simulated"}
        and report.get("execution_provenance")
        in {"none", "simulated_execution"}
        and report.get("research_only") is True
        and report.get("production_mutation_allowed") is False
    )


async def build_latest_leader_evidence_view(
    session: AsyncSession,
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Read persisted evidence only; never construct features or outcomes."""

    if not enabled:
        return _base_view(LEADER_EVIDENCE_API_DISABLED)
    row = await latest_leader_factor_evidence(session)
    if row is None:
        return _base_view(LEADER_EVIDENCE_NOT_MATERIALIZED)
    if (
        row.hypothesis_registry_hash
        != LEADER_HYPOTHESIS_REGISTRY.registry_hash
    ):
        view = _base_view(LEADER_REGISTRY_MISSING)
        view["generated_at"] = row.created_at
        return view
    report = _mapping(row.report_json)
    if not _report_is_compatible(row, report):
        view = _base_view(LEADER_EVIDENCE_INCOMPATIBLE)
        view["generated_at"] = row.created_at
        return view

    status = str(report["status"])
    unavailable_reason = (
        _insufficient_reason(report) if status == "insufficient_data" else None
    )
    return {
        **_base_view(unavailable_reason or LEADER_EVIDENCE_INSUFFICIENT),
        "status": status,
        "unavailable_reason": unavailable_reason,
        "generated_at": row.created_at,
        "data_cutoff": report.get("data_cutoff"),
        "manifest_hash": row.manifest_hash,
        "factor_manifest_hash": report.get("factor_manifest_hash"),
        "source_snapshot_hash": report.get("source_snapshot_hash"),
        "universe_manifest_hash": report.get("universe_manifest_hash"),
        "input_snapshot_hash": report.get("input_snapshot_hash"),
        "feature_panel_hashes": _sequence(report.get("feature_panel_hashes")),
        "ranking_source_kind": "research_replay",
        "policy_mode": report["policy_mode"],
        "notification_provenance": report["notification_provenance"],
        "execution_provenance": report["execution_provenance"],
        "coverage": _mapping(report.get("coverage")),
        "exclusion_counts": _mapping(report.get("exclusion_counts")),
        "primary_metrics": _sequence(report.get("primary_metrics")),
        "exploratory_metrics": _sequence(report.get("exploratory_metrics")),
        "diagnostics": _mapping(report.get("diagnostics")),
        "holdout": _mapping(report.get("holdout")),
        "ma5_policy_shadow": _sequence(report.get("ma5_policy_shadow")),
        "candidate_decisions": _sequence(report.get("candidate_decisions")),
        "costs": _mapping(row.costs_json),
        "limitations": list(
            dict.fromkeys(
                [
                    LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice,
                    "研究代理不代表原作者专有信号、正式排名、实盘邮件或确认成交。",
                    *[
                        str(item)
                        for item in _sequence(row.limitations_json)
                    ],
                ]
            )
        ),
    }
