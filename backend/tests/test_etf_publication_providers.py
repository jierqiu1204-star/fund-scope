from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.services.short_etf import data
from app.services.short_etf.publication_providers import (
    PublicationAdjustedHistoryFetcher,
    PublicationProviderError,
)


def _adjusted_row(provider: str) -> list[dict[str, float | str]]:
    versions = {
        "tickflow": data.TICKFLOW_BACKWARD_ADJUSTMENT_VERSION,
        "eastmoney": data.EASTMONEY_HFQ_ADJUSTMENT_VERSION,
        "efinance": data.EFINANCE_HFQ_ADJUSTMENT_VERSION,
    }
    version = versions[provider]
    return [
        {
            "date": "2026-07-20",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "turnover": 1.0,
            "pct_change": 0.0,
            "research_adjusted_value": 1.0,
            "research_price_basis": data.TOTAL_RETURN_PRICE_BASIS,
            "adjustment_version": version,
            "provider_version": version,
        }
    ]


@pytest.mark.asyncio
async def test_per_provider_timeout_allows_adjusted_fallback() -> None:
    calls: list[str] = []
    primary_cancelled = False

    async def blocked_primary(*_args: object) -> list[dict[str, float | str]]:
        nonlocal primary_cancelled
        calls.append("tickflow")
        try:
            await asyncio.Event().wait()
        finally:
            primary_cancelled = True

    async def working_fallback(*_args: object) -> list[dict[str, float | str]]:
        calls.append("eastmoney")
        return _adjusted_row("eastmoney")

    async def unused(*_args: object) -> list[dict[str, float | str]]:
        raise AssertionError("third provider must not run")

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.01,
        providers=(
            ("tickflow", blocked_primary),
            ("eastmoney", working_fallback),
            ("efinance", unused),
        ),
    ) as fetch:
        result = await fetch(
            "510050",
            date(2026, 7, 20),
            date(2026, 7, 20),
        )

    assert calls == ["tickflow", "eastmoney"]
    assert primary_cancelled is True
    assert result.provider == "eastmoney"
    assert result.fallback_used is True
    assert result.provider_health is not None
    assert result.provider_health["providers"]["tickflow"]["timeout_count"] == 1


@pytest.mark.asyncio
async def test_whole_chain_outer_timeout_would_prevent_same_fallback() -> None:
    fallback_called = False

    async def legacy_chain() -> None:
        nonlocal fallback_called
        await asyncio.sleep(1)
        fallback_called = True

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(legacy_chain(), timeout=0.01)
    assert fallback_called is False


@pytest.mark.asyncio
async def test_raw_only_and_sina_never_satisfy_publication_policy() -> None:
    async def raw_only(*_args: object) -> list[dict[str, float | str]]:
        return [
            {
                "date": "2026-07-20",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "turnover": 1.0,
                "pct_change": 0.0,
            }
        ]

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.1,
        providers=(
            ("tickflow", raw_only),
            ("eastmoney", raw_only),
            ("efinance", raw_only),
            ("sina", raw_only),
        ),
    ) as fetch:
        with pytest.raises(PublicationProviderError) as caught:
            await fetch("510050", date(2026, 7, 20), date(2026, 7, 20))

    assert "no_provenance_valid_adjusted_rows" in str(caught.value)
    assert "sina" not in caught.value.provider_health["providers"]


@pytest.mark.asyncio
async def test_external_cancellation_leaves_no_provider_task() -> None:
    started = asyncio.Event()
    cancelled = False

    async def blocked(*_args: object) -> list[dict[str, float | str]]:
        nonlocal cancelled
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=1,
        providers=(("tickflow", blocked),),
    ) as fetch:
        task = asyncio.create_task(
            fetch("510050", date(2026, 7, 20), date(2026, 7, 20))
        )
        await asyncio.wait_for(started.wait(), timeout=0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"date": "2026-07-19"},
        {"research_adjusted_value": float("nan")},
        {"research_price_basis": "raw_ohlc"},
        {"adjustment_version": ""},
        {"provider_version": ""},
    ],
)
async def test_invalid_adjusted_provenance_is_rejected(
    mutation: dict[str, float | str],
) -> None:
    async def invalid(*_args: object) -> list[dict[str, float | str]]:
        row = _adjusted_row("eastmoney")[0]
        return [{**row, **mutation}]

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.1,
        providers=(("eastmoney", invalid),),
    ) as fetch:
        with pytest.raises(PublicationProviderError):
            await fetch("510050", date(2026, 7, 20), date(2026, 7, 20))
