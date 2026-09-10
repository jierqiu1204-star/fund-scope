"""Point-in-time inputs and bounded evidence checks for route comparison.

The comparison is a research consumer of already persisted facts.  This
module deliberately does not call a market-data provider, run a scheduler, or
read the production ranking outputs.  It builds one immutable decision
snapshot per date and checks V2 evidence for that same date before a caller
may hand the inputs to the comparison core.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import market_data
from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.daily_reconstructable import REQUIRED_BAR_COUNT
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BASE_LAUNCH_V2,
    BREAKOUT_V2,
    FORMER_LEADER_REPAIR_V2,
    V2CandidateObservation,
    V2ContractError,
    V2LifecycleTransition,
    derive_lifecycle,
    screen_dual_universe,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_etf_inputs import (
    V2EtfInputBundle,
    read_etf_v2_asset_inputs,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_lifecycle_storage import (
    MAX_CONTINUATION_SECONDS,
    acquire_v2_global_run_lease,
    persist_v2_lifecycle_transitions,
    release_v2_global_run_lease,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    _validate_materialized_manifest,
)
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ArtifactConflictError,
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_point_in_time_decision_data import (
    MAX_CODES_PER_REPLAY_INPUT_PAGE,
    PointInTimeAdjustedSeries,
    PointInTimeEtfDecisionInputSnapshot,
    ReplayInputExclusion,
    build_point_in_time_adjusted_series,
    load_point_in_time_etf_decision_inputs,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import ForwardAdjustedClose
from app.services.strategy_lab.etf_strategy_route_comparison import (
    ComparisonDecisionSnapshot as CoreComparisonDecisionSnapshot,
)
from app.services.strategy_lab.etf_strategy_route_comparison import (
    ComparisonInput,
    ComparisonValuationInput,
    FrozenComparisonProvenance,
    V2ComparisonEvent,
    V2StateCheck,
    build_v2_comparison_events,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
ComparisonDecisionSnapshot = CoreComparisonDecisionSnapshot
_COMPARISON_FORMULAS = (BREAKOUT_V2, BASE_LAUNCH_V2, FORMER_LEADER_REPAIR_V2)
COMPARISON_INPUT_SCHEMA_VERSION = "etf_strategy_route_comparison_inputs_v1"
V2_DAY_CHECK_SCHEMA_VERSION = "etf_strategy_route_comparison_v2_day_check_v1"
GENERIC_RESEARCH_ARTIFACT_SCHEMA_VERSION = "generic_research_artifact_v1"
COMPARISON_REQUIRED_HISTORY_SESSIONS = 127
COMPARISON_MOMENTUM_LOOKBACK_SESSIONS = 126
COMPARISON_MIN_NONCLONE_ASSETS = 10
COMPARISON_MAX_UNIVERSE_SIZE = 2_000
COMPARISON_MAX_DATES = 512
COMPARISON_MAX_CONTINUATION_SECONDS = 55.0
V2_DAY_CHECK_PHASE = "route-comparison-v2-inputs"
COMPARISON_DECISION_CUTOFF_TIME = wall_time(19, 0)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _db_cutoff(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _json_value(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _json_object(value: object, *, field: str) -> object:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        raise V2ContractError(f"{field} is not JSON text")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise V2ContractError(f"{field} JSON is invalid") from exc
    return parsed


def _bool_value(value: object, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise V2ContractError(f"{field} is not boolean")


def _v2_observation_from_row(row: Mapping[str, Any]) -> V2CandidateObservation:
    gate_facts = _json_object(row.get("gate_facts_json"), field="gate_facts_json")
    exclusions = _json_object(row.get("exclusion_reasons_json"), field="exclusion_reasons_json")
    provenance = _json_object(row.get("provenance_json"), field="provenance_json")
    if not isinstance(gate_facts, Mapping) or not isinstance(exclusions, list):
        raise V2ContractError("persisted V2 observation payload has invalid gate facts")
    if not isinstance(provenance, Mapping):
        raise V2ContractError("persisted V2 observation provenance is invalid")
    signal_date = _as_date(row.get("signal_date"))
    source_cutoff = _as_datetime(row.get("source_cutoff"))
    if signal_date is None or source_cutoff is None:
        raise V2ContractError("persisted V2 observation timing is incomplete")
    asset_code = str(row.get("asset_code") or "").strip()
    tracked_index = str(row.get("tracked_index") or "").strip() or None
    # The ETF adapter persists the tracked index separately and uses the ETF
    # code as the real singleton group when no underlying mapping exists.
    # Reconstruct that exact value so the feature hash and core adapter see
    # the original V2 identity rather than an invented representative group.
    clone_group = f"index:{tracked_index}" if tracked_index else asset_code
    return V2CandidateObservation(
        universe=str(row.get("universe") or ""),
        asset_code=asset_code,
        asset_name=str(row.get("asset_name") or asset_code),
        signal_date=signal_date,
        formula_id=str(row.get("formula_id") or ""),
        state=str(row.get("state") or ""),
        availability=str(row.get("availability") or "unavailable"),  # type: ignore[arg-type]
        qualifies=_bool_value(row.get("qualifies"), field="qualifies"),
        score=(float(row["score"]) if row.get("score") is not None else None),
        gate_facts=tuple(sorted((str(key), value) for key, value in gate_facts.items())),
        exclusion_reasons=tuple(str(item) for item in exclusions),
        source_cutoff=source_cutoff,
        theme=(str(row["theme"]) if row.get("theme") is not None else None),
        sector=(str(row["sector"]) if row.get("sector") is not None else None),
        tracked_index=tracked_index,
        clone_group=clone_group,
        issuer=None,
        feature_hash=str(row.get("feature_hash") or ""),
    )


def _v2_transition_from_row(row: Mapping[str, Any]) -> V2LifecycleTransition:
    payload = _json_object(row.get("payload_json"), field="payload_json")
    if not isinstance(payload, Mapping):
        raise V2ContractError("persisted V2 transition payload is invalid")
    values = dict(payload)
    transition_date = _as_date(values.get("transition_date") or row.get("transition_date"))
    signal_date = _as_date(values.get("signal_date") or row.get("signal_date"))
    if signal_date is None or transition_date is None:
        raise V2ContractError("persisted V2 transition timing is incomplete")
    execution_date = _as_date(values.get("simulated_execution_date"))
    def _float(value: object, field: str) -> float | None:
        if value is None:
            return None
        parsed = _valid_float(value)
        if parsed is None:
            raise V2ContractError(f"persisted V2 transition {field} is invalid")
        return parsed

    transition = V2LifecycleTransition(
        universe=str(values.get("universe") or row.get("universe") or ""),
        asset_code=str(values.get("asset_code") or row.get("asset_code") or ""),
        formula_id=str(values.get("formula_id") or row.get("formula_id") or ""),
        signal_date=signal_date,
        from_state=(str(values["from_state"]) if values.get("from_state") is not None else None),
        to_state=str(values.get("to_state") or row.get("to_state") or ""),
        transition_date=transition_date,
        signal_high=_float(values.get("signal_high"), "signal_high") or 0.0,
        adjusted_close=_float(values.get("adjusted_close"), "adjusted_close"),
        adjusted_ma5=_float(values.get("adjusted_ma5"), "adjusted_ma5"),
        simulated_execution_date=execution_date,
        execution_model=str(values.get("execution_model") or ""),
        reason=str(values.get("reason") or ""),
        transition_hash=str(values.get("transition_hash") or row.get("transition_hash") or ""),
        cost_bps_per_side=float(values.get("cost_bps_per_side", 10.0)),
    )
    row_hash = str(row.get("transition_hash") or "")
    if row_hash and row_hash != transition.transition_hash:
        raise V2ContractError("V2 transition row hash differs from payload hash")
    return transition


def _v2_observation_key(observation: V2CandidateObservation) -> str:
    return (
        f"{observation.asset_code}:{observation.signal_date.isoformat()}:{observation.formula_id}"
    )


def _v2_observation_payload(observation: V2CandidateObservation) -> dict[str, Any]:
    """Serialize the complete immutable observation needed for continuation."""

    return {
        "universe": observation.universe,
        "asset_code": observation.asset_code,
        "asset_name": observation.asset_name,
        "signal_date": observation.signal_date.isoformat(),
        "formula_id": observation.formula_id,
        "state": observation.state,
        "availability": observation.availability,
        "qualifies": observation.qualifies,
        "score": observation.score,
        "gate_facts": {key: value for key, value in observation.gate_facts},
        "exclusion_reasons": list(observation.exclusion_reasons),
        "source_cutoff": observation.source_cutoff.isoformat(),
        "theme": observation.theme,
        "sector": observation.sector,
        "tracked_index": observation.tracked_index,
        "clone_group": observation.clone_group,
        "issuer": observation.issuer,
        "feature_hash": observation.feature_hash,
    }


def _v2_observation_from_payload(payload: Mapping[str, Any]) -> V2CandidateObservation:
    gate_facts = payload.get("gate_facts")
    exclusions = payload.get("exclusion_reasons")
    signal_date = _as_date(payload.get("signal_date"))
    source_cutoff = _as_datetime(payload.get("source_cutoff"))
    if (
        not isinstance(gate_facts, Mapping)
        or not isinstance(exclusions, list)
        or signal_date is None
        or source_cutoff is None
    ):
        raise V2ContractError("sealed V2 observation payload is incomplete")
    asset_code = str(payload.get("asset_code") or "").strip()
    if not asset_code:
        raise V2ContractError("sealed V2 observation asset is missing")
    score = payload.get("score")
    if score is not None:
        score = _valid_float(score)
        if score is None:
            raise V2ContractError("sealed V2 observation score is invalid")
    return V2CandidateObservation(
        universe=str(payload.get("universe") or ""),
        asset_code=asset_code,
        asset_name=str(payload.get("asset_name") or asset_code),
        signal_date=signal_date,
        formula_id=str(payload.get("formula_id") or ""),
        state=str(payload.get("state") or ""),
        availability=str(payload.get("availability") or "unavailable"),  # type: ignore[arg-type]
        qualifies=_bool_value(payload.get("qualifies"), field="qualifies"),
        score=score,
        gate_facts=tuple(sorted((str(key), value) for key, value in gate_facts.items())),
        exclusion_reasons=tuple(str(item) for item in exclusions),
        source_cutoff=source_cutoff,
        theme=(str(payload["theme"]) if payload.get("theme") is not None else None),
        sector=(str(payload["sector"]) if payload.get("sector") is not None else None),
        tracked_index=(
            str(payload["tracked_index"]) if payload.get("tracked_index") is not None else None
        ),
        clone_group=(
            str(payload["clone_group"]) if payload.get("clone_group") is not None else None
        ),
        issuer=(str(payload["issuer"]) if payload.get("issuer") is not None else None),
        feature_hash=str(payload.get("feature_hash") or ""),
    )


def _merge_v2_observations(
    observations: Sequence[V2CandidateObservation],
) -> tuple[V2CandidateObservation, ...]:
    by_key: dict[str, V2CandidateObservation] = {}
    for observation in observations:
        key = _v2_observation_key(observation)
        previous = by_key.get(key)
        if previous is not None and previous != observation:
            raise V2ContractError(f"duplicate sealed V2 observation: {key}")
        by_key[key] = observation
    return tuple(by_key[key] for key in sorted(by_key))


def _valid_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _valid_nonnegative_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _clone_group(metadata: Any) -> tuple[str, str | None]:
    underlying = str(getattr(metadata, "tracked_underlying_id", "") or "").strip()
    if underlying:
        return f"underlying:{underlying}", None
    return f"asset:{metadata.asset_code}", "clone_mapping_missing"


def _history_payload(series: PointInTimeAdjustedSeries) -> dict[str, Any]:
    return {
        "asset_code": series.asset_code,
        "series_hash": series.series_hash,
        "provider": series.provenance.provider,
        "adjustment_version": series.provenance.adjustment_version,
        "price_basis": series.provenance.price_basis,
        "earliest_source_timestamp": series.earliest_source_timestamp.isoformat(),
        "latest_source_timestamp": series.latest_source_timestamp.isoformat(),
        "synchronized_after_cutoff": series.synchronized_after_cutoff,
        "revision_hashes": list(series.revision_hashes),
        "bars": [
            {
                "session_date": bar.session_date.isoformat(),
                "adjusted_close": float(bar.adjusted_close),
            }
            for bar in series.bars
        ],
    }


@dataclass(frozen=True)
class ComparisonSnapshotAudit:
    """One causal cross-section and its common 127-close history.

    ``eligible_asset_codes`` is the membership-qualified universe before the
    history check.  ``nonclone_asset_codes`` is the deterministic PIT clone
    representative set, also before the history check.  ``adjusted_closes``
    contains only representatives with the complete 127-session history.
    Keeping these three sets separate prevents missing history from silently
    shrinking the denominator or from creating a false empty portfolio.
    """

    signal_date: date
    decision_cutoff: datetime
    source_hash: str
    eligible_asset_codes: tuple[str, ...]
    nonclone_asset_codes: tuple[str, ...]
    adjusted_closes: tuple[tuple[str, tuple[tuple[date, float], ...]], ...]
    history_eligible_asset_codes: tuple[str, ...] = ()
    history_missing_asset_codes: tuple[str, ...] = ()
    eligible_denominator: int = 0
    nonclone_denominator: int = 0
    history_numerator: int = 0
    clone_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    clone_representatives: tuple[tuple[str, str], ...] = ()
    clone_representative_liquidity: tuple[tuple[str, str, float], ...] = ()
    clone_mapping_missing_asset_codes: tuple[str, ...] = ()
    exclusions: tuple[tuple[str, str], ...] = ()
    universe_hash: str = ""
    input_hash: str = ""
    coverage_manifest_hash: str = ""
    history_provenance: tuple[tuple[str, str], ...] = ()
    history_fact_provenance: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = ()
    # Keep the original validated PIT series available to the daily-core
    # bridge.  ``as_dict`` exposes only their hashes/provenance; it must not
    # duplicate 127 OHLCV rows into operational reports.
    pit_series: tuple[PointInTimeAdjustedSeries, ...] = ()

    def __post_init__(self) -> None:
        if self.decision_cutoff.tzinfo is None or self.decision_cutoff.utcoffset() is None:
            raise ValueError("comparison decision cutoff must be timezone-aware")
        if self.decision_cutoff.astimezone(_SHANGHAI).date() != self.signal_date:
            raise ValueError("comparison decision cutoff must be on signal_date")
        for field_name in ("eligible_asset_codes", "nonclone_asset_codes"):
            values = getattr(self, field_name)
            if tuple(values) != tuple(sorted(set(values))):
                raise ValueError(f"{field_name} must be sorted and unique")
        if self.history_numerator != len(self.history_eligible_asset_codes):
            raise ValueError("history numerator does not match history asset codes")
        if self.nonclone_denominator != len(self.nonclone_asset_codes):
            raise ValueError("nonclone denominator does not match nonclone asset codes")
        if self.eligible_denominator != len(self.eligible_asset_codes):
            raise ValueError("eligible denominator does not match eligible asset codes")
        series_codes = tuple(item.asset_code for item in self.pit_series)
        if series_codes != tuple(sorted(series_codes)) or len(series_codes) != len(set(series_codes)):
            raise ValueError("pit_series must be sorted and unique")
        if any(code not in self.history_eligible_asset_codes for code in series_codes):
            raise ValueError("pit_series must contain only complete history assets")

    @property
    def adjusted_closes_by_code(self) -> dict[str, tuple[tuple[date, float], ...]]:
        return dict(self.adjusted_closes)

    @property
    def ready_for_signal_filters(self) -> bool:
        return self.history_numerator >= COMPARISON_MIN_NONCLONE_ASSETS

    @property
    def core_snapshot(self) -> CoreComparisonDecisionSnapshot:
        """Return the exact frozen input type consumed by the comparison core."""

        provenance = {
            item[0]: item[1:] for item in self.history_fact_provenance
        }
        rows = tuple(
            ForwardAdjustedClose(
                asset_code=code,
                session_date=session_date,
                adjusted_close=adjusted_close,
                price_basis="total_return_adjusted",
                decision_eligible=True,
                provider=provenance[code][0],
                adjustment_version=provenance[code][1],
                source_hash=(
                    provenance[code][3][
                        next(
                            index
                            for index, item in enumerate(history)
                            if item[0] == session_date
                        )
                    ]
                    if len(provenance[code][3]) == len(history)
                    and all(
                        isinstance(item, str) and len(item) == 64
                        for item in provenance[code][3]
                    )
                    else stable_contract_hash(
                        {
                            "snapshot_source_hash": self.source_hash,
                            "asset_code": code,
                            "session_date": session_date,
                            "adjusted_close": adjusted_close,
                            "series_hash": provenance[code][2],
                        }
                    )
                ),
            )
            for code, history in self.adjusted_closes
            for session_date, adjusted_close in history
        )
        return CoreComparisonDecisionSnapshot(
            signal_date=self.signal_date,
            decision_cutoff=self.decision_cutoff,
            source_hash=self.source_hash,
            eligible_asset_codes=self.eligible_asset_codes,
            nonclone_asset_codes=self.nonclone_asset_codes,
            adjusted_closes=rows,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COMPARISON_INPUT_SCHEMA_VERSION,
            "signal_date": self.signal_date.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "source_hash": self.source_hash,
            "eligible_asset_codes": list(self.eligible_asset_codes),
            "nonclone_asset_codes": list(self.nonclone_asset_codes),
            "adjusted_closes": {
                code: [
                    {"session_date": day.isoformat(), "adjusted_close": close}
                    for day, close in rows
                ]
                for code, rows in self.adjusted_closes
            },
            "history_eligible_asset_codes": list(self.history_eligible_asset_codes),
            "history_missing_asset_codes": list(self.history_missing_asset_codes),
            "eligible_denominator": self.eligible_denominator,
            "nonclone_denominator": self.nonclone_denominator,
            "history_numerator": self.history_numerator,
            "minimum_history_sessions": COMPARISON_REQUIRED_HISTORY_SESSIONS,
            "minimum_nonclone_assets": COMPARISON_MIN_NONCLONE_ASSETS,
            "clone_groups": {group: list(codes) for group, codes in self.clone_groups},
            "clone_representatives": dict(self.clone_representatives),
            "clone_representative_policy": (
                "existing_v2_one_most_liquid_representative_mean_turnover_20_tie_asset_code_desc"
            ),
            "clone_representative_liquidity": [
                {"group": group, "asset_code": code, "mean_turnover_20": value}
                for group, code, value in self.clone_representative_liquidity
            ],
            "clone_mapping_missing_asset_codes": list(self.clone_mapping_missing_asset_codes),
            "exclusions": [list(item) for item in self.exclusions],
            "universe_hash": self.universe_hash,
            "input_hash": self.input_hash,
            "coverage_manifest_hash": self.coverage_manifest_hash,
            "history_provenance": {code: value for code, value in self.history_provenance},
            "history_fact_provenance": {
                code: {
                    "provider": provider,
                    "adjustment_version": adjustment_version,
                    "series_hash": series_hash,
                    "revision_hashes": list(revision_hashes),
                }
                for code, provider, adjustment_version, series_hash, revision_hashes
                in self.history_fact_provenance
            },
            "pit_series": [
                {
                    "asset_code": item.asset_code,
                    "series_hash": item.series_hash,
                    "provider": item.provenance.provider,
                    "adjustment_version": item.provenance.adjustment_version,
                    "revision_hashes": list(item.revision_hashes),
                }
                for item in self.pit_series
            ],
            "signal_filter_gate": (
                "eligible" if self.ready_for_signal_filters else "insufficient_127_history"
            ),
        }


@dataclass(frozen=True)
class V2DayEvidence:
    """A per-date proof that V2 observations and transitions were checked."""

    signal_date: date
    decision_cutoff: datetime
    available: bool
    reason: str
    manifest_hash: str | None = None
    manifest_input_hash: str | None = None
    manifest_decision_cutoff: datetime | None = None
    manifest_data_receipt_cutoff: datetime | None = None
    required_observation_keys: tuple[str, ...] = ()
    observed_observation_keys: tuple[str, ...] = ()
    missing_observation_keys: tuple[str, ...] = ()
    observation_count: int = 0
    screen_observation_count: int = 0
    lifecycle_checked_observation_count: int = 0
    observation_digest: str | None = None
    transition_hashes: tuple[str, ...] = ()
    transition_count: int = 0
    transition_digest: str | None = None
    checked_through_date: date | None = None
    checked_through_cutoff: datetime | None = None
    # This is deliberately separate from ``available``.  A materialized
    # manifest/observation set proves screening only; the day is citable for
    # the comparison after the persisted PIT bars have also gone through the
    # existing lifecycle derivation and its required-set check.
    lifecycle_checked: bool = False
    checked_observation_keys: tuple[str, ...] = ()
    checked_asset_codes: tuple[str, ...] = ()
    held_asset_codes: tuple[str, ...] = ()
    materialization_mode: str = "stored_v2_manifest"
    source_fact_hash: str | None = None
    computed_at: datetime | None = None
    seal_artifact_hash: str | None = None
    core_events: tuple[V2ComparisonEvent, ...] = ()
    # ``observations`` is the union checked on this date.  ``screen`` is the
    # fresh cross-section and ``active_observations`` is the original signal
    # identity carried into the next date's lifecycle pass.
    observations: tuple[V2CandidateObservation, ...] = ()
    screen_observations: tuple[V2CandidateObservation, ...] = ()
    active_observations: tuple[V2CandidateObservation, ...] = ()
    prior_seal_artifact_hash: str | None = None

    @property
    def no_transition_result(self) -> bool:
        return self.transition_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": V2_DAY_CHECK_SCHEMA_VERSION,
            "signal_date": self.signal_date.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "available": self.available,
            "reason": self.reason,
            "manifest_hash": self.manifest_hash,
            "manifest_input_hash": self.manifest_input_hash,
            "manifest_decision_cutoff": (
                self.manifest_decision_cutoff.isoformat()
                if self.manifest_decision_cutoff is not None
                else None
            ),
            "manifest_data_receipt_cutoff": (
                self.manifest_data_receipt_cutoff.isoformat()
                if self.manifest_data_receipt_cutoff is not None
                else None
            ),
            "required_observation_keys": list(self.required_observation_keys),
            "observed_observation_keys": list(self.observed_observation_keys),
            "missing_observation_keys": list(self.missing_observation_keys),
            "observation_count": self.observation_count,
            "screen_observation_count": self.screen_observation_count,
            "lifecycle_checked_observation_count": self.lifecycle_checked_observation_count,
            "observation_digest": self.observation_digest,
            "transition_hashes": list(self.transition_hashes),
            "transition_count": self.transition_count,
            "transition_digest": self.transition_digest,
            "no_transition_result": self.no_transition_result,
            "checked_through_date": (
                self.checked_through_date.isoformat()
                if self.checked_through_date is not None
                else None
            ),
            "checked_through_cutoff": (
                self.checked_through_cutoff.isoformat()
                if self.checked_through_cutoff is not None
                else None
            ),
            "lifecycle_checked": self.lifecycle_checked,
            "checked_observation_keys": list(self.checked_observation_keys),
            "checked_asset_codes": list(self.checked_asset_codes),
            "held_asset_codes": list(self.held_asset_codes),
            "materialization_mode": self.materialization_mode,
            "source_fact_hash": self.source_fact_hash,
            "computed_at": self.computed_at.isoformat() if self.computed_at else None,
            "seal_artifact_hash": self.seal_artifact_hash,
            "observation_keys": [_v2_observation_key(item) for item in self.observations],
            "screen_observation_keys": [
                _v2_observation_key(item) for item in self.screen_observations
            ],
            "active_observation_keys": [
                _v2_observation_key(item) for item in self.active_observations
            ],
            "prior_seal_artifact_hash": self.prior_seal_artifact_hash,
            "core_events": [
                {
                    "signal_date": item.signal_date.isoformat(),
                    "original_signal_date": item.original_signal_date.isoformat(),
                    "asset_code": item.asset_code,
                    "event_type": item.event_type,
                    "score": item.score,
                    "formula_id": item.formula_id,
                    "clone_group": item.clone_group,
                    "source_hash": item.source_hash,
                }
                for item in self.core_events
            ],
        }

    @property
    def core_state_check(self) -> V2StateCheck | None:
        if (
            not self.available
            or not self.lifecycle_checked
            or not self.manifest_hash
            or not self.manifest_input_hash
        ):
            return None
        required_keys = tuple(sorted(set(self.required_observation_keys)))
        checked_keys = tuple(sorted(set(self.checked_observation_keys)))
        event_hashes = tuple(sorted({item.source_hash for item in self.core_events}))
        state_hash = stable_contract_hash(
            {
                "schema_version": V2_DAY_CHECK_SCHEMA_VERSION,
                "session_date": self.signal_date,
                "manifest_hash": self.manifest_hash,
                "input_hash": self.manifest_input_hash,
                "observation_digest": self.observation_digest,
                "transition_digest": self.transition_digest,
                "checked_through": self.checked_through_date,
                "checked_through_cutoff": self.checked_through_cutoff,
                "lifecycle_checked": self.lifecycle_checked,
                "required_observation_keys": required_keys,
                "checked_observation_keys": checked_keys,
                "transition_source_hashes": event_hashes,
            }
        )
        return V2StateCheck(
            session_date=self.signal_date,
            manifest_hash=self.manifest_hash,
            input_hash=self.manifest_input_hash,
            state_hash=state_hash,
            checked_through=self.checked_through_date or self.signal_date,
            observation_count=len(checked_keys),
            transition_count=len(event_hashes),
            checked_through_cutoff=self.checked_through_cutoff,
            required_observation_keys=required_keys,
            checked_observation_keys=checked_keys,
            transition_source_hashes=event_hashes,
            observation_digest=self.observation_digest or stable_contract_hash(()),
            transition_digest=self.transition_digest or stable_contract_hash(()),
            lifecycle_checked=self.lifecycle_checked,
        )


@dataclass(frozen=True)
class ResearchStoreReadiness:
    path: str
    exists: bool
    sqlite_readable: bool
    schema_compatible: bool
    nonempty: bool
    requested_artifact_count: int
    requested_checkpoint_count: int
    invalid_artifact_count: int
    reason: str

    @property
    def ready(self) -> bool:
        return (
            self.exists
            and self.sqlite_readable
            and self.schema_compatible
            and self.nonempty
            and self.invalid_artifact_count == 0
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "sqlite_readable": self.sqlite_readable,
            "schema_compatible": self.schema_compatible,
            "nonempty": self.nonempty,
            "ready": self.ready,
            "requested_artifact_count": self.requested_artifact_count,
            "requested_checkpoint_count": self.requested_checkpoint_count,
            "invalid_artifact_count": self.invalid_artifact_count,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ComparisonPreflightReport:
    start_date: date
    end_date: date
    calendar_hash: str
    snapshots: tuple[ComparisonSnapshotAudit, ...]
    v2_days: tuple[V2DayEvidence, ...]
    research_store: ResearchStoreReadiness
    ready: bool
    reasons: tuple[str, ...]
    elapsed_seconds: float
    truncated: bool = False

    @property
    def decision_snapshots(self) -> tuple[CoreComparisonDecisionSnapshot, ...]:
        """Return the core snapshots without dropping the audit sidecar."""

        return tuple(item.core_snapshot for item in self.snapshots)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COMPARISON_INPUT_SCHEMA_VERSION,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "calendar_hash": self.calendar_hash,
            "ready": self.ready,
            "reasons": list(self.reasons),
            "elapsed_seconds": self.elapsed_seconds,
            "truncated": self.truncated,
            "snapshot_count": len(self.snapshots),
            "snapshots": [item.as_dict() for item in self.snapshots],
            "v2_days": [item.as_dict() for item in self.v2_days],
            "research_store": self.research_store.as_dict(),
        }

    def to_markdown(self) -> str:
        status = "可评估" if self.ready else "等待数据"
        lines = [
            "# ETF 龙头突破与中期动量路线预检",
            "",
            f"状态：**{status}**",
            f"区间：{self.start_date.isoformat()} 至 {self.end_date.isoformat()}，交易日历 hash：`{self.calendar_hash}`",
            "",
            "## 时点资格与历史覆盖",
            "",
            "| 日期 | 资格分母 | 去克隆分母 | 127 根合格分子 | 结论 |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for item in self.snapshots:
            conclusion = "可进入信号筛选" if item.ready_for_signal_filters else "历史不足，阻断信号筛选"
            lines.append(
                f"| {item.signal_date.isoformat()} | {item.eligible_denominator} | "
                f"{item.nonclone_denominator} | {item.history_numerator} | {conclusion} |"
            )
        lines.extend(("", "## V2 按日证据", "", "| 日期 | manifest | observation | transition | 结论 |", "| --- | --- | ---: | ---: | --- |"))
        for item in self.v2_days:
            lines.append(
                f"| {item.signal_date.isoformat()} | `{item.manifest_hash or '暂无'}` | "
                f"{item.observation_count} | {item.transition_count} | "
                f"{'可引用' if item.available else item.reason} |"
            )
        lines.extend(("", "## 研究存储", "", f"路径：`{self.research_store.path}`；{self.research_store.reason}"))
        if self.reasons:
            lines.extend(("", "## 阻断原因", "", *[f"- {reason}" for reason in self.reasons]))
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ResearchPreparationResult:
    status: str
    written_artifact_count: int
    checked_day_count: int
    reasons: tuple[str, ...]
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "written_artifact_count": self.written_artifact_count,
            "checked_day_count": self.checked_day_count,
            "reasons": list(self.reasons),
            "elapsed_seconds": self.elapsed_seconds,
            "research_only": True,
        }


def _normalise_calendar(
    *,
    start_date: date,
    end_date: date,
    trading_sessions: Sequence[date] | None,
) -> tuple[date, ...]:
    if start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    supplied = tuple(trading_sessions) if trading_sessions is not None else ()
    if supplied:
        if supplied != tuple(sorted(set(supplied))):
            raise ValueError("trading_sessions must be unique and chronological")
        try:
            if any(not market_data.is_etf_exchange_trading_day(day) for day in supplied):
                raise ValueError("trading_sessions contains a non-trading date")
        except market_data.ExchangeCalendarUnavailableError as exc:
            raise ValueError("exchange calendar is unavailable for the requested dates") from exc
        calendar = tuple(day for day in supplied if start_date <= day <= end_date)
    else:
        calendar = tuple(
            day
            for ordinal in range((end_date - start_date).days + 1)
            if market_data.is_etf_exchange_trading_day(
                day := date.fromordinal(start_date.toordinal() + ordinal)
            )
        )
    if not calendar:
        raise ValueError("the requested range has no verified trading sessions")
    if len(calendar) > COMPARISON_MAX_DATES:
        raise ValueError(f"date range exceeds {COMPARISON_MAX_DATES} sessions")
    return calendar


def _previous_exchange_session(value: date) -> date | None:
    """Return the prior frozen ETF session when the calendar knows the year."""

    candidate = value - timedelta(days=1)
    for _ in range(14):
        try:
            if market_data.is_etf_exchange_trading_day(candidate):
                return candidate
        except market_data.ExchangeCalendarUnavailableError:
            return None
        candidate -= timedelta(days=1)
    return None


async def _load_all_pit_pages(
    session: AsyncSession,
    *,
    signal_date: date,
    decision_cutoff: datetime,
    required_history_sessions: int,
    page_size: int,
    max_source_rows: int | None,
    max_pages: int,
) -> tuple[PointInTimeEtfDecisionInputSnapshot, ...]:
    if page_size < 1 or page_size > MAX_CODES_PER_REPLAY_INPUT_PAGE:
        raise ValueError("page_size exceeds the PIT reader page bound")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    row_budget = max_source_rows or page_size * required_history_sessions
    if row_budget < page_size * required_history_sessions:
        raise ValueError("max_source_rows is smaller than one complete PIT page")

    async def _wide_price_page(
        page: PointInTimeEtfDecisionInputSnapshot,
    ) -> PointInTimeEtfDecisionInputSnapshot:
        """Re-read at most the comparison window after the 61-bar base check.

        The shared ranking reader intentionally uses one minimum-history gate
        for its score contract.  Clone representative selection has a
        different requirement: a 126-bar representative must remain visible
        even when it cannot enter the 127-bar common pool.  Keep the existing
        validator, but request the wider raw fact window before rebuilding the
        page so the denominator is selected from all factual candidates.
        """

        metadata = tuple(page.authoritative_universe)
        codes = tuple(item.asset_code for item in metadata)
        facts = await market_data.etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=codes,
            replay_date=signal_date,
            rows_per_code=required_history_sessions,
            max_source_rows=(
                max_source_rows or len(codes) * required_history_sessions
            ),
            decision_cutoff=_utc(decision_cutoff),
            compatible_provider_versions=market_data.etf_decision_adjusted_provider_versions(),
        )
        prices_by_code: dict[str, list[Any]] = defaultdict(list)
        for fact in facts:
            prices_by_code[fact.etf_code].append(fact)
        eligible: list[PointInTimeAdjustedSeries] = []
        page_codes = set(codes)
        exclusions = [
            item for item in page.exclusions if item.asset_code not in page_codes
        ]
        for item in metadata:
            result = build_point_in_time_adjusted_series(
                metadata=item,
                rows=prices_by_code[item.asset_code],
                decision_cutoff=_utc(decision_cutoff),
                minimum_history_sessions=REQUIRED_BAR_COUNT,
                maximum_history_sessions=required_history_sessions,
            )
            if isinstance(result, ReplayInputExclusion):
                exclusions.append(result)
            else:
                eligible.append(result)
        page_hash = stable_contract_hash(
            {
                "schema_version": "etf_route_comparison_batched_pit_page_v1",
                "replay_date": signal_date,
                "decision_cutoff": _utc(decision_cutoff),
                "universe_hash": page.universe_hash,
                "series_hashes": tuple(item.series_hash for item in eligible),
            }
        )
        return PointInTimeEtfDecisionInputSnapshot(
            replay_date=page.replay_date,
            decision_cutoff=page.decision_cutoff,
            authoritative_universe=metadata,
            eligible_inputs=tuple(sorted(eligible, key=lambda item: item.asset_code)),
            exclusions=tuple(
                sorted(
                    exclusions,
                    key=lambda item: (item.asset_code or "", item.reason, item.detail),
                )
            ),
            universe_hash=page.universe_hash,
            input_hash=page_hash,
            source_snapshot_hash=page_hash,
            coverage_manifest_hash=stable_contract_hash(
                {
                    "universe_hash": page.universe_hash,
                    "eligible_asset_codes": tuple(item.asset_code for item in eligible),
                    "excluded_count": len(exclusions),
                }
            ),
            page_asset_codes=codes,
            next_code_after=None,
            has_more=False,
        )

    pages: list[PointInTimeEtfDecisionInputSnapshot] = []
    cursor: str | None = None
    for _ in range(max_pages):
        page = await load_point_in_time_etf_decision_inputs(
            session,
            replay_date=signal_date,
            decision_cutoff=decision_cutoff,
            max_source_rows=row_budget,
            code_after=cursor,
            max_codes=page_size,
            # Keep the shared reader's established 61-bar qualification, then
            # widen the factual read below to the full 127-session window.
            required_history_sessions=REQUIRED_BAR_COUNT,
        )
        pages.append(page)
        # The legacy reader repeats a full-universe source watermark and
        # taxonomy lookup for every small page.  Once its first page has
        # supplied the authoritative PIT metadata, use one bounded batch
        # adjusted-price read for the complete universe.  This keeps the
        # causal reader and series validator while avoiding ~100 repeated
        # 16-code queries on the real 1,600-ETF universe.
        if (
            max_source_rows is None
            and max_pages >= 64
            and page.has_more
            and len(page.authoritative_universe) > page_size
        ):
            return (await _wide_price_page(page),)
        if not page.has_more:
            # A small universe does not enter the fast bulk branch above, but
            # still needs the same widened price window and representative
            # policy as a large universe.
            return (await _wide_price_page(page),)
        next_cursor = page.next_code_after
        if not next_cursor or next_cursor == cursor:
            raise RuntimeError("PIT reader returned an invalid pagination cursor")
        cursor = next_cursor
    raise RuntimeError("PIT reader exceeded the bounded page count")


def _build_comparison_snapshot(
    pages: Sequence[PointInTimeEtfDecisionInputSnapshot],
    *,
    signal_date: date,
    decision_cutoff: datetime,
) -> ComparisonSnapshotAudit:
    if not pages:
        raise ValueError("at least one PIT page is required")
    first = pages[0]
    universe = tuple(sorted(first.authoritative_universe, key=lambda item: item.asset_code))
    if len(universe) > COMPARISON_MAX_UNIVERSE_SIZE:
        raise ValueError("PIT universe exceeds the comparison bound")
    metadata_by_code = {item.asset_code: item for item in universe}
    series_by_code: dict[str, PointInTimeAdjustedSeries] = {}
    exclusions: list[tuple[str, str]] = []
    for page in pages:
        if tuple(item.asset_code for item in page.authoritative_universe) != tuple(
            item.asset_code for item in universe
        ):
            raise ValueError("PIT pages disagree on the authoritative universe")
        for series in page.eligible_inputs:
            if series.asset_code not in metadata_by_code:
                raise ValueError("PIT page contains an asset outside the authoritative universe")
            if series.asset_code in series_by_code:
                raise ValueError("PIT pages duplicate an eligible asset")
            series_by_code[series.asset_code] = series
        exclusions.extend(
            (item.asset_code or "", f"{item.reason.value}:{item.detail}")
            for item in page.exclusions
        )

    grouped: dict[str, list[str]] = defaultdict(list)
    missing_mapping: list[str] = []
    for metadata in universe:
        group, reason = _clone_group(metadata)
        grouped[group].append(metadata.asset_code)
        if reason is not None:
            missing_mapping.append(metadata.asset_code)
            exclusions.append((metadata.asset_code, reason))
    clone_groups = tuple((group, tuple(sorted(codes))) for group, codes in sorted(grouped.items()))
    # An unmapped asset is eligible membership, but it is not a proven
    # non-clone representative.  Keeping it as ``asset:<code>`` in the audit
    # grouping makes the missing mapping visible without allowing it into the
    # 127-session denominator or numerator.
    mapped_groups = tuple(
        (group, codes)
        for group, codes in clone_groups
        if not group.startswith("asset:")
    )
    representative_rows: list[tuple[str, str, float]] = []
    representative_pairs: list[tuple[str, str]] = []
    for group, codes in mapped_groups:
        liquidity: list[tuple[str, float]] = []
        for code in codes:
            series = series_by_code.get(code)
            if series is None or len(series.bars) < 20:
                continue
            amounts = tuple(_valid_nonnegative_float(bar.turnover) for bar in series.bars[-20:])
            if any(value is None for value in amounts):
                continue
            liquidity.append((code, sum(value for value in amounts if value is not None) / 20.0))
        if not liquidity:
            exclusions.append((group, "clone_representative_missing_cutoff_visible_turnover_20"))
            continue
        representative, mean_turnover = max(liquidity, key=lambda item: (item[1], item[0]))
        representative_pairs.append((group, representative))
        representative_rows.append((group, representative, mean_turnover))
        for code in codes:
            if code != representative:
                exclusions.append(
                    (code, f"clone_representative_excluded:{group}:{representative}")
                )
    representatives = tuple(sorted(representative_pairs))
    nonclone_codes = tuple(sorted(code for _, code in representatives))
    complete: list[str] = []
    missing: list[str] = []
    adjusted: list[tuple[str, tuple[tuple[date, float], ...]]] = []
    provenance: list[tuple[str, str]] = []
    fact_provenance: list[tuple[str, str, str, str, tuple[str, ...]]] = []
    pit_series: list[PointInTimeAdjustedSeries] = []
    for code in nonclone_codes:
        series = series_by_code.get(code)
        if series is None:
            missing.append(code)
            exclusions.append((code, "insufficient_adjusted_history_127:reader_excluded_asset"))
            continue
        bars = tuple(series.bars)
        if len(bars) != COMPARISON_REQUIRED_HISTORY_SESSIONS:
            missing.append(code)
            exclusions.append(
                (code, f"insufficient_adjusted_history_127:received_{len(bars)}")
            )
            continue
        sessions = tuple(bar.session_date for bar in bars)
        closes = tuple(_valid_float(bar.adjusted_close) for bar in bars)
        if sessions != tuple(sorted(set(sessions))) or sessions[-1] != signal_date:
            missing.append(code)
            exclusions.append((code, "insufficient_adjusted_history_127:non_contiguous_or_stale"))
            continue
        if any(close is None for close in closes):
            missing.append(code)
            exclusions.append((code, "ineligible_adjusted_close"))
            continue
        if series.synchronized_after_cutoff or _utc(series.latest_source_timestamp) > _utc(decision_cutoff):
            missing.append(code)
            exclusions.append((code, "history_observed_after_cutoff"))
            continue
        rows = tuple((bar.session_date, float(bar.adjusted_close)) for bar in bars)
        complete.append(code)
        adjusted.append((code, rows))
        provenance.append((code, stable_contract_hash(_history_payload(series))))
        fact_provenance.append(
            (
                code,
                str(series.provenance.provider),
                str(series.provenance.adjustment_version),
                series.series_hash,
                tuple(series.revision_hashes),
            )
        )
        pit_series.append(series)

    page_hashes = [
        {
            "source_snapshot_hash": page.source_snapshot_hash,
            "input_hash": page.input_hash,
            "coverage_manifest_hash": page.coverage_manifest_hash,
            "page_asset_codes": list(page.page_asset_codes),
        }
        for page in pages
    ]
    source_hash = stable_contract_hash(
        {
            "schema_version": COMPARISON_INPUT_SCHEMA_VERSION,
            "signal_date": signal_date,
            "decision_cutoff": _utc(decision_cutoff),
            "universe_hash": first.universe_hash,
            "pages": page_hashes,
            "membership_fact_hashes": [
                (item.asset_code, item.membership_fact_hash) for item in universe
            ],
            "clone_groups": clone_groups,
            "representatives": representatives,
            "series": [
                (code, stable_contract_hash(_history_payload(series)))
                for code, series in sorted(series_by_code.items())
            ],
        }
    )
    exclusion_tuple = tuple(sorted(set(exclusions), key=lambda item: (item[0], item[1])))
    return ComparisonSnapshotAudit(
        signal_date=signal_date,
        decision_cutoff=_utc(decision_cutoff),
        source_hash=source_hash,
        eligible_asset_codes=tuple(sorted(metadata_by_code)),
        nonclone_asset_codes=nonclone_codes,
        adjusted_closes=tuple(sorted(adjusted)),
        history_eligible_asset_codes=tuple(sorted(complete)),
        history_missing_asset_codes=tuple(sorted(set(missing) | set(missing_mapping))),
        eligible_denominator=len(metadata_by_code),
        nonclone_denominator=len(nonclone_codes),
        history_numerator=len(complete),
        clone_groups=clone_groups,
        clone_representatives=representatives,
        clone_representative_liquidity=tuple(sorted(representative_rows)),
        clone_mapping_missing_asset_codes=tuple(sorted(missing_mapping)),
        exclusions=exclusion_tuple,
        universe_hash=first.universe_hash,
        input_hash=stable_contract_hash(page_hashes),
        coverage_manifest_hash=stable_contract_hash(
            [page.coverage_manifest_hash for page in pages]
        ),
        history_provenance=tuple(sorted(provenance)),
        history_fact_provenance=tuple(sorted(fact_provenance)),
        pit_series=tuple(sorted(pit_series, key=lambda item: item.asset_code)),
    )


async def load_comparison_decision_snapshot(
    session: AsyncSession,
    *,
    signal_date: date,
    decision_cutoff: datetime,
    page_size: int = MAX_CODES_PER_REPLAY_INPUT_PAGE,
    max_source_rows: int | None = None,
    required_history_sessions: int = COMPARISON_REQUIRED_HISTORY_SESSIONS,
    max_pages: int = 128,
) -> CoreComparisonDecisionSnapshot:
    """Load the exact frozen snapshot type consumed by the core."""

    audit = await load_comparison_decision_snapshot_audit(
        session,
        signal_date=signal_date,
        decision_cutoff=decision_cutoff,
        page_size=page_size,
        max_source_rows=max_source_rows,
        required_history_sessions=required_history_sessions,
        max_pages=max_pages,
    )
    return audit.core_snapshot


async def load_comparison_decision_snapshot_audit(
    session: AsyncSession,
    *,
    signal_date: date,
    decision_cutoff: datetime,
    page_size: int = MAX_CODES_PER_REPLAY_INPUT_PAGE,
    max_source_rows: int | None = None,
    required_history_sessions: int = COMPARISON_REQUIRED_HISTORY_SESSIONS,
    max_pages: int = 128,
) -> ComparisonSnapshotAudit:
    """Load one snapshot together with denominator and exclusion evidence."""

    if required_history_sessions != COMPARISON_REQUIRED_HISTORY_SESSIONS:
        raise ValueError("the comparison history window is fixed at 127 sessions")
    pages = await _load_all_pit_pages(
        session,
        signal_date=signal_date,
        decision_cutoff=decision_cutoff,
        required_history_sessions=required_history_sessions,
        page_size=page_size,
        max_source_rows=max_source_rows,
        max_pages=max_pages,
    )
    return _build_comparison_snapshot(
        pages,
        signal_date=signal_date,
        decision_cutoff=decision_cutoff,
    )


async def load_v2_comparison_inputs(
    session: AsyncSession,
    *,
    snapshot: CoreComparisonDecisionSnapshot,
    eligible_codes: Sequence[str] | None = None,
    page_size: int = 64,
) -> V2EtfInputBundle:
    """Read V2's persisted ETF inputs for one identical PIT date.

    This is intentionally a reader only.  Materialization and lifecycle
    persistence remain explicit research operations owned by the V2 bridge.
    """

    return await read_etf_v2_asset_inputs(
        session,
        replay_date=snapshot.signal_date,
        decision_cutoff=snapshot.decision_cutoff,
        identity_cutoff=snapshot.decision_cutoff,
        eligible_codes=(
            tuple(sorted(set(eligible_codes)))
            if eligible_codes is not None
            else snapshot.eligible_asset_codes
        ),
        page_size=page_size,
    )


def _sha256_text(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


async def load_comparison_valuation_input(
    session: AsyncSession,
    *,
    trading_sessions: Sequence[date],
    asset_codes: Sequence[str],
    decision_cutoff: datetime,
    page_size: int = MAX_CODES_PER_REPLAY_INPUT_PAGE,
) -> ComparisonValuationInput:
    """Read the independent valuation stream from persisted adjusted facts.

    Decision snapshots are never reused here.  The caller supplies the
    valuation horizon and asset set, while this reader applies the same
    provider/version and recorded-visibility cutoff as the PIT reader.  A
    missing fact remains missing so the existing ledger can stop at its true
    unavailable interval.
    """

    sessions = _normalise_calendar(
        start_date=min(trading_sessions),
        end_date=max(trading_sessions),
        trading_sessions=trading_sessions,
    )
    codes = tuple(sorted(set(str(code).strip() for code in asset_codes if str(code).strip())))
    if page_size < 1 or page_size > MAX_CODES_PER_REPLAY_INPUT_PAGE:
        raise ValueError("valuation page_size exceeds the PIT reader bound")
    if not codes:
        raise ValueError("valuation requires at least one asset code")
    rows: list[ForwardAdjustedClose] = []
    rows_per_code = len(sessions)
    cutoff = _utc(decision_cutoff)
    compatible = market_data.etf_decision_adjusted_provider_versions()
    for start in range(0, len(codes), page_size):
        page = codes[start : start + page_size]
        facts = await market_data.etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=page,
            replay_date=sessions[-1],
            rows_per_code=rows_per_code,
            max_source_rows=len(page) * rows_per_code,
            decision_cutoff=cutoff,
            compatible_provider_versions=compatible,
        )
        for fact in facts:
            if fact.trade_date not in sessions:
                continue
            adjusted = _valid_float(fact.adjusted_close)
            provider = str(fact.data_provider or "").strip()
            version = str(fact.adjustment_version or fact.provider_version or "").strip()
            if adjusted is None or not provider or not version or fact.decision_eligible is not True:
                continue
            source_hash = (
                str(fact.revision_hash)
                if _sha256_text(fact.revision_hash)
                else stable_contract_hash(
                    {
                        "asset_code": fact.etf_code,
                        "trade_date": fact.trade_date,
                        "adjusted_close": adjusted,
                        "provider": provider,
                        "provider_version": fact.provider_version,
                        "adjustment_version": fact.adjustment_version,
                        "source_timestamp": fact.source_timestamp,
                    }
                )
            )
            rows.append(
                ForwardAdjustedClose(
                    asset_code=fact.etf_code,
                    session_date=fact.trade_date,
                    adjusted_close=adjusted,
                    price_basis="total_return_adjusted",
                    decision_eligible=True,
                    provider=provider,
                    adjustment_version=version,
                    source_hash=source_hash,
                )
            )
    return ComparisonValuationInput(
        trading_sessions=sessions,
        adjusted_closes=tuple(
            sorted(rows, key=lambda row: (row.asset_code, row.session_date))
        ),
        calendar_sessions=sessions,
        calendar_complete_through=sessions[-1],
    )


def build_comparison_input(
    *,
    provenance: FrozenComparisonProvenance,
    start_date: date,
    end_date: date,
    valuation: ComparisonValuationInput,
    snapshots: Sequence[ComparisonSnapshotAudit],
    v2_days: Sequence[V2DayEvidence],
    daily_core_targets: Sequence[Any] = (),
    daily_core_required_signal_dates: Sequence[date] = (),
    initial_capital: float = 1.0,
) -> ComparisonInput:
    """Assemble the core's frozen input contract from loader sidecars."""

    return ComparisonInput(
        provenance=provenance,
        start_date=start_date,
        end_date=end_date,
        valuation=valuation,
        decision_snapshots=tuple(item.core_snapshot for item in snapshots),
        v2_events=tuple(
            event
            for day in v2_days
            if day.available
            for event in day.core_events
        ),
        v2_state_checks=tuple(
            check
            for day in v2_days
            if (check := day.core_state_check) is not None
        ),
        daily_core_targets=tuple(daily_core_targets),
        daily_core_required_signal_dates=tuple(daily_core_required_signal_dates),
        initial_capital=initial_capital,
    )


