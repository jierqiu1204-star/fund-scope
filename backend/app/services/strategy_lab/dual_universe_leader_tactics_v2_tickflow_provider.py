"""Bounded TickFlow/BaoStock facts for the A-share V2 research path.

TickFlow supplies the current A-share universe, security metadata and explicit
backward-adjusted daily OHLCV.  BaoStock supplies only the current industry
label through a physically bounded child process.  The combined snapshot is
point-in-time from its actual receipt timestamp onward; it is never backdated.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
BAOSTOCK_THEME_SOURCE = "baostock.query_stock_industry.current"
BAOSTOCK_TAXONOMY_VERSION = "baostock.industry.current_v1"
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
MAX_HISTORY_SESSIONS = 300
DEFAULT_HISTORY_SESSIONS = 180
DEFAULT_REQUEST_TIMEOUT_SECONDS = 6.0
MAX_REQUEST_TIMEOUT_SECONDS = 8.0
DEFAULT_UNIVERSE_BUDGET_SECONDS = 40.0
MAX_UNIVERSE_BUDGET_SECONDS = 45.0
DEFAULT_CODE_BUDGET_SECONDS = 10.0
MAX_CODE_BUDGET_SECONDS = 20.0
BAOSTOCK_SUBPROCESS_TIMEOUT_SECONDS = 18.0


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
class TickflowAshareMember:
    symbol: str
    code: str
    name: str
    board: str
    current_industry: str | None
    float_shares: float | None

    def __post_init__(self) -> None:
        symbol, code, board = _normalise_symbol(self.symbol)
        if self.code != code or self.board != board:
            raise TickflowAshareProviderError("instrument_identity_mismatch")
        name = _required_text(self.name, "name", max_length=256)
        industry = _optional_text(self.current_industry, "current_industry", max_length=128)
        float_shares = _optional_positive_number(self.float_shares, "float_shares")
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


IndustryLoader = Callable[[], Awaitable[Mapping[str, str]]]
_CURRENT_UNIVERSE_CACHE: tuple[date, tuple[TickflowAshareMember, ...]] | None = None


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


def _payload_data(payload: object, kind: str) -> object:
    if not isinstance(payload, Mapping) or "data" not in payload:
        raise TickflowAshareProviderError(f"{kind}_payload_invalid")
    return payload["data"]


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
    industries: Mapping[str, str],
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
        members.append(
            TickflowAshareMember(
                symbol=symbol,
                code=code,
                name=_required_text(row.get("name"), "name", max_length=256),
                board=board,
                current_industry=industries.get(symbol),
                float_shares=_optional_positive_number(ext.get("float_shares"), "float_shares"),
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


async def _load_baostock_industries() -> Mapping[str, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.strategy_lab.dual_universe_leader_tactics_v2_baostock_snapshot",
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
    if process.returncode != 0:
        summary = stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        error_summary = "".join(summary) or f"returncode={process.returncode}"
        raise TickflowAshareProviderError(
            f"baostock_industry_failed:{error_summary[:200]}"
        )
    if len(stdout) > MAX_SUBPROCESS_BYTES:
        raise TickflowAshareProviderError("baostock_industry_response_too_large")
    try:
        payload = json.loads(stdout)
    except ValueError as exc:
        raise TickflowAshareProviderError("baostock_industry_json_invalid") from exc
    if not isinstance(payload, Mapping):
        raise TickflowAshareProviderError("baostock_industry_payload_invalid")
    industries: dict[str, str] = {}
    for raw_symbol, raw_industry in payload.items():
        symbol = _normalise_symbol(raw_symbol)[0]
        industry = _required_text(raw_industry, "industry", max_length=128)
        industries[symbol] = industry
    if not industries:
        raise TickflowAshareProviderError("baostock_industry_empty")
    return industries


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
        self._industry_loader = industry_loader or _load_baostock_industries
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
            try:
                industries = dict(await self._industry_loader())
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

    async def fetch_facts(
        self,
        member: TickflowAshareMember,
        *,
        signal_date: date,
        received_at: datetime,
        history_sessions: int = DEFAULT_HISTORY_SESSIONS,
    ) -> TickflowAshareFactBundle:
        received_at = _validate_received_at(received_at)
        if signal_date > received_at.astimezone(SHANGHAI).date():
            raise TickflowAshareProviderError("signal_date_after_receipt")
        if not isinstance(history_sessions, int) or isinstance(history_sessions, bool):
            raise TickflowAshareProviderError("history_sessions_invalid")
        if not 1 <= history_sessions <= MAX_HISTORY_SESSIONS:
            raise TickflowAshareProviderError("history_sessions_out_of_range")
        if self._members is None or self._received_at != received_at:
            raise TickflowAshareProviderError("universe_snapshot_required")
        if self._universe_observed_at is None:
            raise TickflowAshareProviderError("universe_receipt_missing")
        if self._members_by_code.get(member.code) != member:
            raise TickflowAshareProviderError("member_not_from_current_universe")

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

        universe_received_at = self._universe_observed_at
        price_received_at = datetime.now(UTC).replace(tzinfo=None)
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
            theme_facts = (
                AshareThemeMembershipFact(
                    asset_code=member.code,
                    group_id=f"baostock_industry:{member.current_industry}",
                    theme=member.current_industry,
                    sector=member.current_industry,
                    effective_from=signal_date,
                    effective_to=None,
                    received_at=universe_received_at,
                    taxonomy_version=BAOSTOCK_TAXONOMY_VERSION,
                    source=BAOSTOCK_THEME_SOURCE,
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


__all__ = [
    "BAOSTOCK_TAXONOMY_VERSION",
    "DEFAULT_HISTORY_SESSIONS",
    "TICKFLOW_ADJUSTMENT_VERSION",
    "TICKFLOW_PROVIDER",
    "TICKFLOW_UNIVERSE_SOURCE",
    "TickflowAshareFactBundle",
    "TickflowAshareMember",
    "TickflowAshareProviderConfig",
    "TickflowAshareProviderError",
    "TickflowAshareV2Provider",
]
