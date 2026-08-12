"""Append-only point-in-time fine-theme facts for leader-tactics research."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    ASHARE_FINE_THEME_FACT_HASH_CONTRACT,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_table,
)

_FINE_THEME_ALIASES = {
    "稀土/稀土永磁": ("rare_earth", "稀土/稀土永磁"),
    "稀土": ("rare_earth", "稀土/稀土永磁"),
    "稀土永磁": ("rare_earth", "稀土/稀土永磁"),
    "稀土磁材": ("rare_earth", "稀土/稀土永磁"),
}


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


async def load_registered_fine_theme_facts(
    *, received_at: datetime
) -> tuple[AshareFineThemeMembershipFact, ...]:
    """Fetch explicit current constituents; facts are eligible only from receipt onward."""

    themes = ("稀土", "稀土永磁")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.strategy_lab.dual_universe_leader_tactics_v2_concept_snapshot",
        *themes,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        summary = stderr.decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"fine_theme_provider_failed:{summary}")
    if len(stdout) > 2_000_000:
        raise RuntimeError("fine_theme_provider_response_too_large")
    facts: dict[tuple[str, str], AshareFineThemeMembershipFact] = {}
    effective_from = received_at.date()
    for line in stdout.splitlines():
        row = json.loads(line)
        normalized_key, normalized_label = normalize_fine_theme_label(row["theme"])
        fact = AshareFineThemeMembershipFact(
            asset_code=str(row["asset_code"]),
            group_id=f"fine_theme:{normalized_key}",
            theme=normalized_label,
            normalized_theme_key=normalized_key,
            effective_from=effective_from,
            effective_to=None,
            received_at=received_at,
            taxonomy_version="eastmoney.concept.current_v1",
            source="akshare.stock_board_concept_cons_em.current",
        ).finalized()
        facts[(fact.asset_code, fact.normalized_theme_key)] = fact
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
    "load_registered_fine_theme_facts",
    "normalize_fine_theme_label",
    "persist_fine_theme_membership_batch",
]
