from __future__ import annotations

from datetime import datetime

from app.services.short_research.ranking_contract import (
    build_ranking_contract,
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
