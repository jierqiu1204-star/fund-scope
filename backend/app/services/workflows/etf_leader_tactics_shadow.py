"""Production-safe orchestration entry for leader-tactics research pages."""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import EtfFactorExperimentEvidence, EtfPitCaptureSource
from app.services import market_data
from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.coverage_policy import (
    ETF_COMPLETE_SCORE_COVERAGE,
    ETF_DAILY_DECISION_MIN_COVERAGE,
    ETF_LEGACY_COMPLETE_SCORE_COVERAGE,
    ETF_PREVIOUS_READINESS_POLICY_VERSION,
    ETF_READINESS_POLICY_VERSION,
)
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_leader_tactics_continuation import (
    LeaderContinuationCheckpoint,
    LeaderContinuationHandlers,
    LeaderContinuationManifest,
    LeaderContinuationPage,
    LeaderPageArtifact,
    leader_continuation_view,
    run_bounded_leader_tactics_continuation,
)
from app.services.strategy_lab.etf_leader_tactics_ma5 import (
    SealedLeaderEntry,
    evaluate_ma5_exit_proxy,
)
from app.services.strategy_lab.etf_leader_tactics_observation import (
    LEADER_MATURITY_EXPERIMENT_FAMILY,
    LEADER_OBSERVATION_EXPERIMENT_FAMILY,
    LeaderObservationFinalization,
    LeaderObservationManifest,
    LeaderObservationMatch,
    LeaderObservationPrimitive,
    LeaderObservationProgress,
    LeaderObservationReport,
    LeaderOutcomeMaturityManifest,
    LeaderPromotionGateProgress,
    build_leader_matured_outcome,
    build_leader_observation_primitive,
    finalize_leader_observation_primitives,
    persist_leader_maturity_evidence,
    persist_leader_observation_evidence,
)
from app.services.strategy_lab.etf_leader_tactics_pit import (
    adapt_leader_pit_inputs,
    load_leader_source_bound_facts,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_HYPOTHESIS_REGISTRY,
    REPAIR_HISTORY_SESSIONS,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    ForwardAdjustedClose,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    MAX_CODES_PER_REPLAY_INPUT_PAGE,
    load_point_in_time_ranking_inputs,
)

LEADER_CONTINUATION_JOB_NAME = "etf_leader_tactics_shadow_continue"
LEADER_CONTINUATION_DISABLED = "leader_tactics_continuation_disabled"
LEADER_CODE_VERSION_MISSING = "leader_tactics_code_version_missing"
LEADER_PIT_SESSIONS_INSUFFICIENT = "leader_tactics_requires_252_pit_sessions"
LEADER_PIT_SOURCE_UNAVAILABLE = "leader_tactics_complete_pit_source_unavailable"
LEADER_PIT_BACKLOG_SCAN_LIMIT = "leader_tactics_pit_backlog_scan_limit"
LEADER_HISTORICAL_TAXONOMY_MISSING = (
    "leader_historical_taxonomy_artifacts_unavailable"
)
MINIMUM_LEADER_PIT_SESSIONS = 252
MAX_SOURCE_DISCOVERY_ROWS = 512
MAX_SOURCE_ROWS_PER_PAGE = 16 * REPAIR_HISTORY_SESSIONS * 4
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MATURITY_PRICE_CONTRACT_HASH = stable_contract_hash(
    {
        "schema_version": "leader_adjusted_outcome_price_v1",
        "price_basis": "total_return_adjusted",
        "entry": "signal_t_plus_one_decision_eligible_adjusted_close",
        "exit": "entry_plus_full_sessions_adjusted_close",
        "forbidden_providers": ("efinance", "sina"),
        "no_intraday_fill": True,
    }
)


