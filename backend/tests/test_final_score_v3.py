from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services.short_research.final_score_v3 import (
    build_final_score_v3_sector_inputs,
    build_v3_shadow_comparison,
    score_final_score_v3,
)
from app.services.short_research.ranking_contract import RankingInput, parse_ranking_manifest


def _manifest():
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "openspec"
        / "changes"
        / "harden-etf-comprehensive-ranking"
        / "final-score-v3-contract.json"
    )
    return parse_ranking_manifest(json.loads(contract_path.read_text(encoding="utf-8")))


def _input(
    code: str,
    *,
    asset_bucket: str = "broad-equity",
    underlying_id: str | None = None,
    return_5d: float = 0.05,
) -> RankingInput:
    return RankingInput(
        asset_code=code,
        asset_bucket=asset_bucket,
        price_basis="total_return_adjusted",
        profile_version="final_score_v3",
        values={
            "tracked_underlying_id": underlying_id,
            "theme_group": "broad",
            "return_5d": return_5d,
            "return_10d": return_5d,
            "return_20d": return_5d,
            "distance_to_ma20": 0.01,
            "trend_consistency": 0.6,
            "realized_volatility_20d": 0.02,
            "downside_volatility_20d": 0.01,
            "max_drawdown_20d": -0.03,
            "overextension_atr": 1.0,
            "average_turnover_20d": 100_000_000,
            "average_turnover_60d": 90_000_000,
            "spread_bps": 2.0,
            "structure_quality": 90.0,
            "sector_breadth_20d": 60.0,
            "sector_momentum_20d": 0.04,
            "sector_turnover_ratio_20_60": 1.1,
            "catalyst_quality": 80.0,
            "catalyst_confidence": 80.0,
            "premium_discount_bps": 5.0,
            "premium_provider_consensus": 90.0,
            "eligible_peer_count": 2,
            "sector_eligible_peer_count": 2,
            "source_trade_date": "2026-01-02",
            "market_data_reliability": "verified",
            "catalyst_effective_at": "2026-01-01T09:30:00",
            "catalyst_expires_at": "2026-01-04T09:30:00",
            "catalyst_source": "fixture",
            "iopv_observed_at": "2026-01-02T10:00:00",
            "component_reliability": {
                "technical_momentum_cross_section": "verified",
                "risk_quality_cross_section": "verified",
                "structure_liquidity": "verified",
                "sector_trend": "verified",
                "theme_catalyst": "verified",
                "premium_discount": "verified",
            },
        },
    )


def test_v3_cluster_weights_clones_once_inside_their_asset_bucket() -> None:
    results = score_final_score_v3(
        [
            _input("510300", underlying_id="000300", return_5d=0.08),
            _input("510310", underlying_id="000300", return_5d=0.08),
            _input("510500", underlying_id="000905", return_5d=0.02),
        ],
        manifest=_manifest(),
    )

    first_clone = results["510300"]
    second_clone = results["510310"]
    assert first_clone.score_eligible is True
    assert first_clone.asset_bucket == "broad-equity"
    assert first_clone.metric_peer_counts["return_5d"] == 2
    assert first_clone.component_scores["technical_momentum_cross_section"] == second_clone.component_scores[
        "technical_momentum_cross_section"
    ]


def test_v3_rejects_missing_and_incompatible_inputs_without_neutral_fill() -> None:
    missing = _input("510300")
    missing_values = dict(missing.values)
    missing_values["average_turnover_60d"] = None
    missing = RankingInput(
        asset_code=missing.asset_code,
        asset_bucket=missing.asset_bucket,
        price_basis=missing.price_basis,
        profile_version=missing.profile_version,
        values=missing_values,
    )
    results = score_final_score_v3(
        [missing, _input("510500"), _input("518880", asset_bucket="unknown")],
        manifest=_manifest(),
    )

    assert results["510300"].ranking_score is None
    assert results["510300"].score_eligible is False
    assert results["510300"].missing_by_component == {"structure_liquidity": ("average_turnover_60d",)}
    assert results["518880"].score_eligible is False
    assert results["518880"].missing_by_component["contract"] == ("incompatible_asset_bucket",)


def test_v3_derives_peer_count_instead_of_accepting_fixture_count() -> None:
    result = score_final_score_v3([_input("510300", underlying_id="000300")], manifest=_manifest())["510300"]

    assert result.score_eligible is False
    assert result.missing_by_component["technical_momentum_cross_section"] == ("eligible_peer_count",)


