"""Shared evidence-safe retention policy for ETF intraday quote details."""

from __future__ import annotations

# Full provider payloads and normalized minute details remain queryable for
# recent operational diagnosis. Older unprotected rows are represented by
# daily summaries, while decision-linked rows are exempt from age expiry.
INTRADAY_FULL_DETAIL_RETENTION_TRADING_DAYS = 10


__all__ = ["INTRADAY_FULL_DETAIL_RETENTION_TRADING_DAYS"]
