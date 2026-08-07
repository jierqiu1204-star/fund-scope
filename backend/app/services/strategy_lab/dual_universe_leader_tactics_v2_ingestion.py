"""Bounded, fail-closed ingestion contracts for A-share V2 PIT facts.

This module deliberately contains no provider calls and no transaction control.
The caller owns the transaction around each batch.  The three immutable input
contracts mirror the existing research tables and are safe to hand to
``AsyncSession.execute`` as one bounded executemany parameter sequence.  The
fact-row bound is separate from the collector's 5-20-security page bound so one
security's bounded 300-session history remains a single database write.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    PRICE_BASIS,
    V2ContractError,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_table,
)

INGESTION_SCHEMA_VERSION = "dual_universe_leader_tactics_v2_ashare_ingestion_v1"
APPROVED_PROVIDERS = frozenset({"akshare", "eastmoney", "tickflow"})
FORBIDDEN_RAW_PROVIDERS = frozenset({"sina", "efinance", "tencent"})
MAX_FACT_BATCH_SIZE = 500


def _required_text(value: object, field: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str):
        raise V2ContractError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise V2ContractError(f"{field} must not be empty")
    if len(normalized) > max_length:
        raise V2ContractError(f"{field} exceeds {max_length} characters")
    return normalized


def _optional_text(value: object, field: str, *, max_length: int = 256) -> str | None:
    if value is None:
        return None
    return _required_text(value, field, max_length=max_length)


def _required_date(value: object, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise V2ContractError(f"{field} must be a date")
    return value


def _required_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise V2ContractError(f"{field} must be a datetime")
    return value


def _finite_number(value: object, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise V2ContractError(f"{field} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise V2ContractError(f"{field} must be a finite number")
    if minimum is not None and parsed < minimum:
        raise V2ContractError(f"{field} must be >= {minimum}")
    return parsed


def _required_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise V2ContractError(f"{field} must be a boolean")
    return value


def _canonical_hash(payload: dict[str, Any], supplied: str) -> str:
    expected = stable_contract_hash(payload)
    if supplied and supplied != expected:
        raise V2ContractError("fact_hash does not match the canonical fact payload")
    return expected


def _normalise_code(value: object) -> str:
    return _required_text(value, "asset_code", max_length=32)


def _normalise_provider(value: object) -> str:
    return _required_text(value, "provider", max_length=64).lower()


@dataclass(frozen=True, slots=True)
class AshareUniverseSnapshotFact:
    """One authoritative stock-pool membership fact visible at a cutoff."""

    snapshot_date: date
    asset_code: str
    asset_name: str
    listing_state: str
    board: str | None
    effective_at: datetime
    received_at: datetime
    provider: str
    source_cutoff: datetime
    exclusion_reason: str | None = None
    fact_hash: str = ""

    def __post_init__(self) -> None:
        snapshot_date = _required_date(self.snapshot_date, "snapshot_date")
        asset_code = _normalise_code(self.asset_code)
        asset_name = _required_text(self.asset_name, "asset_name")
        listing_state = _required_text(self.listing_state, "listing_state", max_length=32)
        board = _optional_text(self.board, "board", max_length=32)
        effective_at = _required_datetime(self.effective_at, "effective_at")
        received_at = _required_datetime(self.received_at, "received_at")
        source_cutoff = _required_datetime(self.source_cutoff, "source_cutoff")
        provider = _normalise_provider(self.provider)
        if provider not in APPROVED_PROVIDERS:
            raise V2ContractError(
                "authoritative universe facts require an approved adjusted-data provider"
            )
        exclusion_reason = _optional_text(self.exclusion_reason, "exclusion_reason")
        if effective_at > received_at:
            raise V2ContractError("effective_at cannot be after received_at")
        if received_at > source_cutoff:
            raise V2ContractError("received_at cannot be after source_cutoff")
        payload = {
            "schema_version": INGESTION_SCHEMA_VERSION,
            "fact_type": "ashare_universe_snapshot",
            "snapshot_date": snapshot_date,
            "asset_code": asset_code,
            "asset_name": asset_name,
            "listing_state": listing_state,
            "board": board,
            "effective_at": effective_at,
            "received_at": received_at,
            "provider": provider,
            "source_cutoff": source_cutoff,
            "exclusion_reason": exclusion_reason,
        }
        object.__setattr__(self, "snapshot_date", snapshot_date)
        object.__setattr__(self, "asset_code", asset_code)
        object.__setattr__(self, "asset_name", asset_name)
        object.__setattr__(self, "listing_state", listing_state)
        object.__setattr__(self, "board", board)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "source_cutoff", source_cutoff)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "exclusion_reason", exclusion_reason)
        object.__setattr__(self, "fact_hash", _canonical_hash(payload, self.fact_hash))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": INGESTION_SCHEMA_VERSION,
            "fact_type": "ashare_universe_snapshot",
            "snapshot_date": self.snapshot_date,
            "asset_code": self.asset_code,
            "asset_name": self.asset_name,
            "listing_state": self.listing_state,
            "board": self.board,
            "effective_at": self.effective_at,
            "received_at": self.received_at,
            "provider": self.provider,
            "source_cutoff": self.source_cutoff,
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass(frozen=True, slots=True)
class AshareThemeMembershipFact:
    """One point-in-time theme/industry membership fact."""

    asset_code: str
    group_id: str
    theme: str | None
    sector: str | None
    effective_from: date
    effective_to: date | None
    received_at: datetime
    taxonomy_version: str
    source: str
    confidence: str
    supersedes_fact_hash: str | None
    mapping_kind: str
    tracked_index: str | None = None
    clone_group: str | None = None
    issuer: str | None = None
    fact_hash: str = ""

    def __post_init__(self) -> None:
        asset_code = _normalise_code(self.asset_code)
        group_id = _required_text(self.group_id, "group_id")
        theme = _optional_text(self.theme, "theme")
        sector = _optional_text(self.sector, "sector")
        if theme is None and sector is None:
            raise V2ContractError("theme or sector is required")
        effective_from = _required_date(self.effective_from, "effective_from")
        effective_to = self.effective_to
        if effective_to is not None:
            effective_to = _required_date(effective_to, "effective_to")
            if effective_to < effective_from:
                raise V2ContractError("effective_to cannot be before effective_from")
        received_at = _required_datetime(self.received_at, "received_at")
        taxonomy_version = _required_text(self.taxonomy_version, "taxonomy_version", max_length=128)
        source = _required_text(self.source, "source", max_length=64)
        confidence = _required_text(self.confidence, "confidence", max_length=32)
        supersedes = _optional_text(
            self.supersedes_fact_hash, "supersedes_fact_hash", max_length=128
        )
        mapping_kind = _required_text(self.mapping_kind, "mapping_kind", max_length=32)
        if mapping_kind != "historical_pit":
            raise V2ContractError("mapping_kind must be historical_pit")
        tracked_index = _optional_text(self.tracked_index, "tracked_index", max_length=64)
        clone_group = _optional_text(self.clone_group, "clone_group", max_length=128)
        issuer = _optional_text(self.issuer, "issuer", max_length=128)
        payload = {
            "schema_version": INGESTION_SCHEMA_VERSION,
            "fact_type": "ashare_theme_membership",
            "asset_code": asset_code,
            "group_id": group_id,
            "theme": theme,
            "sector": sector,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "received_at": received_at,
            "taxonomy_version": taxonomy_version,
            "source": source,
            "confidence": confidence,
            "supersedes_fact_hash": supersedes,
            "mapping_kind": mapping_kind,
            "tracked_index": tracked_index,
            "clone_group": clone_group,
            "issuer": issuer,
        }
        for field, value in (
            ("asset_code", asset_code),
            ("group_id", group_id),
            ("theme", theme),
            ("sector", sector),
            ("effective_from", effective_from),
            ("effective_to", effective_to),
            ("received_at", received_at),
            ("taxonomy_version", taxonomy_version),
            ("source", source),
            ("confidence", confidence),
            ("supersedes_fact_hash", supersedes),
            ("mapping_kind", mapping_kind),
            ("tracked_index", tracked_index),
            ("clone_group", clone_group),
            ("issuer", issuer),
        ):
            object.__setattr__(self, field, value)
        object.__setattr__(self, "fact_hash", _canonical_hash(payload, self.fact_hash))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": INGESTION_SCHEMA_VERSION,
            "fact_type": "ashare_theme_membership",
            "asset_code": self.asset_code,
            "group_id": self.group_id,
            "theme": self.theme,
            "sector": self.sector,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "received_at": self.received_at,
            "taxonomy_version": self.taxonomy_version,
            "source": self.source,
            "confidence": self.confidence,
            "supersedes_fact_hash": self.supersedes_fact_hash,
            "mapping_kind": self.mapping_kind,
            "tracked_index": self.tracked_index,
            "clone_group": self.clone_group,
            "issuer": self.issuer,
        }


@dataclass(frozen=True, slots=True)
class AshareAdjustedPriceFact:
    """One adjusted OHLCV fact with fail-closed decision governance."""

    asset_code: str
    trade_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    volume: float
    amount: float
    turnover: float
    price_basis: str
    provider: str
    adjustment_version: str
    revision_id: str
    received_at: datetime | None
    historical_research_only: bool = False
    decision_eligible: bool = False
    fact_hash: str = ""

    def __post_init__(self) -> None:
        asset_code = _normalise_code(self.asset_code)
        trade_date = _required_date(self.trade_date, "trade_date")
        adjusted_open = _finite_number(self.adjusted_open, "adjusted_open", minimum=0.0)
        adjusted_high = _finite_number(self.adjusted_high, "adjusted_high", minimum=0.0)
        adjusted_low = _finite_number(self.adjusted_low, "adjusted_low", minimum=0.0)
        adjusted_close = _finite_number(self.adjusted_close, "adjusted_close", minimum=0.0)
        if min(adjusted_open, adjusted_high, adjusted_low, adjusted_close) <= 0:
            raise V2ContractError("adjusted OHLC prices must be > 0")
        if adjusted_high < max(adjusted_open, adjusted_close):
            raise V2ContractError("adjusted_high must cover adjusted open and close")
        if adjusted_low > min(adjusted_open, adjusted_close):
            raise V2ContractError("adjusted_low must not exceed adjusted open and close")
        if adjusted_low > adjusted_high:
            raise V2ContractError("adjusted_low cannot exceed adjusted_high")
        volume = _finite_number(self.volume, "volume", minimum=0.0)
        amount = _finite_number(self.amount, "amount", minimum=0.0)
        turnover = _finite_number(self.turnover, "turnover", minimum=0.0)
        price_basis = _required_text(self.price_basis, "price_basis", max_length=64)
        provider = _normalise_provider(self.provider)
        adjustment_version = _required_text(
            self.adjustment_version, "adjustment_version", max_length=128
        )
        revision_id = _required_text(self.revision_id, "revision_id", max_length=128)
        received_at = self.received_at
        if received_at is not None:
            received_at = _required_datetime(received_at, "received_at")
            if received_at.date() < trade_date:
                raise V2ContractError("received_at cannot precede trade_date")
        historical_only = _required_bool(self.historical_research_only, "historical_research_only")
        decision_eligible = _required_bool(self.decision_eligible, "decision_eligible")
        if received_at is None:
            # The database has no receipt timestamp to support a PIT decision.
            # Force the audit-only state instead of allowing an unsafe caller flag.
            historical_only = True
            decision_eligible = False
        elif historical_only and decision_eligible:
            raise V2ContractError("historical_research_only facts cannot be decision eligible")
        if decision_eligible:
            if provider not in APPROVED_PROVIDERS:
                raise V2ContractError(
                    "only approved adjusted facts may be decision eligible"
                )
            if price_basis != PRICE_BASIS:
                raise V2ContractError(
                    "decision-eligible adjusted facts require total_return_adjusted"
                )
        if provider in FORBIDDEN_RAW_PROVIDERS and decision_eligible:
            raise V2ContractError("raw provider facts cannot be decision eligible")
        payload = {
            "schema_version": INGESTION_SCHEMA_VERSION,
            "fact_type": "ashare_adjusted_price",
            "asset_code": asset_code,
            "trade_date": trade_date,
            "adjusted_open": adjusted_open,
            "adjusted_high": adjusted_high,
            "adjusted_low": adjusted_low,
            "adjusted_close": adjusted_close,
            "volume": volume,
            "amount": amount,
            "turnover": turnover,
            "price_basis": price_basis,
            "provider": provider,
            "adjustment_version": adjustment_version,
            "revision_id": revision_id,
            "received_at": received_at,
            "historical_research_only": historical_only,
            "decision_eligible": decision_eligible,
        }
        for field, value in (
            ("asset_code", asset_code),
            ("trade_date", trade_date),
            ("adjusted_open", adjusted_open),
            ("adjusted_high", adjusted_high),
            ("adjusted_low", adjusted_low),
            ("adjusted_close", adjusted_close),
            ("volume", volume),
            ("amount", amount),
            ("turnover", turnover),
            ("price_basis", price_basis),
            ("provider", provider),
            ("adjustment_version", adjustment_version),
            ("revision_id", revision_id),
            ("received_at", received_at),
            ("historical_research_only", historical_only),
            ("decision_eligible", decision_eligible),
        ):
            object.__setattr__(self, field, value)
        object.__setattr__(self, "fact_hash", _canonical_hash(payload, self.fact_hash))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": INGESTION_SCHEMA_VERSION,
            "fact_type": "ashare_adjusted_price",
            "asset_code": self.asset_code,
            "trade_date": self.trade_date,
            "adjusted_open": self.adjusted_open,
            "adjusted_high": self.adjusted_high,
            "adjusted_low": self.adjusted_low,
            "adjusted_close": self.adjusted_close,
            "volume": self.volume,
            "amount": self.amount,
            "turnover": self.turnover,
            "price_basis": self.price_basis,
            "provider": self.provider,
            "adjustment_version": self.adjustment_version,
            "revision_id": self.revision_id,
            "received_at": self.received_at,
            "historical_research_only": self.historical_research_only,
            "decision_eligible": self.decision_eligible,
        }


FactT = TypeVar("FactT")


def _bounded_batch(facts: Sequence[FactT], expected_type: type[FactT]) -> tuple[FactT, ...]:
    batch = tuple(facts)
    if len(batch) > MAX_FACT_BATCH_SIZE:
        raise V2ContractError(f"a fact batch cannot exceed {MAX_FACT_BATCH_SIZE} rows")
    if any(not isinstance(fact, expected_type) for fact in batch):
        raise V2ContractError("fact batch contains an incompatible contract type")
    return batch


async def persist_ashare_universe_snapshot_batch(
    session: AsyncSession,
    facts: Sequence[AshareUniverseSnapshotFact],
) -> int:
    """Batch-insert authoritative universe facts without committing."""

    assert_v2_research_table("ashare_research_universe_snapshots")
    batch = _bounded_batch(facts, AshareUniverseSnapshotFact)
    if not batch:
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO ashare_research_universe_snapshots
                (snapshot_date, asset_code, asset_name, listing_state, board,
                 effective_at, received_at, provider, source_cutoff,
                 exclusion_reason, fact_hash)
            VALUES (:snapshot_date, :asset_code, :asset_name, :listing_state, :board,
                    :effective_at, :received_at, :provider, :source_cutoff,
                    :exclusion_reason, :fact_hash)
            ON CONFLICT DO NOTHING
            """
        ),
        [
            {
                "snapshot_date": fact.snapshot_date,
                "asset_code": fact.asset_code,
                "asset_name": fact.asset_name,
                "listing_state": fact.listing_state,
                "board": fact.board,
                "effective_at": fact.effective_at,
                "received_at": fact.received_at,
                "provider": fact.provider,
                "source_cutoff": fact.source_cutoff,
                "exclusion_reason": fact.exclusion_reason,
                "fact_hash": fact.fact_hash,
            }
            for fact in batch
        ],
    )
    return max(0, int(result.rowcount or 0))


