from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import mean
from typing import Any

from app.services.short_research.ranking import apply_final_score_limits
from app.services.short_research.ranking_contract import (
    RankingInput,
    RankingManifest,
    RankingPrimitive,
    final_score_v3_manifest,
)

_COMPARABLE_BUCKETS = {
    "broad_base": "broad-equity",
    "equity": "sector/theme-equity",
    "bond": "fixed-income",
    "money": "fixed-income",
    "commodity": "commodity",
    "cross_border": "cross-border",
}
_DECISION_RELIABILITIES = {"verified", "alternate_provider"}
_SHARED_PRICE_PRIMITIVES = frozenset(
    {
        "return_5d",
        "return_10d",
        "return_20d",
        "distance_to_ma20",
        "trend_consistency",
        "realized_volatility_20d",
        "downside_volatility_20d",
        "max_drawdown_20d",
        "overextension_atr",
        "sector_breadth_20d",
        "sector_momentum_20d",
        "sector_turnover_ratio_20_60",
    }
)


@dataclass(frozen=True)
class FinalScoreV3Result:
    asset_bucket: str
    ranking_score: float | None
    score_eligible: bool
    component_scores: Mapping[str, float]
    missing_by_component: Mapping[str, tuple[str, ...]]
    metric_peer_counts: Mapping[str, int]
    limitation_reasons: tuple[str, ...]
    weakest_required_primitive_peer_count: int = 0
    clone_policy_active: bool = False
    tracked_underlying_coverage: float = 0.0
    cap_violation: bool = False
    non_finite_reject: bool = False
    clone_group_id: str | None = None
    diversified_representative: bool | None = None


def enforce_history_warmup(
    result: FinalScoreV3Result,
    *,
    usable_sessions: int,
    required_sessions: int = 61,
) -> FinalScoreV3Result:
    if usable_sessions >= required_sessions:
        return result
    reason = "insufficient_decision_eligible_adjusted_sessions"
    return replace(
        result,
        ranking_score=None,
        score_eligible=False,
        missing_by_component={"history_depth_61": (reason,)},
        limitation_reasons=(f"history_depth_61:{reason}",),
    )


def final_score_v3_bucket(theme_bucket: Any) -> str | None:
    return _COMPARABLE_BUCKETS.get(str(theme_bucket or ""))


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _clone_key(ranking_input: RankingInput, *, clone_policy_active: bool = True) -> str:
    tracked_underlying = ranking_input.values.get("tracked_underlying_id")
    return (
        f"underlying:{tracked_underlying}"
        if clone_policy_active and tracked_underlying
        else f"asset:{ranking_input.asset_code}"
    )


def _percentile(value: float, values: Sequence[float], *, higher_is_better: bool) -> float | None:
    if len(values) < 2:
        return None
    ordered = sorted(values)
    less = sum(item < value for item in ordered)
    equal = sum(item == value for item in ordered)
    # Empirical midrank remains defined when ``value`` is not itself one of
    # the clone-group observations. Values below/above the sample map to the
    # closed endpoints instead of escaping the declared score range.
    result = (less + equal / 2) / len(ordered) * 100
    bounded = max(0.0, min(100.0, result))
    return 100 - bounded if not higher_is_better else bounded


def _quantile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _primitive_score(
    primitive: RankingPrimitive, value: float, values: Sequence[float]
) -> float | None:
    normalization = primitive.normalization
    transformed_value = value
    transformed_values = list(values)
    higher_is_better = primitive.direction == "higher_is_better"
    if primitive.direction in {"lower_absolute_loss_is_better", "closer_to_zero_is_better"}:
        transformed_value = abs(value)
        transformed_values = [abs(item) for item in values]
        higher_is_better = False
    elif primitive.direction == "lower_is_better":
        higher_is_better = False
    if normalization == "bounded_0_100":
        return max(0.0, min(100.0, transformed_value))
    if normalization.startswith("winsorized_"):
        lower = _quantile(transformed_values, 0.05)
        upper = _quantile(transformed_values, 0.95)
        transformed_value = max(lower, min(upper, transformed_value))
        transformed_values = [max(lower, min(upper, item)) for item in transformed_values]
    return _percentile(transformed_value, transformed_values, higher_is_better=higher_is_better)


