from __future__ import annotations

from typing import Literal

ETF_DAILY_DECISION_MIN_COVERAGE = 0.95
ETF_SCORE_PUBLICATION_MIN_COVERAGE = 0.90
ETF_COMPLETE_SCORE_COVERAGE = 0.95
ETF_RESEARCH_DEPTH_MIN_COVERAGE = 0.95

EtfCoveragePolicyMode = Literal["blocked", "degraded", "complete"]


def etf_score_coverage_policy_mode(
    coverage_ratio: float | None,
) -> EtfCoveragePolicyMode:
    ratio = float(coverage_ratio or 0.0)
    if ratio < ETF_SCORE_PUBLICATION_MIN_COVERAGE:
        return "blocked"
    if ratio < ETF_COMPLETE_SCORE_COVERAGE:
        return "degraded"
    return "complete"


__all__ = [
    "ETF_COMPLETE_SCORE_COVERAGE",
    "ETF_DAILY_DECISION_MIN_COVERAGE",
    "ETF_RESEARCH_DEPTH_MIN_COVERAGE",
    "ETF_SCORE_PUBLICATION_MIN_COVERAGE",
    "EtfCoveragePolicyMode",
    "etf_score_coverage_policy_mode",
]
