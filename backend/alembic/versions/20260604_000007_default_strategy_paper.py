"""Seed default strategy lab paper portfolio."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.defaults.funds import DEFAULT_RESEARCH_FUND_CODES

revision = "20260604_000007"
down_revision = "20260603_000006"
branch_labels = None
depends_on = None

DEFAULT_STRATEGY_NAME = "默认基金轮动策略"
DEFAULT_PAPER_NAME = "默认基金轮动模拟盘"
LOOKBACK_DAYS = 60
INITIAL_CASH = 100000.0


def _default_started_at(connection: sa.Connection) -> date:
    fund_nav_history = sa.table(
        "fund_nav_history",
        sa.column("fund_code", sa.String()),
        sa.column("nav_date", sa.Date()),
    )
    earliest_nav_date, latest_nav_date = connection.execute(
        sa.select(
            sa.func.min(fund_nav_history.c.nav_date),
            sa.func.max(fund_nav_history.c.nav_date),
        ).where(fund_nav_history.c.fund_code.in_(DEFAULT_RESEARCH_FUND_CODES))
    ).one()
    today = date.today()
    if earliest_nav_date is None or latest_nav_date is None:
        return today

    latest_usable = min(latest_nav_date, today)
    candidate = earliest_nav_date + timedelta(days=LOOKBACK_DAYS + 7)
    return min(candidate, latest_usable)


def _default_config() -> dict[str, Any]:
    return {
        "asset_codes": list(DEFAULT_RESEARCH_FUND_CODES),
        "platform_profile": "alipay",
        "lookback_days": LOOKBACK_DAYS,
        "rebalance_frequency": "monthly",
        "top_n": 3,
        "max_pe_percentile": 80,
        "initial_cash": INITIAL_CASH,
        "fee_rate": 0.001,
    }


def upgrade() -> None:
    now = datetime.utcnow()
    connection = op.get_bind()
    strategy_definitions = sa.table(
        "strategy_definitions",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("strategy_type", sa.String()),
        sa.column("asset_type", sa.String()),
        sa.column("status", sa.String()),
        sa.column("config_json", sa.JSON()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    paper_portfolios = sa.table(
        "paper_portfolios",
        sa.column("id", sa.Integer()),
        sa.column("strategy_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("started_at", sa.Date()),
        sa.column("cash", sa.Float()),
        sa.column("latest_equity", sa.Float()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )

    strategy_id = connection.scalar(
        sa.select(strategy_definitions.c.id).where(strategy_definitions.c.name == DEFAULT_STRATEGY_NAME)
    )
    if strategy_id is None:
        connection.execute(
            strategy_definitions.insert().values(
                name=DEFAULT_STRATEGY_NAME,
                strategy_type="momentum_rotation",
                asset_type="fund",
                status="active",
                config_json=_default_config(),
                created_at=now,
                updated_at=now,
            )
        )
        strategy_id = connection.scalar(
            sa.select(strategy_definitions.c.id).where(strategy_definitions.c.name == DEFAULT_STRATEGY_NAME)
        )

    paper_id = connection.scalar(
        sa.select(paper_portfolios.c.id).where(
            paper_portfolios.c.strategy_id == strategy_id,
            paper_portfolios.c.name == DEFAULT_PAPER_NAME,
        )
    )
    if paper_id is None:
        connection.execute(
            paper_portfolios.insert().values(
                strategy_id=strategy_id,
                name=DEFAULT_PAPER_NAME,
                status="active",
                started_at=_default_started_at(connection),
                cash=INITIAL_CASH,
                latest_equity=INITIAL_CASH,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    connection = op.get_bind()
    strategy_definitions = sa.table(
        "strategy_definitions",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
    )
    paper_portfolios = sa.table(
        "paper_portfolios",
        sa.column("strategy_id", sa.Integer()),
        sa.column("name", sa.String()),
    )
    strategy_id = connection.scalar(
        sa.select(strategy_definitions.c.id).where(strategy_definitions.c.name == DEFAULT_STRATEGY_NAME)
    )
    if strategy_id is not None:
        connection.execute(
            paper_portfolios.delete().where(
                paper_portfolios.c.strategy_id == strategy_id,
                paper_portfolios.c.name == DEFAULT_PAPER_NAME,
            )
        )
        connection.execute(strategy_definitions.delete().where(strategy_definitions.c.id == strategy_id))
