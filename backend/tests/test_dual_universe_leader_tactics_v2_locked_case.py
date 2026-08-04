from __future__ import annotations

from datetime import date, datetime

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2CandidateObservation,
    V2ScreenResult,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    evaluate_locked_case,
)


def _result(*, universe: str = "ashare", signal_date: date = date(2026, 8, 3)) -> V2ScreenResult:
    observations = tuple(
        V2CandidateObservation(
            universe=universe,
            asset_code=code,
            asset_name=code,
            signal_date=signal_date,
            formula_id="leader_breakout_proxy_v2",
            state="preparing",
            availability="available",
            qualifies=True,
            score=0.9,
            gate_facts=(),
            exclusion_reasons=(),
            source_cutoff=datetime(2026, 8, 3, 15),
            theme="ai-application",
            sector=None,
            tracked_index=None,
            clone_group=None,
            issuer=None,
            feature_hash="a" * 64,
        )
        for code in ("603039", "002131")
    )
    return V2ScreenResult(
        universe=universe,
        signal_date=signal_date,
        source_cutoff=datetime(2026, 8, 3, 15),
        observations=observations,
        manifest_hash="m" * 64,
        input_hash="i" * 64,
        exclusions=(),
    )


def test_locked_case_reads_natural_screen_output_only() -> None:
    evidence = evaluate_locked_case(_result(), data_available=True)
    assert evidence.status == "locked_case_match"
    assert evidence.observed_codes == ("002131", "603039")
    assert evidence.expected_codes == ("002131", "603039")


def test_locked_case_is_unavailable_for_wrong_date_or_missing_data() -> None:
    assert (
        evaluate_locked_case(_result(signal_date=date(2026, 8, 2)), data_available=True).status
        == "locked_case_wrong_cutoff"
    )
    assert evaluate_locked_case(_result(), data_available=False).status == "locked_case_unavailable"