def _complete_pit_source_coverage_clause():
    current_policy = and_(
        EtfPitCaptureSource.readiness_policy_version.in_(
            (
                ETF_PREVIOUS_READINESS_POLICY_VERSION,
                ETF_READINESS_POLICY_VERSION,
            )
        ),
        EtfPitCaptureSource.warmup_coverage_ratio
        >= ETF_COMPLETE_SCORE_COVERAGE,
    )
    legacy_policy = and_(
        or_(
            EtfPitCaptureSource.readiness_policy_version
            .notin_(
                (
                    ETF_PREVIOUS_READINESS_POLICY_VERSION,
                    ETF_READINESS_POLICY_VERSION,
                )
            ),
            EtfPitCaptureSource.readiness_policy_version.is_(None),
        ),
        EtfPitCaptureSource.warmup_coverage_ratio
        >= ETF_LEGACY_COMPLETE_SCORE_COVERAGE,
    )
    return or_(current_policy, legacy_policy)


async def _pit_session_count(session: AsyncSession) -> int:
    value = await session.scalar(
        select(func.count(distinct(EtfPitCaptureSource.as_of_trade_date))).where(
            EtfPitCaptureSource.readiness_state == "complete",
            EtfPitCaptureSource.target_date_coverage_ratio
            >= ETF_DAILY_DECISION_MIN_COVERAGE,
            _complete_pit_source_coverage_clause(),
        )
    )
    return int(value or 0)


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_SHANGHAI)
    return value.astimezone(_SHANGHAI)


def build_leader_observation_manifest_for_source(
    source: EtfPitCaptureSource,
    *,
    code_version: str,
) -> LeaderObservationManifest:
    manifest = LeaderObservationManifest(
        source_id=int(source.id),
        source_signal_run_id=int(source.source_signal_run_id),
        signal_date=source.as_of_trade_date,
        source_context_hash=source.source_context_hash,
        source_snapshot_hash=source.source_snapshot_hash,
        universe_manifest_hash=source.universe_manifest_hash,
        input_snapshot_hash=source.input_snapshot_hash,
        ranking_contract_hash=source.ranking_contract_hash,
        research_contract_hash=source.research_contract_hash,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        candidate_registry_hash=FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        data_cutoff=_local(source.replay_visibility_cutoff),
        code_version=code_version,
    )
    manifest.validate()
    return manifest


def build_leader_continuation_manifest_for_source(
    source: EtfPitCaptureSource,
    *,
    code_version: str,
) -> LeaderContinuationManifest:
    observation = build_leader_observation_manifest_for_source(
        source,
        code_version=code_version,
    )
    leader_manifest_hash = stable_contract_hash(
        {
            "schema_version": "leader_observation_lane_v1",
            "observation_manifest_hash": observation.manifest_hash,
            "hypothesis_registry_hash": LEADER_HYPOTHESIS_REGISTRY.registry_hash,
            "candidate_registry_hash": (
                FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash
            ),
        }
    )
    factor_manifest_hash = stable_contract_hash(
        {
            "schema_version": "leader_observation_factor_lane_v1",
            "leader_manifest_hash": leader_manifest_hash,
            "research_contract_hash": source.research_contract_hash,
            "ranking_contract_hash": source.ranking_contract_hash,
        }
    )
    manifest = LeaderContinuationManifest(
        run_key=f"leader-observation:{source.id}:{observation.manifest_hash}",
        code_version=code_version,
        leader_manifest_hash=leader_manifest_hash,
        factor_manifest_hash=factor_manifest_hash,
        source_snapshot_hash=source.source_snapshot_hash,
        universe_manifest_hash=source.universe_manifest_hash,
        input_snapshot_hash=source.input_snapshot_hash,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        candidate_registry_hash=FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        data_cutoff=observation.data_cutoff,
    )
    manifest.validate()
    return manifest


