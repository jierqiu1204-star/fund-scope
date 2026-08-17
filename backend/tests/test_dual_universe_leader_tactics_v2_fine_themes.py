from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_lab import dual_universe_leader_tactics_v2_concept_snapshot
from app.services.strategy_lab import dual_universe_leader_tactics_v2_fine_themes as fine_themes
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


def test_registered_theme_sources_have_stable_keys_and_labels() -> None:
    assert fine_themes.REGISTERED_FINE_THEME_KEYS == (
        "innovation_drug",
        "rare_earth",
        "passive_components",
    )
    assert fine_themes.REGISTERED_FINE_THEME_LABELS == (
        "创新药",
        "稀土",
        "稀土永磁",
        "被动元件概念",
        "MLCC",
    )
    assert (
        tuple(source.provider_label for source in fine_themes.registered_fine_theme_sources())
        == fine_themes.REGISTERED_FINE_THEME_LABELS
    )


def test_rare_earth_aliases_are_normalized_without_inferring_membership() -> None:
    assert normalize_fine_theme_label("创新药概念") == ("innovation_drug", "创新药")
    assert normalize_fine_theme_label("稀土") == ("rare_earth", "稀土/稀土永磁")
    assert normalize_fine_theme_label(" 稀土永磁 ") == (
        "rare_earth",
        "稀土/稀土永磁",
    )
    assert normalize_fine_theme_label("被动元件概念") == (
        "passive_components",
        "被动元件/MLCC",
    )
    assert normalize_fine_theme_label(" MLCC ") == (
        "passive_components",
        "被动元件/MLCC",
    )


def test_concept_snapshot_fetches_one_registered_theme(monkeypatch, capsys) -> None:
    @dataclass
    class _Column:
        values: list[str]

        def tolist(self) -> list[str]:
            return self.values

    class _Frame:
        columns = ("代码",)

        def __getitem__(self, _key: str) -> _Column:
            return _Column(["600111", "600111"])

    calls: list[str] = []

    def fetch(*, symbol: str):
        calls.append(symbol)
        return _Frame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_board_concept_cons_em=fetch),
    )
    monkeypatch.setattr(sys, "argv", ["concept-snapshot", "稀土"])

    assert dual_universe_leader_tactics_v2_concept_snapshot.main() == 0
    captured = capsys.readouterr()
    assert calls == ["稀土"]
    assert captured.out.count('"asset_code":"600111"') == 1


def test_innovation_drug_snapshot_uses_registered_provider_code(
    monkeypatch, capsys
) -> None:
    @dataclass
    class _Column:
        values: list[str]

        def tolist(self) -> list[str]:
            return self.values

    class _Frame:
        columns = ("代码",)

        def __getitem__(self, _key: str) -> _Column:
            return _Column(["002437"])

    calls: list[str] = []

    def fetch(*, symbol: str):
        calls.append(symbol)
        return _Frame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_board_concept_cons_em=fetch),
    )
    monkeypatch.setattr(sys, "argv", ["concept-snapshot", "创新药"])

    assert dual_universe_leader_tactics_v2_concept_snapshot.main() == 0
    captured = capsys.readouterr()
    assert calls == ["BK1106"]
    assert '"theme":"创新药"' in captured.out
    assert '"asset_code":"002437"' in captured.out


def test_concept_snapshot_rejects_multi_theme_invocation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["concept-snapshot", "稀土", "稀土永磁"])
    assert dual_universe_leader_tactics_v2_concept_snapshot.main() == 2
    assert "exactly_one" in capsys.readouterr().err


class _CompletedProcess:
    def __init__(self, *, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


class _HangingProcess(_CompletedProcess):
    def __init__(self) -> None:
        super().__init__(returncode=None)

    async def communicate(self) -> tuple[bytes, bytes]:
        raise TimeoutError

    async def wait(self) -> int:
        return -9


@pytest.mark.asyncio
async def test_single_theme_failure_keeps_a_bounded_summary(monkeypatch) -> None:
    async def spawn(*_args, **_kwargs):
        return _CompletedProcess(returncode=1, stderr=b"HTTP 504 provider timeout")

    monkeypatch.setattr(fine_themes.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="稀土.*HTTP 504"):
        await fine_themes.load_registered_fine_theme_facts_for_source(
            "稀土",
            received_at=datetime(2026, 8, 14, 9, 0),
        )


@pytest.mark.asyncio
async def test_single_theme_timeout_is_failed_closed(monkeypatch) -> None:
    process = _HangingProcess()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(fine_themes.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="fine_theme_provider_timeout:稀土"):
        await fine_themes.load_registered_fine_theme_facts_for_source(
            "稀土",
            received_at=datetime(2026, 8, 14, 9, 0),
        )
    assert process.killed is True


def _fact() -> AshareFineThemeMembershipFact:
    return AshareFineThemeMembershipFact(
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


@pytest.mark.asyncio
async def test_compatibility_loader_serially_aggregates_and_deduplicates(monkeypatch) -> None:
    calls: list[str] = []

    async def load_one(provider_label: str, *, received_at: datetime):
        calls.append(provider_label)
        return (_fact(),)

    monkeypatch.setattr(fine_themes, "load_registered_fine_theme_facts_for_source", load_one)
    facts = await fine_themes.load_registered_fine_theme_facts(
        received_at=datetime(2026, 8, 14, 9, 0),
    )
    assert calls == ["创新药", "稀土", "稀土永磁", "被动元件概念", "MLCC"]
    assert len(facts) == 1
    assert facts[0].asset_code == "600111"


def test_factual_fine_theme_round_trips_with_pit_provenance() -> None:
    fact = _fact()
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
