from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any

import httpx

from app.services.short_etf import data
from app.services.short_etf.data import PriceHistoryRows, ProviderFetchResult

PUBLICATION_PROVIDER_POLICY_VERSION = "adjusted-provider-policy-v2"
MAX_PROVIDER_ATTEMPT_SECONDS = 6.0
PROVIDER_COOLDOWN_SECONDS = 300
ACCEPTED_ADJUSTED_PROVIDER_VERSIONS = {
    "tickflow": data.TICKFLOW_BACKWARD_ADJUSTMENT_VERSION,
    "eastmoney": data.EASTMONEY_HFQ_ADJUSTMENT_VERSION,
    "tencent": data.TENCENT_HFQ_ADJUSTMENT_VERSION,
    "efinance": data.EFINANCE_HFQ_ADJUSTMENT_VERSION,
}

AdjustedProvider = Callable[[str, date, date], Awaitable[PriceHistoryRows]]


class PublicationProviderError(RuntimeError):
    def __init__(self, summary: str, provider_health: dict[str, Any]) -> None:
        super().__init__(summary)
        self.provider_health = provider_health


def _default_health() -> dict[str, Any]:
    return {
        "consecutive_failures": 0,
        "accepted_successes": 0,
        "short_history_count": 0,
        "timeout_count": 0,
        "latency_ms": 0.0,
        "circuit_state": "closed",
        "retry_after": None,
        "last_error": None,
    }


