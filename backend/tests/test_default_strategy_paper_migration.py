from __future__ import annotations

from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa

from app.defaults.funds import DEFAULT_RESEARCH_FUND_CODES

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260604_000007_default_strategy_paper.py"
)
spec = spec_from_file_location("default_strategy_paper_migration", MIGRATION_PATH)
assert spec is not None
assert spec.loader is not None
default_strategy_paper = module_from_spec(spec)
spec.loader.exec_module(default_strategy_paper)


def test_default_strategy_config_uses_research_pool_and_alipay_profile() -> None:
    config = default_strategy_paper._default_config()

    assert config["asset_codes"] == list(DEFAULT_RESEARCH_FUND_CODES)
    assert config["platform_profile"] == "alipay"
    assert config["lookback_days"] == 60
    assert config["top_n"] == 3
    assert config["initial_cash"] == 100000.0


def test_default_paper_start_date_uses_available_history_window() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    fund_nav_history = sa.Table(
        "fund_nav_history",
        metadata,
        sa.Column("fund_code", sa.String()),
        sa.Column("nav_date", sa.Date()),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            fund_nav_history.insert(),
            [
                {"fund_code": DEFAULT_RESEARCH_FUND_CODES[0], "nav_date": date(2026, 1, 1)},
                {"fund_code": DEFAULT_RESEARCH_FUND_CODES[0], "nav_date": date(2026, 4, 1)},
            ],
        )

        started_at = default_strategy_paper._default_started_at(connection)

    assert started_at == date(2026, 3, 9)
