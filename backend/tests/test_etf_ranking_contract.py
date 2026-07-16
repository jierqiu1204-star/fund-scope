from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.models.entities import ShortResearchSignalRun
from app.services.short_research.ranking_contract import (
    RankingInput,
    _final_score_v3_contract_path,
    build_ranking_contract,
    final_score_v3_contract,
    final_score_v3_manifest,
    parse_ranking_manifest,
    scope_kind_for_filters,
)


def _contract(*, price_basis: str = "total_return_adjusted", reverse: bool = False) -> dict[str, object]:
    codes = ["510300", "159915"]
    universe = [
        {"asset_code": "510300", "asset_bucket": "broad-equity"},
        {"asset_code": "159915", "asset_bucket": "sector/theme-equity"},
    ]
    if reverse:
        codes.reverse()
        universe.reverse()
    return build_ranking_contract(
        score_version="final_score_v3",
        rule_version="ranking_rule_v3",
        component_manifest={"dag": {"components": ["risk", "momentum"]}, "weight": 1.0},
        scope={"scope_kind": "codes", "codes": codes},
        universe_snapshot=universe,
        input_snapshot={"dates": {"510300": "2026-01-02", "159915": "2026-01-02"}},
        price_basis=price_basis,
        data_cutoff=datetime(2026, 1, 2, 15, 30),
        reliability_policy={"eligible": ["verified", "alternate_provider"]},
    )


def test_ranking_contract_hash_is_order_independent_for_scope_and_universe() -> None:
    original = _contract()
    reordered = _contract(reverse=True)

    assert original["canonical_json"] == reordered["canonical_json"]
    assert original["ranking_contract_hash"] == reordered["ranking_contract_hash"]


def test_ranking_contract_hash_changes_when_decision_inputs_change() -> None:
    original = _contract()
    incompatible_basis = _contract(price_basis="raw_close")

    assert original["ranking_contract_hash"] != incompatible_basis["ranking_contract_hash"]


def test_scope_kind_is_explicit_for_new_runs() -> None:
    assert scope_kind_for_filters(theme=None, codes=None) == "full"
    assert scope_kind_for_filters(theme="人工智能", codes=[]) == "theme"
    assert scope_kind_for_filters(theme="人工智能", codes=["510300"]) == "codes"


def test_final_score_v3_manifest_has_typed_score_inputs_and_missing_policy() -> None:
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "openspec"
        / "changes"
        / "harden-etf-comprehensive-ranking"
        / "final-score-v3-contract.json"
    )
    manifest = parse_ranking_manifest(json.loads(contract_path.read_text(encoding="utf-8")))

    momentum = manifest.components["technical_momentum_cross_section"]
    assert momentum.score_bearing is True
    assert momentum.weight == pytest.approx(1 / 3)
    assert momentum.primitive_lineage == (
        "return_5d",
        "return_10d",
        "return_20d",
        "distance_to_ma20",
        "trend_consistency",
    )
    assert momentum.units["return_5d"] == "ratio"
    assert manifest.asset_buckets == ("broad-equity", "sector/theme-equity", "fixed-income", "commodity", "cross-border")
    assert manifest.missing_data_behavior == "score_unavailable"
    assert manifest.dag_edges[0].source == "technical_momentum_cross_section"
    assert manifest.dag_edges[0].target == "weighted_aggregate"
    assert manifest.acyclic_order[-1] == "ranking_score"
    assert "label_validation" in manifest.explanatory_only


def test_runtime_v3_manifest_uses_the_frozen_contract() -> None:
    manifest = final_score_v3_manifest()

    assert manifest.score_version == "final_score_v3"
    assert manifest.components["premium_discount"].primitive_inputs[0].primitive_id == "premium_discount_bps"


def test_runtime_v3_contract_is_bundled_with_the_backend_package() -> None:
    runtime_path = _final_score_v3_contract_path()
    openspec_path = (
        Path(__file__).resolve().parents[2]
        / "openspec"
        / "changes"
        / "harden-etf-comprehensive-ranking"
        / "final-score-v3-contract.json"
    )

    assert runtime_path.parent == Path(__file__).resolve().parents[1] / "app/services/short_research"
    assert json.loads(runtime_path.read_text(encoding="utf-8")) == json.loads(openspec_path.read_text(encoding="utf-8"))


def test_final_score_v3_contract_exposes_rule_and_premium_consensus() -> None:
    contract = final_score_v3_contract()

    assert contract["contract_id"] == "final_score_v3"
    assert contract["rule_version"] == "final_score_v3_rule_v2"
    components = contract["calculation"]["components"]
    assert next(item for item in components if item["id"] == "theme_catalyst")["score_bearing"] is False
    assert sum(item["weight"] for item in components if item["score_bearing"]) == pytest.approx(1.0)
    assert "theme_catalyst" in contract["explanatory_only"]
    consensus = contract["freshness"]["premium_discount"]["provider_consensus"]
    assert consensus["minimum_independent_providers"] == 2
    assert consensus["maximum_premium_dispersion_bps"] == 30


def test_dual_coverage_columns_are_nullable() -> None:
    run_columns = ShortResearchSignalRun.__table__.c

    assert run_columns.decision_data_item_count.nullable is True
    assert run_columns.decision_data_coverage_ratio.nullable is True


def test_ranking_input_fails_closed_when_a_required_v3_input_is_missing() -> None:
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "openspec"
        / "changes"
        / "harden-etf-comprehensive-ranking"
        / "final-score-v3-contract.json"
    )
    manifest = parse_ranking_manifest(json.loads(contract_path.read_text(encoding="utf-8")))
    values = {
        required
        for component in manifest.components.values()
        for required in component.required_inputs
    }
    input_values = {key: 1.0 for key in values}
    input_values.update(
        {
            "source_trade_date": "2026-01-02",
            "catalyst_effective_at": "2026-01-01T09:30:00",
            "catalyst_expires_at": "2026-01-04T09:30:00",
            "catalyst_source": "fixture",
        }
    )
    ranking_input = RankingInput(
        asset_code="510300",
        asset_bucket="broad-equity",
        price_basis="total_return_adjusted",
        profile_version="final_score_v3",
        values=input_values,
    )

    assert manifest.validate_input(ranking_input).score_eligible is True
    input_values.pop("average_turnover_60d")
    validation = manifest.validate_input(ranking_input)
    assert validation.score_eligible is False
    assert validation.missing_by_component == {"structure_liquidity": ("average_turnover_60d",)}


def test_ranking_manifest_rejects_double_counted_score_bearing_primitive() -> None:
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "openspec"
        / "changes"
        / "harden-etf-comprehensive-ranking"
        / "final-score-v3-contract.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["calculation"]["components"][1]["primitive_lineage"].append("return_5d")

    with pytest.raises(ValueError, match="double-counted primitive"):
        parse_ranking_manifest(contract)
