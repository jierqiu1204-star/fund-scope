from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.etf_research_evidence import RankingSourceKind, stable_contract_hash
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    build_frozen_research_loop_manifest,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10,
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
    FROZEN_RANKING_CANDIDATES,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    ForwardAdjustedClose,
    calculate_continuous_ranking_portfolio,
    freeze_ranking_portfolio_target,
)
from app.services.strategy_lab.etf_ranking_validation import RankingValidationSourceEvent
from app.services.workflows import etf_point_in_time_capture as workflow


def _hash(value: object) -> str:
    return stable_contract_hash(value)


def _manifest():
    return build_frozen_research_loop_manifest(
        replay_run_key="review-ranking",
        code_version="review",
        source_snapshot_hash=_hash("source"),
        universe_manifest_hash=_hash("universe"),
        split_contract_hash=_hash("split"),
        holdout_identity_hash=_hash("holdout"),
        bootstrap_seed=7,
    )


def _event(day: date) -> RankingValidationSourceEvent:
    value = RankingValidationSourceEvent(
        ranking_source_kind=RankingSourceKind.PRODUCTION_PUBLISHED,
        signal_date=day,
        source_signal_run_id=day.toordinal(),
        source_replay_run_key=None,
        source_replay_contract_hash=None,
        source_event_hash=_hash(str(day)),
        ranking_contract_hash=_hash("ranking"),
        scope_hash=_hash("scope"),
        universe_snapshot_hash=_hash("universe"),
        input_snapshot_hash=_hash("input"),
        score_version="daily_reconstructable_v1",
        score_field="research_score",
        rule_version="dual_ranking_surfaces_v1",
        price_basis="total_return_adjusted",
        publication_state="published",
        scope_kind="full",
        availability_cutoff=datetime.combine(day, datetime.min.time()),
        immutable_hash="pending",
        source_status="success",
        idempotency_key=f"review:{day}",
        expected_asset_count=2,
        decision_data_covered_count=2,
        eligible_asset_count=2,
        item_count=2,
        etf_item_count=2,
        finite_eligible_score_count=2,
        decision_data_coverage_ratio=1.0,
        score_coverage_ratio=1.0,
        contiguous_global_rank=True,
    )
    payload = asdict(value)
    payload.pop("immutable_hash")
    return replace(value, immutable_hash=_hash(payload))


def _close(code: str, day: date, value: float) -> ForwardAdjustedClose:
    return ForwardAdjustedClose(
        asset_code=code,
        session_date=day,
        adjusted_close=value,
        price_basis="total_return_adjusted",
        decision_eligible=True,
        provider="eastmoney",
        adjustment_version="hfq_v1",
        source_hash=_hash((code, str(day), value)),
    )


