"""Frozen, research-only A-share sentiment ladder risk overlay.

The overlay is deliberately separate from the V2 alpha screen.  It consumes
only compact, point-in-time scalar features that the screen has already
validated.  It never infers exchange limit facts from adjusted OHLC bars and
never performs provider or database work.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

from app.services.etf_research_evidence import stable_contract_hash

SENTIMENT_RISK_SCHEMA_VERSION = "ashare_sentiment_risk_proxy_v1"
SENTIMENT_RISK_SOURCE_KIND = "adjusted_bar_middle_echelon_proxy_v1"
SENTIMENT_RISK_PRICE_BASIS = "total_return_adjusted"

RISK_HEALTHY = "healthy"
RISK_WARNING = "warning"
RISK_OFF = "risk_off"
RISK_UNAVAILABLE = "unavailable"
RISK_NOT_APPLICABLE = "not_applicable"
SENTIMENT_RISK_STATES = (
    RISK_HEALTHY,
    RISK_WARNING,
    RISK_OFF,
    RISK_UNAVAILABLE,
    RISK_NOT_APPLICABLE,
)

ACTION_SHADOW_ENTRY_ALLOWED = "shadow_entry_allowed"
ACTION_OBSERVE_ONLY = "observe_only"
ACTION_NOT_APPLICABLE = "not_applicable"
SENTIMENT_ACTION_MODES = (
    ACTION_SHADOW_ENTRY_ALLOWED,
    ACTION_OBSERVE_ONLY,
    ACTION_NOT_APPLICABLE,
)

MIN_HOT_THEMES = 2
MIN_LEADERS = 3
MIN_MIDDLE_TIER = 12
HOT_SCORE_MIN = 2 / 3
LEADER_CORE_MIN = 0.80
MIDDLE_CORE_MIN = 0.50
MIDDLE_CORE_MAX = 0.80
MIDDLE_MEDIAN_RETURN_MAX = 0.0
MIDDLE_POSITIVE_BREADTH_MAX = 0.40
MIDDLE_BELOW_MA5_RATIO_MIN = 0.60
LEADER_MIDDLE_SPREAD_MIN = 0.02

UNSUPPORTED_LIMIT_BOARD_FACTS = (
    "consecutive_limit_up_height",
    "promotion_rate",
    "sealed_board_status",
    "failed_board_rate",
    "exchange_limit_state",
)

SENTIMENT_RISK_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("minimum_hot_theme_count", float(MIN_HOT_THEMES)),
    ("minimum_leader_count", float(MIN_LEADERS)),
    ("minimum_middle_tier_count", float(MIN_MIDDLE_TIER)),
    ("hot_score_min", HOT_SCORE_MIN),
    ("leader_core_min", LEADER_CORE_MIN),
    ("middle_core_min", MIDDLE_CORE_MIN),
    ("middle_core_max_exclusive", MIDDLE_CORE_MAX),
    ("middle_median_return_lte", MIDDLE_MEDIAN_RETURN_MAX),
    ("middle_positive_breadth_lt", MIDDLE_POSITIVE_BREADTH_MAX),
    ("middle_below_ma5_ratio_gt", MIDDLE_BELOW_MA5_RATIO_MIN),
    ("leader_middle_return_spread_gte", LEADER_MIDDLE_SPREAD_MIN),
)


class SentimentRiskContractError(ValueError):
    """Raised when frozen sentiment-risk evidence is malformed."""


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _select_kth(values: Sequence[float], index: int) -> float:
    """Select one value in deterministic linear time using median-of-medians."""

    if not 0 <= index < len(values):
        raise SentimentRiskContractError("median index is out of range")
    if len(values) <= 5:
        return sorted(values)[index]
    medians = [
        sorted(values[offset : offset + 5])[len(values[offset : offset + 5]) // 2]
        for offset in range(0, len(values), 5)
    ]
    pivot = _select_kth(medians, len(medians) // 2)
    lower = [value for value in values if value < pivot]
    equal = [value for value in values if value == pivot]
    if index < len(lower):
        return _select_kth(lower, index)
    if index < len(lower) + len(equal):
        return pivot
    greater = [value for value in values if value > pivot]
    return _select_kth(greater, index - len(lower) - len(equal))


def _median(values: Sequence[float]) -> float:
    if not values:
        raise SentimentRiskContractError("median requires at least one finite value")
    middle = len(values) // 2
    if len(values) % 2:
        return _select_kth(values, middle)
    return (_select_kth(values, middle - 1) + _select_kth(values, middle)) / 2.0


def _contract_payload() -> dict[str, Any]:
    return {
        "schema_version": SENTIMENT_RISK_SCHEMA_VERSION,
        "source_kind": SENTIMENT_RISK_SOURCE_KIND,
        "price_basis": SENTIMENT_RISK_PRICE_BASIS,
        "thresholds": SENTIMENT_RISK_THRESHOLDS,
        "unsupported_limit_board_facts": UNSUPPORTED_LIMIT_BOARD_FACTS,
        "component_order": (
            "middle_median_return_non_positive",
            "middle_positive_breadth_low",
            "middle_below_ma5_ratio_high",
            "leader_middle_return_spread_high",
        ),
        "action_policy": {
            "healthy": ACTION_SHADOW_ENTRY_ALLOWED,
            "warning": ACTION_OBSERVE_ONLY,
            "risk_off": ACTION_OBSERVE_ONLY,
            "unavailable": ACTION_OBSERVE_ONLY,
        },
    }


ASHARE_SENTIMENT_RISK_CONTRACT_HASH = stable_contract_hash(_contract_payload())


@dataclass(frozen=True, slots=True)
class SentimentRiskPoint:
    """Scalar PIT input; no bar history is retained or copied."""

    asset_code: str
    theme_key: str
    hot_score: float
    core_score: float
    return_1: float
    below_adjusted_ma5: bool
    signal_date: date
    source_cutoff: datetime
    pit_visible: bool = True


@dataclass(frozen=True, slots=True)
class SentimentRiskSnapshot:
    schema_version: str
    contract_hash: str
    snapshot_hash: str
    source_kind: str
    price_basis: str
    signal_date: date
    source_cutoff: datetime
    state: Literal["healthy", "warning", "risk_off", "unavailable"]
    action_mode: Literal["shadow_entry_allowed", "observe_only"]
    new_entry_allowed: bool
    unavailable_reason: str | None
    triggered_components: tuple[str, ...]
    metrics: tuple[tuple[str, float | None], ...]
    cohort_counts: tuple[tuple[str, int], ...]
    thresholds: tuple[tuple[str, float], ...]
    unsupported_limit_board_facts: tuple[str, ...]

    def reference_dict(self) -> dict[str, Any]:
        """Small per-observation reference to one manifest-level snapshot."""

        return {
            "contract_hash": self.contract_hash,
            "snapshot_hash": self.snapshot_hash,
            "state": self.state,
            "unavailable_reason": self.unavailable_reason,
        }

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_hash": self.contract_hash,
            "source_kind": self.source_kind,
            "price_basis": self.price_basis,
            "signal_date": self.signal_date.isoformat(),
            "source_cutoff": self.source_cutoff.isoformat(),
            "state": self.state,
            "action_mode": self.action_mode,
            "new_entry_allowed": self.new_entry_allowed,
            "unavailable_reason": self.unavailable_reason,
            "triggered_components": list(self.triggered_components),
            "metrics": dict(self.metrics),
            "cohort_counts": dict(self.cohort_counts),
            "thresholds": dict(self.thresholds),
            "unsupported_limit_board_facts": list(self.unsupported_limit_board_facts),
            "factual_limit_board_data": {
                "available": False,
                "fields": {field: None for field in self.unsupported_limit_board_facts},
                "reason": "independently_timestamped_limit_board_facts_not_integrated",
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "snapshot_hash": self.snapshot_hash}


def _seal_snapshot(snapshot: SentimentRiskSnapshot) -> SentimentRiskSnapshot:
    return replace(
        snapshot,
        snapshot_hash=stable_contract_hash(snapshot.canonical_payload()),
    )


def _unavailable(
    *,
    signal_date: date,
    source_cutoff: datetime,
    reason: str,
    cohort_counts: Mapping[str, int] | None = None,
    metrics: Mapping[str, float | None] | None = None,
) -> SentimentRiskSnapshot:
    return _seal_snapshot(
        SentimentRiskSnapshot(
            schema_version=SENTIMENT_RISK_SCHEMA_VERSION,
            contract_hash=ASHARE_SENTIMENT_RISK_CONTRACT_HASH,
            snapshot_hash="pending",
            source_kind=SENTIMENT_RISK_SOURCE_KIND,
            price_basis=SENTIMENT_RISK_PRICE_BASIS,
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            state=RISK_UNAVAILABLE,
            action_mode=ACTION_OBSERVE_ONLY,
            new_entry_allowed=False,
            unavailable_reason=reason,
            triggered_components=(),
            metrics=tuple(sorted((metrics or {}).items())),
            cohort_counts=tuple(sorted((cohort_counts or {}).items())),
            thresholds=SENTIMENT_RISK_THRESHOLDS,
            unsupported_limit_board_facts=UNSUPPORTED_LIMIT_BOARD_FACTS,
        )
    )


def calculate_sentiment_risk(
    points: Sequence[SentimentRiskPoint],
    *,
    signal_date: date,
    source_cutoff: datetime,
) -> SentimentRiskSnapshot:
    """Evaluate the frozen proxy once for one A-share screen snapshot."""

    if any(
        point.signal_date != signal_date
        or point.source_cutoff != source_cutoff
        or not point.pit_visible
        for point in points
    ):
        return _unavailable(
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            reason="sentiment_risk_pit_input_invalid",
        )
    valid_points: list[SentimentRiskPoint] = []
    for point in points:
        values = (point.hot_score, point.core_score, point.return_1)
        if not point.asset_code.strip() or not point.theme_key.strip() or any(
            _finite(value) is None for value in values
        ):
            return _unavailable(
                signal_date=signal_date,
                source_cutoff=source_cutoff,
                reason="sentiment_risk_non_finite_input",
            )
        valid_points.append(point)

    hot_themes = {
        point.theme_key
        for point in valid_points
        if point.hot_score >= HOT_SCORE_MIN
    }
    hot_points = [point for point in valid_points if point.theme_key in hot_themes]
    leaders = [point for point in hot_points if point.core_score >= LEADER_CORE_MIN]
    middle = [
        point
        for point in hot_points
        if MIDDLE_CORE_MIN <= point.core_score < MIDDLE_CORE_MAX
    ]
    counts = {
        "hot_theme_count": len(hot_themes),
        "leader_count": len(leaders),
        "middle_tier_count": len(middle),
        "finite_point_count": len(valid_points),
    }
    if len(hot_themes) < MIN_HOT_THEMES:
        return _unavailable(
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            reason="sentiment_risk_insufficient_hot_themes",
            cohort_counts=counts,
        )
    if len(leaders) < MIN_LEADERS:
        return _unavailable(
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            reason="sentiment_risk_insufficient_leaders",
            cohort_counts=counts,
        )
    if len(middle) < MIN_MIDDLE_TIER:
        return _unavailable(
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            reason="sentiment_risk_insufficient_middle_tier",
            cohort_counts=counts,
        )

    leader_returns = [point.return_1 for point in leaders]
    middle_returns = [point.return_1 for point in middle]
    middle_median = _median(middle_returns)
    leader_median = _median(leader_returns)
    middle_positive_breadth = sum(value > 0 for value in middle_returns) / len(middle_returns)
    middle_below_ma5_ratio = sum(point.below_adjusted_ma5 for point in middle) / len(middle)
    spread = leader_median - middle_median
    metrics = {
        "middle_median_return": middle_median,
        "middle_positive_breadth": middle_positive_breadth,
        "middle_below_ma5_ratio": middle_below_ma5_ratio,
        "leader_median_return": leader_median,
        "leader_middle_median_return_spread": spread,
    }
    component_triggers = (
        ("middle_median_return_non_positive", middle_median <= MIDDLE_MEDIAN_RETURN_MAX),
        ("middle_positive_breadth_low", middle_positive_breadth < MIDDLE_POSITIVE_BREADTH_MAX),
        ("middle_below_ma5_ratio_high", middle_below_ma5_ratio > MIDDLE_BELOW_MA5_RATIO_MIN),
        (
            "leader_middle_return_spread_high",
            leader_median > 0 and spread >= LEADER_MIDDLE_SPREAD_MIN,
        ),
    )
    triggered = tuple(name for name, active in component_triggers if active)
    state = RISK_HEALTHY if len(triggered) < 2 else RISK_WARNING if len(triggered) == 2 else RISK_OFF
    action_mode = (
        ACTION_SHADOW_ENTRY_ALLOWED if state == RISK_HEALTHY else ACTION_OBSERVE_ONLY
    )
    return _seal_snapshot(
        SentimentRiskSnapshot(
            schema_version=SENTIMENT_RISK_SCHEMA_VERSION,
            contract_hash=ASHARE_SENTIMENT_RISK_CONTRACT_HASH,
            snapshot_hash="pending",
            source_kind=SENTIMENT_RISK_SOURCE_KIND,
            price_basis=SENTIMENT_RISK_PRICE_BASIS,
            signal_date=signal_date,
            source_cutoff=source_cutoff,
            state=state,
            action_mode=action_mode,
            new_entry_allowed=state == RISK_HEALTHY,
            unavailable_reason=None,
            triggered_components=triggered,
            metrics=tuple(sorted(metrics.items())),
            cohort_counts=tuple(sorted(counts.items())),
            thresholds=SENTIMENT_RISK_THRESHOLDS,
            unsupported_limit_board_facts=UNSUPPORTED_LIMIT_BOARD_FACTS,
        )
    )


def _legacy_projection(*, universe: str, formula_id: str, qualifies: bool) -> dict[str, Any]:
    applicable = universe == "ashare"
    breakout = formula_id == "leader_breakout_proxy_v2"
    return {
        "sentiment_risk_state": RISK_UNAVAILABLE if applicable else RISK_NOT_APPLICABLE,
        "sentiment_risk_action_mode": ACTION_OBSERVE_ONLY if breakout and applicable else ACTION_NOT_APPLICABLE,
        "sentiment_risk_new_entry_allowed": False if breakout and applicable else None,
        "sentiment_risk_provenance": {
            "schema_version": SENTIMENT_RISK_SCHEMA_VERSION,
            "contract_hash": None,
            "snapshot_hash": None,
            "source_kind": SENTIMENT_RISK_SOURCE_KIND if applicable else None,
            "price_basis": SENTIMENT_RISK_PRICE_BASIS if applicable else None,
            "unavailable_reason": "sentiment_risk_contract_missing" if applicable else None,
            "research_only": True,
            "notification_provenance": "none",
            "execution_provenance": "none",
        },
    }


def resolve_sentiment_risk_snapshot(
    *,
    gate_facts: Mapping[str, Any],
    shared_snapshot: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    """Resolve a legacy inline snapshot or a compact manifest reference."""

    inline = gate_facts.get("sentiment_risk")
    if isinstance(inline, Mapping):
        return inline if _snapshot_hash_is_valid(inline) else None
    reference = gate_facts.get("sentiment_risk_ref")
    if not isinstance(reference, Mapping) or not isinstance(shared_snapshot, Mapping):
        return None
    if (
        reference.get("contract_hash") != ASHARE_SENTIMENT_RISK_CONTRACT_HASH
        or shared_snapshot.get("contract_hash") != ASHARE_SENTIMENT_RISK_CONTRACT_HASH
        or reference.get("snapshot_hash") != shared_snapshot.get("snapshot_hash")
        or reference.get("state") != shared_snapshot.get("state")
        or reference.get("unavailable_reason") != shared_snapshot.get("unavailable_reason")
        or not _snapshot_hash_is_valid(shared_snapshot)
    ):
        return None
    return shared_snapshot


def _snapshot_hash_is_valid(snapshot: Mapping[str, Any]) -> bool:
    snapshot_hash = snapshot.get("snapshot_hash")
    if not isinstance(snapshot_hash, str) or len(snapshot_hash) != 64:
        return False
    canonical = dict(snapshot)
    canonical.pop("snapshot_hash", None)
    return stable_contract_hash(canonical) == snapshot_hash


def project_sentiment_risk(
    *,
    universe: str,
    formula_id: str,
    qualifies: bool,
    gate_facts: Mapping[str, Any],
    shared_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project persisted risk facts without recomputing current market data."""

    if universe != "ashare":
        return _legacy_projection(universe=universe, formula_id=formula_id, qualifies=qualifies)
    raw = resolve_sentiment_risk_snapshot(
        gate_facts=gate_facts,
        shared_snapshot=shared_snapshot,
    )
    if raw is None:
        return _legacy_projection(universe=universe, formula_id=formula_id, qualifies=qualifies)
    if raw.get("contract_hash") != ASHARE_SENTIMENT_RISK_CONTRACT_HASH:
        return _legacy_projection(universe=universe, formula_id=formula_id, qualifies=qualifies)
    state = raw.get("state")
    if state not in {RISK_HEALTHY, RISK_WARNING, RISK_OFF, RISK_UNAVAILABLE}:
        return _legacy_projection(universe=universe, formula_id=formula_id, qualifies=qualifies)
    breakout = formula_id == "leader_breakout_proxy_v2"
    risk_action = raw.get("action_mode")
    if risk_action not in {ACTION_SHADOW_ENTRY_ALLOWED, ACTION_OBSERVE_ONLY}:
        return _legacy_projection(universe=universe, formula_id=formula_id, qualifies=qualifies)
    action_mode = (
        risk_action
        if breakout and qualifies
        else ACTION_OBSERVE_ONLY
        if breakout
        else ACTION_NOT_APPLICABLE
    )
    new_entry_allowed = (
        bool(qualifies and raw.get("new_entry_allowed") is True) if breakout else None
    )
    return {
        "sentiment_risk_state": state,
        "sentiment_risk_action_mode": action_mode,
        "sentiment_risk_new_entry_allowed": new_entry_allowed,
        "sentiment_risk_provenance": {
            "schema_version": raw.get("schema_version"),
            "contract_hash": raw.get("contract_hash"),
            "snapshot_hash": raw.get("snapshot_hash"),
            "source_kind": raw.get("source_kind"),
            "price_basis": raw.get("price_basis"),
            "signal_date": raw.get("signal_date"),
            "source_cutoff": raw.get("source_cutoff"),
            "unavailable_reason": raw.get("unavailable_reason"),
            "research_only": True,
            "notification_provenance": "none",
            "execution_provenance": "none",
        },
    }


