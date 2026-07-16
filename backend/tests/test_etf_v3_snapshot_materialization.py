from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.defaults.short_research import ASSET_TYPE_ETF, ShortResearchAsset
from app.models.entities import ShortResearchSignalItem, ShortResearchSignalRun
from app.services.short_research import snapshot_materialization
from app.services.short_research.service import ComputedAsset
from app.services.short_research.snapshot_materialization import (
    SnapshotMaterializationError,
    materialize_final_score_v3_snapshot,
)
from app.services.short_research.snapshot_publication import EtfCoverageBarrier

TRADE_DATE = date(2026, 7, 15)
DECISION_CUTOFF = datetime(2026, 7, 15, 15, 0)
SOURCE_AVAILABILITY_CUTOFF = datetime(2026, 7, 15, 21, 0)


def _asset(
    code: str,
    *,
    legacy_score: float,
    v3_score: float | None,
    eligible: bool,
    reasons: list[str] | None = None,
) -> ComputedAsset:
    metadata = ShortResearchAsset(
        asset_type=ASSET_TYPE_ETF,
        code=code,
        name=f"ETF{code}",
        category="broad_index",
        theme_tags=("宽基",),
        investment_direction="fixture",
        trading_rule_label="T+1",
        exchange="SH",
    )
    return ComputedAsset(
        metadata=metadata,
        rank=None,
        total_score=legacy_score,
        conclusion="谨慎观察",
        latest_date=TRADE_DATE,
        latest_value=1.0,
        usable_days=60,
        sample_level="充足",
        metrics={
            "ranking_asset_bucket": "broad-equity",
            "ranking_profile_version": "final_score_v3",
            "source_trade_date": TRADE_DATE.isoformat(),
            "v3_ranking_score": v3_score,
            "v3_score_eligible": eligible,
            "v3_score_limitation_reasons": reasons or [],
            "v3_missing_by_component": {},
            "component_reliability": {},
            "component_source_dates": {},
            "v3_input_values": {
                "return_5d": 0.01,
                "source_trade_date": TRADE_DATE.isoformat(),
            },
        },
        score_breakdown={
            "legacy": {"score": legacy_score},
            "final_score_v3_shadow": {
                "asset_bucket": "broad-equity",
                "score": v3_score,
                "score_eligible": eligible,
            },
        },
        risk_flags=[],
        rationale={"key_reason": "fixture"},
        source_note="fixture",
        entry_timing_label="趋势延续",
        entry_timing_reason="fixture",
    )


def _install_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    assets: list[ComputedAsset],
    *,
    decision_codes: list[str] | None = None,
) -> list[tuple[datetime, datetime]]:
    expected_codes = [asset.metadata.code for asset in assets]
    included_codes = decision_codes if decision_codes is not None else list(expected_codes)
    captured_cutoffs: list[tuple[datetime, datetime]] = []

    async def fake_universe(_session, *, as_of_date):
        assert as_of_date == TRADE_DATE
        return SimpleNamespace(
            as_of_date=as_of_date,
            members=[
                {
                    "asset_code": code,
                    "asset_bucket": "broad-equity",
                    "theme_tags": ["宽基"],
                    "tracked_underlying_id": None,
                    "membership_source": "fixture",
                    "effective_from": date(2025, 1, 1),
                    "effective_to": None,
                }
                for code in expected_codes
            ],
        )

    async def fake_barrier(_session, *, as_of_trade_date, data_cutoff):
        assert as_of_trade_date == TRADE_DATE
        assert data_cutoff.date() == TRADE_DATE
        return EtfCoverageBarrier(
            expected_codes=expected_codes,
            included_codes=included_codes,
            excluded=[
                {"asset_code": code, "reason": "missing_trade_date_price"}
                for code in expected_codes
                if code not in included_codes
            ],
        )

    async def fake_compute(_session, *, codes, as_of_date, decision_cutoff, data_cutoff):
        assert codes == included_codes
        assert as_of_date == TRADE_DATE
        captured_cutoffs.append((decision_cutoff, data_cutoff))
        return [asset for asset in assets if asset.metadata.code in codes]

    monkeypatch.setattr(snapshot_materialization, "build_point_in_time_universe_snapshot", fake_universe)
    monkeypatch.setattr(snapshot_materialization, "build_etf_coverage_barrier", fake_barrier)
    monkeypatch.setattr(snapshot_materialization, "compute_etf_snapshot_assets", fake_compute)
    return captured_cutoffs


