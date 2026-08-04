from __future__ import annotations

from datetime import date, datetime, timedelta

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    V2CandidateObservation,
    V2ScreenResult,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_replay import (
    bootstrap_v2_primary,
    evaluate_v2_etf_primary,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import ForwardAdjustedClose


def _observation(code: str, score: float) -> V2CandidateObservation:
    return V2CandidateObservation(
        universe="etf",
        asset_code=code,
        asset_name=f"ETF {code}",
        signal_date=date(2026, 8, 4),
        formula_id=BREAKOUT_V2,
        state="preparing",
        availability="available",
        qualifies=True,
        score=score,
        gate_facts=(),
        exclusion_reasons=(),
        source_cutoff=datetime(2026, 8, 4, 15, 0),
        theme="ai-application",
        sector="technology",
        tracked_index=None,
        clone_group=None,
        issuer=None,
        feature_hash=f"{code:0>64}"[-64:],
    )


def _closes(code: str, values: tuple[float, ...]) -> tuple[ForwardAdjustedClose, ...]:
    return tuple(
        ForwardAdjustedClose(
            asset_code=code,
            session_date=date(2026, 8, 4) + timedelta(days=index),
            adjusted_close=value,
            price_basis="total_return_adjusted",
            decision_eligible=True,
            provider="eastmoney",
            adjustment_version="eastmoney.hfq.v2",
            source_hash=f"{code}{index}".encode().hex().ljust(64, "0")[:64],
        )
        for index, value in enumerate(values)
    )


def test_v2_etf_primary_reuses_existing_forward_outcome_cost_contract() -> None:
    signal_date = date(2026, 8, 4)
    result = V2ScreenResult(
        universe="etf",
        signal_date=signal_date,
        source_cutoff=__import__("datetime").datetime(2026, 8, 4, 15, 0),
        observations=(_observation("510001", 0.9), _observation("510002", 0.8)),
        manifest_hash="m" * 64,
        input_hash="i" * 64,
        exclusions=(),
    )
    sessions = tuple(signal_date + timedelta(days=index) for index in range(7))
    evidence = evaluate_v2_etf_primary(
        result=result,
        formula_id=BREAKOUT_V2,
        replay_run_key="v2-replay-test",
        trading_sessions=sessions,
        adjusted_closes=(
            *_closes("510001", (100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 110.0)),
            *_closes("510002", (100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 105.0)),
        ),
        frozen_baseline_gross_returns={signal_date: 0.02},
    )
    assert evidence.forward_bundle.execution_model == "t_plus_one_adjusted_close_v1"
    assert evidence.forward_bundle.round_trip_cost_bps == 20
    assert evidence.completed_asset_count == 2
    assert evidence.mean_net_excess is not None and evidence.mean_net_excess > 0


def test_v2_primary_has_stable_unavailable_reason_without_common_support() -> None:
    result = V2ScreenResult(
        universe="etf",
        signal_date=date(2026, 8, 4),
        source_cutoff=__import__("datetime").datetime(2026, 8, 4, 15, 0),
        observations=(_observation("510001", 0.9),),
        manifest_hash="m" * 64,
        input_hash="i" * 64,
        exclusions=(),
    )
    sessions = tuple(date(2026, 8, 4) + timedelta(days=index) for index in range(3))
    evidence = evaluate_v2_etf_primary(
        result=result,
        formula_id=BREAKOUT_V2,
        replay_run_key="v2-replay-empty",
        trading_sessions=sessions,
        adjusted_closes=(),
        frozen_baseline_gross_returns={date(2026, 8, 4): 0.02},
    )
    assert evidence.unavailable_reason == "insufficient_common_support_for_v2_etf_primary"


def test_v2_bootstrap_uses_existing_block_bootstrap() -> None:
    interval = bootstrap_v2_primary(
        (0.01, 0.02, 0.03, 0.02, 0.01),
        block_sessions=2,
        resamples=100,
    )
    assert interval.confidence == 0.95
    assert interval.block_sessions == 2
