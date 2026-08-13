from __future__ import annotations

import math
from datetime import date, datetime

from app.services.strategy_lab.ashare_sentiment_risk import (
    ACTION_NOT_APPLICABLE,
    ACTION_OBSERVE_ONLY,
    ACTION_SHADOW_ENTRY_ALLOWED,
    ASHARE_SENTIMENT_RISK_CONTRACT_HASH,
    RISK_HEALTHY,
    RISK_OFF,
    RISK_UNAVAILABLE,
    RISK_WARNING,
    SentimentRiskPoint,
    calculate_sentiment_risk,
    project_sentiment_risk,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2StagedAssetFeature,
    staged_sentiment_risk_snapshot,
)

SIGNAL_DATE = date(2026, 8, 12)
CUTOFF = datetime(2026, 8, 12, 15, 5)


def _points(
    *,
    middle_returns: tuple[float, ...],
    middle_below_ma5: tuple[bool, ...],
    leader_return: float = 0.01,
) -> list[SentimentRiskPoint]:
    rows = [
        SentimentRiskPoint(
            asset_code=f"L{index:02d}",
            theme_key=f"theme-{index % 2}",
            hot_score=0.80,
            core_score=0.85,
            return_1=leader_return,
            below_adjusted_ma5=False,
            signal_date=SIGNAL_DATE,
            source_cutoff=CUTOFF,
        )
        for index in range(3)
    ]
    rows.extend(
        SentimentRiskPoint(
            asset_code=f"M{index:02d}",
            theme_key=f"theme-{index % 2}",
            hot_score=0.80,
            core_score=0.60,
            return_1=value,
            below_adjusted_ma5=middle_below_ma5[index],
            signal_date=SIGNAL_DATE,
            source_cutoff=CUTOFF,
        )
        for index, value in enumerate(middle_returns)
    )
    return rows


def test_healthy_boundary_has_no_action_override() -> None:
    result = calculate_sentiment_risk(
        _points(
            middle_returns=(0.01,) * 12,
            middle_below_ma5=(False,) * 12,
        ),
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
    )
    assert result.state == RISK_HEALTHY
    assert result.action_mode == ACTION_SHADOW_ENTRY_ALLOWED
    assert result.new_entry_allowed is True
    assert result.triggered_components == ()


def test_exactly_two_components_is_warning_and_order_is_stable() -> None:
    rows = _points(
        middle_returns=(-0.01,) * 6 + (0.01,) * 6,
        middle_below_ma5=(True,) * 8 + (False,) * 4,
    )
    result = calculate_sentiment_risk(rows, signal_date=SIGNAL_DATE, source_cutoff=CUTOFF)
    shuffled = calculate_sentiment_risk(
        list(reversed(rows)), signal_date=SIGNAL_DATE, source_cutoff=CUTOFF
    )
    assert result.state == RISK_WARNING
    assert result.action_mode == ACTION_OBSERVE_ONLY
    assert result.new_entry_allowed is False
    assert result.triggered_components == (
        "middle_median_return_non_positive",
        "middle_below_ma5_ratio_high",
    )
    assert result.to_dict() == shuffled.to_dict()


def test_three_or_more_components_is_risk_off() -> None:
    result = calculate_sentiment_risk(
        _points(
            middle_returns=(-0.01,) * 12,
            middle_below_ma5=(True,) * 12,
            leader_return=0.03,
        ),
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
    )
    assert result.state == RISK_OFF
    assert len(result.triggered_components) == 4


def test_insufficient_support_and_invalid_inputs_fail_closed() -> None:
    rows = _points(
        middle_returns=(-0.01,) * 11,
        middle_below_ma5=(True,) * 11,
    )
    result = calculate_sentiment_risk(rows, signal_date=SIGNAL_DATE, source_cutoff=CUTOFF)
    assert result.state == RISK_UNAVAILABLE
    assert result.unavailable_reason == "sentiment_risk_insufficient_middle_tier"

    invalid = rows[:]
    invalid[0] = SentimentRiskPoint(
        asset_code=invalid[0].asset_code,
        theme_key=invalid[0].theme_key,
        hot_score=invalid[0].hot_score,
        core_score=invalid[0].core_score,
        return_1=math.nan,
        below_adjusted_ma5=invalid[0].below_adjusted_ma5,
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
    )
    invalid_result = calculate_sentiment_risk(
        invalid, signal_date=SIGNAL_DATE, source_cutoff=CUTOFF
    )
    assert invalid_result.state == RISK_UNAVAILABLE
    assert invalid_result.unavailable_reason == "sentiment_risk_non_finite_input"

    invisible = rows[:]
    invisible[0] = SentimentRiskPoint(
        asset_code=invisible[0].asset_code,
        theme_key=invisible[0].theme_key,
        hot_score=invisible[0].hot_score,
        core_score=invisible[0].core_score,
        return_1=invisible[0].return_1,
        below_adjusted_ma5=invisible[0].below_adjusted_ma5,
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
        pit_visible=False,
    )
    assert (
        calculate_sentiment_risk(
            invisible, signal_date=SIGNAL_DATE, source_cutoff=CUTOFF
        ).unavailable_reason
        == "sentiment_risk_pit_input_invalid"
    )


