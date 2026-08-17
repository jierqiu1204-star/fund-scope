from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BASE_LAUNCH_V2,
    BREAKOUT_V2,
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
    _required_batch_qualifiers,
    build_v2_staged_asset_feature,
    screen_dual_universe,
    staged_theme_percentile_overrides,
    staged_v2_input_hash,
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


def _group_membership(group: str, theme: str) -> V2PITMembership:
    draft = replace(
        _membership(),
        group_id=group,
        theme=theme,
        hierarchy_level="fine_theme" if group.startswith("fine_theme:") else "broad_industry",
        normalized_theme_key="rare_earth" if group == "fine_theme:rare_earth" else None,
        resolution_mode=(
            "fine_theme_pit" if group.startswith("fine_theme:") else "broad_industry_fallback"
        ),
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


def test_batch_confirmation_is_proportional_for_fine_themes_and_capped_for_broad_groups() -> None:
    broad = _group_membership("sw1:electronics", "电子")
    fine = _group_membership("fine_theme:passive_components", "被动元件/MLCC")

    assert _required_batch_qualifiers(
        formula_id=BASE_LAUNCH_V2, peer_count=5, membership=broad
    ) == (3, "broad_industry_capped_v1")
    assert _required_batch_qualifiers(
        formula_id=BASE_LAUNCH_V2, peer_count=318, membership=broad
    ) == (
        5,
        "broad_industry_capped_v1",
    )
    assert _required_batch_qualifiers(
        formula_id=BASE_LAUNCH_V2, peer_count=20, membership=fine
    ) == (
        4,
        "fine_theme_proportional_v1",
    )
    assert _required_batch_qualifiers(
        formula_id=BASE_LAUNCH_V2, peer_count=318, membership=fine
    ) == (
        64,
        "fine_theme_proportional_v1",
    )
    assert _required_batch_qualifiers(
        formula_id=BREAKOUT_V2, peer_count=318, membership=fine
    ) == (1, "hot_theme_core_leader_v1")


def test_single_core_breakout_is_not_rejected_by_duplicate_batch_gate() -> None:
    membership = _group_membership("fine_theme:passive_components", "被动元件/MLCC")
    target_bars = list(_bars())
    target_bars[-1] = replace(
        target_bars[-1],
        adjusted_open=119.0,
        adjusted_high=121.0,
        adjusted_low=118.0,
        adjusted_close=120.0,
        volume=10_000.0,
        amount=10_000_000.0,
        turnover=10_000_000.0,
    )
    items = (
        _asset("000636", universe="ashare", bars=tuple(target_bars), membership=membership),
        *tuple(
            _asset(f"00000{index}", universe="ashare", membership=membership)
            for index in range(1, 6)
        ),
    )

    result = screen_dual_universe(
        items,
        theme_percentile_overrides={"fine_theme:passive_components": (1.0, 1.0, 1.0)},
    )
    target = next(
        row
        for row in result.observations
        if row.asset_code == "000636" and row.formula_id == BREAKOUT_V2
    )
    facts = dict(target.gate_facts)

    assert target.qualifies is True
    assert target.exclusion_reasons == ()
    assert facts["batch_qualifier_count"] == 1
    assert facts["batch_required_count"] == 1
    assert facts["batch_policy"] == "hot_theme_core_leader_v1"


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
    ashare_legacy_view = {
        key: value for key, value in ashare_view.items() if key[1] != "low_base_catchup_proxy_v1"
    }
    assert etf_view == ashare_legacy_view
    assert all(key[1] != "low_base_catchup_proxy_v1" for key in etf_view)
    assert any(key[1] == "low_base_catchup_proxy_v1" for key in ashare_view)


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


def test_base_launch_uses_relative_volume_without_inheriting_breakout_peak_gate() -> None:
    bars = list(_bars())
    bars[-40] = replace(bars[-40], volume=10_000.0)
    bars[-1] = replace(bars[-1], volume=2_000.0)
    items = tuple(_asset(f"51000{index}", bars=tuple(bars)) for index in range(1, 7))

    result = screen_dual_universe(items)
    base_rows = [row for row in result.observations if row.formula_id == BASE_LAUNCH_V2]
    breakout_rows = [row for row in result.observations if row.formula_id == BREAKOUT_V2]

    assert base_rows
    assert all(dict(row.gate_facts)["base_volume_confirmed"] is True for row in base_rows)
    assert all("volume_peak_gate_failed" not in row.exclusion_reasons for row in base_rows)
    assert all("volume_peak_gate_failed" in row.exclusion_reasons for row in breakout_rows)


def test_base_launch_amount_confirmation_uses_own_prior_20_sessions() -> None:
    bars = list(_bars())
    bars[-1] = replace(
        bars[-1],
        volume=bars[-2].volume,
        amount=max(bar.amount for bar in bars[-21:-1]) * 2,
    )
    items = tuple(_asset(f"51000{index}", bars=tuple(bars)) for index in range(1, 7))

    result = screen_dual_universe(items)
    base_rows = [row for row in result.observations if row.formula_id == BASE_LAUNCH_V2]

    assert base_rows
    for row in base_rows:
        facts = dict(row.gate_facts)
        assert facts["relative_volume_20"] < 1.20
        assert facts["amount_vs_prior_20_percentile"] == 1.0
        assert facts["base_volume_confirmed"] is True


def test_rare_earth_early_rotation_is_non_actionable_turning_watch() -> None:
    def trend_bars(*, slope: float, amount_scale: float) -> tuple[V2AdjustedBar, ...]:
        rows = []
        for index, bar in enumerate(_bars()):
            close = 100 + index * slope
            rows.append(
                replace(
                    bar,
                    adjusted_open=close - 0.05,
                    adjusted_high=close + 0.20,
                    adjusted_low=close - 0.20,
                    adjusted_close=close,
                    amount=bar.amount * amount_scale,
                )
            )
        return tuple(rows)

    rare_membership = _group_membership("fine_theme:rare_earth", "稀土/稀土永磁")
    fallback_membership = _group_membership("sw1:other", "其他行业")
    rare = tuple(
        _asset(
            f"51000{index}",
            bars=trend_bars(slope=0.04 + index * 0.01, amount_scale=float(index)),
            membership=rare_membership,
        )
        for index in range(1, 7)
    )
    fallback = tuple(
        _asset(
            f"52000{index}",
            bars=trend_bars(slope=-0.01, amount_scale=float(index)),
            membership=fallback_membership,
        )
        for index in range(1, 7)
    )

    result = screen_dual_universe((*rare, *fallback))
    target = next(
        row
        for row in result.observations
        if row.asset_code == "510006" and row.formula_id == BASE_LAUNCH_V2
    )
    facts = dict(target.gate_facts)

    assert target.state == "turning_watch"
    assert target.qualifies is False
    assert target.score is None
    assert "ma5_ma10_cross_missing" in target.exclusion_reasons
    assert "ma5_ma10_cross_missing" in str(facts["turning_watch_formal_blockers"])
    assert facts["theme_hierarchy_level"] == "fine_theme"
    assert facts["theme_resolution_mode"] == "fine_theme_pit"


def test_compact_stage_hash_and_theme_overrides_are_order_invariant() -> None:
    items = tuple(_asset(f"51000{index}") for index in range(1, 7))
    forward = tuple(build_v2_staged_asset_feature(item) for item in items)
    reverse = tuple(build_v2_staged_asset_feature(item) for item in reversed(items))

    assert staged_v2_input_hash(forward) == staged_v2_input_hash(reverse)
    assert staged_theme_percentile_overrides(forward) == staged_theme_percentile_overrides(reverse)