def _manifest_candidate_query() -> str:
    return """
        SELECT m.id, m.manifest_hash, m.universe, m.decision_cutoff,
               m.data_receipt_cutoff, m.input_hash, m.source_registry_hash,
               m.formula_registry_hash, m.code_version, m.holdout_identity,
               m.status, m.research_only, m.provider_health_json,
               m.exclusions_json, m.manifest_payload_json, m.created_at
        FROM leader_tactics_v2_run_manifests m
        WHERE m.universe = :universe
          AND m.status = 'materialized'
          AND m.decision_cutoff <= :decision_cutoff
          AND m.data_receipt_cutoff <= :decision_cutoff
          AND EXISTS (
              SELECT 1
              FROM leader_tactics_v2_candidate_observations o
              WHERE o.manifest_hash = m.manifest_hash
                AND o.universe = :universe
                AND o.signal_date = :signal_date
          )
        ORDER BY m.decision_cutoff DESC, m.created_at DESC, m.id DESC
    """


async def _read_v2_manifest(
    session: AsyncSession,
    *,
    signal_date: date,
    decision_cutoff: datetime,
) -> Mapping[str, Any] | None:
    result = await session.execute(
        text(_manifest_candidate_query()),
        {
            "universe": "etf",
            "signal_date": signal_date,
            "decision_cutoff": _db_cutoff(decision_cutoff),
        },
    )
    row = result.mappings().first()
    if row is None:
        return None
    _validate_materialized_manifest(row)
    return row


