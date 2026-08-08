"""Frozen, point-in-time leader-tactics V2 contracts for A-shares and ETFs.

This module is intentionally pure: it performs no provider calls and writes no
production state.  Persistence, replay and HTTP projection live in separate
modules so a research query cannot accidentally become a ranking or alert
mutation.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from statistics import mean
from typing import Any, Literal

from app.services.etf_research_evidence import stable_contract_hash

V2_SCHEMA_VERSION = "dual_universe_leader_tactics_v2"
V2_EXPERIMENT_FAMILY = "leader_tactics_shadow_v2"
V2_SOURCE_REGISTRY_VERSION = "leader_tactics_source_registry_v2"
V2_FORMULA_REGISTRY_VERSION = "leader_tactics_formula_registry_v2"
V2_LIFECYCLE_VERSION = "leader_tactics_lifecycle_v2"

UNIVERSE_ETF = "etf"
UNIVERSE_ASHARE = "ashare"
SUPPORTED_UNIVERSES = (UNIVERSE_ETF, UNIVERSE_ASHARE)

BREAKOUT_V2 = "leader_breakout_proxy_v2"
BASE_LAUNCH_V2 = "base_launch_proxy_v2"
FORMER_LEADER_REPAIR_V2 = "former_leader_repair_proxy_v2"
V2_CANDIDATE_IDS = (BREAKOUT_V2, BASE_LAUNCH_V2, FORMER_LEADER_REPAIR_V2)

STATE_PREPARING = "preparing"
STATE_CONFIRMED = "confirmed"
STATE_INVALIDATED = "invalidated"
LIFECYCLE_STATES = (STATE_PREPARING, STATE_CONFIRMED, STATE_INVALIDATED)

SESSION_PIT_MODE = "session_pit"
POST_CLOSE_WATCHLIST_MODE = "post_close_watchlist"
DECISION_MODES = (SESSION_PIT_MODE, POST_CLOSE_WATCHLIST_MODE)

PRICE_BASIS = "total_return_adjusted"
FORBIDDEN_DECISION_PROVIDERS = frozenset({"sina", "efinance"})
MINIMUM_PEER_COUNT = 5
MINIMUM_BATCH_QUALIFIERS = 3
BATCH_BREADTH_MINIMUM = 0.20
VOLUME_LOOKBACK = 120
BREAKOUT_LOOKBACK = 20
REPAIR_LOOKBACK = 120
REPAIR_HISTORY = 180
STANDARD_HISTORY = 120
MA5_COST_BPS_PER_SIDE = 5.0
MA5_SLIPPAGE_BPS_PER_SIDE = 5.0


class V2ContractError(ValueError):
    """Raised when a frozen V2 contract or point-in-time input is invalid."""


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise V2ContractError(f"{label} must be a lowercase sha256 hash")


def _percentile(values: Mapping[str, float], *, reverse: bool = False) -> dict[str, float]:
    finite = {
        key: parsed for key, value in values.items() if (parsed := _finite(value)) is not None
    }
    if not finite:
        return {}
    ordered = sorted(finite.items(), key=lambda item: (item[1], item[0]))
    denominator = max(1, len(ordered) - 1)
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + end - 1) / 2 / denominator if len(ordered) > 1 else 0.5
        if reverse:
            rank = 1.0 - rank
        for position in range(index, end):
            result[ordered[position][0]] = rank
        index = end
    return result


def _mean_finite(values: Sequence[object]) -> float | None:
    parsed = [value for raw in values if (value := _finite(raw)) is not None]
    if not parsed:
        return None
    result = mean(parsed)
    return result if math.isfinite(result) else None


@dataclass(frozen=True)
class V2SourceArticle:
    article_id: str
    title: str
    published_on: date
    source_url: str
    captured_content_hash: str
    disclosed_rules: tuple[str, ...]

    def validate(self) -> None:
        if not all((self.article_id.strip(), self.title.strip(), self.source_url.strip())):
            raise V2ContractError("source article metadata is incomplete")
        if not self.source_url.startswith("https://"):
            raise V2ContractError("source article URL must be https")
        _require_sha256(self.captured_content_hash, "captured_content_hash")
        if not self.disclosed_rules:
            raise V2ContractError("source article must have compact rule assertions")


@dataclass(frozen=True)
class V2SourceRegistry:
    version: str
    derivation_cutoff: date
    validation_start: date
    validation_end: date
    locked_case_date: date
    locked_case_assets: tuple[str, ...]
    articles: tuple[V2SourceArticle, ...]
    unavailable_proprietary_rules: tuple[str, ...]
    non_equivalence_notice: str
    registry_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("registry_hash")
        return payload

    def validate(self) -> None:
        if self.version != V2_SOURCE_REGISTRY_VERSION:
            raise V2ContractError("source registry version is incompatible")
        if not self.derivation_cutoff < self.validation_start <= self.validation_end:
            raise V2ContractError("source chronology is invalid")
        if self.locked_case_date <= self.validation_end:
            raise V2ContractError("locked case must be after validation interval")
        if not self.locked_case_assets:
            raise V2ContractError("locked case assets are required")
        if not self.unavailable_proprietary_rules:
            raise V2ContractError("proprietary unavailable rule must be explicit")
        notice = self.non_equivalence_notice.lower()
        if "not" not in notice or "proprietary" not in notice:
            raise V2ContractError("non-equivalence notice is incomplete")
        for article in self.articles:
            article.validate()
        if self.registry_hash != stable_contract_hash(self.canonical_payload()):
            raise V2ContractError("source registry hash is not canonical")


_AUGUST_CASE_TRANSCRIPT = (
    "2026-08-03 AI application theme batch takeoff signals; core candidates "
    "泛微网络 603039 and 利欧股份 002131."
)


def _build_source_registry() -> V2SourceRegistry:
    draft = V2SourceRegistry(
        version=V2_SOURCE_REGISTRY_VERSION,
        derivation_cutoff=date(2026, 7, 15),
        validation_start=date(2026, 7, 16),
        validation_end=date(2026, 7, 29),
        locked_case_date=date(2026, 8, 3),
        locked_case_assets=("603039", "002131"),
        articles=(
            V2SourceArticle(
                article_id="leader-tactics-full",
                title="飞哥干货 - 龙头战法全解",
                published_on=date(2026, 7, 15),
                source_url="https://mp.weixin.qq.com/s/N2yKjiahHGUjidomQ4HIWQ",
                captured_content_hash=(
                    "f297f2838db0e0cb84bc03cb77a186295ef6b22629a440bfd7f7fe7c8f6b74f2"
                ),
                disclosed_rules=("hot_theme", "core_leader", "ma5_gt_ma10_gt_ma20", "peak_volume"),
            ),
            V2SourceArticle(
                article_id="leader-tactics-august-locked-case",
                title="AI 应用板块批量起飞信号（锁定案例）",
                published_on=date(2026, 8, 3),
                source_url="https://mp.weixin.qq.com/s/Ik6xm0EzBMKNR_V7t8aTZA",
                captured_content_hash=_sha256(_AUGUST_CASE_TRANSCRIPT),
                disclosed_rules=("batch_theme_breadth", "named_core_candidates"),
            ),
            V2SourceArticle(
                article_id="proprietary-takeoff-signal",
                title="直接公布！！",
                published_on=date(2026, 7, 3),
                source_url="https://mp.weixin.qq.com/s/3PAPZj9md2cBHByDEUStrw",
                captured_content_hash=(
                    "1c40f383794935005074dfe9e80afde5f4c250fb327c97cab11a5125fb18402a"
                ),
                disclosed_rules=("proprietary_signal_reference_only",),
            ),
        ),
        unavailable_proprietary_rules=("起飞信号精确公式", "作者盘中成交模型"),
        non_equivalence_notice=(
            "V2 is a transparent engineering proxy, not the author proprietary signal; it is not endorsed by the author and is not formal trading advice。"
        ),
        registry_hash="pending",
    )
    return replace(draft, registry_hash=stable_contract_hash(draft.canonical_payload()))


V2_SOURCE_REGISTRY = _build_source_registry()
V2_SOURCE_REGISTRY.validate()


@dataclass(frozen=True)
class V2FormulaDefinition:
    formula_id: str
    required_history_sessions: int
    formula_text: str
    parameter_items: tuple[tuple[str, str | int | float], ...]
    missing_value_rule: str = "fail_closed_no_padding"
    formula_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("formula_hash")
        return payload

    def validate(self) -> None:
        if self.formula_id not in V2_CANDIDATE_IDS:
            raise V2ContractError("undeclared V2 candidate")
        if self.required_history_sessions < STANDARD_HISTORY:
            raise V2ContractError("candidate history must cover the standard tier")
        if self.missing_value_rule != "fail_closed_no_padding":
            raise V2ContractError("missing-value policy is not frozen")
        if self.formula_hash != stable_contract_hash(self.canonical_payload()):
            raise V2ContractError("formula hash is not canonical")


def _formula(
    formula_id: str,
    history: int,
    formula_text: str,
    parameters: tuple[tuple[str, str | int | float], ...],
) -> V2FormulaDefinition:
    draft = V2FormulaDefinition(
        formula_id=formula_id,
        required_history_sessions=history,
        formula_text=formula_text,
        parameter_items=parameters,
    )
    result = replace(draft, formula_hash=stable_contract_hash(draft.canonical_payload()))
    result.validate()
    return result


V2_FORMULAS = (
    _formula(
        BREAKOUT_V2,
        STANDARD_HISTORY,
        "common_gates and adjusted_close_T > max(adjusted_high[T-20:T-1])",
        (
            ("volume_lookback", VOLUME_LOOKBACK),
            ("breakout_lookback", BREAKOUT_LOOKBACK),
            ("hot_score_min", 2 / 3),
            ("core_score_min", 0.80),
            ("peer_count_min", MINIMUM_PEER_COUNT),
        ),
    ),
    _formula(
        BASE_LAUNCH_V2,
        STANDARD_HISTORY,
        "common_gates and cross(ma5,ma10,T-2:T) and close>ma20 and "
        "ma20_T>=ma20_T-5 and atr5/atr20<=0.90 and abs(adjusted_close-adjusted_MA20)/adjusted_ATR20<=1.50",
        (
            ("cross_window", 3),
            ("atr5_atr20_max", 0.90),
            ("overextension_atr_max", 1.50),
            ("hot_score_min", 2 / 3),
            ("core_score_min", 0.80),
        ),
    ),
    _formula(
        FORMER_LEADER_REPAIR_V2,
        REPAIR_HISTORY,
        "hot_theme and prior_leader and drawdown[-0.50,-0.30] and positive_bar and "
        "atr5/atr20<=0.75 and abs(adjusted_close-adjusted_MA20)/adjusted_ATR20<=1.00",
        (
            ("prior_leadership_window", "T-119:T-20"),
            ("prior_leadership_min", 0.80),
            ("drawdown_min", -0.50),
            ("drawdown_max", -0.30),
            ("atr5_atr20_max", 0.75),
            ("overextension_atr_max", 1.00),
            ("hot_score_min", 2 / 3),
        ),
    ),
)
V2_FORMULA_REGISTRY_HASH = stable_contract_hash(
    {
        "version": V2_FORMULA_REGISTRY_VERSION,
        "formula_hashes": tuple(item.formula_hash for item in V2_FORMULAS),
    }
)


@dataclass(frozen=True)
class V2AdjustedBar:
    trade_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    volume: float
    amount: float
    turnover: float
    observed_at: datetime
    provider: str
    adjustment_version: str
    decision_eligible: bool = True
    price_basis: str = PRICE_BASIS
    revision_id: str = ""


@dataclass(frozen=True)
class V2PITMembership:
    group_id: str
    effective_from: date
    effective_to: date | None
    observed_at: datetime
    mapping_kind: str
    taxonomy_version: str
    theme: str | None = None
    sector: str | None = None
    tracked_index: str | None = None
    clone_group: str | None = None
    issuer: str | None = None
    fact_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fact_hash")
        return payload


@dataclass(frozen=True)
class V2AssetInput:
    universe: Literal["etf", "ashare"]
    asset_code: str
    asset_name: str
    signal_date: date
    source_cutoff: datetime
    bars: tuple[V2AdjustedBar, ...]
    membership: V2PITMembership | None
    baseline_score: float | None = None
    input_unavailable_reasons: tuple[str, ...] = ()
    decision_mode: Literal["session_pit", "post_close_watchlist"] = SESSION_PIT_MODE
    membership_evaluation_date: date | None = None
    next_eligible_date: date | None = None


@dataclass(frozen=True)
class V2CandidateObservation:
    universe: str
    asset_code: str
    asset_name: str
    signal_date: date
    formula_id: str
    state: str
    availability: Literal["available", "unavailable"]
    qualifies: bool
    score: float | None
    gate_facts: tuple[tuple[str, str | int | float | bool | None], ...]
    exclusion_reasons: tuple[str, ...]
    source_cutoff: datetime
    theme: str | None
    sector: str | None
    tracked_index: str | None
    clone_group: str | None
    issuer: str | None
    feature_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("feature_hash")
        return payload


@dataclass(frozen=True)
class V2ScreenResult:
    universe: str
    signal_date: date
    source_cutoff: datetime
    observations: tuple[V2CandidateObservation, ...]
    manifest_hash: str
    input_hash: str
    exclusions: tuple[tuple[str, int], ...]
    data_receipt_cutoff: datetime | None = None
    # Keep identity inputs on the screen result so persistence cannot silently
    # rebuild a different manifest from out-of-band defaults.
    code_version: str = "dual-universe-leader-tactics-v2"
    provider_health: tuple[tuple[str, str], ...] = ()

    @property
    def qualifying(self) -> tuple[V2CandidateObservation, ...]:
        return tuple(item for item in self.observations if item.qualifies)


@dataclass(frozen=True)
class V2LifecycleTransition:
    universe: str
    asset_code: str
    formula_id: str
    signal_date: date
    from_state: str | None
    to_state: str
    transition_date: date
    signal_high: float
    adjusted_close: float | None
    adjusted_ma5: float | None
    simulated_execution_date: date | None
    execution_model: str
    reason: str
    transition_hash: str
    cost_bps_per_side: float = MA5_COST_BPS_PER_SIDE + MA5_SLIPPAGE_BPS_PER_SIDE


@dataclass(frozen=True)
class V2ResearchManifest:
    universe: str
    decision_cutoff: datetime
    data_receipt_cutoff: datetime
    input_hash: str
    source_registry_hash: str
    formula_registry_hash: str
    code_version: str
    holdout_identity: str
    research_only: bool
    manifest_hash: str
    formula_ids: tuple[str, ...] = V2_CANDIDATE_IDS
    adjustment_version: str = PRICE_BASIS
    taxonomy_version: str = "pit_theme_taxonomy_v2"
    cost_model: tuple[tuple[str, float], ...] = (
        ("fee_bps_per_side", 5.0),
        ("slippage_bps_per_side", 5.0),
    )
    clone_policy: str = "one_most_liquid_representative_per_pit_clone_group"
    state_policy: str = "preparing_confirmed_invalidated_v2"
    pagination_cursor: str | None = None
    exclusions: tuple[tuple[str, int], ...] = ()
    provider_health: tuple[tuple[str, str], ...] = ()
    runtime_budget_seconds: float = 55.0

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("manifest_hash")
        return payload

    def validate(self) -> None:
        if self.universe not in SUPPORTED_UNIVERSES:
            raise V2ContractError("manifest universe is unsupported")
        if self.decision_cutoff > self.data_receipt_cutoff:
            raise V2ContractError("decision cutoff cannot exceed receipt cutoff")
        _require_sha256(self.input_hash, "input_hash")
        if self.source_registry_hash != V2_SOURCE_REGISTRY.registry_hash:
            raise V2ContractError("source registry hash is incompatible")
        if self.formula_registry_hash != V2_FORMULA_REGISTRY_HASH:
            raise V2ContractError("formula registry hash is incompatible")
        if tuple(self.formula_ids) != V2_CANDIDATE_IDS:
            raise V2ContractError("manifest candidate set is not frozen")
        if self.adjustment_version != PRICE_BASIS:
            raise V2ContractError("manifest adjustment basis is incompatible")
        if not self.code_version.strip() or not self.holdout_identity.strip():
            raise V2ContractError("manifest code and holdout identity are required")
        if self.research_only is not True:
            raise V2ContractError("V2 manifest must be research-only")
        if self.runtime_budget_seconds <= 0 or self.runtime_budget_seconds > 55:
            raise V2ContractError("manifest runtime budget exceeds the V2 bound")
        cost_keys = {key for key, _ in self.cost_model}
        if cost_keys != {"fee_bps_per_side", "slippage_bps_per_side"}:
            raise V2ContractError("manifest cost model is incomplete")
        if any(value < 0 or value > 1000 for _, value in self.cost_model):
            raise V2ContractError("manifest cost model is invalid")
        if self.manifest_hash != stable_contract_hash(self.canonical_payload()):
            raise V2ContractError("manifest hash is not canonical")


def build_v2_manifest(
    *,
    universe: str,
    decision_cutoff: datetime,
    data_receipt_cutoff: datetime,
    input_hash: str,
    code_version: str = "dual-universe-leader-tactics-v2",
    holdout_identity: str = "holdout-2026-08-03-single-use-v1",
    pagination_cursor: str | None = None,
    exclusions: tuple[tuple[str, int], ...] = (),
    provider_health: tuple[tuple[str, str], ...] = (),
) -> V2ResearchManifest:
    draft = V2ResearchManifest(
        universe=universe,
        decision_cutoff=decision_cutoff,
        data_receipt_cutoff=data_receipt_cutoff,
        input_hash=input_hash,
        source_registry_hash=V2_SOURCE_REGISTRY.registry_hash,
        formula_registry_hash=V2_FORMULA_REGISTRY_HASH,
        code_version=code_version,
        holdout_identity=holdout_identity,
        research_only=True,
        manifest_hash="pending",
        pagination_cursor=pagination_cursor,
        exclusions=exclusions,
        provider_health=provider_health,
    )
    result = replace(draft, manifest_hash=stable_contract_hash(draft.canonical_payload()))
    result.validate()
    return result


def validate_runtime_contract(
    *,
    formula_ids: Sequence[str] = V2_CANDIDATE_IDS,
    source_registry_hash: str = V2_SOURCE_REGISTRY.registry_hash,
    formula_registry_hash: str = V2_FORMULA_REGISTRY_HASH,
    holdout_identity: str = "holdout-2026-08-03-single-use-v1",
) -> None:
    if tuple(formula_ids) != V2_CANDIDATE_IDS:
        raise V2ContractError("V2 accepts exactly the three frozen candidates")
    if source_registry_hash != V2_SOURCE_REGISTRY.registry_hash:
        raise V2ContractError("source registry substitution is not allowed")
    if formula_registry_hash != V2_FORMULA_REGISTRY_HASH:
        raise V2ContractError("formula registry substitution is not allowed")
    if holdout_identity != "holdout-2026-08-03-single-use-v1":
        raise V2ContractError("holdout identity substitution is not allowed")


def _membership_reasons(item: V2AssetInput) -> list[str]:
    membership = item.membership
    reasons = list(item.input_unavailable_reasons)
    membership_date = item.membership_evaluation_date or item.signal_date
    if item.decision_mode not in DECISION_MODES:
        reasons.append("decision_mode_unsupported")
    elif item.decision_mode == SESSION_PIT_MODE:
        if membership_date != item.signal_date:
            reasons.append("session_pit_membership_date_mismatch")
    elif (
        membership_date < item.signal_date
        or item.next_eligible_date is None
        or item.next_eligible_date <= item.signal_date
    ):
        reasons.append("post_close_watchlist_timing_invalid")
    if item.universe not in SUPPORTED_UNIVERSES:
        reasons.append("universe_unsupported")
    if not item.asset_code.strip():
        reasons.append("missing_asset_code")
    if membership is None:
        reasons.append("missing_pit_theme_membership")
        return sorted(set(reasons))
    if not membership.group_id.strip():
        reasons.append("missing_pit_peer_group")
    if membership.mapping_kind != "historical_pit":
        reasons.append("taxonomy_not_point_in_time")
    if membership.effective_from > membership_date:
        reasons.append("membership_effective_after_signal")
    if membership.effective_to is not None and membership.effective_to < membership_date:
        reasons.append("membership_expired_before_signal")
    if membership.observed_at > item.source_cutoff:
        reasons.append("membership_received_after_cutoff")
    if not membership.fact_hash:
        reasons.append("missing_membership_fact_hash")
    elif membership.fact_hash != stable_contract_hash(membership.canonical_payload()):
        reasons.append("membership_fact_hash_mismatch")
    return sorted(set(reasons))


def _bar_reasons(item: V2AssetInput, required_history: int) -> list[str]:
    reasons: list[str] = []
    if len(item.bars) < required_history:
        reasons.append("insufficient_adjusted_history")
    dates = tuple(bar.trade_date for bar in item.bars)
    if dates != tuple(sorted(set(dates))):
        reasons.append("adjusted_history_not_canonical")
    if not item.bars or item.bars[-1].trade_date != item.signal_date:
        reasons.append("adjusted_history_not_current_to_signal")
    for bar in item.bars:
        values = (
            bar.adjusted_open,
            bar.adjusted_high,
            bar.adjusted_low,
            bar.adjusted_close,
            bar.volume,
            bar.amount,
            bar.turnover,
        )
        if not bar.decision_eligible:
            reasons.append("adjusted_bar_not_decision_eligible")
        if bar.price_basis != PRICE_BASIS:
            reasons.append("wrong_price_basis")
        if bar.provider.strip().lower() in FORBIDDEN_DECISION_PROVIDERS:
            reasons.append("forbidden_raw_price_provider")
        if (
            not bar.provider.strip()
            or not bar.adjustment_version.strip()
            or not bar.revision_id.strip()
        ):
            reasons.append("missing_adjusted_provenance")
        if bar.observed_at.date() < bar.trade_date:
            reasons.append("adjusted_bar_received_before_trade_date")
        if bar.observed_at > item.source_cutoff:
            reasons.append("adjusted_bar_received_after_cutoff")
        if any(_finite(value) is None for value in values):
            reasons.append("non_finite_adjusted_input")
        if (
            bar.adjusted_open <= 0
            or bar.adjusted_high <= 0
            or bar.adjusted_low <= 0
            or bar.adjusted_close <= 0
            or bar.volume < 0
            or bar.amount < 0
            or bar.turnover < 0
            or bar.adjusted_high < max(bar.adjusted_open, bar.adjusted_close)
            or bar.adjusted_low > min(bar.adjusted_open, bar.adjusted_close)
        ):
            reasons.append("invalid_adjusted_ohlcv")
    return sorted(set(reasons))


def _bars_by_date(item: V2AssetInput) -> dict[date, V2AdjustedBar]:
    return {bar.trade_date: bar for bar in item.bars}


def _ma(item: V2AssetInput, sessions: int, end: int = -1) -> float | None:
    if len(item.bars) < sessions:
        return None
    bars = item.bars[:end] if end != -1 else item.bars
    if len(bars) < sessions:
        return None
    return _mean_finite([bar.adjusted_close for bar in bars[-sessions:]])


def _atr(item: V2AssetInput, sessions: int, end: int = -1) -> float | None:
    bars = item.bars[:end] if end != -1 else item.bars
    if len(bars) < sessions + 1:
        return None
    window = bars[-(sessions + 1) :]
    ranges = [
        max(
            current.adjusted_high - current.adjusted_low,
            abs(current.adjusted_high - previous.adjusted_close),
            abs(current.adjusted_low - previous.adjusted_close),
        )
        for previous, current in zip(window[:-1], window[1:], strict=True)
    ]
    result = _mean_finite(ranges)
    return result if result is not None and result > 0 else None


def _return(item: V2AssetInput, sessions: int, end: int = -1) -> float | None:
    bars = item.bars[:end] if end != -1 else item.bars
    if len(bars) <= sessions:
        return None
    start = bars[-sessions - 1].adjusted_close
    finish = bars[-1].adjusted_close
    if start <= 0:
        return None
    result = finish / start - 1.0
    return result if math.isfinite(result) else None


def _group_key(item: V2AssetInput) -> str | None:
    return item.membership.group_id if item.membership is not None else None


def _clone_representatives(
    items: Sequence[V2AssetInput],
    *,
    standard_reasons: Mapping[str, Sequence[str]] | None = None,
) -> tuple[set[str], set[str]]:
    representatives: set[str] = set()
    excluded: set[str] = set()
    by_clone: dict[tuple[str, str], list[V2AssetInput]] = defaultdict(list)
    for item in items:
        group = _group_key(item)
        clone = item.membership.clone_group if item.membership else None
        if group and clone:
            by_clone[(group, clone)].append(item)
        else:
            representatives.add(item.asset_code)
    for clone_items in by_clone.values():
        # An invalid clone must never displace a valid representative.  This
        # also keeps a bad amount/price row from changing the peer universe.
        valid_items = [
            item
            for item in clone_items
            if not (
                standard_reasons.get(item.asset_code, ())
                if standard_reasons is not None
                else _base_input_reasons(item, STANDARD_HISTORY)
            )
        ]
        if not valid_items:
            excluded.update(item.asset_code for item in clone_items)
            continue
        representative = max(
            valid_items,
            key=lambda item: (
                (
                    amount
                    if (amount := _mean_finite([bar.amount for bar in item.bars[-20:]])) is not None
                    else -math.inf
                ),
                item.asset_code,
            ),
        )
        representatives.add(representative.asset_code)
        excluded.update(
            item.asset_code for item in clone_items if item.asset_code != representative.asset_code
        )
    return representatives, excluded


def _theme_features(
    items: Sequence[V2AssetInput],
    representatives: set[str],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    groups: dict[str, list[V2AssetInput]] = defaultdict(list)
    for item in items:
        if item.asset_code in representatives and _group_key(item):
            groups[_group_key(item)].append(item)  # type: ignore[index]
    one_day: dict[str, float] = {}
    five_day: dict[str, float] = {}
    breadth: dict[str, float] = {}
    for group, members in groups.items():
        returns_1 = [value for item in members if (value := _return(item, 1)) is not None]
        returns_5 = [value for item in members if (value := _return(item, 5)) is not None]
        one_day[group] = _mean_finite(returns_1) or 0.0
        five_day[group] = _mean_finite(returns_5) or 0.0
        breadth[group] = (
            sum(value > 0 for value in returns_1) / len(returns_1) if returns_1 else 0.0
        )
    return (
        _percentile(one_day),
        _percentile(five_day),
        _percentile(breadth),
    )


def _peer_features(
    items: Sequence[V2AssetInput],
    representatives: set[str],
) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, int]]:
    groups: dict[str, list[V2AssetInput]] = defaultdict(list)
    for item in items:
        if item.asset_code in representatives and _group_key(item):
            groups[_group_key(item)].append(item)  # type: ignore[index]
    return_20_percentiles: dict[str, float] = {}
    return_5_percentiles: dict[str, float] = {}
    turnover_percentiles: dict[str, float] = {}
    peer_counts: dict[str, int] = {}
    for members in groups.values():
        returns_20 = {
            item.asset_code: value for item in members if (value := _return(item, 20)) is not None
        }
        returns_5 = {
            item.asset_code: value for item in members if (value := _return(item, 5)) is not None
        }
        amount_20 = {
            item.asset_code: value
            for item in members
            if (value := _mean_finite([bar.amount for bar in item.bars[-20:]])) is not None
        }
        return_20_percentiles.update(_percentile(returns_20))
        return_5_percentiles.update(_percentile(returns_5))
        turnover_percentiles.update(_percentile(amount_20))
        for item in members:
            peer_counts[item.asset_code] = len(returns_20)
    return return_20_percentiles, return_5_percentiles, turnover_percentiles, peer_counts


def _prior_leadership_index(
    items: Sequence[V2AssetInput],
) -> dict[str, float]:
    """Precompute each asset's best prior-leader percentile per peer group.

    The old implementation rebuilt a peer date map, sorted the peer bars and
    searched for the target date for every asset/date/peer combination.  This
    version builds one date-indexed return matrix per group and ranks each date
    once.  It remains an in-memory, bounded calculation for the pure engine.
    """

    groups: dict[str, list[V2AssetInput]] = defaultdict(list)
    for item in items:
        if (group := _group_key(item)) is not None:
            groups[group].append(item)

    best_by_asset: dict[str, float] = {}
    for peers in groups.values():
        target_dates: set[date] = set()
        for item in peers:
            if len(item.bars) >= REPAIR_HISTORY:
                target_dates.update(bar.trade_date for bar in item.bars[-120:-20])
        if not target_dates:
            continue

        returns_by_date: dict[date, dict[str, float]] = defaultdict(dict)
        for peer in peers:
            # Build one index per peer, rather than one per target date.
            bars_by_date = {bar.trade_date: index for index, bar in enumerate(peer.bars)}
            for target_date in target_dates:
                index = bars_by_date.get(target_date)
                if index is None or index < 20:
                    continue
                start = _finite(peer.bars[index - 20].adjusted_close)
                finish = _finite(peer.bars[index].adjusted_close)
                if start is None or finish is None or start <= 0:
                    continue
                value = finish / start - 1.0
                if math.isfinite(value):
                    returns_by_date[target_date][peer.asset_code] = value

        percentiles_by_date = {
            target_date: _percentile(values) for target_date, values in returns_by_date.items()
        }
        for item in peers:
            if len(item.bars) < REPAIR_HISTORY:
                continue
            for target_date in (bar.trade_date for bar in item.bars[-120:-20]):
                rank = percentiles_by_date.get(target_date, {}).get(item.asset_code)
                if rank is not None:
                    prior = best_by_asset.get(item.asset_code)
                    best_by_asset[item.asset_code] = rank if prior is None else max(prior, rank)
    return best_by_asset


def _prior_leadership(
    item: V2AssetInput,
    peers: Sequence[V2AssetInput],
    *,
    precomputed: Mapping[str, float] | None = None,
) -> float | None:
    """Return a cached prior-leader percentile, with a compatibility path."""

    if precomputed is not None:
        return precomputed.get(item.asset_code)
    return _prior_leadership_index((*peers, item)).get(item.asset_code)


def _group_component_percentiles(
    rows: Sequence[tuple[V2AssetInput, dict[str, Any]]],
    *,
    reverse_components: Mapping[str, bool],
) -> dict[str, dict[tuple[str, str], float]]:
    """Rank raw score components within each PIT peer group exactly once."""

    values_by_component: dict[str, dict[str, dict[str, float]]] = {
        component: defaultdict(dict) for component in reverse_components
    }
    for item, details in rows:
        group = _group_key(item)
        if group is None:
            continue
        raw_values = details.get("raw_score_values", {})
        for component in reverse_components:
            value = _finite(raw_values.get(component))
            if value is not None:
                values_by_component[component][group][item.asset_code] = value

    ranked: dict[str, dict[tuple[str, str], float]] = {}
    for component, reverse in reverse_components.items():
        component_rank: dict[tuple[str, str], float] = {}
        for group, values in values_by_component[component].items():
            component_rank.update(
                {(group, code): rank for code, rank in _percentile(values, reverse=reverse).items()}
            )
        ranked[component] = component_rank
    return ranked


def _encoded_input_hash_value(value: object) -> bytes:
    if value is None:
        text = "none:"
    elif isinstance(value, datetime):
        text = f"datetime:{value.isoformat()}"
    elif isinstance(value, date):
        text = f"date:{value.isoformat()}"
    elif isinstance(value, bool):
        text = f"bool:{value!r}"
    elif isinstance(value, (int, float)):
        text = f"number:{type(value).__name__}:{value!r}"
    else:
        text = f"string:{str(value)}"
    return text.encode("utf-8")


def _append_input_hash_value(buffer: bytearray, value: object) -> None:
    encoded = _encoded_input_hash_value(value)
    buffer.extend(len(encoded).to_bytes(8, "big"))
    buffer.extend(encoded)


def _update_input_hash(hasher: Any, value: object) -> None:
    """Feed one length-delimited scalar into a digest."""

    encoded = _encoded_input_hash_value(value)
    hasher.update(len(encoded).to_bytes(8, "big"))
    hasher.update(encoded)


def _incremental_input_hash(items: Sequence[V2AssetInput]) -> str:
    """Hash factual inputs with one bounded byte buffer per asset.

    Hash digests are independent of update chunk boundaries.  Buffering one
    asset preserves the exact byte stream and manifest identity while avoiding
    millions of tiny Python-to-OpenSSL calls.  Memory remains bounded by one
    asset's capped history rather than the full universe.
    """

    hasher = hashlib.sha256()
    _update_input_hash(hasher, V2_SCHEMA_VERSION)
    _update_input_hash(hasher, "inputs-v2-layered")
    for item in items:
        item_bytes = bytearray()
        for value in (
            item.universe,
            item.asset_code,
            item.asset_name,
            item.signal_date,
            item.source_cutoff,
            item.baseline_score,
            item.decision_mode,
            item.membership_evaluation_date,
            item.next_eligible_date,
        ):
            _append_input_hash_value(item_bytes, value)
        for reason in sorted(item.input_unavailable_reasons):
            _append_input_hash_value(item_bytes, reason)
        _append_input_hash_value(item_bytes, "membership")
        membership = item.membership
        if membership is None:
            _append_input_hash_value(item_bytes, None)
        else:
            for value in (
                membership.group_id,
                membership.effective_from,
                membership.effective_to,
                membership.observed_at,
                membership.mapping_kind,
                membership.taxonomy_version,
                membership.theme,
                membership.sector,
                membership.tracked_index,
                membership.clone_group,
                membership.issuer,
                membership.fact_hash,
            ):
                _append_input_hash_value(item_bytes, value)
        _append_input_hash_value(item_bytes, "bars")
        _append_input_hash_value(item_bytes, len(item.bars))
        for bar in item.bars:
            for value in (
                bar.trade_date,
                bar.adjusted_open,
                bar.adjusted_high,
                bar.adjusted_low,
                bar.adjusted_close,
                bar.volume,
                bar.amount,
                bar.turnover,
                bar.observed_at,
                bar.provider,
                bar.adjustment_version,
                bar.decision_eligible,
                bar.price_basis,
                bar.revision_id,
            ):
                _append_input_hash_value(item_bytes, value)
        hasher.update(item_bytes)
    return hasher.hexdigest()


def _base_input_reasons(item: V2AssetInput, required_history: int) -> list[str]:
    return sorted(set((*_membership_reasons(item), *_bar_reasons(item, required_history))))


def _observation(
    *,
    item: V2AssetInput,
    formula_id: str,
    availability: Literal["available", "unavailable"],
    qualifies: bool,
    score: float | None,
    gate_facts: Mapping[str, str | int | float | bool | None],
    exclusion_reasons: Sequence[str],
    clone_excluded: bool,
) -> V2CandidateObservation:
    reasons = tuple(sorted(set(exclusion_reasons)))
    facts = dict(gate_facts)
    membership_date = item.membership_evaluation_date or item.signal_date
    facts.update(
        {
            "decision_mode": item.decision_mode,
            "feature_trade_date": item.signal_date.isoformat(),
            "membership_evaluation_date": membership_date.isoformat(),
            "next_eligible_date": (
                item.next_eligible_date.isoformat() if item.next_eligible_date else None
            ),
            "historical_validation_eligible": item.decision_mode == SESSION_PIT_MODE,
        }
    )
    if clone_excluded:
        facts["clone_representative"] = False
    draft = V2CandidateObservation(
        universe=item.universe,
        asset_code=item.asset_code,
        asset_name=item.asset_name,
        signal_date=item.signal_date,
        formula_id=formula_id,
        state=STATE_PREPARING,
        availability=availability,
        qualifies=qualifies,
        score=score,
        gate_facts=tuple(sorted(facts.items())),
        exclusion_reasons=reasons,
        source_cutoff=item.source_cutoff,
        theme=item.membership.theme if item.membership else None,
        sector=item.membership.sector if item.membership else None,
        tracked_index=item.membership.tracked_index if item.membership else None,
        clone_group=item.membership.clone_group if item.membership else None,
        issuer=item.membership.issuer if item.membership else None,
        feature_hash="pending",
    )
    return replace(draft, feature_hash=stable_contract_hash(draft.canonical_payload()))


def _cross_close(item: V2AssetInput) -> bool:
    if len(item.bars) < 4:
        return False
    for end in range(len(item.bars) - 3, len(item.bars)):
        current_ma5 = _ma(item, 5, end + 1)
        current_ma10 = _ma(item, 10, end + 1)
        previous_ma5 = _ma(item, 5, end)
        previous_ma10 = _ma(item, 10, end)
        if None not in (current_ma5, current_ma10, previous_ma5, previous_ma10):
            if current_ma5 > current_ma10 and previous_ma5 <= previous_ma10:  # type: ignore[operator]
                return True
    return False


def screen_dual_universe(
    items: Sequence[V2AssetInput],
    *,
    code_version: str = "dual-universe-leader-tactics-v2",
    provider_health: tuple[tuple[str, str], ...] = (),
) -> V2ScreenResult:
    """Screen one universe/session using only factual PIT adjusted inputs."""

    if not items:
        raise V2ContractError("screen requires at least one input")
    universes = {item.universe for item in items}
    dates = {item.signal_date for item in items}
    cutoffs = {item.source_cutoff for item in items}
    modes = {item.decision_mode for item in items}
    membership_dates = {item.membership_evaluation_date or item.signal_date for item in items}
    next_eligible_dates = {item.next_eligible_date for item in items}
    if any(
        len(values) != 1
        for values in (universes, dates, cutoffs, modes, membership_dates, next_eligible_dates)
    ):
        raise V2ContractError(
            "screen inputs must share universe, signal date, cutoff and decision timing"
        )
    universe = next(iter(universes))
    if universe not in SUPPORTED_UNIVERSES:
        raise V2ContractError("unsupported screening universe")
    if len({item.asset_code for item in items}) != len(items):
        raise V2ContractError("screen input asset codes must be unique")
    ordered = tuple(sorted(items, key=lambda item: item.asset_code))
    membership_reasons = {
        item.asset_code: tuple(_membership_reasons(item)) for item in ordered
    }
    intrinsic_bar_reasons = {
        item.asset_code: tuple(_bar_reasons(item, 0)) for item in ordered
    }

    def cached_base_reasons(item: V2AssetInput, required_history: int) -> tuple[str, ...]:
        reasons = set(membership_reasons[item.asset_code])
        reasons.update(intrinsic_bar_reasons[item.asset_code])
        if len(item.bars) < required_history:
            reasons.add("insufficient_adjusted_history")
        return tuple(sorted(reasons))

    standard_reasons = {
        item.asset_code: cached_base_reasons(item, STANDARD_HISTORY)
        for item in ordered
    }
    repair_reasons = {
        item.asset_code: cached_base_reasons(item, REPAIR_HISTORY)
        for item in ordered
    }
    representatives, clone_excluded = _clone_representatives(
        ordered,
        standard_reasons=standard_reasons,
    )
    eligible_standard = tuple(
        item
        for item in ordered
        if item.asset_code in representatives and not standard_reasons[item.asset_code]
    )
    hot_1, hot_5, hot_breadth = _theme_features(eligible_standard, representatives)
    return_20_pct, return_5_pct, turnover_pct, peer_counts = _peer_features(
        eligible_standard, representatives
    )
    peers_by_group: dict[str, list[V2AssetInput]] = defaultdict(list)
    for item in eligible_standard:
        if _group_key(item):
            peers_by_group[_group_key(item)].append(item)  # type: ignore[index]
    prior_leadership = _prior_leadership_index(eligible_standard)

    intermediate: dict[str, list[tuple[V2AssetInput, dict[str, Any]]]] = defaultdict(list)
    observations: list[V2CandidateObservation] = []
    for item in ordered:
        group = _group_key(item)
        group_peers = peers_by_group.get(group or "", [])
        reasons = list(standard_reasons[item.asset_code])
        hot_score = None
        core_score = None
        if group:
            hot_score = _mean_finite((hot_1.get(group), hot_5.get(group), hot_breadth.get(group)))
        core_score = _mean_finite(
            (
                return_20_pct.get(item.asset_code),
                return_5_pct.get(item.asset_code),
                turnover_pct.get(item.asset_code),
            )
        )
        common_facts: dict[str, Any] = {
            "hot_score": hot_score,
            "core_score": core_score,
            "peer_return_20_percentile": return_20_pct.get(item.asset_code),
            "peer_return_5_percentile": return_5_pct.get(item.asset_code),
            "peer_amount_20_percentile": turnover_pct.get(item.asset_code),
            "peer_count": peer_counts.get(item.asset_code, 0),
            "batch_peer_count": len(group_peers),
            "adjusted_ma5": _ma(item, 5),
            "adjusted_ma10": _ma(item, 10),
            "adjusted_ma20": _ma(item, 20),
        }
        for formula_id in V2_CANDIDATE_IDS:
            candidate_reasons = list(reasons)
            facts = dict(common_facts)
            raw_score_values: dict[str, float | None] = {}
            if formula_id == FORMER_LEADER_REPAIR_V2:
                candidate_reasons = list(repair_reasons[item.asset_code])
                prior = _prior_leadership(item, group_peers, precomputed=prior_leadership)
                atr5 = _atr(item, 5)
                atr20 = _atr(item, 20)
                close = item.bars[-1].adjusted_close if item.bars else None
                high_120 = max(
                    (bar.adjusted_close for bar in item.bars[-REPAIR_LOOKBACK:]), default=0.0
                )
                drawdown = close / high_120 - 1.0 if close is not None and high_120 > 0 else None
                overextension = (
                    abs(close - float(facts["adjusted_ma20"])) / atr20
                    if close is not None and facts["adjusted_ma20"] is not None and atr20
                    else None
                )
                compression = atr5 / atr20 if atr5 is not None and atr20 else None
                raw_score_values.update(
                    {"compression": compression, "overextension": overextension}
                )
                facts.update(
                    {
                        "prior_leadership_percentile": prior,
                        "drawdown_120": drawdown,
                        "adjusted_atr5": atr5,
                        "adjusted_atr20": atr20,
                        "atr5_atr20_ratio": compression,
                        "overextension_atr": overextension,
                    }
                )
                if hot_score is None or hot_score < 2 / 3:
                    candidate_reasons.append("hot_theme_gate_failed")
                if peer_counts.get(item.asset_code, 0) < MINIMUM_PEER_COUNT:
                    candidate_reasons.append("insufficient_peer_count")
                if prior is None:
                    candidate_reasons.append("prior_leadership_unavailable")
                elif prior < 0.8:
                    candidate_reasons.append("prior_leadership_gate_failed")
                if drawdown is None or not -0.50 <= drawdown <= -0.30:
                    candidate_reasons.append("drawdown_band_failed")
                if len(item.bars) < 2 or not (
                    item.bars[-1].adjusted_close > item.bars[-1].adjusted_open
                    and item.bars[-1].adjusted_close > item.bars[-2].adjusted_close
                ):
                    candidate_reasons.append("positive_stabilization_failed")
                if compression is None or compression > 0.75:
                    candidate_reasons.append("range_compression_failed")
                if overextension is None or overextension > 1.0:
                    candidate_reasons.append("overextension_gate_failed")
            else:
                if hot_score is None or hot_score < 2 / 3:
                    candidate_reasons.append("hot_theme_gate_failed")
                if core_score is None or core_score < 0.80:
                    candidate_reasons.append("core_leader_gate_failed")
                ma5, ma10, ma20 = (
                    facts["adjusted_ma5"],
                    facts["adjusted_ma10"],
                    facts["adjusted_ma20"],
                )
                if None in (ma5, ma10, ma20) or not (ma5 > ma10 > ma20):
                    candidate_reasons.append("ma_alignment_failed")
                if peer_counts.get(item.asset_code, 0) < MINIMUM_PEER_COUNT:
                    candidate_reasons.append("insufficient_peer_count")
                if len(item.bars) >= VOLUME_LOOKBACK:
                    volume_max = max(bar.volume for bar in item.bars[-VOLUME_LOOKBACK:])
                    facts["latest_120_volume_max"] = volume_max
                    if item.bars[-1].volume < volume_max:
                        candidate_reasons.append("volume_peak_gate_failed")
                else:
                    candidate_reasons.append("insufficient_volume_history")
                if formula_id == BREAKOUT_V2:
                    prior_high = (
                        max(bar.adjusted_high for bar in item.bars[-21:-1])
                        if len(item.bars) >= 21
                        else None
                    )
                    magnitude = (
                        item.bars[-1].adjusted_close / prior_high - 1.0
                        if prior_high and item.bars[-1].adjusted_close > 0
                        else None
                    )
                    raw_score_values["breakout_magnitude"] = magnitude
                    facts.update(
                        {"prior_20_adjusted_high": prior_high, "breakout_magnitude": magnitude}
                    )
                    if prior_high is None or item.bars[-1].adjusted_close <= prior_high:
                        candidate_reasons.append("price_breakout_gate_failed")
                else:
                    atr5, atr20 = _atr(item, 5), _atr(item, 20)
                    close = item.bars[-1].adjusted_close if item.bars else None
                    overextension = (
                        abs(close - float(ma20)) / atr20
                        if close is not None and ma20 is not None and atr20
                        else None
                    )
                    compression = atr5 / atr20 if atr5 is not None and atr20 else None
                    raw_score_values.update(
                        {"compression": compression, "overextension": overextension}
                    )
                    facts.update(
                        {
                            "adjusted_atr5": atr5,
                            "adjusted_atr20": atr20,
                            "atr5_atr20_ratio": compression,
                            "overextension_atr": overextension,
                        }
                    )
                    if not _cross_close(item):
                        candidate_reasons.append("ma5_ma10_cross_missing")
                    if close is None or ma20 is None or close <= ma20:
                        candidate_reasons.append("ma20_position_failed")
                    ma20_5 = _ma(item, 20, len(item.bars) - 5) if len(item.bars) >= 25 else None
                    facts["ma20_five_sessions_ago"] = ma20_5
                    if ma20_5 is None or ma20 < ma20_5:
                        candidate_reasons.append("ma20_slope_failed")
                    if compression is None or compression > 0.90:
                        candidate_reasons.append("base_atr_compression_failed")
                    if overextension is None or overextension > 1.50:
                        candidate_reasons.append("base_overextension_gate_failed")
            intermediate[formula_id].append(
                (
                    item,
                    {
                        "facts": facts,
                        "reasons": candidate_reasons,
                        "raw_score_values": raw_score_values,
                    },
                )
            )

    component_specs = {
        BREAKOUT_V2: {"breakout_magnitude": False},
        BASE_LAUNCH_V2: {"compression": True, "overextension": True},
        FORMER_LEADER_REPAIR_V2: {},
    }
    component_percentiles: dict[str, dict[str, dict[tuple[str, str], float]]] = {}
    for formula_id, rows in intermediate.items():
        # Percentiles must use the same valid representative pool as the peer
        # features; invalid/non-representative clones cannot move a valid rank.
        eligible_rows = [
            (item, details)
            for item, details in rows
            if item.asset_code in representatives
            and not standard_reasons[item.asset_code]
        ]
        component_percentiles[formula_id] = _group_component_percentiles(
            eligible_rows,
            reverse_components=component_specs[formula_id],
        )
    required_history = {
        definition.formula_id: definition.required_history_sessions for definition in V2_FORMULAS
    }
    for formula_id, rows in intermediate.items():
        basic_by_group: dict[str, int] = defaultdict(int)
        for item, details in rows:
            if not details["reasons"] and _group_key(item):
                basic_by_group[_group_key(item)] += 1  # type: ignore[index]
        for item, details in rows:
            facts = details["facts"]
            reasons = list(details["reasons"])
            group = _group_key(item)
            peer_count = peer_counts.get(item.asset_code, 0)
            ranks = component_percentiles[formula_id]
            score_components: list[float] = []
            hot_component = _finite(facts.get("hot_score")) or 0.0
            core_component = _finite(facts.get("core_score")) or 0.0
            if not reasons:
                if formula_id == BREAKOUT_V2:
                    rank = (
                        ranks["breakout_magnitude"].get((group, item.asset_code)) if group else None
                    )
                    if rank is None:
                        reasons.append("breakout_magnitude_percentile_unavailable")
                    else:
                        facts["breakout_magnitude_percentile"] = rank
                        score_components = [hot_component, core_component, rank]
                elif formula_id == BASE_LAUNCH_V2:
                    compression_rank = (
                        ranks["compression"].get((group, item.asset_code)) if group else None
                    )
                    overextension_rank = (
                        ranks["overextension"].get((group, item.asset_code)) if group else None
                    )
                    if compression_rank is None:
                        reasons.append("compression_percentile_unavailable")
                    else:
                        facts["atr5_atr20_reverse_percentile"] = compression_rank
                    if overextension_rank is None:
                        reasons.append("overextension_percentile_unavailable")
                    else:
                        facts["overextension_reverse_percentile"] = overextension_rank
                    if not reasons:
                        score_components = [
                            hot_component,
                            core_component,
                            compression_rank,
                            overextension_rank,
                        ]
                elif not reasons:
                    compression = _finite(facts.get("atr5_atr20_ratio"))
                    overextension = _finite(facts.get("overextension_atr"))
                    score_components = [
                        _finite(facts.get("prior_leadership_percentile")) or 0.0,
                        1.0 - (compression if compression is not None else 1.0),
                        1.0 - min(overextension if overextension is not None else 1.0, 1.0),
                    ]
            if not reasons and (
                basic_by_group.get(group or "", 0) < MINIMUM_BATCH_QUALIFIERS
                or peer_count <= 0
                or basic_by_group.get(group or "", 0) / max(peer_count, 1) < BATCH_BREADTH_MINIMUM
            ):
                reasons.append("batch_breadth_gate_failed")
            clone = item.asset_code in clone_excluded
            if clone:
                reasons.append("clone_not_representative")
            qualifies = not reasons
            score = _mean_finite(score_components) if qualifies else None
            observations.append(
                _observation(
                    item=item,
                    formula_id=formula_id,
                    availability=(
                        "unavailable"
                        if (
                            repair_reasons[item.asset_code]
                            if required_history[formula_id] == REPAIR_HISTORY
                            else standard_reasons[item.asset_code]
                        )
                        else "available"
                    ),
                    qualifies=qualifies,
                    score=score,
                    gate_facts=facts,
                    exclusion_reasons=reasons,
                    clone_excluded=clone,
                )
            )

    ordered_observations = tuple(
        sorted(observations, key=lambda row: (row.formula_id, row.asset_code))
    )
    exclusion_counts: dict[str, int] = defaultdict(int)
    for row in ordered_observations:
        for reason in row.exclusion_reasons:
            exclusion_counts[reason] += 1
    input_hash = _incremental_input_hash(ordered)
    manifest = build_v2_manifest(
        universe=universe,
        decision_cutoff=next(iter(cutoffs)),
        data_receipt_cutoff=next(iter(cutoffs)),
        input_hash=input_hash,
        code_version=code_version,
        exclusions=tuple(sorted(exclusion_counts.items())),
        provider_health=provider_health,
    )
    return V2ScreenResult(
        universe=universe,
        signal_date=next(iter(dates)),
        source_cutoff=next(iter(cutoffs)),
        observations=ordered_observations,
        manifest_hash=manifest.manifest_hash,
        input_hash=input_hash,
        exclusions=tuple(sorted(exclusion_counts.items())),
        data_receipt_cutoff=next(iter(cutoffs)),
        code_version=code_version,
        provider_health=provider_health,
    )


def _transition_hash(transition: V2LifecycleTransition) -> str:
    payload = asdict(transition)
    payload.pop("transition_hash")
    return stable_contract_hash(payload)


def derive_lifecycle(
    *,
    observation: V2CandidateObservation,
    signal_bars: Sequence[V2AdjustedBar],
    evaluation_cutoff: datetime | None = None,
    visible_through: date | None = None,
) -> tuple[V2LifecycleTransition, ...]:
    """Derive causal lifecycle transitions using a separate evidence cutoff.

    ``observation.source_cutoff`` is the signal decision cutoff.  Later
    factual bars may be evaluated only when the caller supplies a later
    ``evaluation_cutoff`` and, optionally, a ``visible_through`` trade date.
    Both the MA5 window and the simulated exit use only qualified bars visible
    under those limits.
    """

    if observation.state != STATE_PREPARING:
        raise V2ContractError("lifecycle input must start in preparing state")
    evaluation_cutoff = evaluation_cutoff or observation.source_cutoff
    if evaluation_cutoff < observation.source_cutoff:
        raise V2ContractError("lifecycle evaluation cutoff precedes signal cutoff")
    if visible_through is not None and visible_through < observation.signal_date:
        raise V2ContractError("lifecycle visible-through precedes signal date")

    gate_facts = dict(observation.gate_facts)
    decision_mode = str(gate_facts.get("decision_mode") or SESSION_PIT_MODE)
    if decision_mode not in DECISION_MODES:
        raise V2ContractError("lifecycle decision mode is unsupported")
    transition_start = observation.signal_date
    next_eligible_date: date | None = None
    if decision_mode == POST_CLOSE_WATCHLIST_MODE:
        try:
            transition_start = date.fromisoformat(
                str(gate_facts["membership_evaluation_date"])
            )
            next_eligible_date = date.fromisoformat(str(gate_facts["next_eligible_date"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise V2ContractError("post-close lifecycle timing is incomplete") from exc
        if transition_start < observation.signal_date or next_eligible_date <= observation.signal_date:
            raise V2ContractError("post-close lifecycle timing is invalid")

    ordered = tuple(sorted(signal_bars, key=lambda bar: bar.trade_date))
    signal_index = next(
        (index for index, bar in enumerate(ordered) if bar.trade_date == observation.signal_date),
        None,
    )
    if signal_index is None:
        raise V2ContractError("signal bar is missing")
    signal_bar = ordered[signal_index]

    def is_qualified_visible(bar: V2AdjustedBar, *, cutoff: datetime) -> bool:
        return (
            bar.decision_eligible
            and bar.observed_at.date() >= bar.trade_date
            and bar.observed_at <= cutoff
            and bar.price_basis == PRICE_BASIS
            and bar.provider.strip().lower() not in FORBIDDEN_DECISION_PROVIDERS
            and bool(bar.revision_id.strip())
            and _finite(bar.adjusted_close) is not None
            and _finite(bar.adjusted_high) is not None
        )

    signal_visible = tuple(
        bar
        for bar in ordered[: signal_index + 1]
        if bar.trade_date <= observation.signal_date
        and is_qualified_visible(bar, cutoff=observation.source_cutoff)
    )
    if not signal_visible or signal_visible[-1].trade_date != observation.signal_date:
        raise V2ContractError("signal bar is not qualified and visible at signal cutoff")

    transitions: list[V2LifecycleTransition] = []
    preparing = V2LifecycleTransition(
        universe=observation.universe,
        asset_code=observation.asset_code,
        formula_id=observation.formula_id,
        signal_date=observation.signal_date,
        from_state=None,
        to_state=STATE_PREPARING,
        transition_date=transition_start,
        signal_high=signal_bar.adjusted_high,
        adjusted_close=signal_bar.adjusted_close,
        adjusted_ma5=_mean_finite([bar.adjusted_close for bar in signal_visible[-5:]]),
        simulated_execution_date=None,
        execution_model="research_state_only",
        reason=(
            "formula_passed_for_post_close_watchlist"
            if decision_mode == POST_CLOSE_WATCHLIST_MODE
            else "formula_passed_at_signal_cutoff"
        ),
        transition_hash="pending",
    )
    transitions.append(replace(preparing, transition_hash=_transition_hash(preparing)))

    current_state = STATE_PREPARING
    signal_high = signal_bar.adjusted_high
    future_visible = tuple(
        bar
        for bar in ordered[signal_index + 1 :]
        if bar.trade_date > observation.signal_date
        and (next_eligible_date is None or bar.trade_date >= next_eligible_date)
        and (visible_through is None or bar.trade_date <= visible_through)
        and is_qualified_visible(bar, cutoff=evaluation_cutoff)
    )
    visible_history = list(signal_visible)
    for index, current in enumerate(future_visible):
        visible_history.append(current)
        ma5 = _mean_finite([bar.adjusted_close for bar in visible_history[-5:]])
        if ma5 is None:
            continue
        if (
            current_state != STATE_CONFIRMED
            and current.adjusted_close > signal_high
            and current.adjusted_close >= ma5
        ):
            transition = V2LifecycleTransition(
                universe=observation.universe,
                asset_code=observation.asset_code,
                formula_id=observation.formula_id,
                signal_date=observation.signal_date,
                from_state=current_state,
                to_state=STATE_CONFIRMED,
                transition_date=current.trade_date,
                signal_high=signal_high,
                adjusted_close=current.adjusted_close,
                adjusted_ma5=ma5,
                simulated_execution_date=None,
                execution_model="research_state_only",
                reason="later_close_breaks_signal_high_and_holds_ma5",
                transition_hash="pending",
            )
            transitions.append(replace(transition, transition_hash=_transition_hash(transition)))
            current_state = STATE_CONFIRMED
            continue
        if current.adjusted_close < ma5:
            next_bar = future_visible[index + 1] if index + 1 < len(future_visible) else None
            transition = V2LifecycleTransition(
                universe=observation.universe,
                asset_code=observation.asset_code,
                formula_id=observation.formula_id,
                signal_date=observation.signal_date,
                from_state=current_state,
                to_state=STATE_INVALIDATED,
                transition_date=current.trade_date,
                signal_high=signal_high,
                adjusted_close=current.adjusted_close,
                adjusted_ma5=ma5,
                simulated_execution_date=next_bar.trade_date if next_bar else None,
                execution_model=(
                    "next_eligible_adjusted_close_with_costs"
                    if next_bar
                    else "unavailable_future_execution"
                ),
                reason="adjusted_close_below_same_session_ma5",
                transition_hash="pending",
            )
            transitions.append(replace(transition, transition_hash=_transition_hash(transition)))
            break
    return tuple(transitions)


def screen_result_payload(result: V2ScreenResult) -> dict[str, Any]:
    """Return a recursively JSON-safe research payload."""

    def json_safe(value: object) -> object:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [json_safe(item) for item in value]
        if isinstance(value, set):
            return [json_safe(item) for item in sorted(value, key=str)]
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    payload = {
        "schema_version": V2_SCHEMA_VERSION,
        "experiment_family": V2_EXPERIMENT_FAMILY,
        "universe": result.universe,
        "signal_date": result.signal_date,
        "source_cutoff": result.source_cutoff,
        "data_receipt_cutoff": result.data_receipt_cutoff,
        "manifest_hash": result.manifest_hash,
        "input_hash": result.input_hash,
        "code_version": result.code_version,
        "provider_health": dict(result.provider_health),
        "source_registry_hash": V2_SOURCE_REGISTRY.registry_hash,
        "formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
        "research_only": True,
        "production_mutation_allowed": False,
        "observations": [asdict(item) for item in result.observations],
        "exclusions": dict(result.exclusions),
    }
    return json_safe(payload)  # type: ignore[return-value]


__all__ = [
    "BASE_LAUNCH_V2",
    "BREAKOUT_V2",
    "FORMER_LEADER_REPAIR_V2",
    "LIFECYCLE_STATES",
    "STATE_CONFIRMED",
    "STATE_INVALIDATED",
    "STATE_PREPARING",
    "SUPPORTED_UNIVERSES",
    "V2AdjustedBar",
    "V2AssetInput",
    "V2CandidateObservation",
    "V2ContractError",
    "V2FormulaDefinition",
    "V2_FORMULAS",
    "V2_FORMULA_REGISTRY_HASH",
    "V2LifecycleTransition",
    "V2PITMembership",
    "V2ResearchManifest",
    "V2ScreenResult",
    "V2SourceArticle",
    "V2SourceRegistry",
    "V2_SOURCE_REGISTRY",
    "V2_EXPERIMENT_FAMILY",
    "build_v2_manifest",
    "derive_lifecycle",
    "screen_dual_universe",
    "screen_result_payload",
    "validate_runtime_contract",
]
