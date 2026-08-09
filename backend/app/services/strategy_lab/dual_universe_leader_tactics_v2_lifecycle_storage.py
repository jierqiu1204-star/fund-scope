"""Append-only lifecycle/checkpoint persistence for V2 research runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2LifecycleTransition,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_table,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    MAX_BATCH_SIZE,
    MAX_CONTINUATION_SECONDS,
    MIN_BATCH_SIZE,
    V2CollectorCheckpoint,
    _validate_sha256,
)

CHECKPOINT_SCHEMA_VERSION = "dual_universe_leader_tactics_v2_checkpoint_v2"
CHECKPOINT_STORAGE_VERSION_INCREMENTAL = 2
V2_GLOBAL_RUN_LEASE_KEY = "__dual_universe_leader_tactics_v2_global_run__"
_VALID_CHECKPOINT_STATUSES = frozenset({"paused", "partial", "complete"})


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))


def _checkpoint_completed_payload(checkpoint: V2CollectorCheckpoint) -> dict[str, object]:
    completed_codes = tuple(checkpoint.completed_codes)
    if any(
        not isinstance(code, str) or not code or code != code.strip() for code in completed_codes
    ):
        raise ValueError("checkpoint completed_codes contains an invalid code")
    if len(set(completed_codes)) != len(completed_codes):
        raise ValueError("checkpoint completed_codes contains duplicates")
    content_hashes = tuple(sorted(checkpoint.completed_hashes))
    content_codes = {code for code, _ in content_hashes}
    if not content_codes.issubset(set(completed_codes)):
        raise ValueError("checkpoint content_hashes contains an incomplete code")
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "completed_codes": list(completed_codes),
        "content_hashes": [list(item) for item in content_hashes],
    }


def _loads_checkpoint_json(value: object, field: str) -> object:
    if not isinstance(value, str):
        raise ValueError(f"checkpoint {field} JSON is not text")
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"checkpoint {field} JSON is invalid") from exc


def _parse_completed_json(value: object) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Read both the legacy code list and the versioned checkpoint shape."""

    if isinstance(value, list):
        if not value:
            return (), ()
        if all(isinstance(item, str) for item in value):
            return tuple(value), ()
        if all(isinstance(item, (list, tuple)) and len(item) == 2 for item in value):
            pairs = tuple((item[0], item[1]) for item in value)
            return tuple(code for code, _ in pairs), pairs
        raise ValueError("checkpoint completed JSON has mixed or invalid entries")
    if not isinstance(value, dict):
        raise ValueError("checkpoint completed JSON must be a list or versioned object")
    if value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("checkpoint completed JSON schema version is unsupported")
    codes = value.get("completed_codes")
    hashes = value.get("content_hashes")
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        raise ValueError("checkpoint completed_codes is invalid")
    if not isinstance(hashes, list):
        raise ValueError("checkpoint content_hashes is invalid")
    if not all(isinstance(item, (list, tuple)) and len(item) == 2 for item in hashes):
        raise ValueError("checkpoint content_hashes has invalid pairs")
    return tuple(codes), tuple((item[0], item[1]) for item in hashes)


