"""Strict PIT next-morning confirmation for low-base volume watches."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    LOW_BASE_CATCHUP_V1,
    PRICE_BASIS,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_table,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    get_v2_materialized_manifest,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
MORNING_CONFIRMATION_SCHEMA_VERSION = "low_base_morning_confirmation_v1"
MORNING_CONFIRMATION_START = time(10, 40)
MORNING_CONFIRMATION_END = time(11, 30)
MORNING_MIN_CLOSED_BARS = 7
MORNING_MAX_WATCH_POOL = 20
MORNING_CUMULATIVE_VOLUME_RATIO_MIN = 1.20
MORNING_OVEREXTENSION_ATR_MAX = 1.50


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _decode(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _aware_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


@dataclass(frozen=True, slots=True)
class MorningConfirmationBar:
    bar_start: datetime
    bar_end: datetime
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    volume: float
    received_at: datetime
    normalization_factor: float
    normalization_identity: str
    decision_eligible: bool = True


@dataclass(frozen=True, slots=True)
class MorningWatchCandidate:
    manifest_hash: str
    asset_code: str
    asset_name: str
    signal_date: date
    source_cutoff: datetime
    feature_hash: str
    signal_adjusted_close: float
    signal_adjusted_high: float
    signal_adjusted_ma5: float
    signal_adjusted_ma20: float
    signal_adjusted_atr20: float


@dataclass(frozen=True, slots=True)
class MorningConfirmationResult:
    available: bool
    confirmed: bool
    entry_status: str
    reason: str
    decision_at: datetime
    closed_bar_count: int
    latest_bar_end: datetime | None
    current_adjusted_price: float | None
    cumulative_volume: float | None
    prior_20_mean_daily_volume: float | None
    cumulative_volume_ratio: float | None
    overextension_atr: float | None
    evidence_hash: str


def evaluate_low_base_morning_confirmation(
    *,
    candidate: MorningWatchCandidate,
    decision_at: datetime,
    bars: Sequence[MorningConfirmationBar],
    prior_20_mean_daily_volume: float | None,
) -> MorningConfirmationResult:
    """Evaluate observed morning facts without projecting full-day volume."""

    cutoff = _aware_shanghai(decision_at)
    reasons: list[str] = []
    if not MORNING_CONFIRMATION_START <= cutoff.time() <= MORNING_CONFIRMATION_END:
        reasons.append("outside_morning_confirmation_window")
    if candidate.signal_date >= cutoff.date():
        reasons.append("signal_not_from_prior_session")
    baseline = _finite(prior_20_mean_daily_volume)
    if baseline is None or baseline <= 0:
        reasons.append("prior_20_daily_volume_unavailable")

    ordered = tuple(sorted(bars, key=lambda row: (row.bar_end, row.received_at)))
    eligible: list[MorningConfirmationBar] = []
    identities: set[str] = set()
    factors: set[float] = set()
    for bar in ordered:
        start = _aware_shanghai(bar.bar_start)
        end = _aware_shanghai(bar.bar_end)
        received = _aware_shanghai(bar.received_at)
        values = (
            _finite(bar.raw_open),
            _finite(bar.raw_high),
            _finite(bar.raw_low),
            _finite(bar.raw_close),
            _finite(bar.volume),
            _finite(bar.normalization_factor),
        )
        raw_open, raw_high, raw_low, raw_close = values[:4]
        valid_ohlc = (
            raw_open is not None
            and raw_high is not None
            and raw_low is not None
            and raw_close is not None
            and raw_high >= max(raw_open, raw_close)
            and raw_low <= min(raw_open, raw_close)
            and raw_high >= raw_low
        )
        if (
            not bar.decision_eligible
            or end.date() != cutoff.date()
            or end > cutoff
            or received > cutoff
            or end - start != timedelta(minutes=10)
            or any(value is None for value in values)
            or not valid_ohlc
            or min(float(value) for value in values[:4] if value is not None) <= 0
            or values[4] is None
            or values[4] < 0
            or values[5] is None
            or values[5] <= 0
            or not bar.normalization_identity.strip()
        ):
            continue
        eligible.append(bar)
        identities.add(bar.normalization_identity)
        factors.add(float(values[5]))

    if eligible and (len(identities) != 1 or len(factors) != 1):
        reasons.append("intraday_normalization_identity_conflict")
    if len(eligible) < MORNING_MIN_CLOSED_BARS:
        reasons.append("insufficient_closed_10m_bars")
    if eligible:
        first_start = _aware_shanghai(eligible[0].bar_start)
        if first_start.time() != time(9, 30):
            reasons.append("morning_bar_sequence_does_not_start_at_open")
        if any(
            _aware_shanghai(current.bar_start) != _aware_shanghai(previous.bar_end)
            for previous, current in zip(eligible, eligible[1:], strict=False)
        ):
            reasons.append("morning_bar_sequence_has_gap")

    latest = eligible[-1] if eligible else None
    current_price = None
    cumulative_volume = None
    cumulative_ratio = None
    overextension = None
    if latest is not None and len(factors) == 1:
        factor = next(iter(factors))
        current_price = float(latest.raw_close) * factor
        cumulative_volume = sum(float(row.volume) for row in eligible)
        if baseline is not None and baseline > 0:
            cumulative_ratio = cumulative_volume / baseline
        overextension = (
            abs(current_price - candidate.signal_adjusted_ma20)
            / candidate.signal_adjusted_atr20
            if candidate.signal_adjusted_atr20 > 0
            else None
        )

    if not reasons:
        if cumulative_ratio is None or cumulative_ratio < MORNING_CUMULATIVE_VOLUME_RATIO_MIN:
            reasons.append("morning_cumulative_volume_not_confirmed")
        if current_price is None or current_price <= candidate.signal_adjusted_close:
            reasons.append("morning_price_not_above_signal_close")
        if current_price is None or current_price < candidate.signal_adjusted_ma5:
            reasons.append("morning_price_below_signal_ma5")
        if overextension is None or overextension > MORNING_OVEREXTENSION_ATR_MAX:
            reasons.append("morning_atr_extension_unsafe")

    confirmed = not reasons
    reason = "morning_volume_and_price_confirmed" if confirmed else reasons[0]
    payload = {
        "schema_version": MORNING_CONFIRMATION_SCHEMA_VERSION,
        "manifest_hash": candidate.manifest_hash,
        "asset_code": candidate.asset_code,
        "signal_date": candidate.signal_date,
        "decision_at": cutoff,
        "confirmed": confirmed,
        "reason": reason,
        "closed_bar_count": len(eligible),
        "latest_bar_end": _aware_shanghai(latest.bar_end) if latest else None,
        "current_adjusted_price": current_price,
        "cumulative_volume": cumulative_volume,
        "prior_20_mean_daily_volume": baseline,
        "cumulative_volume_ratio": cumulative_ratio,
        "overextension_atr": overextension,
        "normalization_identity": next(iter(identities)) if len(identities) == 1 else None,
    }
    return MorningConfirmationResult(
        available=not any(
            item
            in {
                "outside_morning_confirmation_window",
                "signal_not_from_prior_session",
                "prior_20_daily_volume_unavailable",
                "intraday_normalization_identity_conflict",
                "insufficient_closed_10m_bars",
                "morning_bar_sequence_does_not_start_at_open",
                "morning_bar_sequence_has_gap",
            }
            for item in reasons
        ),
        confirmed=confirmed,
        entry_status="actionable" if confirmed else "watch",
        reason=reason,
        decision_at=cutoff,
        closed_bar_count=len(eligible),
        latest_bar_end=_aware_shanghai(latest.bar_end) if latest else None,
        current_adjusted_price=current_price,
        cumulative_volume=cumulative_volume,
        prior_20_mean_daily_volume=baseline,
        cumulative_volume_ratio=cumulative_ratio,
        overextension_atr=overextension,
        evidence_hash=stable_contract_hash(payload),
    )


async def read_low_base_morning_watch_pool(
    session: AsyncSession,
    *,
    decision_at: datetime,
    limit: int = MORNING_MAX_WATCH_POOL,
) -> tuple[MorningWatchCandidate, ...]:
    if not 1 <= limit <= MORNING_MAX_WATCH_POOL:
        raise ValueError("morning watch pool limit is out of bounds")
    cutoff = _aware_shanghai(decision_at)
    manifest = await get_v2_materialized_manifest(
        session,
        universe="ashare",
        as_of=cutoff.replace(tzinfo=None),
    )
    if manifest is None:
        return ()
    rows = (
        await session.execute(
            text(
                """
                SELECT manifest_hash, asset_code, asset_name, signal_date,
                       source_cutoff, gate_facts_json, feature_hash
                FROM leader_tactics_v2_candidate_observations
                WHERE manifest_hash = :manifest_hash
                  AND universe = 'ashare'
                  AND formula_id = :formula_id
                  AND availability = 'available'
                  AND state IN ('turning_watch', 'preparing')
                  AND signal_date < :decision_date
                ORDER BY signal_date DESC, asset_code ASC
                """
            ),
            {
                "manifest_hash": str(manifest["manifest_hash"]),
                "formula_id": LOW_BASE_CATCHUP_V1,
                "decision_date": cutoff.date(),
            },
        )
    ).mappings().all()
    candidates: list[MorningWatchCandidate] = []
    for row in rows:
        facts = _decode(row["gate_facts_json"])
        if facts.get("single_day_volume_watch") is not True:
            continue
        if str(facts.get("entry_status") or "") != "watch":
            continue
        next_date = facts.get("next_eligible_date")
        if str(next_date or "") != cutoff.date().isoformat():
            continue
        values = {
            "signal_adjusted_close": _finite(facts.get("signal_adjusted_close")),
            "signal_adjusted_high": _finite(facts.get("signal_adjusted_high")),
            "signal_adjusted_ma5": _finite(facts.get("adjusted_ma5")),
            "signal_adjusted_ma20": _finite(facts.get("adjusted_ma20")),
            "signal_adjusted_atr20": _finite(facts.get("adjusted_atr20")),
        }
        if any(value is None or value <= 0 for value in values.values()):
            continue
        source_cutoff = row["source_cutoff"]
        if isinstance(source_cutoff, str):
            source_cutoff = datetime.fromisoformat(source_cutoff)
        signal_date = row["signal_date"]
        if isinstance(signal_date, str):
            signal_date = date.fromisoformat(signal_date)
        candidates.append(
            MorningWatchCandidate(
                manifest_hash=str(row["manifest_hash"]),
                asset_code=str(row["asset_code"])[-6:],
                asset_name=str(row["asset_name"]),
                signal_date=signal_date,
                source_cutoff=source_cutoff,
                feature_hash=str(row["feature_hash"]),
                **{key: float(value) for key, value in values.items()},
            )
        )
    candidates.sort(key=lambda item: (item.signal_date, item.asset_code), reverse=True)
    return tuple(candidates[:limit])


async def _read_confirmation_inputs(
    session: AsyncSession,
    *,
    candidate: MorningWatchCandidate,
    decision_at: datetime,
) -> tuple[tuple[MorningConfirmationBar, ...], float | None]:
    cutoff = _aware_shanghai(decision_at)
    local_cutoff = cutoff.replace(tzinfo=None)
    daily_rows = (
        await session.execute(
            text(
                """
                SELECT volume
                FROM (
                    SELECT facts.volume, facts.trade_date,
                           ROW_NUMBER() OVER (
                               PARTITION BY facts.trade_date
                               ORDER BY facts.received_at DESC,
                                        facts.revision_id DESC, facts.id DESC
                           ) AS revision_rank
                    FROM ashare_adjusted_price_facts facts
                    WHERE facts.asset_code = :asset_code
                      AND facts.trade_date < :signal_date
                      AND facts.received_at <= :signal_cutoff
                      AND facts.decision_eligible = TRUE
                      AND facts.historical_research_only = FALSE
                      AND facts.price_basis = :price_basis
                ) ranked
                WHERE revision_rank = 1
                ORDER BY trade_date DESC
                LIMIT 20
                """
            ),
            {
                "asset_code": candidate.asset_code,
                "signal_date": candidate.signal_date,
                "signal_cutoff": candidate.source_cutoff,
                "price_basis": PRICE_BASIS,
            },
        )
    ).scalars().all()
    volumes = [value for raw in daily_rows if (value := _finite(raw)) is not None and value >= 0]
    baseline = sum(volumes) / len(volumes) if len(volumes) == 20 else None
    rows = (
        await session.execute(
            text(
                """
                SELECT bar_start, bar_end, raw_open, raw_high, raw_low, raw_close,
                       volume, received_at, normalization_factor,
                       normalization_identity, decision_eligible
                FROM (
                    SELECT facts.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY facts.bar_start, facts.bar_end
                               ORDER BY facts.received_at DESC, facts.id DESC
                           ) AS revision_rank
                    FROM ashare_intraday_10m_facts facts
                    WHERE facts.asset_code = :asset_code
                      AND facts.trade_date = :trade_date
                      AND facts.bar_end <= :decision_at
                      AND facts.received_at <= :decision_at
                      AND facts.decision_eligible = TRUE
                ) ranked
                WHERE revision_rank = 1
                ORDER BY bar_end ASC, received_at ASC
                """
            ),
            {
                "asset_code": candidate.asset_code,
                "trade_date": cutoff.date(),
                "decision_at": local_cutoff,
            },
        )
    ).mappings().all()
    bars = tuple(
        MorningConfirmationBar(
            bar_start=row["bar_start"],
            bar_end=row["bar_end"],
            raw_open=float(row["raw_open"]),
            raw_high=float(row["raw_high"]),
            raw_low=float(row["raw_low"]),
            raw_close=float(row["raw_close"]),
            volume=float(row["volume"]),
            received_at=row["received_at"],
            normalization_factor=float(row["normalization_factor"]),
            normalization_identity=str(row["normalization_identity"]),
            decision_eligible=bool(row["decision_eligible"]),
        )
        for row in rows
    )
    return bars, baseline


async def persist_confirmed_morning_transition(
    session: AsyncSession,
    *,
    candidate: MorningWatchCandidate,
    result: MorningConfirmationResult,
) -> int:
    if not result.confirmed:
        return 0
    assert_v2_research_table("leader_tactics_v2_state_transitions")
    payload = {
        "schema_version": MORNING_CONFIRMATION_SCHEMA_VERSION,
        "manifest_hash": candidate.manifest_hash,
        "universe": "ashare",
        "asset_code": candidate.asset_code,
        "formula_id": LOW_BASE_CATCHUP_V1,
        "signal_date": candidate.signal_date,
        "from_state": "turning_watch",
        "to_state": "confirmed",
        "transition_date": result.decision_at.date(),
        "decision_at": result.decision_at,
        "entry_status": "actionable",
        "reason": result.reason,
        "intraday_confirmation": asdict(result),
        "research_only": True,
        "notification_provenance": "none",
        "execution_provenance": "none",
    }
    transition_hash = stable_contract_hash(payload)
    inserted = await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_state_transitions
                (manifest_hash, universe, asset_code, formula_id, signal_date,
                 from_state, to_state, transition_date, payload_json,
                 transition_hash, created_at, evidence_cutoff,
                 projected_entry_status)
            VALUES
                (:manifest_hash, 'ashare', :asset_code, :formula_id, :signal_date,
                 'turning_watch', 'confirmed', :transition_date, :payload_json,
                 :transition_hash, :created_at, :evidence_cutoff, 'actionable')
            ON CONFLICT (transition_hash) DO NOTHING
            """
        ),
        {
            "manifest_hash": candidate.manifest_hash,
            "asset_code": candidate.asset_code,
            "formula_id": LOW_BASE_CATCHUP_V1,
            "signal_date": candidate.signal_date,
            "transition_date": result.decision_at.date(),
            "payload_json": json.dumps(
                payload,
                default=str,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "transition_hash": transition_hash,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
            "evidence_cutoff": result.decision_at.replace(tzinfo=None),
        },
    )
    return max(0, int(inserted.rowcount or 0))


