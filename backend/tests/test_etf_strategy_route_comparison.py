"""Focused contract tests for the pure ETF comparison core."""

from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.market_data import is_etf_exchange_trading_day
from app.services.short_research.daily_reconstructable import (
    AdjustedOhlcvBar,
    AdjustmentProvenance,
    daily_reconstructable_manifest,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    BREAKOUT_V2,
    STATE_PREPARING,
    V2AdjustedBar,
    V2CandidateObservation,
    derive_lifecycle,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    RANKING_PORTFOLIO_BASE_COST_POLICY,
    ForwardAdjustedClose,
    calculate_continuous_ranking_portfolio,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    PointInTimeAdjustedSeries,
    PointInTimeEtfMetadata,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    StageBRankedItem,
    StageBRankingEvent,
    StageBReplayContract,
)
from app.services.strategy_lab.etf_strategy_route_comparison import (
    ComparisonContractError,
    ComparisonDataUnavailableError,
    ComparisonDecisionSnapshot,
    ComparisonInput,
    ComparisonValuationInput,
    DailyCorePITDateInput,
    FrozenComparisonProvenance,
    V2ComparisonEvent,
    V2StateCheck,
    bridge_v2_targets,
    build_v2_comparison_events,
    canonical_daily_core_targets,
    canonical_daily_core_targets_from_pit,
    run_comparison,
)

START = date(2026, 8, 26)
MIDDLE = date(2026, 8, 27)
END = date(2026, 8, 28)
SHANGHAI = ZoneInfo("Asia/Shanghai")
CODES = tuple(f"510{index:03d}" for index in range(10))


def _hash(value: object) -> str:
    return stable_contract_hash({"comparison-test": value})


def _calendar() -> tuple[date, ...]:
    origin = date(2026, 1, 1)
    end = date(2026, 9, 1)
    return tuple(
        day
        for offset in range((end - origin).days + 1)
        if is_etf_exchange_trading_day(day := origin + timedelta(days=offset))
    )


def _close(code: str, day: date, value: float) -> ForwardAdjustedClose:
    return ForwardAdjustedClose(
        asset_code=code,
        session_date=day,
        adjusted_close=value,
        price_basis="total_return_adjusted",
        decision_eligible=True,
        provider="fixture",
        adjustment_version="fixture-v1",
        source_hash=_hash((code, day, value)),
    )


def _state_check(
    day: date,
    *,
    keys: tuple[str, ...] = (),
    events: tuple[V2ComparisonEvent, ...] = (),
) -> V2StateCheck:
    transition_hashes = tuple(sorted(item.source_hash for item in events))
    payload = {
        "schema_version": "etf_strategy_route_comparison_v2_day_check_v1",
        "session_date": day,
        "manifest_hash": _hash(("manifest", day)),
        "input_hash": _hash(("input", day)),
        "observation_digest": _hash(("observations", day, keys)),
        "transition_digest": _hash(("transitions", day, transition_hashes)),
        "checked_through": day,
        "checked_through_cutoff": datetime.combine(day, time(19), SHANGHAI),
        "lifecycle_checked": True,
        "required_observation_keys": keys,
        "checked_observation_keys": keys,
        "transition_source_hashes": transition_hashes,
    }
    return V2StateCheck(
        **{key: value for key, value in payload.items() if key != "schema_version"},
        state_hash=stable_contract_hash(payload),
        observation_count=len(keys),
        transition_count=len(transition_hashes),
    )


def _event(
    day: date,
    code: str,
    event_type: str,
    *,
    original_day: date = START,
    score: float = 1.0,
    clone_group: str | None = None,
) -> V2ComparisonEvent:
    return V2ComparisonEvent(
        signal_date=day,
        original_signal_date=original_day,
        asset_code=code,
        event_type=event_type,  # type: ignore[arg-type]
        score=score,
        formula_id=BREAKOUT_V2,
        clone_group=clone_group or f"clone:{code}",
        source_hash=_hash(("event", day, original_day, code, event_type)),
    )


def _key(code: str, original_day: date = START) -> str:
    return f"{code}:{original_day.isoformat()}:{BREAKOUT_V2}"