def _validate_loaded_checkpoint(
    *,
    completed_count: object,
    completed_codes: tuple[str, ...],
    content_hashes: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    if isinstance(completed_count, bool) or not isinstance(completed_count, int):
        raise ValueError("checkpoint completed_count is invalid")
    if completed_count != len(completed_codes):
        raise ValueError("checkpoint completed_count does not match completed_codes")
    if any(
        not isinstance(code, str) or not code or code != code.strip() for code in completed_codes
    ):
        raise ValueError("checkpoint completed_codes contains an invalid code")
    if len(set(completed_codes)) != len(completed_codes):
        raise ValueError("checkpoint completed_codes contains duplicates")
    normalized: dict[str, str] = {}
    for item in content_hashes:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("checkpoint content_hashes has invalid pairs")
        code, content_hash = item
        if not isinstance(code, str) or code != code.strip() or not code:
            raise ValueError("checkpoint content_hashes contains an invalid code")
        if code not in completed_codes:
            raise ValueError("checkpoint content_hashes contains an incomplete code")
        validated_hash = _validate_sha256(content_hash)
        previous = normalized.get(code)
        if previous is not None and previous != validated_hash:
            raise ValueError("checkpoint content_hashes contains conflicting hashes")
        if previous is not None:
            raise ValueError("checkpoint content_hashes contains duplicate codes")
        normalized[code] = validated_hash
    return completed_codes, tuple(sorted(normalized.items()))


async def persist_v2_lifecycle_transitions(
    session: AsyncSession,
    *,
    manifest_hash: str,
    transitions: tuple[V2LifecycleTransition, ...],
) -> int:
    """Write immutable transitions in one executemany statement."""

    assert_v2_research_table("leader_tactics_v2_state_transitions")
    if not transitions:
        return 0

    now = datetime.utcnow()
    payloads = [
        {
            "manifest_hash": manifest_hash,
            "universe": transition.universe,
            "asset_code": transition.asset_code,
            "formula_id": transition.formula_id,
            "signal_date": transition.signal_date,
            "from_state": transition.from_state,
            "to_state": transition.to_state,
            "transition_date": transition.transition_date,
            "payload_json": _json(asdict(transition)),
            "transition_hash": transition.transition_hash,
            "created_at": now,
        }
        for transition in transitions
    ]
    result = await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_state_transitions
                (manifest_hash, universe, asset_code, formula_id, signal_date,
                 from_state, to_state, transition_date, payload_json,
                 transition_hash, created_at)
            VALUES (:manifest_hash, :universe, :asset_code, :formula_id,
                    :signal_date, :from_state, :to_state, :transition_date,
                    :payload_json, :transition_hash, :created_at)
            ON CONFLICT (transition_hash) DO NOTHING
            """
        ),
        payloads,
    )
    # The caller owns the transaction so transitions can be committed or
    # rolled back atomically with the surrounding research materialization.
    return max(0, int(result.rowcount or 0))


class V2LeaseLostError(RuntimeError):
    """Raised when a checkpoint writer no longer owns its database lease."""


async def acquire_v2_checkpoint_lease(
    session: AsyncSession,
    *,
    manifest_hash: str,
    lease_owner: str,
    lease_seconds: float = MAX_CONTINUATION_SECONDS + 5.0,
    now: datetime | None = None,
) -> datetime | None:
    """Acquire or renew a checkpoint lease with a database-side CAS.

    The compare-and-set is the source of truth across processes. The in-process
    asyncio lock remains only a cheap duplicate-work guard for one worker.
    """

    if not manifest_hash.strip() or not lease_owner.strip():
        raise ValueError("manifest_hash and lease_owner are required")
    if lease_seconds <= 0 or lease_seconds > MAX_CONTINUATION_SECONDS + 10.0:
        raise ValueError("checkpoint lease must be positive and bounded")
    effective_now = now or datetime.utcnow()
    expires_at = effective_now + timedelta(seconds=lease_seconds)
    updated = await session.execute(
        text(
            """
            UPDATE leader_tactics_v2_checkpoints
            SET lease_owner = :lease_owner,
                lease_expires_at = :lease_expires_at,
                updated_at = :updated_at
            WHERE manifest_hash = :manifest_hash
              AND (lease_owner IS NULL
                   OR lease_expires_at IS NULL
                   OR lease_expires_at <= :updated_at
                   OR lease_owner = :lease_owner)
            """
        ),
        {
            "manifest_hash": manifest_hash,
            "lease_owner": lease_owner,
            "lease_expires_at": expires_at,
            "updated_at": effective_now,
        },
    )
    if int(updated.rowcount or 0) > 0:
        await session.commit()
        return expires_at

    inserted = await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_checkpoints
                (manifest_hash, cursor, batch_size, completed_count,
                 completed_hashes_json, failed_codes_json, status,
                 lease_owner, lease_expires_at, error_summary, updated_at,
                 storage_version)
            VALUES (:manifest_hash, NULL, :batch_size, 0, :completed_hashes, '[]', 'paused',
                    :lease_owner, :lease_expires_at, NULL, :updated_at,
                    :storage_version)
            ON CONFLICT (manifest_hash) DO NOTHING
            """
        ),
        {
            "manifest_hash": manifest_hash,
            "batch_size": MIN_BATCH_SIZE,
            "completed_hashes": _json(
                {
                    "schema_version": CHECKPOINT_SCHEMA_VERSION,
                    "completed_codes": [],
                    "content_hashes": [],
                }
            ),
            "lease_owner": lease_owner,
            "lease_expires_at": expires_at,
            "updated_at": effective_now,
            "storage_version": CHECKPOINT_STORAGE_VERSION_INCREMENTAL,
        },
    )
    await session.commit()
    return expires_at if int(inserted.rowcount or 0) > 0 else None


