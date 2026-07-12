from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _unordered(values: list[Any]) -> list[Any]:
    return sorted((_canonical(value) for value in values), key=_canonical_json)


def build_ranking_contract(
    *,
    score_version: str,
    rule_version: str,
    component_manifest: dict[str, Any],
    scope: dict[str, Any],
    universe_snapshot: list[dict[str, Any]],
    input_snapshot: dict[str, Any],
    price_basis: str,
    data_cutoff: date | datetime,
    reliability_policy: dict[str, Any],
) -> dict[str, Any]:
    canonical_scope = dict(scope)
    if isinstance(canonical_scope.get("codes"), list):
        canonical_scope["codes"] = sorted(str(code) for code in canonical_scope["codes"])
    canonical_universe = _unordered(universe_snapshot)
    canonical_reliability = dict(reliability_policy)
    if isinstance(canonical_reliability.get("eligible"), list):
        canonical_reliability["eligible"] = sorted(str(value) for value in canonical_reliability["eligible"])

    payload = {
        "score_version": score_version,
        "rule_version": rule_version,
        "component_manifest": component_manifest,
        "scope": canonical_scope,
        "scope_hash": _hash(canonical_scope),
        "universe_snapshot_hash": _hash(canonical_universe),
        "input_snapshot_hash": _hash(input_snapshot),
        "price_basis": price_basis,
        "data_cutoff": data_cutoff,
        "reliability_policy": canonical_reliability,
    }
    canonical_json = _canonical_json(payload)
    return {
        **_canonical(payload),
        "canonical_json": canonical_json,
        "ranking_contract_hash": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    }
