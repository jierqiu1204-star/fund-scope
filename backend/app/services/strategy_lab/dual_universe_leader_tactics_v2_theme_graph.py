"""Immutable contracts and bounded persistence for the A-share theme graph.

This module intentionally has no provider calls, scheduler integration, or
transaction ownership.  Callers persist at most one bounded batch and decide
when to commit it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import V2ContractError

THEME_GRAPH_SCHEMA_VERSION = "ashare_multilayer_theme_graph_v1"
THEME_REGISTRY_VERSION = "ashare_curated_theme_registry_v2"
MAX_THEME_GRAPH_BATCH_SIZE = 500
MIN_AVAILABLE_THEME_PEERS = 5

_GRAPH_TABLES = frozenset(
    {
        "ashare_industry_path_facts",
        "ashare_fine_theme_membership_facts",
        "ashare_theme_capture_runs",
        "ashare_theme_state_facts",
    }
)


class ThemeRelationKind(StrEnum):
    PROVIDER_CONCEPT = "provider_concept"
    INDUSTRY_UNION_PROXY = "industry_union_proxy"


class CaptureStatus(StrEnum):
    PARTIAL = "partial"
    COMPLETE = "complete"
    FAILED = "failed"


def _required_text(value: object, field: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str):
        raise V2ContractError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise V2ContractError(f"{field} must not be empty")
    if len(normalized) > max_length:
        raise V2ContractError(f"{field} exceeds {max_length} characters")
    return normalized


def _optional_text(
    value: object, field: str, *, max_length: int = 256
) -> str | None:
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


def _finite_number(
    value: object,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise V2ContractError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise V2ContractError(f"{field} must be a finite number")
    if minimum is not None and result < minimum:
        raise V2ContractError(f"{field} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise V2ContractError(f"{field} must be <= {maximum}")
    return result


def _optional_finite_number(
    value: object,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field, minimum=minimum, maximum=maximum)


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise V2ContractError(f"{field} must be a non-negative integer")
    return value


def _canonical_hash(payload: dict[str, Any], supplied: str, field: str) -> str:
    expected = stable_contract_hash(payload)
    if supplied and supplied != expected:
        raise V2ContractError(f"{field} does not match the canonical payload")
    return expected


def _assert_graph_table(table_name: str) -> None:
    if table_name not in _GRAPH_TABLES:
        raise V2ContractError("theme graph persistence cannot leave its research tables")


@dataclass(frozen=True, slots=True)
class ThemeDefinition:
    canonical_key: str
    display_label: str
    relation_kind: ThemeRelationKind
    source: str
    aliases: tuple[str, ...]
    priority: int
    provider_theme_code: str | None = None
    provider_theme_label: str | None = None
    sw3_component_labels: tuple[str, ...] = ()
    registry_version: str = THEME_REGISTRY_VERSION
    definition_hash: str = ""

    def __post_init__(self) -> None:
        canonical_key = _required_text(
            self.canonical_key, "canonical_key", max_length=128
        )
        display_label = _required_text(self.display_label, "display_label")
        if not isinstance(self.relation_kind, ThemeRelationKind):
            raise V2ContractError("relation_kind must be registered")
        source = _required_text(self.source, "source", max_length=128)
        aliases = tuple(
            sorted(
                {
                    display_label,
                    *(_required_text(alias, "alias") for alias in self.aliases),
                }
            )
        )
        priority = _non_negative_int(self.priority, "priority")
        provider_theme_code = _optional_text(
            self.provider_theme_code, "provider_theme_code", max_length=128
        )
        provider_theme_label = _optional_text(
            self.provider_theme_label, "provider_theme_label"
        )
        sw3_labels = tuple(
            sorted(
                {
                    _required_text(label, "sw3_component_label")
                    for label in self.sw3_component_labels
                }
            )
        )
        registry_version = _required_text(
            self.registry_version, "registry_version", max_length=128
        )
        if self.relation_kind is ThemeRelationKind.PROVIDER_CONCEPT:
            if provider_theme_label is None or sw3_labels:
                raise V2ContractError(
                    "provider concepts require a provider label and no SW3 proxy rules"
                )
        elif provider_theme_code is not None or provider_theme_label is not None:
            raise V2ContractError("industry union proxies cannot claim provider identity")
        elif not sw3_labels:
            raise V2ContractError("industry union proxies require disclosed SW3 components")

        payload = {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "theme_definition",
            "canonical_key": canonical_key,
            "display_label": display_label,
            "relation_kind": self.relation_kind.value,
            "source": source,
            "aliases": aliases,
            "priority": priority,
            "provider_theme_code": provider_theme_code,
            "provider_theme_label": provider_theme_label,
            "sw3_component_labels": sw3_labels,
            "registry_version": registry_version,
        }
        object.__setattr__(self, "canonical_key", canonical_key)
        object.__setattr__(self, "display_label", display_label)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "provider_theme_code", provider_theme_code)
        object.__setattr__(self, "provider_theme_label", provider_theme_label)
        object.__setattr__(self, "sw3_component_labels", sw3_labels)
        object.__setattr__(self, "registry_version", registry_version)
        object.__setattr__(
            self,
            "definition_hash",
            _canonical_hash(payload, self.definition_hash, "definition_hash"),
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "theme_definition",
            "canonical_key": self.canonical_key,
            "display_label": self.display_label,
            "relation_kind": self.relation_kind.value,
            "source": self.source,
            "aliases": self.aliases,
            "priority": self.priority,
            "provider_theme_code": self.provider_theme_code,
            "provider_theme_label": self.provider_theme_label,
            "sw3_component_labels": self.sw3_component_labels,
            "registry_version": self.registry_version,
        }


THEME_DEFINITIONS = (
    ThemeDefinition(
        canonical_key="innovation_drug",
        display_label="创新药",
        relation_kind=ThemeRelationKind.PROVIDER_CONCEPT,
        source="eastmoney.concept.current",
        aliases=("创新药", "创新药概念"),
        provider_theme_code="BK1106",
        provider_theme_label="创新药",
        priority=10,
    ),
    ThemeDefinition(
        canonical_key="rare_earth",
        display_label="稀土/稀土永磁",
        relation_kind=ThemeRelationKind.PROVIDER_CONCEPT,
        source="eastmoney.concept.current",
        aliases=("稀土", "稀土永磁", "稀土磁材"),
        provider_theme_label="稀土永磁",
        priority=20,
    ),
    ThemeDefinition(
        canonical_key="passive_components",
        display_label="被动元件/MLCC",
        relation_kind=ThemeRelationKind.PROVIDER_CONCEPT,
        source="eastmoney.concept.current",
        aliases=("被动元件", "被动元件概念", "MLCC"),
        provider_theme_label="被动元件概念",
        priority=30,
    ),
    ThemeDefinition(
        canonical_key="liquid_cooling",
        display_label="液冷",
        relation_kind=ThemeRelationKind.PROVIDER_CONCEPT,
        source="eastmoney.concept.current",
        aliases=("液冷", "液冷概念", "液冷服务器", "数据中心液冷"),
        provider_theme_code="BK1138",
        provider_theme_label="液冷服务器",
        priority=40,
    ),
    ThemeDefinition(
        canonical_key="innovation_drug",
        display_label="创新药产业代理",
        relation_kind=ThemeRelationKind.INDUSTRY_UNION_PROXY,
        source="tickflow.sw2021.industry_union_proxy",
        aliases=("创新药产业代理",),
        sw3_component_labels=("化学制剂", "疫苗", "其他生物制品"),
        priority=110,
    ),
    ThemeDefinition(
        canonical_key="rare_earth",
        display_label="稀土/稀土永磁产业代理",
        relation_kind=ThemeRelationKind.INDUSTRY_UNION_PROXY,
        source="tickflow.sw2021.industry_union_proxy",
        aliases=("稀土产业代理", "稀土永磁产业代理"),
        sw3_component_labels=("稀土", "磁性材料"),
        priority=120,
    ),
    ThemeDefinition(
        canonical_key="passive_components",
        display_label="被动元件/MLCC产业代理",
        relation_kind=ThemeRelationKind.INDUSTRY_UNION_PROXY,
        source="tickflow.sw2021.industry_union_proxy",
        aliases=("被动元件产业代理", "MLCC产业代理"),
        sw3_component_labels=("被动元件",),
        priority=130,
    ),
)


THEME_REGISTRY_HASH = stable_contract_hash(
    {
        "registry_version": THEME_REGISTRY_VERSION,
        "definitions": [
            definition.canonical_payload()
            for definition in sorted(
                THEME_DEFINITIONS,
                key=lambda item: (item.canonical_key, item.relation_kind.value, item.source),
            )
        ],
    }
)


def registered_theme_definitions(
    *, relation_kind: ThemeRelationKind | None = None
) -> tuple[ThemeDefinition, ...]:
    if relation_kind is not None and not isinstance(relation_kind, ThemeRelationKind):
        raise V2ContractError("relation_kind must be registered")
    return tuple(
        definition
        for definition in THEME_DEFINITIONS
        if relation_kind is None or definition.relation_kind is relation_kind
    )


def theme_definitions_for_key(canonical_key: str) -> tuple[ThemeDefinition, ...]:
    normalized = _required_text(canonical_key, "canonical_key", max_length=128)
    matches = tuple(
        definition
        for definition in THEME_DEFINITIONS
        if definition.canonical_key == normalized
    )
    if not matches:
        raise V2ContractError("canonical theme key is not registered")
    return matches


def resolve_theme_definitions(
    label: str, *, relation_kind: ThemeRelationKind | None = None
) -> tuple[ThemeDefinition, ...]:
    normalized = _required_text(label, "label")
    matches = tuple(
        definition
        for definition in THEME_DEFINITIONS
        if normalized in definition.aliases
        and (relation_kind is None or definition.relation_kind is relation_kind)
    )
    if not matches:
        raise V2ContractError("theme label is not registered")
    return matches


@dataclass(frozen=True, slots=True)
class AshareIndustryPathFact:
    asset_code: str
    taxonomy: str
    taxonomy_version: str
    mapping_kind: str
    level1_code: str | None
    level1_label: str | None
    level2_code: str | None
    level2_label: str | None
    level3_code: str | None
    level3_label: str | None
    effective_from: date
    effective_to: date | None
    snapshot_date: date
    received_at: datetime
    source: str
    confidence: float
    source_snapshot_hash: str
    fact_hash: str = ""

    def __post_init__(self) -> None:
        asset_code = _required_text(self.asset_code, "asset_code", max_length=32)
        taxonomy = _required_text(self.taxonomy, "taxonomy", max_length=64)
        taxonomy_version = _required_text(
            self.taxonomy_version, "taxonomy_version", max_length=128
        )
        mapping_kind = _required_text(self.mapping_kind, "mapping_kind", max_length=32)
        if mapping_kind not in {"primary_hierarchy", "broad_fallback"}:
            raise V2ContractError("industry path mapping_kind is not registered")
        levels = tuple(
            _optional_text(value, field, max_length=256)
            for value, field in (
                (self.level1_code, "level1_code"),
                (self.level1_label, "level1_label"),
                (self.level2_code, "level2_code"),
                (self.level2_label, "level2_label"),
                (self.level3_code, "level3_code"),
                (self.level3_label, "level3_label"),
            )
        )
        if mapping_kind == "primary_hierarchy" and any(value is None for value in levels):
            raise V2ContractError("primary industry hierarchy requires all three levels")
        if mapping_kind == "broad_fallback" and levels[1] is None:
            raise V2ContractError("broad industry fallback requires a factual level-one label")
        effective_from = _required_date(self.effective_from, "effective_from")
        effective_to = self.effective_to
        if effective_to is not None:
            effective_to = _required_date(effective_to, "effective_to")
            if effective_to < effective_from:
                raise V2ContractError("effective_to cannot be before effective_from")
        snapshot_date = _required_date(self.snapshot_date, "snapshot_date")
        received_at = _required_datetime(self.received_at, "received_at")
        if max(effective_from, snapshot_date) > received_at.date():
            raise V2ContractError("industry path cannot be visible before receipt")
        source = _required_text(self.source, "source", max_length=128)
        confidence = _finite_number(
            self.confidence, "confidence", minimum=0.0, maximum=1.0
        )
        source_snapshot_hash = _required_text(
            self.source_snapshot_hash, "source_snapshot_hash", max_length=128
        )
        payload = {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_industry_path",
            "asset_code": asset_code,
            "taxonomy": taxonomy,
            "taxonomy_version": taxonomy_version,
            "mapping_kind": mapping_kind,
            "level1_code": levels[0],
            "level1_label": levels[1],
            "level2_code": levels[2],
            "level2_label": levels[3],
            "level3_code": levels[4],
            "level3_label": levels[5],
            "effective_from": effective_from,
            "effective_to": effective_to,
            "snapshot_date": snapshot_date,
            "received_at": received_at,
            "source": source,
            "confidence": confidence,
            "source_snapshot_hash": source_snapshot_hash,
        }
        for field, value in payload.items():
            if field not in {"schema_version", "fact_type"}:
                object.__setattr__(self, field, value)
        object.__setattr__(
            self, "fact_hash", _canonical_hash(payload, self.fact_hash, "fact_hash")
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_industry_path",
            **{
                field: getattr(self, field)
                for field in (
                    "asset_code",
                    "taxonomy",
                    "taxonomy_version",
                    "mapping_kind",
                    "level1_code",
                    "level1_label",
                    "level2_code",
                    "level2_label",
                    "level3_code",
                    "level3_label",
                    "effective_from",
                    "effective_to",
                    "snapshot_date",
                    "received_at",
                    "source",
                    "confidence",
                    "source_snapshot_hash",
                )
            },
        }


@dataclass(frozen=True, slots=True)
class AshareThemeRelationFact:
    asset_code: str
    canonical_theme_key: str
    theme_label: str
    relation_kind: ThemeRelationKind
    effective_from: date
    effective_to: date | None
    received_at: datetime
    taxonomy_version: str
    source: str
    confidence: float
    source_snapshot_date: date
    source_snapshot_hash: str
    capture_run_hash: str
    provider_theme_code: str | None = None
    provider_theme_label: str | None = None
    membership_reason: str | None = None
    exposure_weight: float | None = None
    fact_hash: str = ""

    def __post_init__(self) -> None:
        asset_code = _required_text(self.asset_code, "asset_code", max_length=32)
        key = _required_text(
            self.canonical_theme_key, "canonical_theme_key", max_length=128
        )
        theme_label = _required_text(self.theme_label, "theme_label")
        if not isinstance(self.relation_kind, ThemeRelationKind):
            raise V2ContractError("relation_kind must be registered")
        effective_from = _required_date(self.effective_from, "effective_from")
        effective_to = self.effective_to
        if effective_to is not None:
            effective_to = _required_date(effective_to, "effective_to")
            if effective_to < effective_from:
                raise V2ContractError("effective_to cannot be before effective_from")
        received_at = _required_datetime(self.received_at, "received_at")
        snapshot_date = _required_date(self.source_snapshot_date, "source_snapshot_date")
        if max(effective_from, snapshot_date) > received_at.date():
            raise V2ContractError("theme relation cannot be visible before receipt")
        taxonomy_version = _required_text(
            self.taxonomy_version, "taxonomy_version", max_length=128
        )
        source = _required_text(self.source, "source", max_length=128)
        confidence = _finite_number(
            self.confidence, "confidence", minimum=0.0, maximum=1.0
        )
        snapshot_hash = _required_text(
            self.source_snapshot_hash, "source_snapshot_hash", max_length=128
        )
        capture_hash = _required_text(
            self.capture_run_hash, "capture_run_hash", max_length=128
        )
        provider_code = _optional_text(
            self.provider_theme_code, "provider_theme_code", max_length=128
        )
        provider_label = _optional_text(
            self.provider_theme_label, "provider_theme_label"
        )
        reason = _optional_text(self.membership_reason, "membership_reason", max_length=512)
        weight = _optional_finite_number(
            self.exposure_weight, "exposure_weight", minimum=0.0, maximum=1.0
        )
        if self.relation_kind is ThemeRelationKind.PROVIDER_CONCEPT:
            if provider_label is None:
                raise V2ContractError("provider concept relation requires provider label")
        elif provider_code is not None or provider_label is not None:
            raise V2ContractError("industry union proxy cannot claim provider identity")
        elif reason is None:
            raise V2ContractError("industry union proxy requires a disclosed membership reason")
        payload = {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_theme_relation",
            "asset_code": asset_code,
            "canonical_theme_key": key,
            "theme_label": theme_label,
            "relation_kind": self.relation_kind.value,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "received_at": received_at,
            "taxonomy_version": taxonomy_version,
            "source": source,
            "confidence": confidence,
            "source_snapshot_date": snapshot_date,
            "source_snapshot_hash": snapshot_hash,
            "capture_run_hash": capture_hash,
            "provider_theme_code": provider_code,
            "provider_theme_label": provider_label,
            "membership_reason": reason,
            "exposure_weight": weight,
        }
        for field, value in (
            ("asset_code", asset_code),
            ("canonical_theme_key", key),
            ("theme_label", theme_label),
            ("effective_from", effective_from),
            ("effective_to", effective_to),
            ("received_at", received_at),
            ("taxonomy_version", taxonomy_version),
            ("source", source),
            ("confidence", confidence),
            ("source_snapshot_date", snapshot_date),
            ("source_snapshot_hash", snapshot_hash),
            ("capture_run_hash", capture_hash),
            ("provider_theme_code", provider_code),
            ("provider_theme_label", provider_label),
            ("membership_reason", reason),
            ("exposure_weight", weight),
        ):
            object.__setattr__(self, field, value)
        object.__setattr__(
            self, "fact_hash", _canonical_hash(payload, self.fact_hash, "fact_hash")
        )

    @property
    def group_id(self) -> str:
        return f"fine_theme:{self.canonical_theme_key}"

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_theme_relation",
            "asset_code": self.asset_code,
            "canonical_theme_key": self.canonical_theme_key,
            "theme_label": self.theme_label,
            "relation_kind": self.relation_kind.value,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "received_at": self.received_at,
            "taxonomy_version": self.taxonomy_version,
            "source": self.source,
            "confidence": self.confidence,
            "source_snapshot_date": self.source_snapshot_date,
            "source_snapshot_hash": self.source_snapshot_hash,
            "capture_run_hash": self.capture_run_hash,
            "provider_theme_code": self.provider_theme_code,
            "provider_theme_label": self.provider_theme_label,
            "membership_reason": self.membership_reason,
            "exposure_weight": self.exposure_weight,
        }


@dataclass(frozen=True, slots=True)
class AshareThemeCaptureRunFact:
    source: str
    source_snapshot_id: str
    source_snapshot_date: date
    taxonomy_version: str
    registry_version: str
    status: CaptureStatus
    expected_count: int
    completed_count: int
    content_hash: str | None
    cursor: str | None
    started_at: datetime
    completed_at: datetime | None
    received_at: datetime
    error_reason: str | None = None
    run_hash: str = ""

    def __post_init__(self) -> None:
        source = _required_text(self.source, "source", max_length=128)
        snapshot_id = _required_text(
            self.source_snapshot_id, "source_snapshot_id", max_length=256
        )
        snapshot_date = _required_date(self.source_snapshot_date, "source_snapshot_date")
        taxonomy_version = _required_text(
            self.taxonomy_version, "taxonomy_version", max_length=128
        )
        registry_version = _required_text(
            self.registry_version, "registry_version", max_length=128
        )
        if not isinstance(self.status, CaptureStatus):
            raise V2ContractError("capture status must be registered")
        expected = _non_negative_int(self.expected_count, "expected_count")
        completed = _non_negative_int(self.completed_count, "completed_count")
        if completed > expected:
            raise V2ContractError("completed_count cannot exceed expected_count")
        content_hash = _optional_text(self.content_hash, "content_hash", max_length=128)
        cursor = _optional_text(self.cursor, "cursor", max_length=512)
        started_at = _required_datetime(self.started_at, "started_at")
        completed_at = self.completed_at
        if completed_at is not None:
            completed_at = _required_datetime(completed_at, "completed_at")
        received_at = _required_datetime(self.received_at, "received_at")
        if snapshot_date > received_at.date() or started_at > received_at:
            raise V2ContractError("capture run cannot be visible before receipt")
        if completed_at is not None and not (started_at <= completed_at <= received_at):
            raise V2ContractError("capture completion time is invalid")
        error = _optional_text(self.error_reason, "error_reason", max_length=512)
        if self.status is CaptureStatus.COMPLETE:
            if completed != expected or content_hash is None or completed_at is None or error:
                raise V2ContractError("complete capture must be sealed and error-free")
        elif self.status is CaptureStatus.FAILED:
            if error is None or completed_at is not None:
                raise V2ContractError("failed capture requires an error and cannot be sealed")
        elif completed == expected and expected > 0:
            raise V2ContractError("fully captured source must be sealed as complete")
        payload = {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_theme_capture_run",
            "source": source,
            "source_snapshot_id": snapshot_id,
            "source_snapshot_date": snapshot_date,
            "taxonomy_version": taxonomy_version,
            "registry_version": registry_version,
            "status": self.status.value,
            "expected_count": expected,
            "completed_count": completed,
            "content_hash": content_hash,
            "cursor": cursor,
            "started_at": started_at,
            "completed_at": completed_at,
            "received_at": received_at,
            "error_reason": error,
        }
        for field, value in (
            ("source", source),
            ("source_snapshot_id", snapshot_id),
            ("source_snapshot_date", snapshot_date),
            ("taxonomy_version", taxonomy_version),
            ("registry_version", registry_version),
            ("expected_count", expected),
            ("completed_count", completed),
            ("content_hash", content_hash),
            ("cursor", cursor),
            ("started_at", started_at),
            ("completed_at", completed_at),
            ("received_at", received_at),
            ("error_reason", error),
        ):
            object.__setattr__(self, field, value)
        object.__setattr__(
            self, "run_hash", _canonical_hash(payload, self.run_hash, "run_hash")
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_theme_capture_run",
            "source": self.source,
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_date": self.source_snapshot_date,
            "taxonomy_version": self.taxonomy_version,
            "registry_version": self.registry_version,
            "status": self.status.value,
            "expected_count": self.expected_count,
            "completed_count": self.completed_count,
            "content_hash": self.content_hash,
            "cursor": self.cursor,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "received_at": self.received_at,
            "error_reason": self.error_reason,
        }


@dataclass(frozen=True, slots=True)
class AshareThemeStateFact:
    context_kind: str
    context_key: str
    state_date: date
    source_cutoff: datetime
    received_at: datetime
    registry_version: str
    source_snapshot_hash: str
    eligible_member_count: int
    up_member_count: int
    up_breadth: float | None
    median_return_1d: float | None
    median_return_5d: float | None
    relative_market_return_1d: float | None
    amount_participation: float | None
    leader_count: int | None
    limit_up_count: int | None
    limit_down_count: int | None
    available: bool
    unavailable_reasons: tuple[str, ...]
    input_hash: str
    state_hash: str = ""

    def __post_init__(self) -> None:
        context_kind = _required_text(self.context_kind, "context_kind", max_length=32)
        if context_kind not in {
            "provider_concept",
            "industry_union_proxy",
            "industry_l3",
            "industry_l2",
            "industry_l1",
            "broad_fallback",
        }:
            raise V2ContractError("theme state context_kind is not registered")
        context_key = _required_text(self.context_key, "context_key")
        state_date = _required_date(self.state_date, "state_date")
        source_cutoff = _required_datetime(self.source_cutoff, "source_cutoff")
        received_at = _required_datetime(self.received_at, "received_at")
        if source_cutoff > received_at or state_date > source_cutoff.date():
            raise V2ContractError("theme state visibility window is invalid")
        registry_version = _required_text(
            self.registry_version, "registry_version", max_length=128
        )
        snapshot_hash = _required_text(
            self.source_snapshot_hash, "source_snapshot_hash", max_length=128
        )
        eligible = _non_negative_int(
            self.eligible_member_count, "eligible_member_count"
        )
        up_count = _non_negative_int(self.up_member_count, "up_member_count")
        if up_count > eligible:
            raise V2ContractError("up_member_count cannot exceed eligible_member_count")
        breadth = _optional_finite_number(
            self.up_breadth, "up_breadth", minimum=0.0, maximum=1.0
        )
        return_1d = _optional_finite_number(self.median_return_1d, "median_return_1d")
        return_5d = _optional_finite_number(self.median_return_5d, "median_return_5d")
        relative = _optional_finite_number(
            self.relative_market_return_1d, "relative_market_return_1d"
        )
        participation = _optional_finite_number(
            self.amount_participation,
            "amount_participation",
            minimum=0.0,
            maximum=1.0,
        )
        leader_count = (
            None
            if self.leader_count is None
            else _non_negative_int(self.leader_count, "leader_count")
        )
        limit_up_count = (
            None
            if self.limit_up_count is None
            else _non_negative_int(self.limit_up_count, "limit_up_count")
        )
        limit_down_count = (
            None
            if self.limit_down_count is None
            else _non_negative_int(self.limit_down_count, "limit_down_count")
        )
        if not isinstance(self.available, bool):
            raise V2ContractError("available must be a boolean")
        reasons = tuple(
            sorted(
                {
                    _required_text(reason, "unavailable_reason", max_length=128)
                    for reason in self.unavailable_reasons
                }
            )
        )
        core_metrics = (breadth, return_1d, return_5d, relative, participation, leader_count)
        if self.available:
            if eligible < MIN_AVAILABLE_THEME_PEERS or any(
                metric is None for metric in core_metrics
            ):
                raise V2ContractError("available theme state requires five peers and metrics")
            if reasons:
                raise V2ContractError("available theme state cannot have unavailable reasons")
        elif not reasons:
            raise V2ContractError("unavailable theme state requires exact reasons")
        input_hash = _required_text(self.input_hash, "input_hash", max_length=128)
        payload = {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_theme_state",
            "context_kind": context_kind,
            "context_key": context_key,
            "state_date": state_date,
            "source_cutoff": source_cutoff,
            "received_at": received_at,
            "registry_version": registry_version,
            "source_snapshot_hash": snapshot_hash,
            "eligible_member_count": eligible,
            "up_member_count": up_count,
            "up_breadth": breadth,
            "median_return_1d": return_1d,
            "median_return_5d": return_5d,
            "relative_market_return_1d": relative,
            "amount_participation": participation,
            "leader_count": leader_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "available": self.available,
            "unavailable_reasons": reasons,
            "input_hash": input_hash,
        }
        for field, value in (
            ("context_kind", context_kind),
            ("context_key", context_key),
            ("state_date", state_date),
            ("source_cutoff", source_cutoff),
            ("received_at", received_at),
            ("registry_version", registry_version),
            ("source_snapshot_hash", snapshot_hash),
            ("eligible_member_count", eligible),
            ("up_member_count", up_count),
            ("up_breadth", breadth),
            ("median_return_1d", return_1d),
            ("median_return_5d", return_5d),
            ("relative_market_return_1d", relative),
            ("amount_participation", participation),
            ("leader_count", leader_count),
            ("limit_up_count", limit_up_count),
            ("limit_down_count", limit_down_count),
            ("unavailable_reasons", reasons),
            ("input_hash", input_hash),
        ):
            object.__setattr__(self, field, value)
        object.__setattr__(
            self, "state_hash", _canonical_hash(payload, self.state_hash, "state_hash")
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": THEME_GRAPH_SCHEMA_VERSION,
            "fact_type": "ashare_theme_state",
            "context_kind": self.context_kind,
            "context_key": self.context_key,
            "state_date": self.state_date,
            "source_cutoff": self.source_cutoff,
            "received_at": self.received_at,
            "registry_version": self.registry_version,
            "source_snapshot_hash": self.source_snapshot_hash,
            "eligible_member_count": self.eligible_member_count,
            "up_member_count": self.up_member_count,
            "up_breadth": self.up_breadth,
            "median_return_1d": self.median_return_1d,
            "median_return_5d": self.median_return_5d,
            "relative_market_return_1d": self.relative_market_return_1d,
            "amount_participation": self.amount_participation,
            "leader_count": self.leader_count,
            "limit_up_count": self.limit_up_count,
            "limit_down_count": self.limit_down_count,
            "available": self.available,
            "unavailable_reasons": self.unavailable_reasons,
            "input_hash": self.input_hash,
        }


FactT = TypeVar(
    "FactT",
    AshareIndustryPathFact,
    AshareThemeRelationFact,
    AshareThemeCaptureRunFact,
    AshareThemeStateFact,
)


def _bounded_batch(facts: Sequence[FactT], expected_type: type[FactT]) -> tuple[FactT, ...]:
    batch = tuple(facts)
    if len(batch) > MAX_THEME_GRAPH_BATCH_SIZE:
        raise V2ContractError(
            f"a theme graph batch cannot exceed {MAX_THEME_GRAPH_BATCH_SIZE} rows"
        )
    if any(not isinstance(fact, expected_type) for fact in batch):
        raise V2ContractError("theme graph batch contains an incompatible contract type")
    return batch


async def persist_industry_path_batch(
    session: AsyncSession, facts: Sequence[AshareIndustryPathFact]
) -> int:
    _assert_graph_table("ashare_industry_path_facts")
    batch = _bounded_batch(facts, AshareIndustryPathFact)
    if not batch:
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO ashare_industry_path_facts
                (asset_code, taxonomy, taxonomy_version, mapping_kind,
                 level1_code, level1_label, level2_code, level2_label,
                 level3_code, level3_label, effective_from, effective_to,
                 snapshot_date, received_at, source, confidence,
                 source_snapshot_hash, fact_hash)
            VALUES
                (:asset_code, :taxonomy, :taxonomy_version, :mapping_kind,
                 :level1_code, :level1_label, :level2_code, :level2_label,
                 :level3_code, :level3_label, :effective_from, :effective_to,
                 :snapshot_date, :received_at, :source, :confidence,
                 :source_snapshot_hash, :fact_hash)
            ON CONFLICT DO NOTHING
            """
        ),
        [
            {
                **fact.canonical_payload(),
                "fact_hash": fact.fact_hash,
            }
            for fact in batch
        ],
    )
    return max(0, int(result.rowcount or 0))


