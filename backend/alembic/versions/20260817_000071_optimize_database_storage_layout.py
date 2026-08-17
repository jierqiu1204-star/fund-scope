"""Bound quote storage and finish incremental checkpoint migration."""

from __future__ import annotations

import json
import re
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "20260817_000071"
down_revision = "20260814_000070"
branch_labels = None
depends_on = None

_CHECKPOINT_SCHEMA_VERSION = "dual_universe_leader_tactics_v2_checkpoint_v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REDUNDANT_QUOTE_INDEX = "ix_etf_intraday_quotes_code_time"


def _json_value(value: object, *, field: str) -> object:
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        raise RuntimeError(f"legacy checkpoint {field} is not JSON")
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"legacy checkpoint {field} is malformed") from exc


def _completed_items(value: object) -> dict[str, str | None]:
    payload = _json_value(value, field="completed_hashes_json")
    if isinstance(payload, list):
        if all(isinstance(item, str) for item in payload):
            pairs: list[tuple[object, object]] = [(item, None) for item in payload]
        elif all(isinstance(item, (list, tuple)) and len(item) == 2 for item in payload):
            pairs = [(item[0], item[1]) for item in payload]
        else:
            raise RuntimeError("legacy checkpoint completed payload is ambiguous")
    elif isinstance(payload, dict):
        if payload.get("schema_version") != _CHECKPOINT_SCHEMA_VERSION:
            raise RuntimeError("legacy checkpoint schema version is unsupported")
        codes = payload.get("completed_codes")
        hashes = payload.get("content_hashes")
        if not isinstance(codes, list) or not isinstance(hashes, list):
            raise RuntimeError("legacy checkpoint completed payload is malformed")
        if not all(isinstance(item, str) for item in codes):
            raise RuntimeError("legacy checkpoint completed code is malformed")
        if not all(isinstance(item, (list, tuple)) and len(item) == 2 for item in hashes):
            raise RuntimeError("legacy checkpoint content hashes are malformed")
        hash_map: dict[object, object] = {}
        for code, content_hash in hashes:
            if code in hash_map:
                raise RuntimeError("legacy checkpoint content hash is duplicated")
            hash_map[code] = content_hash
        if not set(hash_map).issubset(set(codes)):
            raise RuntimeError("legacy checkpoint hash has no completed code")
        pairs = [(code, hash_map.get(code)) for code in codes]
    else:
        raise RuntimeError("legacy checkpoint completed payload is malformed")

    result: dict[str, str | None] = {}
    for code_value, hash_value in pairs:
        if (
            not isinstance(code_value, str)
            or not code_value
            or code_value != code_value.strip()
        ):
            raise RuntimeError("legacy checkpoint completed code is malformed")
        if code_value in result:
            raise RuntimeError("legacy checkpoint completed code is duplicated")
        if hash_value is not None and (
            not isinstance(hash_value, str) or _SHA256_RE.fullmatch(hash_value) is None
        ):
            raise RuntimeError("legacy checkpoint content hash is malformed")
        result[code_value] = hash_value
    return result


def _failed_items(value: object) -> dict[str, str]:
    payload = _json_value(value, field="failed_codes_json")
    if not isinstance(payload, list):
        raise RuntimeError("legacy checkpoint failed payload is malformed")
    result: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise RuntimeError("legacy checkpoint failed entry is malformed")
        code, message = item
        if (
            not isinstance(code, str)
            or not code
            or code != code.strip()
            or not isinstance(message, str)
            or not message
        ):
            raise RuntimeError("legacy checkpoint failed entry is malformed")
        if code in result:
            raise RuntimeError("legacy checkpoint failed code is duplicated")
        result[code] = message
    return result


