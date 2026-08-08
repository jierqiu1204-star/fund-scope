from __future__ import annotations

import inspect
import math
import time
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from datetime import time as dt_time

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab import dual_universe_leader_tactics_v2 as v2_engine
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BASE_LAUNCH_V2,
    FORMER_LEADER_REPAIR_V2,
    STATE_CONFIRMED,
    STATE_INVALIDATED,
    V2_INPUT_HASH_SCHEMA_VERSION,
    V2AdjustedBar,
    V2AssetInput,
    V2CandidateObservation,
    V2PITMembership,
    _group_component_percentiles,
    _prior_leadership_index,
    build_v2_manifest,
    derive_lifecycle,
    screen_dual_universe,
)


def _membership(*, clone_group: str | None = None) -> V2PITMembership:
    draft = V2PITMembership(
        group_id="theme-a",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        observed_at=datetime(2026, 1, 1, 15),
        mapping_kind="historical_pit",
        taxonomy_version="theme-v1",
        theme="theme-a",
        sector="technology",
        tracked_index="index-a",
        clone_group=clone_group,
        issuer="issuer-a",
        fact_hash="",
    )
    return replace(draft, fact_hash=stable_contract_hash(draft.canonical_payload()))


def _bars(
    *,
    count: int = 120,
    start: date = date(2026, 1, 1),
    amount: float = 100_000.0,
    invalid_provider: bool = False,
) -> tuple[V2AdjustedBar, ...]:
    return tuple(
        V2AdjustedBar(
            trade_date=start + timedelta(days=index),
            adjusted_open=100 + index * 0.05 - 0.1,
            adjusted_high=100 + index * 0.05 + 0.2,
            adjusted_low=100 + index * 0.05 - 0.2,
            adjusted_close=100 + index * 0.05,
            volume=1000 + index,
            amount=amount + index,
            turnover=amount + index,
            observed_at=datetime.combine(start + timedelta(days=index), dt_time(15)),
            provider="sina" if invalid_provider else "eastmoney",
            adjustment_version="total-return-v1",
            revision_id=f"rev-{index}",
        )
        for index in range(count)
    )


def _asset(
    code: str,
    *,
    bars: tuple[V2AdjustedBar, ...] | None = None,
    clone_group: str | None = None,
    amount: float = 100_000.0,
    invalid_provider: bool = False,
) -> V2AssetInput:
    actual_bars = bars or _bars(amount=amount, invalid_provider=invalid_provider)
    return V2AssetInput(
        universe="etf",
        asset_code=code,
        asset_name=f"Asset {code}",
        signal_date=actual_bars[-1].trade_date,
        source_cutoff=datetime.combine(actual_bars[-1].trade_date, dt_time(15, 30)),
        bars=actual_bars,
        membership=_membership(clone_group=clone_group),
    )


def test_clone_representative_uses_valid_highest_amount_and_not_return() -> None:
    items = (
        _asset("510001", clone_group="clone-a", amount=100_000),
        _asset("510002", clone_group="clone-a", amount=200_000),
        _asset("510003", clone_group="clone-a", amount=999_999_999, invalid_provider=True),
        *(_asset(f"51000{index}") for index in range(4, 8)),
    )
    result = screen_dual_universe(items)
    valid_rep_rows = [row for row in result.observations if row.asset_code == "510002"]
    low_liquidity_rows = [row for row in result.observations if row.asset_code == "510001"]
    invalid_rows = [row for row in result.observations if row.asset_code == "510003"]
    assert valid_rep_rows and low_liquidity_rows and invalid_rows
    assert all("clone_not_representative" not in row.exclusion_reasons for row in valid_rep_rows)
    assert all("clone_not_representative" in row.exclusion_reasons for row in low_liquidity_rows)
    assert all("clone_not_representative" in row.exclusion_reasons for row in invalid_rows)
    assert all("forbidden_raw_price_provider" in row.exclusion_reasons for row in invalid_rows)


def test_score_components_are_group_percentiles_with_reverse_low_is_better() -> None:
    rows = [
        (
            _asset("510001"),
            {
                "raw_score_values": {
                    "breakout_magnitude": 0.01,
                    "compression": 0.50,
                    "overextension": 0.90,
                }
            },
        ),
        (
            _asset("510002"),
            {
                "raw_score_values": {
                    "breakout_magnitude": 0.03,
                    "compression": 0.30,
                    "overextension": 0.40,
                }
            },
        ),
        (
            _asset("510003"),
            {
                "raw_score_values": {
                    "breakout_magnitude": 0.08,
                    "compression": 0.10,
                    "overextension": 0.10,
                }
            },
        ),
    ]
    ranks = _group_component_percentiles(
        rows,
        reverse_components={
            "breakout_magnitude": False,
            "compression": True,
            "overextension": True,
        },
    )
    assert ranks["breakout_magnitude"][("theme-a", "510001")] == 0.0
    assert ranks["breakout_magnitude"][("theme-a", "510003")] == 1.0
    assert ranks["compression"][("theme-a", "510003")] == 1.0
    assert ranks["overextension"][("theme-a", "510001")] == 0.0


