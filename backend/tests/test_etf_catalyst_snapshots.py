from dataclasses import replace
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.models.entities import (
    EtfOptimizedAllocationSnapshot,
    EtfThemeCatalystEvent,
    NotificationLog,
    TrackedPosition,
    TrackedPositionAlert,
)
from app.services.etf_catalyst_shadow.events import (
    EventCandidate,
    migrate_manual_seeds_display_only,
    normalize_event_candidate,
)
from app.services.etf_catalyst_shadow.ingestion import (
    ReceiptInput,
    ingest_receipt,
    register_approved_sources,
)
from app.services.etf_catalyst_shadow.policy import APPROVED_CATALYST_SOURCES
from app.services.etf_catalyst_shadow.snapshots import (
    build_shadow_snapshot,
    record_coverage_observation,
)
from app.services.short_research.daily_reconstructable import (
    AdjustedOhlcvBar,
    AdjustmentProvenance,
    score_daily_reconstructable,
)
from app.services.short_research.final_score_v3 import FinalScoreV3Result
from app.services.short_research.ranking_surfaces import (
    MANDATORY_ACTIONABLE_FIELDS,
    ActionableFieldEvidence,
    evaluate_actionable_rank,
)

PUBLISHED = datetime(2026, 7, 1, 6, 30)
RECEIVED = datetime(2026, 7, 1, 7, 0)
CUTOFF = datetime(2026, 7, 1, 15, 0)


async def _item_receipt(
    session,
    *,
    content: str = "version one",
    received_at: datetime = RECEIVED,
):
    await register_approved_sources(session)
    return await ingest_receipt(
        session,
        ReceiptInput(
            source_id="ndrc_policy",
            fetch_state="item",
            fetched_at=received_at + timedelta(minutes=1),
            first_received_at=received_at,
            external_id="snapshot-event-1",
            canonical_url="https://www.ndrc.gov.cn/policy/snapshot.html",
            source_published_at=PUBLISHED,
            raw_content=content,
            raw_content_ref=f"raw://snapshot/{content}",
            metadata={
                "supported_entities": ["国家发展改革委"],
                "supported_theme_ids": ["新能源"],
                "supported_direction": "positive",
            },
        ),
    )


def _candidate(
    receipt_id: str,
    *,
    event_id: str = "stable-event-1",
    title: str = "新能源政策第一版",
    effective_start: datetime = PUBLISHED,
    effective_end: datetime = PUBLISHED + timedelta(days=30),
) -> EventCandidate:
    return EventCandidate(
        event_id=event_id,
        event_type="industry_policy",
        entities=("国家发展改革委",),
        title=title,
        summary=title,
        receipt_ids=(receipt_id,),
        source_published_at=PUBLISHED,
        effective_start=effective_start,
        effective_end=effective_end,
        direction="positive",
        direct_theme_ids=("新能源",),
    )


async def _observation(
    session,
    *,
    source_id: str,
    state: str,
    cutoff_at: datetime = CUTOFF,
):
    receipt_state = {
        "observed_none": "successful_empty",
        "unavailable": "unavailable",
        "not_applicable": "not_applicable",
    }[state]
    receipt = await ingest_receipt(
        session,
        ReceiptInput(
            source_id=source_id,
            fetch_state=receipt_state,  # type: ignore[arg-type]
            fetched_at=cutoff_at - timedelta(minutes=1),
            first_received_at=cutoff_at - timedelta(minutes=2),
            observation_key=f"{source_id}:{cutoff_at.isoformat()}:{receipt_state}",
            error_summary="TimeoutError: 50 seconds" if state == "unavailable" else None,
        ),
    )
    return await record_coverage_observation(
        session,
        source_id=source_id,
        theme_id="新能源",
        session_date=cutoff_at.date(),
        cutoff_at=cutoff_at,
        state=state,  # type: ignore[arg-type]
        receipt_ids=(receipt.receipt_id,),
        reason=(
            "source timed out"
            if state == "unavailable"
            else "source/theme policy excludes observation"
            if state == "not_applicable"
            else None
        ),
    )


async def test_historical_cutoff_excludes_late_discovery_and_is_deterministic(
    app,
) -> None:
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        before = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=CUTOFF.date(),
            cutoff_at=CUTOFF,
        )
        late_receipt = await _item_receipt(
            session,
            received_at=CUTOFF + timedelta(hours=1),
        )
        await normalize_event_candidate(
            session,
            _candidate(late_receipt.receipt_id, event_id="late-event"),
            extraction_method="deterministic",
            verify=True,
        )
        repeated = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=CUTOFF.date(),
            cutoff_at=CUTOFF,
        )

    assert before.snapshot_hash == repeated.snapshot_hash
    assert before.coverage_state == "unavailable"
    assert before.event_versions_json == []


async def test_correction_and_effective_period_follow_point_in_time_versions(app) -> None:
    correction_received = CUTOFF + timedelta(days=1)
    async with app.state.db.session() as session:
        first_receipt = await _item_receipt(session)
        first = await normalize_event_candidate(
            session,
            _candidate(first_receipt.receipt_id),
            extraction_method="deterministic",
            verify=True,
        )
        historical = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=CUTOFF.date(),
            cutoff_at=CUTOFF,
        )
        correction_receipt = await _item_receipt(
            session,
            content="corrected version",
            received_at=correction_received,
        )
        correction = await normalize_event_candidate(
            session,
            _candidate(
                correction_receipt.receipt_id,
                title="新能源政策纠正版",
            ),
            extraction_method="deterministic",
            verify=True,
        )
        corrected_cutoff = correction_received + timedelta(hours=1)
        current = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=corrected_cutoff.date(),
            cutoff_at=corrected_cutoff,
        )
        expired_cutoff = PUBLISHED + timedelta(days=31)
        expired = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=expired_cutoff.date(),
            cutoff_at=expired_cutoff,
        )

    assert historical.event_versions_json[0]["event_hash"] == first.event_hash
    assert current.event_versions_json[0]["event_hash"] == correction.event_hash
    assert current.event_versions_json[0]["event_version"] == 2
    assert expired.event_versions_json == []


