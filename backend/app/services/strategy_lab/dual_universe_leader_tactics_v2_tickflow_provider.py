"""Bounded TickFlow/BaoStock facts for the A-share V2 research path.

TickFlow supplies the current A-share universe, security metadata, SW1 pools
and explicit backward-adjusted daily OHLCV. BaoStock only supplements symbols
missing from those pools, through physically bounded pages. The combined
snapshot is point-in-time from its actual receipt timestamp onward and is never
backdated.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import PRICE_BASIS
from app.services.strategy_lab.dual_universe_leader_tactics_v2_ingestion import (
    AshareAdjustedPriceFact,
    AshareThemeMembershipFact,
    AshareUniverseSnapshotFact,
)

TICKFLOW_BASE_URL = "https://free-api.tickflow.org/v1"
TICKFLOW_UNIVERSE_ID = "CN_Equity_A"
TICKFLOW_PROVIDER = "tickflow"
TICKFLOW_UNIVERSE_SOURCE = "tickflow.free.universes.CN_Equity_A"
TICKFLOW_ADJUSTMENT_VERSION = "tickflow.free.klines.backward_v1"
TICKFLOW_SW1_UNIVERSE_PREFIX = "CN_Equity_SW1_"
TICKFLOW_SW2_UNIVERSE_PREFIX = "CN_Equity_SW2_"
TICKFLOW_SW3_UNIVERSE_PREFIX = "CN_Equity_SW3_"
TICKFLOW_THEME_SOURCE = "tickflow.free.universes.SW1"
TICKFLOW_TAXONOMY_VERSION = "tickflow.sw1.current_v1"
TICKFLOW_SW_PATH_SOURCE = "tickflow.free.universes.SW1_SW2_SW3"
TICKFLOW_SW_PATH_TAXONOMY_VERSION = "tickflow.sw2021.current_v1"
BAOSTOCK_THEME_SOURCE = "baostock.query_stock_industry.current"
BAOSTOCK_TAXONOMY_VERSION = "baostock.industry.current_v1"
CAPCO_THEME_SOURCE = "capco.2025_h2.listed_company_industry"
CAPCO_TAXONOMY_VERSION = "capco.listed_company_industry.2025_h2"
CAPCO_SOURCE_DOCUMENT_SHA256 = (
    "b1d0140572b20de11cd62ca478edb4da6e229928b216116df6345b3c2e461f58"
)
CAPCO_ENTRIES_SHA256 = (
    "18988c33a803edcc5f746085fb607b65ea3b86030ee2d5afeb5d7ce87d3ba359"
)
ASHARE_TAXONOMY_VERSION = (
    "tickflow_sw1_plus_baostock_current_plus_capco_2025_h2_v1"
)
TICKFLOW_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "FundScope/leader-tactics-v2",
}
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_RESPONSE_BYTES = 8_000_000
MAX_SUBPROCESS_BYTES = 2_000_000
MAX_UNIVERSE_ROWS = 6_000
MAX_INSTRUMENT_BATCH_SIZE = 1_000
MAX_UNIVERSE_BATCH_IDS = 1_000
MAX_SW3_PATH_PAGE_IDS = 200
MAX_HISTORY_SESSIONS = 300
DEFAULT_HISTORY_SESSIONS = 180
DEFAULT_REQUEST_TIMEOUT_SECONDS = 6.0
MAX_REQUEST_TIMEOUT_SECONDS = 8.0
DEFAULT_UNIVERSE_BUDGET_SECONDS = 40.0
MAX_UNIVERSE_BUDGET_SECONDS = 45.0
DEFAULT_CODE_BUDGET_SECONDS = 10.0
MAX_CODE_BUDGET_SECONDS = 20.0
BAOSTOCK_SUBPROCESS_TIMEOUT_SECONDS = 27.0


class TickflowAshareProviderError(RuntimeError):
    """Raised when a source response cannot become factual V2 input."""


@dataclass(frozen=True, slots=True)
class TickflowAshareProviderConfig:
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    universe_budget_seconds: float = DEFAULT_UNIVERSE_BUDGET_SECONDS
    code_budget_seconds: float = DEFAULT_CODE_BUDGET_SECONDS

    def __post_init__(self) -> None:
        if not 0 < self.request_timeout_seconds <= MAX_REQUEST_TIMEOUT_SECONDS:
            raise ValueError("request timeout is outside the bounded range")
        if not 0 < self.universe_budget_seconds <= MAX_UNIVERSE_BUDGET_SECONDS:
            raise ValueError("universe budget is outside the bounded range")
        if not 0 < self.code_budget_seconds <= MAX_CODE_BUDGET_SECONDS:
            raise ValueError("code budget is outside the bounded range")


@dataclass(frozen=True, slots=True)
class AshareIndustryClassification:
    name: str
    group_id: str
    source: str
    taxonomy_version: str
    effective_from: date
    received_at: datetime
    confidence: str = "observed_current"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "industry", max_length=128))
        object.__setattr__(
            self, "group_id", _required_text(self.group_id, "industry_group_id", max_length=256)
        )
        object.__setattr__(
            self, "source", _required_text(self.source, "industry_source", max_length=64)
        )
        object.__setattr__(
            self,
            "taxonomy_version",
            _required_text(
                self.taxonomy_version, "industry_taxonomy_version", max_length=128
            ),
        )
        if not isinstance(self.effective_from, date) or isinstance(
            self.effective_from, datetime
        ):
            raise TickflowAshareProviderError("industry_effective_from_invalid")
        if not isinstance(self.received_at, datetime):
            raise TickflowAshareProviderError("industry_received_at_invalid")
        object.__setattr__(
            self,
            "confidence",
            _required_text(self.confidence, "industry_confidence", max_length=32),
        )


@dataclass(frozen=True, slots=True)
class TickflowSWIndustryPath:
    """One complete current SW1/SW2/SW3 path from a shared TickFlow code."""

    terminal_code: str
    level1_code: str
    level1_label: str
    level2_code: str
    level2_label: str
    level3_code: str
    level3_label: str
    sw3_universe_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "terminal_code",
            "level1_code",
            "level1_label",
            "level2_code",
            "level2_label",
            "level3_code",
            "level3_label",
            "sw3_universe_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name, max_length=128),
            )


@dataclass(frozen=True, slots=True)
class TickflowSWIndustryPathMember:
    """A security-to-path relation returned by one bounded SW3 pool page."""

    symbol: str
    path: TickflowSWIndustryPath

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalise_symbol(self.symbol)[0])


@dataclass(frozen=True, slots=True)
class TickflowSWIndustryCatalog:
    paths: tuple[TickflowSWIndustryPath, ...]
    level3_count: int
    incomplete_terminal_codes: tuple[str, ...]
    catalog_hash: str


@dataclass(frozen=True, slots=True)
class TickflowSWIndustryPathPage:
    members: tuple[TickflowSWIndustryPathMember, ...]
    cursor: int
    next_cursor: int | None
    source_count: int
    page_hash: str


def _sw_path_payload(path: TickflowSWIndustryPath) -> dict[str, str]:
    return {
        "terminal_code": path.terminal_code,
        "level1_code": path.level1_code,
        "level1_label": path.level1_label,
        "level2_code": path.level2_code,
        "level2_label": path.level2_label,
        "level3_code": path.level3_code,
        "level3_label": path.level3_label,
        "sw3_universe_id": path.sw3_universe_id,
    }


@dataclass(frozen=True, slots=True)
class BaoStockIndustryBatchResult:
    """Completed and retryable portions of one bounded subprocess page."""

    industries: Mapping[str, str]
    completed_symbols: tuple[str, ...]
    failures: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TickflowAshareMember:
    symbol: str
    code: str
    name: str
    board: str
    current_industry: str | None
    float_shares: float | None
    industry_group_id: str | None = None
    industry_source: str | None = None
    industry_taxonomy_version: str | None = None
    industry_effective_from: date | None = None
    industry_received_at: datetime | None = None
    industry_confidence: str | None = None

    def __post_init__(self) -> None:
        symbol, code, board = _normalise_symbol(self.symbol)
        if self.code != code or self.board != board:
            raise TickflowAshareProviderError("instrument_identity_mismatch")
        name = _required_text(self.name, "name", max_length=256)
        industry = _optional_text(self.current_industry, "current_industry", max_length=128)
        float_shares = _optional_positive_number(self.float_shares, "float_shares")
        industry_fields = (
            self.industry_group_id,
            self.industry_source,
            self.industry_taxonomy_version,
            self.industry_effective_from,
            self.industry_received_at,
            self.industry_confidence,
        )
        if industry is None and any(value is not None for value in industry_fields):
            raise TickflowAshareProviderError("industry_metadata_without_industry")
        if industry is not None and any(value is None for value in industry_fields):
            raise TickflowAshareProviderError("industry_metadata_incomplete")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "board", board)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "current_industry", industry)
        object.__setattr__(self, "float_shares", float_shares)


@dataclass(frozen=True, slots=True)
class TickflowAshareFactBundle:
    asset_code: str
    universe_fact: AshareUniverseSnapshotFact
    theme_facts: tuple[AshareThemeMembershipFact, ...]
    adjusted_price_facts: tuple[AshareAdjustedPriceFact, ...]


@dataclass(frozen=True, slots=True)
class TickflowAshareFactBatchResult:
    """One official batch response split into usable facts and per-code failures."""

    bundles: Mapping[str, TickflowAshareFactBundle]
    failures: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _ParsedAdjustedBar:
    trade_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    volume: float
    amount: float
    turnover: float
    raw_record: tuple[object, ...]


IndustryLoader = Callable[
    [], Awaitable[Mapping[str, str | AshareIndustryClassification]]
]
_CURRENT_UNIVERSE_CACHE: tuple[date, tuple[TickflowAshareMember, ...]] | None = None
_CAPCO_BSE_DATA_PATH = (
    Path(__file__).with_name("data") / "capco_2025_h2_bse_industries.json"
)


def _required_text(value: object, field: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise TickflowAshareProviderError(f"{field}_invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise TickflowAshareProviderError(f"{field}_invalid")
    return normalized


def _optional_text(value: object, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = _required_text(value, field, max_length=max_length)
    return normalized


def _finite_number(value: object, field: str, *, minimum: float) -> float:
    if isinstance(value, bool) or value is None:
        raise TickflowAshareProviderError(f"{field}_non_finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise TickflowAshareProviderError(f"{field}_non_finite") from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise TickflowAshareProviderError(f"{field}_non_finite")
    return parsed


def _optional_positive_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field, minimum=1.0)


def _normalise_symbol(value: object) -> tuple[str, str, str]:
    if not isinstance(value, str):
        raise TickflowAshareProviderError("symbol_invalid")
    code, separator, exchange = value.strip().upper().partition(".")
    if separator != "." or len(code) != 6 or not code.isdigit():
        raise TickflowAshareProviderError("symbol_invalid")
    board = {"SH": "SH", "SZ": "SZ", "BJ": "BSE"}.get(exchange)
    if board is None:
        raise TickflowAshareProviderError("exchange_invalid")
    return f"{code}.{exchange}", code, board


def _validate_received_at(received_at: datetime) -> datetime:
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise TickflowAshareProviderError("received_at_must_be_timezone_aware")
    return received_at


@lru_cache(maxsize=1)
def _load_capco_bse_snapshot() -> dict[str, tuple[str, str, str]]:
    """Load and validate the vendored official CAPCO BSE classification."""

    try:
        payload = json.loads(_CAPCO_BSE_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TickflowAshareProviderError("capco_snapshot_unavailable") from exc
    if not isinstance(payload, Mapping):
        raise TickflowAshareProviderError("capco_snapshot_invalid")
    if (
        payload.get("schema_version") != "fundscope.capco_bse_industry_snapshot.v1"
        or payload.get("source") != CAPCO_THEME_SOURCE
        or payload.get("source_document_sha256") != CAPCO_SOURCE_DOCUMENT_SHA256
        or payload.get("entries_sha256") != CAPCO_ENTRIES_SHA256
    ):
        raise TickflowAshareProviderError("capco_snapshot_identity_invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != 285:
        raise TickflowAshareProviderError("capco_snapshot_entries_invalid")
    canonical_entries = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical_entries).hexdigest() != CAPCO_ENTRIES_SHA256:
        raise TickflowAshareProviderError("capco_snapshot_hash_mismatch")

    parsed: dict[str, tuple[str, str, str]] = {}
    for row in entries:
        if not isinstance(row, Mapping):
            raise TickflowAshareProviderError("capco_snapshot_row_invalid")
        code = _required_text(row.get("code"), "capco_code", max_length=6)
        if len(code) != 6 or not code.startswith("920") or not code.isdigit():
            raise TickflowAshareProviderError("capco_code_invalid")
        name = _required_text(row.get("name"), "capco_name", max_length=64)
        division_code = _required_text(
            row.get("division_code"), "capco_division_code", max_length=4
        )
        division_name = _required_text(
            row.get("division_name"), "capco_division_name", max_length=128
        )
        if code in parsed:
            raise TickflowAshareProviderError("capco_snapshot_duplicate_code")
        parsed[code] = (name, division_code, division_name)
    return parsed


def load_capco_bse_industries(
    members: tuple[TickflowAshareMember, ...],
    *,
    signal_date: date,
    received_at: datetime,
) -> dict[str, AshareIndustryClassification]:
    """Return official frozen classifications only for missing BSE members.

    The source period is historical metadata, but the facts become visible only
    at this run's real receipt time and are never backdated.
    """

    if not isinstance(signal_date, date) or isinstance(signal_date, datetime):
        raise TickflowAshareProviderError("capco_signal_date_invalid")
    if not isinstance(received_at, datetime):
        raise TickflowAshareProviderError("capco_received_at_invalid")
    snapshot = _load_capco_bse_snapshot()
    classifications: dict[str, AshareIndustryClassification] = {}
    for member in members:
        if member.board != "BSE" or member.current_industry is not None:
            continue
        entry = snapshot.get(member.code)
        if entry is None:
            continue
        _name, division_code, division_name = entry
        classifications[member.symbol] = AshareIndustryClassification(
            name=division_name,
            group_id=f"capco_division:{division_code}",
            source=CAPCO_THEME_SOURCE,
            taxonomy_version=CAPCO_TAXONOMY_VERSION,
            effective_from=signal_date,
            received_at=received_at,
            confidence="official_frozen_snapshot",
        )
    return classifications


def _payload_data(payload: object, kind: str) -> object:
    if not isinstance(payload, Mapping) or "data" not in payload:
        raise TickflowAshareProviderError(f"{kind}_payload_invalid")
    return payload["data"]


def parse_tickflow_sw_industry_catalog(rows: object) -> TickflowSWIndustryCatalog:
    """Build complete SW paths without fetching duplicate SW1/SW2 member pools."""

    if not isinstance(rows, list) or not rows or len(rows) > 2_000:
        raise TickflowAshareProviderError("industry_universe_list_invalid")
    prefixes = {
        1: TICKFLOW_SW1_UNIVERSE_PREFIX,
        2: TICKFLOW_SW2_UNIVERSE_PREFIX,
        3: TICKFLOW_SW3_UNIVERSE_PREFIX,
    }
    grouped: dict[str, dict[int, tuple[str, str]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise TickflowAshareProviderError("industry_universe_row_invalid")
        universe_id = row.get("id")
        if not isinstance(universe_id, str):
            continue
        level = next(
            (candidate for candidate, prefix in prefixes.items() if universe_id.startswith(prefix)),
            None,
        )
        if level is None:
            continue
        prefix = prefixes[level]
        terminal_code = _required_text(
            universe_id.removeprefix(prefix),
            "industry_terminal_code",
            max_length=64,
        )
        raw_name = _required_text(row.get("name"), "industry_name", max_length=128)
        name_prefix = f"SW{level}"
        if not raw_name.startswith(name_prefix):
            raise TickflowAshareProviderError("industry_universe_name_invalid")
        label = _required_text(
            raw_name.removeprefix(name_prefix),
            f"industry_level{level}_label",
            max_length=128,
        )
        previous = grouped.setdefault(terminal_code, {}).setdefault(
            level, (universe_id, label)
        )
        if previous != (universe_id, label):
            raise TickflowAshareProviderError(
                f"industry_catalog_conflict:{terminal_code}:level{level}"
            )

    level3_codes = sorted(code for code, levels in grouped.items() if 3 in levels)
    paths: list[TickflowSWIndustryPath] = []
    incomplete: list[str] = []
    for terminal_code in level3_codes:
        levels = grouped[terminal_code]
        if set(levels) != {1, 2, 3}:
            incomplete.append(terminal_code)
            continue
        paths.append(
            TickflowSWIndustryPath(
                terminal_code=terminal_code,
                level1_code=levels[1][0],
                level1_label=levels[1][1],
                level2_code=levels[2][0],
                level2_label=levels[2][1],
                level3_code=levels[3][0],
                level3_label=levels[3][1],
                sw3_universe_id=levels[3][0],
            )
        )
    if not paths:
        raise TickflowAshareProviderError("industry_catalog_has_no_complete_sw3_paths")
    ordered = tuple(sorted(paths, key=lambda item: item.sw3_universe_id))
    return TickflowSWIndustryCatalog(
        paths=ordered,
        level3_count=len(level3_codes),
        incomplete_terminal_codes=tuple(incomplete),
        catalog_hash=stable_contract_hash(
            {
                "schema_version": "tickflow_sw_industry_catalog_v1",
                "taxonomy_version": TICKFLOW_SW_PATH_TAXONOMY_VERSION,
                "paths": tuple(_sw_path_payload(path) for path in ordered),
                "incomplete_terminal_codes": tuple(incomplete),
            }
        ),
    )


def _parse_universe(payload: object) -> tuple[str, ...]:
    data = _payload_data(payload, "universe")
    if not isinstance(data, Mapping) or data.get("id") != TICKFLOW_UNIVERSE_ID:
        raise TickflowAshareProviderError("universe_identity_invalid")
    declared = data.get("symbol_count")
    symbols = data.get("symbols")
    if (
        isinstance(declared, bool)
        or not isinstance(declared, int)
        or not 0 < declared <= MAX_UNIVERSE_ROWS
        or not isinstance(symbols, list)
        or len(symbols) != declared
    ):
        raise TickflowAshareProviderError("universe_partial_or_invalid")
    normalized = tuple(sorted(_normalise_symbol(symbol)[0] for symbol in symbols))
    if len(set(normalized)) != len(normalized):
        raise TickflowAshareProviderError("universe_duplicate_symbol")
    return normalized


def _parse_instruments(
    payload: object,
    *,
    expected_symbols: tuple[str, ...],
    industries: Mapping[str, AshareIndustryClassification],
) -> tuple[TickflowAshareMember, ...]:
    rows = _payload_data(payload, "instruments")
    if not isinstance(rows, list) or len(rows) != len(expected_symbols):
        raise TickflowAshareProviderError("instrument_batch_partial")
    members: list[TickflowAshareMember] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise TickflowAshareProviderError("instrument_row_invalid")
        symbol, code, board = _normalise_symbol(row.get("symbol"))
        if symbol in seen:
            raise TickflowAshareProviderError("instrument_duplicate_symbol")
        if row.get("type") != "stock" or row.get("region") != "CN":
            raise TickflowAshareProviderError("instrument_type_invalid")
        ext = row.get("ext")
        if not isinstance(ext, Mapping):
            raise TickflowAshareProviderError("instrument_ext_invalid")
        classification = industries.get(symbol)
        members.append(
            TickflowAshareMember(
                symbol=symbol,
                code=code,
                name=_required_text(row.get("name"), "name", max_length=256),
                board=board,
                current_industry=classification.name if classification else None,
                float_shares=_optional_positive_number(ext.get("float_shares"), "float_shares"),
                industry_group_id=classification.group_id if classification else None,
                industry_source=classification.source if classification else None,
                industry_taxonomy_version=(
                    classification.taxonomy_version if classification else None
                ),
                industry_effective_from=(
                    classification.effective_from if classification else None
                ),
                industry_received_at=classification.received_at if classification else None,
                industry_confidence=classification.confidence if classification else None,
            )
        )
        seen.add(symbol)
    if seen != set(expected_symbols):
        raise TickflowAshareProviderError("instrument_batch_identity_mismatch")
    return tuple(sorted(members, key=lambda member: member.symbol))


def _parse_adjusted_history(
    payload: object,
    *,
    signal_date: date,
    history_sessions: int,
    float_shares: float | None,
) -> tuple[_ParsedAdjustedBar, ...]:
    data = _payload_data(payload, "adjusted_history")
    if not isinstance(data, Mapping):
        raise TickflowAshareProviderError("adjusted_history_payload_invalid")
    columns = ("timestamp", "open", "high", "low", "close", "volume", "amount")
    values = tuple(data.get(column) for column in columns)
    if any(not isinstance(value, list) for value in values):
        raise TickflowAshareProviderError("adjusted_history_columns_invalid")
    rows = tuple(values)  # type: ignore[arg-type]
    lengths = {len(value) for value in rows}
    if len(lengths) != 1 or next(iter(lengths), 0) > MAX_HISTORY_SESSIONS:
        raise TickflowAshareProviderError("adjusted_history_length_invalid")

    parsed: list[_ParsedAdjustedBar] = []
    seen_dates: set[date] = set()
    for timestamp, open_, high, low, close, volume, amount in zip(*rows, strict=True):
        timestamp_value = _finite_number(timestamp, "timestamp", minimum=0.0)
        trade_date = datetime.fromtimestamp(timestamp_value / 1000, tz=SHANGHAI).date()
        if trade_date > signal_date:
            continue
        if trade_date in seen_dates:
            raise TickflowAshareProviderError("adjusted_history_duplicate_date")
        seen_dates.add(trade_date)
        adjusted_open = _finite_number(open_, "adjusted_open", minimum=0.0)
        adjusted_high = _finite_number(high, "adjusted_high", minimum=0.0)
        adjusted_low = _finite_number(low, "adjusted_low", minimum=0.0)
        adjusted_close = _finite_number(close, "adjusted_close", minimum=0.0)
        volume_value = _finite_number(volume, "volume", minimum=0.0)
        amount_value = _finite_number(amount, "amount", minimum=0.0)
        if min(adjusted_open, adjusted_high, adjusted_low, adjusted_close) <= 0:
            raise TickflowAshareProviderError("adjusted_ohlc_non_positive")
        if adjusted_high < max(adjusted_open, adjusted_close):
            raise TickflowAshareProviderError("adjusted_high_invalid")
        if adjusted_low > min(adjusted_open, adjusted_close) or adjusted_low > adjusted_high:
            raise TickflowAshareProviderError("adjusted_low_invalid")
        parsed.append(
            _ParsedAdjustedBar(
                trade_date=trade_date,
                adjusted_open=adjusted_open,
                adjusted_high=adjusted_high,
                adjusted_low=adjusted_low,
                adjusted_close=adjusted_close,
                volume=volume_value,
                amount=amount_value,
                # TickFlow's CN daily volume is reported in 100-share lots.
                # Missing float shares remains an honest unavailable zero; the
                # frozen V2 peer-liquidity component uses amount, not turnover.
                turnover=(
                    volume_value * 100.0 / float_shares * 100.0
                    if float_shares is not None
                    else 0.0
                ),
                raw_record=(timestamp, open_, high, low, close, volume, amount),
            )
        )
    parsed.sort(key=lambda bar: bar.trade_date)
    return tuple(parsed[-history_sessions:])


def _normalise_industry_mapping(
    mapping: Mapping[str, str | AshareIndustryClassification],
    *,
    observed_at: datetime,
) -> dict[str, AshareIndustryClassification]:
    industries: dict[str, AshareIndustryClassification] = {}
    effective_from = observed_at.replace(tzinfo=UTC).astimezone(SHANGHAI).date()
    for raw_symbol, raw_value in mapping.items():
        symbol = _normalise_symbol(raw_symbol)[0]
        classification = (
            raw_value
            if isinstance(raw_value, AshareIndustryClassification)
            else AshareIndustryClassification(
                name=_required_text(raw_value, "industry", max_length=128),
                group_id=f"baostock_industry:{raw_value}",
                source=BAOSTOCK_THEME_SOURCE,
                taxonomy_version=BAOSTOCK_TAXONOMY_VERSION,
                effective_from=effective_from,
                received_at=observed_at,
            )
        )
        previous = industries.setdefault(symbol, classification)
        if previous != classification:
            raise TickflowAshareProviderError(f"industry_conflict:{symbol}")
    return industries


def _parse_baostock_industry_output(
    *,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    requested_symbols: tuple[str, ...],
) -> BaoStockIndustryBatchResult:
    if len(stdout) > MAX_SUBPROCESS_BYTES:
        raise TickflowAshareProviderError("baostock_industry_response_too_large")
    completed: list[str] = []
    industries: dict[str, str] = {}
    requested = set(requested_symbols)
    for raw_line in stdout.splitlines():
        try:
            record = json.loads(raw_line)
        except ValueError as exc:
            raise TickflowAshareProviderError("baostock_industry_json_invalid") from exc
        if not isinstance(record, Mapping) or set(record) != {"symbol", "industry"}:
            raise TickflowAshareProviderError("baostock_industry_payload_invalid")
        symbol = _normalise_symbol(record["symbol"])[0]
        if symbol not in requested:
            raise TickflowAshareProviderError("baostock_industry_unrequested_symbol")
        if symbol in completed:
            raise TickflowAshareProviderError("baostock_industry_duplicate_result")
        completed.append(symbol)
        raw_industry = record["industry"]
        if raw_industry is not None:
            industries[symbol] = _required_text(raw_industry, "industry", max_length=128)

    unreported = tuple(symbol for symbol in requested_symbols if symbol not in completed)
    if returncode == 0 and unreported:
        raise TickflowAshareProviderError("baostock_industry_response_incomplete")
    summary_lines = stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
    error_summary = "".join(summary_lines) or f"returncode={returncode}"
    failures = (
        tuple((symbol, f"baostock_industry_failed:{error_summary[:200]}") for symbol in unreported)
        if returncode != 0
        else ()
    )
    return BaoStockIndustryBatchResult(
        industries=industries,
        completed_symbols=tuple(completed),
        failures=failures,
    )


async def load_baostock_industries(
    symbols: tuple[str, ...],
) -> BaoStockIndustryBatchResult:
    if not 1 <= len(symbols) <= 20:
        raise TickflowAshareProviderError("baostock_industry_batch_size_invalid")
    normalized_symbols = tuple(_normalise_symbol(symbol)[0] for symbol in symbols)
    if len(set(normalized_symbols)) != len(normalized_symbols):
        raise TickflowAshareProviderError("baostock_industry_duplicate_symbol")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.strategy_lab.dual_universe_leader_tactics_v2_baostock_snapshot",
        *normalized_symbols,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=BAOSTOCK_SUBPROCESS_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise TickflowAshareProviderError("baostock_industry_timeout") from exc
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    return _parse_baostock_industry_output(
        stdout=stdout,
        stderr=stderr,
        returncode=int(process.returncode or 0),
        requested_symbols=normalized_symbols,
    )


class TickflowAshareV2Provider:
    """Fetch a complete current universe and bounded adjusted histories."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        config: TickflowAshareProviderConfig | None = None,
        industry_loader: IndustryLoader | None = None,
    ) -> None:
        self.config = config or TickflowAshareProviderConfig()
        self._client = client
        self._owns_client = client is None
        self._industry_loader = industry_loader
        self._members: tuple[TickflowAshareMember, ...] | None = None
        self._members_by_code: dict[str, TickflowAshareMember] = {}
        self._received_at: datetime | None = None
        self._universe_observed_at: datetime | None = None
        self._request_count = 0
        self._last_error: str | None = None
        self._universe_cache_hit = False
        self._industry_count = 0

    async def __aenter__(self) -> TickflowAshareV2Provider:
        if self._client is None:
            timeout = httpx.Timeout(
                self.config.request_timeout_seconds,
                connect=min(3.0, self.config.request_timeout_seconds),
            )
            self._client = httpx.AsyncClient(
                base_url=TICKFLOW_BASE_URL,
                headers=TICKFLOW_HEADERS,
                timeout=timeout,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def transport_diagnostics(self) -> dict[str, object]:
        count = len(self._members or ())
        return {
            "request_count": self._request_count,
            "last_error": self._last_error,
            "universe_cache_hit": self._universe_cache_hit,
            "industry_count": self._industry_count,
            "industry_coverage": self._industry_count / count if count else 0.0,
        }

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise TickflowAshareProviderError("provider_not_open")
        return self._client

    async def _request_json(
        self,
        kind: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float,
    ) -> object:
        self._request_count += 1
        try:
            response = await asyncio.wait_for(
                self._require_client().request(
                    "POST" if json_body is not None else "GET",
                    path,
                    params=params,
                    json=json_body,
                ),
                timeout=timeout_seconds,
            )
            response.raise_for_status()
        except TimeoutError as exc:
            self._last_error = "TimeoutError"
            raise TickflowAshareProviderError(f"{kind}_timeout") from exc
        except httpx.HTTPError as exc:
            self._last_error = type(exc).__name__
            raise TickflowAshareProviderError(
                f"{kind}_request_error:{type(exc).__name__}"
            ) from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise TickflowAshareProviderError(f"{kind}_response_too_large")
        try:
            return response.json()
        except ValueError as exc:
            raise TickflowAshareProviderError(f"{kind}_json_invalid") from exc

    async def _fetch_tickflow_sw1_industries(
        self,
        *,
        expected_symbols: tuple[str, ...],
        observed_at: datetime,
    ) -> dict[str, AshareIndustryClassification]:
        list_payload = await self._request_json(
            "industry_universe_list",
            "/universes",
            timeout_seconds=self.config.request_timeout_seconds,
        )
        rows = _payload_data(list_payload, "industry_universe_list")
        if not isinstance(rows, list) or len(rows) > 2_000:
            raise TickflowAshareProviderError("industry_universe_list_invalid")
        ids: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise TickflowAshareProviderError("industry_universe_row_invalid")
            universe_id = row.get("id")
            if isinstance(universe_id, str) and universe_id.startswith(
                TICKFLOW_SW1_UNIVERSE_PREFIX
            ):
                ids.append(universe_id)
        ids = sorted(set(ids))
        if not ids or len(ids) > MAX_UNIVERSE_BATCH_IDS:
            raise TickflowAshareProviderError("industry_universe_ids_invalid")
        batch_payload = await self._request_json(
            "industry_universe_batch",
            "/universes/batch",
            json_body={"ids": ids},
            timeout_seconds=self.config.request_timeout_seconds,
        )
        pools = _payload_data(batch_payload, "industry_universe_batch")
        if not isinstance(pools, Mapping) or set(pools) != set(ids):
            raise TickflowAshareProviderError("industry_universe_batch_partial")

        expected = set(expected_symbols)
        classifications: dict[str, AshareIndustryClassification] = {}
        effective_from = observed_at.replace(tzinfo=UTC).astimezone(SHANGHAI).date()
        for universe_id in ids:
            pool = pools.get(universe_id)
            if not isinstance(pool, Mapping) or pool.get("id") != universe_id:
                raise TickflowAshareProviderError("industry_universe_identity_mismatch")
            pool_name = _required_text(pool.get("name"), "industry_name", max_length=128)
            if not pool_name.startswith("SW1"):
                raise TickflowAshareProviderError("industry_universe_name_invalid")
            industry = _required_text(
                pool_name.removeprefix("SW1"), "industry_name", max_length=128
            )
            symbols = pool.get("symbols")
            declared = pool.get("symbol_count")
            if (
                not isinstance(symbols, list)
                or isinstance(declared, bool)
                or not isinstance(declared, int)
                or declared != len(symbols)
            ):
                raise TickflowAshareProviderError("industry_universe_symbols_invalid")
            classification = AshareIndustryClassification(
                name=industry,
                group_id=f"tickflow_sw1:{industry}",
                source=TICKFLOW_THEME_SOURCE,
                taxonomy_version=TICKFLOW_TAXONOMY_VERSION,
                effective_from=effective_from,
                received_at=observed_at,
            )
            for raw_symbol in symbols:
                symbol = _normalise_symbol(raw_symbol)[0]
                if symbol not in expected:
                    continue
                previous = classifications.setdefault(symbol, classification)
                if previous.name != classification.name:
                    raise TickflowAshareProviderError(
                        f"industry_universe_conflict:{symbol}"
                    )
        return classifications

    async def fetch_sw_industry_catalog(self) -> TickflowSWIndustryCatalog:
        """Fetch one bounded catalog and retain only complete SW1/SW2/SW3 paths."""

        payload = await self._request_json(
            "industry_universe_list",
            "/universes",
            timeout_seconds=self.config.request_timeout_seconds,
        )
        return parse_tickflow_sw_industry_catalog(
            _payload_data(payload, "industry_universe_list")
        )

    async def fetch_sw3_industry_path_page(
        self,
        *,
        catalog: TickflowSWIndustryCatalog,
        cursor: int = 0,
        page_size: int = MAX_SW3_PATH_PAGE_IDS,
        expected_symbols: tuple[str, ...] | None = None,
    ) -> TickflowSWIndustryPathPage:
        """Fetch one deterministic SW3 pool page and join catalog parent labels."""

        if cursor < 0 or cursor > len(catalog.paths):
            raise TickflowAshareProviderError("industry_path_cursor_invalid")
        if page_size <= 0 or page_size > MAX_SW3_PATH_PAGE_IDS:
            raise TickflowAshareProviderError("industry_path_page_size_invalid")
        page_paths = catalog.paths[cursor : cursor + page_size]
        if not page_paths:
            return TickflowSWIndustryPathPage(
                members=(),
                cursor=cursor,
                next_cursor=None,
                source_count=0,
                page_hash=stable_contract_hash(
                    {
                        "schema_version": "tickflow_sw3_path_page_v1",
                        "catalog_hash": catalog.catalog_hash,
                        "cursor": cursor,
                        "members": (),
                    }
                ),
            )
        ids = [path.sw3_universe_id for path in page_paths]
        payload = await self._request_json(
            "industry_sw3_batch",
            "/universes/batch",
            json_body={"ids": ids},
            timeout_seconds=self.config.request_timeout_seconds,
        )
        pools = _payload_data(payload, "industry_sw3_batch")
        if not isinstance(pools, Mapping) or set(pools) != set(ids):
            raise TickflowAshareProviderError("industry_sw3_batch_partial")
        expected = set(expected_symbols) if expected_symbols is not None else None
        by_symbol: dict[str, TickflowSWIndustryPathMember] = {}
        for path in page_paths:
            pool = pools.get(path.sw3_universe_id)
            if not isinstance(pool, Mapping) or pool.get("id") != path.sw3_universe_id:
                raise TickflowAshareProviderError("industry_sw3_identity_mismatch")
            symbols = pool.get("symbols")
            declared = pool.get("symbol_count")
            if (
                not isinstance(symbols, list)
                or isinstance(declared, bool)
                or not isinstance(declared, int)
                or declared != len(symbols)
            ):
                raise TickflowAshareProviderError("industry_sw3_symbols_invalid")
            for raw_symbol in symbols:
                symbol = _normalise_symbol(raw_symbol)[0]
                if expected is not None and symbol not in expected:
                    continue
                member = TickflowSWIndustryPathMember(symbol=symbol, path=path)
                previous = by_symbol.setdefault(symbol, member)
                if previous.path != path:
                    raise TickflowAshareProviderError(
                        f"industry_sw3_membership_conflict:{symbol}"
                    )
        members = tuple(by_symbol[key] for key in sorted(by_symbol))
        next_cursor_value = cursor + len(page_paths)
        next_cursor = (
            next_cursor_value if next_cursor_value < len(catalog.paths) else None
        )
        return TickflowSWIndustryPathPage(
            members=members,
            cursor=cursor,
            next_cursor=next_cursor,
            source_count=len(page_paths),
            page_hash=stable_contract_hash(
                {
                    "schema_version": "tickflow_sw3_path_page_v1",
                    "catalog_hash": catalog.catalog_hash,
                    "cursor": cursor,
                    "source_ids": tuple(ids),
                    "members": tuple(
                        {
                            "symbol": member.symbol,
                            "path": _sw_path_payload(member.path),
                        }
                        for member in members
                    ),
                }
            ),
        )

    async def fetch_universe(
        self, *, received_at: datetime
    ) -> tuple[TickflowAshareMember, ...]:
        received_at = _validate_received_at(received_at)
        if self._members is not None:
            if received_at != self._received_at:
                raise TickflowAshareProviderError("universe_snapshot_already_bound")
            return self._members

        global _CURRENT_UNIVERSE_CACHE
        received_date = received_at.astimezone(SHANGHAI).date()
        if (
            self._owns_client
            and _CURRENT_UNIVERSE_CACHE is not None
            and _CURRENT_UNIVERSE_CACHE[0] == received_date
        ):
            self._members = _CURRENT_UNIVERSE_CACHE[1]
            self._members_by_code = {member.code: member for member in self._members}
            self._received_at = received_at
            self._universe_observed_at = datetime.now(UTC).replace(tzinfo=None)
            self._industry_count = sum(
                member.current_industry is not None for member in self._members
            )
            self._universe_cache_hit = True
            return self._members

        async def load() -> tuple[TickflowAshareMember, ...]:
            universe_payload = await self._request_json(
                "universe",
                f"/universes/{TICKFLOW_UNIVERSE_ID}",
                timeout_seconds=self.config.request_timeout_seconds,
            )
            symbols = _parse_universe(universe_payload)
            industry_observed_at = datetime.now(UTC).replace(tzinfo=None)
            if self._industry_loader is None:
                industries = await self._fetch_tickflow_sw1_industries(
                    expected_symbols=symbols,
                    observed_at=industry_observed_at,
                )
            else:
                try:
                    raw_industries = await self._industry_loader()
                    industries = _normalise_industry_mapping(
                        raw_industries,
                        observed_at=industry_observed_at,
                    )
                except TickflowAshareProviderError:
                    raise
                except Exception as exc:
                    raise TickflowAshareProviderError(
                        f"industry_loader_failed:{type(exc).__name__}"
                    ) from exc
            members: list[TickflowAshareMember] = []
            for start in range(0, len(symbols), MAX_INSTRUMENT_BATCH_SIZE):
                batch = symbols[start : start + MAX_INSTRUMENT_BATCH_SIZE]
                payload = await self._request_json(
                    "instruments",
                    "/instruments",
                    json_body={"symbols": list(batch)},
                    timeout_seconds=self.config.request_timeout_seconds,
                )
                members.extend(
                    _parse_instruments(payload, expected_symbols=batch, industries=industries)
                )
            if len(members) != len(symbols):
                raise TickflowAshareProviderError("universe_metadata_incomplete")
            return tuple(sorted(members, key=lambda member: member.code))

        try:
            members = await asyncio.wait_for(
                load(), timeout=self.config.universe_budget_seconds
            )
        except TimeoutError as exc:
            raise TickflowAshareProviderError("universe_budget_timeout") from exc
        self._members = members
        self._members_by_code = {member.code: member for member in members}
        self._received_at = received_at
        self._universe_observed_at = datetime.now(UTC).replace(tzinfo=None)
        self._industry_count = sum(member.current_industry is not None for member in members)
        if self._owns_client:
            _CURRENT_UNIVERSE_CACHE = (received_date, members)
        return members

    def apply_industry_supplements(
        self,
        supplements: Mapping[str, AshareIndustryClassification],
    ) -> tuple[TickflowAshareMember, ...]:
        """Overlay only previously missing classifications on this provider instance."""

        if self._members is None:
            raise TickflowAshareProviderError("universe_snapshot_required")
        normalized = _normalise_industry_mapping(
            supplements,
            observed_at=datetime.now(UTC).replace(tzinfo=None),
        )
        unknown = set(normalized) - {member.symbol for member in self._members}
        if unknown:
            raise TickflowAshareProviderError("supplement_not_in_current_universe")
        updated: list[TickflowAshareMember] = []
        for member in self._members:
            supplement = normalized.get(member.symbol)
            if supplement is None:
                updated.append(member)
                continue
            if member.current_industry is not None:
                if member.current_industry != supplement.name:
                    raise TickflowAshareProviderError(
                        f"supplement_would_replace_observed_industry:{member.symbol}"
                    )
                updated.append(member)
                continue
            updated.append(
                replace(
                    member,
                    current_industry=supplement.name,
                    industry_group_id=supplement.group_id,
                    industry_source=supplement.source,
                    industry_taxonomy_version=supplement.taxonomy_version,
                    industry_effective_from=supplement.effective_from,
                    industry_received_at=supplement.received_at,
                    industry_confidence=supplement.confidence,
                )
            )
        self._members = tuple(updated)
        self._members_by_code = {member.code: member for member in self._members}
        self._industry_count = sum(
            member.current_industry is not None for member in self._members
        )
        return self._members

    def restrict_industries_to_signal_date(
        self,
        signal_date: date,
    ) -> tuple[TickflowAshareMember, ...]:
        """Remove current classifications that were not effective by the signal date."""

        if self._members is None:
            raise TickflowAshareProviderError("universe_snapshot_required")
        updated: list[TickflowAshareMember] = []
        for member in self._members:
            if (
                member.current_industry is None
                or member.industry_effective_from is None
                or member.industry_effective_from <= signal_date
            ):
                updated.append(member)
                continue
            updated.append(
                replace(
                    member,
                    current_industry=None,
                    industry_group_id=None,
                    industry_source=None,
                    industry_taxonomy_version=None,
                    industry_effective_from=None,
                    industry_received_at=None,
                    industry_confidence=None,
                )
            )
        self._members = tuple(updated)
        self._members_by_code = {member.code: member for member in self._members}
        self._industry_count = sum(
            member.current_industry is not None for member in self._members
        )
        return self._members

    def _validate_fact_request(
        self,
        members: tuple[TickflowAshareMember, ...],
        *,
        signal_date: date,
        received_at: datetime,
        history_sessions: int,
    ) -> None:
        _validate_received_at(received_at)
        if signal_date > received_at.astimezone(SHANGHAI).date():
            raise TickflowAshareProviderError("signal_date_after_receipt")
        if not isinstance(history_sessions, int) or isinstance(history_sessions, bool):
            raise TickflowAshareProviderError("history_sessions_invalid")
        if not 1 <= history_sessions <= MAX_HISTORY_SESSIONS:
            raise TickflowAshareProviderError("history_sessions_out_of_range")
        if not members or len(members) > 20:
            raise TickflowAshareProviderError("history_batch_size_invalid")
        if self._members is None or self._received_at != received_at:
            raise TickflowAshareProviderError("universe_snapshot_required")
        if self._universe_observed_at is None:
            raise TickflowAshareProviderError("universe_receipt_missing")
        codes: set[str] = set()
        for member in members:
            if member.code in codes:
                raise TickflowAshareProviderError("history_batch_duplicate_code")
            codes.add(member.code)
            if self._members_by_code.get(member.code) != member:
                raise TickflowAshareProviderError("member_not_from_current_universe")

    def _build_fact_bundle(
        self,
        member: TickflowAshareMember,
        *,
        signal_date: date,
        parsed: tuple[_ParsedAdjustedBar, ...],
        price_received_at: datetime,
    ) -> TickflowAshareFactBundle:
        if self._universe_observed_at is None:
            raise TickflowAshareProviderError("universe_receipt_missing")
        universe_received_at = self._universe_observed_at
        universe_fact = AshareUniverseSnapshotFact(
            snapshot_date=signal_date,
            asset_code=member.code,
            asset_name=member.name,
            listing_state="listed",
            board=member.board,
            effective_at=universe_received_at,
            received_at=universe_received_at,
            provider=TICKFLOW_PROVIDER,
            source_cutoff=universe_received_at,
        )
        theme_facts: tuple[AshareThemeMembershipFact, ...] = ()
        if member.current_industry is not None:
            if (
                member.industry_group_id is None
                or member.industry_source is None
                or member.industry_taxonomy_version is None
                or member.industry_effective_from is None
                or member.industry_received_at is None
                or member.industry_confidence is None
            ):
                raise TickflowAshareProviderError("industry_metadata_incomplete")
            theme_facts = (
                AshareThemeMembershipFact(
                    asset_code=member.code,
                    group_id=member.industry_group_id,
                    theme=member.current_industry,
                    sector=member.current_industry,
                    effective_from=member.industry_effective_from,
                    effective_to=None,
                    received_at=member.industry_received_at,
                    taxonomy_version=member.industry_taxonomy_version,
                    source=member.industry_source,
                    confidence=member.industry_confidence,
                    supersedes_fact_hash=None,
                    mapping_kind="historical_pit",
                ),
            )
        price_facts = tuple(
            AshareAdjustedPriceFact(
                asset_code=member.code,
                trade_date=bar.trade_date,
                adjusted_open=bar.adjusted_open,
                adjusted_high=bar.adjusted_high,
                adjusted_low=bar.adjusted_low,
                adjusted_close=bar.adjusted_close,
                volume=bar.volume,
                amount=bar.amount,
                turnover=bar.turnover,
                price_basis=PRICE_BASIS,
                provider=TICKFLOW_PROVIDER,
                adjustment_version=TICKFLOW_ADJUSTMENT_VERSION,
                revision_id=stable_contract_hash(
                    {
                        "provider": TICKFLOW_PROVIDER,
                        "adjustment_version": TICKFLOW_ADJUSTMENT_VERSION,
                        "asset_code": member.code,
                        "trade_date": bar.trade_date,
                        "record": bar.raw_record,
                    }
                ),
                received_at=price_received_at,
                historical_research_only=False,
                decision_eligible=True,
            )
            for bar in parsed
        )
        return TickflowAshareFactBundle(
            asset_code=member.code,
            universe_fact=universe_fact,
            theme_facts=theme_facts,
            adjusted_price_facts=price_facts,
        )

    async def fetch_fact_batch(
        self,
        members: tuple[TickflowAshareMember, ...],
        *,
        signal_date: date,
        received_at: datetime,
        history_sessions: int = DEFAULT_HISTORY_SESSIONS,
    ) -> TickflowAshareFactBatchResult:
        """Fetch 1-20 symbols in one official TickFlow batch request."""

        self._validate_fact_request(
            members,
            signal_date=signal_date,
            received_at=received_at,
            history_sessions=history_sessions,
        )
        end_time = int(
            datetime.combine(signal_date, datetime.min.time(), tzinfo=SHANGHAI).timestamp()
            * 1000
        )
        payload = await self._request_json(
            "adjusted_history_batch",
            "/klines/batch",
            params={
                "symbols": ",".join(member.symbol for member in members),
                "period": "1d",
                "count": history_sessions,
                "end_time": end_time,
                "adjust": "backward",
            },
            timeout_seconds=self.config.code_budget_seconds,
        )
        rows = _payload_data(payload, "adjusted_history_batch")
        if not isinstance(rows, Mapping):
            raise TickflowAshareProviderError("adjusted_history_batch_payload_invalid")
        expected_symbols = {member.symbol for member in members}
        unknown_symbols = set(rows) - expected_symbols
        if unknown_symbols:
            raise TickflowAshareProviderError("adjusted_history_batch_contains_unknown_symbol")

        price_received_at = datetime.now(UTC).replace(tzinfo=None)
        bundles: dict[str, TickflowAshareFactBundle] = {}
        failures: list[tuple[str, str]] = []
        for member in members:
            raw = rows.get(member.symbol)
            if raw is None:
                failures.append((member.code, "adjusted_history_batch_result_missing"))
                continue
            try:
                parsed = _parse_adjusted_history(
                    {"data": raw},
                    signal_date=signal_date,
                    history_sessions=history_sessions,
                    float_shares=member.float_shares,
                )
                if not parsed:
                    raise TickflowAshareProviderError("adjusted_history_empty")
                bundles[member.code] = self._build_fact_bundle(
                    member,
                    signal_date=signal_date,
                    parsed=parsed,
                    price_received_at=price_received_at,
                )
            except TickflowAshareProviderError as exc:
                failures.append((member.code, str(exc)[:500]))
        return TickflowAshareFactBatchResult(
            bundles=bundles,
            failures=tuple(failures),
        )

    async def fetch_facts(
        self,
        member: TickflowAshareMember,
        *,
        signal_date: date,
        received_at: datetime,
        history_sessions: int = DEFAULT_HISTORY_SESSIONS,
    ) -> TickflowAshareFactBundle:
        self._validate_fact_request(
            (member,),
            signal_date=signal_date,
            received_at=received_at,
            history_sessions=history_sessions,
        )

        end_time = int(
            datetime.combine(signal_date, datetime.min.time(), tzinfo=SHANGHAI).timestamp()
            * 1000
        )
        payload = await self._request_json(
            "adjusted_history",
            "/klines",
            params={
                "symbol": member.symbol,
                "period": "1d",
                "count": history_sessions,
                "end_time": end_time,
                "adjust": "backward",
            },
            timeout_seconds=self.config.code_budget_seconds,
        )
        parsed = _parse_adjusted_history(
            payload,
            signal_date=signal_date,
            history_sessions=history_sessions,
            float_shares=member.float_shares,
        )
        if not parsed:
            raise TickflowAshareProviderError("adjusted_history_empty")

        return self._build_fact_bundle(
            member,
            signal_date=signal_date,
            parsed=parsed,
            price_received_at=datetime.now(UTC).replace(tzinfo=None),
        )


__all__ = [
    "ASHARE_TAXONOMY_VERSION",
    "AshareIndustryClassification",
    "BaoStockIndustryBatchResult",
    "BAOSTOCK_TAXONOMY_VERSION",
    "BAOSTOCK_THEME_SOURCE",
    "CAPCO_ENTRIES_SHA256",
    "CAPCO_SOURCE_DOCUMENT_SHA256",
    "CAPCO_TAXONOMY_VERSION",
    "CAPCO_THEME_SOURCE",
    "DEFAULT_HISTORY_SESSIONS",
    "TICKFLOW_ADJUSTMENT_VERSION",
    "TICKFLOW_PROVIDER",
    "TICKFLOW_TAXONOMY_VERSION",
    "TICKFLOW_THEME_SOURCE",
    "TICKFLOW_UNIVERSE_SOURCE",
    "TickflowAshareFactBundle",
    "TickflowAshareFactBatchResult",
    "TickflowAshareMember",
    "TickflowAshareProviderConfig",
    "TickflowAshareProviderError",
    "TickflowAshareV2Provider",
    "load_baostock_industries",
    "load_capco_bse_industries",
]
