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
        "tencent": data.TENCENT_HFQ_ADJUSTMENT_VERSION,
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


def _adjusted_rows(
    provider: str,
    count: int,
) -> list[dict[str, float | str]]:
    base = _adjusted_row(provider)[0]
    return [
        {
            **base,
            "date": date(2026, 7, 20 + offset).isoformat(),
        }
        for offset in range(count)
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


@pytest.mark.asyncio
async def test_provider_filters_invalid_rows_from_partly_valid_response() -> None:
    async def mixed(*_args: object) -> list[dict[str, float | str]]:
        valid = _adjusted_row("eastmoney")[0]
        invalid = {**valid, "date": "2026-07-21", "provider_version": "wrong"}
        return [valid, invalid]

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.1,
        providers=(("eastmoney", mixed),),
    ) as fetch:
        result = await fetch(
            "510050",
            date(2026, 7, 20),
            date(2026, 7, 21),
        )

    assert result.rows == _adjusted_row("eastmoney")


@pytest.mark.asyncio
async def test_short_primary_history_falls_through_to_deeper_adjusted_provider() -> None:
    calls: list[str] = []

    async def shallow(*_args: object) -> list[dict[str, float | str]]:
        calls.append("tickflow")
        return _adjusted_rows("tickflow", 2)

    async def deep(*_args: object) -> list[dict[str, float | str]]:
        calls.append("eastmoney")
        return _adjusted_rows("eastmoney", 3)

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.1,
        minimum_eligible_rows=3,
        providers=(("tickflow", shallow), ("eastmoney", deep)),
    ) as fetch:
        result = await fetch(
            "510050",
            date(2026, 7, 20),
            date(2026, 7, 22),
        )

    assert calls == ["tickflow", "eastmoney"]
    assert result.provider == "eastmoney"
    assert result.fallback_used is True
    assert len(result.rows) == 3
    assert result.primary_error == "tickflow:eligible_rows_below_minimum:2<3"
    assert result.provider_health is not None
    assert (
        result.provider_health["providers"]["tickflow"]["short_history_count"]
        == 1
    )


@pytest.mark.asyncio
async def test_dynamic_minimum_counts_only_missing_required_dates() -> None:
    calls: list[str] = []

    async def wrong_dates(*_args: object) -> list[dict[str, float | str]]:
        calls.append("tickflow")
        rows = _adjusted_rows("tickflow", 3)
        return [rows[0], rows[2]]

    async def exact_dates(*_args: object) -> list[dict[str, float | str]]:
        calls.append("eastmoney")
        return _adjusted_rows("eastmoney", 2)

    required_dates = (date(2026, 7, 20), date(2026, 7, 21))
    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.1,
        providers=(
            ("tickflow", wrong_dates),
            ("eastmoney", exact_dates),
        ),
    ) as fetch:
        result = await fetch.fetch_with_minimum(
            "510050",
            date(2026, 7, 20),
            date(2026, 7, 22),
            minimum_eligible_rows=2,
            required_trade_dates=required_dates,
        )

    assert calls == ["tickflow", "eastmoney"]
    assert result.provider == "eastmoney"
    assert [row["date"] for row in result.rows] == [
        "2026-07-20",
        "2026-07-21",
    ]


@pytest.mark.asyncio
async def test_deepest_valid_partial_is_kept_when_all_providers_are_short() -> None:
    async def two_rows(*_args: object) -> list[dict[str, float | str]]:
        return _adjusted_rows("tickflow", 2)

    async def three_rows(*_args: object) -> list[dict[str, float | str]]:
        return _adjusted_rows("eastmoney", 3)

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.1,
        minimum_eligible_rows=5,
        providers=(("tickflow", two_rows), ("eastmoney", three_rows)),
    ) as fetch:
        result = await fetch(
            "510050",
            date(2026, 7, 20),
            date(2026, 7, 24),
        )

    assert result.provider == "eastmoney"
    assert len(result.rows) == 3
    assert result.provider_health is not None
    assert (
        result.provider_health["providers"]["eastmoney"][
            "short_history_count"
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_independent_tencent_adjusted_fallback_survives_eastmoney_failure() -> None:
    calls: list[str] = []

    async def shallow(*_args: object) -> list[dict[str, float | str]]:
        calls.append("tickflow")
        return _adjusted_rows("tickflow", 2)

    async def disconnected(*_args: object) -> list[dict[str, float | str]]:
        calls.append("eastmoney")
        raise ConnectionError("push2his disconnected")

    async def independent(*_args: object) -> list[dict[str, float | str]]:
        calls.append("tencent")
        return _adjusted_rows("tencent", 3)

    async def unused(*_args: object) -> list[dict[str, float | str]]:
        raise AssertionError("efinance must not run after a sufficient result")

    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=0.1,
        minimum_eligible_rows=3,
        providers=(
            ("tickflow", shallow),
            ("eastmoney", disconnected),
            ("tencent", independent),
            ("efinance", unused),
        ),
    ) as fetch:
        result = await fetch(
            "510050",
            date(2026, 7, 20),
            date(2026, 7, 22),
        )

    assert calls == ["tickflow", "eastmoney", "tencent"]
    assert result.provider == "tencent"
    assert result.fallback_used is True
    assert len(result.rows) == 3
    assert result.provider_health is not None
    assert result.provider_health["providers"]["eastmoney"]["last_error"].startswith(
        "ConnectionError:"
    )
