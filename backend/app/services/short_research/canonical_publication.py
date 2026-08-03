from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfCanonicalPublicationRegistry, ShortResearchSignalRun
from app.services.short_research.ranking_contract import canonical_hash
from app.services.short_research.ranking_surfaces import (
    ActionableRankManifest,
    actionable_product_field_policy,
)

CANONICAL_PUBLICATION_IDENTITY_VERSION = "etf_canonical_publication_v1"
PROVIDER_HEALTH_SEAL_VERSION = "etf_provider_health_seal_v1"
PROVIDER_HEALTH_REQUIRED_FIELDS = (
    "provider",
    "provider_health",
    "freshness",
    "provider_consensus",
)
_PROVIDER_HEALTH_STATES = frozenset(
    {"compatible", "missing", "stale", "incompatible", "not_applicable"}
)


class CanonicalPublicationError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalPublicationDraft:
    source_signal_run_id: int
    as_of_trade_date: date
    scope_kind: str
    scope_hash: str
    price_basis: str
    research_contract_hash: str
    actionable_contract_hash: str
    readiness_policy_version: str
    readiness_policy_hash: str
    universe_snapshot_hash: str
    input_snapshot_hash: str
    data_cutoff: datetime
    provider_health_seal: dict[str, Any]
    surface_group_hash: str
    publication_identity_hash: str
    canonical_slot_hash: str

    @property
    def actionable_available(self) -> bool:
        return self.provider_health_seal.get("state") == "compatible"

    @property
    def actionable_unavailable_reason(self) -> str | None:
        if self.actionable_available:
            return None
        reason = self.provider_health_seal.get("unavailable_reason")
        return str(reason) if isinstance(reason, str) and reason else "provider_health_seal_incompatible"


@dataclass(frozen=True)
class CanonicalPublicationRegistration:
    registry: EtfCanonicalPublicationRegistry
    created: bool

    @property
    def winner_run_id(self) -> int:
        return self.registry.source_signal_run_id


def _normalise_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def provider_health_policy(manifest: ActionableRankManifest) -> dict[str, Any]:
    product_policy = actionable_product_field_policy()
    return {
        "policy_version": manifest.eligibility_policy_version,
        "actionable_contract_hash": manifest.manifest_hash,
        "product_field_policy_id": product_policy.policy_id,
        "product_field_policy_hash": product_policy.policy_hash,
        "required_covered_fields": list(PROVIDER_HEALTH_REQUIRED_FIELDS),
    }


def _seal_payload(
    *,
    policy: Mapping[str, Any],
    covered_fields: Sequence[str],
    check_time: datetime | None,
    source_range: Mapping[str, Any],
    candidate_records: Sequence[Mapping[str, Any]],
    state: str,
    unavailable_reason: str | None,
) -> dict[str, Any]:
    return {
        "seal_version": PROVIDER_HEALTH_SEAL_VERSION,
        "policy": dict(policy),
        "covered_fields": list(covered_fields),
        "check_time": _iso_datetime(check_time),
        "source_range": dict(source_range),
        "candidate_records": [dict(record) for record in candidate_records],
        "state": state,
        "unavailable_reason": unavailable_reason,
    }


def _sealed_provider_health_identity(
    *,
    policy: Mapping[str, Any],
    covered_fields: Sequence[str],
    check_time: datetime | None,
    source_range: Mapping[str, Any],
    candidate_records: Sequence[Mapping[str, Any]],
    state: str,
    unavailable_reason: str | None,
) -> dict[str, Any]:
    payload = _seal_payload(
        policy=policy,
        covered_fields=covered_fields,
        check_time=check_time,
        source_range=source_range,
        candidate_records=candidate_records,
        state=state,
        unavailable_reason=unavailable_reason,
    )
    return {"hash": canonical_hash(payload), **payload}