async def persist_theme_relation_batch(
    session: AsyncSession, facts: Sequence[AshareThemeRelationFact]
) -> int:
    _assert_graph_table("ashare_fine_theme_membership_facts")
    batch = _bounded_batch(facts, AshareThemeRelationFact)
    if not batch:
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO ashare_fine_theme_membership_facts
                (asset_code, group_id, theme, normalized_theme_key,
                 hierarchy_level, effective_from, effective_to, received_at,
                 taxonomy_version, source, confidence, mapping_kind, fact_hash,
                 provider_theme_code, provider_theme_label, membership_reason,
                 exposure_weight, relation_kind, source_snapshot_date,
                 source_snapshot_hash, capture_run_hash)
            VALUES
                (:asset_code, :group_id, :theme, :normalized_theme_key,
                 'fine_theme', :effective_from, :effective_to, :received_at,
                 :taxonomy_version, :source, :confidence, 'historical_pit', :fact_hash,
                 :provider_theme_code, :provider_theme_label, :membership_reason,
                 :exposure_weight, :relation_kind, :source_snapshot_date,
                 :source_snapshot_hash, :capture_run_hash)
            ON CONFLICT DO NOTHING
            """
        ),
        [
            {
                "asset_code": fact.asset_code,
                "group_id": fact.group_id,
                "theme": fact.theme_label,
                "normalized_theme_key": fact.canonical_theme_key,
                "effective_from": fact.effective_from,
                "effective_to": fact.effective_to,
                "received_at": fact.received_at,
                "taxonomy_version": fact.taxonomy_version,
                "source": fact.source,
                "confidence": str(fact.confidence),
                "fact_hash": fact.fact_hash,
                "provider_theme_code": fact.provider_theme_code,
                "provider_theme_label": fact.provider_theme_label,
                "membership_reason": fact.membership_reason,
                "exposure_weight": fact.exposure_weight,
                "relation_kind": fact.relation_kind.value,
                "source_snapshot_date": fact.source_snapshot_date,
                "source_snapshot_hash": fact.source_snapshot_hash,
                "capture_run_hash": fact.capture_run_hash,
            }
            for fact in batch
        ],
    )
    return max(0, int(result.rowcount or 0))


async def persist_theme_capture_run_batch(
    session: AsyncSession, facts: Sequence[AshareThemeCaptureRunFact]
) -> int:
    _assert_graph_table("ashare_theme_capture_runs")
    batch = _bounded_batch(facts, AshareThemeCaptureRunFact)
    if not batch:
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO ashare_theme_capture_runs
                (run_hash, source, source_snapshot_id, source_snapshot_date,
                 taxonomy_version, registry_version, status, expected_count,
                 completed_count, content_hash, cursor, started_at, completed_at,
                 received_at, error_reason)
            VALUES
                (:run_hash, :source, :source_snapshot_id, :source_snapshot_date,
                 :taxonomy_version, :registry_version, :status, :expected_count,
                 :completed_count, :content_hash, :cursor, :started_at, :completed_at,
                 :received_at, :error_reason)
            ON CONFLICT (run_hash) DO NOTHING
            """
        ),
        [
            {
                "run_hash": fact.run_hash,
                "source": fact.source,
                "source_snapshot_id": fact.source_snapshot_id,
                "source_snapshot_date": fact.source_snapshot_date,
                "taxonomy_version": fact.taxonomy_version,
                "registry_version": fact.registry_version,
                "status": fact.status.value,
                "expected_count": fact.expected_count,
                "completed_count": fact.completed_count,
                "content_hash": fact.content_hash,
                "cursor": fact.cursor,
                "started_at": fact.started_at,
                "completed_at": fact.completed_at,
                "received_at": fact.received_at,
                "error_reason": fact.error_reason,
            }
            for fact in batch
        ],
    )
    return max(0, int(result.rowcount or 0))


