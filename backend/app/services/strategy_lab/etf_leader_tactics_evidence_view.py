"""Fail-closed read projection for the latest leader-tactics evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentCheckpoint
from app.services.strategy_lab.etf_leader_tactics_continuation import (
    LEADER_CONTINUATION_STATE_KEY,
)
from app.services.strategy_lab.etf_leader_tactics_evidence import (
    LEADER_EVIDENCE_SCHEMA_VERSION,
    latest_leader_factor_evidence,
    latest_leader_historical_backtest_evidence,
    latest_leader_historical_proxy_evidence,
    latest_leader_maturity_evidence,
    latest_leader_observation_evidence,
)
from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    HISTORICAL_BACKTEST_CONTRACT_HASH,
    LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
    LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_BACKTEST_INCOMPATIBLE,
    LEADER_HISTORICAL_BACKTEST_NOT_PIT,
    LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
    LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
    LEADER_HISTORICAL_BACKTEST_UNAVAILABLE,
)
from app.services.strategy_lab.etf_leader_tactics_historical_proxy import (
    LEADER_HISTORICAL_PROXY_EVIDENCE_MODE,
    LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_PROXY_INCOMPATIBLE,
    LEADER_HISTORICAL_PROXY_NOT_PIT,
    LEADER_HISTORICAL_PROXY_REPORT_KIND,
    LEADER_HISTORICAL_PROXY_SCHEMA_VERSION,
    LEADER_HISTORICAL_PROXY_UNAVAILABLE,
)
from app.services.strategy_lab.etf_leader_tactics_observation import (
    LEADER_MATURITY_EXPERIMENT_FAMILY,
    LEADER_MATURITY_REPORT_KIND,
    LEADER_MATURITY_SCHEMA_VERSION,
    LEADER_OBSERVATION_EXPERIMENT_FAMILY,
    LEADER_OBSERVATION_PARTIAL,
    LEADER_OBSERVATION_REPORT_KIND,
    LEADER_OBSERVATION_SCHEMA_VERSION,
    MINIMUM_PROMOTION_INDEPENDENT_DATES,
    MINIMUM_PROMOTION_PIT_SESSIONS,
    MINIMUM_PROMOTION_WALK_FORWARD_FOLDS,
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
LEADER_OBSERVATION_EVIDENCE_INCOMPATIBLE = (
    "leader_observation_evidence_incompatible"
)

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


def _base_historical_proxy(reason: str) -> dict[str, Any]:
    return {
        "schema_version": LEADER_HISTORICAL_PROXY_SCHEMA_VERSION,
        "status": "unavailable",
        "unavailable_reason": reason,
        "generated_at": None,
        "manifest_hash": None,
        "contract_hash": None,
        "source_ranking_contract_hash": None,
        "source_input_snapshot_hash": None,
        "ranking_source_kind": "research_replay",
        "evidence_mode": LEADER_HISTORICAL_PROXY_EVIDENCE_MODE,
        "policy_mode": "none",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "signal_date": None,
        "signal_run_id": None,
        "history_sessions": None,
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "coverage": {},
        "exclusion_counts": {},
        "candidate_counts": {},
        "candidates": [],
        "promotion_gate_credit": {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        },
        "limitations": [
            "历史行情代理不具备事实 PIT 成员与接收时间，不能计入正式验证。",
            "仅供研究；不影响排名、持仓、提醒、邮件或执行。",
        ],
        "research_only": True,
        "production_mutation_allowed": False,
    }


def _base_historical_backtest(reason: str) -> dict[str, Any]:
    return {
        "schema_version": LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
        "status": "unavailable",
        "unavailable_reason": reason,
        "generated_at": None,
        "manifest_hash": None,
        "contract_hash": HISTORICAL_BACKTEST_CONTRACT_HASH,
        "source_signal_run_id": None,
        "source_signal_date": None,
        "first_signal_date": None,
        "last_signal_date": None,
        "ranking_source_kind": "research_replay",
        "evidence_mode": LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "coverage": {},
        "exclusion_counts": {},
        "aggregates": [],
        "promotion_gate_credit": {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        },
        "limitations": [
            "当前成员和分类存在幸存者/前视偏差，不能计入正式 PIT 验证。",
            "仅供研究；不影响排名、持仓、提醒、邮件或执行。",
        ],
        "research_only": True,
        "production_mutation_allowed": False,
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
        "observation_state": "not_started",
        "observation_unavailable_reason": reason,
        "observation_data_cutoff": None,
        "observation_manifest_hash": None,
        "observation_counts": {
            "eligible_pit_sessions": 0,
            "materialized_pit_sessions": 0,
            "required_pit_sessions": MINIMUM_PROMOTION_PIT_SESSIONS,
            "current_input_asset_count": 0,
            "current_available_observation_count": 0,
            "current_qualifying_observation_count": 0,
            "returned_current_observation_count": 0,
            "current_observations_truncated": False,
            "pending_outcome_count": 0,
            "matured_outcome_count": 0,
            "outcomes_by_horizon": [],
            "independent_primary_date_count": 0,
            "required_primary_date_count": (
                MINIMUM_PROMOTION_INDEPENDENT_DATES
            ),
            "completed_walk_forward_fold_count": 0,
            "required_walk_forward_fold_count": (
                MINIMUM_PROMOTION_WALK_FORWARD_FOLDS
            ),
        },
        "current_observations": [],
        "pending_outcomes": [],
        "partial_checkpoint": {},
        "historical_proxy": _base_historical_proxy(
            LEADER_HISTORICAL_PROXY_UNAVAILABLE
        ),
        "historical_backtest": _base_historical_backtest(
            LEADER_HISTORICAL_BACKTEST_UNAVAILABLE
        ),
        "costs": {},
        "limitations": [
            LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice,
            "研究代理不代表原作者专有信号、正式排名、实盘邮件或确认成交。",
        ],
        "research_only": True,
        "production_mutation_allowed": False,
    }


def _historical_proxy_projection(row: Any | None) -> dict[str, Any]:
    if row is None:
        return _base_historical_proxy(LEADER_HISTORICAL_PROXY_UNAVAILABLE)
    report = _mapping(row.report_json)
    candidates = _sequence(report.get("candidates"))
    promotion_gate_credit = _mapping(report.get("promotion_gate_credit"))
    compatible = (
        row.experiment_family == LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY
        and row.hypothesis_registry_hash
        == LEADER_HYPOTHESIS_REGISTRY.registry_hash
        and row.promotion_state == "insufficient_data"
        and report.get("schema_version")
        == LEADER_HISTORICAL_PROXY_SCHEMA_VERSION
        and report.get("report_kind") == LEADER_HISTORICAL_PROXY_REPORT_KIND
        and report.get("experiment_family")
        == LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY
        and report.get("manifest_hash") == row.manifest_hash
        and report.get("status") == "insufficient_data"
        and report.get("unavailable_reason") == LEADER_HISTORICAL_PROXY_NOT_PIT
        and report.get("ranking_source_kind") == "research_replay"
        and report.get("evidence_mode")
        == LEADER_HISTORICAL_PROXY_EVIDENCE_MODE
        and report.get("policy_mode") == "none"
        and report.get("notification_provenance") == "none"
        and report.get("execution_provenance") == "none"
        and report.get("price_basis") == "total_return_adjusted"
        and report.get("membership_mode")
        == "sealed_source_snapshot_current_vintage_proxy"
        and report.get("research_only") is True
        and report.get("production_mutation_allowed") is False
        and len(candidates) <= 40
        and promotion_gate_credit
        == {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        }
    )
    if not compatible:
        view = _base_historical_proxy(LEADER_HISTORICAL_PROXY_INCOMPATIBLE)
        view.update(
            {
                "status": "incompatible",
                "generated_at": row.created_at,
            }
        )
        return view
    return {
        **_base_historical_proxy(LEADER_HISTORICAL_PROXY_NOT_PIT),
        "status": "complete",
        "generated_at": row.created_at,
        "manifest_hash": row.manifest_hash,
        "contract_hash": report.get("contract_hash"),
        "source_ranking_contract_hash": report.get(
            "source_ranking_contract_hash"
        ),
        "source_input_snapshot_hash": report.get(
            "source_input_snapshot_hash"
        ),
        "signal_date": report.get("signal_date"),
        "signal_run_id": report.get("signal_run_id"),
        "history_sessions": report.get("history_sessions"),
        "coverage": _mapping(report.get("coverage")),
        "exclusion_counts": _mapping(report.get("exclusion_counts")),
        "candidate_counts": _mapping(report.get("candidate_counts")),
        "candidates": candidates,
        "promotion_gate_credit": promotion_gate_credit,
        "limitations": _sequence(report.get("limitations")),
    }


def _historical_backtest_projection(row: Any | None) -> dict[str, Any]:
    if row is None:
        return _base_historical_backtest(LEADER_HISTORICAL_BACKTEST_UNAVAILABLE)
    report = _mapping(row.report_json)
    promotion_gate_credit = _mapping(report.get("promotion_gate_credit"))
    aggregates = _sequence(report.get("aggregates"))
    compatible = (
        row.experiment_family == LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY
        and row.hypothesis_registry_hash
        == LEADER_HYPOTHESIS_REGISTRY.registry_hash
        and row.promotion_state == "insufficient_data"
        and report.get("schema_version")
        == LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION
        and report.get("report_kind") == LEADER_HISTORICAL_BACKTEST_REPORT_KIND
        and report.get("experiment_family")
        == LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY
        and report.get("manifest_hash") == row.manifest_hash
        and report.get("status") == "insufficient_data"
        and report.get("unavailable_reason") == LEADER_HISTORICAL_BACKTEST_NOT_PIT
        and report.get("contract_hash") == HISTORICAL_BACKTEST_CONTRACT_HASH
        and report.get("ranking_source_kind") == "research_replay"
        and report.get("evidence_mode") == LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE
        and report.get("membership_mode")
        == "sealed_source_snapshot_current_vintage_proxy"
        and report.get("price_basis") == "total_return_adjusted"
        and report.get("research_only") is True
        and report.get("production_mutation_allowed") is False
        and len(aggregates) <= 15
        and promotion_gate_credit
        == {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        }
    )
    if not compatible:
        view = _base_historical_backtest(
            LEADER_HISTORICAL_BACKTEST_INCOMPATIBLE
        )
        view.update({"status": "incompatible", "generated_at": row.created_at})
        return view
    return {
        **_base_historical_backtest(LEADER_HISTORICAL_BACKTEST_NOT_PIT),
        "status": "complete",
        "generated_at": row.created_at,
        "manifest_hash": row.manifest_hash,
        "source_signal_run_id": report.get("source_signal_run_id"),
        "source_signal_date": report.get("source_signal_date"),
        "first_signal_date": report.get("first_signal_date"),
        "last_signal_date": report.get("last_signal_date"),
        "coverage": _mapping(report.get("coverage")),
        "exclusion_counts": _mapping(report.get("exclusion_counts")),
        "aggregates": aggregates,
        "promotion_gate_credit": promotion_gate_credit,
        "limitations": _sequence(report.get("limitations")),
    }


def _observation_report_is_compatible(row: Any, report: Mapping[str, Any]) -> bool:
    observations = _sequence(report.get("current_observations"))
    counts = _mapping(report.get("observation_counts"))
    returned_count = _finite_int(counts.get("returned_current_observation_count"))
    return (
        row.experiment_family == LEADER_OBSERVATION_EXPERIMENT_FAMILY
        and row.hypothesis_registry_hash
        == LEADER_HYPOTHESIS_REGISTRY.registry_hash
        and row.promotion_state == "insufficient_data"
        and report.get("schema_version") == LEADER_OBSERVATION_SCHEMA_VERSION
        and report.get("report_kind") == LEADER_OBSERVATION_REPORT_KIND
        and report.get("experiment_family")
        == LEADER_OBSERVATION_EXPERIMENT_FAMILY
        and report.get("manifest_hash") == row.manifest_hash
        and report.get("observation_manifest_hash") == row.manifest_hash
        and report.get("status") == "insufficient_data"
        and report.get("ranking_source_kind") == "research_replay"
        and report.get("policy_mode") == "none"
        and report.get("notification_provenance") == "none"
        and report.get("execution_provenance") == "none"
        and report.get("research_only") is True
        and report.get("production_mutation_allowed") is False
        and len(observations) <= 20
        and returned_count is not None
        and returned_count == len(observations)
    )


def _observation_projection(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    report = _mapping(row.report_json)
    if not _observation_report_is_compatible(row, report):
        return {
            "observation_state": "blocked",
            "observation_unavailable_reason": (
                LEADER_OBSERVATION_EVIDENCE_INCOMPATIBLE
            ),
        }
    return {
        "observation_state": report.get("observation_state"),
        "observation_unavailable_reason": report.get(
            "observation_unavailable_reason"
        ),
        "observation_data_cutoff": report.get("observation_data_cutoff"),
        "observation_manifest_hash": row.manifest_hash,
        "observation_counts": _mapping(report.get("observation_counts")),
        "current_observations": _sequence(report.get("current_observations")),
        "pending_outcomes": _sequence(report.get("pending_outcomes")),
        "partial_checkpoint": _mapping(report.get("partial_checkpoint")),
    }


def _merge_maturity_projection(
    observation: dict[str, Any],
    row: Any | None,
) -> dict[str, Any]:
    if row is None or not observation:
        return observation
    report = _mapping(row.report_json)
    if (
        row.experiment_family != LEADER_MATURITY_EXPERIMENT_FAMILY
        or report.get("schema_version") != LEADER_MATURITY_SCHEMA_VERSION
        or report.get("report_kind") != LEADER_MATURITY_REPORT_KIND
        or report.get("experiment_family")
        != LEADER_MATURITY_EXPERIMENT_FAMILY
        or report.get("observation_manifest_hash")
        != observation.get("observation_manifest_hash")
        or report.get("ranking_source_kind") != "research_replay"
        or report.get("research_only") is not True
        or report.get("production_mutation_allowed") is not False
        or report.get("holdout_consumed") is not False
    ):
        return observation
    outcomes = [
        _mapping(item) for item in _sequence(report.get("outcomes"))
    ]
    by_horizon: dict[int, dict[str, int]] = {}
    for item in outcomes:
        horizon = _finite_int(item.get("horizon_sessions"))
        status = item.get("status")
        if horizon not in {5, 10} or status not in {
            "matured",
            "pending",
            "unavailable",
        }:
            continue
        counts = by_horizon.setdefault(
            int(horizon),
            {"pending": 0, "matured": 0, "unavailable": 0},
        )
        counts[str(status)] += 1
    counts = _mapping(observation.get("observation_counts"))
    counts.update(
        {
            "pending_outcome_count": _finite_int(
                report.get("pending_outcome_count")
            )
            or 0,
            "matured_outcome_count": _finite_int(
                report.get("matured_outcome_count")
            )
            or 0,
            "outcomes_by_horizon": [
                {"horizon_sessions": horizon, **values}
                for horizon, values in sorted(by_horizon.items())
            ],
        }
    )
    return {**observation, "observation_counts": counts}


async def _latest_partial_observation_projection(
    session: AsyncSession,
) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(EtfFactorExperimentCheckpoint)
            .where(EtfFactorExperimentCheckpoint.status != "complete")
            .order_by(
                EtfFactorExperimentCheckpoint.updated_at.desc(),
                EtfFactorExperimentCheckpoint.id.desc(),
            )
            .limit(32)
        )
    ).all()
    for row in rows:
        payload = _mapping(row.cached_factor_rows_json).get(
            LEADER_CONTINUATION_STATE_KEY
        )
        if not isinstance(payload, Mapping):
            continue
        checkpoint = _mapping(payload)
        phase_counts = _mapping(checkpoint.get("phase_item_counts"))
        coverage = _mapping(checkpoint.get("coverage"))
        input_coverage = _mapping(coverage.get("observation_input"))
        processed = _finite_int(phase_counts.get("features")) or 0
        expected = _finite_int(input_coverage.get("expected")) or processed
        base_counts = _base_view(LEADER_OBSERVATION_PARTIAL)[
            "observation_counts"
        ]
        return {
            "observation_state": "partial",
            "observation_unavailable_reason": LEADER_OBSERVATION_PARTIAL,
            "observation_counts": {
                **base_counts,
                "current_input_asset_count": expected,
                "returned_current_observation_count": 0,
            },
            "partial_checkpoint": {
                "phase": checkpoint.get("phase"),
                "generation": checkpoint.get("generation"),
                "processed_asset_count": processed,
                "total_asset_count": expected,
                "page_profile": _mapping(checkpoint.get("page_profile")),
                "cursor": _mapping(checkpoint.get("phase_cursor")),
                "stop_reason": checkpoint.get("stop_reason"),
                "checkpoint_hash": checkpoint.get("checkpoint_hash"),
            },
        }
    return {}


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
    historical_proxy = _historical_proxy_projection(
        await latest_leader_historical_proxy_evidence(session)
    )
    historical_backtest = _historical_backtest_projection(
        await latest_leader_historical_backtest_evidence(session)
    )
    observation_row = await latest_leader_observation_evidence(session)
    observation = _observation_projection(observation_row)
    if not observation:
        observation = await _latest_partial_observation_projection(session)
    maturity_row = await latest_leader_maturity_evidence(session)
    observation = _merge_maturity_projection(observation, maturity_row)
    row = await latest_leader_factor_evidence(session)
    if row is None:
        observation_state = observation.get("observation_state")
        reason = (
            LEADER_DATES_INSUFFICIENT
            if observation_state == "observing"
            else LEADER_OBSERVATION_PARTIAL
            if observation_state == "partial"
            else LEADER_EVIDENCE_NOT_MATERIALIZED
        )
        view = {**_base_view(reason), **observation}
        if observation_row is not None:
            view["generated_at"] = observation_row.created_at
        view["historical_proxy"] = historical_proxy
        view["historical_backtest"] = historical_backtest
        return view
    if (
        row.hypothesis_registry_hash
        != LEADER_HYPOTHESIS_REGISTRY.registry_hash
    ):
        view = _base_view(LEADER_REGISTRY_MISSING)
        view["generated_at"] = row.created_at
        return {
            **view,
            **observation,
            "historical_proxy": historical_proxy,
            "historical_backtest": historical_backtest,
        }
    report = _mapping(row.report_json)
    if not _report_is_compatible(row, report):
        view = _base_view(LEADER_EVIDENCE_INCOMPATIBLE)
        view["generated_at"] = row.created_at
        return {
            **view,
            **observation,
            "historical_proxy": historical_proxy,
            "historical_backtest": historical_backtest,
        }

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
        **observation,
        "historical_proxy": historical_proxy,
        "historical_backtest": historical_backtest,
    }