def test_v3_rejects_non_finite_input_without_weight_renormalization() -> None:
    invalid = _input("510300")
    invalid_values = dict(invalid.values)
    invalid_values["return_5d"] = float("nan")
    invalid = RankingInput(
        asset_code=invalid.asset_code,
        asset_bucket=invalid.asset_bucket,
        price_basis=invalid.price_basis,
        profile_version=invalid.profile_version,
        values=invalid_values,
    )

    result = score_final_score_v3([invalid, _input("510500"), _input("510880")], manifest=_manifest())["510300"]

    assert result.ranking_score is None
    assert result.score_eligible is False
    assert result.missing_by_component["technical_momentum_cross_section"] == ("return_5d",)


def test_v3_sector_inputs_deduplicate_clones_before_breadth() -> None:
    payloads = build_final_score_v3_sector_inputs(
        [
            _input("510300", underlying_id="000300", return_5d=0.08),
            _input("510310", underlying_id="000300", return_5d=0.08),
            _input("510500", underlying_id="000905", return_5d=-0.02),
        ]
    )

    assert payloads["510300"]["sector_eligible_peer_count"] == 2
    assert payloads["510300"]["sector_breadth_20d"] == payloads["510310"]["sector_breadth_20d"]
    assert payloads["510300"]["sector_momentum_20d"] == payloads["510310"]["sector_momentum_20d"]


def test_v3_keeps_unimplemented_factor_groups_explanatory_only() -> None:
    explanatory = _input("510300")
    explanatory_values = dict(explanatory.values)
    explanatory_values.update(
        {
            "fund_flow_score": 100.0,
            "fundamental_score": 100.0,
            "valuation_percentile_score": 100.0,
            "macro_style_score": 100.0,
        }
    )
    explanatory = RankingInput(
        asset_code=explanatory.asset_code,
        asset_bucket=explanatory.asset_bucket,
        price_basis=explanatory.price_basis,
        profile_version=explanatory.profile_version,
        values=explanatory_values,
    )
    baseline = score_final_score_v3([_input("510300"), _input("510500"), _input("510880")], manifest=_manifest())
    with_explanatory = score_final_score_v3(
        [explanatory, _input("510500"), _input("510880")],
        manifest=_manifest(),
    )

    assert with_explanatory["510300"].ranking_score == baseline["510300"].ranking_score
    assert all("factor" not in component for component in with_explanatory["510300"].component_scores)


def test_v3_quality_gate_cannot_be_restored_by_later_enrichment() -> None:
    gated = _input("510300")
    gated_values = dict(gated.values)
    gated_values.update(
        {
            "quality_gate_rejected": True,
            "catalyst_quality": 100.0,
            "catalyst_confidence": 100.0,
            "premium_discount_bps": 0.0,
            "premium_provider_consensus": 100.0,
        }
    )
    gated = RankingInput(
        asset_code=gated.asset_code,
        asset_bucket=gated.asset_bucket,
        price_basis=gated.price_basis,
        profile_version=gated.profile_version,
        values=gated_values,
    )

    result = score_final_score_v3([gated, _input("510500"), _input("510880")], manifest=_manifest())["510300"]

    assert result.ranking_score is None
    assert result.missing_by_component["technical_momentum_cross_section"] == ("quality_gate_rejected",)


def test_v3_missing_sparse_theme_catalyst_remains_explanatory_without_neutral_fill() -> None:
    inputs: list[RankingInput] = []
    for code in ("510300", "510500"):
        item = _input(code)
        values = dict(item.values)
        values.update(
            {
                "catalyst_quality": None,
                "catalyst_confidence": None,
                "catalyst_effective_at": None,
                "catalyst_expires_at": None,
                "catalyst_source": None,
                "component_reliability": {
                    **dict(values["component_reliability"]),
                    "theme_catalyst": "unavailable",
                },
            }
        )
        inputs.append(
            RankingInput(
                asset_code=item.asset_code,
                asset_bucket=item.asset_bucket,
                price_basis=item.price_basis,
                profile_version=item.profile_version,
                values=values,
            )
        )

    result = score_final_score_v3(inputs, manifest=_manifest())["510300"]

    assert result.score_eligible is True
    assert result.ranking_score is not None
    assert "theme_catalyst" not in result.component_scores
    assert "theme_catalyst" not in result.missing_by_component


