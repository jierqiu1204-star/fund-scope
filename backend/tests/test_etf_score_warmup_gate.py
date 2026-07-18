from __future__ import annotations

from app.services.short_research.final_score_v3 import (
    FinalScoreV3Result,
    enforce_history_warmup,
)


def _result() -> FinalScoreV3Result:
    return FinalScoreV3Result(
        asset_bucket="sector",
        ranking_score=81.5,
        score_eligible=True,
        component_scores={"momentum": 80.0},
        missing_by_component={},
        metric_peer_counts={"return_20d": 50},
        limitation_reasons=(),
    )


def test_fewer_than_61_adjusted_sessions_cannot_receive_degraded_score() -> None:
    gated = enforce_history_warmup(_result(), usable_sessions=60)

    assert gated.ranking_score is None
    assert gated.score_eligible is False
    assert gated.missing_by_component == {
        "history_depth_61": ("insufficient_decision_eligible_adjusted_sessions",)
    }
    assert gated.limitation_reasons == (
        "history_depth_61:insufficient_decision_eligible_adjusted_sessions",
    )


def test_61_adjusted_sessions_preserve_the_calculated_score() -> None:
    original = _result()

    assert enforce_history_warmup(original, usable_sessions=61) is original