async def _oldest_due_source(
    session: AsyncSession,
    *,
    code_version: str,
) -> tuple[EtfPitCaptureSource | None, bool]:
    sources = (
        await session.scalars(
            select(EtfPitCaptureSource)
            .where(
                EtfPitCaptureSource.readiness_state == "complete",
                EtfPitCaptureSource.target_date_coverage_ratio
                >= ETF_DAILY_DECISION_MIN_COVERAGE,
                _complete_pit_source_coverage_clause(),
            )
            .order_by(
                EtfPitCaptureSource.as_of_trade_date.asc(),
                EtfPitCaptureSource.id.asc(),
            )
            .limit(MAX_SOURCE_DISCOVERY_ROWS)
        )
    ).all()
    if not sources:
        return None, False
    manifest_hashes = {
        source.id: build_leader_observation_manifest_for_source(
            source,
            code_version=code_version,
        ).manifest_hash
        for source in sources
    }
    materialized = set(
        (
            await session.scalars(
                select(EtfFactorExperimentEvidence.manifest_hash).where(
                    EtfFactorExperimentEvidence.experiment_family
                    == LEADER_OBSERVATION_EXPERIMENT_FAMILY,
                    EtfFactorExperimentEvidence.code_version == code_version,
                    EtfFactorExperimentEvidence.manifest_hash.in_(
                        tuple(manifest_hashes.values())
                    ),
                )
            )
        ).all()
    )
    for source in sources:
        if manifest_hashes[source.id] not in materialized:
            return source, False
    return None, len(sources) == MAX_SOURCE_DISCOVERY_ROWS


async def _latest_maturity_source(
    session: AsyncSession,
    *,
    after_date: date,
) -> EtfPitCaptureSource | None:
    return await session.scalar(
        select(EtfPitCaptureSource)
        .where(
            EtfPitCaptureSource.readiness_state == "complete",
            EtfPitCaptureSource.target_date_coverage_ratio
            >= ETF_DAILY_DECISION_MIN_COVERAGE,
            _complete_pit_source_coverage_clause(),
            EtfPitCaptureSource.as_of_trade_date > after_date,
        )
        .order_by(
            EtfPitCaptureSource.as_of_trade_date.desc(),
            EtfPitCaptureSource.id.desc(),
        )
        .limit(1)
    )


async def _oldest_due_maturity(
    session: AsyncSession,
    *,
    code_version: str,
) -> tuple[EtfFactorExperimentEvidence, EtfPitCaptureSource] | None:
    observations = (
        await session.scalars(
            select(EtfFactorExperimentEvidence)
            .where(
                EtfFactorExperimentEvidence.experiment_family
                == LEADER_OBSERVATION_EXPERIMENT_FAMILY,
                EtfFactorExperimentEvidence.code_version == code_version,
            )
            .order_by(EtfFactorExperimentEvidence.created_at.asc())
            .limit(128)
        )
    ).all()
    maturities = (
        await session.scalars(
            select(EtfFactorExperimentEvidence)
            .where(
                EtfFactorExperimentEvidence.experiment_family
                == LEADER_MATURITY_EXPERIMENT_FAMILY,
                EtfFactorExperimentEvidence.code_version == code_version,
            )
            .order_by(EtfFactorExperimentEvidence.created_at.desc())
            .limit(256)
        )
    ).all()
    latest_by_observation: dict[str, EtfFactorExperimentEvidence] = {}
    for row in maturities:
        report = dict(row.report_json or {})
        observation_hash = str(report.get("observation_manifest_hash") or "")
        latest_by_observation.setdefault(observation_hash, row)
    for observation in observations:
        report = dict(observation.report_json or {})
        pending = list(report.get("pending_outcomes") or ())
        if not pending:
            continue
        signal_date = date.fromisoformat(str(report["signal_date"]))
        source = await _latest_maturity_source(session, after_date=signal_date)
        if source is None:
            continue
        latest = latest_by_observation.get(observation.manifest_hash)
        if latest is not None:
            latest_report = dict(latest.report_json or {})
            if int(latest_report.get("pending_outcome_count") or 0) == 0:
                continue
            latest_cutoff = datetime.fromisoformat(
                str(latest_report["outcome_cutoff"])
            )
            if latest_cutoff >= _local(source.replay_visibility_cutoff):
                continue
        return observation, source
    return None


