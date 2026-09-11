"""Read-only loader and JSON freeze format for historical leader-exit research."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfListingDateObservation, TradableEtf
from app.services import market_data
from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.etf_identity_facts import (
    select_taxonomy_facts_at_cutoff,
    select_tracked_underlying_facts_at_cutoff,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    FORBIDDEN_DECISION_PROVIDERS,
    PRICE_BASIS,
    V2AdjustedBar,
    V2PITMembership,
)
from app.services.strategy_lab.etf_leader_exit_historical import (
    HistoricalV2Asset,
    HistoricalV2Dataset,
)

WARMUP_START = date(2026, 1, 5)
FORMAL_START = date(2026, 7, 15)
FORMAL_END = date(2026, 9, 10)
DATASET_NAMESPACE = "etf_leader_exit_historical_v2_current_vintage"
PAGE_SIZE = 16
HISTORY_SESSIONS = 180


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _utc(value: datetime | None) -> datetime | None:
    """Normalize UTC-naive database timestamps without changing the instant."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _db_utc(value: datetime) -> datetime:
    normalized = _utc(value)
    assert normalized is not None
    return normalized.replace(tzinfo=None)


def _text(value: object) -> str:
    return str(value or "").strip()


def _finite(value: object, *, minimum: float | None = None) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        return None
    return result


def dataset_to_json(dataset: HistoricalV2Dataset) -> str:
    return json.dumps(asdict(dataset), default=_json_default, ensure_ascii=False, sort_keys=True)


def dataset_from_json(payload: str | bytes) -> HistoricalV2Dataset:
    raw = json.loads(payload)
    source_hash = raw.get("source_hash")
    if not isinstance(source_hash, str) or not source_hash.strip():
        raise ValueError("frozen dataset source_hash is required")
    frozen_at = datetime.fromisoformat(raw["frozen_at"])
    assets = []
    for item in raw["assets"]:
        membership = item.get("membership")
        if membership is not None:
            membership = {
                **membership,
                "industry_path": tuple(tuple(value) for value in membership.get("industry_path", ())),
                "theme_state_percentiles": (
                    tuple(membership["theme_state_percentiles"])
                    if membership.get("theme_state_percentiles") is not None
                    else None
                ),
                "theme_state_unavailable_reasons": tuple(
                    membership.get("theme_state_unavailable_reasons", ())
                ),
            }
            membership = V2PITMembership(
                **{
                    **membership,
                    "effective_from": date.fromisoformat(membership["effective_from"]),
                    "effective_to": (
                        date.fromisoformat(membership["effective_to"])
                        if membership.get("effective_to")
                        else None
                    ),
                    "observed_at": datetime.fromisoformat(membership["observed_at"]),
                    "snapshot_date": (
                        date.fromisoformat(membership["snapshot_date"])
                        if membership.get("snapshot_date")
                        else None
                    ),
                }
            )
        assets.append(
            HistoricalV2Asset(
                asset_code=item["asset_code"],
                name=item["name"],
                listed_date=date.fromisoformat(item["listed_date"]),
                membership=membership,
                underlying=item.get("underlying", ""),
                bars=tuple(
                    V2AdjustedBar(
                        **{
                            **bar,
                            "trade_date": date.fromisoformat(bar["trade_date"]),
                            "observed_at": datetime.fromisoformat(bar["observed_at"]),
                        }
                    )
                    for bar in item["bars"]
                ),
            )
        )
    return HistoricalV2Dataset(
        frozen_at=frozen_at,
        start_date=date.fromisoformat(raw["start_date"]),
        end_date=date.fromisoformat(raw["end_date"]),
        trading_sessions=tuple(date.fromisoformat(item) for item in raw["trading_sessions"]),
        assets=tuple(assets),
        source_hash=source_hash,
        source_manifest=tuple(tuple(item) for item in raw.get("source_manifest", ())),
        exclusions=tuple(tuple(item) for item in raw.get("exclusions", ())),
        universe=raw.get("universe", "etf"),
        theme=raw.get("theme", "current_vintage"),
        underlying=raw.get("underlying", "current_vintage"),
    )