async def persist_ashare_theme_membership_batch(
    session: AsyncSession,
    facts: Sequence[AshareThemeMembershipFact],
) -> int:
    """Batch-insert PIT theme/industry membership facts without committing."""

    assert_v2_research_table("ashare_theme_membership_facts")
    batch = _bounded_batch(facts, AshareThemeMembershipFact)
    if not batch:
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO ashare_theme_membership_facts
                (asset_code, group_id, theme, sector, effective_from, effective_to,
                 received_at, taxonomy_version, source, confidence,
                 supersedes_fact_hash, mapping_kind, tracked_index, clone_group,
                 issuer, fact_hash)
            VALUES (:asset_code, :group_id, :theme, :sector, :effective_from, :effective_to,
                    :received_at, :taxonomy_version, :source, :confidence,
                    :supersedes_fact_hash, :mapping_kind, :tracked_index, :clone_group,
                    :issuer, :fact_hash)
            ON CONFLICT (fact_hash) DO NOTHING
            """
        ),
        [
            {
                "asset_code": fact.asset_code,
                "group_id": fact.group_id,
                "theme": fact.theme,
                "sector": fact.sector,
                "effective_from": fact.effective_from,
                "effective_to": fact.effective_to,
                "received_at": fact.received_at,
                "taxonomy_version": fact.taxonomy_version,
                "source": fact.source,
                "confidence": fact.confidence,
                "supersedes_fact_hash": fact.supersedes_fact_hash,
                "mapping_kind": fact.mapping_kind,
                "tracked_index": fact.tracked_index,
                "clone_group": fact.clone_group,
                "issuer": fact.issuer,
                "fact_hash": fact.fact_hash,
            }
            for fact in batch
        ],
    )
    return max(0, int(result.rowcount or 0))


async def persist_ashare_adjusted_price_batch(
    session: AsyncSession,
    facts: Sequence[AshareAdjustedPriceFact],
) -> int:
    """Batch-insert adjusted OHLCV facts without committing or rolling back."""

    assert_v2_research_table("ashare_adjusted_price_facts")
    batch = _bounded_batch(facts, AshareAdjustedPriceFact)
    if not batch:
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO ashare_adjusted_price_facts
                (asset_code, trade_date, adjusted_open, adjusted_high, adjusted_low,
                 adjusted_close, volume, amount, turnover, price_basis, provider,
                 adjustment_version, revision_id, received_at,
                 historical_research_only, decision_eligible, fact_hash)
            VALUES (:asset_code, :trade_date, :adjusted_open, :adjusted_high, :adjusted_low,
                    :adjusted_close, :volume, :amount, :turnover, :price_basis, :provider,
                    :adjustment_version, :revision_id, :received_at,
                    :historical_research_only, :decision_eligible, :fact_hash)
            ON CONFLICT (fact_hash) DO NOTHING
            """
        ),
        [
            {
                "asset_code": fact.asset_code,
                "trade_date": fact.trade_date,
                "adjusted_open": fact.adjusted_open,
                "adjusted_high": fact.adjusted_high,
                "adjusted_low": fact.adjusted_low,
                "adjusted_close": fact.adjusted_close,
                "volume": fact.volume,
                "amount": fact.amount,
                "turnover": fact.turnover,
                "price_basis": fact.price_basis,
                "provider": fact.provider,
                "adjustment_version": fact.adjustment_version,
                "revision_id": fact.revision_id,
                "received_at": fact.received_at,
                "historical_research_only": fact.historical_research_only,
                "decision_eligible": fact.decision_eligible,
                "fact_hash": fact.fact_hash,
            }
            for fact in batch
        ],
    )
    return max(0, int(result.rowcount or 0))


__all__ = [
    "APPROVED_PROVIDERS",
    "AshareAdjustedPriceFact",
    "AshareThemeMembershipFact",
    "AshareUniverseSnapshotFact",
    "FORBIDDEN_RAW_PROVIDERS",
    "INGESTION_SCHEMA_VERSION",
    "MAX_FACT_BATCH_SIZE",
    "persist_ashare_adjusted_price_batch",
    "persist_ashare_theme_membership_batch",
    "persist_ashare_universe_snapshot_batch",
]
