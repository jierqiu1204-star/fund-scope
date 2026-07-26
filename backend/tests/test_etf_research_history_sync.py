from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.workflows import etf_research_history_sync as coordinator


def _lane(
    *,
    scope: str,
    required_sessions: int,
    ratio: float,
    authoritative: bool,
) -> dict[str, Any]:
    required_trade_dates = [
        (date(2024, 12, 1) + timedelta(days=offset)).isoformat()
        for offset in range(required_sessions)
    ]
    return {
        "scope": scope,
        "required_sessions": required_sessions,
        "authoritative": authoritative,
        "expected_count": 2,
        "covered_count": int(ratio * 2),
        "excluded_count": 2 - int(ratio * 2),
        "coverage_ratio": ratio,
        "pending_codes": [] if ratio >= 0.95 else ["510001"],
        "cohort_codes": ["510001", "510002"],
        "cohort_hash": "d" * 64,
        "required_trade_dates": required_trade_dates,
        "listing_metadata_gate_passed": True,
        "listing_metadata_coverage_ratio": 1.0,
        "completion_gate_passed": ratio >= 0.95,
        "completion_blockers": (
            [] if ratio >= 0.95 else ["adjusted_research_coverage_below_95pct"]
        ),
    }


def _readiness(
    *,
    daily: float = 1.0,
    warmup: float = 1.0,
    contract: float = 0.5,
    telemetry: float = 0.0,
) -> dict[str, Any]:
    return {
        "contract_hash": "a" * 64,
        "universe": {
            "snapshot_hash": "b" * 64,
            "codes": ["510001", "510002"],
        },
        "daily_freshness": _lane(
            scope="daily_freshness",
            required_sessions=1,
            ratio=daily,
            authoritative=True,
        ),
        "history_depth_61": _lane(
            scope="history_depth_61",
            required_sessions=61,
            ratio=warmup,
            authoritative=True,
        ),
        "contract_depth": _lane(
            scope="history_depth_required:" + "a" * 64,
            required_sessions=300,
            ratio=contract,
            authoritative=True,
        ),
        "telemetry_depth_500": _lane(
            scope="history_depth_500_telemetry",
            required_sessions=500,
            ratio=telemetry,
            authoritative=False,
        ),
    }


class _Fetcher:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _Fetcher:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _sync_result() -> SimpleNamespace:
    return SimpleNamespace(
        status="partial",
        stop_reason="continuation_required",
        attempted_codes=("510001",),
        completed_codes=("510001",),
        exclusions=(),
        fetched_rows=300,
        persisted_rows=300,
        elapsed_seconds=8.0,
        peak_rss_bytes=128 * 1024 * 1024,
        last_durable_checkpoint={"identity_hash": "c" * 64},
    )


def test_adaptive_research_profile_grows_and_backs_off() -> None:
    healthy = {
        "status": "partial",
        "stop_reason": "continuation_required",
        "profile_max_codes": 10,
        "provider_attempt_count": 10,
        "last_completed_code": "510010",
        "elapsed_seconds": 20.0,
        "peak_rss_bytes": 128 * 1024 * 1024,
        "exclusions": [],
    }
    assert coordinator.choose_research_depth_batch_size([]) == 10
    assert coordinator.choose_research_depth_batch_size([healthy]) == 10
    assert coordinator.choose_research_depth_batch_size([healthy, healthy]) == 15
    assert (
        coordinator.choose_research_depth_batch_size(
            [
                {**healthy, "profile_max_codes": 15},
                {**healthy, "profile_max_codes": 10},
            ]
        )
        == 15
    )
    assert (
        coordinator.choose_research_depth_batch_size(
            [{**healthy, "profile_max_codes": 20, "stop_reason": "rss_limit"}]
        )
        == 10
    )
    assert (
        coordinator.choose_research_depth_batch_size(
            [{**healthy, "profile_max_codes": 5, "stop_reason": "worker_deadline"}]
        )
        == 5
    )
    assert (
        coordinator.choose_research_depth_batch_size(
            [
                {
                    **healthy,
                    "profile_max_codes": 20,
                    "status": "failed",
                    "stop_reason": "page_persistence_error:RuntimeError",
                }
            ]
        )
        == 10
    )


