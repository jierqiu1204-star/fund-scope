"""Independent ETF freshness, score warm-up, and replay-depth lanes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

DAILY_FRESHNESS_SCOPE = "daily_freshness"
SCORE_WARMUP_SCOPE = "history_depth_61"
TELEMETRY_DEPTH_SCOPE = "history_depth_180_telemetry"
DEEP_TELEMETRY_DEPTH_SCOPE = "history_depth_500_telemetry"
SCORE_WARMUP_SESSIONS = 61
TELEMETRY_DEPTH_SESSIONS = 180
DEEP_TELEMETRY_DEPTH_SESSIONS = 500
REQUIRED_INDEPENDENT_DATES = 20


def history_depth_scope(contract_hash: str) -> str:
    if len(contract_hash) != 64:
        raise ValueError("contract_hash must be a sha256 digest")
    return f"history_depth_required:{contract_hash}"


def derived_replay_depth_sessions(
    *,
    horizons: Sequence[int],
    warmup_sessions: int = SCORE_WARMUP_SESSIONS,
    required_independent_dates: int = REQUIRED_INDEPENDENT_DATES,
) -> int:
    frozen_horizons = tuple(sorted(set(horizons)))
    if (
        not frozen_horizons
        or any(horizon <= 0 for horizon in frozen_horizons)
        or warmup_sessions <= 0
        or required_independent_dates <= 0
    ):
        raise ValueError("replay depth inputs must be positive")
    longest_horizon = max(frozen_horizons)
    return (
        warmup_sessions
        + required_independent_dates * (longest_horizon + 2)
        - 1
    )


def contiguous_tail_session_count(
    trading_sessions: Sequence[date],
    eligible_dates: Iterable[date],
) -> int:
    sessions = tuple(trading_sessions)
    if sessions != tuple(sorted(set(sessions))):
        raise ValueError("trading_sessions must be unique and chronological")
    available = set(eligible_dates)
    count = 0
    for session_date in reversed(sessions):
        if session_date not in available:
            break
        count += 1
    return count


@dataclass(frozen=True)
class EtfHistoryLaneCoverage:
    scope: str
    required_sessions: int
    authoritative: bool
    expected_count: int
    attempted_count: int
    covered_count: int
    eligible_count: int
    excluded_count: int
    coverage_ratio: float
    covered_codes: tuple[str, ...]
    pending_codes: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalProductionSnapshotReadiness:
    compatible_source_date_count: int
    required_source_date_count: int
    ready: bool


@dataclass(frozen=True)
class EtfHistoryReadiness:
    daily_freshness: EtfHistoryLaneCoverage
    history_depth_61: EtfHistoryLaneCoverage
    contract_depth: EtfHistoryLaneCoverage
    telemetry_depth_180: EtfHistoryLaneCoverage
    telemetry_depth_500: EtfHistoryLaneCoverage
    historical_production_snapshots: HistoricalProductionSnapshotReadiness
    score_eligible_codes: tuple[str, ...]


def _lane(
    *,
    scope: str,
    required_sessions: int,
    authoritative: bool,
    codes: tuple[str, ...],
    attempted_codes: set[str],
    depth_by_code: Mapping[str, int],
) -> EtfHistoryLaneCoverage:
    covered = tuple(
        code for code in codes if depth_by_code.get(code, 0) >= required_sessions
    )
    pending = tuple(code for code in codes if code not in set(covered))
    expected_count = len(codes)
    covered_count = len(covered)
    return EtfHistoryLaneCoverage(
        scope=scope,
        required_sessions=required_sessions,
        authoritative=authoritative,
        expected_count=expected_count,
        attempted_count=sum(code in attempted_codes for code in codes),
        covered_count=covered_count,
        eligible_count=covered_count,
        excluded_count=expected_count - covered_count,
        coverage_ratio=(covered_count / expected_count if expected_count else 0.0),
        covered_codes=covered,
        pending_codes=pending,
    )


def build_etf_history_readiness(
    *,
    decision_eligible_codes: Sequence[str],
    trading_sessions: Sequence[date],
    eligible_dates_by_code: Mapping[str, frozenset[date]],
    contract_hash: str,
    horizons: Sequence[int],
    attempted_codes: Sequence[str] = (),
    compatible_production_source_dates: Sequence[date] = (),
) -> EtfHistoryReadiness:
    codes = tuple(sorted(set(decision_eligible_codes)))
    sessions = tuple(trading_sessions)
    if not sessions or sessions != tuple(sorted(set(sessions))):
        raise ValueError("trading_sessions must be non-empty, unique, and chronological")
    depth_by_code = {
        code: contiguous_tail_session_count(
            sessions,
            eligible_dates_by_code.get(code, frozenset()),
        )
        for code in codes
    }
    attempted = set(attempted_codes)
    contract_depth = derived_replay_depth_sessions(horizons=horizons)
    daily = _lane(
        scope=DAILY_FRESHNESS_SCOPE,
        required_sessions=1,
        authoritative=True,
        codes=codes,
        attempted_codes=attempted,
        depth_by_code=depth_by_code,
    )
    warmup = _lane(
        scope=SCORE_WARMUP_SCOPE,
        required_sessions=SCORE_WARMUP_SESSIONS,
        authoritative=True,
        codes=codes,
        attempted_codes=attempted,
        depth_by_code=depth_by_code,
    )
    derived = _lane(
        scope=history_depth_scope(contract_hash),
        required_sessions=contract_depth,
        authoritative=True,
        codes=codes,
        attempted_codes=attempted,
        depth_by_code=depth_by_code,
    )
    telemetry = _lane(
        scope=TELEMETRY_DEPTH_SCOPE,
        required_sessions=TELEMETRY_DEPTH_SESSIONS,
        authoritative=False,
        codes=codes,
        attempted_codes=attempted,
        depth_by_code=depth_by_code,
    )
    deep_telemetry = _lane(
        scope=DEEP_TELEMETRY_DEPTH_SCOPE,
        required_sessions=DEEP_TELEMETRY_DEPTH_SESSIONS,
        authoritative=False,
        codes=codes,
        attempted_codes=attempted,
        depth_by_code=depth_by_code,
    )
    source_date_count = len(set(compatible_production_source_dates))
    return EtfHistoryReadiness(
        daily_freshness=daily,
        history_depth_61=warmup,
        contract_depth=derived,
        telemetry_depth_180=telemetry,
        telemetry_depth_500=deep_telemetry,
        historical_production_snapshots=HistoricalProductionSnapshotReadiness(
            compatible_source_date_count=source_date_count,
            required_source_date_count=REQUIRED_INDEPENDENT_DATES,
            ready=source_date_count >= REQUIRED_INDEPENDENT_DATES,
        ),
        score_eligible_codes=warmup.covered_codes,
    )
