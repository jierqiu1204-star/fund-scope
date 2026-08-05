"""Async, fail-closed Eastmoney facts for the A-share V2 research path.

This module is intentionally limited to provider parsing and immutable input
contracts.  It does not schedule work, write a database, materialize a screen,
or call any other market-data provider.  A caller must first fetch one complete
universe snapshot and then may fetch one adjusted history for each returned
member.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    PRICE_BASIS,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_ingestion import (
    AshareAdjustedPriceFact,
    AshareThemeMembershipFact,
    AshareUniverseSnapshotFact,
)

EASTMONEY_A_SHARE_UNIVERSE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_A_SHARE_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_PROVIDER = "eastmoney"
EASTMONEY_ADJUSTMENT_VERSION = "eastmoney.push2his.kline.hfq_v1"
EASTMONEY_UNIVERSE_SOURCE = "eastmoney.push2.clist"
EASTMONEY_THEME_SOURCE = "eastmoney.push2.clist.f100"
EASTMONEY_TAXONOMY_VERSION = "eastmoney.stock.industry.f100_v1"
EASTMONEY_A_SHARE_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EASTMONEY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
EASTMONEY_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": EASTMONEY_USER_AGENT,
}
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_RESPONSE_BYTES = 8_000_000
MAX_UNIVERSE_ROWS = 10_000
MAX_HISTORY_SESSIONS = 300
DEFAULT_HISTORY_SESSIONS = 180
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
MAX_REQUEST_TIMEOUT_SECONDS = 8.0
DEFAULT_CODE_BUDGET_SECONDS = 20.0
MAX_CODE_BUDGET_SECONDS = 55.0
_MISSING_TEXT = frozenset({"", "-", "--", "n/a", "na", "none", "null"})


class EastmoneyAshareProviderError(RuntimeError):
    """Raised when an Eastmoney response cannot be used as factual input."""


@dataclass(frozen=True, slots=True)
class EastmoneyAshareProviderConfig:
    """Hard limits for one provider instance.

    There are deliberately no retry settings.  One request attempt is enough
    to keep a code's work bounded; the durable caller can retry the code on a
    later continuation.
    """

    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    code_budget_seconds: float = DEFAULT_CODE_BUDGET_SECONDS

    def __post_init__(self) -> None:
        if not 0 < self.request_timeout_seconds <= MAX_REQUEST_TIMEOUT_SECONDS:
            raise ValueError(
                f"request timeout must be in (0, {MAX_REQUEST_TIMEOUT_SECONDS:g}] seconds"
            )
        if not 0 < self.code_budget_seconds <= MAX_CODE_BUDGET_SECONDS:
            raise ValueError(
                f"code budget must be in (0, {MAX_CODE_BUDGET_SECONDS:g}] seconds"
            )


@dataclass(frozen=True, slots=True)
class AshareUniverseMember:
    """One member from a complete, current Eastmoney A-share snapshot."""

    code: str
    name: str
    board: str
    current_industry: str | None

    def __post_init__(self) -> None:
        code = _normalise_code(self.code)
        name = _required_text(self.name, "name", max_length=256)
        board = _normalise_board(self.board)
        current_industry = _optional_text(self.current_industry, "current_industry", max_length=256)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "board", board)
        object.__setattr__(self, "current_industry", current_industry)


@dataclass(frozen=True, slots=True)
class EastmoneyAshareFactBundle:
    """Provider-layer facts adapted by the workflow into its capture contract."""

    asset_code: str
    universe_fact: AshareUniverseSnapshotFact
    theme_facts: tuple[AshareThemeMembershipFact, ...]
    adjusted_price_facts: tuple[AshareAdjustedPriceFact, ...]


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
    raw_record: str


def _required_text(value: object, field: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise EastmoneyAshareProviderError(f"{field}_invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise EastmoneyAshareProviderError(f"{field}_invalid")
    return normalized


def _optional_text(value: object, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EastmoneyAshareProviderError(f"{field}_invalid")
    normalized = value.strip()
    if normalized.lower() in _MISSING_TEXT:
        return None
    if len(normalized) > max_length:
        raise EastmoneyAshareProviderError(f"{field}_invalid")
    return normalized


def _normalise_code(value: object) -> str:
    if isinstance(value, bool):
        raise EastmoneyAshareProviderError("asset_code_invalid")
    raw = str(value or "").strip()
    if not raw.isdigit() or len(raw) > 6:
        raise EastmoneyAshareProviderError("asset_code_invalid")
    normalized = raw.zfill(6)
    if len(normalized) != 6 or normalized == "000000":
        raise EastmoneyAshareProviderError("asset_code_invalid")
    return normalized


def _normalise_board(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"0", "SZ", "深", "深圳"}:
        return "SZ"
    if normalized in {"1", "SH", "沪", "上海"}:
        return "SH"
    raise EastmoneyAshareProviderError("board_invalid")


def _validate_received_at(received_at: datetime) -> datetime:
    if not isinstance(received_at, datetime):
        raise EastmoneyAshareProviderError("received_at_invalid")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise EastmoneyAshareProviderError("received_at_must_be_timezone_aware")
    return received_at


def _finite_number(value: object, field: str, *, minimum: float) -> float:
    if isinstance(value, bool) or value is None:
        raise EastmoneyAshareProviderError(f"{field}_non_finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EastmoneyAshareProviderError(f"{field}_non_finite") from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise EastmoneyAshareProviderError(f"{field}_non_finite")
    return parsed


def _payload_data(payload: object, kind: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("rc") != 0:
        raise EastmoneyAshareProviderError(f"{kind}_payload_invalid")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise EastmoneyAshareProviderError(f"{kind}_payload_invalid")
    return data


def _market_board(value: object) -> str:
    return _normalise_board(value)


def _parse_universe_payload(
    payload: object,
) -> tuple[AshareUniverseMember, ...]:
    data = _payload_data(payload, "universe")
    total = data.get("total")
    rows = data.get("diff")
    if isinstance(total, bool) or not isinstance(total, int) or not 0 < total <= MAX_UNIVERSE_ROWS:
        raise EastmoneyAshareProviderError("universe_total_invalid")
    if not isinstance(rows, list) or total != len(rows):
        raise EastmoneyAshareProviderError("universe_partial_or_invalid")

    members: list[AshareUniverseMember] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise EastmoneyAshareProviderError("universe_row_invalid")
        try:
            code = _normalise_code(row.get("f12"))
            member = AshareUniverseMember(
                code=code,
                name=_required_text(row.get("f14"), "name", max_length=256),
                board=_market_board(row.get("f13")),
                current_industry=_optional_text(
                    row.get("f100"), "current_industry", max_length=256
                ),
            )
        except EastmoneyAshareProviderError:
            raise
        if member.code in seen:
            raise EastmoneyAshareProviderError("universe_duplicate_code")
        seen.add(member.code)
        members.append(member)
    if not members:
        raise EastmoneyAshareProviderError("universe_empty")
    return tuple(sorted(members, key=lambda member: member.code))


def _parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise EastmoneyAshareProviderError("trade_date_invalid")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise EastmoneyAshareProviderError("trade_date_invalid") from exc


def _record_fields(record: object) -> tuple[str, ...]:
    if isinstance(record, str):
        fields = tuple(record.split(","))
    elif isinstance(record, (list, tuple)):
        fields = tuple(str(value) for value in record)
    else:
        raise EastmoneyAshareProviderError("adjusted_history_row_invalid")
    if len(fields) < 11:
        raise EastmoneyAshareProviderError("adjusted_history_row_incomplete")
    return fields


def _parse_adjusted_history_payload(
    payload: object,
    *,
    code: str,
    from_date: date,
    to_date: date,
    history_sessions: int,
) -> tuple[_ParsedAdjustedBar, ...]:
    data = _payload_data(payload, "adjusted_history")
    response_code = data.get("code")
    if response_code is None:
        raise EastmoneyAshareProviderError("adjusted_history_code_missing")
    if _normalise_code(response_code) != code:
        raise EastmoneyAshareProviderError("adjusted_history_code_mismatch")
    records = data.get("klines")
    if not isinstance(records, list):
        raise EastmoneyAshareProviderError("adjusted_history_missing")

    parsed: list[_ParsedAdjustedBar] = []
    seen_dates: set[date] = set()
    for record in records:
        fields = _record_fields(record)
        trade_date = _parse_date(fields[0])
        if not from_date <= trade_date <= to_date:
            continue
        if trade_date in seen_dates:
            raise EastmoneyAshareProviderError("adjusted_history_duplicate_date")
        seen_dates.add(trade_date)
        adjusted_open = _finite_number(fields[1], "adjusted_open", minimum=0.0)
        adjusted_close = _finite_number(fields[2], "adjusted_close", minimum=0.0)
        adjusted_high = _finite_number(fields[3], "adjusted_high", minimum=0.0)
        adjusted_low = _finite_number(fields[4], "adjusted_low", minimum=0.0)
        if min(adjusted_open, adjusted_high, adjusted_low, adjusted_close) <= 0:
            raise EastmoneyAshareProviderError("adjusted_ohlc_non_positive")
        if adjusted_high < max(adjusted_open, adjusted_close):
            raise EastmoneyAshareProviderError("adjusted_high_invalid")
        if adjusted_low > min(adjusted_open, adjusted_close) or adjusted_low > adjusted_high:
            raise EastmoneyAshareProviderError("adjusted_low_invalid")
        parsed.append(
            _ParsedAdjustedBar(
                trade_date=trade_date,
                adjusted_open=adjusted_open,
                adjusted_high=adjusted_high,
                adjusted_low=adjusted_low,
                adjusted_close=adjusted_close,
                volume=_finite_number(fields[5], "volume", minimum=0.0),
                amount=_finite_number(fields[6], "amount", minimum=0.0),
                turnover=_finite_number(fields[10], "turnover", minimum=0.0),
                raw_record=",".join(fields),
            )
        )
    parsed.sort(key=lambda bar: bar.trade_date)
    return tuple(parsed[-history_sessions:])


def _shanghai_date(value: datetime) -> date:
    return value.astimezone(SHANGHAI).date()


class EastmoneyAshareV2Provider:
    """Fetch one complete Eastmoney universe and adjusted facts for its members.

    Exact async contract:

    * ``await fetch_universe(received_at=...)`` returns a sorted tuple of
      :class:`AshareUniverseMember` and fails closed on any partial payload.
    * ``await fetch_facts(member, signal_date=..., received_at=...)`` returns
      :class:`V2CapturedAshareFacts`.  ``fetch_universe`` must have completed
      first, and the same receipt timestamp must be supplied to both calls.

    ``signal_date`` is required to be no later than the Shanghai date of
    ``received_at``.  The caller remains responsible for passing a completed
    trading session when requesting the day's bar.  Current industry is
    represented with an effective date equal to the receipt day, so it cannot
    be used as an earlier historical membership.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        config: EastmoneyAshareProviderConfig | None = None,
    ) -> None:
        self.config = config or EastmoneyAshareProviderConfig()
        self._client = client
        self._owns_client = client is None
        self._members: tuple[AshareUniverseMember, ...] | None = None
        self._members_by_code: dict[str, AshareUniverseMember] = {}
        self._received_at: datetime | None = None

    async def __aenter__(self) -> EastmoneyAshareV2Provider:
        if self._client is None:
            timeout = httpx.Timeout(
                self.config.request_timeout_seconds,
                connect=min(2.0, self.config.request_timeout_seconds),
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                headers=EASTMONEY_HEADERS,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise EastmoneyAshareProviderError("provider_not_open")
        return self._client

    async def _request_json(self, kind: str, params: Mapping[str, str | int]) -> object:
        client = self._require_client()
        try:
            response = await asyncio.wait_for(
                client.get(
                    EASTMONEY_A_SHARE_UNIVERSE_URL if kind == "universe" else EASTMONEY_A_SHARE_HISTORY_URL,
                    params=params,
                    headers=EASTMONEY_HEADERS,
                ),
                timeout=self.config.request_timeout_seconds,
            )
        except TimeoutError as exc:
            raise EastmoneyAshareProviderError(f"{kind}_request_timeout") from exc
        except httpx.HTTPError as exc:
            raise EastmoneyAshareProviderError(f"{kind}_request_error:{type(exc).__name__}") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise EastmoneyAshareProviderError(f"{kind}_response_too_large")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EastmoneyAshareProviderError(f"{kind}_http_status:{response.status_code}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise EastmoneyAshareProviderError(f"{kind}_json_invalid") from exc

    async def fetch_universe(self, *, received_at: datetime) -> tuple[AshareUniverseMember, ...]:
        """Fetch one complete authoritative current A-share snapshot."""

        received_at = _validate_received_at(received_at)
        if self._members is not None:
            if received_at != self._received_at:
                raise EastmoneyAshareProviderError("universe_snapshot_already_bound")
            return self._members
        payload = await self._request_json(
            "universe",
            {
                "pn": 1,
                "pz": MAX_UNIVERSE_ROWS,
                "po": 1,
                "np": 1,
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": EASTMONEY_A_SHARE_FILTER,
                "fields": "f12,f13,f14,f100",
            },
        )
        members = _parse_universe_payload(payload)
        self._members = members
        self._members_by_code = {member.code: member for member in members}
        self._received_at = received_at
        return members

    async def fetch_facts(
        self,
        member: AshareUniverseMember,
        *,
        signal_date: date,
        received_at: datetime,
        history_sessions: int = DEFAULT_HISTORY_SESSIONS,
    ) -> EastmoneyAshareFactBundle:
        """Fetch one member's current membership and adjusted OHLCV facts."""

        received_at = _validate_received_at(received_at)
        if not isinstance(signal_date, date) or isinstance(signal_date, datetime):
            raise EastmoneyAshareProviderError("signal_date_invalid")
        if signal_date > _shanghai_date(received_at):
            raise EastmoneyAshareProviderError("signal_date_after_receipt")
        if not isinstance(history_sessions, int) or isinstance(history_sessions, bool):
            raise EastmoneyAshareProviderError("history_sessions_invalid")
        if not 1 <= history_sessions <= MAX_HISTORY_SESSIONS:
            raise EastmoneyAshareProviderError("history_sessions_out_of_range")
        if self._members is None or received_at != self._received_at:
            raise EastmoneyAshareProviderError("universe_snapshot_required")
        stored_member = self._members_by_code.get(member.code)
        if stored_member != member:
            raise EastmoneyAshareProviderError("member_not_from_current_universe")

        from_date = signal_date - timedelta(days=max(365, history_sessions * 4))
        started = asyncio.get_running_loop().time()
        try:
            payload = await asyncio.wait_for(
                self._request_json(
                    "adjusted_history",
                    {
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                        "ut": "7eea3edcaed734bea9cbfc24409ed989",
                        "invt": 2,
                        "klt": 101,
                        "fqt": 2,
                        "beg": from_date.strftime("%Y%m%d"),
                        "end": signal_date.strftime("%Y%m%d"),
                        "secid": f"{1 if member.board == 'SH' else 0}.{member.code}",
                    },
                ),
                timeout=self.config.code_budget_seconds,
            )
        except TimeoutError as exc:
            raise EastmoneyAshareProviderError("code_budget_timeout") from exc
        if asyncio.get_running_loop().time() - started > self.config.code_budget_seconds:
            raise EastmoneyAshareProviderError("code_budget_exceeded")
        parsed = _parse_adjusted_history_payload(
            payload,
            code=member.code,
            from_date=from_date,
            to_date=signal_date,
            history_sessions=history_sessions,
        )
        if not parsed:
            raise EastmoneyAshareProviderError("adjusted_history_empty")

        received_date = _shanghai_date(received_at)
        # Production tables use UTC-naive timestamps.  Normalize before
        # hashing/persistence so PostgreSQL and later hash validation observe
        # exactly the same value while the provider boundary still requires an
        # unambiguous timezone-aware receipt instant.
        persisted_received_at = received_at.astimezone(UTC).replace(tzinfo=None)
        universe_fact = AshareUniverseSnapshotFact(
            snapshot_date=received_date,
            asset_code=member.code,
            asset_name=member.name,
            listing_state="listed",
            board=member.board,
            effective_at=persisted_received_at,
            received_at=persisted_received_at,
            provider=EASTMONEY_PROVIDER,
            source_cutoff=persisted_received_at,
        )
        theme_facts: tuple[AshareThemeMembershipFact, ...] = ()
        if member.current_industry is not None:
            theme_facts = (
                AshareThemeMembershipFact(
                    asset_code=member.code,
                    group_id=f"eastmoney_industry:{member.current_industry}",
                    theme=member.current_industry,
                    sector=member.current_industry,
                    effective_from=received_date,
                    effective_to=None,
                    received_at=persisted_received_at,
                    taxonomy_version=EASTMONEY_TAXONOMY_VERSION,
                    source=EASTMONEY_THEME_SOURCE,
                    confidence="observed_current",
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
                provider=EASTMONEY_PROVIDER,
                adjustment_version=EASTMONEY_ADJUSTMENT_VERSION,
                revision_id=stable_contract_hash(
                    {
                        "provider": EASTMONEY_PROVIDER,
                        "adjustment_version": EASTMONEY_ADJUSTMENT_VERSION,
                        "asset_code": member.code,
                        "trade_date": bar.trade_date,
                        "record": bar.raw_record,
                    }
                ),
                received_at=persisted_received_at,
                historical_research_only=False,
                decision_eligible=True,
            )
            for bar in parsed
        )
        return EastmoneyAshareFactBundle(
            asset_code=member.code,
            universe_fact=universe_fact,
            theme_facts=theme_facts,
            adjusted_price_facts=price_facts,
        )


__all__ = [
    "AshareUniverseMember",
    "DEFAULT_HISTORY_SESSIONS",
    "EASTMONEY_ADJUSTMENT_VERSION",
    "EastmoneyAshareFactBundle",
    "EastmoneyAshareProviderConfig",
    "EastmoneyAshareProviderError",
    "EastmoneyAshareV2Provider",
]
