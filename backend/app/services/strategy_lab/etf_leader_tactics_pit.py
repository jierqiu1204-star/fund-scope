"""Fail-closed adapter from immutable ETF PIT artifacts to leader features."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfObservationPortfolioSnapshot,
    EtfPitCaptureSource,
    ShortResearchSignalItem,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    PRICE_BASIS,
    LeaderAdjustedBar,
    LeaderPitAssetInput,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    PointInTimeRankingInputSnapshot,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class LeaderPitAdapterError(ValueError):
    """Raised when immutable source artifacts cannot share one PIT identity."""


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _aware(value: datetime, *, timezone: ZoneInfo = _SHANGHAI) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _utc(value: datetime) -> datetime:
    return _aware(value).astimezone(UTC)


def _database_created_at(value: datetime) -> datetime:
    """Interpret model ``created_at`` values as the repository's naive UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).astimezone(_SHANGHAI)
    return value.astimezone(_SHANGHAI)


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


@dataclass(frozen=True)
class LeaderTaxonomyFact:
    asset_code: str
    peer_group: str
    effective_from: date
    effective_to: date | None
    observed_at: datetime
    mapping_kind: str
    taxonomy_contract_hash: str
    clone_group: str | None = None
    tracked_index: str | None = None
    issuer: str | None = None
    theme: str | None = None
    sector: str | None = None
    fact_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fact_hash")
        return payload

    def validate(self) -> None:
        if not self.asset_code.strip() or not self.peer_group.strip():
            raise LeaderPitAdapterError("taxonomy fact identity is incomplete")
        if not _is_sha256(self.taxonomy_contract_hash):
            raise LeaderPitAdapterError("taxonomy contract hash is invalid")
        if self.fact_hash != stable_contract_hash(self.canonical_payload()):
            raise LeaderPitAdapterError("taxonomy fact hash is incompatible")


@dataclass(frozen=True)
class LeaderSectorTrendFact:
    peer_group: str
    signal_date: date
    score: float
    observed_at: datetime
    contract_hash: str
    fact_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fact_hash")
        return payload

    def validate(self) -> None:
        if not self.peer_group.strip() or _finite(self.score) is None:
            raise LeaderPitAdapterError("sector trend fact is incomplete")
        if not _is_sha256(self.contract_hash):
            raise LeaderPitAdapterError("sector trend contract hash is invalid")
        if self.fact_hash != stable_contract_hash(self.canonical_payload()):
            raise LeaderPitAdapterError("sector trend fact hash is incompatible")


@dataclass(frozen=True)
class LeaderRegimeFact:
    signal_date: date
    market_regime: str
    observed_at: datetime
    contract_hash: str
    status: str
    fact_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fact_hash")
        return payload

    def validate(self) -> None:
        if self.status not in {"available", "unavailable", "stale"}:
            raise LeaderPitAdapterError("regime availability is invalid")
        if self.contract_hash != REGIME_LIQUIDITY_GATE_CONTRACT_HASH:
            raise LeaderPitAdapterError("regime contract hash is incompatible")
        if self.fact_hash != stable_contract_hash(self.canonical_payload()):
            raise LeaderPitAdapterError("regime fact hash is incompatible")


@dataclass(frozen=True)
class LeaderBaselineScoreFact:
    asset_code: str
    signal_date: date
    score: float
    observed_at: datetime
    research_contract_hash: str
    input_snapshot_hash: str
    fact_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fact_hash")
        return payload

    def validate(self) -> None:
        if not self.asset_code.strip() or _finite(self.score) is None:
            raise LeaderPitAdapterError("baseline score fact is incomplete")
        if not _is_sha256(self.research_contract_hash) or not _is_sha256(
            self.input_snapshot_hash
        ):
            raise LeaderPitAdapterError("baseline score provenance is invalid")
        if self.fact_hash != stable_contract_hash(self.canonical_payload()):
            raise LeaderPitAdapterError("baseline score fact hash is incompatible")


@dataclass(frozen=True)
class LeaderPitAdaptation:
    signal_date: date
    source_cutoff: datetime
    capture_source_context_hash: str
    replay_input_hash: str
    inputs: tuple[LeaderPitAssetInput, ...]
    exclusions: Mapping[str, tuple[str, ...]]
    adapter_hash: str