def _reliability_missing(
    ranking_input: RankingInput,
    manifest: RankingManifest,
    missing_by_component: dict[str, tuple[str, ...]],
) -> None:
    if ranking_input.values.get("quality_gate_rejected") is True:
        for component_id, component in manifest.components.items():
            if component.score_bearing:
                missing_by_component[component_id] = (
                    *missing_by_component.get(component_id, ()),
                    "quality_gate_rejected",
                )
        return
    reliabilities = ranking_input.values.get("component_reliability")
    if not isinstance(reliabilities, Mapping):
        reliabilities = {}
    for component_id, component in manifest.components.items():
        if not component.score_bearing:
            continue
        reliability = str(reliabilities.get(component_id) or "unavailable")
        if reliability in _DECISION_RELIABILITIES:
            continue
        missing_by_component[component_id] = (
            *missing_by_component.get(component_id, ()),
            "unreliable_input",
        )


def _cluster_distributions(
    inputs: Sequence[RankingInput],
    manifest: RankingManifest,
    *,
    clone_policy_active: bool,
) -> dict[str, dict[str, list[float]]]:
    primitive_components = {
        primitive.primitive_id: component_id
        for component_id, component in manifest.components.items()
        for primitive in component.primitive_inputs
        if component.score_bearing
    }
    clusters: dict[str, dict[str, list[RankingInput]]] = defaultdict(lambda: defaultdict(list))
    for ranking_input in inputs:
        if ranking_input.asset_bucket in manifest.asset_buckets:
            clusters[ranking_input.asset_bucket][
                _clone_key(ranking_input, clone_policy_active=clone_policy_active)
            ].append(ranking_input)
    distributions: dict[str, dict[str, list[float]]] = {}
    for bucket, clone_groups in clusters.items():
        bucket_distributions: dict[str, list[float]] = {}
        for primitive_id, component_id in primitive_components.items():
            values: list[float] = []
            for clone_inputs in clone_groups.values():
                clone_values = [
                    _numeric(item.values.get(primitive_id))
                    for item in clone_inputs
                    if item.values.get("quality_gate_rejected") is not True
                    and isinstance(item.values.get("component_reliability"), Mapping)
                    and str(item.values["component_reliability"].get(component_id) or "unavailable")
                    in _DECISION_RELIABILITIES
                ]
                usable = [item for item in clone_values if item is not None]
                if usable:
                    if clone_policy_active and primitive_id in _SHARED_PRICE_PRIMITIVES:
                        values.append(mean(usable))
                    else:
                        values.extend(usable)
            bucket_distributions[primitive_id] = values
        distributions[bucket] = bucket_distributions
    return distributions


def _shared_clone_price_values(
    inputs: Sequence[RankingInput],
    manifest: RankingManifest,
    *,
    clone_policy_active: bool,
) -> dict[str, dict[str, float]]:
    if not clone_policy_active:
        return {}
    primitive_components = {
        primitive.primitive_id: component_id
        for component_id, component in manifest.components.items()
        for primitive in component.primitive_inputs
        if component.score_bearing and primitive.primitive_id in _SHARED_PRICE_PRIMITIVES
    }
    groups: dict[tuple[str, str], list[RankingInput]] = defaultdict(list)
    for ranking_input in inputs:
        if ranking_input.asset_bucket in manifest.asset_buckets:
            groups[
                (
                    ranking_input.asset_bucket,
                    _clone_key(ranking_input, clone_policy_active=True),
                )
            ].append(ranking_input)
    shared: dict[str, dict[str, float]] = defaultdict(dict)
    for members in groups.values():
        for primitive_id, component_id in primitive_components.items():
            values = [
                _numeric(member.values.get(primitive_id))
                for member in members
                if member.values.get("quality_gate_rejected") is not True
                and isinstance(member.values.get("component_reliability"), Mapping)
                and str(member.values["component_reliability"].get(component_id) or "unavailable")
                in _DECISION_RELIABILITIES
            ]
            usable = [value for value in values if value is not None]
            if not usable:
                continue
            group_value = mean(usable)
            for member in members:
                shared[member.asset_code][primitive_id] = group_value
    return dict(shared)


