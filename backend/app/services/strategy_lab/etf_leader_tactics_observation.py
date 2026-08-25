"""Immutable contracts for prospective ETF leader-shadow observations."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from statistics import mean
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentEvidence
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_leader_entry_quality import (
    assess_leader_breakout_entry_quality,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    BREAKOUT_HISTORY_SESSIONS,
    CYCLE_ROUTED_LEADER_CANDIDATE,
    FORMER_LEADER_REPAIR_CANDIDATE,
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_BREAKOUT_CANDIDATE,
    LEADER_CANDIDATE_IDS,
    LEADER_HYPOTHESIS_REGISTRY,
    MINIMUM_PEER_COUNT,
    REPAIR_HISTORY_SESSIONS,
    LeaderPitAssetInput,
    percentile_ranks,
    validate_leader_pit_input,
)

LEADER_OBSERVATION_SCHEMA_VERSION = "etf_leader_tactics_observation_v1"
LEADER_MATURITY_SCHEMA_VERSION = "etf_leader_tactics_maturity_v1"
LEADER_OBSERVATION_EXPERIMENT_FAMILY = "leader_tactics_observation_v1"
LEADER_MATURITY_EXPERIMENT_FAMILY = "leader_tactics_maturity_v1"
LEADER_OBSERVATION_REPORT_KIND = "session_observation"
LEADER_MATURITY_REPORT_KIND = "outcome_maturity"

MINIMUM_PROMOTION_PIT_SESSIONS = 252
MINIMUM_PROMOTION_INDEPENDENT_DATES = 40
MINIMUM_PROMOTION_WALK_FORWARD_FOLDS = 3

LEADER_OBSERVATION_SOURCE_UNAVAILABLE = "leader_observation_source_unavailable"
LEADER_OBSERVATION_SOURCE_INCOMPATIBLE = "leader_observation_source_incompatible"
LEADER_OBSERVATION_PARTIAL = "leader_observation_cross_section_partial"
LEADER_OBSERVATION_ZERO_MATCH = "leader_observation_zero_match"
LEADER_OBSERVATION_OUTCOMES_PENDING = "leader_observation_outcomes_pending"
LEADER_OBSERVATION_DATES_INSUFFICIENT = "leader_observation_dates_below_252"
LEADER_OBSERVATION_INDEPENDENT_DATES_INSUFFICIENT = (
    "leader_observation_independent_dates_below_40"
)
LEADER_OBSERVATION_FOLDS_INSUFFICIENT = (
    "leader_observation_walk_forward_folds_below_3"
)


class LeaderObservationContractError(ValueError):
    """Raised when an observation or maturity identity is not canonical."""


@dataclass(frozen=True)
class LeaderObservationPrimitive:
    """Compact, source-bound inputs sufficient for the frozen proxy formulas."""

    asset_code: str
    signal_date: date
    source_cutoff: datetime
    baseline_score: float | None
    peer_group: str | None
    clone_group: str
    tracked_index: str | None
    issuer: str | None
    theme: str | None
    sector: str | None
    market_regime: str | None
    market_regime_status: str
    market_regime_contract_hash: str | None
    breakout_unavailable_reasons: tuple[str, ...]
    repair_unavailable_reasons: tuple[str, ...]
    history_tier: str
    sector_trend_score: float | None
    current_return20: float | None
    average_turnover20: float | None
    prior_return20_by_date: tuple[tuple[str, float], ...]
    adjusted_ma5: float | None
    adjusted_ma10: float | None
    adjusted_ma20: float | None
    adjusted_close: float | None
    adjusted_open: float | None
    prior_adjusted_close: float | None
    preceding_20_adjusted_high: float | None
    current_volume: float | None
    latest_120_volume_max: float | None
    maximum_120_adjusted_close: float | None
    adjusted_atr5: float | None
    adjusted_atr20: float | None
    primitive_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("primitive_hash")
        return payload

    def validate(self) -> None:
        if (
            not self.asset_code.strip()
            or not _aware(self.source_cutoff)
            or not _is_sha256(self.primitive_hash)
            or self.primitive_hash
            != stable_contract_hash(self.canonical_payload())
        ):
            raise LeaderObservationContractError(
                "leader observation primitive is incompatible"
            )
        numeric = (
            self.baseline_score,
            self.sector_trend_score,
            self.current_return20,
            self.average_turnover20,
            self.adjusted_ma5,
            self.adjusted_ma10,
            self.adjusted_ma20,
            self.adjusted_close,
            self.adjusted_open,
            self.prior_adjusted_close,
            self.preceding_20_adjusted_high,
            self.current_volume,
            self.latest_120_volume_max,
            self.maximum_120_adjusted_close,
            self.adjusted_atr5,
            self.adjusted_atr20,
        )
        if any(value is not None and not _finite(value) for value in numeric):
            raise LeaderObservationContractError(
                "leader observation primitive contains non-finite values"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "signal_date": self.signal_date.isoformat(),
            "source_cutoff": self.source_cutoff.isoformat(),
            "prior_return20_by_date": [
                [session_date, value]
                for session_date, value in self.prior_return20_by_date
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LeaderObservationPrimitive:
        value = cls(
            **{
                **payload,
                "signal_date": date.fromisoformat(str(payload["signal_date"])),
                "source_cutoff": datetime.fromisoformat(
                    str(payload["source_cutoff"])
                ),
                "breakout_unavailable_reasons": tuple(
                    payload.get("breakout_unavailable_reasons") or ()
                ),
                "repair_unavailable_reasons": tuple(
                    payload.get("repair_unavailable_reasons") or ()
                ),
                "prior_return20_by_date": tuple(
                    (str(item[0]), float(item[1]))
                    for item in payload.get("prior_return20_by_date") or ()
                ),
            }
        )
        value.validate()
        return value


@dataclass(frozen=True)
class LeaderObservationFinalization:
    observation_hash: str
    current_matches: tuple[LeaderObservationMatch, ...]
    available_observation_count: int
    qualifying_observation_count: int
    exclusion_counts: dict[str, int]
    pending_outcomes: tuple[dict[str, Any], ...]
    all_observation_hashes: tuple[str, ...]


@dataclass(frozen=True)
class LeaderMaturedOutcome:
    candidate_id: str
    asset_code: str
    signal_date: date
    horizon_sessions: int
    status: Literal["matured", "pending", "unavailable"]
    entry_date: date | None
    exit_date: date | None
    gross_return: float | None
    net_return: float | None
    unavailable_reason: str | None
    feature_hash: str
    outcome_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("outcome_hash")
        return payload

    def validate(self) -> None:
        if (
            self.candidate_id not in FROZEN_LEADER_CANDIDATE_REGISTRY.by_id
            or not self.asset_code.strip()
            or self.horizon_sessions not in {5, 10}
            or not _is_sha256(self.feature_hash)
            or self.outcome_hash
            != stable_contract_hash(self.canonical_payload())
        ):
            raise LeaderObservationContractError(
                "leader matured outcome is incompatible"
            )
        if self.status == "matured":
            if (
                self.entry_date is None
                or self.exit_date is None
                or not _finite(self.gross_return)
                or not _finite(self.net_return)
                or self.unavailable_reason is not None
            ):
                raise LeaderObservationContractError(
                    "matured leader outcome is incomplete"
                )
        elif self.gross_return is not None or self.net_return is not None:
            raise LeaderObservationContractError(
                "pending leader outcome cannot fabricate a return"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "signal_date": self.signal_date.isoformat(),
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "exit_date": self.exit_date.isoformat() if self.exit_date else None,
        }


def build_leader_matured_outcome(
    *,
    candidate_id: str,
    asset_code: str,
    signal_date: date,
    horizon_sessions: int,
    feature_hash: str,
    adjusted_closes_after_signal: tuple[tuple[date, float], ...],
    round_trip_cost_bps: float,
) -> LeaderMaturedOutcome:
    """Use T+1 close and a full-session exit; incomplete windows stay pending."""

    status: Literal["matured", "pending", "unavailable"] = "pending"
    entry_date: date | None = None
    exit_date: date | None = None
    gross_return: float | None = None
    net_return: float | None = None
    reason: str | None = "future_window_pending"
    if len(adjusted_closes_after_signal) > horizon_sessions:
        entry_date, entry_close = adjusted_closes_after_signal[0]
        exit_date, exit_close = adjusted_closes_after_signal[horizon_sessions]
        if (
            entry_close > 0
            and exit_close > 0
            and _finite(entry_close)
            and _finite(exit_close)
            and _finite(round_trip_cost_bps)
            and round_trip_cost_bps >= 0
        ):
            status = "matured"
            gross_return = exit_close / entry_close - 1.0
            net_return = gross_return - round_trip_cost_bps / 10_000.0
            reason = None
        else:
            status = "unavailable"
            reason = "decision_eligible_adjusted_price_invalid"
    draft = LeaderMaturedOutcome(
        candidate_id=candidate_id,
        asset_code=asset_code,
        signal_date=signal_date,
        horizon_sessions=horizon_sessions,
        status=status,
        entry_date=entry_date,
        exit_date=exit_date,
        gross_return=gross_return,
        net_return=net_return,
        unavailable_reason=reason,
        feature_hash=feature_hash,
        outcome_hash="pending",
    )
    value = LeaderMaturedOutcome(
        **{
            **asdict(draft),
            "outcome_hash": stable_contract_hash(draft.canonical_payload()),
        }
    )
    value.validate()
    return value


def _average(values: tuple[float, ...]) -> float | None:
    return mean(values) if values else None


def _moving_average(item: LeaderPitAssetInput, sessions: int) -> float | None:
    if len(item.bars) < sessions:
        return None
    return mean(bar.adjusted_close for bar in item.bars[-sessions:])


def _atr(item: LeaderPitAssetInput, sessions: int) -> float | None:
    if len(item.bars) < sessions + 1:
        return None
    window = item.bars[-(sessions + 1) :]
    ranges = tuple(
        max(
            current.adjusted_high - current.adjusted_low,
            abs(current.adjusted_high - previous.adjusted_close),
            abs(current.adjusted_low - previous.adjusted_close),
        )
        for previous, current in zip(window[:-1], window[1:], strict=True)
    )
    value = _average(ranges)
    return value if value is not None and value > 0 and _finite(value) else None


def _return_at_index(item: LeaderPitAssetInput, index: int, sessions: int) -> float | None:
    if index < sessions:
        return None
    start = item.bars[index - sessions].adjusted_close
    end = item.bars[index].adjusted_close
    if start <= 0:
        return None
    value = end / start - 1.0
    return value if _finite(value) else None


def build_leader_observation_primitive(
    item: LeaderPitAssetInput,
) -> LeaderObservationPrimitive:
    """Reduce one 180-session input before the cross-section is finalized."""

    breakout_reasons = validate_leader_pit_input(
        item,
        required_history=BREAKOUT_HISTORY_SESSIONS,
    )
    repair_reasons = validate_leader_pit_input(
        item,
        required_history=REPAIR_HISTORY_SESSIONS,
        include_sector=False,
    )
    bars = item.bars
    prior_returns: list[tuple[str, float]] = []
    if len(bars) >= REPAIR_HISTORY_SESSIONS:
        start_index = len(bars) - 120
        end_index = len(bars) - 20
        for index in range(start_index, end_index):
            value = _return_at_index(item, index, 20)
            if value is not None:
                prior_returns.append((bars[index].trade_date.isoformat(), value))
    current_return20 = (
        _return_at_index(item, len(bars) - 1, 20)
        if len(bars) >= 21
        else None
    )
    average_turnover20 = (
        mean(bar.turnover for bar in bars[-20:]) if len(bars) >= 20 else None
    )
    draft = LeaderObservationPrimitive(
        asset_code=item.asset_code,
        signal_date=item.signal_date,
        source_cutoff=item.source_cutoff,
        baseline_score=item.baseline_score,
        peer_group=item.peer_group,
        clone_group=item.clone_group or item.asset_code,
        tracked_index=item.tracked_index,
        issuer=item.issuer,
        theme=item.theme,
        sector=item.sector,
        market_regime=item.market_regime,
        market_regime_status=item.market_regime_status,
        market_regime_contract_hash=item.market_regime_contract_hash,
        breakout_unavailable_reasons=breakout_reasons,
        repair_unavailable_reasons=repair_reasons,
        history_tier=(
            "full_history_context"
            if len(bars) >= 250
            else "standard_history"
            if len(bars) >= 120
            else "provisional_short_history"
            if len(bars) >= 61
            else "insufficient_history"
        ),
        sector_trend_score=item.sector_trend_score,
        current_return20=current_return20,
        average_turnover20=average_turnover20,
        prior_return20_by_date=tuple(prior_returns),
        adjusted_ma5=_moving_average(item, 5),
        adjusted_ma10=_moving_average(item, 10),
        adjusted_ma20=_moving_average(item, 20),
        adjusted_close=bars[-1].adjusted_close if bars else None,
        adjusted_open=bars[-1].adjusted_open if bars else None,
        prior_adjusted_close=bars[-2].adjusted_close if len(bars) >= 2 else None,
        preceding_20_adjusted_high=(
            max(bar.adjusted_high for bar in bars[-21:-1])
            if len(bars) >= 21
            else None
        ),
        current_volume=bars[-1].volume if bars else None,
        latest_120_volume_max=(
            max(bar.volume for bar in bars[-120:]) if len(bars) >= 120 else None
        ),
        maximum_120_adjusted_close=(
            max(bar.adjusted_close for bar in bars[-120:])
            if len(bars) >= 120
            else None
        ),
        adjusted_atr5=_atr(item, 5),
        adjusted_atr20=_atr(item, 20),
        primitive_hash="pending",
    )
    value = LeaderObservationPrimitive(
        **{
            **asdict(draft),
            "primitive_hash": stable_contract_hash(draft.canonical_payload()),
        }
    )
    value.validate()
    return value


def _observation_payload(
    *,
    primitive: LeaderObservationPrimitive,
    candidate_id: str,
    availability: str,
    qualifies: bool,
    score: float | None,
    components: dict[str, Any],
    gate_reasons: tuple[str, ...] = (),
    unavailable_reasons: tuple[str, ...] = (),
) -> dict[str, Any]:
    payload = {
        "candidate_id": candidate_id,
        "asset_code": primitive.asset_code,
        "signal_date": primitive.signal_date.isoformat(),
        "source_cutoff": primitive.source_cutoff.isoformat(),
        "availability": availability,
        "qualifies": qualifies,
        "score": score,
        "peer_group": primitive.peer_group,
        "theme": primitive.theme,
        "sector": primitive.sector,
        "clone_group": primitive.clone_group,
        "tracked_index": primitive.tracked_index,
        "issuer": primitive.issuer,
        "history_tier": primitive.history_tier,
        "components": dict(sorted(components.items())),
        "gate_reasons": list(sorted(set(gate_reasons))),
        "unavailable_reasons": list(sorted(set(unavailable_reasons))),
    }
    payload["feature_hash"] = stable_contract_hash(payload)
    return payload


def finalize_leader_observation_primitives(
    primitives: tuple[LeaderObservationPrimitive, ...],
) -> LeaderObservationFinalization:
    """Seal one full same-session cross-section and only then assign ranks."""

    if not primitives:
        raise LeaderObservationContractError(
            "leader observation finalization requires primitives"
        )
    ordered = tuple(sorted(primitives, key=lambda item: item.asset_code))
    if len({item.asset_code for item in ordered}) != len(ordered):
        raise LeaderObservationContractError(
            "leader observation primitive assets must be unique"
        )
    for item in ordered:
        item.validate()
    if len({item.signal_date for item in ordered}) != 1 or len(
        {item.source_cutoff for item in ordered}
    ) != 1:
        raise LeaderObservationContractError(
            "leader observation primitives must share one PIT cutoff"
        )

    breakout_peer = [item for item in ordered if not item.breakout_unavailable_reasons]
    repair_peer = [item for item in ordered if not item.repair_unavailable_reasons]
    groups: dict[str, list[LeaderObservationPrimitive]] = defaultdict(list)
    for item in breakout_peer:
        if item.peer_group:
            groups[item.peer_group].append(item)
    return_pct: dict[str, float] = {}
    turnover_pct: dict[str, float] = {}
    breakout_peer_count: dict[str, int] = {}
    for items in groups.values():
        returns = {
            item.asset_code: item.current_return20
            for item in items
            if item.current_return20 is not None
        }
        turnovers = {
            item.asset_code: item.average_turnover20
            for item in items
            if item.average_turnover20 is not None
        }
        return_pct.update(percentile_ranks(returns))
        turnover_pct.update(percentile_ranks(turnovers))
        for item in items:
            breakout_peer_count[item.asset_code] = len(returns)

    sector_values: dict[str, set[float]] = defaultdict(set)
    for item in breakout_peer:
        if item.peer_group and item.sector_trend_score is not None:
            sector_values[item.peer_group].add(float(item.sector_trend_score))
    canonical_sector = {
        group: next(iter(values))
        for group, values in sector_values.items()
        if len(values) == 1
    }
    sector_group_pct = percentile_ranks(canonical_sector)

    repair_groups: dict[str, list[LeaderObservationPrimitive]] = defaultdict(list)
    for item in repair_peer:
        if item.peer_group:
            repair_groups[item.peer_group].append(item)
    prior_leadership: dict[str, float] = {}
    repair_peer_count: dict[str, int] = {}
    for items in repair_groups.values():
        current_returns = {
            item.asset_code: item.current_return20
            for item in items
            if item.current_return20 is not None
        }
        prior_by_code = {
            item.asset_code: dict(item.prior_return20_by_date) for item in items
        }
        all_dates = sorted(
            {session_date for values in prior_by_code.values() for session_date in values}
        )
        best: dict[str, float] = {}
        for session_date in all_dates:
            values = {
                code: history[session_date]
                for code, history in prior_by_code.items()
                if session_date in history
            }
            if len(values) < MINIMUM_PEER_COUNT:
                continue
            for code, percentile in percentile_ranks(values).items():
                best[code] = max(best.get(code, percentile), percentile)
        prior_leadership.update(best)
        for item in items:
            repair_peer_count[item.asset_code] = len(current_returns)

    observations: list[dict[str, Any]] = []
    repair_raw: dict[str, dict[str, float]] = {}
    repair_gates: dict[str, tuple[str, ...]] = {}
    for item in ordered:
        sector_pct = (
            sector_group_pct.get(item.peer_group) if item.peer_group else None
        )
        breakout_components = {
            "sector_trend_percentile": sector_pct,
            "peer_return20_percentile": return_pct.get(item.asset_code),
            "peer_turnover20_percentile": turnover_pct.get(item.asset_code),
            "peer_count": breakout_peer_count.get(item.asset_code, 0),
            "adjusted_ma5": item.adjusted_ma5,
            "adjusted_ma10": item.adjusted_ma10,
            "adjusted_ma20": item.adjusted_ma20,
            "adjusted_close": item.adjusted_close,
            "preceding_20_adjusted_high": item.preceding_20_adjusted_high,
            "current_volume": item.current_volume,
            "latest_120_volume_max": item.latest_120_volume_max,
            "average_turnover20": item.average_turnover20,
        }
        breakout_components.update(
            assess_leader_breakout_entry_quality(
                adjusted_close=item.adjusted_close,
                preceding_adjusted_high=item.preceding_20_adjusted_high,
                adjusted_ma20=item.adjusted_ma20,
                adjusted_atr20=item.adjusted_atr20,
                volume_confirmed=(
                    item.current_volume >= item.latest_120_volume_max
                    if item.current_volume is not None
                    and item.latest_120_volume_max is not None
                    else None
                ),
            ).component_payload()
        )
        if item.breakout_unavailable_reasons:
            breakout = _observation_payload(
                primitive=item,
                candidate_id=LEADER_BREAKOUT_CANDIDATE,
                availability="unavailable",
                qualifies=False,
                score=None,
                components=breakout_components,
                unavailable_reasons=item.breakout_unavailable_reasons,
            )
        else:
            missing = tuple(
                label
                for label, value in (
                    ("sector_percentile_unavailable", sector_pct),
                    ("peer_return_percentile_unavailable", return_pct.get(item.asset_code)),
                    ("peer_turnover_percentile_unavailable", turnover_pct.get(item.asset_code)),
                )
                if value is None
            )
            if missing:
                breakout = _observation_payload(
                    primitive=item,
                    candidate_id=LEADER_BREAKOUT_CANDIDATE,
                    availability="unavailable",
                    qualifies=False,
                    score=None,
                    components=breakout_components,
                    unavailable_reasons=missing,
                )
            else:
                gates = tuple(
                    key
                    for key, failed in (
                        ("insufficient_peer_count", breakout_peer_count.get(item.asset_code, 0) < MINIMUM_PEER_COUNT),
                        ("ma_alignment_failed", not (float(item.adjusted_ma5) > float(item.adjusted_ma10) > float(item.adjusted_ma20))),
                        ("price_breakout_failed", not (float(item.adjusted_close) > float(item.preceding_20_adjusted_high))),
                        ("volume_breakout_failed", not (float(item.current_volume) >= float(item.latest_120_volume_max))),
                        ("sector_heat_gate_failed", float(sector_pct) < 2 / 3),
                        ("peer_leadership_gate_failed", float(return_pct[item.asset_code]) < 0.8),
                        ("peer_liquidity_gate_failed", float(turnover_pct[item.asset_code]) < 0.5),
                    )
                    if failed
                )
                qualifies = not gates
                breakout = _observation_payload(
                    primitive=item,
                    candidate_id=LEADER_BREAKOUT_CANDIDATE,
                    availability="available",
                    qualifies=qualifies,
                    score=(mean((float(sector_pct), float(return_pct[item.asset_code]), float(turnover_pct[item.asset_code]))) if qualifies else None),
                    components=breakout_components,
                    gate_reasons=gates,
                )
        observations.append(breakout)

        if not item.repair_unavailable_reasons:
            prior = prior_leadership.get(item.asset_code)
            atr5 = item.adjusted_atr5
            atr20 = item.adjusted_atr20
            if prior is not None and atr5 is not None and atr20 is not None:
                drawdown = float(item.adjusted_close) / float(item.maximum_120_adjusted_close) - 1.0
                compression = atr5 / atr20
                overextension = abs(float(item.adjusted_close) - float(item.adjusted_ma20)) / atr20
                gates = tuple(
                    key
                    for key, failed in (
                        ("insufficient_peer_count", repair_peer_count.get(item.asset_code, 0) < MINIMUM_PEER_COUNT),
                        ("prior_leadership_gate_failed", prior < 0.8),
                        ("drawdown_band_failed", not (-0.50 <= drawdown <= -0.30)),
                        ("positive_stabilization_failed", not (float(item.adjusted_close) > float(item.adjusted_open) and float(item.adjusted_close) > float(item.prior_adjusted_close))),
                        ("range_compression_failed", compression > 0.75),
                        ("overextension_gate_failed", overextension > 1.0),
                    )
                    if failed
                )
                repair_gates[item.asset_code] = gates
                repair_raw[item.asset_code] = {
                    "prior_leadership_percentile": prior,
                    "drawdown_120": drawdown,
                    "adjusted_atr5": atr5,
                    "adjusted_atr20": atr20,
                    "atr5_atr20_ratio": compression,
                    "adjusted_ma20": float(item.adjusted_ma20),
                    "overextension_atr": overextension,
                    "adjusted_close": float(item.adjusted_close),
                    "adjusted_open": float(item.adjusted_open),
                    "prior_adjusted_close": float(item.prior_adjusted_close),
                    "average_turnover20": float(item.average_turnover20),
                    "peer_count": float(repair_peer_count.get(item.asset_code, 0)),
                }

    qualifying_repair = {
        code for code, gates in repair_gates.items() if not gates
    }
    reverse_compression = percentile_ranks(
        {code: repair_raw[code]["atr5_atr20_ratio"] for code in qualifying_repair},
        reverse=True,
    )
    reverse_overextension = percentile_ranks(
        {code: repair_raw[code]["overextension_atr"] for code in qualifying_repair},
        reverse=True,
    )
    by_code = {item.asset_code: item for item in ordered}
    repair_by_code: dict[str, dict[str, Any]] = {}
    for code, item in by_code.items():
        components = dict(repair_raw.get(code, {}))
        components["reverse_atr5_atr20_percentile"] = reverse_compression.get(code)
        components["reverse_overextension_atr_percentile"] = reverse_overextension.get(code)
        unavailable = item.repair_unavailable_reasons
        if not unavailable and code not in repair_raw:
            missing: list[str] = []
            if item.adjusted_atr5 is None:
                missing.append("adjusted_atr5_unavailable")
            if item.adjusted_atr20 is None:
                missing.append("adjusted_atr20_unavailable")
            if code not in prior_leadership:
                missing.append("prior_peer_leadership_unavailable")
            unavailable = tuple(missing)
        gates = repair_gates.get(code, ())
        qualifies = not unavailable and not gates
        repair = _observation_payload(
            primitive=item,
            candidate_id=FORMER_LEADER_REPAIR_CANDIDATE,
            availability="unavailable" if unavailable else "available",
            qualifies=qualifies,
            score=(mean((repair_raw[code]["prior_leadership_percentile"], reverse_compression[code], reverse_overextension[code])) if qualifies else None),
            components=components,
            gate_reasons=gates,
            unavailable_reasons=unavailable,
        )
        observations.append(repair)
        repair_by_code[code] = repair

    breakout_by_code = {
        row["asset_code"]: row
        for row in observations
        if row["candidate_id"] == LEADER_BREAKOUT_CANDIDATE
    }
    for code, item in by_code.items():
        regime_reasons: list[str] = []
        if item.market_regime_status != "available":
            regime_reasons.append(
                f"market_regime_{item.market_regime_status or 'unavailable'}"
            )
        if item.market_regime not in {"risk_on", "neutral", "defensive", "cash_wait"}:
            regime_reasons.append("market_regime_incompatible")
        if not _is_sha256(item.market_regime_contract_hash):
            regime_reasons.append("market_regime_contract_incompatible")
        components = {
            "market_regime": item.market_regime,
            "market_regime_contract_hash": item.market_regime_contract_hash,
            "routed_candidate_id": None,
        }
        if regime_reasons:
            routed = _observation_payload(
                primitive=item,
                candidate_id=CYCLE_ROUTED_LEADER_CANDIDATE,
                availability="unavailable",
                qualifies=False,
                score=None,
                components=components,
                unavailable_reasons=tuple(regime_reasons),
            )
        elif item.market_regime in {"defensive", "cash_wait"}:
            routed = _observation_payload(
                primitive=item,
                candidate_id=CYCLE_ROUTED_LEADER_CANDIDATE,
                availability="available",
                qualifies=False,
                score=None,
                components=components,
                gate_reasons=(f"market_regime_{item.market_regime}_no_selection",),
            )
        else:
            source = breakout_by_code[code] if item.market_regime == "risk_on" else repair_by_code[code]
            components["routed_candidate_id"] = source["candidate_id"]
            components["routed_feature_hash"] = source["feature_hash"]
            routed = _observation_payload(
                primitive=item,
                candidate_id=CYCLE_ROUTED_LEADER_CANDIDATE,
                availability=source["availability"],
                qualifies=bool(source["qualifies"]),
                score=source["score"],
                components=components,
                gate_reasons=tuple(source["gate_reasons"]),
                unavailable_reasons=tuple(source["unavailable_reasons"]),
            )
        observations.append(routed)

    # Clone selection is cross-sectional and must happen before ranking.
    for candidate_id in LEADER_CANDIDATE_IDS:
        clones: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in observations:
            if row["candidate_id"] == candidate_id and row["qualifies"]:
                clones[str(row["clone_group"])].append(row)
        for rows in clones.values():
            if len(rows) <= 1:
                continue
            representative = max(
                rows,
                key=lambda row: (
                    float(row["components"].get("average_turnover20") or 0.0),
                    str(row["asset_code"]),
                ),
            )
            for row in rows:
                if row is representative:
                    continue
                row["qualifies"] = False
                row["score"] = None
                row["gate_reasons"] = sorted(
                    {*row["gate_reasons"], "clone_not_representative"}
                )
                identity = {key: value for key, value in row.items() if key != "feature_hash"}
                row["feature_hash"] = stable_contract_hash(identity)

    matches: list[LeaderObservationMatch] = []
    for candidate_id in LEADER_CANDIDATE_IDS:
        rows = sorted(
            (
                row
                for row in observations
                if row["candidate_id"] == candidate_id and row["qualifies"]
            ),
            key=lambda row: (-float(row["score"]), str(row["asset_code"])),
        )
        for rank, row in enumerate(rows, start=1):
            matches.append(
                LeaderObservationMatch(
                    candidate_id=candidate_id,
                    asset_code=str(row["asset_code"]),
                    asset_name=None,
                    score=float(row["score"]),
                    rank=rank,
                    matched_gates=tuple(row["gate_reasons"]),
                    feature_hash=str(row["feature_hash"]),
                )
            )
    matches.sort(key=lambda item: (LEADER_CANDIDATE_IDS.index(item.candidate_id), item.rank, item.asset_code))
    exclusion_counts: Counter[str] = Counter()
    for row in observations:
        exclusion_counts.update(row["unavailable_reasons"])
        if "clone_not_representative" in row["gate_reasons"]:
            exclusion_counts.update(("clone_not_representative",))
    pending = tuple(
        {
            "candidate_id": item.candidate_id,
            "asset_code": item.asset_code,
            "signal_date": ordered[0].signal_date.isoformat(),
            "pending_horizons": [5, 10],
            "feature_hash": item.feature_hash,
        }
        for item in matches[:20]
    )
    hashes = tuple(sorted(str(row["feature_hash"]) for row in observations))
    return LeaderObservationFinalization(
        observation_hash=stable_contract_hash(
            {
                "signal_date": ordered[0].signal_date,
                "source_cutoff": ordered[0].source_cutoff,
                "feature_hashes": hashes,
            }
        ),
        current_matches=tuple(matches[:20]),
        available_observation_count=sum(
            row["availability"] == "available" for row in observations
        ),
        qualifying_observation_count=len(matches),
        exclusion_counts=dict(sorted(exclusion_counts.items())),
        pending_outcomes=pending,
        all_observation_hashes=hashes,
    )


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    )


@dataclass(frozen=True)
class LeaderPromotionGateProgress:
    eligible_pit_sessions: int
    independent_primary_dates: int
    completed_walk_forward_folds: int

    def __post_init__(self) -> None:
        if min(
            self.eligible_pit_sessions,
            self.independent_primary_dates,
            self.completed_walk_forward_folds,
        ) < 0:
            raise LeaderObservationContractError(
                "leader promotion counts cannot be negative"
            )

    @property
    def accumulation_allowed(self) -> bool:
        return self.eligible_pit_sessions >= 1

    @property
    def failed_gates(self) -> tuple[str, ...]:
        failed: list[str] = []
        if self.eligible_pit_sessions < MINIMUM_PROMOTION_PIT_SESSIONS:
            failed.append(LEADER_OBSERVATION_DATES_INSUFFICIENT)
        if (
            self.independent_primary_dates
            < MINIMUM_PROMOTION_INDEPENDENT_DATES
        ):
            failed.append(LEADER_OBSERVATION_INDEPENDENT_DATES_INSUFFICIENT)
        if (
            self.completed_walk_forward_folds
            < MINIMUM_PROMOTION_WALK_FORWARD_FOLDS
        ):
            failed.append(LEADER_OBSERVATION_FOLDS_INSUFFICIENT)
        return tuple(failed)

    @property
    def sample_gates_passed(self) -> bool:
        return not self.failed_gates

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible_pit_sessions": self.eligible_pit_sessions,
            "required_pit_sessions": MINIMUM_PROMOTION_PIT_SESSIONS,
            "independent_primary_dates": self.independent_primary_dates,
            "required_independent_primary_dates": (
                MINIMUM_PROMOTION_INDEPENDENT_DATES
            ),
            "completed_walk_forward_folds": self.completed_walk_forward_folds,
            "required_walk_forward_folds": (
                MINIMUM_PROMOTION_WALK_FORWARD_FOLDS
            ),
            "accumulation_allowed": self.accumulation_allowed,
            "sample_gates_passed": self.sample_gates_passed,
            "failed_gates": list(self.failed_gates),
        }


@dataclass(frozen=True)
class LeaderObservationManifest:
    source_id: int
    source_signal_run_id: int
    signal_date: date
    source_context_hash: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    input_snapshot_hash: str
    ranking_contract_hash: str
    research_contract_hash: str
    hypothesis_registry_hash: str
    candidate_registry_hash: str
    data_cutoff: datetime
    code_version: str
    schema_version: str = LEADER_OBSERVATION_SCHEMA_VERSION

    def validate(self) -> None:
        if self.source_id < 1 or self.source_signal_run_id < 1:
            raise LeaderObservationContractError(
                "leader observation source identities are required"
            )
        if not self.code_version.strip() or not _aware(self.data_cutoff):
            raise LeaderObservationContractError(
                "leader observation code version and aware cutoff are required"
            )
        hashes = (
            self.source_context_hash,
            self.source_snapshot_hash,
            self.universe_manifest_hash,
            self.input_snapshot_hash,
            self.ranking_contract_hash,
            self.research_contract_hash,
            self.hypothesis_registry_hash,
            self.candidate_registry_hash,
        )
        if not all(_is_sha256(value) for value in hashes):
            raise LeaderObservationContractError(
                "leader observation identities must be SHA-256 hashes"
            )
        if (
            self.hypothesis_registry_hash
            != LEADER_HYPOTHESIS_REGISTRY.registry_hash
            or self.candidate_registry_hash
            != FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash
        ):
            raise LeaderObservationContractError(
                "leader observation registry identity is incompatible"
            )
        if self.schema_version != LEADER_OBSERVATION_SCHEMA_VERSION:
            raise LeaderObservationContractError(
                "leader observation schema is incompatible"
            )

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return stable_contract_hash(asdict(self))


@dataclass(frozen=True)
class LeaderOutcomeMaturityManifest:
    observation_manifest_hash: str
    observation_hash: str
    signal_date: date
    outcome_cutoff: datetime
    execution_cost_contract_hash: str
    adjusted_price_contract_hash: str
    code_version: str
    schema_version: str = LEADER_MATURITY_SCHEMA_VERSION

    def validate(self) -> None:
        hashes = (
            self.observation_manifest_hash,
            self.observation_hash,
            self.execution_cost_contract_hash,
            self.adjusted_price_contract_hash,
        )
        if not all(_is_sha256(value) for value in hashes):
            raise LeaderObservationContractError(
                "leader maturity identities must be SHA-256 hashes"
            )
        if not self.code_version.strip() or not _aware(self.outcome_cutoff):
            raise LeaderObservationContractError(
                "leader maturity code version and aware cutoff are required"
            )
        if self.schema_version != LEADER_MATURITY_SCHEMA_VERSION:
            raise LeaderObservationContractError(
                "leader maturity schema is incompatible"
            )

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return stable_contract_hash(asdict(self))


@dataclass(frozen=True)
class LeaderObservationMatch:
    candidate_id: str
    asset_code: str
    asset_name: str | None
    score: float
    rank: int
    matched_gates: tuple[str, ...]
    feature_hash: str

    def __post_init__(self) -> None:
        if (
            self.candidate_id
            not in FROZEN_LEADER_CANDIDATE_REGISTRY.by_id
            or not self.asset_code.strip()
            or not _finite(self.score)
            or self.rank < 1
            or not _is_sha256(self.feature_hash)
        ):
            raise LeaderObservationContractError(
                "leader observation match is incomplete or incompatible"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LeaderObservationProgress:
    source_id: int
    signal_date: date
    state: Literal["pending", "partial", "complete"]
    processed_asset_count: int
    total_asset_count: int
    page_size: int
    current_phase: str
    checkpoint_hash: str | None = None

    def __post_init__(self) -> None:
        if (
            self.source_id < 1
            or self.processed_asset_count < 0
            or self.total_asset_count < 0
            or self.processed_asset_count > self.total_asset_count
            or not 1 <= self.page_size <= 20
        ):
            raise LeaderObservationContractError(
                "leader observation progress is invalid"
            )
        if self.checkpoint_hash is not None and not _is_sha256(
            self.checkpoint_hash
        ):
            raise LeaderObservationContractError(
                "leader observation checkpoint hash is invalid"
            )
        if self.state == "complete" and (
            self.processed_asset_count != self.total_asset_count
        ):
            raise LeaderObservationContractError(
                "complete leader observation must seal the full cross-section"
            )

    @property
    def completion_ratio(self) -> float:
        if self.total_asset_count == 0:
            return 1.0 if self.state == "complete" else 0.0
        return self.processed_asset_count / self.total_asset_count

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "signal_date": self.signal_date.isoformat(),
            "completion_ratio": self.completion_ratio,
        }


@dataclass(frozen=True)
class LeaderObservationReport:
    manifest_hash: str
    observation_hash: str
    signal_date: date
    data_cutoff: datetime
    progress: LeaderObservationProgress
    promotion_gates: LeaderPromotionGateProgress
    current_matches: tuple[LeaderObservationMatch, ...] = ()
    exclusion_counts: dict[str, int] = field(default_factory=dict)
    available_observation_count: int = 0
    qualifying_observation_count: int = 0
    current_observations_truncated: bool = False
    pending_outcome_count: int = 0
    matured_outcome_count: int = 0
    report_kind: str = LEADER_OBSERVATION_REPORT_KIND
    schema_version: str = LEADER_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            not _is_sha256(self.manifest_hash)
            or not _is_sha256(self.observation_hash)
            or not _aware(self.data_cutoff)
            or self.pending_outcome_count < 0
            or self.matured_outcome_count < 0
            or self.available_observation_count < 0
            or self.qualifying_observation_count < 0
            or self.qualifying_observation_count < len(self.current_matches)
            or any(value < 0 for value in self.exclusion_counts.values())
        ):
            raise LeaderObservationContractError(
                "leader observation report is incomplete"
            )
        if self.progress.signal_date != self.signal_date:
            raise LeaderObservationContractError(
                "leader observation report date is inconsistent"
            )
        if self.report_kind != LEADER_OBSERVATION_REPORT_KIND:
            raise LeaderObservationContractError(
                "leader observation report kind is incompatible"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_kind": self.report_kind,
            "manifest_hash": self.manifest_hash,
            "observation_hash": self.observation_hash,
            "signal_date": self.signal_date.isoformat(),
            "data_cutoff": self.data_cutoff.isoformat(),
            "progress": self.progress.to_dict(),
            "promotion_gates": self.promotion_gates.to_dict(),
            "current_matches": [item.to_dict() for item in self.current_matches],
            "exclusion_counts": dict(sorted(self.exclusion_counts.items())),
            "available_observation_count": self.available_observation_count,
            "qualifying_observation_count": self.qualifying_observation_count,
            "current_observations_truncated": self.current_observations_truncated,
            "pending_outcome_count": self.pending_outcome_count,
            "matured_outcome_count": self.matured_outcome_count,
            "status": "insufficient_data",
            "research_only": True,
            "production_mutation_allowed": False,
        }


def leader_observation_evidence_payload(
    *,
    manifest: LeaderObservationManifest,
    report: LeaderObservationReport,
    pending_outcomes: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Build the read projection payload without implying final validation."""

    if report.manifest_hash != manifest.manifest_hash:
        raise LeaderObservationContractError(
            "leader observation report and manifest identities differ"
        )
    counts = report.promotion_gates.to_dict()
    counts.update(
        {
            "materialized_pit_sessions": (
                report.promotion_gates.eligible_pit_sessions
            ),
            "current_input_asset_count": report.progress.total_asset_count,
            "current_available_observation_count": (
                report.available_observation_count
            ),
            "current_qualifying_observation_count": (
                report.qualifying_observation_count
            ),
            "returned_current_observation_count": len(
                report.current_matches
            ),
            "current_observations_truncated": (
                report.current_observations_truncated
            ),
            "pending_outcome_count": report.pending_outcome_count,
            "matured_outcome_count": report.matured_outcome_count,
            "outcomes_by_horizon": [
                {
                    "horizon_sessions": horizon,
                    "pending": report.pending_outcome_count,
                    "matured": report.matured_outcome_count,
                    "unavailable": 0,
                }
                for horizon in (5, 10)
            ],
        }
    )
    return {
        **report.to_dict(),
        "experiment_family": LEADER_OBSERVATION_EXPERIMENT_FAMILY,
        "source_snapshot_hash": manifest.source_snapshot_hash,
        "universe_manifest_hash": manifest.universe_manifest_hash,
        "input_snapshot_hash": manifest.input_snapshot_hash,
        "ranking_contract_hash": manifest.ranking_contract_hash,
        "research_contract_hash": manifest.research_contract_hash,
        "hypothesis_registry_hash": manifest.hypothesis_registry_hash,
        "candidate_registry_hash": manifest.candidate_registry_hash,
        "ranking_source_kind": "research_replay",
        "policy_mode": "none",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "observation_state": (
            "observing" if report.progress.state == "complete" else "partial"
        ),
        "observation_unavailable_reason": (
            report.promotion_gates.failed_gates[0]
            if report.promotion_gates.failed_gates
            else None
        ),
        "observation_data_cutoff": report.data_cutoff.isoformat(),
        "observation_manifest_hash": manifest.manifest_hash,
        "observation_counts": counts,
        "current_observations": [
            {
                **item.to_dict(),
                "signal_date": report.signal_date.isoformat(),
                "source_cutoff": report.data_cutoff.isoformat(),
                "availability": "available",
                "qualifies": True,
                "peer_group": None,
                "theme": None,
                "sector": None,
                "gate_reasons": [],
                "unavailable_reasons": [],
                "components": {},
            }
            for item in report.current_matches
        ],
        "pending_outcomes": list(pending_outcomes),
        "partial_checkpoint": report.progress.to_dict(),
        "coverage": {
            "eligible_point_in_time_sessions": (
                report.promotion_gates.eligible_pit_sessions
            ),
            "complete_observation_sessions": (
                report.promotion_gates.eligible_pit_sessions
            ),
        },
    }


