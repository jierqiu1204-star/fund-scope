from __future__ import annotations

RELIABILITY_FRESH_CONSENSUS = "fresh_consensus"
RELIABILITY_SINGLE_FRESH = "single_fresh"
RELIABILITY_DIVERGED = "diverged"
RELIABILITY_STALE = "stale"
RELIABILITY_ESTIMATED = "estimated"
RELIABILITY_UNAVAILABLE = "unavailable"

DECISION_ELIGIBLE_RELIABILITIES = {RELIABILITY_FRESH_CONSENSUS, RELIABILITY_SINGLE_FRESH}
DISPLAY_ONLY_RELIABILITIES = {
    RELIABILITY_DIVERGED,
    RELIABILITY_STALE,
    RELIABILITY_ESTIMATED,
    RELIABILITY_UNAVAILABLE,
}


def quote_reliability_from_consensus(consensus_status: str | None) -> str:
    if consensus_status == "consistent":
        return RELIABILITY_FRESH_CONSENSUS
    if consensus_status == "single_provider":
        return RELIABILITY_SINGLE_FRESH
    if consensus_status == "diverged":
        return RELIABILITY_DIVERGED
    if consensus_status == "stale":
        return RELIABILITY_STALE
    return RELIABILITY_UNAVAILABLE
