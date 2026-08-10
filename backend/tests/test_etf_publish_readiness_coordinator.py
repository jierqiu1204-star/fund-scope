from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.workflows.etf_publish_readiness as coordinator
from app.services.short_etf.bounded_history_sync import BoundedHistorySyncResult
from app.services.short_research.snapshot_publication import SnapshotPublicationError

TRADE_DATE = date(2026, 7, 20)
DECISION_CUTOFF = datetime(2026, 7, 20, 15, 0)


def _readiness(daily: float, warmup: float) -> dict[str, Any]:
    codes = ["510050", "159915"]
    return {
        "target_date": TRADE_DATE.isoformat(),
        "contract_hash": "a" * 64,
        "universe": {
            "snapshot_hash": "b" * 64,
            "expected_count": len(codes),
            "codes": codes,
        },
        "daily_freshness": {
            "scope": "daily_freshness",
            "required_sessions": 1,
            "expected_count": len(codes),
            "covered_count": round(len(codes) * daily),
            "excluded_count": len(codes) - round(len(codes) * daily),
            "coverage_ratio": daily,
            "pending_codes": codes,
        },
        "history_depth_61": {
            "scope": "history_depth_61",
            "required_sessions": 61,
            "expected_count": len(codes),
            "covered_count": round(len(codes) * warmup),
            "excluded_count": len(codes) - round(len(codes) * warmup),
            "coverage_ratio": warmup,
            "pending_codes": codes,
        },
        "history_publication_gate_passed": daily >= 0.95 and warmup >= 0.90,
        "complete_publication_gate_passed": daily >= 0.95 and warmup >= 0.90,
        "publication_coverage_thresholds": {
            "daily_freshness": 0.95,
            "history_depth_61_preview": 0.90,
            "history_depth_61_complete": 0.90,
        },
        "coverage_policy_mode": (
            "blocked"
            if daily < 0.95 or warmup < 0.90
            else "complete"
        ),
        "blockers": [],
    }


def _slice_result(*, stop_reason: str | None = "continuation_required") -> BoundedHistorySyncResult:
    return BoundedHistorySyncResult(
        status="partial" if stop_reason else "complete",
        stop_reason=stop_reason,
        attempted_codes=("510050",),
        completed_codes=(),
        exclusions=(),
        fetched_rows=61,
        persisted_rows=61,
        inserted_rows=61,
        updated_rows=0,
        unchanged_rows=0,
        excluded_rows=0,
        max_page_rows=61,
        elapsed_seconds=1.0,
        peak_rss_bytes=32 * 1024 * 1024,
        sql_statements=5,
        max_page_sql_statements=5,
        retries=0,
        last_durable_checkpoint={"active_code": "510050"},
    )