@pytest.mark.asyncio
async def test_old_source_sample_uses_future_daily_targets_and_pit_momentum(monkeypatch, app, tmp_path):
    manifest = _manifest()
    signal_date = date(2026, 7, 1)
    sessions = tuple(signal_date + timedelta(days=index) for index in range(-25, 7))
    history = []
    for offset in range(6):
        day = signal_date + timedelta(days=offset)
        source = SimpleNamespace(
            as_of_trade_date=day,
            source_signal_run_id=day.toordinal(),
            replay_visibility_cutoff=datetime.combine(day, datetime.min.time()),
        )
        history.append(workflow._RankingHistoryDate(
            source=source,
            manifest=manifest,
            event=SimpleNamespace(replay_date=day, all_scored=("A", "B")),
            selections=tuple(SimpleNamespace(
                candidate_id=candidate.candidate_id,
                selected_asset_codes=("B",) if offset >= 1 else ("A",),
                selection_hash=_hash((candidate.candidate_id, offset)),
            ) for candidate in FROZEN_RANKING_CANDIDATES),
        ))
    async def load_history(**kwargs):
        return tuple(history), ()
    momentum_cutoffs = []
    async def adjusted_closes(**kwargs):
        if kwargs.get("decision_cutoff") is not None:
            momentum_cutoffs.append(kwargs["decision_cutoff"])
        return tuple(
            _close(code, day, 50.0 if code == "A" and day > signal_date + timedelta(days=2) else 100.0)
            for day in kwargs["trading_sessions"] for code in ("A", "B")
        )
    async def build_event(session, run):
        return _event(date.fromordinal(run))
    class Session:
        async def get(self, model, identity):
            return identity
    monkeypatch.setattr(workflow, "_load_ranking_history", load_history)
    monkeypatch.setattr(workflow, "_adjusted_closes_for_codes", adjusted_closes)
    monkeypatch.setattr(workflow, "build_production_validation_source_event", build_event)
    monkeypatch.setattr(workflow, "_exchange_sessions_between", lambda *args, **kwargs: sessions)
    monkeypatch.setattr(workflow, "_exchange_sessions_through", lambda *args, **kwargs: sessions[25:])
    calculation = await workflow._calculate_ranking_evidence(
        session=Session(), source=history[0].source, manifest=manifest,
        artifact_store=None, timeout_seconds=5,
    )
    sample = calculation.samples_by_candidate[CANDIDATE_DAILY_CORE_TOP10][0]
    assert sample.status == "completed"
    # A loses 50% after the real second target has already exited it.
    assert sample.candidate_net_return > -0.001
    assert len(calculation.source_cohort.events) == 1
    assert momentum_cutoffs == [item.source.replay_visibility_cutoff for item in history]
    ledger = calculation.base_ledgers[CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS]
    result = next(item for item in calculation.endpoint_results if item.candidate_id == CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS)
    assert result.candidate_maximum_drawdown == ledger.net_maximum_drawdown
    # Exercise the real downstream handlers against SQLite and the application
    # database, then forbid recalculation to prove restart-safe sealed reuse.
    from sqlalchemy import func, select
    from test_etf_production_pit_capture import (
        PROVIDER_HEALTH,
        RECEIPT_CUTOFF,
        _seed_complete_snapshot,
    )

    from app.models.entities import EtfFactorExperimentEvidence
    from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
    from app.services.strategy_lab.etf_point_in_time_research_loop import (
        new_research_loop_checkpoint,
    )

    async with app.state.db.session() as session:
        run = await _seed_complete_snapshot(session)
        captured = await workflow.capture_complete_pit_source(
            session, source_signal_run_id=run.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=RECEIPT_CUTOFF,
        )
        assert captured.source is not None
        handler_manifest = workflow.build_production_pit_manifest(
            captured.source, code_version="review", split_contract_hash=_hash("split"),
            holdout_identity_hash=_hash("holdout"), bootstrap_seed=7,
        )
        store = ReplayArtifactStore(tmp_path / "sealed-ranking.sqlite3")
        handlers = workflow.build_production_pit_phase_handlers(
            session=session, source=captured.source, artifact_store=store,
        )
        checkpoint = new_research_loop_checkpoint(handler_manifest)
        async def calculated(**kwargs):
            return calculation
        monkeypatch.setattr(workflow, "_calculate_ranking_evidence", calculated)
        ranked = await handlers.ranking_validation(handler_manifest, checkpoint, 20, 5.0)
        assert ranked.phase_complete is True
        async def must_not_recalculate(**kwargs):
            raise AssertionError("sealed ranking evidence must not be recalculated")
        monkeypatch.setattr(workflow, "_calculate_ranking_evidence", must_not_recalculate)
        monkeypatch.setattr(workflow, "utcnow", lambda: datetime(2030, 1, 1))
        repeated = await handlers.ranking_validation(handler_manifest, checkpoint, 20, 5.0)
        assert repeated.phase_complete is True
        first_factor = await handlers.factor_evidence(handler_manifest, checkpoint, 20, 5.0)
        assert first_factor.phase_complete is True
        second_factor = await handlers.factor_evidence(handler_manifest, checkpoint, 20, 5.0)
        assert second_factor.phase_complete is True
        assert await session.scalar(select(func.count(EtfFactorExperimentEvidence.id))) == 1
        stored = await session.scalar(select(EtfFactorExperimentEvidence))
        assert stored is not None
        assert stored.promotion_state == "insufficient_data"