async def materialize_low_base_morning_confirmations(
    session: AsyncSession,
    *,
    decision_at: datetime,
) -> dict[str, Any]:
    pool = await read_low_base_morning_watch_pool(session, decision_at=decision_at)
    evaluated = 0
    confirmed = 0
    reasons: dict[str, int] = {}
    for candidate in pool:
        bars, baseline = await _read_confirmation_inputs(
            session,
            candidate=candidate,
            decision_at=decision_at,
        )
        result = evaluate_low_base_morning_confirmation(
            candidate=candidate,
            decision_at=decision_at,
            bars=bars,
            prior_20_mean_daily_volume=baseline,
        )
        evaluated += 1
        reasons[result.reason] = reasons.get(result.reason, 0) + 1
        if result.confirmed:
            confirmed += await persist_confirmed_morning_transition(
                session,
                candidate=candidate,
                result=result,
            )
    await session.commit()
    return {
        "status": "complete",
        "schema_version": MORNING_CONFIRMATION_SCHEMA_VERSION,
        "decision_at": _aware_shanghai(decision_at).isoformat(),
        "declared_count": len(pool),
        "evaluated_count": evaluated,
        "confirmed_count": confirmed,
        "reason_counts": dict(sorted(reasons.items())),
        "research_only": True,
        "notification_provenance": "none",
        "execution_provenance": "none",
        "production_mutation_allowed": False,
    }


__all__ = [
    "MORNING_CONFIRMATION_END",
    "MORNING_CONFIRMATION_SCHEMA_VERSION",
    "MORNING_CONFIRMATION_START",
    "MORNING_MAX_WATCH_POOL",
    "MORNING_MIN_CLOSED_BARS",
    "MorningConfirmationBar",
    "MorningConfirmationResult",
    "MorningWatchCandidate",
    "evaluate_low_base_morning_confirmation",
    "materialize_low_base_morning_confirmations",
    "persist_confirmed_morning_transition",
    "read_low_base_morning_watch_pool",
]