def _decision_eligible_outcome_prices(
    facts: tuple[market_data.EtfAdjustedDailyFact, ...],
    *,
    signal_date: date,
    cutoff: datetime,
) -> dict[str, tuple[tuple[date, float], ...]]:
    cutoff_utc = cutoff.astimezone(UTC).replace(tzinfo=None)
    values: dict[str, list[tuple[date, float]]] = {}
    for fact in facts:
        source_time = fact.source_timestamp
        if source_time is not None and source_time.tzinfo is not None:
            source_time = source_time.astimezone(UTC).replace(tzinfo=None)
        provider = str(fact.data_provider or "").strip().lower()
        close = fact.adjusted_close
        if (
            fact.trade_date <= signal_date
            or fact.decision_eligible is not True
            or fact.research_price_basis != "total_return_adjusted"
            or provider in {"sina", "efinance"}
            or source_time is None
            or source_time > cutoff_utc
            or close is None
            or not math.isfinite(float(close))
            or float(close) <= 0
        ):
            continue
        values.setdefault(fact.etf_code, []).append(
            (fact.trade_date, float(close))
        )
    return {
        code: tuple(sorted(set(rows), key=lambda item: item[0]))
        for code, rows in values.items()
    }


def _decision_eligible_ma5_prices(
    facts: tuple[market_data.EtfAdjustedDailyFact, ...],
    *,
    cutoff: datetime,
) -> dict[str, tuple[ForwardAdjustedClose, ...]]:
    cutoff_utc = cutoff.astimezone(UTC).replace(tzinfo=None)
    values: dict[str, list[ForwardAdjustedClose]] = {}
    for fact in facts:
        source_time = fact.source_timestamp
        if source_time is not None and source_time.tzinfo is not None:
            source_time = source_time.astimezone(UTC).replace(tzinfo=None)
        provider = str(fact.data_provider or "").strip().lower()
        close = fact.adjusted_close
        if (
            fact.decision_eligible is not True
            or fact.research_price_basis != "total_return_adjusted"
            or provider in {"sina", "efinance"}
            or source_time is None
            or source_time > cutoff_utc
            or close is None
            or not math.isfinite(float(close))
            or float(close) <= 0
            or not fact.adjustment_version
        ):
            continue
        values.setdefault(fact.etf_code, []).append(
            ForwardAdjustedClose(
                asset_code=fact.etf_code,
                session_date=fact.trade_date,
                adjusted_close=float(close),
                price_basis="total_return_adjusted",
                decision_eligible=True,
                provider=provider,
                adjustment_version=str(fact.adjustment_version),
                source_hash=stable_contract_hash(
                    {
                        "asset_code": fact.etf_code,
                        "trade_date": fact.trade_date,
                        "adjusted_close": close,
                        "provider": provider,
                        "provider_version": fact.provider_version,
                        "adjustment_version": fact.adjustment_version,
                        "source_timestamp": source_time,
                    }
                ),
            )
        )
    return {
        code: tuple(sorted(rows, key=lambda item: item.session_date))
        for code, rows in values.items()
    }