def test_missing_daily_target_stops_account_after_observable_boundary():
    days = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(8))
    targets = tuple(freeze_ranking_portfolio_target(
        signal_date=day, target_weights={"A": 0.1}, source_hash=_hash(str(day)),
    ) for day in days[:-1] if day != days[2])
    ledger = calculate_continuous_ranking_portfolio(
        trading_sessions=days, targets=targets,
        adjusted_closes=tuple(_close("A", day, 100.0) for day in days),
        required_signal_dates=days[:-1],
    )
    assert ledger.status == "unavailable"
    assert ledger.points[-1].session_date == days[3]
    assert ledger.unavailable_intervals[0].reason == "missing_daily_ranking_target"
    assert ledger.net_return is None


@pytest.mark.asyncio
async def test_history_exclusion_cannot_be_deleted_from_comparison(monkeypatch):
    async def load_history(**kwargs):
        return (SimpleNamespace(),), ("2026-07-02:candidate_page_incomplete",)
    monkeypatch.setattr(workflow, "_load_ranking_history", load_history)
    with pytest.raises(ValueError, match="pit_ranking_history_incomplete"):
        await workflow._calculate_ranking_evidence(
            session=None, source=None, manifest=_manifest(),
            artifact_store=None, timeout_seconds=5,
        )


def test_frozen_plan_drives_real_folds_and_purges_cross_boundary_samples(tmp_path):
    from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
    from app.services.strategy_lab.etf_factor_experiment import ChronologicalSplit

    days = tuple(date(2025, 1, 1) + timedelta(days=index) for index in range(110))
    split = ChronologicalSplit(days[0], days[29], days[30], days[89], days[90], days[-1])
    manifest = _manifest()
    plan = workflow.freeze_production_ranking_validation_plan(
        manifest=manifest, split=split, fold_sessions=20,
        registered_at=datetime(2024, 12, 1), declared_regimes=("risk_on",),
    )
    store = ReplayArtifactStore(tmp_path / "folds.sqlite3")
    assert workflow._load_ranking_validation_plan(store, manifest) is None
    store.write_research_artifacts(
        run_id=workflow._ranking_protocol_key(manifest), phase="validation_plan",
        artifacts=(("frozen-plan", plan),),
    )
    assert workflow._load_ranking_validation_plan(store, manifest) == plan
    samples = [
        {"signal_date": days[index].isoformat(), "entry_session": days[index + 1].isoformat(),
         "exit_session": days[index + 6].isoformat(), "status": "completed",
         "candidate_net_return": 0.02, "baseline_net_return": 0.01}
        for index in range(30, 87, 7)
    ]
    report = workflow._ranking_fold_report(plan=plan, trading_sessions=days, paired_samples=samples)
    assert report["completed_fold_count"] == 3
    assert report["fold_sign_stable"] is True
    assert [item["independent_date_count"] for item in report["folds"]] == [2, 2, 2]
    assert all(len(item["purged_dates"]) == len(item["embargoed_dates"]) == 10 for item in report["folds"])
    missing = workflow._ranking_fold_report(plan=None, trading_sessions=days, paired_samples=samples)
    assert missing["reason"] == "frozen_chronological_split_missing"