async def test_coverage_states_never_convert_unavailable_to_observed_none(app) -> None:
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        for source in APPROVED_CATALYST_SOURCES:
            await _observation(
                session,
                source_id=source.source_id,
                state=(
                    "unavailable"
                    if source.source_id == "ndrc_policy"
                    else "not_applicable"
                ),
            )
        unavailable = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=CUTOFF.date(),
            cutoff_at=CUTOFF,
        )

    assert unavailable.coverage_state == "unavailable"
    ndrc = next(
        item
        for item in unavailable.source_coverage_json
        if item["source_id"] == "ndrc_policy"
    )
    assert ndrc["state"] == "unavailable"
    assert "timed out" in ndrc["reason"]


async def test_observed_none_and_not_applicable_require_explicit_receipts(app) -> None:
    all_na_cutoff = CUTOFF + timedelta(days=1)
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        for source in APPROVED_CATALYST_SOURCES:
            await _observation(
                session,
                source_id=source.source_id,
                state=(
                    "observed_none"
                    if source.source_id == "ndrc_policy"
                    else "not_applicable"
                ),
            )
        observed_none = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=CUTOFF.date(),
            cutoff_at=CUTOFF,
        )
        for source in APPROVED_CATALYST_SOURCES:
            await _observation(
                session,
                source_id=source.source_id,
                state="not_applicable",
                cutoff_at=all_na_cutoff,
            )
        not_applicable = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=all_na_cutoff.date(),
            cutoff_at=all_na_cutoff,
        )

    assert observed_none.coverage_state == "observed_none"
    assert not_applicable.coverage_state == "not_applicable"


async def test_manual_display_only_never_enters_verified_snapshot(app) -> None:
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        session.add(
            EtfThemeCatalystEvent(
                theme_key="新能源",
                theme_name="新能源",
                catalyst_type="manual",
                title="人工种子",
                summary="展示专用",
                event_date=CUTOFF.date(),
                effective_start=CUTOFF.date(),
                effective_end=CUTOFF.date() + timedelta(days=30),
                direction="positive",
                strength_score=100,
                confidence_score=100,
                status="active",
                source_type="manual_seed",
                metadata_json={},
            )
        )
        await session.commit()
        migrated = await migrate_manual_seeds_display_only(session)
        snapshot = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=CUTOFF.date(),
            cutoff_at=CUTOFF,
        )

    assert migrated[0].verification_state == "manual_display_only"
    assert snapshot.event_versions_json == []


def _bars() -> tuple[AdjustedOhlcvBar, ...]:
    start = date(2026, 4, 1)
    return tuple(
        AdjustedOhlcvBar(
            session_date=start + timedelta(days=offset),
            adjusted_open=100 + offset,
            adjusted_high=101 + offset,
            adjusted_low=99 + offset,
            adjusted_close=100.5 + offset,
            volume=1_000_000 + offset * 1_000,
        )
        for offset in range(61)
    )


async def test_active_shadow_cannot_change_scores_ranks_or_action_tables(app) -> None:
    provenance = AdjustmentProvenance(
        provider="fixture",
        adjustment_version="fixture-v1",
        price_basis="total_return_adjusted",
        transform_kind="constant_multiplicative",
        scale_invariance_proven=True,
    )
    research_before = score_daily_reconstructable(_bars(), provenance=provenance)
    final_score = FinalScoreV3Result(
        asset_bucket="sector/theme-equity",
        ranking_score=76.0,
        score_eligible=True,
        component_scores={"technical": 76.0},
        missing_by_component={},
        metric_peer_counts={"return_20d": 20},
        limitation_reasons=(),
    )
    fields = {
        field: ActionableFieldEvidence(status="available")
        for field in MANDATORY_ACTIONABLE_FIELDS
    }
    actionable_before = evaluate_actionable_rank(
        final_score,
        eligible_sessions=120,
        field_evidence=fields,
    )

    async with app.state.db.session() as session:
        models = (
            EtfOptimizedAllocationSnapshot,
            TrackedPosition,
            TrackedPositionAlert,
            NotificationLog,
        )
        before_values: list[int] = []
        for model in models:
            before_values.append(
                int(
                    await session.scalar(select(func.count()).select_from(model))
                    or 0
                )
            )
        before = tuple(before_values)
        receipt = await _item_receipt(session)
        await normalize_event_candidate(
            session,
            _candidate(receipt.receipt_id),
            extraction_method="deterministic",
            verify=True,
        )
        snapshot = await build_shadow_snapshot(
            session,
            theme_id="新能源",
            session_date=CUTOFF.date(),
            cutoff_at=CUTOFF,
        )
        after_values: list[int] = []
        for model in models:
            after_values.append(
                int(
                    await session.scalar(select(func.count()).select_from(model))
                    or 0
                )
            )
        after = tuple(after_values)

    research_after = score_daily_reconstructable(_bars(), provenance=provenance)
    actionable_after = evaluate_actionable_rank(
        replace(final_score),
        eligible_sessions=120,
        field_evidence=fields,
    )
    assert snapshot.coverage_state == "active"
    assert research_after == research_before
    assert actionable_after == actionable_before
    assert before == after
    assert sorted([("A", research_before.research_score), ("B", 50.0)]) == sorted(
        [("A", research_after.research_score), ("B", 50.0)]
    )
