from __future__ import annotations

from datetime import date, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.services.short_etf import data

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260811_000065_correct_tencent_etf_turnover.py"


def _migration():
    spec = spec_from_file_location("correct_tencent_etf_turnover", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tencent_turnover_correction_is_deterministic_and_versioned() -> None:
    migration = _migration()
    repair_time = datetime(2026, 8, 11, 0, 5)
    row = {
        "etf_code": "159001",
        "trade_date": date(2026, 8, 10),
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.0,
        "volume": 100.0,
        "turnover": 100.0,
        "pct_change": 0.0,
        "raw_price_basis": "unadjusted_close",
        "research_adjusted_value": 2.0,
        "research_price_basis": "total_return_adjusted",
        "data_provider": "tencent",
        "provider_version": migration.OLD_VERSION,
        "adjustment_version": migration.OLD_VERSION,
        "decision_eligible": True,
        "decision_ineligibility_reason": None,
        "supersedes_revision_id": 7,
        "supersedes_revision_hash": "a" * 64,
    }

    corrected = migration._corrected_revision_values(row, repair_time=repair_time)
    repeated = migration._corrected_revision_values(row, repair_time=repair_time)

    assert corrected["turnover"] == 10_000.0
    assert corrected["provider_version"] == migration.NEW_VERSION
    assert corrected["adjustment_version"] == migration.NEW_VERSION
    assert corrected["source_timestamp"] == repair_time
    assert corrected["first_seen_at"] == repair_time
    assert corrected["observed_at"] == repair_time
    assert corrected["supersedes_revision_id"] == 7
    assert len(corrected["payload_hash"]) == 64
    assert len(corrected["revision_hash"]) == 64
    assert corrected["payload_hash"] == data._stable_hash(  # noqa: SLF001
        {
            "etf_code": row["etf_code"],
            "trade_date": row["trade_date"].isoformat(),
            **{
                field: corrected[field]
                for field in data._ADJUSTED_PRICE_MATERIAL_FIELDS  # noqa: SLF001
            },
        }
    )
    assert corrected == repeated


def test_tencent_turnover_correction_is_the_single_migration_head() -> None:
    migration = _migration()
    assert migration.revision == "20260811_000065"
    assert migration.down_revision == "20260810_000064"

    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260817_000072"]