def test_holdout_requires_gates_authorization_and_claim_before_result_read(tmp_path, monkeypatch):
    from test_etf_ranking_validation_endpoints import _fixture, _sample

    from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
    from app.services.strategy_lab.etf_factor_experiment import ChronologicalSplit
    from app.services.strategy_lab.etf_point_in_time_research_loop import PromotionGateEvidence
    from app.services.strategy_lab.etf_ranking_forward_outcomes import (
        CONTINUOUS_RANKING_EXECUTION_MODEL,
    )
    from app.services.strategy_lab.etf_ranking_validation import (
        CONTINUOUS_FIVE_SESSION_ENDPOINT_CONTRACT_HASH,
        evaluate_ranking_endpoint,
    )

    manifest = _manifest()
    plan = workflow.freeze_production_ranking_validation_plan(
        manifest=manifest,
        split=ChronologicalSplit(date(2022, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 12, 31), date(2024, 1, 1), date(2025, 12, 31)),
        fold_sessions=40, registered_at=datetime(2021, 12, 1),
    )
    gates = PromotionGateEvidence(1.0, 1.0, 300, 40, 3, 0.01, True, True, 0.01, 0.02)
    store = ReplayArtifactStore(tmp_path / "holdout.sqlite3")
    evidence_hash = _hash("frozen-non-holdout")
    common = dict(artifact_store=store, manifest=manifest, plan=plan,
                  frozen_non_holdout_evidence_hash=evidence_hash, consumed_at=datetime(2026, 1, 2))
    assert workflow._read_authorized_ranking_holdout(**common, non_holdout_gates=replace(gates, eligible_point_in_time_sessions=1))["reason"] == "non_holdout_gates_not_passed"
    assert workflow._read_authorized_ranking_holdout(**common, non_holdout_gates=gates)["reason"] == "frozen_holdout_authorization_missing"
    assert workflow._read_authorized_ranking_holdout(
        **{**common, "consumed_at": datetime(2025, 12, 31, 0, 0)}, non_holdout_gates=gates,
    )["reason"] == "holdout_window_not_mature"
    dates = [(date(2024, 1, 2) + timedelta(days=14 * index)).isoformat() for index in range(40)]
    sessions, events, cohort, registry, contract = _fixture()
    base_endpoint = evaluate_ranking_endpoint(
        source_cohort=cohort, candidate_registry=registry, contract=contract,
        candidate_id=CANDIDATE_DAILY_CORE_TOP10, top_n=10, horizon_sessions=5,
        samples=tuple(_sample(event=event, sessions=sessions) for event in events),
        trading_sessions=sessions,
    )
    endpoint = {
        **asdict(base_endpoint),
        "candidate_id": CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
        "candidate_manifest_hash": next(item.manifest_hash for item in FROZEN_RANKING_CANDIDATES if item.candidate_id == CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS),
        "candidate_registry_hash": manifest.candidate_registry_hash,
        "ranking_source_kind": "production_published", "execution_model": CONTINUOUS_RANKING_EXECUTION_MODEL,
        "endpoint_contract_identity_hash": CONTINUOUS_FIVE_SESSION_ENDPOINT_CONTRACT_HASH,
        "top_n": 10, "horizon_sessions": 5, "independent_dates": dates,
        "accepted_sample_hashes": [_hash(value) for value in dates],
        "sample_gate_passed": True, "coverage_ratio": 1.0,
        "coverage_numerator": 40, "coverage_denominator": 40, "completed_outcome_count": 40,
        "bootstrap_confidence_interval": [0.01, 0.03], "maximum_drawdown_gate_passed": True,
    }
    endpoint.pop("result_hash")
    result = {"plan_hash": plan["plan_hash"], "frozen_non_holdout_evidence_hash": evidence_hash,
              "primary_candidate_id": CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS, "split": plan["split"],
              "outcome_data_cutoff": "2026-01-01T00:00:00",
              "endpoint_result": {**endpoint, "result_hash": _hash(endpoint)}}
    authorization = workflow.freeze_production_holdout_authorization(
        manifest=manifest, plan_hash=plan["plan_hash"], frozen_non_holdout_evidence_hash=evidence_hash,
    )
    store.write_research_artifacts(run_id=workflow._ranking_protocol_key(manifest), phase="holdout_authorization", artifacts=(("frozen-approval", {"authorization": asdict(authorization), "expected_holdout_evidence_hash": _hash(result)}),))
    store.write_research_artifacts(run_id=workflow._ranking_protocol_key(manifest), phase="holdout_result", artifacts=(("frozen-result", result),))
    original_read = store.read_research_artifact_page
    reads = []
    def checked_read(**kwargs):
        reads.append(kwargs["phase"])
        if kwargs["phase"] == "holdout_result":
            assert "holdout_consumption" in reads
        return original_read(**kwargs)
    monkeypatch.setattr(store, "read_research_artifact_page", checked_read)
    monkeypatch.setattr(workflow, "_exchange_sessions_between", lambda start, **kwargs: tuple(start + timedelta(days=offset) for offset in range((kwargs["through_date"] - start).days + 1)))
    first = workflow._read_authorized_ranking_holdout(**common, non_holdout_gates=gates)
    assert first["status"] == "consumed" and first["passed"] is True
    restarted = workflow._read_authorized_ranking_holdout(**{**common, "manifest": replace(manifest, replay_run_key="next-daily-source")}, non_holdout_gates=gates)
    assert restarted["consumption_hash"] == first["consumption_hash"]
    with pytest.raises(ValueError, match="authorization_inputs_changed"):
        workflow._read_authorized_ranking_holdout(**{**common, "frozen_non_holdout_evidence_hash": _hash("different")}, non_holdout_gates=gates)
    next_manifest = replace(manifest, code_version="review-next")
    next_plan = workflow.freeze_production_ranking_validation_plan(
        manifest=next_manifest, split=workflow._plan_split(plan), fold_sessions=40,
        registered_at=datetime(2021, 12, 1),
    )
    next_result = {**result, "plan_hash": next_plan["plan_hash"]}
    next_authorization = workflow.freeze_production_holdout_authorization(
        manifest=next_manifest, plan_hash=next_plan["plan_hash"],
        frozen_non_holdout_evidence_hash=evidence_hash,
    )
    store.write_research_artifacts(
        run_id=workflow._ranking_protocol_key(next_manifest), phase="holdout_authorization",
        artifacts=(("frozen-approval", {"authorization": asdict(next_authorization), "expected_holdout_evidence_hash": _hash(next_result)}),),
    )
    with pytest.raises(ValueError, match="different evidence"):
        workflow._read_authorized_ranking_holdout(
            **{**common, "manifest": next_manifest, "plan": next_plan}, non_holdout_gates=gates,
        )
    import copy
    for failure in ("passed_only", "wrong_hash", "wrong_cost", "overlapping_dates", "before_close"):
        invalid_result = copy.deepcopy(result)
        if failure == "passed_only":
            invalid_result.pop("endpoint_result")
            invalid_result["passed"] = True
        elif failure == "before_close":
            invalid_result["outcome_data_cutoff"] = "2025-12-31T00:00:00+00:00"
        else:
            invalid_endpoint = invalid_result["endpoint_result"]
            if failure == "wrong_hash":
                invalid_endpoint["result_hash"] = _hash("wrong")
            else:
                if failure == "wrong_cost":
                    invalid_endpoint["fee_bps_per_side"] = 0
                else:
                    invalid_endpoint["independent_dates"] = [(date(2024, 1, 2) + timedelta(days=index)).isoformat() for index in range(40)]
                invalid_endpoint.pop("result_hash")
                invalid_endpoint["result_hash"] = _hash(invalid_endpoint)
        invalid_store = ReplayArtifactStore(tmp_path / f"invalid-{failure}.sqlite3")
        invalid_store.write_research_artifacts(
            run_id=workflow._ranking_protocol_key(manifest), phase="holdout_authorization",
            artifacts=(("frozen-approval", {"authorization": asdict(authorization), "expected_holdout_evidence_hash": _hash(invalid_result)}),),
        )
        invalid_store.write_research_artifacts(
            run_id=workflow._ranking_protocol_key(manifest), phase="holdout_result",
            artifacts=(("frozen-result", invalid_result),),
        )
        with pytest.raises(ValueError):
            workflow._read_authorized_ranking_holdout(
                **{**common, "artifact_store": invalid_store}, non_holdout_gates=gates,
            )
