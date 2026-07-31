"""Read-only point-in-time inputs for Strategy Lab ETF ranking replay."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import market_data
from app.services.short_research.daily_reconstructable import (
    PRICE_BASIS,
    REQUIRED_BAR_COUNT,
    AdjustedOhlcvBar,
    AdjustmentProvenance,
)

_PROVEN_MULTIPLICATIVE_ADJUSTMENTS = {
    ("eastmoney", "eastmoney.push2his.kline.hfq_v1"),
    ("tickflow", "tickflow.free.klines.backward_v1"),
}
_RAW_OR_FALLBACK_PROVIDER_MARKERS = ("sina", "efinance", "raw", "fallback")
_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DAILY_BAR_AVAILABLE_AT = time(15, 0)
MAX_CODES_PER_REPLAY_INPUT_PAGE = 16
MAX_REPLAY_HISTORY_SESSIONS = 180


class ReplayInputExclusionReason(StrEnum):
    INSUFFICIENT_POINT_IN_TIME_UNIVERSE = "insufficient_point_in_time_universe"
    MEMBERSHIP_OBSERVED_AFTER_CUTOFF = "membership_observed_after_cutoff"
    MEMBERSHIP_FACT_CONFLICT = "membership_fact_conflict"
    MEMBERSHIP_EXCLUDED = "membership_excluded"
    UNPROVEN_ADJUSTMENT_POINT_IN_TIME = "unproven_adjustment_point_in_time"
    RAW_OR_FALLBACK_PROVIDER_DATA = "raw_or_fallback_provider_data"
    STALE_OR_INELIGIBLE_ADJUSTED_INPUT = "stale_or_ineligible_adjusted_input"
    FUTURE_KNOWN_INPUT = "future_known_input"


@dataclass(frozen=True)
class PointInTimeEtfMetadata:
    asset_code: str
    membership_source: str
    membership_external_source_id: str
    membership_provider_version: str
    membership_evidence_hash: str
    membership_raw_payload_hash: str
    membership_fact_hash: str
    tracked_underlying_id: str | None
    membership_known_at: datetime
    membership_last_modified_at: datetime
    membership_ingested_at: datetime
    eligible_from: date
    eligible_at: date


@dataclass(frozen=True)
class ReplayInputExclusion:
    asset_code: str | None
    reason: ReplayInputExclusionReason
    detail: str


@dataclass(frozen=True)
class PointInTimeAdjustedSeries:
    asset_code: str
    metadata: PointInTimeEtfMetadata
    bars: tuple[AdjustedOhlcvBar, ...]
    provenance: AdjustmentProvenance
    earliest_source_timestamp: datetime
    latest_source_timestamp: datetime
    synchronized_after_cutoff: bool
    series_hash: str


@dataclass(frozen=True)
class PointInTimeRankingInputSnapshot:
    replay_date: date
    decision_cutoff: datetime
    authoritative_universe: tuple[PointInTimeEtfMetadata, ...]
    eligible_inputs: tuple[PointInTimeAdjustedSeries, ...]
    exclusions: tuple[ReplayInputExclusion, ...]
    universe_hash: str
    input_hash: str
    source_snapshot_hash: str
    coverage_manifest_hash: str
    page_asset_codes: tuple[str, ...]
    next_code_after: str | None
    has_more: bool


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _hash(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _metadata_payload(item: PointInTimeEtfMetadata) -> dict[str, Any]:
    return {
        "asset_code": item.asset_code,
        "membership_source": item.membership_source,
        "membership_external_source_id": item.membership_external_source_id,
        "membership_provider_version": item.membership_provider_version,
        "membership_evidence_hash": item.membership_evidence_hash,
        "membership_raw_payload_hash": item.membership_raw_payload_hash,
        "membership_fact_hash": item.membership_fact_hash,
        "tracked_underlying_id": item.tracked_underlying_id,
        "membership_known_at": item.membership_known_at.isoformat(),
        "membership_last_modified_at": item.membership_last_modified_at.isoformat(),
        "membership_ingested_at": item.membership_ingested_at.isoformat(),
        "eligible_from": item.eligible_from.isoformat(),
        "eligible_at": item.eligible_at.isoformat(),
    }


def _bar_payload(item: AdjustedOhlcvBar) -> dict[str, Any]:
    return {
        "session_date": item.session_date.isoformat(),
        "adjusted_open": item.adjusted_open,
        "adjusted_high": item.adjusted_high,
        "adjusted_low": item.adjusted_low,
        "adjusted_close": item.adjusted_close,
        "volume": item.volume,
        "turnover": item.turnover,
    }


def _series_from_rows(
    *,
    metadata: PointInTimeEtfMetadata,
    rows: list[market_data.EtfAdjustedDailyFact],
    decision_cutoff: datetime,
    required_history_sessions: int,
) -> PointInTimeAdjustedSeries | ReplayInputExclusion:
    window = rows[-required_history_sessions:]
    if (
        len(window) != required_history_sessions
        or window[-1].trade_date != metadata.eligible_at
    ):
        return ReplayInputExclusion(
            metadata.asset_code,
            ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
            f"requires {required_history_sessions} rows ending at replay_date",
        )
    provider_names = {str(row.data_provider or "").strip().lower() for row in window}
    if any(
        any(marker in provider for marker in _RAW_OR_FALLBACK_PROVIDER_MARKERS)
        for provider in provider_names
    ) or any(
        any(
            marker in str(row.raw_price_basis or "").strip().lower()
            for marker in ("sina", "efinance", "fallback")
        )
        for row in window
    ) or any(
        row.research_price_basis != PRICE_BASIS
        and (
            row.research_price_basis in {None, "raw", "raw_ohlc"}
            or row.decision_ineligibility_reason == "missing_total_return_provenance"
        )
        for row in window
    ):
        return ReplayInputExclusion(
            metadata.asset_code,
            ReplayInputExclusionReason.RAW_OR_FALLBACK_PROVIDER_DATA,
            "raw or fallback provider data cannot become replay decision evidence",
        )
    provider_versions = {
        (str(row.data_provider or "").lower(), str(row.adjustment_version or ""))
        for row in window
    }
    if len(provider_versions) != 1:
        return ReplayInputExclusion(
            metadata.asset_code,
            ReplayInputExclusionReason.UNPROVEN_ADJUSTMENT_POINT_IN_TIME,
            "mixed adjusted providers or versions",
        )
    provider, adjustment_version = next(iter(provider_versions))
    if (provider, adjustment_version) not in _PROVEN_MULTIPLICATIVE_ADJUSTMENTS:
        return ReplayInputExclusion(
            metadata.asset_code,
            ReplayInputExclusionReason.UNPROVEN_ADJUSTMENT_POINT_IN_TIME,
            "adjustment transform is not in the frozen proven registry",
        )

    bars: list[AdjustedOhlcvBar] = []
    timestamps: list[datetime] = []
    for row in window:
        values = (
            row.raw_open,
            row.raw_high,
            row.raw_low,
            row.raw_close,
            row.volume,
            row.adjusted_close,
        )
        if (
            row.decision_eligible is not True
            or row.research_price_basis != PRICE_BASIS
            or row.provider_version != adjustment_version
            or row.source_timestamp is None
            or any(
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or float(value) <= 0.0
                for value in values
            )
        ):
            return ReplayInputExclusion(
                metadata.asset_code,
                ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
                "daily row is not decision-eligible total-return-adjusted data",
            )
        row_source_timestamp = _utc(row.source_timestamp)
        if row_source_timestamp > decision_cutoff:
            return ReplayInputExclusion(
                metadata.asset_code,
                ReplayInputExclusionReason.FUTURE_KNOWN_INPUT,
                "adjusted daily row revision exceeds the decision cutoff",
            )
        if row.trade_date.weekday() >= 5:
            return ReplayInputExclusion(
                metadata.asset_code,
                ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
                "daily row is not an exchange weekday session",
            )
        assert row.adjusted_close is not None
        if (
            float(row.raw_high)
            < max(float(row.raw_open), float(row.raw_close), float(row.raw_low))
            or float(row.raw_low)
            > min(float(row.raw_open), float(row.raw_close), float(row.raw_high))
        ):
            return ReplayInputExclusion(
                metadata.asset_code,
                ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
                "daily row violates OHLC ordering",
            )
        factor = float(row.adjusted_close) / float(row.raw_close)
        bars.append(
            AdjustedOhlcvBar(
                session_date=row.trade_date,
                adjusted_open=float(row.raw_open) * factor,
                adjusted_high=float(row.raw_high) * factor,
                adjusted_low=float(row.raw_low) * factor,
                adjusted_close=float(row.adjusted_close),
                volume=float(row.volume),
                turnover=(
                    float(row.turnover)
                    if isinstance(row.turnover, int | float)
                    and not isinstance(row.turnover, bool)
                    and math.isfinite(float(row.turnover))
                    and float(row.turnover) >= 0.0
                    else None
                ),
            )
        )
        timestamps.append(row_source_timestamp)

    provenance = AdjustmentProvenance(
        provider=provider,
        adjustment_version=adjustment_version,
        price_basis=PRICE_BASIS,
        transform_kind="constant_multiplicative",
        scale_invariance_proven=True,
    )
    payload = {
        "asset_code": metadata.asset_code,
        "metadata": _metadata_payload(metadata),
        "bars": [_bar_payload(item) for item in bars],
        "provenance": {
            "provider": provenance.provider,
            "adjustment_version": provenance.adjustment_version,
            "price_basis": provenance.price_basis,
            "transform_kind": provenance.transform_kind,
            "scale_invariance_proven": provenance.scale_invariance_proven,
        },
        "earliest_source_timestamp": min(timestamps).isoformat(),
        "latest_source_timestamp": max(timestamps).isoformat(),
    }
    return PointInTimeAdjustedSeries(
        asset_code=metadata.asset_code,
        metadata=metadata,
        bars=tuple(bars),
        provenance=provenance,
        earliest_source_timestamp=min(timestamps),
        latest_source_timestamp=max(timestamps),
        synchronized_after_cutoff=max(timestamps) > decision_cutoff,
        series_hash=_hash(payload),
    )


async def load_point_in_time_ranking_inputs(
    session: AsyncSession,
    *,
    replay_date: date,
    decision_cutoff: datetime,
    max_source_rows: int,
    code_after: str | None = None,
    max_codes: int = MAX_CODES_PER_REPLAY_INPUT_PAGE,
    required_history_sessions: int = REQUIRED_BAR_COUNT,
) -> PointInTimeRankingInputSnapshot:
    """Load only factual membership and proven adjusted inputs available for replay."""

    if decision_cutoff.tzinfo is None or decision_cutoff.utcoffset() is None:
        raise ValueError("decision_cutoff must be timezone-aware")
    local_cutoff = decision_cutoff.astimezone(_ASIA_SHANGHAI)
    if (
        local_cutoff.date() != replay_date
        or local_cutoff.time().replace(tzinfo=None) < _DAILY_BAR_AVAILABLE_AT
        or replay_date.weekday() >= 5
    ):
        raise ValueError(
            "decision_cutoff must identify a completed Shanghai trading session"
        )
    if max_codes < 1 or max_codes > MAX_CODES_PER_REPLAY_INPUT_PAGE:
        raise ValueError(
            f"max_codes must be between 1 and {MAX_CODES_PER_REPLAY_INPUT_PAGE}"
        )
    if not REQUIRED_BAR_COUNT <= required_history_sessions <= MAX_REPLAY_HISTORY_SESSIONS:
        raise ValueError(
            "required_history_sessions must be between "
            f"{REQUIRED_BAR_COUNT} and {MAX_REPLAY_HISTORY_SESSIONS}"
        )
    cutoff = _utc(decision_cutoff)
    facts = await market_data.etf_membership_facts_covering(
        session,
        replay_date=replay_date,
    )
    facts_by_code: dict[str, list[market_data.EtfPointInTimeMembershipFact]] = defaultdict(list)
    exclusions: list[ReplayInputExclusion] = []
    for fact in facts:
        fact_known_at = _utc(fact.observed_at)
        if fact_known_at > cutoff:
            exclusions.append(
                ReplayInputExclusion(
                    fact.etf_code,
                    ReplayInputExclusionReason.MEMBERSHIP_OBSERVED_AFTER_CUTOFF,
                    "membership fact was not known by the decision cutoff",
                )
            )
            continue
        facts_by_code[fact.etf_code].append(fact)

    universe: list[PointInTimeEtfMetadata] = []
    for code in sorted(facts_by_code):
        rows = sorted(
            facts_by_code[code],
            key=lambda item: (
                _utc(item.observed_at),
                item.external_source_id,
                item.fact_hash,
            ),
        )
        states = {item.membership_state for item in rows}
        if len(states) != 1:
            exclusions.append(
                ReplayInputExclusion(
                    code,
                    ReplayInputExclusionReason.MEMBERSHIP_FACT_CONFLICT,
                    "overlapping factual membership receipts disagree at replay_date",
                )
            )
            continue
        fact = rows[0]
        if fact.membership_state != "included":
            exclusions.append(
                ReplayInputExclusion(
                    code,
                    ReplayInputExclusionReason.MEMBERSHIP_EXCLUDED,
                    "factual membership receipt excludes the ETF at replay_date",
                )
            )
            continue
        fact_known_at = _utc(fact.observed_at)
        universe.append(
            PointInTimeEtfMetadata(
                asset_code=code,
                membership_source=fact.provider,
                membership_external_source_id=fact.external_source_id,
                membership_provider_version=fact.provider_version,
                membership_evidence_hash=fact.evidence_hash,
                membership_raw_payload_hash=fact.raw_payload_hash,
                membership_fact_hash=fact.fact_hash,
                tracked_underlying_id=None,
                membership_known_at=fact_known_at,
                membership_last_modified_at=fact_known_at,
                membership_ingested_at=_utc(fact.created_at),
                eligible_from=fact.effective_from,
                eligible_at=replay_date,
            )
        )
    if not universe:
        exclusions.append(
            ReplayInputExclusion(
                None,
                ReplayInputExclusionReason.INSUFFICIENT_POINT_IN_TIME_UNIVERSE,
                "no factual membership covers replay_date by the decision cutoff",
            )
        )

    universe_tuple = tuple(universe)
    universe_hash = _hash([_metadata_payload(item) for item in universe_tuple])
    remaining_universe = tuple(
        item
        for item in universe_tuple
        if code_after is None or item.asset_code > code_after
    )
    page_universe = remaining_universe[:max_codes]
    has_more = len(remaining_universe) > len(page_universe)
    page_asset_codes = tuple(item.asset_code for item in page_universe)
    next_code_after = page_asset_codes[-1] if page_asset_codes and has_more else None
    price_facts = await market_data.etf_adjusted_daily_facts_on_or_before(
        session,
        etf_codes=page_asset_codes,
        replay_date=replay_date,
        rows_per_code=required_history_sessions,
        max_source_rows=max_source_rows,
    )
    prices_by_code: dict[str, list[market_data.EtfAdjustedDailyFact]] = defaultdict(list)
    for row in price_facts:
        prices_by_code[row.etf_code].append(row)

    eligible: list[PointInTimeAdjustedSeries] = []
    for metadata in page_universe:
        result = _series_from_rows(
            metadata=metadata,
            rows=prices_by_code[metadata.asset_code],
            decision_cutoff=cutoff,
            required_history_sessions=required_history_sessions,
        )
        if isinstance(result, ReplayInputExclusion):
            exclusions.append(result)
        else:
            eligible.append(result)
    exclusions_tuple = tuple(
        sorted(exclusions, key=lambda item: (item.asset_code or "", item.reason, item.detail))
    )
    eligible_tuple = tuple(sorted(eligible, key=lambda item: item.asset_code))
    watermark = await market_data.etf_adjusted_source_watermark(
        session,
        etf_codes=tuple(item.asset_code for item in universe_tuple),
        replay_date=replay_date,
    )
    source_snapshot_hash = _hash(
        {
            "replay_date": replay_date.isoformat(),
            "decision_cutoff": cutoff.isoformat(),
            "universe_hash": universe_hash,
            "adjustment_registry": sorted(_PROVEN_MULTIPLICATIVE_ADJUSTMENTS),
            "required_history_sessions": required_history_sessions,
            "price_source_watermark": {
                "row_count": watermark.row_count,
                "max_row_id": watermark.max_row_id,
                "max_source_timestamp": (
                    _utc(watermark.max_source_timestamp).isoformat()
                    if watermark.max_source_timestamp is not None
                    else None
                ),
            },
        }
    )
    coverage_manifest_hash = _hash(
        {
            "universe_hash": universe_hash,
            "page_asset_codes": page_asset_codes,
            "required_history_sessions": required_history_sessions,
            "eligible_asset_codes": [item.asset_code for item in eligible_tuple],
            "exclusions": [
                {
                    "asset_code": item.asset_code,
                    "reason": item.reason.value,
                    "detail": item.detail,
                }
                for item in exclusions_tuple
            ],
        }
    )
    input_hash = _hash(
        {
            "replay_date": replay_date.isoformat(),
            "decision_cutoff": cutoff.isoformat(),
            "universe_hash": universe_hash,
            "page_asset_codes": page_asset_codes,
            "required_history_sessions": required_history_sessions,
            "eligible_series_hashes": [item.series_hash for item in eligible_tuple],
        }
    )
    return PointInTimeRankingInputSnapshot(
        replay_date=replay_date,
        decision_cutoff=cutoff,
        authoritative_universe=universe_tuple,
        eligible_inputs=eligible_tuple,
        exclusions=exclusions_tuple,
        universe_hash=universe_hash,
        input_hash=input_hash,
        source_snapshot_hash=source_snapshot_hash,
        coverage_manifest_hash=coverage_manifest_hash,
        page_asset_codes=page_asset_codes,
        next_code_after=next_code_after,
        has_more=has_more,
    )
