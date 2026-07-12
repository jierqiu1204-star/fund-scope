from __future__ import annotations

from datetime import date

import pytest

from app.models.entities import EtfUniverseMembership, TradableEtf
from app.services.short_research.universe import build_point_in_time_universe_snapshot


@pytest.mark.asyncio
async def test_point_in_time_universe_keeps_later_inactive_etfs_in_historical_snapshot(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="510300",
                    name="沪深300ETF",
                    exchange="SH",
                    theme_tags_json=["宽基"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                ),
                TradableEtf(
                    code="159915",
                    name="创业板ETF",
                    exchange="SZ",
                    theme_tags_json=["成长"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                ),
            ]
        )
        session.add_all(
            [
                EtfUniverseMembership(
                    etf_code="510300",
                    effective_from=date(2026, 1, 1),
                    effective_to=date(2026, 1, 3),
                    source="fixture",
                    tracked_underlying_id="CSI300",
                ),
                EtfUniverseMembership(
                    etf_code="159915",
                    effective_from=date(2026, 1, 4),
                    source="fixture",
                    tracked_underlying_id="CHINEXT",
                ),
            ]
        )
        await session.commit()

        historical = await build_point_in_time_universe_snapshot(session, as_of_date=date(2026, 1, 2))
        current = await build_point_in_time_universe_snapshot(session, as_of_date=date(2026, 1, 5))

    assert [member["asset_code"] for member in historical.members] == ["510300"]
    assert historical.members[0]["tracked_underlying_id"] == "CSI300"
    assert [member["asset_code"] for member in current.members] == ["159915"]
    assert historical.universe_snapshot_hash != current.universe_snapshot_hash