def _sessions(start_date: date, end_date: date) -> tuple[date, ...]:
    return tuple(
        day
        for ordinal in range((end_date - start_date).days + 1)
        if market_data.is_etf_exchange_trading_day(day := date.fromordinal(start_date.toordinal() + ordinal))
    )


async def load_historical_v2_dataset(
    session: AsyncSession,
    *,
    frozen_at: datetime,
    start_date: date,
    end_date: date,
    cursor: str | None = None,
    page_size: int = PAGE_SIZE,
) -> HistoricalV2Dataset:
    """Load one bounded page of current-vintage ETF facts without provider calls."""

    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise ValueError("frozen_at must be timezone-aware")
    if not 1 <= page_size <= PAGE_SIZE:
        raise ValueError("page_size must be between 1 and 16")
    if not FORMAL_START <= start_date <= end_date <= FORMAL_END:
        raise ValueError("historical window must remain within 2026-07-15..2026-09-10")
    cutoff = frozen_at.astimezone(UTC)
    all_rows = (
        await session.scalars(select(TradableEtf).order_by(TradableEtf.code.asc()))
    ).all()
    all_codes = tuple(row.code for row in all_rows)
    if cursor is not None and cursor not in all_codes:
        raise ValueError("cursor is not in the frozen tradable ETF universe")
    start_index = all_codes.index(cursor) + 1 if cursor is not None else 0
    rows = all_rows[start_index : start_index + page_size]
    codes = tuple(row.code for row in rows)
    has_more = start_index + len(rows) < len(all_codes)
    next_cursor = rows[-1].code if has_more and rows else None
    taxonomy_all = await select_taxonomy_facts_at_cutoff(
        session, etf_codes=all_codes, cutoff=cutoff
    )
    underlying_all = await select_tracked_underlying_facts_at_cutoff(
        session, etf_codes=all_codes, cutoff=cutoff
    )
    taxonomy = {code: taxonomy_all[code] for code in codes if code in taxonomy_all}
    underlyings = {code: underlying_all[code] for code in codes if code in underlying_all}
    cutoff_db = _db_utc(frozen_at)
    listing_query = (
        select(EtfListingDateObservation)
        .where(
            EtfListingDateObservation.etf_code.in_(codes),
            EtfListingDateObservation.observed_at <= cutoff_db,
        )
        .order_by(
            EtfListingDateObservation.etf_code,
            EtfListingDateObservation.observed_at.desc(),
            EtfListingDateObservation.id.desc(),
        )
    )
    listings = {}
    for row in (await session.scalars(listing_query)).all():
        listings.setdefault(row.etf_code, row)
    sessions = _sessions(WARMUP_START, end_date)
    assets = []
    exclusions: list[tuple[str, str]] = []
    candidates = []
    for row in rows:
        listing = listings.get(row.code)
        if listing is None:
            exclusions.append((row.code, "listing_fact_missing"))
            continue
        if listing.listing_date > end_date:
            exclusions.append((row.code, "listing_after_end_date"))
            continue
        fact = taxonomy.get(row.code)
        if fact is None:
            exclusions.append((row.code, "taxonomy_fact_missing"))
            continue
        underlying = underlyings.get(row.code)
        underlying_id = _text(getattr(underlying, "tracked_underlying_id", None))
        if underlying is None:
            exclusions.append((row.code, "underlying_fact_missing"))
            continue
        if _text(getattr(underlying, "identity_state", None)) != "resolved" or not underlying_id:
            exclusions.append((row.code, "underlying_unresolved"))
            continue
        theme_group = _text(getattr(fact, "theme_group", None))
        primary_theme = _text(getattr(fact, "primary_theme", None))
        if theme_group.casefold() in {"", "unknown"} and primary_theme.casefold() in {
            "",
            "unknown",
            "未分类",
        }:
            exclusions.append((row.code, "taxonomy_group_missing"))
            continue
        candidates.append((row, listing, fact, underlying))

    quote_codes = tuple(item[0].code for item in candidates)
    bars_by_code = {}
    if quote_codes:
        bars = await market_data.etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=quote_codes,
            replay_date=end_date,
            rows_per_code=HISTORY_SESSIONS,
            max_source_rows=len(quote_codes) * HISTORY_SESSIONS,
            decision_cutoff=cutoff,
            compatible_provider_versions=tuple(
                pair
                for pair in market_data.etf_decision_adjusted_provider_versions()
                if pair[0].casefold() not in FORBIDDEN_DECISION_PROVIDERS
            ),
        )
        for bar in bars:
            bars_by_code.setdefault(bar.etf_code, []).append(bar)

    for row, listing, fact, underlying in candidates:
        underlying_id = _text(getattr(underlying, "tracked_underlying_id", None))
        theme_group = _text(getattr(fact, "theme_group", None))
        primary_theme = _text(getattr(fact, "primary_theme", None))
        bars = bars_by_code.get(row.code, ())
        converted = []
        for bar in bars:
            if bar.trade_date < WARMUP_START or bar.trade_date > end_date:
                continue
            adjusted_close = _finite(bar.adjusted_close, minimum=0.0)
            raw_open = _finite(bar.raw_open, minimum=0.0)
            raw_high = _finite(bar.raw_high, minimum=0.0)
            raw_low = _finite(bar.raw_low, minimum=0.0)
            raw_close = _finite(bar.raw_close, minimum=0.0)
            volume = _finite(bar.volume, minimum=0.0)
            turnover = _finite(bar.turnover, minimum=0.0)
            observed = _utc(bar.observed_at)
            provider = _text(bar.data_provider)
            adjustment_version = _text(bar.adjustment_version)
            revision_id = _text(bar.revision_hash)
            if (
                bar.decision_eligible is not True
                or bar.research_price_basis != PRICE_BASIS
                or adjusted_close is None
                or raw_open is None
                or raw_high is None
                or raw_low is None
                or raw_close is None
                or raw_close <= 0
                or volume is None
                or turnover is None
                or observed is None
                or not provider
                or not adjustment_version
                or not revision_id
            ):
                continue
            factor = adjusted_close / raw_close
            converted.append(
                V2AdjustedBar(
                    trade_date=bar.trade_date,
                    adjusted_open=raw_open * factor,
                    adjusted_high=raw_high * factor,
                    adjusted_low=raw_low * factor,
                    adjusted_close=adjusted_close,
                    volume=volume,
                    amount=turnover,
                    turnover=turnover,
                    observed_at=observed,
                    provider=provider,
                    adjustment_version=adjustment_version,
                    decision_eligible=True,
                    price_basis=bar.research_price_basis,
                    revision_id=revision_id,
                    fact_hash=revision_id,
                )
            )
        if not converted:
            exclusions.append((row.code, "history_ineligible"))
            continue
        taxonomy_observed = _utc(fact.observed_at)
        underlying_observed = _utc(underlying.observed_at)
        if taxonomy_observed is None or underlying_observed is None:
            exclusions.append((row.code, "identity_receipt_missing"))
            continue
        observed_at = max(taxonomy_observed, underlying_observed)
        membership_effective = max(taxonomy_observed.date(), underlying_observed.date())
        group_id = theme_group if theme_group.casefold() not in {"", "unknown"} else primary_theme
        membership_source = _text(getattr(fact, "source", None)) or _text(
            getattr(underlying, "source", None)
        )
        taxonomy_version = _text(getattr(fact, "rule_version", None))
        if not group_id or not membership_source or not taxonomy_version:
            exclusions.append((row.code, "membership_fact_incomplete"))
            continue
        membership = V2PITMembership(
            group_id=group_id,
            effective_from=membership_effective,
            effective_to=None,
            observed_at=observed_at,
            mapping_kind="current_vintage_proxy",
            taxonomy_version=taxonomy_version,
            theme=primary_theme or None,
            sector=theme_group or None,
            tracked_index=underlying_id,
            clone_group=underlying_id,
            fact_hash="",
            source=_text(getattr(fact, "source", None)) or None,
            confidence=_text(getattr(fact, "confidence", None)) or None,
            snapshot_date=membership_effective,
        )
        membership = V2PITMembership(
            **{**asdict(membership), "fact_hash": stable_contract_hash(membership.canonical_payload())}
        )
        assets.append(
            HistoricalV2Asset(
                asset_code=row.code,
                name=row.name,
                listed_date=listing.listing_date,
                membership=membership,
                bars=tuple(converted),
                underlying=underlying_id,
            )
        )
    manifest: list[tuple[str, object]] = [
        (
            "__config__",
            {
                "namespace": DATASET_NAMESPACE,
                "frozen_at": frozen_at.astimezone(UTC).isoformat(),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "page_size": page_size,
            },
        ),
        (
            "__pagination__",
            {
                "cursor": cursor,
                "last_code": rows[-1].code if rows else cursor,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "load_complete": not has_more,
            },
        ),
        (
            "__universe__",
            {
                "codes": list(all_codes),
                "tradable_count": len(all_codes),
                "taxonomy_count": len(taxonomy_all),
                "underlying_resolved_count": sum(
                    _text(getattr(item, "identity_state", None)) == "resolved"
                    and bool(_text(getattr(item, "tracked_underlying_id", None)))
                    for item in underlying_all.values()
                ),
                "underlying_unresolved_count": sum(
                    _text(getattr(item, "identity_state", None)) != "resolved"
                    or not _text(getattr(item, "tracked_underlying_id", None))
                    for item in underlying_all.values()
                ),
                "underlying_fact_count": len(underlying_all),
            },
        ),
    ]
    for code in codes:
        listing = listings.get(code)
        fact = taxonomy.get(code)
        underlying = underlyings.get(code)
        if listing is not None:
            manifest.append(
                (
                    f"listing:{code}",
                    {
                        "listing_date": listing.listing_date.isoformat(),
                        "source": listing.source,
                        "provider_version": listing.provider_version,
                        "observed_at": (
                            _utc(listing.observed_at).isoformat()
                            if _utc(listing.observed_at) is not None
                            else None
                        ),
                        "universe_snapshot_hash": listing.universe_snapshot_hash,
                        "raw_payload_hash": listing.raw_payload_hash,
                        "evidence_hash": listing.evidence_hash,
                    },
                )
            )
        if fact is not None:
            manifest.append(
                (
                    f"taxonomy:{code}",
                    {
                        "fact_hash": fact.fact_hash,
                        "evidence_hash": fact.evidence_hash,
                        "raw_payload_hash": fact.raw_payload_hash,
                        "source": fact.source,
                        "provider_version": fact.provider_version,
                        "rule_version": fact.rule_version,
                        "observed_at": (
                            _utc(fact.observed_at).isoformat()
                            if _utc(fact.observed_at) is not None
                            else None
                        ),
                    },
                )
            )
        if underlying is not None:
            manifest.append(
                (
                    f"underlying:{code}",
                    {
                        "fact_hash": underlying.fact_hash,
                        "evidence_hash": underlying.evidence_hash,
                        "raw_payload_hash": underlying.raw_payload_hash,
                        "source": underlying.source,
                        "provider_version": underlying.provider_version,
                        "rule_version": underlying.rule_version,
                        "identity_state": underlying.identity_state,
                        "tracked_underlying_id": underlying.tracked_underlying_id,
                        "observed_at": (
                            _utc(underlying.observed_at).isoformat()
                            if _utc(underlying.observed_at) is not None
                            else None
                        ),
                    },
                )
            )
    return HistoricalV2Dataset(
        frozen_at=frozen_at,
        start_date=start_date,
        end_date=end_date,
        trading_sessions=sessions,
        assets=tuple(sorted(assets, key=lambda item: item.asset_code)),
        exclusions=tuple(sorted(exclusions)),
        source_manifest=tuple(sorted(manifest, key=lambda item: item[0])),
        universe="etf",
        theme="current_vintage",
        underlying="current_vintage",
    )
