from __future__ import annotations

from datetime import date

import pytest

from app.services.short_research.service import run_signal_generation


@pytest.mark.asyncio
async def test_new_signal_run_persists_explicit_code_scope(app) -> None:
    async with app.state.db.session() as session:
        run = await run_signal_generation(
            session,
            as_of_date=date(2026, 1, 2),
            asset_type="etf",
            theme="人工智能",
            codes=["510300"],
        )
        assert session.in_transaction() is False

    assert run.scope_kind == "codes"
