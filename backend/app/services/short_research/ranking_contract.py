from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
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

_REQUIRED_STRATEGY_SEMANTICS = frozenset(
    {
        "schema_version",
        "contract_id",
        "rule_version",
        "selector",
        "calculation",
        "freshness",
        "reliability",
        "eligibility",
        "hard_limits",
        "asset_buckets",
        "ordering",
    }
)
_VALID_PRIMITIVE_DIRECTIONS = frozenset(
    {
        "higher_is_better",
        "lower_is_better",
        "lower_absolute_loss_is_better",
        "closer_to_zero_is_better",
    }
)
_VALID_PRIMITIVE_NORMALIZATIONS = frozenset(
    {
        "bucket_percentile",
        "winsorized_bucket_percentile",
        "absolute_distance_bucket_percentile",
        "bounded_0_100",
    }
)


@dataclass(frozen=True)
class RankingInput:
    asset_code: str
    asset_bucket: str
    price_basis: str
    profile_version: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class RankingPrimitive:
    primitive_id: str
    weight: float
    direction: str
    normalization: str


@dataclass(frozen=True)
class RankingComponent:
    component_id: str
    weight: float
    score_bearing: bool
    required_inputs: tuple[str, ...]
    primitive_lineage: tuple[str, ...]
    primitive_inputs: tuple[RankingPrimitive, ...]
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
    minimum_peer_count: int
    clone_activation_minimum_coverage: float

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
    if missing_data_behavior != "score_unavailable":
        raise ValueError("score-bearing missing inputs must make the score unavailable")
    if calculation.get("renormalize_missing_weights") is not False:
        raise ValueError("ranking contract must explicitly forbid missing-weight renormalization")
    components: dict[str, RankingComponent] = {}
    score_bearing_primitives: dict[str, str] = {}
    for raw_component in raw_components:
        if not isinstance(raw_component, Mapping):
            raise ValueError("ranking component must be an object")
        component_id = str(raw_component.get("id") or "")
        required_inputs = tuple(str(value) for value in raw_component.get("required_inputs") or [])
        primitive_lineage = tuple(str(value) for value in raw_component.get("primitive_lineage") or [])
        formula = raw_component.get("formula")
        raw_primitive_inputs = formula.get("inputs") if isinstance(formula, Mapping) else None
        if not component_id or component_id in components or not required_inputs or not isinstance(raw_primitive_inputs, list):
            raise ValueError("ranking component id and required inputs must be unique and non-empty")
        primitive_inputs = tuple(
            RankingPrimitive(
                primitive_id=str(raw_input.get("primitive") or ""),
                weight=float(raw_input.get("weight") or 0.0),
                direction=str(raw_input.get("direction") or ""),
                normalization=str(raw_input.get("normalization") or ""),
            )
            for raw_input in raw_primitive_inputs
            if isinstance(raw_input, Mapping)
        )
        if len(primitive_inputs) != len(raw_primitive_inputs) or any(not item.primitive_id for item in primitive_inputs):
            raise ValueError(f"ranking component {component_id} has invalid primitive inputs")
        component = RankingComponent(
            component_id=component_id,
            weight=float(raw_component.get("weight") or 0.0),
            score_bearing=bool(raw_component.get("score_bearing")),
            required_inputs=required_inputs,
            primitive_lineage=primitive_lineage,
            primitive_inputs=primitive_inputs,
            units={key: _PRIMITIVE_UNITS.get(key, "unknown") for key in required_inputs},
            missing_data_behavior=missing_data_behavior,
        )
        if not 0.0 <= component.weight <= 1.0:
            raise ValueError(f"ranking component {component_id} weight must be within [0, 1]")
        if not component.score_bearing and component.weight != 0.0:
            raise ValueError(f"explanatory component {component_id} must have zero weight")
        if any(not 0.0 <= primitive.weight <= 1.0 for primitive in primitive_inputs):
            raise ValueError(f"ranking component {component_id} primitive weights must be within [0, 1]")
        if any(primitive.direction not in _VALID_PRIMITIVE_DIRECTIONS for primitive in primitive_inputs):
            raise ValueError(f"ranking component {component_id} has undeclared primitive direction")
        if any(
            primitive.normalization not in _VALID_PRIMITIVE_NORMALIZATIONS
            for primitive in primitive_inputs
        ):
            raise ValueError(f"ranking component {component_id} has undeclared normalization")
        if not math.isclose(
            sum(primitive.weight for primitive in primitive_inputs),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError(f"ranking component {component_id} primitive weights must sum to 1")
        primitive_ids = tuple(primitive.primitive_id for primitive in primitive_inputs)
        if len(set(primitive_ids)) != len(primitive_ids):
            raise ValueError(f"ranking component {component_id} has duplicate primitive inputs")
        if component.score_bearing:
            for primitive in component.primitive_lineage:
                prior_component = score_bearing_primitives.get(primitive)
                if prior_component is not None:
                    raise ValueError(
                        f"double-counted primitive {primitive} in {prior_component} and {component.component_id}"
                    )
                score_bearing_primitives[primitive] = component.component_id
        if set(primitive_ids) != set(primitive_lineage):
            raise ValueError(f"ranking component {component_id} primitive lineage must match formula inputs")
        components[component_id] = component
    score_bearing_weight = sum(
        component.weight for component in components.values() if component.score_bearing
    )
    if not math.isclose(score_bearing_weight, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("score-bearing ranking component weights must sum to 1")
    component_ids = set(components)
    composite_primitives = component_ids.intersection(score_bearing_primitives)
    if composite_primitives:
        raise ValueError(f"score-bearing component cannot be weighted as a primitive: {sorted(composite_primitives)}")
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
    acyclic_order = tuple(str(value) for value in dag.get("acyclic_order") or [])
    if not acyclic_order or len(set(acyclic_order)) != len(acyclic_order):
        raise ValueError("ranking DAG acyclic order must contain unique nodes")
    node_positions = {node: index for index, node in enumerate(acyclic_order)}
    seen_edges: set[tuple[str, str]] = set()
    for edge in dag_edges:
        edge_key = (edge.source, edge.target)
        if edge_key in seen_edges:
            raise ValueError(f"ranking DAG contains duplicate edge {edge.source}->{edge.target}")
        seen_edges.add(edge_key)
        if edge.source not in node_positions or edge.target not in node_positions:
            raise ValueError(f"ranking DAG edge references undeclared node {edge.source}->{edge.target}")
        if node_positions[edge.source] >= node_positions[edge.target]:
            raise ValueError(f"ranking DAG is cyclic or out of order at {edge.source}->{edge.target}")
    minimum_peer_count = asset_buckets.get("minimum_eligible_non_clone_peers", 2)
    if isinstance(minimum_peer_count, bool) or not isinstance(minimum_peer_count, int) or minimum_peer_count < 2:
        raise ValueError("minimum eligible non-clone peer count must be an integer of at least 2")
    clone_activation_minimum_coverage = asset_buckets.get(
        "clone_activation_minimum_underlying_coverage",
        1.0,
    )
    if (
        isinstance(clone_activation_minimum_coverage, bool)
        or not isinstance(clone_activation_minimum_coverage, int | float)
        or not 0.0 <= float(clone_activation_minimum_coverage) <= 1.0
    ):
        raise ValueError("clone activation minimum coverage must be within [0, 1]")
    return RankingManifest(
        score_version=str(contract.get("contract_id") or ""),
        price_basis=str(calculation.get("price_basis") or ""),
        components=components,
        asset_buckets=tuple(str(value) for value in asset_buckets.get("comparable_bucket_ids") or []),
        missing_data_behavior=missing_data_behavior,
        explanatory_only=tuple(str(value) for value in contract.get("explanatory_only") or []),
        dag_edges=tuple(dag_edges),
        acyclic_order=acyclic_order,
        minimum_peer_count=minimum_peer_count,
        clone_activation_minimum_coverage=float(clone_activation_minimum_coverage),
    )


def _final_score_v3_contract_path() -> Path:
    return Path(__file__).with_name("final-score-v3-contract.json")


@lru_cache(maxsize=1)
def final_score_v3_contract() -> Mapping[str, Any]:
    loaded: object = json.loads(_final_score_v3_contract_path().read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("ranking contract must be a JSON object")
    return {str(key): value for key, value in loaded.items()}


def ranking_strategy_semantics(contract: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(_REQUIRED_STRATEGY_SEMANTICS - set(contract))
    if missing:
        raise ValueError(f"ranking strategy semantics are incomplete: {', '.join(missing)}")
    return {str(key): value for key, value in contract.items()}


@lru_cache(maxsize=1)
def final_score_v3_manifest() -> RankingManifest:
    return parse_ranking_manifest(final_score_v3_contract())


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
