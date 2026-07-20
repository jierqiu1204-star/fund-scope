from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

EVIDENCE_SCHEMA_VERSION = "etf_research_evidence_v1"
SIGNAL_CONTRACT_VERSION = "short_research_signal_v1"
ALLOCATION_CONTRACT_VERSION = "etf_portfolio_allocation_contract_v1"
REPLAY_CONTRACT_VERSION = "etf_replay_contract_v2"
EXIT_CALIBRATION_CONTRACT_VERSION = "etf_exit_calibration_contract_v1"
EXIT_ACTION_CONTRACT_VERSION = "etf_exit_action_v2"
REENTRY_CONTRACT_VERSION = "etf_reentry_rule_v1"
BUCKET_THRESHOLD_CONTRACT_VERSION = "etf_bucket_threshold_v1"
EXIT_V2_EVIDENCE_CONTRACT_VERSION = "etf_exit_v2_evidence_contract_v1"
EXECUTION_MODEL_DAILY_CLOSE = "daily_close_v1"
EXECUTION_MODEL_INTRADAY_ALERT = "intraday_alert_v1"
FEE_MODEL_SIMPLE_RATE = "simple_fee_rate_v1"
REPLAY_PROVENANCE_SCHEMA_VERSION = "etf_replay_provenance_v1"

EVIDENCE_STATUS_SAME_CONTRACT = "同源已验证"
EVIDENCE_STATUS_WAITING = "等待验证"
EVIDENCE_STATUS_INSUFFICIENT = "样本不足"
EVIDENCE_STATUS_VERSION_MISMATCH = "版本不一致"
EVIDENCE_STATUS_LEGACY = "旧口径结果"


class RankingSourceKind(StrEnum):
    PRODUCTION_PUBLISHED = "production_published"
    RESEARCH_REPLAY = "research_replay"


class SignalContractCompatibility(StrEnum):
    SAME_PRODUCTION_CONTRACT = "same_production_contract"
    SAME_REPLAY_CONTRACT = "same_replay_contract"
    MISMATCH = "mismatch"
    LEGACY = "legacy"


class ActionPolicyContractCompatibility(StrEnum):
    SAME_CONTRACT = "same_contract"
    MISMATCH = "mismatch"
    LEGACY = "legacy"


class PolicyMode(StrEnum):
    PRODUCTION_LIVE = "production_live"
    POLICY_SHADOW = "policy_shadow"


