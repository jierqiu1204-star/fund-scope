from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2_evidence_storage import (
    persist_v2_locked_case_evidence_once,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    V2LockedCaseEvidence,
)


def test_locked_case_storage_rejects_identity_substitution_before_db_access() -> None:
    evidence = V2LockedCaseEvidence(
        case_date=date(2026, 8, 3),
        expected_codes=("002131", "603039"),
        observed_codes=(),
        status="locked_case_unavailable",
        source_registry_hash="s" * 64,
        data_available=False,
    )
    with pytest.raises(ValueError, match="holdout identity"):
        asyncio.run(
            persist_v2_locked_case_evidence_once(
                None,  # type: ignore[arg-type]
                evidence,
                holdout_identity="holdout-substitution",
            )
        )
