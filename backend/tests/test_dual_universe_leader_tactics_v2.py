from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    FORMER_LEADER_REPAIR_V2,
    LOW_BASE_SOURCE_CAPTURES,
    POST_CLOSE_WATCHLIST_MODE,
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
        "low_base_catchup_proxy_v1",
    )
    assert all(
        "abs(adjusted_close-adjusted_MA20)/adjusted_ATR20" in item.formula_text
        for item in V2_FORMULAS[1:]
    )
    validate_runtime_contract()
    with pytest.raises(V2ContractError):
        validate_runtime_contract(formula_ids=(BREAKOUT_V2,))


def test_user_capture_keeps_unknown_publication_and_actual_receipt_time() -> None:
    capture = LOW_BASE_SOURCE_CAPTURES[0]

    assert capture.publication_status == "unknown"
    assert capture.received_at == datetime(2026, 8, 17, 0, 0)


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


def test_post_close_identity_cutoff_does_not_relax_market_data_cutoff() -> None:
    item = _asset("510001")
    identity_cutoff = item.source_cutoff + timedelta(hours=3)
    membership = replace(
        item.membership,
        observed_at=item.source_cutoff + timedelta(hours=2),
        fact_hash="",
    )
    membership = replace(
        membership,
        fact_hash=stable_contract_hash(membership.canonical_payload()),
    )

    accepted = screen_dual_universe(
        (replace(item, membership=membership, identity_cutoff=identity_cutoff),)
    )
    rejected = screen_dual_universe((replace(item, membership=membership),))
    late_bar = replace(item.bars[-1], observed_at=item.source_cutoff + timedelta(hours=1))
    late_market_data = screen_dual_universe(
        (
            replace(
                item,
                bars=(*item.bars[:-1], late_bar),
                membership=membership,
                identity_cutoff=identity_cutoff,
            ),
        )
    )

    assert accepted.data_receipt_cutoff == identity_cutoff
    assert all(
        "membership_received_after_cutoff" not in row.exclusion_reasons
        for row in accepted.observations
    )
    assert all(
        "membership_received_after_cutoff" in row.exclusion_reasons
        for row in rejected.observations
    )
    assert all(
        "adjusted_bar_received_after_cutoff" in row.exclusion_reasons
        for row in late_market_data.observations
    )


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


def test_ashare_sentiment_overlay_preserves_raw_screen_and_is_absent_from_etf() -> None:
    etf_items = tuple(_asset(f"5100{index:02d}") for index in range(6))
    ashare_items = tuple(
        replace(item, universe="ashare", asset_code=f"0000{index:02d}")
        for index, item in enumerate(etf_items)
    )

    etf_result = screen_dual_universe(etf_items)
    ashare_result = screen_dual_universe(ashare_items)
    etf_rows = sorted(etf_result.observations, key=lambda row: (row.formula_id, row.asset_code))
    ashare_rows = sorted(
        ashare_result.observations,
        key=lambda row: (row.formula_id, row.asset_code),
    )

    ashare_rows = [
        row for row in ashare_rows if row.formula_id != "low_base_catchup_proxy_v1"
    ]
    assert len(etf_rows) == len(ashare_rows)
    snapshot_count = 0
    for etf_row, ashare_row in zip(etf_rows, ashare_rows, strict=True):
        assert etf_row.formula_id == ashare_row.formula_id
        assert etf_row.qualifies == ashare_row.qualifies
        assert etf_row.score == ashare_row.score
        assert etf_row.state == ashare_row.state
        assert etf_row.exclusion_reasons == ashare_row.exclusion_reasons
        assert "sentiment_risk" not in dict(etf_row.gate_facts)
        facts = dict(ashare_row.gate_facts)
        reference = facts["sentiment_risk_ref"]
        assert reference["state"] == "unavailable"
        assert reference["unavailable_reason"] == "sentiment_risk_insufficient_hot_themes"
        if "sentiment_risk_snapshot" in facts:
            snapshot_count += 1
            assert facts["sentiment_risk_snapshot"]["new_entry_allowed"] is False
    assert snapshot_count == 1


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


