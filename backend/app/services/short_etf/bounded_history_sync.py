from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any, Literal

from sqlalchemy import and_, case, event, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfAdjustedHistoryAvailability,
    EtfAdjustedPriceRevision,
    EtfPriceHistory,
    EtfSyncCursor,
    JobRun,
    TradableEtf,
)
from app.services.market_data import (
    _adjusted_revision_coherence_predicate,
    etf_decision_adjusted_provider_versions,
)
from app.services.short_etf.data import (
    _ADJUSTED_PRICE_MATERIAL_FIELDS,
    MAX_ADJUSTED_PRICE_PERSISTENCE_PAGE_ROWS,
    ProviderFetchResult,
    _research_price_fields,
    compute_etf_metric,
    fetch_etf_price_history_with_provider,
    persist_etf_price_history_page,
)

HistoryFetcher = Callable[[str, date, date], Awaitable[ProviderFetchResult]]
Clock = Callable[[], float]
RssReader = Callable[[], int | None]
PageCommitHook = Callable[[], None]
MAX_SQL_STATEMENTS_PER_PAGE = 8
HISTORY_SELECTION_POLICY: Literal["history_depth"] = "history_depth"
PUBLICATION_READINESS_SELECTION_POLICY: Literal["publication_readiness"] = (
    "publication_readiness"
)
RESEARCH_DEPTH_SELECTION_POLICY: Literal["research_depth"] = "research_depth"
HISTORY_AVAILABILITY_COOLDOWN_DAYS = 7
PUBLICATION_NEAR_COMPLETE_COOLDOWN_MINUTES = 30


@dataclass(frozen=True)
class PublicationReadinessCandidate:
    code: str
    has_target_date: bool
    warmup_depth: int
    priority: bool = False


def plan_publication_readiness_candidates(
    candidates: Sequence[PublicationReadinessCandidate],
    *,
    rotation_anchor: str | None = None,
) -> list[PublicationReadinessCandidate]:
    pending = [
        candidate
        for candidate in candidates
        if not candidate.has_target_date or candidate.warmup_depth < 61
    ]
    ordered = sorted(
        pending,
        key=lambda candidate: (
            1 if candidate.has_target_date else 0,
            -candidate.warmup_depth,
            candidate.code,
        ),
    )
    priority_codes = [candidate.code for candidate in ordered if candidate.priority]
    regular_codes = [candidate.code for candidate in ordered if not candidate.priority]
    if rotation_anchor in priority_codes:
        priority_codes = _rotate_after(priority_codes, rotation_anchor)
    else:
        regular_codes = _rotate_after(regular_codes, rotation_anchor)
    rotated_codes = [*priority_codes, *regular_codes]
    by_code = {candidate.code: candidate for candidate in ordered}
    return [by_code[code] for code in rotated_codes]


