from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    authorize_snapshot_publication,
    utcnow,
)
from app.services.market_data import etf_adjusted_price_provenance_issue
from app.services.short_research.coverage_policy import (
    evaluate_etf_readiness,
)
from app.services.short_research.ranking_contract import canonical_hash
from app.services.short_research.universe import build_point_in_time_universe_snapshot


class SnapshotPublicationError(ValueError):
    pass


@dataclass(frozen=True)
class EtfCoverageBarrier:
    expected_codes: list[str]
    included_codes: list[str]
    excluded: list[dict[str, str]]

    @property
    def coverage_ratio(self) -> float:
        return len(self.included_codes) / len(self.expected_codes) if self.expected_codes else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "expected_count": len(self.expected_codes),
            "included_count": len(self.included_codes),
            "coverage_ratio": round(self.coverage_ratio, 6),
        }


_REQUIRED_IDENTITY_FIELDS = (
    "scope_kind",
    "scope_hash",
    "universe_snapshot_hash",
    "input_snapshot_hash",
    "score_version",
    "rule_version",
    "ranking_contract_hash",
    "score_field",
    "data_cutoff",
    "as_of_trade_date",
    "price_basis",
    "expected_item_count",
    "decision_data_item_count",
    "decision_data_coverage_ratio",
    "eligible_item_count",
    "coverage_ratio",
    "idempotency_key",
)

_DRAFT_SEAL_VERSION = "etf-ranking-draft-v1"


def _summary_without_draft_seal(summary: Mapping[str, object] | None) -> dict[str, object]:
    return {str(key): value for key, value in (summary or {}).items() if key != "draft_seal"}


def _snapshot_item_content(item: ShortResearchSignalItem) -> dict[str, object]:
    return {
        "asset_type": item.asset_type,
        "asset_code": item.asset_code,
        "rank": item.rank,
        "global_rank": item.global_rank,
        "total_score": item.total_score,
        "ranking_score": item.ranking_score,
        "score_eligible": item.score_eligible,
        "conclusion": item.conclusion,
        "score_breakdown": item.score_breakdown_json,
        "risk_flags": item.risk_flags_json,
        "rationale": item.rationale_json,
        "metrics": item.metrics_json,
    }


def build_snapshot_draft_seal(
    run: ShortResearchSignalRun,
    items: Sequence[ShortResearchSignalItem],
) -> dict[str, object]:
    ordered_items = sorted(
        items,
        key=lambda item: (
            item.global_rank if item.global_rank is not None else math.inf,
            item.asset_code,
            item.asset_type,
        ),
    )
    item_content_hashes = [
        {
            "asset_type": item.asset_type,
            "asset_code": item.asset_code,
            "global_rank": item.global_rank,
            "content_hash": canonical_hash(_snapshot_item_content(item)),
        }
        for item in ordered_items
    ]
    run_content = {
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "as_of_date": run.as_of_date,
        "config": run.config_json,
        "error_message": run.error_message,
        **{field: getattr(run, field) for field in _REQUIRED_IDENTITY_FIELDS},
        "summary": _summary_without_draft_seal(run.summary_json),
    }
    run_content_hash = canonical_hash(run_content)
    items_content_hash = canonical_hash(item_content_hashes)
    return {
        "version": _DRAFT_SEAL_VERSION,
        "algorithm": "sha256-canonical-json",
        "item_count": len(ordered_items),
        "run_content_hash": run_content_hash,
        "item_content_hashes": item_content_hashes,
        "items_content_hash": items_content_hash,
        "snapshot_content_hash": canonical_hash(
            {
                "version": _DRAFT_SEAL_VERSION,
                "run_content_hash": run_content_hash,
                "items_content_hash": items_content_hash,
            }
        ),
    }


def _verify_snapshot_draft_seal(
    run: ShortResearchSignalRun,
    items: Sequence[ShortResearchSignalItem],
) -> None:
    stored = (run.summary_json or {}).get("draft_seal")
    if not isinstance(stored, Mapping):
        raise SnapshotPublicationError("draft content seal is unavailable")
    if dict(stored) != build_snapshot_draft_seal(run, items):
        raise SnapshotPublicationError("draft content seal mismatch")


