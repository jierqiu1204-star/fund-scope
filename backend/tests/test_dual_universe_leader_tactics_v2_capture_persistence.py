from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    PRICE_BASIS,
    V2_FORMULA_REGISTRY_HASH,
    V2_SOURCE_REGISTRY,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
    run_bounded_batch,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_ingestion import (
    AshareAdjustedPriceFact,
    AshareThemeMembershipFact,
    AshareUniverseSnapshotFact,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_lifecycle_storage import (
    CHECKPOINT_SCHEMA_VERSION,
    load_v2_checkpoint,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_tickflow_provider import (
    TICKFLOW_TAXONOMY_VERSION,
    TICKFLOW_THEME_SOURCE,
    TickflowAshareMember,
    TickflowAshareV2Provider,
)
from app.services.workflows import dual_universe_leader_tactics_v2_jobs as jobs
from app.services.workflows.dual_universe_leader_tactics_v2 import (
    V2CapturedAshareFacts,
    V2CheckpointContract,
    run_v2_capture_batch,
    run_v2_fact_capture_batch,
)

SOURCE_TIME = datetime(2026, 8, 4, 15, 0)
MANIFEST_HASH = "m" * 64


def _contract() -> V2CheckpointContract:
    return V2CheckpointContract(
        manifest_hash=MANIFEST_HASH,
        source_registry_hash=V2_SOURCE_REGISTRY.registry_hash,
        formula_registry_hash=V2_FORMULA_REGISTRY_HASH,
        adjustment_version=PRICE_BASIS,
        taxonomy_version="pit_theme_taxonomy_v2",
        cost_model=(("fee_bps_per_side", 5.0), ("slippage_bps_per_side", 5.0)),
        state_policy="preparing_confirmed_invalidated_v2",
    )


def _checkpoint(*, batch_size: int = 5) -> V2CollectorCheckpoint:
    return V2CollectorCheckpoint(
        cursor=None,
        batch_size=batch_size,
        completed_codes=(),
        status="paused",
    )


def _universe(code: str) -> AshareUniverseSnapshotFact:
    return AshareUniverseSnapshotFact(
        snapshot_date=date(2026, 8, 4),
        asset_code=code,
        asset_name=f"测试股票{code}",
        listing_state="listed",
        board="main",
        effective_at=datetime(2026, 8, 4, 9),
        received_at=SOURCE_TIME,
        provider="akshare",
        source_cutoff=SOURCE_TIME,
    )


def _theme(code: str) -> AshareThemeMembershipFact:
    return AshareThemeMembershipFact(
        asset_code=code,
        group_id=f"group-{code}",
        theme="AI应用",
        sector="软件",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        received_at=SOURCE_TIME,
        taxonomy_version="taxonomy-v1",
        source="akshare",
        confidence="high",
        supersedes_fact_hash=None,
        mapping_kind="historical_pit",
    )


def _price(code: str) -> AshareAdjustedPriceFact:
    return AshareAdjustedPriceFact(
        asset_code=code,
        trade_date=date(2026, 8, 4),
        adjusted_open=10.0,
        adjusted_high=11.0,
        adjusted_low=9.0,
        adjusted_close=10.5,
        volume=0.0,
        amount=0.0,
        turnover=0.0,
        price_basis=PRICE_BASIS,
        provider="eastmoney",
        adjustment_version="total-return-v1",
        revision_id=f"revision-{code}",
        received_at=SOURCE_TIME,
        historical_research_only=False,
        decision_eligible=True,
    )


def _bundle(code: str) -> V2CapturedAshareFacts:
    return V2CapturedAshareFacts(
        asset_code=code,
        universe_fact=_universe(code),
        theme_facts=(_theme(code),),
        adjusted_price_facts=(_price(code),),
    )


async def _create_tables(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_research_universe_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date DATE NOT NULL,
                    asset_code VARCHAR(32) NOT NULL,
                    asset_name VARCHAR(256) NOT NULL,
                    listing_state VARCHAR(32) NOT NULL,
                    board VARCHAR(32),
                    effective_at DATETIME NOT NULL,
                    received_at DATETIME NOT NULL,
                    provider VARCHAR(64) NOT NULL,
                    source_cutoff DATETIME NOT NULL,
                    exclusion_reason VARCHAR(256),
                    fact_hash VARCHAR(128) NOT NULL UNIQUE
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_theme_membership_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code VARCHAR(32) NOT NULL,
                    group_id VARCHAR(256) NOT NULL,
                    theme VARCHAR(256),
                    sector VARCHAR(256),
                    effective_from DATE NOT NULL,
                    effective_to DATE,
                    received_at DATETIME NOT NULL,
                    taxonomy_version VARCHAR(128) NOT NULL,
                    source VARCHAR(64) NOT NULL,
                    confidence VARCHAR(32) NOT NULL,
                    supersedes_fact_hash VARCHAR(128),
                    mapping_kind VARCHAR(32) NOT NULL,
                    tracked_index VARCHAR(64),
                    clone_group VARCHAR(128),
                    issuer VARCHAR(128),
                    fact_hash VARCHAR(128) NOT NULL UNIQUE
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ashare_adjusted_price_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code VARCHAR(32) NOT NULL,
                    trade_date DATE NOT NULL,
                    adjusted_open FLOAT NOT NULL,
                    adjusted_high FLOAT NOT NULL,
                    adjusted_low FLOAT NOT NULL,
                    adjusted_close FLOAT NOT NULL,
                    volume FLOAT NOT NULL,
                    amount FLOAT NOT NULL,
                    turnover FLOAT NOT NULL,
                    price_basis VARCHAR(64) NOT NULL,
                    provider VARCHAR(64) NOT NULL,
                    adjustment_version VARCHAR(128) NOT NULL,
                    revision_id VARCHAR(128) NOT NULL,
                    received_at DATETIME,
                    historical_research_only BOOLEAN NOT NULL,
                    decision_eligible BOOLEAN NOT NULL,
                    fact_hash VARCHAR(128) NOT NULL UNIQUE,
                    UNIQUE (asset_code, trade_date, revision_id)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    manifest_hash TEXT NOT NULL UNIQUE,
                    cursor TEXT,
                    batch_size INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL,
                    completed_hashes_json TEXT NOT NULL,
                    failed_codes_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at DATETIME,
                    error_summary TEXT,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )


async def _factory(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    await _create_tables(engine)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_fact_capture_writes_three_tables_and_real_hashes(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "success.db")
    bundles = {code: _bundle(code) for code in ("000001", "000002")}

    async with session_factory() as session:
        result = await run_v2_fact_capture_batch(
            session,
            manifest_hash=MANIFEST_HASH,
            codes=tuple(bundles),
            checkpoint=_checkpoint(),
            fetch_one=lambda code: _async_bundle(bundles[code]),
            lease_owner="worker-success",
        )
        assert result.checkpoint.completed_codes == ("000001", "000002")
        assert dict(result.checkpoint.completed_hashes) == {
            code: bundles[code].content_hash for code in bundles
        }
        row = (
            (
                await session.execute(
                    text(
                        """
                    SELECT completed_count, completed_hashes_json
                    FROM leader_tactics_v2_checkpoints
                    WHERE manifest_hash = :manifest_hash
                    """
                    ),
                    {"manifest_hash": MANIFEST_HASH},
                )
            )
            .mappings()
            .one()
        )
        payload = json.loads(row["completed_hashes_json"])
        assert row["completed_count"] == 2
        assert payload["schema_version"] == CHECKPOINT_SCHEMA_VERSION
        assert payload["completed_codes"] == ["000001", "000002"]
        assert payload["content_hashes"] == [[code, bundles[code].content_hash] for code in bundles]
        counts = (
            await session.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM ashare_research_universe_snapshots),
                        (SELECT COUNT(*) FROM ashare_theme_membership_facts),
                        (SELECT COUNT(*) FROM ashare_adjusted_price_facts)
                    """
                )
            )
        ).one()
        assert tuple(counts) == (2, 2, 2)
    await engine.dispose()


@pytest.mark.asyncio
async def test_industry_bootstrap_persists_bounded_supplements_and_reaches_gate(
    tmp_path,
    monkeypatch,
) -> None:
    engine, session_factory = await _factory(tmp_path, "industry-bootstrap.db")
    observed_at = datetime(2026, 8, 7, 12)
    members: list[TickflowAshareMember] = []
    for index in range(10):
        code = f"{index + 1:06d}"
        classified = index < 8
        members.append(
            TickflowAshareMember(
                symbol=f"{code}.SZ",
                code=code,
                name=f"测试{code}",
                board="SZ",
                current_industry="软件" if classified else None,
                float_shares=1_000_000,
                industry_group_id="tickflow_sw1:软件" if classified else None,
                industry_source=TICKFLOW_THEME_SOURCE if classified else None,
                industry_taxonomy_version=(
                    TICKFLOW_TAXONOMY_VERSION if classified else None
                ),
                industry_effective_from=date(2026, 8, 7) if classified else None,
                industry_received_at=observed_at if classified else None,
                industry_confidence="observed_current" if classified else None,
            )
        )

    provider = TickflowAshareV2Provider(industry_loader=lambda: None)  # type: ignore[arg-type]
    provider._members = tuple(members)
    provider._members_by_code = {member.code: member for member in members}

    async def industries(symbols: tuple[str, ...]) -> dict[str, str]:
        assert symbols == ("000009.SZ", "000010.SZ")
        return {symbol: "计算机" for symbol in symbols}

    monkeypatch.setattr(jobs, "load_baostock_industries", industries)
    async with session_factory() as session:
        completed, evidence = await jobs._bootstrap_baostock_industries(
            session,
            provider=provider,
            baseline_members=tuple(members),
            signal_date=date(2026, 8, 7),
            code_version="test-code-version",
            budget_seconds=40.0,
        )
        persisted_count = await session.scalar(
            text("SELECT COUNT(*) FROM ashare_theme_membership_facts")
        )

    assert evidence["coverage"] == 1.0
    assert evidence["queried_count"] == 2
    assert evidence["classified_count"] == 2
    assert persisted_count == 2
    assert all(member.current_industry is not None for member in completed)
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_capture_receipt_does_not_duplicate_an_existing_price_revision(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "revision-dedup.db")
    first = _bundle("000001")
    later_price = replace(
        _price("000001"),
        received_at=datetime(2026, 8, 5, 15),
        fact_hash="",
    )
    later = V2CapturedAshareFacts(
        asset_code="000001",
        universe_fact=replace(
            _universe("000001"),
            snapshot_date=date(2026, 8, 5),
            effective_at=datetime(2026, 8, 5, 15),
            received_at=datetime(2026, 8, 5, 15),
            source_cutoff=datetime(2026, 8, 5, 15),
            fact_hash="",
        ),
        theme_facts=(),
        adjusted_price_facts=(later_price,),
    )

    async with session_factory() as session:
        for manifest_hash, bundle in (("m" * 64, first), ("n" * 64, later)):
            result = await run_v2_fact_capture_batch(
                session,
                manifest_hash=manifest_hash,
                codes=("000001",),
                checkpoint=_checkpoint(),
                fetch_one=lambda _code, value=bundle: _async_bundle(value),
                lease_owner="worker-revision-dedup",
            )
            assert result.checkpoint.status == "complete"
        count = (
            await session.execute(text("SELECT COUNT(*) FROM ashare_adjusted_price_facts"))
        ).scalar_one()
        assert count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_late_blocking_provider_cannot_persist_success_facts(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "late-provider.db")

    async def late_fetch(_: str) -> V2CapturedAshareFacts:
        # A same-thread provider cannot be physically preempted. The workflow
        # must still keep it out of the committed fact/checkpoint evidence.
        time.sleep(0.15)  # noqa: ASYNC251 - intentional provider-boundary regression
        return _bundle("000001")

    async with session_factory() as session:
        result = await run_v2_fact_capture_batch(
            session,
            manifest_hash=MANIFEST_HASH,
            codes=("000001",),
            checkpoint=_checkpoint(),
            fetch_one=late_fetch,
            lease_owner="worker-late-provider",
            budget_seconds=0.03,
        )
        assert result.completed == ()
        assert result.checkpoint.completed_codes == ()
        assert result.failed and result.failed[0][0] == "000001"
        for table in (
            "ashare_research_universe_snapshots",
            "ashare_theme_membership_facts",
            "ashare_adjusted_price_facts",
        ):
            count = (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
            assert count == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_global_run_lease_blocks_different_manifest_sessions(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "global-lease.db")
    started = asyncio.Event()
    release = asyncio.Event()

    async def first_fetch(_: str) -> None:
        started.set()
        await release.wait()

    async with session_factory() as first_session:
        first_task = asyncio.create_task(
            run_v2_capture_batch(
                first_session,
                manifest_hash="a" * 64,
                codes=("000001",),
                checkpoint=_checkpoint(),
                fetch_one=first_fetch,
                lease_owner="worker-shared-label",
            )
        )
        await started.wait()

        async with session_factory() as second_session:
            second = await run_v2_capture_batch(
                second_session,
                manifest_hash="b" * 64,
                codes=("000001",),
                checkpoint=_checkpoint(),
                fetch_one=lambda _: _async_none(),
                lease_owner="worker-shared-label",
            )
        assert second.stopped_reason == "database_lease_busy"

        release.set()
        first = await first_task
        assert first.checkpoint.completed_codes == ("000001",)

    await engine.dispose()


async def _async_bundle(bundle: V2CapturedAshareFacts) -> V2CapturedAshareFacts:
    return bundle


@pytest.mark.asyncio
async def test_checkpoint_load_resumes_old_none_callback_without_duplicate_fetch(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "resume.db")
    contract = _contract()
    calls: list[str] = []

    async with session_factory() as session:
        first = await run_v2_capture_batch(
            session,
            manifest_hash=MANIFEST_HASH,
            codes=("000001", "000002"),
            checkpoint=_checkpoint(),
            fetch_one=lambda _code: _async_none(),
            lease_owner="worker-first",
            expected_contract=contract,
            checkpoint_contract=contract,
        )
        assert first.checkpoint.completed_codes == ("000001", "000002")

    async with session_factory() as session:
        loaded = await load_v2_checkpoint(session, manifest_hash=MANIFEST_HASH)
        assert loaded is not None
        assert loaded.completed_codes == ("000001", "000002")
        assert loaded.completed_hashes == ()

        async def fetch(code: str) -> None:
            calls.append(code)

        resumed = await run_v2_capture_batch(
            session,
            manifest_hash=MANIFEST_HASH,
            codes=("000001", "000002", "000003"),
            checkpoint=loaded,
            fetch_one=fetch,
            lease_owner="worker-resume",
            expected_contract=contract,
            checkpoint_contract=contract,
        )
        assert resumed.checkpoint.completed_codes == ("000001", "000002", "000003")
    assert calls == ["000003"]
    await engine.dispose()


async def _async_none() -> None:
    return None


@pytest.mark.asyncio
async def test_fact_bundle_hashes_are_repeatable_across_batch_sizes(tmp_path) -> None:
    codes = tuple(f"{index:06d}" for index in range(1, 21))
    bundles = {code: _bundle(code) for code in codes}
    assert _bundle("000001").content_hash == _bundle("000001").content_hash

    async def run(name: str, batch_size: int) -> dict[str, str]:
        engine, session_factory = await _factory(tmp_path, name)
        contract = _contract()
        checkpoint = _checkpoint(batch_size=batch_size)
        rounds = 0
        async with session_factory() as session:
            while checkpoint.status != "complete":
                rounds += 1
                assert rounds <= 10
                result = await run_v2_fact_capture_batch(
                    session,
                    manifest_hash=MANIFEST_HASH,
                    codes=codes,
                    checkpoint=checkpoint,
                    fetch_one=lambda code: _async_bundle(bundles[code]),
                    lease_owner=f"worker-{batch_size}",
                    expected_contract=contract,
                    checkpoint_contract=contract,
                )
                checkpoint = result.checkpoint
            loaded = await load_v2_checkpoint(session, manifest_hash=MANIFEST_HASH)
            assert loaded is not None
            result_hashes = dict(checkpoint.completed_hashes)
            assert dict(loaded.completed_hashes) == result_hashes
        await engine.dispose()
        return result_hashes

    batch_five = await run("batch-five.db", 5)
    batch_twenty = await run("batch-twenty.db", 20)
    assert batch_five == batch_twenty
    assert len(batch_five) == len(codes)


@pytest.mark.asyncio
async def test_provider_exception_commits_success_and_failed_code_is_retryable(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "retry.db")
    attempts = {"000002": 0}
    contract = _contract()

    async with session_factory() as session:

        async def first_fetch(code: str) -> V2CapturedAshareFacts:
            if code == "000002":
                attempts[code] += 1
                raise OSError("temporary provider failure")
            return _bundle(code)

        first = await run_v2_fact_capture_batch(
            session,
            manifest_hash=MANIFEST_HASH,
            codes=("000001", "000002"),
            checkpoint=_checkpoint(),
            fetch_one=first_fetch,
            lease_owner="worker-retry-1",
            expected_contract=contract,
            checkpoint_contract=contract,
        )
        assert first.checkpoint.completed_codes == ("000001",)
        assert first.checkpoint.failed_codes[0][0] == "000002"
        count = (
            await session.execute(text("SELECT COUNT(*) FROM ashare_research_universe_snapshots"))
        ).scalar_one()
        assert count == 1

        async def second_fetch(code: str) -> V2CapturedAshareFacts:
            attempts[code] += 1
            return _bundle(code)

        second = await run_v2_fact_capture_batch(
            session,
            manifest_hash=MANIFEST_HASH,
            codes=("000001", "000002"),
            checkpoint=first.checkpoint,
            fetch_one=second_fetch,
            lease_owner="worker-retry-2",
            expected_contract=contract,
            checkpoint_contract=contract,
        )
        assert second.checkpoint.completed_codes == ("000001", "000002")
        assert second.checkpoint.failed_codes == ()
    assert attempts["000002"] == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_capture_rolls_back_facts_and_checkpoint_progress(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "cancel.db")

    async with session_factory() as session:

        async def fetch(code: str) -> V2CapturedAshareFacts:
            if code == "000002":
                raise asyncio.CancelledError()
            return _bundle(code)

        with pytest.raises(asyncio.CancelledError):
            await run_v2_fact_capture_batch(
                session,
                manifest_hash=MANIFEST_HASH,
                codes=("000001", "000002"),
                checkpoint=_checkpoint(),
                fetch_one=fetch,
                lease_owner="worker-cancel",
            )
        counts = (
            await session.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM ashare_research_universe_snapshots),
                        (SELECT completed_count FROM leader_tactics_v2_checkpoints
                         WHERE manifest_hash = :manifest_hash)
                    """
                ),
                {"manifest_hash": MANIFEST_HASH},
            )
        ).one()
        assert tuple(counts) == (0, 0)
        loaded = await load_v2_checkpoint(session, manifest_hash=MANIFEST_HASH)
        assert loaded is not None
        assert loaded.completed_codes == ()
    await engine.dispose()


