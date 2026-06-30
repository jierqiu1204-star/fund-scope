from __future__ import annotations

from datetime import date

from app.services.etf_research_evidence import (
    EVIDENCE_STATUS_INSUFFICIENT,
    EVIDENCE_STATUS_LEGACY,
    EVIDENCE_STATUS_SAME_CONTRACT,
    EVIDENCE_STATUS_VERSION_MISMATCH,
    EVIDENCE_STATUS_WAITING,
    build_evidence_summary,
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
