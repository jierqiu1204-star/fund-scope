from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.entities import (
    EtfAdjustedPriceRevision,
    EtfLabelOutcome,
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
from app.services.short_research import service
from app.services.short_research.coverage_policy import ETF_READINESS_POLICY_VERSION
from app.services.short_research.daily_reconstructable import daily_reconstructable_manifest
from app.services.short_research.ranking_contract import canonical_hash
from app.services.strategy_lab.etf_validation_session_planner import (
    expand_validation_source_search,
    plan_production_validation_sources,
    published_validation_source_identity,
    theoretical_session_span,
)


def _sessions(count: int) -> tuple[date, ...]:
    values: list[date] = []
    cursor = date(2025, 1, 2)
    holidays = {date(2025, 1, 29), date(2025, 1, 30), date(2025, 1, 31)}
    while len(values) < count:
        if cursor.weekday() < 5 and cursor not in holidays:
            values.append(cursor)
        cursor += timedelta(days=1)
    return tuple(values)


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [(1, 60), (3, 100), (5, 140), (10, 240)],
)
def test_theoretical_span_covers_twenty_non_overlapping_t_plus_one_outcomes(
    horizon: int,
    expected: int,
) -> None:
    assert theoretical_session_span(horizon_sessions=horizon, required_dates=20) == expected


@pytest.mark.asyncio
async def test_planner_uses_exchange_session_indexes_across_holidays() -> None:
    sessions = _sessions(260)

    async def load(start: date, end: date) -> tuple[date, ...]:
        return tuple(session for session in sessions if start <= session <= end)

    plan = await expand_validation_source_search(
        trading_sessions=sessions,
        load_compatible_source_dates=load,
        horizon_sessions=10,
        required_dates=20,
        retention_cap_sessions=260,
    )

    assert plan.status == "ready"
    assert plan.theoretical_session_span == 240
    assert plan.searched_session_span == 240
    assert plan.completed_count == 229
    assert plan.non_overlapping_count >= 20
    assert len(plan.selected_source_dates) == 20
    assert plan.pending_count == 11
    assert plan.overlapping_count > 0
    assert plan.source_date_shortfall == 0
    assert all(day in sessions for day in plan.selected_source_dates)


@pytest.mark.asyncio
async def test_sparse_sources_expand_beyond_theoretical_span_until_ready() -> None:
    sessions = _sessions(320)
    sparse_dates = set(sessions[::4])
    calls: list[tuple[date, date]] = []

    async def load(start: date, end: date) -> tuple[date, ...]:
        calls.append((start, end))
        return tuple(day for day in sparse_dates if start <= day <= end)

    plan = await expand_validation_source_search(
        trading_sessions=sessions,
        load_compatible_source_dates=load,
        horizon_sessions=5,
        required_dates=20,
        retention_cap_sessions=300,
        expansion_page_sessions=28,
    )

    assert plan.status == "ready"
    assert plan.theoretical_session_span == 140
    assert 140 < plan.searched_session_span <= 300
    assert plan.completed_count >= 20
    assert plan.non_overlapping_count >= 20
    assert len(plan.selected_source_dates) == 20
    assert plan.source_date_shortfall == 0
    assert plan.query_count == len(calls) > 1
    assert all(calls[index][1] < calls[index - 1][0] for index in range(1, len(calls)))


@pytest.mark.asyncio
async def test_planner_reports_exact_shortfall_at_retention_cap() -> None:
    sessions = _sessions(300)
    sparse_dates = set(sessions[::30])

    async def load(start: date, end: date) -> tuple[date, ...]:
        return tuple(day for day in sparse_dates if start <= day <= end)

    plan = await expand_validation_source_search(
        trading_sessions=sessions,
        load_compatible_source_dates=load,
        horizon_sessions=10,
        required_dates=20,
        retention_cap_sessions=240,
    )

    assert plan.status == "insufficient"
    assert plan.searched_session_span == 240
    assert plan.non_overlapping_count < 20
    assert plan.source_date_shortfall == 20 - plan.non_overlapping_count
    assert plan.reason == "sparse_compatible_sources_at_retention_cap"