@pytest.mark.asyncio
async def test_wrong_bundle_code_is_failed_without_partial_facts(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "bad-bundle.db")

    async with session_factory() as session:

        async def fetch(_code: str) -> V2CapturedAshareFacts:
            return _bundle("000002")

        result = await run_v2_fact_capture_batch(
            session,
            manifest_hash=MANIFEST_HASH,
            codes=("000001",),
            checkpoint=_checkpoint(),
            fetch_one=fetch,
            lease_owner="worker-bad-bundle",
        )
        assert result.checkpoint.completed_codes == ()
        assert result.checkpoint.failed_codes[0][0] == "000001"
        count = (
            await session.execute(text("SELECT COUNT(*) FROM ashare_research_universe_snapshots"))
        ).scalar_one()
        assert count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_load_checkpoint_accepts_legacy_codes_and_rejects_count_or_hash_conflicts(
    tmp_path,
) -> None:
    engine, session_factory = await _factory(tmp_path, "load-validation.db")

    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_checkpoints
                    (manifest_hash, cursor, batch_size, completed_count,
                     completed_hashes_json, failed_codes_json, status, updated_at)
                VALUES (:manifest_hash, NULL, 5, 1, :completed_json, '[]',
                        'partial', :updated_at)
                """
            ),
            {
                "manifest_hash": MANIFEST_HASH,
                "completed_json": json.dumps(["000001"]),
                "updated_at": SOURCE_TIME,
            },
        )
        await session.commit()
        loaded = await load_v2_checkpoint(session, manifest_hash=MANIFEST_HASH)
        assert loaded is not None
        assert loaded.completed_codes == ("000001",)
        assert loaded.completed_hashes == ()

        await session.execute(
            text(
                "UPDATE leader_tactics_v2_checkpoints SET completed_count = 2 "
                "WHERE manifest_hash = :manifest_hash"
            ),
            {"manifest_hash": MANIFEST_HASH},
        )
        await session.commit()
        with pytest.raises(ValueError, match="completed_count"):
            await load_v2_checkpoint(session, manifest_hash=MANIFEST_HASH)

        conflict_payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "completed_codes": ["000001"],
            "content_hashes": [["000001", "a" * 64], ["000001", "b" * 64]],
        }
        await session.execute(
            text(
                """
                UPDATE leader_tactics_v2_checkpoints
                SET completed_count = 1, completed_hashes_json = :completed_json
                WHERE manifest_hash = :manifest_hash
                """
            ),
            {
                "manifest_hash": MANIFEST_HASH,
                "completed_json": json.dumps(conflict_payload),
            },
        )
        await session.commit()
        with pytest.raises(ValueError, match="conflicting|duplicate"):
            await load_v2_checkpoint(session, manifest_hash=MANIFEST_HASH)
    await engine.dispose()


@pytest.mark.asyncio
async def test_load_checkpoint_rejects_completed_failed_overlap(tmp_path) -> None:
    engine, session_factory = await _factory(tmp_path, "overlap.db")

    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_checkpoints
                    (manifest_hash, cursor, batch_size, completed_count,
                     completed_hashes_json, failed_codes_json, status, updated_at)
                VALUES (:manifest_hash, NULL, 5, 1, :completed_json, :failed_json,
                        'partial', :updated_at)
                """
            ),
            {
                "manifest_hash": MANIFEST_HASH,
                "completed_json": json.dumps(["000001"]),
                "failed_json": json.dumps([["000001", "transient failure"]]),
                "updated_at": SOURCE_TIME,
            },
        )
        await session.commit()

        with pytest.raises(ValueError, match="completed and failed codes overlap"):
            await load_v2_checkpoint(session, manifest_hash=MANIFEST_HASH)

    await engine.dispose()


@pytest.mark.asyncio
async def test_collector_rejects_noncanonical_hash_but_keeps_none_compatibility() -> None:
    async def valid(_code: str) -> str:
        return "a" * 64

    result = await run_bounded_batch(
        ("000001",),
        checkpoint=_checkpoint(),
        fetch_one=valid,
        provider_cooldown_seconds=0,
    )
    assert result.checkpoint.completed_hashes == (("000001", "a" * 64),)

    async def invalid(_code: str) -> str:
        return "A" * 64

    rejected = await run_bounded_batch(
        ("000001",),
        checkpoint=_checkpoint(),
        fetch_one=invalid,
        provider_cooldown_seconds=0,
    )
    assert rejected.checkpoint.completed_codes == ()
    assert rejected.checkpoint.failed_codes[0][0] == "000001"
