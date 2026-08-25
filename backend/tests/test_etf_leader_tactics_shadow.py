from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    BREAKOUT_HISTORY_SESSIONS,
    CYCLE_ROUTED_LEADER_CANDIDATE,
    FORMER_LEADER_REPAIR_CANDIDATE,
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    FROZEN_LEADER_CANDIDATES,
    HYPOTHESIS_REGISTRY_VERSION,
    LEADER_BREAKOUT_CANDIDATE,
    LEADER_CANDIDATE_IDS,
    LEADER_EXPERIMENT_FAMILY,
    LEADER_HYPOTHESIS_REGISTRY,
    REPAIR_HISTORY_SESSIONS,
    FrozenLeaderCandidate,
    LeaderAdjustedBar,
    LeaderPitAssetInput,
    LeaderTacticsContractError,
    build_leader_experiment_manifest,
    build_leader_feature_panel,
    freeze_leader_candidate_registry,
    percentile_ranks,
    validate_leader_pit_input,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
    freeze_ranking_candidate_registry,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _observed(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(15, 0))


def _breakout_bars(
    *,
    start: date,
    slope: float,
    final_breakout: bool,
    turnover: float,
    count: int = REPAIR_HISTORY_SESSIONS,
) -> tuple[LeaderAdjustedBar, ...]:
    bars: list[LeaderAdjustedBar] = []
    for index in range(count):
        trade_date = start + timedelta(days=index)
        close = 50.0 + slope * index
        if final_breakout and index == count - 1:
            close += 3.0
        high = close + 0.10
        low = close - 0.20
        volume = 1_000.0 + index
        if final_breakout and index == count - 1:
            volume = 10_000.0
        bars.append(
            LeaderAdjustedBar(
                trade_date=trade_date,
                adjusted_open=close - 0.05,
                adjusted_high=high,
                adjusted_low=low,
                adjusted_close=close,
                volume=volume,
                turnover=turnover,
                observed_at=_observed(trade_date),
            )
        )
    return tuple(bars)


def _repair_bars(
    *,
    start: date,
    prior_leader: bool,
    count: int = REPAIR_HISTORY_SESSIONS,
) -> tuple[LeaderAdjustedBar, ...]:
    bars: list[LeaderAdjustedBar] = []
    for index in range(count):
        trade_date = start + timedelta(days=index)
        if prior_leader:
            if index <= 70:
                close = 100.0 + 100.0 * index / 70.0
            elif index <= 140:
                close = 200.0 - 80.0 * (index - 70) / 70.0
            else:
                close = 119.5 + 0.01 * (index - 140)
            half_range = 0.20 if index >= count - 5 else 2.0
        else:
            close = 100.0 + 0.01 * index
            half_range = 1.0
        bars.append(
            LeaderAdjustedBar(
                trade_date=trade_date,
                adjusted_open=close - 0.05,
                adjusted_high=close + half_range,
                adjusted_low=close - half_range,
                adjusted_close=close,
                volume=1_000.0,
                turnover=1_000_000.0,
                observed_at=_observed(trade_date),
            )
        )
    return tuple(bars)


def _input(
    *,
    code: str,
    bars: tuple[LeaderAdjustedBar, ...],
    peer_group: str,
    sector_score: float,
    regime: str = "risk_on",
    clone_group: str | None = None,
) -> LeaderPitAssetInput:
    signal_date = bars[-1].trade_date
    cutoff = datetime.combine(signal_date, time(15, 30))
    return LeaderPitAssetInput(
        asset_code=code,
        signal_date=signal_date,
        source_cutoff=cutoff,
        baseline_score=70.0,
        bars=bars,
        historical_member=True,
        membership_effective_date=bars[0].trade_date,
        membership_observed_at=_observed(bars[0].trade_date),
        peer_group=peer_group,
        peer_mapping_effective_date=bars[0].trade_date,
        peer_mapping_observed_at=_observed(bars[0].trade_date),
        peer_mapping_kind="historical_pit",
        sector_trend_score=sector_score,
        sector_trend_as_of=signal_date,
        sector_trend_observed_at=_observed(signal_date),
        sector_trend_contract_hash=HASH_A,
        market_regime=regime,
        market_regime_as_of=signal_date,
        market_regime_observed_at=_observed(signal_date),
        market_regime_contract_hash=REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
        market_regime_status="available",
        clone_group=clone_group,
        tracked_index=f"index-{peer_group}",
        issuer=f"issuer-{code[-1]}",
        theme=peer_group,
        sector=peer_group,
    )


