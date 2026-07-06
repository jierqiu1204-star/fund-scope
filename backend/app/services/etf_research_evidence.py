from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

EVIDENCE_SCHEMA_VERSION = "etf_research_evidence_v1"
SIGNAL_CONTRACT_VERSION = "short_research_signal_v1"
ALLOCATION_CONTRACT_VERSION = "etf_portfolio_allocation_contract_v1"
REPLAY_CONTRACT_VERSION = "etf_replay_contract_v1"
EXIT_CALIBRATION_CONTRACT_VERSION = "etf_exit_calibration_contract_v1"
EXIT_ACTION_CONTRACT_VERSION = "etf_exit_action_v2"
REENTRY_CONTRACT_VERSION = "etf_reentry_rule_v1"
BUCKET_THRESHOLD_CONTRACT_VERSION = "etf_bucket_threshold_v1"
EXIT_V2_EVIDENCE_CONTRACT_VERSION = "etf_exit_v2_evidence_contract_v1"
EXECUTION_MODEL_DAILY_CLOSE = "daily_close_v1"
EXECUTION_MODEL_INTRADAY_ALERT = "intraday_alert_v1"
FEE_MODEL_SIMPLE_RATE = "simple_fee_rate_v1"

EVIDENCE_STATUS_SAME_CONTRACT = "同源已验证"
EVIDENCE_STATUS_WAITING = "等待验证"
EVIDENCE_STATUS_INSUFFICIENT = "样本不足"
EVIDENCE_STATUS_VERSION_MISMATCH = "版本不一致"
EVIDENCE_STATUS_LEGACY = "旧口径结果"


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def stable_contract_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(_canonical(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _with_hash(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["contract_hash"] = stable_contract_hash(result)
    return result


@dataclass(frozen=True)
class ResearchSignalContract:
    asset_type: str
    asset_code: str
    signal_run_id: int | None
    signal_date: date | None
    score: float | None
    observation_label: str
    entry_timing_label: str
    data_reliability: str
    source_data_time: str | None
    rule_version: str = SIGNAL_CONTRACT_VERSION
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _with_hash(asdict(self))


@dataclass(frozen=True)
class AllocationContract:
    portfolio_run_id: int | None
    source_signal_run_id: int | None
    allocation_version: str
    portfolio_mode: str
    market_regime: str
    target_weights: dict[str, float]
    allocation_layers: dict[str, Any]
    constraints: dict[str, Any]
    data_as_of_time: str | None
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _with_hash(asdict(self))


@dataclass(frozen=True)
class ReplayContract:
    replay_run_id: int | None
    signal_rule_version: str
    allocation_version: str
    execution_model: str
    fee_model: str
    date_range: dict[str, str | None]
    data_cutoff: str | None
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION
    replay_contract_version: str = REPLAY_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _with_hash(asdict(self))


@dataclass(frozen=True)
class ExitCalibrationContract:
    calibration_run_id: int | None
    calibration_candidate_id: int | None
    approved_parameter_id: int | None
    signal_rule_version: str
    exit_rule_version: str
    calibration_rule_version: str
    execution_model: str
    evidence_status: str
    data_cutoff: str | None
    data_window: dict[str, str | None]
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION
    calibration_contract_version: str = EXIT_CALIBRATION_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _with_hash(asdict(self))


@dataclass(frozen=True)
class ExitV2EvidenceContract:
    validation_run_id: int | None
    signal_contract_hash: str | None
    signal_rule_version: str
    exit_action_version: str
    reentry_version: str
    bucket_threshold_version: str
    execution_model: str
    data_cutoff: str | None
    research_only: bool = True
    approved_for_live: bool = False
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION
    exit_v2_contract_version: str = EXIT_V2_EVIDENCE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _with_hash(asdict(self))


@dataclass(frozen=True)
class EvidenceSummary:
    evidence_status: str
    contract_hash: str | None
    sample_count: int = 0
    coverage: float | None = None
    label_outcome_stats: dict[str, Any] | None = None
    backtest_metrics: dict[str, Any] | None = None
    caveats: list[str] | None = None
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_research_signal_contract(
    *,
    asset_type: str,
    asset_code: str,
    signal_run_id: int | None,
    signal_date: date | None,
    score: float | None,
    observation_label: str,
    entry_timing_label: str,
    data_reliability: str | None,
    source_data_time: Any,
    rule_version: str = SIGNAL_CONTRACT_VERSION,
) -> dict[str, Any]:
    return ResearchSignalContract(
        asset_type=asset_type,
        asset_code=asset_code,
        signal_run_id=signal_run_id,
        signal_date=signal_date,
        score=round(float(score), 6) if score is not None else None,
        observation_label=observation_label,
        entry_timing_label=entry_timing_label,
        data_reliability=data_reliability or "unavailable",
        source_data_time=str(_canonical(source_data_time)) if source_data_time is not None else None,
        rule_version=rule_version,
    ).to_dict()


def build_allocation_contract(
    *,
    portfolio_run_id: int | None,
    source_signal_run_id: int | None,
    portfolio_mode: str,
    market_regime: str,
    target_weights: dict[str, float],
    allocation_layers: dict[str, Any],
    constraints: dict[str, Any],
    data_as_of_time: Any,
    allocation_version: str = ALLOCATION_CONTRACT_VERSION,
) -> dict[str, Any]:
    return AllocationContract(
        portfolio_run_id=portfolio_run_id,
        source_signal_run_id=source_signal_run_id,
        allocation_version=allocation_version,
        portfolio_mode=portfolio_mode,
        market_regime=market_regime,
        target_weights={code: round(float(weight), 6) for code, weight in sorted(target_weights.items())},
        allocation_layers=allocation_layers,
        constraints=constraints,
        data_as_of_time=str(_canonical(data_as_of_time)) if data_as_of_time is not None else None,
    ).to_dict()


def build_replay_contract(
    *,
    replay_run_id: int | None,
    signal_rule_version: str,
    allocation_version: str,
    execution_model: str,
    fee_model: str,
    start_date: date | None,
    end_date: date | None,
    data_cutoff: date | datetime | None,
) -> dict[str, Any]:
    return ReplayContract(
        replay_run_id=replay_run_id,
        signal_rule_version=signal_rule_version,
        allocation_version=allocation_version,
        execution_model=execution_model,
        fee_model=fee_model,
        date_range={
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        data_cutoff=str(_canonical(data_cutoff)) if data_cutoff is not None else None,
    ).to_dict()


def build_exit_calibration_contract(
    *,
    calibration_run_id: int | None,
    calibration_candidate_id: int | None,
    approved_parameter_id: int | None,
    signal_rule_version: str,
    exit_rule_version: str,
    calibration_rule_version: str,
    execution_model: str,
    evidence_status: str,
    start_date: date | None,
    end_date: date | None,
    data_cutoff: date | datetime | None,
) -> dict[str, Any]:
    return ExitCalibrationContract(
        calibration_run_id=calibration_run_id,
        calibration_candidate_id=calibration_candidate_id,
        approved_parameter_id=approved_parameter_id,
        signal_rule_version=signal_rule_version,
        exit_rule_version=exit_rule_version,
        calibration_rule_version=calibration_rule_version,
        execution_model=execution_model,
        evidence_status=evidence_status,
        data_cutoff=str(_canonical(data_cutoff)) if data_cutoff is not None else None,
        data_window={
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
    ).to_dict()


def build_exit_v2_evidence_contract(
    *,
    validation_run_id: int | None,
    signal_contract_hash: str | None,
    signal_rule_version: str,
    execution_model: str,
    data_cutoff: date | datetime | None,
    exit_action_version: str = EXIT_ACTION_CONTRACT_VERSION,
    reentry_version: str = REENTRY_CONTRACT_VERSION,
    bucket_threshold_version: str = BUCKET_THRESHOLD_CONTRACT_VERSION,
    research_only: bool = True,
    approved_for_live: bool = False,
) -> dict[str, Any]:
    return ExitV2EvidenceContract(
        validation_run_id=validation_run_id,
        signal_contract_hash=signal_contract_hash,
        signal_rule_version=signal_rule_version,
        exit_action_version=exit_action_version,
        reentry_version=reentry_version,
        bucket_threshold_version=bucket_threshold_version,
        execution_model=execution_model,
        data_cutoff=str(_canonical(data_cutoff)) if data_cutoff is not None else None,
        research_only=research_only,
        approved_for_live=approved_for_live,
    ).to_dict()


def build_exit_v2_baseline_comparison(
    *,
    topn_hold: dict[str, Any],
    current_exit: dict[str, Any],
    guard_only: dict[str, Any],
    exit_v2: dict[str, Any],
) -> dict[str, Any]:
    baselines = {
        "topn_fixed_hold": topn_hold,
        "current_live_exit_rules": current_exit,
        "guard_only": guard_only,
        "exit_v2_reentry": exit_v2,
    }
    topn_return = float(
        topn_hold.get("total_return_pct") or topn_hold.get("return_pct") or topn_hold.get("cumulative_return") or 0.0
    )
    v2_return = float(
        exit_v2.get("total_return_pct") or exit_v2.get("return_pct") or exit_v2.get("cumulative_return") or 0.0
    )
    topn_drawdown = abs(float(topn_hold.get("max_drawdown_pct") or topn_hold.get("max_drawdown") or 0.0))
    v2_drawdown = abs(float(exit_v2.get("max_drawdown_pct") or exit_v2.get("max_drawdown") or 0.0))
    drawdown_improvement = topn_drawdown - v2_drawdown
    meaningful_drawdown_improvement = 1.0 if max(topn_drawdown, v2_drawdown) > 1.0 else 0.01
    v2_underperforms_hold = v2_return < topn_return and drawdown_improvement < meaningful_drawdown_improvement
    return {
        "baselines": baselines,
        "v2_underperforms_hold": v2_underperforms_hold,
        "drawdown_improvement_pct": round(drawdown_improvement, 4),
        "meaningful_drawdown_improvement_pct": meaningful_drawdown_improvement,
        "approved_for_live": False,
        "research_only": True,
        "conclusion": "V2 暂不适合升级为实时规则" if v2_underperforms_hold else "V2 可继续作为候选研究",
    }


def classify_evidence_status(
    current_contract: dict[str, Any] | None,
    evidence_summary: dict[str, Any] | None,
    *,
    min_sample_count: int = 20,
) -> str:
    if not evidence_summary:
        return EVIDENCE_STATUS_WAITING
    evidence_hash = evidence_summary.get("contract_hash")
    if not evidence_hash:
        return EVIDENCE_STATUS_LEGACY
    current_hash = (current_contract or {}).get("contract_hash")
    if current_hash and evidence_hash != current_hash:
        return EVIDENCE_STATUS_VERSION_MISMATCH
    sample_count = int(evidence_summary.get("sample_count") or 0)
    if sample_count < min_sample_count:
        return EVIDENCE_STATUS_INSUFFICIENT
    return EVIDENCE_STATUS_SAME_CONTRACT


def build_evidence_summary(
    *,
    current_contract: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None = None,
    backtest_metrics: dict[str, Any] | None = None,
    caveats: list[str] | None = None,
) -> dict[str, Any]:
    if validation_evidence is None and not backtest_metrics:
        return EvidenceSummary(
            evidence_status=EVIDENCE_STATUS_WAITING,
            contract_hash=(current_contract or {}).get("contract_hash"),
            caveats=caveats or [],
        ).to_dict()

    validation = validation_evidence or {}
    sample_count = int(validation.get("sample_count") or 0)
    if sample_count <= 0:
        quality = validation.get("sample_quality")
        if isinstance(quality, dict):
            sample_count = int(quality.get("sample_count_total") or 0)
    contract_hash = validation.get("contract_hash") or (current_contract or {}).get("contract_hash")
    summary = EvidenceSummary(
        evidence_status=classify_evidence_status(
            current_contract,
            {"contract_hash": contract_hash, "sample_count": sample_count} if contract_hash else validation,
        ),
        contract_hash=contract_hash,
        sample_count=sample_count,
        coverage=validation.get("coverage"),
        label_outcome_stats=validation or None,
        backtest_metrics=backtest_metrics or None,
        caveats=caveats or [],
    )
    return summary.to_dict()
