from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    FORMER_LEADER_REPAIR_V2,
    STATE_CONFIRMED,
    STATE_INVALIDATED,
    STATE_PREPARING,
    V2_FORMULAS,
    V2_SOURCE_REGISTRY,
    V2AdjustedBar,
    V2AssetInput,
    V2CandidateObservation,
    V2ContractError,
    V2PITMembership,
    derive_lifecycle,
    screen_dual_universe,
    validate_runtime_contract,
)


def _membership(*, group: str = "theme-a", fact_hash: str | None = None) -> V2PITMembership:
    draft = V2PITMembership(
        group_id=group,
        effective_from=date(2025, 1, 1),
        effective_to=None,
        observed_at=datetime(2026, 1, 1, 15),
        mapping_kind="historical_pit",
        taxonomy_version="theme-v1",
        theme=group,
        sector=group,
        tracked_index=f"index-{group}",
        clone_group=None,
        issuer="issuer-a",
        fact_hash="",
    )
    return replace(
        draft,
        fact_hash=stable_contract_hash(draft.canonical_payload())
        if fact_hash is None
        else fact_hash,
    )


def _bars(*, count: int = 120, start: date = date(2026, 1, 1)) -> tuple[V2AdjustedBar, ...]:
    result = []
    for index in range(count):
        close = 100.0 + index * 0.05
        result.append(
            V2AdjustedBar(
                trade_date=start + timedelta(days=index),
                adjusted_open=close - 0.1,
                adjusted_high=close + 0.2,
                adjusted_low=close - 0.2,
                adjusted_close=close,
                volume=1000.0 + index,
                amount=100000.0 + index,
                turnover=100000.0 + index,
                observed_at=datetime.combine(start + timedelta(days=index), time(15)),
                provider="eastmoney",
                adjustment_version="total-return-v1",
                revision_id=f"rev-{index}",
            )
        )
    return tuple(result)


def _asset(code: str, *, membership: V2PITMembership | None = None) -> V2AssetInput:
    bars = _bars()
    return V2AssetInput(
        universe="etf",
        asset_code=code,
        asset_name=f"ETF {code}",
        signal_date=bars[-1].trade_date,
        source_cutoff=datetime.combine(bars[-1].trade_date, time(15, 30)),
        bars=bars,
        membership=membership or _membership(),
    )


def test_v2_registry_and_candidates_are_frozen() -> None:
    V2_SOURCE_REGISTRY.validate()
    assert tuple(item.formula_id for item in V2_FORMULAS) == (
        "leader_breakout_proxy_v2",
        "base_launch_proxy_v2",
        "former_leader_repair_proxy_v2",
    )
    assert all(
        "abs(adjusted_close-adjusted_MA20)/adjusted_ATR20" in item.formula_text
        for item in V2_FORMULAS[1:]
    )
    validate_runtime_contract()
    with pytest.raises(V2ContractError):
        validate_runtime_contract(formula_ids=(BREAKOUT_V2,))


def test_missing_membership_hash_and_forbidden_raw_price_fail_closed() -> None:
    item = _asset("510001", membership=_membership(fact_hash=""))
    bad_bar = replace(item.bars[-1], provider="sina")
    item = replace(item, bars=(*item.bars[:-1], bad_bar))
    result = screen_dual_universe((item,))
    assert result.observations
    reasons = {reason for row in result.observations for reason in row.exclusion_reasons}
    assert "missing_membership_fact_hash" in reasons
    assert "forbidden_raw_price_provider" in reasons
    assert all(row.availability == "unavailable" for row in result.observations)


def test_common_gate_facts_keep_three_equal_core_components() -> None:
    items = tuple(_asset(f"51000{index}") for index in range(6))
    result = screen_dual_universe(items)
    row = next(item for item in result.observations if item.formula_id == BREAKOUT_V2)
    facts = dict(row.gate_facts)
    assert {
        "peer_return_20_percentile",
        "peer_return_5_percentile",
        "peer_amount_20_percentile",
    } <= facts.keys()
    assert "core_score" in facts


def test_lifecycle_uses_later_eligible_daily_close_and_next_close_for_exit() -> None:
    signal_day = date(2026, 8, 1)
    bars = []
    closes = (100.0, 105.0, 90.0, 88.0)
    for index, close in enumerate(closes):
        trade_date = signal_day + timedelta(days=index)
        bars.append(
            V2AdjustedBar(
                trade_date=trade_date,
                adjusted_open=close - 0.2,
                adjusted_high=close + 0.2,
                adjusted_low=close - 0.4,
                adjusted_close=close,
                volume=1000.0,
                amount=100000.0,
                turnover=100000.0,
                observed_at=datetime.combine(trade_date, time(15)),
                provider="eastmoney",
                adjustment_version="total-return-v1",
                revision_id=f"lifecycle-{index}",
            )
        )
    observation = V2CandidateObservation(
        universe="etf",
        asset_code="510001",
        asset_name="ETF 510001",
        signal_date=signal_day + timedelta(days=1),
        formula_id=FORMER_LEADER_REPAIR_V2,
        state=STATE_PREPARING,
        availability="available",
        qualifies=True,
        score=0.8,
        gate_facts=(),
        exclusion_reasons=(),
        source_cutoff=datetime.combine(signal_day + timedelta(days=3), time(15, 30)),
        theme="theme-a",
        sector="theme-a",
        tracked_index="index-theme-a",
        clone_group=None,
        issuer="issuer-a",
        feature_hash="f" * 64,
    )
    transitions = derive_lifecycle(observation=observation, signal_bars=tuple(bars))
    assert transitions[0].to_state == STATE_PREPARING
    assert transitions[-1].to_state == STATE_INVALIDATED
    assert transitions[-1].simulated_execution_date == signal_day + timedelta(days=3)
    assert transitions[-1].execution_model == "next_eligible_adjusted_close_with_costs"
    assert all(item.to_state != STATE_CONFIRMED for item in transitions)