def _ma5_result_payload(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    for field_name in (
        "signal_date",
        "entry_session",
        "trigger_session",
        "exit_session",
    ):
        field_value = payload.get(field_name)
        if isinstance(field_value, date):
            payload[field_name] = field_value.isoformat()
    return payload


async def mature_oldest_leader_observation(
    session: AsyncSession,
    *,
    code_version: str,
) -> dict[str, Any] | None:
    due = await _oldest_due_maturity(session, code_version=code_version)
    if due is None:
        return None
    observation, source = due
    report = dict(observation.report_json or {})
    pending = tuple(dict(item) for item in report.get("pending_outcomes") or ())
    codes = tuple(sorted({str(item["asset_code"]) for item in pending}))
    facts = await market_data.etf_adjusted_daily_facts_on_or_before(
        session,
        etf_codes=codes,
        replay_date=source.as_of_trade_date,
        rows_per_code=64,
        max_source_rows=max(1, len(codes) * 64),
    )
    cutoff = _local(source.replay_visibility_cutoff)
    prices = _decision_eligible_outcome_prices(
        facts,
        signal_date=date.fromisoformat(str(report["signal_date"])),
        cutoff=cutoff,
    )
    ma5_prices = _decision_eligible_ma5_prices(facts, cutoff=cutoff)
    round_trip_cost_bps = 2.0 * (
        RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE
    )
    outcomes = tuple(
        build_leader_matured_outcome(
            candidate_id=str(item["candidate_id"]),
            asset_code=str(item["asset_code"]),
            signal_date=date.fromisoformat(str(item["signal_date"])),
            horizon_sessions=int(horizon),
            feature_hash=str(item["feature_hash"]),
            adjusted_closes_after_signal=prices.get(
                str(item["asset_code"]), ()
            ),
            round_trip_cost_bps=round_trip_cost_bps,
        )
        for item in pending
        for horizon in item.get("pending_horizons") or (5, 10)
    )
    entries = tuple(
        SealedLeaderEntry(
            candidate_id=str(item["candidate_id"]),
            asset_code=str(item["asset_code"]),
            signal_date=date.fromisoformat(str(item["signal_date"])),
            source_sample_hash=str(item["feature_hash"]),
        )
        for item in pending
    )
    trading_sessions = tuple(
        sorted(
            {
                row.session_date
                for rows in ma5_prices.values()
                for row in rows
            }
        )
    )
    ma5_results = evaluate_ma5_exit_proxy(
        entries,
        trading_sessions=trading_sessions,
        adjusted_closes_by_code=ma5_prices,
    )
    maturity_manifest = LeaderOutcomeMaturityManifest(
        observation_manifest_hash=observation.manifest_hash,
        observation_hash=str(report["observation_hash"]),
        signal_date=date.fromisoformat(str(report["signal_date"])),
        outcome_cutoff=cutoff,
        execution_cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        adjusted_price_contract_hash=_MATURITY_PRICE_CONTRACT_HASH,
        code_version=code_version,
    )
    await persist_leader_maturity_evidence(
        session,
        manifest=maturity_manifest,
        outcomes=outcomes,
        ma5_policy_shadow=tuple(
            _ma5_result_payload(item) for item in ma5_results
        ),
    )
    await session.commit()
    status_counts = Counter(item.status for item in outcomes)
    return {
        "job_name": LEADER_CONTINUATION_JOB_NAME,
        "status": "maturity_advanced",
        "observation_manifest_hash": observation.manifest_hash,
        "maturity_manifest_hash": maturity_manifest.manifest_hash,
        "matured_outcome_count": status_counts.get("matured", 0),
        "pending_outcome_count": status_counts.get("pending", 0),
        "unavailable_outcome_count": status_counts.get("unavailable", 0),
        "live_provider_calls": 0,
        "advanced_pages": 1,
        "single_worker": True,
        "research_only": True,
        "production_mutation_allowed": False,
    }


def _artifact_store_for_manifest(
    settings: Settings,
    manifest: LeaderContinuationManifest,
) -> ReplayArtifactStore:
    return ReplayArtifactStore(
        Path(settings.etf_leader_tactics_artifact_dir)
        / f"{manifest.manifest_hash}.sqlite3"
    )


def _read_all_phase_artifacts(
    store: ReplayArtifactStore,
    *,
    run_id: str,
    phase: str,
    deadline: float,
) -> list[dict[str, Any]]:
    after: str | None = None
    output: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        rows = store.read_research_artifact_page(
            run_id=run_id,
            phase=phase,
            after_item_key=after,
            max_rows=20,
            max_seconds=min(2.0, max(0.05, deadline - time.monotonic())),
        )
        if not rows:
            break
        output.extend(dict(row.payload) for row in rows)
        after = rows[-1].item_key
        if len(rows) < 20:
            break
    if time.monotonic() >= deadline:
        raise TimeoutError("leader local artifact read exceeded its bounded deadline")
    return output


def build_production_leader_observation_handlers(
    session: AsyncSession,
    *,
    source: EtfPitCaptureSource,
    store: ReplayArtifactStore,
    observation_manifest: LeaderObservationManifest,
) -> LeaderContinuationHandlers:
    """Compose source-bound handlers; no handler constructs a provider client."""

    async def features(
        _manifest: LeaderContinuationManifest,
        checkpoint: LeaderContinuationCheckpoint,
        page_size: int,
        _timeout_seconds: float,
    ) -> LeaderContinuationPage:
        max_codes = min(page_size, MAX_CODES_PER_REPLAY_INPUT_PAGE)
        code_after = str(checkpoint.phase_cursor.get("code_after") or "") or None
        snapshot = await load_point_in_time_ranking_inputs(
            session,
            replay_date=source.as_of_trade_date,
            decision_cutoff=observation_manifest.data_cutoff,
            max_source_rows=MAX_SOURCE_ROWS_PER_PAGE,
            code_after=code_after,
            max_codes=max_codes,
            required_history_sessions=REPAIR_HISTORY_SESSIONS,
        )
        facts = await load_leader_source_bound_facts(
            session,
            capture_source=source,
            asset_codes=snapshot.page_asset_codes,
        )
        adapted = adapt_leader_pit_inputs(
            capture_source=source,
            replay_snapshot=snapshot,
            taxonomy_facts=facts.taxonomy,
            sector_trend_facts=facts.sector_trends,
            baseline_score_facts=facts.baseline_scores,
            regime_fact=facts.regime,
        )
        inputs_by_code = {item.asset_code: item for item in adapted.inputs}
        replay_reasons: dict[str, list[str]] = {}
        for item in snapshot.exclusions:
            if item.asset_code in snapshot.page_asset_codes:
                replay_reasons.setdefault(str(item.asset_code), []).append(
                    item.reason.value
                )
        artifacts: list[LeaderPageArtifact] = []
        exclusions: Counter[str] = Counter()
        for code in snapshot.page_asset_codes:
            item = inputs_by_code.get(code)
            if item is None:
                reasons = tuple(
                    sorted(
                        set(
                            replay_reasons.get(code, ())
                            or facts.exclusions.get(code, ())
                            or ("decision_eligible_adjusted_history_unavailable",)
                        )
                    )
                )
                exclusions.update(reasons)
                payload = {
                    "kind": "exclusion",
                    "asset_code": code,
                    "reasons": list(reasons),
                    "source_id": source.id,
                }
            else:
                primitive = build_leader_observation_primitive(item)
                payload = {
                    "kind": "primitive",
                    "asset_code": code,
                    "primitive": primitive.to_dict(),
                    "source_facts_hash": facts.facts_hash,
                }
                exclusions.update(primitive.breakout_unavailable_reasons)
                exclusions.update(primitive.repair_unavailable_reasons)
            artifacts.append(LeaderPageArtifact(item_key=code, payload=payload))
        return LeaderContinuationPage(
            phase_complete=not snapshot.has_more,
            next_cursor={"code_after": snapshot.next_code_after},
            artifacts=tuple(artifacts),
            coverage={
                "observation_input": {
                    "expected": len(snapshot.authoritative_universe),
                    "available": len(snapshot.eligible_inputs),
                    "ratio": (
                        len(snapshot.eligible_inputs)
                        / max(1, len(snapshot.page_asset_codes))
                    ),
                }
            },
            exclusions=dict(exclusions),
        )

    async def outcomes(
        manifest: LeaderContinuationManifest,
        _checkpoint: LeaderContinuationCheckpoint,
        _page_size: int,
        timeout_seconds: float,
    ) -> LeaderContinuationPage:
        deadline = time.monotonic() + timeout_seconds
        payloads = _read_all_phase_artifacts(
            store,
            run_id=manifest.manifest_hash,
            phase="features",
            deadline=deadline,
        )
        primitives = tuple(
            LeaderObservationPrimitive.from_dict(dict(item["primitive"]))
            for item in payloads
            if item.get("kind") == "primitive"
        )
        if primitives:
            finalized = finalize_leader_observation_primitives(primitives)
        else:
            feature_hash, _count = store.research_phase_digest(
                run_id=manifest.manifest_hash,
                phase="features",
                max_seconds=min(2.0, max(0.05, deadline - time.monotonic())),
            )
            finalized = LeaderObservationFinalization(
                observation_hash=feature_hash,
                current_matches=(),
                available_observation_count=0,
                qualifying_observation_count=0,
                exclusion_counts={"no_decision_eligible_assets": len(payloads)},
                pending_outcomes=(),
                all_observation_hashes=(),
            )
        return LeaderContinuationPage(
            phase_complete=True,
            next_cursor={},
            artifacts=(
                LeaderPageArtifact(
                    item_key="session-observation",
                    payload={
                        "kind": "session_observation",
                        "observation_hash": finalized.observation_hash,
                        "current_matches": [
                            item.to_dict() for item in finalized.current_matches
                        ],
                        "available_observation_count": (
                            finalized.available_observation_count
                        ),
                        "qualifying_observation_count": (
                            finalized.qualifying_observation_count
                        ),
                        "exclusion_counts": finalized.exclusion_counts,
                        "pending_outcomes": list(finalized.pending_outcomes),
                        "input_asset_count": len(payloads),
                        "feature_hash_count": len(
                            finalized.all_observation_hashes
                        ),
                    },
                ),
            ),
        )

    async def no_op(
        _manifest: LeaderContinuationManifest,
        _checkpoint: LeaderContinuationCheckpoint,
        _page_size: int,
        _timeout_seconds: float,
    ) -> LeaderContinuationPage:
        return LeaderContinuationPage(phase_complete=True, next_cursor={})

    async def final_evidence(
        manifest: LeaderContinuationManifest,
        checkpoint: LeaderContinuationCheckpoint,
        _page_size: int,
        timeout_seconds: float,
    ) -> LeaderContinuationPage:
        rows = _read_all_phase_artifacts(
            store,
            run_id=manifest.manifest_hash,
            phase="outcomes",
            deadline=time.monotonic() + timeout_seconds,
        )
        summary = next(
            (item for item in rows if item.get("kind") == "session_observation"),
            None,
        )
        if summary is None:
            raise RuntimeError("sealed leader observation summary is missing")
        eligible_sessions = await _pit_session_count(session)
        materialized_sessions = int(
            await session.scalar(
                select(func.count())
                .select_from(EtfFactorExperimentEvidence)
                .where(
                    EtfFactorExperimentEvidence.experiment_family
                    == LEADER_OBSERVATION_EXPERIMENT_FAMILY
                )
            )
            or 0
        ) + 1
        matches = tuple(
            LeaderObservationMatch(
                candidate_id=str(item["candidate_id"]),
                asset_code=str(item["asset_code"]),
                asset_name=(
                    str(item["asset_name"])
                    if item.get("asset_name") is not None
                    else None
                ),
                score=float(item["score"]),
                rank=int(item["rank"]),
                matched_gates=tuple(item.get("matched_gates") or ()),
                feature_hash=str(item["feature_hash"]),
            )
            for item in summary.get("current_matches") or ()
        )
        progress = LeaderObservationProgress(
            source_id=int(source.id),
            signal_date=source.as_of_trade_date,
            state="complete",
            processed_asset_count=int(summary["input_asset_count"]),
            total_asset_count=int(summary["input_asset_count"]),
            page_size=min(
                checkpoint.page_profile.page_size,
                MAX_CODES_PER_REPLAY_INPUT_PAGE,
            ),
            current_phase="observation_complete",
            checkpoint_hash=checkpoint.checkpoint_hash,
        )
        report = LeaderObservationReport(
            manifest_hash=observation_manifest.manifest_hash,
            observation_hash=str(summary["observation_hash"]),
            signal_date=source.as_of_trade_date,
            data_cutoff=observation_manifest.data_cutoff,
            progress=progress,
            promotion_gates=LeaderPromotionGateProgress(
                eligible_pit_sessions=eligible_sessions,
                independent_primary_dates=0,
                completed_walk_forward_folds=0,
            ),
            current_matches=matches,
            exclusion_counts={
                str(key): int(value)
                for key, value in dict(
                    summary.get("exclusion_counts") or {}
                ).items()
            },
            available_observation_count=int(
                summary["available_observation_count"]
            ),
            qualifying_observation_count=int(
                summary["qualifying_observation_count"]
            ),
            current_observations_truncated=(
                int(summary["qualifying_observation_count"]) > len(matches)
            ),
            pending_outcome_count=len(
                list(summary.get("pending_outcomes") or ())
            ),
            matured_outcome_count=0,
        )
        await persist_leader_observation_evidence(
            session,
            manifest=observation_manifest,
            report=report,
            pending_outcomes=tuple(summary.get("pending_outcomes") or ()),
        )
        return LeaderContinuationPage(
            phase_complete=True,
            next_cursor={},
            artifacts=(
                LeaderPageArtifact(
                    item_key="observation-evidence",
                    payload={
                        "observation_manifest_hash": (
                            observation_manifest.manifest_hash
                        ),
                        "materialized_pit_sessions": materialized_sessions,
                        "research_only": True,
                        "production_mutation_allowed": False,
                    },
                ),
            ),
        )

    return LeaderContinuationHandlers(
        features=features,
        outcomes=outcomes,
        diagnostics=no_op,
        ma5_policy=no_op,
        final_evidence=final_evidence,
    )


def _unavailable(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "job_name": LEADER_CONTINUATION_JOB_NAME,
        "status": "insufficient_data" if details else "disabled",
        "unavailable_reason": reason,
        "live_provider_calls": 0,
        "advanced_pages": 0,
        "single_worker": True,
        "research_only": True,
        "production_mutation_allowed": False,
        **details,
    }


async def continue_etf_leader_tactics_shadow_job(
    session: AsyncSession,
    *,
    settings: Settings,
    manifest: LeaderContinuationManifest | None = None,
    handlers: LeaderContinuationHandlers | None = None,
    artifact_store: ReplayArtifactStore | None = None,
    timeout_seconds: float = 50.0,
) -> dict[str, Any]:
    """Advance one injected/registered page; discovery never calls a provider."""

    if not settings.etf_leader_tactics_continuation_enabled:
        return _unavailable(LEADER_CONTINUATION_DISABLED)
    if not settings.etf_leader_tactics_code_version.strip():
        return _unavailable(LEADER_CODE_VERSION_MISSING)
    if manifest is None or handlers is None:
        pit_sessions = await _pit_session_count(session)
        source, scan_exhausted = await _oldest_due_source(
            session,
            code_version=settings.etf_leader_tactics_code_version,
        )
        if source is None:
            maturity = await mature_oldest_leader_observation(
                session,
                code_version=settings.etf_leader_tactics_code_version,
            )
            if maturity is not None:
                return maturity
            await session.rollback()
            return _unavailable(
                (
                    LEADER_PIT_BACKLOG_SCAN_LIMIT
                    if scan_exhausted
                    else LEADER_PIT_SOURCE_UNAVAILABLE
                ),
                eligible_pit_sessions=pit_sessions,
                required_pit_sessions=MINIMUM_LEADER_PIT_SESSIONS,
            )
        manifest = build_leader_continuation_manifest_for_source(
            source,
            code_version=settings.etf_leader_tactics_code_version,
        )
        observation_manifest = build_leader_observation_manifest_for_source(
            source,
            code_version=settings.etf_leader_tactics_code_version,
        )
        artifact_store = artifact_store or _artifact_store_for_manifest(
            settings,
            manifest,
        )
        handlers = build_production_leader_observation_handlers(
            session,
            source=source,
            store=artifact_store,
            observation_manifest=observation_manifest,
        )

    if manifest.code_version != settings.etf_leader_tactics_code_version:
        return _unavailable(
            "leader_tactics_code_version_incompatible",
            manifest_code_version=manifest.code_version,
        )
    store = artifact_store or _artifact_store_for_manifest(settings, manifest)
    checkpoint = await run_bounded_leader_tactics_continuation(
        session,
        artifact_store=store,
        manifest=manifest,
        handlers=handlers,
        timeout_seconds=timeout_seconds,
    )
    return {
        "job_name": LEADER_CONTINUATION_JOB_NAME,
        "advanced_pages": 1,
        **leader_continuation_view(checkpoint),
    }
