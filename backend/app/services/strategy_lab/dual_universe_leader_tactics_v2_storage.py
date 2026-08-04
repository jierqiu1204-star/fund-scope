"""Append-only storage and bounded reads for leader-tactics V2 research."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2_SOURCE_REGISTRY,
    V2ContractError,
    V2ResearchManifest,
    V2ScreenResult,
    build_v2_manifest,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_boundary import (
    assert_v2_research_table,
)

V2_STORAGE_SCHEMA_VERSION = "leader_tactics_v2_storage_v1"
V2_MANIFEST_STATUS = "materialized"
MAX_V2_PAGE_SIZE = 100

_FORMULA_IDS = {
    "breakout": "leader_breakout_proxy_v2",
    "base_launch": "base_launch_proxy_v2",
    "former_leader_repair": "former_leader_repair_proxy_v2",
}


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )


def _as_of_datetime(value: datetime | date | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.max)
    # datetime.fromisoformat("YYYY-MM-DD") returns midnight on supported
    # Python versions. A date-only PIT cutoff is inclusive through the end
    # of that trading day, so detect it before parsing as a datetime.
    if isinstance(value, str) and len(value) == 10:
        try:
            return datetime.combine(date.fromisoformat(value), time.max)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("as_of must be an ISO date or datetime") from exc
        return datetime.combine(parsed_date, time.max)
    return parsed


_MANIFEST_FIELDS = frozenset(field.name for field in fields(V2ResearchManifest))


def _manifest_datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise V2ContractError(f"manifest {label} is invalid") from exc
    raise V2ContractError(f"manifest {label} is invalid")


def _manifest_pairs(value: object, label: str) -> tuple[tuple[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise V2ContractError(f"manifest {label} is invalid")
    pairs: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            raise V2ContractError(f"manifest {label} is invalid")
        if len(item) != 2 or not isinstance(item[0], str) or item[0] in seen:
            raise V2ContractError(f"manifest {label} is invalid")
        seen.add(item[0])
        pairs.append((item[0], item[1]))
    return tuple(pairs)


def _required_json_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise V2ContractError(f"manifest {label} JSON is invalid")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise V2ContractError(f"manifest {label} JSON is invalid") from exc
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise V2ContractError(f"manifest {label} JSON is invalid")
    return parsed


def _manifest_from_payload(value: object) -> V2ResearchManifest:
    if not isinstance(value, str):
        raise V2ContractError("manifest payload is invalid")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise V2ContractError("manifest payload is invalid") from exc
    if not isinstance(payload, dict):
        raise V2ContractError("manifest payload is invalid")
    if set(payload) != _MANIFEST_FIELDS:
        raise V2ContractError("manifest payload fields are incomplete or unexpected")

    normalized = dict(payload)
    normalized["decision_cutoff"] = _manifest_datetime(
        normalized["decision_cutoff"], "decision_cutoff"
    )
    normalized["data_receipt_cutoff"] = _manifest_datetime(
        normalized["data_receipt_cutoff"], "data_receipt_cutoff"
    )
    formula_ids = normalized["formula_ids"]
    if not isinstance(formula_ids, Sequence) or isinstance(formula_ids, (str, bytes, bytearray)):
        raise V2ContractError("manifest formula_ids is invalid")
    normalized["formula_ids"] = tuple(formula_ids)
    for label in ("cost_model", "exclusions", "provider_health"):
        normalized[label] = _manifest_pairs(normalized[label], label)

    try:
        manifest = V2ResearchManifest(**normalized)
        manifest.validate()
    except (TypeError, ValueError, V2ContractError) as exc:
        if isinstance(exc, V2ContractError):
            raise
        raise V2ContractError("manifest payload is invalid") from exc
    return manifest


def _db_bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise V2ContractError(f"manifest {label} is invalid")


def _validate_materialized_manifest(row: Mapping[str, Any]) -> None:
    manifest = _manifest_from_payload(row.get("manifest_payload_json"))
    scalar_columns = {
        "manifest_hash": manifest.manifest_hash,
        "universe": manifest.universe,
        "input_hash": manifest.input_hash,
        "source_registry_hash": manifest.source_registry_hash,
        "formula_registry_hash": manifest.formula_registry_hash,
        "code_version": manifest.code_version,
        "holdout_identity": manifest.holdout_identity,
    }
    if any(row.get(column) != expected for column, expected in scalar_columns.items()):
        raise V2ContractError("materialized manifest columns disagree with payload")
    if row.get("status") != V2_MANIFEST_STATUS:
        raise V2ContractError("materialized manifest status is invalid")
    if _db_bool(row.get("research_only"), "research_only") is not True:
        raise V2ContractError("materialized manifest must be research-only")
    if _manifest_datetime(row.get("decision_cutoff"), "decision_cutoff") != (
        manifest.decision_cutoff
    ):
        raise V2ContractError("materialized decision cutoff disagrees with payload")
    if _manifest_datetime(row.get("data_receipt_cutoff"), "data_receipt_cutoff") != (
        manifest.data_receipt_cutoff
    ):
        raise V2ContractError("materialized receipt cutoff disagrees with payload")

    provider_health = _required_json_object(row.get("provider_health_json"), "provider_health")
    exclusions = _required_json_object(row.get("exclusions_json"), "exclusions")
    if provider_health != dict(manifest.provider_health):
        raise V2ContractError("materialized provider health disagrees with payload")
    if exclusions != dict(manifest.exclusions):
        raise V2ContractError("materialized exclusions disagree with payload")


def _as_date(value: datetime | date | str | None) -> date:
    if value is None:
        raise ValueError("transition cutoff is required")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError("transition cutoff must contain an ISO date") from exc


def _encode_cursor(
    *,
    signal_date: date,
    formula_id: str,
    asset_code: str,
    manifest_hash: str,
) -> str:
    payload = {
        "version": 1,
        "signal_date": signal_date.isoformat(),
        "formula_id": formula_id,
        "asset_code": asset_code,
        "manifest_hash": manifest_hash,
    }
    return urlsafe_b64encode(_json(payload).encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (Base64Error, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("cursor is invalid")
    required = ("signal_date", "formula_id", "asset_code", "manifest_hash")
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
        raise ValueError("cursor is invalid")
    try:
        date.fromisoformat(payload["signal_date"])
    except ValueError as exc:
        raise ValueError("cursor is invalid") from exc
    return payload


async def get_v2_materialized_manifest(
    session: AsyncSession,
    *,
    universe: str,
    as_of: datetime | date | str | None = None,
) -> Any | None:
    """Return only the newest compatible materialized manifest at as_of."""

    if universe not in {"etf", "ashare"}:
        raise ValueError("universe must be etf or ashare")
    params: dict[str, Any] = {"universe": universe}
    cutoff_clause = ""
    normalized_as_of = _as_of_datetime(as_of)
    if normalized_as_of is not None:
        cutoff_clause = """
              AND decision_cutoff <= :as_of
              AND data_receipt_cutoff <= :as_of
        """
        params["as_of"] = normalized_as_of
    result = await session.execute(
        text(
            f"""
            SELECT manifest_hash, universe, decision_cutoff, data_receipt_cutoff,
                   input_hash, source_registry_hash, formula_registry_hash,
                   code_version, holdout_identity, status, research_only,
                   provider_health_json, exclusions_json, manifest_payload_json,
                   created_at
            FROM leader_tactics_v2_run_manifests
            WHERE universe = :universe
              {cutoff_clause}
            ORDER BY decision_cutoff DESC, created_at DESC, id DESC
            LIMIT 1
            """
        ),
        params,
    )
    row = result.mappings().first()
    if row is None:
        return None
    try:
        _validate_materialized_manifest(row)
    except V2ContractError:
        # The newest row is the evidence selected by the PIT query. Do not
        # silently fall back to an older manifest if this row is corrupt.
        return None
    return row


async def persist_v2_screen_result(
    session: AsyncSession,
    result: V2ScreenResult,
    *,
    code_version: str | None = None,
) -> str:
    """Persist research facts only; repeated manifests and observations are idempotent."""

    assert_v2_research_table("leader_tactics_v2_run_manifests")
    assert_v2_research_table("leader_tactics_v2_candidate_observations")

    if result.data_receipt_cutoff is None:
        raise V2ContractError("V2 screen result is missing factual data receipt cutoff")
    if result.data_receipt_cutoff < result.source_cutoff:
        raise V2ContractError("V2 data receipt cutoff precedes decision cutoff")
    if code_version is not None and code_version != result.code_version:
        raise V2ContractError("external code_version does not match screen result identity")
    manifest = build_v2_manifest(
        universe=result.universe,
        decision_cutoff=result.source_cutoff,
        data_receipt_cutoff=result.data_receipt_cutoff,
        input_hash=result.input_hash,
        code_version=result.code_version,
        exclusions=result.exclusions,
        provider_health=result.provider_health,
    )
    if manifest.manifest_hash != result.manifest_hash:
        raise V2ContractError("screen result manifest hash does not match its canonical identity")
    now = datetime.utcnow()
    await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_source_registries
                (registry_version, registry_hash, payload_json, created_at)
            VALUES (:version, :hash, :payload, :created_at)
            ON CONFLICT (registry_hash) DO NOTHING
            """
        ),
        {
            "version": V2_SOURCE_REGISTRY.version,
            "hash": V2_SOURCE_REGISTRY.registry_hash,
            "payload": _json(
                {
                    "version": V2_SOURCE_REGISTRY.version,
                    "registry_hash": V2_SOURCE_REGISTRY.registry_hash,
                    "payload": V2_SOURCE_REGISTRY.canonical_payload(),
                }
            ),
            "created_at": now,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO leader_tactics_v2_run_manifests
                (manifest_hash, universe, decision_cutoff, data_receipt_cutoff,
                 input_hash, source_registry_hash, formula_registry_hash,
                 code_version, holdout_identity, status, research_only,
                 provider_health_json, exclusions_json, manifest_payload_json, created_at)
            VALUES (:manifest_hash, :universe, :decision_cutoff, :receipt_cutoff,
                    :input_hash, :source_hash, :formula_hash, :code_version,
                    :holdout_identity, :status, :research_only, :provider_health,
                    :exclusions, :manifest_payload, :created_at)
            ON CONFLICT (manifest_hash) DO NOTHING
            """
        ),
        {
            "manifest_hash": manifest.manifest_hash,
            "universe": manifest.universe,
            "decision_cutoff": manifest.decision_cutoff,
            "receipt_cutoff": manifest.data_receipt_cutoff,
            "input_hash": manifest.input_hash,
            "source_hash": manifest.source_registry_hash,
            "formula_hash": manifest.formula_registry_hash,
            "code_version": manifest.code_version,
            "holdout_identity": manifest.holdout_identity,
            "status": V2_MANIFEST_STATUS,
            "research_only": True,
            "provider_health": _json(dict(result.provider_health)),
            "exclusions": _json(dict(result.exclusions)),
            "manifest_payload": _json(asdict(manifest)),
            "created_at": now,
        },
    )

    observation_insert = text(
        """
        INSERT INTO leader_tactics_v2_candidate_observations
            (manifest_hash, universe, asset_code, asset_name, theme, sector,
             tracked_index, formula_id, state, availability, qualifies, score,
             signal_date, source_cutoff, gate_facts_json,
             exclusion_reasons_json, provenance_json, feature_hash, created_at)
        VALUES (:manifest_hash, :universe, :asset_code, :asset_name, :theme,
                :sector, :tracked_index, :formula_id, :state, :availability,
                :qualifies, :score, :signal_date, :source_cutoff, :gate_facts,
                :exclusions, :provenance, :feature_hash, :created_at)
        ON CONFLICT (manifest_hash, universe, asset_code, formula_id, signal_date)
        DO NOTHING
        """
    )
    observation_rows = [
        {
            "manifest_hash": manifest.manifest_hash,
            "universe": observation.universe,
            "asset_code": observation.asset_code,
            "asset_name": observation.asset_name,
            "theme": observation.theme,
            "sector": observation.sector,
            "tracked_index": observation.tracked_index,
            "formula_id": observation.formula_id,
            "state": observation.state,
            "availability": observation.availability,
            "qualifies": observation.qualifies,
            "score": observation.score,
            "signal_date": observation.signal_date,
            "source_cutoff": observation.source_cutoff,
            "gate_facts": _json(dict(observation.gate_facts)),
            "exclusions": _json(list(observation.exclusion_reasons)),
            "provenance": _json(
                {
                    "schema_version": V2_STORAGE_SCHEMA_VERSION,
                    "source_registry_hash": V2_SOURCE_REGISTRY.registry_hash,
                    "research_only": True,
                    "notification_provenance": "none",
                    "execution_provenance": "none",
                }
            ),
            "feature_hash": observation.feature_hash,
            "created_at": now,
        }
        for observation in result.observations
    ]
    if observation_rows:
        # A parameter sequence uses the driver executemany path while the
        # unique constraint preserves idempotency on repeated materialization.
        await session.execute(observation_insert, observation_rows)
    await session.commit()
    return manifest.manifest_hash


def _decode_json(value: object, fallback: Any) -> Any:
    if not isinstance(value, str):
        return fallback
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return parsed


def _candidate_cte(filter_where: str) -> str:
    return f"""
        WITH visible_transitions AS (
            SELECT manifest_hash, universe, asset_code, formula_id, signal_date,
                   to_state, transition_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY manifest_hash, universe, asset_code,
                                    formula_id, signal_date
                       ORDER BY transition_date DESC, id DESC
                   ) AS transition_rank
            FROM leader_tactics_v2_state_transitions
            WHERE manifest_hash = :manifest_hash
              AND universe = :universe
              AND transition_date <= :transition_cutoff
        ),
        observations AS (
            SELECT o.*,
                   COALESCE(t.to_state, o.state) AS effective_state,
                   t.transition_date AS transition_date
            FROM leader_tactics_v2_candidate_observations o
            LEFT JOIN visible_transitions t
              ON t.manifest_hash = o.manifest_hash
             AND t.universe = o.universe
             AND t.asset_code = o.asset_code
             AND t.formula_id = o.formula_id
             AND t.signal_date = o.signal_date
             AND t.transition_rank = 1
            WHERE o.manifest_hash = :manifest_hash
              AND o.universe = :universe
        ),
        filtered AS (
            SELECT *
            FROM observations
            WHERE {filter_where}
        )
    """


async def read_v2_candidates(
    session: AsyncSession,
    *,
    universe: str = "etf",
    formula: str = "all",
    state: str = "all",
    as_of: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Read one compatible materialized manifest with bounded keyset pages."""

    if universe not in {"etf", "ashare"}:
        raise ValueError("universe must be etf or ashare")
    if formula not in {"all", *_FORMULA_IDS}:
        raise ValueError("formula filter is invalid")
    if state not in {"all", "preparing", "confirmed", "invalidated"}:
        raise ValueError("state filter is invalid")
    if not 1 <= limit <= MAX_V2_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_V2_PAGE_SIZE}")

    manifest = await get_v2_materialized_manifest(
        session,
        universe=universe,
        as_of=as_of,
    )
    if manifest is None:
        return {
            "schema_version": "dual_universe_leader_tactics_screen_v2",
            "experiment_family": "leader_tactics_shadow_v2",
            "universe": universe,
            "formula": formula,
            "state": state,
            "as_of": as_of,
            "manifest_hash": None,
            "manifest_decision_cutoff": None,
            "candidates": [],
            "next_cursor": None,
            "has_more": False,
            "summary": {
                "observation_count": 0,
                "available_count": 0,
                "qualifying_count": 0,
                "returned_count": 0,
                "coverage": "insufficient_data",
                "unavailable_reason": "leader_tactics_v2_not_materialized",
                "exclusion_counts": {},
                "manifest_hash": None,
            },
            "ranking_source_kind": "research_replay",
            "notification_provenance": "none",
            "execution_provenance": "none",
            "research_only": True,
            "production_mutation_allowed": False,
        }

    manifest_hash = str(manifest["manifest_hash"])
    decoded_cursor = _decode_cursor(cursor) if cursor else None
    if decoded_cursor is not None and decoded_cursor["manifest_hash"] != manifest_hash:
        raise ValueError("cursor belongs to a different materialized manifest")

    params: dict[str, Any] = {
        "manifest_hash": manifest_hash,
        "universe": universe,
        "transition_cutoff": _as_date(as_of or manifest["decision_cutoff"]),
        "available": "available",
        "qualifies": True,
        "limit": limit + 1,
    }
    filter_clauses: list[str] = []
    if formula != "all":
        params["formula_id"] = _FORMULA_IDS[formula]
        filter_clauses.append("formula_id = :formula_id")
    if state != "all":
        params["state"] = state
        filter_clauses.append("effective_state = :state")
    filter_where = " AND ".join(filter_clauses) or "1 = 1"
    cursor_clause = ""
    if decoded_cursor is not None:
        params.update(
            {
                "cursor_signal_date": date.fromisoformat(decoded_cursor["signal_date"]),
                "cursor_formula_id": decoded_cursor["formula_id"],
                "cursor_asset_code": decoded_cursor["asset_code"],
            }
        )
        cursor_clause = (
            " AND (signal_date < :cursor_signal_date "
            "OR (signal_date = :cursor_signal_date AND formula_id > :cursor_formula_id) "
            "OR (signal_date = :cursor_signal_date AND formula_id = :cursor_formula_id "
            "AND asset_code > :cursor_asset_code))"
        )
    cte = _candidate_cte(filter_where)

    rows = (
        (
            await session.execute(
                text(
                    f"""
                {cte}
                SELECT manifest_hash, universe, asset_code, asset_name, theme, sector,
                       tracked_index, formula_id, effective_state AS state,
                       availability, qualifies, score, signal_date, transition_date,
                       source_cutoff, gate_facts_json,
                       exclusion_reasons_json, provenance_json, feature_hash
                FROM filtered
                WHERE availability = :available AND qualifies = :qualifies
                {cursor_clause}
                ORDER BY signal_date DESC, formula_id ASC, asset_code ASC
                LIMIT :limit
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    candidates = [
        {
            **dict(row),
            "gate_facts": _decode_json(row["gate_facts_json"], {}),
            "exclusion_reasons": _decode_json(row["exclusion_reasons_json"], []),
            "provenance": _decode_json(row["provenance_json"], {}),
        }
        for row in rows
    ]
    next_cursor = None
    if has_more and candidates:
        last = candidates[-1]
        next_cursor = _encode_cursor(
            signal_date=_as_date(last["signal_date"]),
            formula_id=str(last["formula_id"]),
            asset_code=str(last["asset_code"]),
            manifest_hash=manifest_hash,
        )

    summary_params = {
        key: value
        for key, value in params.items()
        if key != "limit" and not key.startswith("cursor_")
    }
    summary_row = (
        (
            await session.execute(
                text(
                    f"""
                {cte}
                SELECT COUNT(*) AS count,
                       SUM(CASE WHEN availability = :available THEN 1 ELSE 0 END)
                           AS available,
                       SUM(CASE WHEN qualifies = :qualifies THEN 1 ELSE 0 END)
                           AS qualifying,
                       SUM(CASE WHEN availability = :available AND qualifies = :qualifies
                                THEN 1 ELSE 0 END) AS returned
                FROM filtered
                """
                ),
                summary_params,
            )
        )
        .mappings()
        .one()
    )
    count = int(summary_row["count"] or 0)
    available = int(summary_row["available"] or 0)
    qualifying = int(summary_row["qualifying"] or 0)
    returned = int(summary_row["returned"] or 0)

    exclusion_rows = (
        (
            await session.execute(
                text(
                    f"""
                {cte}
                SELECT exclusion_reasons_json
                FROM filtered
                """
                ),
                summary_params,
            )
        )
        .mappings()
        .all()
    )
    exclusion_counts: dict[str, int] = {}
    for row in exclusion_rows:
        reasons = _decode_json(row["exclusion_reasons_json"], [])
        if isinstance(reasons, list):
            for reason in reasons:
                exclusion_counts[str(reason)] = exclusion_counts.get(str(reason), 0) + 1
    manifest_exclusions = _decode_json(manifest["exclusions_json"], {})
    if isinstance(manifest_exclusions, dict):
        for reason, value in manifest_exclusions.items():
            exclusion_counts[str(reason)] = max(exclusion_counts.get(str(reason), 0), int(value))

    return {
        "schema_version": "dual_universe_leader_tactics_screen_v2",
        "experiment_family": "leader_tactics_shadow_v2",
        "universe": universe,
        "formula": formula,
        "state": state,
        "as_of": as_of,
        "manifest_hash": manifest_hash,
        "manifest_decision_cutoff": str(manifest["decision_cutoff"]),
        "candidates": candidates,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "summary": {
            "observation_count": count,
            "available_count": available,
            "qualifying_count": qualifying,
            "returned_count": returned,
            "coverage": "materialized_only" if count else "insufficient_data",
            "unavailable_reason": None if count else "leader_tactics_v2_empty_materialization",
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "manifest_hash": manifest_hash,
        },
        "ranking_source_kind": "research_replay",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "research_only": True,
        "production_mutation_allowed": False,
    }


__all__ = [
    "MAX_V2_PAGE_SIZE",
    "get_v2_materialized_manifest",
    "persist_v2_screen_result",
    "read_v2_candidates",
]