async def _read_v2_observations(
    session: AsyncSession,
    *,
    manifest_hash: str | None,
    signal_date: date,
    decision_cutoff: datetime,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    result = await session.execute(
        text(
            """
            SELECT id, manifest_hash, universe, asset_code, asset_name, theme,
                   sector, tracked_index, formula_id, state,
                   availability, qualifies, score, signal_date, source_cutoff,
                   gate_facts_json, exclusion_reasons_json, provenance_json,
                   feature_hash, created_at
            FROM leader_tactics_v2_candidate_observations
            WHERE (:manifest_hash IS NULL OR manifest_hash = :manifest_hash)
              AND universe = 'etf'
              AND signal_date <= :signal_date
              AND source_cutoff <= :decision_cutoff
              AND created_at <= :decision_cutoff
            ORDER BY asset_code, formula_id, id
            """
        ),
        {
            "manifest_hash": manifest_hash,
            "signal_date": signal_date,
            "decision_cutoff": _db_cutoff(decision_cutoff),
        },
    )
    rows = tuple(result.mappings().all())
    return tuple(
        (
            f"{row['asset_code']}:{_as_date(row['signal_date']).isoformat() if _as_date(row['signal_date']) else 'unknown'}:{row['formula_id']}",
            row,
        )
        for row in rows
    )


async def _read_v2_transitions(
    session: AsyncSession,
    *,
    manifest_hash: str | None,
    signal_date: date,
    decision_cutoff: datetime,
) -> tuple[Mapping[str, Any], ...]:
    result = await session.execute(
        text(
            """
            SELECT id, asset_code, formula_id, signal_date, from_state,
                   to_state, transition_date, payload_json, transition_hash,
                   created_at, evidence_cutoff, projected_entry_status
            FROM leader_tactics_v2_state_transitions
            WHERE (:manifest_hash IS NULL OR manifest_hash = :manifest_hash)
              AND universe = 'etf'
              AND transition_date <= :signal_date
              AND created_at <= :decision_cutoff
              AND (evidence_cutoff IS NULL OR evidence_cutoff <= :decision_cutoff)
            ORDER BY transition_date, asset_code, formula_id, id
            """
        ),
        {
            "manifest_hash": manifest_hash,
            "signal_date": signal_date,
            "decision_cutoff": _db_cutoff(decision_cutoff),
        },
    )
    rows = tuple(result.mappings().all())
    visible: list[Mapping[str, Any]] = []
    cutoff = _db_cutoff(decision_cutoff)
    for row in rows:
        transition_date = _as_date(row.get("transition_date"))
        if transition_date is None or transition_date > signal_date:
            continue
        created_at = _as_datetime(row.get("created_at"))
        if created_at is not None and _db_cutoff(created_at) > cutoff:
            continue
        evidence_cutoff = _as_datetime(row.get("evidence_cutoff"))
        if evidence_cutoff is not None and _db_cutoff(evidence_cutoff) > cutoff:
            continue
        visible.append(row)
    return tuple(visible)


def _v2_events_from_transitions(
    *,
    observations: Sequence[tuple[str, Mapping[str, Any]]],
    transitions: Sequence[Mapping[str, Any]],
    event_date: date | None = None,
) -> tuple[V2ComparisonEvent, ...]:
    """Adapt persisted rows through the core's canonical V2 adapter.

    The old implementation reconstructed events from a few scalar columns and
    silently discarded malformed rows.  That could turn a missing original
    signal observation into a false no-event day.  Deserialize the immutable
    payloads and let ``build_v2_comparison_events`` validate the observation,
    original signal identity, feature hash, transition hash and clone group.
    """

    parsed_observations = tuple(_v2_observation_from_row(row) for _, row in observations)
    parsed_transitions = tuple(
        _v2_transition_from_row(row)
        for row in transitions
        if str(row.get("formula_id") or "") == BREAKOUT_V2
        and (
            event_date is None
            or _as_date(row.get("transition_date")) == event_date
        )
    )
    return build_v2_comparison_events(
        observations=parsed_observations,
        transitions=parsed_transitions,
    )


def _artifact_hash(*, run_id: str, phase: str, item_key: str, payload: Mapping[str, Any]) -> str:
    return stable_contract_hash(
        {
            "schema_version": GENERIC_RESEARCH_ARTIFACT_SCHEMA_VERSION,
            "run_id": run_id,
            "phase": phase,
            "item_key": item_key,
            "payload": dict(payload),
        }
    )


def _store_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_dir():
        return path / "production-pit-research.sqlite3"
    return path


def inspect_research_store(
    path: str | Path,
    *,
    run_id: str | None = None,
    phase: str = V2_DAY_CHECK_PHASE,
) -> ResearchStoreReadiness:
    """Inspect a configured SQLite store without creating or modifying it."""

    resolved = _store_path(path)
    if not resolved.exists():
        return ResearchStoreReadiness(
            str(resolved), False, False, False, False, 0, 0, 0, "研究 store 不存在"
        )
    if not resolved.is_file():
        return ResearchStoreReadiness(
            str(resolved), True, False, False, False, 0, 0, 0, "研究 store 路径不是文件"
        )
    uri = f"file:{resolved.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {"research_artifacts", "pipeline_checkpoints"}
            schema_compatible = required <= tables
            if not schema_compatible:
                return ResearchStoreReadiness(
                    str(resolved), True, True, False, False, 0, 0, 0,
                    "研究 store 缺少既有 artifact/checkpoint 表",
                )
            if run_id is None:
                artifact_count = int(
                    connection.execute("SELECT COUNT(*) FROM research_artifacts").fetchone()[0]
                )
                checkpoint_count = int(
                    connection.execute("SELECT COUNT(*) FROM pipeline_checkpoints").fetchone()[0]
                )
                artifact_query = connection.execute(
                    "SELECT run_id, phase, item_key, artifact_hash, payload_json "
                    "FROM research_artifacts ORDER BY rowid"
                )
            else:
                artifact_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM research_artifacts WHERE run_id=? AND phase=?",
                        (run_id, phase),
                    ).fetchone()[0]
                )
                checkpoint_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM pipeline_checkpoints WHERE run_id=?",
                        (run_id,),
                    ).fetchone()[0]
                )
                artifact_query = connection.execute(
                    """
                    SELECT run_id, phase, item_key, artifact_hash, payload_json
                    FROM research_artifacts WHERE run_id=? AND phase=?
                    ORDER BY rowid
                    """,
                    (run_id, phase),
                )
            invalid_count = 0
            if run_id is not None:
                # Keep the same bounded cursor path for a requested namespace;
                # ``fetchall`` here would make a large replay run unbounded.
                artifact_query = connection.execute(
                    """
                    SELECT run_id, phase, item_key, artifact_hash, payload_json
                    FROM research_artifacts WHERE run_id=? AND phase=?
                    ORDER BY rowid
                    """,
                    (run_id, phase),
                )
            while True:
                rows = artifact_query.fetchmany(256)
                if not rows:
                    break
                for stored_run_id, stored_phase, item_key, stored_hash, raw_payload in rows:
                    try:
                        payload = json.loads(str(raw_payload))
                        if not isinstance(payload, dict) or _artifact_hash(
                            run_id=str(stored_run_id),
                            phase=str(stored_phase),
                            item_key=str(item_key),
                            payload=payload,
                        ) != str(stored_hash):
                            invalid_count += 1
                    except (TypeError, ValueError, json.JSONDecodeError):
                        invalid_count += 1
            nonempty = artifact_count > 0 or checkpoint_count > 0
            global_nonempty = nonempty
            if run_id is not None and not nonempty:
                global_nonempty = bool(
                    int(
                        connection.execute(
                            "SELECT COUNT(*) FROM research_artifacts"
                        ).fetchone()[0]
                    )
                    or int(
                        connection.execute(
                            "SELECT COUNT(*) FROM pipeline_checkpoints"
                        ).fetchone()[0]
                    )
                )
            reason = (
                "研究 store 可读且已有内容"
                if nonempty and invalid_count == 0
                else "研究 store 为空，不能视为就绪"
                if not nonempty and not global_nonempty
                else "本比较命名空间尚无封存结果；共享研究 store 仍有其他内容"
                if not nonempty
                else "研究 artifact hash 或 JSON 校验失败"
            )
            return ResearchStoreReadiness(
                str(resolved), True, True, True, nonempty, artifact_count,
                checkpoint_count, invalid_count, reason,
            )
    except (OSError, sqlite3.Error) as exc:
        return ResearchStoreReadiness(
            str(resolved), True, False, False, False, 0, 0, 0,
            f"研究 store 不可读：{type(exc).__name__}",
        )