@dataclass(frozen=True)
class BoundedHistorySyncRequest:
    scope: str
    contract_hash: str
    universe_hash: str
    eligible_codes: tuple[str, ...]
    from_date: date
    to_date: date
    required_sessions: int
    max_codes: int = 10
    page_size: int = 500
    max_rows: int = 5_000
    admission_deadline_seconds: float = 45.0
    worker_deadline_seconds: float = 55.0
    process_deadline_seconds: float = 60.0
    rss_limit_bytes: int = 768 * 1024 * 1024
    provider_timeout_seconds: float = 8.0
    target_trade_date: date | None = None
    selection_policy: Literal[
        "history_depth",
        "publication_readiness",
        "research_depth",
    ] = HISTORY_SELECTION_POLICY
    provider_policy_version: str = "legacy-adjusted-provider-policy-v1"
    adjustment_contract: str = "total-return-adjusted-v1"
    price_basis: str = "total_return_adjusted"
    required_trade_dates: tuple[date, ...] = ()
    priority_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scope:
            raise ValueError("scope is required")
        _require_sha256("contract_hash", self.contract_hash)
        _require_sha256("universe_hash", self.universe_hash)
        if not self.eligible_codes:
            raise ValueError("eligible_codes must not be empty")
        if len(self.eligible_codes) > 5_000:
            raise ValueError("eligible_codes must not exceed 5000")
        if tuple(sorted(set(self.eligible_codes))) != self.eligible_codes:
            raise ValueError("eligible_codes must be sorted and unique")
        if self.from_date > self.to_date:
            raise ValueError("from_date must not be after to_date")
        if self.required_sessions <= 0:
            raise ValueError("required_sessions must be positive")
        if (
            self.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY
            and not 5 <= self.max_codes <= 20
        ):
            raise ValueError("research depth max_codes must be between 5 and 20")
        if (
            self.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY
            and not 1 <= self.max_codes <= 20
        ):
            raise ValueError(
                "publication readiness max_codes must be between 1 and 20"
            )
        if (
            self.selection_policy == HISTORY_SELECTION_POLICY
            and not 1 <= self.max_codes <= 10
        ):
            raise ValueError("max_codes must be between 1 and 10")
        if not 1 <= self.page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        if not 1 <= self.max_rows <= 5_000:
            raise ValueError("max_rows must be between 1 and 5000")
        if not (
            0 < self.admission_deadline_seconds
            < self.worker_deadline_seconds
            < self.process_deadline_seconds
            <= 60
        ):
            raise ValueError("deadlines must be ordered and process deadline must be <= 60s")
        if self.rss_limit_bytes <= 0:
            raise ValueError("rss_limit_bytes must be positive")
        if not 0 < self.provider_timeout_seconds <= 8:
            raise ValueError("provider_timeout_seconds must be between 0 and 8")
        if self.selection_policy not in {
            HISTORY_SELECTION_POLICY,
            PUBLICATION_READINESS_SELECTION_POLICY,
            RESEARCH_DEPTH_SELECTION_POLICY,
        }:
            raise ValueError("unsupported history selection policy")
        if self.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY:
            if self.target_trade_date is None:
                raise ValueError("publication readiness target_trade_date is required")
            if not self.from_date <= self.target_trade_date <= self.to_date:
                raise ValueError("publication readiness target_trade_date must be in range")
            if self.required_sessions != 61:
                raise ValueError("publication readiness requires 61 sessions")
            if self.rss_limit_bytes > 512 * 1024 * 1024:
                raise ValueError("publication readiness rss limit must not exceed 512 MiB")
            if self.provider_timeout_seconds > 6:
                raise ValueError("publication readiness provider timeout must not exceed 6 seconds")
            if (
                len(self.required_trade_dates) != self.required_sessions
                or self.required_trade_dates != tuple(sorted(set(self.required_trade_dates)))
                or self.required_trade_dates[-1] != self.target_trade_date
            ):
                raise ValueError(
                    "publication readiness requires 61 unique ordered trade dates ending at target"
                )
        if self.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
            if (
                len(self.required_trade_dates) != self.required_sessions
                or self.required_trade_dates != tuple(sorted(set(self.required_trade_dates)))
                or self.required_trade_dates[0] != self.from_date
                or self.required_trade_dates[-1] != self.to_date
            ):
                raise ValueError(
                    "research depth requires an exact unique ordered frozen trade-date calendar"
                )
            if self.rss_limit_bytes > 512 * 1024 * 1024:
                raise ValueError("research depth rss limit must not exceed 512 MiB")
            if self.provider_timeout_seconds > 6:
                raise ValueError(
                    "research depth provider timeout must not exceed 6 seconds"
                )
        if not self.provider_policy_version:
            raise ValueError("provider_policy_version is required")
        if not self.adjustment_contract:
            raise ValueError("adjustment_contract is required")
        if self.price_basis != "total_return_adjusted":
            raise ValueError("bounded history sync requires total_return_adjusted price basis")

    @property
    def required_calendar_hash(self) -> str:
        payload = {
            "scope": self.scope,
            "selection_policy": self.selection_policy,
            "required_sessions": self.required_sessions,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "required_trade_dates": [
                trade_date.isoformat() for trade_date in self.required_trade_dates
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def identity_hash(self) -> str:
        payload = {
            "schema_version": "bounded_etf_history_sync_v1",
            "scope": self.scope,
            "contract_hash": self.contract_hash,
            "universe_hash": self.universe_hash,
            "eligible_codes": self.eligible_codes,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "required_sessions": self.required_sessions,
            "max_codes": self.max_codes,
            "page_size": self.page_size,
            "max_rows": self.max_rows,
            "target_trade_date": (
                self.target_trade_date.isoformat() if self.target_trade_date else None
            ),
            "selection_policy": self.selection_policy,
            "provider_policy_version": self.provider_policy_version,
            "adjustment_contract": self.adjustment_contract,
            "price_basis": self.price_basis,
            "required_trade_dates": [
                trade_date.isoformat() for trade_date in self.required_trade_dates
            ],
            "priority_codes": sorted(set(self.priority_codes)),
            "admission_deadline_seconds": self.admission_deadline_seconds,
            "worker_deadline_seconds": self.worker_deadline_seconds,
            "process_deadline_seconds": self.process_deadline_seconds,
            "rss_limit_bytes": self.rss_limit_bytes,
            "provider_timeout_seconds": self.provider_timeout_seconds,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class BoundedHistorySyncResult:
    status: str
    stop_reason: str | None
    attempted_codes: tuple[str, ...]
    completed_codes: tuple[str, ...]
    exclusions: tuple[tuple[str, str], ...]
    fetched_rows: int
    persisted_rows: int
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int
    excluded_rows: int
    max_page_rows: int
    elapsed_seconds: float
    peak_rss_bytes: int
    sql_statements: int
    max_page_sql_statements: int
    retries: int
    last_durable_checkpoint: dict[str, Any] | None
    rss_limit_bytes: int = 0
    configured_rss_limit_bytes: int = 0
    baseline_rss_bytes: int | None = None
    current_rss_bytes: int | None = None
    slice_peak_current_rss_bytes: int | None = None
    lifetime_peak_rss_bytes: int | None = None
    rss_delta_bytes: int | None = None


def _require_sha256(name: str, value: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    try:
        valid = int(value, 16) >= 0 and value == value.lower()
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _remaining_hard_seconds(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


def _invalidate_session(session: AsyncSession) -> None:
    bind = session.bind
    if bind is not None and bind.dialect.name == "sqlite":
        return
    try:
        session.sync_session.invalidate()
    except Exception:  # noqa: BLE001
        pass


async def _rollback_before(session: AsyncSession, *, deadline: float) -> bool:
    remaining = _remaining_hard_seconds(deadline)
    if remaining <= 0:
        _invalidate_session(session)
        return False
    try:
        await asyncio.wait_for(session.rollback(), timeout=remaining)
    except Exception:  # noqa: BLE001
        _invalidate_session(session)
        return False
    return True


async def _run_before(
    operation: Callable[[], Awaitable[Any]],
    *,
    deadline: float,
    timeout_message: str,
) -> Any:
    remaining = _remaining_hard_seconds(deadline)
    if remaining <= 0:
        raise TimeoutError(timeout_message)
    try:
        return await asyncio.wait_for(operation(), timeout=remaining)
    except TimeoutError as exc:
        raise TimeoutError(timeout_message) from exc


def _windows_process_memory_bytes() -> tuple[int, int] | None:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        ):
            return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
    except (AttributeError, OSError):
        return None
    return None


def _darwin_current_rss_bytes() -> int | None:
    try:
        import ctypes

        class ProcTaskInfo(ctypes.Structure):
            _fields_ = [
                ("virtual_size", ctypes.c_uint64),
                ("resident_size", ctypes.c_uint64),
                ("total_user", ctypes.c_uint64),
                ("total_system", ctypes.c_uint64),
                ("threads_user", ctypes.c_uint64),
                ("threads_system", ctypes.c_uint64),
                ("policy", ctypes.c_int32),
                ("faults", ctypes.c_int32),
                ("pageins", ctypes.c_int32),
                ("cow_faults", ctypes.c_int32),
                ("messages_sent", ctypes.c_int32),
                ("messages_received", ctypes.c_int32),
                ("syscalls_mach", ctypes.c_int32),
                ("syscalls_unix", ctypes.c_int32),
                ("csw", ctypes.c_int32),
                ("threadnum", ctypes.c_int32),
                ("numrunning", ctypes.c_int32),
                ("priority", ctypes.c_int32),
            ]

        info = ProcTaskInfo()
        libproc = ctypes.CDLL("libproc.dylib", use_errno=True)
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        libproc.proc_pidinfo.restype = ctypes.c_int
        size = ctypes.sizeof(info)
        read_size = libproc.proc_pidinfo(
            os.getpid(),
            4,  # PROC_PIDTASKINFO
            0,
            ctypes.byref(info),
            size,
        )
        if read_size == size and info.resident_size > 0:
            return int(info.resident_size)
    except (AttributeError, OSError):
        return None
    return None


def _default_current_rss_reader() -> int | None:
    if sys.platform == "win32":
        memory = _windows_process_memory_bytes()
        return memory[0] if memory is not None else None
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/statm", encoding="ascii") as statm:
                fields = statm.read(128).split()
            if len(fields) < 2:
                return None
            resident_pages = int(fields[1])
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            rss = resident_pages * page_size
            return rss if rss > 0 else None
        except (OSError, TypeError, ValueError):
            return None
    if sys.platform == "darwin":
        return _darwin_current_rss_bytes()
    return None


def _default_lifetime_peak_rss_reader() -> int | None:
    if sys.platform == "win32":
        memory = _windows_process_memory_bytes()
        return memory[1] if memory is not None else None
    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = int(rss if sys.platform == "darwin" else rss * 1024)
        return peak if peak > 0 else None
    except (ImportError, OSError):
        return None


def read_process_rss_bytes() -> int:
    return int(_default_current_rss_reader() or 0)


def read_process_lifetime_peak_rss_bytes() -> int:
    return int(_default_lifetime_peak_rss_reader() or 0)


def _read_positive_rss(reader: RssReader) -> int | None:
    try:
        value = reader()
        rss = int(value) if value is not None else 0
    except (OSError, TypeError, ValueError):
        return None
    return rss if rss > 0 else None


def _sample_current_rss(
    totals: dict[str, Any],
    reader: RssReader,
) -> int | None:
    rss = _read_positive_rss(reader)
    totals["current_rss_bytes"] = rss
    if rss is not None:
        peak = totals.get("slice_peak_current_rss_bytes")
        totals["slice_peak_current_rss_bytes"] = max(int(peak or 0), rss)
        totals["peak_rss_bytes"] = totals["slice_peak_current_rss_bytes"]
    baseline = totals.get("baseline_rss_bytes")
    slice_peak = totals.get("slice_peak_current_rss_bytes")
    totals["rss_delta_bytes"] = (
        max(0, int(slice_peak) - int(baseline))
        if baseline is not None and slice_peak is not None
        else None
    )
    return rss


def _chunks(
    rows: Sequence[dict[str, float | str]],
    size: int,
) -> Iterator[Sequence[dict[str, float | str]]]:
    for offset in range(0, len(rows), size):
        yield rows[offset : offset + size]


def _accepted_adjusted_provider_filter() -> Any:
    return or_(
        *(
            and_(
                EtfPriceHistory.data_provider == provider,
                EtfPriceHistory.provider_version == version,
                EtfPriceHistory.adjustment_version == version,
            )
            for provider, version in etf_decision_adjusted_provider_versions()
        )
    )


def _accepted_adjusted_revision_provider_filter() -> Any:
    return or_(
        *(
            and_(
                EtfAdjustedPriceRevision.data_provider == provider,
                EtfAdjustedPriceRevision.provider_version == version,
                EtfAdjustedPriceRevision.adjustment_version == version,
            )
            for provider, version in etf_decision_adjusted_provider_versions()
        )
    )


def _latest_eligible_revision_rows(
    *,
    codes: Sequence[str],
    trade_dates: Sequence[date],
    source_cutoff: datetime | None = None,
) -> Any:
    """Return the latest visible revision row per provider and session.

    Provider rows are ranked before the eligibility filter so a newer invalid
    revision cannot make an older valid revision look current.  Callers can
    then count one provider's dates without treating a cross-provider union as
    a coherent history.  A cutoff is applied before ranking, matching the PIT
    reader's visibility rule.
    """

    accepted_pairs = etf_decision_adjusted_provider_versions()
    accepted_providers = tuple(sorted({provider for provider, _version in accepted_pairs}))
    cutoff = _utc_naive(source_cutoff) if source_cutoff is not None else None
    visible_filters = [
        EtfAdjustedPriceRevision.etf_code.in_(tuple(codes)),
        EtfAdjustedPriceRevision.trade_date.in_(tuple(trade_dates)),
        func.lower(EtfAdjustedPriceRevision.data_provider).in_(accepted_providers),
    ]
    if cutoff is not None:
        visible_filters.extend(
            (
                EtfAdjustedPriceRevision.first_seen_at <= cutoff,
                EtfAdjustedPriceRevision.observed_at <= cutoff,
            )
        )
    visible = (
        select(
            EtfAdjustedPriceRevision.etf_code.label("etf_code"),
            EtfAdjustedPriceRevision.trade_date.label("trade_date"),
            func.lower(EtfAdjustedPriceRevision.data_provider).label("provider_key"),
            EtfAdjustedPriceRevision.provider_version.label("provider_version"),
            EtfAdjustedPriceRevision.adjustment_version.label("adjustment_version"),
            EtfAdjustedPriceRevision.research_price_basis.label("research_price_basis"),
            EtfAdjustedPriceRevision.research_adjusted_value.label("adjusted_close"),
            EtfAdjustedPriceRevision.open.label("raw_open"),
            EtfAdjustedPriceRevision.high.label("raw_high"),
            EtfAdjustedPriceRevision.low.label("raw_low"),
            EtfAdjustedPriceRevision.close.label("raw_close"),
            EtfAdjustedPriceRevision.volume.label("volume"),
            EtfAdjustedPriceRevision.decision_eligible.label("decision_eligible"),
            EtfAdjustedPriceRevision.source_timestamp.label("source_timestamp"),
            EtfAdjustedPriceRevision.first_seen_at.label("first_seen_at"),
            EtfAdjustedPriceRevision.observed_at.label("observed_at"),
            EtfAdjustedPriceRevision.revision_hash.label("revision_hash"),
            func.row_number()
            .over(
                partition_by=(
                    EtfAdjustedPriceRevision.etf_code,
                    EtfAdjustedPriceRevision.trade_date,
                    func.lower(EtfAdjustedPriceRevision.data_provider),
                ),
                order_by=(
                    EtfAdjustedPriceRevision.first_seen_at.desc(),
                    EtfAdjustedPriceRevision.observed_at.desc(),
                    EtfAdjustedPriceRevision.id.desc(),
                ),
            )
            .label("revision_rank"),
        )
        .where(*visible_filters)
        .subquery()
    )
    return (
        select(visible)
        .where(
            visible.c.revision_rank == 1,
            _adjusted_revision_coherence_predicate(
                visible,
                accepted_pairs=accepted_pairs,
                source_cutoff=cutoff,
            ),
        )
        .subquery()
    )


def _revision_value_is_positive_finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _revision_hash_is_valid(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _revision_row_is_coherent(
    row: Mapping[str, Any],
    *,
    accepted_pairs: Mapping[str, str],
    source_cutoff: datetime | None = None,
) -> bool:
    """Apply replay row invariants after selecting the latest revision."""

    provider = str(row["provider_key"] or "").lower()
    expected_version = accepted_pairs.get(provider)
    values = (
        row["raw_open"],
        row["raw_high"],
        row["raw_low"],
        row["raw_close"],
        row["volume"],
        row["adjusted_close"],
    )
    if (
        row["decision_eligible"] is not True
        or row["research_price_basis"] != "total_return_adjusted"
        or expected_version is None
        or row["provider_version"] != expected_version
        or row["adjustment_version"] != expected_version
        or any(not _revision_value_is_positive_finite(value) for value in values)
        or row["source_timestamp"] is None
        or row["first_seen_at"] is None
        or row["observed_at"] is None
        or not _revision_hash_is_valid(row["revision_hash"])
    ):
        return False
    if (
        source_cutoff is not None
        and _utc_naive(row["source_timestamp"]) > _utc_naive(source_cutoff)
    ):
        return False
    return not (
        float(row["raw_high"]) < max(
            float(row["raw_open"]),
            float(row["raw_close"]),
            float(row["raw_low"]),
        )
        or float(row["raw_low"]) > min(
            float(row["raw_open"]),
            float(row["raw_close"]),
            float(row["raw_high"]),
        )
    )


async def _coherent_revision_dates(
    session: AsyncSession,
    *,
    codes: Sequence[str],
    trade_dates: Sequence[date],
    source_cutoff: datetime | None = None,
) -> dict[str, dict[str, set[date]]]:
    if not codes or not trade_dates:
        return {}
    cutoff = _utc_naive(source_cutoff) if source_cutoff is not None else None
    latest = _latest_eligible_revision_rows(
        codes=codes,
        trade_dates=trade_dates,
        source_cutoff=cutoff,
    )
    accepted_pairs = dict(etf_decision_adjusted_provider_versions())
    dates_by_code: dict[str, dict[str, set[date]]] = {}
    rows = await session.stream(select(latest).execution_options(yield_per=500))
    async for row in rows.mappings():
        if not _revision_row_is_coherent(
            row,
            accepted_pairs=accepted_pairs,
            source_cutoff=cutoff,
        ):
            continue
        dates_by_code.setdefault(str(row["etf_code"]), {}).setdefault(
            str(row["provider_key"]), set()
        ).add(row["trade_date"])
    return dates_by_code


async def _coherent_revision_depths(
    session: AsyncSession,
    *,
    codes: Sequence[str],
    windows: Sequence[Sequence[date]],
    source_cutoff: datetime | None = None,
) -> dict[str, tuple[int, ...]]:
    """Count coherent same-provider depths for all requested windows in one query."""

    if not codes or not windows:
        return {}
    targets = tuple(
        sorted({trade_date for window in windows for trade_date in window})
    )
    if not targets:
        return {}
    latest = _latest_eligible_revision_rows(
        codes=codes,
        trade_dates=targets,
        source_cutoff=source_cutoff,
    )
    counts = tuple(
        func.sum(
            case((latest.c.trade_date.in_(tuple(window)), 1), else_=0)
        ).label(f"window_{index}")
        for index, window in enumerate(windows)
    )
    result = await session.execute(
        select(latest.c.etf_code, latest.c.provider_key, *counts).group_by(
            latest.c.etf_code,
            latest.c.provider_key,
        )
    )
    depths: dict[str, list[int]] = {}
    for row in result.mappings():
        code = str(row["etf_code"])
        current = depths.setdefault(code, [0] * len(windows))
        for index in range(len(windows)):
            current[index] = max(current[index], int(row[f"window_{index}"] or 0))
    return {code: tuple(values) for code, values in depths.items()}


async def _eligible_depths(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
) -> dict[str, int]:
    projection_depths: dict[str, int] = {}
    if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
        target_sessions = request.required_trade_dates
    else:
        target_sessions = tuple(
            await session.scalars(
                select(EtfPriceHistory.trade_date)
                .where(
                    EtfPriceHistory.trade_date >= request.from_date,
                    EtfPriceHistory.trade_date <= request.to_date,
                    EtfPriceHistory.decision_eligible.is_(True),
                    EtfPriceHistory.research_price_basis == request.price_basis,
                    _accepted_adjusted_provider_filter(),
                )
                .distinct()
                .order_by(EtfPriceHistory.trade_date.desc())
                .limit(request.required_sessions)
            )
        )
    if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
        coherent_depths = await _coherent_revision_depths(
            session,
            codes=request.eligible_codes,
            windows=(target_sessions,),
        )
        depths = {
            code: coherent_depths.get(code, (0,))[0]
            for code in request.eligible_codes
        }
        depth_statement = None
    else:
        depths = {}
        depth_statement = (
            select(
                EtfPriceHistory.etf_code,
                func.count(func.distinct(EtfPriceHistory.trade_date)),
            )
            .where(
                EtfPriceHistory.etf_code.in_(request.eligible_codes),
                EtfPriceHistory.trade_date.in_(target_sessions),
                EtfPriceHistory.decision_eligible.is_(True),
                EtfPriceHistory.research_price_basis == request.price_basis,
                _accepted_adjusted_provider_filter(),
            )
            .group_by(EtfPriceHistory.etf_code)
        )
    if depth_statement is not None:
        depth_rows = (await session.execute(depth_statement)).all()
        depths = {str(code): int(count or 0) for code, count in depth_rows}
    if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
        projection_rows = (
            await session.execute(
                select(
                    EtfPriceHistory.etf_code,
                    func.count(func.distinct(EtfPriceHistory.trade_date)),
                )
                .where(
                    EtfPriceHistory.etf_code.in_(request.eligible_codes),
                    EtfPriceHistory.trade_date.in_(target_sessions),
                    EtfPriceHistory.decision_eligible.is_(True),
                    EtfPriceHistory.research_price_basis == request.price_basis,
                    EtfPriceHistory.source_timestamp.is_not(None),
                    _accepted_adjusted_provider_filter(),
                )
                .group_by(EtfPriceHistory.etf_code)
            )
        ).all()
        projection_depths = {
            str(code): int(count or 0) for code, count in projection_rows
        }
    watchlist_rows = (
        await session.execute(
            select(TradableEtf.code, TradableEtf.is_watchlist).where(
                TradableEtf.code.in_(request.eligible_codes)
            )
        )
    ).all()

    def order_key(item: tuple[str, bool]) -> tuple[int | bool | str, ...]:
        code, is_watchlist = item
        if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
            migration_ready = (
                projection_depths.get(code, 0) >= request.required_sessions
            )
            return (not migration_ready, -depths.get(code, 0), not is_watchlist, code)
        return (not is_watchlist, depths.get(code, 0), code)

    ordered = sorted(
        ((str(code), bool(is_watchlist)) for code, is_watchlist in watchlist_rows),
        key=order_key,
    )
    return {code: depths.get(code, 0) for code, _is_watchlist in ordered}


async def _missing_required_trade_dates(
    session: AsyncSession,
    *,
    code: str,
    request: BoundedHistorySyncRequest,
) -> tuple[date, ...]:
    if request.selection_policy != RESEARCH_DEPTH_SELECTION_POLICY:
        return ()
    missing, _provider = await _required_trade_date_state(
        session,
        code=code,
        request=request,
    )
    return missing


async def _required_trade_date_state(
    session: AsyncSession,
    *,
    code: str,
    request: BoundedHistorySyncRequest,
) -> tuple[tuple[date, ...], str | None]:
    if request.selection_policy != RESEARCH_DEPTH_SELECTION_POLICY:
        return (), None
    coherent_dates = await _coherent_revision_dates(
        session,
        codes=(code,),
        trade_dates=request.required_trade_dates,
    )
    provider_dates = coherent_dates.get(code, {})
    if not provider_dates:
        return request.required_trade_dates, None
    best_provider, existing_dates = max(
        provider_dates.items(),
        key=lambda item: (len(item[1]), item[0]),
    )
    return (
        tuple(
            trade_date
            for trade_date in request.required_trade_dates
            if trade_date not in existing_dates
        ),
        best_provider,
    )


async def _backfill_revisions_from_projection(
    session: AsyncSession,
    *,
    code: str,
    request: BoundedHistorySyncRequest,
) -> int:
    """Promote trusted legacy projections without inventing a receipt timestamp."""

    existing_revision_dates = select(EtfAdjustedPriceRevision.trade_date).where(
        EtfAdjustedPriceRevision.etf_code == code,
        EtfAdjustedPriceRevision.trade_date.in_(request.required_trade_dates),
        EtfAdjustedPriceRevision.decision_eligible.is_(True),
        EtfAdjustedPriceRevision.research_price_basis == request.price_basis,
        _accepted_adjusted_revision_provider_filter(),
    )
    projections = list(
        (
            await session.scalars(
                select(EtfPriceHistory)
                .where(
                    EtfPriceHistory.etf_code == code,
                    EtfPriceHistory.trade_date.in_(request.required_trade_dates),
                    EtfPriceHistory.trade_date.not_in(existing_revision_dates),
                    EtfPriceHistory.decision_eligible.is_(True),
                    EtfPriceHistory.research_price_basis == request.price_basis,
                    EtfPriceHistory.source_timestamp.is_not(None),
                    _accepted_adjusted_provider_filter(),
                )
                .order_by(EtfPriceHistory.trade_date.asc())
            )
        ).all()
    )
    if not projections:
        return 0
    revision_rows = 0
    for offset in range(0, len(projections), MAX_ADJUSTED_PRICE_PERSISTENCE_PAGE_ROWS):
        persisted = await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=tuple(
                (
                    row.trade_date,
                    {
                        **{
                            field: getattr(row, field)
                            for field in _ADJUSTED_PRICE_MATERIAL_FIELDS
                        },
                        "source_timestamp": row.source_timestamp,
                    },
                )
                for row in projections[
                    offset : offset + MAX_ADJUSTED_PRICE_PERSISTENCE_PAGE_ROWS
                ]
            ),
        )
        revision_rows += persisted.revision_rows
    return revision_rows


async def _fetch_history_window(
    fetcher: HistoryFetcher,
    *,
    code: str,
    from_date: date,
    to_date: date,
    missing_trade_dates: tuple[date, ...],
) -> ProviderFetchResult:
    fetch_with_minimum = getattr(fetcher, "fetch_with_minimum", None)
    if callable(fetch_with_minimum) and missing_trade_dates:
        return await fetch_with_minimum(
            code,
            from_date,
            to_date,
            minimum_eligible_rows=len(missing_trade_dates),
            required_trade_dates=missing_trade_dates,
        )
    return await fetcher(code, from_date, to_date)


async def _publication_readiness_candidates(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
) -> list[PublicationReadinessCandidate]:
    priority_codes = set(request.priority_codes)
    rows = (
        await session.execute(
            select(
                TradableEtf.code,
                TradableEtf.is_watchlist,
                func.sum(
                    case(
                        (EtfPriceHistory.trade_date == request.target_trade_date, 1),
                        else_=0,
                    )
                ),
                func.count(func.distinct(EtfPriceHistory.trade_date)),
            )
            .outerjoin(
                EtfPriceHistory,
                (EtfPriceHistory.etf_code == TradableEtf.code)
                & (EtfPriceHistory.trade_date.in_(request.required_trade_dates))
                & (EtfPriceHistory.decision_eligible.is_(True))
                & (EtfPriceHistory.research_price_basis == request.price_basis)
                & _accepted_adjusted_provider_filter(),
            )
            .where(TradableEtf.code.in_(request.eligible_codes))
            .group_by(TradableEtf.code, TradableEtf.is_watchlist)
        )
    ).all()
    return [
        PublicationReadinessCandidate(
            code=str(code),
            has_target_date=bool(target_count),
            warmup_depth=int(warmup_depth or 0),
            priority=bool(is_watchlist) or str(code) in priority_codes,
        )
        for code, is_watchlist, target_count, warmup_depth in rows
    ]


def _rotate_after(codes: list[str], last_code: str | None) -> list[str]:
    if not codes or last_code not in codes:
        return codes
    position = codes.index(last_code) + 1
    return codes[position:] + codes[:position]


def _rotate_within_depth_bucket(
    codes: list[str],
    *,
    depths: Mapping[str, int],
    last_code: str | None,
) -> list[str]:
    if not codes or last_code not in codes:
        return codes
    anchor_depth = depths.get(str(last_code), 0)
    bucket_positions = [
        index for index, code in enumerate(codes) if depths.get(code, 0) == anchor_depth
    ]
    if not bucket_positions:
        return codes
    start = bucket_positions[0]
    stop = bucket_positions[-1] + 1
    bucket = codes[start:stop]
    return codes[:start] + _rotate_after(bucket, last_code) + codes[stop:]


async def _active_lease(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
) -> JobRun | None:
    leases = (
        await session.scalars(
        select(JobRun)
        .where(
            JobRun.job_name.like("etf_history_continuation:%"),
            JobRun.status == "running",
        )
        .order_by(JobRun.id.desc())
        )
    ).all()
    maximum_age = max(request.process_deadline_seconds * 2, 120.0)
    stale_before = _utcnow() - timedelta(seconds=maximum_age)
    stale_found = False
    for lease in leases:
        if lease.started_at >= stale_before:
            return lease
        lease.status = "failed"
        lease.finished_at = _utcnow()
        lease.error_message = "stale_worker_lease"
        lease.details_json = {
            **(lease.details_json or {}),
            "stop_reason": "stale_worker_lease",
        }
        stale_found = True
    if stale_found:
        await session.commit()
    return None


async def _previous_attempt_anchor(
    session: AsyncSession,
    *,
    job_name: str,
    request: BoundedHistorySyncRequest,
) -> str | None:
    previous = await session.scalar(
        select(JobRun)
        .where(JobRun.job_name == job_name, JobRun.status == "partial")
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    if (
        previous is not None
        and (previous.details_json or {}).get("identity_hash")
        == request.identity_hash
    ):
        attempted = (previous.details_json or {}).get("attempted_codes")
        if isinstance(attempted, list) and attempted:
            return str(attempted[-1])
    if request.selection_policy not in {
        PUBLICATION_READINESS_SELECTION_POLICY,
        RESEARCH_DEPTH_SELECTION_POLICY,
    }:
        return None
    cursor = await session.get(EtfSyncCursor, request.scope)
    if cursor is None:
        return None
    return cursor.last_priority_code or cursor.last_regular_code


async def _active_history_cooldowns(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
) -> dict[str, datetime]:
    if request.selection_policy not in {
        PUBLICATION_READINESS_SELECTION_POLICY,
        RESEARCH_DEPTH_SELECTION_POLICY,
    }:
        return {}
    now = _utcnow()
    rows = (
        await session.execute(
            select(
                EtfAdjustedHistoryAvailability.etf_code,
                EtfAdjustedHistoryAvailability.retry_after,
                EtfAdjustedHistoryAvailability.observed_at,
                EtfAdjustedHistoryAvailability.evidence_json,
            ).where(
                EtfAdjustedHistoryAvailability.etf_code.in_(
                    request.eligible_codes
                ),
                EtfAdjustedHistoryAvailability.provider_policy_version
                == request.provider_policy_version,
                EtfAdjustedHistoryAvailability.scope == request.scope,
                EtfAdjustedHistoryAvailability.required_calendar_hash
                == request.required_calendar_hash,
                EtfAdjustedHistoryAvailability.status
                == "source_history_shortfall",
                EtfAdjustedHistoryAvailability.retry_after.is_not(None),
                EtfAdjustedHistoryAvailability.retry_after > now,
            )
        )
    ).all()
    active: dict[str, datetime] = {}
    for code, retry_after, observed_at, evidence_json in rows:
        if retry_after is None:
            continue
        evidence = evidence_json if isinstance(evidence_json, Mapping) else {}
        requested = evidence.get("requested_sessions")
        covered = evidence.get("covered_required_sessions")
        if (
            request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY
            and isinstance(requested, int)
            and requested > 0
            and isinstance(covered, int)
            and covered >= requested - 1
            and observed_at is not None
        ):
            retry_after = min(
                retry_after,
                observed_at
                + timedelta(minutes=PUBLICATION_NEAR_COMPLETE_COOLDOWN_MINUTES),
            )
        if retry_after > now:
            active[str(code)] = retry_after
    return active


def _eligible_provider_observation(
    provider_result: ProviderFetchResult,
    *,
    request: BoundedHistorySyncRequest,
    requested_from: date,
    requested_to: date,
) -> tuple[tuple[date, ...], str, str] | None:
    source_timestamp = _utcnow()
    eligible_rows: list[tuple[date, str, str]] = []
    for row in provider_result.rows:
        try:
            trade_date = date.fromisoformat(str(row["date"]))
            fields = _research_price_fields(
                row,
                provider=provider_result.provider,
                source_timestamp=source_timestamp,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not requested_from <= trade_date <= requested_to
            or fields.get("decision_eligible") is not True
            or fields.get("research_price_basis") != request.price_basis
        ):
            continue
        provider_version = str(fields.get("provider_version") or "")
        adjustment_version = str(fields.get("adjustment_version") or "")
        if not provider_version or not adjustment_version:
            continue
        eligible_rows.append(
            (trade_date, provider_version, adjustment_version)
        )
    if not eligible_rows:
        accepted_version = dict(etf_decision_adjusted_provider_versions()).get(
            provider_result.provider
        )
        if not provider_result.rows and accepted_version:
            return (), accepted_version, accepted_version
        return None
    dates = tuple(sorted({item[0] for item in eligible_rows}))
    return dates, eligible_rows[0][1], eligible_rows[0][2]


async def _record_history_availability(
    session: AsyncSession,
    *,
    code: str,
    request: BoundedHistorySyncRequest,
    provider_result: ProviderFetchResult,
    requested_from: date,
    requested_to: date,
    requested_trade_dates: tuple[date, ...],
    depth_complete: bool,
) -> None:
    if request.selection_policy not in {
        PUBLICATION_READINESS_SELECTION_POLICY,
        RESEARCH_DEPTH_SELECTION_POLICY,
    }:
        return
    observation = _eligible_provider_observation(
        provider_result,
        request=request,
        requested_from=requested_from,
        requested_to=requested_to,
    )
    if observation is None:
        accepted_version = dict(etf_decision_adjusted_provider_versions()).get(
            provider_result.provider
        )
        if not accepted_version:
            return
        eligible_dates = ()
        provider_version = adjustment_version = accepted_version
    else:
        eligible_dates, provider_version, adjustment_version = observation
    returned_eligible_session_count = len(eligible_dates)
    effective_required_dates = (
        request.required_trade_dates
        if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY
        else requested_trade_dates or request.required_trade_dates
    )
    if not eligible_dates:
        effective_required_dates = request.required_trade_dates
        if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
            coherent_dates = await _coherent_revision_dates(
                session,
                codes=(code,),
                trade_dates=effective_required_dates,
            )
            eligible_dates = tuple(
                sorted(
                    max(
                        coherent_dates.get(code, {}).values(),
                        key=len,
                        default=set(),
                    )
                )
            )
        else:
            eligible_dates = tuple(
                await session.scalars(
                    select(EtfAdjustedPriceRevision.trade_date)
                    .where(
                        EtfAdjustedPriceRevision.etf_code == code,
                        EtfAdjustedPriceRevision.trade_date.in_(effective_required_dates),
                        EtfAdjustedPriceRevision.decision_eligible.is_(True),
                        EtfAdjustedPriceRevision.research_price_basis
                        == request.price_basis,
                        _accepted_adjusted_revision_provider_filter(),
                    )
                    .distinct()
                    .order_by(EtfAdjustedPriceRevision.trade_date.asc())
                )
            )
        if not eligible_dates:
            return
    now = _utcnow()
    covered_required_sessions = len(
        set(eligible_dates).intersection(effective_required_dates)
    )
    provider_depth_sufficient = depth_complete
    status = (
        "sufficient"
        if provider_depth_sufficient
        else "source_history_shortfall"
    )
    near_complete_publication = (
        request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY
        and bool(effective_required_dates)
        and covered_required_sessions >= len(effective_required_dates) - 1
    )
    retry_after = None
    if not provider_depth_sufficient:
        retry_after = now + (
            timedelta(minutes=PUBLICATION_NEAR_COMPLETE_COOLDOWN_MINUTES)
            if near_complete_publication
            else timedelta(days=HISTORY_AVAILABILITY_COOLDOWN_DAYS)
        )
    key = (
        code,
        request.provider_policy_version,
        request.scope,
        request.required_calendar_hash,
    )
    row = await session.get(EtfAdjustedHistoryAvailability, key)
    values = {
        "provider": provider_result.provider,
        "provider_version": provider_version,
        "adjustment_version": adjustment_version,
        "requested_from": requested_from,
        "requested_to": requested_to,
        "earliest_eligible_date": eligible_dates[0],
        "latest_eligible_date": eligible_dates[-1],
        "eligible_session_count": len(eligible_dates),
        "status": status,
        "observed_at": now,
        "retry_after": retry_after,
        "evidence_json": {
            "inferred_listing_date": False,
            "requested_sessions": len(effective_required_dates),
            "returned_eligible_sessions": returned_eligible_session_count,
            "covered_required_sessions": covered_required_sessions,
            "post_persist_depth_complete": depth_complete,
            "scope": request.scope,
            "required_calendar_hash": request.required_calendar_hash,
            "price_basis": request.price_basis,
        },
    }
    if row is None:
        session.add(
            EtfAdjustedHistoryAvailability(
                etf_code=code,
                provider_policy_version=request.provider_policy_version,
                scope=request.scope,
                required_calendar_hash=request.required_calendar_hash,
                **values,
            )
        )
        return
    for field, value in values.items():
        setattr(row, field, value)


async def read_latest_compatible_provider_health(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
) -> dict[str, Any]:
    run = await session.scalar(
        select(JobRun)
        .where(JobRun.job_name == f"etf_history_continuation:{request.scope}")
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    details = run.details_json or {} if run is not None else {}
    if details.get("identity_hash") != request.identity_hash:
        return {}
    health = details.get("provider_health")
    return dict(health) if isinstance(health, dict) else {}


def _row_values(
    row: dict[str, float | str],
    *,
    provider: str,
    source_timestamp: datetime,
) -> tuple[date, dict[str, Any]]:
    trade_date = date.fromisoformat(str(row["date"]))
    values: dict[str, Any] = {
        "trade_date": trade_date,
        "open": float(row.get("open", 0.0)),
        "high": float(row.get("high", 0.0)),
        "low": float(row.get("low", 0.0)),
        "close": float(row.get("close", 0.0)),
        "volume": float(row.get("volume", 0.0)),
        "turnover": float(row.get("turnover", 0.0)),
        "pct_change": float(row.get("pct_change", 0.0)),
    }
    values.update(
        _research_price_fields(
            row,
            provider=provider,
            source_timestamp=source_timestamp,
        )
    )
    return trade_date, values


async def _persist_pages(
    session: AsyncSession,
    *,
    job: JobRun,
    code: str,
    provider_result: ProviderFetchResult,
    request: BoundedHistorySyncRequest,
    requested_from: date,
    requested_to: date,
    requested_trade_dates: tuple[date, ...],
    started: float,
    clock: Clock,
    rss_reader: RssReader,
    totals: dict[str, Any],
    before_page_commit: PageCommitHook | None,
    hard_process_deadline: float,
) -> tuple[int, dict[str, Any] | None, str | None]:
    rows = list(provider_result.rows[: max(0, request.max_rows - totals["fetched_rows"])])
    totals["fetched_rows"] += len(rows)
    persisted_for_code = 0
    checkpoint: dict[str, Any] | None = None
    source_timestamp = _utcnow()
    requested_date_set = set(requested_trade_dates)

    for page in _chunks(
        rows,
        min(request.page_size, MAX_ADJUSTED_PRICE_PERSISTENCE_PAGE_ROWS),
    ):
        elapsed = clock() - started
        rss = _sample_current_rss(totals, rss_reader)
        if elapsed >= request.worker_deadline_seconds:
            return persisted_for_code, checkpoint, "worker_deadline"
        if elapsed >= request.process_deadline_seconds:
            return persisted_for_code, checkpoint, "process_deadline"
        if rss is None:
            return persisted_for_code, checkpoint, "current_rss_unavailable"
        if rss > request.rss_limit_bytes:
            return persisted_for_code, checkpoint, "rss_limit"

        page_sql_statements = 0
        connection = await session.connection()
        sync_connection = connection.sync_connection
        previous_checkpoint = checkpoint
        previous_persisted_for_code = persisted_for_code
        rollback_counts = {
            key: totals[key]
            for key in (
                "persisted_rows",
                "inserted_rows",
                "updated_rows",
                "unchanged_rows",
                "excluded_rows",
                "max_page_rows",
            )
        }

        def count_statement(*_args: Any) -> None:
            nonlocal page_sql_statements
            page_sql_statements += 1
            if page_sql_statements > MAX_SQL_STATEMENTS_PER_PAGE:
                raise RuntimeError("history_sync_page_sql_limit_exceeded")

        event.listen(sync_connection, "before_cursor_execute", count_statement)
        try:
            normalized_by_date: dict[date, dict[str, Any]] = {}
            last_date: date | None = None
            for row in page:
                trade_date, values = _row_values(
                    row,
                    provider=provider_result.provider,
                    source_timestamp=source_timestamp,
                )
                if not requested_from <= trade_date <= requested_to:
                    totals["excluded_rows"] += 1
                    continue
                if (
                    request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY
                    and trade_date not in requested_date_set
                ):
                    totals["excluded_rows"] += 1
                    continue
                last_date = trade_date
                previous = normalized_by_date.get(trade_date)
                if previous is not None:
                    totals["excluded_rows"] += 1
                    if previous["decision_eligible"] and not values["decision_eligible"]:
                        continue
                normalized_by_date[trade_date] = values

            normalized = list(normalized_by_date.items())
            persisted = await persist_etf_price_history_page(
                session,
                etf_code=code,
                rows=normalized,
            )
            totals["inserted_rows"] += persisted.inserted_rows
            totals["updated_rows"] += persisted.updated_rows
            totals["unchanged_rows"] += persisted.unchanged_rows
            totals["excluded_rows"] += persisted.projection_skipped_rows
            totals["sql_statements"] += persisted.sql_statements

            page_persisted = persisted.inserted_rows + persisted.updated_rows
            persisted_for_code += page_persisted
            totals["persisted_rows"] += page_persisted
            totals["max_page_rows"] = max(totals["max_page_rows"], len(normalized))
            checkpoint = {
                "identity_hash": request.identity_hash,
                "scope": request.scope,
                "active_code": code,
                "last_trade_date": last_date.isoformat() if last_date else None,
                "provider": provider_result.provider,
                "fetched_rows": totals["fetched_rows"],
                "persisted_rows": totals["persisted_rows"],
                "inserted_rows": totals["inserted_rows"],
                "updated_rows": totals["updated_rows"],
                "unchanged_rows": totals["unchanged_rows"],
                "excluded_rows": totals["excluded_rows"],
                "elapsed_seconds": round(clock() - started, 6),
                "peak_rss_bytes": totals["peak_rss_bytes"],
                "rss_limit_bytes": request.rss_limit_bytes,
                "configured_rss_limit_bytes": request.rss_limit_bytes,
                "baseline_rss_bytes": totals["baseline_rss_bytes"],
                "current_rss_bytes": totals["current_rss_bytes"],
                "slice_peak_current_rss_bytes": totals[
                    "slice_peak_current_rss_bytes"
                ],
                "lifetime_peak_rss_bytes": totals["lifetime_peak_rss_bytes"],
                "rss_delta_bytes": totals["rss_delta_bytes"],
                "sql_statements": totals["sql_statements"],
                "provider_health": provider_result.provider_health,
            }
            job.details_json = checkpoint
            if before_page_commit is not None:
                before_page_commit()
            await session.commit()
            totals["sql_statements"] += 1
            totals["max_page_sql_statements"] = max(
                totals["max_page_sql_statements"],
                page_sql_statements,
            )
            del normalized, normalized_by_date
        except (Exception, asyncio.CancelledError):
            await _rollback_before(session, deadline=hard_process_deadline)
            for key, value in rollback_counts.items():
                totals[key] = value
            persisted_for_code = previous_persisted_for_code
            checkpoint = previous_checkpoint
            raise
        finally:
            event.remove(sync_connection, "before_cursor_execute", count_statement)

    return persisted_for_code, checkpoint, None


async def _depth_is_complete(
    session: AsyncSession,
    *,
    code: str,
    request: BoundedHistorySyncRequest,
) -> bool:
    if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
        coherent_depths = await _coherent_revision_depths(
            session,
            codes=(code,),
            windows=(request.required_trade_dates,),
        )
        return coherent_depths.get(code, (0,))[0] == request.required_sessions

    target_sessions = (
        select(EtfPriceHistory.trade_date)
        .where(
            EtfPriceHistory.trade_date >= request.from_date,
            EtfPriceHistory.trade_date <= request.to_date,
            EtfPriceHistory.decision_eligible.is_(True),
            EtfPriceHistory.research_price_basis == request.price_basis,
        )
        .distinct()
        .order_by(EtfPriceHistory.trade_date.desc())
        .limit(request.required_sessions)
        .subquery()
    )
    target_count, covered_count = (
        await session.execute(
            select(
                select(func.count()).select_from(target_sessions).scalar_subquery(),
                func.count(func.distinct(EtfPriceHistory.trade_date)),
            ).where(
                EtfPriceHistory.etf_code == code,
                EtfPriceHistory.trade_date.in_(select(target_sessions.c.trade_date)),
                EtfPriceHistory.decision_eligible.is_(True),
                EtfPriceHistory.research_price_basis == request.price_basis,
                _accepted_adjusted_provider_filter(),
            )
        )
    ).one()
    return (
        int(target_count or 0) == request.required_sessions
        and int(covered_count or 0) == request.required_sessions
    )


async def _publication_readiness_state(
    session: AsyncSession,
    *,
    code: str,
    request: BoundedHistorySyncRequest,
) -> tuple[bool, int]:
    target_count, warmup_depth = (
        await session.execute(
            select(
                func.sum(
                    case(
                        (EtfPriceHistory.trade_date == request.target_trade_date, 1),
                        else_=0,
                    )
                ),
                func.count(func.distinct(EtfPriceHistory.trade_date)),
            ).where(
                EtfPriceHistory.etf_code == code,
                EtfPriceHistory.trade_date.in_(request.required_trade_dates),
                EtfPriceHistory.decision_eligible.is_(True),
                EtfPriceHistory.research_price_basis == request.price_basis,
                _accepted_adjusted_provider_filter(),
            )
        )
    ).one()
    return bool(target_count), int(warmup_depth or 0)


async def _advance_cursor(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
    code: str,
) -> None:
    cursor = await session.get(EtfSyncCursor, request.scope)
    if cursor is None:
        cursor = EtfSyncCursor(scope=request.scope)
        session.add(cursor)
    cursor.last_regular_code = code
    cursor.last_lane = "regular"
    cursor.updated_at = _utcnow()
    await session.flush()


async def _advance_history_attempt_cursor(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
    code: str,
) -> None:
    if request.selection_policy not in {
        PUBLICATION_READINESS_SELECTION_POLICY,
        RESEARCH_DEPTH_SELECTION_POLICY,
    }:
        return
    cursor = await session.get(EtfSyncCursor, request.scope)
    if cursor is None:
        cursor = EtfSyncCursor(scope=request.scope)
        session.add(cursor)
    cursor.last_priority_code = code
    cursor.last_lane = "history_attempt"
    cursor.updated_at = _utcnow()
    await session.flush()


def _result(
    *,
    status: str,
    stop_reason: str | None,
    attempted_codes: list[str],
    completed_codes: list[str],
    exclusions: list[tuple[str, str]],
    totals: dict[str, Any],
    elapsed_seconds: float,
    checkpoint: dict[str, Any] | None,
) -> BoundedHistorySyncResult:
    return BoundedHistorySyncResult(
        status=status,
        stop_reason=stop_reason,
        attempted_codes=tuple(attempted_codes),
        completed_codes=tuple(completed_codes),
        exclusions=tuple(exclusions),
        fetched_rows=totals["fetched_rows"],
        persisted_rows=totals["persisted_rows"],
        inserted_rows=totals["inserted_rows"],
        updated_rows=totals["updated_rows"],
        unchanged_rows=totals["unchanged_rows"],
        excluded_rows=totals["excluded_rows"],
        max_page_rows=totals["max_page_rows"],
        elapsed_seconds=round(max(0.0, elapsed_seconds), 6),
        peak_rss_bytes=totals["peak_rss_bytes"],
        sql_statements=totals["sql_statements"],
        max_page_sql_statements=totals["max_page_sql_statements"],
        retries=totals["retries"],
        last_durable_checkpoint=checkpoint,
        rss_limit_bytes=totals["rss_limit_bytes"],
        configured_rss_limit_bytes=totals["configured_rss_limit_bytes"],
        baseline_rss_bytes=totals["baseline_rss_bytes"],
        current_rss_bytes=totals["current_rss_bytes"],
        slice_peak_current_rss_bytes=totals["slice_peak_current_rss_bytes"],
        lifetime_peak_rss_bytes=totals["lifetime_peak_rss_bytes"],
        rss_delta_bytes=totals["rss_delta_bytes"],
    )


async def _finalize_interrupted_job(
    session: AsyncSession,
    *,
    job_id: int,
    request: BoundedHistorySyncRequest,
    status: str,
    stop_reason: str,
    attempted_codes: Sequence[str],
    completed_codes: Sequence[str],
    exclusions: Sequence[tuple[str, str]],
    checkpoint: dict[str, Any] | None,
    totals: dict[str, Any],
    started: float,
    clock: Clock,
    hard_process_deadline: float,
) -> bool:
    if not await _rollback_before(session, deadline=hard_process_deadline):
        return False
    remaining = _remaining_hard_seconds(hard_process_deadline)
    if remaining <= 0:
        _invalidate_session(session)
        return False
    try:
        persisted_job = await asyncio.wait_for(
            session.get(JobRun, job_id),
            timeout=remaining,
        )
    except Exception:  # noqa: BLE001
        _invalidate_session(session)
        return False
    if persisted_job is None:
        return False
    persisted_job.status = status
    persisted_job.finished_at = _utcnow()
    persisted_job.error_message = stop_reason if status == "failed" else None
    persisted_job.details_json = {
        **(checkpoint or persisted_job.details_json or {}),
        "identity_hash": request.identity_hash,
        "status": status,
        "stop_reason": stop_reason,
        "attempted_codes": list(attempted_codes),
        "completed_codes": list(completed_codes),
        "exclusions": [list(item) for item in exclusions],
        "elapsed_seconds": round(clock() - started, 6),
        "fetched_rows": totals["fetched_rows"],
        "persisted_rows": totals["persisted_rows"],
        "max_page_sql_statements": totals["max_page_sql_statements"],
        "rss_limit_bytes": totals["rss_limit_bytes"],
        "configured_rss_limit_bytes": totals["configured_rss_limit_bytes"],
        "baseline_rss_bytes": totals["baseline_rss_bytes"],
        "current_rss_bytes": totals["current_rss_bytes"],
        "slice_peak_current_rss_bytes": totals["slice_peak_current_rss_bytes"],
        "lifetime_peak_rss_bytes": totals["lifetime_peak_rss_bytes"],
        "rss_delta_bytes": totals["rss_delta_bytes"],
    }
    remaining = _remaining_hard_seconds(hard_process_deadline)
    if remaining <= 0:
        _invalidate_session(session)
        return False
    try:
        await asyncio.wait_for(session.commit(), timeout=remaining)
    except Exception:  # noqa: BLE001
        _invalidate_session(session)
        return False
    return True


async def run_bounded_history_sync_slice(
    session: AsyncSession,
    *,
    request: BoundedHistorySyncRequest,
    fetcher: HistoryFetcher = fetch_etf_price_history_with_provider,
    clock: Clock = time.monotonic,
    rss_reader: RssReader = _default_current_rss_reader,
    lifetime_peak_rss_reader: RssReader = _default_lifetime_peak_rss_reader,
    before_page_commit: PageCommitHook | None = None,
) -> BoundedHistorySyncResult:
    started = clock()
    loop = asyncio.get_running_loop()
    hard_worker_deadline = loop.time() + request.worker_deadline_seconds
    hard_process_deadline = loop.time() + request.process_deadline_seconds
    baseline_rss = _read_positive_rss(rss_reader)
    lifetime_peak_rss = _read_positive_rss(lifetime_peak_rss_reader)
    totals: dict[str, Any] = {
        "fetched_rows": 0,
        "persisted_rows": 0,
        "inserted_rows": 0,
        "updated_rows": 0,
        "unchanged_rows": 0,
        "excluded_rows": 0,
        "max_page_rows": 0,
        "rss_limit_bytes": request.rss_limit_bytes,
        "configured_rss_limit_bytes": request.rss_limit_bytes,
        "baseline_rss_bytes": baseline_rss,
        "current_rss_bytes": baseline_rss,
        "slice_peak_current_rss_bytes": baseline_rss,
        "lifetime_peak_rss_bytes": lifetime_peak_rss,
        "rss_delta_bytes": 0 if baseline_rss is not None else None,
        "peak_rss_bytes": int(baseline_rss or 0),
        "sql_statements": 0,
        "max_page_sql_statements": 0,
        "retries": 0,
    }
    attempted_codes: list[str] = []
    completed_codes: list[str] = []
    exclusions: list[tuple[str, str]] = []
    checkpoint: dict[str, Any] | None = None
    latest_provider_health: dict[str, Any] | None = None
    job_name = f"etf_history_continuation:{request.scope}"

    try:
        active_lease = await _run_before(
            lambda: _active_lease(session, request=request),
            deadline=hard_worker_deadline,
            timeout_message="history sync worker deadline exhausted during lease check",
        )
        if active_lease is not None:
            return _result(
                status="skipped",
                stop_reason="overlapping_worker_lease",
                attempted_codes=attempted_codes,
                completed_codes=completed_codes,
                exclusions=exclusions,
                totals=totals,
                elapsed_seconds=clock() - started,
                checkpoint=None,
            )
        previous_attempt_anchor = await _run_before(
            lambda: _previous_attempt_anchor(
                session,
                job_name=job_name,
                request=request,
            ),
            deadline=hard_worker_deadline,
            timeout_message=(
                "history sync worker deadline exhausted reading previous checkpoint"
            ),
        )

        job = JobRun(
            job_name=job_name,
            status="running",
            started_at=_utcnow(),
            details_json={
                "identity_hash": request.identity_hash,
                "scope": request.scope,
                "contract_hash": request.contract_hash,
                "universe_hash": request.universe_hash,
                "selection_policy": request.selection_policy,
                "provider_policy_version": request.provider_policy_version,
                "profile_max_codes": request.max_codes,
                "target_trade_date": (
                    request.target_trade_date.isoformat()
                    if request.target_trade_date
                    else None
                ),
            },
        )
        session.add(job)
        await _run_before(
            session.commit,
            deadline=hard_worker_deadline,
            timeout_message="history sync worker deadline exhausted creating job",
        )
        job_id = job.id
        totals["sql_statements"] += 1

        if request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY:
            readiness_candidates = await _run_before(
                lambda: _publication_readiness_candidates(session, request=request),
                deadline=hard_worker_deadline,
                timeout_message="history sync worker deadline exhausted reading readiness",
            )
            depths: dict[str, int] = {}
        else:
            depths = await _run_before(
                lambda: _eligible_depths(session, request=request),
                deadline=hard_worker_deadline,
                timeout_message="history sync worker deadline exhausted reading depth",
            )
            readiness_candidates = []
        totals["sql_statements"] += 1
    except TimeoutError:
        await _rollback_before(session, deadline=hard_process_deadline)
        raise
    deferred_cooldowns: dict[str, datetime] = {}
    if request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY:
        planned = plan_publication_readiness_candidates(
            readiness_candidates,
            rotation_anchor=previous_attempt_anchor,
        )
        pending = [candidate.code for candidate in planned]
    else:
        pending = [code for code, depth in depths.items() if depth < request.required_sessions]
        rotation_anchor = previous_attempt_anchor if previous_attempt_anchor in pending else None
        if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
            pending = _rotate_within_depth_bucket(
                pending,
                depths=depths,
                last_code=rotation_anchor,
            )
        else:
            pending = _rotate_after(pending, rotation_anchor)
    deferred_cooldowns = await _run_before(
        lambda: _active_history_cooldowns(session, request=request),
        deadline=hard_worker_deadline,
        timeout_message=(
            "history sync worker deadline exhausted reading availability cooldowns"
        ),
    )
    if deferred_cooldowns:
        pending = [
            code for code in pending if code not in deferred_cooldowns
        ]
    selected = pending[: request.max_codes]
    stop_reason: str | None = None
    consecutive_provider_failures = 0
    fetch_windows: list[dict[str, Any]] = []
    checkpoint_reserve_seconds = (
        request.process_deadline_seconds - request.worker_deadline_seconds
    )

    for code in selected:
        elapsed = clock() - started
        rss = _sample_current_rss(totals, rss_reader)
        if elapsed >= request.admission_deadline_seconds:
            stop_reason = "admission_deadline"
            break
        if rss is None:
            stop_reason = "current_rss_unavailable"
            break
        if rss > request.rss_limit_bytes:
            stop_reason = "rss_limit"
            break
        if totals["fetched_rows"] >= request.max_rows:
            stop_reason = "row_limit"
            break

        attempted_codes.append(code)
        if request.selection_policy in {
            PUBLICATION_READINESS_SELECTION_POLICY,
            RESEARCH_DEPTH_SELECTION_POLICY,
        }:
            try:
                await _run_before(
                    partial(
                        _advance_history_attempt_cursor,
                        session,
                        request=request,
                        code=code,
                    ),
                    deadline=hard_worker_deadline,
                    timeout_message=(
                        "history sync worker deadline exhausted advancing "
                        "history attempt cursor"
                    ),
                )
                await _run_before(
                    session.commit,
                    deadline=hard_worker_deadline,
                    timeout_message=(
                        "history sync worker deadline exhausted committing "
                        "history attempt cursor"
                    ),
                )
            except TimeoutError:
                stop_reason = "worker_deadline"
                break
        requested_from = request.from_date
        requested_to = request.to_date
        requested_trade_dates = (
            request.required_trade_dates
            if request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY
            else ()
        )
        existing_coherent_provider: str | None = None
        if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
            try:
                migrated_revisions = await _run_before(
                    partial(
                        _backfill_revisions_from_projection,
                        session,
                        code=code,
                        request=request,
                    ),
                    deadline=hard_worker_deadline,
                    timeout_message=(
                        "history sync worker deadline exhausted migrating "
                        "immutable revisions"
                    ),
                )
                if migrated_revisions:
                    await _run_before(
                        session.commit,
                        deadline=hard_worker_deadline,
                        timeout_message=(
                            "history sync worker deadline exhausted committing "
                            "immutable revisions"
                        ),
                    )
                required_date_state = await _run_before(
                    partial(
                        _required_trade_date_state,
                        session,
                        code=code,
                        request=request,
                    ),
                    deadline=hard_worker_deadline,
                    timeout_message=(
                        "history sync worker deadline exhausted reading missing required sessions"
                    ),
                )
                requested_trade_dates, existing_coherent_provider = required_date_state
            except TimeoutError:
                stop_reason = "worker_deadline"
                break
            totals["sql_statements"] += 1
            if not requested_trade_dates:
                completed_codes.append(code)
                continue
            requested_from = requested_trade_dates[0]
            requested_to = requested_trade_dates[-1]
        fetch_from = (
            request.from_date
            if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY
            else requested_from
        )
        fetch_to = (
            request.to_date
            if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY
            else requested_to
        )
        fetch_windows.append(
            {
                "code": code,
                "from": fetch_from.isoformat(),
                "to": fetch_to.isoformat(),
                "missing_session_count": len(requested_trade_dates),
            }
        )
        elapsed = clock() - started
        remaining_limits = [
            request.admission_deadline_seconds - elapsed,
            request.worker_deadline_seconds - elapsed,
            _remaining_hard_seconds(hard_worker_deadline),
        ]
        if request.selection_policy == HISTORY_SELECTION_POLICY:
            remaining_limits.append(request.provider_timeout_seconds)
        remaining = min(remaining_limits)
        if remaining <= 0:
            stop_reason = "worker_deadline"
            break
        try:
            provider_result = await asyncio.wait_for(
                _fetch_history_window(
                    fetcher,
                    code=code,
                    from_date=fetch_from,
                    to_date=fetch_to,
                    missing_trade_dates=requested_trade_dates,
                ),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            await _finalize_interrupted_job(
                session,
                job_id=job_id,
                request=request,
                status="partial",
                stop_reason="worker_cancelled",
                attempted_codes=attempted_codes,
                completed_codes=completed_codes,
                exclusions=exclusions,
                checkpoint=checkpoint,
                totals=totals,
                started=started,
                clock=clock,
                hard_process_deadline=hard_process_deadline,
            )
            raise
        except TimeoutError:
            exclusions.append((code, "provider_timeout"))
            consecutive_provider_failures += 1
            if consecutive_provider_failures >= 3:
                stop_reason = "provider_circuit_open"
                break
            continue
        except Exception as exc:  # noqa: BLE001
            error_health = getattr(exc, "provider_health", None)
            if isinstance(error_health, dict):
                latest_provider_health = error_health
            reason = f"provider_error:{type(exc).__name__}:{str(exc)[:160]}"
            exclusions.append((code, reason))
            consecutive_provider_failures += 1
            if consecutive_provider_failures >= 3:
                stop_reason = "provider_circuit_open"
                break
            continue

        consecutive_provider_failures = 0
        if isinstance(provider_result.provider_health, dict):
            latest_provider_health = provider_result.provider_health
        if provider_result.fallback_used:
            totals["retries"] += 1
        elapsed = clock() - started
        if (
            request.worker_deadline_seconds - elapsed
            <= checkpoint_reserve_seconds
        ):
            stop_reason = "worker_deadline"
            del provider_result
            break

        persist_requested_from = requested_from
        persist_requested_to = requested_to
        persist_requested_trade_dates = requested_trade_dates
        if request.selection_policy == RESEARCH_DEPTH_SELECTION_POLICY:
            observation = _eligible_provider_observation(
                provider_result,
                request=request,
                requested_from=request.from_date,
                requested_to=request.to_date,
            )
            if (
                observation is not None
                and observation[0]
                and provider_result.provider.lower()
                != (existing_coherent_provider or "").lower()
            ):
                persist_requested_from = request.from_date
                persist_requested_to = request.to_date
                persist_requested_trade_dates = observation[0]

        try:
            _, page_checkpoint, page_stop = await asyncio.wait_for(
                _persist_pages(
                    session,
                    job=job,
                    code=code,
                    provider_result=provider_result,
                    request=request,
                    requested_from=persist_requested_from,
                    requested_to=persist_requested_to,
                    requested_trade_dates=persist_requested_trade_dates,
                    started=started,
                    clock=clock,
                    rss_reader=rss_reader,
                    totals=totals,
                    before_page_commit=before_page_commit,
                    hard_process_deadline=hard_process_deadline,
                ),
                timeout=min(
                    request.worker_deadline_seconds - elapsed,
                    _remaining_hard_seconds(hard_worker_deadline),
                ),
            )
        except asyncio.CancelledError:
            await _finalize_interrupted_job(
                session,
                job_id=job_id,
                request=request,
                status="partial",
                stop_reason="worker_cancelled",
                attempted_codes=attempted_codes,
                completed_codes=completed_codes,
                exclusions=exclusions,
                checkpoint=checkpoint,
                totals=totals,
                started=started,
                clock=clock,
                hard_process_deadline=hard_process_deadline,
            )
            raise
        except TimeoutError:
            await _finalize_interrupted_job(
                session,
                job_id=job_id,
                request=request,
                status="partial",
                stop_reason="worker_deadline",
                attempted_codes=attempted_codes,
                completed_codes=completed_codes,
                exclusions=exclusions,
                checkpoint=checkpoint,
                totals=totals,
                started=started,
                clock=clock,
                hard_process_deadline=hard_process_deadline,
            )
            del provider_result
            return _result(
                status="partial",
                stop_reason="worker_deadline",
                attempted_codes=attempted_codes,
                completed_codes=completed_codes,
                exclusions=exclusions,
                totals=totals,
                elapsed_seconds=clock() - started,
                checkpoint=checkpoint,
            )
        except Exception as exc:
            await _finalize_interrupted_job(
                session,
                job_id=job_id,
                request=request,
                status="failed",
                stop_reason=f"page_persistence_error:{type(exc).__name__}",
                attempted_codes=attempted_codes,
                completed_codes=completed_codes,
                exclusions=exclusions,
                checkpoint=checkpoint,
                totals=totals,
                started=started,
                clock=clock,
                hard_process_deadline=hard_process_deadline,
            )
            raise
        checkpoint = page_checkpoint or checkpoint
        elapsed = clock() - started
        remaining_worker = request.worker_deadline_seconds - elapsed
        if remaining_worker <= checkpoint_reserve_seconds:
            stop_reason = "worker_deadline"
            break
        try:
            if request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY:
                has_target_date, warmup_depth = await asyncio.wait_for(
                    _publication_readiness_state(session, code=code, request=request),
                    timeout=min(
                        remaining_worker - checkpoint_reserve_seconds,
                        _remaining_hard_seconds(hard_worker_deadline),
                    ),
                )
                depth_complete = (
                    has_target_date and warmup_depth >= request.required_sessions
                )
            else:
                has_target_date = None
                warmup_depth = None
                depth_complete = await asyncio.wait_for(
                    _depth_is_complete(session, code=code, request=request),
                    timeout=min(
                        remaining_worker - checkpoint_reserve_seconds,
                        _remaining_hard_seconds(hard_worker_deadline),
                    ),
                )
        except TimeoutError:
            await _rollback_before(session, deadline=hard_process_deadline)
            stop_reason = "worker_deadline"
            del provider_result
            break
        await _record_history_availability(
            session,
            code=code,
            request=request,
            provider_result=provider_result,
            requested_from=persist_requested_from,
            requested_to=persist_requested_to,
            requested_trade_dates=persist_requested_trade_dates,
            depth_complete=depth_complete,
        )
        del provider_result
        if depth_complete:
            completed_codes.append(code)
            elapsed = clock() - started
            remaining_worker = request.worker_deadline_seconds - elapsed
            if remaining_worker <= checkpoint_reserve_seconds:
                completed_codes.pop()
                stop_reason = "worker_deadline"
                break
            try:
                await asyncio.wait_for(
                    compute_etf_metric(
                        session,
                        code,
                        request.to_date,
                        commit=False,
                    ),
                    timeout=min(
                        remaining_worker - checkpoint_reserve_seconds,
                        _remaining_hard_seconds(hard_worker_deadline),
                    ),
                )
                elapsed = clock() - started
                remaining_worker = request.worker_deadline_seconds - elapsed
                await asyncio.wait_for(
                    _advance_cursor(session, request=request, code=code),
                    timeout=min(
                        remaining_worker,
                        _remaining_hard_seconds(hard_worker_deadline),
                    ),
                )
                elapsed = clock() - started
                remaining_worker = request.worker_deadline_seconds - elapsed
                if remaining_worker <= 0:
                    raise TimeoutError
                job.details_json = {
                    **(job.details_json or {}),
                    "identity_hash": request.identity_hash,
                    "last_completed_code": code,
                    "target_date_complete": has_target_date,
                    "warmup_depth": warmup_depth,
                    "attempted_codes": attempted_codes,
                    "completed_codes": completed_codes,
                }
                await asyncio.wait_for(
                    session.commit(),
                    timeout=min(
                        remaining_worker,
                        _remaining_hard_seconds(hard_worker_deadline),
                    ),
                )
            except TimeoutError:
                await _rollback_before(session, deadline=hard_process_deadline)
                completed_codes.pop()
                stop_reason = "worker_deadline"
                break
            totals["sql_statements"] += 2
        else:
            exclusions.append((code, "insufficient_contiguous_adjusted_sessions"))
        if page_stop is not None:
            stop_reason = page_stop
            break

    remaining_process = request.process_deadline_seconds - (clock() - started)
    remaining_process = min(
        remaining_process,
        _remaining_hard_seconds(hard_process_deadline),
    )
    if remaining_process <= 0:
        return _result(
            status="partial",
            stop_reason=stop_reason or "process_deadline",
            attempted_codes=attempted_codes,
            completed_codes=completed_codes,
            exclusions=exclusions,
            totals=totals,
            elapsed_seconds=clock() - started,
            checkpoint=checkpoint,
        )
    try:
        if request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY:
            remaining_candidates = await asyncio.wait_for(
                _publication_readiness_candidates(session, request=request),
                timeout=remaining_process,
            )
            remaining_depths = {}
        else:
            remaining_depths = await asyncio.wait_for(
                _eligible_depths(session, request=request),
                timeout=remaining_process,
            )
            remaining_candidates = []
    except TimeoutError:
        await _rollback_before(session, deadline=hard_process_deadline)
        return _result(
            status="partial",
            stop_reason=stop_reason or "process_deadline",
            attempted_codes=attempted_codes,
            completed_codes=completed_codes,
            exclusions=exclusions,
            totals=totals,
            elapsed_seconds=clock() - started,
            checkpoint=checkpoint,
        )
    totals["sql_statements"] += 1
    if request.selection_policy == PUBLICATION_READINESS_SELECTION_POLICY:
        remaining_planned = plan_publication_readiness_candidates(remaining_candidates)
        remaining_candidate_count = len(remaining_planned)
        all_complete = remaining_candidate_count == 0
    else:
        remaining_candidate_count = sum(
            depth < request.required_sessions for depth in remaining_depths.values()
        )
        all_complete = remaining_candidate_count == 0
    if not all_complete and stop_reason is None:
        if totals["fetched_rows"] >= request.max_rows:
            stop_reason = "row_limit"
        elif deferred_cooldowns and not selected:
            stop_reason = "history_availability_cooldown"
        elif len(pending) > len(selected) or len(completed_codes) < len(selected):
            stop_reason = "continuation_required"

    status = "complete" if all_complete else "partial"
    job.status = status
    job.finished_at = _utcnow()
    job.error_message = None
    latest_lifetime_peak = _read_positive_rss(lifetime_peak_rss_reader)
    if latest_lifetime_peak is not None:
        totals["lifetime_peak_rss_bytes"] = max(
            int(totals["lifetime_peak_rss_bytes"] or 0),
            latest_lifetime_peak,
        )
    final_details = {
        **(checkpoint or job.details_json or {}),
        "identity_hash": request.identity_hash,
        "selection_policy": request.selection_policy,
        "provider_policy_version": request.provider_policy_version,
        "profile_max_codes": request.max_codes,
        "target_trade_date": (
            request.target_trade_date.isoformat() if request.target_trade_date else None
        ),
        "status": status,
        "stop_reason": stop_reason,
        "attempted_codes": attempted_codes,
        "fetch_window_count": len(fetch_windows),
        "fetch_window_samples": fetch_windows[:20],
        "completed_codes": completed_codes,
        "exclusions": exclusions,
        "elapsed_seconds": round(clock() - started, 6),
        "peak_rss_bytes": totals["peak_rss_bytes"],
        "rss_limit_bytes": totals["rss_limit_bytes"],
        "configured_rss_limit_bytes": totals["configured_rss_limit_bytes"],
        "baseline_rss_bytes": totals["baseline_rss_bytes"],
        "current_rss_bytes": totals["current_rss_bytes"],
        "slice_peak_current_rss_bytes": totals["slice_peak_current_rss_bytes"],
        "lifetime_peak_rss_bytes": totals["lifetime_peak_rss_bytes"],
        "rss_delta_bytes": totals["rss_delta_bytes"],
        "fetched_rows": totals["fetched_rows"],
        "persisted_rows": totals["persisted_rows"],
        "inserted_rows": totals["inserted_rows"],
        "updated_rows": totals["updated_rows"],
        "unchanged_rows": totals["unchanged_rows"],
        "excluded_rows": totals["excluded_rows"],
        "provider_attempt_count": len(attempted_codes),
        "last_completed_code": completed_codes[-1] if completed_codes else None,
        "remaining_candidate_count": remaining_candidate_count,
        "deferred_history_count": len(deferred_cooldowns),
        "deferred_history_samples": sorted(deferred_cooldowns)[:20],
        "circuit_state": "open" if stop_reason == "provider_circuit_open" else "closed",
        "provider_health": latest_provider_health,
        "rows_per_second": (
            round(totals["persisted_rows"] / max(clock() - started, 0.000001), 3)
        ),
        "sql_statements": totals["sql_statements"],
        "max_page_sql_statements": totals["max_page_sql_statements"],
        "retries": totals["retries"],
    }
    job.details_json = final_details
    remaining_process = request.process_deadline_seconds - (clock() - started)
    remaining_process = min(
        remaining_process,
        _remaining_hard_seconds(hard_process_deadline),
    )
    if remaining_process <= 0:
        return _result(
            status="partial",
            stop_reason=stop_reason or "process_deadline",
            attempted_codes=attempted_codes,
            completed_codes=completed_codes,
            exclusions=exclusions,
            totals=totals,
            elapsed_seconds=clock() - started,
            checkpoint=checkpoint,
        )
    try:
        await asyncio.wait_for(session.commit(), timeout=remaining_process)
    except TimeoutError:
        await _rollback_before(session, deadline=hard_process_deadline)
        return _result(
            status="partial",
            stop_reason=stop_reason or "process_deadline",
            attempted_codes=attempted_codes,
            completed_codes=completed_codes,
            exclusions=exclusions,
            totals=totals,
            elapsed_seconds=clock() - started,
            checkpoint=checkpoint,
        )
    totals["sql_statements"] += 1

    return _result(
        status=status,
        stop_reason=stop_reason,
        attempted_codes=attempted_codes,
        completed_codes=completed_codes,
        exclusions=exclusions,
        totals=totals,
        elapsed_seconds=clock() - started,
        checkpoint=checkpoint,
    )


__all__ = [
    "BoundedHistorySyncRequest",
    "BoundedHistorySyncResult",
    "RESEARCH_DEPTH_SELECTION_POLICY",
    "read_latest_compatible_provider_health",
    "read_process_lifetime_peak_rss_bytes",
    "read_process_rss_bytes",
    "run_bounded_history_sync_slice",
]
