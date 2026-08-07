"""Point-in-time adapters for the dual-universe V2 formula engine.

The adapters are deliberately read-only.  They turn already persisted facts
into the pure V2 input contract and reject raw/audit-only price sources before
they reach the formula engine.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    PRICE_BASIS,
    V2AdjustedBar,
    V2AssetInput,
    V2PITMembership,
)

APPROVED_ASHARE_PROVIDERS = frozenset({"akshare", "eastmoney", "tickflow"})
FORBIDDEN_RAW_PROVIDERS = frozenset({"sina", "efinance", "tencent"})


@dataclass(frozen=True)
class AshareReadinessMetric:
    layer: str
    eligible_count: int
    total_count: int
    excluded_count: int
    unavailable_reason: str | None

    @property
    def coverage(self) -> float:
        return (
            min(1.0, max(0.0, self.eligible_count / self.total_count)) if self.total_count else 0.0
        )


def adjusted_fact_exclusion_reason(
    *,
    provider: str,
    price_basis: str,
    adjustment_version: str,
    received_at: datetime | None,
    source_cutoff: datetime,
    decision_eligible: bool,
    values: tuple[object, ...],
    trade_date: date | None = None,
    historical_research_only: bool = False,
) -> str | None:
    """Return a stable fail-closed reason for an A-share adjusted bar."""

    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider in FORBIDDEN_RAW_PROVIDERS:
        return "raw_or_audit_only_provider"
    if normalized_provider not in APPROVED_ASHARE_PROVIDERS:
        return "unsupported_adjusted_provider"
    if price_basis != PRICE_BASIS:
        return "incompatible_research_price_basis"
    if not isinstance(adjustment_version, str) or not adjustment_version.strip():
        return "missing_adjustment_identity"
    if received_at is None or historical_research_only:
        return "historical_research_only"
    if isinstance(received_at, str):
        try:
            received_at = datetime.fromisoformat(received_at)
        except ValueError:
            return "invalid_received_at"
    if not isinstance(received_at, datetime):
        return "invalid_received_at"
    if isinstance(trade_date, str):
        try:
            trade_date = date.fromisoformat(trade_date[:10])
        except ValueError:
            return "invalid_trade_date"
    if trade_date is not None and received_at.date() < trade_date:
        return "adjusted_bar_received_before_trade_date"
    if received_at > source_cutoff:
        return "adjusted_bar_received_after_cutoff"
    if not decision_eligible:
        return "adjusted_bar_not_decision_eligible"
    price_values = values[:4]
    non_price_values = values[4:]
    parsed_prices = tuple(
        float(value)
        for value in price_values
        if isinstance(value, int | float) and not isinstance(value, bool)
    )
    if any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0
        for value in price_values
    ) or any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) < 0
        for value in non_price_values
    ):
        return "non_finite_adjusted_input"
    if (
        len(parsed_prices) != 4
        or parsed_prices[1] < max(parsed_prices[0], parsed_prices[3])
        or parsed_prices[2] > min(parsed_prices[0], parsed_prices[3])
        or parsed_prices[2] > parsed_prices[1]
    ):
        return "invalid_adjusted_ohlcv"
    return None


def ashare_membership_to_v2(row: dict[str, Any]) -> V2PITMembership:
    """Convert one persisted PIT theme fact without consulting current taxonomy."""

    effective_from = row["effective_from"]
    if isinstance(effective_from, str):
        effective_from = date.fromisoformat(effective_from[:10])
    effective_to = row.get("effective_to")
    if isinstance(effective_to, str):
        effective_to = date.fromisoformat(effective_to[:10])
    observed_at = row["received_at"]
    if isinstance(observed_at, str):
        observed_at = datetime.fromisoformat(observed_at)
    return V2PITMembership(
        group_id=str(row["group_id"]),
        effective_from=effective_from,
        effective_to=effective_to,
        observed_at=observed_at,
        mapping_kind=str(row["mapping_kind"]),
        taxonomy_version=str(row["taxonomy_version"]),
        theme=row.get("theme"),
        sector=row.get("sector"),
        tracked_index=row.get("tracked_index"),
        clone_group=row.get("clone_group"),
        issuer=row.get("issuer"),
        fact_hash=str(row["fact_hash"]),
    )


def ashare_price_fact_to_bar(row: dict[str, Any]) -> tuple[V2AdjustedBar | None, str | None]:
    reason = adjusted_fact_exclusion_reason(
        provider=str(row.get("provider") or ""),
        price_basis=str(row.get("price_basis") or ""),
        adjustment_version=str(row.get("adjustment_version") or ""),
        received_at=row.get("received_at"),
        source_cutoff=row["source_cutoff"],
        decision_eligible=bool(row.get("decision_eligible")),
        values=(
            row.get("adjusted_open"),
            row.get("adjusted_high"),
            row.get("adjusted_low"),
            row.get("adjusted_close"),
            row.get("volume"),
            row.get("amount"),
            row.get("turnover"),
        ),
        trade_date=row.get("trade_date"),
        historical_research_only=bool(row.get("historical_research_only")),
    )
    if reason:
        return None, reason
    return (
        V2AdjustedBar(
            trade_date=row["trade_date"],
            adjusted_open=float(row["adjusted_open"]),
            adjusted_high=float(row["adjusted_high"]),
            adjusted_low=float(row["adjusted_low"]),
            adjusted_close=float(row["adjusted_close"]),
            volume=float(row["volume"]),
            amount=float(row["amount"]),
            turnover=float(row["turnover"]),
            observed_at=row["received_at"],
            provider=str(row["provider"]),
            adjustment_version=str(row["adjustment_version"]),
            decision_eligible=True,
            price_basis=PRICE_BASIS,
            revision_id=str(row["revision_id"]),
        ),
        None,
    )


async def read_ashare_pit_membership(
    session: AsyncSession,
    *,
    asset_code: str,
    signal_date: date,
    source_cutoff: datetime,
) -> V2PITMembership | None:
    """Read the latest factual membership received by the historical cutoff."""

    result = await session.execute(
        text(
            """
            SELECT group_id, theme, sector, effective_from, effective_to,
                   received_at, taxonomy_version, mapping_kind, tracked_index,
                   clone_group, issuer, fact_hash
            FROM ashare_theme_membership_facts
            WHERE asset_code = :asset_code
              AND effective_from <= :signal_date
              AND (effective_to IS NULL OR effective_to >= :signal_date)
              AND received_at <= :source_cutoff
            ORDER BY received_at DESC, effective_from DESC, fact_hash DESC
            LIMIT 1
            """
        ),
        {
            "asset_code": asset_code,
            "signal_date": signal_date,
            "source_cutoff": source_cutoff,
        },
    )
    row = result.mappings().first()
    return ashare_membership_to_v2(dict(row)) if row else None


async def read_ashare_adjusted_bars(
    session: AsyncSession,
    *,
    asset_code: str,
    signal_date: date,
    source_cutoff: datetime,
    limit: int = 180,
) -> tuple[tuple[V2AdjustedBar, ...], tuple[str, ...]]:
    """Read a bounded, cutoff-visible adjusted history and its exclusions."""

    if not 1 <= limit <= 300:
        raise ValueError("A-share history limit must be between 1 and 300")
    result = await session.execute(
        text(
            """
            SELECT trade_date, adjusted_open, adjusted_high, adjusted_low,
                   adjusted_close, volume, amount, turnover, price_basis,
                   provider, adjustment_version, revision_id, received_at,
                   decision_eligible, historical_research_only
            FROM (
                SELECT facts.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY trade_date
                           ORDER BY received_at DESC, revision_id DESC, id DESC
                       ) AS revision_rank
                FROM ashare_adjusted_price_facts AS facts
                WHERE asset_code = :asset_code
                  AND trade_date <= :signal_date
                  AND received_at IS NOT NULL
                  AND received_at <= :source_cutoff
                  AND decision_eligible = :eligible
                  AND historical_research_only = :historical_only
                  AND price_basis = :price_basis
                  AND LOWER(provider) IN ('akshare', 'eastmoney', 'tickflow')
                  AND adjustment_version IS NOT NULL
                  AND TRIM(adjustment_version) <> ''
                  AND adjusted_open > 0
                  AND adjusted_open < :finite_max
                  AND adjusted_high > 0
                  AND adjusted_high < :finite_max
                  AND adjusted_low > 0
                  AND adjusted_low < :finite_max
                  AND adjusted_close > 0
                  AND adjusted_close < :finite_max
                  AND adjusted_high >= adjusted_open
                  AND adjusted_high >= adjusted_close
                  AND adjusted_low <= adjusted_open
                  AND adjusted_low <= adjusted_close
                  AND adjusted_low <= adjusted_high
                  AND volume >= 0
                  AND volume < :finite_max
                  AND amount >= 0
                  AND amount < :finite_max
                  AND turnover >= 0
                  AND turnover < :finite_max
                  AND DATE(received_at) >= trade_date
            ) qualified_facts
            WHERE revision_rank = 1
            ORDER BY trade_date DESC
            LIMIT :limit
            """
        ),
        {
            "asset_code": asset_code,
            "signal_date": signal_date,
            "source_cutoff": source_cutoff,
            "eligible": True,
            "historical_only": False,
            "price_basis": PRICE_BASIS,
            "finite_max": 1.7976931348623157e308,
            "limit": limit,
        },
    )
    bars: list[V2AdjustedBar] = []
    exclusions: list[str] = []
    for raw_row in reversed(result.mappings().all()):
        row = dict(raw_row)
        if isinstance(row.get("trade_date"), str):
            row["trade_date"] = date.fromisoformat(row["trade_date"][:10])
        if isinstance(row.get("received_at"), str):
            row["received_at"] = datetime.fromisoformat(row["received_at"])
        row["source_cutoff"] = source_cutoff
        bar, reason = ashare_price_fact_to_bar(row)
        if bar is not None:
            bars.append(bar)
        elif reason:
            exclusions.append(reason)
    return tuple(bars), tuple(sorted(set(exclusions)))


def _asset_code_pages(
    assets: tuple[tuple[str, str], ...],
    *,
    page_size: int,
) -> Iterable[tuple[tuple[str, str], ...]]:
    for start in range(0, len(assets), page_size):
        yield assets[start : start + page_size]


def _asset_code_bindings(codes: tuple[str, ...], *, prefix: str) -> tuple[str, dict[str, str]]:
    bindings = tuple(f":{prefix}_{index}" for index in range(len(codes)))
    params = {f"{prefix}_{index}": code for index, code in enumerate(codes)}
    return ", ".join(bindings), params


async def read_ashare_asset_inputs(
    session: AsyncSession,
    *,
    assets: Iterable[tuple[str, str]],
    signal_date: date,
    source_cutoff: datetime,
    history_limit: int = 180,
    page_size: int = 100,
) -> tuple[V2AssetInput, ...]:
    """Build ordered A-share inputs with three bounded queries per code page.

    The page size is intentionally capped below SQLite's default bind-variable
    limit. Each page materializes only that page's universe, membership and
    adjusted bars, so a large authoritative pool never creates one query per
    asset or an unbounded intermediate result.
    """

    if not 1 <= page_size <= 100:
        raise ValueError("A-share input page_size must be between 1 and 100")
    if not 1 <= history_limit <= 300:
        raise ValueError("A-share history limit must be between 1 and 300")

    requested = tuple((str(code).strip(), str(name)) for code, name in assets)
    if any(not code for code, _ in requested):
        raise ValueError("A-share asset codes are required")
    codes = tuple(code for code, _ in requested)
    if len(set(codes)) != len(codes):
        raise ValueError("A-share asset codes must be unique")

    signal_end = datetime.combine(signal_date, datetime.max.time())
    result_inputs: list[V2AssetInput] = []
    for page in _asset_code_pages(requested, page_size=page_size):
        page_codes = tuple(code for code, _ in page)
        code_sql, code_params = _asset_code_bindings(page_codes, prefix="asset_code")
        base_params: dict[str, Any] = {
            "signal_end": signal_end,
            "signal_date": signal_date,
            "source_cutoff": source_cutoff,
            "eligible": True,
            "historical_only": False,
            "price_basis": PRICE_BASIS,
            "finite_max": 1.7976931348623157e308,
            "limit": history_limit,
            **code_params,
        }

        universe_result = await session.execute(
            text(
                f"""
                SELECT asset_code, asset_name, listing_state, effective_at,
                       received_at, provider, source_cutoff, exclusion_reason
                FROM (
                    SELECT snapshots.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code
                               ORDER BY effective_at DESC, received_at DESC,
                                        fact_hash DESC
                           ) AS snapshot_rank
                    FROM ashare_research_universe_snapshots AS snapshots
                    WHERE asset_code IN ({code_sql})
                      AND effective_at <= :signal_end
                      AND received_at <= :source_cutoff
                      AND source_cutoff <= :source_cutoff
                ) latest_universe
                WHERE snapshot_rank = 1
                """
            ),
            base_params,
        )
        universe_by_code = {
            str(row["asset_code"]): dict(row) for row in universe_result.mappings().all()
        }

        membership_result = await session.execute(
            text(
                f"""
                SELECT asset_code, group_id, theme, sector, effective_from, effective_to,
                       received_at, taxonomy_version, mapping_kind, tracked_index,
                       clone_group, issuer, fact_hash
                FROM (
                    SELECT memberships.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code
                               ORDER BY received_at DESC, effective_from DESC,
                                        fact_hash DESC
                           ) AS membership_rank
                    FROM ashare_theme_membership_facts AS memberships
                    WHERE asset_code IN ({code_sql})
                      AND effective_from <= :signal_date
                      AND (effective_to IS NULL OR effective_to >= :signal_date)
                      AND received_at <= :source_cutoff
                ) latest_membership
                WHERE membership_rank = 1
                """
            ),
            base_params,
        )
        membership_by_code = {
            str(row["asset_code"]): ashare_membership_to_v2(dict(row))
            for row in membership_result.mappings().all()
        }

        bars_result = await session.execute(
            text(
                f"""
                SELECT asset_code, trade_date, adjusted_open, adjusted_high,
                       adjusted_low, adjusted_close, volume, amount, turnover,
                       price_basis, provider, adjustment_version, revision_id,
                       received_at, decision_eligible, historical_research_only
                FROM (
                    SELECT qualified_facts.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code
                               ORDER BY trade_date DESC
                           ) AS history_rank
                    FROM (
                        SELECT ranked_revisions.*
                        FROM (
                            SELECT facts.*,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY asset_code, trade_date
                                       ORDER BY received_at DESC, revision_id DESC, id DESC
                                   ) AS revision_rank
                            FROM ashare_adjusted_price_facts AS facts
                            WHERE asset_code IN ({code_sql})
                              AND trade_date <= :signal_date
                              AND received_at IS NOT NULL
                              AND received_at <= :source_cutoff
                              AND decision_eligible = :eligible
                              AND historical_research_only = :historical_only
                              AND price_basis = :price_basis
                              AND LOWER(provider) IN ('akshare', 'eastmoney', 'tickflow')
                              AND adjustment_version IS NOT NULL
                              AND TRIM(adjustment_version) <> ''
                              AND adjusted_open > 0
                              AND adjusted_open < :finite_max
                              AND adjusted_high > 0
                              AND adjusted_high < :finite_max
                              AND adjusted_low > 0
                              AND adjusted_low < :finite_max
                              AND adjusted_close > 0
                              AND adjusted_close < :finite_max
                              AND adjusted_high >= adjusted_open
                              AND adjusted_high >= adjusted_close
                              AND adjusted_low <= adjusted_open
                              AND adjusted_low <= adjusted_close
                              AND adjusted_low <= adjusted_high
                              AND volume >= 0
                              AND volume < :finite_max
                              AND amount >= 0
                              AND amount < :finite_max
                              AND turnover >= 0
                              AND turnover < :finite_max
                              AND DATE(received_at) >= trade_date
                        ) ranked_revisions
                        WHERE revision_rank = 1
                    ) qualified_facts
                ) limited_facts
                WHERE history_rank <= :limit
                ORDER BY asset_code, trade_date DESC
                """
            ),
            base_params,
        )
        bars_by_code: dict[str, list[V2AdjustedBar]] = {code: [] for code in page_codes}
        exclusions_by_code: dict[str, set[str]] = {code: set() for code in page_codes}
        for raw_row in bars_result.mappings().all():
            row = dict(raw_row)
            code = str(row["asset_code"])
            if isinstance(row.get("trade_date"), str):
                row["trade_date"] = date.fromisoformat(row["trade_date"][:10])
            if isinstance(row.get("received_at"), str):
                row["received_at"] = datetime.fromisoformat(row["received_at"])
            row["source_cutoff"] = source_cutoff
            bar, reason = ashare_price_fact_to_bar(row)
            if bar is not None:
                bars_by_code.setdefault(code, []).append(bar)
            elif reason:
                exclusions_by_code.setdefault(code, set()).add(reason)

        for code, asset_name in page:
            universe_row = universe_by_code.get(code)
            input_reasons: list[str] = []
            resolved_name = asset_name
            if universe_row is None:
                input_reasons.append("missing_pit_universe")
            else:
                resolved_name = str(universe_row.get("asset_name") or asset_name)
                if str(universe_row.get("listing_state") or "").lower() != "listed":
                    input_reasons.append("universe_listing_excluded")
                if universe_row.get("exclusion_reason"):
                    input_reasons.append(str(universe_row["exclusion_reason"]))
            input_reasons.extend(exclusions_by_code.get(code, ()))
            result_inputs.append(
                V2AssetInput(
                    universe="ashare",
                    asset_code=code,
                    asset_name=resolved_name,
                    signal_date=signal_date,
                    source_cutoff=source_cutoff,
                    bars=tuple(reversed(bars_by_code.get(code, ()))),
                    membership=membership_by_code.get(code),
                    input_unavailable_reasons=tuple(sorted(set(input_reasons))),
                )
            )
    return tuple(result_inputs)


async def read_ashare_asset_input(
    session: AsyncSession,
    *,
    asset_code: str,
    asset_name: str,
    signal_date: date,
    source_cutoff: datetime,
    history_limit: int = 180,
) -> V2AssetInput:
    """Build one A-share V2 input using the bounded batch reader."""

    inputs = await read_ashare_asset_inputs(
        session,
        assets=((asset_code, asset_name),),
        signal_date=signal_date,
        source_cutoff=source_cutoff,
        history_limit=history_limit,
        page_size=1,
    )
    return inputs[0]


__all__ = [
    "APPROVED_ASHARE_PROVIDERS",
    "AshareReadinessMetric",
    "ashare_membership_to_v2",
    "ashare_price_fact_to_bar",
    "adjusted_fact_exclusion_reason",
    "read_ashare_adjusted_bars",
    "read_ashare_asset_inputs",
    "read_ashare_pit_membership",
    "read_ashare_asset_input",
]