async def acquire_v2_global_run_lease(
    session: AsyncSession,
    *,
    lease_owner: str,
    lease_seconds: float = MAX_CONTINUATION_SECONDS + 5.0,
    now: datetime | None = None,
) -> datetime | None:
    """Acquire the cross-process lease for any full V2 run.

    The per-manifest checkpoint lease protects resume state. This separate
    reserved row protects the two-core host when a new manifest would
    otherwise allow a second full capture/screen run to start concurrently.
    It deliberately reuses the same database CAS primitive and has no
    in-process-lock dependency.
    """

    return await acquire_v2_checkpoint_lease(
        session,
        manifest_hash=V2_GLOBAL_RUN_LEASE_KEY,
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
        now=now,
    )


async def release_v2_global_run_lease(
    session: AsyncSession,
    *,
    lease_owner: str,
) -> bool:
    """Release the global V2 run lease only when owned by this worker."""

    return await release_v2_checkpoint_lease(
        session,
        manifest_hash=V2_GLOBAL_RUN_LEASE_KEY,
        lease_owner=lease_owner,
    )


async def release_v2_checkpoint_lease(
    session: AsyncSession,
    *,
    manifest_hash: str,
    lease_owner: str,
) -> bool:
    """Release only the lease owned by this worker."""

    result = await session.execute(
        text(
            """
            UPDATE leader_tactics_v2_checkpoints
            SET lease_owner = NULL, lease_expires_at = NULL, updated_at = :updated_at
            WHERE manifest_hash = :manifest_hash AND lease_owner = :lease_owner
            """
        ),
        {
            "manifest_hash": manifest_hash,
            "lease_owner": lease_owner,
            "updated_at": datetime.utcnow(),
        },
    )
    await session.commit()
    return int(result.rowcount or 0) > 0


