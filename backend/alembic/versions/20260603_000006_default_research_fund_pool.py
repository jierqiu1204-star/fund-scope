"""Expand default research fund pool."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from alembic import op
from app.defaults.funds import DEFAULT_RESEARCH_FUNDS

revision = "20260603_000006"
down_revision = "20260601_000005"
branch_labels = None
depends_on = None

LEGACY_SEED_ONLY_CODES = ("000198",)


def upgrade() -> None:
    now = datetime.utcnow()
    connection = op.get_bind()
    funds = sa.table(
        "funds",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("category", sa.String()),
        sa.column("tracking_index_code", sa.String()),
        sa.column("target_allocation", sa.Float()),
        sa.column("is_watchlist", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )

    for default_fund in DEFAULT_RESEARCH_FUNDS:
        exists = connection.scalar(sa.select(funds.c.code).where(funds.c.code == default_fund.code))
        values = {
            "name": default_fund.name,
            "category": default_fund.category,
            "tracking_index_code": default_fund.tracking_index_code,
            "target_allocation": default_fund.target_allocation,
            "is_watchlist": True,
        }
        if exists:
            connection.execute(
                funds.update().where(funds.c.code == default_fund.code).values(**values),
            )
            continue

        connection.execute(
            funds.insert().values(
                code=default_fund.code,
                created_at=now,
                **values,
            )
        )

    connection.execute(
        funds.update().where(funds.c.code.in_(LEGACY_SEED_ONLY_CODES)).values(is_watchlist=False)
    )


def downgrade() -> None:
    connection = op.get_bind()
    funds = sa.table(
        "funds",
        sa.column("code", sa.String()),
        sa.column("is_watchlist", sa.Boolean()),
    )
    connection.execute(
        funds.update().where(funds.c.code.in_(LEGACY_SEED_ONLY_CODES)).values(is_watchlist=True)
    )
    connection.execute(
        funds.delete().where(
            funds.c.code.in_(
                [fund.code for fund in DEFAULT_RESEARCH_FUNDS if fund.code not in {"001052", "270042"}]
            )
        )
    )
