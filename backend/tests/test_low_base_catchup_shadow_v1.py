from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    LOW_BASE_CATCHUP_V1,
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
    derive_lifecycle,
    screen_dual_universe,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_replay import (
    evaluate_low_base_ashare_forward_outcomes,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import ForwardAdjustedClose


def _membership() -> V2PITMembership:
    draft = V2PITMembership(
        group_id="fine_theme:innovation_drug",
        effective_from=date(2026, 4, 1),
        effective_to=None,
        observed_at=datetime(2026, 4, 1, 16),
        mapping_kind="historical_pit",
        taxonomy_version="eastmoney.concept.current_v1",
        theme="创新药",
        sector="医药生物",
        tracked_index=None,
        clone_group=None,
        issuer=None,
        fact_hash="",
        hierarchy_level="fine_theme",
        normalized_theme_key="innovation_drug",
        resolution_mode="fine_theme_pit",
    )
    return replace(draft, fact_hash=stable_contract_hash(draft.canonical_payload()))


def _bars(
    *,
    isolated_spike: bool = False,
    overextended: bool = False,
    extended_watch: bool = False,
    missing_turning: bool = False,
):
    start = date(2026, 4, 1)
    rows: list[V2AdjustedBar] = []
    for index in range(129):
        if index < 90:
            close = 10.0 - 5.0 * index / 89
        elif index < 124:
            close = 5.0
        else:
            close = (5.02, 5.05, 5.08, 5.10, 5.30)[index - 124]
        if overextended and index == 128:
            close = 7.0
        if extended_watch and index == 128:
            close = 5.55
        if missing_turning and index == 128:
            close = 5.09
        volume = 150.0 if index >= 124 else 100.0
        if isolated_spike:
            volume = 300.0 if index == 128 else 100.0
        session = start + timedelta(days=index)
        rows.append(
            V2AdjustedBar(
                trade_date=session,
                adjusted_open=close - 0.05,
                adjusted_high=close + 0.15,
                adjusted_low=close - 0.15,
                adjusted_close=close,
                volume=volume,
                amount=volume * close,
                turnover=volume * close,
                observed_at=datetime.combine(session, time(15)),
                provider="eastmoney",
                adjustment_version="total-return-v1",
                revision_id=f"rev-{index}",
            )
        )
    return tuple(rows)


def _asset(code: str, **bar_options: bool) -> V2AssetInput:
    bars = _bars(**bar_options)
    return V2AssetInput(
        universe="ashare",
        asset_code=code,
        asset_name=f"创新药 {code}",
        signal_date=bars[-1].trade_date,
        source_cutoff=datetime.combine(bars[-1].trade_date, time(15, 30)),
        bars=bars,
        membership=_membership(),
    )


def _screen(target: V2AssetInput):
    peers = (
        target,
        *(
            replace(
                target,
                asset_code=f"60000{index}",
                asset_name=f"同主题可比股 {index}",
            )
            for index in range(1, 6)
        ),
    )
    return screen_dual_universe(
        peers,
        theme_percentile_overrides={"fine_theme:innovation_drug": (1.0, 1.0, 1.0)},
    )


def _target(result, code: str = "002437"):
    return next(
        row
        for row in result.observations
        if row.asset_code == code and row.formula_id == LOW_BASE_CATCHUP_V1
    )


def _memory_bars(
    *,
    signal_date: date,
    signal_close: float = 5.75,
    stale_setup: bool = False,
) -> tuple[V2AdjustedBar, ...]:
    """Build a bounded setup-then-launch path without using future outcomes."""

    count = 135 if stale_setup else 129
    start = signal_date - timedelta(days=count - 1)
    rows: list[V2AdjustedBar] = []
    for index in range(count):
        if index == 0:
            close = 10.0
        elif index < 110:
            close = 5.4
        elif index < 120:
            close = 5.0
        else:
            progress = (index - 119) / (count - 120)
            close = 5.0 + 0.4 * progress
        if index == count - 1:
            close = signal_close
        volume = 150.0 if 115 <= index <= 119 else 100.0
        session = start + timedelta(days=index)
        rows.append(
            V2AdjustedBar(
                trade_date=session,
                adjusted_open=close - 0.05,
                adjusted_high=close + 0.25,
                adjusted_low=close - 0.25,
                adjusted_close=close,
                volume=volume,
                amount=volume * close,
                turnover=volume * close,
                observed_at=datetime.combine(session, time(15)),
                provider="eastmoney",
                adjustment_version="total-return-v1",
                revision_id=f"memory-{index}",
            )
        )
    return tuple(rows)


def _memory_asset(
    code: str,
    *,
    signal_date: date,
    signal_close: float = 5.75,
    stale_setup: bool = False,
) -> V2AssetInput:
    bars = _memory_bars(
        signal_date=signal_date,
        signal_close=signal_close,
        stale_setup=stale_setup,
    )
    return V2AssetInput(
        universe="ashare",
        asset_code=code,
        asset_name=code,
        signal_date=signal_date,
        source_cutoff=datetime.combine(signal_date, time(15, 30)),
        bars=bars,
        membership=_membership(),
    )


def test_complete_low_base_pattern_is_actionable_preparing() -> None:
    row = _target(_screen(_asset("002437")))
    facts = dict(row.gate_facts)

    assert row.qualifies is True
    assert row.state == "preparing"
    assert facts["entry_status"] == "actionable"
    assert facts["range_position_120"] <= 0.35
    assert facts["drawdown_120"] <= -0.25
    assert facts["volume_expansion_5v20"] >= 1.30
    assert facts["relative_volume_confirmed_days"] >= 2


def test_setup_memory_can_launch_after_price_leaves_same_day_low_base() -> None:
    asset = _memory_asset("000636", signal_date=date(2026, 7, 29))
    row = _target(_screen(asset), "000636")
    facts = dict(row.gate_facts)

    assert row.qualifies is True
    assert row.state == "preparing"
    assert facts["launch_signal"] is True
    assert facts["entry_status"] == "actionable"
    assert facts["range_position_120"] > 0.40
    assert facts["setup_range_position_120"] <= 0.40
    assert facts["setup_drawdown_120"] <= -0.25
    assert facts["volume_memory_expansion_5v20"] >= 1.30
    assert facts["setup_drawdown_evidence_date"] < asset.signal_date.isoformat()
    assert facts["volume_memory_evidence_date"] < asset.signal_date.isoformat()


def test_stale_setup_cannot_be_revived_by_a_later_breakout() -> None:
    asset = _memory_asset(
        "000636",
        signal_date=date(2026, 8, 5),
        stale_setup=True,
    )
    row = _target(_screen(asset), "000636")

    assert row.qualifies is False
    assert row.state == "invalidated"
    assert "low_base_drawdown_failed" in row.exclusion_reasons
    assert "low_base_volume_expansion_failed" in row.exclusion_reasons


def test_extended_launch_is_preserved_but_not_actionable() -> None:
    asset = _memory_asset(
        "002131",
        signal_date=date(2026, 7, 31),
        signal_close=6.20,
    )
    row = _target(_screen(asset), "002131")
    facts = dict(row.gate_facts)

    assert row.qualifies is False
    assert row.state == "preparing"
    assert facts["launch_signal"] is True
    assert facts["extension_band"] == "extended_watch"
    assert facts["entry_status"] == "watch"
    assert "low_base_atr_extended_watch" in row.exclusion_reasons


def test_three_known_exemplars_keep_distinct_signal_and_entry_semantics() -> None:
    fenghua = _target(
        _screen(_memory_asset("000636", signal_date=date(2026, 7, 29))),
        "000636",
    )
    yuheng = _target(
        _screen(_memory_asset("002437", signal_date=date(2026, 8, 7))),
        "002437",
    )
    leo = _target(
        _screen(
            _memory_asset(
                "002131",
                signal_date=date(2026, 7, 31),
                signal_close=6.20,
            )
        ),
        "002131",
    )

    assert fenghua.signal_date == date(2026, 7, 29)
    assert yuheng.signal_date == date(2026, 8, 7)
    assert leo.signal_date == date(2026, 7, 31)
    assert dict(fenghua.gate_facts)["entry_status"] == "actionable"
    assert dict(yuheng.gate_facts)["entry_status"] == "actionable"
    assert dict(leo.gate_facts)["entry_status"] == "watch"
    assert all(
        dict(row.gate_facts)["launch_signal"] is True
        for row in (fenghua, yuheng, leo)
    )


def test_one_isolated_volume_spike_cannot_confirm() -> None:
    row = _target(_screen(_asset("002437", isolated_spike=True)))
    facts = dict(row.gate_facts)

    assert row.qualifies is False
    assert row.state == "turning_watch"
    assert facts["entry_status"] == "watch"
    assert facts["single_day_volume_watch"] is True
    assert "low_base_repeated_volume_failed" in row.exclusion_reasons
    assert "low_base_single_day_volume_watch" in row.exclusion_reasons


def test_extended_candidate_is_visible_but_not_actionable() -> None:
    row = _target(_screen(_asset("002437", overextended=True)))
    facts = dict(row.gate_facts)

    assert row.qualifies is False
    assert row.state == "preparing"
    assert facts["launch_signal"] is True
    assert facts["entry_status"] == "overextended"
    assert "low_base_return_5_overextended" in row.exclusion_reasons


def test_pre_receipt_replay_is_not_promoted_to_pit_validation() -> None:
    row = _target(_screen(_asset("002437")))
    facts = dict(row.gate_facts)

    assert facts["retrospective_hypothesis_replay"] is True
    assert facts["historical_validation_eligible"] is False
    assert facts["hypothesis_received_at"] == "2026-08-17T00:00:00"


def test_low_base_formula_is_absent_from_etf_universe() -> None:
    etf_items = tuple(replace(_asset(f"51000{index}"), universe="etf") for index in range(1, 7))
    result = screen_dual_universe(etf_items)

    assert all(row.formula_id != LOW_BASE_CATCHUP_V1 for row in result.observations)


def _future_bars(signal_bar: V2AdjustedBar, count: int, *, close: float):
    return tuple(
        replace(
            signal_bar,
            trade_date=signal_bar.trade_date + timedelta(days=index),
            adjusted_open=close - 0.05,
            adjusted_high=close + 0.15,
            adjusted_low=close - 0.15,
            adjusted_close=close,
            observed_at=datetime.combine(
                signal_bar.trade_date + timedelta(days=index), time(15)
            ),
            revision_id=f"future-{index}",
        )
        for index in range(1, count + 1)
    )


def test_preparing_expires_after_three_sessions_without_confirmation() -> None:
    asset = _asset("002437")
    row = _target(_screen(asset))
    future = _future_bars(asset.bars[-1], 3, close=5.30)

    transitions = derive_lifecycle(
        observation=row,
        signal_bars=(*asset.bars, *future),
        evaluation_cutoff=future[-1].observed_at,
    )

    assert transitions[-1].to_state == "invalidated"
    assert transitions[-1].reason == "confirmation_window_expired"


def test_turning_watch_can_prepare_then_confirm_in_separate_sessions() -> None:
    asset = _asset("002437", missing_turning=True)
    row = _target(_screen(asset))
    signal = asset.bars[-1]
    first = replace(
        _future_bars(signal, 1, close=5.30)[0],
        adjusted_high=5.40,
    )
    second = replace(
        _future_bars(signal, 2, close=5.45)[1],
        adjusted_high=5.55,
    )

    assert row.state == "turning_watch"
    assert dict(row.gate_facts)["entry_status"] == "watch"
    transitions = derive_lifecycle(
        observation=row,
        signal_bars=(*asset.bars, first, second),
        evaluation_cutoff=second.observed_at,
    )

    assert [item.to_state for item in transitions] == [
        "turning_watch",
        "preparing",
        "confirmed",
    ]


def test_low_base_forward_outcomes_include_twenty_sessions() -> None:
    result = _screen(_asset("002437"))
    start = result.signal_date
    sessions = tuple(start + timedelta(days=index) for index in range(22))
    closes = tuple(
        ForwardAdjustedClose(
            asset_code=code,
            session_date=session,
            adjusted_close=5.30 * (1.0 + 0.01 * index),
            price_basis="total_return_adjusted",
            decision_eligible=True,
            provider="eastmoney",
            adjustment_version="total-return-v1",
            source_hash=f"source-{code}-{index}",
        )
        for code in ("002437", "600001", "600002", "600003", "600004", "600005")
        for index, session in enumerate(sessions)
    )

    bundle = evaluate_low_base_ashare_forward_outcomes(
        result=result,
        replay_run_key="low-base-test",
        trading_sessions=sessions,
        adjusted_closes=closes,
    )

    assert bundle.horizons == (1, 3, 5, 10, 20)
    assert {item.horizon_sessions for item in bundle.outcomes} == {1, 3, 5, 10, 20}