async def build_etf_coverage_barrier(
    session: AsyncSession,
    *,
    as_of_trade_date: date | None,
    data_cutoff: datetime,
) -> EtfCoverageBarrier:
    if as_of_trade_date is None:
        raise SnapshotPublicationError("snapshot trade date is required")
    universe = await build_point_in_time_universe_snapshot(session, as_of_date=as_of_trade_date)
    expected_codes = [str(member["asset_code"]) for member in universe.members]
    if not expected_codes:
        return EtfCoverageBarrier(expected_codes=[], included_codes=[], excluded=[])
    rows = (
        await session.scalars(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code.in_(expected_codes),
                EtfPriceHistory.trade_date == as_of_trade_date,
            )
        )
    ).all()
    by_code = {row.etf_code: row for row in rows}
    included_codes: list[str] = []
    excluded: list[dict[str, str]] = []
    for code in expected_codes:
        row = by_code.get(code)
        reason: str | None
        if row is None:
            reason = "missing_trade_date_price"
        elif row.decision_eligible is not True:
            reason = row.decision_ineligibility_reason or "decision_ineligible_price"
        else:
            reason = etf_adjusted_price_provenance_issue(
                adjusted_value=row.research_adjusted_value,
                price_basis=row.research_price_basis,
                data_provider=row.data_provider,
                provider_version=row.provider_version,
                source_timestamp=row.source_timestamp,
                adjustment_version=row.adjustment_version,
                data_cutoff=data_cutoff,
            )
            if reason is None:
                included_codes.append(code)
                continue
        excluded.append({"asset_code": code, "reason": reason})
    return EtfCoverageBarrier(expected_codes=expected_codes, included_codes=included_codes, excluded=excluded)


def _validate_publishable(
    run: ShortResearchSignalRun,
    items: Sequence[ShortResearchSignalItem],
    *,
    decision_data_codes: set[str],
) -> None:
    if run.status != "success":
        raise SnapshotPublicationError("snapshot status must be success")
    if run.scope_kind != "full":
        raise SnapshotPublicationError("only full scope snapshots can publish")
    missing = [field for field in _REQUIRED_IDENTITY_FIELDS if getattr(run, field) is None]
    if missing:
        raise SnapshotPublicationError(f"missing snapshot identity: {', '.join(missing)}")
    if run.as_of_date != run.as_of_trade_date:
        raise SnapshotPublicationError("snapshot as-of date must match trade date")
    readiness = evaluate_etf_readiness(
        daily_coverage_ratio=run.decision_data_coverage_ratio,
        warmup_coverage_ratio=run.coverage_ratio,
    )
    if not readiness.complete_publication_allowed:
        if "daily_freshness_coverage_below_95pct" in readiness.blocker_reasons:
            raise SnapshotPublicationError(
                "decision-data coverage is below publication threshold"
            )
        raise SnapshotPublicationError(
            "score coverage is below complete publication threshold"
        )
    if run.expected_item_count is None or run.expected_item_count <= 0:
        raise SnapshotPublicationError("expected item count must be positive")
    expected_score_ratio = len(items) / run.expected_item_count
    if not math.isclose(run.coverage_ratio, expected_score_ratio, abs_tol=1e-6):
        raise SnapshotPublicationError("score coverage ratio does not match persisted items")
    if len(items) != run.eligible_item_count:
        raise SnapshotPublicationError("item count does not match eligible item count")
    if (run.summary_json or {}).get("item_count") != len(items):
        raise SnapshotPublicationError("summary item count does not match persisted items")
    coverage = (run.summary_json or {}).get("coverage")
    score_coverage = coverage.get("score") if isinstance(coverage, Mapping) else None
    eligible_codes = [item.asset_code for item in items]
    if (
        not isinstance(score_coverage, Mapping)
        or score_coverage.get("expected_count") != run.expected_item_count
        or score_coverage.get("eligible_count") != len(items)
        or score_coverage.get("eligible_codes") != eligible_codes
        or score_coverage.get("coverage_ratio") != round(run.coverage_ratio, 6)
    ):
        raise SnapshotPublicationError("score coverage summary does not match persisted items")
    if any(item.asset_type != "etf" for item in items):
        raise SnapshotPublicationError("full ETF snapshot contains a non-ETF item")
    if not {item.asset_code for item in items}.issubset(decision_data_codes):
        raise SnapshotPublicationError("score-eligible codes are not a subset of decision-data coverage")
    if [item.global_rank for item in items] != list(range(1, len(items) + 1)):
        raise SnapshotPublicationError("global ranks are not continuous")
    if any(
        item.score_eligible is not True
        or item.ranking_score is None
        or not math.isfinite(item.ranking_score)
        for item in items
    ):
        raise SnapshotPublicationError("snapshot contains an ineligible or non-finite ranking score")


