from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.entities import EtfPitCaptureSource
from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.daily_reconstructable import (
    AdjustedOhlcvBar,
    AdjustmentProvenance,
)
from app.services.strategy_lab.etf_leader_tactics_pit import (
    LeaderBaselineScoreFact,
    LeaderPitAdapterError,
    LeaderRegimeFact,
    LeaderSectorTrendFact,
    LeaderTaxonomyFact,
    adapt_leader_pit_inputs,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    LEADER_BREAKOUT_CANDIDATE,
    build_leader_feature_panel,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    PointInTimeAdjustedSeries,
    PointInTimeEtfMetadata,
    PointInTimeRankingInputSnapshot,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _with_hash(value):
    return replace(value, fact_hash=stable_contract_hash(value.canonical_payload()))


def _cutoff() -> datetime:
    return datetime(2026, 7, 30, 15, 0, tzinfo=SHANGHAI)


def _source() -> EtfPitCaptureSource:
    context = {"kind": "immutable-pit-source", "version": 1}
    cutoff = _cutoff().replace(tzinfo=None)
    return EtfPitCaptureSource(
        id=1,
        source_signal_run_id=1,
        as_of_trade_date=date(2026, 7, 30),
        source_snapshot_hash=HASH_A,
        source_context_hash=stable_contract_hash(context),
        universe_manifest_hash=HASH_B,
        input_snapshot_hash=HASH_C,
        ranking_contract_hash=HASH_D,
        research_contract_hash=HASH_A,
        actionable_contract_hash=HASH_B,
        readiness_policy_version="dual_95_v1",
        readiness_state="complete",
        target_date_coverage_ratio=0.99,
        warmup_coverage_ratio=0.98,
        market_decision_cutoff=cutoff,
        data_receipt_cutoff=cutoff + timedelta(minutes=5),
        replay_visibility_cutoff=cutoff + timedelta(minutes=2),
        cutoff_timezone="Asia/Shanghai",
        provider_health_hash=HASH_C,
        source_context_json=context,
    )


def _metadata(code: str) -> PointInTimeEtfMetadata:
    observed = _cutoff() - timedelta(days=200)
    return PointInTimeEtfMetadata(
        asset_code=code,
        membership_source="sse",
        membership_external_source_id=f"listing:{code}",
        membership_provider_version="sse-listing-v1",
        membership_evidence_hash=HASH_A,
        membership_raw_payload_hash=HASH_B,
        membership_fact_hash=HASH_C,
        tracked_underlying_id=None,
        membership_known_at=observed,
        membership_last_modified_at=observed,
        membership_ingested_at=observed,
        eligible_from=date(2025, 1, 1),
        eligible_at=date(2026, 7, 30),
    )


def _series(code: str, *, turnover: float | None = 1_000_000.0) -> PointInTimeAdjustedSeries:
    start = date(2026, 2, 1)
    bars = tuple(
        AdjustedOhlcvBar(
            session_date=start + timedelta(days=index),
            adjusted_open=100.0 + index,
            adjusted_high=101.0 + index,
            adjusted_low=99.0 + index,
            adjusted_close=100.5 + index,
            volume=1_000.0 + index,
            turnover=turnover,
        )
        for index in range(180)
    )
    bars = (*bars[:-1], replace(bars[-1], session_date=date(2026, 7, 30)))
    return PointInTimeAdjustedSeries(
        asset_code=code,
        metadata=_metadata(code),
        bars=bars,
        provenance=AdjustmentProvenance(
            provider="eastmoney",
            adjustment_version="eastmoney.push2his.kline.hfq_v1",
            price_basis="total_return_adjusted",
            transform_kind="constant_multiplicative",
            scale_invariance_proven=True,
        ),
        earliest_source_timestamp=_cutoff() - timedelta(minutes=2),
        latest_source_timestamp=_cutoff() - timedelta(minutes=1),
        synchronized_after_cutoff=False,
        series_hash=HASH_D,
    )


def _snapshot(*series: PointInTimeAdjustedSeries) -> PointInTimeRankingInputSnapshot:
    return PointInTimeRankingInputSnapshot(
        replay_date=date(2026, 7, 30),
        decision_cutoff=_cutoff(),
        authoritative_universe=tuple(item.metadata for item in series),
        eligible_inputs=series,
        exclusions=(),
        universe_hash=HASH_A,
        input_hash=HASH_B,
        source_snapshot_hash=HASH_C,
        coverage_manifest_hash=HASH_D,
        page_asset_codes=tuple(item.asset_code for item in series),
        next_code_after=None,
        has_more=False,
    )


def _taxonomy(code: str, *, observed_at: datetime | None = None, kind: str = "historical_pit"):
    return _with_hash(
        LeaderTaxonomyFact(
            asset_code=code,
            peer_group="technology",
            effective_from=date(2025, 1, 1),
            effective_to=None,
            observed_at=observed_at or (_cutoff() - timedelta(minutes=5)),
            mapping_kind=kind,
            taxonomy_contract_hash=HASH_A,
            clone_group=code,
            tracked_index="index-technology",
            issuer="issuer-a",
            theme="人工智能",
            sector="科技",
        )
    )


def _sector():
    return _with_hash(
        LeaderSectorTrendFact(
            peer_group="technology",
            signal_date=date(2026, 7, 30),
            score=80.0,
            observed_at=_cutoff() - timedelta(minutes=2),
            contract_hash=HASH_B,
            fact_hash="",
        )
    )


def _regime():
    return _with_hash(
        LeaderRegimeFact(
            signal_date=date(2026, 7, 30),
            market_regime="risk_on",
            observed_at=_cutoff() - timedelta(minutes=2),
            contract_hash=REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
            status="available",
            fact_hash="",
        )
    )


def _baseline(code: str):
    return _with_hash(
        LeaderBaselineScoreFact(
            asset_code=code,
            signal_date=date(2026, 7, 30),
            score=70.0,
            observed_at=_cutoff() - timedelta(minutes=1),
            research_contract_hash=HASH_A,
            input_snapshot_hash=HASH_C,
            fact_hash="",
        )
    )


def test_adapter_uses_only_immutable_capture_replay_and_pit_facts() -> None:
    series = _series("510001")
    result = adapt_leader_pit_inputs(
        capture_source=_source(),
        replay_snapshot=_snapshot(series),
        taxonomy_facts={"510001": _taxonomy("510001")},
        sector_trend_facts={"technology": _sector()},
        baseline_score_facts={"510001": _baseline("510001")},
        regime_fact=_regime(),
    )

    item = result.inputs[0]
    assert len(item.bars) == 180
    assert item.peer_mapping_kind == "historical_pit"
    assert item.market_regime == "risk_on"
    assert item.baseline_score == 70.0
    assert item.input_unavailable_reasons == ()
    assert len(result.adapter_hash) == 64


@pytest.mark.parametrize(
    ("taxonomy", "reason"),
    [
        (None, "missing_historical_peer_mapping"),
        (_taxonomy("510001", kind="current_only"), "peer_mapping_not_point_in_time"),
        (
            _taxonomy("510001", observed_at=_cutoff() + timedelta(minutes=1)),
            "historical_peer_mapping_not_visible",
        ),
    ],
)
def test_adapter_never_backfills_current_or_late_taxonomy(taxonomy, reason: str) -> None:
    series = _series("510001")
    result = adapt_leader_pit_inputs(
        capture_source=_source(),
        replay_snapshot=_snapshot(series),
        taxonomy_facts={"510001": taxonomy} if taxonomy is not None else {},
        sector_trend_facts={"technology": _sector()},
        baseline_score_facts={"510001": _baseline("510001")},
        regime_fact=_regime(),
    )
    panel = build_leader_feature_panel(result.inputs)
    observation = next(
        item
        for item in panel.observations
        if item.candidate_id == LEADER_BREAKOUT_CANDIDATE
    )

    assert observation.availability == "unavailable"
    assert reason in (
        observation.unavailable_reasons
        or result.inputs[0].input_unavailable_reasons
    )


def test_adapter_rejects_missing_turnover_without_estimation() -> None:
    series = _series("510001", turnover=None)
    result = adapt_leader_pit_inputs(
        capture_source=_source(),
        replay_snapshot=_snapshot(series),
        taxonomy_facts={"510001": _taxonomy("510001")},
        sector_trend_facts={"technology": _sector()},
        baseline_score_facts={"510001": _baseline("510001")},
        regime_fact=_regime(),
    )

    assert result.inputs[0].bars == ()
    assert result.exclusions["510001"] == ("adjusted_turnover_unavailable",)


def test_adapter_rejects_mismatched_capture_cutoff() -> None:
    source = _source()
    source.market_decision_cutoff += timedelta(minutes=1)

    with pytest.raises(LeaderPitAdapterError, match="cutoffs"):
        adapt_leader_pit_inputs(
            capture_source=source,
            replay_snapshot=_snapshot(_series("510001")),
            taxonomy_facts={},
            sector_trend_facts={},
            baseline_score_facts={},
            regime_fact=None,
        )