def _clone_representatives(inputs: Sequence[RankingInput]) -> dict[str, str]:
    """Choose one display representative without changing canonical rank identity."""

    groups: dict[str, list[RankingInput]] = defaultdict(list)
    for ranking_input in inputs:
        groups[_clone_key(ranking_input, clone_policy_active=True)].append(ranking_input)

    def sort_key(ranking_input: RankingInput) -> tuple[float, float, float, float, str]:
        turnover = _numeric(ranking_input.values.get("average_turnover_20d"))
        structure = _numeric(ranking_input.values.get("structure_quality"))
        spread = _numeric(ranking_input.values.get("spread_bps"))
        premium = _numeric(ranking_input.values.get("premium_discount_bps"))
        return (
            -(turnover if turnover is not None else -math.inf),
            -(structure if structure is not None else -math.inf),
            spread if spread is not None else math.inf,
            abs(premium) if premium is not None else math.inf,
            ranking_input.asset_code,
        )

    return {group_id: min(members, key=sort_key).asset_code for group_id, members in groups.items()}


def _input_with_derived_peer_count(
    ranking_input: RankingInput,
    manifest: RankingManifest,
    bucket_distributions: Mapping[str, Sequence[float]],
) -> RankingInput:
    peer_primitives = {
        primitive.primitive_id
        for component in manifest.components.values()
        if component.score_bearing and "eligible_peer_count" in component.required_inputs
        for primitive in component.primitive_inputs
    }
    peer_counts = [
        len(bucket_distributions.get(primitive_id, ())) for primitive_id in peer_primitives
    ]
    eligible_peer_count = (
        max(peer_counts)
        if peer_counts and max(peer_counts) >= manifest.minimum_peer_count
        else None
    )
    return RankingInput(
        asset_code=ranking_input.asset_code,
        asset_bucket=ranking_input.asset_bucket,
        price_basis=ranking_input.price_basis,
        profile_version=ranking_input.profile_version,
        values={**ranking_input.values, "eligible_peer_count": eligible_peer_count},
    )


def _weakest_required_primitive_peer_count(
    manifest: RankingManifest,
    bucket_distributions: Mapping[str, Sequence[float]],
) -> int:
    peer_primitives = {
        primitive.primitive_id
        for component in manifest.components.values()
        if component.score_bearing and "eligible_peer_count" in component.required_inputs
        for primitive in component.primitive_inputs
    }
    counts = [len(bucket_distributions.get(primitive_id, ())) for primitive_id in peer_primitives]
    return min(counts) if counts else 0


