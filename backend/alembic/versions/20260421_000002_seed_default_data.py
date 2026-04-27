"""Seed default user, watchlist indices, and starter funds."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "20260421_000002"
down_revision = "20260421_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = datetime.utcnow()
    users = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("email", sa.String()),
        sa.column("recipient_email", sa.String()),
        sa.column("reminder_day", sa.Integer()),
        sa.column("reference_index_code", sa.String()),
        sa.column("base_monthly_amount", sa.Float()),
        sa.column("smtp_host", sa.String()),
        sa.column("smtp_port", sa.Integer()),
        sa.column("smtp_username", sa.String()),
        sa.column("smtp_password_ref", sa.String()),
        sa.column("smtp_from", sa.String()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    portfolios = sa.table(
        "portfolios",
        sa.column("id", sa.Integer()),
        sa.column("user_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )
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
    indices = sa.table(
        "indices",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("region", sa.String()),
        sa.column("is_watchlist", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )

    op.bulk_insert(
        users,
        [
            {
                "id": 1,
                "email": "owner@example.com",
                "recipient_email": "owner@example.com",
                "reminder_day": 1,
                "reference_index_code": "CSI300",
                "base_monthly_amount": 833.0,
                "smtp_host": None,
                "smtp_port": None,
                "smtp_username": None,
                "smtp_password_ref": "env:SMTP_PASSWORD",
                "smtp_from": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    op.bulk_insert(
        portfolios,
        [{"id": 1, "user_id": 1, "name": "Default Portfolio", "is_default": True, "created_at": now}],
    )
    op.bulk_insert(
        indices,
        [
            {"code": "CSI300", "name": "CSI 300", "region": "CN", "is_watchlist": True, "created_at": now},
            {"code": "CSI500", "name": "CSI 500", "region": "CN", "is_watchlist": True, "created_at": now},
            {"code": "CSI800", "name": "CSI 800", "region": "CN", "is_watchlist": True, "created_at": now},
            {"code": "CHINEXT", "name": "ChiNext", "region": "CN", "is_watchlist": True, "created_at": now},
            {"code": "SP500", "name": "S&P 500", "region": "US", "is_watchlist": True, "created_at": now},
            {"code": "NDX100", "name": "Nasdaq 100", "region": "US", "is_watchlist": True, "created_at": now},
        ],
    )
    op.bulk_insert(
        funds,
        [
            {
                "code": "007339",
                "name": "E Fund CSI 300",
                "category": "equity",
                "tracking_index_code": "CSI300",
                "target_allocation": 0.4,
                "is_watchlist": True,
                "created_at": now,
            },
            {
                "code": "001052",
                "name": "Huaxia SP500",
                "category": "equity",
                "tracking_index_code": "SP500",
                "target_allocation": 0.3,
                "is_watchlist": True,
                "created_at": now,
            },
            {
                "code": "270042",
                "name": "GF Nasdaq 100",
                "category": "equity",
                "tracking_index_code": "NDX100",
                "target_allocation": 0.2,
                "is_watchlist": True,
                "created_at": now,
            },
            {
                "code": "000198",
                "name": "Tianhong YEB",
                "category": "money_market",
                "tracking_index_code": None,
                "target_allocation": 0.1,
                "is_watchlist": True,
                "created_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM funds WHERE code IN ('007339', '001052', '270042', '000198')")
    op.execute("DELETE FROM indices WHERE code IN ('CSI300', 'CSI500', 'CSI800', 'CHINEXT', 'SP500', 'NDX100')")
    op.execute("DELETE FROM portfolios WHERE id = 1")
    op.execute("DELETE FROM users WHERE id = 1")
