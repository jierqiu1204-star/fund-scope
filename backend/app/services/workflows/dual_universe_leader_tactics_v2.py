"""Disabled-by-default V2 research coordinator and A-share readiness reads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    V2ContractError,
    V2ScreenResult,
    validate_runtime_contract,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    MAX_CONTINUATION_SECONDS,
    V2CollectorBatch,
    V2CollectorCheckpoint,
    run_bounded_batch,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_ingestion import (
    AshareAdjustedPriceFact,
    AshareThemeMembershipFact,
    AshareUniverseSnapshotFact,
    persist_ashare_adjusted_price_batch,
    persist_ashare_theme_membership_batch,
    persist_ashare_universe_snapshot_batch,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_lifecycle_storage import (
    acquire_v2_checkpoint_lease,
    acquire_v2_global_run_lease,
    release_v2_checkpoint_lease,
    release_v2_global_run_lease,
    save_v2_checkpoint,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    persist_v2_screen_result,
)

WORKFLOW_FINALIZATION_RESERVE_SECONDS = 2.0
ASHARE_V2_COVERAGE_THRESHOLD = 0.90


@dataclass(frozen=True)
class V2CheckpointContract:
    manifest_hash: str
    source_registry_hash: str
    formula_registry_hash: str
    adjustment_version: str
    taxonomy_version: str
    cost_model: tuple[tuple[str, float], ...]
    state_policy: str


@dataclass(frozen=True)
class AshareReadinessReport:
    as_of: datetime
    universe_count: int
    adjusted_daily_count: int
    pit_theme_count: int
    history_counts: tuple[tuple[int, int], ...]
    provider_health: tuple[tuple[str, int], ...]
    raw_decision_violations: int
    non_finite_violations: int
    exclusions: tuple[tuple[str, int], ...]
    coverage_threshold: float = ASHARE_V2_COVERAGE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        denominator = max(1, self.universe_count)

        def coverage(numerator: int) -> float:
            if not self.universe_count:
                return 0.0
            return min(1.0, max(0.0, numerator / denominator))

        history = {
            str(required): {
                "eligible_count": min(max(0, count), self.universe_count),
                "denominator": self.universe_count,
                "coverage": coverage(count),
                "threshold": self.coverage_threshold,
                "unavailable_reason": None
                if coverage(count) >= self.coverage_threshold
                else "insufficient_history",
            }
            for required, count in self.history_counts
        }
        return {
            "schema_version": "dual_universe_leader_tactics_readiness_v1",
            "as_of": self.as_of.isoformat(),
            "authoritative_universe": {
                "eligible_count": self.universe_count,
                "denominator": self.universe_count,
                "coverage": 1.0 if self.universe_count else 0.0,
                "threshold": self.coverage_threshold,
            },
            "adjusted_daily": {
                "eligible_count": min(max(0, self.adjusted_daily_count), self.universe_count),
                "denominator": self.universe_count,
                "coverage": coverage(self.adjusted_daily_count),
                "threshold": self.coverage_threshold,
            },
            "pit_theme": {
                "eligible_count": min(max(0, self.pit_theme_count), self.universe_count),
                "denominator": self.universe_count,
                "coverage": coverage(self.pit_theme_count),
                "threshold": self.coverage_threshold,
            },
            "history_tiers": history,
            "provider_health": dict(self.provider_health),
            "raw_decision_violations": self.raw_decision_violations,
            "non_finite_violations": self.non_finite_violations,
            "exclusions": dict(self.exclusions),
            "research_only": True,
            "unavailable_reason": None
            if self.universe_count
            else "ashare_universe_not_materialized",
        }


@dataclass(frozen=True)
class V2MaterializationReadiness:
    """Fail-closed decision for daily research materialization.

    The 300-session tier remains a diagnostic/promotion input.  Daily formula
    evaluation needs 120 sessions for breakout/base-launch and 180 sessions
    for former-leader repair, so those are the only history tiers admitted to
    this operational gate.
    """

    ready: bool
    reasons: tuple[str, ...]
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reasons": list(self.reasons),
            "threshold": self.threshold,
            "research_only": True,
        }


def evaluate_v2_materialization_readiness(
    report: AshareReadinessReport,
) -> V2MaterializationReadiness:
    """Require complete factual inputs before any A-share screen is written."""

    threshold = report.coverage_threshold
    denominator = report.universe_count
    reasons: list[str] = []

    def below_threshold(count: int) -> bool:
        return denominator <= 0 or count / denominator < threshold

    if denominator <= 0:
        reasons.append("ashare_universe_not_materialized")
    if below_threshold(report.adjusted_daily_count):
        reasons.append("insufficient_adjusted_daily_coverage")
    if below_threshold(report.pit_theme_count):
        reasons.append("insufficient_pit_theme_coverage")

    history_by_tier = dict(report.history_counts)
    for required in (120, 180):
        count = history_by_tier.get(required)
        if count is None or below_threshold(count):
            reasons.append(f"insufficient_history_{required}")

    provider_counts = dict(report.provider_health)
    if not provider_counts or sum(max(0, count) for count in provider_counts.values()) <= 0:
        reasons.append("provider_health_unavailable")
    if any(provider not in {"akshare", "eastmoney", "tickflow"} for provider in provider_counts):
        reasons.append("unsupported_decision_provider")
    if report.raw_decision_violations:
        reasons.append("raw_decision_price_violation")
    if report.non_finite_violations:
        reasons.append("non_finite_adjusted_input")

    normalized = tuple(dict.fromkeys(reasons))
    return V2MaterializationReadiness(
        ready=not normalized,
        reasons=normalized,
        threshold=threshold,
    )


def bind_v2_checkpoint_manifest(
    checkpoint: V2CollectorCheckpoint,
    *,
    manifest_hash: str,
) -> V2CollectorCheckpoint:
    """Bind a checkpoint once; changing manifest identity fails closed."""

    if not manifest_hash.strip():
        raise ValueError("manifest_hash is required")
    if checkpoint.manifest_hash not in (None, manifest_hash):
        raise ValueError("V2 checkpoint manifest identity is incompatible")
    return replace(checkpoint, manifest_hash=manifest_hash)


def validate_v2_checkpoint_contract(
    *,
    expected: V2CheckpointContract,
    checkpoint: V2CheckpointContract,
) -> None:
    if expected != checkpoint:
        raise ValueError("V2 checkpoint contract is incompatible; start a new manifest")


@dataclass(frozen=True, slots=True)
class V2CapturedAshareFacts:
    """One immutable, point-in-time A-share capture unit."""

    asset_code: str
    universe_fact: AshareUniverseSnapshotFact
    theme_facts: tuple[AshareThemeMembershipFact, ...] = ()
    adjusted_price_facts: tuple[AshareAdjustedPriceFact, ...] = ()
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.asset_code, str) or not self.asset_code.strip():
            raise V2ContractError("captured A-share bundle asset_code is required")
        asset_code = self.asset_code.strip()
        if not isinstance(self.universe_fact, AshareUniverseSnapshotFact):
            raise V2ContractError("captured A-share bundle requires one universe fact")
        theme_facts = tuple(self.theme_facts)
        adjusted_price_facts = tuple(self.adjusted_price_facts)
        all_facts = (self.universe_fact, *theme_facts, *adjusted_price_facts)
        if any(not isinstance(fact, AshareThemeMembershipFact) for fact in theme_facts):
            raise V2ContractError("captured A-share bundle contains an invalid theme fact")
        if any(not isinstance(fact, AshareAdjustedPriceFact) for fact in adjusted_price_facts):
            raise V2ContractError("captured A-share bundle contains an invalid adjusted price fact")
        if any(fact.asset_code != asset_code for fact in all_facts):
            raise V2ContractError("captured A-share bundle facts must share asset_code")
        object.__setattr__(self, "asset_code", asset_code)
        object.__setattr__(self, "theme_facts", theme_facts)
        object.__setattr__(self, "adjusted_price_facts", adjusted_price_facts)
        object.__setattr__(self, "content_hash", stable_contract_hash(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "dual_universe_leader_tactics_v2_captured_ashare_facts_v1",
            "asset_code": self.asset_code,
            "universe_fact_hash": self.universe_fact.fact_hash,
            "theme_fact_hashes": sorted(fact.fact_hash for fact in self.theme_facts),
            "adjusted_price_fact_hashes": sorted(
                fact.fact_hash for fact in self.adjusted_price_facts
            ),
        }


async def run_v2_fact_capture_batch(
    session: AsyncSession,
    *,
    manifest_hash: str,
    codes: tuple[str, ...],
    checkpoint: V2CollectorCheckpoint,
    fetch_one: Callable[[str], Awaitable[V2CapturedAshareFacts]],
    lease_owner: str,
    budget_seconds: float = MAX_CONTINUATION_SECONDS,
    expected_contract: V2CheckpointContract | None = None,
    checkpoint_contract: V2CheckpointContract | None = None,
) -> V2CollectorBatch:
    """Capture one bundle per security and commit facts with its checkpoint.

    The savepoint is per security: a failed theme/price write cannot leave a
    partial bundle, while successful securities in the same bounded page remain
    resumable through the normal collector checkpoint. Provider results are
    held only until the collector has accepted them within its wall-clock
    budget; a late blocking callback therefore cannot write successful facts.
    """

    bundles: dict[str, V2CapturedAshareFacts] = {}

    async def fetch_bundle(code: str) -> str:
        bundle = await fetch_one(code)
        if not isinstance(bundle, V2CapturedAshareFacts):
            raise V2ContractError("fact capture callback must return V2CapturedAshareFacts")
        if bundle.asset_code != code:
            raise V2ContractError("fact capture bundle asset_code does not match requested code")
        bundles[code] = bundle
        return bundle.content_hash

    async def persist_completed(code: str) -> None:
        bundle = bundles.pop(code, None)
        if bundle is None:
            raise V2ContractError("accepted fact capture has no buffered bundle")
        async with session.begin_nested():
            await persist_ashare_universe_snapshot_batch(session, (bundle.universe_fact,))
            await persist_ashare_theme_membership_batch(session, bundle.theme_facts)
            price_facts = bundle.adjusted_price_facts
            if price_facts:
                earliest = min(fact.trade_date for fact in price_facts)
                existing = {
                    str(value)
                    for value in (
                        await session.scalars(
                            text(
                                """
                                SELECT revision_id
                                FROM ashare_adjusted_price_facts
                                WHERE asset_code = :asset_code
                                  AND trade_date >= :earliest
                                """
                            ),
                            {"asset_code": code, "earliest": earliest},
                        )
                    ).all()
                }
                price_facts = tuple(
                    fact for fact in price_facts if fact.revision_id not in existing
                )
            await persist_ashare_adjusted_price_batch(session, price_facts)

    return await run_v2_capture_batch(
        session,
        manifest_hash=manifest_hash,
        codes=codes,
        checkpoint=checkpoint,
        fetch_one=fetch_bundle,
        lease_owner=lease_owner,
        budget_seconds=budget_seconds,
        persist_completed=persist_completed,
        expected_contract=expected_contract,
        checkpoint_contract=checkpoint_contract,
    )


async def run_v2_capture_batch(
    session: AsyncSession,
    *,
    manifest_hash: str,
    codes: tuple[str, ...],
    checkpoint: V2CollectorCheckpoint,
    fetch_one: Any,
    lease_owner: str,
    budget_seconds: float = MAX_CONTINUATION_SECONDS,
    persist_completed: Callable[[str], Awaitable[None]] | None = None,
    expected_contract: V2CheckpointContract | None = None,
    checkpoint_contract: V2CheckpointContract | None = None,
) -> V2CollectorBatch:
    """Run one capture page under a durable lease and validated resume contract."""

    validate_runtime_contract()
    if expected_contract is not None or checkpoint_contract is not None:
        if expected_contract is None or checkpoint_contract is None:
            raise ValueError("expected and checkpoint contracts must be supplied together")
        validate_v2_checkpoint_contract(
            expected=expected_contract,
            checkpoint=checkpoint_contract,
        )
        if expected_contract.manifest_hash != manifest_hash:
            raise ValueError("checkpoint contract manifest identity is incompatible")
    elif checkpoint.manifest_hash is not None:
        raise ValueError("checkpoint contract is required when resuming a bound checkpoint")
    if budget_seconds <= 0 or budget_seconds > MAX_CONTINUATION_SECONDS:
        raise ValueError("collector budget must be in (0, 55] seconds")
    if not isinstance(lease_owner, str) or not lease_owner.strip():
        raise ValueError("lease_owner is required")
    # A caller-supplied label is not a process identity. Add a per-run token
    # so two workers configured with the same label cannot renew each other's
    # database lease.
    effective_lease_owner = f"{lease_owner.strip()[:64]}:{uuid4().hex}"
    checkpoint = bind_v2_checkpoint_manifest(checkpoint, manifest_hash=manifest_hash)
    global_lease_expires_at = await acquire_v2_global_run_lease(
        session,
        lease_owner=effective_lease_owner,
        lease_seconds=min(MAX_CONTINUATION_SECONDS + 10.0, budget_seconds + 5.0),
    )
    if global_lease_expires_at is None:
        return V2CollectorBatch(
            completed=(),
            failed=(),
            checkpoint=checkpoint,
            elapsed_seconds=0.0,
            stopped_reason="database_lease_busy",
        )
    try:
        lease_expires_at = await acquire_v2_checkpoint_lease(
            session,
            manifest_hash=manifest_hash,
            lease_owner=effective_lease_owner,
            lease_seconds=min(MAX_CONTINUATION_SECONDS + 10.0, budget_seconds + 5.0),
        )
    except BaseException:
        await release_v2_global_run_lease(session, lease_owner=effective_lease_owner)
        raise
    if lease_expires_at is None:
        await release_v2_global_run_lease(session, lease_owner=effective_lease_owner)
        return V2CollectorBatch(
            completed=(),
            failed=(),
            checkpoint=checkpoint,
            elapsed_seconds=0.0,
            stopped_reason="database_lease_busy",
        )
    checkpoint_saved = False
    try:
        # Lease acquisition commits by design. Start an explicit outer
        # transaction before per-security savepoints so releasing a savepoint
        # cannot become an implicit top-level commit on SQLite. SQLite's
        # deferred transaction needs an explicit BEGIN before SAVEPOINT.
        await session.begin()
        if session.sync_session.get_bind().dialect.name == "sqlite":
            await session.execute(text("BEGIN"))
        # Leave a small bounded window for AsyncSession checkpoint/fact writes
        # and lease release. This is a return-time guard, not a promise that
        # can preempt a callback which blocks the event loop.
        collector_budget_seconds = max(0.01, budget_seconds - WORKFLOW_FINALIZATION_RESERVE_SECONDS)
        result = await run_bounded_batch(
            codes,
            checkpoint=checkpoint,
            fetch_one=fetch_one,
            budget_seconds=collector_budget_seconds,
        )
        if persist_completed is not None:
            completed_before = set(checkpoint.completed_codes)
            newly_completed = (code for code in result.completed if code not in completed_before)
            for code in newly_completed:
                await persist_completed(code)
        await save_v2_checkpoint(
            session,
            manifest_hash=manifest_hash,
            checkpoint=result.checkpoint,
            lease_owner=effective_lease_owner,
            lease_expires_at=lease_expires_at,
        )
        checkpoint_saved = True
        return result
    except BaseException:
        # A cancellation or other non-Exception must not let lease release
        # commit facts whose checkpoint was never durably saved.
        if not checkpoint_saved:
            await session.rollback()
        raise
    finally:
        await release_v2_checkpoint_lease(
            session,
            manifest_hash=manifest_hash,
            lease_owner=effective_lease_owner,
        )
        await release_v2_global_run_lease(session, lease_owner=effective_lease_owner)


async def materialize_v2_result(
    session: AsyncSession,
    result: V2ScreenResult,
    *,
    enabled: bool,
    code_version: str | None = None,
) -> str | None:
    """Persist a pure screen result only when explicit research materialization is enabled."""

    if not enabled:
        return None
    if code_version is None:
        return await persist_v2_screen_result(session, result)
    return await persist_v2_screen_result(session, result, code_version=code_version)


_AUTHORITATIVE_UNIVERSE_CTE = """
WITH latest_universe AS (
    SELECT snapshots.*,
           ROW_NUMBER() OVER (
               PARTITION BY asset_code
               ORDER BY snapshot_date DESC, effective_at DESC,
                        received_at DESC, fact_hash DESC
           ) AS snapshot_rank
    FROM ashare_research_universe_snapshots AS snapshots
    WHERE snapshot_date <= :as_of_date
      AND effective_at <= :as_of
      AND received_at <= :as_of
      AND source_cutoff <= :as_of
),
authoritative_universe AS (
    SELECT asset_code
    FROM latest_universe
    WHERE snapshot_rank = 1
      AND LOWER(listing_state) = 'listed'
      AND exclusion_reason IS NULL
)
"""


def _readiness_params(
    *,
    as_of: datetime,
    required_trade_date: date | None = None,
) -> dict[str, Any]:
    return {
        "as_of": as_of,
        "as_of_date": required_trade_date or as_of.date(),
        "required_trade_date": required_trade_date,
        "eligible": True,
        "historical_only": False,
        "price_basis": "total_return_adjusted",
        "finite_max": 1.7976931348623157e308,
    }


_QUALIFIED_ADJUSTED_FACT_PREDICATE = """
        facts.received_at IS NOT NULL
        AND facts.received_at <= :as_of
        AND facts.trade_date <= :as_of_date
        AND DATE(facts.received_at) >= facts.trade_date
        AND facts.decision_eligible = :eligible
        AND facts.historical_research_only = :historical_only
        AND facts.price_basis = :price_basis
        AND LOWER(facts.provider) IN ('akshare', 'eastmoney')
        AND facts.adjustment_version IS NOT NULL
        AND TRIM(facts.adjustment_version) <> ''
        AND facts.adjusted_open > 0
        AND facts.adjusted_open < :finite_max
        AND facts.adjusted_high > 0
        AND facts.adjusted_high < :finite_max
        AND facts.adjusted_low > 0
        AND facts.adjusted_low < :finite_max
        AND facts.adjusted_close > 0
        AND facts.adjusted_close < :finite_max
        AND facts.adjusted_high >= facts.adjusted_open
        AND facts.adjusted_high >= facts.adjusted_close
        AND facts.adjusted_low <= facts.adjusted_open
        AND facts.adjusted_low <= facts.adjusted_close
        AND facts.adjusted_low <= facts.adjusted_high
        AND facts.volume >= 0
        AND facts.volume < :finite_max
        AND facts.amount >= 0
        AND facts.amount < :finite_max
        AND facts.turnover >= 0
        AND facts.turnover < :finite_max