def build_final_score_v3_sector_inputs(inputs: Sequence[RankingInput]) -> dict[str, dict[str, Any]]:
    grouped: dict[tuple[str, str], list[RankingInput]] = defaultdict(list)
    for ranking_input in inputs:
        theme_group = str(ranking_input.values.get("theme_group") or "").strip()
        if ranking_input.asset_bucket and theme_group and theme_group != "unknown":
            grouped[(ranking_input.asset_bucket, theme_group)].append(ranking_input)
    payloads: dict[str, dict[str, Any]] = {}
    for members in grouped.values():
        clone_groups: dict[str, list[RankingInput]] = defaultdict(list)
        for member in members:
            clone_groups[_clone_key(member)].append(member)
        peer_values: list[tuple[float, float]] = []
        source_dates: set[str] = set()
        reliabilities: set[str] = set()
        for clones in clone_groups.values():
            eligible_clones = [
                member
                for member in clones
                if member.values.get("quality_gate_rejected") is not True
            ]
            returns = [_numeric(member.values.get("return_20d")) for member in eligible_clones]
            turnovers_20 = [
                _numeric(member.values.get("average_turnover_20d")) for member in eligible_clones
            ]
            turnovers_60 = [
                _numeric(member.values.get("average_turnover_60d")) for member in eligible_clones
            ]
            sources = {
                str(member.values.get("source_trade_date") or "") for member in eligible_clones
            }
            clone_reliabilities = {
                str(member.values.get("market_data_reliability") or "unavailable")
                for member in eligible_clones
            }
            if (
                not sources
                or "" in sources
                or len(sources) != 1
                or not clone_reliabilities.issubset(_DECISION_RELIABILITIES)
                or any(value is None for value in returns + turnovers_20 + turnovers_60)
            ):
                continue
            turnover_60 = mean(value for value in turnovers_60 if value is not None)
            if turnover_60 <= 0:
                continue
            source_dates.update(sources)
            reliabilities.update(clone_reliabilities)
            peer_values.append(
                (
                    mean(value for value in returns if value is not None),
                    mean(value for value in turnovers_20 if value is not None) / turnover_60,
                )
            )
        peer_count = len(peer_values)
        available = (
            peer_count >= 2
            and len(source_dates) == 1
            and reliabilities.issubset(_DECISION_RELIABILITIES)
        )
        payload = {
            "sector_breadth_20d": round(
                sum(value > 0 for value, _ in peer_values) / peer_count * 100, 4
            )
            if available
            else None,
            "sector_momentum_20d": round(mean(value for value, _ in peer_values), 8)
            if available
            else None,
            "sector_turnover_ratio_20_60": round(mean(value for _, value in peer_values), 8)
            if available
            else None,
            "sector_eligible_peer_count": peer_count if available else None,
            "sector_input_status": "alternate_provider"
            if available and "alternate_provider" in reliabilities
            else "verified"
            if available
            else "unavailable",
            "sector_source_trade_date": next(iter(source_dates)) if available else None,
        }
        for member in members:
            payloads[member.asset_code] = payload
    return payloads


