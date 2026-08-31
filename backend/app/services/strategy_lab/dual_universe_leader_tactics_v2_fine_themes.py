"""Append-only point-in-time fine-theme facts for leader-tactics research."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    ASHARE_FINE_THEME_FACT_HASH_CONTRACT,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_table,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_concept_snapshot import (
    MAX_OUTPUT_BYTES,
    REGISTERED_FINE_THEME_KEYS,
    REGISTERED_FINE_THEME_LABELS,
    REGISTERED_FINE_THEME_SOURCES,
    RegisteredFineThemeSource,
    registered_fine_theme_keys,
    registered_fine_theme_sources,
    resolve_registered_fine_theme_source,
)

_FINE_THEME_ALIASES = {
    "创新药": ("innovation_drug", "创新药"),
    "创新药概念": ("innovation_drug", "创新药"),
    "稀土/稀土永磁": ("rare_earth", "稀土/稀土永磁"),
    "稀土": ("rare_earth", "稀土/稀土永磁"),
    "稀土永磁": ("rare_earth", "稀土/稀土永磁"),
    "稀土磁材": ("rare_earth", "稀土/稀土永磁"),
    "被动元件/MLCC": ("passive_components", "被动元件/MLCC"),
    "被动元件": ("passive_components", "被动元件/MLCC"),
    "被动元件概念": ("passive_components", "被动元件/MLCC"),
    "MLCC": ("passive_components", "被动元件/MLCC"),
    "液冷": ("liquid_cooling", "液冷"),
    "液冷概念": ("liquid_cooling", "液冷"),
    "液冷服务器": ("liquid_cooling", "液冷"),
    "数据中心液冷": ("liquid_cooling", "液冷"),
}

FINE_THEME_SUBPROCESS_TIMEOUT_SECONDS = 15.0
FINE_THEME_MAX_STDERR_BYTES = 32_000
MAX_ERROR_SUMMARY_LENGTH = 300


def normalize_fine_theme_label(label: str) -> tuple[str, str]:
    """Normalize an observed label without inferring which assets belong to it."""

    compact = "".join(str(label).split())
    try:
        return _FINE_THEME_ALIASES[compact]
    except KeyError as exc:
        raise ValueError("fine_theme_label_not_registered") from exc


@dataclass(frozen=True, slots=True)
class AshareFineThemeMembershipFact:
    asset_code: str
    group_id: str
    theme: str
    normalized_theme_key: str
    effective_from: date
    effective_to: date | None
    received_at: datetime
    taxonomy_version: str
    source: str
    confidence: str = "observed_current"
    mapping_kind: str = "historical_pit"
    hierarchy_level: str = "fine_theme"
    fact_hash: str = ""

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": ASHARE_FINE_THEME_FACT_HASH_CONTRACT,
            "fact_type": "ashare_fine_theme_membership",
            "asset_code": self.asset_code,
            "group_id": self.group_id,
            "theme": self.theme,
            "normalized_theme_key": self.normalized_theme_key,
            "hierarchy_level": self.hierarchy_level,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "received_at": self.received_at,
            "taxonomy_version": self.taxonomy_version,
            "source": self.source,
            "confidence": self.confidence,
            "mapping_kind": self.mapping_kind,
        }

    def finalized(self) -> AshareFineThemeMembershipFact:
        if not self.asset_code.strip() or not self.group_id.strip() or not self.source.strip():
            raise ValueError("fine_theme_identity_incomplete")
        if self.mapping_kind != "historical_pit":
            raise ValueError("fine_theme_must_be_point_in_time")
        if self.received_at.date() < self.effective_from:
            raise ValueError("fine_theme_received_before_effective_date")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("fine_theme_effective_window_invalid")
        normalized_key, normalized_label = normalize_fine_theme_label(self.theme)
        if self.normalized_theme_key != normalized_key or self.theme != normalized_label:
            raise ValueError("fine_theme_label_not_canonical")
        expected_group = f"fine_theme:{normalized_key}"
        if self.group_id != expected_group:
            raise ValueError("fine_theme_group_not_canonical")
        digest = stable_contract_hash(self.identity_payload())
        if self.fact_hash and self.fact_hash != digest:
            raise ValueError("fine_theme_fact_hash_mismatch")
        return replace(self, fact_hash=digest)


def _error_summary(stderr: bytes, *, fallback: str) -> str:
    decoded = stderr[:FINE_THEME_MAX_STDERR_BYTES].decode("utf-8", errors="replace")
    summary = " ".join(decoded.split())[-MAX_ERROR_SUMMARY_LENGTH:]
    return summary or fallback


async def _stop_subprocess(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except (TimeoutError, ProcessLookupError):
        pass


def _fact_from_snapshot_row(
    row: dict[str, Any], *, received_at: datetime
) -> AshareFineThemeMembershipFact:
    normalized_key, normalized_label = normalize_fine_theme_label(str(row["theme"]))
    return AshareFineThemeMembershipFact(
        asset_code=str(row["asset_code"]),
        group_id=f"fine_theme:{normalized_key}",
        theme=normalized_label,
        normalized_theme_key=normalized_key,
        effective_from=received_at.date(),
        effective_to=None,
        received_at=received_at,
        taxonomy_version="eastmoney.concept.current_v1",
        source="akshare.stock_board_concept_cons_em.current",
    ).finalized()


async def load_registered_fine_theme_facts_for_source(
    provider_label: str, *, received_at: datetime
) -> tuple[AshareFineThemeMembershipFact, ...]:
    """Fetch exactly one registered provider source in one bounded subprocess."""

    source = resolve_registered_fine_theme_source(provider_label)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.strategy_lab.dual_universe_leader_tactics_v2_concept_snapshot",
        source.provider_label,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=FINE_THEME_SUBPROCESS_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        await _stop_subprocess(process)
        raise RuntimeError(f"fine_theme_provider_timeout:{source.provider_label}") from exc
    except asyncio.CancelledError:
        await _stop_subprocess(process)
        raise
    if process.returncode != 0:
        summary = _error_summary(
            stderr,
            fallback=f"returncode={int(process.returncode or 0)}",
        )
        raise RuntimeError(f"fine_theme_provider_failed:{source.provider_label}:{summary}")
    if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > FINE_THEME_MAX_STDERR_BYTES:
        raise RuntimeError("fine_theme_provider_response_too_large")

    facts: dict[tuple[str, str], AshareFineThemeMembershipFact] = {}
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("snapshot_row_not_object")
            fact = _fact_from_snapshot_row(row, received_at=received_at)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"fine_theme_provider_invalid_output:{type(exc).__name__}") from exc
        facts[(fact.asset_code, fact.normalized_theme_key)] = fact
    return tuple(facts[key] for key in sorted(facts))


async def load_fine_theme_facts_for_registered_theme(
    provider_label: str, *, received_at: datetime
) -> tuple[AshareFineThemeMembershipFact, ...]:
    """Descriptive alias for resumable per-source callers."""

    return await load_registered_fine_theme_facts_for_source(
        provider_label, received_at=received_at
    )


async def load_registered_fine_theme_facts(
    *, received_at: datetime, normalized_theme_key: str | None = None
) -> tuple[AshareFineThemeMembershipFact, ...]:
    """Compatibility loader that serially aggregates registered sources.

    Each provider label runs in its own bounded subprocess. A failed alias does
    not discard successful aliases; if all selected sources fail, loading fails.
    """

    if normalized_theme_key is None:
        sources = REGISTERED_FINE_THEME_SOURCES
    else:
        if normalized_theme_key not in REGISTERED_FINE_THEME_KEYS:
            raise ValueError("fine_theme_key_not_registered")
        sources = tuple(
            source
            for source in REGISTERED_FINE_THEME_SOURCES
            if source.normalized_theme_key == normalized_theme_key
        )

    facts: dict[tuple[str, str], AshareFineThemeMembershipFact] = {}
    failures: list[str] = []
    for source in sources:
        try:
            source_facts = await load_registered_fine_theme_facts_for_source(
                source.provider_label, received_at=received_at
            )
        except (RuntimeError, ValueError) as exc:
            failures.append(f"{source.provider_label}:{str(exc)[:120]}")
            continue
        for fact in source_facts:
            facts[(fact.asset_code, fact.normalized_theme_key)] = fact

    if not facts and failures:
        summary = _error_summary(
            " ".join(failures).encode(),
            fallback="all_sources_failed",
        )
        raise RuntimeError(f"fine_theme_provider_failed:{summary}")
    return tuple(facts[key] for key in sorted(facts))


async def persist_fine_theme_membership_batch(
    session: AsyncSession,
    facts: tuple[AshareFineThemeMembershipFact, ...],
) -> int:
    assert_v2_research_table("ashare_fine_theme_membership_facts")
    rows = [fact.finalized() for fact in facts]
    inserted = 0
    for fact in rows:
        result = await session.execute(
            text(
                """
                INSERT INTO ashare_fine_theme_membership_facts
                    (asset_code, group_id, theme, normalized_theme_key,
                     hierarchy_level, effective_from, effective_to, received_at,
                     taxonomy_version, source, confidence, mapping_kind, fact_hash)
                VALUES
                    (:asset_code, :group_id, :theme, :normalized_theme_key,
                     :hierarchy_level, :effective_from, :effective_to, :received_at,
                     :taxonomy_version, :source, :confidence, :mapping_kind, :fact_hash)
                ON CONFLICT (fact_hash) DO NOTHING
                """
            ),
            {
                "asset_code": fact.asset_code,
                "group_id": fact.group_id,
                "theme": fact.theme,
                "normalized_theme_key": fact.normalized_theme_key,
                "hierarchy_level": fact.hierarchy_level,
                "effective_from": fact.effective_from,
                "effective_to": fact.effective_to,
                "received_at": fact.received_at,
                "taxonomy_version": fact.taxonomy_version,
                "source": fact.source,
                "confidence": fact.confidence,
                "mapping_kind": fact.mapping_kind,
                "fact_hash": fact.fact_hash,
            },
        )
        inserted += max(0, result.rowcount or 0)
    return inserted


__all__ = [
    "AshareFineThemeMembershipFact",
    "FINE_THEME_MAX_STDERR_BYTES",
    "FINE_THEME_SUBPROCESS_TIMEOUT_SECONDS",
    "REGISTERED_FINE_THEME_KEYS",
    "REGISTERED_FINE_THEME_LABELS",
    "REGISTERED_FINE_THEME_SOURCES",
    "RegisteredFineThemeSource",
    "load_fine_theme_facts_for_registered_theme",
    "load_registered_fine_theme_facts",
    "load_registered_fine_theme_facts_for_source",
    "normalize_fine_theme_label",
    "persist_fine_theme_membership_batch",
    "registered_fine_theme_keys",
    "registered_fine_theme_sources",
    "resolve_registered_fine_theme_source",
]
