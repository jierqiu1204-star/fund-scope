"""Adapter from the existing ETF PIT replay series into V2 inputs."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    PRICE_BASIS,
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
)


def etf_membership_to_v2(
    *,
    group_id: str,
    effective_from: date,
    effective_to: date | None,
    observed_at: datetime,
    taxonomy_version: str,
    fact_hash: str,
    theme: str | None = None,
    sector: str | None = None,
    tracked_index: str | None = None,
    clone_group: str | None = None,
    issuer: str | None = None,
) -> V2PITMembership:
    return V2PITMembership(
        group_id=group_id,
        effective_from=effective_from,
        effective_to=effective_to,
        observed_at=observed_at,
        mapping_kind="historical_pit",
        taxonomy_version=taxonomy_version,
        theme=theme,
        sector=sector,
        tracked_index=tracked_index,
        clone_group=clone_group,
        issuer=issuer,
        fact_hash=fact_hash,
    )


def etf_series_to_v2_asset(
    series: Any,
    *,
    asset_name: str,
    source_cutoff: datetime,
    membership: V2PITMembership | None,
) -> V2AssetInput:
    """Map a proven ETF replay series without invoking a provider.

    The existing replay series exposes adjusted OHLCV and `turnover`; V2 uses
    that factual turnover amount for its amount rank.  A missing amount is
    recorded as an input exclusion rather than replaced by another source.
    """

    bars: list[V2AdjustedBar] = []
    reasons: list[str] = []
    provenance = series.provenance
    observed_at = series.latest_source_timestamp
    if source_cutoff.tzinfo is None and observed_at.tzinfo is not None:
        observed_at = observed_at.astimezone(UTC).replace(tzinfo=None)
    elif source_cutoff.tzinfo is not None and observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    if provenance.price_basis != PRICE_BASIS:
        reasons.append("wrong_price_basis")
    if series.synchronized_after_cutoff:
        reasons.append("adjusted_history_received_after_cutoff")
    for item in series.bars:
        amount = item.turnover
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int | float)
            or not math.isfinite(float(amount))
            or float(amount) < 0
        ):
            reasons.append("missing_amount_for_core_rank")
            amount = 0.0
        bars.append(
            V2AdjustedBar(
                trade_date=item.session_date,
                adjusted_open=float(item.adjusted_open),
                adjusted_high=float(item.adjusted_high),
                adjusted_low=float(item.adjusted_low),
                adjusted_close=float(item.adjusted_close),
                volume=float(item.volume),
                amount=float(amount),
                turnover=float(amount),
                observed_at=observed_at,
                provider=str(provenance.provider),
                adjustment_version=str(provenance.adjustment_version),
                decision_eligible=not reasons,
                price_basis=PRICE_BASIS,
                revision_id=f"{series.series_hash}:{item.session_date.isoformat()}",
            )
        )
    return V2AssetInput(
        universe="etf",
        asset_code=str(series.asset_code),
        asset_name=asset_name,
        signal_date=bars[-1].trade_date if bars else date.min,
        source_cutoff=source_cutoff,
        bars=tuple(bars),
        membership=membership,
        input_unavailable_reasons=tuple(sorted(set(reasons))),
    )


__all__ = ["etf_membership_to_v2", "etf_series_to_v2_asset"]