def score_final_score_v3(
    inputs: Sequence[RankingInput],
    *,
    manifest: RankingManifest,
) -> dict[str, FinalScoreV3Result]:
    resolved_underlying_count = sum(
        bool(str(item.values.get("tracked_underlying_id") or "").strip()) for item in inputs
    )
    tracked_underlying_coverage = resolved_underlying_count / len(inputs) if inputs else 0.0
    clone_policy_active = (
        bool(inputs) and tracked_underlying_coverage >= manifest.clone_activation_minimum_coverage
    )
    distributions = _cluster_distributions(
        inputs,
        manifest,
        clone_policy_active=clone_policy_active,
    )
    shared_clone_values = _shared_clone_price_values(
        inputs,
        manifest,
        clone_policy_active=clone_policy_active,
    )
    representative_by_group = _clone_representatives(inputs) if clone_policy_active else {}
    results: dict[str, FinalScoreV3Result] = {}
    for ranking_input in inputs:
        bucket_distributions = distributions.get(ranking_input.asset_bucket, {})
        clone_values = shared_clone_values.get(ranking_input.asset_code, {})
        clone_adjusted_input = RankingInput(
            asset_code=ranking_input.asset_code,
            asset_bucket=ranking_input.asset_bucket,
            price_basis=ranking_input.price_basis,
            profile_version=ranking_input.profile_version,
            values={**ranking_input.values, **clone_values},
        )
        effective_input = _input_with_derived_peer_count(
            clone_adjusted_input,
            manifest,
            bucket_distributions,
        )
        validation = manifest.validate_input(effective_input)
        missing_by_component = dict(validation.missing_by_component)
        _reliability_missing(effective_input, manifest, missing_by_component)
        metric_peer_counts = {key: len(values) for key, values in bucket_distributions.items()}
        component_scores: dict[str, float] = {}
        cap_violation = False
        non_finite_reject = False
        for component_id, component in manifest.components.items():
            if not component.score_bearing or component_id in missing_by_component:
                continue
            primitive_scores: list[tuple[RankingPrimitive, float]] = []
            for primitive in component.primitive_inputs:
                value = _numeric(effective_input.values.get(primitive.primitive_id))
                score = (
                    _primitive_score(
                        primitive, value, bucket_distributions.get(primitive.primitive_id, [])
                    )
                    if value is not None
                    else None
                )
                if score is None:
                    missing_by_component[component_id] = (
                        *missing_by_component.get(component_id, ()),
                        "insufficient_bucket_peers",
                    )
                    primitive_scores = []
                    break
                if not math.isfinite(score):
                    non_finite_reject = True
                    missing_by_component[component_id] = (
                        *missing_by_component.get(component_id, ()),
                        "non_finite_primitive_score",
                    )
                    primitive_scores = []
                    break
                if not 0.0 <= score <= 100.0:
                    cap_violation = True
                    missing_by_component[component_id] = (
                        *missing_by_component.get(component_id, ()),
                        "primitive_score_out_of_bounds",
                    )
                    primitive_scores = []
                    break
                primitive_scores.append((primitive, score))
            if primitive_scores:
                component_score = sum(
                    primitive.weight * score for primitive, score in primitive_scores
                )
                if not math.isfinite(component_score):
                    non_finite_reject = True
                    missing_by_component[component_id] = ("non_finite_component_score",)
                elif not 0.0 <= component_score <= 100.0:
                    cap_violation = True
                    missing_by_component[component_id] = ("component_score_out_of_bounds",)
                else:
                    component_scores[component_id] = round(component_score, 4)
        score_bearing_count = sum(
            component.score_bearing for component in manifest.components.values()
        )
        score_eligible = not missing_by_component and len(component_scores) == score_bearing_count
        ranking_score = (
            round(
                sum(
                    manifest.components[component_id].weight * score
                    for component_id, score in component_scores.items()
                ),
                4,
            )
            if score_eligible
            else None
        )
        if ranking_score is not None and not math.isfinite(ranking_score):
            non_finite_reject = True
            ranking_score = None
            score_eligible = False
        elif ranking_score is not None and not 0.0 <= ranking_score <= 100.0:
            cap_violation = True
            ranking_score = None
            score_eligible = False
        limitation_reasons: tuple[str, ...] = ()
        if ranking_score is not None:
            risk_flags = ranking_input.values.get("risk_flags")
            bounded_score, limitations = apply_final_score_limits(
                ranking_score,
                risk_flags=[str(flag) for flag in risk_flags]
                if isinstance(risk_flags, Sequence)
                else [],
            )
            ranking_score = round(bounded_score, 4) if math.isfinite(bounded_score) else None
            limitation_reasons = tuple(limitations)
        if ranking_score is None and not score_eligible:
            limitation_reasons = tuple(
                f"{component_id}:{','.join(reasons)}"
                for component_id, reasons in sorted(missing_by_component.items())
            )
        results[ranking_input.asset_code] = FinalScoreV3Result(
            asset_bucket=ranking_input.asset_bucket,
            ranking_score=ranking_score,
            score_eligible=score_eligible,
            component_scores=component_scores,
            missing_by_component=missing_by_component,
            metric_peer_counts=metric_peer_counts,
            limitation_reasons=limitation_reasons,
            weakest_required_primitive_peer_count=(
                _weakest_required_primitive_peer_count(
                    manifest,
                    bucket_distributions,
                )
            ),
            clone_policy_active=clone_policy_active,
            tracked_underlying_coverage=round(tracked_underlying_coverage, 6),
            cap_violation=cap_violation,
            non_finite_reject=non_finite_reject,
            clone_group_id=(
                _clone_key(ranking_input, clone_policy_active=True) if clone_policy_active else None
            ),
            diversified_representative=(
                representative_by_group.get(_clone_key(ranking_input, clone_policy_active=True))
                == ranking_input.asset_code
                if clone_policy_active
                else None
            ),
        )
    return results


