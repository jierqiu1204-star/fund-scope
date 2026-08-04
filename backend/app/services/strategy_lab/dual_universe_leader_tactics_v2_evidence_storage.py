"""Append-only source-label and single-use holdout storage for V2."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import V2ContractError
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_table,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    V2LockedCaseEvidence,
    V2SourceLabel,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))


async def persist_v2_source_labels(
    session: AsyncSession,
    labels: tuple[V2SourceLabel, ...],
) -> int:
    """Persist only explicit labels; unmentioned assets are never synthesized."""

    assert_v2_research_table("leader_tactics_v2_source_labels")

    created = 0
    now = datetime.utcnow()
    for label in labels:
        result = await session.execute(
            text(
                """
                INSERT INTO leader_tactics_v2_source_labels
                    (article_id, asset_code, label_kind, label_date, theme,
                     observability, payload_json, label_hash, created_at)
                VALUES (:article_id, :asset_code, :label_kind, :label_date, :theme,
                        :observability, :payload_json, :label_hash, :created_at)
                ON CONFLICT (label_hash) DO NOTHING
                """
            ),
            {
                "article_id": label.article_id,
                "asset_code": label.asset_code,
                "label_kind": label.label_kind,
                "label_date": label.label_date,
                "theme": label.theme,
                "observability": label.observability,
                "payload_json": _json(asdict(label)),
                "label_hash": label.evidence_hash,
                "created_at": now,
            },
        )
        created += int(result.rowcount or 0)
    await session.commit()
    return created


async def persist_v2_holdout_once(
    session: AsyncSession,
    *,
    holdout_identity: str,
    case_date: date,
    status: str,
    payload: dict[str, object],
) -> None:
    """Record a locked-case evaluation exactly once; repeat access fails closed."""

    assert_v2_research_table("leader_tactics_v2_holdout_events")

    existing = await session.execute(
        text(
            "SELECT status FROM leader_tactics_v2_holdout_events WHERE holdout_identity = :identity"
        ),
        {"identity": holdout_identity},
    )
    if existing.first() is not None:
        raise V2ContractError("V2 holdout identity was already consumed")
    await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_holdout_events
                (holdout_identity, case_date, status, payload_json, created_at)
            VALUES (:identity, :case_date, :status, :payload_json, :created_at)
            """
        ),
        {
            "identity": holdout_identity,
            "case_date": case_date,
            "status": status,
            "payload_json": _json(payload),
            "created_at": datetime.utcnow(),
        },
    )
    await session.commit()


V2_HOLDOUT_IDENTITY = "holdout-2026-08-03-single-use-v1"


async def persist_v2_locked_case_evidence_once(
    session: AsyncSession,
    evidence: V2LockedCaseEvidence,
    *,
    holdout_identity: str = V2_HOLDOUT_IDENTITY,
) -> None:
    """Persist the pre-registered locked case exactly once."""

    if holdout_identity != V2_HOLDOUT_IDENTITY:
        raise V2ContractError("V2 locked-case holdout identity is incompatible")
    if evidence.status not in {
        "locked_case_match",
        "locked_case_mismatch",
        "locked_case_unavailable",
    }:
        raise V2ContractError("V2 locked-case status is invalid")
    await persist_v2_holdout_once(
        session,
        holdout_identity=holdout_identity,
        case_date=evidence.case_date,
        status=evidence.status,
        payload=asdict(evidence),
    )


async def persist_v2_revision_audit(
    session: AsyncSession,
    *,
    manifest_hash: str,
    entity_type: str,
    entity_key: str,
    prior_hash: str | None,
    new_hash: str,
    reason: str,
) -> int:
    """Persist a later revision without replacing the original PIT fact."""

    assert_v2_research_table("leader_tactics_v2_revision_audits")
    result = await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_revision_audits
                (manifest_hash, entity_type, entity_key, prior_hash, new_hash,
                 reason, observed_at)
            VALUES (:manifest_hash, :entity_type, :entity_key, :prior_hash, :new_hash,
                    :reason, :observed_at)
            ON CONFLICT (entity_type, entity_key, new_hash) DO NOTHING
            """
        ),
        {
            "manifest_hash": manifest_hash,
            "entity_type": entity_type,
            "entity_key": entity_key,
            "prior_hash": prior_hash,
            "new_hash": new_hash,
            "reason": reason,
            "observed_at": datetime.utcnow(),
        },
    )
    await session.commit()
    return int(result.rowcount or 0)


__all__ = [
    "V2_HOLDOUT_IDENTITY",
    "persist_v2_holdout_once",
    "persist_v2_locked_case_evidence_once",
    "persist_v2_revision_audit",
    "persist_v2_source_labels",
]
