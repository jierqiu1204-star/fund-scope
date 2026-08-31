from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import market_data
from app.services.strategy_lab import dual_universe_leader_tactics_v2_etf_inputs as inputs
from app.services.strategy_lab.etf_point_in_time_decision_data import PointInTimeEtfMetadata


def _sessions(end: date, count: int) -> tuple[date, ...]:
    values: list[date] = []
    current = end
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(values))


def _metadata(
    signal_date: date,
    cutoff: datetime,
    code: str = "510001",
) -> PointInTimeEtfMetadata:
    return PointInTimeEtfMetadata(
        asset_code=code,
        membership_source="authoritative",
        membership_external_source_id=code,
        membership_provider_version="v1",
        membership_evidence_hash="a" * 64,
        membership_raw_payload_hash="b" * 64,
        membership_fact_hash="c" * 64,
        tracked_underlying_id=None,
        membership_known_at=cutoff.astimezone(UTC),
        membership_last_modified_at=cutoff.astimezone(UTC),
        membership_ingested_at=cutoff.astimezone(UTC),
        eligible_from=date(2020, 1, 1),
        eligible_at=signal_date,
    )


def _taxonomy(**overrides):
    values = {
        "theme_group": "technology",
        "primary_theme": "人工智能",
        "secondary_themes_json": [],
        "classification_reason": "国内名称或标签命中“人工智能”。",
        "classification_source": "domestic_keyword",
        "confidence": "high",
        "source": "fundscope.etf_taxonomy",
        "provider_version": "taxonomy-v1",
        "rule_version": "rule-v1",
        "fact_hash": "d" * 64,
        "observed_at": datetime(2026, 8, 4, 10),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_etf_membership_prefers_verified_eastmoney_board_over_tracked_index() -> None:
    signal_date = date(2026, 8, 4)
    cutoff = datetime(2026, 8, 4, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    metadata = replace(
        _metadata(signal_date, cutoff),
        tracked_underlying_id="CSI-RARE-EARTH",
    )
    taxonomy = _taxonomy(
        theme_group="materials",
        primary_theme="基础材料",
        classification_reason="国内名称或标签命中“稀土”。",
    )

    membership = inputs._membership_for(
        metadata,
        taxonomy=taxonomy,
        signal_date=signal_date,
        source_cutoff=datetime(2026, 8, 4, 11),
    )

    assert membership is not None
    assert membership.group_id == "eastmoney:concept:BK0578"
    assert membership.theme == "稀土永磁"
    assert membership.provider_theme_code == "BK0578"
    assert membership.provider_theme_label == "稀土永磁"
    assert membership.resolution_mode == "eastmoney_concept_etf_proxy"
    assert membership.tracked_index == "CSI-RARE-EARTH"
    assert membership.clone_group == "index:CSI-RARE-EARTH"


def test_etf_membership_uses_pit_theme_before_tracked_index_fallback() -> None:
    signal_date = date(2026, 8, 4)
    cutoff = datetime(2026, 8, 4, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    metadata = replace(
        _metadata(signal_date, cutoff),
        tracked_underlying_id="CSI-AI-APPLICATION",
    )

    membership = inputs._membership_for(
        metadata,
        taxonomy=_taxonomy(theme_group="AI应用"),
        signal_date=signal_date,
        source_cutoff=datetime(2026, 8, 4, 11),
    )

    assert membership is not None
    assert membership.group_id == "theme:AI应用"
    assert membership.tracked_index == "CSI-AI-APPLICATION"
    assert membership.clone_group == "index:CSI-AI-APPLICATION"


def test_etf_board_match_keeps_mlcc_distinct_from_passive_components() -> None:
    mlcc = inputs._eastmoney_concept_for_etf(
        _taxonomy(primary_theme="MLCC", classification_reason="名称命中 MLCC。")
    )
    passive = inputs._eastmoney_concept_for_etf(
        _taxonomy(
            primary_theme="被动元件",
            classification_reason="名称命中被动元件概念。",
        )
    )

    assert mlcc is not None and mlcc.provider_theme_code == "BK0890"
    assert passive is not None and passive.provider_theme_code == "BK0976"


@pytest.mark.asyncio
async def test_etf_v2_input_reader_pages_persisted_data_without_provider_calls(
    monkeypatch,
) -> None:
    signal_date = date(2026, 8, 4)
    decision_cutoff = datetime(2026, 8, 4, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    identity_cutoff = datetime(2026, 8, 4, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    metadata = _metadata(signal_date, decision_cutoff)
    facts = tuple(
        market_data.EtfAdjustedDailyFact(
            etf_code="510001",
            trade_date=trade_date,
            raw_open=10.0,
            raw_high=10.5,
            raw_low=9.5,
            raw_close=10.0,
            volume=1_000.0 + index,
            turnover=10_000.0 + index,
            raw_price_basis="raw_ohlcv",
            adjusted_close=10.0 + index / 100.0,
            research_price_basis="total_return_adjusted",
            data_provider="eastmoney",
            provider_version="eastmoney.push2his.kline.hfq_v1",
            source_timestamp=datetime.combine(trade_date, datetime.min.time()).replace(hour=6),
            adjustment_version="eastmoney.push2his.kline.hfq_v1",
            decision_eligible=True,
            decision_ineligibility_reason=None,
            revision_hash=f"revision-{index}",
            # Trusted history arrived after the 15:00 decision snapshot but
            # before the materialization receipt cutoff.
            first_seen_at=datetime(2026, 8, 4, 9),
            observed_at=datetime(2026, 8, 4, 9),
        )
        for index, trade_date in enumerate(_sessions(signal_date, 180), 1)
    )
    taxonomy = SimpleNamespace(
        theme_group="AI应用",
        primary_theme="人工智能",
        provider_version="taxonomy-v1",
        rule_version="rule-v1",
        fact_hash="d" * 64,
        observed_at=datetime(2026, 8, 4, 10),
    )
    calls: list[tuple[str, ...]] = []

    async def seed(*_args, **_kwargs):
        return SimpleNamespace(
            authoritative_universe=(
                metadata,
                _metadata(signal_date, decision_cutoff, "510002"),
            )
        )

    async def taxonomy_facts(*_args, **kwargs):
        assert kwargs["cutoff"] == identity_cutoff
        return {"510001": taxonomy}

    async def adjusted(
        *_args,
        etf_codes,
        compatible_provider_versions,
        decision_cutoff,
        **_kwargs,
    ):
        assert decision_cutoff == identity_cutoff
        assert compatible_provider_versions == (
            ("eastmoney", "eastmoney.push2his.kline.hfq_v1"),
            ("tickflow", "tickflow.free.klines.backward_v1"),
        )
        calls.append(etf_codes)
        return facts

    monkeypatch.setattr(inputs, "load_point_in_time_etf_decision_inputs", seed)
    monkeypatch.setattr(inputs, "_taxonomy_by_code", taxonomy_facts)
    monkeypatch.setattr(
        inputs.market_data,
        "etf_adjusted_daily_facts_on_or_before",
        adjusted,
    )

    result = await inputs.read_etf_v2_asset_inputs(
        object(),  # type: ignore[arg-type]
        replay_date=signal_date,
        decision_cutoff=decision_cutoff,
        identity_cutoff=identity_cutoff,
        eligible_codes=("510001",),
        page_size=1,
    )

    assert calls == [("510001",)]
    assert result.universe_count == 1
    assert result.adjusted_120_count == 1
    assert result.adjusted_180_count == 1
    assert result.pit_group_count == 1
    assert result.inputs[0].asset_name == "510001"
    assert result.inputs[0].membership is not None
    assert result.inputs[0].membership.group_id == "theme:AI应用"
    assert result.inputs[0].source_cutoff.tzinfo is None
    assert result.inputs[0].identity_cutoff == datetime(2026, 8, 4, 11)
    assert result.source_cutoff == datetime(2026, 8, 4, 7)
    assert result.identity_cutoff == datetime(2026, 8, 4, 11)
    assert result.provider_health == (("eastmoney", "healthy"),)