def test_post_close_watchlist_accepts_current_membership_but_stays_out_of_replay() -> None:
    signal_item = _asset("510099")
    decision_date = signal_item.signal_date + timedelta(days=1)
    late_draft = replace(
        _membership(),
        effective_from=decision_date,
        observed_at=datetime.combine(decision_date, time(9)),
        fact_hash="",
    )
    late_membership = replace(
        late_draft,
        fact_hash=stable_contract_hash(late_draft.canonical_payload()),
    )
    session_pit = replace(
        signal_item,
        membership=late_membership,
        source_cutoff=datetime.combine(decision_date, time(10)),
    )
    session_result = screen_dual_universe((session_pit,))
    assert "membership_effective_after_signal" in {
        reason
        for row in session_result.observations
        for reason in row.exclusion_reasons
    }

    watchlist = replace(
        session_pit,
        decision_mode=POST_CLOSE_WATCHLIST_MODE,
        membership_evaluation_date=decision_date,
        next_eligible_date=decision_date + timedelta(days=2),
    )
    watchlist_result = screen_dual_universe((watchlist,))
    assert "membership_effective_after_signal" not in {
        reason
        for row in watchlist_result.observations
        for reason in row.exclusion_reasons
    }
    facts = dict(watchlist_result.observations[0].gate_facts)
    assert facts["feature_trade_date"] == signal_item.signal_date.isoformat()
    assert facts["membership_evaluation_date"] == decision_date.isoformat()
    assert facts["historical_validation_eligible"] is False
def test_post_close_lifecycle_ignores_bars_before_next_eligible_session() -> None:
    signal_date = date(2026, 8, 7)
    decision_date = date(2026, 8, 8)
    next_eligible_date = date(2026, 8, 10)
    trade_dates = (
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
        signal_date,
        decision_date,
        next_eligible_date,
    )
    closes = (98.0, 99.0, 99.5, 100.0, 100.0, 120.0, 95.0)
    bars = tuple(
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
            revision_id=f"post-close-{trade_date.isoformat()}",
        )
        for trade_date, close in zip(trade_dates, closes, strict=True)
    )
    observation = V2CandidateObservation(
        universe="ashare",
        asset_code="000001",
        asset_name="sample",
        signal_date=signal_date,
        formula_id=BREAKOUT_V2,
        state=STATE_PREPARING,
        availability="available",
        qualifies=True,
        score=0.9,
        gate_facts=(
            ("decision_mode", POST_CLOSE_WATCHLIST_MODE),
            ("membership_evaluation_date", decision_date.isoformat()),
            ("next_eligible_date", next_eligible_date.isoformat()),
        ),
        exclusion_reasons=(),
        source_cutoff=datetime.combine(decision_date, time(16)),
        theme="AI",
        sector="technology",
        tracked_index=None,
        clone_group=None,
        issuer=None,
        feature_hash="f" * 64,
    )
    transitions = derive_lifecycle(
        observation=observation,
        signal_bars=bars,
        evaluation_cutoff=datetime.combine(next_eligible_date, time(16)),
        visible_through=next_eligible_date,
    )
    assert transitions[0].transition_date == decision_date
    assert transitions[0].reason == "formula_passed_for_post_close_watchlist"
    assert all(item.to_state != STATE_CONFIRMED for item in transitions)

def test_single_bar_asset_is_excluded_without_index_error() -> None:
    item = _asset("510098")
    one_bar = replace(item, bars=(item.bars[-1],))
    result = screen_dual_universe((one_bar,))
    reasons = {
        reason
        for row in result.observations
        for reason in row.exclusion_reasons
    }
    assert "insufficient_adjusted_history" in reasons
    assert "positive_stabilization_failed" in reasons