def _read_sealed_day_check(
    path: str | Path,
    *,
    run_id: str,
    phase: str,
    item_key: str,
) -> tuple[dict[str, Any], str] | None:
    resolved = _store_path(path)
    if not resolved.exists():
        return None
    uri = f"file:{resolved.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                """
                SELECT artifact_hash, payload_json FROM research_artifacts
                WHERE run_id=? AND phase=? AND item_key=?
                """,
                (run_id, phase, item_key),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        payload = json.loads(str(row[1]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    stored_hash = str(row[0])
    if _artifact_hash(run_id=run_id, phase=phase, item_key=item_key, payload=payload) != stored_hash:
        return None
    return payload, stored_hash


def _sealed_v2_day_dates(
    path: str | Path,
    *,
    run_id: str,
    phase: str,
) -> tuple[date, ...]:
    """Return only this run's sealed V2 day keys for resume-gap checks."""

    resolved = _store_path(path)
    if not resolved.exists():
        return ()
    uri = f"file:{resolved.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(
                """
                SELECT item_key FROM research_artifacts
                WHERE run_id=? AND phase=? AND item_key LIKE 'v2-day-check:%'
                ORDER BY item_key
                """,
                (run_id, phase),
            ).fetchall()
    except sqlite3.Error:
        return ()
    dates: list[date] = []
    for (item_key,) in rows:
        try:
            dates.append(date.fromisoformat(str(item_key).split(":", 1)[1]))
        except (IndexError, TypeError, ValueError):
            continue
    return tuple(sorted(set(dates)))