def test_v3_quality_rejected_asset_cannot_change_eligible_peer_distributions() -> None:
    clean = [_input("510300", return_5d=0.08), _input("510500", return_5d=0.02)]
    dirty = _input("510880", return_5d=99.0)
    dirty_values = dict(dirty.values)
    dirty_values["quality_gate_rejected"] = True
    dirty = RankingInput(
        asset_code=dirty.asset_code,
        asset_bucket=dirty.asset_bucket,
        price_basis=dirty.price_basis,
        profile_version=dirty.profile_version,
        values=dirty_values,
    )

    baseline = score_final_score_v3(clean, manifest=_manifest())["510300"]
    contaminated = score_final_score_v3([*clean, dirty], manifest=_manifest())["510300"]
    baseline_sector = build_final_score_v3_sector_inputs(clean)["510300"]
    contaminated_sector = build_final_score_v3_sector_inputs([*clean, dirty])["510300"]

    assert contaminated.ranking_score == baseline.ranking_score
    assert contaminated.component_scores == baseline.component_scores
    assert contaminated.metric_peer_counts == baseline.metric_peer_counts
    assert contaminated_sector == baseline_sector


def test_missing_risk_primitive_does_not_invalidate_complete_technical_component() -> None:
    def without_atr(code: str) -> RankingInput:
        ranking_input = _input(code)
        values = dict(ranking_input.values)
        values["overextension_atr"] = None
        return RankingInput(
            asset_code=ranking_input.asset_code,
            asset_bucket=ranking_input.asset_bucket,
            price_basis=ranking_input.price_basis,
            profile_version=ranking_input.profile_version,
            values=values,
        )

    result = score_final_score_v3(
        [without_atr("510300"), without_atr("510500"), without_atr("510880")],
        manifest=_manifest(),
    )["510300"]

    assert "technical_momentum_cross_section" in result.component_scores
    assert result.missing_by_component["risk_quality_cross_section"] == ("overextension_atr",)


def test_v3_applies_final_risk_cap_once_after_component_aggregation() -> None:
    stale = _input("510300")
    stale_values = dict(stale.values)
    stale_values["risk_flags"] = ["数据滞后"]
    stale = RankingInput(
        asset_code=stale.asset_code,
        asset_bucket=stale.asset_bucket,
        price_basis=stale.price_basis,
        profile_version=stale.profile_version,
        values=stale_values,
    )

    result = score_final_score_v3([stale, _input("510500"), _input("510880")], manifest=_manifest())["510300"]

    assert result.score_eligible is True
    assert result.ranking_score is not None
    assert result.ranking_score <= 55.0
    assert result.limitation_reasons == ("旧数据不能提高最终排序。",)


def test_v3_shadow_comparison_reports_coverage_components_and_rank_changes() -> None:
    assets = [
        SimpleNamespace(
            metadata=SimpleNamespace(asset_type="etf", code="510300"),
            total_score=90.0,
            metrics={
                "v3_score_eligible": True,
                "v3_ranking_score": 80.0,
                "v3_missing_by_component": {},
                "v3_score_limitation_reasons": [],
            },
            score_breakdown={"final_score_v3_shadow": {"component_scores": {"technical_momentum_cross_section": 80.0}}},
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(asset_type="etf", code="510500"),
            total_score=80.0,
            metrics={
                "v3_score_eligible": False,
                "v3_ranking_score": None,
                "v3_missing_by_component": {"premium_discount": ["premium_provider_consensus"]},
                "v3_score_limitation_reasons": [
                    "premium_discount:premium_provider_consensus",
                    "数据不足不能形成高分排序。",
                ],
            },
            score_breakdown={"final_score_v3_shadow": {"component_scores": {"technical_momentum_cross_section": 70.0}}},
        ),
    ]

    comparison = build_v3_shadow_comparison(assets)

    assert comparison["coverage"] == {"total": 2, "eligible": 1, "ratio": 0.5}
    assert comparison["component_availability"]["technical_momentum_cross_section"] == {"available": 2, "total": 2}
    assert comparison["component_availability"]["premium_discount"] == {"available": 0, "total": 2}
    assert comparison["caps"] == {"applied_count": 1}
    assert comparison["exclusion_reasons"] == {
        "premium_discount:premium_provider_consensus": 1,
        "数据不足不能形成高分排序。": 1,
    }
    assert comparison["top_n_changes"]["v2_only"] == ["510500"]
