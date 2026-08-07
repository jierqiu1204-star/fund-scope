"""Neutral point-in-time ETF decision-data interface for research consumers.

The persisted source currently originates from the production PIT capture
workflow, but consumers see only factual universe, adjusted-history readiness,
cutoff, and provider provenance. Ranking rows and scores are not part of this
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfPitCaptureSource
from app.services.short_research.coverage_policy import (
    ETF_DAILY_DECISION_MIN_COVERAGE,
    evaluate_persisted_etf_readiness,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    MAX_CODES_PER_REPLAY_INPUT_PAGE,
    MAX_REPLAY_HISTORY_SESSIONS,
    PointInTimeAdjustedSeries,
    PointInTimeEtfMetadata,
    PointInTimeRankingInputSnapshot,
    ReplayInputExclusion,
    ReplayInputExclusionReason,
    build_point_in_time_adjusted_series,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    load_point_in_time_ranking_inputs as _load_legacy_point_in_time_inputs,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
PointInTimeEtfDecisionInputSnapshot = PointInTimeRankingInputSnapshot



@dataclass(frozen=True)
class EtfDecisionDataSnapshot:
    """Read-only readiness identity shared by independent ETF research paths."""

    snapshot_id: int
    trade_date: date
    decision_cutoff: datetime
    daily_coverage_ratio: float
    warmup_coverage_ratio: float
    readiness_policy_version: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    input_snapshot_hash: str
    provider_health_hash: str
    provider_health: tuple[tuple[str, str], ...]

    def evidence_dict(self) -> dict[str, Any]:
        """Return neutral provenance without exposing a ranking-run identity."""

        return {
            "schema_version": "etf_decision_data_snapshot_v1",
            "snapshot_id": self.snapshot_id,
            "trade_date": self.trade_date.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "daily_coverage_ratio": self.daily_coverage_ratio,
            "warmup_coverage_ratio": self.warmup_coverage_ratio,
            "readiness_policy_version": self.readiness_policy_version,
            "source_snapshot_hash": self.source_snapshot_hash,
            "universe_manifest_hash": self.universe_manifest_hash,
            "input_snapshot_hash": self.input_snapshot_hash,
            "provider_health_hash": self.provider_health_hash,
            "provenance_kind": "persisted_etf_pit_decision_data",
        }


def _decision_cutoff(source: EtfPitCaptureSource) -> datetime:
    try:
        timezone = ZoneInfo(source.cutoff_timezone)
    except (KeyError, ValueError) as exc:
        raise ValueError("ETF decision-data cutoff timezone is invalid") from exc
    cutoff = source.replay_visibility_cutoff
    if cutoff.tzinfo is not None and cutoff.utcoffset() is not None:
        return cutoff.astimezone(timezone)
    return cutoff.replace(tzinfo=timezone)


def _provider_health(source: EtfPitCaptureSource) -> tuple[tuple[str, str], ...]:
    context = source.source_context_json
    identity = context.get("provider_health_identity") if isinstance(context, dict) else None
    health = identity.get("provider_health") if isinstance(identity, dict) else None
    providers = health.get("providers") if isinstance(health, dict) else None
    if not isinstance(providers, dict):
        return ()
    normalized: list[tuple[str, str]] = []
    for provider, details in providers.items():
        if not isinstance(provider, str) or not provider.strip():
            continue
        payload = details if isinstance(details, dict) else {}
        explicit = str(payload.get("status") or "").strip().lower()
        circuit = str(payload.get("circuit_state") or "").strip().lower()
        if explicit in {"healthy", "available", "ok"} or circuit == "closed":
            status = "healthy"
        elif explicit in {"unavailable", "failed"} or circuit == "open":
            status = "unavailable"
        else:
            status = "degraded"
        normalized.append((provider.strip().lower(), status))
    return tuple(sorted(normalized))


def etf_decision_data_snapshot_from_source(
    source: EtfPitCaptureSource,
) -> EtfDecisionDataSnapshot:
    """Adapt the legacy persisted source into the neutral read contract."""

    return EtfDecisionDataSnapshot(
        snapshot_id=source.id,
        trade_date=source.as_of_trade_date,
        decision_cutoff=_decision_cutoff(source),
        daily_coverage_ratio=source.target_date_coverage_ratio,
        warmup_coverage_ratio=source.warmup_coverage_ratio,
        readiness_policy_version=source.readiness_policy_version,
        source_snapshot_hash=source.source_snapshot_hash,
        universe_manifest_hash=source.universe_manifest_hash,
        input_snapshot_hash=source.input_snapshot_hash,
        provider_health_hash=source.provider_health_hash,
        provider_health=_provider_health(source),
    )


async def latest_ready_etf_decision_data_snapshot(
    session: AsyncSession,
    *,
    as_of: datetime,
) -> EtfDecisionDataSnapshot | None:
    """Return the newest causally visible complete data snapshot, read-only."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    local_as_of = as_of.astimezone(_SHANGHAI).replace(tzinfo=None)
    candidates = (
        await session.scalars(
            select(EtfPitCaptureSource)
            .where(
                EtfPitCaptureSource.readiness_state == "complete",
                EtfPitCaptureSource.target_date_coverage_ratio
                >= ETF_DAILY_DECISION_MIN_COVERAGE,
                EtfPitCaptureSource.replay_visibility_cutoff <= local_as_of,
            )
            .order_by(
                EtfPitCaptureSource.as_of_trade_date.desc(),
                EtfPitCaptureSource.id.desc(),
            )
            .limit(20)
        )
    ).all()
    for source in candidates:
        readiness = evaluate_persisted_etf_readiness(
            policy_version=source.readiness_policy_version,
            daily_coverage_ratio=source.target_date_coverage_ratio,
            warmup_coverage_ratio=source.warmup_coverage_ratio,
        )
        if readiness.complete_publication_allowed:
            return etf_decision_data_snapshot_from_source(source)
    return None


async def load_point_in_time_etf_decision_inputs(
    session: AsyncSession,
    *,
    replay_date: date,
    decision_cutoff: datetime,
    max_source_rows: int,
    code_after: str | None = None,
    max_codes: int = MAX_CODES_PER_REPLAY_INPUT_PAGE,
    required_history_sessions: int,
) -> PointInTimeEtfDecisionInputSnapshot:
    """Load factual ETF decision inputs without consuming ranking outputs."""

    return await _load_legacy_point_in_time_inputs(
        session,
        replay_date=replay_date,
        decision_cutoff=decision_cutoff,
        max_source_rows=max_source_rows,
        code_after=code_after,
        max_codes=max_codes,
        required_history_sessions=required_history_sessions,
    )


__all__ = [
    "MAX_CODES_PER_REPLAY_INPUT_PAGE",
    "MAX_REPLAY_HISTORY_SESSIONS",
    "EtfDecisionDataSnapshot",
    "PointInTimeAdjustedSeries",
    "PointInTimeEtfMetadata",
    "PointInTimeEtfDecisionInputSnapshot",
    "PointInTimeRankingInputSnapshot",
    "ReplayInputExclusion",
    "ReplayInputExclusionReason",
    "build_point_in_time_adjusted_series",
    "etf_decision_data_snapshot_from_source",
    "latest_ready_etf_decision_data_snapshot",
    "load_point_in_time_etf_decision_inputs",
]