def test_snapshot_is_adjusted_bar_proxy_and_does_not_infer_limit_facts() -> None:
    result = calculate_sentiment_risk(
        _points(middle_returns=(0.01,) * 12, middle_below_ma5=(False,) * 12),
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
    )
    facts = result.to_dict()
    assert facts["contract_hash"] == ASHARE_SENTIMENT_RISK_CONTRACT_HASH
    assert len(facts["snapshot_hash"]) == 64
    assert facts["factual_limit_board_data"]["available"] is False
    assert all(value is None for value in facts["factual_limit_board_data"]["fields"].values())

    tampered = dict(facts)
    tampered["state"] = RISK_OFF
    rejected = project_sentiment_risk(
        universe="ashare",
        formula_id="leader_breakout_proxy_v2",
        qualifies=True,
        gate_facts={"sentiment_risk": tampered},
    )
    assert rejected["sentiment_risk_state"] == RISK_UNAVAILABLE
    assert rejected["sentiment_risk_provenance"]["contract_hash"] is None


def test_projection_is_formula_and_universe_isolated_and_legacy_is_unavailable() -> None:
    snapshot = calculate_sentiment_risk(
        _points(middle_returns=(0.01,) * 12, middle_below_ma5=(False,) * 12),
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
    ).to_dict()
    facts = {"sentiment_risk": snapshot}
    breakout = project_sentiment_risk(
        universe="ashare",
        formula_id="leader_breakout_proxy_v2",
        qualifies=True,
        gate_facts=facts,
    )
    assert breakout["sentiment_risk_action_mode"] == ACTION_SHADOW_ENTRY_ALLOWED
    assert breakout["sentiment_risk_new_entry_allowed"] is True
    non_qualifying_breakout = project_sentiment_risk(
        universe="ashare",
        formula_id="leader_breakout_proxy_v2",
        qualifies=False,
        gate_facts=facts,
    )
    assert non_qualifying_breakout["sentiment_risk_action_mode"] == ACTION_OBSERVE_ONLY
    assert non_qualifying_breakout["sentiment_risk_new_entry_allowed"] is False
    base = project_sentiment_risk(
        universe="ashare",
        formula_id="base_launch_proxy_v2",
        qualifies=True,
        gate_facts=facts,
    )
    assert base["sentiment_risk_action_mode"] == ACTION_NOT_APPLICABLE
    assert base["sentiment_risk_new_entry_allowed"] is None

    etf = project_sentiment_risk(
        universe="etf",
        formula_id="leader_breakout_proxy_v2",
        qualifies=True,
        gate_facts=facts,
    )
    assert etf["sentiment_risk_state"] == "not_applicable"
    assert etf["sentiment_risk_action_mode"] == ACTION_NOT_APPLICABLE

    legacy = project_sentiment_risk(
        universe="ashare",
        formula_id="leader_breakout_proxy_v2",
        qualifies=True,
        gate_facts={},
    )
    assert legacy["sentiment_risk_state"] == RISK_UNAVAILABLE
    assert legacy["sentiment_risk_new_entry_allowed"] is False
    assert (
        legacy["sentiment_risk_provenance"]["unavailable_reason"]
        == "sentiment_risk_contract_missing"
    )


def test_staged_cross_section_uses_multiple_themes_instead_of_group_local_data() -> None:
    features = []
    for group_index in range(2):
        for rank in range(20):
            middle = 10 <= rank < 16
            leader = rank >= 16
            features.append(
                V2StagedAssetFeature(
                    asset_code=f"{group_index}{rank:05d}",
                    asset_name="sample",
                    group_key=f"theme-{group_index}",
                    clone_group=None,
                    standard_available=True,
                    return_1=0.03 if leader else -0.01 if middle else 0.0,
                    return_5=float(rank),
                    mean_amount_20=float(rank + 1),
                    input_digest=f"{group_index}{rank:063d}"[-64:],
                    return_20=float(rank),
                    below_adjusted_ma5=middle,
                )
            )
    overrides = {
        "theme-0": (0.8, 0.8, 0.8),
        "theme-1": (0.8, 0.8, 0.8),
    }

    result = staged_sentiment_risk_snapshot(
        features,
        theme_percentile_overrides=overrides,
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
    )
    reversed_result = staged_sentiment_risk_snapshot(
        tuple(reversed(features)),
        theme_percentile_overrides=overrides,
        signal_date=SIGNAL_DATE,
        source_cutoff=CUTOFF,
    )

    assert result.state == RISK_OFF
    assert dict(result.cohort_counts)["hot_theme_count"] == 2
    assert dict(result.cohort_counts)["middle_tier_count"] == 12
    assert result.to_dict() == reversed_result.to_dict()