def _read_prior_rebuilt_observations(
    path: str | Path,
    *,
    run_id: str,
    phase: str,
    signal_date: date,
) -> tuple[tuple[V2CandidateObservation, ...], str] | None:
    """Load only the prior sealed active identities for lifecycle continuation."""

    sealed = _read_sealed_day_check(
        path,
        run_id=run_id,
        phase=phase,
        item_key=f"v2-day-check:{signal_date.isoformat()}",
    )
    if sealed is None:
        return None
    payload, artifact_hash = sealed
    if (
        payload.get("materialization_mode") != "pit_rebuild_research_artifact_v1"
        or payload.get("lifecycle_checked") is not True
        or not isinstance(payload.get("active_observations"), list)
        or not isinstance(payload.get("observations"), list)
        or not isinstance(payload.get("screen_observations"), list)
    ):
        return None
    try:
        observations = _merge_v2_observations(
            tuple(
                _v2_observation_from_payload(item)
                for item in payload["observations"]
                if isinstance(item, Mapping)
            )
        )
        screen_observations = _merge_v2_observations(
            tuple(
                _v2_observation_from_payload(item)
                for item in payload["screen_observations"]
                if isinstance(item, Mapping)
            )
        )
        active = _merge_v2_observations(
            tuple(
                _v2_observation_from_payload(item)
                for item in payload["active_observations"]
                if isinstance(item, Mapping)
            )
        )
        signal_date = _as_date(payload.get("signal_date"))
        checked_through_date = _as_date(payload.get("checked_through_date"))
        checked_through_cutoff = _as_datetime(payload.get("checked_through_cutoff"))
        observation_keys = tuple(_v2_observation_key(item) for item in observations)
        screen_keys = tuple(_v2_observation_key(item) for item in screen_observations)
        checked = tuple(str(item) for item in payload.get("checked_observation_keys", ()))
        required = tuple(str(item) for item in payload.get("required_observation_keys", ()))
        observed = tuple(str(item) for item in payload.get("observed_observation_keys", ()))
        missing = tuple(str(item) for item in payload.get("missing_observation_keys", ()))
        active_keys = tuple(_v2_observation_key(item) for item in active)
        transition_hashes = tuple(str(item) for item in payload.get("transition_hashes", ()))
        events = tuple(
            V2ComparisonEvent(
                signal_date=date.fromisoformat(str(item["signal_date"])),
                original_signal_date=date.fromisoformat(str(item["original_signal_date"])),
                asset_code=str(item["asset_code"]),
                event_type=str(item["event_type"]),  # type: ignore[arg-type]
                score=float(item["score"]),
                formula_id=str(item["formula_id"]),
                clone_group=str(item["clone_group"]),
                source_hash=str(item["source_hash"]),
            )
            for item in payload.get("core_events", ())
            if isinstance(item, Mapping)
        )
        observation_digest = stable_contract_hash(
            [item.canonical_payload() | {"feature_hash": item.feature_hash} for item in observations]
        )
        expected_checked_assets = tuple(sorted({item.asset_code for item in observations}))
        if (
            signal_date is None
            or checked_through_date != signal_date
            or checked_through_cutoff is None
            or checked_through_cutoff.date() < signal_date
            or tuple(sorted(set(required))) != required
            or tuple(sorted(set(observed))) != observed
            or tuple(sorted(set(checked))) != checked
            or observed != tuple(sorted(observation_keys))
            or checked != observed
            or required != observed
            or missing != tuple(sorted(set(required) - set(observed)))
            or tuple(sorted(screen_keys)) != screen_keys
            or any(item.signal_date != signal_date for item in screen_observations)
            or any(key not in set(observed) for key in active_keys)
            or int(payload.get("observation_count", -1)) != len(observations)
            or int(payload.get("screen_observation_count", -1)) != len(screen_observations)
            or int(payload.get("lifecycle_checked_observation_count", -1)) != len(observations)
            or str(payload.get("observation_digest") or "") != observation_digest
            or tuple(sorted(set(transition_hashes))) != transition_hashes
            or int(payload.get("transition_count", -1)) != len(transition_hashes)
            or tuple(sorted(str(item.source_hash) for item in events)) != transition_hashes
            or any(item.signal_date != signal_date for item in events)
            or tuple(str(item) for item in payload.get("checked_asset_codes", ()))
            != expected_checked_assets
            or not _sha256_text(payload.get("transition_digest"))
            or not _sha256_text(payload.get("source_fact_hash"))
            or not _sha256_text(payload.get("manifest_hash"))
            or not _sha256_text(payload.get("manifest_input_hash"))
            or not _as_datetime(payload.get("computed_at"))
        ):
            return None
        return active, artifact_hash
    except (TypeError, ValueError, V2ContractError):
        return None


def _v2_marker_payload(
    *,
    snapshot: CoreComparisonDecisionSnapshot,
    evidence: V2DayEvidence,
) -> dict[str, Any]:
    return {
        "schema_version": V2_DAY_CHECK_SCHEMA_VERSION,
        "signal_date": snapshot.signal_date.isoformat(),
        "decision_cutoff": snapshot.decision_cutoff.isoformat(),
        "snapshot_source_hash": snapshot.source_hash,
        "manifest_hash": evidence.manifest_hash,
        "manifest_input_hash": evidence.manifest_input_hash,
        "required_observation_keys": list(evidence.required_observation_keys),
        "observed_observation_keys": list(evidence.observed_observation_keys),
        "missing_observation_keys": list(evidence.missing_observation_keys),
        "observation_count": evidence.observation_count,
        "screen_observation_count": evidence.screen_observation_count,
        "lifecycle_checked_observation_count": evidence.lifecycle_checked_observation_count,
        "observation_digest": evidence.observation_digest,
        "transition_hashes": list(evidence.transition_hashes),
        "transition_count": evidence.transition_count,
        "transition_digest": evidence.transition_digest,
        "checked_through_date": snapshot.signal_date.isoformat(),
        "checked_through_cutoff": snapshot.decision_cutoff.isoformat(),
        "lifecycle_checked": evidence.lifecycle_checked,
        "checked_observation_keys": list(evidence.checked_observation_keys),
        "checked_asset_codes": list(evidence.checked_asset_codes),
        "held_asset_codes": list(evidence.held_asset_codes),
        "materialization_mode": evidence.materialization_mode,
        "source_fact_hash": evidence.source_fact_hash,
        "computed_at": evidence.computed_at.isoformat() if evidence.computed_at else None,
        "no_transition_result": evidence.no_transition_result,
        "prior_seal_artifact_hash": evidence.prior_seal_artifact_hash,
        "observations": [
            _v2_observation_payload(item) for item in evidence.observations
        ],
        "screen_observations": [
            _v2_observation_payload(item) for item in evidence.screen_observations
        ],
        "active_observations": [
            _v2_observation_payload(item) for item in evidence.active_observations
        ],
        "core_events": [
            {
                "signal_date": item.signal_date.isoformat(),
                "original_signal_date": item.original_signal_date.isoformat(),
                "asset_code": item.asset_code,
                "event_type": item.event_type,
                "score": item.score,
                "formula_id": item.formula_id,
                "clone_group": item.clone_group,
                "source_hash": item.source_hash,
            }
            for item in evidence.core_events
        ],
    }


def _rebuild_source_fact_hash(bundle: V2EtfInputBundle) -> str:
    return stable_contract_hash(
        {
            "schema_version": "etf_route_comparison_v2_rebuild_source_v1",
            "signal_date": bundle.signal_date,
            "source_cutoff": bundle.source_cutoff,
            "identity_cutoff": bundle.identity_cutoff,
            "inputs": [
                {
                    "asset_code": item.asset_code,
                    "membership_fact_hash": item.membership.fact_hash
                    if item.membership is not None
                    else None,
                    "bars": [
                        {
                            "trade_date": bar.trade_date,
                            "adjusted_close": bar.adjusted_close,
                            "adjusted_high": bar.adjusted_high,
                            "amount": bar.amount,
                            "revision_id": bar.revision_id,
                            "observed_at": bar.observed_at,
                        }
                        for bar in item.bars
                    ],
                }
                for item in sorted(bundle.inputs, key=lambda value: value.asset_code)
            ],
        }
    )


def _rebuild_context_source_fact_hash(
    bundle: V2EtfInputBundle,
    *,
    prior_observations: Sequence[V2CandidateObservation],
    prior_seal_artifact_hash: str | None,
) -> str:
    return stable_contract_hash(
        {
            "bundle_source_fact_hash": _rebuild_source_fact_hash(bundle),
            "prior_seal_artifact_hash": prior_seal_artifact_hash,
            "prior_observation_hash": stable_contract_hash(
                [_v2_observation_payload(item) for item in prior_observations]
            ),
        }
    )


def _build_rebuilt_v2_evidence(
    *,
    snapshot: ComparisonSnapshotAudit,
    bundle: V2EtfInputBundle,
    screen: Any,
    prior_observations: Sequence[V2CandidateObservation] = (),
    prior_seal_artifact_hash: str | None = None,
    transitions: Sequence[V2LifecycleTransition],
) -> V2DayEvidence:
    screen_observations = _merge_v2_observations(tuple(screen.observations))
    observations = _merge_v2_observations((*prior_observations, *screen_observations))
    current_required_keys = {
        f"{code}:{snapshot.signal_date.isoformat()}:{formula}"
        for code in snapshot.eligible_asset_codes
        for formula in _COMPARISON_FORMULAS
    }
    prior_required_keys = {_v2_observation_key(item) for item in prior_observations}
    required_keys = tuple(sorted(current_required_keys | prior_required_keys))
    observed_keys = tuple(sorted(_v2_observation_key(item) for item in observations))
    screen_keys = {_v2_observation_key(item) for item in screen_observations}
    missing_keys = tuple(sorted((current_required_keys - screen_keys) | (prior_required_keys - set(observed_keys))))
    observation_digest = stable_contract_hash(
        [item.canonical_payload() | {"feature_hash": item.feature_hash} for item in observations]
    )
    unique_transitions: dict[str, V2LifecycleTransition] = {}
    for transition in transitions:
        previous = unique_transitions.get(transition.transition_hash)
        if previous is not None and previous != transition:
            return V2DayEvidence(
                signal_date=snapshot.signal_date,
                decision_cutoff=snapshot.decision_cutoff,
                available=False,
                reason="v2_rebuilt_transition_hash_collision",
                manifest_hash=screen.manifest_hash,
                manifest_input_hash=screen.input_hash,
            )
        unique_transitions[transition.transition_hash] = transition
    transitions = tuple(
        unique_transitions[key] for key in sorted(unique_transitions)
    )
    transition_digest = stable_contract_hash([asdict(item) for item in transitions])
    # A daily evidence record carries the full lifecycle proof for the
    # checked state, while its comparison events are only the transitions
    # caused on this date.  Replaying the complete causal history here would
    # emit yesterday's confirmation again on every later date.
    daily_transitions = tuple(
        item for item in transitions if item.transition_date == snapshot.signal_date
    )
    try:
        events = build_v2_comparison_events(
            observations=observations,
            transitions=daily_transitions,
        )
    except (TypeError, ValueError, V2ContractError) as exc:
        return V2DayEvidence(
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
            available=False,
            reason=f"v2_rebuilt_transition_invalid:{type(exc).__name__}",
            manifest_hash=screen.manifest_hash,
            manifest_input_hash=screen.input_hash,
        )
    latest: dict[tuple[str, str, date], V2LifecycleTransition] = {}
    for item in transitions:
        key = (item.asset_code, item.formula_id, item.signal_date)
        current = latest.get(key)
        if current is None or (item.transition_date, item.transition_hash) > (
            current.transition_date,
            current.transition_hash,
        ):
            latest[key] = item
    active_keys = {
        key
        for key, item in latest.items()
        if item.to_state in {"turning_watch", "preparing", "confirmed"}
    }
    active_observations = tuple(
        item for item in observations if _v2_observation_key(item) in {
            f"{asset}:{signal}:{formula}" for asset, formula, signal in active_keys
        }
    )
    held_asset_codes = tuple(
        sorted({key[0] for key, item in latest.items() if item.to_state == "confirmed"})
    )
    source_fact_hash = _rebuild_context_source_fact_hash(
        bundle,
        prior_observations=prior_observations,
        prior_seal_artifact_hash=prior_seal_artifact_hash,
    )
    return V2DayEvidence(
        signal_date=snapshot.signal_date,
        decision_cutoff=snapshot.decision_cutoff,
        available=not missing_keys,
        reason=(
            "pit_rebuilt_screen_and_lifecycle_checked"
            if not missing_keys
            else "v2_rebuilt_observation_set_incomplete"
        ),
        manifest_hash=screen.manifest_hash,
        manifest_input_hash=screen.input_hash,
        manifest_decision_cutoff=screen.source_cutoff,
        manifest_data_receipt_cutoff=screen.data_receipt_cutoff,
        required_observation_keys=required_keys,
        observed_observation_keys=observed_keys,
        missing_observation_keys=missing_keys,
        observation_count=len(observations),
        screen_observation_count=len(screen_observations),
        lifecycle_checked_observation_count=len(observations),
        observation_digest=observation_digest,
        transition_hashes=tuple(sorted(item.source_hash for item in events)),
        transition_count=len(events),
        transition_digest=transition_digest,
        checked_through_date=snapshot.signal_date,
        checked_through_cutoff=snapshot.decision_cutoff,
        lifecycle_checked=True,
        checked_observation_keys=observed_keys,
        checked_asset_codes=tuple(sorted({item.asset_code for item in observations})),
        held_asset_codes=held_asset_codes,
        materialization_mode="pit_rebuild_research_artifact_v1",
        source_fact_hash=source_fact_hash,
        computed_at=datetime.now(UTC),
        core_events=events,
        observations=observations,
        screen_observations=screen_observations,
        active_observations=active_observations,
        prior_seal_artifact_hash=prior_seal_artifact_hash,
    )


