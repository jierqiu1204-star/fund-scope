"""Frozen, point-in-time leader-tactics V2 contracts for A-shares and ETFs.

This module is intentionally pure: it performs no provider calls and writes no
production state.  Persistence, replay and HTTP projection live in separate
modules so a research query cannot accidentally become a ranking or alert
mutation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.ashare_sentiment_risk import (
    ASHARE_SENTIMENT_RISK_CONTRACT_HASH,
    SentimentRiskPoint,
    SentimentRiskSnapshot,
    calculate_sentiment_risk,
)

V2_SCHEMA_VERSION = "dual_universe_leader_tactics_v2"
V2_EXPERIMENT_FAMILY = "leader_tactics_shadow_v2"
V2_SOURCE_REGISTRY_VERSION = "leader_tactics_source_registry_v2"
V2_FORMULA_REGISTRY_VERSION = "leader_tactics_formula_registry_v8"
V2_LIFECYCLE_VERSION = "leader_tactics_lifecycle_v3"
V2_INPUT_HASH_SCHEMA_VERSION = "leader_tactics_v2_input_hash_v5"
ASHARE_MEMBERSHIP_FACT_HASH_CONTRACT = "dual_universe_leader_tactics_v2_ashare_ingestion_v1"
ASHARE_FINE_THEME_FACT_HASH_CONTRACT = "dual_universe_leader_tactics_v2_fine_theme_ingestion_v1"
ASHARE_THEME_GRAPH_SCHEMA_VERSION = "ashare_multilayer_theme_graph_v1"
ASHARE_INDUSTRY_PATH_FACT_HASH_CONTRACT = "ashare_industry_path_fact_v1"
ASHARE_THEME_RELATION_FACT_HASH_CONTRACT = "ashare_theme_relation_fact_v1"

UNIVERSE_ETF = "etf"
UNIVERSE_ASHARE = "ashare"
SUPPORTED_UNIVERSES = (UNIVERSE_ETF, UNIVERSE_ASHARE)

BREAKOUT_V2 = "leader_breakout_proxy_v2"
BASE_LAUNCH_V2 = "base_launch_proxy_v2"
FORMER_LEADER_REPAIR_V2 = "former_leader_repair_proxy_v2"
LOW_BASE_CATCHUP_V1 = "low_base_catchup_proxy_v1"
LEGACY_V2_CANDIDATE_IDS = (BREAKOUT_V2, BASE_LAUNCH_V2, FORMER_LEADER_REPAIR_V2)
V2_CANDIDATE_IDS = (*LEGACY_V2_CANDIDATE_IDS, LOW_BASE_CATCHUP_V1)

STATE_PREPARING = "preparing"
STATE_TURNING_WATCH = "turning_watch"
STATE_CONFIRMED = "confirmed"
STATE_INVALIDATED = "invalidated"
LIFECYCLE_STATES = (
    STATE_PREPARING,
    STATE_TURNING_WATCH,
    STATE_CONFIRMED,
    STATE_INVALIDATED,
)

SESSION_PIT_MODE = "session_pit"
POST_CLOSE_WATCHLIST_MODE = "post_close_watchlist"
HISTORICAL_RECONSTRUCTION_MODE = "historical_reconstruction"
DECISION_MODES = (
    SESSION_PIT_MODE,
    POST_CLOSE_WATCHLIST_MODE,
    HISTORICAL_RECONSTRUCTION_MODE,
)

PRICE_BASIS = "total_return_adjusted"
FORBIDDEN_DECISION_PROVIDERS = frozenset({"sina", "efinance"})
MINIMUM_PEER_COUNT = 5
FINE_CONTEXT_MAX_AGE_DAYS = 7
INDUSTRY_CONTEXT_MAX_AGE_DAYS = 370
MINIMUM_BATCH_QUALIFIERS = 3
BATCH_BREADTH_MINIMUM = 0.20
BROAD_INDUSTRY_BATCH_QUALIFIER_CAP = 5
VOLUME_LOOKBACK = 120
BASE_VOLUME_LOOKBACK = 20
BASE_RELATIVE_VOLUME_MIN = 1.20
BASE_AMOUNT_PERCENTILE_MIN = 0.70
STANDARD_HISTORY = 120
LOW_BASE_SETUP_MEMORY_SESSIONS = 10
LOW_BASE_REQUIRED_HISTORY = STANDARD_HISTORY + LOW_BASE_SETUP_MEMORY_SESSIONS - 1
LOW_BASE_RANGE_POSITION_MAX = 0.40
LOW_BASE_DRAWDOWN_MAX = -0.25
LOW_BASE_VOLUME_EXPANSION_MIN = 1.30
LOW_BASE_RELATIVE_VOLUME_MIN = 1.20
LOW_BASE_RELATIVE_VOLUME_DAYS_MIN = 2
LOW_BASE_SINGLE_DAY_VOLUME_WATCH_MIN = 2.00
LOW_BASE_RETURN_5_MAX = 0.25
LOW_BASE_OVEREXTENSION_ATR_MAX = 1.50
LOW_BASE_OVEREXTENSION_ATR_HARD_MAX = 2.00
LOW_BASE_WATCH_WINDOW_SESSIONS = 5
LOW_BASE_CONFIRMATION_WINDOW_SESSIONS = 3
BREAKOUT_LOOKBACK = 20
REPAIR_LOOKBACK = 120
REPAIR_HISTORY = 180
MA5_COST_BPS_PER_SIDE = 5.0
MA5_SLIPPAGE_BPS_PER_SIDE = 5.0
V2_CODE_VERSION = "dual-universe-leader-tactics-v2-exit-facts-v1"


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
    # ``statistics.mean`` promotes float inputs through its exact-ratio path.
    # Formula inputs are already finite floats, so fsum preserves numerical
    # stability without allocating Fraction intermediates for every MA/ATR.
    result = math.fsum(parsed) / len(parsed)
    return result if math.isfinite(result) else None


def _latest_history_percentile(latest: object, history: Sequence[object]) -> float | None:
    """Return the PIT empirical percentile of ``latest`` against prior values."""

    latest_value = _finite(latest)
    prior = [value for raw in history if (value := _finite(raw)) is not None]
    if latest_value is None or not prior:
        return None
    below = sum(value < latest_value for value in prior)
    equal = sum(value == latest_value for value in prior)
    return (below + 0.5 * equal) / len(prior)


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
class LowBaseSourceCapture:
    capture_id: str
    title: str
    captured_content_hash: str
    received_at: datetime
    disclosed_rules: tuple[str, ...]
    source_kind: str = "user_supplied_capture"
    publication_status: str = "unknown"

    def validate(self) -> None:
        if not self.capture_id.strip() or not self.title.strip():
            raise V2ContractError("low-base source capture identity is incomplete")
        _require_sha256(self.captured_content_hash, "captured_content_hash")
        if self.source_kind != "user_supplied_capture":
            raise V2ContractError("low-base source capture kind is incompatible")
        if self.publication_status != "unknown" or not self.disclosed_rules:
            raise V2ContractError("low-base source capture metadata is incomplete")


LOW_BASE_HYPOTHESIS_RECEIVED_AT = datetime(2026, 8, 17, 0, 0)
LOW_BASE_SOURCE_CAPTURES = (
    LowBaseSourceCapture(
        capture_id="low-base-catchup-user-capture-conditions",
        title="创新药低位补涨公开条件（用户截图一）",
        captured_content_hash=(
            "91a7a86cd795792e2a608bd5fcc3a14fbb4442ae74b644759e8a287a0291a817"
        ),
        received_at=LOW_BASE_HYPOTHESIS_RECEIVED_AT,
        disclosed_rules=(
            "oversold_low_base",
            "hot_theme",
            "repeated_bottom_volume",
            "initial_strengthening",
            "proprietary_signal_unavailable",
        ),
    ),
    LowBaseSourceCapture(
        capture_id="low-base-catchup-user-capture-chart",
        title="誉衡药业底部连续放量案例（用户截图二）",
        captured_content_hash=(
            "2cb8e0057edbae11c005985408271fb69844e1907f36c0fe2273ce50c7e0a143"
        ),
        received_at=LOW_BASE_HYPOTHESIS_RECEIVED_AT,
        disclosed_rules=(
            "repeated_bottom_volume",
            "initial_strengthening",
            "proprietary_signal_unavailable",
        ),
    ),
)
for _capture in LOW_BASE_SOURCE_CAPTURES:
    _capture.validate()
LOW_BASE_SOURCE_REGISTRY_VERSION = "low_base_catchup_source_registry_v1"
LOW_BASE_SOURCE_REGISTRY_HASH = stable_contract_hash(
    {
        "version": LOW_BASE_SOURCE_REGISTRY_VERSION,
        "captures": tuple(asdict(item) for item in LOW_BASE_SOURCE_CAPTURES),
        "publication_time_policy": "unknown_not_backfilled",
        "non_equivalence_notice": "transparent proxy, not proprietary takeoff signal",
    }
)


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
        "ma20_T>=ma20_T-5 and (volume_T>=1.20*mean(volume[T-20:T-1]) or "
        "percentile(amount_T,amount[T-20:T-1])>=0.70) and atr5/atr20<=0.90 and "
        "abs(adjusted_close-adjusted_MA20)/adjusted_ATR20<=1.50",
        (
            ("cross_window", 3),
            ("atr5_atr20_max", 0.90),
            ("overextension_atr_max", 1.50),
            ("relative_volume_20_min", BASE_RELATIVE_VOLUME_MIN),
            ("amount_vs_prior_20_percentile_min", BASE_AMOUNT_PERCENTILE_MIN),
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
    _formula(
        LOW_BASE_CATCHUP_V1,
        LOW_BASE_REQUIRED_HISTORY,
        "ashare and hot_theme and exists(d in T-9:T,range_position_120_d<=0.40) and "
        "exists(d in T-9:T,drawdown_120_d<=-0.25) and "
        "exists(d in T-9:T,mean(volume[d-4:d])/mean(volume[d-24:d-5])>=1.30 "
        "and count(volume[j]/mean(volume[j-20:j-1])>=1.20,j=d-4:d)>=2) and "
        "close_T>ma5_T>ma5_T-1 and close_T>max(high[T-5:T-1]) and close_T>close_T-1 "
        "and return_5<=0.25; entry actionable only when "
        "abs(adjusted_close-adjusted_MA20)/adjusted_ATR20<=1.50, "
        "extended_watch when <=2.00, otherwise overextended; a launch with "
        "latest_relative_volume>=2.00 but without repeated-volume confirmation "
        "is watch-only",
        (
            ("setup_memory_sessions", LOW_BASE_SETUP_MEMORY_SESSIONS),
            ("range_position_120_max", LOW_BASE_RANGE_POSITION_MAX),
            ("drawdown_120_max", LOW_BASE_DRAWDOWN_MAX),
            ("volume_expansion_5v20_min", LOW_BASE_VOLUME_EXPANSION_MIN),
            ("relative_volume_min", LOW_BASE_RELATIVE_VOLUME_MIN),
            ("relative_volume_days_min", LOW_BASE_RELATIVE_VOLUME_DAYS_MIN),
            (
                "single_day_volume_watch_min",
                LOW_BASE_SINGLE_DAY_VOLUME_WATCH_MIN,
            ),
            ("return_5_max", LOW_BASE_RETURN_5_MAX),
            ("overextension_atr_max", LOW_BASE_OVEREXTENSION_ATR_MAX),
            ("overextension_atr_hard_max", LOW_BASE_OVEREXTENSION_ATR_HARD_MAX),
            ("hot_score_min", 2 / 3),
            ("peer_count_min", MINIMUM_PEER_COUNT),
            ("watch_window_sessions", LOW_BASE_WATCH_WINDOW_SESSIONS),
            ("confirmation_window_sessions", LOW_BASE_CONFIRMATION_WINDOW_SESSIONS),
            ("source_registry_hash", LOW_BASE_SOURCE_REGISTRY_HASH),
        ),
    ),
)
V2_FORMULA_REGISTRY_HASH = stable_contract_hash(
    {
        "version": V2_FORMULA_REGISTRY_VERSION,
        "formula_hashes": tuple(item.formula_hash for item in V2_FORMULAS),
        "batch_confirmation_policy": {
            "minimum_qualifiers": MINIMUM_BATCH_QUALIFIERS,
            "fine_theme_breadth_minimum": BATCH_BREADTH_MINIMUM,
            "broad_industry_qualifier_cap": BROAD_INDUSTRY_BATCH_QUALIFIER_CAP,
            "applies_to": (BASE_LAUNCH_V2,),
            "breakout_confirmation": "hot_theme_and_core_leader",
            "repair_confirmation": "individual_former_leader",
        },
        "low_base_source_registry_hash": LOW_BASE_SOURCE_REGISTRY_HASH,
    }
)
V2_LEGACY_FORMULA_REGISTRY_PAIRS = frozenset(
    {
        (
            "a34045d97551e2bc1c58d5c5f9b60a4942a4be1f0c22075f4852d3deb3fb6a1c",
            "leader_tactics_v2_input_hash_v5",
        ),
        (
            "f8461fdd97d75fc06985edf5b7d839b07d85bd42406ef3fb3afee6dd17447b9e",
            "leader_tactics_v2_input_hash_v5",
        ),
        (
            "d270a37280775b8fb51721380dcd757e1442c2a4b360a7c68e352ec07900fdb5",
            "leader_tactics_v2_input_hash_v5",
        ),
        (
            "f85819290cc7ec5105c8622c23567d9f8d76559f246d685070414a4187bbb89e",
            "leader_tactics_v2_input_hash_v3",
        ),
        (
            "978c114bf9b60b89818fd1e2755d80764e496c6eed0a506b1a615877aee2ea5d",
            "leader_tactics_v2_input_hash_v4",
        ),
    }
)


@dataclass(frozen=True, slots=True)
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
    fact_hash: str = ""


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
    fact_hash_contract: str = "v2_membership"
    source_asset_code: str | None = None
    source: str | None = None
    confidence: str | float | None = None
    supersedes_fact_hash: str | None = None
    hierarchy_level: str = "broad_industry"
    normalized_theme_key: str | None = None
    resolution_mode: str = "broad_industry_fallback"
    fallback_reason: str | None = None
    relation_kind: str = "broad_industry"
    registry_priority: int = 100
    hierarchy_depth: int = 1
    industry_path: tuple[tuple[str, str, str], ...] = ()
    snapshot_date: date | None = None
    snapshot_hash: str | None = None
    snapshot_complete: bool | None = None
    eligible_peer_count: int | None = None
    theme_state_hash: str | None = None
    theme_state_available: bool | None = None
    theme_state_percentiles: tuple[float, float, float] | None = None
    theme_state_unavailable_reasons: tuple[str, ...] = ()
    taxonomy: str | None = None
    provider_theme_code: str | None = None
    provider_theme_label: str | None = None
    membership_reason: str | None = None
    exposure_weight: float | None = None
    capture_run_hash: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        # Avoid dataclasses.asdict's recursive deepcopy in the cross-section
        # hot path while retaining the exact canonical field contract.
        return {
            "group_id": self.group_id,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "observed_at": self.observed_at,
            "mapping_kind": self.mapping_kind,
            "taxonomy_version": self.taxonomy_version,
            "theme": self.theme,
            "sector": self.sector,
            "tracked_index": self.tracked_index,
            "clone_group": self.clone_group,
            "issuer": self.issuer,
            "hierarchy_level": self.hierarchy_level,
            "normalized_theme_key": self.normalized_theme_key,
            "resolution_mode": self.resolution_mode,
            "fallback_reason": self.fallback_reason,
        }

    def fact_identity_payload(self, *, asset_code: str) -> dict[str, Any]:
        if self.fact_hash_contract == ASHARE_INDUSTRY_PATH_FACT_HASH_CONTRACT:
            levels = {level: (code, label) for level, code, label in self.industry_path}
            return {
                "schema_version": ASHARE_THEME_GRAPH_SCHEMA_VERSION,
                "fact_type": "ashare_industry_path",
                "asset_code": self.source_asset_code or asset_code,
                "taxonomy": self.taxonomy,
                "taxonomy_version": self.taxonomy_version,
                "mapping_kind": self.mapping_kind,
                "level1_code": levels.get("industry_l1", (None, None))[0],
                "level1_label": levels.get("industry_l1", (None, None))[1],
                "level2_code": levels.get("industry_l2", (None, None))[0],
                "level2_label": levels.get("industry_l2", (None, None))[1],
                "level3_code": levels.get("industry_l3", (None, None))[0],
                "level3_label": levels.get("industry_l3", (None, None))[1],
                "effective_from": self.effective_from,
                "effective_to": self.effective_to,
                "snapshot_date": self.snapshot_date,
                "received_at": self.observed_at,
                "source": self.source,
                "confidence": self.confidence,
                "source_snapshot_hash": self.snapshot_hash,
            }
        if self.fact_hash_contract == ASHARE_THEME_RELATION_FACT_HASH_CONTRACT:
            return {
                "schema_version": ASHARE_THEME_GRAPH_SCHEMA_VERSION,
                "fact_type": "ashare_theme_relation",
                "asset_code": self.source_asset_code or asset_code,
                "canonical_theme_key": self.normalized_theme_key,
                "theme_label": self.theme,
                "relation_kind": self.relation_kind,
                "effective_from": self.effective_from,
                "effective_to": self.effective_to,
                "received_at": self.observed_at,
                "taxonomy_version": self.taxonomy_version,
                "source": self.source,
                "confidence": self.confidence,
                "source_snapshot_date": self.snapshot_date,
                "source_snapshot_hash": self.snapshot_hash,
                "capture_run_hash": self.capture_run_hash,
                "provider_theme_code": self.provider_theme_code,
                "provider_theme_label": self.provider_theme_label,
                "membership_reason": self.membership_reason,
                "exposure_weight": self.exposure_weight,
            }
        if self.fact_hash_contract == ASHARE_FINE_THEME_FACT_HASH_CONTRACT:
            return {
                "schema_version": ASHARE_FINE_THEME_FACT_HASH_CONTRACT,
                "fact_type": "ashare_fine_theme_membership",
                "asset_code": self.source_asset_code or asset_code,
                "group_id": self.group_id,
                "theme": self.theme,
                "normalized_theme_key": self.normalized_theme_key,
                "hierarchy_level": self.hierarchy_level,
                "effective_from": self.effective_from,
                "effective_to": self.effective_to,
                "received_at": self.observed_at,
                "taxonomy_version": self.taxonomy_version,
                "source": self.source,
                "confidence": self.confidence,
                "mapping_kind": self.mapping_kind,
            }
        if self.fact_hash_contract != ASHARE_MEMBERSHIP_FACT_HASH_CONTRACT:
            return self.canonical_payload()
        return {
            "schema_version": ASHARE_MEMBERSHIP_FACT_HASH_CONTRACT,
            "fact_type": "ashare_theme_membership",
            "asset_code": self.source_asset_code or asset_code,
            "group_id": self.group_id,
            "theme": self.theme,
            "sector": self.sector,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "received_at": self.observed_at,
            "taxonomy_version": self.taxonomy_version,
            "source": self.source,
            "confidence": self.confidence,
            "supersedes_fact_hash": self.supersedes_fact_hash,
            "mapping_kind": self.mapping_kind,
            "tracked_index": self.tracked_index,
            "clone_group": self.clone_group,
            "issuer": self.issuer,
        }


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
    decision_mode: Literal[
        "session_pit", "post_close_watchlist", "historical_reconstruction"
    ] = SESSION_PIT_MODE
    membership_evaluation_date: date | None = None
    next_eligible_date: date | None = None
    primary_industry: V2PITMembership | None = None
    theme_memberships: tuple[V2PITMembership, ...] = ()
    alternative_memberships: tuple[V2PITMembership, ...] = ()
    rejected_contexts: tuple[tuple[str, str], ...] = ()
    identity_cutoff: datetime | None = None


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
    gate_facts: tuple[tuple[str, Any], ...]
    exclusion_reasons: tuple[str, ...]
    source_cutoff: datetime
    theme: str | None
    sector: str | None
    tracked_index: str | None
    clone_group: str | None
    issuer: str | None
    feature_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        # Keep this payload byte-for-byte compatible with ``asdict(self)``
        # minus feature_hash, without recursively deepcopying every gate fact.
        return {
            "universe": self.universe,
            "asset_code": self.asset_code,
            "asset_name": self.asset_name,
            "signal_date": self.signal_date,
            "formula_id": self.formula_id,
            "state": self.state,
            "availability": self.availability,
            "qualifies": self.qualifies,
            "score": self.score,
            "gate_facts": self.gate_facts,
            "exclusion_reasons": self.exclusion_reasons,
            "source_cutoff": self.source_cutoff,
            "theme": self.theme,
            "sector": self.sector,
            "tracked_index": self.tracked_index,
            "clone_group": self.clone_group,
            "issuer": self.issuer,
        }


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
    code_version: str = V2_CODE_VERSION
    provider_health: tuple[tuple[str, str], ...] = ()

    @property
    def qualifying(self) -> tuple[V2CandidateObservation, ...]:
        return tuple(item for item in self.observations if item.qualifies)


@dataclass(frozen=True, slots=True)
class V2StagedAssetFeature:
    """Small durable Stage-A projection; it deliberately contains no bar objects."""

    asset_code: str
    asset_name: str
    group_key: str | None
    clone_group: str | None
    standard_available: bool
    return_1: float | None
    return_5: float | None
    mean_amount_20: float | None
    input_digest: str
    return_20: float | None = None
    below_adjusted_ma5: bool | None = None
    selected_context_hash: str | None = None
    alternative_context_hashes: tuple[str, ...] = ()
    rejected_contexts: tuple[tuple[str, str], ...] = ()
    theme_state_hash: str | None = None
    selected_context_relation_kind: str | None = None
    selected_context_snapshot_hash: str | None = None


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
    input_hash_schema_version: str = V2_INPUT_HASH_SCHEMA_VERSION
    formula_ids: tuple[str, ...] = LEGACY_V2_CANDIDATE_IDS
    adjustment_version: str = PRICE_BASIS
    taxonomy_version: str = "pit_theme_taxonomy_v2"
    cost_model: tuple[tuple[str, float], ...] = (
        ("fee_bps_per_side", 5.0),
        ("slippage_bps_per_side", 5.0),
    )
    clone_policy: str = "one_most_liquid_representative_per_pit_clone_group"
    state_policy: str = "preparing_turning_watch_confirmed_invalidated_v3"
    pagination_cursor: str | None = None
    exclusions: tuple[tuple[str, int], ...] = ()
    provider_health: tuple[tuple[str, str], ...] = ()
    runtime_budget_seconds: float = 55.0
    sentiment_risk_contract_hash: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("manifest_hash")
        if payload["sentiment_risk_contract_hash"] is None:
            payload.pop("sentiment_risk_contract_hash")
        return payload

    def validate(self) -> None:
        if self.universe not in SUPPORTED_UNIVERSES:
            raise V2ContractError("manifest universe is unsupported")
        if self.decision_cutoff > self.data_receipt_cutoff:
            raise V2ContractError("decision cutoff cannot exceed receipt cutoff")
        _require_sha256(self.input_hash, "input_hash")
        if self.source_registry_hash != V2_SOURCE_REGISTRY.registry_hash:
            raise V2ContractError("source registry hash is incompatible")
        registry_pair = (self.formula_registry_hash, self.input_hash_schema_version)
        if (
            registry_pair != (V2_FORMULA_REGISTRY_HASH, V2_INPUT_HASH_SCHEMA_VERSION)
            and registry_pair not in V2_LEGACY_FORMULA_REGISTRY_PAIRS
        ):
            raise V2ContractError("formula registry hash is incompatible")
        expected_formula_ids = (
            V2_CANDIDATE_IDS if self.universe == UNIVERSE_ASHARE else LEGACY_V2_CANDIDATE_IDS
        )
        legacy_formula_ids = (
            registry_pair in V2_LEGACY_FORMULA_REGISTRY_PAIRS
            and tuple(self.formula_ids) == LEGACY_V2_CANDIDATE_IDS
        )
        if tuple(self.formula_ids) != expected_formula_ids and not legacy_formula_ids:
            raise V2ContractError("manifest candidate set is not frozen")
        if self.adjustment_version != PRICE_BASIS:
            raise V2ContractError("manifest adjustment basis is incompatible")
        if not self.code_version.strip() or not self.holdout_identity.strip():
            raise V2ContractError("manifest code and holdout identity are required")
        if self.research_only is not True:
            raise V2ContractError("V2 manifest must be research-only")
        if self.runtime_budget_seconds <= 0 or self.runtime_budget_seconds > 55:
            raise V2ContractError("manifest runtime budget exceeds the V2 bound")
        if (
            self.sentiment_risk_contract_hash is not None
            and self.sentiment_risk_contract_hash
            != ASHARE_SENTIMENT_RISK_CONTRACT_HASH
        ):
            raise V2ContractError("sentiment risk contract hash is incompatible")
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
    code_version: str = V2_CODE_VERSION,
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
        formula_ids=(
            V2_CANDIDATE_IDS if universe == UNIVERSE_ASHARE else LEGACY_V2_CANDIDATE_IDS
        ),
        pagination_cursor=pagination_cursor,
        exclusions=exclusions,
        provider_health=provider_health,
        sentiment_risk_contract_hash=(
            ASHARE_SENTIMENT_RISK_CONTRACT_HASH
            if universe == UNIVERSE_ASHARE
            else None
        ),
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
        raise V2ContractError("V2 candidate registry is incompatible")
    if source_registry_hash != V2_SOURCE_REGISTRY.registry_hash:
        raise V2ContractError("source registry substitution is not allowed")
    if formula_registry_hash != V2_FORMULA_REGISTRY_HASH:
        raise V2ContractError("formula registry substitution is not allowed")
    if holdout_identity != "holdout-2026-08-03-single-use-v1":
        raise V2ContractError("holdout identity substitution is not allowed")


_CONTEXT_RELATION_PRIORITY = {
    "provider_concept": 0,
    "industry_union_proxy": 1,
    "industry_l3": 2,
    "industry_l2": 3,
    "industry_l1": 4,
    "broad_industry": 5,
}
_CONTEXT_CONFIDENCE_PRIORITY = {
    "provider": 0,
    "authoritative": 0,
    "high": 1,
    "research_proxy": 2,
    "medium": 3,
    "fallback": 4,
}


def _context_identity(membership: V2PITMembership) -> tuple[Any, ...]:
    return (
        membership.group_id,
        membership.fact_hash,
        membership.relation_kind,
        membership.snapshot_date,
        membership.snapshot_hash,
        membership.theme_state_hash,
    )


def _context_sort_key(membership: V2PITMembership) -> tuple[Any, ...]:
    relation_kind = membership.relation_kind or membership.hierarchy_level
    confidence = membership.confidence
    confidence_priority = (
        -float(confidence)
        if isinstance(confidence, int | float) and not isinstance(confidence, bool)
        else _CONTEXT_CONFIDENCE_PRIORITY.get(str(confidence or "").lower(), 9)
    )
    return (
        _CONTEXT_RELATION_PRIORITY.get(relation_kind, 99),
        confidence_priority,
        membership.registry_priority,
        -membership.hierarchy_depth,
        membership.group_id,
        membership.fact_hash,
    )


def _context_rejection_reason(
    item: V2AssetInput,
    membership: V2PITMembership,
    *,
    peer_count: int,
) -> str | None:
    evaluation_date = item.membership_evaluation_date or item.signal_date
    if not membership.group_id.strip():
        return "missing_pit_peer_group"
    accepted_mapping_kinds = {
        "historical_pit",
        "primary_hierarchy",
        "broad_fallback",
    }
    if item.decision_mode == HISTORICAL_RECONSTRUCTION_MODE:
        accepted_mapping_kinds.add("current_vintage_proxy")
    if membership.mapping_kind not in accepted_mapping_kinds:
        return "taxonomy_not_point_in_time"
    if membership.effective_from > evaluation_date:
        return "membership_effective_after_signal"
    if membership.effective_to is not None and membership.effective_to < evaluation_date:
        return "membership_expired_before_signal"
    if membership.observed_at > (item.identity_cutoff or item.source_cutoff):
        return "membership_received_after_cutoff"
    if membership.snapshot_complete is False:
        return "partial_theme_capture"
    if not membership.fact_hash:
        return "missing_membership_fact_hash"
    if membership.fact_hash != stable_contract_hash(
        membership.fact_identity_payload(asset_code=item.asset_code)
    ):
        return "membership_fact_hash_mismatch"
    relation_kind = membership.relation_kind or membership.hierarchy_level
    snapshot_date = membership.snapshot_date or membership.effective_from
    max_age_days = (
        FINE_CONTEXT_MAX_AGE_DAYS
        if relation_kind in {"provider_concept", "industry_union_proxy"}
        or membership.hierarchy_level == "fine_theme"
        else INDUSTRY_CONTEXT_MAX_AGE_DAYS
    )
    if (evaluation_date - snapshot_date).days > max_age_days:
        return "stale_context_snapshot"
    if peer_count < MINIMUM_PEER_COUNT:
        return "insufficient_context_peers"
    if membership.theme_state_available is False:
        return (
            membership.theme_state_unavailable_reasons[0]
            if membership.theme_state_unavailable_reasons
            else "theme_state_unavailable"
        )
    if membership.theme_state_available is True:
        percentiles = membership.theme_state_percentiles
        if (
            not membership.theme_state_hash
            or percentiles is None
            or len(percentiles) != 3
            or any(_finite(value) is None for value in percentiles)
        ):
            return "theme_state_unavailable"
    return None


def resolve_v2_asset_contexts(
    items: Sequence[V2AssetInput],
) -> tuple[V2AssetInput, ...]:
    """Resolve one peer context while retaining every cutoff-compatible relation.

    Legacy callers that only provide ``membership`` are returned unchanged.
    Graph-aware callers provide ``primary_industry`` and/or
    ``theme_memberships``.  Resolution never consumes price outcomes or the
    formula score; its order is frozen by relation kind, confidence, registry
    priority, hierarchy depth and stable identity.
    """

    graph_asset_codes = {
        item.asset_code
        for item in items
        if item.primary_industry is not None or bool(item.theme_memberships)
    }
    if not graph_asset_codes:
        return tuple(items)

    eligible_assets = {
        item.asset_code
        for item in items
        if not item.input_unavailable_reasons and not _bar_reasons(item, STANDARD_HISTORY)
    }
    observed_peer_counts: dict[str, set[str]] = defaultdict(set)
    contexts_by_asset: dict[str, tuple[V2PITMembership, ...]] = {}
    for item in items:
        candidates = (
            *item.theme_memberships,
            *((item.primary_industry,) if item.primary_industry is not None else ()),
            *((item.membership,) if item.membership is not None else ()),
        )
        unique: dict[tuple[Any, ...], V2PITMembership] = {}
        for membership in candidates:
            unique.setdefault(_context_identity(membership), membership)
        contexts = tuple(sorted(unique.values(), key=_context_sort_key))
        contexts_by_asset[item.asset_code] = contexts
        if item.asset_code in eligible_assets:
            for membership in contexts:
                observed_peer_counts[membership.group_id].add(item.asset_code)

    resolved: list[V2AssetInput] = []
    for item in items:
        contexts = contexts_by_asset[item.asset_code]
        if item.asset_code not in graph_asset_codes:
            resolved.append(item)
            continue
        accepted: list[V2PITMembership] = []
        rejected: list[tuple[str, str]] = []
        for membership in contexts:
            peer_count = (
                membership.eligible_peer_count
                if membership.eligible_peer_count is not None
                else len(observed_peer_counts[membership.group_id])
            )
            reason = _context_rejection_reason(
                item,
                membership,
                peer_count=peer_count,
            )
            if reason is None:
                accepted.append(membership)
            else:
                rejected.append((membership.group_id, reason))
        selected = accepted[0] if accepted else None
        reasons = set(item.input_unavailable_reasons)
        if selected is None:
            reasons.add("missing_compatible_peer_context")
        resolved.append(
            replace(
                item,
                membership=selected,
                alternative_memberships=tuple(accepted[1:]),
                rejected_contexts=tuple(sorted(set(rejected))),
                input_unavailable_reasons=tuple(sorted(reasons)),
            )
        )
    return tuple(resolved)


def _membership_reasons(item: V2AssetInput) -> list[str]:
    membership = item.membership
    reasons = list(item.input_unavailable_reasons)
    membership_date = item.membership_evaluation_date or item.signal_date
    if item.decision_mode not in DECISION_MODES:
        reasons.append("decision_mode_unsupported")
    elif item.decision_mode == SESSION_PIT_MODE:
        if membership_date != item.signal_date:
            reasons.append("session_pit_membership_date_mismatch")
    elif item.decision_mode == HISTORICAL_RECONSTRUCTION_MODE:
        if membership_date < item.signal_date:
            reasons.append("historical_membership_date_before_signal")
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
    accepted_mapping_kinds = {
        "historical_pit",
        "primary_hierarchy",
        "broad_fallback",
    }
    if item.decision_mode == HISTORICAL_RECONSTRUCTION_MODE:
        accepted_mapping_kinds.add("current_vintage_proxy")
    if membership.mapping_kind not in accepted_mapping_kinds:
        reasons.append("taxonomy_not_point_in_time")
    if membership.effective_from > membership_date:
        reasons.append("membership_effective_after_signal")
    if membership.effective_to is not None and membership.effective_to < membership_date:
        reasons.append("membership_expired_before_signal")
    if membership.observed_at > (item.identity_cutoff or item.source_cutoff):
        reasons.append("membership_received_after_cutoff")
    if not membership.fact_hash:
        reasons.append("missing_membership_fact_hash")
    elif membership.fact_hash != stable_contract_hash(
        membership.fact_identity_payload(asset_code=item.asset_code)
    ):
        reasons.append("membership_fact_hash_mismatch")
    return sorted(set(reasons))


def _bar_reasons(item: V2AssetInput, required_history: int) -> list[str]:
    reasons: set[str] = set()
    if len(item.bars) < required_history:
        reasons.add("insufficient_adjusted_history")
    if not item.bars or item.bars[-1].trade_date != item.signal_date:
        reasons.add("adjusted_history_not_current_to_signal")
    previous_date: date | None = None
    for bar in item.bars:
        if previous_date is not None and bar.trade_date <= previous_date:
            reasons.add("adjusted_history_not_canonical")
        previous_date = bar.trade_date
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
            reasons.add("adjusted_bar_not_decision_eligible")
        if bar.price_basis != PRICE_BASIS:
            reasons.add("wrong_price_basis")
        if bar.provider.strip().lower() in FORBIDDEN_DECISION_PROVIDERS:
            reasons.add("forbidden_raw_price_provider")
        if (
            not bar.provider.strip()
            or not bar.adjustment_version.strip()
            or not bar.revision_id.strip()
        ):
            reasons.add("missing_adjusted_provenance")
        if bar.observed_at.date() < bar.trade_date:
            reasons.add("adjusted_bar_received_before_trade_date")
        if bar.observed_at > item.source_cutoff:
            reasons.add("adjusted_bar_received_after_cutoff")
        for value in values:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                reasons.add("non_finite_adjusted_input")
                break
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
            reasons.add("invalid_adjusted_ohlcv")
    return sorted(reasons)


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


def _low_base_catchup_features(item: V2AssetInput) -> dict[str, Any]:
    """Calculate frozen low-base facts without looking beyond the signal bar."""

    bars = item.bars
    if len(bars) < LOW_BASE_REQUIRED_HISTORY:
        return {}
    close = _finite(bars[-1].adjusted_close)
    previous_close = _finite(bars[-2].adjusted_close)
    setup_start = len(bars) - LOW_BASE_SETUP_MEMORY_SESSIONS
    setup_rows: list[dict[str, Any]] = []
    volume_rows: list[dict[str, Any]] = []
    for end_index in range(setup_start, len(bars)):
        setup_window = bars[end_index - STANDARD_HISTORY + 1 : end_index + 1]
        if len(setup_window) != STANDARD_HISTORY:
            continue
        evidence_close = _finite(bars[end_index].adjusted_close)
        closes_120 = [bar.adjusted_close for bar in setup_window]
        low_120 = min(closes_120)
        high_120 = max(closes_120)
        price_range = high_120 - low_120
        range_position = (
            (evidence_close - low_120) / price_range
            if evidence_close is not None and price_range > 0
            else None
        )
        drawdown = (
            evidence_close / high_120 - 1.0
            if evidence_close is not None and high_120 > 0
            else None
        )
        setup_rows.append(
            {
                "date": bars[end_index].trade_date,
                "range_low_120": low_120,
                "range_high_120": high_120,
                "range_position_120": range_position,
                "drawdown_120": drawdown,
            }
        )

        recent_five = bars[end_index - 4 : end_index + 1]
        disjoint_prior_twenty = bars[end_index - 24 : end_index - 4]
        recent_mean_volume = _mean_finite([bar.volume for bar in recent_five])
        prior_mean_volume = _mean_finite([bar.volume for bar in disjoint_prior_twenty])
        volume_expansion = (
            recent_mean_volume / prior_mean_volume
            if recent_mean_volume is not None and prior_mean_volume and prior_mean_volume > 0
            else None
        )
        relative_volume_values: list[float | None] = []
        for index in range(end_index - 4, end_index + 1):
            prior_volume = _mean_finite([bar.volume for bar in bars[index - 20 : index]])
            current_volume = _finite(bars[index].volume)
            relative_volume_values.append(
                current_volume / prior_volume
                if current_volume is not None and prior_volume and prior_volume > 0
                else None
            )
        relative_volume_days = sum(
            value is not None and value >= LOW_BASE_RELATIVE_VOLUME_MIN
            for value in relative_volume_values
        )
        volume_rows.append(
            {
                "date": bars[end_index].trade_date,
                "recent_5_mean_volume": recent_mean_volume,
                "disjoint_prior_20_mean_volume": prior_mean_volume,
                "volume_expansion_5v20": volume_expansion,
                "relative_volume_last_5": list(relative_volume_values),
                "relative_volume_confirmed_days": relative_volume_days,
            }
        )

    current_setup = setup_rows[-1]
    current_volume = volume_rows[-1]
    range_evidence = min(
        (row for row in setup_rows if row["range_position_120"] is not None),
        key=lambda row: (row["range_position_120"], row["date"]),
        default=None,
    )
    drawdown_evidence = min(
        (row for row in setup_rows if row["drawdown_120"] is not None),
        key=lambda row: (row["drawdown_120"], row["date"]),
        default=None,
    )
    qualifying_volume_rows = [
        row
        for row in volume_rows
        if row["volume_expansion_5v20"] is not None
        and row["volume_expansion_5v20"] >= LOW_BASE_VOLUME_EXPANSION_MIN
        and row["relative_volume_confirmed_days"] >= LOW_BASE_RELATIVE_VOLUME_DAYS_MIN
    ]
    volume_evidence = max(
        qualifying_volume_rows,
        key=lambda row: (row["volume_expansion_5v20"], row["date"]),
        default=None,
    )
    max_volume_expansion = max(
        (row for row in volume_rows if row["volume_expansion_5v20"] is not None),
        key=lambda row: (row["volume_expansion_5v20"], row["date"]),
        default=None,
    )
    max_relative_volume_days = max(
        volume_rows,
        key=lambda row: (
            row["relative_volume_confirmed_days"],
            row["volume_expansion_5v20"] or -math.inf,
            row["date"],
        ),
    )

    ma5 = _ma(item, 5)
    previous_ma5 = _ma(item, 5, len(bars) - 1)
    ma20 = _ma(item, 20)
    atr20 = _atr(item, 20)
    prior_five_high = max(bar.adjusted_high for bar in bars[-6:-1])
    return_5 = _return(item, 5)
    overextension = (
        abs(close - ma20) / atr20
        if close is not None and ma20 is not None and atr20
        else None
    )
    setup_range_position = (
        range_evidence["range_position_120"] if range_evidence is not None else None
    )
    setup_drawdown = drawdown_evidence["drawdown_120"] if drawdown_evidence else None
    low_base_gate = (
        setup_range_position is not None
        and setup_range_position <= LOW_BASE_RANGE_POSITION_MAX
        and setup_drawdown is not None
        and setup_drawdown <= LOW_BASE_DRAWDOWN_MAX
    )
    volume_gate = volume_evidence is not None
    turning_gate = (
        close is not None
        and previous_close is not None
        and ma5 is not None
        and previous_ma5 is not None
        and close > ma5
        and ma5 > previous_ma5
        and close > prior_five_high
        and close > previous_close
    )
    latest_relative_volume = current_volume["relative_volume_last_5"][-1]
    single_day_volume_watch = (
        not volume_gate
        and latest_relative_volume is not None
        and latest_relative_volume >= LOW_BASE_SINGLE_DAY_VOLUME_WATCH_MIN
        and turning_gate
    )
    if return_5 is None or overextension is None:
        extension_band = "unavailable"
    elif (
        return_5 > LOW_BASE_RETURN_5_MAX
        or overextension > LOW_BASE_OVEREXTENSION_ATR_HARD_MAX
    ):
        extension_band = "overextended"
    elif overextension > LOW_BASE_OVEREXTENSION_ATR_MAX:
        extension_band = "extended_watch"
    else:
        extension_band = "actionable"
    signal_quality = extension_band if turning_gate else "not_triggered"
    return {
        "setup_memory_sessions": LOW_BASE_SETUP_MEMORY_SESSIONS,
        "setup_memory_start_date": setup_rows[0]["date"].isoformat(),
        "setup_memory_end_date": setup_rows[-1]["date"].isoformat(),
        "setup_range_position_evidence_date": (
            range_evidence["date"].isoformat() if range_evidence else None
        ),
        "setup_range_position_120": setup_range_position,
        "setup_drawdown_evidence_date": (
            drawdown_evidence["date"].isoformat() if drawdown_evidence else None
        ),
        "setup_drawdown_120": setup_drawdown,
        "range_low_120": current_setup["range_low_120"],
        "range_high_120": current_setup["range_high_120"],
        "range_position_120": current_setup["range_position_120"],
        "drawdown_120": current_setup["drawdown_120"],
        "recent_5_mean_volume": current_volume["recent_5_mean_volume"],
        "disjoint_prior_20_mean_volume": current_volume["disjoint_prior_20_mean_volume"],
        "volume_expansion_5v20": current_volume["volume_expansion_5v20"],
        "relative_volume_last_5": current_volume["relative_volume_last_5"],
        "relative_volume_confirmed_days": current_volume[
            "relative_volume_confirmed_days"
        ],
        "latest_relative_volume": latest_relative_volume,
        "single_day_volume_watch": single_day_volume_watch,
        "volume_memory_evidence_date": (
            volume_evidence["date"].isoformat() if volume_evidence else None
        ),
        "volume_memory_expansion_5v20": (
            volume_evidence["volume_expansion_5v20"] if volume_evidence else None
        ),
        "volume_memory_relative_volume_confirmed_days": (
            volume_evidence["relative_volume_confirmed_days"] if volume_evidence else None
        ),
        "volume_memory_max_expansion_date": (
            max_volume_expansion["date"].isoformat() if max_volume_expansion else None
        ),
        "volume_memory_max_expansion_5v20": (
            max_volume_expansion["volume_expansion_5v20"]
            if max_volume_expansion
            else None
        ),
        "volume_memory_max_relative_days_date": max_relative_volume_days[
            "date"
        ].isoformat(),
        "volume_memory_max_relative_volume_confirmed_days": max_relative_volume_days[
            "relative_volume_confirmed_days"
        ],
        "adjusted_ma5": ma5,
        "adjusted_ma5_previous": previous_ma5,
        "adjusted_ma20": ma20,
        "adjusted_atr20": atr20,
        "signal_adjusted_close": close,
        "signal_adjusted_high": _finite(bars[-1].adjusted_high),
        "prior_5_adjusted_high": prior_five_high,
        "return_5": return_5,
        "overextension_atr": overextension,
        "low_base_gate": low_base_gate,
        "repeated_volume_gate": volume_gate,
        "initial_turning_gate": turning_gate,
        "launch_signal": turning_gate,
        "extension_band": extension_band,
        "signal_quality": signal_quality,
        "extended_watch": extension_band == "extended_watch",
        "overextended": extension_band == "overextended",
    }


def _group_key(item: V2AssetInput) -> str | None:
    return item.membership.group_id if item.membership is not None else None


def _required_batch_qualifiers(
    *, formula_id: str, peer_count: int, membership: V2PITMembership | None
) -> tuple[int, str]:
    """Return an auditable confirmation count for the observed peer hierarchy.

    Breakout already requires hot-theme breadth and an 80th-percentile core
    leader, while former-leader repair is explicitly an individual lifecycle.
    Requiring several peers to pass the *entire same formula* duplicates those
    gates. Only base-launch keeps a batch confirmation: fine themes use the
    proportional rule, and broad industries use a capped absolute count.
    """

    if formula_id == BREAKOUT_V2:
        return 1, "hot_theme_core_leader_v1"
    if formula_id == FORMER_LEADER_REPAIR_V2:
        return 1, "individual_former_leader_v1"
    if formula_id == LOW_BASE_CATCHUP_V1:
        return 1, "individual_low_base_catchup_v1"
    proportional = max(
        MINIMUM_BATCH_QUALIFIERS,
        math.ceil(max(peer_count, 0) * BATCH_BREADTH_MINIMUM),
    )
    if membership is not None and membership.hierarchy_level == "fine_theme":
        return proportional, "fine_theme_proportional_v1"
    return (
        min(proportional, BROAD_INDUSTRY_BATCH_QUALIFIER_CAP),
        "broad_industry_capped_v1",
    )


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


def _incremental_input_hash(items: Sequence[V2AssetInput]) -> str:
    """Hash canonical factual inputs with bounded, content-addressed bar identity."""

    hasher = hashlib.sha256()
    hasher.update(V2_INPUT_HASH_SCHEMA_VERSION.encode("utf-8"))
    for item in items:
        membership = item.membership
        def membership_identity(value: V2PITMembership) -> tuple[Any, ...]:
            return (
                value.group_id,
                value.effective_from,
                value.effective_to,
                value.observed_at,
                value.mapping_kind,
                value.taxonomy_version,
                value.theme,
                value.sector,
                value.tracked_index,
                value.clone_group,
                value.issuer,
                value.fact_hash,
                value.fact_hash_contract,
                value.source_asset_code,
                value.source,
                value.confidence,
                value.supersedes_fact_hash,
                value.hierarchy_level,
                value.normalized_theme_key,
                value.resolution_mode,
                value.fallback_reason,
                value.relation_kind,
                value.registry_priority,
                value.hierarchy_depth,
                value.industry_path,
                value.snapshot_date,
                value.snapshot_hash,
                value.snapshot_complete,
                value.eligible_peer_count,
                value.theme_state_hash,
                value.theme_state_available,
                value.theme_state_percentiles,
                value.theme_state_unavailable_reasons,
                value.taxonomy,
                value.provider_theme_code,
                value.provider_theme_label,
                value.membership_reason,
                value.exposure_weight,
                value.capture_run_hash,
            )

        metadata = (
            item.universe,
            item.asset_code,
            item.asset_name,
            item.signal_date,
            item.source_cutoff,
            item.baseline_score,
            item.decision_mode,
            item.membership_evaluation_date,
            item.next_eligible_date,
            sorted(item.input_unavailable_reasons),
            None if membership is None else membership_identity(membership),
            (
                membership_identity(item.primary_industry)
                if item.primary_industry is not None
                else None
            ),
            tuple(membership_identity(value) for value in item.theme_memberships),
            tuple(membership_identity(value) for value in item.alternative_memberships),
            item.rejected_contexts,
        )
        encoded_metadata = json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda value: (
                value.isoformat() if isinstance(value, (date, datetime)) else str(value)
            ),
        ).encode("utf-8")
        hasher.update(len(encoded_metadata).to_bytes(8, "big"))
        hasher.update(encoded_metadata)

        encoded_fact_hashes: list[bytes] = []
        for bar in item.bars:
            value = bar.fact_hash
            if len(value) != 64 or value != value.lower():
                break
            try:
                encoded = bytes.fromhex(value)
            except ValueError:
                break
            if len(encoded) != 32:
                break
            encoded_fact_hashes.append(encoded)
        if encoded_fact_hashes and len(encoded_fact_hashes) == len(item.bars):
            # Persisted fact hashes are generated from every adjusted OHLCV,
            # receipt and provenance field at ingestion. Reusing that content
            # identity avoids serializing roughly one million bars per run.
            hasher.update(b"H")
            hasher.update(len(encoded_fact_hashes).to_bytes(4, "big"))
            for value in encoded_fact_hashes:
                hasher.update(value)
            continue

        # Synthetic/legacy callers without verified fact identity retain a
        # full-field fallback so no input becomes invisible to the manifest.
        hasher.update(b"F")
        bar_payload = [
            (
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
                bar.fact_hash,
            )
            for bar in item.bars
        ]
        encoded_bars = json.dumps(
            bar_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda value: (
                value.isoformat() if isinstance(value, (date, datetime)) else str(value)
            ),
        ).encode("utf-8")
        hasher.update(len(encoded_bars).to_bytes(8, "big"))
        hasher.update(encoded_bars)
    return hasher.hexdigest()


def build_v2_staged_asset_feature(item: V2AssetInput) -> V2StagedAssetFeature:
    """Project one fully validated input into a bounded Stage-A row."""

    adjusted_ma5 = _ma(item, 5)
    adjusted_close = item.bars[-1].adjusted_close if item.bars else None
    return V2StagedAssetFeature(
        asset_code=item.asset_code,
        asset_name=item.asset_name,
        group_key=_group_key(item),
        clone_group=item.membership.clone_group if item.membership else None,
        standard_available=not _base_input_reasons(item, STANDARD_HISTORY),
        return_1=_return(item, 1),
        return_5=_return(item, 5),
        mean_amount_20=_mean_finite([bar.amount for bar in item.bars[-20:]]),
        input_digest=_incremental_input_hash((item,)),
        return_20=_return(item, 20),
        below_adjusted_ma5=(
            adjusted_close < adjusted_ma5
            if adjusted_close is not None and adjusted_ma5 is not None
            else None
        ),
        selected_context_hash=(item.membership.fact_hash if item.membership else None),
        alternative_context_hashes=tuple(
            membership.fact_hash for membership in item.alternative_memberships
        ),
        rejected_contexts=item.rejected_contexts,
        theme_state_hash=(item.membership.theme_state_hash if item.membership else None),
        selected_context_relation_kind=(
            item.membership.relation_kind if item.membership else None
        ),
        selected_context_snapshot_hash=(
            item.membership.snapshot_hash if item.membership else None
        ),
    )


def staged_theme_percentile_overrides(
    features: Sequence[V2StagedAssetFeature],
) -> dict[str, tuple[float, float, float]]:
    """Build global theme percentiles from compact rows only."""

    representatives: list[V2StagedAssetFeature] = []
    clones: dict[tuple[str, str], list[V2StagedAssetFeature]] = defaultdict(list)
    for feature in features:
        if not feature.standard_available or feature.group_key is None:
            continue
        if feature.clone_group:
            clones[(feature.group_key, feature.clone_group)].append(feature)
        else:
            representatives.append(feature)
    for rows in clones.values():
        representatives.append(
            max(
                rows,
                key=lambda row: (
                    row.mean_amount_20 if row.mean_amount_20 is not None else -math.inf,
                    row.asset_code,
                ),
            )
        )
    groups: dict[str, list[V2StagedAssetFeature]] = defaultdict(list)
    for feature in representatives:
        groups[feature.group_key].append(feature)  # type: ignore[index]
    one_day: dict[str, float] = {}
    five_day: dict[str, float] = {}
    breadth: dict[str, float] = {}
    for group, rows in groups.items():
        one = [row.return_1 for row in rows if row.return_1 is not None]
        five = [row.return_5 for row in rows if row.return_5 is not None]
        one_day[group] = _mean_finite(one) or 0.0
        five_day[group] = _mean_finite(five) or 0.0
        breadth[group] = sum(value > 0 for value in one) / len(one) if one else 0.0
    one_pct = _percentile(one_day)
    five_pct = _percentile(five_day)
    breadth_pct = _percentile(breadth)
    return {
        group: (one_pct[group], five_pct[group], breadth_pct[group]) for group in sorted(groups)
    }


def staged_sentiment_risk_snapshot(
    features: Sequence[V2StagedAssetFeature],
    *,
    theme_percentile_overrides: Mapping[str, tuple[float, float, float]],
    signal_date: date,
    source_cutoff: datetime,
) -> SentimentRiskSnapshot:
    """Build the market-wide risk snapshot from bounded Stage-A scalars."""

    representatives: list[V2StagedAssetFeature] = []
    clones: dict[tuple[str, str], list[V2StagedAssetFeature]] = defaultdict(list)
    for feature in features:
        if not feature.standard_available or feature.group_key is None:
            continue
        if feature.clone_group:
            clones[(feature.group_key, feature.clone_group)].append(feature)
        else:
            representatives.append(feature)
    for rows in clones.values():
        representatives.append(
            max(
                rows,
                key=lambda row: (
                    row.mean_amount_20
                    if row.mean_amount_20 is not None
                    else -math.inf,
                    row.asset_code,
                ),
            )
        )

    groups: dict[str, list[V2StagedAssetFeature]] = defaultdict(list)
    for feature in representatives:
        groups[feature.group_key].append(feature)  # type: ignore[index]
    points: list[SentimentRiskPoint] = []
    for group, rows in groups.items():
        return_20_pct = _percentile(
            {
                row.asset_code: row.return_20
                for row in rows
                if row.return_20 is not None
            }
        )
        return_5_pct = _percentile(
            {
                row.asset_code: row.return_5
                for row in rows
                if row.return_5 is not None
            }
        )
        amount_pct = _percentile(
            {
                row.asset_code: row.mean_amount_20
                for row in rows
                if row.mean_amount_20 is not None
            }
        )
        theme_components = theme_percentile_overrides.get(group)
        hot_score = _mean_finite(theme_components or ())
        for row in rows:
            core_score = _mean_finite(
                (
                    return_20_pct.get(row.asset_code),
                    return_5_pct.get(row.asset_code),
                    amount_pct.get(row.asset_code),
                )
            )
            if (
                hot_score is None
                or core_score is None
                or row.return_1 is None
                or row.below_adjusted_ma5 is None
            ):
                continue
            points.append(
                SentimentRiskPoint(
                    asset_code=row.asset_code,
                    theme_key=group,
                    hot_score=hot_score,
                    core_score=core_score,
                    return_1=row.return_1,
                    below_adjusted_ma5=row.below_adjusted_ma5,
                    signal_date=signal_date,
                    source_cutoff=source_cutoff,
                )
            )
    return calculate_sentiment_risk(
        points,
        signal_date=signal_date,
        source_cutoff=source_cutoff,
    )


def staged_v2_input_hash(features: Sequence[V2StagedAssetFeature]) -> str:
    return stable_contract_hash(
        {
            "schema_version": V2_INPUT_HASH_SCHEMA_VERSION,
            "asset_digests": tuple(
                (row.asset_code, row.input_digest)
                for row in sorted(features, key=lambda item: item.asset_code)
            ),
        }
    )


def _base_input_reasons(item: V2AssetInput, required_history: int) -> list[str]:
    return sorted(set((*_membership_reasons(item), *_bar_reasons(item, required_history))))


def _observation(
    *,
    item: V2AssetInput,
    formula_id: str,
    availability: Literal["available", "unavailable"],
    qualifies: bool,
    score: float | None,
    gate_facts: Mapping[str, Any],
    exclusion_reasons: Sequence[str],
    clone_excluded: bool,
    state: str = STATE_PREPARING,
    sentiment_risk: SentimentRiskSnapshot | None = None,
) -> V2CandidateObservation:
    reasons = tuple(sorted(set(exclusion_reasons)))
    facts = dict(gate_facts)
    membership_date = item.membership_evaluation_date or item.signal_date
    membership = item.membership
    primary = item.primary_industry
    industry_levels = {
        level: {"code": code, "label": label}
        for level, code, label in (primary.industry_path if primary else ())
    }
    selected_context = (
        {
            "context_key": membership.group_id,
            "display_label": membership.theme or membership.sector,
            "fact_hash": membership.fact_hash,
            "relation_kind": membership.relation_kind,
            "hierarchy_level": membership.hierarchy_level,
            "taxonomy": membership.taxonomy,
            "source": membership.source,
            "snapshot_date": (
                membership.snapshot_date.isoformat()
                if membership.snapshot_date is not None
                else None
            ),
            "snapshot_hash": membership.snapshot_hash,
            "state_hash": membership.theme_state_hash,
            "peer_count": membership.eligible_peer_count,
            "confidence": membership.confidence,
        }
        if membership is not None
        else None
    )
    classification_graph = {
        "classification_status": (
            "available"
            if membership is not None and membership.theme_state_available is True
            else "unavailable"
        ),
        "industry_path": (
            {
                "taxonomy": primary.taxonomy,
                "taxonomy_version": primary.taxonomy_version,
                "mapping_kind": primary.mapping_kind,
                "source": primary.source,
                "confidence": primary.confidence,
                "level_1": industry_levels.get("industry_l1"),
                "level_2": industry_levels.get("industry_l2"),
                "level_3": industry_levels.get("industry_l3"),
                "missing_levels": [
                    label
                    for label in ("level_1", "level_2", "level_3")
                    if label.replace("level_", "industry_l") not in industry_levels
                ],
                "effective_from": primary.effective_from.isoformat(),
                "effective_to": (
                    primary.effective_to.isoformat() if primary.effective_to else None
                ),
                "received_at": primary.observed_at.isoformat(),
                "fact_hash": primary.fact_hash,
                "snapshot_hash": primary.snapshot_hash,
            }
            if primary is not None
            else None
        ),
        "selected_context": selected_context,
        "alternative_contexts": [
            {
                "context_key": alternative.group_id,
                "display_label": alternative.theme or alternative.sector,
                "fact_hash": alternative.fact_hash,
                "relation_kind": alternative.relation_kind,
                "hierarchy_level": alternative.hierarchy_level,
                "taxonomy": alternative.taxonomy,
                "source": alternative.source,
                "snapshot_date": (
                    alternative.snapshot_date.isoformat()
                    if alternative.snapshot_date is not None
                    else None
                ),
                "snapshot_hash": alternative.snapshot_hash,
                "state_hash": alternative.theme_state_hash,
                "peer_count": alternative.eligible_peer_count,
                "confidence": alternative.confidence,
            }
            for alternative in item.alternative_memberships
        ],
        "rejected_contexts": [
            {"context_key": context_key, "reason": reason}
            for context_key, reason in item.rejected_contexts
        ],
        "theme_state": (
            {
                "status": (
                    "available" if membership.theme_state_available else "unavailable"
                ),
                "state_hash": membership.theme_state_hash,
                "session_date": item.signal_date.isoformat(),
                "eligible_member_count": membership.eligible_peer_count,
                "unavailable_reasons": list(
                    membership.theme_state_unavailable_reasons
                ),
            }
            if membership is not None
            else None
        ),
        "classification_unavailable_reasons": [
            reason
            for reason in item.input_unavailable_reasons
            if reason
            in {
                "partial_theme_capture",
                "stale_context_snapshot",
                "insufficient_context_peers",
                "theme_state_unavailable",
                "missing_compatible_peer_context",
            }
        ],
    }
    facts.update(
        {
            "classification_graph": classification_graph,
            "theme_hierarchy_level": (
                membership.hierarchy_level if membership is not None else None
            ),
            "theme_normalized_key": (
                membership.normalized_theme_key if membership is not None else None
            ),
            "theme_resolution_mode": (
                membership.resolution_mode if membership is not None else None
            ),
            "theme_fallback_reason": (
                membership.fallback_reason if membership is not None else None
            ),
            "theme_fact_hash": membership.fact_hash if membership is not None else None,
            "selected_context": selected_context,
            "alternative_contexts": [
                {
                    "group_id": alternative.group_id,
                    "fact_hash": alternative.fact_hash,
                    "relation_kind": alternative.relation_kind,
                    "hierarchy_level": alternative.hierarchy_level,
                    "snapshot_date": (
                        alternative.snapshot_date.isoformat()
                        if alternative.snapshot_date is not None
                        else None
                    ),
                    "snapshot_hash": alternative.snapshot_hash,
                }
                for alternative in item.alternative_memberships
            ],
            "rejected_contexts": [
                {"context_key": context_key, "reason": reason}
                for context_key, reason in item.rejected_contexts
            ],
            "primary_industry_path": (
                [
                    {"level": level, "code": code, "label": label}
                    for level, code, label in item.primary_industry.industry_path
                ]
                if item.primary_industry is not None
                else []
            ),
            "theme_state_hash": (
                membership.theme_state_hash if membership is not None else None
            ),
            "theme_state_available": (
                membership.theme_state_available if membership is not None else None
            ),
            "decision_mode": item.decision_mode,
            "feature_trade_date": item.signal_date.isoformat(),
            "membership_evaluation_date": membership_date.isoformat(),
            "next_eligible_date": (
                item.next_eligible_date.isoformat() if item.next_eligible_date else None
            ),
            "historical_validation_eligible": item.decision_mode == SESSION_PIT_MODE,
        }
    )
    signal_bar = item.bars[-1] if item.bars and item.bars[-1].trade_date == item.signal_date else None
    signal_low = _finite(signal_bar.adjusted_low) if signal_bar is not None else None
    if (
        availability == "available"
        and qualifies
        and signal_bar is not None
        and signal_bar.decision_eligible
        and signal_bar.observed_at.date() >= signal_bar.trade_date
        and signal_bar.observed_at <= item.source_cutoff
        and signal_bar.price_basis == PRICE_BASIS
        and signal_bar.provider.strip().lower() not in FORBIDDEN_DECISION_PROVIDERS
        and bool(signal_bar.revision_id.strip())
        and signal_low is not None
        and signal_low > 0
    ):
        facts["adjusted_low"] = signal_low
    if formula_id == LOW_BASE_CATCHUP_V1:
        hypothesis_visible = item.source_cutoff >= LOW_BASE_HYPOTHESIS_RECEIVED_AT
        facts.update(
            {
                "hypothesis_source_registry_hash": LOW_BASE_SOURCE_REGISTRY_HASH,
                "hypothesis_received_at": LOW_BASE_HYPOTHESIS_RECEIVED_AT.isoformat(),
                "retrospective_hypothesis_replay": not hypothesis_visible,
                "historical_validation_eligible": (
                    item.decision_mode == SESSION_PIT_MODE and hypothesis_visible
                ),
            }
        )
    if clone_excluded:
        facts["clone_representative"] = False
    if sentiment_risk is not None:
        facts["sentiment_risk_ref"] = sentiment_risk.reference_dict()
    draft = V2CandidateObservation(
        universe=item.universe,
        asset_code=item.asset_code,
        asset_name=item.asset_name,
        signal_date=item.signal_date,
        formula_id=formula_id,
        state=state,
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


def attach_sentiment_risk_snapshot(
    observations: Sequence[V2CandidateObservation],
    snapshot: SentimentRiskSnapshot,
) -> tuple[V2CandidateObservation, ...]:
    """Attach one full snapshot and compact references to a materialization."""

    rewritten: list[V2CandidateObservation] = []
    for observation in observations:
        facts = dict(observation.gate_facts)
        facts.pop("sentiment_risk", None)
        facts.pop("sentiment_risk_snapshot", None)
        facts["sentiment_risk_ref"] = snapshot.reference_dict()
        draft = replace(
            observation,
            gate_facts=tuple(sorted(facts.items())),
            feature_hash="pending",
        )
        rewritten.append(
            replace(draft, feature_hash=stable_contract_hash(draft.canonical_payload()))
        )
    ordered = sorted(rewritten, key=lambda row: (row.formula_id, row.asset_code))
    if not ordered:
        return ()
    anchor = ordered[0]
    anchor_facts = dict(anchor.gate_facts)
    anchor_facts["sentiment_risk_snapshot"] = snapshot.to_dict()
    anchor_draft = replace(
        anchor,
        gate_facts=tuple(sorted(anchor_facts.items())),
        feature_hash="pending",
    )
    ordered[0] = replace(
        anchor_draft,
        feature_hash=stable_contract_hash(anchor_draft.canonical_payload()),
    )
    return tuple(ordered)


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
    code_version: str = V2_CODE_VERSION,
    provider_health: tuple[tuple[str, str], ...] = (),
    theme_percentile_overrides: Mapping[str, tuple[float, float, float]] | None = None,
) -> V2ScreenResult:
    """Screen one universe/session using only factual PIT adjusted inputs."""

    if not items:
        raise V2ContractError("screen requires at least one input")
    universes = {item.universe for item in items}
    dates = {item.signal_date for item in items}
    cutoffs = {item.source_cutoff for item in items}
    identity_cutoffs = {item.identity_cutoff or item.source_cutoff for item in items}
    modes = {item.decision_mode for item in items}
    membership_dates = {item.membership_evaluation_date or item.signal_date for item in items}
    next_eligible_dates = {item.next_eligible_date for item in items}
    if any(
        len(values) != 1
        for values in (
            universes,
            dates,
            cutoffs,
            identity_cutoffs,
            modes,
            membership_dates,
            next_eligible_dates,
        )
    ):
        raise V2ContractError(
            "screen inputs must share universe, signal date, cutoff and decision timing"
        )
    universe = next(iter(universes))
    if universe not in SUPPORTED_UNIVERSES:
        raise V2ContractError("unsupported screening universe")
    if len({item.asset_code for item in items}) != len(items):
        raise V2ContractError("screen input asset codes must be unique")
    ordered = resolve_v2_asset_contexts(
        tuple(sorted(items, key=lambda item: item.asset_code))
    )
    membership_reasons = {item.asset_code: tuple(_membership_reasons(item)) for item in ordered}
    intrinsic_bar_reasons = {item.asset_code: tuple(_bar_reasons(item, 0)) for item in ordered}

    def cached_base_reasons(item: V2AssetInput, required_history: int) -> tuple[str, ...]:
        reasons = set(membership_reasons[item.asset_code])
        reasons.update(intrinsic_bar_reasons[item.asset_code])
        if len(item.bars) < required_history:
            reasons.add("insufficient_adjusted_history")
        return tuple(sorted(reasons))

    standard_reasons = {
        item.asset_code: cached_base_reasons(item, STANDARD_HISTORY) for item in ordered
    }
    low_base_reasons = {
        item.asset_code: cached_base_reasons(item, LOW_BASE_REQUIRED_HISTORY)
        for item in ordered
    }
    repair_reasons = {
        item.asset_code: cached_base_reasons(item, REPAIR_HISTORY) for item in ordered
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
    persisted_theme_state = {
        item.membership.group_id: item.membership.theme_state_percentiles
        for item in eligible_standard
        if item.membership is not None
        and item.membership.theme_state_available is True
        and item.membership.theme_state_percentiles is not None
    }
    for group, values in persisted_theme_state.items():
        hot_1[group], hot_5[group], hot_breadth[group] = values
    if theme_percentile_overrides is not None:
        hot_1 = {key: values[0] for key, values in theme_percentile_overrides.items()}
        hot_5 = {key: values[1] for key, values in theme_percentile_overrides.items()}
        hot_breadth = {key: values[2] for key, values in theme_percentile_overrides.items()}
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
            "ma20_five_sessions_ago": (
                _ma(item, 20, len(item.bars) - 5) if len(item.bars) >= 25 else None
            ),
        }
        applicable_formula_ids = (
            V2_CANDIDATE_IDS if universe == UNIVERSE_ASHARE else LEGACY_V2_CANDIDATE_IDS
        )
        for formula_id in applicable_formula_ids:
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
            elif formula_id == LOW_BASE_CATCHUP_V1:
                candidate_reasons = list(low_base_reasons[item.asset_code])
                low_base_facts = _low_base_catchup_features(item)
                facts.update(low_base_facts)
                hot_gate = hot_score is not None and hot_score >= 2 / 3
                peer_gate = peer_counts.get(item.asset_code, 0) >= MINIMUM_PEER_COUNT
                low_base_gate = low_base_facts.get("low_base_gate") is True
                volume_gate = low_base_facts.get("repeated_volume_gate") is True
                turning_gate = low_base_facts.get("initial_turning_gate") is True
                overextended = low_base_facts.get("overextended") is True
                facts.update(
                    {
                        "hot_theme_gate": hot_gate,
                        "peer_count_gate": peer_gate,
                        "gate_family_hot_theme": hot_gate and peer_gate,
                        "gate_family_low_base": low_base_gate,
                        "gate_family_repeated_volume": volume_gate,
                        "gate_family_initial_turning": turning_gate,
                    }
                )
                raw_score_values.update(
                    {
                        "low_base_position": _finite(
                            low_base_facts.get("setup_range_position_120")
                        ),
                        "volume_expansion": _finite(
                            low_base_facts.get("volume_memory_expansion_5v20")
                        ),
                        "initial_turning": _finite(low_base_facts.get("return_5")),
                    }
                )
                if not hot_gate:
                    candidate_reasons.append("hot_theme_gate_failed")
                if not peer_gate:
                    candidate_reasons.append("insufficient_peer_count")
                if not low_base_gate:
                    if low_base_facts.get("setup_range_position_120") is None:
                        candidate_reasons.append("low_base_range_unavailable")
                    elif (
                        low_base_facts["setup_range_position_120"]
                        > LOW_BASE_RANGE_POSITION_MAX
                    ):
                        candidate_reasons.append("low_base_range_position_failed")
                    if low_base_facts.get("setup_drawdown_120") is None:
                        candidate_reasons.append("low_base_drawdown_unavailable")
                    elif low_base_facts["setup_drawdown_120"] > LOW_BASE_DRAWDOWN_MAX:
                        candidate_reasons.append("low_base_drawdown_failed")
                if not volume_gate:
                    max_expansion = low_base_facts.get(
                        "volume_memory_max_expansion_5v20"
                    )
                    max_relative_days = int(
                        low_base_facts.get(
                            "volume_memory_max_relative_volume_confirmed_days"
                        )
                        or 0
                    )
                    if max_expansion is None:
                        candidate_reasons.append("low_base_volume_expansion_unavailable")
                    elif max_expansion < LOW_BASE_VOLUME_EXPANSION_MIN:
                        candidate_reasons.append("low_base_volume_expansion_failed")
                    if max_relative_days < LOW_BASE_RELATIVE_VOLUME_DAYS_MIN:
                        candidate_reasons.append("low_base_repeated_volume_failed")
                    if (
                        max_expansion is not None
                        and max_expansion >= LOW_BASE_VOLUME_EXPANSION_MIN
                        and max_relative_days >= LOW_BASE_RELATIVE_VOLUME_DAYS_MIN
                    ):
                        candidate_reasons.append("low_base_volume_memory_joint_gate_failed")
                    if low_base_facts.get("single_day_volume_watch") is True:
                        candidate_reasons.append("low_base_single_day_volume_watch")
                if not turning_gate:
                    candidate_reasons.append("low_base_initial_turning_failed")
                if low_base_facts.get("return_5") is None:
                    candidate_reasons.append("low_base_return_5_unavailable")
                elif low_base_facts["return_5"] > LOW_BASE_RETURN_5_MAX:
                    candidate_reasons.append("low_base_return_5_overextended")
                if low_base_facts.get("overextension_atr") is None:
                    candidate_reasons.append("low_base_overextension_unavailable")
                elif low_base_facts["overextension_atr"] > LOW_BASE_OVEREXTENSION_ATR_HARD_MAX:
                    candidate_reasons.append("low_base_atr_overextended")
                elif low_base_facts["overextension_atr"] > LOW_BASE_OVEREXTENSION_ATR_MAX:
                    candidate_reasons.append("low_base_atr_extended_watch")
                facts["entry_status"] = "overextended" if overextended else "invalidated"
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
                if formula_id == BREAKOUT_V2:
                    if len(item.bars) >= VOLUME_LOOKBACK:
                        volume_max = max(bar.volume for bar in item.bars[-VOLUME_LOOKBACK:])
                        facts["latest_120_volume_max"] = volume_max
                        facts["breakout_volume_confirmed"] = item.bars[-1].volume >= volume_max
                        if item.bars[-1].volume < volume_max:
                            candidate_reasons.append("volume_peak_gate_failed")
                    else:
                        facts["breakout_volume_confirmed"] = False
                        candidate_reasons.append("insufficient_volume_history")
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
                    prior_volume_20 = _mean_finite(
                        [bar.volume for bar in item.bars[-(BASE_VOLUME_LOOKBACK + 1) : -1]]
                    )
                    relative_volume_20 = (
                        item.bars[-1].volume / prior_volume_20
                        if item.bars and prior_volume_20 and prior_volume_20 > 0
                        else None
                    )
                    amount_percentile = _latest_history_percentile(
                        item.bars[-1].amount if item.bars else None,
                        [bar.amount for bar in item.bars[-(BASE_VOLUME_LOOKBACK + 1) : -1]],
                    )
                    volume_confirmed = (
                        relative_volume_20 is not None
                        and relative_volume_20 >= BASE_RELATIVE_VOLUME_MIN
                    ) or (
                        amount_percentile is not None
                        and amount_percentile >= BASE_AMOUNT_PERCENTILE_MIN
                    )
                    facts.update(
                        {
                            "prior_20_mean_volume": prior_volume_20,
                            "relative_volume_20": relative_volume_20,
                            "amount_vs_prior_20_percentile": amount_percentile,
                            "base_volume_confirmed": volume_confirmed,
                        }
                    )
                    if not volume_confirmed:
                        candidate_reasons.append("base_volume_confirmation_failed")
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

    sentiment_risk: SentimentRiskSnapshot | None = None
    if universe == UNIVERSE_ASHARE:
        risk_points: list[SentimentRiskPoint] = []
        for item, details in intermediate[BREAKOUT_V2]:
            if item.asset_code not in representatives or standard_reasons[item.asset_code]:
                continue
            facts = details["facts"]
            group = _group_key(item)
            hot_score = _finite(facts.get("hot_score"))
            core_score = _finite(facts.get("core_score"))
            return_1 = _return(item, 1)
            ma5 = _finite(facts.get("adjusted_ma5"))
            close = _finite(item.bars[-1].adjusted_close if item.bars else None)
            if group is None or None in (hot_score, core_score, return_1, ma5, close):
                continue
            risk_points.append(
                SentimentRiskPoint(
                    asset_code=item.asset_code,
                    theme_key=group,
                    hot_score=hot_score,
                    core_score=core_score,
                    return_1=return_1,
                    below_adjusted_ma5=close < ma5,
                    signal_date=item.signal_date,
                    source_cutoff=item.source_cutoff,
                    pit_visible=True,
                )
            )
        sentiment_risk = calculate_sentiment_risk(
            risk_points,
            signal_date=next(iter(dates)),
            source_cutoff=next(iter(cutoffs)),
        )

    component_specs = {
        BREAKOUT_V2: {"breakout_magnitude": False},
        BASE_LAUNCH_V2: {"compression": True, "overextension": True},
        FORMER_LEADER_REPAIR_V2: {},
        LOW_BASE_CATCHUP_V1: {
            "low_base_position": True,
            "volume_expansion": False,
            "initial_turning": False,
        },
    }
    component_percentiles: dict[str, dict[str, dict[tuple[str, str], float]]] = {}
    for formula_id, rows in intermediate.items():
        # Percentiles must use the same valid representative pool as the peer
        # features; invalid/non-representative clones cannot move a valid rank.
        eligible_rows = [
            (item, details)
            for item, details in rows
            if item.asset_code in representatives
            and not (
                low_base_reasons[item.asset_code]
                if formula_id == LOW_BASE_CATCHUP_V1
                else standard_reasons[item.asset_code]
            )
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
            batch_qualifier_count = basic_by_group.get(group or "", 0)
            batch_required_count, batch_policy = _required_batch_qualifiers(
                formula_id=formula_id,
                peer_count=peer_count,
                membership=item.membership,
            )
            facts.update(
                {
                    "batch_qualifier_count": batch_qualifier_count,
                    "batch_required_count": batch_required_count,
                    "batch_breadth": (
                        batch_qualifier_count / peer_count if peer_count > 0 else None
                    ),
                    "batch_policy": batch_policy,
                }
            )
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
                elif formula_id == LOW_BASE_CATCHUP_V1:
                    low_base_rank = (
                        ranks["low_base_position"].get((group, item.asset_code))
                        if group
                        else None
                    )
                    volume_rank = (
                        ranks["volume_expansion"].get((group, item.asset_code))
                        if group
                        else None
                    )
                    turning_rank = (
                        ranks["initial_turning"].get((group, item.asset_code))
                        if group
                        else None
                    )
                    if None in (low_base_rank, volume_rank, turning_rank):
                        reasons.append("low_base_score_percentile_unavailable")
                    else:
                        facts.update(
                            {
                                "low_base_position_reverse_percentile": low_base_rank,
                                "volume_expansion_percentile": volume_rank,
                                "initial_turning_percentile": turning_rank,
                            }
                        )
                        score_components = [
                            hot_component,
                            float(low_base_rank),
                            float(volume_rank),
                            float(turning_rank),
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
                peer_count <= 0 or batch_qualifier_count < batch_required_count
            ):
                reasons.append("batch_breadth_gate_failed")
            clone = item.asset_code in clone_excluded
            if clone:
                reasons.append("clone_not_representative")
            qualifies = not reasons
            score = _mean_finite(score_components) if qualifies else None
            observation_state = STATE_PREPARING
            if formula_id == LOW_BASE_CATCHUP_V1:
                family_keys = (
                    "gate_family_hot_theme",
                    "gate_family_low_base",
                    "gate_family_repeated_volume",
                    "gate_family_initial_turning",
                )
                passed_families = tuple(
                    key.removeprefix("gate_family_")
                    for key in family_keys
                    if facts.get(key) is True
                )
                failed_families = tuple(
                    key.removeprefix("gate_family_")
                    for key in family_keys
                    if facts.get(key) is not True
                )
                facts.update(
                    {
                        "passed_gate_families": list(passed_families),
                        "failed_gate_families": list(failed_families),
                        "watch_window_sessions": LOW_BASE_WATCH_WINDOW_SESSIONS,
                        "confirmation_window_sessions": LOW_BASE_CONFIRMATION_WINDOW_SESSIONS,
                    }
                )
                setup_ready = (
                    not low_base_reasons[item.asset_code]
                    and group is not None
                    and not clone
                    and all(
                        facts.get(key) is True
                        for key in (
                            "gate_family_hot_theme",
                            "gate_family_low_base",
                            "gate_family_repeated_volume",
                        )
                    )
                )
                low_base_setup_ready = (
                    not low_base_reasons[item.asset_code]
                    and group is not None
                    and not clone
                    and facts.get("gate_family_hot_theme") is True
                    and facts.get("gate_family_low_base") is True
                )
                facts["setup_memory_ready"] = setup_ready
                if qualifies:
                    facts["entry_status"] = "actionable"
                elif (
                    facts.get("overextended") is True
                    and facts.get("launch_signal") is True
                    and low_base_setup_ready
                ):
                    observation_state = STATE_PREPARING
                    facts["entry_status"] = "overextended"
                elif (
                    facts.get("extension_band") == "extended_watch"
                    and facts.get("launch_signal") is True
                    and setup_ready
                ):
                    observation_state = STATE_PREPARING
                    facts["entry_status"] = "watch"
                elif (
                    facts.get("single_day_volume_watch") is True
                    and low_base_setup_ready
                ):
                    observation_state = STATE_TURNING_WATCH
                    facts["entry_status"] = "watch"
                elif (
                    setup_ready
                    and failed_families == ("initial_turning",)
                ):
                    observation_state = STATE_TURNING_WATCH
                    facts["entry_status"] = "watch"
                else:
                    observation_state = STATE_INVALIDATED
                    facts["entry_status"] = "invalidated"
            if (
                formula_id in {BASE_LAUNCH_V2, BREAKOUT_V2}
                and not qualifies
                and not standard_reasons[item.asset_code]
                and group is not None
                and not clone
            ):
                ma5 = _finite(facts.get("adjusted_ma5"))
                ma10 = _finite(facts.get("adjusted_ma10"))
                ma20 = _finite(facts.get("adjusted_ma20"))
                ma20_5 = _finite(facts.get("ma20_five_sessions_ago"))
                close = item.bars[-1].adjusted_close if item.bars else None
                watch_conditions = {
                    "hot_theme": hot_component >= 2 / 3,
                    "core_leader": core_component >= 0.80,
                    "ma_alignment": (None not in (ma5, ma10, ma20) and ma5 > ma10 > ma20),
                    "volume_confirmation": (
                        facts.get("base_volume_confirmed") is True
                        if formula_id == BASE_LAUNCH_V2
                        else facts.get("breakout_volume_confirmed") is True
                    ),
                }
                watch_prerequisites = (
                    close is not None
                    and ma20 is not None
                    and close > ma20
                    and ma20_5 is not None
                    and ma20 >= ma20_5
                )
                passed_watch = sum(watch_conditions.values())
                missing_watch = tuple(key for key, passed in watch_conditions.items() if not passed)
                relative_volume = _finite(facts.get("relative_volume_20"))
                amount_percentile = _finite(facts.get("amount_vs_prior_20_percentile"))
                volume_distance = min(
                    (
                        max(0.0, BASE_RELATIVE_VOLUME_MIN - relative_volume)
                        / BASE_RELATIVE_VOLUME_MIN
                        if relative_volume is not None
                        else 1.0
                    ),
                    (
                        max(0.0, BASE_AMOUNT_PERCENTILE_MIN - amount_percentile)
                        / BASE_AMOUNT_PERCENTILE_MIN
                        if amount_percentile is not None
                        else 1.0
                    ),
                )
                if formula_id == BREAKOUT_V2:
                    latest_volume = item.bars[-1].volume if item.bars else None
                    volume_max = _finite(facts.get("latest_120_volume_max"))
                    volume_distance = (
                        max(0.0, volume_max - latest_volume) / max(volume_max, 1e-12)
                        if latest_volume is not None and volume_max is not None
                        else 1.0
                    )
                ma_distance = 1.0
                if None not in (ma5, ma10, ma20):
                    ma_distance = max(
                        0.0,
                        (ma10 - ma5) / max(abs(ma10), 1e-12),
                        (ma20 - ma10) / max(abs(ma20), 1e-12),
                    )
                facts.update(
                    {
                        "turning_watch_hot_distance": max(0.0, 2 / 3 - hot_component),
                        "turning_watch_core_distance": max(0.0, 0.80 - core_component),
                        "turning_watch_ma_distance": ma_distance,
                        "turning_watch_volume_distance": volume_distance,
                        "turning_watch_passed_conditions": passed_watch,
                        "turning_watch_missing_count": len(missing_watch),
                        "turning_watch_missing_conditions": ",".join(missing_watch),
                        "turning_watch_formal_blocker_count": len(reasons),
                        "turning_watch_formal_blockers": ",".join(reasons),
                    }
                )
                if watch_prerequisites and passed_watch >= 3:
                    observation_state = STATE_TURNING_WATCH
            observations.append(
                _observation(
                    item=item,
                    formula_id=formula_id,
                    availability=(
                        "unavailable"
                        if (
                            low_base_reasons[item.asset_code]
                            if formula_id == LOW_BASE_CATCHUP_V1
                            else (
                                repair_reasons[item.asset_code]
                                if required_history[formula_id] == REPAIR_HISTORY
                                else standard_reasons[item.asset_code]
                            )
                        )
                        else "available"
                    ),
                    qualifies=qualifies,
                    score=score,
                    gate_facts=facts,
                    exclusion_reasons=reasons,
                    clone_excluded=clone,
                    state=observation_state,
                    sentiment_risk=sentiment_risk,
                )
            )

    ordered_observations = tuple(
        sorted(observations, key=lambda row: (row.formula_id, row.asset_code))
    )
    if sentiment_risk is not None:
        ordered_observations = attach_sentiment_risk_snapshot(
            ordered_observations,
            sentiment_risk,
        )
    exclusion_counts: dict[str, int] = defaultdict(int)
    for row in ordered_observations:
        for reason in row.exclusion_reasons:
            exclusion_counts[reason] += 1
    input_hash = _incremental_input_hash(ordered)
    data_receipt_cutoff = next(iter(identity_cutoffs))
    manifest = build_v2_manifest(
        universe=universe,
        decision_cutoff=next(iter(cutoffs)),
        data_receipt_cutoff=data_receipt_cutoff,
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
        data_receipt_cutoff=data_receipt_cutoff,
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

    allowed_start_states = {STATE_PREPARING}
    if observation.formula_id == LOW_BASE_CATCHUP_V1:
        allowed_start_states.add(STATE_TURNING_WATCH)
    if observation.state not in allowed_start_states:
        raise V2ContractError("lifecycle input must start in an eligible research state")
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
            transition_start = date.fromisoformat(str(gate_facts["membership_evaluation_date"]))
            next_eligible_date = date.fromisoformat(str(gate_facts["next_eligible_date"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise V2ContractError("post-close lifecycle timing is incomplete") from exc
        if (
            transition_start < observation.signal_date
            or next_eligible_date <= observation.signal_date
        ):
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
    initial = V2LifecycleTransition(
        universe=observation.universe,
        asset_code=observation.asset_code,
        formula_id=observation.formula_id,
        signal_date=observation.signal_date,
        from_state=None,
        to_state=observation.state,
        transition_date=transition_start,
        signal_high=signal_bar.adjusted_high,
        adjusted_close=signal_bar.adjusted_close,
        adjusted_ma5=_mean_finite([bar.adjusted_close for bar in signal_visible[-5:]]),
        simulated_execution_date=None,
        execution_model="research_state_only",
        reason=(
            "formula_passed_for_post_close_watchlist"
            if decision_mode == POST_CLOSE_WATCHLIST_MODE
            else (
                "turning_watch_observed_at_signal_cutoff"
                if observation.state == STATE_TURNING_WATCH
                else "formula_passed_at_signal_cutoff"
            )
        ),
        transition_hash="pending",
    )
    transitions.append(replace(initial, transition_hash=_transition_hash(initial)))

    current_state = observation.state
    signal_high = signal_bar.adjusted_high
    preparing_high = signal_high
    preparing_started_index = -1 if current_state == STATE_PREPARING else None
    entry_status = str(gate_facts.get("entry_status") or "")
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
            current_state == STATE_TURNING_WATCH
            and entry_status == "watch"
            and current.adjusted_close > signal_high
            and current.adjusted_close >= ma5
        ):
            transition = V2LifecycleTransition(
                universe=observation.universe,
                asset_code=observation.asset_code,
                formula_id=observation.formula_id,
                signal_date=observation.signal_date,
                from_state=current_state,
                to_state=STATE_PREPARING,
                transition_date=current.trade_date,
                signal_high=signal_high,
                adjusted_close=current.adjusted_close,
                adjusted_ma5=ma5,
                simulated_execution_date=None,
                execution_model="research_state_only",
                reason="watch_breaks_signal_high_and_holds_ma5",
                transition_hash="pending",
            )
            transitions.append(replace(transition, transition_hash=_transition_hash(transition)))
            current_state = STATE_PREPARING
            preparing_high = current.adjusted_high
            preparing_started_index = index
            continue
        if (
            current_state == STATE_PREPARING
            and preparing_started_index is not None
            and index > preparing_started_index
            and current.adjusted_close > preparing_high
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
        timeout_sessions = (
            LOW_BASE_WATCH_WINDOW_SESSIONS
            if current_state == STATE_TURNING_WATCH
            else LOW_BASE_CONFIRMATION_WINDOW_SESSIONS
        )
        elapsed_sessions = (
            index + 1
            if current_state == STATE_TURNING_WATCH or preparing_started_index is None
            else index - preparing_started_index
        )
        if (
            observation.formula_id == LOW_BASE_CATCHUP_V1
            and current_state != STATE_CONFIRMED
            and elapsed_sessions >= timeout_sessions
        ):
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
                simulated_execution_date=None,
                execution_model="research_state_only",
                reason=(
                    "turning_watch_window_expired"
                    if current_state == STATE_TURNING_WATCH
                    else "confirmation_window_expired"
                ),
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
    "ASHARE_FINE_THEME_FACT_HASH_CONTRACT",
    "BASE_LAUNCH_V2",
    "BREAKOUT_V2",
    "DECISION_MODES",
    "FORMER_LEADER_REPAIR_V2",
    "HISTORICAL_RECONSTRUCTION_MODE",
    "LOW_BASE_CATCHUP_V1",
    "LOW_BASE_HYPOTHESIS_RECEIVED_AT",
    "LOW_BASE_SOURCE_CAPTURES",
    "LOW_BASE_SOURCE_REGISTRY_HASH",
    "LOW_BASE_SOURCE_REGISTRY_VERSION",
    "LEGACY_V2_CANDIDATE_IDS",
    "LIFECYCLE_STATES",
    "STATE_CONFIRMED",
    "STATE_INVALIDATED",
    "STATE_PREPARING",
    "STATE_TURNING_WATCH",
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
    "V2StagedAssetFeature",
    "V2SourceArticle",
    "V2SourceRegistry",
    "V2_SOURCE_REGISTRY",
    "V2_EXPERIMENT_FAMILY",
    "build_v2_manifest",
    "build_v2_staged_asset_feature",
    "attach_sentiment_risk_snapshot",
    "derive_lifecycle",
    "screen_dual_universe",
    "staged_sentiment_risk_snapshot",
    "staged_theme_percentile_overrides",
    "staged_v2_input_hash",
    "screen_result_payload",
    "validate_runtime_contract",
]