@pytest.mark.asyncio
async def test_research_depth_yields_to_either_publication_gate(monkeypatch) -> None:
    async def fake_lease(_session: object) -> None:
        return None

    async def fake_readiness(_session: object, **_kwargs: Any) -> dict[str, Any]:
        return _readiness(daily=1.0, warmup=0.89)

    async def forbidden(*_args: object, **_kwargs: Any) -> None:
        raise AssertionError("provider work must not start below publication gates")

    monkeypatch.setattr(coordinator, "_active_history_lease", fake_lease)
    monkeypatch.setattr(coordinator, "read_etf_history_readiness", fake_readiness)
    monkeypatch.setattr(coordinator, "run_bounded_history_sync_slice", forbidden)

    result = await coordinator.run_post_publication_etf_research_history_slice(
        object(),  # type: ignore[arg-type]
        target_date=date(2026, 7, 24),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "publication_priority_active"
    assert result["publication_gates"]["thresholds"] == {
        "daily_freshness": 0.95,
        "history_depth_61": 0.90,
    }


@pytest.mark.asyncio
async def test_research_completion_waits_for_authoritative_listing_metadata(
    monkeypatch,
) -> None:
    async def fake_lease(_session: object) -> None:
        return None

    async def fake_readiness(_session: object, **_kwargs: Any) -> dict[str, Any]:
        result = _readiness(contract=1.0)
        result["contract_depth"].update(
            {
                "completion_gate_passed": False,
                "listing_metadata_gate_passed": False,
                "listing_metadata_coverage_ratio": 0.94,
                "completion_blockers": ["authoritative_listing_metadata_below_95pct"],
            }
        )
        return result

    async def forbidden(*_args: object, **_kwargs: Any) -> None:
        raise AssertionError("no provider work remains for the known cohort")

    monkeypatch.setattr(coordinator, "_active_history_lease", fake_lease)
    monkeypatch.setattr(coordinator, "read_etf_history_readiness", fake_readiness)
    monkeypatch.setattr(coordinator, "run_bounded_history_sync_slice", forbidden)

    result = await coordinator.run_post_publication_etf_research_history_slice(
        object(),  # type: ignore[arg-type]
        target_date=date(2026, 7, 24),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "authoritative_listing_metadata_below_95pct"


@pytest.mark.asyncio
async def test_research_depth_skips_weekend_active_lease_and_invalid_universe(
    monkeypatch,
) -> None:
    weekend = await coordinator.run_post_publication_etf_research_history_slice(
        object(),  # type: ignore[arg-type]
        target_date=date(2026, 7, 25),
    )
    assert weekend["reason"] == "not_etf_exchange_trading_day"

    async def active_lease(_session: object) -> object:
        return object()

    monkeypatch.setattr(coordinator, "_active_history_lease", active_lease)
    overlap = await coordinator.run_post_publication_etf_research_history_slice(
        object(),  # type: ignore[arg-type]
        target_date=date(2026, 7, 24),
    )
    assert overlap["reason"] == "overlapping_history_worker_lease"

    async def no_lease(_session: object) -> None:
        return None

    async def invalid_readiness(
        _session: object,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = _readiness()
        result["universe"] = {"snapshot_hash": "", "codes": []}
        result["contract_depth"]["cohort_hash"] = ""
        return result

    monkeypatch.setattr(coordinator, "_active_history_lease", no_lease)
    monkeypatch.setattr(
        coordinator,
        "read_etf_history_readiness",
        invalid_readiness,
    )
    invalid = await coordinator.run_post_publication_etf_research_history_slice(
        object(),  # type: ignore[arg-type]
        target_date=date(2026, 7, 24),
    )
    assert invalid["reason"] == "point_in_time_universe_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contract_ratio", "expected_scope", "expected_sessions"),
    [
        (0.5, "history_depth_required:" + "a" * 64, 300),
        (1.0, "history_depth_500_telemetry", 500),
    ],
)
async def test_coordinator_runs_300_before_500_with_safe_bounded_profile(
    monkeypatch,
    contract_ratio: float,
    expected_scope: str,
    expected_sessions: int,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_lease(_session: object) -> None:
        return None

    async def fake_recent(_session: object, **_kwargs: Any) -> list[object]:
        return []

    async def fake_readiness(_session: object, **_kwargs: Any) -> dict[str, Any]:
        return _readiness(
            warmup=0.90,
            contract=contract_ratio,
            telemetry=0.0,
        )

    async def fake_run(
        _session: object,
        *,
        request: Any,
        fetcher: object,
    ) -> SimpleNamespace:
        captured["request"] = request
        captured["fetcher"] = fetcher
        return _sync_result()

    monkeypatch.setattr(coordinator, "_active_history_lease", fake_lease)
    monkeypatch.setattr(coordinator, "_recent_lane_slices", fake_recent)
    monkeypatch.setattr(coordinator, "read_etf_history_readiness", fake_readiness)
    monkeypatch.setattr(coordinator, "PublicationAdjustedHistoryFetcher", _Fetcher)
    monkeypatch.setattr(coordinator, "run_bounded_history_sync_slice", fake_run)

    result = await coordinator.run_post_publication_etf_research_history_slice(
        object(),  # type: ignore[arg-type]
        target_date=date(2026, 7, 24),
    )

    request = captured["request"]
    assert request.scope == expected_scope
    assert request.required_sessions == expected_sessions
    assert request.selection_policy == "research_depth"
    assert request.max_codes == 10
    assert request.page_size == 500
    assert request.max_rows == 5_000
    assert request.rss_limit_bytes == 512 * 1024 * 1024
    assert request.provider_timeout_seconds == 6.0
    assert request.process_deadline_seconds == 60.0
    assert result["sync"]["attempted_count"] == 1
    assert "attempted_codes" not in result["sync"]
