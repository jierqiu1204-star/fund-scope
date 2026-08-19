"""Bounded daily state materialization for persisted A-share peer contexts."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from statistics import median
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2StagedAssetFeature,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_theme_graph import (
    MAX_THEME_GRAPH_BATCH_SIZE,
    MIN_AVAILABLE_THEME_PEERS,
    THEME_REGISTRY_VERSION,
    AshareThemeStateFact,
    persist_theme_state_batch,
)


def _context_kind(value: str | None) -> str:
    if value in {
        "provider_concept",
        "industry_union_proxy",
        "industry_l3",
        "industry_l2",
        "industry_l1",
    }:
        return value
    return "broad_fallback"


def _percentiles(values: Mapping[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    denominator = max(1, len(ordered) - 1)
    return {key: index / denominator for index, (key, _value) in enumerate(ordered)}


async def materialize_theme_states_from_features(
    session: AsyncSession,
    *,
    features: Sequence[V2StagedAssetFeature],
    state_date: date,
    source_cutoff: datetime,
    received_at: datetime | None = None,
) -> tuple[AshareThemeStateFact, ...]:
    """Calculate from compact Stage-A scalars; no adjusted history is reloaded."""

    received = received_at or datetime.now(UTC).replace(tzinfo=None)
    groups: dict[str, list[V2StagedAssetFeature]] = defaultdict(list)
    eligible_market = [
        feature
        for feature in features
        if feature.standard_available and feature.return_1 is not None
    ]
    for feature in eligible_market:
        if feature.group_key:
            groups[feature.group_key].append(feature)
    market_return = median(feature.return_1 for feature in eligible_market) if eligible_market else None
    market_amount = sum(
        feature.mean_amount_20 or 0.0 for feature in eligible_market
    )
    facts: list[AshareThemeStateFact] = []
    for context_key in sorted(groups):
        rows = groups[context_key]
        returns_1 = [row.return_1 for row in rows if row.return_1 is not None]
        returns_5 = [row.return_5 for row in rows if row.return_5 is not None]
        up_count = sum(value > 0 for value in returns_1)
        breadth = up_count / len(returns_1) if returns_1 else None
        median_1 = median(returns_1) if returns_1 else None
        median_5 = median(returns_5) if returns_5 else None
        relative = (
            median_1 - market_return
            if median_1 is not None and market_return is not None
            else None
        )
        group_amount = sum(row.mean_amount_20 or 0.0 for row in rows)
        participation = group_amount / market_amount if market_amount > 0 else None
        snapshots = {
            row.selected_context_snapshot_hash
            for row in rows
            if row.selected_context_snapshot_hash
        }
        relation_kinds = {
            _context_kind(row.selected_context_relation_kind) for row in rows
        }
        reasons: list[str] = []
        if len(rows) < MIN_AVAILABLE_THEME_PEERS:
            reasons.append("theme_peer_count_insufficient")
        if len(snapshots) != 1:
            reasons.append("incompatible_theme_snapshot")
        if len(relation_kinds) != 1:
            reasons.append("incompatible_theme_taxonomy")
        if any(value is None for value in (breadth, median_1, median_5, relative)):
            reasons.append("theme_return_inputs_incomplete")
        if participation is None:
            reasons.append("theme_amount_participation_unavailable")
        available = not reasons
        snapshot_hash = (
            next(iter(snapshots))
            if len(snapshots) == 1
            else stable_contract_hash(
                {
                    "schema_version": "theme_state_missing_snapshot_v1",
                    "context_key": context_key,
                    "state_date": state_date,
                    "feature_hashes": tuple(sorted(row.input_digest for row in rows)),
                }
            )
        )
        input_hash = stable_contract_hash(
            {
                "schema_version": "ashare_theme_state_input_v1",
                "context_key": context_key,
                "state_date": state_date,
                "source_cutoff": source_cutoff,
                "snapshot_hash": snapshot_hash,
                "feature_hashes": tuple(sorted(row.input_digest for row in rows)),
            }
        )
        facts.append(
            AshareThemeStateFact(
                context_kind=(
                    next(iter(relation_kinds)) if len(relation_kinds) == 1 else "broad_fallback"
                ),
                context_key=context_key,
                state_date=state_date,
                source_cutoff=source_cutoff,
                received_at=received,
                registry_version=THEME_REGISTRY_VERSION,
                source_snapshot_hash=snapshot_hash,
                eligible_member_count=len(rows),
                up_member_count=up_count,
                up_breadth=breadth,
                median_return_1d=median_1,
                median_return_5d=median_5,
                relative_market_return_1d=relative,
                amount_participation=participation,
                leader_count=sum(value >= 0.03 for value in returns_1),
                limit_up_count=None,
                limit_down_count=None,
                available=available,
                unavailable_reasons=tuple(sorted(set(reasons))),
                input_hash=input_hash,
            )
        )
    for start in range(0, len(facts), MAX_THEME_GRAPH_BATCH_SIZE):
        await persist_theme_state_batch(
            session,
            facts[start : start + MAX_THEME_GRAPH_BATCH_SIZE],
        )
    return tuple(facts)


async def load_persisted_theme_state_map(
    session: AsyncSession,
    *,
    group_ids: tuple[str, ...],
    signal_date: date,
    source_cutoff: datetime,
) -> dict[str, dict[str, Any]]:
    if not group_ids:
        return {}
    requested = set(group_ids)
    params: dict[str, Any] = {
        "signal_date": signal_date,
        "source_cutoff": source_cutoff,
    }
    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT *
                    FROM (
                        SELECT states.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY context_key
                                   ORDER BY state_date DESC, source_cutoff DESC,
                                            received_at DESC, state_hash DESC
                               ) AS state_rank
                        FROM ashare_theme_state_facts states
                        WHERE state_date <= :signal_date
                          AND source_cutoff <= :source_cutoff
                          AND received_at <= :source_cutoff
                    ) ranked_states
                    WHERE state_rank = 1
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    available_rows = [row for row in rows if bool(row["available"])]
    one_pct = _percentiles(
        {str(row["context_key"]): float(row["median_return_1d"]) for row in available_rows}
    )
    five_pct = _percentiles(
        {str(row["context_key"]): float(row["median_return_5d"]) for row in available_rows}
    )
    breadth_pct = _percentiles(
        {str(row["context_key"]): float(row["up_breadth"]) for row in available_rows}
    )
    # Percentiles are intentionally calculated over the full compatible
    # cross-section before filtering to the requested page. Otherwise the
    # same snapshot changes score when the materialization page size changes.
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["context_key"])
        if key not in requested:
            continue
        result[key] = {
            "available": bool(row["available"]),
            "state_hash": str(row["state_hash"]),
            "eligible_member_count": int(row["eligible_member_count"] or 0),
            "unavailable_reasons": tuple(
                json.loads(str(row["unavailable_reasons_json"] or "[]"))
            ),
            "percentiles": (
                (one_pct[key], five_pct[key], breadth_pct[key])
                if key in one_pct and key in five_pct and key in breadth_pct
                else None
            ),
        }
    return result


__all__ = [
    "load_persisted_theme_state_map",
    "materialize_theme_states_from_features",
]