def _materialized_coverage_code_sets(run: ShortResearchSignalRun) -> tuple[set[str], set[str]]:
    coverage = (run.summary_json or {}).get("coverage")
    decision_data = coverage.get("decision_data") if isinstance(coverage, Mapping) else None
    if not isinstance(decision_data, Mapping):
        raise SnapshotPublicationError("materialized coverage code sets are unavailable")
    expected_codes = decision_data.get("expected_codes")
    included_codes = decision_data.get("included_codes")
    if (
        not isinstance(expected_codes, list)
        or not isinstance(included_codes, list)
        or any(not isinstance(code, str) for code in [*expected_codes, *included_codes])
        or len(set(expected_codes)) != len(expected_codes)
        or len(set(included_codes)) != len(included_codes)
    ):
        raise SnapshotPublicationError("materialized coverage code sets are unavailable")
    return set(expected_codes), set(included_codes)


async def publish_full_snapshot(session: AsyncSession, *, run_id: int) -> ShortResearchSignalRun:
    async with session.begin():
        run = await session.scalar(
            select(ShortResearchSignalRun).where(ShortResearchSignalRun.id == run_id).with_for_update()
        )
        if run is None:
            raise SnapshotPublicationError("snapshot run does not exist")
        if run.publication_state == "published":
            return run
        if run.data_cutoff is None:
            raise SnapshotPublicationError("snapshot data cutoff is required")
        barrier = await build_etf_coverage_barrier(
            session,
            as_of_trade_date=run.as_of_trade_date,
            data_cutoff=run.data_cutoff,
        )
        if not barrier.expected_codes:
            raise SnapshotPublicationError("expected universe coverage is unavailable")
        materialized_universe_codes, materialized_decision_codes = _materialized_coverage_code_sets(run)
        if materialized_universe_codes != set(barrier.expected_codes):
            raise SnapshotPublicationError("universe code set changed after materialization")
        if materialized_decision_codes != set(barrier.included_codes):
            raise SnapshotPublicationError("decision-data code set changed after materialization")
        if run.expected_item_count != len(barrier.expected_codes):
            raise SnapshotPublicationError("expected item count does not match point-in-time universe")
        if run.decision_data_item_count != len(barrier.included_codes):
            raise SnapshotPublicationError("decision-data item count does not match coverage barrier")
        if run.decision_data_coverage_ratio is None or not math.isclose(
            run.decision_data_coverage_ratio,
            barrier.coverage_ratio,
            abs_tol=1e-6,
        ):
            raise SnapshotPublicationError("decision-data coverage ratio does not match coverage barrier")
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run.id)
                .order_by(ShortResearchSignalItem.global_rank.asc(), ShortResearchSignalItem.asset_code.asc())
                .with_for_update()
            )
        ).all()
        _validate_publishable(run, items, decision_data_codes=set(barrier.included_codes))
        _verify_snapshot_draft_seal(run, items)
        with authorize_snapshot_publication(session.sync_session, run_id=run.id):
            run.publication_state = "published"
            run.published_at = utcnow()
            await session.flush()
    return run
