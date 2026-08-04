from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
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


def _bars(count: int = 180, start: date = date(2026, 1, 1)) -> tuple[V2AdjustedBar, ...]:
    return tuple(
        V2AdjustedBar(
            trade_date=start + timedelta(days=index),
            adjusted_open=100 + index * 0.05 - 0.1,
            adjusted_high=100 + index * 0.05 + 0.2,
            adjusted_low=100 + index * 0.05 - 0.2,
            adjusted_close=100 + index * 0.05,
            volume=1000 + index,
            amount=100000 + index,
            turnover=100000 + index,
            observed_at=datetime.combine(start + timedelta(days=index), time(15)),
            provider="eastmoney",
            adjustment_version="total-return-v1",
            revision_id=f"rev-{index}",
        )
        for index in range(count)
    )


def _asset(
    code: str,
    *,
    universe: str = "etf",
    bars: tuple[V2AdjustedBar, ...] | None = None,
    membership: V2PITMembership | None = None,
) -> V2AssetInput:
    actual_bars = bars or _bars()
    return V2AssetInput(
        universe=universe,  # type: ignore[arg-type]
        asset_code=code,
        asset_name=f"Asset {code}",
        signal_date=actual_bars[-1].trade_date,
        source_cutoff=datetime.combine(actual_bars[-1].trade_date, time(15, 30)),
        bars=actual_bars,
        membership=membership or _membership(),
    )


def test_all_frozen_formulas_fail_closed_for_history_and_nonfinite_inputs() -> None:
    short = _asset("510001", bars=_bars(61, start=date(2026, 4, 30)))
    nonfinite = _asset(
        "510002", bars=(*_bars()[:-1], replace(_bars()[-1], adjusted_close=math.nan))
    )
    result = screen_dual_universe((short, nonfinite))
    assert {row.formula_id for row in result.observations} == {
        "leader_breakout_proxy_v2",
        "base_launch_proxy_v2",
        "former_leader_repair_proxy_v2",
    }
    assert all(row.availability == "unavailable" for row in result.observations)
    reasons = {reason for row in result.observations for reason in row.exclusion_reasons}
    assert "insufficient_adjusted_history" in reasons
    assert "non_finite_adjusted_input" in reasons


def test_peer_count_and_clone_policy_are_explicit() -> None:
    one = screen_dual_universe((_asset("510001"),))
    assert all("insufficient_peer_count" in row.exclusion_reasons for row in one.observations)

    items = tuple(
        _asset(
            f"51000{index}", membership=_membership(clone_group="clone-a" if index <= 2 else None)
        )
        for index in range(1, 7)
    )
    result = screen_dual_universe(items)
    clone_rows = [row for row in result.observations if row.asset_code == "510001"]
    assert clone_rows
    assert all("clone_not_representative" in row.exclusion_reasons for row in clone_rows)


def test_identical_factual_inputs_have_cross_adapter_formula_parity() -> None:
    etf = screen_dual_universe(
        tuple(_asset(f"51000{index}", universe="etf") for index in range(1, 7))
    )
    ashare = screen_dual_universe(
        tuple(_asset(f"51000{index}", universe="ashare") for index in range(1, 7))
    )
    etf_view = {
        (row.asset_code, row.formula_id): (
            row.availability,
            row.qualifies,
            row.exclusion_reasons,
            row.score,
        )
        for row in etf.observations
    }
    ashare_view = {
        (row.asset_code, row.formula_id): (
            row.availability,
            row.qualifies,
            row.exclusion_reasons,
            row.score,
        )
        for row in ashare.observations
    }
    assert etf_view == ashare_view


def test_future_membership_and_bar_receipt_are_not_looked_through() -> None:
    item = _asset("510001")
    late_membership = replace(
        item.membership, observed_at=item.source_cutoff + timedelta(minutes=1)
    )
    late_bar = replace(item.bars[-1], observed_at=item.source_cutoff + timedelta(minutes=1))
    result = screen_dual_universe(
        (replace(item, membership=late_membership, bars=(*item.bars[:-1], late_bar)),)
    )
    reasons = {reason for row in result.observations for reason in row.exclusion_reasons}
    assert "membership_received_after_cutoff" in reasons
    assert "adjusted_bar_received_after_cutoff" in reasons
