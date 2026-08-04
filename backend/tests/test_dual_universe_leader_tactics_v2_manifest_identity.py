from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2AdjustedBar,
    V2AssetInput,
    V2ContractError,
    V2PITMembership,
    build_v2_manifest,
    derive_lifecycle,
    screen_dual_universe,
    screen_result_payload,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    persist_v2_screen_result,
)


def _asset() -> V2AssetInput:
    start = date(2026, 1, 1)
    bars = tuple(
        V2AdjustedBar(
            trade_date=start + timedelta(days=index),
            adjusted_open=99.9 + index * 0.05,
            adjusted_high=100.2 + index * 0.05,
            adjusted_low=99.8 + index * 0.05,
            adjusted_close=100.0 + index * 0.05,
            volume=1000.0 + index,
            amount=100000.0 + index,
            turnover=100000.0 + index,
            observed_at=datetime.combine(
                start + timedelta(days=index),
                time(15),
            ),
            provider="eastmoney",
            adjustment_version="total-return-v1",
            revision_id=f"revision-{index}",
        )
        for index in range(120)
    )
    membership = V2PITMembership(
        group_id="theme-a",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        observed_at=datetime(2026, 1, 1, 15),
        mapping_kind="historical_pit",
        taxonomy_version="theme-v1",
        theme="theme-a",
        sector="technology",
        tracked_index="index-a",
        issuer="issuer-a",
        fact_hash="",
    )
    membership = replace(
        membership,
        fact_hash=stable_contract_hash(membership.canonical_payload()),
    )
    return V2AssetInput(
        universe="etf",
        asset_code="510001",
        asset_name="Test ETF",
        signal_date=bars[-1].trade_date,
        source_cutoff=datetime(2026, 4, 30, 15),
        bars=bars,
        membership=membership,
    )


def _canonical_manifest_for(result):
    return build_v2_manifest(
        universe=result.universe,
        decision_cutoff=result.source_cutoff,
        data_receipt_cutoff=result.data_receipt_cutoff,
        input_hash=result.input_hash,
        code_version=result.code_version,
        exclusions=result.exclusions,
        provider_health=result.provider_health,
    )


def _manifest_ddl() -> str:
    return """
    CREATE TABLE leader_tactics_v2_source_registries (
        id INTEGER PRIMARY KEY,
        registry_version TEXT NOT NULL,
        registry_hash TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL,
        created_at DATETIME NOT NULL
    )
    """


def _run_manifest_ddl() -> str:
    return """
    CREATE TABLE leader_tactics_v2_run_manifests (
        id INTEGER PRIMARY KEY,
        manifest_hash TEXT NOT NULL UNIQUE,
        universe TEXT NOT NULL,
        decision_cutoff DATETIME NOT NULL,
        data_receipt_cutoff DATETIME NOT NULL,
        input_hash TEXT NOT NULL,
        source_registry_hash TEXT NOT NULL,
        formula_registry_hash TEXT NOT NULL,
        code_version TEXT NOT NULL,
        holdout_identity TEXT NOT NULL,
        status TEXT NOT NULL,
        research_only BOOLEAN NOT NULL,
        provider_health_json TEXT NOT NULL,
        exclusions_json TEXT NOT NULL,
        manifest_payload_json TEXT NOT NULL,
        created_at DATETIME NOT NULL
    )
    """


def _observations_ddl() -> str:
    return """
    CREATE TABLE leader_tactics_v2_candidate_observations (
        id INTEGER PRIMARY KEY,
        manifest_hash TEXT NOT NULL,
        universe TEXT NOT NULL,
        asset_code TEXT NOT NULL,
        asset_name TEXT NOT NULL,
        theme TEXT,
        sector TEXT,
        tracked_index TEXT,
        formula_id TEXT NOT NULL,
        state TEXT NOT NULL,
        availability TEXT NOT NULL,
        qualifies BOOLEAN NOT NULL,
        score FLOAT,
        signal_date DATE NOT NULL,
        source_cutoff DATETIME NOT NULL,
        gate_facts_json TEXT NOT NULL,
        exclusion_reasons_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        feature_hash TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE (manifest_hash, universe, asset_code, formula_id, signal_date)
    )
    """


def test_default_screen_identity_is_reproducible() -> None:
    result = screen_dual_universe((_asset(),))
    expected = _canonical_manifest_for(result)

    assert result.code_version == "dual-universe-leader-tactics-v2"
    assert result.provider_health == ()
    assert result.manifest_hash == expected.manifest_hash


