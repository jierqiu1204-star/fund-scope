"""Correct Tencent ETF turnover units and append v2 revision evidence.

Revision ID: 20260811_000065
Revises: 20260810_000064
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260811_000065"
down_revision = "20260810_000064"
branch_labels = None
depends_on = None

OLD_VERSION = "tencent.ifzq.fqkline.hfq_v1"
NEW_VERSION = "tencent.ifzq.fqkline.hfq_turnover_yuan_v2"
BATCH_SIZE = 500

_MATERIAL_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "pct_change",
    "raw_price_basis",
    "research_adjusted_value",
    "research_price_basis",
    "data_provider",
    "provider_version",
    "adjustment_version",
    "decision_eligible",
    "decision_ineligibility_reason",
)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _corrected_revision_values(
    row: Mapping[str, Any],
    *,
    repair_time: datetime,
) -> dict[str, Any]:
    values = {field: row[field] for field in _MATERIAL_FIELDS}
    values["turnover"] = float(row["volume"]) * 100.0 * float(row["close"])
    values["provider_version"] = NEW_VERSION
    values["adjustment_version"] = NEW_VERSION
    payload_hash = _stable_hash(
        {
            "etf_code": str(row["etf_code"]),
            "trade_date": row["trade_date"].isoformat(),
            **values,
        }
    )
    revision_hash = _stable_hash(
        {
            "payload_hash": payload_hash,
            "supersedes_revision_hash": row["supersedes_revision_hash"],
        }
    )
    return {
        "etf_code": row["etf_code"],
        "trade_date": row["trade_date"],
        **values,
        # The corrected transformation first became decision-visible during
        # this migration. Keep old v1 revisions untouched for PIT replay.
        "source_timestamp": repair_time,
        "first_seen_at": repair_time,
        "observed_at": repair_time,
        "payload_hash": payload_hash,
        "revision_hash": revision_hash,
        "supersedes_revision_id": row["supersedes_revision_id"],
        "created_at": repair_time,
    }


def _revision_table() -> sa.TableClause:
    return sa.table(
        "etf_adjusted_price_revisions",
        sa.column("etf_code"),
        sa.column("trade_date"),
        *(sa.column(field) for field in _MATERIAL_FIELDS),
        sa.column("source_timestamp"),
        sa.column("first_seen_at"),
        sa.column("observed_at"),
        sa.column("payload_hash"),
        sa.column("revision_hash"),
        sa.column("supersedes_revision_id"),
        sa.column("created_at"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    repair_time = datetime.now(UTC).replace(tzinfo=None)
    candidates = bind.execute(
        sa.text(
            """
            SELECT
                history.etf_code,
                history.trade_date,
                history.open,
                history.high,
                history.low,
                history.close,
                history.volume,
                history.turnover,
                history.pct_change,
                history.raw_price_basis,
                history.research_adjusted_value,
                history.research_price_basis,
                history.data_provider,
                history.provider_version,
                history.adjustment_version,
                history.decision_eligible,
                history.decision_ineligibility_reason,
                previous.id AS supersedes_revision_id,
                previous.revision_hash AS supersedes_revision_hash
            FROM etf_price_history AS history
            LEFT JOIN LATERAL (
                SELECT revision.id, revision.revision_hash
                FROM etf_adjusted_price_revisions AS revision
                WHERE revision.etf_code = history.etf_code
                  AND revision.trade_date = history.trade_date
                ORDER BY
                    revision.first_seen_at DESC,
                    revision.observed_at DESC,
                    revision.id DESC
                LIMIT 1
            ) AS previous ON TRUE
            WHERE history.data_provider = 'tencent'
              AND history.provider_version = :old_version
              AND history.adjustment_version = :old_version
              AND history.volume IS NOT NULL
              AND history.close IS NOT NULL
              AND history.turnover IS NOT NULL
              AND history.volume >= 0
              AND history.close > 0
              AND abs(history.turnover - history.volume * history.close)
                  <= greatest(0.000001, abs(history.volume * history.close) * 0.000000001)
            ORDER BY history.id
            """
        ),
        {"old_version": OLD_VERSION},
    ).mappings()

    revision_table = _revision_table()
    insert_batch: list[dict[str, Any]] = []
    for row in candidates:
        insert_batch.append(
            _corrected_revision_values(row, repair_time=repair_time)
        )
        if len(insert_batch) < BATCH_SIZE:
            continue
        statement = postgresql.insert(revision_table).values(insert_batch)
        bind.execute(
            statement.on_conflict_do_nothing(
                index_elements=[revision_table.c.revision_hash]
            )
        )
        insert_batch.clear()
    if insert_batch:
        statement = postgresql.insert(revision_table).values(insert_batch)
        bind.execute(
            statement.on_conflict_do_nothing(
                index_elements=[revision_table.c.revision_hash]
            )
        )

    bind.execute(
        sa.text(
            """
            UPDATE etf_price_history
            SET turnover = volume * 100.0 * close,
                provider_version = :new_version,
                adjustment_version = :new_version,
                source_timestamp = :repair_time
            WHERE data_provider = 'tencent'
              AND provider_version = :old_version
              AND adjustment_version = :old_version
              AND volume IS NOT NULL
              AND close IS NOT NULL
              AND turnover IS NOT NULL
              AND volume >= 0
              AND close > 0
              AND abs(turnover - volume * close)
                  <= greatest(0.000001, abs(volume * close) * 0.000000001)
            """
        ),
        {
            "old_version": OLD_VERSION,
            "new_version": NEW_VERSION,
            "repair_time": repair_time,
        },
    )


def downgrade() -> None:
    # The append-only v1 revisions remain available, while older application
    # versions fail closed on v2 projection rows. Never reintroduce the known
    # 100x turnover-unit defect during rollback.
    return