"""


async def read_ashare_readiness(
    session: AsyncSession,
    *,
    as_of: datetime,
    required_history_tiers: tuple[int, ...] = (61, 120, 180, 300),
    required_trade_date: date | None = None,
) -> AshareReadinessReport:
    """Return bounded readiness metrics from the as-of authoritative PIT pool."""

    params = _readiness_params(
        as_of=as_of,
        required_trade_date=required_trade_date,
    )
    universe_row = (
        (
            await session.execute(
                text(
                    _AUTHORITATIVE_UNIVERSE_CTE
                    + """
                SELECT COUNT(*) AS count FROM authoritative_universe
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    universe_count = int(universe_row["count"] or 0)

    history_tier_sql = ",\n".join(
        (
            "            COALESCE(SUM(CASE WHEN sessions_count >= "
            f":history_tier_{index} AND (:required_trade_date IS NULL OR "
            ":required_trade_date = latest_trade_date) THEN 1 ELSE 0 END), 0) "
            f"AS tier_{index}"
        )
        for index, _ in enumerate(required_history_tiers)
    )
    history_tier_params = {
        f"history_tier_{index}": required for index, required in enumerate(required_history_tiers)
    }
    summary_tier_projection = "".join(
        f",\n               summary.tier_{index} AS tier_{index}"
        for index, _ in enumerate(required_history_tiers)
    )
    provider_tier_projection = "".join(
        f",\n               NULL AS tier_{index}" for index, _ in enumerate(required_history_tiers)
    )
    summary_tier_cte = f",\n{history_tier_sql}" if history_tier_sql else ""
    summary_result = await session.execute(
        text(
            _AUTHORITATIVE_UNIVERSE_CTE
            + f"""
        ,qualified_adjusted AS (
            SELECT DISTINCT facts.asset_code, facts.trade_date, facts.provider
            FROM ashare_adjusted_price_facts AS facts
            JOIN authoritative_universe AS universe
              ON universe.asset_code = facts.asset_code
            WHERE
            {_QUALIFIED_ADJUSTED_FACT_PREDICATE}
        ),
        per_asset AS (
            SELECT asset_code, COUNT(DISTINCT trade_date) AS sessions_count,
                   MAX(trade_date) AS latest_trade_date
            FROM qualified_adjusted
            GROUP BY asset_code
        ),
        summary AS (
            SELECT COALESCE(SUM(CASE
                       WHEN :required_trade_date IS NULL
                            OR latest_trade_date = :required_trade_date
                       THEN 1 ELSE 0 END), 0) AS adjusted_count{summary_tier_cte}
            FROM per_asset
        ),
        provider_health AS (
            SELECT provider, COUNT(*) AS provider_count
            FROM qualified_adjusted
            GROUP BY provider
        )
        SELECT 'summary' AS row_kind, NULL AS provider,
               summary.adjusted_count{summary_tier_projection},
               NULL AS provider_count
        FROM summary
        UNION ALL
        SELECT 'provider' AS row_kind, provider, NULL AS adjusted_count{provider_tier_projection},
               provider_count
        FROM provider_health
        """
        ),
        {**params, **history_tier_params},
    )
    summary_rows = summary_result.mappings().all()
    summary_row = next(row for row in summary_rows if row["row_kind"] == "summary")
    adjusted_row = {"count": int(summary_row["adjusted_count"] or 0)}
    tiers = [
        (required, int(summary_row[f"tier_{index}"] or 0))
        for index, required in enumerate(required_history_tiers)
    ]
    provider_rows = sorted(
        (row for row in summary_rows if row["row_kind"] == "provider"),
        key=lambda row: str(row["provider"]),
    )
    theme_row = (
        (
            await session.execute(
                text(
                    _AUTHORITATIVE_UNIVERSE_CTE
                    + """
                SELECT COUNT(*) AS count
                FROM (
                    SELECT DISTINCT memberships.asset_code
                    FROM ashare_theme_membership_facts AS memberships
                    JOIN authoritative_universe AS universe
                      ON universe.asset_code = memberships.asset_code
                    WHERE memberships.received_at <= :as_of
                      AND memberships.effective_from <= :as_of_date
                      AND (
                          memberships.effective_to IS NULL
                          OR memberships.effective_to >= :as_of_date
                      )
                ) valid_memberships
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    raw_row = (
        (
            await session.execute(
                text(
                    _AUTHORITATIVE_UNIVERSE_CTE
                    + """
                SELECT COUNT(*) AS count
                FROM ashare_adjusted_price_facts AS facts
                JOIN authoritative_universe AS universe
                  ON universe.asset_code = facts.asset_code
                WHERE facts.received_at IS NOT NULL
                  AND facts.received_at <= :as_of
                  AND facts.trade_date <= :as_of_date
                  AND (
                      LOWER(COALESCE(facts.provider, '')) NOT IN ('akshare', 'eastmoney')
                      OR facts.price_basis IS NULL
                      OR facts.price_basis <> :price_basis
                      OR facts.adjustment_version IS NULL
                      OR TRIM(facts.adjustment_version) = ''
                      OR facts.decision_eligible IS NULL
                      OR facts.decision_eligible <> :eligible
                      OR facts.historical_research_only IS NULL
                      OR facts.historical_research_only <> :historical_only
                  )
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    non_finite_row = (
        (
            await session.execute(
                text(
                    _AUTHORITATIVE_UNIVERSE_CTE
                    + """
                SELECT COUNT(*) AS count
                FROM ashare_adjusted_price_facts AS facts
                JOIN authoritative_universe AS universe
                  ON universe.asset_code = facts.asset_code
                WHERE facts.received_at IS NOT NULL
                  AND facts.received_at <= :as_of
                  AND facts.trade_date <= :as_of_date
                  AND (
                      facts.adjusted_open IS NULL OR facts.adjusted_high IS NULL
                      OR facts.adjusted_low IS NULL OR facts.adjusted_close IS NULL
                      OR facts.volume IS NULL OR facts.amount IS NULL OR facts.turnover IS NULL
                      OR facts.adjusted_open <= 0 OR facts.adjusted_open >= :finite_max
                      OR facts.adjusted_high <= 0 OR facts.adjusted_high >= :finite_max
                      OR facts.adjusted_low <= 0 OR facts.adjusted_low >= :finite_max
                      OR facts.adjusted_close <= 0 OR facts.adjusted_close >= :finite_max
                      OR facts.adjusted_high < facts.adjusted_open
                      OR facts.adjusted_high < facts.adjusted_close
                      OR facts.adjusted_low > facts.adjusted_open
                      OR facts.adjusted_low > facts.adjusted_close
                      OR facts.adjusted_low > facts.adjusted_high
                      OR facts.volume < 0 OR facts.volume >= :finite_max
                      OR facts.amount < 0 OR facts.amount >= :finite_max
                      OR facts.turnover < 0 OR facts.turnover >= :finite_max
                      OR DATE(facts.received_at) < facts.trade_date
                  )
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    exclusion_rows = (
        (
            await session.execute(
                text(
                    """
                WITH latest_universe AS (
                    SELECT snapshots.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code
                               ORDER BY snapshot_date DESC, effective_at DESC,
                                        received_at DESC, fact_hash DESC
                           ) AS snapshot_rank
                    FROM ashare_research_universe_snapshots AS snapshots
                    WHERE snapshot_date <= :as_of_date
                      AND effective_at <= :as_of
                      AND received_at <= :as_of
                      AND source_cutoff <= :as_of
                )
                SELECT COALESCE(exclusion_reason, 'none') AS reason, COUNT(*) AS count
                FROM latest_universe
                WHERE snapshot_rank = 1
                GROUP BY COALESCE(exclusion_reason, 'none')
                ORDER BY reason
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    def bounded_count(value: int) -> int:
        return min(max(0, value), universe_count)

    return AshareReadinessReport(
        as_of=as_of,
        universe_count=universe_count,
        adjusted_daily_count=bounded_count(int(adjusted_row["count"] or 0)),
        pit_theme_count=bounded_count(int(theme_row["count"] or 0)),
        history_counts=tuple((required, bounded_count(count)) for required, count in tiers),
        provider_health=tuple(
            (str(row["provider"]), int(row["provider_count"] or 0)) for row in provider_rows
        ),
        raw_decision_violations=int(raw_row["count"] or 0),
        non_finite_violations=int(non_finite_row["count"] or 0),
        exclusions=tuple((str(row["reason"]), int(row["count"] or 0)) for row in exclusion_rows),
    )


async def read_ashare_authoritative_assets(
    session: AsyncSession,
    *,
    signal_date: date,
    as_of: datetime,
    limit: int = 6_000,
) -> tuple[tuple[str, str], ...]:
    """Read one bounded, deterministic A-share universe for screening.

    Both the snapshot date and factual visibility cutoff are enforced so a
    later universe cannot leak into an earlier signal.
    """

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 6_000:
        raise ValueError("A-share authoritative asset limit must be between 1 and 6000")
    result = await session.execute(
        text(
            """
            SELECT asset_code, asset_name
            FROM (
                SELECT snapshots.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY asset_code
                           ORDER BY snapshot_date DESC, effective_at DESC,
                                    received_at DESC, fact_hash DESC
                       ) AS snapshot_rank
                FROM ashare_research_universe_snapshots AS snapshots
                WHERE snapshot_date <= :signal_date
                  AND effective_at <= :as_of
                  AND received_at <= :as_of
                  AND source_cutoff <= :as_of
            ) latest_universe
            WHERE snapshot_rank = 1
              AND LOWER(listing_state) = 'listed'
              AND exclusion_reason IS NULL
            ORDER BY asset_code
            LIMIT :limit
            """
        ),
        {"signal_date": signal_date, "as_of": as_of, "limit": limit + 1},
    )
    rows = tuple((str(row["asset_code"]), str(row["asset_name"])) for row in result.mappings())
    if len(rows) > limit:
        raise V2ContractError("A-share authoritative universe exceeds the bounded limit")
    return rows


__all__ = [
    "AshareReadinessReport",
    "V2MaterializationReadiness",
    "V2CapturedAshareFacts",
    "V2CheckpointContract",
    "bind_v2_checkpoint_manifest",
    "evaluate_v2_materialization_readiness",
    "materialize_v2_result",
    "read_ashare_authoritative_assets",
    "read_ashare_readiness",
    "run_v2_capture_batch",
    "run_v2_fact_capture_batch",
    "validate_v2_checkpoint_contract",
]