def _lifecycle_evaluation_cutoff(
    observation: V2CandidateObservation,
    decision_cutoff: datetime,
) -> datetime:
    """Match the timestamp representation used by the persisted V2 contract."""

    if observation.source_cutoff.tzinfo is None or observation.source_cutoff.utcoffset() is None:
        # V2's legacy tables use timezone-naive UTC datetimes.  Comparing that
        # representation with an aware Shanghai cutoff raises before the
        # lifecycle engine can inspect any bar; normalize the boundary only,
        # preserving the original observation and feature hash.
        return _db_cutoff(decision_cutoff)
    return decision_cutoff


async def _materialize_and_check_v2_day(
    session: AsyncSession,
    *,
    snapshot: ComparisonSnapshotAudit,
    started: float,
    max_seconds: float,
    research_store: str | Path | None = None,
    run_id: str = "etf-strategy-route-comparison",
    phase: str = V2_DAY_CHECK_PHASE,
    prior_evidence: V2DayEvidence | None = None,
    prior_signal_date: date | None = None,
) -> tuple[V2DayEvidence | None, tuple[str, ...]]:
    """Screen one PIT date and continue every prior active signal identity."""

    if time.monotonic() - started >= max_seconds:
        return None, ("preparation_time_budget_exceeded",)
    core_snapshot = snapshot.core_snapshot
    if not snapshot.ready_for_signal_filters:
        return None, (
            "v2_common_pool_unavailable:"
            f"history_numerator={snapshot.history_numerator}/"
            f"nonclone_denominator={snapshot.nonclone_denominator}/"
            f"required={COMPARISON_MIN_NONCLONE_ASSETS}",
        )

    prior_observations: tuple[V2CandidateObservation, ...] = ()
    prior_seal_artifact_hash: str | None = None
    if prior_evidence is not None:
        if not prior_evidence.available or not prior_evidence.lifecycle_checked:
            return None, ("v2_prior_day_check_unavailable",)
        prior_observations = prior_evidence.active_observations
        prior_seal_artifact_hash = prior_evidence.seal_artifact_hash
    elif prior_signal_date is not None:
        if research_store is None:
            return None, ("v2_prior_day_check_required",)
        prior = _read_prior_rebuilt_observations(
            research_store,
            run_id=run_id,
            phase=phase,
            signal_date=prior_signal_date,
        )
        if prior is None:
            return None, (f"v2_prior_day_check_missing:{prior_signal_date.isoformat()}",)
        prior_observations, prior_seal_artifact_hash = prior

    active_codes = {item.asset_code for item in prior_observations}
    eligible_codes = tuple(sorted(set(core_snapshot.eligible_asset_codes) | active_codes))
    manifest = await _read_v2_manifest(
        session,
        signal_date=core_snapshot.signal_date,
        decision_cutoff=core_snapshot.decision_cutoff,
    )
    try:
        bundle = await load_v2_comparison_inputs(
            session,
            snapshot=core_snapshot,
            eligible_codes=eligible_codes,
        )
    except (SQLAlchemyError, ValueError, RuntimeError) as exc:
        return None, (f"v2_pit_input_unavailable:{type(exc).__name__}",)
    if not bundle.inputs:
        return None, ("v2_pit_input_empty",)

    # Build the current immutable screen view before touching any lifecycle
    # rows.  A previously sealed comparison artifact is append-only: reruns
    # must prove that the same PIT facts and screen identities are present and
    # then reuse the old seal rather than generating a new computed_at and
    # colliding with the artifact key.
    all_observations: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    parsed_observations: tuple[V2CandidateObservation, ...] = ()
    if manifest is None:
        if time.monotonic() - started >= max_seconds:
            return None, ("preparation_time_budget_exceeded",)
        try:
            screen = screen_dual_universe(
                bundle.inputs,
                code_version="dual-universe-leader-tactics-v2",
                provider_health=bundle.provider_health,
            )
        except (SQLAlchemyError, ValueError, V2ContractError, TypeError) as exc:
            return None, (f"v2_screen_materialization_failed:{type(exc).__name__}",)
    else:
        # Existing manifests remain immutable.  Read their complete causal
        # observation history once so both seal reuse and the continuation
        # pass use the exact same parsed rows.
        try:
            all_observations = await _read_v2_observations(
                session,
                manifest_hash=None,
                signal_date=core_snapshot.signal_date,
                decision_cutoff=core_snapshot.decision_cutoff,
            )
            parsed_observations = tuple(
                _v2_observation_from_row(row) for _key, row in all_observations
            )
        except (SQLAlchemyError, TypeError, ValueError, V2ContractError) as exc:
            return None, (f"v2_observation_history_read_failed:{type(exc).__name__}",)
        current_rows = tuple(
            _v2_observation_from_row(row)
            for _key, row in all_observations
            if str(row.get("manifest_hash") or "") == str(manifest["manifest_hash"])
            and _as_date(row.get("signal_date")) == core_snapshot.signal_date
        )
        screen = type(
            "_SealedScreen",
            (),
            {
                "manifest_hash": str(manifest["manifest_hash"]),
                "input_hash": str(manifest["input_hash"]),
                "source_cutoff": _as_datetime(manifest.get("decision_cutoff")),
                "data_receipt_cutoff": _as_datetime(manifest.get("data_receipt_cutoff")),
                "observations": current_rows,
            },
        )()

    if research_store is not None:
        marker_key = f"v2-day-check:{core_snapshot.signal_date.isoformat()}"
        raw_seal = _read_sealed_day_check(
            research_store,
            run_id=run_id,
            phase=phase,
            item_key=marker_key,
        )
        if raw_seal is not None:
            existing = _read_rebuilt_day_check(
                research_store,
                snapshot=core_snapshot,
                run_id=run_id,
                phase=phase,
            )
            if existing is None:
                return None, ("v2_existing_rebuild_seal_unreadable",)
            if not existing.available:
                return existing, (existing.reason,)
            expected_source_fact_hash = _rebuild_context_source_fact_hash(
                bundle,
                prior_observations=prior_observations,
                prior_seal_artifact_hash=prior_seal_artifact_hash,
            )
            current_screen_observations = _merge_v2_observations(
                tuple(screen.observations)
            )
            same_screen = tuple(
                _v2_observation_payload(item) for item in current_screen_observations
            ) == tuple(
                _v2_observation_payload(item) for item in existing.screen_observations
            )
            if (
                existing.source_fact_hash != expected_source_fact_hash
                or existing.manifest_hash != str(screen.manifest_hash)
                or existing.manifest_input_hash != str(screen.input_hash)
                or not same_screen
            ):
                mismatch = replace(
                    existing,
                    available=False,
                    reason="v2_existing_rebuild_seal_input_mismatch",
                )
                return mismatch, (mismatch.reason,)
            # Preserve the original computed_at and artifact identity.  The
            # caller recognizes seal_artifact_hash and skips a duplicate
            # write, so the immutable store remains byte-for-byte stable.
            return existing, ()

    if manifest is None:
        inputs_by_code = {item.asset_code: item for item in bundle.inputs}
        rebuilt_transitions: list[V2LifecycleTransition] = []
        missing_active: list[str] = []
        lifecycle_observations = _merge_v2_observations(
            (*prior_observations, *screen.observations)
        )
        for observation in lifecycle_observations:
            if (
                observation.qualifies is not True
                or observation.state not in {"preparing", "turning_watch"}
            ):
                continue
            signal_key = _v2_observation_key(observation)
            item = inputs_by_code.get(observation.asset_code)
            if item is None or not item.bars:
                missing_active.append(signal_key)
                continue
            if time.monotonic() - started >= max_seconds:
                return None, ("preparation_time_budget_exceeded",)
            try:
                rebuilt_transitions.extend(
                    derive_lifecycle(
                        observation=observation,
                        signal_bars=item.bars,
                        evaluation_cutoff=_lifecycle_evaluation_cutoff(
                            observation, core_snapshot.decision_cutoff
                        ),
                        visible_through=core_snapshot.signal_date,
                    )
                )
            except (ValueError, TypeError, V2ContractError):
                missing_active.append(signal_key)
        if missing_active:
            return None, (f"v2_active_signal_inputs_missing:{len(set(missing_active))}",)
        evidence = _build_rebuilt_v2_evidence(
            snapshot=snapshot,
            bundle=bundle,
            screen=screen,
            prior_observations=prior_observations,
            prior_seal_artifact_hash=prior_seal_artifact_hash,
            transitions=tuple(rebuilt_transitions),
        )
        return evidence, (() if evidence.available else (evidence.reason,))

    # Existing V2 manifests are still read through their immutable tables, but
    # lifecycle continuation is sealed in the independent comparison artifact
    # because rows written now cannot satisfy a historical created_at cutoff.
    inputs_by_code = {item.asset_code: item for item in bundle.inputs}
    rebuilt_transitions: list[V2LifecycleTransition] = []
    missing_active: list[str] = []
    for observation in _merge_v2_observations((*prior_observations, *parsed_observations)):
        if (
            observation.qualifies is not True
            or observation.state not in {"preparing", "turning_watch"}
        ):
            continue
        signal_key = _v2_observation_key(observation)
        item = inputs_by_code.get(observation.asset_code)
        if item is None or not item.bars:
            missing_active.append(signal_key)
            continue
        try:
            rebuilt_transitions.extend(
                derive_lifecycle(
                    observation=observation,
                    signal_bars=item.bars,
                    evaluation_cutoff=_lifecycle_evaluation_cutoff(
                        observation, core_snapshot.decision_cutoff
                    ),
                    visible_through=core_snapshot.signal_date,
                )
            )
        except (ValueError, TypeError, V2ContractError):
            missing_active.append(signal_key)
    if missing_active:
        return None, (f"v2_active_signal_inputs_missing:{len(set(missing_active))}",)

    transitions_by_manifest: dict[str, list[V2LifecycleTransition]] = defaultdict(list)
    manifest_by_observation_key = {
        _v2_observation_key(_v2_observation_from_row(row)): str(
            row.get("manifest_hash") or ""
        )
        for _key, row in all_observations
    }
    for transition in rebuilt_transitions:
        observation_key = (
            f"{transition.asset_code}:{transition.signal_date.isoformat()}:{transition.formula_id}"
        )
        manifest_hash = manifest_by_observation_key.get(observation_key)
        if manifest_hash is not None:
            transitions_by_manifest[manifest_hash].append(transition)
    try:
        for manifest_hash, transitions in transitions_by_manifest.items():
            if not manifest_hash:
                return None, ("v2_transition_manifest_identity_missing",)
            await persist_v2_lifecycle_transitions(
                session,
                manifest_hash=manifest_hash,
                transitions=tuple(transitions),
            )
        if transitions_by_manifest:
            await session.commit()
    except (SQLAlchemyError, ValueError, V2ContractError) as exc:
        return None, (f"v2_lifecycle_persistence_failed:{type(exc).__name__}",)

    evidence = _build_rebuilt_v2_evidence(
        snapshot=snapshot,
        bundle=bundle,
        screen=screen,
        prior_observations=prior_observations,
        prior_seal_artifact_hash=prior_seal_artifact_hash,
        transitions=tuple(rebuilt_transitions),
    )
    return evidence, (() if evidence.available else (evidence.reason,))