def _breakout_panel_inputs(*, top_two_are_clones: bool = False) -> list[LeaderPitAssetInput]:
    start = date(2026, 1, 1)
    items: list[LeaderPitAssetInput] = []
    for sector_index, sector_score in enumerate((10.0, 20.0, 30.0), start=1):
        for peer_index in range(5):
            is_hot_group = sector_index == 3
            is_target = is_hot_group and peer_index == 4
            is_second_clone = top_two_are_clones and is_hot_group and peer_index == 3
            slope = 0.02 * (peer_index + 1)
            if is_second_clone:
                slope = 0.10
            final_breakout = is_target or is_second_clone
            turnover = 1_000_000.0 * (peer_index + 1)
            code = f"51{sector_index:02d}{peer_index:02d}"
            items.append(
                _input(
                    code=code,
                    bars=_breakout_bars(
                        start=start,
                        slope=slope,
                        final_breakout=final_breakout,
                        turnover=turnover,
                    ),
                    peer_group=f"sector-{sector_index}",
                    sector_score=sector_score,
                    clone_group=(
                        "hot-clone"
                        if top_two_are_clones and final_breakout
                        else code
                    ),
                )
            )
    return items


def _repair_panel_inputs(*, regime: str = "neutral") -> list[LeaderPitAssetInput]:
    start = date(2026, 1, 1)
    return [
        _input(
            code=f"52000{index}",
            bars=_repair_bars(start=start, prior_leader=index == 4),
            peer_group="repair-sector",
            sector_score=20.0,
            regime=regime,
        )
        for index in range(5)
    ]


def _observation(panel, candidate_id: str, code: str):
    return next(
        item
        for item in panel.observations
        if item.candidate_id == candidate_id and item.asset_code == code
    )


def test_hypothesis_registry_records_five_sources_and_non_equivalence() -> None:
    registry = LEADER_HYPOTHESIS_REGISTRY

    assert registry.version == HYPOTHESIS_REGISTRY_VERSION
    assert len(registry.articles) == 5
    assert all(len(item.captured_content_hash) == 64 for item in registry.articles)
    proprietary = next(
        item
        for item in registry.statements
        if item.disclosure_state == "unavailable_proprietary"
    )
    assert proprietary.proxy_id is None
    assert "not the source author's proprietary signal" in registry.non_equivalence_notice
    registry.validate()


def test_frozen_registry_rejects_recomputed_runtime_parameter_change() -> None:
    original = FROZEN_LEADER_CANDIDATES[0]
    changed_draft = replace(
        original,
        parameter_items=(
            *original.parameter_items[:-1],
            ("minimum_peer_count", 4),
        ),
        manifest_hash="pending",
    )
    changed = replace(
        changed_draft,
        manifest_hash=stable_contract_hash(changed_draft.canonical_payload()),
    )

    with pytest.raises(LeaderTacticsContractError, match="not canonical"):
        freeze_leader_candidate_registry(
            (changed, *FROZEN_LEADER_CANDIDATES[1:])
        )


def test_leader_manifest_binds_all_identities_without_mutating_production_registry() -> None:
    production_before = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    research = daily_reconstructable_manifest()
    manifest = build_leader_experiment_manifest(
        baseline_contract_id=research.contract_id,
        baseline_contract_hash=research.manifest_hash,
        split_contract_hash=HASH_A,
        code_version="leader-shadow-test-v1",
        holdout_identity_hash=HASH_B,
        production_candidate_registry_hash=production_before.registry_hash,
    )
    production_after = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)

    assert manifest.experiment_family == LEADER_EXPERIMENT_FAMILY
    assert manifest.candidate_registry_hash == FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash
    assert production_before == production_after
    assert tuple(production_after.by_id) != LEADER_CANDIDATE_IDS


def test_percentile_ranks_average_ties_and_are_deterministic() -> None:
    values = {"b": 2.0, "a": 1.0, "d": 2.0, "c": 3.0}

    assert percentile_ranks(values) == {
        "a": 0.0,
        "b": 0.5,
        "d": 0.5,
        "c": 1.0,
    }
    assert percentile_ranks(dict(reversed(tuple(values.items())))) == percentile_ranks(
        values
    )
    assert percentile_ranks(values, reverse=True)["a"] == 1.0


def test_breakout_proxy_uses_exact_adjusted_gates_and_equal_weight_score() -> None:
    panel = build_leader_feature_panel(_breakout_panel_inputs())
    target = _observation(panel, LEADER_BREAKOUT_CANDIDATE, "510304")
    components = dict(target.components)

    assert len(next(iter(_breakout_panel_inputs())).bars) >= BREAKOUT_HISTORY_SESSIONS
    assert target.availability == "available"
    assert target.qualifies is True
    assert target.score == pytest.approx(1.0)
    assert components["adjusted_ma5"] > components["adjusted_ma10"] > components["adjusted_ma20"]
    assert components["adjusted_close"] > components["preceding_20_adjusted_high"]
    assert components["current_volume"] == components["latest_120_volume_max"]
    assert components["sector_trend_percentile"] == 1.0
    assert components["peer_return20_percentile"] == 1.0
    assert components["peer_turnover20_percentile"] == 1.0
    assert components["entry_quality_state"] == "overextended"
    assert "atr_extension_excessive" in components["entry_quality_reason_codes"]