def _migrate_legacy_checkpoints(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT manifest_hash, completed_count, completed_hashes_json,
                   failed_codes_json, updated_at
            FROM leader_tactics_v2_checkpoints
            WHERE storage_version = 1
            ORDER BY manifest_hash
            """
        )
    ).mappings()
    for row in rows:
        manifest_hash = row["manifest_hash"]
        completed = _completed_items(row["completed_hashes_json"])
        failed = _failed_items(row["failed_codes_json"])
        if set(completed) & set(failed):
            raise RuntimeError(f"legacy checkpoint {manifest_hash} has overlapping states")
        if row["completed_count"] != len(completed):
            raise RuntimeError(f"legacy checkpoint {manifest_hash} count is inconsistent")

        expected = {
            **{code: ("completed", content_hash, None) for code, content_hash in completed.items()},
            **{code: ("failed", None, message) for code, message in failed.items()},
        }
        existing = {
            item["asset_code"]: (
                item["item_state"],
                item["content_hash"],
                item["error_message"],
            )
            for item in bind.execute(
                sa.text(
                    """
                    SELECT asset_code, item_state, content_hash, error_message
                    FROM leader_tactics_v2_checkpoint_items
                    WHERE manifest_hash = :manifest_hash
                    """
                ),
                {"manifest_hash": manifest_hash},
            ).mappings()
        }
        for code, item in existing.items():
            if expected.get(code) != item:
                raise RuntimeError(
                    f"legacy checkpoint {manifest_hash} conflicts with incremental item {code}"
                )

        missing = [
            {
                "manifest_hash": manifest_hash,
                "asset_code": code,
                "item_state": state,
                "content_hash": content_hash,
                "error_message": error_message,
                "updated_at": row["updated_at"] or datetime.utcnow(),
            }
            for code, (state, content_hash, error_message) in expected.items()
            if code not in existing
        ]
        if missing:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO leader_tactics_v2_checkpoint_items
                        (manifest_hash, asset_code, item_state, content_hash,
                         error_message, updated_at)
                    VALUES (:manifest_hash, :asset_code, :item_state, :content_hash,
                            :error_message, :updated_at)
                    ON CONFLICT (manifest_hash, asset_code) DO NOTHING
                    """
                ),
                missing,
            )
        represented_count = bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM leader_tactics_v2_checkpoint_items
                WHERE manifest_hash = :manifest_hash
                """
            ),
            {"manifest_hash": manifest_hash},
        ).scalar_one()
        if represented_count != len(expected):
            raise RuntimeError(f"legacy checkpoint {manifest_hash} migration is incomplete")
        bind.execute(
            sa.text(
                """
                UPDATE leader_tactics_v2_checkpoints
                SET completed_hashes_json = '[]',
                    failed_codes_json = '[]',
                    storage_version = 2
                WHERE manifest_hash = :manifest_hash AND storage_version = 1
                """
            ),
            {"manifest_hash": manifest_hash},
        )


def _restore_legacy_checkpoints(bind: sa.Connection) -> None:
    manifest_hashes = bind.execute(
        sa.text(
            """
            SELECT manifest_hash
            FROM leader_tactics_v2_checkpoints
            WHERE storage_version = 2
            ORDER BY manifest_hash
            """
        )
    ).scalars()
    for manifest_hash in manifest_hashes:
        items = list(
            bind.execute(
                sa.text(
                    """
                    SELECT asset_code, item_state, content_hash, error_message
                    FROM leader_tactics_v2_checkpoint_items
                    WHERE manifest_hash = :manifest_hash
                    ORDER BY asset_code
                    """
                ),
                {"manifest_hash": manifest_hash},
            ).mappings()
        )
        completed_codes = [item["asset_code"] for item in items if item["item_state"] == "completed"]
        content_hashes = [
            [item["asset_code"], item["content_hash"]]
            for item in items
            if item["item_state"] == "completed" and item["content_hash"] is not None
        ]
        failed_codes = [
            [item["asset_code"], item["error_message"]]
            for item in items
            if item["item_state"] == "failed"
        ]
        completed_payload = json.dumps(
            {
                "schema_version": _CHECKPOINT_SCHEMA_VERSION,
                "completed_codes": completed_codes,
                "content_hashes": content_hashes,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        bind.execute(
            sa.text(
                """
                UPDATE leader_tactics_v2_checkpoints
                SET completed_count = :completed_count,
                    completed_hashes_json = :completed_hashes,
                    failed_codes_json = :failed_codes,
                    storage_version = 1
                WHERE manifest_hash = :manifest_hash
                """
            ),
            {
                "manifest_hash": manifest_hash,
                "completed_count": len(completed_codes),
                "completed_hashes": completed_payload,
                "failed_codes": json.dumps(failed_codes, separators=(",", ":")),
            },
        )


def _has_index(bind: sa.Connection, name: str) -> bool:
    return any(
        index["name"] == name
        for index in sa.inspect(bind).get_indexes("etf_intraday_quotes")
    )


def upgrade() -> None:
    bind = op.get_bind()
    _migrate_legacy_checkpoints(bind)
    if _has_index(bind, _REDUNDANT_QUOTE_INDEX):
        op.drop_index(_REDUNDANT_QUOTE_INDEX, table_name="etf_intraday_quotes")


def downgrade() -> None:
    bind = op.get_bind()
    _restore_legacy_checkpoints(bind)
    if not _has_index(bind, _REDUNDANT_QUOTE_INDEX):
        op.create_index(
            _REDUNDANT_QUOTE_INDEX,
            "etf_intraday_quotes",
            ["etf_code", "quote_time"],
        )
