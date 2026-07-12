from __future__ import annotations

from datetime import date

import pytest

from app.services.etf_research_evidence import (
    EVIDENCE_STATUS_INSUFFICIENT,
    EVIDENCE_STATUS_LEGACY,
    EVIDENCE_STATUS_SAME_CONTRACT,
    EVIDENCE_STATUS_VERSION_MISMATCH,
    EVIDENCE_STATUS_WAITING,
    EXECUTION_MODEL_INTRADAY_ALERT,
    build_evidence_summary,
    build_exit_v2_baseline_comparison,
    build_exit_v2_evidence_contract,
    build_research_signal_contract,
    classify_evidence_status,
    stable_contract_hash,
)


def test_contract_hash_is_stable_for_equivalent_payloads() -> None:
    left = {
        "asset_code": "513520",
        "date": date(2026, 6, 29),
        "config": {"b": 2, "a": [1, {"z": 3}]},
    }
    right = {
        "config": {"a": [1, {"z": 3}], "b": 2},
        "date": date(2026, 6, 29),
        "asset_code": "513520",
    }

    assert stable_contract_hash(left) == stable_contract_hash(right)


def test_evidence_status_distinguishes_waiting_legacy_mismatch_and_verified() -> None:
    contract = build_research_signal_contract(
        asset_type="etf",
        asset_code="513520",
        signal_run_id=1,
        signal_date=date(2026, 6, 29),
        score=88.1234567,
        observation_label="短线观察",
        entry_timing_label="健康回踩",
        data_reliability="verified",
        source_data_time="2026-06-29T14:59:00",
    )

    assert classify_evidence_status(contract, None) == EVIDENCE_STATUS_WAITING
    assert classify_evidence_status(contract, {"sample_count": 30}) == EVIDENCE_STATUS_LEGACY
    assert classify_evidence_status(contract, {"contract_hash": "different", "sample_count": 30}) == EVIDENCE_STATUS_VERSION_MISMATCH
    assert (
        classify_evidence_status(contract, {"contract_hash": contract["contract_hash"], "sample_count": 2})
        == EVIDENCE_STATUS_INSUFFICIENT
    )
    assert (
        classify_evidence_status(contract, {"contract_hash": contract["contract_hash"], "sample_count": 30})
        == EVIDENCE_STATUS_SAME_CONTRACT
    )


def test_evidence_without_contract_hash_is_always_legacy() -> None:
    contract = build_research_signal_contract(
        asset_type="etf",
        asset_code="513520",
        signal_run_id=1,
        signal_date=date(2026, 6, 29),
        score=88.0,
        observation_label="短线观察",
        entry_timing_label="健康回踩",
        data_reliability="verified",
        source_data_time="2026-06-29",
    )

    summary = build_evidence_summary(
        current_contract=contract,
        validation_evidence={"sample_count": 30, "score": 88.0, "rule_version": contract["rule_version"]},
    )

    assert summary["evidence_status"] == EVIDENCE_STATUS_LEGACY
    assert summary["contract_hash"] is None


@pytest.mark.parametrize(
    ("current_identity", "evidence_identity"),
    [
        ({"score_version": "final_score_v3"}, {"source_score_version": "final_score_v2"}),
        ({"rule_version": "short_research_rule_v3"}, {"source_rule_version": "short_research_rule_v2"}),
        ({"universe_snapshot_hash": "universe-current"}, {"source_universe_snapshot_hash": "universe-old"}),
        ({"price_basis": "total_return_v1"}, {"price_basis": "raw_close_v1"}),
        ({"allocation_version": "allocation_v3"}, {"allocation_version": "allocation_v2"}),
        ({"allocation_contract_hash": "allocation-current"}, {"allocation_contract_hash": "allocation-old"}),
    ],
)
def test_evidence_identity_mismatch_is_not_same_contract(
    current_identity: dict[str, str],
    evidence_identity: dict[str, str],
) -> None:
    current_contract = {"contract_hash": "ranking-contract", **current_identity}
    validation_evidence = {"contract_hash": "ranking-contract", "sample_count": 30, **evidence_identity}

    summary = build_evidence_summary(
        current_contract=current_contract,
        validation_evidence=validation_evidence,
    )

    assert summary["evidence_status"] == EVIDENCE_STATUS_VERSION_MISMATCH
    assert summary["contract_hash"] == "ranking-contract"


def test_evidence_summary_matches_typed_ranking_identity() -> None:
    contract = build_research_signal_contract(
        asset_type="etf",
        asset_code="513520",
        signal_run_id=1,
        signal_date=date(2026, 6, 29),
        score=88.0,
        observation_label="短线观察",
        entry_timing_label="健康回踩",
        data_reliability="verified",
        source_data_time="2026-06-29",
        ranking_contract_hash="ranking-contract",
        score_version="final_score_v3",
        score_field="ranking_score",
        universe_snapshot_hash="universe-current",
        price_basis="total_return_v1",
    )

    summary = build_evidence_summary(
        current_contract=contract,
        validation_evidence={
            "source_ranking_contract_hash": "ranking-contract",
            "source_score_version": "final_score_v3",
            "source_score_field": "ranking_score",
            "source_rule_version": contract["rule_version"],
            "source_universe_snapshot_hash": "universe-current",
            "price_basis": "total_return_v1",
            "sample_count": 30,
        },
    )

    assert summary["evidence_status"] == EVIDENCE_STATUS_SAME_CONTRACT
    assert summary["contract_hash"] == "ranking-contract"


def test_empty_validation_with_current_contract_is_waiting_not_insufficient() -> None:
    contract = build_research_signal_contract(
        asset_type="etf",
        asset_code="588220",
        signal_run_id=2,
        signal_date=date(2026, 6, 29),
        score=91.0,
        observation_label="高位观察",
        entry_timing_label="冲高别追",
        data_reliability="verified",
        source_data_time="2026-06-29",
    )

    summary = build_evidence_summary(current_contract=contract)

    assert summary["evidence_status"] == EVIDENCE_STATUS_WAITING
    assert summary["contract_hash"] == contract["contract_hash"]


def test_exit_v2_evidence_contract_is_research_only_and_stable() -> None:
    contract = build_exit_v2_evidence_contract(
        validation_run_id=7,
        signal_contract_hash="abc123",
        signal_rule_version="short_research_signal_v1",
        execution_model=EXECUTION_MODEL_INTRADAY_ALERT,
        data_cutoff=date(2026, 7, 6),
    )

    assert contract["research_only"] is True
    assert contract["approved_for_live"] is False
    assert contract["signal_contract_hash"] == "abc123"
    assert contract["execution_model"] == EXECUTION_MODEL_INTRADAY_ALERT
    assert contract["contract_hash"]


def test_exit_v2_baseline_comparison_rejects_v2_when_hold_is_better_without_drawdown_help() -> None:
    comparison = build_exit_v2_baseline_comparison(
        topn_hold={"total_return_pct": 12.0, "max_drawdown_pct": -8.0},
        current_exit={"total_return_pct": 7.0, "max_drawdown_pct": -6.5},
        guard_only={"total_return_pct": 9.0, "max_drawdown_pct": -8.2},
        exit_v2={"total_return_pct": 8.0, "max_drawdown_pct": -7.5},
    )

    assert comparison["research_only"] is True
    assert comparison["approved_for_live"] is False
    assert comparison["v2_underperforms_hold"] is True
    assert comparison["baselines"]["topn_fixed_hold"]["total_return_pct"] == 12.0
