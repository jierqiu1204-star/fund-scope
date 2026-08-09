from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.entities import (
    EtfIntradayQuote,
    EtfIntradayQuoteEvidenceRef,
    IntradayQuoteEvidenceImmutableError,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
    authorize_snapshot_publication,
)
from app.services.workflows.etf_intraday_retention import (
    intraday_etf_retention_job,
)


def _etf(code: str) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"ETF{code}",
        exchange="SH",
        theme_tags_json=["test"],
        trading_rule_label="T+1",
        asset_class="equity",
        is_short_term_eligible=True,
        is_watchlist=True,
    )


@pytest.mark.asyncio
async def test_retention_backfills_publication_evidence_before_bounded_delete(app) -> None:
    protected_quote_id: int
    removable_quote_id: int
    async with app.state.db.session() as session:
        session.add_all([_etf("510101"), _etf("510102")])
        await session.flush()
        protected_quote = EtfIntradayQuote(
            etf_code="510101",
            quote_time=datetime(2026, 6, 10, 14, 50),
            trade_date=date(2026, 6, 10),
            latest_price=1.0,
            created_at=datetime(2026, 6, 10, 6, 50),
        )
        removable_quote = EtfIntradayQuote(
            etf_code="510102",
            quote_time=datetime(2026, 6, 10, 14, 50),
            trade_date=date(2026, 6, 10),
            latest_price=2.0,
            created_at=datetime(2026, 6, 10, 6, 50),
        )
        session.add_all(
            [
                protected_quote,
                removable_quote,
                EtfIntradayQuote(
                    etf_code="510101",
                    quote_time=datetime(2026, 6, 11, 14, 50),
                    trade_date=date(2026, 6, 11),
                    latest_price=1.1,
                ),
                EtfIntradayQuote(
                    etf_code="510101",
                    quote_time=datetime(2026, 6, 12, 14, 50),
                    trade_date=date(2026, 6, 12),
                    latest_price=1.2,
                ),
            ]
        )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 6, 10),
            as_of_trade_date=date(2026, 6, 10),
            config_json={
                "market_decision_cutoff": datetime(2026, 6, 10, 15, 0).isoformat()
            },
            summary_json={},
            data_cutoff=datetime(2026, 6, 10, 15, 1),
        )
        session.add(run)
        await session.flush()
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type="etf",
                asset_code="510101",
                rank=1,
                total_score=80.0,
                conclusion="观察",
            )
        )
        await session.flush()
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = datetime(2026, 6, 10, 15, 2)
            await session.flush()
        await session.commit()
        protected_quote_id = protected_quote.id
        removable_quote_id = removable_quote.id

        result = await intraday_etf_retention_job(
            session,
            retention_trading_days=2,
            batch_size=10,
        )
        remaining_ids = set(await session.scalars(select(EtfIntradayQuote.id)))
        evidence = await session.scalar(
            select(EtfIntradayQuoteEvidenceRef).where(
                EtfIntradayQuoteEvidenceRef.owner_kind == "published_snapshot",
                EtfIntradayQuoteEvidenceRef.owner_id == run.id,
                EtfIntradayQuoteEvidenceRef.asset_code == "510101",
            )
        )

    assert result["deleted_rows"] == 1
    assert result["evidence_backfill"][0]["protected_count"] == 1
    assert protected_quote_id in remaining_ids
    assert removable_quote_id not in remaining_ids
    assert evidence is not None
    assert evidence.quote_id == protected_quote_id
    assert evidence.evidence_state == "protected"

    async with app.state.db.session() as session:
        persisted = await session.get(EtfIntradayQuoteEvidenceRef, evidence.id)
        assert persisted is not None
        persisted.evidence_purpose = "tampered"
        with pytest.raises(IntradayQuoteEvidenceImmutableError):
            await session.flush()
