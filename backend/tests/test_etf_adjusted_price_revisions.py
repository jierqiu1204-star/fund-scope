from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from app.models.entities import (
    AdjustedPriceRevisionImmutableError,
    EtfAdjustedPriceRevision,
    EtfPointInTimeMembershipFact,
    EtfPriceHistory,
    TradableEtf,
)
from app.services.market_data import etf_adjusted_daily_facts_on_or_before
from app.services.short_etf.data import persist_etf_price_history_page
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    load_point_in_time_ranking_inputs,
)


def _etf(code: str) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"revision-{code}",
        exchange="SH",
        theme_tags_json=[],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="broad",
        is_short_term_eligible=True,
        is_watchlist=True,
    )


def _values(
    *,
    adjusted_close: float,
    observed_at: datetime,
    provider: str = "eastmoney",
    provider_version: str = "eastmoney.push2his.kline.hfq_v1",
    decision_eligible: bool = True,
) -> dict[str, object]:
    return {
        "open": 1.0,
        "high": 1.02,
        "low": 0.98,
        "close": 1.0,
        "volume": 1_000_000.0,
        "turnover": 100_000_000.0,
        "pct_change": 0.0,
        "raw_price_basis": "raw_ohlc",
        "research_adjusted_value": adjusted_close if decision_eligible else None,
        "research_price_basis": (
            "total_return_adjusted" if decision_eligible else None
        ),
        "data_provider": provider,
        "provider_version": provider_version if decision_eligible else None,
        "source_timestamp": observed_at,
        "adjustment_version": provider_version if decision_eligible else None,
        "decision_eligible": decision_eligible,
        "decision_ineligibility_reason": (
            None if decision_eligible else "missing_total_return_provenance"
        ),
    }


@pytest.mark.asyncio
async def test_adjusted_price_revisions_are_append_only_idempotent_and_superseded(app) -> None:
    code = "510801"
    trade_date = date(2026, 7, 10)
    first_seen = datetime(2026, 7, 10, 6, 0)
    repeat_seen = datetime(2026, 7, 10, 7, 0)
    changed_seen = datetime(2026, 7, 10, 8, 0)

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()

        first = await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[(trade_date, _values(adjusted_close=1.2, observed_at=first_seen))],
        )
        await session.commit()
        repeated = await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[(trade_date, _values(adjusted_close=1.2, observed_at=repeat_seen))],
        )
        await session.commit()
        projection_after_repeat = await session.scalar(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code == code,
                EtfPriceHistory.trade_date == trade_date,
            )
        )
        assert projection_after_repeat is not None
        assert projection_after_repeat.source_timestamp == first_seen
        changed = await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[(trade_date, _values(adjusted_close=1.4, observed_at=changed_seen))],
        )
        await session.commit()
        session.expire_all()

        revisions = (
            await session.scalars(
                select(EtfAdjustedPriceRevision)
                .where(
                    EtfAdjustedPriceRevision.etf_code == code,
                    EtfAdjustedPriceRevision.trade_date == trade_date,
                )
                .order_by(EtfAdjustedPriceRevision.id.asc())
            )
        ).all()
        projection = await session.scalar(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code == code,
                EtfPriceHistory.trade_date == trade_date,
            )
        )

        assert first.revision_rows == 1
        assert repeated.revision_rows == 0
        assert repeated.unchanged_rows == 1
        assert changed.revision_rows == 1
        assert changed.updated_rows == 1
        assert len(revisions) == 2
        assert revisions[0].first_seen_at == first_seen
        assert revisions[0].observed_at == first_seen
        assert revisions[1].supersedes_revision_id == revisions[0].id
        assert revisions[0].revision_hash != revisions[1].revision_hash
        assert projection is not None
        assert projection.source_timestamp == changed_seen
        assert projection.research_adjusted_value == 1.4

        revisions[0].close = 99.0
        with pytest.raises(AdjustedPriceRevisionImmutableError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_cutoff_selector_uses_latest_compatible_revision_and_excludes_legacy_rows(
    app,
) -> None:
    code = "510802"
    legacy_code = "510803"
    trade_date = date(2026, 7, 10)
    first_seen = datetime(2026, 7, 10, 6, 0)
    late_seen = datetime(2026, 7, 10, 8, 0)
    raw_seen = datetime(2026, 7, 10, 9, 0)

    async with app.state.db.session() as session:
        session.add_all([_etf(code), _etf(legacy_code)])
        session.add(
            EtfPriceHistory(
                etf_code=legacy_code,
                trade_date=trade_date,
                **_values(adjusted_close=1.1, observed_at=first_seen),
            )
        )
        await session.commit()

        await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[(trade_date, _values(adjusted_close=1.1, observed_at=first_seen))],
        )
        await session.commit()
        before = await etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=(code,),
            replay_date=trade_date,
            rows_per_code=1,
            max_source_rows=1,
            decision_cutoff=datetime(2026, 7, 10, 7, 0),
        )

        await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[
                (
                    trade_date,
                    _values(
                        adjusted_close=1.3,
                        observed_at=late_seen,
                        provider="tickflow",
                        provider_version="tickflow.free.klines.backward_v1",
                    ),
                )
            ],
        )
        await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[
                (
                    trade_date,
                    _values(
                        adjusted_close=1.3,
                        observed_at=raw_seen,
                        provider="sina",
                        decision_eligible=False,
                    ),
                )
            ],
        )
        await session.commit()
        after = await etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=(code,),
            replay_date=trade_date,
            rows_per_code=1,
            max_source_rows=1,
            decision_cutoff=datetime(2026, 7, 10, 10, 0),
        )
        legacy_pit = await etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=(legacy_code,),
            replay_date=trade_date,
            rows_per_code=1,
            max_source_rows=1,
            decision_cutoff=datetime(2026, 7, 10, 10, 0),
        )
        legacy_current = await etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=(legacy_code,),
            replay_date=trade_date,
            rows_per_code=1,
            max_source_rows=1,
        )

    assert len(before) == 1
    assert len(after) == 1
    assert before[0].adjusted_close == 1.1
    assert after[0].adjusted_close == 1.3
    assert before[0].revision_hash is not None
    assert after[0].revision_hash is not None
    assert before[0].revision_hash != after[0].revision_hash
    assert legacy_pit == ()
    assert len(legacy_current) == 1
    assert legacy_current[0].revision_hash is None


