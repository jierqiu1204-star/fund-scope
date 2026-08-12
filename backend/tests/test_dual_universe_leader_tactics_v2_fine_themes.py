from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_lab import dual_universe_leader_tactics_v2_concept_snapshot
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    ASHARE_FINE_THEME_FACT_HASH_CONTRACT,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
    fine_theme_membership_to_v2,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_fine_themes import (
    AshareFineThemeMembershipFact,
    normalize_fine_theme_label,
    persist_fine_theme_membership_batch,
)


def test_rare_earth_aliases_are_normalized_without_inferring_membership() -> None:
    assert normalize_fine_theme_label("稀土") == ("rare_earth", "稀土/稀土永磁")
    assert normalize_fine_theme_label(" 稀土永磁 ") == (
        "rare_earth",
        "稀土/稀土永磁",
    )


def test_concept_snapshot_keeps_a_valid_alias_when_another_alias_fails(monkeypatch, capsys) -> None:
    @dataclass
    class _Column:
        values: list[str]

        def tolist(self) -> list[str]:
            return self.values

    class _Frame:
        columns = ("代码",)

        def __getitem__(self, _key: str) -> _Column:
            return _Column(["600111"])

    def fetch(*, symbol: str):
        if symbol == "稀土":
            raise RuntimeError("alias unavailable")
        return _Frame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_board_concept_cons_em=fetch),
    )
    monkeypatch.setattr(sys, "argv", ["concept-snapshot", "稀土", "稀土永磁"])

    assert dual_universe_leader_tactics_v2_concept_snapshot.main() == 0
    captured = capsys.readouterr()
    assert '"asset_code": "600111"' in captured.out
    assert "稀土:RuntimeError" in captured.err


def test_factual_fine_theme_round_trips_with_pit_provenance() -> None:
    fact = AshareFineThemeMembershipFact(
        asset_code="600111",
        group_id="fine_theme:rare_earth",
        theme="稀土/稀土永磁",
        normalized_theme_key="rare_earth",
        effective_from=date(2026, 8, 12),
        effective_to=None,
        received_at=datetime(2026, 8, 12, 15, 10),
        taxonomy_version="eastmoney.concept.current_v1",
        source="eastmoney.concept.constituents",
    ).finalized()
    membership = fine_theme_membership_to_v2(
        {
            **fact.identity_payload(),
            "fact_hash": fact.fact_hash,
        }
    )

    assert membership.fact_hash_contract == ASHARE_FINE_THEME_FACT_HASH_CONTRACT
    assert membership.hierarchy_level == "fine_theme"
    assert membership.resolution_mode == "fine_theme_pit"
    assert membership.normalized_theme_key == "rare_earth"
    assert membership.fact_identity_payload(asset_code="600111") == fact.identity_payload()


@pytest.mark.asyncio
async def test_fine_theme_persistence_is_research_only_and_idempotent(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fine-theme.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_fine_theme_membership_facts (
                    id INTEGER PRIMARY KEY, asset_code TEXT NOT NULL,
                    group_id TEXT NOT NULL, theme TEXT NOT NULL,
                    normalized_theme_key TEXT NOT NULL,
                    hierarchy_level TEXT NOT NULL, effective_from DATE NOT NULL,
                    effective_to DATE, received_at DATETIME NOT NULL,
                    taxonomy_version TEXT NOT NULL, source TEXT NOT NULL,
                    confidence TEXT NOT NULL, mapping_kind TEXT NOT NULL,
                    fact_hash TEXT NOT NULL UNIQUE
                )
                """
            )
        )
    fact = AshareFineThemeMembershipFact(
        asset_code="600111",
        group_id="fine_theme:rare_earth",
        theme="稀土/稀土永磁",
        normalized_theme_key="rare_earth",
        effective_from=date(2026, 8, 12),
        effective_to=None,
        received_at=datetime(2026, 8, 12, 15, 10),
        taxonomy_version="eastmoney.concept.current_v1",
        source="akshare.stock_board_concept_cons_em.current",
    ).finalized()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        inserted = await persist_fine_theme_membership_batch(session, (fact,))
        duplicate = await persist_fine_theme_membership_batch(session, (fact,))
        count = await session.scalar(
            text("SELECT COUNT(*) FROM ashare_fine_theme_membership_facts")
        )
    await engine.dispose()

    assert inserted == 1
    assert duplicate == 0
    assert count == 1
