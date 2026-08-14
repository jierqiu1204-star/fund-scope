"""Append-only persistence and read contracts for late-day turnaround evidence."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.late_day_turnaround_shadow import (
    CONTRACT_HASH,
    POLICY_MODE,
    STRATEGY_VERSION,
)

Universe = Literal["etf", "ashare"]

NO_COMPLETED_MANIFEST = "late_day_turnaround_not_materialized"
API_DISABLED = "late_day_turnaround_api_disabled"
MINUTE_DATA_UNAVAILABLE = "minute_data_unavailable"
INCOMPLETE_DECLARED_COVERAGE = "incomplete_declared_coverage"
INPUT_BUDGET_EXCEEDED = "input_budget_exceeded"
JOB_TIMEOUT = "job_timeout"

def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}



def unavailable_payload(
    *, universe: Universe, reason: str, as_of: datetime | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "late_day_turnaround_evidence.v1",
        "strategy_version": STRATEGY_VERSION,
        "contract_hash": CONTRACT_HASH,
        "policy_mode": POLICY_MODE,
        "universe": universe,
        "decision_at": as_of.isoformat() if as_of else None,
        "manifest_hash": None,
        "status": "unavailable",
        "summary": {
            "expected_count": 0,
            "evaluated_count": 0,
            "available_count": 0,
            "qualifying_count": 0,
            "coverage_ratio": 0.0,
            "exclusion_counts": {},
            "unavailable_reason": reason,
        },
        "items": [],
        "next_cursor": None,
        "has_more": False,
        "ranking_source_kind": "research_shadow",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "research_only": True,
        "production_mutation_allowed": False,
    }


async def persist_run(
    session: AsyncSession,
    *,
    universe: Universe,
    decision_at: datetime,
    status: str,
    universe_codes: Sequence[str],
    observations: Sequence[dict[str, Any]],
    expected_count: int,
    provider_health: dict[str, Any],
    unavailable_reason: str | None = None,
) -> str:
    """Persist one immutable run. Repeating identical evidence is idempotent."""

    ordered_codes = tuple(sorted(set(universe_codes)))
    ordered_observations = tuple(
        sorted(observations, key=lambda row: (str(row["asset_code"]), str(row["reason"])))
    )
    universe_hash = stable_contract_hash({"universe": universe, "codes": ordered_codes})
    input_hash = stable_contract_hash(
        {
            "universe": universe,
            "decision_at": decision_at.isoformat(),
            "observations": ordered_observations,
        }
    )
    manifest_hash = stable_contract_hash(
        {
            "strategy_version": STRATEGY_VERSION,
            "contract_hash": CONTRACT_HASH,
            "universe": universe,
            "decision_at": decision_at.isoformat(),
            "universe_hash": universe_hash,
            "input_hash": input_hash,
            "status": status,
        }
    )
    exclusion_counts = Counter(
        str(item["reason"])
        for item in ordered_observations
        if item.get("reason") not in {None, "ok"}
    )
    available_count = sum(bool(item.get("available")) for item in ordered_observations)
    qualifying_count = sum(
        item.get("observation_kind") == "formal_candidate" for item in ordered_observations
    )
    run_result = await session.execute(
        text(
            """
            INSERT INTO late_day_turnaround_runs
                (manifest_hash, universe, signal_date, decision_at, status,
                 policy_mode, strategy_version, contract_hash, universe_hash,
                 input_hash, expected_count, evaluated_count, available_count,
                 qualifying_count, provider_health_json, exclusion_counts_json,
                 unavailable_reason, created_at)
            VALUES
                (:manifest_hash, :universe, :signal_date, :decision_at, :status,
                 :policy_mode, :strategy_version, :contract_hash, :universe_hash,
                 :input_hash, :expected_count, :evaluated_count, :available_count,
                 :qualifying_count, :provider_health, :exclusion_counts,
                 :unavailable_reason, :created_at)
            ON CONFLICT DO NOTHING
            RETURNING id
            """
        ),
        {
            "manifest_hash": manifest_hash,
            "universe": universe,
            "signal_date": decision_at.date(),
            "decision_at": decision_at.replace(tzinfo=None),
            "status": status,
            "policy_mode": POLICY_MODE,
            "strategy_version": STRATEGY_VERSION,
            "contract_hash": CONTRACT_HASH,
            "universe_hash": universe_hash,
            "input_hash": input_hash,
            "expected_count": expected_count,
            "evaluated_count": len(ordered_observations),
            "available_count": available_count,
            "qualifying_count": qualifying_count,
            "provider_health": json.dumps(provider_health, sort_keys=True),
            "exclusion_counts": json.dumps(dict(exclusion_counts), sort_keys=True),
            "unavailable_reason": unavailable_reason,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
        },
    )
    run_id = run_result.scalar_one_or_none()
    if run_id is None:
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id, manifest_hash
                    FROM late_day_turnaround_runs
                    WHERE manifest_hash = :manifest_hash
                       OR (universe = :universe
                           AND decision_at = :decision_at
                           AND contract_hash = :contract_hash)
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {
                    "manifest_hash": manifest_hash,
                    "universe": universe,
                    "decision_at": decision_at.replace(tzinfo=None),
                    "contract_hash": CONTRACT_HASH,
                },
            )
        ).mappings().first()
        if existing is None:
            raise RuntimeError("late-day run identity was not persisted")
        if existing["manifest_hash"] != manifest_hash:
            await session.rollback()
            return str(existing["manifest_hash"])
        run_id = int(existing["id"])

    for item in ordered_observations:
        await session.execute(
            text(
                """
                INSERT INTO late_day_turnaround_observations
                    (run_id, asset_code, asset_name, observation_kind, available,
                     reason, score, ma5, gain_pct, ma_deviation_pct, amount_ratio,
                     signal_date, decision_at, input_hash, provenance_json, created_at)
                VALUES
                    (:run_id, :asset_code, :asset_name, :observation_kind, :available,
                     :reason, :score, :ma5, :gain_pct, :ma_deviation_pct, :amount_ratio,
                     :signal_date, :decision_at, :input_hash, :provenance, :created_at)
                ON CONFLICT(run_id, asset_code) DO NOTHING
                """
            ),
            {
                "run_id": run_id,
                "asset_code": item["asset_code"],
                "asset_name": item.get("asset_name") or item["asset_code"],
                "observation_kind": item["observation_kind"],
                "available": bool(item.get("available")),
                "reason": item.get("reason") or "invalid_input",
                "score": item.get("score"),
                "ma5": item.get("ma5"),
                "gain_pct": item.get("gain_pct"),
                "ma_deviation_pct": item.get("ma_deviation_pct"),
                "amount_ratio": item.get("amount_ratio"),
                "signal_date": decision_at.date(),
                "decision_at": decision_at.replace(tzinfo=None),
                "input_hash": stable_contract_hash(item),
                "provenance": json.dumps(item.get("provenance") or {}, sort_keys=True),
                "created_at": datetime.now(UTC).replace(tzinfo=None),
            },
        )
    await session.commit()
    return manifest_hash


async def read_latest_evidence(
    session: AsyncSession,
    *,
    universe: Universe,
    as_of: datetime | None = None,
    observation_kind: str = "formal_candidate",
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    if observation_kind not in {"formal_candidate", "daily_proxy_watchlist"}:
        raise ValueError("invalid observation kind")
    if cursor < 0 or not 1 <= limit <= 100:
        raise ValueError("invalid pagination")
    cutoff_clause = " AND decision_at <= :as_of" if as_of is not None else ""
    params: dict[str, Any] = {
        "universe": universe,
        "contract_hash": CONTRACT_HASH,
    }
    if as_of is not None:
        params["as_of"] = as_of.replace(tzinfo=None)
    run = (
        await session.execute(
            text(
                f"""
                SELECT * FROM late_day_turnaround_runs
                WHERE universe = :universe
                  AND contract_hash = :contract_hash
                  {cutoff_clause}
                ORDER BY decision_at DESC, id DESC
                LIMIT 1
                """
            ),
            params,
        )
    ).mappings().first()
    if run is None:
        return unavailable_payload(universe=universe, reason=NO_COMPLETED_MANIFEST, as_of=as_of)

    item_rows = (
        await session.execute(
            text(
                """
                SELECT asset_code, asset_name, observation_kind, available, reason,
                       score, ma5, gain_pct, ma_deviation_pct, amount_ratio,
                       signal_date, decision_at, provenance_json
                FROM late_day_turnaround_observations
                WHERE run_id = :run_id AND observation_kind = :observation_kind
                ORDER BY score DESC, asset_code ASC
                LIMIT :fetch_limit OFFSET :cursor
                """
            ),
            {
                "run_id": run["id"],
                "observation_kind": observation_kind,
                "fetch_limit": limit + 1,
                "cursor": cursor,
            },
        )
    ).mappings().all()
    has_more = len(item_rows) > limit
    items = [dict(row) for row in item_rows[:limit]]
    for item in items:
        for key in ("signal_date", "decision_at"):
            if hasattr(item.get(key), "isoformat"):
                item[key] = item[key].isoformat()
        item["provenance_json"] = _json_object(item.get("provenance_json"))
    expected = int(run["expected_count"] or 0)
    evaluated = int(run["evaluated_count"] or 0)
    return {
        "schema_version": "late_day_turnaround_evidence.v1",
        "strategy_version": run["strategy_version"],
        "contract_hash": run["contract_hash"],
        "policy_mode": run["policy_mode"],
        "universe": universe,
        "decision_at": (
            run["decision_at"].isoformat()
            if hasattr(run["decision_at"], "isoformat")
            else str(run["decision_at"])
        ),
        "manifest_hash": run["manifest_hash"],
        "status": run["status"],
        "summary": {
            "expected_count": expected,
            "evaluated_count": evaluated,
            "available_count": int(run["available_count"] or 0),
            "qualifying_count": int(run["qualifying_count"] or 0),
            "coverage_ratio": round(evaluated / expected, 6) if expected else 0.0,
            "exclusion_counts": _json_object(run["exclusion_counts_json"]),
            "unavailable_reason": run["unavailable_reason"],
        },
        "items": items,
        "next_cursor": cursor + limit if has_more else None,
        "has_more": has_more,
        "ranking_source_kind": "research_shadow",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "research_only": True,
        "production_mutation_allowed": False,
    }


__all__ = [
    "API_DISABLED",
    "INCOMPLETE_DECLARED_COVERAGE",
    "INPUT_BUDGET_EXCEEDED",
    "JOB_TIMEOUT",
    "MINUTE_DATA_UNAVAILABLE",
    "NO_COMPLETED_MANIFEST",
    "Universe",
    "persist_run",
    "read_latest_evidence",
    "unavailable_payload",
]
