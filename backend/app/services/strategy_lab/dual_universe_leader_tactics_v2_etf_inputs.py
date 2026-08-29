"""Bounded persisted ETF inputs for leader-tactics V2 materialization."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfTaxonomyFact
from app.services import market_data
from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.etf_identity_facts import (
    select_taxonomy_facts_at_cutoff,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2AssetInput,
    V2PITMembership,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_etf_adapter import (
    etf_membership_to_v2,
    etf_series_to_v2_asset,
)
from app.services.strategy_lab.etf_point_in_time_decision_data import (
    MAX_CODES_PER_REPLAY_INPUT_PAGE,
    PointInTimeEtfMetadata,
    ReplayInputExclusion,
    build_point_in_time_adjusted_series,
    load_point_in_time_etf_decision_inputs,
)

ETF_V2_MINIMUM_HISTORY_SESSIONS = 120
ETF_V2_MAXIMUM_HISTORY_SESSIONS = 180
ETF_V2_INPUT_PAGE_SIZE = 64
ETF_V2_IDENTITY_PAGE_SIZE = 256
ETF_V2_MAX_UNIVERSE_SIZE = 2_000
_T = TypeVar("_T")


@dataclass(frozen=True)
class V2EtfInputBundle:
    signal_date: date
    source_cutoff: datetime
    identity_cutoff: datetime
    inputs: tuple[V2AssetInput, ...]
    universe_count: int
    adjusted_120_count: int
    adjusted_180_count: int
    pit_group_count: int
    provider_health: tuple[tuple[str, str], ...]
    exclusions: tuple[tuple[str, int], ...]
    raw_decision_violations: int = 0
    non_finite_violations: int = 0

    def readiness_dict(self, *, threshold: float) -> dict[str, Any]:
        denominator = max(1, self.universe_count)

        def layer(count: int) -> dict[str, Any]:
            coverage = count / denominator if self.universe_count else 0.0
            return {
                "eligible_count": count,
                "denominator": self.universe_count,
                "coverage": coverage,
                "threshold": threshold,
            }

        return {
            "schema_version": "leader_tactics_v2_etf_readiness_v1",
            "signal_date": self.signal_date.isoformat(),
            "data_cutoff": self.source_cutoff.isoformat(),
            "identity_cutoff": self.identity_cutoff.isoformat(),
            "authoritative_universe": layer(self.universe_count),
            "adjusted_history_120": layer(self.adjusted_120_count),
            "adjusted_history_180": layer(self.adjusted_180_count),
            "pit_peer_group": layer(self.pit_group_count),
            "provider_health": dict(self.provider_health),
            "raw_decision_violations": self.raw_decision_violations,
            "non_finite_violations": self.non_finite_violations,
            "exclusions": dict(self.exclusions),
            "research_only": True,
        }


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _pages(values: tuple[_T, ...], size: int) -> tuple[tuple[_T, ...], ...]:
    return tuple(values[start : start + size] for start in range(0, len(values), size))


async def _taxonomy_by_code(
    session: AsyncSession,
    *,
    codes: tuple[str, ...],
    cutoff: datetime,
) -> dict[str, EtfTaxonomyFact]:
    facts: dict[str, EtfTaxonomyFact] = {}
    for page in _pages(codes, ETF_V2_IDENTITY_PAGE_SIZE):
        facts.update(
            await select_taxonomy_facts_at_cutoff(
                session,
                etf_codes=page,
                cutoff=cutoff,
            )
        )
    return facts


def _membership_for(
    metadata: PointInTimeEtfMetadata,
    *,
    taxonomy: EtfTaxonomyFact | None,
    signal_date: date,
    source_cutoff: datetime,
) -> V2PITMembership | None:
    tracked_index = metadata.tracked_underlying_id
    theme_group = str(getattr(taxonomy, "theme_group", "") or "").strip()
    primary_theme = str(getattr(taxonomy, "primary_theme", "") or "").strip()
    usable_theme_group = theme_group if theme_group.lower() not in {"", "unknown"} else ""
    usable_primary_theme = (
        primary_theme if primary_theme.lower() not in {"", "unknown", "未分类"} else ""
    )
    if tracked_index:
        group_id = f"index:{tracked_index}"
    elif usable_theme_group:
        group_id = f"theme:{usable_theme_group}"
    elif usable_primary_theme:
        group_id = f"theme:{usable_primary_theme}"
    else:
        return None

    observed_values = [metadata.membership_known_at]
    if taxonomy is not None:
        observed_values.append(taxonomy.observed_at)
    if metadata.underlying_observed_at is not None:
        observed_values.append(metadata.underlying_observed_at)
    observed_at = max(_utc_naive(value) for value in observed_values)
    if observed_at > source_cutoff:
        return None
    taxonomy_version = "etf_pit_identity:" + stable_contract_hash(
        {
            "membership_fact_hash": metadata.membership_fact_hash,
            "taxonomy_fact_hash": (taxonomy.fact_hash if taxonomy is not None else None),
            "underlying_fact_hash": metadata.underlying_fact_hash,
            "taxonomy_provider_version": (
                taxonomy.provider_version if taxonomy is not None else None
            ),
            "taxonomy_rule_version": (taxonomy.rule_version if taxonomy is not None else None),
            "underlying_provider_version": metadata.underlying_provider_version,
            "underlying_rule_version": metadata.underlying_rule_version,
        }
    )
    draft = etf_membership_to_v2(
        group_id=group_id,
        effective_from=metadata.eligible_from,
        effective_to=signal_date,
        observed_at=observed_at,
        taxonomy_version=taxonomy_version,
        fact_hash="",
        theme=usable_primary_theme or None,
        sector=usable_theme_group or None,
        tracked_index=tracked_index,
        clone_group=(f"index:{tracked_index}" if tracked_index else metadata.asset_code),
    )
    return replace(
        draft,
        fact_hash=stable_contract_hash(draft.canonical_payload()),
    )


async def read_etf_v2_asset_inputs(
    session: AsyncSession,
    *,
    replay_date: date,
    decision_cutoff: datetime,
    identity_cutoff: datetime | None = None,
    page_size: int = ETF_V2_INPUT_PAGE_SIZE,
) -> V2EtfInputBundle:
    """Load one complete ETF cross-section through bounded persisted-data pages."""

    if decision_cutoff.tzinfo is None or decision_cutoff.utcoffset() is None:
        raise ValueError("decision_cutoff must be timezone-aware")
    identity_cutoff = identity_cutoff or decision_cutoff
    if identity_cutoff.tzinfo is None or identity_cutoff.utcoffset() is None:
        raise ValueError("identity_cutoff must be timezone-aware")
    if identity_cutoff < decision_cutoff:
        raise ValueError("identity_cutoff cannot precede decision_cutoff")
    if page_size < 1 or page_size > ETF_V2_INPUT_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {ETF_V2_INPUT_PAGE_SIZE}")
    seed = await load_point_in_time_etf_decision_inputs(
        session,
        replay_date=replay_date,
        decision_cutoff=decision_cutoff,
        max_source_rows=(MAX_CODES_PER_REPLAY_INPUT_PAGE * ETF_V2_MINIMUM_HISTORY_SESSIONS),
        max_codes=MAX_CODES_PER_REPLAY_INPUT_PAGE,
        required_history_sessions=ETF_V2_MINIMUM_HISTORY_SESSIONS,
    )
    metadata = tuple(seed.authoritative_universe)
    if len(metadata) > ETF_V2_MAX_UNIVERSE_SIZE:
        raise ValueError("ETF V2 authoritative universe exceeds the bounded limit")
    codes = tuple(item.asset_code for item in metadata)
    taxonomy = await _taxonomy_by_code(
        session,
        codes=codes,
        cutoff=identity_cutoff,
    )
    v2_cutoff = _utc_naive(decision_cutoff)
    v2_identity_cutoff = _utc_naive(identity_cutoff)
    inputs: list[V2AssetInput] = []
    exclusions: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    adjusted_120_count = 0
    adjusted_180_count = 0
    pit_group_count = 0

    for metadata_page in _pages(metadata, page_size):
        page_codes = tuple(item.asset_code for item in metadata_page)
        price_facts = await market_data.etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=page_codes,
            replay_date=replay_date,
            rows_per_code=ETF_V2_MAXIMUM_HISTORY_SESSIONS,
            max_source_rows=len(page_codes) * ETF_V2_MAXIMUM_HISTORY_SESSIONS,
            decision_cutoff=decision_cutoff,
        )
        prices_by_code: dict[str, list[market_data.EtfAdjustedDailyFact]] = defaultdict(list)
        for fact in price_facts:
            prices_by_code[fact.etf_code].append(fact)
        for item in metadata_page:
            membership = _membership_for(
                item,
                taxonomy=taxonomy.get(item.asset_code),
                signal_date=replay_date,
                source_cutoff=v2_identity_cutoff,
            )
            if membership is None:
                exclusions["missing_pit_theme_membership"] += 1
            else:
                pit_group_count += 1
            series = build_point_in_time_adjusted_series(
                metadata=item,
                rows=prices_by_code[item.asset_code],
                decision_cutoff=decision_cutoff.astimezone(UTC),
                minimum_history_sessions=ETF_V2_MINIMUM_HISTORY_SESSIONS,
                maximum_history_sessions=ETF_V2_MAXIMUM_HISTORY_SESSIONS,
            )
            asset_name = item.asset_code
            if isinstance(series, ReplayInputExclusion):
                reason = series.reason.value
                exclusions[reason] += 1
                inputs.append(
                    V2AssetInput(
                        universe="etf",
                        asset_code=item.asset_code,
                        asset_name=asset_name,
                        signal_date=replay_date,
                        source_cutoff=v2_cutoff,
                        identity_cutoff=v2_identity_cutoff,
                        bars=(),
                        membership=membership,
                        input_unavailable_reasons=(reason,),
                    )
                )
                continue
            adjusted_120_count += 1
            if len(series.bars) >= ETF_V2_MAXIMUM_HISTORY_SESSIONS:
                adjusted_180_count += 1
            provider_counts[str(series.provenance.provider)] += 1
            adapted = etf_series_to_v2_asset(
                series,
                asset_name=asset_name,
                source_cutoff=v2_cutoff,
                identity_cutoff=v2_identity_cutoff,
                membership=membership,
            )
            for reason in adapted.input_unavailable_reasons:
                exclusions[reason] += 1
            inputs.append(adapted)

    provider_health = tuple(
        (provider, "healthy" if count > 0 else "unavailable")
        for provider, count in sorted(provider_counts.items())
    )
    return V2EtfInputBundle(
        signal_date=replay_date,
        source_cutoff=v2_cutoff,
        identity_cutoff=v2_identity_cutoff,
        inputs=tuple(inputs),
        universe_count=len(metadata),
        adjusted_120_count=adjusted_120_count,
        adjusted_180_count=adjusted_180_count,
        pit_group_count=pit_group_count,
        provider_health=provider_health,
        exclusions=tuple(sorted(exclusions.items())),
        raw_decision_violations=sum(
            count
            for reason, count in exclusions.items()
            if reason == "raw_or_fallback_provider_data"
        ),
        non_finite_violations=sum(
            count for reason, count in exclusions.items() if reason == "non_finite_adjusted_input"
        ),
    )


__all__ = [
    "ETF_V2_INPUT_PAGE_SIZE",
    "ETF_V2_MAXIMUM_HISTORY_SESSIONS",
    "ETF_V2_MINIMUM_HISTORY_SESSIONS",
    "V2EtfInputBundle",
    "read_etf_v2_asset_inputs",
]
