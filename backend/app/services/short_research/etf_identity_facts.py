"""Bounded, append-only ETF taxonomy and formal-underlying facts.

This module intentionally has no provider or name-parsing implementation.  A formal
tracked underlying must arrive as an explicit authoritative or manual mapping; fund
names and keyword rules are not accepted as formal-underlying evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfSyncCursor,
    EtfTaxonomyFact,
    EtfThemeProfile,
    EtfTrackedUnderlyingFact,
    EtfUniverseMembership,
    TradableEtf,
    utcnow,
)

MAX_IDENTITY_FACT_RECORDS = 20
MIN_IDENTITY_FACT_PAGE_SIZE = 5
MAX_IDENTITY_FACT_PAGE_SIZE = 20
MAX_IDENTITY_FACT_SLICE_SECONDS = 55.0
IDENTITY_FACT_CURSOR_LANE = "identity_facts"
IDENTITY_FACT_CONTRACT_VERSION = "etf_identity_fact_contract_v1"
TAXONOMY_PRECEDENCE_POLICY_VERSION = "etf_taxonomy_precedence_v1"
TRACKED_UNDERLYING_EVIDENCE_POLICY_VERSION = "etf_tracked_underlying_evidence_v1"

_TAXONOMY_PRECEDENCE = {
    "authoritative": 0,
    "manual": 0,
    "manual_override": 0,
    "cross_border": 1,
    "cross_border_keyword": 1,
    "asset_class": 2,
    "domestic_keyword": 3,
    "unknown": 4,
}
_UNDERLYING_PRECEDENCE = {"manual": 0, "authoritative": 1}


@dataclass(frozen=True)
class TaxonomyFactInput:
    etf_code: str
    external_source_id: str
    source: str
    provider_version: str
    observed_at: datetime
    confidence: str
    rule_version: str
    asset_bucket: str
    theme_group: str
    primary_theme: str
    secondary_themes: Sequence[str]
    classification_source: str
    classification_reason: str
    raw_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrackedUnderlyingFactInput:
    """An explicit, non-name-derived mapping to the underlying index or asset."""

    etf_code: str
    external_source_id: str
    source: str
    provider_version: str
    observed_at: datetime
    identity_state: str
    tracked_underlying_id: str | None
    mapping_basis: str
    confidence: str
    rule_version: str
    identity_reason: str
    raw_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IdentityFactPersistResult:
    taxonomy_facts_inserted: int = 0
    taxonomy_facts_existing: int = 0
    underlying_facts_inserted: int = 0
    underlying_facts_existing: int = 0
    taxonomy_projections_inserted: int = 0
    taxonomy_projections_updated: int = 0
    membership_projections_updated: int = 0


@dataclass(frozen=True)
class IdentityFactProviderPage:
    """Explicit factual provider output for one preselected authoritative page."""

    taxonomy_records: Sequence[TaxonomyFactInput] = ()
    underlying_records: Sequence[TrackedUnderlyingFactInput] = ()


IdentityFactPageFetcher = Callable[[tuple[str, ...]], Awaitable[IdentityFactProviderPage]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class IdentityFactIngestionRequest:
    """Limits one serial identity-fact ingestion slice without choosing a provider."""

    scope: str
    target_page_size: int = 10
    slice_budget_seconds: float = MAX_IDENTITY_FACT_SLICE_SECONDS
    estimated_seconds_per_etf: float = 1.0
    commit_reserve_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("scope is required")
        if self.target_page_size <= 0:
            raise ValueError("target_page_size must be positive")
        if not 0 < self.slice_budget_seconds <= MAX_IDENTITY_FACT_SLICE_SECONDS:
            raise ValueError("slice_budget_seconds must be between 0 and 55")
        if self.estimated_seconds_per_etf <= 0:
            raise ValueError("estimated_seconds_per_etf must be positive")
        if not 0 <= self.commit_reserve_seconds < self.slice_budget_seconds:
            raise ValueError("commit_reserve_seconds must be within the slice budget")


@dataclass(frozen=True)
class IdentityFactIngestionSliceResult:
    status: str
    stop_reason: str | None
    selected_codes: tuple[str, ...]
    page_size: int
    has_more: bool
    cursor_before: str | None
    cursor_after: str | None
    persisted: IdentityFactPersistResult | None
    elapsed_seconds: float
    error_summary: str | None = None


@dataclass(frozen=True)
class IdentityFactCoverageProjection:
    """Compact, cutoff-bound identity coverage for the authoritative ETF universe."""

    cutoff: datetime
    universe_count: int
    taxonomy_fact_count: int
    known_taxonomy_count: int
    unknown_taxonomy_count: int
    taxonomy_coverage_ratio: float
    tracked_underlying_fact_count: int
    resolved_underlying_count: int
    unresolved_underlying_count: int
    tracked_underlying_coverage_ratio: float
    evidence_groups: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff,
            "universe_count": self.universe_count,
            "taxonomy_fact_count": self.taxonomy_fact_count,
            "known_taxonomy_count": self.known_taxonomy_count,
            "unknown_taxonomy_count": self.unknown_taxonomy_count,
            "taxonomy_coverage_ratio": self.taxonomy_coverage_ratio,
            "tracked_underlying_fact_count": self.tracked_underlying_fact_count,
            "resolved_underlying_count": self.resolved_underlying_count,
            "unresolved_underlying_count": self.unresolved_underlying_count,
            "tracked_underlying_coverage_ratio": self.tracked_underlying_coverage_ratio,
            "evidence_groups": [dict(group) for group in self.evidence_groups],
            "identity_fact_contract": identity_fact_contract(),
        }


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, set):
        return json.dumps(sorted(value), ensure_ascii=False)
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def _hash(value: object) -> str:
    payload = json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def identity_fact_contract() -> dict[str, Any]:
    """Return the immutable semantics used to interpret identity facts."""

    return {
        "contract_version": IDENTITY_FACT_CONTRACT_VERSION,
        "taxonomy_precedence_policy_version": TAXONOMY_PRECEDENCE_POLICY_VERSION,
        "tracked_underlying_evidence_policy_version": (
            TRACKED_UNDERLYING_EVIDENCE_POLICY_VERSION
        ),
        "taxonomy_precedence": [
            "authoritative_or_manual_override",
            "cross_border_marker",
            "asset_class",
            "domestic_keyword",
            "unknown",
        ],
        "tracked_underlying_precedence": ["manual", "authoritative"],
        "visibility_field": "observed_at",
        "historical_selection": "highest_precedence_visible_at_cutoff",
        "name_only_formal_underlying_inference": "forbidden",
        "supersession_mode": "append_only",
    }


def _required_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalized_observed_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _secondary_themes(values: Sequence[str]) -> list[str]:
    return list(
        dict.fromkeys(
            _required_text(value, field_name="secondary_theme")
            for value in values
            if value.strip()
        )
    )


def _taxonomy_order_columns() -> tuple[Any, ...]:
    return (
        case(
            _TAXONOMY_PRECEDENCE,
            value=EtfTaxonomyFact.classification_source,
            else_=4,
        ),
        EtfTaxonomyFact.observed_at.desc(),
        EtfTaxonomyFact.id.desc(),
    )


def _underlying_order_columns() -> tuple[Any, ...]:
    return (
        case(
            _UNDERLYING_PRECEDENCE,
            value=EtfTrackedUnderlyingFact.mapping_basis,
            else_=2,
        ),
        EtfTrackedUnderlyingFact.observed_at.desc(),
        EtfTrackedUnderlyingFact.id.desc(),
    )


async def _taxonomy_facts_by_code(
    session: AsyncSession,
    codes: Sequence[str],
    *,
    cutoff: datetime | None,
) -> dict[str, EtfTaxonomyFact]:
    unique_codes = sorted(set(codes))
    if not unique_codes:
        return {}
    statement = select(EtfTaxonomyFact).where(EtfTaxonomyFact.etf_code.in_(unique_codes))
    if cutoff is not None:
        statement = statement.where(
            EtfTaxonomyFact.observed_at <= _normalized_observed_at(cutoff),
        )
    rows = (
        await session.scalars(
            statement.order_by(
                EtfTaxonomyFact.etf_code.asc(),
                *_taxonomy_order_columns(),
            )
        )
    ).all()
    selected: dict[str, EtfTaxonomyFact] = {}
    for row in rows:
        selected.setdefault(row.etf_code, row)
    return selected


async def _underlying_facts_by_code(
    session: AsyncSession,
    codes: Sequence[str],
    *,
    cutoff: datetime | None,
) -> dict[str, EtfTrackedUnderlyingFact]:
    unique_codes = sorted(set(codes))
    if not unique_codes:
        return {}
    statement = select(EtfTrackedUnderlyingFact).where(
        EtfTrackedUnderlyingFact.etf_code.in_(unique_codes),
    )
    if cutoff is not None:
        statement = statement.where(
            EtfTrackedUnderlyingFact.observed_at <= _normalized_observed_at(cutoff),
        )
    rows = (
        await session.scalars(
            statement.order_by(
                EtfTrackedUnderlyingFact.etf_code.asc(),
                *_underlying_order_columns(),
            )
        )
    ).all()
    selected: dict[str, EtfTrackedUnderlyingFact] = {}
    for row in rows:
        selected.setdefault(row.etf_code, row)
    return selected


async def select_taxonomy_facts_at_cutoff(
    session: AsyncSession,
    *,
    etf_codes: Sequence[str],
    cutoff: datetime,
) -> dict[str, EtfTaxonomyFact]:
    """Return the highest-precedence fact visible at ``cutoff`` for each ETF."""

    return await _taxonomy_facts_by_code(session, etf_codes, cutoff=cutoff)


async def select_taxonomy_fact_at_cutoff(
    session: AsyncSession,
    *,
    etf_code: str,
    cutoff: datetime,
) -> EtfTaxonomyFact | None:
    return (await select_taxonomy_facts_at_cutoff(session, etf_codes=[etf_code], cutoff=cutoff)).get(etf_code)


async def select_tracked_underlying_facts_at_cutoff(
    session: AsyncSession,
    *,
    etf_codes: Sequence[str],
    cutoff: datetime,
) -> dict[str, EtfTrackedUnderlyingFact]:
    """Return explicit formal-underlying facts visible at ``cutoff`` for each ETF."""

    return await _underlying_facts_by_code(session, etf_codes, cutoff=cutoff)


async def select_tracked_underlying_fact_at_cutoff(
    session: AsyncSession,
    *,
    etf_code: str,
    cutoff: datetime,
) -> EtfTrackedUnderlyingFact | None:
    return (
        await select_tracked_underlying_facts_at_cutoff(
            session,
            etf_codes=[etf_code],
            cutoff=cutoff,
        )
    ).get(etf_code)


async def identity_fact_coverage_at_cutoff(
    session: AsyncSession,
    *,
    cutoff: datetime,
) -> IdentityFactCoverageProjection:
    """Summarize only facts and universe membership visible at ``cutoff``.

    The projection returns aggregate counts and bounded source/rule groups rather
    than fact payloads.  It deliberately derives historical membership from its
    effective interval instead of giving current-vintage rows historical credit.
    """

    normalized_cutoff = _normalized_observed_at(cutoff)
    cutoff_date = normalized_cutoff.date()
    codes = tuple(
        (
            await session.scalars(
                select(EtfUniverseMembership.etf_code)
                .where(
                    EtfUniverseMembership.effective_from <= cutoff_date,
                    (
                        EtfUniverseMembership.effective_to.is_(None)
                        | (EtfUniverseMembership.effective_to >= cutoff_date)
                    ),
                )
                .distinct()
                .order_by(EtfUniverseMembership.etf_code.asc())
            )
        ).all()
    )
    taxonomy = await _taxonomy_facts_by_code(
        session,
        codes,
        cutoff=normalized_cutoff,
    )
    underlying = await _underlying_facts_by_code(
        session,
        codes,
        cutoff=normalized_cutoff,
    )
    known_taxonomy_count = sum(
        str(fact.asset_bucket).strip().casefold() not in {"", "unknown"}
        for fact in taxonomy.values()
    )
    resolved_underlying_count = sum(
        fact.identity_state == "resolved"
        and bool(str(fact.tracked_underlying_id or "").strip())
        for fact in underlying.values()
    )
    universe_count = len(codes)
    evidence: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for kind, facts in (("taxonomy", taxonomy), ("tracked_underlying", underlying)):
        for fact in facts.values():
            key = (kind, fact.source, fact.provider_version, fact.rule_version)
            group = evidence.setdefault(
                key,
                {
                    "fact_kind": kind,
                    "source": fact.source,
                    "provider_version": fact.provider_version,
                    "rule_version": fact.rule_version,
                    "count": 0,
                    "latest_observed_at": fact.observed_at,
                },
            )
            group["count"] += 1
            group["latest_observed_at"] = max(
                group["latest_observed_at"],
                fact.observed_at,
            )
    groups = tuple(evidence[key] for key in sorted(evidence))
    return IdentityFactCoverageProjection(
        cutoff=normalized_cutoff,
        universe_count=universe_count,
        taxonomy_fact_count=len(taxonomy),
        known_taxonomy_count=known_taxonomy_count,
        unknown_taxonomy_count=universe_count - known_taxonomy_count,
        taxonomy_coverage_ratio=round(
            known_taxonomy_count / universe_count if universe_count else 0.0,
            6,
        ),
        tracked_underlying_fact_count=len(underlying),
        resolved_underlying_count=resolved_underlying_count,
        unresolved_underlying_count=universe_count - resolved_underlying_count,
        tracked_underlying_coverage_ratio=round(
            resolved_underlying_count / universe_count if universe_count else 0.0,
            6,
        ),
        evidence_groups=groups,
    )


def _taxonomy_payload(record: TaxonomyFactInput) -> dict[str, Any]:
    observed_at = _normalized_observed_at(record.observed_at)
    payload = {
        "etf_code": _required_text(record.etf_code, field_name="etf_code"),
        "external_source_id": _required_text(
            record.external_source_id,
            field_name="external_source_id",
        ),
        "source": _required_text(record.source, field_name="source"),
        "provider_version": _required_text(
            record.provider_version,
            field_name="provider_version",
        ),
        "observed_at": observed_at,
        "confidence": _required_text(record.confidence, field_name="confidence"),
        "rule_version": _required_text(record.rule_version, field_name="rule_version"),
        "asset_bucket": _required_text(record.asset_bucket, field_name="asset_bucket"),
        "theme_group": _required_text(record.theme_group, field_name="theme_group"),
        "primary_theme": _required_text(record.primary_theme, field_name="primary_theme"),
        "secondary_themes_json": _secondary_themes(record.secondary_themes),
        "classification_source": _required_text(
            record.classification_source,
            field_name="classification_source",
        ),
        "classification_reason": _required_text(
            record.classification_reason,
            field_name="classification_reason",
        ),
    }
    raw_payload_hash = _hash(dict(record.raw_payload))
    payload["raw_payload_hash"] = raw_payload_hash
    payload["evidence_hash"] = _hash({"kind": "taxonomy", **payload})
    return payload


def _underlying_payload(record: TrackedUnderlyingFactInput) -> dict[str, Any]:
    observed_at = _normalized_observed_at(record.observed_at)
    identity_state = _required_text(record.identity_state, field_name="identity_state")
    if identity_state not in {"resolved", "unresolved"}:
        raise ValueError("identity_state must be resolved or unresolved")
    mapping_basis = _required_text(record.mapping_basis, field_name="mapping_basis")
    if mapping_basis not in _UNDERLYING_PRECEDENCE:
        raise ValueError("mapping_basis must be authoritative or manual")
    source = _required_text(record.source, field_name="source")
    if source.casefold() in {"fund_name", "name_inference", "keyword_rule"}:
        raise ValueError("formal tracked underlyings cannot be inferred from fund names")
    underlying_id = (
        _required_text(record.tracked_underlying_id, field_name="tracked_underlying_id")
        if record.tracked_underlying_id is not None
        else None
    )
    if (identity_state == "resolved") != (underlying_id is not None):
        raise ValueError("resolved facts require an id and unresolved facts must not include one")
    payload = {
        "etf_code": _required_text(record.etf_code, field_name="etf_code"),
        "external_source_id": _required_text(
            record.external_source_id,
            field_name="external_source_id",
        ),
        "source": source,
        "provider_version": _required_text(
            record.provider_version,
            field_name="provider_version",
        ),
        "observed_at": observed_at,
        "identity_state": identity_state,
        "tracked_underlying_id": underlying_id,
        "mapping_basis": mapping_basis,
        "confidence": _required_text(record.confidence, field_name="confidence"),
        "rule_version": _required_text(record.rule_version, field_name="rule_version"),
        "identity_reason": _required_text(
            record.identity_reason,
            field_name="identity_reason",
        ),
    }
    raw_payload_hash = _hash(dict(record.raw_payload))
    payload["raw_payload_hash"] = raw_payload_hash
    payload["evidence_hash"] = _hash({"kind": "tracked_underlying", **payload})
    return payload


async def _persist_taxonomy_fact(
    session: AsyncSession,
    *,
    payload: Mapping[str, Any],
) -> tuple[EtfTaxonomyFact, bool]:
    existing = await session.scalar(
        select(EtfTaxonomyFact).where(
            EtfTaxonomyFact.evidence_hash == payload["evidence_hash"],
        )
    )
    if existing is not None:
        return existing, False
    previous = await session.scalar(
        select(EtfTaxonomyFact)
        .where(
            EtfTaxonomyFact.etf_code == payload["etf_code"],
            EtfTaxonomyFact.source == payload["source"],
            EtfTaxonomyFact.external_source_id == payload["external_source_id"],
            EtfTaxonomyFact.observed_at <= payload["observed_at"],
        )
        .order_by(EtfTaxonomyFact.observed_at.desc(), EtfTaxonomyFact.id.desc())
        .limit(1)
    )
    fact = EtfTaxonomyFact(
        **payload,
        fact_hash=_hash(
            {
                "evidence_hash": payload["evidence_hash"],
                "supersedes_fact_hash": previous.fact_hash if previous else None,
            }
        ),
        supersedes_fact_id=previous.id if previous else None,
        created_at=utcnow(),
    )
    session.add(fact)
    await session.flush()
    return fact, True


async def _persist_underlying_fact(
    session: AsyncSession,
    *,
    payload: Mapping[str, Any],
) -> tuple[EtfTrackedUnderlyingFact, bool]:
    existing = await session.scalar(
        select(EtfTrackedUnderlyingFact).where(
            EtfTrackedUnderlyingFact.evidence_hash == payload["evidence_hash"],
        )
    )
    if existing is not None:
        return existing, False
    previous = await session.scalar(
        select(EtfTrackedUnderlyingFact)
        .where(
            EtfTrackedUnderlyingFact.etf_code == payload["etf_code"],
            EtfTrackedUnderlyingFact.source == payload["source"],
            EtfTrackedUnderlyingFact.external_source_id == payload["external_source_id"],
            EtfTrackedUnderlyingFact.observed_at <= payload["observed_at"],
        )
        .order_by(EtfTrackedUnderlyingFact.observed_at.desc(), EtfTrackedUnderlyingFact.id.desc())
        .limit(1)
    )
    fact = EtfTrackedUnderlyingFact(
        **payload,
        fact_hash=_hash(
            {
                "evidence_hash": payload["evidence_hash"],
                "supersedes_fact_hash": previous.fact_hash if previous else None,
            }
        ),
        supersedes_fact_id=previous.id if previous else None,
        created_at=utcnow(),
    )
    session.add(fact)
    await session.flush()
    return fact, True


async def _project_taxonomy(
    session: AsyncSession,
    *,
    codes: Sequence[str],
) -> tuple[int, int]:
    selected = await _taxonomy_facts_by_code(session, codes, cutoff=None)
    if not selected:
        return 0, 0
    existing = {
        row.etf_code: row
        for row in (
            await session.scalars(
                select(EtfThemeProfile).where(EtfThemeProfile.etf_code.in_(selected)),
            )
        ).all()
    }
    created = 0
    updated = 0
    for code, fact in selected.items():
        values = {
            "asset_bucket": fact.asset_bucket,
            "theme_group": fact.theme_group,
            "primary_theme": fact.primary_theme,
            "secondary_themes_json": list(fact.secondary_themes_json or []),
            "classification_source": fact.classification_source,
            "classification_confidence": fact.confidence,
            "classification_reason": fact.classification_reason,
        }
        profile = existing.get(code)
        if profile is None:
            session.add(EtfThemeProfile(etf_code=code, **values, created_at=utcnow(), updated_at=utcnow()))
            created += 1
            continue
        if any(getattr(profile, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(profile, key, value)
            profile.updated_at = utcnow()
            updated += 1
    return created, updated


async def _project_tracked_underlyings(
    session: AsyncSession,
    *,
    codes: Sequence[str],
) -> int:
    selected = await _underlying_facts_by_code(session, codes, cutoff=None)
    if not selected:
        return 0
    memberships = (
        await session.scalars(
            select(EtfUniverseMembership).where(
                EtfUniverseMembership.etf_code.in_(selected),
                EtfUniverseMembership.effective_to.is_(None),
            )
        )
    ).all()
    updated = 0
    for membership in memberships:
        fact = selected[membership.etf_code]
        projected_id = (
            fact.tracked_underlying_id if fact.identity_state == "resolved" else None
        )
        if membership.tracked_underlying_id != projected_id:
            membership.tracked_underlying_id = projected_id
            membership.updated_at = utcnow()
            updated += 1
    return updated


async def persist_etf_identity_facts(
    session: AsyncSession,
    *,
    taxonomy_records: Sequence[TaxonomyFactInput] = (),
    underlying_records: Sequence[TrackedUnderlyingFactInput] = (),
) -> IdentityFactPersistResult:
    """Persist at most twenty immutable facts and refresh their current projections.

    The caller owns the transaction and is responsible for committing after this
    function returns.  The bounded interface keeps enrichment workers from loading
    or retaining an unbounded provider response.
    """

    taxonomy_inputs = tuple(taxonomy_records)
    underlying_inputs = tuple(underlying_records)
    total = len(taxonomy_inputs) + len(underlying_inputs)
    if total > MAX_IDENTITY_FACT_RECORDS:
        raise ValueError(f"at most {MAX_IDENTITY_FACT_RECORDS} identity facts may be persisted at once")
    if total == 0:
        return IdentityFactPersistResult()

    taxonomy_payloads = [_taxonomy_payload(record) for record in taxonomy_inputs]
    underlying_payloads = [_underlying_payload(record) for record in underlying_inputs]
    codes = sorted(
        {
            str(payload["etf_code"])
            for payload in [*taxonomy_payloads, *underlying_payloads]
        }
    )
    known_codes = set(
        (
            await session.scalars(
                select(TradableEtf.code).where(TradableEtf.code.in_(codes)),
            )
        ).all()
    )
    missing_codes = sorted(set(codes) - known_codes)
    if missing_codes:
        raise ValueError(f"cannot persist identity facts for unknown ETFs: {', '.join(missing_codes)}")

    taxonomy_inserted = 0
    taxonomy_existing = 0
    for payload in taxonomy_payloads:
        _, inserted = await _persist_taxonomy_fact(session, payload=payload)
        taxonomy_inserted += int(inserted)
        taxonomy_existing += int(not inserted)

    underlying_inserted = 0
    underlying_existing = 0
    for payload in underlying_payloads:
        _, inserted = await _persist_underlying_fact(session, payload=payload)
        underlying_inserted += int(inserted)
        underlying_existing += int(not inserted)

    taxonomy_projection_codes = [str(payload["etf_code"]) for payload in taxonomy_payloads]
    underlying_projection_codes = [str(payload["etf_code"]) for payload in underlying_payloads]
    taxonomy_created, taxonomy_updated = await _project_taxonomy(
        session,
        codes=taxonomy_projection_codes,
    )
    membership_updated = await _project_tracked_underlyings(
        session,
        codes=underlying_projection_codes,
    )
    await session.flush()
    return IdentityFactPersistResult(
        taxonomy_facts_inserted=taxonomy_inserted,
        taxonomy_facts_existing=taxonomy_existing,
        underlying_facts_inserted=underlying_inserted,
        underlying_facts_existing=underlying_existing,
        taxonomy_projections_inserted=taxonomy_created,
        taxonomy_projections_updated=taxonomy_updated,
        membership_projections_updated=membership_updated,
    )


def _clamp_page_size(value: int) -> int:
    return max(
        MIN_IDENTITY_FACT_PAGE_SIZE,
        min(MAX_IDENTITY_FACT_PAGE_SIZE, value),
    )


def _elapsed_seconds(*, started: float, clock: Clock) -> float:
    return max(0.0, clock() - started)


def _adaptive_page_size(
    request: IdentityFactIngestionRequest,
    *,
    remaining_seconds: float,
) -> int:
    """Adapt the requested page to remaining time while preserving the 5--20 bound."""

    available_seconds = remaining_seconds - request.commit_reserve_seconds
    capacity = int(available_seconds / request.estimated_seconds_per_etf)
    if capacity < MIN_IDENTITY_FACT_PAGE_SIZE:
        return 0
    return min(_clamp_page_size(request.target_page_size), capacity)


async def _authoritative_etf_code_page(
    session: AsyncSession,
    *,
    after_code: str | None,
    page_size: int,
) -> tuple[tuple[str, ...], bool]:
    """Read one deterministic page from the active authoritative-universe projection."""

    statement = (
        select(TradableEtf.code)
        .join(
            EtfUniverseMembership,
            EtfUniverseMembership.etf_code == TradableEtf.code,
        )
        .where(EtfUniverseMembership.effective_to.is_(None))
    )
    if after_code is not None:
        statement = statement.where(TradableEtf.code > after_code)
    statement = statement.distinct().order_by(TradableEtf.code.asc()).limit(page_size + 1)
    rows = (await session.scalars(statement)).all()
    return tuple(rows[:page_size]), len(rows) > page_size


def _name_only_source(source: object) -> bool:
    if not isinstance(source, str):
        return True
    normalized = source.casefold()
    return any(marker in normalized for marker in ("name", "keyword"))


def _validate_provider_page(
    page: IdentityFactProviderPage,
    *,
    selected_codes: Sequence[str],
) -> tuple[tuple[TaxonomyFactInput, ...], tuple[TrackedUnderlyingFactInput, ...]]:
    taxonomy_records = tuple(page.taxonomy_records)
    underlying_records = tuple(page.underlying_records)
    total = len(taxonomy_records) + len(underlying_records)
    if total > MAX_IDENTITY_FACT_RECORDS:
        raise ValueError(
            f"provider page exceeds {MAX_IDENTITY_FACT_RECORDS} identity facts",
        )
    allowed_codes = set(selected_codes)
    for record in taxonomy_records:
        if not isinstance(record, TaxonomyFactInput):
            raise ValueError("provider taxonomy output must use TaxonomyFactInput")
        if record.etf_code not in allowed_codes:
            raise ValueError("provider returned taxonomy evidence outside the selected page")
    for record in underlying_records:
        if not isinstance(record, TrackedUnderlyingFactInput):
            raise ValueError(
                "provider underlying output must use TrackedUnderlyingFactInput",
            )
        if record.etf_code not in allowed_codes:
            raise ValueError("provider returned underlying evidence outside the selected page")
        if record.mapping_basis != "authoritative":
            raise ValueError("serial ingestion accepts only authoritative tracked underlyings")
        if _name_only_source(record.source):
            raise ValueError("formal tracked underlyings cannot be inferred from fund names")
    return taxonomy_records, underlying_records


def _slice_result(
    *,
    status: str,
    stop_reason: str | None,
    selected_codes: Sequence[str],
    page_size: int,
    has_more: bool,
    cursor_before: str | None,
    cursor_after: str | None,
    persisted: IdentityFactPersistResult | None,
    started: float,
    clock: Clock,
    error_summary: str | None = None,
) -> IdentityFactIngestionSliceResult:
    return IdentityFactIngestionSliceResult(
        status=status,
        stop_reason=stop_reason,
        selected_codes=tuple(selected_codes),
        page_size=page_size,
        has_more=has_more,
        cursor_before=cursor_before,
        cursor_after=cursor_after,
        persisted=persisted,
        elapsed_seconds=round(_elapsed_seconds(started=started, clock=clock), 6),
        error_summary=error_summary,
    )


async def run_identity_fact_ingestion_slice(
    session: AsyncSession,
    *,
    request: IdentityFactIngestionRequest,
    fetch_page: IdentityFactPageFetcher,
    clock: Clock = time.monotonic,
) -> IdentityFactIngestionSliceResult:
    """Ingest at most one authoritative ETF page without committing the transaction.

    ``fetch_page`` is deliberately required and receives the already selected,
    deterministic code page.  It has no fallback path and this coordinator never
    starts a second provider call.  On success facts and the cursor are flushed in
    one savepoint; the caller commits or rolls back that outer transaction.
    """

    started = clock()
    cursor = await session.get(EtfSyncCursor, request.scope)
    cursor_before = cursor.last_regular_code if cursor is not None else None
    remaining = request.slice_budget_seconds - _elapsed_seconds(
        started=started,
        clock=clock,
    )
    page_size = _adaptive_page_size(request, remaining_seconds=remaining)
    if page_size == 0:
        return _slice_result(
            status="skipped",
            stop_reason="slice_budget_exhausted",
            selected_codes=(),
            page_size=0,
            has_more=False,
            cursor_before=cursor_before,
            cursor_after=cursor_before,
            persisted=None,
            started=started,
            clock=clock,
        )

    selected_codes, has_more = await _authoritative_etf_code_page(
        session,
        after_code=cursor_before,
        page_size=page_size,
    )
    if not selected_codes:
        return _slice_result(
            status="complete",
            stop_reason=None,
            selected_codes=(),
            page_size=page_size,
            has_more=False,
            cursor_before=cursor_before,
            cursor_after=cursor_before,
            persisted=None,
            started=started,
            clock=clock,
        )

    fetch_timeout = (
        request.slice_budget_seconds
        - _elapsed_seconds(started=started, clock=clock)
        - request.commit_reserve_seconds
    )
    if fetch_timeout <= 0:
        return _slice_result(
            status="skipped",
            stop_reason="slice_budget_exhausted",
            selected_codes=selected_codes,
            page_size=page_size,
            has_more=has_more,
            cursor_before=cursor_before,
            cursor_after=cursor_before,
            persisted=None,
            started=started,
            clock=clock,
        )
    try:
        provider_page = await asyncio.wait_for(
            fetch_page(selected_codes),
            timeout=fetch_timeout,
        )
    except TimeoutError:
        return _slice_result(
            status="failed",
            stop_reason="provider_timeout",
            selected_codes=selected_codes,
            page_size=page_size,
            has_more=has_more,
            cursor_before=cursor_before,
            cursor_after=cursor_before,
            persisted=None,
            started=started,
            clock=clock,
        )
    except Exception as exc:  # noqa: BLE001
        return _slice_result(
            status="failed",
            stop_reason="provider_fetch_failed",
            selected_codes=selected_codes,
            page_size=page_size,
            has_more=has_more,
            cursor_before=cursor_before,
            cursor_after=cursor_before,
            persisted=None,
            started=started,
            clock=clock,
            error_summary=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
    if not isinstance(provider_page, IdentityFactProviderPage):
        return _slice_result(
            status="failed",
            stop_reason="invalid_provider_page",
            selected_codes=selected_codes,
            page_size=page_size,
            has_more=has_more,
            cursor_before=cursor_before,
            cursor_after=cursor_before,
            persisted=None,
            started=started,
            clock=clock,
            error_summary="provider must return IdentityFactProviderPage",
        )

    try:
        taxonomy_records, underlying_records = _validate_provider_page(
            provider_page,
            selected_codes=selected_codes,
        )
        async with session.begin_nested():
            persisted = await persist_etf_identity_facts(
                session,
                taxonomy_records=taxonomy_records,
                underlying_records=underlying_records,
            )
            next_cursor = await session.get(EtfSyncCursor, request.scope)
            if next_cursor is None:
                next_cursor = EtfSyncCursor(scope=request.scope)
                session.add(next_cursor)
            next_cursor.last_regular_code = selected_codes[-1]
            next_cursor.last_lane = IDENTITY_FACT_CURSOR_LANE
            next_cursor.updated_at = utcnow()
            await session.flush()
    except Exception as exc:  # noqa: BLE001
        return _slice_result(
            status="failed",
            stop_reason="identity_fact_persist_failed",
            selected_codes=selected_codes,
            page_size=page_size,
            has_more=has_more,
            cursor_before=cursor_before,
            cursor_after=cursor_before,
            persisted=None,
            started=started,
            clock=clock,
            error_summary=f"{type(exc).__name__}: {str(exc)[:160]}",
        )

    return _slice_result(
        status="partial" if has_more else "complete",
        stop_reason=None,
        selected_codes=selected_codes,
        page_size=page_size,
        has_more=has_more,
        cursor_before=cursor_before,
        cursor_after=selected_codes[-1],
        persisted=persisted,
        started=started,
        clock=clock,
    )