async def persist_theme_state_batch(
    session: AsyncSession, facts: Sequence[AshareThemeStateFact]
) -> int:
    _assert_graph_table("ashare_theme_state_facts")
    batch = _bounded_batch(facts, AshareThemeStateFact)
    if not batch:
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO ashare_theme_state_facts
                (context_kind, context_key, state_date, source_cutoff, received_at,
                 registry_version, source_snapshot_hash, eligible_member_count,
                 up_member_count, up_breadth, median_return_1d, median_return_5d,
                 relative_market_return_1d, amount_participation, leader_count,
                 limit_up_count, limit_down_count, available,
                 unavailable_reasons_json, input_hash, state_hash)
            VALUES
                (:context_kind, :context_key, :state_date, :source_cutoff, :received_at,
                 :registry_version, :source_snapshot_hash, :eligible_member_count,
                 :up_member_count, :up_breadth, :median_return_1d, :median_return_5d,
                 :relative_market_return_1d, :amount_participation, :leader_count,
                 :limit_up_count, :limit_down_count, :available,
                 :unavailable_reasons_json, :input_hash, :state_hash)
            ON CONFLICT DO NOTHING
            """
        ),
        [
            {
                **{
                    key: value
                    for key, value in fact.canonical_payload().items()
                    if key not in {"schema_version", "fact_type", "unavailable_reasons"}
                },
                "unavailable_reasons_json": json.dumps(
                    fact.unavailable_reasons,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "state_hash": fact.state_hash,
            }
            for fact in batch
        ],
    )
    return max(0, int(result.rowcount or 0))


__all__ = [
    "AshareIndustryPathFact",
    "AshareThemeCaptureRunFact",
    "AshareThemeRelationFact",
    "AshareThemeStateFact",
    "CaptureStatus",
    "MAX_THEME_GRAPH_BATCH_SIZE",
    "MIN_AVAILABLE_THEME_PEERS",
    "THEME_DEFINITIONS",
    "THEME_GRAPH_SCHEMA_VERSION",
    "THEME_REGISTRY_HASH",
    "THEME_REGISTRY_VERSION",
    "ThemeDefinition",
    "ThemeRelationKind",
    "persist_industry_path_batch",
    "persist_theme_capture_run_batch",
    "persist_theme_relation_batch",
    "persist_theme_state_batch",
    "registered_theme_definitions",
    "resolve_theme_definitions",
    "theme_definitions_for_key",
]