def missing_provider_health_seal(
    *,
    manifest: ActionableRankManifest,
) -> dict[str, Any]:
    return _sealed_provider_health_identity(
        policy=provider_health_policy(manifest),
        covered_fields=PROVIDER_HEALTH_REQUIRED_FIELDS,
        check_time=None,
        source_range={"start": None, "end": None},
        candidate_records=[],
        state="missing",
        unavailable_reason="provider_health_seal_missing",
    )


def build_provider_health_seal(
    *,
    candidate_evidence: Sequence[Mapping[str, Any]],
    manifest: ActionableRankManifest,
    trade_date: date,
    decision_cutoff: datetime,
) -> dict[str, Any]:
    """Build an aggregate seal from factual per-candidate quote evidence.

    The policy can be declared by every candidate, but a missing declaration is
    intentionally not treated as health evidence. It only defaults the *known
    contract policy*, while the provider statuses and source times still have
    to be factual and complete.
    """

    expected_policy = provider_health_policy(manifest)
    records: list[dict[str, Any]] = []
    all_source_times: list[datetime] = []
    health_check_times: list[datetime] = []
    state = "compatible"
    unavailable_reason: str | None = None

    for raw_candidate in sorted(
        candidate_evidence,
        key=lambda item: str(item.get("asset_code") or ""),
    ):
        code = str(raw_candidate.get("asset_code") or "").strip()
        if not code:
            state = "incompatible"
            unavailable_reason = "provider_health_seal_incompatible"
            continue
        statuses = _mapping(raw_candidate.get("field_statuses"))
        source_times = _mapping(raw_candidate.get("source_times"))
        declared_policy = raw_candidate.get("policy")
        if declared_policy is not None and _mapping(declared_policy) != expected_policy:
            state = "incompatible"
            unavailable_reason = "provider_health_seal_incompatible"

        record_statuses = {
            field: str(statuses.get(field) or "missing")
            for field in PROVIDER_HEALTH_REQUIRED_FIELDS
        }
        record_source_times = {
            field: str(source_times[field]) if source_times.get(field) is not None else None
            for field in PROVIDER_HEALTH_REQUIRED_FIELDS
        }
        records.append(
            {
                "asset_code": code,
                "field_statuses": record_statuses,
                "source_times": record_source_times,
            }
        )

        for field in PROVIDER_HEALTH_REQUIRED_FIELDS:
            field_status = record_statuses[field]
            if field_status == "stale":
                if state == "compatible":
                    state = "stale"
                    unavailable_reason = "provider_health_seal_stale"
            elif field_status == "unhealthy":
                state = "incompatible"
                unavailable_reason = "provider_health_seal_incompatible"
            elif field_status != "available" and state == "compatible":
                state = "missing"
                unavailable_reason = "provider_health_seal_missing"

            source_time = _normalise_datetime(record_source_times[field])
            if source_time is None:
                if state == "compatible":
                    state = "missing"
                    unavailable_reason = "provider_health_seal_missing"
                continue
            all_source_times.append(source_time)
            if field == "provider_health":
                health_check_times.append(source_time)
            if source_time.date() != trade_date or source_time > decision_cutoff:
                if state in {"compatible", "missing"}:
                    state = "stale"
                    unavailable_reason = "provider_health_seal_stale"

    if not records:
        state = "not_applicable"
        unavailable_reason = "no_actionable_candidates"
    check_time = max(health_check_times, default=None)
    source_range = {
        "start": _iso_datetime(min(all_source_times)) if all_source_times else None,
        "end": _iso_datetime(max(all_source_times)) if all_source_times else None,
    }
    return _sealed_provider_health_identity(
        policy=expected_policy,
        covered_fields=PROVIDER_HEALTH_REQUIRED_FIELDS,
        check_time=check_time,
        source_range=source_range,
        candidate_records=records,
        state=state,
        unavailable_reason=unavailable_reason,
    )