@pytest.mark.asyncio
async def test_materializer_persists_complete_identity_and_ranks_by_v3_score(app, monkeypatch) -> None:
    assets = [
        _asset("510001", legacy_score=99.0, v3_score=70.0, eligible=True),
        _asset("510002", legacy_score=1.0, v3_score=80.0, eligible=True),
    ]
    assets[0].metrics["v3_adjusted_price_history_digest"] = "a" * 64
    assets[1].metrics["v3_adjusted_price_history_digest"] = "not-a-sha256-digest"
    captured_cutoffs = _install_dependencies(monkeypatch, assets)

    async with app.state.db.session() as session:
        run = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
            source_availability_cutoff=SOURCE_AVAILABILITY_CUTOFF,
        )
        await session.commit()
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run.id)
                .order_by(ShortResearchSignalItem.global_rank)
            )
        ).all()

    assert captured_cutoffs == [(DECISION_CUTOFF, SOURCE_AVAILABILITY_CUTOFF)]
    assert run.status == "success"
    assert run.scope_kind == "full"
    assert run.score_version == "final_score_v3"
    assert run.rule_version == "final_score_v3_rule_v2"
    assert run.score_field == "ranking_score"
    assert run.price_basis == "total_return_adjusted"
    assert run.data_cutoff == SOURCE_AVAILABILITY_CUTOFF
    assert run.as_of_trade_date == TRADE_DATE
    assert run.publication_state == "unpublished"
    assert all(
        [
            run.scope_hash,
            run.universe_snapshot_hash,
            run.input_snapshot_hash,
            run.ranking_contract_hash,
            run.idempotency_key,
        ]
    )
    assert run.expected_item_count == 2
    assert run.decision_data_item_count == 2
    assert run.decision_data_coverage_ratio == 1.0
    assert run.eligible_item_count == 2
    assert run.coverage_ratio == 1.0
    assert [(item.asset_code, item.global_rank, item.ranking_score) for item in items] == [
        ("510002", 1, 80.0),
        ("510001", 2, 70.0),
    ]
    assert [item.total_score for item in items] == [80.0, 70.0]

    input_assets = run.summary_json["input_snapshot"]["assets"]
    assert all(asset["input_values_hash"] for asset in input_assets)
    assert input_assets[0]["adjusted_price_history_provenance"] == {
        "digest": "a" * 64,
        "status": "sealed_digest",
        "fallback_input_values_hash": None,
    }
    assert input_assets[1]["adjusted_price_history_provenance"]["status"] == "unavailable_not_emitted"
    assert input_assets[0]["final_bucket_evidence"]["asset_bucket"] == "broad-equity"
    assert input_assets[0]["final_bucket_evidence"]["evidence_hash"]
    assert run.summary_json["draft_seal"]["snapshot_content_hash"]


@pytest.mark.asyncio
async def test_materializer_contract_hash_changes_when_hard_limit_changes(app, monkeypatch) -> None:
    assets = [_asset("510001", legacy_score=99.0, v3_score=70.0, eligible=True)]
    _install_dependencies(monkeypatch, assets)

    async with app.state.db.session() as session:
        original = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )
        await session.commit()
        changed_contract = deepcopy(snapshot_materialization.final_score_v3_contract())
        changed_contract["hard_limits"]["stale_score_cap"] = 54
        monkeypatch.setattr(snapshot_materialization, "final_score_v3_contract", lambda: changed_contract)
        changed = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )
        await session.commit()

    assert changed.id != original.id
    assert changed.ranking_contract_hash != original.ranking_contract_hash