@pytest.fixture
async def validation_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _published_cohort(session, day: date, code: str, **overrides):
    manifest = daily_reconstructable_manifest()
    values = {
        "status": "success", "as_of_date": day, "as_of_trade_date": day,
        "started_at": datetime.combine(day, time(7)), "scope_kind": "full",
        "scope_hash": "a" * 64, "universe_snapshot_hash": "b" * 64,
        "input_snapshot_hash": "c" * 64, "score_version": manifest.contract_id,
        "rule_version": "dual_ranking_surfaces_v1", "score_field": manifest.score_field,
        "ranking_contract_hash": manifest.manifest_hash,
        "price_basis": "total_return_adjusted", "publication_state": "unpublished",
        "published_at": datetime.combine(day, time(8)),
        "data_cutoff": datetime.combine(day, time(15)), "expected_item_count": 1,
        "decision_data_item_count": 1, "decision_data_coverage_ratio": 1.0,
        "eligible_item_count": 1, "coverage_ratio": 1.0,
        "idempotency_key": f"cohort-{day}-{code}",
        "config_json": {"asset_type": "etf"},
        "summary_json": {"item_count": 1, "etf_count": 1, "fund_count": 0,
                         "readiness_policy_version": ETF_READINESS_POLICY_VERSION},
    }
    values.update(overrides)
    run = ShortResearchSignalRun(**values)
    session.add(run)
    await session.flush()
    if await session.get(TradableEtf, code) is None:
        session.add(TradableEtf(code=code, name=code, exchange="SH",
                               trading_rule_label="T+1", asset_class="sector"))
    item = ShortResearchSignalItem(
        run_id=run.id, asset_type="etf", asset_code=code, rank=1, global_rank=1,
        total_score=80, ranking_score=80, score_eligible=True, conclusion="短线观察",
        metrics_json={"entry_timing_label": "健康回踩", "data_reliability": "verified"},
    )
    session.add(item)
    await session.flush()
    # Load an already sealed historical fixture; publishing itself is covered separately.
    await session.execute(
        update(ShortResearchSignalRun.__table__).where(ShortResearchSignalRun.id == run.id)
        .values(publication_state=overrides.get("publication_state", "published"))
    )
    await session.refresh(run)
    return run, item


def _outcome_price(code: str, day: date, value: float, *, received: datetime | None = None):
    timestamp = received or datetime.combine(day, time(7))
    return EtfAdjustedPriceRevision(
        etf_code=code, trade_date=day, open=value, high=value, low=value, close=value,
        volume=1000, turnover=10000, pct_change=0, research_adjusted_value=value,
        research_price_basis="total_return_adjusted", data_provider="eastmoney",
        provider_version="eastmoney.push2his.kline.hfq_v1",
        adjustment_version="eastmoney.push2his.kline.hfq_v1", decision_eligible=True,
        source_timestamp=timestamp, first_seen_at=timestamp, observed_at=timestamp,
        payload_hash=canonical_hash([code, day.isoformat(), value]),
        revision_hash=canonical_hash([code, day.isoformat(), value, timestamp.isoformat()]),
    )


@pytest.mark.asyncio
async def test_label_recovery_matures_old_cohort_without_todays_publication(validation_session):
    session = validation_session
    run, item = await _published_cohort(session, date(2026, 8, 3), "560001")
    session.add(_outcome_price(item.asset_code, date(2026, 8, 3), 100))
    await session.commit()
    first = await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 3, 8, tzinfo=UTC)
    )
    assert first.summary_json["pending_outcomes"] == 4
    for offset in range(1, 17):
        day = date(2026, 8, 3) + timedelta(days=offset)
        if day.weekday() < 5:
            session.add(_outcome_price(item.asset_code, day, 100 + offset))
    await session.commit()
    recovered = await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 20, 8, tzinfo=UTC)
    )
    assert recovered.status == "success"
    assert recovered.source_score_version == "daily_reconstructable_v1"
    assert recovered.source_signal_run_id == run.id
    assert recovered.summary_json["current_publication_state"] == "unavailable"
    assert recovered.summary_json["completed_outcomes"] == 4
    original = [(row.id, row.forward_return, row.metrics_json) for row in
                (await session.scalars(select(EtfLabelOutcome))).all()]
    await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 21, 8, tzinfo=UTC)
    )
    repeated = [(row.id, row.forward_return, row.metrics_json) for row in
                (await session.scalars(select(EtfLabelOutcome))).all()]
    assert repeated == original
    earlier = await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 3, 8, tzinfo=UTC)
    )
    assert earlier.summary_json["completed_outcomes"] == 0
    assert [(row.id, row.forward_return, row.metrics_json) for row in
            (await session.scalars(select(EtfLabelOutcome))).all()] == original