class _Fetcher:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _Fetcher:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    finishes: list[dict[str, Any]] = []

    async def authoritative(*_args: Any, **_kwargs: Any) -> tuple[bool, str | None]:
        return True, None

    async def acquire(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def finish(_session: object, _date: date, status: str, details: dict[str, Any]) -> None:
        finishes.append({"status": status, "details": details})

    async def selection(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(state="waiting", run=None)

    async def tracked(*_args: Any, **_kwargs: Any) -> list[str]:
        return []

    async def restored(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def recent(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def run_slice(*_args: Any, **_kwargs: Any) -> BoundedHistorySyncResult:
        return _slice_result()

    monkeypatch.setattr(coordinator, "_latest_authoritative_universe", authoritative)
    monkeypatch.setattr(coordinator, "try_acquire_etf_daily_workflow_lock", acquire)
    monkeypatch.setattr(coordinator, "finish_etf_daily_workflow_lock", finish)
    monkeypatch.setattr(
        coordinator,
        "resolve_current_etf_ranking_surface_snapshot",
        selection,
    )
    monkeypatch.setattr(coordinator, "active_tracked_etf_codes", tracked)
    monkeypatch.setattr(coordinator, "read_latest_compatible_provider_health", restored)
    monkeypatch.setattr(coordinator, "_recent_publication_slices", recent)
    monkeypatch.setattr(coordinator, "PublicationAdjustedHistoryFetcher", _Fetcher)
    monkeypatch.setattr(coordinator, "run_bounded_history_sync_slice", run_slice)
    return finishes


def test_profile_promotes_only_after_three_healthy_slices_and_demotes_on_degradation() -> None:
    healthy = {
        "elapsed_seconds": 10.0,
        "peak_rss_bytes": 64 * 1024 * 1024,
        "stop_reason": "continuation_required",
        "last_completed_code": "510050",
        "provider_health": {
            "providers": {
                "eastmoney": {
                    "circuit_state": "closed",
                    "last_error": None,
                }
            }
        },
    }
    assert coordinator.choose_publication_profile([healthy, healthy]).name == "conservative"
    assert (
        coordinator.choose_publication_profile([healthy, healthy, healthy]).name
        == "maximum"
    )
    degraded = {**healthy, "stop_reason": "rss_limit"}
    assert (
        coordinator.choose_publication_profile([degraded, healthy, healthy]).name
        == "conservative"
    )


def test_profile_treats_durable_unseasoned_checkpoint_as_healthy_progress() -> None:
    factual_exclusion = {
        "elapsed_seconds": 10.0,
        "peak_rss_bytes": 64 * 1024 * 1024,
        "stop_reason": "continuation_required",
        "last_completed_code": None,
        "active_code": "159068",
        "exclusions": [["159068", "insufficient_contiguous_adjusted_sessions"]],
        "provider_health": {
            "providers": {
                "eastmoney": {
                    "circuit_state": "closed",
                    "last_error": None,
                }
            }
        },
    }

    assert (
        coordinator.choose_publication_profile(
            [factual_exclusion, factual_exclusion, factual_exclusion]
        ).name
        == "maximum"
    )


def test_profile_cadence_is_one_or_half_minute_without_boundary_drift() -> None:
    now = datetime(2026, 7, 20, 15, 30, 30)
    half_minute_ago = SimpleNamespace(
        started_at=datetime(2026, 7, 20, 15, 30, 1, 900_000),
        status="partial",
    )
    assert (
        coordinator._profile_cadence_due(
            [half_minute_ago],
            profile=coordinator.CONSERVATIVE_PUBLICATION_PROFILE,
            now=now,
        )
        is False
    )
    assert (
        coordinator._profile_cadence_due(
            [half_minute_ago],
            profile=coordinator.MAXIMUM_PUBLICATION_PROFILE,
            now=now,
        )
        is True
    )

    running = SimpleNamespace(
        started_at=datetime(2026, 7, 20, 15, 0),
        status="running",
    )
    assert (
        coordinator._profile_cadence_due(
            [running],
            profile=coordinator.MAXIMUM_PUBLICATION_PROFILE,
            now=now,
        )
        is False
    )


def test_slice_gate_upper_bound_only_defers_remeasurement_far_from_gate() -> None:
    far = _readiness(0.50, 0.50)
    far["universe"]["expected_count"] = 1_500
    far["daily_freshness"]["expected_count"] = 1_500
    far["history_depth_61"]["expected_count"] = 1_500
    assert (
        coordinator._slice_could_reach_both_gates(far, max_codes=20)
        is False
    )

    near = _readiness(0.94, 0.89)
    near["universe"]["expected_count"] = 1_500
    assert coordinator._slice_could_reach_both_gates(near, max_codes=20) is True

    missing_denominator = _readiness(0.50, 0.50)
    missing_denominator["universe"]["expected_count"] = 0
    assert (
        coordinator._slice_could_reach_both_gates(
            missing_denominator,
            max_codes=20,
        )
        is True
    )


@pytest.mark.asyncio
async def test_coordinator_defers_remeasurement_when_slice_cannot_reach_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    readiness = _readiness(0.50, 0.50)
    readiness["universe"]["expected_count"] = 1_500
    readiness["daily_freshness"]["expected_count"] = 1_500
    readiness["history_depth_61"]["expected_count"] = 1_500
    read_count = 0

    async def read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal read_count
        read_count += 1
        return readiness

    monkeypatch.setattr(coordinator, "read_etf_history_readiness", read)

    result = await coordinator.run_post_close_etf_publication_readiness(
        object(),  # type: ignore[arg-type]
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )

    assert read_count == 1
    assert result["status"] == "waiting"
    assert result["sync"]["readiness_remeasurement"] == (
        "deferred_below_gate_upper_bound"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"),
    [
        ((0.94, 1.0), (0.94, 1.0)),
        ((1.0, 0.89), (1.0, 0.89)),
    ],
)
async def test_coordinator_requires_both_independent_gates_and_never_publishes_below_gate(
    monkeypatch: pytest.MonkeyPatch,
    before: tuple[float, float],
    after: tuple[float, float],
) -> None:
    _patch_common(monkeypatch)
    readings = iter([_readiness(*before), _readiness(*after)])
    publish_called = False

    async def read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return next(readings)

    async def unexpected_publish(*_args: Any, **_kwargs: Any) -> None:
        nonlocal publish_called
        publish_called = True

    monkeypatch.setattr(coordinator, "read_etf_history_readiness", read)
    monkeypatch.setattr(coordinator, "generate_and_publish_etf_snapshot", unexpected_publish)

    result = await coordinator.run_post_close_etf_publication_readiness(
        object(),  # type: ignore[arg-type]
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )

    assert result["status"] == "waiting"
    assert result["publication_state"] == "not_run"
    assert result["reason"] == "adjusted_price_or_warmup_coverage_below_publication_gate"
    assert publish_called is False
    assert "codes" not in result["coverage"]["universe"]
    assert "pending_codes" not in result["coverage"]["daily_freshness"]


@pytest.mark.asyncio
async def test_coordinator_remeasures_then_publishes_once_both_gates_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    readings = iter([_readiness(0.94, 0.89), _readiness(0.95, 0.95)])
    publish_calls: list[dict[str, Any]] = []

    async def read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return next(readings)

    async def barrier(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(expected_codes=("510050", "159915"), coverage_ratio=0.95)

    async def publish(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        publish_calls.append(kwargs)
        return SimpleNamespace(
            id=9,
            status="success",
            as_of_date=TRADE_DATE,
            publication_state="published",
            summary_json={"item_count": 2, "fund_count": 0, "etf_count": 2},
        )

    monkeypatch.setattr(coordinator, "read_etf_history_readiness", read)
    monkeypatch.setattr(coordinator, "build_etf_coverage_barrier", barrier)
    monkeypatch.setattr(coordinator, "generate_and_publish_etf_snapshot", publish)
    monkeypatch.setattr(
        coordinator,
        "etf_source_availability_cutoff",
        lambda _date: datetime(2026, 7, 20, 15, 10),
    )

    result = await coordinator.run_post_close_etf_publication_readiness(
        object(),  # type: ignore[arg-type]
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )

    assert result["publication_state"] == "published"
    assert result["run_id"] == 9
    assert len(publish_calls) == 1


@pytest.mark.asyncio
async def test_coordinator_publishes_at_ninety_percent_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    readings = iter([_readiness(0.94, 0.92), _readiness(0.95, 0.92)])
    publish_calls: list[dict[str, Any]] = []

    async def read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return next(readings)

    async def barrier(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            expected_codes=("510050", "159915"),
            coverage_ratio=0.95,
        )

    async def publish(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        publish_calls.append(kwargs)
        return SimpleNamespace(
            id=11,
            status="success",
            as_of_date=TRADE_DATE,
            publication_state="published",
            summary_json={"item_count": 2, "fund_count": 0, "etf_count": 2},
        )

    monkeypatch.setattr(coordinator, "read_etf_history_readiness", read)
    monkeypatch.setattr(coordinator, "build_etf_coverage_barrier", barrier)
    monkeypatch.setattr(coordinator, "generate_and_publish_etf_snapshot", publish)
    monkeypatch.setattr(
        coordinator,
        "etf_source_availability_cutoff",
        lambda _date: datetime(2026, 7, 20, 15, 10),
    )

    result = await coordinator.run_post_close_etf_publication_readiness(
        object(),  # type: ignore[arg-type]
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )

    assert result["publication_state"] == "published"
    assert result["run_id"] == 11
    assert len(publish_calls) == 1


@pytest.mark.asyncio
async def test_coordinator_waits_on_publication_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)

    async def read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _readiness(1.0, 1.0)

    async def barrier(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(expected_codes=("510050",), coverage_ratio=1.0)

    async def publish(*_args: Any, **_kwargs: Any) -> None:
        raise SnapshotPublicationError("validation failed")

    class _Session:
        async def rollback(self) -> None:
            return None

    monkeypatch.setattr(coordinator, "read_etf_history_readiness", read)
    monkeypatch.setattr(coordinator, "build_etf_coverage_barrier", barrier)
    monkeypatch.setattr(coordinator, "generate_and_publish_etf_snapshot", publish)

    result = await coordinator.run_post_close_etf_publication_readiness(
        _Session(),  # type: ignore[arg-type]
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )

    assert result["status"] == "waiting"
    assert result["reason"].startswith("score_coverage_or_publication_gate_failed")


@pytest.mark.asyncio
async def test_coordinator_rolls_back_before_finalizing_failed_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finishes = _patch_common(monkeypatch)

    class _Session:
        rolled_back = False

        async def rollback(self) -> None:
            self.rolled_back = True

    async def fail_selection(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("provider transaction failed")

    session = _Session()
    monkeypatch.setattr(
        coordinator,
        "resolve_current_etf_ranking_surface_snapshot",
        fail_selection,
    )

    with pytest.raises(RuntimeError, match="provider transaction failed"):
        await coordinator.run_post_close_etf_publication_readiness(
            session,  # type: ignore[arg-type]
            trade_date=TRADE_DATE,
            decision_cutoff=DECISION_CUTOFF,
        )

    assert session.rolled_back is True
    assert finishes == [
        {
            "status": "failed",
            "details": {"trade_date": TRADE_DATE.isoformat()},
        }
    ]


@pytest.mark.asyncio
async def test_coordinator_stops_before_provider_work_when_already_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)

    async def selection(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            state="ready",
            run=SimpleNamespace(
                id=10,
                status="success",
                as_of_date=TRADE_DATE,
                publication_state="published",
                summary_json={"item_count": 2, "fund_count": 0, "etf_count": 2},
            )
        )

    async def unexpected_read(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("published date must not read or fetch readiness data")

    monkeypatch.setattr(
        coordinator,
        "resolve_current_etf_ranking_surface_snapshot",
        selection,
    )
    monkeypatch.setattr(coordinator, "read_etf_history_readiness", unexpected_read)

    result = await coordinator.run_post_close_etf_publication_readiness(
        object(),  # type: ignore[arg-type]
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )

    assert result["already_published"] is True
    assert result["publication_state"] == "published"


@pytest.mark.asyncio
async def test_readiness_status_exposes_compact_operational_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _readiness(0.5, 0.25)

    async def recent(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(
                status="partial",
                started_at=datetime.utcnow(),
                details_json={
                    "identity_hash": "c" * 64,
                    "stop_reason": "continuation_required",
                    "attempted_codes": ["510050"],
                    "completed_codes": [],
                    "exclusions": [["159915", "provider_timeout"]],
                    "remaining_candidate_count": 1,
                    "provider_health": {"providers": {}},
                    "elapsed_seconds": 1.5,
                    "peak_rss_bytes": 64 * 1024 * 1024,
                    "rows_per_second": 40.0,
                },
            )
        ]

    monkeypatch.setattr(coordinator, "read_etf_history_readiness", read)
    monkeypatch.setattr(coordinator, "_recent_publication_slices", recent)

    payload = await coordinator.read_publication_readiness_status(
        object(),  # type: ignore[arg-type]
        target_date=TRADE_DATE,
    )

    assert "codes" not in payload["universe"]
    assert "pending_codes" not in payload["daily_freshness"]
    assert payload["daily_freshness"]["reason_aggregates"]
    assert payload["synchronization"]["attempted_count"] == 1
    assert payload["synchronization"]["failed_count"] == 1
    assert payload["synchronization"]["checkpoint_age_seconds"] is not None
    assert payload["synchronization"]["max_codes"] == 10
    assert payload["synchronization"]["estimated_remaining_slices"] == 1
    assert payload["synchronization"]["estimated_eta_minutes"] == 1.0
    assert payload["synchronization"]["lease"]["active"] is False
    assert "checkpoint" in payload["synchronization"]