def test_screen_uses_symmetric_overextension_definition() -> None:
    bars = list(_bars())
    last = bars[-1]
    bars[-1] = replace(
        last,
        adjusted_open=94.9,
        adjusted_high=95.2,
        adjusted_low=94.8,
        adjusted_close=95.0,
    )
    result = screen_dual_universe((_asset("510001", bars=tuple(bars)),))
    row = next(item for item in result.observations if item.formula_id == BASE_LAUNCH_V2)
    facts = dict(row.gate_facts)
    expected = abs(float(facts["adjusted_ma20"]) - 95.0) / float(facts["adjusted_atr20"])
    assert math.isclose(float(facts["overextension_atr"]), expected, rel_tol=1e-12)
    assert expected >= 0.0


def _lifecycle_observation(signal_date: date) -> V2CandidateObservation:
    return V2CandidateObservation(
        universe="etf",
        asset_code="510001",
        asset_name="Asset 510001",
        signal_date=signal_date,
        formula_id=FORMER_LEADER_REPAIR_V2,
        state="preparing",
        availability="available",
        qualifies=True,
        score=0.8,
        gate_facts=(),
        exclusion_reasons=(),
        source_cutoff=datetime.combine(signal_date, dt_time(15, 30)),
        theme="theme-a",
        sector="technology",
        tracked_index="index-a",
        clone_group=None,
        issuer="issuer-a",
        feature_hash="f" * 64,
    )


def test_lifecycle_separates_signal_cutoff_and_future_visibility() -> None:
    start = date(2026, 8, 1)
    closes = (100.0, 100.0, 100.0, 102.0, 1000.0, 90.0, 90.0, 90.0)
    bars = []
    for index, close in enumerate(closes):
        trade_date = start + timedelta(days=index)
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
                observed_at=datetime.combine(trade_date, dt_time(15)),
                provider="eastmoney",
                adjustment_version="total-return-v1",
                decision_eligible=index != 4,
                revision_id=f"lifecycle-{index}",
            )
        )
    observation = _lifecycle_observation(start + timedelta(days=2))
    without_future = derive_lifecycle(observation=observation, signal_bars=tuple(bars))
    assert [item.to_state for item in without_future] == ["preparing"]

    evaluated = derive_lifecycle(
        observation=observation,
        signal_bars=tuple(bars),
        evaluation_cutoff=datetime.combine(start + timedelta(days=7), dt_time(15, 30)),
        visible_through=start + timedelta(days=7),
    )
    assert sum(item.to_state == STATE_CONFIRMED for item in evaluated) == 1
    invalidated = next(item for item in evaluated if item.to_state == STATE_INVALIDATED)
    assert invalidated.simulated_execution_date == start + timedelta(days=6)
    assert math.isclose(float(invalidated.adjusted_ma5), (100 + 100 + 100 + 102 + 90) / 5)


def test_prior_leadership_precomputes_group_matrix_without_per_target_sorting() -> None:
    items = tuple(
        _asset(f"510{index:03d}", bars=_bars(count=180, amount=100_000 + index))
        for index in range(32)
    )
    source = inspect.getsource(_prior_leadership_index)
    assert "sorted(peer.bars" not in source
    started = time.perf_counter()
    index = _prior_leadership_index(items)
    elapsed = time.perf_counter() - started
    assert set(index) == {item.asset_code for item in items}
    assert elapsed < 2.0


def test_incremental_input_hash_and_manifest_are_batch_order_independent() -> None:
    items = tuple(_asset(f"510{index:03d}") for index in range(1, 7))
    first = screen_dual_universe(items)
    second = screen_dual_universe(tuple(reversed(items)))
    assert first.input_hash == second.input_hash
    assert first.manifest_hash == second.manifest_hash
    assert first.observations == second.observations
    manifest = build_v2_manifest(
        universe=first.universe,
        decision_cutoff=first.source_cutoff,
        data_receipt_cutoff=first.source_cutoff,
        input_hash=first.input_hash,
    )
    assert manifest.input_hash_schema_version == V2_INPUT_HASH_SCHEMA_VERSION


def test_hot_path_canonical_payloads_match_dataclass_contract() -> None:
    membership = _membership()
    expected_membership = asdict(membership)
    expected_membership.pop("fact_hash")
    assert membership.canonical_payload() == expected_membership

    items = tuple(_asset(f"510{index:03d}") for index in range(1, 7))
    observation = screen_dual_universe(items).observations[0]
    expected_observation = asdict(observation)
    expected_observation.pop("feature_hash")
    assert observation.canonical_payload() == expected_observation


def test_screen_validates_each_assets_membership_and_bars_once(monkeypatch) -> None:
    items = tuple(_asset(f"510{index:03d}") for index in range(1, 7))
    original_membership = v2_engine._membership_reasons
    original_bars = v2_engine._bar_reasons
    membership_calls = 0
    bar_calls = 0

    def counted_membership(item: V2AssetInput) -> list[str]:
        nonlocal membership_calls
        membership_calls += 1
        return original_membership(item)

    def counted_bars(item: V2AssetInput, required_history: int) -> list[str]:
        nonlocal bar_calls
        bar_calls += 1
        return original_bars(item, required_history)

    monkeypatch.setattr(v2_engine, "_membership_reasons", counted_membership)
    monkeypatch.setattr(v2_engine, "_bar_reasons", counted_bars)
    screen_dual_universe(items)
    assert membership_calls == len(items)
    assert bar_calls == len(items)
