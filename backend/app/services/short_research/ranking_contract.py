from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

_METADATA_INPUTS = frozenset(
    {
        "source_trade_date",
        "catalyst_effective_at",
        "catalyst_expires_at",
        "catalyst_source",
        "iopv_observed_at",
    }
)
_PRIMITIVE_UNITS = {
    "return_5d": "ratio",
    "return_10d": "ratio",
    "return_20d": "ratio",
    "distance_to_ma20": "ratio",
    "trend_consistency": "ratio",
    "realized_volatility_20d": "ratio",
    "downside_volatility_20d": "ratio",
    "max_drawdown_20d": "ratio",
    "overextension_atr": "ratio",
    "average_turnover_20d": "currency",
    "average_turnover_60d": "currency",
    "spread_bps": "basis_points",
    "structure_quality": "score_0_100",
    "sector_breadth_20d": "score_0_100",
    "sector_momentum_20d": "ratio",
    "sector_turnover_ratio_20_60": "ratio",
    "catalyst_quality": "score_0_100",
    "catalyst_confidence": "score_0_100",
    "premium_discount_bps": "basis_points",
    "premium_provider_consensus": "score_0_100",
    "eligible_peer_count": "count",
    "sector_eligible_peer_count": "count",
    "source_trade_date": "trade_date",
    "catalyst_effective_at": "datetime",
    "catalyst_expires_at": "datetime",
    "catalyst_source": "source_id",
    "iopv_observed_at": "datetime",
}


@dataclass(frozen=True)
class RankingInput:
    asset_code: str
    asset_bucket: str
    price_basis: str
    profile_version: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class RankingComponent:
    component_id: str
    weight: float
    score_bearing: bool
    required_inputs: tuple[str, ...]
    primitive_lineage: tuple[str, ...]
    units: Mapping[str, str]
    missing_data_behavior: str


@dataclass(frozen=True)
class RankingDagEdge:
    source: str
    target: str
    weight: float | None = None


@dataclass(frozen=True)
class RankingInputValidation:
    score_eligible: bool
    missing_by_component: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class RankingManifest:
    score_version: str
    price_basis: str
    components: Mapping[str, RankingComponent]
    asset_buckets: tuple[str, ...]
    missing_data_behavior: str
    explanatory_only: tuple[str, ...]
    dag_edges: tuple[RankingDagEdge, ...]
    acyclic_order: tuple[str, ...]

    def validate_input(self, ranking_input: RankingInput) -> RankingInputValidation:
        missing: dict[str, tuple[str, ...]] = {}
        if ranking_input.asset_bucket not in self.asset_buckets:
            missing["contract"] = ("incompatible_asset_bucket",)
        if ranking_input.price_basis != self.price_basis:
            missing["contract"] = (*missing.get("contract", ()), "incompatible_price_basis")
        if ranking_input.profile_version != self.score_version:
            missing["contract"] = (*missing.get("contract", ()), "incompatible_profile_version")
        for component_id, component in self.components.items():
            if not component.score_bearing:
                continue
            absent = tuple(
                key
                for key in component.required_inputs
                if not _has_usable_input(ranking_input.values.get(key), metadata=key in _METADATA_INPUTS)
            )
            if absent:
                missing[component_id] = absent
        return RankingInputValidation(score_eligible=not missing, missing_by_component=missing)


def _has_usable_input(value: Any, *, metadata: bool) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if metadata:
        return bool(str(value).strip())
    if not isinstance(value, int | float):
        return False
    return math.isfinite(float(value))


def parse_ranking_manifest(contract: Mapping[str, Any]) -> RankingManifest:
    calculation = contract.get("calculation")
    asset_buckets = contract.get("asset_buckets")
    if not isinstance(calculation, Mapping) or not isinstance(asset_buckets, Mapping):
        raise ValueError("ranking contract is missing calculation or asset buckets")
    raw_components = calculation.get("components")
    if not isinstance(raw_components, list):
        raise ValueError("ranking contract components must be a list")
    missing_data_behavior = str(calculation.get("missing_score_bearing_input") or "score_unavailable")
    components: dict[str, RankingComponent] = {}
    for raw_component in raw_components:
        if not isinstance(raw_component, Mapping):
            raise ValueError("ranking component must be an object")
        component_id = str(raw_component.get("id") or "")
        required_inputs = tuple(str(value) for value in raw_component.get("required_inputs") or [])
        primitive_lineage = tuple(str(value) for value in raw_component.get("primitive_lineage") or [])
        if not component_id or component_id in components or not required_inputs:
            raise ValueError("ranking component id and required inputs must be unique and non-empty")
        components[component_id] = RankingComponent(
            component_id=component_id,
            weight=float(raw_component.get("weight") or 0.0),
            score_bearing=bool(raw_component.get("score_bearing")),
            required_inputs=required_inputs,
            primitive_lineage=primitive_lineage,
            units={key: _PRIMITIVE_UNITS.get(key, "unknown") for key in required_inputs},
            missing_data_behavior=missing_data_behavior,
        )
    dag = calculation.get("dag")
    if not isinstance(dag, Mapping):
        raise ValueError("ranking contract DAG must be an object")
    dag_edges: list[RankingDagEdge] = []
    for raw_edge in [*dag.get("component_edges", []), *dag.get("terminal_edges", [])]:
        if not isinstance(raw_edge, Mapping):
            raise ValueError("ranking DAG edge must be an object")
        source = str(raw_edge.get("from") or "")
        target = str(raw_edge.get("to") or "")
        if not source or not target:
            raise ValueError("ranking DAG edges require source and target")
        weight = raw_edge.get("weight")
        dag_edges.append(RankingDagEdge(source=source, target=target, weight=float(weight) if weight is not None else None))
    return RankingManifest(
        score_version=str(contract.get("contract_id") or ""),
        price_basis=str(calculation.get("price_basis") or ""),
        components=components,
        asset_buckets=tuple(str(value) for value in asset_buckets.get("comparable_bucket_ids") or []),
        missing_data_behavior=missing_data_behavior,
        explanatory_only=tuple(str(value) for value in contract.get("explanatory_only") or []),
        dag_edges=tuple(dag_edges),
        acyclic_order=tuple(str(value) for value in dag.get("acyclic_order") or []),
    )


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


def canonical_hash(value: Any) -> str:
    return _hash(value)


def _unordered(values: list[Any]) -> list[Any]:
    return sorted((_canonical(value) for value in values), key=_canonical_json)


def scope_kind_for_filters(*, theme: str | None, codes: list[str] | None) -> str:
    if any(code.strip() for code in codes or []):
        return "codes"
    if theme and theme.strip():
        return "theme"
    return "full"


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