def build_v3_shadow_comparison(assets: Sequence[Any], *, top_n: int = 10) -> dict[str, Any]:
    etf_assets = [
        asset
        for asset in assets
        if str(getattr(getattr(asset, "metadata", None), "asset_type", "")) == "etf"
    ]
    v2_scores: list[tuple[str, float]] = []
    v3_scores: list[tuple[str, float]] = []
    component_available: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    capped_count = 0
    for asset in etf_assets:
        code = str(getattr(getattr(asset, "metadata", None), "code", ""))
        metrics = getattr(asset, "metrics", {})
        breakdown = getattr(asset, "score_breakdown", {})
        metrics = metrics if isinstance(metrics, Mapping) else {}
        breakdown = breakdown if isinstance(breakdown, Mapping) else {}
        total_score = _numeric(getattr(asset, "total_score", None))
        if code and total_score is not None:
            v2_scores.append((code, total_score))
        v3_score = _numeric(metrics.get("v3_ranking_score"))
        if code and metrics.get("v3_score_eligible") is True and v3_score is not None:
            v3_scores.append((code, v3_score))
        shadow = breakdown.get("final_score_v3_shadow")
        component_scores = shadow.get("component_scores") if isinstance(shadow, Mapping) else None
        if isinstance(component_scores, Mapping):
            component_available.update(str(component_id) for component_id in component_scores)
        limitations = metrics.get("v3_score_limitation_reasons")
        if isinstance(limitations, Sequence) and not isinstance(limitations, str):
            exclusions.update(str(reason) for reason in limitations if reason)
            capped_count += sum(
                reason
                in {
                    "数据不足不能形成高分排序。",
                    "旧数据不能提高最终排序。",
                    "不可决策数据不能提高最终排序。",
                }
                for reason in limitations
            )
        elif isinstance(metrics.get("v3_missing_by_component"), Mapping):
            for component_id, reasons in metrics["v3_missing_by_component"].items():
                for reason in (
                    reasons
                    if isinstance(reasons, Sequence) and not isinstance(reasons, str)
                    else [reasons]
                ):
                    exclusions[f"{component_id}:{reason}"] += 1
    v2_ordered = sorted(v2_scores, key=lambda item: (-item[1], item[0]))
    v3_ordered = sorted(v3_scores, key=lambda item: (-item[1], item[0]))
    v2_ranks = {code: index for index, (code, _score) in enumerate(v2_ordered, start=1)}
    v3_ranks = {code: index for index, (code, _score) in enumerate(v3_ordered, start=1)}
    common_codes = sorted(set(v2_ranks).intersection(v3_ranks))
    rank_correlation = None
    if len(common_codes) >= 2:
        squared_distance = sum((v2_ranks[code] - v3_ranks[code]) ** 2 for code in common_codes)
        count = len(common_codes)
        rank_correlation = round(1 - 6 * squared_distance / (count * (count**2 - 1)), 6)
    v2_top = [code for code, _score in v2_ordered[:top_n]]
    v3_top = [code for code, _score in v3_ordered[:top_n]]
    total = len(etf_assets)
    return {
        "coverage": {
            "total": total,
            "eligible": len(v3_scores),
            "ratio": round(len(v3_scores) / total, 4) if total else 0.0,
        },
        "component_availability": {
            component_id: {"available": count, "total": total}
            for component_id, count in sorted(
                (component_id, component_available[component_id])
                for component_id in final_score_v3_manifest().components
            )
        },
        "caps": {"applied_count": capped_count},
        "rank_correlation": rank_correlation,
        "top_n_changes": {
            "top_n": top_n,
            "v2_only": [code for code in v2_top if code not in set(v3_top)],
            "v3_only": [code for code in v3_top if code not in set(v2_top)],
        },
        "exclusion_reasons": dict(sorted(exclusions.items())),
    }