@pytest.mark.asyncio
async def test_label_recovery_distinguishes_missing_and_pending_without_skipping_sessions(
    validation_session,
):
    session = validation_session
    _run, item = await _published_cohort(session, date(2026, 8, 3), "560002")
    session.add_all([
        _outcome_price(item.asset_code, date(2026, 8, 3), 100),
        _outcome_price(item.asset_code, date(2026, 8, 5), 105),
    ])
    await session.commit()
    await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 5, 8, tzinfo=UTC)
    )
    outcomes = {row.horizon_days: row for row in
                (await session.scalars(select(EtfLabelOutcome))).all()}
    assert outcomes[1].status == "excluded"
    assert outcomes[1].exclusion_reason == "missing_horizon_exit_price"
    assert outcomes[3].status == "pending"
    assert outcomes[3].exclusion_reason == "future_window_not_elapsed"
    session.add(_outcome_price(item.asset_code, date(2026, 8, 4), 102))
    await session.commit()
    await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 6, 8, tzinfo=UTC)
    )
    assert outcomes[1].status == "completed"
    assert outcomes[1].forward_return == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_bounded_recovery_rotates_missing_cohort_and_keeps_new_cohort_pending(
    validation_session,
):
    session = validation_session
    _missing, missing_item = await _published_cohort(session, date(2026, 8, 3), "560003")
    _old, old_item = await _published_cohort(session, date(2026, 8, 4), "560004")
    _new, new_item = await _published_cohort(session, date(2026, 8, 20), "560005")
    for offset in range(17):
        day = date(2026, 8, 4) + timedelta(days=offset)
        if day.weekday() < 5:
            session.add(_outcome_price(old_item.asset_code, day, 100 + offset))
    await session.commit()
    for _ in range(3):
        result = await service.review_etf_label_outcomes(
            session, max_signal_items=1,
            outcome_cutoff=datetime(2026, 8, 20, 8, tzinfo=UTC),
        )
        assert result["processed_signal_items"] <= 1
        await session.commit()
    outcomes = (await session.scalars(select(EtfLabelOutcome))).all()
    assert sum(row.status == "completed" for row in outcomes
               if row.signal_item_id == old_item.id) == 4
    assert not any(row.status == "completed" for row in outcomes
                   if row.signal_item_id in {new_item.id, missing_item.id})


@pytest.mark.asyncio
async def test_published_source_planner_preserves_versions_and_rejects_missing_identity(
    validation_session,
):
    session = validation_session
    research, _ = await _published_cohort(session, date(2026, 8, 3), "560011")
    old, _ = await _published_cohort(
        session, date(2026, 8, 4), "560012", score_version="final_score_v3",
        score_field="ranking_score", rule_version="final_score_v3_rule_v2",
        ranking_contract_hash="d" * 64,
    )
    missing, _ = await _published_cohort(
        session, date(2026, 8, 5), "560013", ranking_contract_hash=None,
    )
    fund, _ = await _published_cohort(
        session, date(2026, 8, 6), "560014", config_json={"asset_type": "fund"},
    )
    for offset in range(18):
        day = date(2026, 8, 3) + timedelta(days=offset)
        if day.weekday() < 5:
            session.add(EtfPriceHistory(
                etf_code="560011", trade_date=day, open=100, high=100, low=100,
                close=100, volume=1000, turnover=10000, pct_change=0,
                research_adjusted_value=100, research_price_basis="total_return_adjusted",
                decision_eligible=True,
            ))
    await session.commit()
    current = await plan_production_validation_sources(
        session, as_of_date=date(2026, 8, 20), horizon_sessions=1, required_dates=1,
    )
    legacy = await plan_production_validation_sources(
        session, as_of_date=date(2026, 8, 20), horizon_sessions=1, required_dates=1,
        score_version="final_score_v3",
    )
    assert [run.id for run in current.source_runs] == [research.id]
    assert [run.id for run in legacy.source_runs] == [old.id]
    assert published_validation_source_identity(research)["score_field"] == "research_score"
    with pytest.raises(ValueError, match="missing_ranking_contract_hash"):
        published_validation_source_identity(missing)
    with pytest.raises(ValueError, match="non_etf_source_snapshot"):
        published_validation_source_identity(fund)
    validation = await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 20, 8, tzinfo=UTC),
    )
    identities = [group["source_identity"] for group in validation.summary_json["evidence_groups"]]
    assert {identity["score_version"] for identity in identities} == {
        "final_score_v3", "daily_reconstructable_v1",
    }
    assert validation.source_ranking_contract_hash == research.ranking_contract_hash
    assert all(identity["ranking_source_kind"] == "production_published" for identity in identities)


