from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfIntradayQuote,
    EtfIntradayQuoteEvidenceRef,
    utcnow,
)
from app.services.intraday_etf.exchange_calendar import ASIA_SHANGHAI
from app.services.intraday_etf.service import quotes_at_or_before_cutoff

PUBLISHED_SNAPSHOT_OWNER = "published_snapshot"
PIT_CAPTURE_SOURCE_OWNER = "pit_capture_source"
ACTIONABLE_RANKING_INPUT_PURPOSE = "actionable_ranking_input"
EVIDENCE_STATE_PROTECTED = "protected"
EVIDENCE_STATE_UNAVAILABLE = "unavailable"
UNAVAILABLE_NO_QUOTE_AT_CUTOFF = "no_quote_at_cutoff"
UNAVAILABLE_DECISION_CUTOFF_MISSING = "market_decision_cutoff_missing"


def _local_naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(ASIA_SHANGHAI).replace(tzinfo=None)


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return {"non_finite_float": repr(value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def intraday_quote_evidence_hash(quote: EtfIntradayQuote) -> str:
    payload = {
        "asset_code": quote.etf_code,
        "quote_time": quote.quote_time,
        "trade_date": quote.trade_date,
        "created_at": quote.created_at,
        "latest_price": quote.latest_price,
        "change_percent": quote.change_percent,
        "volume": quote.volume,
        "turnover": quote.turnover,
        "bid_price": quote.bid_price,
        "ask_price": quote.ask_price,
        "iopv": quote.iopv,
        "premium_discount_pct": quote.premium_discount_pct,
        "source": quote.source,
        "freshness_status": quote.freshness_status,
        "raw_json": quote.raw_json,
    }
    encoded = json.dumps(
        _canonical_json_value(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def seal_intraday_quote_evidence(
    session: AsyncSession,
    *,
    owner_kind: str,
    owner_id: int,
    asset_codes: Sequence[str],
    evidence_purpose: str = ACTIONABLE_RANKING_INPUT_PURPOSE,
    trade_date: date,
    decision_cutoff: datetime | None,
    receipt_cutoff: datetime | None,
) -> dict[str, Any]:
    """Record exact quote inputs, or a factual unavailable row, without fallback data."""

    codes = sorted({code.strip() for code in asset_codes if code and code.strip()})
    if not codes:
        return {
            "owner_kind": owner_kind,
            "owner_id": owner_id,
            "expected_count": 0,
            "protected_count": 0,
            "unavailable_count": 0,
            "created_count": 0,
            "complete": True,
        }
    existing_rows = (
        await session.scalars(
            select(EtfIntradayQuoteEvidenceRef).where(
                EtfIntradayQuoteEvidenceRef.owner_kind == owner_kind,
                EtfIntradayQuoteEvidenceRef.owner_id == owner_id,
                EtfIntradayQuoteEvidenceRef.evidence_purpose == evidence_purpose,
                EtfIntradayQuoteEvidenceRef.asset_code.in_(codes),
            )
        )
    ).all()
    existing_by_code = {row.asset_code: row for row in existing_rows}
    missing_codes = [code for code in codes if code not in existing_by_code]
    local_decision_cutoff = _local_naive(decision_cutoff)
    local_receipt_cutoff = _local_naive(receipt_cutoff)
    selected_quotes = (
        await quotes_at_or_before_cutoff(
            session,
            missing_codes,
            trade_date=trade_date,
            decision_cutoff=local_decision_cutoff,
        )
        if missing_codes and local_decision_cutoff is not None
        else {}
    )
    created_rows: list[EtfIntradayQuoteEvidenceRef] = []
    for code in missing_codes:
        quote = selected_quotes.get(code)
        if quote is None:
            reason = (
                UNAVAILABLE_NO_QUOTE_AT_CUTOFF
                if local_decision_cutoff is not None
                else UNAVAILABLE_DECISION_CUTOFF_MISSING
            )
            created_rows.append(
                EtfIntradayQuoteEvidenceRef(
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    asset_code=code,
                    evidence_purpose=evidence_purpose,
                    evidence_state=EVIDENCE_STATE_UNAVAILABLE,
                    decision_cutoff=local_decision_cutoff,
                    receipt_cutoff=local_receipt_cutoff,
                    unavailable_reason=reason,
                    protected_at=utcnow(),
                )
            )
            continue
        created_rows.append(
            EtfIntradayQuoteEvidenceRef(
                quote_id=quote.id,
                owner_kind=owner_kind,
                owner_id=owner_id,
                asset_code=code,
                evidence_purpose=evidence_purpose,
                evidence_state=EVIDENCE_STATE_PROTECTED,
                quote_hash=intraday_quote_evidence_hash(quote),
                decision_cutoff=local_decision_cutoff,
                receipt_cutoff=local_receipt_cutoff,
                protected_at=utcnow(),
            )
        )
    if created_rows:
        try:
            async with session.begin_nested():
                session.add_all(created_rows)
                await session.flush()
        except IntegrityError:
            # A concurrent scheduler/admin invocation may have sealed the same
            # owner after our initial read.  Reload below; never overwrite it.
            pass
    all_rows = (
        await session.scalars(
            select(EtfIntradayQuoteEvidenceRef).where(
                EtfIntradayQuoteEvidenceRef.owner_kind == owner_kind,
                EtfIntradayQuoteEvidenceRef.owner_id == owner_id,
                EtfIntradayQuoteEvidenceRef.evidence_purpose == evidence_purpose,
                EtfIntradayQuoteEvidenceRef.asset_code.in_(codes),
            )
        )
    ).all()
    protected_count = sum(
        row.evidence_state == EVIDENCE_STATE_PROTECTED for row in all_rows
    )
    unavailable_count = sum(
        row.evidence_state == EVIDENCE_STATE_UNAVAILABLE for row in all_rows
    )
    return {
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "expected_count": len(codes),
        "protected_count": protected_count,
        "unavailable_count": unavailable_count,
        "created_count": max(0, len(all_rows) - len(existing_rows)),
        "complete": len(all_rows) == len(codes),
    }


def evidence_row_is_cleanup_eligible(row: EtfIntradayQuoteEvidenceRef) -> bool:
    return row.evidence_state == EVIDENCE_STATE_PROTECTED or (
        row.evidence_state == EVIDENCE_STATE_UNAVAILABLE
        and row.unavailable_reason == UNAVAILABLE_NO_QUOTE_AT_CUTOFF
    )
