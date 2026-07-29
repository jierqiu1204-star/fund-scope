from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ETF_DAILY_DECISION_MIN_COVERAGE = 0.95
ETF_SCORE_PUBLICATION_MIN_COVERAGE = 0.90
ETF_COMPLETE_SCORE_COVERAGE = 0.95
ETF_RESEARCH_DEPTH_MIN_COVERAGE = 0.95
ETF_READINESS_POLICY_VERSION = "etf_readiness_policy_v1"

EtfCoveragePolicyMode = Literal["blocked", "degraded", "complete"]


@dataclass(frozen=True)
class EtfReadinessPolicyResult:
    policy_version: str
    state: EtfCoveragePolicyMode
    daily_coverage_ratio: float
    warmup_coverage_ratio: float
    blocker_reasons: tuple[str, ...]
    preview_allowed: bool
    complete_publication_allowed: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["blocker_reasons"] = list(self.blocker_reasons)
        return payload


def _coverage_ratio(value: float | None) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def evaluate_etf_readiness(
    *,
    daily_coverage_ratio: float | None,
    warmup_coverage_ratio: float | None,
) -> EtfReadinessPolicyResult:
    daily = _coverage_ratio(daily_coverage_ratio)
    warmup = _coverage_ratio(warmup_coverage_ratio)
    blockers: list[str] = []
    if daily < ETF_DAILY_DECISION_MIN_COVERAGE:
        blockers.append("daily_freshness_coverage_below_95pct")
    if warmup < ETF_SCORE_PUBLICATION_MIN_COVERAGE:
        blockers.append("history_depth_61_coverage_below_90pct")
    elif warmup < ETF_COMPLETE_SCORE_COVERAGE:
        blockers.append("history_depth_61_coverage_below_95pct")

    if daily < ETF_DAILY_DECISION_MIN_COVERAGE or warmup < ETF_SCORE_PUBLICATION_MIN_COVERAGE:
        state: EtfCoveragePolicyMode = "blocked"
    elif warmup < ETF_COMPLETE_SCORE_COVERAGE:
        state = "degraded"
    else:
        state = "complete"
    return EtfReadinessPolicyResult(
        policy_version=ETF_READINESS_POLICY_VERSION,
        state=state,
        daily_coverage_ratio=daily,
        warmup_coverage_ratio=warmup,
        blocker_reasons=tuple(blockers),
        preview_allowed=state != "blocked",
        complete_publication_allowed=state == "complete",
    )


def etf_score_coverage_policy_mode(
    coverage_ratio: float | None,
) -> EtfCoveragePolicyMode:
    return evaluate_etf_readiness(
        daily_coverage_ratio=1.0,
        warmup_coverage_ratio=coverage_ratio,
    ).state


__all__ = [
    "ETF_COMPLETE_SCORE_COVERAGE",
    "ETF_DAILY_DECISION_MIN_COVERAGE",
    "ETF_READINESS_POLICY_VERSION",
    "ETF_RESEARCH_DEPTH_MIN_COVERAGE",
    "ETF_SCORE_PUBLICATION_MIN_COVERAGE",
    "EtfCoveragePolicyMode",
    "EtfReadinessPolicyResult",
    "etf_score_coverage_policy_mode",
    "evaluate_etf_readiness",
]