async def persist_leader_observation_evidence(
    session: AsyncSession,
    *,
    manifest: LeaderObservationManifest,
    report: LeaderObservationReport,
    pending_outcomes: tuple[dict[str, Any], ...] = (),
) -> EtfFactorExperimentEvidence:
    """Append one immutable observation summary and resume idempotently."""

    payload = leader_observation_evidence_payload(
        manifest=manifest,
        report=report,
        pending_outcomes=pending_outcomes,
    )
    evidence_hash = stable_contract_hash(payload)
    existing = await session.scalar(
        select(EtfFactorExperimentEvidence).where(
            EtfFactorExperimentEvidence.manifest_hash
            == manifest.manifest_hash
        )
    )
    if existing is not None:
        if (
            existing.experiment_family
            != LEADER_OBSERVATION_EXPERIMENT_FAMILY
            or existing.evidence_hash != evidence_hash
        ):
            raise LeaderObservationContractError(
                "leader observation manifest already has incompatible evidence"
            )
        return existing
    row = EtfFactorExperimentEvidence(
        manifest_hash=manifest.manifest_hash,
        ranking_contract_hash=manifest.ranking_contract_hash,
        code_version=manifest.code_version,
        experiment_family=LEADER_OBSERVATION_EXPERIMENT_FAMILY,
        hypothesis_registry_hash=manifest.hypothesis_registry_hash,
        evidence_hash=evidence_hash,
        samples_json=[item.to_dict() for item in report.current_matches],
        aggregates_json=report.promotion_gates.to_dict(),
        exclusions_json=[
            {"reason": reason, "count": count}
            for reason, count in sorted(report.exclusion_counts.items())
        ],
        intervals_json={},
        split_reports_json={},
        costs_json={},
        limitations_json=[
            LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice,
            "research observation only; not a buy signal or email trigger",
        ],
        promotion_state="insufficient_data",
        report_json=payload,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        existing = await session.scalar(
            select(EtfFactorExperimentEvidence).where(
                EtfFactorExperimentEvidence.manifest_hash
                == manifest.manifest_hash
            )
        )
        if (
            existing is None
            or existing.experiment_family
            != LEADER_OBSERVATION_EXPERIMENT_FAMILY
            or existing.evidence_hash != evidence_hash
        ):
            raise LeaderObservationContractError(
                "leader observation evidence conflicts on resume"
            ) from exc
        return existing
    return row


async def persist_leader_maturity_evidence(
    session: AsyncSession,
    *,
    manifest: LeaderOutcomeMaturityManifest,
    outcomes: tuple[LeaderMaturedOutcome, ...],
    ma5_policy_shadow: tuple[dict[str, Any], ...] = (),
) -> EtfFactorExperimentEvidence:
    """Append one compatible maturity state without rewriting its observation."""

    manifest.validate()
    for outcome in outcomes:
        outcome.validate()
        if outcome.signal_date != manifest.signal_date:
            raise LeaderObservationContractError(
                "leader maturity outcome date differs from its observation"
            )
    status_counts = Counter(item.status for item in outcomes)
    report = {
        "schema_version": LEADER_MATURITY_SCHEMA_VERSION,
        "report_kind": LEADER_MATURITY_REPORT_KIND,
        "experiment_family": LEADER_MATURITY_EXPERIMENT_FAMILY,
        "manifest_hash": manifest.manifest_hash,
        "observation_manifest_hash": manifest.observation_manifest_hash,
        "observation_hash": manifest.observation_hash,
        "signal_date": manifest.signal_date.isoformat(),
        "outcome_cutoff": manifest.outcome_cutoff.isoformat(),
        "outcomes": [item.to_dict() for item in outcomes],
        "ma5_policy_shadow": list(ma5_policy_shadow),
        "matured_outcome_count": status_counts.get("matured", 0),
        "pending_outcome_count": status_counts.get("pending", 0),
        "unavailable_outcome_count": status_counts.get("unavailable", 0),
        "ranking_source_kind": "research_replay",
        "policy_mode": "policy_shadow",
        "notification_provenance": "none",
        "execution_provenance": "simulated_execution",
        "holdout_consumed": False,
        "research_only": True,
        "production_mutation_allowed": False,
    }
    evidence_hash = stable_contract_hash(report)
    existing = await session.scalar(
        select(EtfFactorExperimentEvidence).where(
            EtfFactorExperimentEvidence.manifest_hash == manifest.manifest_hash
        )
    )
    if existing is not None:
        if (
            existing.experiment_family != LEADER_MATURITY_EXPERIMENT_FAMILY
            or existing.evidence_hash != evidence_hash
        ):
            raise LeaderObservationContractError(
                "leader maturity manifest already has incompatible evidence"
            )
        return existing
    row = EtfFactorExperimentEvidence(
        manifest_hash=manifest.manifest_hash,
        ranking_contract_hash=manifest.adjusted_price_contract_hash,
        code_version=manifest.code_version,
        experiment_family=LEADER_MATURITY_EXPERIMENT_FAMILY,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        evidence_hash=evidence_hash,
        samples_json=[item.to_dict() for item in outcomes],
        aggregates_json=dict(status_counts),
        exclusions_json=[
            {
                "asset_code": item.asset_code,
                "horizon_sessions": item.horizon_sessions,
                "reason": item.unavailable_reason,
            }
            for item in outcomes
            if item.status != "matured"
        ],
        intervals_json={},
        split_reports_json={},
        costs_json={},
        limitations_json=[
            "research-only adjusted-close outcome; no intraday fill is inferred",
        ],
        promotion_state="insufficient_data",
        report_json=report,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        existing = await session.scalar(
            select(EtfFactorExperimentEvidence).where(
                EtfFactorExperimentEvidence.manifest_hash
                == manifest.manifest_hash
            )
        )
        if existing is None or existing.evidence_hash != evidence_hash:
            raise LeaderObservationContractError(
                "leader maturity evidence conflicts on resume"
            ) from exc
        return existing
    return row