@pytest.mark.asyncio
async def test_materializer_reuses_idempotent_run_without_mutating_legacy(app, monkeypatch) -> None:
    assets = [_asset("510001", legacy_score=99.0, v3_score=70.0, eligible=True)]
    _install_dependencies(monkeypatch, assets)

    async with app.state.db.session() as session:
        legacy = ShortResearchSignalRun(
            status="success",
            as_of_date=TRADE_DATE,
            config_json={"asset_type": "etf"},
            summary_json={"item_count": 0, "legacy": True},
        )
        session.add(legacy)
        await session.flush()
        first = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )
        second = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )
        await session.commit()
        run_count = len((await session.scalars(select(ShortResearchSignalRun))).all())
        item_count = len(
            (
                await session.scalars(
                    select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == first.id)
                )
            ).all()
        )

    assert first.id == second.id
    assert run_count == 2
    assert item_count == 1
    assert legacy.score_version is None
    assert legacy.ranking_contract_hash is None
    assert legacy.summary_json == {"item_count": 0, "legacy": True}


@pytest.mark.asyncio
async def test_materializer_rejects_incomplete_idempotent_run(app, monkeypatch) -> None:
    assets = [_asset("510001", legacy_score=99.0, v3_score=70.0, eligible=True)]
    _install_dependencies(monkeypatch, assets)

    async with app.state.db.session() as session:
        run = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )
        await session.commit()
        item = await session.scalar(
            select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run.id)
        )
        assert item is not None
        await session.delete(item)
        await session.commit()

        with pytest.raises(SnapshotMaterializationError, match="idempotent snapshot is incomplete"):
            await materialize_final_score_v3_snapshot(
                session,
                trade_date=TRADE_DATE,
                decision_cutoff=DECISION_CUTOFF,
            )


@pytest.mark.asyncio
async def test_materializer_rejects_idempotent_run_with_different_identity(app, monkeypatch) -> None:
    assets = [_asset("510001", legacy_score=99.0, v3_score=70.0, eligible=True)]
    _install_dependencies(monkeypatch, assets)

    async with app.state.db.session() as session:
        run = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )
        await session.commit()
        run.input_snapshot_hash = "corrupted-input-hash"
        await session.commit()

        with pytest.raises(SnapshotMaterializationError, match="idempotent snapshot identity does not match"):
            await materialize_final_score_v3_snapshot(
                session,
                trade_date=TRADE_DATE,
                decision_cutoff=DECISION_CUTOFF,
            )


@pytest.mark.asyncio
async def test_materializer_excludes_missing_nonfinite_and_price_ineligible_scores(app, monkeypatch) -> None:
    assets = [
        _asset("510001", legacy_score=10.0, v3_score=75.0, eligible=True),
        _asset("510002", legacy_score=20.0, v3_score=None, eligible=False, reasons=["missing_component"]),
        _asset("510003", legacy_score=30.0, v3_score=float("inf"), eligible=True),
        _asset("510004", legacy_score=40.0, v3_score=90.0, eligible=True),
    ]
    _install_dependencies(monkeypatch, assets, decision_codes=["510001", "510002", "510003"])

    async with app.state.db.session() as session:
        run = await materialize_final_score_v3_snapshot(
            session,
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )
        await session.commit()
        items = (
            await session.scalars(select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run.id))
        ).all()

    assert [item.asset_code for item in items] == ["510001"]
    assert run.decision_data_item_count == 3
    assert run.decision_data_coverage_ratio == 0.75
    assert run.eligible_item_count == 1
    assert run.coverage_ratio == 0.25
    exclusions = {item["asset_code"]: item["reasons"] for item in run.summary_json["coverage"]["score"]["excluded"]}
    assert exclusions == {
        "510002": ["missing_component"],
        "510003": ["non_finite_v3_ranking_score"],
        "510004": ["missing_trade_date_price"],
    }


@pytest.mark.asyncio
async def test_materializer_rejects_cutoff_from_another_trade_date(app, monkeypatch) -> None:
    _install_dependencies(monkeypatch, [_asset("510001", legacy_score=10.0, v3_score=75.0, eligible=True)])

    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="decision cutoff must match trade date"):
            await materialize_final_score_v3_snapshot(
                session,
                trade_date=TRADE_DATE,
                decision_cutoff=datetime(2026, 7, 16, 15, 0),
            )