def test_screen_result_payload_is_json_safe_and_retains_pit_evidence() -> None:
    result = screen_dual_universe((_asset(),), code_version="payload-build")

    payload = screen_result_payload(result)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    decoded = json.loads(encoded)

    assert decoded["data_receipt_cutoff"] == result.data_receipt_cutoff.isoformat()
    assert decoded["source_cutoff"] == result.source_cutoff.isoformat()
    assert decoded["code_version"] == "payload-build"
    assert decoded["provider_health"] == {}
    assert decoded["observations"][0]["signal_date"] == result.signal_date.isoformat()
    assert "gate_facts" in decoded["observations"][0]


def test_screen_rejects_bar_received_before_trade_date() -> None:
    asset = _asset()
    early_signal_bar = replace(
        asset.bars[-1],
        observed_at=datetime.combine(
            asset.signal_date - timedelta(days=1),
            time(15),
        ),
    )

    result = screen_dual_universe((replace(asset, bars=(*asset.bars[:-1], early_signal_bar)),))

    assert all(row.availability == "unavailable" for row in result.observations)
    assert {reason for row in result.observations for reason in row.exclusion_reasons} >= {
        "adjusted_bar_received_before_trade_date"
    }


def test_lifecycle_ignores_future_bar_received_before_trade_date() -> None:
    asset = _asset()
    observation = screen_dual_universe((asset,)).observations[0]
    future_date = asset.signal_date + timedelta(days=1)
    future_close = asset.bars[-1].adjusted_close + 10.0
    early_future_bar = replace(
        asset.bars[-1],
        trade_date=future_date,
        adjusted_open=future_close - 0.1,
        adjusted_high=future_close + 0.2,
        adjusted_low=future_close - 0.2,
        adjusted_close=future_close,
        observed_at=datetime.combine(asset.signal_date, time(16)),
        revision_id="future-received-early",
    )

    transitions = derive_lifecycle(
        observation=observation,
        signal_bars=(*asset.bars, early_future_bar),
        evaluation_cutoff=datetime.combine(future_date, time(16)),
        visible_through=future_date,
    )

    assert [transition.to_state for transition in transitions] == ["preparing"]


def test_custom_screen_identity_is_carried_into_result() -> None:
    provider_health = (("eastmoney", "healthy"), ("akshare", "degraded"))
    result = screen_dual_universe(
        (_asset(),),
        code_version="build-2026-08-04.1",
        provider_health=provider_health,
    )
    expected = _canonical_manifest_for(result)

    assert result.code_version == "build-2026-08-04.1"
    assert result.provider_health == provider_health
    assert result.manifest_hash == expected.manifest_hash


@pytest.mark.asyncio
async def test_materialize_default_does_not_add_external_identity(monkeypatch) -> None:
    from app.services.workflows import dual_universe_leader_tactics_v2 as workflow

    result = screen_dual_universe((_asset(),), code_version="screen-build")
    calls = []

    async def fake_persist(session, observed, **kwargs):
        calls.append((session, observed, kwargs))
        return observed.manifest_hash

    monkeypatch.setattr(workflow, "persist_v2_screen_result", fake_persist)
    persisted_hash = await workflow.materialize_v2_result(object(), result, enabled=True)

    assert persisted_hash == result.manifest_hash
    assert calls[0][1] is result
    assert calls[0][2] == {}


@pytest.mark.asyncio
async def test_persistence_fails_closed_for_mismatched_external_identity() -> None:
    result = screen_dual_universe((_asset(),), code_version="screen-build")
    with pytest.raises(V2ContractError, match="code_version"):
        await persist_v2_screen_result(None, result, code_version="different-build")


@pytest.mark.asyncio
async def test_persistence_writes_canonical_hash_and_provider_health(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    async with engine.begin() as connection:
        await connection.execute(text(_manifest_ddl()))
        await connection.execute(text(_run_manifest_ddl()))
        await connection.execute(text(_observations_ddl()))

    provider_health = (("eastmoney", "healthy"), ("akshare", "degraded"))
    result = screen_dual_universe(
        (_asset(),),
        code_version="screen-build",
        provider_health=provider_health,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        persisted_hash = await persist_v2_screen_result(session, result)
        row = (
            (
                await session.execute(
                    text(
                        """
                    SELECT manifest_hash, code_version, provider_health_json,
                           manifest_payload_json
                    FROM leader_tactics_v2_run_manifests
                    """
                    )
                )
            )
            .mappings()
            .one()
        )
    await engine.dispose()

    assert persisted_hash == result.manifest_hash
    assert row["manifest_hash"] == result.manifest_hash
    assert row["code_version"] == "screen-build"
    assert json.loads(row["provider_health_json"]) == {
        "akshare": "degraded",
        "eastmoney": "healthy",
    }
    payload = json.loads(row["manifest_payload_json"])
    assert payload["manifest_hash"] == result.manifest_hash
    assert payload["code_version"] == "screen-build"
    assert payload["provider_health"] == [
        ["eastmoney", "healthy"],
        ["akshare", "degraded"],
    ]