@dataclass(frozen=True)
class LeaderSourceBoundFacts:
    taxonomy: Mapping[str, LeaderTaxonomyFact]
    sector_trends: Mapping[str, LeaderSectorTrendFact]
    baseline_scores: Mapping[str, LeaderBaselineScoreFact]
    regime: LeaderRegimeFact | None
    exclusions: Mapping[str, tuple[str, ...]]
    facts_hash: str


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fact(value: Any) -> Any:
    return value.__class__(
        **{
            **asdict(value),
            "fact_hash": stable_contract_hash(value.canonical_payload()),
        }
    )


async def load_leader_source_bound_facts(
    session: AsyncSession,
    *,
    capture_source: EtfPitCaptureSource,
    asset_codes: Sequence[str],
) -> LeaderSourceBoundFacts:
    """Freeze only metadata already carried by the captured published source.

    This loader deliberately never reads current taxonomy tables.  A missing or
    late source field remains unavailable so old sessions cannot be repaired
    with today's metadata.
    """

    codes = tuple(sorted({code.strip() for code in asset_codes if code.strip()}))
    visibility_cutoff = _aware(capture_source.replay_visibility_cutoff)
    rows = (
        (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(
                    ShortResearchSignalItem.run_id
                    == capture_source.source_signal_run_id,
                    ShortResearchSignalItem.asset_type == "etf",
                    ShortResearchSignalItem.asset_code.in_(codes),
                )
                .order_by(ShortResearchSignalItem.asset_code.asc())
            )
        ).all()
        if codes
        else []
    )
    taxonomy: dict[str, LeaderTaxonomyFact] = {}
    sector_trends: dict[str, LeaderSectorTrendFact] = {}
    baseline_scores: dict[str, LeaderBaselineScoreFact] = {}
    exclusions: dict[str, tuple[str, ...]] = {}
    for row in rows:
        observed_at = _database_created_at(row.created_at)
        metrics = _mapping(row.metrics_json)
        rationale = _mapping(row.rationale_json)
        profile = _mapping(
            metrics.get("theme_profile") or rationale.get("theme_profile")
        )
        v3_inputs = _mapping(metrics.get("v3_input_values"))
        tracked_index = _first_text(
            metrics.get("tracked_underlying_id"),
            v3_inputs.get("tracked_underlying_id"),
        )
        peer_group = _first_text(
            metrics.get("theme_group"),
            v3_inputs.get("theme_group"),
            profile.get("theme_group"),
            profile.get("primary_theme"),
            tracked_index,
        )
        reasons: list[str] = []
        if observed_at > visibility_cutoff:
            reasons.append("source_item_received_after_visibility_cutoff")
        if peer_group is None:
            reasons.append("missing_historical_peer_mapping")
        if not reasons and peer_group is not None:
            taxonomy_contract_hash = stable_contract_hash(
                {
                    "schema_version": "leader_source_taxonomy_v1",
                    "source_context_hash": capture_source.source_context_hash,
                    "research_contract_hash": capture_source.research_contract_hash,
                    "fields": (
                        "theme_group",
                        "tracked_underlying_id",
                        "theme_profile",
                    ),
                }
            )
            taxonomy[row.asset_code] = _fact(
                LeaderTaxonomyFact(
                    asset_code=row.asset_code,
                    peer_group=peer_group,
                    effective_from=capture_source.as_of_trade_date,
                    effective_to=capture_source.as_of_trade_date,
                    observed_at=observed_at,
                    mapping_kind="historical_pit",
                    taxonomy_contract_hash=taxonomy_contract_hash,
                    clone_group=tracked_index or row.asset_code,
                    tracked_index=tracked_index,
                    issuer=_first_text(metrics.get("issuer"), profile.get("issuer")),
                    theme=_first_text(
                        profile.get("primary_theme"),
                        profile.get("theme"),
                        peer_group,
                    ),
                    sector=_first_text(
                        metrics.get("sector"),
                        profile.get("sector"),
                        profile.get("theme_group"),
                    ),
                )
            )

        sector_score = _finite(
            metrics.get("sector_trend_score")
            if "sector_trend_score" in metrics
            else v3_inputs.get("sector_trend_score")
        )
        if (
            not reasons
            and peer_group is not None
            and sector_score is not None
        ):
            sector_trends[row.asset_code] = _fact(
                LeaderSectorTrendFact(
                    peer_group=peer_group,
                    signal_date=capture_source.as_of_trade_date,
                    score=sector_score,
                    observed_at=observed_at,
                    contract_hash=stable_contract_hash(
                        {
                            "schema_version": "leader_source_sector_trend_v1",
                            "ranking_contract_hash": (
                                capture_source.ranking_contract_hash
                            ),
                            "source_context_hash": (
                                capture_source.source_context_hash
                            ),
                        }
                    ),
                    fact_hash="",
                )
            )
        elif observed_at <= visibility_cutoff:
            reasons.append("sector_trend_fact_unavailable")

        baseline_score = _finite(
            row.ranking_score
            if row.ranking_score is not None
            else metrics.get("v3_ranking_score")
        )
        if observed_at <= visibility_cutoff and baseline_score is not None:
            baseline_scores[row.asset_code] = _fact(
                LeaderBaselineScoreFact(
                    asset_code=row.asset_code,
                    signal_date=capture_source.as_of_trade_date,
                    score=baseline_score,
                    observed_at=observed_at,
                    research_contract_hash=(
                        capture_source.research_contract_hash
                    ),
                    input_snapshot_hash=capture_source.input_snapshot_hash,
                    fact_hash="",
                )
            )
        elif baseline_score is None:
            reasons.append("baseline_score_fact_unavailable")
        if reasons:
            exclusions[row.asset_code] = tuple(sorted(set(reasons)))

    missing_rows = set(codes) - {row.asset_code for row in rows}
    for code in missing_rows:
        exclusions[code] = ("source_signal_item_missing",)

    portfolio = await session.scalar(
        select(EtfObservationPortfolioSnapshot)
        .where(
            EtfObservationPortfolioSnapshot.source_signal_run_id
            == capture_source.source_signal_run_id,
            EtfObservationPortfolioSnapshot.status == "success",
            EtfObservationPortfolioSnapshot.as_of_date
            == capture_source.as_of_trade_date,
        )
        .order_by(
            EtfObservationPortfolioSnapshot.created_at.desc(),
            EtfObservationPortfolioSnapshot.id.desc(),
        )
        .limit(1)
    )
    regime: LeaderRegimeFact | None = None
    if portfolio is not None:
        observed_at = _database_created_at(portfolio.created_at)
        summary = _mapping(portfolio.summary_json)
        market_regime = _first_text(summary.get("market_regime"))
        status = (
            "available"
            if observed_at <= visibility_cutoff
            and market_regime in {"risk_on", "neutral", "defensive", "cash_wait"}
            else "unavailable"
        )
        regime = _fact(
            LeaderRegimeFact(
                signal_date=capture_source.as_of_trade_date,
                market_regime=market_regime or "unavailable",
                observed_at=observed_at,
                contract_hash=REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
                status=status,
                fact_hash="",
            )
        )

    payload = {
        "source_context_hash": capture_source.source_context_hash,
        "asset_codes": codes,
        "taxonomy": {
            code: item.fact_hash for code, item in sorted(taxonomy.items())
        },
        "sector_trends": {
            code: item.fact_hash
            for code, item in sorted(sector_trends.items())
        },
        "baseline_scores": {
            code: item.fact_hash
            for code, item in sorted(baseline_scores.items())
        },
        "regime": regime.fact_hash if regime is not None else None,
        "exclusions": exclusions,
    }
    return LeaderSourceBoundFacts(
        taxonomy=taxonomy,
        sector_trends=sector_trends,
        baseline_scores=baseline_scores,
        regime=regime,
        exclusions=exclusions,
        facts_hash=stable_contract_hash(payload),
    )