def _accepted_adjusted_rows(
    provider: str,
    rows: PriceHistoryRows,
    *,
    from_date: date,
    to_date: date,
) -> PriceHistoryRows:
    expected_version = ACCEPTED_ADJUSTED_PROVIDER_VERSIONS[provider]
    accepted: PriceHistoryRows = []
    for row in rows:
        try:
            trade_date = date.fromisoformat(str(row["date"]))
            adjusted = float(row["research_adjusted_value"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            from_date <= trade_date <= to_date
            and isfinite(adjusted)
            and adjusted > 0
            and row.get("research_price_basis") == data.TOTAL_RETURN_PRICE_BASIS
            and row.get("adjustment_version") == expected_version
            and row.get("provider_version") == expected_version
        ):
            accepted.append(row)
    return accepted


class PublicationAdjustedHistoryFetcher:
    def __init__(
        self,
        *,
        attempt_timeout_seconds: float = MAX_PROVIDER_ATTEMPT_SECONDS,
        minimum_eligible_rows: int = 1,
        restored_health: Mapping[str, Mapping[str, Any]] | None = None,
        providers: tuple[tuple[str, AdjustedProvider], ...] | None = None,
    ) -> None:
        if not 0 < attempt_timeout_seconds <= MAX_PROVIDER_ATTEMPT_SECONDS:
            raise ValueError("provider attempt timeout must be between 0 and 6 seconds")
        if not 1 <= minimum_eligible_rows <= 5_000:
            raise ValueError("minimum eligible rows must be between 1 and 5000")
        self.attempt_timeout_seconds = attempt_timeout_seconds
        self.minimum_eligible_rows = minimum_eligible_rows
        self._injected_providers = providers
        self._providers: tuple[tuple[str, AdjustedProvider], ...] = ()
        self._client: httpx.AsyncClient | None = None
        self._health = {
            provider: {
                **_default_health(),
                **dict((restored_health or {}).get(provider, {})),
            }
            for provider in ACCEPTED_ADJUSTED_PROVIDER_VERSIONS
        }

    async def __aenter__(self) -> PublicationAdjustedHistoryFetcher:
        if self._injected_providers is not None:
            self._providers = self._injected_providers
            return self
        self._client = httpx.AsyncClient(
            timeout=self.attempt_timeout_seconds,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )
        self._providers = (
            (
                "tickflow",
                lambda code, from_date, to_date: data.fetch_tickflow_etf_price_history_once(
                    self._require_client(), code, from_date, to_date
                ),
            ),
            (
                "eastmoney",
                lambda code, from_date, to_date: data.fetch_eastmoney_etf_price_history_once(
                    self._require_client(),
                    code,
                    from_date,
                    to_date,
                    keep_alive=True,
                ),
            ),
            (
                "tencent",
                lambda code, from_date, to_date: data.fetch_tencent_etf_price_history_once(
                    self._require_client(),
                    code,
                    from_date,
                    to_date,
                ),
            ),
            ("efinance", data.fetch_efinance_etf_price_history_once),
        )
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._providers = ()

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("publication provider pool is not open")
        return self._client

    def health_payload(self) -> dict[str, Any]:
        return {
            "policy_version": PUBLICATION_PROVIDER_POLICY_VERSION,
            "providers": {
                provider: dict(health) for provider, health in self._health.items()
            },
        }

    @staticmethod
    def _retry_at(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    async def __call__(
        self,
        code: str,
        from_date: date,
        to_date: date,
    ) -> ProviderFetchResult:
        return await self.fetch_with_minimum(
            code,
            from_date,
            to_date,
            minimum_eligible_rows=self.minimum_eligible_rows,
        )

    async def fetch_with_minimum(
        self,
        code: str,
        from_date: date,
        to_date: date,
        *,
        minimum_eligible_rows: int,
        required_trade_dates: tuple[date, ...] = (),
    ) -> ProviderFetchResult:
        if not self._providers:
            raise RuntimeError("publication provider pool is not open")
        if not 1 <= minimum_eligible_rows <= 5_000:
            raise ValueError("minimum eligible rows must be between 1 and 5000")
        if required_trade_dates and (
            required_trade_dates != tuple(sorted(set(required_trade_dates)))
            or required_trade_dates[0] < from_date
            or required_trade_dates[-1] > to_date
        ):
            raise ValueError("required trade dates must be unique, ordered, and inside the request")
        required_date_set = set(required_trade_dates)
        errors: list[str] = []
        attempted_index = 0
        deepest_partial: tuple[int, str, PriceHistoryRows, int] | None = None
        now = datetime.utcnow()
        for provider, fetcher in self._providers:
            if provider not in ACCEPTED_ADJUSTED_PROVIDER_VERSIONS:
                continue
            health = self._health[provider]
            retry_at = self._retry_at(health.get("retry_after"))
            if retry_at is not None and retry_at > now:
                health["circuit_state"] = "open"
                errors.append(f"{provider}:circuit_open")
                continue
            health["circuit_state"] = "closed"
            started = time.monotonic()
            task = asyncio.ensure_future(fetcher(code, from_date, to_date))
            try:
                rows = await asyncio.wait_for(
                    task,
                    timeout=self.attempt_timeout_seconds,
                )
                accepted_rows = _accepted_adjusted_rows(
                    provider,
                    rows,
                    from_date=from_date,
                    to_date=to_date,
                )
                if not accepted_rows:
                    raise ValueError("no_provenance_valid_adjusted_rows")
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                health["timeout_count"] = int(health["timeout_count"] or 0) + 1
                error = "provider_timeout"
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}:{str(exc)[:120]}"
            else:
                accepted_dates = {
                    date.fromisoformat(str(row["date"]))
                    for row in accepted_rows
                }
                eligible_date_count = len(
                    accepted_dates & required_date_set
                    if required_date_set
                    else accepted_dates
                )
                health["consecutive_failures"] = 0
                health["accepted_successes"] = int(health["accepted_successes"] or 0) + 1
                health["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
                health["retry_after"] = None
                health["last_error"] = None
                if eligible_date_count >= minimum_eligible_rows:
                    return ProviderFetchResult(
                        rows=accepted_rows,
                        provider=provider,
                        fallback_used=attempted_index > 0,
                        primary_error=errors[0] if errors else None,
                        provider_health=self.health_payload(),
                    )
                health["short_history_count"] = (
                    int(health["short_history_count"] or 0) + 1
                )
                if (
                    deepest_partial is None
                    or eligible_date_count > deepest_partial[0]
                ):
                    deepest_partial = (
                        eligible_date_count,
                        provider,
                        accepted_rows,
                        attempted_index,
                    )
                errors.append(
                    f"{provider}:eligible_rows_below_minimum:"
                    f"{eligible_date_count}<{minimum_eligible_rows}"
                )
                attempted_index += 1
                continue
            health["consecutive_failures"] = int(health["consecutive_failures"] or 0) + 1
            health["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
            health["last_error"] = error
            if health["consecutive_failures"] >= 3:
                health["circuit_state"] = "open"
                health["retry_after"] = (
                    datetime.utcnow() + timedelta(seconds=PROVIDER_COOLDOWN_SECONDS)
                ).isoformat()
            errors.append(f"{provider}:{error}")
            attempted_index += 1
        if deepest_partial is not None:
            _, provider, rows, provider_index = deepest_partial
            return ProviderFetchResult(
                rows=rows,
                provider=provider,
                fallback_used=provider_index > 0,
                primary_error=errors[0] if errors else None,
                provider_health=self.health_payload(),
            )
        raise PublicationProviderError(";".join(errors), self.health_payload())


__all__ = [
    "ACCEPTED_ADJUSTED_PROVIDER_VERSIONS",
    "MAX_PROVIDER_ATTEMPT_SECONDS",
    "PUBLICATION_PROVIDER_POLICY_VERSION",
    "PublicationAdjustedHistoryFetcher",
    "PublicationProviderError",
]