@pytest.mark.asyncio
async def test_replay_input_binds_selected_revision_hashes(app) -> None:
    code = "510804"
    replay_date = date(2022, 6, 30)
    initial_cutoff = datetime(2022, 6, 30, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    later_cutoff = datetime(2022, 6, 30, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    source_seen = datetime(2022, 6, 30, 6, 0)
    revised_seen = datetime(2022, 6, 30, 7, 30)

    dates: list[date] = []
    cursor = replay_date
    while len(dates) < 61:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    dates.reverse()

    async with app.state.db.session() as session:
        session.add(_etf(code))
        digest = hashlib.sha256(code.encode()).hexdigest()
        session.add(
            EtfPointInTimeMembershipFact(
                etf_code=code,
                external_source_id=f"fixture:{code}",
                provider="fixture",
                provider_version="v1",
                observed_at=datetime(2021, 1, 1),
                effective_from=date(2020, 1, 1),
                effective_to=None,
                membership_state="included",
                evidence_hash=digest,
                raw_payload_hash=hashlib.sha256((digest + "raw").encode()).hexdigest(),
                fact_hash=hashlib.sha256((digest + "fact").encode()).hexdigest(),
                created_at=datetime(2021, 1, 1),
            )
        )
        await session.commit()

        for offset in range(0, len(dates), 20):
            page = [
                (
                    trade_date,
                    _values(
                        adjusted_close=2.0 + index * 0.01,
                        observed_at=source_seen,
                    ),
                )
                for index, trade_date in enumerate(dates[offset : offset + 20], offset)
            ]
            await persist_etf_price_history_page(session, etf_code=code, rows=page)
        await session.commit()

        initial = await load_point_in_time_ranking_inputs(
            session,
            replay_date=replay_date,
            decision_cutoff=initial_cutoff,
            max_source_rows=61,
        )
        await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[
                (
                    replay_date,
                    _values(adjusted_close=9.0, observed_at=revised_seen),
                )
            ],
        )
        await session.commit()
        later = await load_point_in_time_ranking_inputs(
            session,
            replay_date=replay_date,
            decision_cutoff=later_cutoff,
            max_source_rows=61,
        )

    assert [item.asset_code for item in initial.eligible_inputs] == [code]
    assert [item.asset_code for item in later.eligible_inputs] == [code]
    assert len(initial.eligible_inputs[0].revision_hashes) == 61
    assert initial.eligible_inputs[0].revision_hashes[-1] != later.eligible_inputs[0].revision_hashes[-1]
    assert initial.eligible_inputs[0].series_hash != later.eligible_inputs[0].series_hash


@pytest.mark.asyncio
async def test_revision_pit_lookup_uses_the_declared_index(app) -> None:
    code = "510805"
    trade_date = date(2026, 7, 10)
    observed_at = datetime(2026, 7, 10, 6, 0)
    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        await persist_etf_price_history_page(
            session,
            etf_code=code,
            rows=[(trade_date, _values(adjusted_close=1.2, observed_at=observed_at))],
        )
        await session.commit()
        plan = (
            await session.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT id FROM etf_adjusted_price_revisions "
                    "WHERE etf_code = :code AND trade_date <= :trade_date "
                    "AND first_seen_at <= :cutoff AND observed_at <= :cutoff"
                ),
                {
                    "code": code,
                    "trade_date": trade_date,
                    "cutoff": observed_at,
                },
            )
        ).all()

    assert any(
        "ix_etf_adjusted_price_revisions_pit_lookup" in " ".join(map(str, row))
        for row in plan
    )
