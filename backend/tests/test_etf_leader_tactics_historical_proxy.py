from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_evidence import (
    FactorEvidenceContractError,
)
from app.services.strategy_lab.etf_leader_tactics_historical_proxy import (
    LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_PROXY_NOT_PIT,
    build_leader_historical_proxy_evidence,
)


def _source() -> dict:
    contract = {
        "schema_version": "leader_source_snapshot_historical_proxy_v1",
        "signal_run_id": 121,
        "signal_date": "2026-07-31",
        "history_sessions": 180,
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "forbidden_providers": ["sina", "efinance"],
        "production_mutation_allowed": False,
    }
    candidate = {
        "candidate_id": "former_leader_repair_proxy_v1",
        "asset_code": "512710",
        "name": "军工龙头ETF富国",
        "score": 0.6481481481481481,
        "baseline_score": 41.212968016154,
        "peer_group": "industrial",
        "components": {
            "prior_leadership_percentile": 0.9444444444444444,
            "drawdown_120": -0.3447958035714286,
            "atr5_atr20_ratio": 0.6591422121896163,
            "overextension_atr": 0.6839729119638814,
            "peer_count": 19,
        },
    }
    candidate["feature_hash"] = stable_contract_hash(candidate)
    return {
        **contract,
        "contract_hash": stable_contract_hash(contract),
        "source_ranking_contract_hash": stable_contract_hash(
            {"source": "ranking"}
        ),
        "source_input_snapshot_hash": stable_contract_hash(
            {"source": "input"}
        ),
        "ranking_source_kind": "research_replay",
        "evidence_mode": "source_snapshot_historical_proxy",
        "status": "complete",
        "input_asset_count": 750,
        "excluded_asset_count": 626,
        "exclusion_counts": {"unclassified_peer_group": 299},
        "candidate_counts": {
            "leader_breakout_proxy_v1": 0,
            "former_leader_repair_proxy_v1": 1,
        },
        "candidates": [candidate],
        "limitations": [
            "membership and peer taxonomy use the sealed source snapshot, not factual historical receipts",
            "historical proxy dates and candidates cannot count toward PIT promotion gates",
            "research only; no ranking, position, alert, email, or execution mutation",
        ],
    }


def test_build_historical_proxy_keeps_all_formal_gate_credit_at_zero() -> None:
    evidence = build_leader_historical_proxy_evidence(
        _source(),
        code_version="leader-historical-proxy-test-v1",
    )

    assert evidence.experiment_family == LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY
    assert evidence.promotion.state == "insufficient_data"
    assert evidence.promotion.production_mutation_allowed is False
    assert evidence.report["unavailable_reason"] == LEADER_HISTORICAL_PROXY_NOT_PIT
    assert evidence.report["promotion_gate_credit"] == {
        "eligible_pit_sessions": 0,
        "independent_primary_dates": 0,
        "walk_forward_folds": 0,
    }
    assert evidence.report["candidates"][0]["asset_code"] == "512710"
    assert evidence.report["notification_provenance"] == "none"
    assert evidence.report["execution_provenance"] == "none"
    evidence.validate()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("price_basis", "raw_close", "total-return-adjusted"),
        ("production_mutation_allowed", True, "provenance"),
    ],
)
def test_historical_proxy_rejects_unsafe_source_contract(
    field: str,
    value: object,
    message: str,
) -> None:
    source = _source()
    source[field] = value

    with pytest.raises(FactorEvidenceContractError, match=message):
        build_leader_historical_proxy_evidence(
            source,
            code_version="leader-historical-proxy-test-v1",
        )


def test_historical_proxy_rejects_unclassified_peer_candidate() -> None:
    source = deepcopy(_source())
    candidate = source["candidates"][0]
    candidate["peer_group"] = "unknown"
    candidate_without_hash = {
        key: value for key, value in candidate.items() if key != "feature_hash"
    }
    candidate["feature_hash"] = stable_contract_hash(candidate_without_hash)

    with pytest.raises(
        FactorEvidenceContractError,
        match="classified peer group",
    ):
        build_leader_historical_proxy_evidence(
            source,
            code_version="leader-historical-proxy-test-v1",
        )