def test_repair_proxy_uses_prior_leadership_drawdown_compression_and_symmetric_atr() -> None:
    panel = build_leader_feature_panel(_repair_panel_inputs())
    target = _observation(panel, FORMER_LEADER_REPAIR_CANDIDATE, "520004")
    components = dict(target.components)

    assert len(next(iter(_repair_panel_inputs())).bars) == REPAIR_HISTORY_SESSIONS
    assert target.availability == "available"
    assert target.qualifies is True
    assert components["prior_leadership_percentile"] >= 0.8
    assert -0.50 <= components["drawdown_120"] <= -0.30
    assert components["adjusted_close"] > components["adjusted_open"]
    assert components["adjusted_close"] > components["prior_adjusted_close"]
    assert components["atr5_atr20_ratio"] <= 0.75
    expected = abs(
        components["adjusted_close"] - components["adjusted_ma20"]
    ) / components["adjusted_atr20"]
    assert components["overextension_atr"] == pytest.approx(expected)
    assert components["overextension_atr"] <= 1.0


def test_cycle_router_uses_risk_on_and_neutral_and_fails_closed() -> None:
    breakout_panel = build_leader_feature_panel(_breakout_panel_inputs())
    breakout = _observation(
        breakout_panel,
        CYCLE_ROUTED_LEADER_CANDIDATE,
        "510304",
    )
    repair_panel = build_leader_feature_panel(_repair_panel_inputs(regime="neutral"))
    repair = _observation(
        repair_panel,
        CYCLE_ROUTED_LEADER_CANDIDATE,
        "520004",
    )
    defensive_panel = build_leader_feature_panel(
        _repair_panel_inputs(regime="defensive")
    )
    defensive = _observation(
        defensive_panel,
        CYCLE_ROUTED_LEADER_CANDIDATE,
        "520004",
    )

    assert dict(breakout.components)["routed_candidate_id"] == LEADER_BREAKOUT_CANDIDATE
    assert breakout.qualifies is True
    assert dict(repair.components)["routed_candidate_id"] == FORMER_LEADER_REPAIR_CANDIDATE
    assert repair.qualifies is True
    assert defensive.availability == "available"
    assert defensive.qualifies is False
    assert defensive.gate_reasons == ("market_regime_defensive_no_selection",)


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (
            lambda item: replace(
                item,
                bars=(
                    *item.bars[:-1],
                    replace(item.bars[-1], data_provider="sina"),
                ),
            ),
            "forbidden_raw_price_provider",
        ),
        (
            lambda item: replace(
                item,
                bars=(
                    *item.bars[:-1],
                    replace(
                        item.bars[-1],
                        observed_at=item.source_cutoff + timedelta(minutes=1),
                    ),
                ),
            ),
            "adjusted_bar_received_after_cutoff",
        ),
        (
            lambda item: replace(item, peer_mapping_kind="current_only"),
            "peer_mapping_not_point_in_time",
        ),
        (
            lambda item: replace(
                item,
                membership_effective_date=item.signal_date + timedelta(days=1),
            ),
            "membership_effective_after_signal",
        ),
        (
            lambda item: replace(
                item,
                market_regime_observed_at=item.source_cutoff + timedelta(minutes=1),
            ),
            "market_regime_received_after_cutoff",
        ),
    ],
)
def test_pit_adapter_rejects_raw_late_current_or_future_facts(mutator, reason) -> None:
    item = _breakout_panel_inputs()[0]
    changed = mutator(item)

    reasons = validate_leader_pit_input(
        changed,
        required_history=BREAKOUT_HISTORY_SESSIONS,
        include_regime=True,
    )

    assert reason in reasons


def test_clone_policy_keeps_only_more_liquid_representative() -> None:
    panel = build_leader_feature_panel(_breakout_panel_inputs(top_two_are_clones=True))
    lower_liquidity = _observation(panel, LEADER_BREAKOUT_CANDIDATE, "510303")
    higher_liquidity = _observation(panel, LEADER_BREAKOUT_CANDIDATE, "510304")

    assert lower_liquidity.qualifies is False
    assert "clone_not_representative" in lower_liquidity.gate_reasons
    assert higher_liquidity.qualifies is True
    assert higher_liquidity.clone_group == "hot-clone"
    assert higher_liquidity.tracked_index == "index-sector-3"
    assert higher_liquidity.issuer is not None
    assert higher_liquidity.theme == "sector-3"
    assert higher_liquidity.sector == "sector-3"


def test_candidate_contract_cannot_be_constructed_with_undeclared_identity() -> None:
    draft = FrozenLeaderCandidate(
        candidate_id="retuned_after_outcomes",
        required_history_sessions=120,
        formula="outcome_grid_search",
        missing_value_rule="fail_closed_no_padding",
        parameter_items=(("top_n", 3),),
        manifest_hash=HASH_C,
    )

    with pytest.raises(LeaderTacticsContractError, match="undeclared"):
        draft.validate()