def _validate_capture_source(
    source: EtfPitCaptureSource,
    snapshot: PointInTimeRankingInputSnapshot,
) -> None:
    hashes = (
        source.source_snapshot_hash,
        source.source_context_hash,
        source.universe_manifest_hash,
        source.input_snapshot_hash,
        source.ranking_contract_hash,
        source.research_contract_hash,
        source.actionable_contract_hash,
        source.provider_health_hash,
    )
    context = source.source_context_json if isinstance(source.source_context_json, dict) else {}
    if (
        source.readiness_state != "complete"
        or source.target_date_coverage_ratio < 0.95
        or source.warmup_coverage_ratio < 0.95
        or source.cutoff_timezone != "Asia/Shanghai"
        or not all(_is_sha256(value) for value in hashes)
        or stable_contract_hash(context) != source.source_context_hash
    ):
        raise LeaderPitAdapterError("leader capture source is incomplete or incompatible")
    if source.as_of_trade_date != snapshot.replay_date:
        raise LeaderPitAdapterError("capture and replay dates do not match")
    replay_cutoff = _aware(snapshot.decision_cutoff)
    market_cutoff = _aware(source.market_decision_cutoff)
    visibility_cutoff = _aware(source.replay_visibility_cutoff)
    receipt_cutoff = _aware(source.data_receipt_cutoff)
    if (
        replay_cutoff not in {market_cutoff, visibility_cutoff}
        or not market_cutoff <= visibility_cutoff <= receipt_cutoff
    ):
        raise LeaderPitAdapterError("capture and replay cutoffs are incompatible")


