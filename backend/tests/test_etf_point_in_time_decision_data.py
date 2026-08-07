from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.services.strategy_lab.etf_point_in_time_decision_data import (
    etf_decision_data_snapshot_from_source,
    latest_ready_etf_decision_data_snapshot,
)


def _source(*, source_id: int = 7, warmup: float = 0.90) -> SimpleNamespace:
    return SimpleNamespace(
        id=source_id,
        source_signal_run_id=70,
        as_of_trade_date=date(2026, 8, 4),
        replay_visibility_cutoff=datetime(2026, 8, 4, 23, 0),
        cutoff_timezone="Asia/Shanghai",
        readiness_policy_version="etf_readiness_policy_v3",
        target_date_coverage_ratio=0.95,
        warmup_coverage_ratio=warmup,
        source_snapshot_hash="s" * 64,
        universe_manifest_hash="u" * 64,
        input_snapshot_hash="i" * 64,
        provider_health_hash="p" * 64,
        source_context_json={
            "provider_health_identity": {
                "provider_health": {
                    "providers": {
                        "eastmoney": {"circuit_state": "closed"},
                        "backup": {"status": "failed"},
                    }
                }
            }
        },
    )


def test_snapshot_adapter_exposes_neutral_data_provenance_only() -> None:
    snapshot = etf_decision_data_snapshot_from_source(_source())  # type: ignore[arg-type]

    assert snapshot.snapshot_id == 7
    assert snapshot.provider_health == (
        ("backup", "unavailable"),
        ("eastmoney", "healthy"),
    )
    assert snapshot.decision_cutoff.isoformat() == "2026-08-04T23:00:00+08:00"
    evidence = snapshot.evidence_dict()
    assert evidence["provenance_kind"] == "persisted_etf_pit_decision_data"
    assert "source_signal_run_id" not in evidence
    assert "ranking_score" not in evidence


class _ScalarResult:
    def __init__(self, values: tuple[SimpleNamespace, ...]) -> None:
        self._values = values

    def all(self) -> tuple[SimpleNamespace, ...]:
        return self._values


class _Session:
    def __init__(self, values: tuple[SimpleNamespace, ...]) -> None:
        self._values = values

    async def scalars(self, _statement) -> _ScalarResult:
        return _ScalarResult(self._values)


@pytest.mark.asyncio
async def test_latest_snapshot_skips_sources_below_their_persisted_policy() -> None:
    session = _Session((_source(source_id=8, warmup=0.89), _source(source_id=7)))

    snapshot = await latest_ready_etf_decision_data_snapshot(
        session,  # type: ignore[arg-type]
        as_of=datetime.fromisoformat("2026-08-05T09:10:00+08:00"),
    )

    assert snapshot is not None
    assert snapshot.snapshot_id == 7


@pytest.mark.asyncio
async def test_latest_snapshot_requires_an_aware_cutoff() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await latest_ready_etf_decision_data_snapshot(
            _Session(()),  # type: ignore[arg-type]
            as_of=datetime(2026, 8, 5, 9, 10),
        )