def _ranking_event(day: date, codes: tuple[str, ...]) -> StageBRankingEvent:
    items = tuple(
        StageBRankedItem(
            asset_code=code,
            rank=index,
            research_score=100.0 - index,
            feature_hash=_hash(("feature", day, code)),
        )
        for index, code in enumerate(codes, start=1)
    )
    draft = StageBRankingEvent(
        replay_run_key="daily-core-comparison-test",
        replay_date=day,
        ranking_source_kind="research_replay",
        score_contract_id="daily_reconstructable_v1",
        score_field="research_score",
        score_manifest_hash=_hash("score-manifest"),
        stage_b_schema_version="etf-ranking-stage-b-v1",
        date_manifest_hash=_hash(("date-manifest", day)),
        source_date_manifest_hash=_hash(("source-manifest", day)),
        universe_hash=_hash(("universe", day)),
        input_hash=_hash(("input", day)),
        feature_manifest_hash=_hash(("features", day)),
        ranked_items=items,
        all_scored=codes,
        top5=codes[:5],
        top10=codes[:10],
        top20=codes[:20],
        event_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("event_hash")
    return replace(draft, event_hash=stable_contract_hash(payload))


def _pit_series(code: str, day: date) -> PointInTimeAdjustedSeries:
    sessions = tuple(item for item in _calendar() if item <= day)[-61:]
    bars = tuple(
        AdjustedOhlcvBar(
            session_date=session,
            adjusted_open=100.0 + index,
            adjusted_high=101.0 + index,
            adjusted_low=99.0 + index,
            adjusted_close=100.0 + index,
            volume=1_000.0 + index,
            turnover=100_000.0 + index,
        )
        for index, session in enumerate(sessions)
    )
    metadata = PointInTimeEtfMetadata(
        asset_code=code,
        membership_source="fixture",
        membership_external_source_id=f"membership:{code}",
        membership_provider_version="fixture-v1",
        membership_evidence_hash=_hash(("membership-evidence", code, day)),
        membership_raw_payload_hash=_hash(("membership-raw", code, day)),
        membership_fact_hash=_hash(("membership-fact", code, day)),
        tracked_underlying_id=None,
        membership_known_at=datetime.combine(day, time(19), SHANGHAI),
        membership_last_modified_at=datetime.combine(day, time(19), SHANGHAI),
        membership_ingested_at=datetime.combine(day, time(19), SHANGHAI),
        eligible_from=sessions[0],
        eligible_at=day,
    )
    payload = {
        "asset_code": code,
        "metadata": asdict(metadata),
        "bars": [asdict(item) for item in bars],
        "provenance": asdict(
            AdjustmentProvenance(
                provider="fixture",
                adjustment_version="fixture-v1",
                price_basis="total_return_adjusted",
                transform_kind="constant_multiplicative",
                scale_invariance_proven=True,
            )
        ),
        "earliest_source_timestamp": datetime.combine(sessions[0], time(16), SHANGHAI),
        "latest_source_timestamp": datetime.combine(day, time(16), SHANGHAI),
        "revision_hashes": tuple(_hash(("revision", code, item)) for item in sessions),
    }
    return PointInTimeAdjustedSeries(
        asset_code=code,
        metadata=metadata,
        bars=bars,
        provenance=AdjustmentProvenance(
            provider="fixture",
            adjustment_version="fixture-v1",
            price_basis="total_return_adjusted",
            transform_kind="constant_multiplicative",
            scale_invariance_proven=True,
        ),
        earliest_source_timestamp=datetime.combine(sessions[0], time(16), SHANGHAI),
        latest_source_timestamp=datetime.combine(day, time(16), SHANGHAI),
        synchronized_after_cutoff=False,
        revision_hashes=payload["revision_hashes"],
        series_hash=stable_contract_hash(payload),
    )


def _stage_b_contract() -> StageBReplayContract:
    registry = freeze_ranking_candidate_registry((FROZEN_RANKING_CANDIDATES[1],))
    return StageBReplayContract(
        replay_run_key="daily-core-pit-comparison-test",
        score_contract_id="daily_reconstructable_v1",
        score_manifest_hash=daily_reconstructable_manifest().manifest_hash,
        source_snapshot_hash=_hash("source-snapshot"),
        universe_manifest_hash=_hash("universe-manifest"),
        feature_schema_version="etf-ranking-stage-b-v1",
        candidate_registry_hash=registry.registry_hash,
        decision_cutoff_semantics="recorded_available_at_lte_signal_cutoff",
    )


def _snapshot(
    day: date,
    codes: tuple[str, ...] = CODES,
    *,
    negative_momentum: bool = False,
) -> ComparisonDecisionSnapshot:
    sessions = tuple(item for item in _calendar() if item <= day)
    rows = tuple(
        _close(
            code,
            item,
            100.0 if item < START else (99.0 if negative_momentum else 110.0),
        )
        for code in codes
        for item in sessions
    )
    return ComparisonDecisionSnapshot(
        signal_date=day,
        decision_cutoff=datetime.combine(day, time(19), SHANGHAI),
        source_hash=_hash(("snapshot", day, codes)),
        eligible_asset_codes=codes,
        nonclone_asset_codes=codes,
        adjusted_closes=rows,
    )


def _input(
    *,
    events: tuple[V2ComparisonEvent, ...] = (),
    keys_by_day: dict[date, tuple[str, ...]] | None = None,
    valuation_codes: tuple[str, ...] = CODES,
    missing_valuation: set[tuple[str, date]] | None = None,
    negative_momentum: bool = False,
) -> ComparisonInput:
    calendar = _calendar()
    sessions = tuple(day for day in calendar if day <= END)
    decisions = (START, MIDDLE, END)
    snapshots = tuple(
        _snapshot(day, valuation_codes, negative_momentum=negative_momentum)
        for day in decisions
    )
    checks = tuple(
        _state_check(day, keys=(keys_by_day or {}).get(day, ()), events=tuple(item for item in events if item.signal_date == day))
        for day in decisions
    )
    valuation_rows = tuple(
        _close(
            code,
            day,
            (99.0 if negative_momentum else (110.0 if day >= START else 100.0)),
        )
        for code in valuation_codes
        for day in decisions
        if (code, day) not in (missing_valuation or set())
    )
    provenance = FrozenComparisonProvenance(
        source_mode="factual_pit_fixture",
        data_version="comparison-test-v1",
        universe_policy_hash=_hash("universe"),
        membership_policy_hash=_hash("membership"),
        clone_policy_hash=_hash("clone"),
        adjusted_price_policy_hash=_hash("price"),
        calendar_hash=_hash(calendar),
        source_snapshot_hash=_hash(tuple(item.source_hash for item in snapshots)),
    )
    return ComparisonInput(
        provenance=provenance,
        start_date=START,
        end_date=END,
        valuation=ComparisonValuationInput(
            trading_sessions=sessions,
            adjusted_closes=valuation_rows,
            calendar_sessions=calendar,
            calendar_complete_through=calendar[-1],
        ),
        decision_snapshots=snapshots,
        v2_events=events,
        v2_state_checks=checks,
        daily_core_targets=(),
        daily_core_required_signal_dates=(),
    )


def test_lifecycle_bridge_uses_causal_date_and_does_not_double_shift() -> None:
    confirmation = _event(START, CODES[0], "confirmation")
    keys = {
        START: (_key(CODES[0]),),
        MIDDLE: (_key(CODES[0]),),
        END: (_key(CODES[0]),),
    }
    targets = bridge_v2_targets(
        input_data=_input(events=(confirmation,), keys_by_day=keys)
    )
    assert tuple(item.signal_date for item in targets) == (START,)
    assert targets[0].target_weights == ((CODES[0], 0.1),)


def test_exit_matches_lifecycle_and_clone_group_does_not_flatten_sibling() -> None:
    entry = _event(START, CODES[0], "confirmation", clone_group="clone:A")
    sibling_exit = _event(
        MIDDLE,
        CODES[1],
        "exit",
        clone_group="clone:A",
    )
    keys = {
        START: (_key(CODES[0]),),
        MIDDLE: (_key(CODES[0]), _key(CODES[1])),
        END: (_key(CODES[0]),),
    }
    targets = bridge_v2_targets(
        input_data=_input(events=(entry, sibling_exit), keys_by_day=keys)
    )
    assert tuple(item.signal_date for item in targets) == (START,)


def test_same_cutoff_exit_precedes_new_signal_for_same_asset() -> None:
    entry = _event(START, CODES[0], "confirmation")
    exit_old = _event(MIDDLE, CODES[0], "exit", original_day=START)
    entry_new = _event(MIDDLE, CODES[0], "confirmation", original_day=MIDDLE)
    keys = {
        START: (_key(CODES[0]),),
        MIDDLE: (_key(CODES[0]), _key(CODES[0], MIDDLE)),
        END: (),
    }
    targets = bridge_v2_targets(
        input_data=_input(events=(entry, exit_old, entry_new), keys_by_day=keys)
    )
    assert tuple(item.signal_date for item in targets) == (START, MIDDLE)
    assert targets[-1].target_weights == ()


def test_full_slots_keep_old_membership_and_do_not_queue_new_confirmation() -> None:
    codes = (*CODES, "510010")
    initial_events = tuple(
        _event(START, code, "confirmation", score=100.0 - index)
        for index, code in enumerate(CODES)
    )
    late_event = _event(MIDDLE, "510010", "confirmation", original_day=MIDDLE, score=999.0)
    keys = {
        START: tuple(_key(code) for code in CODES),
        MIDDLE: tuple(_key(code) for code in CODES) + (_key("510010", MIDDLE),),
        END: tuple(_key(code) for code in CODES),
    }
    targets = bridge_v2_targets(
        input_data=_input(
            events=(*initial_events, late_event),
            keys_by_day=keys,
            valuation_codes=codes,
        )
    )
    assert tuple(item.signal_date for item in targets) == (START,)
    assert tuple(code for code, _weight in targets[0].target_weights) == CODES


def test_missing_held_price_stops_account_without_stale_substitution() -> None:
    confirmation = _event(START, CODES[0], "confirmation")
    keys = {day: (_key(CODES[0]),) for day in (START, MIDDLE, END)}
    result = run_comparison(
        _input(
            events=(confirmation,),
            keys_by_day=keys,
            missing_valuation={(CODES[0], MIDDLE)},
        )
    )
    route = next(item for item in result.routes if item.route_id == "v2_breakout")
    assert route.status == "unavailable"
    assert route.base_ledger is not None
    assert len(route.base_ledger.points) == 1
    assert route.base_ledger.unavailable_intervals[0].reason == (
        "missing_decision_eligible_adjusted_valuation"
    )


def test_all_negative_momentum_is_valid_cash_after_common_pool_gate() -> None:
    result = run_comparison(_input(negative_momentum=True))
    route = next(item for item in result.routes if item.route_id == "medium_term_momentum_126")
    assert route.status == "completed"
    assert route.targets[0].target_weights == ()
    assert route.base_ledger is not None
    assert route.base_ledger.order_count == 0
    assert route.base_ledger.net_return == 0.0


def test_daily_core_hysteresis_targets_reuse_frozen_candidate_for_two_sessions() -> None:
    first_codes = tuple(f"D{index:02d}" for index in range(20))
    second_codes = ("D19", "D18", *first_codes[:18])
    targets = canonical_daily_core_targets(
        ranking_events=(
            _ranking_event(START, first_codes),
            _ranking_event(MIDDLE, second_codes),
        )
    )
    assert len(targets) == 2
    assert targets[0].target_weights == targets[1].target_weights
    closes = tuple(
        _close(code, day, 100.0 + index)
        for code in first_codes[:10]
        for index, day in enumerate((START, MIDDLE, END))
    )
    ledger = calculate_continuous_ranking_portfolio(
        trading_sessions=(START, MIDDLE, END),
        adjusted_closes=closes,
        targets=targets,
        cost_policy=RANKING_PORTFOLIO_BASE_COST_POLICY,
    )
    assert ledger.rebalance_count == 2


def test_daily_core_pit_bridge_scores_series_and_reuses_stage_b_hysteresis(monkeypatch) -> None:
    import app.services.strategy_lab.etf_strategy_route_comparison as comparison_module

    series_by_date = tuple(
        DailyCorePITDateInput(
            replay_date=day,
            decision_cutoff=datetime.combine(day, time(19), SHANGHAI),
            universe_hash=_hash(("universe", day)),
            authoritative_asset_codes=CODES[:2],
            series=tuple(_pit_series(code, day) for code in CODES[:2]),
        )
        for day in (START, MIDDLE)
    )
    calls: list[int] = []
    original = comparison_module.score_daily_reconstructable

    def spy(bars, *, provenance):
        calls.append(len(bars))
        return original(bars, provenance=provenance)

    monkeypatch.setattr(comparison_module, "score_daily_reconstructable", spy)
    targets = canonical_daily_core_targets_from_pit(
        date_inputs=series_by_date,
        stage_b_contract=_stage_b_contract(),
    )
    assert calls == [61, 61, 61, 61]
    assert tuple(item.signal_date for item in targets) == (START, MIDDLE)
    assert targets[0].target_weights == targets[1].target_weights
    assert len(targets[0].target_weights) == 2


def test_daily_core_pit_bridge_rejects_late_sealed_series() -> None:
    series = _pit_series(CODES[0], START)
    with pytest.raises(ComparisonDataUnavailableError, match="synchronized_after_cutoff"):
        DailyCorePITDateInput(
            replay_date=START,
            decision_cutoff=datetime.combine(START, time(19), SHANGHAI),
            universe_hash=_hash(("universe", START)),
            authoritative_asset_codes=(CODES[0],),
            series=(replace(series, synchronized_after_cutoff=True),),
        )


def test_v2_event_builder_rejects_tampered_observation_and_keeps_original_date() -> None:
    signal = START
    bars = tuple(
        V2AdjustedBar(
            trade_date=signal + timedelta(days=index - 4),
            adjusted_open=100.0,
            adjusted_high=100.5 if index <= 4 else 102.5,
            adjusted_low=99.5,
            adjusted_close=100.0 if index <= 4 else 102.0,
            volume=1_000.0,
            amount=100_000.0,
            turnover=100_000.0,
            observed_at=datetime.combine(signal + timedelta(days=index - 4), time(19), SHANGHAI),
            provider="fixture",
            adjustment_version="fixture-v1",
            revision_id=f"bar-{index}",
        )
        for index in range(6)
    )
    observation = V2CandidateObservation(
        universe="etf",
        asset_code=CODES[0],
        asset_name="fixture",
        signal_date=signal,
        formula_id=BREAKOUT_V2,
        state=STATE_PREPARING,
        availability="available",
        qualifies=True,
        score=1.0,
        gate_facts=(),
        exclusion_reasons=(),
        source_cutoff=datetime.combine(signal, time(19), SHANGHAI),
        theme="theme",
        sector="sector",
        tracked_index="index",
        clone_group="clone:A",
        issuer="issuer",
        feature_hash="pending",
    )
    observation = replace(
        observation,
        feature_hash=stable_contract_hash(observation.canonical_payload()),
    )
    transitions = derive_lifecycle(
        observation=observation,
        signal_bars=bars,
        evaluation_cutoff=datetime.combine(signal + timedelta(days=1), time(19), SHANGHAI),
        visible_through=signal + timedelta(days=1),
    )
    events = build_v2_comparison_events(
        observations=(observation,), transitions=transitions
    )
    assert events and events[0].original_signal_date == signal
    naive_utc_observation = replace(
        observation,
        source_cutoff=datetime.combine(signal, time(11)),
        feature_hash="pending",
    )
    naive_utc_observation = replace(
        naive_utc_observation,
        feature_hash=stable_contract_hash(naive_utc_observation.canonical_payload()),
    )
    assert build_v2_comparison_events(
        observations=(naive_utc_observation,), transitions=transitions
    )
    with pytest.raises(ComparisonContractError):
        build_v2_comparison_events(
            observations=(
                replace(
                    observation,
                    score=999.0,
                    source_cutoff=datetime.combine(signal + timedelta(days=7), time(19), SHANGHAI),
                ),
            ),
            transitions=transitions,
        )