def _read_rebuilt_day_check(
    path: str | Path,
    *,
    snapshot: CoreComparisonDecisionSnapshot,
    run_id: str,
    phase: str,
) -> V2DayEvidence | None:
    sealed = _read_sealed_day_check(
        path,
        run_id=run_id,
        phase=phase,
        item_key=f"v2-day-check:{snapshot.signal_date.isoformat()}",
    )
    if sealed is None:
        return None
    payload, artifact_hash = sealed
    if payload.get("materialization_mode") != "pit_rebuild_research_artifact_v1":
        return None
    if payload.get("snapshot_source_hash") != snapshot.source_hash:
        return V2DayEvidence(
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
            available=False,
            reason="v2_rebuild_snapshot_source_mismatch",
            seal_artifact_hash=artifact_hash,
        )
    try:
        raw_observations = payload["observations"]
        raw_screen_observations = payload["screen_observations"]
        raw_active_observations = payload["active_observations"]
        if not all(
            isinstance(value, list)
            for value in (
                raw_observations,
                raw_screen_observations,
                raw_active_observations,
            )
        ):
            raise V2ContractError("sealed V2 observation collections are invalid")
        observations = _merge_v2_observations(
            tuple(
                _v2_observation_from_payload(item)
                for item in raw_observations
                if isinstance(item, Mapping)
            )
        )
        screen_observations = _merge_v2_observations(
            tuple(
                _v2_observation_from_payload(item)
                for item in raw_screen_observations
                if isinstance(item, Mapping)
            )
        )
        active_observations = _merge_v2_observations(
            tuple(
                _v2_observation_from_payload(item)
                for item in raw_active_observations
                if isinstance(item, Mapping)
            )
        )
        observation_keys = tuple(_v2_observation_key(item) for item in observations)
        active_keys = tuple(_v2_observation_key(item) for item in active_observations)
        required_keys = tuple(str(item) for item in payload["required_observation_keys"])
        observed_keys = tuple(str(item) for item in payload["observed_observation_keys"])
        checked_keys = tuple(str(item) for item in payload["checked_observation_keys"])
        current_required = {
            f"{code}:{snapshot.signal_date.isoformat()}:{formula}"
            for code in snapshot.eligible_asset_codes
            for formula in _COMPARISON_FORMULAS
        }
        historical_keys = {
            key for key, item in zip(observation_keys, observations, strict=True)
            if item.signal_date != snapshot.signal_date
        }
        expected_required = tuple(sorted(current_required | historical_keys))
        if (
            required_keys != expected_required
            or observed_keys != tuple(sorted(observation_keys))
            or checked_keys != observed_keys
            or any(key not in set(observed_keys) for key in active_keys)
            or any(item.signal_date != snapshot.signal_date for item in screen_observations)
            or int(payload["observation_count"]) != len(observations)
            or int(payload["screen_observation_count"]) != len(screen_observations)
            or int(payload["lifecycle_checked_observation_count"]) != len(observations)
        ):
            raise V2ContractError("sealed V2 observation proof sets are inconsistent")
        events = tuple(
            V2ComparisonEvent(
                signal_date=date.fromisoformat(str(item["signal_date"])),
                original_signal_date=date.fromisoformat(str(item["original_signal_date"])),
                asset_code=str(item["asset_code"]),
                event_type=str(item["event_type"]),  # type: ignore[arg-type]
                score=float(item["score"]),
                formula_id=str(item["formula_id"]),
                clone_group=str(item["clone_group"]),
                source_hash=str(item["source_hash"]),
            )
            for item in payload["core_events"]
            if isinstance(item, Mapping)
        )
        event_hashes = tuple(sorted(item.source_hash for item in events))
        expected_observation_digest = stable_contract_hash(
            [item.canonical_payload() | {"feature_hash": item.feature_hash} for item in observations]
        )
        expected_checked_assets = tuple(sorted({item.asset_code for item in observations}))
        missing_keys = tuple(str(item) for item in payload["missing_observation_keys"])
        transition_hashes = tuple(str(item) for item in payload["transition_hashes"])
        evidence = V2DayEvidence(
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
            available=True,
            reason="sealed_pit_rebuild_research_artifact",
            manifest_hash=str(payload["manifest_hash"]),
            manifest_input_hash=str(payload["manifest_input_hash"]),
            required_observation_keys=required_keys,
            observed_observation_keys=observed_keys,
            missing_observation_keys=missing_keys,
            observation_count=int(payload["observation_count"]),
            screen_observation_count=int(payload["screen_observation_count"]),
            lifecycle_checked_observation_count=int(payload["lifecycle_checked_observation_count"]),
            observation_digest=str(payload["observation_digest"]),
            transition_hashes=tuple(str(item) for item in payload["transition_hashes"]),
            transition_count=int(payload["transition_count"]),
            transition_digest=str(payload["transition_digest"]),
            checked_through_date=date.fromisoformat(str(payload["checked_through_date"])),
            checked_through_cutoff=_as_datetime(payload["checked_through_cutoff"]),
            lifecycle_checked=True,
            checked_observation_keys=checked_keys,
            checked_asset_codes=tuple(str(item) for item in payload["checked_asset_codes"]),
            held_asset_codes=tuple(str(item) for item in payload["held_asset_codes"]),
            materialization_mode="pit_rebuild_research_artifact_v1",
            source_fact_hash=str(payload["source_fact_hash"]),
            computed_at=_as_datetime(payload.get("computed_at")),
            seal_artifact_hash=artifact_hash,
            core_events=events,
            observations=observations,
            screen_observations=screen_observations,
            active_observations=active_observations,
            prior_seal_artifact_hash=(
                str(payload["prior_seal_artifact_hash"])
                if payload.get("prior_seal_artifact_hash") is not None
                else None
            ),
        )
        if (
            tuple(sorted(set(required_keys))) != required_keys
            or tuple(sorted(set(observed_keys))) != observed_keys
            or tuple(sorted(set(checked_keys))) != checked_keys
            or observed_keys != tuple(sorted(observation_keys))
            or required_keys != observed_keys
            or checked_keys != observed_keys
            or missing_keys != tuple(sorted(set(required_keys) - set(observed_keys)))
            or tuple(sorted(_v2_observation_key(item) for item in screen_observations))
            != tuple(_v2_observation_key(item) for item in screen_observations)
            or tuple(sorted(set(transition_hashes))) != transition_hashes
            or any(not _sha256_text(item) for item in transition_hashes)
            or transition_hashes != event_hashes
            or int(payload["transition_count"]) != len(event_hashes)
            or any(item.signal_date != snapshot.signal_date for item in events)
            or tuple(str(item) for item in payload["checked_asset_codes"])
            != expected_checked_assets
            or str(payload["observation_digest"]) != expected_observation_digest
            or not _sha256_text(payload.get("manifest_hash"))
            or not _sha256_text(payload.get("manifest_input_hash"))
            or evidence.checked_through_date is None
            or evidence.checked_through_cutoff is None
            or not _sha256_text(payload.get("source_fact_hash"))
            or not _sha256_text(payload.get("transition_digest"))
            or not _as_datetime(payload.get("computed_at"))
        ):
            raise V2ContractError("sealed V2 lifecycle evidence is inconsistent")
        if evidence.core_state_check is None:
            raise V2ContractError("sealed V2 state check is unavailable")
    except (KeyError, TypeError, ValueError, V2ContractError):
        return V2DayEvidence(
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
            available=False,
            reason="v2_rebuild_artifact_payload_invalid",
            seal_artifact_hash=artifact_hash,
        )
    if _v2_marker_payload(snapshot=snapshot, evidence=evidence) != payload:
        return V2DayEvidence(
            **{
                **evidence.__dict__,
                "available": False,
                "reason": "v2_rebuild_artifact_mismatch",
            }
        )
    if evidence.prior_seal_artifact_hash is not None:
        previous_date = _previous_exchange_session(snapshot.signal_date)
        previous = (
            _read_sealed_day_check(
                path,
                run_id=run_id,
                phase=phase,
                item_key=(
                    f"v2-day-check:{previous_date.isoformat()}"
                    if previous_date is not None
                    else "v2-day-check:missing"
                ),
            )
            if previous_date is not None
            else None
        )
        if previous is None or previous[1] != evidence.prior_seal_artifact_hash:
            return V2DayEvidence(
                **{
                    **evidence.__dict__,
                    "available": False,
                    "reason": "v2_rebuild_prior_seal_missing",
                }
            )
    return evidence


async def read_v2_day_evidence(
    session: AsyncSession,
    *,
    snapshot: CoreComparisonDecisionSnapshot,
    research_store: str | Path | None = None,
    run_id: str = "etf-strategy-route-comparison",
    phase: str = V2_DAY_CHECK_PHASE,
    require_seal: bool = True,
) -> V2DayEvidence:
    """Read one exact-date V2 manifest, observation set and causal transitions."""

    # A historical PIT reconstruction is intentionally stored only in the
    # comparison research namespace.  It cannot satisfy the original V2
    # table's ``created_at <= decision_cutoff`` visibility rule, so resolve a
    # verified rebuild artifact before looking for an online manifest.
    if research_store is not None:
        rebuilt = _read_rebuilt_day_check(
            research_store,
            snapshot=snapshot,
            run_id=run_id,
            phase=phase,
        )
        if rebuilt is not None:
            return rebuilt

    try:
        manifest = await _read_v2_manifest(
            session,
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
        )
    except SQLAlchemyError as exc:
        return V2DayEvidence(
            snapshot.signal_date, snapshot.decision_cutoff, False,
            f"v2_research_tables_unavailable:{type(exc).__name__}",
        )
    except (ValueError, TypeError) as exc:
        return V2DayEvidence(
            snapshot.signal_date, snapshot.decision_cutoff, False,
            f"v2_manifest_invalid:{type(exc).__name__}",
        )
    if manifest is None:
        return V2DayEvidence(
            snapshot.signal_date, snapshot.decision_cutoff, False,
            "v2_manifest_missing_for_date",
        )
    manifest_hash = str(manifest["manifest_hash"])
    manifest_cutoff = _as_datetime(manifest.get("decision_cutoff"))
    receipt_cutoff = _as_datetime(manifest.get("data_receipt_cutoff"))
    try:
        observations = await _read_v2_observations(
            session,
            # Read the complete causal observation history.  A later date's
            # lifecycle may continue an earlier signal; restricting this to
            # today's manifest would silently turn an unfinished signal into
            # a no-transition result.
            manifest_hash=None,
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
        )
        transitions = await _read_v2_transitions(
            session,
            manifest_hash=None,
            signal_date=snapshot.signal_date,
            decision_cutoff=snapshot.decision_cutoff,
        )
    except SQLAlchemyError as exc:
        return V2DayEvidence(
            snapshot.signal_date, snapshot.decision_cutoff, False,
            f"v2_observation_or_transition_read_failed:{type(exc).__name__}",
            manifest_hash=manifest_hash,
            manifest_input_hash=str(manifest["input_hash"]),
            manifest_decision_cutoff=manifest_cutoff,
            manifest_data_receipt_cutoff=receipt_cutoff,
        )

    required_keys = tuple(
        sorted(
            f"{code}:{snapshot.signal_date.isoformat()}:{formula}"
            for code in snapshot.eligible_asset_codes
            for formula in _COMPARISON_FORMULAS
        )
    )
    observed_keys = tuple(sorted(key for key, _ in observations))
    missing_keys = tuple(sorted(set(required_keys) - set(observed_keys)))
    screen_observations = tuple(
        (key, row)
        for key, row in observations
        if str(row.get("manifest_hash") or "") == manifest_hash
        and _as_date(row.get("signal_date")) == snapshot.signal_date
    )
    observation_digest = stable_contract_hash(
        [
            {
                "key": key,
                "state": row.get("state"),
                "availability": row.get("availability"),
                "qualifies": row.get("qualifies"),
                "score": row.get("score"),
                "source_cutoff": _json_value(row.get("source_cutoff")),
                "gate_facts_json": row.get("gate_facts_json"),
                "exclusion_reasons_json": row.get("exclusion_reasons_json"),
                "provenance_json": row.get("provenance_json"),
                "feature_hash": row.get("feature_hash"),
            }
                for key, row in observations
        ]
    ) if observations else None
    transition_digest = stable_contract_hash(
        [
            {
                "transition_hash": row.get("transition_hash"),
                "asset_code": row.get("asset_code"),
                "formula_id": row.get("formula_id"),
                "signal_date": _json_value(row.get("signal_date")),
                "transition_date": _json_value(row.get("transition_date")),
                "from_state": row.get("from_state"),
                "to_state": row.get("to_state"),
                "evidence_cutoff": _json_value(row.get("evidence_cutoff")),
            }
            for row in transitions
        ]
    )
    try:
        core_events = _v2_events_from_transitions(
            observations=observations,
            transitions=transitions,
            event_date=snapshot.signal_date,
        )
    except (TypeError, ValueError, V2ContractError) as exc:
        return V2DayEvidence(
            snapshot.signal_date,
            snapshot.decision_cutoff,
            False,
            f"v2_transition_payload_invalid:{type(exc).__name__}",
            manifest_hash=manifest_hash,
            manifest_input_hash=str(manifest["input_hash"]),
            manifest_decision_cutoff=manifest_cutoff,
            manifest_data_receipt_cutoff=receipt_cutoff,
        )
    transition_hashes = tuple(sorted(item.source_hash for item in core_events))
    checked_asset_codes = tuple(sorted({key.split(":", 1)[0] for key in observed_keys}))
    lifecycle_checked_observations = tuple(
        row
        for _key, row in observations
        if str(row.get("state") or "") in {"preparing", "turning_watch", "confirmed"}
    )
    latest_states: dict[tuple[str, str, date], Mapping[str, Any]] = {}
    for row in transitions:
        transition_date = _as_date(row.get("transition_date"))
        asset_code = str(row.get("asset_code") or "")
        formula_id = str(row.get("formula_id") or "")
        source_signal = _as_date(row.get("signal_date"))
        if not asset_code or not formula_id or transition_date is None or source_signal is None:
            continue
        key = (asset_code, formula_id, source_signal)
        previous = latest_states.get(key)
        if previous is None or (
            transition_date,
            int(row.get("id") or 0),
        ) >= (
            _as_date(previous.get("transition_date")) or date.min,
            int(previous.get("id") or 0),
        ):
            latest_states[key] = row
    held_asset_codes = tuple(
        sorted(
            {
                asset_code
                for (asset_code, _formula, _signal), row in latest_states.items()
                if str(row.get("to_state") or "") == "confirmed"
            }
        )
    )
    evidence = V2DayEvidence(
        snapshot.signal_date,
        snapshot.decision_cutoff,
        not missing_keys,
        "observations_complete" if not missing_keys else "v2_observation_set_incomplete",
        manifest_hash=manifest_hash,
        manifest_input_hash=str(manifest["input_hash"]),
        manifest_decision_cutoff=manifest_cutoff,
        manifest_data_receipt_cutoff=receipt_cutoff,
        required_observation_keys=required_keys,
        observed_observation_keys=observed_keys,
        missing_observation_keys=missing_keys,
        observation_count=len(screen_observations),
        screen_observation_count=len(screen_observations),
        lifecycle_checked_observation_count=len(lifecycle_checked_observations),
        observation_digest=observation_digest,
        transition_hashes=transition_hashes,
        transition_count=len(core_events),
        transition_digest=transition_digest,
        checked_through_date=snapshot.signal_date,
        checked_through_cutoff=snapshot.decision_cutoff,
        lifecycle_checked=False,
        checked_asset_codes=checked_asset_codes,
        held_asset_codes=held_asset_codes,
        core_events=core_events,
    )
    if not evidence.available:
        return evidence
    if research_store is None:
        if not require_seal:
            return evidence
        return V2DayEvidence(
            **{
                **evidence.__dict__,
                "available": False,
                "reason": "v2_day_check_unsealed",
            }
        )
    sealed = _read_sealed_day_check(
        research_store,
        run_id=run_id,
        phase=phase,
        item_key=f"v2-day-check:{snapshot.signal_date.isoformat()}",
    )
    if sealed is None:
        if require_seal:
            return V2DayEvidence(
                **{
                    **evidence.__dict__,
                    "available": False,
                    "reason": "v2_day_check_unsealed",
                }
            )
        return evidence
    payload, artifact_hash = sealed
    # A seal created by preparation carries the result of the actual
    # lifecycle pass.  The read path still recomputes all raw observation and
    # transition digests first; the marker is only an identity/reference.
    if payload.get("lifecycle_checked") is not True:
        return V2DayEvidence(
            **{
                **evidence.__dict__,
                "available": False,
                "reason": "v2_lifecycle_check_missing",
                "seal_artifact_hash": artifact_hash,
            }
        )
    sealed_evidence = replace(
        evidence,
        lifecycle_checked=True,
        checked_observation_keys=tuple(
            str(item) for item in payload.get("checked_observation_keys", ())
        ),
        checked_asset_codes=tuple(str(item) for item in payload.get("checked_asset_codes", ())),
        held_asset_codes=tuple(str(item) for item in payload.get("held_asset_codes", ())),
        materialization_mode=str(payload.get("materialization_mode") or "stored_v2_manifest"),
        source_fact_hash=(
            str(payload["source_fact_hash"])
            if payload.get("source_fact_hash") is not None
            else None
        ),
        computed_at=_as_datetime(payload.get("computed_at")),
    )
    expected = _v2_marker_payload(snapshot=snapshot, evidence=sealed_evidence)
    if payload != expected:
        return V2DayEvidence(
            **{
                **evidence.__dict__,
                "available": False,
                "reason": "v2_day_check_artifact_mismatch",
                "seal_artifact_hash": artifact_hash,
            }
        )
    return replace(
        sealed_evidence,
        available=True,
        reason="sealed_observations_and_lifecycle_checked",
        seal_artifact_hash=artifact_hash,
    )