async def load_v2_checkpoint(
    session: AsyncSession,
    *,
    manifest_hash: str,
) -> V2CollectorCheckpoint | None:
    """Load and validate a durable checkpoint; malformed state fails closed."""

    if not isinstance(manifest_hash, str) or not manifest_hash.strip():
        raise ValueError("manifest_hash is required")
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT manifest_hash, cursor, batch_size, completed_count,
                       completed_hashes_json, failed_codes_json, status,
                       error_summary, storage_version
                FROM leader_tactics_v2_checkpoints
                WHERE manifest_hash = :manifest_hash
                """
                ),
                {"manifest_hash": manifest_hash},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if row["manifest_hash"] != manifest_hash:
        raise ValueError("checkpoint manifest identity is incompatible")
    storage_version = row["storage_version"]
    if (
        isinstance(storage_version, bool)
        or not isinstance(storage_version, int)
        or storage_version not in {1, CHECKPOINT_STORAGE_VERSION_INCREMENTAL}
    ):
        raise ValueError("checkpoint storage_version is invalid")
    if storage_version == CHECKPOINT_STORAGE_VERSION_INCREMENTAL:
        item_rows = (
            (
                await session.execute(
                    text(
                        """
                        SELECT asset_code, item_state, content_hash, error_message
                        FROM leader_tactics_v2_checkpoint_items
                        WHERE manifest_hash = :manifest_hash
                        ORDER BY asset_code
                        """
                    ),
                    {"manifest_hash": manifest_hash},
                )
            )
            .mappings()
            .all()
        )
        completed_codes_list: list[str] = []
        content_hashes_list: list[tuple[str, str]] = []
        failed_codes: list[tuple[str, str]] = []
        for item in item_rows:
            code = item["asset_code"]
            state = item["item_state"]
            content_hash = item["content_hash"]
            error_message = item["error_message"]
            if not isinstance(code, str) or not code or code != code.strip():
                raise ValueError("checkpoint item asset_code is invalid")
            if state == "completed":
                if error_message is not None:
                    raise ValueError("completed checkpoint item has an error")
                completed_codes_list.append(code)
                if content_hash is not None:
                    content_hashes_list.append((code, content_hash))
            elif state == "failed":
                if (
                    content_hash is not None
                    or not isinstance(error_message, str)
                    or not error_message
                ):
                    raise ValueError("failed checkpoint item is invalid")
                failed_codes.append((code, error_message))
            else:
                raise ValueError("checkpoint item state is invalid")
        completed_codes = tuple(completed_codes_list)
        content_hashes = tuple(content_hashes_list)
    else:
        completed_json = _loads_checkpoint_json(row["completed_hashes_json"], "completed_hashes")
        completed_codes, content_hashes = _parse_completed_json(completed_json)
        failed_json = _loads_checkpoint_json(row["failed_codes_json"], "failed_codes")
        if not isinstance(failed_json, list):
            raise ValueError("checkpoint failed_codes JSON is invalid")
        failed_codes = []
        for item in failed_json:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("checkpoint failed_codes has invalid pairs")
            failed_codes.append((item[0], item[1]))
    completed_codes, content_hashes = _validate_loaded_checkpoint(
        completed_count=row["completed_count"],
        completed_codes=completed_codes,
        content_hashes=content_hashes,
    )
    batch_size = row["batch_size"]
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("checkpoint batch_size is invalid")
    if not MIN_BATCH_SIZE <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("checkpoint batch_size is outside the bounded range")
    status = row["status"]
    if not isinstance(status, str) or status not in _VALID_CHECKPOINT_STATUSES:
        raise ValueError("checkpoint status is invalid")
    normalized_failed_codes: list[tuple[str, str]] = []
    seen_failed: set[str] = set()
    for code, message in failed_codes:
        if (
            not isinstance(code, str)
            or code != code.strip()
            or not code
            or not isinstance(message, str)
            or not message
            or code in seen_failed
        ):
            raise ValueError("checkpoint failed_codes contains invalid or duplicate entries")
        seen_failed.add(code)
        normalized_failed_codes.append((code, message))
    overlap = set(completed_codes) & seen_failed
    if overlap:
        raise ValueError(
            "checkpoint completed and failed codes overlap: " + ",".join(sorted(overlap))
        )
    cursor = row["cursor"]
    if cursor is not None and (
        not isinstance(cursor, str) or not cursor or cursor != cursor.strip()
    ):
        raise ValueError("checkpoint cursor is invalid")
    error_summary = row["error_summary"]
    if error_summary is not None and (not isinstance(error_summary, str) or not error_summary):
        raise ValueError("checkpoint error_summary is invalid")
    return V2CollectorCheckpoint(
        cursor=cursor,
        batch_size=batch_size,
        completed_codes=completed_codes,
        status=status,
        failed_codes=tuple(normalized_failed_codes),
        error_summary=row["error_summary"],
        manifest_hash=manifest_hash,
        completed_hashes=content_hashes,
    )


def _incremental_checkpoint_items(
    checkpoint: V2CollectorCheckpoint,
    previous_checkpoint: V2CollectorCheckpoint | None,
    *,
    manifest_hash: str,
    updated_at: datetime,
) -> list[dict[str, object]]:
    previous_completed = (
        set(previous_checkpoint.completed_codes) if previous_checkpoint is not None else set()
    )
    previous_hashes = (
        dict(previous_checkpoint.completed_hashes) if previous_checkpoint is not None else {}
    )
    previous_failed = (
        dict(previous_checkpoint.failed_codes) if previous_checkpoint is not None else {}
    )
    current_hashes = dict(checkpoint.completed_hashes)
    payloads: list[dict[str, object]] = []
    for code in checkpoint.completed_codes:
        content_hash = current_hashes.get(code)
        if code in previous_completed and previous_hashes.get(code) == content_hash:
            continue
        payloads.append(
            {
                "manifest_hash": manifest_hash,
                "asset_code": code,
                "item_state": "completed",
                "content_hash": content_hash,
                "error_message": None,
                "updated_at": updated_at,
            }
        )
    for code, message in checkpoint.failed_codes:
        if previous_failed.get(code) == message:
            continue
        payloads.append(
            {
                "manifest_hash": manifest_hash,
                "asset_code": code,
                "item_state": "failed",
                "content_hash": None,
                "error_message": message,
                "updated_at": updated_at,
            }
        )
    return payloads


async def save_v2_checkpoint(
    session: AsyncSession,
    *,
    manifest_hash: str,
    checkpoint: V2CollectorCheckpoint,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    previous_checkpoint: V2CollectorCheckpoint | None = None,
) -> None:
    """Persist a checkpoint without overwriting another valid lease."""

    if checkpoint.manifest_hash not in (None, manifest_hash):
        raise ValueError("checkpoint manifest identity is incompatible")
    now = datetime.utcnow()
    if not isinstance(checkpoint.batch_size, int) or isinstance(checkpoint.batch_size, bool):
        raise ValueError("checkpoint batch_size is invalid")
    if not MIN_BATCH_SIZE <= checkpoint.batch_size <= MAX_BATCH_SIZE:
        raise ValueError("checkpoint batch_size is outside the bounded range")
    if checkpoint.status not in _VALID_CHECKPOINT_STATUSES:
        raise ValueError("checkpoint status is invalid")
    completed_payload = _checkpoint_completed_payload(checkpoint)
    failed_codes = tuple(checkpoint.failed_codes)
    failed_code_set = {code for code, _ in failed_codes}
    overlap = set(checkpoint.completed_codes) & failed_code_set
    if overlap:
        raise ValueError(
            "checkpoint completed and failed codes overlap: " + ",".join(sorted(overlap))
        )
    fields = {
        "manifest_hash": manifest_hash,
        "cursor": checkpoint.cursor,
        "batch_size": checkpoint.batch_size,
        "completed_count": len(checkpoint.completed_codes),
        "completed_hashes": _json(completed_payload),
        "failed_codes": _json(failed_codes),
        "status": checkpoint.status,
        "error_summary": checkpoint.error_summary,
        "updated_at": now,
        "storage_version": CHECKPOINT_STORAGE_VERSION_INCREMENTAL,
    }
    if lease_owner is not None:
        if lease_expires_at is None:
            raise ValueError("lease_expires_at is required for an owned checkpoint save")
        result = await session.execute(
            text(
                """
                UPDATE leader_tactics_v2_checkpoints
                SET cursor = :cursor,
                    batch_size = :batch_size,
                    completed_count = :completed_count,
                    completed_hashes_json = CASE
                        WHEN storage_version = 1 THEN :completed_hashes
                        ELSE completed_hashes_json
                    END,
                    failed_codes_json = CASE
                        WHEN storage_version = 1 THEN :failed_codes
                        ELSE failed_codes_json
                    END,
                    status = :status,
                    error_summary = :error_summary,
                    updated_at = :updated_at
                WHERE manifest_hash = :manifest_hash
                  AND lease_owner = :lease_owner
                  AND lease_expires_at > :updated_at
                """
            ),
            {**fields, "lease_owner": lease_owner},
        )
    else:
        result = await session.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_checkpoints
                    (manifest_hash, cursor, batch_size, completed_count,
                     completed_hashes_json, failed_codes_json, status,
                     lease_owner, lease_expires_at, error_summary, updated_at,
                     storage_version)
                VALUES (:manifest_hash, :cursor, :batch_size, :completed_count,
                        :completed_hashes, :failed_codes, :status, NULL, NULL,
                        :error_summary, :updated_at, :storage_version)
                ON CONFLICT (manifest_hash) DO UPDATE SET
                    cursor = excluded.cursor,
                    batch_size = excluded.batch_size,
                    completed_count = excluded.completed_count,
                    completed_hashes_json = CASE
                        WHEN leader_tactics_v2_checkpoints.storage_version = 1
                        THEN excluded.completed_hashes_json
                        ELSE leader_tactics_v2_checkpoints.completed_hashes_json
                    END,
                    failed_codes_json = CASE
                        WHEN leader_tactics_v2_checkpoints.storage_version = 1
                        THEN excluded.failed_codes_json
                        ELSE leader_tactics_v2_checkpoints.failed_codes_json
                    END,
                    status = excluded.status,
                    error_summary = excluded.error_summary,
                    updated_at = excluded.updated_at
                WHERE leader_tactics_v2_checkpoints.lease_owner IS NULL
                   OR leader_tactics_v2_checkpoints.lease_expires_at IS NULL
                   OR leader_tactics_v2_checkpoints.lease_expires_at <= :updated_at
                """
            ),
            fields,
        )
    if int(result.rowcount or 0) <= 0:
        await session.rollback()
        raise V2LeaseLostError("checkpoint lease is not owned or has expired")
    storage_version = await session.scalar(
        text(
            """
            SELECT storage_version
            FROM leader_tactics_v2_checkpoints
            WHERE manifest_hash = :manifest_hash
            """
        ),
        {"manifest_hash": manifest_hash},
    )
    if storage_version == CHECKPOINT_STORAGE_VERSION_INCREMENTAL:
        assert_v2_research_table("leader_tactics_v2_checkpoint_items")
        item_payloads = _incremental_checkpoint_items(
            checkpoint,
            previous_checkpoint,
            manifest_hash=manifest_hash,
            updated_at=now,
        )
        if item_payloads:
            await session.execute(
                text(
                    """
                    INSERT INTO leader_tactics_v2_checkpoint_items
                        (manifest_hash, asset_code, item_state, content_hash,
                         error_message, updated_at)
                    VALUES (:manifest_hash, :asset_code, :item_state, :content_hash,
                            :error_message, :updated_at)
                    ON CONFLICT (manifest_hash, asset_code) DO UPDATE SET
                        item_state = excluded.item_state,
                        content_hash = excluded.content_hash,
                        error_message = excluded.error_message,
                        updated_at = excluded.updated_at
                    """
                ),
                item_payloads,
            )
    await session.commit()


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CHECKPOINT_STORAGE_VERSION_INCREMENTAL",
    "V2_GLOBAL_RUN_LEASE_KEY",
    "V2LeaseLostError",
    "acquire_v2_checkpoint_lease",
    "acquire_v2_global_run_lease",
    "load_v2_checkpoint",
    "persist_v2_lifecycle_transitions",
    "release_v2_checkpoint_lease",
    "release_v2_global_run_lease",
    "save_v2_checkpoint",
]
