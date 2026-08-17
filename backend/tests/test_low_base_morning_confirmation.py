from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.strategy_lab.dual_universe_leader_tactics_v2_intraday_confirmation import (
    MorningConfirmationBar,
    MorningWatchCandidate,
    evaluate_low_base_morning_confirmation,
    persist_confirmed_morning_transition,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _candidate() -> MorningWatchCandidate:
    return MorningWatchCandidate(
        manifest_hash="m" * 64,
        asset_code="002437",
        asset_name="誉衡药业",
        signal_date=date(2026, 8, 17),
        source_cutoff=datetime(2026, 8, 17, 15, 0),
        feature_hash="f" * 64,
        signal_adjusted_close=10.0,
        signal_adjusted_high=10.2,
        signal_adjusted_ma5=9.8,
        signal_adjusted_ma20=9.5,
        signal_adjusted_atr20=1.0,
    )


def _bars(*, volume: float = 200.0, close: float = 10.2) -> tuple[MorningConfirmationBar, ...]:
    opened = datetime(2026, 8, 18, 9, 30, tzinfo=SHANGHAI)
    received = datetime(2026, 8, 18, 10, 41, tzinfo=SHANGHAI)
    return tuple(
        MorningConfirmationBar(
            bar_start=opened + timedelta(minutes=index * 10),
            bar_end=opened + timedelta(minutes=(index + 1) * 10),
            raw_open=10.0,
            raw_high=max(10.3, close),
            raw_low=9.9,
            raw_close=close,
            volume=volume,
            received_at=received,
            normalization_factor=1.0,
            normalization_identity="identity-v1",
        )
        for index in range(7)
    )


def _evaluate(
    bars: tuple[MorningConfirmationBar, ...],
    *,
    decision_at: datetime | None = None,
):
    return evaluate_low_base_morning_confirmation(
        candidate=_candidate(),
        decision_at=decision_at
        or datetime(2026, 8, 18, 10, 42, tzinfo=SHANGHAI),
        bars=bars,
        prior_20_mean_daily_volume=1_000.0,
    )


def test_observed_morning_volume_and_safe_price_confirm_actionable() -> None:
    result = _evaluate(_bars())

    assert result.available is True
    assert result.confirmed is True
    assert result.entry_status == "actionable"
    assert result.closed_bar_count == 7
    assert result.cumulative_volume_ratio == 1.4
    assert result.overextension_atr == pytest.approx(0.7)


def test_weak_morning_volume_remains_watch() -> None:
    result = _evaluate(_bars(volume=100.0))

    assert result.available is True
    assert result.confirmed is False
    assert result.entry_status == "watch"
    assert result.reason == "morning_cumulative_volume_not_confirmed"


def test_unsafe_atr_extension_remains_watch() -> None:
    result = _evaluate(_bars(close=11.2))

    assert result.available is True
    assert result.confirmed is False
    assert result.reason == "morning_atr_extension_unsafe"


def test_future_or_late_bars_cannot_create_confirmation() -> None:
    future = replace(
        _bars()[0],
        bar_start=datetime(2026, 8, 18, 10, 40, tzinfo=SHANGHAI),
        bar_end=datetime(2026, 8, 18, 10, 50, tzinfo=SHANGHAI),
    )
    late = replace(
        _bars()[1],
        received_at=datetime(2026, 8, 18, 10, 43, tzinfo=SHANGHAI),
    )
    result = _evaluate((_bars()[2], future, late))

    assert result.available is False
    assert result.confirmed is False
    assert result.reason == "insufficient_closed_10m_bars"


def test_invalid_ohlc_and_normalization_conflict_fail_closed() -> None:
    bars = list(_bars())
    bars[0] = replace(bars[0], raw_high=9.8)
    bars[1] = replace(bars[1], normalization_identity="identity-v2")
    result = _evaluate(tuple(bars))

    assert result.available is False
    assert result.confirmed is False
    assert result.reason == "intraday_normalization_identity_conflict"


def test_missing_historical_intraday_evidence_stays_unavailable() -> None:
    result = _evaluate(())

    assert result.available is False
    assert result.confirmed is False
    assert result.entry_status == "watch"
    assert result.reason == "insufficient_closed_10m_bars"


@pytest.mark.asyncio
async def test_confirmed_transition_is_append_only_and_idempotent(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'confirmation.db'}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE leader_tactics_v2_state_transitions (
                    id INTEGER PRIMARY KEY,
                    manifest_hash TEXT NOT NULL,
                    universe TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    formula_id TEXT NOT NULL,
                    signal_date DATE NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    transition_date DATE NOT NULL,
                    payload_json TEXT NOT NULL,
                    transition_hash TEXT NOT NULL UNIQUE,
                    created_at DATETIME NOT NULL,
                    evidence_cutoff DATETIME,
                    projected_entry_status TEXT
                )
                """
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    result = _evaluate(_bars())
    async with factory() as session:
        assert await persist_confirmed_morning_transition(
            session,
            candidate=_candidate(),
            result=result,
        ) == 1
        assert await persist_confirmed_morning_transition(
            session,
            candidate=_candidate(),
            result=result,
        ) == 0
        await session.commit()
        row = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS count, MIN(projected_entry_status) AS entry_status
                    FROM leader_tactics_v2_state_transitions
                    """
                )
            )
        ).mappings().one()
    await engine.dispose()

    assert int(row["count"]) == 1
    assert row["entry_status"] == "actionable"