def validate_provider_health_seal(
    value: object,
    *,
    manifest: ActionableRankManifest,
    trade_date: date,
    decision_cutoff: datetime,
) -> dict[str, Any]:
    """Return the stored seal if valid, otherwise an explicit unavailable seal."""

    raw = _mapping(value)
    if not raw:
        return missing_provider_health_seal(manifest=manifest)
    expected_policy = provider_health_policy(manifest)
    seal_hash = raw.get("hash")
    policy = _mapping(raw.get("policy"))
    covered_fields = raw.get("covered_fields")
    source_range = _mapping(raw.get("source_range"))
    records = raw.get("candidate_records")
    state = str(raw.get("state") or "")
    unavailable_reason = raw.get("unavailable_reason")
    if not isinstance(unavailable_reason, str) and unavailable_reason is not None:
        unavailable_reason = None
    expected_fields = list(PROVIDER_HEALTH_REQUIRED_FIELDS)
    check_time = _normalise_datetime(raw.get("check_time"))
    invalid = (
        raw.get("seal_version") != PROVIDER_HEALTH_SEAL_VERSION
        or not isinstance(seal_hash, str)
        or len(seal_hash) != 64
        or policy != expected_policy
        or not isinstance(covered_fields, list)
        or sorted(str(field) for field in covered_fields) != sorted(expected_fields)
        or not isinstance(records, list)
        or state not in _PROVIDER_HEALTH_STATES
        or set(source_range) != {"start", "end"}
    )
    if invalid:
        return _sealed_provider_health_identity(
            policy=expected_policy,
            covered_fields=expected_fields,
            check_time=None,
            source_range={"start": None, "end": None},
            candidate_records=[],
            state="incompatible",
            unavailable_reason="provider_health_seal_incompatible",
        )

    payload = _seal_payload(
        policy=policy,
        covered_fields=[str(field) for field in covered_fields],
        check_time=check_time,
        source_range=source_range,
        candidate_records=[_mapping(record) for record in records],
        state=state,
        unavailable_reason=unavailable_reason,
    )
    if canonical_hash(payload) != seal_hash:
        return _sealed_provider_health_identity(
            policy=expected_policy,
            covered_fields=expected_fields,
            check_time=None,
            source_range={"start": None, "end": None},
            candidate_records=[],
            state="incompatible",
            unavailable_reason="provider_health_seal_incompatible",
        )
    if state == "compatible":
        source_start = _normalise_datetime(source_range.get("start"))
        source_end = _normalise_datetime(source_range.get("end"))
        if (
            check_time is None
            or source_start is None
            or source_end is None
            or check_time.date() != trade_date
            or source_start.date() != trade_date
            or source_end.date() != trade_date
            or check_time > decision_cutoff
            or source_end > decision_cutoff
        ):
            return _sealed_provider_health_identity(
                policy=expected_policy,
                covered_fields=expected_fields,
                check_time=check_time,
                source_range={
                    "start": _iso_datetime(source_start),
                    "end": _iso_datetime(source_end),
                },
                candidate_records=[_mapping(record) for record in records],
                state="stale",
                unavailable_reason="provider_health_seal_stale",
            )
    return {
        "hash": seal_hash,
        **payload,
    }