def summarize_sentiment_risk(
    rows: Iterable[Mapping[str, Any]],
    *,
    shared_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    unavailable_reasons: dict[str, int] = {}
    new_entry_allowed_count = 0
    total = 0
    for row in rows:
        projection = project_sentiment_risk(
            universe=str(row.get("universe") or ""),
            formula_id=str(row.get("formula_id") or ""),
            qualifies=bool(row.get("qualifies")),
            gate_facts=(
                row.get("gate_facts")
                if isinstance(row.get("gate_facts"), Mapping)
                else {}
            ),
            shared_snapshot=shared_snapshot,
        )
        total += 1
        state = str(projection["sentiment_risk_state"])
        action = str(projection["sentiment_risk_action_mode"])
        state_counts[state] = state_counts.get(state, 0) + 1
        action_counts[action] = action_counts.get(action, 0) + 1
        if projection["sentiment_risk_new_entry_allowed"] is True:
            new_entry_allowed_count += 1
        reason = projection["sentiment_risk_provenance"].get("unavailable_reason")
        if reason:
            unavailable_reasons[str(reason)] = unavailable_reasons.get(str(reason), 0) + 1
    return {
        "observation_count": total,
        "state_counts": dict(sorted(state_counts.items())),
        "action_mode_counts": dict(sorted(action_counts.items())),
        "new_entry_allowed_count": new_entry_allowed_count,
        "unavailable_reasons": dict(sorted(unavailable_reasons.items())),
    }


__all__ = [
    "ACTION_NOT_APPLICABLE",
    "ACTION_OBSERVE_ONLY",
    "ACTION_SHADOW_ENTRY_ALLOWED",
    "ASHARE_SENTIMENT_RISK_CONTRACT_HASH",
    "HOT_SCORE_MIN",
    "LEADER_CORE_MIN",
    "MIDDLE_CORE_MAX",
    "MIDDLE_CORE_MIN",
    "RISK_HEALTHY",
    "RISK_NOT_APPLICABLE",
    "RISK_OFF",
    "RISK_UNAVAILABLE",
    "RISK_WARNING",
    "SENTIMENT_ACTION_MODES",
    "SENTIMENT_RISK_SCHEMA_VERSION",
    "SENTIMENT_RISK_SOURCE_KIND",
    "SENTIMENT_RISK_THRESHOLDS",
    "SENTIMENT_RISK_STATES",
    "SentimentRiskContractError",
    "SentimentRiskPoint",
    "SentimentRiskSnapshot",
    "UNSUPPORTED_LIMIT_BOARD_FACTS",
    "calculate_sentiment_risk",
    "project_sentiment_risk",
    "resolve_sentiment_risk_snapshot",
    "summarize_sentiment_risk",
]
