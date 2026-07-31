"""Transparent, research-only ETF proxies for source-described leader tactics."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from statistics import mean
from typing import Any, Literal

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
)

HYPOTHESIS_REGISTRY_VERSION = "etf_leader_tactics_hypothesis_v1"
LEADER_EXPERIMENT_FAMILY = "leader_tactics_shadow_v1"
LEADER_BREAKOUT_CANDIDATE = "leader_breakout_proxy_v1"
FORMER_LEADER_REPAIR_CANDIDATE = "former_leader_repair_proxy_v1"
CYCLE_ROUTED_LEADER_CANDIDATE = "cycle_routed_leader_proxy_v1"
LEADER_CANDIDATE_IDS = (
    LEADER_BREAKOUT_CANDIDATE,
    FORMER_LEADER_REPAIR_CANDIDATE,
    CYCLE_ROUTED_LEADER_CANDIDATE,
)
MA5_EXIT_POLICY_ID = "ma5_exit_proxy_v1"
PRICE_BASIS = "total_return_adjusted"
FORBIDDEN_DECISION_PROVIDERS = frozenset({"sina", "efinance"})
MINIMUM_PEER_COUNT = 5
BREAKOUT_HISTORY_SESSIONS = 120
REPAIR_HISTORY_SESSIONS = 180

DisclosureState = Literal[
    "disclosed",
    "subjective_proxy",
    "unavailable_proprietary",
    "unavailable_execution_model",
]
AvailabilityState = Literal["available", "unavailable"]


class LeaderTacticsContractError(ValueError):
    """Raised when a leader-tactics contract is not canonical."""


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class LeaderSourceArticle:
    article_id: str
    title: str
    account: str
    author: str
    published_at: datetime
    source_url: str
    captured_content_hash: str

    def validate(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.article_id,
                self.title,
                self.account,
                self.author,
                self.source_url,
            )
        ):
            raise LeaderTacticsContractError("source article metadata is incomplete")
        if not self.source_url.startswith("https://mp.weixin.qq.com/s/"):
            raise LeaderTacticsContractError("source article URL is not canonical")
        if not _is_sha256(self.captured_content_hash):
            raise LeaderTacticsContractError("captured content hash must be sha256")


@dataclass(frozen=True)
class LeaderSourceStatement:
    statement_id: str
    article_id: str
    disclosure_state: DisclosureState
    source_summary: str
    proxy_id: str | None
    interpretation: str
    limitation: str

    def validate(self, *, article_ids: set[str]) -> None:
        if self.article_id not in article_ids:
            raise LeaderTacticsContractError("statement source article is not registered")
        if not all(
            value.strip()
            for value in (
                self.statement_id,
                self.source_summary,
                self.interpretation,
                self.limitation,
            )
        ):
            raise LeaderTacticsContractError("source statement metadata is incomplete")
        if self.disclosure_state in {"disclosed", "subjective_proxy"} and not self.proxy_id:
            raise LeaderTacticsContractError("reproducible source statement requires a proxy")
        if self.disclosure_state.startswith("unavailable_") and self.proxy_id is not None:
            raise LeaderTacticsContractError("unavailable source statement cannot claim a proxy")


@dataclass(frozen=True)
class LeaderHypothesisRegistry:
    version: str
    interpretation_version: str
    articles: tuple[LeaderSourceArticle, ...]
    statements: tuple[LeaderSourceStatement, ...]
    non_equivalence_notice: str
    registry_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "interpretation_version": self.interpretation_version,
            "articles": tuple(asdict(item) for item in self.articles),
            "statements": tuple(asdict(item) for item in self.statements),
            "non_equivalence_notice": self.non_equivalence_notice,
        }

    def validate(self) -> None:
        if self.version != HYPOTHESIS_REGISTRY_VERSION:
            raise LeaderTacticsContractError("hypothesis registry version is incompatible")
        if not self.interpretation_version.strip():
            raise LeaderTacticsContractError("interpretation version is required")
        if len(self.articles) != 5:
            raise LeaderTacticsContractError("exactly five methodology sources are required")
        article_ids = {item.article_id for item in self.articles}
        if len(article_ids) != len(self.articles):
            raise LeaderTacticsContractError("source article identities must be unique")
        for article in self.articles:
            article.validate()
        statement_ids = {item.statement_id for item in self.statements}
        if len(statement_ids) != len(self.statements):
            raise LeaderTacticsContractError("source statement identities must be unique")
        for statement in self.statements:
            statement.validate(article_ids=article_ids)
        if not any(
            item.disclosure_state == "unavailable_proprietary"
            for item in self.statements
        ):
            raise LeaderTacticsContractError("proprietary limitation must be explicit")
        notice = self.non_equivalence_notice.lower()
        if "not" not in notice or "proprietary" not in notice:
            raise LeaderTacticsContractError("proxy non-equivalence notice is incomplete")
        if self.registry_hash != stable_contract_hash(self.canonical_payload()):
            raise LeaderTacticsContractError("hypothesis registry hash is not canonical")


def _source_article(
    article_id: str,
    title: str,
    published_at: datetime,
    source_url: str,
    captured_content_hash: str,
) -> LeaderSourceArticle:
    return LeaderSourceArticle(
        article_id=article_id,
        title=title,
        account="最强飞哥",
        author="潇洒的飞哥",
        published_at=published_at,
        source_url=source_url,
        captured_content_hash=captured_content_hash,
    )


_SOURCE_ARTICLES = (
    _source_article(
        "leader-tactics-full",
        "飞哥干货 - 龙头战法全解",
        datetime(2026, 7, 25, 9, 12),
        "https://mp.weixin.qq.com/s/N2yKjiahHGUjidomQ4HIWQ",
        "f297f2838db0e0cb84bc03cb77a186295ef6b22629a440bfd7f7fe7c8f6b74f2",
    ),
    _source_article(
        "former-leader-repair",
        "明天，就俩字！！",
        datetime(2026, 7, 14, 16, 32),
        "https://mp.weixin.qq.com/s/Gk2BD1DoiqsnOqWDbqlnZw",
        "f296730dd6c9c55b49eeb68d4ba5350591c6158094de22159183b60d4bac416c",
    ),
    _source_article(
        "market-cycle-modes",
        "接下来的思路！！",
        datetime(2026, 7, 19, 13, 31),
        "https://mp.weixin.qq.com/s/fZi9o4yoi4QkV5g38mFSTw",
        "c98c37c8f23c065ee89c543327bc78d836614524948ba85c38adb300d66e190c",
    ),
    _source_article(
        "intraday-t-idea",
        "被低估的机会！！",
        datetime(2026, 7, 23, 16, 50),
        "https://mp.weixin.qq.com/s/iOaL8ILU4u36uWieZdjnKA",
        "f5616a77bb1bc84474a857bf0382f2e9b031cf52d75a5b45df71f4ec1efe456a",
    ),
    _source_article(
        "proprietary-takeoff-signal",
        "直接公布！！",
        datetime(2026, 7, 3, 14, 55),
        "https://mp.weixin.qq.com/s/3PAPZj9md2cBHByDEUStrw",
        "1c40f383794935005074dfe9e80afde5f4c250fb327c97cab11a5125fb18402a",
    ),
)

_SOURCE_STATEMENTS = (
    LeaderSourceStatement(
        statement_id="hot-sector-core-leader",
        article_id="leader-tactics-full",
        disclosure_state="subjective_proxy",
        source_summary="The method first looks for a hot sector and its core leader.",
        proxy_id=LEADER_BREAKOUT_CANDIDATE,
        interpretation="Use factual PIT sector and peer percentile gates.",
        limitation="Sector heat and leader status were not disclosed as exact formulas.",
    ),
    LeaderSourceStatement(
        statement_id="ma-alignment-and-volume",
        article_id="leader-tactics-full",
        disclosure_state="disclosed",
        source_summary="MA5, MA10 and MA20 align upward with unusually high volume.",
        proxy_id=LEADER_BREAKOUT_CANDIDATE,
        interpretation="Use adjusted MA5>MA10>MA20 and a frozen 120-session volume maximum.",
        limitation="The source allowed a wider volume lookback; this ETF proxy freezes 120 sessions.",
    ),
    LeaderSourceStatement(
        statement_id="ma5-life-line",
        article_id="leader-tactics-full",
        disclosure_state="disclosed",
        source_summary="Hold above MA5 and leave after a break below MA5.",
        proxy_id=MA5_EXIT_POLICY_ID,
        interpretation="Observe close below adjusted MA5 and execute at the next eligible close.",
        limitation="Daily next-close execution is not the source author's intraday execution.",
    ),
    LeaderSourceStatement(
        statement_id="former-leader-repair",
        article_id="former-leader-repair",
        disclosure_state="subjective_proxy",
        source_summary="A prior leader after a deep decline can be observed for stabilization.",
        proxy_id=FORMER_LEADER_REPAIR_CANDIDATE,
        interpretation="Use frozen prior leadership, drawdown, compression and MA20 proximity gates.",
        limitation="Stabilization and do-not-chase were qualitative in the source.",
    ),
    LeaderSourceStatement(
        statement_id="cycle-mode-switch",
        article_id="market-cycle-modes",
        disclosure_state="subjective_proxy",
        source_summary="Use leader mode in launch phases and swing mode in basing phases.",
        proxy_id=CYCLE_ROUTED_LEADER_CANDIDATE,
        interpretation="Route the frozen proxies with the existing PIT market-regime contract.",
        limitation="The source market-cycle labels do not exactly equal FundScope regimes.",
    ),
    LeaderSourceStatement(
        statement_id="intraday-t-trading",
        article_id="intraday-t-idea",
        disclosure_state="unavailable_execution_model",
        source_summary="Intraday buy-low and sell-high operations may reduce holding cost.",
        proxy_id=None,
        interpretation="Do not simulate intraday T-trading from daily bars.",
        limitation="No point-in-time executable intraday model or fill contract was disclosed.",
    ),
    LeaderSourceStatement(
        statement_id="takeoff-signal",
        article_id="proprietary-takeoff-signal",
        disclosure_state="unavailable_proprietary",
        source_summary="The source references a proprietary takeoff signal.",
        proxy_id=None,
        interpretation="Keep the proprietary signal unavailable.",
        limitation="No reproducible formula was disclosed.",
    ),
)


def build_leader_hypothesis_registry() -> LeaderHypothesisRegistry:
    draft = LeaderHypothesisRegistry(
        version=HYPOTHESIS_REGISTRY_VERSION,
        interpretation_version="etf_transparent_adaptation_v1",
        articles=_SOURCE_ARTICLES,
        statements=_SOURCE_STATEMENTS,
        non_equivalence_notice=(
            "These transparent ETF proxies are not the source author's proprietary signal "
            "and do not imply exact replication or endorsement."
        ),
        registry_hash="pending",
    )
    registry = replace(
        draft,
        registry_hash=stable_contract_hash(draft.canonical_payload()),
    )
    registry.validate()
    return registry


LEADER_HYPOTHESIS_REGISTRY = build_leader_hypothesis_registry()


@dataclass(frozen=True)
class FrozenLeaderCandidate:
    candidate_id: str
    required_history_sessions: int
    formula: str
    missing_value_rule: str
    parameter_items: tuple[tuple[str, str | int | float], ...]
    manifest_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "required_history_sessions": self.required_history_sessions,
            "formula": self.formula,
            "missing_value_rule": self.missing_value_rule,
            "parameter_items": self.parameter_items,
        }

    def validate(self) -> None:
        if self.candidate_id not in LEADER_CANDIDATE_IDS:
            raise LeaderTacticsContractError("leader candidate identity is undeclared")
        if self.required_history_sessions < 120:
            raise LeaderTacticsContractError("leader candidate history is insufficient")
        if not self.formula.strip() or self.missing_value_rule != "fail_closed_no_padding":
            raise LeaderTacticsContractError("leader candidate formula is incomplete")
        if self.manifest_hash != stable_contract_hash(self.canonical_payload()):
            raise LeaderTacticsContractError("leader candidate hash is not canonical")


def _leader_candidate(
    candidate_id: str,
    *,
    required_history_sessions: int,
    formula: str,
    parameter_items: tuple[tuple[str, str | int | float], ...],
) -> FrozenLeaderCandidate:
    draft = FrozenLeaderCandidate(
        candidate_id=candidate_id,
        required_history_sessions=required_history_sessions,
        formula=formula,
        missing_value_rule="fail_closed_no_padding",
        parameter_items=parameter_items,
        manifest_hash="pending",
    )
    result = replace(
        draft,
        manifest_hash=stable_contract_hash(draft.canonical_payload()),
    )
    result.validate()
    return result


FROZEN_LEADER_CANDIDATES = (
    _leader_candidate(
        LEADER_BREAKOUT_CANDIDATE,
        required_history_sessions=BREAKOUT_HISTORY_SESSIONS,
        formula=(
            "mean(sector_trend_percentile,peer_return20_percentile,"
            "peer_turnover20_percentile)|all_breakout_gates"
        ),
        parameter_items=(
            ("ma_alignment", "adjusted_ma5>adjusted_ma10>adjusted_ma20"),
            ("price_breakout_sessions", 20),
            ("volume_breakout_sessions", 120),
            ("sector_percentile_min", 2 / 3),
            ("peer_return20_percentile_min", 0.8),
            ("peer_turnover20_percentile_min", 0.5),
            ("minimum_peer_count", MINIMUM_PEER_COUNT),
        ),
    ),
    _leader_candidate(
        FORMER_LEADER_REPAIR_CANDIDATE,
        required_history_sessions=REPAIR_HISTORY_SESSIONS,
        formula=(
            "mean(prior_leadership_percentile,reverse_atr5_atr20_percentile,"
            "reverse_overextension_atr_percentile)|all_repair_gates"
        ),
        parameter_items=(
            ("prior_leadership_window", "T-119:T-20"),
            ("prior_leadership_percentile_min", 0.8),
            ("drawdown_min", -0.5),
            ("drawdown_max", -0.3),
            ("atr5_atr20_max", 0.75),
            ("overextension_atr_max", 1.0),
            ("minimum_peer_count", MINIMUM_PEER_COUNT),
        ),
    ),
    _leader_candidate(
        CYCLE_ROUTED_LEADER_CANDIDATE,
        required_history_sessions=REPAIR_HISTORY_SESSIONS,
        formula=(
            "risk_on:leader_breakout_proxy_v1|neutral:former_leader_repair_proxy_v1|"
            "defensive_or_cash_wait:no_selection"
        ),
        parameter_items=(
            ("risk_on_route", LEADER_BREAKOUT_CANDIDATE),
            ("neutral_route", FORMER_LEADER_REPAIR_CANDIDATE),
            ("defensive_route", "no_selection"),
            ("cash_wait_route", "no_selection"),
        ),
    ),
)


@dataclass(frozen=True)
class FrozenLeaderCandidateRegistry:
    candidates: tuple[FrozenLeaderCandidate, ...]
    registry_hash: str

    @property
    def by_id(self) -> dict[str, FrozenLeaderCandidate]:
        return {item.candidate_id: item for item in self.candidates}

    def validate(self) -> None:
        if self.candidates != FROZEN_LEADER_CANDIDATES:
            raise LeaderTacticsContractError("leader candidate registry is not canonical")
        if tuple(item.candidate_id for item in self.candidates) != LEADER_CANDIDATE_IDS:
            raise LeaderTacticsContractError("leader candidate order is not canonical")
        for item in self.candidates:
            item.validate()
        expected = stable_contract_hash(
            {"candidate_manifest_hashes": tuple(item.manifest_hash for item in self.candidates)}
        )
        if self.registry_hash != expected:
            raise LeaderTacticsContractError("leader candidate registry hash is not canonical")


def freeze_leader_candidate_registry(
    candidates: Iterable[FrozenLeaderCandidate],
) -> FrozenLeaderCandidateRegistry:
    values = tuple(candidates)
    registry = FrozenLeaderCandidateRegistry(
        candidates=values,
        registry_hash=stable_contract_hash(
            {"candidate_manifest_hashes": tuple(item.manifest_hash for item in values)}
        ),
    )
    registry.validate()
    return registry


FROZEN_LEADER_CANDIDATE_REGISTRY = freeze_leader_candidate_registry(
    FROZEN_LEADER_CANDIDATES
)


@dataclass(frozen=True)
class LeaderExperimentManifest:
    experiment_family: str
    hypothesis_registry_hash: str
    candidate_registry_hash: str
    baseline_contract_id: str
    baseline_contract_hash: str
    regime_contract_hash: str
    split_contract_hash: str
    cost_contract_hash: str
    code_version: str
    holdout_identity_hash: str
    production_candidate_registry_hash: str
    manifest_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("manifest_hash")
        return payload

    def validate(self) -> None:
        if self.experiment_family != LEADER_EXPERIMENT_FAMILY:
            raise LeaderTacticsContractError("leader experiment family is incompatible")
        expected_hashes = (
            self.hypothesis_registry_hash,
            self.candidate_registry_hash,
            self.baseline_contract_hash,
            self.regime_contract_hash,
            self.split_contract_hash,
            self.cost_contract_hash,
            self.holdout_identity_hash,
            self.production_candidate_registry_hash,
        )
        if any(not _is_sha256(value) for value in expected_hashes):
            raise LeaderTacticsContractError("leader experiment identity hash is invalid")
        if self.hypothesis_registry_hash != LEADER_HYPOTHESIS_REGISTRY.registry_hash:
            raise LeaderTacticsContractError("hypothesis registry identity is incompatible")
        if (
            self.candidate_registry_hash
            != FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash
        ):
            raise LeaderTacticsContractError("leader candidate registry identity is incompatible")
        if self.regime_contract_hash != REGIME_LIQUIDITY_GATE_CONTRACT_HASH:
            raise LeaderTacticsContractError("market regime contract is incompatible")
        if self.cost_contract_hash != RANKING_COST_CONTRACT_HASH:
            raise LeaderTacticsContractError("ranking cost contract is incompatible")
        if not self.baseline_contract_id.strip() or not self.code_version.strip():
            raise LeaderTacticsContractError("baseline and code identity are required")
        if self.manifest_hash != stable_contract_hash(self.canonical_payload()):
            raise LeaderTacticsContractError("leader experiment manifest is not canonical")


def build_leader_experiment_manifest(
    *,
    baseline_contract_id: str,
    baseline_contract_hash: str,
    split_contract_hash: str,
    code_version: str,
    holdout_identity_hash: str,
    production_candidate_registry_hash: str,
) -> LeaderExperimentManifest:
    draft = LeaderExperimentManifest(
        experiment_family=LEADER_EXPERIMENT_FAMILY,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        candidate_registry_hash=FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        baseline_contract_id=baseline_contract_id,
        baseline_contract_hash=baseline_contract_hash,
        regime_contract_hash=REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
        split_contract_hash=split_contract_hash,
        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        code_version=code_version,
        holdout_identity_hash=holdout_identity_hash,
        production_candidate_registry_hash=production_candidate_registry_hash,
        manifest_hash="pending",
    )
    result = replace(
        draft,
        manifest_hash=stable_contract_hash(draft.canonical_payload()),
    )
    result.validate()
    return result


@dataclass(frozen=True)
class LeaderAdjustedBar:
    trade_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    volume: float
    turnover: float
    observed_at: datetime
    decision_eligible: bool = True
    price_basis: str = PRICE_BASIS
    data_provider: str = "eastmoney"
    provider_version: str = "v1"
    adjustment_version: str = "hfq-v1"


@dataclass(frozen=True)
class LeaderPitAssetInput:
    asset_code: str
    signal_date: date
    source_cutoff: datetime
    baseline_score: float | None
    bars: tuple[LeaderAdjustedBar, ...]
    historical_member: bool
    membership_effective_date: date
    membership_observed_at: datetime
    peer_group: str | None
    peer_mapping_effective_date: date | None
    peer_mapping_observed_at: datetime | None
    peer_mapping_kind: str
    sector_trend_score: float | None
    sector_trend_as_of: date | None
    sector_trend_observed_at: datetime | None
    sector_trend_contract_hash: str | None
    market_regime: str | None
    market_regime_as_of: date | None
    market_regime_observed_at: datetime | None
    market_regime_contract_hash: str | None
    market_regime_status: str
    clone_group: str | None = None
    tracked_index: str | None = None
    issuer: str | None = None
    theme: str | None = None
    sector: str | None = None
    input_unavailable_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaderCandidateObservation:
    asset_code: str
    signal_date: date
    candidate_id: str
    availability: AvailabilityState
    qualifies: bool
    score: float | None
    components: tuple[tuple[str, str | int | float | bool | None], ...]
    gate_reasons: tuple[str, ...]
    unavailable_reasons: tuple[str, ...]
    baseline_score: float | None
    peer_group: str | None
    clone_group: str
    tracked_index: str | None
    issuer: str | None
    theme: str | None
    sector: str | None
    history_tier: str
    source_cutoff: datetime
    feature_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("feature_hash")
        return payload


@dataclass(frozen=True)
class LeaderFeaturePanel:
    signal_date: date
    source_cutoff: datetime
    observations: tuple[LeaderCandidateObservation, ...]
    panel_hash: str

    @property
    def by_candidate(self) -> dict[str, tuple[LeaderCandidateObservation, ...]]:
        return {
            candidate_id: tuple(
                item
                for item in self.observations
                if item.candidate_id == candidate_id
            )
            for candidate_id in LEADER_CANDIDATE_IDS
        }


def _average_percentile_ranks(
    values: Mapping[str, float],
    *,
    reverse: bool = False,
) -> dict[str, float]:
    finite = {
        key: parsed
        for key, value in values.items()
        if (parsed := _finite(value)) is not None
    }
    if not finite:
        return {}
    ordered = sorted(finite.items(), key=lambda item: (item[1], item[0]))
    result: dict[str, float] = {}
    index = 0
    denominator = max(1, len(ordered) - 1)
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_position = (index + end - 1) / 2
        percentile = average_position / denominator if len(ordered) > 1 else 0.5
        if reverse:
            percentile = 1.0 - percentile
        for position in range(index, end):
            result[ordered[position][0]] = percentile
        index = end
    return result


def percentile_ranks(
    values: Mapping[str, float],
    *,
    reverse: bool = False,
) -> dict[str, float]:
    """Public deterministic percentile helper used by contract tests."""

    return _average_percentile_ranks(values, reverse=reverse)


def _bar_reasons(
    item: LeaderPitAssetInput,
    *,
    required_history: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(item.bars) < required_history:
        reasons.append("insufficient_decision_eligible_adjusted_sessions")
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
            bar.turnover,
        )
        if not bar.decision_eligible:
            reasons.append("adjusted_bar_not_decision_eligible")
        if bar.price_basis != PRICE_BASIS:
            reasons.append("adjusted_bar_wrong_price_basis")
        if bar.data_provider.strip().lower() in FORBIDDEN_DECISION_PROVIDERS:
            reasons.append("forbidden_raw_price_provider")
        if not bar.data_provider or not bar.provider_version or not bar.adjustment_version:
            reasons.append("adjusted_bar_missing_provenance")
        if bar.observed_at > item.source_cutoff:
            reasons.append("adjusted_bar_received_after_cutoff")
        if any(_finite(value) is None for value in values):
            reasons.append("adjusted_bar_non_finite")
        if (
            bar.adjusted_open <= 0
            or bar.adjusted_high <= 0
            or bar.adjusted_low <= 0
            or bar.adjusted_close <= 0
            or bar.volume < 0
            or bar.turnover < 0
            or bar.adjusted_high < max(bar.adjusted_open, bar.adjusted_close)
            or bar.adjusted_low > min(bar.adjusted_open, bar.adjusted_close)
        ):
            reasons.append("adjusted_bar_invalid_ohlcv")
    return tuple(sorted(set(reasons)))


def _pit_reasons(
    item: LeaderPitAssetInput,
    *,
    include_sector: bool,
) -> tuple[str, ...]:
    reasons: list[str] = list(item.input_unavailable_reasons)
    if not item.asset_code.strip():
        reasons.append("missing_asset_code")
    if not item.historical_member:
        reasons.append("missing_historical_membership")
    if item.membership_effective_date > item.signal_date:
        reasons.append("membership_effective_after_signal")
    if item.membership_observed_at > item.source_cutoff:
        reasons.append("membership_received_after_cutoff")
    if not item.peer_group:
        reasons.append("missing_historical_peer_mapping")
    if item.peer_mapping_kind != "historical_pit":
        reasons.append("peer_mapping_not_point_in_time")
    if item.peer_mapping_effective_date is None:
        reasons.append("peer_mapping_missing_effective_date")
    elif item.peer_mapping_effective_date > item.signal_date:
        reasons.append("peer_mapping_effective_after_signal")
    if item.peer_mapping_observed_at is None:
        reasons.append("peer_mapping_missing_receipt")
    elif item.peer_mapping_observed_at > item.source_cutoff:
        reasons.append("peer_mapping_received_after_cutoff")
    if include_sector:
        if item.sector_trend_as_of is None or item.sector_trend_as_of > item.signal_date:
            reasons.append("sector_trend_not_visible_at_signal")
        if (
            item.sector_trend_observed_at is None
            or item.sector_trend_observed_at > item.source_cutoff
        ):
            reasons.append("sector_trend_received_after_cutoff")
        if not item.sector_trend_contract_hash or not _is_sha256(
            item.sector_trend_contract_hash
        ):
            reasons.append("sector_trend_contract_unavailable")
        if _finite(item.sector_trend_score) is None:
            reasons.append("sector_trend_non_finite")
    return tuple(sorted(set(reasons)))


def _regime_reasons(item: LeaderPitAssetInput) -> tuple[str, ...]:
    reasons: list[str] = []
    if item.market_regime_status != "available":
        reasons.append(f"market_regime_{item.market_regime_status or 'unavailable'}")
    if item.market_regime not in {"risk_on", "neutral", "defensive", "cash_wait"}:
        reasons.append("market_regime_incompatible")
    if item.market_regime_as_of is None or item.market_regime_as_of > item.signal_date:
        reasons.append("market_regime_not_visible_at_signal")
    if (
        item.market_regime_observed_at is None
        or item.market_regime_observed_at > item.source_cutoff
    ):
        reasons.append("market_regime_received_after_cutoff")
    if item.market_regime_contract_hash != REGIME_LIQUIDITY_GATE_CONTRACT_HASH:
        reasons.append("market_regime_contract_incompatible")
    return tuple(sorted(set(reasons)))


def validate_leader_pit_input(
    item: LeaderPitAssetInput,
    *,
    required_history: int,
    include_regime: bool = False,
    include_sector: bool = True,
) -> tuple[str, ...]:
    reasons = [
        *_pit_reasons(item, include_sector=include_sector),
        *_bar_reasons(item, required_history=required_history),
    ]
    if include_regime:
        reasons.extend(_regime_reasons(item))
    return tuple(sorted(set(reasons)))


def _moving_average(bars: Sequence[LeaderAdjustedBar], sessions: int) -> float:
    return mean(bar.adjusted_close for bar in bars[-sessions:])


def _atr(bars: Sequence[LeaderAdjustedBar], sessions: int) -> float | None:
    if len(bars) < sessions + 1:
        return None
    window = bars[-(sessions + 1) :]
    true_ranges = [
        max(
            current.adjusted_high - current.adjusted_low,
            abs(current.adjusted_high - previous.adjusted_close),
            abs(current.adjusted_low - previous.adjusted_close),
        )
        for previous, current in zip(window[:-1], window[1:], strict=True)
    ]
    value = mean(true_ranges)
    return value if math.isfinite(value) and value > 0 else None


def _return_at_date(
    bars: Sequence[LeaderAdjustedBar],
    target_date: date,
    sessions: int,
) -> float | None:
    index_by_date = {bar.trade_date: index for index, bar in enumerate(bars)}
    index = index_by_date.get(target_date)
    if index is None or index < sessions:
        return None
    start = bars[index - sessions].adjusted_close
    end = bars[index].adjusted_close
    if start <= 0:
        return None
    value = end / start - 1.0
    return value if math.isfinite(value) else None


def _history_tier(count: int) -> str:
    if count >= 250:
        return "full_history_context"
    if count >= 120:
        return "standard_history"
    if count >= 61:
        return "provisional_short_history"
    return "insufficient_history"


def _make_observation(
    *,
    item: LeaderPitAssetInput,
    candidate_id: str,
    availability: AvailabilityState,
    qualifies: bool,
    score: float | None,
    components: Mapping[str, str | int | float | bool | None],
    gate_reasons: Iterable[str] = (),
    unavailable_reasons: Iterable[str] = (),
) -> LeaderCandidateObservation:
    draft = LeaderCandidateObservation(
        asset_code=item.asset_code,
        signal_date=item.signal_date,
        candidate_id=candidate_id,
        availability=availability,
        qualifies=qualifies,
        score=score,
        components=tuple(sorted(components.items())),
        gate_reasons=tuple(sorted(set(gate_reasons))),
        unavailable_reasons=tuple(sorted(set(unavailable_reasons))),
        baseline_score=_finite(item.baseline_score),
        peer_group=item.peer_group,
        clone_group=item.clone_group or item.asset_code,
        tracked_index=item.tracked_index,
        issuer=item.issuer,
        theme=item.theme,
        sector=item.sector,
        history_tier=_history_tier(len(item.bars)),
        source_cutoff=item.source_cutoff,
        feature_hash="pending",
    )
    return replace(
        draft,
        feature_hash=stable_contract_hash(draft.canonical_payload()),
    )


def _current_peer_percentiles(
    items: Sequence[LeaderPitAssetInput],
) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    return_percentiles: dict[str, float] = {}
    turnover_percentiles: dict[str, float] = {}
    peer_counts: dict[str, int] = {}
    groups: dict[str, list[LeaderPitAssetInput]] = defaultdict(list)
    for item in items:
        if item.peer_group:
            groups[item.peer_group].append(item)
    for group_items in groups.values():
        returns = {
            item.asset_code: value
            for item in group_items
            if (value := _return_at_date(item.bars, item.signal_date, 20)) is not None
        }
        turnovers = {
            item.asset_code: mean(bar.turnover for bar in item.bars[-20:])
            for item in group_items
            if len(item.bars) >= 20
            and all(_finite(bar.turnover) is not None for bar in item.bars[-20:])
        }
        return_percentiles.update(_average_percentile_ranks(returns))
        turnover_percentiles.update(_average_percentile_ranks(turnovers))
        for item in group_items:
            peer_counts[item.asset_code] = len(returns)
    return return_percentiles, turnover_percentiles, peer_counts


def _sector_percentiles(
    items: Sequence[LeaderPitAssetInput],
) -> dict[str, float]:
    by_group: dict[str, set[float]] = defaultdict(set)
    for item in items:
        score = _finite(item.sector_trend_score)
        if item.peer_group and score is not None:
            by_group[item.peer_group].add(score)
    canonical = {
        group: next(iter(values))
        for group, values in by_group.items()
        if len(values) == 1
    }
    group_percentiles = _average_percentile_ranks(canonical)
    return {
        item.asset_code: group_percentiles[item.peer_group]
        for item in items
        if item.peer_group in group_percentiles
    }


def _prior_leadership_percentiles(
    items: Sequence[LeaderPitAssetInput],
) -> dict[str, float]:
    groups: dict[str, list[LeaderPitAssetInput]] = defaultdict(list)
    for item in items:
        if item.peer_group:
            groups[item.peer_group].append(item)
    result: dict[str, float] = {}
    for group_items in groups.values():
        for target in group_items:
            if len(target.bars) < REPAIR_HISTORY_SESSIONS:
                continue
            best: float | None = None
            target_dates = tuple(bar.trade_date for bar in target.bars[-120:-20])
            for target_date in target_dates:
                returns = {
                    peer.asset_code: value
                    for peer in group_items
                    if (
                        value := _return_at_date(peer.bars, target_date, 20)
                    )
                    is not None
                }
                if len(returns) < MINIMUM_PEER_COUNT:
                    continue
                percentile = _average_percentile_ranks(returns).get(target.asset_code)
                if percentile is not None:
                    best = percentile if best is None else max(best, percentile)
            if best is not None:
                result[target.asset_code] = best
    return result


def _breakout_observations(
    items: Sequence[LeaderPitAssetInput],
    *,
    sector_percentiles: Mapping[str, float],
    return_percentiles: Mapping[str, float],
    turnover_percentiles: Mapping[str, float],
    peer_counts: Mapping[str, int],
) -> dict[str, LeaderCandidateObservation]:
    observations: dict[str, LeaderCandidateObservation] = {}
    for item in items:
        unavailable = validate_leader_pit_input(
            item,
            required_history=BREAKOUT_HISTORY_SESSIONS,
        )
        components: dict[str, str | int | float | bool | None] = {
            "sector_trend_percentile": sector_percentiles.get(item.asset_code),
            "peer_return20_percentile": return_percentiles.get(item.asset_code),
            "peer_turnover20_percentile": turnover_percentiles.get(item.asset_code),
            "peer_count": peer_counts.get(item.asset_code, 0),
        }
        if unavailable:
            observations[item.asset_code] = _make_observation(
                item=item,
                candidate_id=LEADER_BREAKOUT_CANDIDATE,
                availability="unavailable",
                qualifies=False,
                score=None,
                components=components,
                unavailable_reasons=unavailable,
            )
            continue
        bars = item.bars
        ma5 = _moving_average(bars, 5)
        ma10 = _moving_average(bars, 10)
        ma20 = _moving_average(bars, 20)
        close = bars[-1].adjusted_close
        preceding_high = max(bar.adjusted_high for bar in bars[-21:-1])
        volume_max = max(bar.volume for bar in bars[-120:])
        sector_pct = sector_percentiles.get(item.asset_code)
        return_pct = return_percentiles.get(item.asset_code)
        turnover_pct = turnover_percentiles.get(item.asset_code)
        current_turnover20 = mean(bar.turnover for bar in bars[-20:])
        components.update(
            {
                "adjusted_ma5": ma5,
                "adjusted_ma10": ma10,
                "adjusted_ma20": ma20,
                "adjusted_close": close,
                "preceding_20_adjusted_high": preceding_high,
                "current_volume": bars[-1].volume,
                "latest_120_volume_max": volume_max,
                "average_turnover20": current_turnover20,
            }
        )
        missing_components = [
            label
            for label, value in (
                ("sector_percentile_unavailable", sector_pct),
                ("peer_return_percentile_unavailable", return_pct),
                ("peer_turnover_percentile_unavailable", turnover_pct),
            )
            if value is None
        ]
        if missing_components:
            observations[item.asset_code] = _make_observation(
                item=item,
                candidate_id=LEADER_BREAKOUT_CANDIDATE,
                availability="unavailable",
                qualifies=False,
                score=None,
                components=components,
                unavailable_reasons=missing_components,
            )
            continue
        gates = {
            "insufficient_peer_count": peer_counts.get(item.asset_code, 0)
            < MINIMUM_PEER_COUNT,
            "ma_alignment_failed": not (ma5 > ma10 > ma20),
            "price_breakout_failed": not (close > preceding_high),
            "volume_breakout_failed": not (bars[-1].volume >= volume_max),
            "sector_heat_gate_failed": float(sector_pct) < 2 / 3,
            "peer_leadership_gate_failed": float(return_pct) < 0.8,
            "peer_liquidity_gate_failed": float(turnover_pct) < 0.5,
        }
        gate_reasons = tuple(key for key, failed in gates.items() if failed)
        qualifies = not gate_reasons
        score = (
            mean((float(sector_pct), float(return_pct), float(turnover_pct)))
            if qualifies
            else None
        )
        observations[item.asset_code] = _make_observation(
            item=item,
            candidate_id=LEADER_BREAKOUT_CANDIDATE,
            availability="available",
            qualifies=qualifies,
            score=score,
            components=components,
            gate_reasons=gate_reasons,
        )
    return observations


def _repair_observations(
    items: Sequence[LeaderPitAssetInput],
    *,
    prior_leadership: Mapping[str, float],
    peer_counts: Mapping[str, int],
) -> dict[str, LeaderCandidateObservation]:
    raw: dict[str, dict[str, float]] = {}
    gate_reasons_by_code: dict[str, tuple[str, ...]] = {}
    unavailable_by_code: dict[str, tuple[str, ...]] = {}
    for item in items:
        unavailable = validate_leader_pit_input(
            item,
            required_history=REPAIR_HISTORY_SESSIONS,
            include_sector=False,
        )
        if unavailable:
            unavailable_by_code[item.asset_code] = unavailable
            continue
        bars = item.bars
        atr5 = _atr(bars, 5)
        atr20 = _atr(bars, 20)
        prior = prior_leadership.get(item.asset_code)
        if atr5 is None or atr20 is None or prior is None:
            missing: list[str] = []
            if atr5 is None:
                missing.append("adjusted_atr5_unavailable")
            if atr20 is None:
                missing.append("adjusted_atr20_unavailable")
            if prior is None:
                missing.append("prior_peer_leadership_unavailable")
            unavailable_by_code[item.asset_code] = tuple(missing)
            continue
        ma20 = _moving_average(bars, 20)
        close = bars[-1].adjusted_close
        drawdown = close / max(bar.adjusted_close for bar in bars[-120:]) - 1.0
        compression = atr5 / atr20
        overextension = abs(close - ma20) / atr20
        gates = {
            "insufficient_peer_count": peer_counts.get(item.asset_code, 0)
            < MINIMUM_PEER_COUNT,
            "prior_leadership_gate_failed": prior < 0.8,
            "drawdown_band_failed": not (-0.50 <= drawdown <= -0.30),
            "positive_stabilization_failed": not (
                close > bars[-1].adjusted_open
                and close > bars[-2].adjusted_close
            ),
            "range_compression_failed": compression > 0.75,
            "overextension_gate_failed": overextension > 1.0,
        }
        gate_reasons = tuple(key for key, failed in gates.items() if failed)
        gate_reasons_by_code[item.asset_code] = gate_reasons
        raw[item.asset_code] = {
            "prior_leadership_percentile": prior,
            "drawdown_120": drawdown,
            "adjusted_atr5": atr5,
            "adjusted_atr20": atr20,
            "atr5_atr20_ratio": compression,
            "adjusted_ma20": ma20,
            "overextension_atr": overextension,
            "adjusted_close": close,
            "adjusted_open": bars[-1].adjusted_open,
            "prior_adjusted_close": bars[-2].adjusted_close,
            "average_turnover20": mean(bar.turnover for bar in bars[-20:]),
            "peer_count": float(peer_counts.get(item.asset_code, 0)),
        }
    qualifying_codes = {
        code for code, reasons in gate_reasons_by_code.items() if not reasons
    }
    reverse_compression = _average_percentile_ranks(
        {code: raw[code]["atr5_atr20_ratio"] for code in qualifying_codes},
        reverse=True,
    )
    reverse_overextension = _average_percentile_ranks(
        {code: raw[code]["overextension_atr"] for code in qualifying_codes},
        reverse=True,
    )
    observations: dict[str, LeaderCandidateObservation] = {}
    by_code = {item.asset_code: item for item in items}
    for code, item in by_code.items():
        components: dict[str, str | int | float | bool | None] = dict(raw.get(code, {}))
        components["reverse_atr5_atr20_percentile"] = reverse_compression.get(code)
        components["reverse_overextension_atr_percentile"] = reverse_overextension.get(code)
        unavailable = unavailable_by_code.get(code, ())
        if unavailable:
            observations[code] = _make_observation(
                item=item,
                candidate_id=FORMER_LEADER_REPAIR_CANDIDATE,
                availability="unavailable",
                qualifies=False,
                score=None,
                components=components,
                unavailable_reasons=unavailable,
            )
            continue
        gates = gate_reasons_by_code.get(code, ())
        qualifies = not gates
        score = (
            mean(
                (
                    raw[code]["prior_leadership_percentile"],
                    reverse_compression[code],
                    reverse_overextension[code],
                )
            )
            if qualifies
            else None
        )
        observations[code] = _make_observation(
            item=item,
            candidate_id=FORMER_LEADER_REPAIR_CANDIDATE,
            availability="available",
            qualifies=qualifies,
            score=score,
            components=components,
            gate_reasons=gates,
        )
    return observations


def _routed_observation(
    item: LeaderPitAssetInput,
    *,
    breakout: LeaderCandidateObservation,
    repair: LeaderCandidateObservation,
) -> LeaderCandidateObservation:
    regime_reasons = _regime_reasons(item)
    components: dict[str, str | int | float | bool | None] = {
        "market_regime": item.market_regime,
        "market_regime_contract_hash": item.market_regime_contract_hash,
        "routed_candidate_id": None,
    }
    if regime_reasons:
        return _make_observation(
            item=item,
            candidate_id=CYCLE_ROUTED_LEADER_CANDIDATE,
            availability="unavailable",
            qualifies=False,
            score=None,
            components=components,
            unavailable_reasons=regime_reasons,
        )
    if item.market_regime in {"defensive", "cash_wait"}:
        return _make_observation(
            item=item,
            candidate_id=CYCLE_ROUTED_LEADER_CANDIDATE,
            availability="available",
            qualifies=False,
            score=None,
            components=components,
            gate_reasons=(f"market_regime_{item.market_regime}_no_selection",),
        )
    source = breakout if item.market_regime == "risk_on" else repair
    components["routed_candidate_id"] = source.candidate_id
    components["routed_feature_hash"] = source.feature_hash
    if source.availability != "available":
        return _make_observation(
            item=item,
            candidate_id=CYCLE_ROUTED_LEADER_CANDIDATE,
            availability="unavailable",
            qualifies=False,
            score=None,
            components=components,
            unavailable_reasons=source.unavailable_reasons,
        )
    return _make_observation(
        item=item,
        candidate_id=CYCLE_ROUTED_LEADER_CANDIDATE,
        availability="available",
        qualifies=source.qualifies,
        score=source.score,
        components=components,
        gate_reasons=source.gate_reasons,
    )


def _panel_with_hash(
    *,
    signal_date: date,
    source_cutoff: datetime,
    observations: Sequence[LeaderCandidateObservation],
) -> LeaderFeaturePanel:
    ordered = tuple(
        sorted(
            observations,
            key=lambda item: (
                LEADER_CANDIDATE_IDS.index(item.candidate_id),
                item.asset_code,
            ),
        )
    )
    return LeaderFeaturePanel(
        signal_date=signal_date,
        source_cutoff=source_cutoff,
        observations=ordered,
        panel_hash=stable_contract_hash(
            {
                "signal_date": signal_date,
                "source_cutoff": source_cutoff,
                "feature_hashes": tuple(item.feature_hash for item in ordered),
            }
        ),
    )


def apply_clone_representative_policy(
    panel: LeaderFeaturePanel,
) -> LeaderFeaturePanel:
    replacements: dict[tuple[str, str], LeaderCandidateObservation] = {}
    for candidate_id, rows in panel.by_candidate.items():
        groups: dict[str, list[LeaderCandidateObservation]] = defaultdict(list)
        for row in rows:
            if row.qualifies:
                groups[row.clone_group].append(row)
        for clone_rows in groups.values():
            if len(clone_rows) <= 1:
                continue
            representative = max(
                clone_rows,
                key=lambda row: (
                    float(dict(row.components).get("average_turnover20") or 0.0),
                    row.asset_code,
                ),
            )
            for row in clone_rows:
                if row.asset_code == representative.asset_code:
                    continue
                updated = _make_observation(
                    item=LeaderPitAssetInput(
                        asset_code=row.asset_code,
                        signal_date=row.signal_date,
                        source_cutoff=row.source_cutoff,
                        baseline_score=row.baseline_score,
                        bars=(),
                        historical_member=True,
                        membership_effective_date=row.signal_date,
                        membership_observed_at=row.source_cutoff,
                        peer_group=row.peer_group,
                        peer_mapping_effective_date=row.signal_date,
                        peer_mapping_observed_at=row.source_cutoff,
                        peer_mapping_kind="historical_pit",
                        sector_trend_score=0.0,
                        sector_trend_as_of=row.signal_date,
                        sector_trend_observed_at=row.source_cutoff,
                        sector_trend_contract_hash="0" * 64,
                        market_regime=None,
                        market_regime_as_of=None,
                        market_regime_observed_at=None,
                        market_regime_contract_hash=None,
                        market_regime_status="unavailable",
                        clone_group=row.clone_group,
                        tracked_index=row.tracked_index,
                        issuer=row.issuer,
                        theme=row.theme,
                        sector=row.sector,
                    ),
                    candidate_id=row.candidate_id,
                    availability=row.availability,
                    qualifies=False,
                    score=None,
                    components=dict(row.components),
                    gate_reasons=(*row.gate_reasons, "clone_not_representative"),
                    unavailable_reasons=row.unavailable_reasons,
                )
                replacements[(candidate_id, row.asset_code)] = updated
    observations = tuple(
        replacements.get((row.candidate_id, row.asset_code), row)
        for row in panel.observations
    )
    return _panel_with_hash(
        signal_date=panel.signal_date,
        source_cutoff=panel.source_cutoff,
        observations=observations,
    )


def build_leader_feature_panel(
    items: Sequence[LeaderPitAssetInput],
) -> LeaderFeaturePanel:
    if not items:
        raise ValueError("leader feature panel requires inputs")
    signal_dates = {item.signal_date for item in items}
    source_cutoffs = {item.source_cutoff for item in items}
    if len(signal_dates) != 1 or len(source_cutoffs) != 1:
        raise LeaderTacticsContractError(
            "leader feature panel requires one signal date and source cutoff"
        )
    if len({item.asset_code for item in items}) != len(items):
        raise LeaderTacticsContractError("leader feature panel asset codes must be unique")
    ordered_items = tuple(sorted(items, key=lambda item: item.asset_code))
    breakout_peer_items = tuple(
        item
        for item in ordered_items
        if not validate_leader_pit_input(
            item,
            required_history=BREAKOUT_HISTORY_SESSIONS,
        )
    )
    repair_peer_items = tuple(
        item
        for item in ordered_items
        if not validate_leader_pit_input(
            item,
            required_history=REPAIR_HISTORY_SESSIONS,
            include_sector=False,
        )
    )
    return_percentiles, turnover_percentiles, peer_counts = _current_peer_percentiles(
        breakout_peer_items
    )
    sector_percentiles = _sector_percentiles(breakout_peer_items)
    prior_leadership = _prior_leadership_percentiles(repair_peer_items)
    _, _, repair_peer_counts = _current_peer_percentiles(repair_peer_items)
    breakout = _breakout_observations(
        ordered_items,
        sector_percentiles=sector_percentiles,
        return_percentiles=return_percentiles,
        turnover_percentiles=turnover_percentiles,
        peer_counts=peer_counts,
    )
    repair = _repair_observations(
        ordered_items,
        prior_leadership=prior_leadership,
        peer_counts=repair_peer_counts,
    )
    routed = {
        item.asset_code: _routed_observation(
            item,
            breakout=breakout[item.asset_code],
            repair=repair[item.asset_code],
        )
        for item in ordered_items
    }
    panel = _panel_with_hash(
        signal_date=next(iter(signal_dates)),
        source_cutoff=next(iter(source_cutoffs)),
        observations=(
            *breakout.values(),
            *repair.values(),
            *routed.values(),
        ),
    )
    return apply_clone_representative_policy(panel)