class NotificationProvenance(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    SHADOW_ELIGIBLE = "shadow_eligible"
    SMTP_FAILED_LIVE = "smtp_failed_live"
    SMTP_UNKNOWN_LIVE = "smtp_unknown_live"
    SMTP_ACCEPTED_LIVE = "smtp_accepted_live"
    PROVIDER_DELIVERED_LIVE = "provider_delivered_live"


class ReplayExecutionProvenance(StrEnum):
    NONE = "none"
    SIMULATED_EXECUTION = "simulated_execution"
    USER_CONFIRMED = "user_confirmed"


def require_single_validation_ranking_source(
    evidence_summaries: Sequence[dict[str, Any]],
) -> RankingSourceKind:
    if not evidence_summaries:
        raise ValueError("validation evidence requires a ranking source")
    try:
        sources = {
            RankingSourceKind(summary["ranking_source_kind"])
            for summary in evidence_summaries
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("validation evidence requires a registered ranking source") from exc
    if len(sources) != 1:
        raise ValueError("validation evidence from different ranking sources cannot be merged")
    return next(iter(sources))


_CONTRACT_HASH_FIELDS = (
    "ranking_contract_hash",
    "source_ranking_contract_hash",
    "contract_hash",
)
_EVIDENCE_IDENTITY_FIELDS = (
    (("ranking_surface",), ("ranking_surface", "source_ranking_surface")),
    (("score_version",), ("source_score_version", "score_version")),
    (("score_field",), ("source_score_field", "score_field")),
    (("rule_version",), ("source_rule_version", "rule_version")),
    (("universe_snapshot_hash",), ("source_universe_snapshot_hash", "universe_snapshot_hash")),
    (("price_basis",), ("price_basis",)),
    (("allocation_version",), ("allocation_version",)),
    (("allocation_contract_hash",), ("source_allocation_contract_hash", "allocation_contract_hash")),
)


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
class ReplayEvidenceProvenanceContract:
    ranking_source_kind: RankingSourceKind
    signal_contract_compatibility: SignalContractCompatibility
    action_policy_contract_compatibility: ActionPolicyContractCompatibility
    policy_mode: PolicyMode
    notification_provenance: NotificationProvenance
    execution_provenance: ReplayExecutionProvenance
    provider_receipt_id: str | None = None
    provider_receipt_timestamp: datetime | None = None
    schema_version: str = REPLAY_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        dimensions = (
            (self.ranking_source_kind, RankingSourceKind),
            (self.signal_contract_compatibility, SignalContractCompatibility),
            (
                self.action_policy_contract_compatibility,
                ActionPolicyContractCompatibility,
            ),
            (self.policy_mode, PolicyMode),
            (self.notification_provenance, NotificationProvenance),
            (self.execution_provenance, ReplayExecutionProvenance),
        )
        if not all(isinstance(value, enum_type) for value, enum_type in dimensions):
            raise ValueError("registered provenance enum values are required")
        if (
            self.ranking_source_kind is RankingSourceKind.RESEARCH_REPLAY
            and self.signal_contract_compatibility
            is SignalContractCompatibility.SAME_PRODUCTION_CONTRACT
        ) or (
            self.ranking_source_kind is RankingSourceKind.PRODUCTION_PUBLISHED
            and self.signal_contract_compatibility
            is SignalContractCompatibility.SAME_REPLAY_CONTRACT
        ):
            raise ValueError("ranking source and signal contract compatibility conflict")
        if self.notification_provenance is NotificationProvenance.PROVIDER_DELIVERED_LIVE:
            if (
                not isinstance(self.provider_receipt_id, str)
                or not self.provider_receipt_id.strip()
                or not isinstance(self.provider_receipt_timestamp, datetime)
            ):
                raise ValueError(
                    "provider receipt id and timestamp are required for provider delivery"
                )

    def _payload(self) -> dict[str, Any]:
        return {
            "action_policy_contract_compatibility": (
                self.action_policy_contract_compatibility.value
            ),
            "execution_provenance": self.execution_provenance.value,
            "notification_provenance": self.notification_provenance.value,
            "policy_mode": self.policy_mode.value,
            "provider_receipt_id": self.provider_receipt_id,
            "provider_receipt_timestamp": (
                self.provider_receipt_timestamp.isoformat()
                if self.provider_receipt_timestamp is not None
                else None
            ),
            "ranking_source_kind": self.ranking_source_kind.value,
            "schema_version": self.schema_version,
            "signal_contract_compatibility": self.signal_contract_compatibility.value,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self._payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _with_hash(self._payload())


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
    ranking_contract_hash: str | None = None
    score_version: str | None = None
    score_field: str | None = None
    universe_snapshot_hash: str | None = None
    price_basis: str | None = None
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
    action_lifecycle_version: str
    target_semantics: str
    action_event_source: str
    execution_model: str
    fee_model: str
    date_range: dict[str, str | None]
    data_cutoff: str | None
    ranking_surface: str | None = None
    warmup_range: dict[str, str | None] | None = None
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
    ranking_contract_hash: str | None = None,
    score_version: str | None = None,
    score_field: str | None = None,
    universe_snapshot_hash: str | None = None,
    price_basis: str | None = None,
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
        ranking_contract_hash=ranking_contract_hash,
        score_version=score_version,
        score_field=score_field,
        universe_snapshot_hash=universe_snapshot_hash,
        price_basis=price_basis,
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
    action_lifecycle_version: str,
    target_semantics: str,
    action_event_source: str,
    execution_model: str,
    fee_model: str,
    start_date: date | None,
    end_date: date | None,
    data_cutoff: date | datetime | None,
    ranking_surface: str | None = None,
    warmup_start_date: date | None = None,
    warmup_end_date: date | None = None,
) -> dict[str, Any]:
    return ReplayContract(
        replay_run_id=replay_run_id,
        signal_rule_version=signal_rule_version,
        allocation_version=allocation_version,
        action_lifecycle_version=action_lifecycle_version,
        target_semantics=target_semantics,
        action_event_source=action_event_source,
        execution_model=execution_model,
        fee_model=fee_model,
        date_range={
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        data_cutoff=str(_canonical(data_cutoff)) if data_cutoff is not None else None,
        ranking_surface=ranking_surface,
        warmup_range={
            "start_date": warmup_start_date.isoformat() if warmup_start_date else None,
            "end_date": warmup_end_date.isoformat() if warmup_end_date else None,
        },
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


def _identity_value(payload: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        value = payload.get(field)
        if value is not None:
            return value
    return None


def _contract_hash(payload: dict[str, Any]) -> str | None:
    value = _identity_value(payload, _CONTRACT_HASH_FIELDS)
    return str(value) if value else None


def _has_identity_mismatch(current_contract: dict[str, Any], evidence_summary: dict[str, Any]) -> bool:
    for current_fields, evidence_fields in _EVIDENCE_IDENTITY_FIELDS:
        current_value = _identity_value(current_contract, current_fields)
        evidence_value = _identity_value(evidence_summary, evidence_fields)
        if current_value is not None and evidence_value is not None and current_value != evidence_value:
            return True
    return False


def classify_evidence_status(
    current_contract: dict[str, Any] | None,
    evidence_summary: dict[str, Any] | None,
    *,
    min_sample_count: int = 20,
) -> str:
    if not evidence_summary:
        return EVIDENCE_STATUS_WAITING
    evidence_hash = _contract_hash(evidence_summary)
    if not evidence_hash:
        return EVIDENCE_STATUS_LEGACY
    current_hash = _contract_hash(current_contract or {})
    if not current_hash:
        return EVIDENCE_STATUS_LEGACY
    if evidence_hash != current_hash:
        return EVIDENCE_STATUS_VERSION_MISMATCH
    if _has_identity_mismatch(current_contract or {}, evidence_summary):
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
    if not validation_evidence and not backtest_metrics:
        return EvidenceSummary(
            evidence_status=EVIDENCE_STATUS_WAITING,
            contract_hash=_contract_hash(current_contract or {}),
            caveats=caveats or [],
        ).to_dict()

    validation = validation_evidence or {}
    sample_count = int(validation.get("sample_count") or 0)
    if sample_count <= 0:
        quality = validation.get("sample_quality")
        if isinstance(quality, dict):
            sample_count = int(quality.get("sample_count_total") or 0)
    contract_hash = _contract_hash(validation)
    evidence_for_status = validation or {"sample_count": sample_count}
    summary = EvidenceSummary(
        evidence_status=classify_evidence_status(
            current_contract,
            evidence_for_status,
        ),
        contract_hash=contract_hash,
        sample_count=sample_count,
        coverage=validation.get("coverage"),
        label_outcome_stats=validation or None,
        backtest_metrics=backtest_metrics or None,
        caveats=caveats or [],
    )
    return summary.to_dict()