@pytest.mark.asyncio
async def test_label_outcomes_use_separate_receipt_cutoffs_and_preserve_completed_legacy(
    validation_session,
):
    session = validation_session
    run, item = await _published_cohort(session, date(2026, 8, 3), "560015")
    session.add_all([
        _outcome_price(item.asset_code, date(2026, 8, 3), 100),
        _outcome_price(item.asset_code, date(2026, 8, 3), 200,
                       received=datetime(2026, 8, 10, 7)),
        _outcome_price(item.asset_code, date(2026, 8, 4), 102,
                       received=datetime(2026, 8, 10, 7)),
        EtfLabelOutcome(signal_item_id=item.id, signal_run_id=run.id, asset_type="etf",
                        asset_code=item.asset_code, label=item.conclusion,
                        entry_timing_label="健康回踩", rule_version="label_validation_v1",
                        signal_date=date(2026, 8, 3), horizon_days=10, status="completed",
                        forward_return=0.777, metrics_json={"legacy_fixture": True}),
    ])
    await session.commit()
    early = await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 5, 8, tzinfo=UTC),
    )
    assert early.summary_json["completed_outcomes"] == 0
    assert early.summary_json["legacy_outcomes"] == 1
    late = await service.run_etf_signal_validation(
        session, outcome_cutoff=datetime(2026, 8, 10, 8, tzinfo=UTC),
    )
    outcomes = {row.horizon_days: row for row in
                (await session.scalars(select(EtfLabelOutcome))).all()}
    assert late.summary_json["completed_outcomes"] == 1
    assert outcomes[1].signal_price == 100
    assert outcomes[1].forward_return == pytest.approx(0.02)
    assert outcomes[1].metrics_json["signal_cutoff"] != outcomes[1].metrics_json["outcome_cutoff"]
    assert outcomes[10].forward_return == 0.777
    assert outcomes[10].metrics_json == {"legacy_fixture": True}


@pytest.mark.asyncio
async def test_incompatible_outcomes_and_failed_publications_do_not_starve_recovery(
    validation_session, monkeypatch,
):
    session = validation_session
    blocked, blocked_item = await _published_cohort(session, date(2026, 8, 3), "560016")
    live, item = await _published_cohort(session, date(2026, 8, 4), "560017")
    for horizon in (1, 3, 5, 10):
        session.add(EtfLabelOutcome(
            signal_item_id=blocked_item.id, signal_run_id=blocked.id, asset_type="etf",
            asset_code=blocked_item.asset_code, label=blocked_item.conclusion,
            entry_timing_label="健康回踩", rule_version="another_execution",
            signal_date=blocked.as_of_date, horizon_days=horizon, status="pending",
            created_at=datetime(2026, 8, 3, 7), updated_at=datetime(2026, 8, 3, 7),
        ))
    for offset in range(17):
        day = date(2026, 8, 4) + timedelta(days=offset)
        if day.weekday() < 5:
            session.add(_outcome_price(item.asset_code, day, 100 + offset))
    for offset in range(3):
        await _published_cohort(
            session, date(2026, 8, 17) + timedelta(days=offset), f"56100{offset}",
            status="failed", publication_state="unpublished",
        )
    await session.commit()
    monkeypatch.setattr(service, "_LABEL_VALIDATION_SOURCE_LIMIT", 2)
    result = await service.review_etf_label_outcomes(
        session, max_signal_items=1, outcome_cutoff=datetime(2026, 8, 20, 8, tzinfo=UTC),
    )
    assert result["processed_signal_items"] == 1
    assert result["completed_outcomes"] == 4
    assert result["last_signal_item_id"] == item.id
    assert len(result["excluded_source_snapshots"]) == 3
    assert live.id != blocked.id


@pytest.mark.asyncio
async def test_source_retention_is_applied_after_full_contract_validation(validation_session, monkeypatch):
    session = validation_session
    valid, _ = await _published_cohort(session, date(2026, 8, 3), "560021")
    await _published_cohort(session, date(2026, 8, 4), "560022", score_field="wrong_field")
    await _published_cohort(session, date(2026, 8, 5), "560023", rule_version="wrong_rule")
    await session.commit()
    monkeypatch.setattr(service, "_LABEL_VALIDATION_SOURCE_LIMIT", 2)
    sources, exclusions = await service._historical_label_sources(
        session, datetime(2026, 8, 20, 8, tzinfo=UTC)
    )
    assert [source.id for source in sources] == [valid.id]
    assert {item["reason"] for item in exclusions} == {
        "incompatible_score_field", "incompatible_rule_version",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("signal_date", "outcome_cutoff"), [
    (date(2025, 12, 30), datetime(2025, 12, 31, 8, tzinfo=UTC)),
    (date(2025, 12, 30), datetime(2027, 1, 20, 8, tzinfo=UTC)),
    (date(2026, 12, 31), datetime(2027, 1, 20, 8, tzinfo=UTC)),
])
async def test_unknown_calendar_never_shifts_or_scans_unbounded_horizons(
    validation_session, signal_date, outcome_cutoff,
):
    session = validation_session
    await _published_cohort(session, signal_date, "560024")
    await session.commit()
    assert service._label_outcome_dates(signal_date) == (signal_date,)
    result = await service.run_etf_signal_validation(session, outcome_cutoff=outcome_cutoff)
    assert result.summary_json["completed_outcomes"] == 0
    outcomes = (await session.scalars(select(EtfLabelOutcome))).all()
    assert len(outcomes) == 4
    assert {row.exclusion_reason for row in outcomes} == {"exchange_calendar_unavailable"}