def adapt_leader_pit_inputs(
    *,
    capture_source: EtfPitCaptureSource,
    replay_snapshot: PointInTimeRankingInputSnapshot,
    taxonomy_facts: Mapping[str, LeaderTaxonomyFact],
    sector_trend_facts: Mapping[str, LeaderSectorTrendFact],
    baseline_score_facts: Mapping[str, LeaderBaselineScoreFact],
    regime_fact: LeaderRegimeFact | None,
) -> LeaderPitAdaptation:
    """Adapt persisted facts only; absent historical metadata stays unavailable."""

    _validate_capture_source(capture_source, replay_snapshot)
    if regime_fact is not None:
        regime_fact.validate()
    source_cutoff = replay_snapshot.decision_cutoff
    cutoff_utc = _utc(source_cutoff)
    inputs: list[LeaderPitAssetInput] = []
    exclusions: dict[str, tuple[str, ...]] = {}
    for series in sorted(replay_snapshot.eligible_inputs, key=lambda item: item.asset_code):
        code = series.asset_code
        reasons: list[str] = []
        taxonomy = taxonomy_facts.get(code)
        if taxonomy is not None:
            taxonomy.validate()
            if taxonomy.asset_code != code:
                raise LeaderPitAdapterError("taxonomy asset identity mismatch")
        else:
            reasons.append("missing_historical_peer_mapping")

        baseline = baseline_score_facts.get(code)
        if baseline is not None:
            baseline.validate()
            if (
                baseline.asset_code != code
                or baseline.signal_date != replay_snapshot.replay_date
                or baseline.research_contract_hash != capture_source.research_contract_hash
                or baseline.input_snapshot_hash != capture_source.input_snapshot_hash
                or _utc(baseline.observed_at) > cutoff_utc
            ):
                baseline = None

        sector_fact = (
            sector_trend_facts.get(code)
            or sector_trend_facts.get(taxonomy.peer_group)
            if taxonomy is not None
            else None
        )
        if sector_fact is not None:
            sector_fact.validate()
            if sector_fact.peer_group != taxonomy.peer_group:
                raise LeaderPitAdapterError("sector trend peer identity mismatch")

        bars: list[LeaderAdjustedBar] = []
        for bar in series.bars:
            if bar.turnover is None or _finite(bar.turnover) is None or bar.turnover < 0:
                reasons.append("adjusted_turnover_unavailable")
                continue
            bars.append(
                LeaderAdjustedBar(
                    trade_date=bar.session_date,
                    adjusted_open=bar.adjusted_open,
                    adjusted_high=bar.adjusted_high,
                    adjusted_low=bar.adjusted_low,
                    adjusted_close=bar.adjusted_close,
                    volume=bar.volume,
                    turnover=bar.turnover,
                    observed_at=series.latest_source_timestamp,
                    decision_eligible=True,
                    price_basis=PRICE_BASIS,
                    data_provider=series.provenance.provider,
                    provider_version=series.provenance.adjustment_version,
                    adjustment_version=series.provenance.adjustment_version,
                )
            )
        if len(bars) != len(series.bars):
            bars = []

        peer_visible = (
            taxonomy is not None
            and taxonomy.effective_from <= replay_snapshot.replay_date
            and (
                taxonomy.effective_to is None
                or taxonomy.effective_to >= replay_snapshot.replay_date
            )
            and _utc(taxonomy.observed_at) <= cutoff_utc
        )
        if taxonomy is not None and not peer_visible:
            reasons.append("historical_peer_mapping_not_visible")
        sector_visible = (
            sector_fact is not None
            and sector_fact.signal_date == replay_snapshot.replay_date
            and _utc(sector_fact.observed_at) <= cutoff_utc
        )
        regime_visible = (
            regime_fact is not None
            and regime_fact.signal_date == replay_snapshot.replay_date
            and _utc(regime_fact.observed_at) <= cutoff_utc
        )
        inputs.append(
            LeaderPitAssetInput(
                asset_code=code,
                signal_date=replay_snapshot.replay_date,
                source_cutoff=source_cutoff,
                baseline_score=baseline.score if baseline is not None else None,
                bars=tuple(bars),
                historical_member=True,
                membership_effective_date=series.metadata.eligible_from,
                membership_observed_at=series.metadata.membership_known_at,
                peer_group=taxonomy.peer_group if peer_visible else None,
                peer_mapping_effective_date=(
                    taxonomy.effective_from if taxonomy is not None else None
                ),
                peer_mapping_observed_at=(
                    taxonomy.observed_at if taxonomy is not None else None
                ),
                peer_mapping_kind=(
                    taxonomy.mapping_kind if taxonomy is not None else "unavailable"
                ),
                sector_trend_score=(sector_fact.score if sector_visible else None),
                sector_trend_as_of=(
                    sector_fact.signal_date if sector_fact is not None else None
                ),
                sector_trend_observed_at=(
                    sector_fact.observed_at if sector_fact is not None else None
                ),
                sector_trend_contract_hash=(
                    sector_fact.contract_hash if sector_fact is not None else None
                ),
                market_regime=(regime_fact.market_regime if regime_visible else None),
                market_regime_as_of=(
                    regime_fact.signal_date if regime_fact is not None else None
                ),
                market_regime_observed_at=(
                    regime_fact.observed_at if regime_fact is not None else None
                ),
                market_regime_contract_hash=(
                    regime_fact.contract_hash if regime_fact is not None else None
                ),
                market_regime_status=(
                    regime_fact.status if regime_visible else "unavailable"
                ),
                clone_group=taxonomy.clone_group if taxonomy is not None else None,
                tracked_index=taxonomy.tracked_index if taxonomy is not None else None,
                issuer=taxonomy.issuer if taxonomy is not None else None,
                theme=taxonomy.theme if taxonomy is not None else None,
                sector=taxonomy.sector if taxonomy is not None else None,
                input_unavailable_reasons=tuple(sorted(set(reasons))),
            )
        )
        if reasons:
            exclusions[code] = tuple(sorted(set(reasons)))

    payload = {
        "signal_date": replay_snapshot.replay_date,
        "source_cutoff": source_cutoff,
        "capture_source_context_hash": capture_source.source_context_hash,
        "replay_input_hash": replay_snapshot.input_hash,
        "asset_inputs": tuple(asdict(item) for item in inputs),
        "exclusions": dict(sorted(exclusions.items())),
    }
    return LeaderPitAdaptation(
        signal_date=replay_snapshot.replay_date,
        source_cutoff=source_cutoff,
        capture_source_context_hash=capture_source.source_context_hash,
        replay_input_hash=replay_snapshot.input_hash,
        inputs=tuple(inputs),
        exclusions=dict(sorted(exclusions.items())),
        adapter_hash=stable_contract_hash(payload),
    )