def _required_string(value: object, *, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise CanonicalPublicationError(f"canonical publication is missing {name}")
    return normalized


def canonical_publication_draft_from_run(
    run: ShortResearchSignalRun,
    *,
    actionable_manifest: ActionableRankManifest,
) -> CanonicalPublicationDraft:
    summary = _mapping(run.summary_json)
    surfaces = _mapping(summary.get("ranking_surfaces"))
    research_surface = _mapping(surfaces.get("research"))
    actionable_surface = _mapping(surfaces.get("actionable"))
    readiness_policy = _mapping(summary.get("readiness_policy"))
    readiness_policy_version = _required_string(
        readiness_policy.get("policy_version") or summary.get("readiness_policy_version"),
        name="readiness policy version",
    )
    if run.data_cutoff is None or run.as_of_trade_date is None:
        raise CanonicalPublicationError("canonical publication requires trade date and cutoff")
    cutoff_provenance = _mapping(summary.get("cutoff_provenance"))
    market_decision_cutoff = _normalise_datetime(
        cutoff_provenance.get("market_decision_cutoff")
        or _mapping(run.config_json).get("market_decision_cutoff")
    )
    if market_decision_cutoff is None:
        raise CanonicalPublicationError(
            "canonical publication requires market decision cutoff"
        )

    provider_seal = validate_provider_health_seal(
        summary.get("provider_health_identity"),
        manifest=actionable_manifest,
        trade_date=run.as_of_trade_date,
        decision_cutoff=market_decision_cutoff,
    )
    research_contract_hash = _required_string(
        research_surface.get("contract_hash"),
        name="research contract hash",
    )
    actionable_contract_hash = _required_string(
        actionable_surface.get("contract_hash"),
        name="actionable contract hash",
    )
    scope_kind = _required_string(run.scope_kind, name="scope kind")
    scope_hash = _required_string(run.scope_hash, name="scope hash")
    price_basis = _required_string(run.price_basis, name="price basis")
    universe_snapshot_hash = _required_string(
        run.universe_snapshot_hash,
        name="universe snapshot hash",
    )
    input_snapshot_hash = _required_string(
        run.input_snapshot_hash,
        name="input snapshot hash",
    )
    surface_group_hash = _required_string(
        summary.get("surface_group_hash")
        or _mapping(run.config_json).get("surface_group_hash"),
        name="surface group hash",
    )
    readiness_policy_hash = canonical_hash(readiness_policy)
    canonical_slot_hash = canonical_hash(
        {
            "trade_date": run.as_of_trade_date,
            "scope_kind": scope_kind,
            "scope_hash": scope_hash,
            "price_basis": price_basis,
        }
    )
    identity_payload = {
        "identity_version": CANONICAL_PUBLICATION_IDENTITY_VERSION,
        "trade_date": run.as_of_trade_date,
        "scope_kind": scope_kind,
        "scope_hash": scope_hash,
        "price_basis": price_basis,
        "research_contract_hash": research_contract_hash,
        "actionable_contract_hash": actionable_contract_hash,
        "readiness_policy_version": readiness_policy_version,
        "readiness_policy_hash": readiness_policy_hash,
        "universe_snapshot_hash": universe_snapshot_hash,
        "input_snapshot_hash": input_snapshot_hash,
        "data_cutoff": run.data_cutoff,
        "market_decision_cutoff": market_decision_cutoff,
        "provider_health_seal_hash": provider_seal["hash"],
        "surface_group_hash": surface_group_hash,
    }
    return CanonicalPublicationDraft(
        source_signal_run_id=run.id,
        as_of_trade_date=run.as_of_trade_date,
        scope_kind=scope_kind,
        scope_hash=scope_hash,
        price_basis=price_basis,
        research_contract_hash=research_contract_hash,
        actionable_contract_hash=actionable_contract_hash,
        readiness_policy_version=readiness_policy_version,
        readiness_policy_hash=readiness_policy_hash,
        universe_snapshot_hash=universe_snapshot_hash,
        input_snapshot_hash=input_snapshot_hash,
        data_cutoff=run.data_cutoff,
        provider_health_seal=provider_seal,
        surface_group_hash=surface_group_hash,
        publication_identity_hash=canonical_hash(identity_payload),
        canonical_slot_hash=canonical_slot_hash,
    )


def _registry_from_draft(
    draft: CanonicalPublicationDraft,
    *,
    supersedes_publication_id: int | None,
) -> EtfCanonicalPublicationRegistry:
    seal = draft.provider_health_seal
    return EtfCanonicalPublicationRegistry(
        source_signal_run_id=draft.source_signal_run_id,
        as_of_trade_date=draft.as_of_trade_date,
        scope_kind=draft.scope_kind,
        scope_hash=draft.scope_hash,
        price_basis=draft.price_basis,
        research_contract_hash=draft.research_contract_hash,
        actionable_contract_hash=draft.actionable_contract_hash,
        readiness_policy_version=draft.readiness_policy_version,
        readiness_policy_hash=draft.readiness_policy_hash,
        universe_snapshot_hash=draft.universe_snapshot_hash,
        input_snapshot_hash=draft.input_snapshot_hash,
        data_cutoff=draft.data_cutoff,
        provider_health_seal_hash=str(seal["hash"]),
        provider_health_check_time=_normalise_datetime(seal.get("check_time")),
        provider_health_policy=_mapping(seal.get("policy")),
        provider_health_covered_fields=[
            str(field) for field in seal.get("covered_fields") or []
        ],
        provider_health_source_range=_mapping(seal.get("source_range")),
        provider_health_state=str(seal["state"]),
        provider_health_unavailable_reason=(
            str(seal["unavailable_reason"])
            if seal.get("unavailable_reason") is not None
            else None
        ),
        surface_group_hash=draft.surface_group_hash,
        publication_identity_hash=draft.publication_identity_hash,
        canonical_slot_hash=draft.canonical_slot_hash,
        supersedes_publication_id=supersedes_publication_id,
        is_current=True,
    )


async def register_canonical_publication(
    session: AsyncSession,
    *,
    run: ShortResearchSignalRun,
    actionable_manifest: ActionableRankManifest,
) -> CanonicalPublicationRegistration:
    """Create one immutable canonical record or return the exact existing winner.

    A partial unique index owns current-slot arbitration. The nested transaction
    leaves the caller's publication transaction usable after a duplicate race.
    """

    draft = canonical_publication_draft_from_run(
        run,
        actionable_manifest=actionable_manifest,
    )
    existing = await session.scalar(
        select(EtfCanonicalPublicationRegistry).where(
            EtfCanonicalPublicationRegistry.publication_identity_hash
            == draft.publication_identity_hash
        )
    )
    if existing is not None:
        return CanonicalPublicationRegistration(registry=existing, created=False)

    for attempt in range(2):
        try:
            async with session.begin_nested():
                existing = await session.scalar(
                    select(EtfCanonicalPublicationRegistry)
                    .where(
                        EtfCanonicalPublicationRegistry.publication_identity_hash
                        == draft.publication_identity_hash
                    )
                    .with_for_update()
                )
                if existing is not None:
                    return CanonicalPublicationRegistration(
                        registry=existing,
                        created=False,
                    )
                current = await session.scalar(
                    select(EtfCanonicalPublicationRegistry)
                    .where(
                        EtfCanonicalPublicationRegistry.canonical_slot_hash
                        == draft.canonical_slot_hash,
                        EtfCanonicalPublicationRegistry.is_current.is_(True),
                    )
                    .with_for_update()
                )
                registry = _registry_from_draft(
                    draft,
                    supersedes_publication_id=(current.id if current is not None else None),
                )
                if current is not None:
                    current.is_current = False
                session.add(registry)
                await session.flush()
            return CanonicalPublicationRegistration(registry=registry, created=True)
        except IntegrityError as exc:
            existing = await session.scalar(
                select(EtfCanonicalPublicationRegistry).where(
                    EtfCanonicalPublicationRegistry.publication_identity_hash
                    == draft.publication_identity_hash
                )
            )
            if existing is not None:
                return CanonicalPublicationRegistration(registry=existing, created=False)
            if attempt:
                raise CanonicalPublicationError(
                    "canonical publication registry conflict did not yield an exact winner"
                ) from exc
    raise AssertionError("canonical publication registration retry was not reached")


__all__ = [
    "CANONICAL_PUBLICATION_IDENTITY_VERSION",
    "PROVIDER_HEALTH_REQUIRED_FIELDS",
    "PROVIDER_HEALTH_SEAL_VERSION",
    "CanonicalPublicationDraft",
    "CanonicalPublicationError",
    "CanonicalPublicationRegistration",
    "build_provider_health_seal",
    "canonical_publication_draft_from_run",
    "missing_provider_health_seal",
    "provider_health_policy",
    "register_canonical_publication",
    "validate_provider_health_seal",
]