async def prepare_v2_day_checks(
    session: AsyncSession,
    *,
    snapshots: Sequence[ComparisonSnapshotAudit],
    research_store: str | Path,
    run_id: str = "etf-strategy-route-comparison",
    phase: str = V2_DAY_CHECK_PHASE,
    lease_owner: str | None = None,
    max_seconds: float = COMPARISON_MAX_CONTINUATION_SECONDS,
) -> ResearchPreparationResult:
    """Seal per-day V2 checks in the existing research artifact store.

    This action writes generic comparison artifacts and, when compatible PIT
    input exists, append-only V2 screen/lifecycle rows.  It never alters an
    existing manifest or its economic evidence.  A real V2 database lease is
    mandatory; the wall-clock bound is separate from that lease.
    """

    if not 0 < max_seconds <= COMPARISON_MAX_CONTINUATION_SECONDS:
        raise ValueError("research preparation must be within (0, 55] seconds")
    if not lease_owner or not lease_owner.strip():
        return ResearchPreparationResult(
            "blocked", 0, 0, ("v2_global_lease_owner_required",), 0.0
        )
    started = time.monotonic()
    ordered_dates = tuple(snapshot.signal_date for snapshot in snapshots)
    if len(ordered_dates) != len(set(ordered_dates)) or ordered_dates != tuple(sorted(ordered_dates)):
        return ResearchPreparationResult(
            "blocked", 0, 0, ("v2_snapshots_not_chronological",), time.monotonic() - started
        )
    for previous, current in zip(ordered_dates[:-1], ordered_dates[1:], strict=True):
        if _previous_exchange_session(current) != previous:
            return ResearchPreparationResult(
                "blocked",
                0,
                0,
                (f"v2_snapshot_calendar_gap:{previous.isoformat()}:{current.isoformat()}",),
                time.monotonic() - started,
            )
    resolved_store = _store_path(research_store)
    if not resolved_store.exists():
        if not resolved_store.parent.exists() or not resolved_store.parent.is_dir():
            return ResearchPreparationResult(
                "blocked",
                0,
                0,
                ("research_store_parent_unavailable",),
                time.monotonic() - started,
            )
        try:
            # Explicit preparation may initialize the existing generic
            # research store.  Read-only preflight and compare paths never
            # construct ReplayArtifactStore, so they cannot create a file.
            ReplayArtifactStore(resolved_store)
        except (OSError, sqlite3.Error) as exc:
            return ResearchPreparationResult(
                "blocked",
                0,
                0,
                (f"research_store_initialization_failed:{type(exc).__name__}",),
                time.monotonic() - started,
            )
    readiness = inspect_research_store(research_store, run_id=run_id, phase=phase)
    if not readiness.schema_compatible:
        return ResearchPreparationResult(
            "blocked", 0, 0, ("research_store_schema_incompatible",), time.monotonic() - started
        )
    if readiness.invalid_artifact_count:
        return ResearchPreparationResult(
            "blocked",
            0,
            0,
            ("research_store_invalid_artifact_hash",),
            time.monotonic() - started,
        )
    store = ReplayArtifactStore(_store_path(research_store))
    lease_acquired = False
    reasons: list[str] = []
    written = 0
    checked = 0
    existing_day_dates = _sealed_v2_day_dates(
        research_store,
        run_id=run_id,
        phase=phase,
    )
    if snapshots:
        first_date = snapshots[0].signal_date
        candidate_previous_date = _previous_exchange_session(first_date)
        candidate_previous_seal = (
            _read_sealed_day_check(
                research_store,
                run_id=run_id,
                phase=phase,
                item_key=(
                    f"v2-day-check:{candidate_previous_date.isoformat()}"
                    if candidate_previous_date is not None
                    else "v2-day-check:missing"
                ),
            )
            if candidate_previous_date is not None
            else None
        )
        if (
            candidate_previous_date is not None
            and candidate_previous_seal is None
            and any(day < first_date for day in existing_day_dates)
        ):
            return ResearchPreparationResult(
                "blocked",
                0,
                0,
                (f"v2_prior_day_check_missing:{candidate_previous_date.isoformat()}",),
                time.monotonic() - started,
            )
        if (
            candidate_previous_date is not None
            and candidate_previous_seal is not None
            and _read_prior_rebuilt_observations(
                research_store,
                run_id=run_id,
                phase=phase,
                signal_date=candidate_previous_date,
            )
            is None
        ):
            return ResearchPreparationResult(
                "blocked",
                0,
                0,
                (f"v2_prior_day_check_invalid:{candidate_previous_date.isoformat()}",),
                time.monotonic() - started,
            )
    try:
        try:
            expires = await acquire_v2_global_run_lease(
                session,
                lease_owner=lease_owner,
                lease_seconds=min(MAX_CONTINUATION_SECONDS, max_seconds),
            )
        except SQLAlchemyError as exc:
            return ResearchPreparationResult(
                "blocked", 0, 0, (f"v2_global_lease_unavailable:{type(exc).__name__}",), time.monotonic() - started
            )
        if expires is None:
            return ResearchPreparationResult(
                "waiting", 0, 0, ("v2_global_lease_held_by_other_worker",), time.monotonic() - started
            )
        lease_acquired = True
        pending: list[tuple[str, Mapping[str, Any]]] = []
        previous_evidence: V2DayEvidence | None = None
        previous_date: date | None = None
        if snapshots:
            candidate_previous_date = _previous_exchange_session(snapshots[0].signal_date)
            if (
                candidate_previous_date is not None
                and _read_prior_rebuilt_observations(
                    research_store,
                    run_id=run_id,
                    phase=phase,
                    signal_date=candidate_previous_date,
                )
                is not None
            ):
                previous_date = candidate_previous_date
        for snapshot in snapshots:
            if time.monotonic() - started >= max_seconds:
                reasons.append("preparation_time_budget_exceeded")
                break
            evidence, day_reasons = await _materialize_and_check_v2_day(
                session,
                snapshot=snapshot,
                started=started,
                max_seconds=max_seconds,
                research_store=research_store,
                run_id=run_id,
                phase=phase,
                prior_evidence=previous_evidence,
                prior_signal_date=(None if previous_evidence is not None else previous_date),
            )
            if evidence is None or not evidence.manifest_hash or not evidence.available or not evidence.lifecycle_checked:
                reasons.extend(
                    f"{snapshot.signal_date.isoformat()}:{reason}"
                    for reason in (
                        day_reasons
                        or ((evidence.reason,) if evidence is not None else ("v2_day_unavailable",))
                    )
                )
                previous_evidence = None
                previous_date = snapshot.signal_date
                continue
            payload = _v2_marker_payload(snapshot=snapshot.core_snapshot, evidence=evidence)
            if evidence.seal_artifact_hash is not None:
                # ``_materialize_and_check_v2_day`` already validated the
                # stored payload against current PIT facts.  Reusing the
                # verified identity avoids an immutable-artifact conflict and
                # preserves the original real computation timestamp.
                expected_hash = _artifact_hash(
                    run_id=run_id,
                    phase=phase,
                    item_key=f"v2-day-check:{snapshot.signal_date.isoformat()}",
                    payload=payload,
                )
                if expected_hash != evidence.seal_artifact_hash:
                    reasons.append(
                        f"{snapshot.signal_date.isoformat()}:v2_existing_rebuild_seal_hash_mismatch"
                    )
                    previous_evidence = None
                    previous_date = snapshot.signal_date
                    continue
                checked += 1
                previous_evidence = evidence
                previous_date = snapshot.signal_date
                continue
            pending.append((f"v2-day-check:{snapshot.signal_date.isoformat()}", payload))
            checked += 1
            marker_key = f"v2-day-check:{snapshot.signal_date.isoformat()}"
            previous_evidence = replace(
                evidence,
                # Compute the immutable identity before the batched write so
                # the next date can reference this exact prior seal.
                seal_artifact_hash=_artifact_hash(
                    run_id=run_id,
                    phase=phase,
                    item_key=marker_key,
                    payload=payload,
                ),
            )
            previous_date = snapshot.signal_date
            if len(pending) == 20:
                store.write_research_artifacts(
                    run_id=run_id,
                    phase=phase,
                    artifacts=pending,
                    max_seconds=max(0.1, min(5.0, max_seconds - (time.monotonic() - started))),
                )
                written += len(pending)
                pending.clear()
        if pending and time.monotonic() - started < max_seconds:
            store.write_research_artifacts(
                run_id=run_id,
                phase=phase,
                artifacts=pending,
                max_seconds=max(0.1, min(5.0, max_seconds - (time.monotonic() - started))),
            )
            written += len(pending)
        status = "complete" if checked == len(snapshots) and not reasons else "partial"
        return ResearchPreparationResult(status, written, checked, tuple(reasons), time.monotonic() - started)
    except (ArtifactConflictError, OSError, sqlite3.Error, ValueError) as exc:
        reasons.append(f"research_artifact_write_failed:{type(exc).__name__}")
        return ResearchPreparationResult("blocked", written, checked, tuple(reasons), time.monotonic() - started)
    finally:
        if lease_acquired and lease_owner:
            await release_v2_global_run_lease(session, lease_owner=lease_owner)


async def preflight_comparison_inputs(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    trading_sessions: Sequence[date] | None = None,
    research_store: str | Path | None = None,
    run_id: str = "etf-strategy-route-comparison",
    phase: str = V2_DAY_CHECK_PHASE,
    max_seconds: float = COMPARISON_MAX_CONTINUATION_SECONDS,
    max_source_rows: int | None = None,
) -> ComparisonPreflightReport:
    """Run the real configured PIT/V2/store preflight without strategy returns."""

    if not 0 < max_seconds <= COMPARISON_MAX_CONTINUATION_SECONDS:
        raise ValueError("preflight max_seconds must be within (0, 55]")
    started = time.monotonic()
    calendar = _normalise_calendar(
        start_date=start_date,
        end_date=end_date,
        trading_sessions=trading_sessions,
    )
    calendar_hash = stable_contract_hash(
        {"schema_version": "etf_strategy_route_comparison_calendar_v1", "sessions": calendar}
    )
    snapshots: list[ComparisonSnapshotAudit] = []
    v2_days: list[V2DayEvidence] = []
    reasons: list[str] = []
    truncated = False
    for signal_date in calendar:
        if time.monotonic() - started >= max_seconds:
            truncated = True
            reasons.append("preflight_time_budget_exceeded")
            break
        cutoff = datetime.combine(
            signal_date,
            COMPARISON_DECISION_CUTOFF_TIME,
            tzinfo=_SHANGHAI,
        )
        try:
            snapshot = await load_comparison_decision_snapshot_audit(
                session,
                signal_date=signal_date,
                decision_cutoff=cutoff,
                max_source_rows=max_source_rows,
            )
        except (SQLAlchemyError, ValueError, RuntimeError) as exc:
            reasons.append(f"{signal_date.isoformat()}:pit_input_unavailable:{type(exc).__name__}")
            continue
        snapshots.append(snapshot)
        if not snapshot.ready_for_signal_filters:
            reasons.append(
                f"{signal_date.isoformat()}:127根合格分子{snapshot.history_numerator}/"
                f"去克隆分母{snapshot.nonclone_denominator}，至少需要{COMPARISON_MIN_NONCLONE_ASSETS}只"
            )
        if snapshot.clone_mapping_missing_asset_codes:
            reasons.append(
                f"{signal_date.isoformat()}:clone映射缺失{len(snapshot.clone_mapping_missing_asset_codes)}只"
            )
        if research_store is not None:
            evidence = await read_v2_day_evidence(
                session,
                snapshot=snapshot.core_snapshot,
                research_store=research_store,
                run_id=run_id,
                phase=phase,
            )
            v2_days.append(evidence)
            if not evidence.available:
                reasons.append(f"{signal_date.isoformat()}:{evidence.reason}")
    store = inspect_research_store(research_store, run_id=run_id, phase=phase) if research_store else ResearchStoreReadiness(
        "未配置", False, False, False, False, 0, 0, 0, "未配置研究 store"
    )
    if research_store is None:
        reasons.append("未配置研究 store，V2 不能视为可用")
    elif not store.ready:
        reasons.append(store.reason)
    if not snapshots:
        reasons.append("没有成功读取任何真实 PIT 决策日")
    ready = bool(snapshots) and not truncated and not reasons
    return ComparisonPreflightReport(
        start_date=start_date,
        end_date=end_date,
        calendar_hash=calendar_hash,
        snapshots=tuple(snapshots),
        v2_days=tuple(v2_days),
        research_store=store,
        ready=ready,
        reasons=tuple(dict.fromkeys(reasons)),
        elapsed_seconds=time.monotonic() - started,
        truncated=truncated,
    )


async def run_bounded_read_step(
    operation: Callable[[], Awaitable[Any]],
    *,
    max_seconds: float = COMPARISON_MAX_CONTINUATION_SECONDS,
) -> Any:
    """Run an explicitly supplied existing research operation under 55s."""

    if not 0 < max_seconds <= COMPARISON_MAX_CONTINUATION_SECONDS:
        raise ValueError("bounded research operation must be within (0, 55] seconds")
    return await asyncio.wait_for(operation(), timeout=max_seconds)


__all__ = [
    "COMPARISON_INPUT_SCHEMA_VERSION",
    "COMPARISON_MAX_CONTINUATION_SECONDS",
    "COMPARISON_DECISION_CUTOFF_TIME",
    "COMPARISON_MIN_NONCLONE_ASSETS",
    "COMPARISON_MOMENTUM_LOOKBACK_SESSIONS",
    "COMPARISON_REQUIRED_HISTORY_SESSIONS",
    "ComparisonDecisionSnapshot",
    "ComparisonSnapshotAudit",
    "ComparisonPreflightReport",
    "build_comparison_input",
    "load_comparison_valuation_input",
    "ResearchPreparationResult",
    "ResearchStoreReadiness",
    "V2DayEvidence",
    "inspect_research_store",
    "load_comparison_decision_snapshot",
    "load_comparison_decision_snapshot_audit",
    "load_v2_comparison_inputs",
    "preflight_comparison_inputs",
    "prepare_v2_day_checks",
    "read_v2_day_evidence",
    "run_bounded_read_step",
]
