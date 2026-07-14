from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.entities import (
    EtfActionValidationImmutableError,
    EtfActionValidationRun,
)
from app.services.strategy_lab import etf_action_policy_validation as validation_module
from app.services.strategy_lab.etf_action_policy_validation import (
    ActionCycleBenefit,
    ActionOutcomeWindow,
    Candidate3Parameters,
    CandidateDefinition,
    CandidateEndpointResult,
    CandidateName,
    CandidateRegistryError,
    EvidenceDimensions,
    FrozenValidationError,
    HoldoutConsumedError,
    SampleGateError,
    SecondarySlice,
    ValidationRunContract,
    build_validation_report,
    candidate3_effective_stop,
    consume_final_holdout,
    evaluate_primary_selection,
    freeze_candidate_registry,
    mark_development_outcomes_calculated,
    purge_boundary_crossing,
    seal_validation_run,
    ten_trading_day_outcome_window,
    trading_day_block_bootstrap_difference,
)
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
    replay_artifact_identity,
)
from app.services.strategy_lab.etf_action_replay.checkpoint import (
    ReplayRunContract,
    build_replay_checkpoint,
)
from app.services.strategy_lab.etf_action_replay.replay import (
    CandidatePortfolioState,
    DailyRankingEvent,
    ReplayState,
)


def _candidate3_parameters() -> Candidate3Parameters:
    return Candidate3Parameters(
        atr_k=2.5,
        atr_warmup_trading_days=20,
        peak_source="adjusted_high",
        trigger_field="adjusted_close",
        recovery_hysteresis_atr=0.5,
        same_day_ohlc_ordering="close_only",
        missing_data_behavior="fail_closed",
        monotonic_stop_invariant=True,
    )


def _candidate_definitions() -> tuple[CandidateDefinition, ...]:
    return (
        CandidateDefinition(
            name=CandidateName.CURRENT_SEMANTICS_BASELINE,
            policy_version="legacy-diagnostic-v1",
            parameters={"target_semantics": "relative", "take_profit_watch": "reduce"},
            promotion_eligible=False,
        ),
        CandidateDefinition(
            name=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
            policy_version="absolute-v2",
            parameters={
                "target_semantics": "absolute",
                "episode_idempotency": True,
                "take_profit_watch": "hold",
            },
            promotion_eligible=True,
        ),
        CandidateDefinition(
            name=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
            policy_version="absolute-monotonic-v3",
            parameters={"base_candidate": "idempotent_absolute_targets"},
            promotion_eligible=True,
        ),
    )


def _registry():
    return freeze_candidate_registry(
        _candidate_definitions(),
        candidate3_parameters=_candidate3_parameters(),
    )


def _replay_contract(registry) -> ReplayRunContract:
    return ReplayRunContract(
        run_id="alert-action-development-replay",
        contract_hash="section9-replay-contract-v1",
        input_snapshot_hash="a" * 64,
        code_hash="section9-code-v1",
        schema_hash="section9-schema-v1",
        candidate_config_hash="section9-candidate-config-v1",
        frozen_parameter_hash=registry.registry_hash,
        policy_input_hash="section9-policy-input-v1",
        data_cutoff=date(2026, 1, 31),
        warmup_boundary=date(2026, 1, 1),
        candidate_ids=(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS.value,
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING.value,
        ),
    )


def _contract(
    *,
    run_key: str = "alert-action-validation-2026q3",
    development_replay_run_contract_hash: str | None = None,
) -> ValidationRunContract:
    if development_replay_run_contract_hash is None:
        development_replay_run_contract_hash = (
            validation_module.replay_run_contract_identity_hash(
                _replay_contract(_registry())
            )
        )
    return ValidationRunContract(
        run_key=run_key,
        policy_version="alert-action-policy-v3",
        h_day_mark_to_market="t_plus_10_adjusted_close",
        counterfactual_cash_handling="cash_zero_return",
        cost_model_version="cn-etf-tax-fee-spread-v1",
        maximum_drawdown_noninferiority_tolerance=0.01,
        minimum_practical_benefit=0.001,
        sample_gate_independent_days=3,
        sample_gate_complete_action_cycles=3,
        sample_gate_minimum_coverage_ratio=0.75,
        bootstrap_seed=20260714,
        bootstrap_resamples=200,
        bootstrap_block_length=2,
        walk_forward_boundaries=(date(2026, 2, 1), date(2026, 3, 1)),
        development_replay_run_contract_hash=(
            development_replay_run_contract_hash
        ),
        development_input_snapshot_hash="a" * 64,
        final_holdout_input_snapshot_hash="b" * 64,
    )


def _sealed_replay_attestation(
    tmp_path: Path,
    *,
    registry,
    contract: ValidationRunContract,
    endpoints: tuple[CandidateEndpointResult, ...],
    purged_samples,
):
    replay_contract = _replay_contract(registry)
    store = ReplayArtifactStore(tmp_path / f"{contract.run_key}.sqlite")
    ranking = DailyRankingEvent(
        run_id=replay_contract.run_id,
        session_date=date(2026, 1, 31),
        ordered_asset_codes=("510300",),
        cross_section_hash="sealed-cross-section",
        manifest_hash="sealed-feature-manifest",
    )
    manifest_hash, output_keys = replay_artifact_identity((ranking,), (), ())
    checkpoint = build_replay_checkpoint(
        contract=replay_contract,
        stage="replay",
        generation=1,
        manifest_hash=manifest_hash,
        state=ReplayState(
            candidate_states={
                candidate_id: CandidatePortfolioState(cash=1.0, equity=1.0)
                for candidate_id in replay_contract.candidate_ids
            }
        ),
        last_completed_unit="2026-01-31",
        output_keys=output_keys,
    )
    store.commit_replay_batch(
        rankings=(ranking,),
        events=(),
        equity_curve=(),
        checkpoint=checkpoint,
        expected_generation=0,
        max_rankings=1,
        max_events=1,
        max_equity_rows=1,
        max_seconds=5.0,
    )
    store.seal_validation_evidence_bundle(
        run_id=replay_contract.run_id,
        expected_contract=replay_contract,
        evidence_bundle_hash=validation_module.development_evidence_bundle_hash(
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purged_samples,
        ),
        max_seconds=5.0,
    )
    return validation_module.attest_development_replay_evidence(
        store=store,
        replay_contract=replay_contract,
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
        max_seconds=5.0,
    )


def _validation_trading_calendar() -> tuple[date, ...]:
    return tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(100))


def test_validation_contract_preregisters_split_and_distinct_snapshot_scopes() -> None:
    parameters = inspect.signature(ValidationRunContract).parameters

    assert "walk_forward_boundaries" in parameters
    assert "development_input_snapshot_hash" in parameters
    assert "final_holdout_input_snapshot_hash" in parameters

    with pytest.raises(FrozenValidationError, match="snapshot scopes must be distinct"):
        replace(
            _contract(),
            final_holdout_input_snapshot_hash="a" * 64,
        )


def test_public_development_apis_do_not_accept_caller_built_selection_or_artifact() -> None:
    assert "selection" not in inspect.signature(
        validation_module.build_development_gate_artifact
    ).parameters
    assert "selection" not in inspect.signature(build_validation_report).parameters
    assert "artifact" not in inspect.signature(
        mark_development_outcomes_calculated
    ).parameters


def test_candidate_registry_is_exactly_frozen_and_rejects_search_or_legacy_promotion() -> None:
    registry = _registry()

    assert tuple(item.name for item in registry.candidates) == tuple(CandidateName)
    assert len(registry.registry_hash) == 64
    candidate3 = registry.by_name(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
    )
    assert candidate3.parameters["atr_k"] == 2.5
    assert candidate3.parameters["monotonic_stop_invariant"] is True

    with pytest.raises(CandidateRegistryError, match="exactly three"):
        freeze_candidate_registry(
            _candidate_definitions()[:2],
            candidate3_parameters=_candidate3_parameters(),
        )
    with pytest.raises(CandidateRegistryError, match="pre-registered"):
        freeze_candidate_registry(
            _candidate_definitions()
            + (
                CandidateDefinition(
                    name="generated_variant",  # type: ignore[arg-type]
                    policy_version="generated",
                    parameters={"atr_k": 3.0},
                    promotion_eligible=True,
                ),
            ),
            candidate3_parameters=_candidate3_parameters(),
        )
    grid = replace(
        _candidate_definitions()[1],
        parameters={"atr_k_grid": [1.5, 2.0, 2.5]},
    )
    with pytest.raises(CandidateRegistryError, match="grid|generated|search"):
        freeze_candidate_registry(
            (_candidate_definitions()[0], grid, _candidate_definitions()[2]),
            candidate3_parameters=_candidate3_parameters(),
        )
    legacy_promoted = replace(_candidate_definitions()[0], promotion_eligible=True)
    with pytest.raises(CandidateRegistryError, match="legacy baseline"):
        freeze_candidate_registry(
            (legacy_promoted, *_candidate_definitions()[1:]),
            candidate3_parameters=_candidate3_parameters(),
        )
    legacy_with_threshold = replace(
        _candidate_definitions()[0],
        parameters={**_candidate_definitions()[0].parameters, "hard_stop_threshold": -0.08},
    )
    absolute_with_changed_threshold = replace(
        _candidate_definitions()[1],
        parameters={**_candidate_definitions()[1].parameters, "hard_stop_threshold": -0.07},
    )
    with pytest.raises(CandidateRegistryError, match="ranking inputs and threshold"):
        freeze_candidate_registry(
            (
                legacy_with_threshold,
                absolute_with_changed_threshold,
                _candidate_definitions()[2],
            ),
            candidate3_parameters=_candidate3_parameters(),
        )

    absolute_with_unregistered_behavior = replace(
        _candidate_definitions()[1],
        parameters={
            **_candidate_definitions()[1].parameters,
            "allocation_cap": 0.75,
        },
    )
    with pytest.raises(CandidateRegistryError, match="only registered semantic"):
        freeze_candidate_registry(
            (
                _candidate_definitions()[0],
                absolute_with_unregistered_behavior,
                _candidate_definitions()[2],
            ),
            candidate3_parameters=_candidate3_parameters(),
        )

    legacy_with_neutral_grid = replace(
        _candidate_definitions()[0],
        parameters={**_candidate_definitions()[0].parameters, "values": [1.0, 2.0]},
    )
    absolute_with_neutral_grid = replace(
        _candidate_definitions()[1],
        parameters={**_candidate_definitions()[1].parameters, "values": [1.0, 2.0]},
    )
    with pytest.raises(CandidateRegistryError, match="grid|search|multi-value"):
        freeze_candidate_registry(
            (
                legacy_with_neutral_grid,
                absolute_with_neutral_grid,
                _candidate_definitions()[2],
            ),
            candidate3_parameters=_candidate3_parameters(),
        )

    candidate3_with_threshold_override = replace(
        _candidate_definitions()[2],
        parameters={
            **_candidate_definitions()[2].parameters,
            "hard_stop_threshold": -0.07,
        },
    )
    with pytest.raises(CandidateRegistryError, match="only extend candidate 2"):
        freeze_candidate_registry(
            (
                _candidate_definitions()[0],
                _candidate_definitions()[1],
                candidate3_with_threshold_override,
            ),
            candidate3_parameters=_candidate3_parameters(),
        )


def test_frozen_candidate_rejects_direct_construction_and_hash_tampering() -> None:
    candidate = _registry().by_name(CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS)

    with pytest.raises(CandidateRegistryError, match="factory|attestation"):
        validation_module.FrozenCandidate(
            name=candidate.name,
            policy_version=candidate.policy_version,
            parameters_json=candidate.parameters_json,
            parameter_hash=candidate.parameter_hash,
            promotion_eligible=candidate.promotion_eligible,
        )
    with pytest.raises(CandidateRegistryError, match="integrity|attestation"):
        replace(candidate, parameter_hash="f" * 64)


def test_frozen_registry_rejects_direct_construction_and_hash_tampering() -> None:
    registry = _registry()

    with pytest.raises(CandidateRegistryError, match="factory|attestation"):
        validation_module.FrozenCandidateRegistry(
            candidates=registry.candidates,
            registry_hash=registry.registry_hash,
        )
    with pytest.raises(CandidateRegistryError, match="integrity|attestation"):
        replace(registry, registry_hash="f" * 64)


def test_frozen_candidate_rejects_recomputed_private_attestation() -> None:
    candidate = _registry().by_name(CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS)
    changed_parameters = {
        **candidate.parameters,
        "unregistered_threshold": 0.123,
    }
    parameters_json = validation_module.json.dumps(
        changed_parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    parameter_hash = validation_module.stable_contract_hash(changed_parameters)
    recomputed_attestation = validation_module._FactoryAttestation(
        validation_module._candidate_attestation_fingerprint(
            name=candidate.name,
            policy_version=candidate.policy_version,
            parameter_hash=parameter_hash,
            promotion_eligible=candidate.promotion_eligible,
        )
    )

    with pytest.raises(CandidateRegistryError, match="factory"):
        validation_module.FrozenCandidate(
            name=candidate.name,
            policy_version=candidate.policy_version,
            parameters_json=parameters_json,
            parameter_hash=parameter_hash,
            promotion_eligible=candidate.promotion_eligible,
            _factory_attestation=recomputed_attestation,
        )


def test_candidate3_contract_has_no_implicit_parameters_and_stop_is_monotonic() -> None:
    with pytest.raises(TypeError):
        Candidate3Parameters(  # type: ignore[call-arg]
            atr_k=2.5,
            atr_warmup_trading_days=20,
            peak_source="adjusted_high",
        )
    with pytest.raises(ValueError, match="fail_closed"):
        replace(_candidate3_parameters(), missing_data_behavior="forward_fill")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="monotonic"):
        replace(_candidate3_parameters(), monotonic_stop_invariant=False)

    parameters = _candidate3_parameters()
    first = candidate3_effective_stop(
        previous_effective_stop=None,
        adjusted_peak=100.0,
        adjusted_atr20=4.0,
        parameters=parameters,
    )
    tightened = candidate3_effective_stop(
        previous_effective_stop=first,
        adjusted_peak=110.0,
        adjusted_atr20=4.0,
        parameters=parameters,
    )
    later_looser_raw_stop = candidate3_effective_stop(
        previous_effective_stop=tightened,
        adjusted_peak=105.0,
        adjusted_atr20=5.0,
        parameters=parameters,
    )
    assert first == 90.0
    assert tightened == 100.0
    assert later_looser_raw_stop == tightened


def test_candidate3_requires_a_complete_atr20_warmup() -> None:
    with pytest.raises(ValueError, match="at least 20"):
        replace(_candidate3_parameters(), atr_warmup_trading_days=19)


def test_candidate3_trailing_evaluator_consumes_the_frozen_execution_contract() -> None:
    parameters = _candidate3_parameters()
    evaluated = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=100.0,
        previous_effective_stop=90.0,
        was_firing=False,
        adjusted_high=110.0,
        adjusted_close=99.0,
        adjusted_low=98.0,
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=parameters,
    )

    assert evaluated.data_eligible is True
    assert evaluated.high_watermark == 110.0
    assert evaluated.effective_stop == 100.0
    assert evaluated.triggered_this_bar is True
    assert evaluated.is_firing is True

    waiting = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=evaluated.high_watermark,
        previous_effective_stop=evaluated.effective_stop,
        was_firing=evaluated.is_firing,
        adjusted_high=None,
        adjusted_close=101.0,
        adjusted_low=98.0,
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=parameters,
    )
    assert waiting.data_eligible is False
    assert waiting.reason == "missing_data_fail_closed"
    assert waiting.high_watermark == evaluated.high_watermark
    assert waiting.effective_stop == evaluated.effective_stop
    assert waiting.is_firing is evaluated.is_firing

    still_firing = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=evaluated.high_watermark,
        previous_effective_stop=evaluated.effective_stop,
        was_firing=True,
        adjusted_high=110.0,
        adjusted_close=101.0,
        adjusted_low=99.0,
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=parameters,
    )
    assert still_firing.is_firing is True
    assert still_firing.recovered_this_bar is False

    recovered = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=evaluated.high_watermark,
        previous_effective_stop=evaluated.effective_stop,
        was_firing=True,
        adjusted_high=110.0,
        adjusted_close=102.0,
        adjusted_low=99.0,
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=parameters,
    )
    assert recovered.recovered_this_bar is True
    assert recovered.is_firing is False


def test_candidate3_evaluator_rejects_unknown_ohlc_order_and_corrupt_prior_state() -> None:
    with pytest.raises(ValueError, match="close_only.*adjusted_close"):
        replace(
            _candidate3_parameters(),
            trigger_field="adjusted_low",
            same_day_ohlc_ordering="close_only",
        )

    with pytest.raises(ValueError, match="previous high watermark"):
        validation_module.evaluate_candidate3_trailing(
            previous_high_watermark=float("nan"),
            previous_effective_stop=90.0,
            was_firing=False,
            adjusted_high=110.0,
            adjusted_close=99.0,
            adjusted_low=98.0,
            adjusted_atr20=4.0,
            atr_observation_count=20,
            parameters=_candidate3_parameters(),
        )

    inverted = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=100.0,
        previous_effective_stop=90.0,
        was_firing=False,
        adjusted_high=98.0,
        adjusted_close=100.0,
        adjusted_low=99.0,
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=_candidate3_parameters(),
    )
    assert inverted.data_eligible is False
    assert inverted.reason == "invalid_ohlc_fail_closed"
    assert inverted.triggered_this_bar is False

    nonfinite_low = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=100.0,
        previous_effective_stop=90.0,
        was_firing=False,
        adjusted_high=110.0,
        adjusted_close=99.0,
        adjusted_low=float("nan"),
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=_candidate3_parameters(),
    )
    assert nonfinite_low.data_eligible is False
    assert nonfinite_low.reason == "missing_data_fail_closed"


def test_candidate3_low_trigger_uses_prior_bar_stop_before_same_day_high_tightening() -> None:
    parameters = replace(
        _candidate3_parameters(),
        trigger_field="adjusted_low",
        same_day_ohlc_ordering="prior_stop_then_low_then_close",
    )

    tightened = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=100.0,
        previous_effective_stop=90.0,
        was_firing=False,
        adjusted_high=120.0,
        adjusted_close=115.0,
        adjusted_low=95.0,
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=parameters,
    )
    assert tightened.effective_stop == 110.0
    assert tightened.triggered_this_bar is False

    triggered_next_bar = validation_module.evaluate_candidate3_trailing(
        previous_high_watermark=tightened.high_watermark,
        previous_effective_stop=tightened.effective_stop,
        was_firing=False,
        adjusted_high=120.0,
        adjusted_close=109.0,
        adjusted_low=108.0,
        adjusted_atr20=4.0,
        atr_observation_count=20,
        parameters=parameters,
    )
    assert triggered_next_bar.triggered_this_bar is True
    assert triggered_next_bar.is_firing is True


def test_walk_forward_purges_every_ten_day_outcome_crossing_a_boundary() -> None:
    assert "contract" in inspect.signature(purge_boundary_crossing).parameters
    assert "boundaries" not in inspect.signature(purge_boundary_crossing).parameters
    trading_days = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(30))
    calculated = ten_trading_day_outcome_window(
        "calendar-derived",
        trading_days[2],
        actual_fill_trading_day=trading_days[2],
        trading_days=trading_days,
    )
    assert calculated.outcome_end_trading_day == trading_days[12]

    calendar = _validation_trading_calendar()
    samples = (
        ten_trading_day_outcome_window(
            "train-kept",
            date(2026, 1, 2),
            actual_fill_trading_day=date(2026, 1, 3),
            trading_days=calendar,
        ),
        ten_trading_day_outcome_window(
            "train-crosses",
            date(2026, 1, 25),
            actual_fill_trading_day=date(2026, 1, 26),
            trading_days=calendar,
        ),
        ten_trading_day_outcome_window(
            "validation-kept",
            date(2026, 2, 2),
            actual_fill_trading_day=date(2026, 2, 3),
            trading_days=calendar,
        ),
        ten_trading_day_outcome_window(
            "validation-crosses",
            date(2026, 2, 20),
            actual_fill_trading_day=date(2026, 2, 21),
            trading_days=calendar,
        ),
        ten_trading_day_outcome_window(
            "holdout",
            date(2026, 3, 2),
            actual_fill_trading_day=date(2026, 3, 3),
            trading_days=calendar,
        ),
    )

    result = purge_boundary_crossing(
        samples,
        contract=_contract(),
        trading_calendar=calendar,
    )

    assert [item.action_cycle_id for item in result.kept] == [
        "train-kept",
        "validation-kept",
        "holdout",
    ]
    assert result.purged_action_cycle_ids == ("train-crosses", "validation-crosses")
    assert result.walk_forward_boundaries_hash == _contract().walk_forward_boundaries_hash
    assert len(result.purged_samples_hash) == 64
    with pytest.raises(FrozenValidationError, match="purge evidence.*integrity"):
        replace(result, purged_action_cycle_ids=())


def test_development_artifact_rejects_purge_evidence_from_changed_boundaries() -> None:
    registry = _registry()
    contract = _contract()
    endpoints, _ = _development_gate_inputs(registry, contract)
    changed_contract = replace(
        contract,
        walk_forward_boundaries=(date(2026, 2, 2), date(2026, 3, 2)),
    )
    _, changed_purge = _development_gate_inputs(registry, changed_contract)

    with pytest.raises(FrozenValidationError, match="frozen split contract"):
        validation_module.build_development_gate_artifact(
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=changed_purge,
        )


def test_development_artifact_rejects_endpoint_marked_purged() -> None:
    registry = _registry()
    contract = replace(
        _contract(),
        walk_forward_boundaries=(date(2026, 1, 10), date(2026, 2, 1)),
    )
    endpoints = (
        _endpoint(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
            development=0.005,
            validation=0.004,
            drawdown=0.08,
            lower=0.001,
            upper=0.007,
            registry=registry,
            contract=contract,
        ),
        _endpoint(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
            development=0.009,
            validation=0.007,
            drawdown=0.085,
            lower=0.003,
            upper=0.011,
            registry=registry,
            contract=contract,
        ),
    )
    calendar = _validation_trading_calendar()
    purge = purge_boundary_crossing(
        tuple(
            ten_trading_day_outcome_window(
                action_cycle_id,
                signal_day,
                actual_fill_trading_day=signal_day,
                trading_days=calendar,
            )
            for action_cycle_id, signal_day in (
                ("a", date(2026, 1, 5)),
                ("b", date(2026, 1, 6)),
                ("c", date(2026, 1, 7)),
            )
        ),
        contract=contract,
        trading_calendar=calendar,
    )

    with pytest.raises(FrozenValidationError, match="purged endpoint"):
        validation_module.build_development_gate_artifact(
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purge,
        )


def test_development_artifact_rejects_final_holdout_endpoint() -> None:
    registry = _registry()
    contract = replace(
        _contract(),
        walk_forward_boundaries=(date(2026, 1, 2), date(2026, 1, 4)),
    )
    endpoints = (
        _endpoint(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
            development=0.005,
            validation=0.004,
            drawdown=0.08,
            lower=0.001,
            upper=0.007,
            registry=registry,
            contract=contract,
        ),
        _endpoint(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
            development=0.009,
            validation=0.007,
            drawdown=0.085,
            lower=0.003,
            upper=0.011,
            registry=registry,
            contract=contract,
        ),
    )
    calendar = _validation_trading_calendar()
    purge = purge_boundary_crossing(
        tuple(
            ten_trading_day_outcome_window(
                action_cycle_id,
                signal_day,
                actual_fill_trading_day=signal_day,
                trading_days=calendar,
            )
            for action_cycle_id, signal_day in (
                ("a", date(2026, 1, 5)),
                ("b", date(2026, 1, 6)),
                ("c", date(2026, 1, 7)),
            )
        ),
        contract=contract,
        trading_calendar=calendar,
    )

    with pytest.raises(FrozenValidationError, match="final holdout"):
        validation_module.build_development_gate_artifact(
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purge,
        )


def test_ten_day_outcome_starts_from_actual_fill_not_signal_day() -> None:
    trading_days = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(30))

    calculated = ten_trading_day_outcome_window(
        "deferred-fill",
        trading_days[2],
        actual_fill_trading_day=trading_days[5],
        trading_days=trading_days,
    )

    assert calculated.actual_fill_trading_day == trading_days[5]
    assert calculated.outcome_end_trading_day == trading_days[15]


def test_purge_rejects_window_not_derived_from_frozen_trading_calendar() -> None:
    trading_days = tuple(
        date(2026, 1, 1) + timedelta(days=index) for index in range(30)
    )
    forged_short_window = ActionOutcomeWindow(
        "forged-short-window",
        trading_days[2],
        trading_days[3],
        trading_days[5],
    )

    with pytest.raises(FrozenValidationError, match="10-trading-day outcome"):
        purge_boundary_crossing(
            (forged_short_window,),
            contract=replace(
                _contract(),
                walk_forward_boundaries=(trading_days[10], trading_days[20]),
            ),
            trading_calendar=trading_days,
        )


def test_purge_evidence_rejects_manual_self_consistent_construction() -> None:
    trading_days = tuple(
        date(2026, 1, 1) + timedelta(days=index) for index in range(30)
    )
    contract = replace(
        _contract(),
        walk_forward_boundaries=(trading_days[20], trading_days[25]),
    )
    result = purge_boundary_crossing(
        (
            ten_trading_day_outcome_window(
                "factory-window",
                trading_days[2],
                actual_fill_trading_day=trading_days[3],
                trading_days=trading_days,
            ),
        ),
        contract=contract,
        trading_calendar=trading_days,
    )

    with pytest.raises(FrozenValidationError, match="factory|integrity"):
        validation_module.PurgedWalkForwardSamples(
            all_samples=result.all_samples,
            kept=result.kept,
            purged_action_cycle_ids=result.purged_action_cycle_ids,
            walk_forward_boundaries=result.walk_forward_boundaries,
            trading_calendar=result.trading_calendar,
            contract_hash=result.contract_hash,
            walk_forward_boundaries_hash=result.walk_forward_boundaries_hash,
            purged_samples_hash=result.purged_samples_hash,
        )


def _benefits(
    candidate: CandidateName,
    values: list[tuple[date, str, float]],
    *,
    registry=None,
):
    registry = registry or _registry()
    frozen_candidate = registry.by_name(candidate)
    return tuple(
        ActionCycleBenefit(
            candidate=candidate,
            registry_hash=registry.registry_hash,
            candidate_parameter_hash=frozen_candidate.parameter_hash,
            signal_trading_day=signal_day,
            experimental_unit_id=action_cycle_id,
            action_cycle_id=action_cycle_id,
            tax_fee_adjusted_benefit=benefit,
            top_n=20,
            horizon_trading_days=10,
        )
        for signal_day, action_cycle_id, benefit in values
    )


def test_bootstrap_clusters_same_day_actions_and_uses_frozen_inference_contract() -> None:
    days = [date(2026, 1, 5) + timedelta(days=index) for index in range(4)]
    registry = _registry()
    baseline_values = [
        (days[0], "a", 0.01),
        (days[0], "b", 0.02),
        (days[1], "c", 0.00),
        (days[2], "d", 0.01),
        (days[3], "e", -0.01),
    ]
    candidate3_values = [(day, cycle, value + 0.003) for day, cycle, value in baseline_values]
    contract = _contract()

    result = trading_day_block_bootstrap_difference(
        _benefits(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
            baseline_values,
            registry=registry,
        ),
        _benefits(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
            candidate3_values,
            registry=registry,
        ),
        registry=registry,
        contract=contract,
        trading_calendar=tuple(days),
        input_snapshot_hash="a" * 64,
    )
    repeated = trading_day_block_bootstrap_difference(
        _benefits(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
            baseline_values,
            registry=registry,
        ),
        _benefits(
            CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
            candidate3_values,
            registry=registry,
        ),
        registry=registry,
        contract=contract,
        trading_calendar=tuple(days),
        input_snapshot_hash="a" * 64,
    )

    assert result == repeated
    assert result.action_cycle_count == 5
    assert result.independent_trading_day_count == 4
    assert result.independent_trading_day_count < result.action_cycle_count
    assert result.seed == contract.bootstrap_seed
    assert result.resamples == contract.bootstrap_resamples
    assert result.block_length == contract.bootstrap_block_length
    assert result.contract_hash == contract.contract_hash
    assert result.input_snapshot_hash == "a" * 64
    assert result.trading_calendar_hash == validation_module.trading_calendar_snapshot_hash(days)
    assert result.point_estimate == pytest.approx(0.003)


def test_bootstrap_pairs_common_experimental_units_not_candidate_cycle_ids() -> None:
    calendar = tuple(date(2026, 1, 5) + timedelta(days=index) for index in range(5))
    registry = _registry()
    candidate2_parameter_hash = registry.by_name(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS
    ).parameter_hash
    candidate3_parameter_hash = registry.by_name(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
    ).parameter_hash
    candidate2 = (
        ActionCycleBenefit(
            candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
            registry_hash=registry.registry_hash,
            candidate_parameter_hash=candidate2_parameter_hash,
            signal_trading_day=calendar[0],
            experimental_unit_id="position-episode-a",
            action_cycle_id="candidate2-cycle-a",
            tax_fee_adjusted_benefit=0.001,
            top_n=20,
            horizon_trading_days=10,
        ),
        ActionCycleBenefit(
            candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
            registry_hash=registry.registry_hash,
            candidate_parameter_hash=candidate2_parameter_hash,
            signal_trading_day=calendar[4],
            experimental_unit_id="position-episode-b",
            action_cycle_id="candidate2-cycle-b",
            tax_fee_adjusted_benefit=0.002,
            top_n=20,
            horizon_trading_days=10,
        ),
    )
    candidate3 = (
        replace(
            candidate2[0],
            candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
            candidate_parameter_hash=candidate3_parameter_hash,
            action_cycle_id="candidate3-cycle-x",
            tax_fee_adjusted_benefit=0.004,
        ),
        replace(
            candidate2[1],
            candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
            candidate_parameter_hash=candidate3_parameter_hash,
            action_cycle_id="candidate3-cycle-y",
            tax_fee_adjusted_benefit=0.005,
        ),
    )

    gapped = trading_day_block_bootstrap_difference(
        candidate2,
        candidate3,
        registry=registry,
        contract=_contract(),
        trading_calendar=calendar,
        input_snapshot_hash="b" * 64,
    )
    compressed = trading_day_block_bootstrap_difference(
        candidate2,
        candidate3,
        registry=registry,
        contract=_contract(),
        trading_calendar=(calendar[0], calendar[4]),
        input_snapshot_hash="b" * 64,
    )

    assert gapped.action_cycle_count == 2
    assert gapped.independent_trading_day_count == 2
    assert gapped.point_estimate == pytest.approx(0.003)
    assert gapped.confidence_interval != compressed.confidence_interval


def test_bootstrap_rejects_an_action_cycle_repeated_on_different_signal_days() -> None:
    registry = _registry()
    candidate2 = _benefits(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        [
            (date(2026, 1, 5), "same-cycle", 0.001),
            (date(2026, 1, 6), "same-cycle", 0.002),
        ],
        registry=registry,
    )
    candidate3 = _benefits(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
        [
            (date(2026, 1, 5), "candidate3-a", 0.003),
            (date(2026, 1, 6), "candidate3-b", 0.004),
        ],
        registry=registry,
    )

    with pytest.raises(FrozenValidationError, match="action cycle.*once"):
        trading_day_block_bootstrap_difference(
            candidate2,
            candidate3,
            registry=registry,
            contract=_contract(),
            trading_calendar=(date(2026, 1, 5), date(2026, 1, 6)),
            input_snapshot_hash="a" * 64,
        )


def _endpoint(
    candidate: CandidateName,
    *,
    development: float,
    validation: float,
    drawdown: float,
    lower: float,
    upper: float,
    registry=None,
    contract=None,
    validation_values: list[tuple[date, str, float]] | None = None,
    coverage_denominator: int = 4,
) -> CandidateEndpointResult:
    registry = registry or _registry()
    contract = contract or _contract()
    del lower, upper
    values = validation_values or [
        (date(2026, 1, 5), "a", validation),
        (date(2026, 1, 6), "b", validation),
        (date(2026, 1, 7), "c", validation),
    ]
    benefits = _benefits(candidate, values, registry=registry)
    completed_ids = tuple(sample.experimental_unit_id for sample in benefits)
    extra_ids = tuple(
        f"eligible-missing-{index}"
        for index in range(coverage_denominator - len(completed_ids))
    )
    raw_evidence = validation_module.CandidateEndpointEvidence(
        development_benefits=(development, development, development),
        validation_benefits=benefits,
        equity_curve=(1.0, 1.0 - drawdown, 1.0),
        eligible_experimental_unit_ids=(*completed_ids, *extra_ids),
        opportunity_costs=(0.004,) * len(benefits),
        trading_calendar=_validation_trading_calendar(),
        input_snapshot_hash="a" * 64,
    )
    return validation_module.derive_candidate_endpoint(
        candidate=candidate,
        registry=registry,
        contract=contract,
        raw_evidence=raw_evidence,
    )


def _development_gate_inputs(registry, contract: ValidationRunContract):
    baseline = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        development=0.005,
        validation=0.004,
        drawdown=0.08,
        lower=0.001,
        upper=0.007,
        registry=registry,
        contract=contract,
    )
    candidate3 = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
        development=0.009,
        validation=0.007,
        drawdown=0.085,
        lower=0.003,
        upper=0.011,
        registry=registry,
        contract=contract,
    )
    calendar = _validation_trading_calendar()
    purged_samples = purge_boundary_crossing(
        tuple(
            ten_trading_day_outcome_window(
                action_cycle_id,
                signal_day,
                actual_fill_trading_day=fill_day,
                trading_days=calendar,
            )
            for action_cycle_id, signal_day, fill_day in (
                ("a", date(2026, 1, 5), date(2026, 1, 6)),
                ("b", date(2026, 1, 6), date(2026, 1, 7)),
                ("c", date(2026, 1, 7), date(2026, 1, 8)),
                (
                    "development-purged",
                    date(2026, 1, 25),
                    date(2026, 1, 26),
                ),
            )
        ),
        contract=contract,
        trading_calendar=calendar,
    )
    return (baseline, candidate3), purged_samples


def _development_gate_artifact(registry, contract: ValidationRunContract):
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    return validation_module.build_development_gate_artifact(
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )


def test_primary_selection_uses_only_fixed_endpoint_drawdown_and_lower_bound() -> None:
    registry = _registry()
    baseline = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        development=0.005,
        validation=0.004,
        drawdown=0.08,
        lower=0.001,
        upper=0.007,
        registry=registry,
    )
    candidate3 = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
        development=0.009,
        validation=0.007,
        drawdown=0.085,
        lower=0.003,
        upper=0.011,
        registry=registry,
    )
    baseline_benefits = baseline.raw_evidence.validation_benefits
    candidate3_benefits = candidate3.raw_evidence.validation_benefits
    decision = evaluate_primary_selection(
        baseline,
        candidate3,
        registry=registry,
        candidate2_benefits=baseline_benefits,
        candidate3_benefits=candidate3_benefits,
        trading_calendar=_validation_trading_calendar(),
        input_snapshot_hash="a" * 64,
        contract=_contract(),
    )

    assert decision.review_candidate is CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING
    assert decision.maximum_drawdown_gate_passed is True
    assert decision.minimum_practical_benefit_gate_passed is True
    assert decision.promotion_status == "manual_review_required"
    assert decision.auto_promoted is False

    failed_baseline = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        development=0.005,
        validation=0.0,
        drawdown=0.08,
        lower=0.0,
        upper=0.0,
        registry=registry,
        validation_values=[
            (date(2026, 1, 5), "a", 0.0),
            (date(2026, 1, 6), "b", 0.0),
            (date(2026, 1, 7), "c", 0.0),
        ],
    )
    failed_candidate3 = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
        development=0.009,
        validation=0.003,
        drawdown=0.085,
        lower=0.0,
        upper=0.0,
        registry=registry,
        validation_values=[
            (date(2026, 1, 5), "a", -0.01),
            (date(2026, 1, 6), "b", 0.009),
            (date(2026, 1, 7), "c", 0.01),
        ],
    )
    failed_lower_bound = evaluate_primary_selection(
        failed_baseline,
        failed_candidate3,
        registry=registry,
        candidate2_benefits=failed_baseline.raw_evidence.validation_benefits,
        candidate3_benefits=failed_candidate3.raw_evidence.validation_benefits,
        trading_calendar=_validation_trading_calendar(),
        input_snapshot_hash="a" * 64,
        contract=_contract(),
    )
    assert failed_lower_bound.review_candidate is CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS
    assert failed_lower_bound.minimum_practical_benefit_gate_passed is False
    assert failed_lower_bound.auto_promoted is False

    legacy = replace(baseline, candidate=CandidateName.CURRENT_SEMANTICS_BASELINE)
    with pytest.raises(FrozenValidationError, match="legacy"):
        evaluate_primary_selection(
            legacy,
            candidate3,
            registry=registry,
            candidate2_benefits=baseline_benefits,
            candidate3_benefits=candidate3_benefits,
            trading_calendar=_validation_trading_calendar(),
            input_snapshot_hash="a" * 64,
            contract=_contract(),
        )


def test_public_selector_does_not_accept_a_caller_constructed_bootstrap_result() -> None:
    assert "improvement" not in inspect.signature(evaluate_primary_selection).parameters
    assert "registry" in inspect.signature(evaluate_primary_selection).parameters


def test_selector_rejects_same_candidate_name_with_a_changed_parameter_hash() -> None:
    registry = _registry()
    baseline = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        development=0.005,
        validation=0.004,
        drawdown=0.08,
        lower=0.001,
        upper=0.007,
        registry=registry,
    )
    candidate3 = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
        development=0.009,
        validation=0.007,
        drawdown=0.085,
        lower=0.003,
        upper=0.011,
        registry=registry,
    )
    baseline_benefits = baseline.raw_evidence.validation_benefits
    candidate3_benefits = candidate3.raw_evidence.validation_benefits
    forged = (
        replace(baseline_benefits[0], candidate_parameter_hash="f" * 64),
        *baseline_benefits[1:],
    )

    with pytest.raises(
        FrozenValidationError,
        match="raw selector evidence|candidate parameter hash",
    ):
        evaluate_primary_selection(
            baseline,
            candidate3,
            registry=registry,
            candidate2_benefits=forged,
            candidate3_benefits=candidate3_benefits,
            trading_calendar=(date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
            input_snapshot_hash="a" * 64,
            contract=_contract(),
        )

    with pytest.raises(
        FrozenValidationError,
        match="derived endpoint|endpoint candidate parameter hash",
    ):
        evaluate_primary_selection(
            replace(baseline, candidate_parameter_hash="e" * 64),
            candidate3,
            registry=registry,
            candidate2_benefits=baseline_benefits,
            candidate3_benefits=candidate3_benefits,
            trading_calendar=(date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
            input_snapshot_hash="a" * 64,
            contract=_contract(),
        )


def _aggregate_forgery_fixture():
    registry = _registry()
    contract = _contract()
    baseline = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        development=0.005,
        validation=0.004,
        drawdown=0.08,
        lower=0.001,
        upper=0.007,
        registry=registry,
    )
    candidate3 = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
        development=0.009,
        validation=0.007,
        drawdown=0.085,
        lower=0.003,
        upper=0.011,
        registry=registry,
    )
    baseline_benefits = baseline.raw_evidence.validation_benefits
    candidate3_benefits = candidate3.raw_evidence.validation_benefits
    return registry, contract, baseline, candidate3, baseline_benefits, candidate3_benefits


def test_selector_rejects_endpoint_means_shifted_away_from_raw_benefits() -> None:
    registry, contract, baseline, candidate3, baseline_benefits, candidate3_benefits = (
        _aggregate_forgery_fixture()
    )

    with pytest.raises(FrozenValidationError, match="raw endpoint evidence|derived endpoint"):
        evaluate_primary_selection(
            replace(baseline, validation_mean_benefit=baseline.validation_mean_benefit + 0.1),
            replace(candidate3, validation_mean_benefit=candidate3.validation_mean_benefit + 0.1),
            registry=registry,
            candidate2_benefits=baseline_benefits,
            candidate3_benefits=candidate3_benefits,
            trading_calendar=(date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
            input_snapshot_hash="a" * 64,
            contract=contract,
        )


def test_selector_rejects_endpoint_drawdown_not_derived_from_equity_curve() -> None:
    registry, contract, baseline, candidate3, baseline_benefits, candidate3_benefits = (
        _aggregate_forgery_fixture()
    )
    forged = replace(replace(candidate3, maximum_drawdown=0.2), maximum_drawdown=0.01)

    with pytest.raises(FrozenValidationError, match="raw endpoint evidence|derived endpoint"):
        evaluate_primary_selection(
            baseline,
            forged,
            registry=registry,
            candidate2_benefits=baseline_benefits,
            candidate3_benefits=candidate3_benefits,
            trading_calendar=(date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
            input_snapshot_hash="a" * 64,
            contract=contract,
        )


def test_selector_rejects_endpoint_coverage_not_derived_from_eligible_cycles() -> None:
    registry, contract, baseline, candidate3, baseline_benefits, candidate3_benefits = (
        _aggregate_forgery_fixture()
    )
    forged_baseline = replace(replace(baseline, coverage_denominator=5), coverage_denominator=3)
    forged_candidate3 = replace(
        replace(candidate3, coverage_denominator=5), coverage_denominator=3
    )

    with pytest.raises(FrozenValidationError, match="raw endpoint evidence|derived endpoint"):
        evaluate_primary_selection(
            forged_baseline,
            forged_candidate3,
            registry=registry,
            candidate2_benefits=baseline_benefits,
            candidate3_benefits=candidate3_benefits,
            trading_calendar=(date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
            input_snapshot_hash="a" * 64,
            contract=contract,
        )


def test_report_rejects_forged_endpoint_ci_and_opportunity_cost() -> None:
    registry, contract, baseline, candidate3, baseline_benefits, candidate3_benefits = (
        _aggregate_forgery_fixture()
    )
    del baseline_benefits, candidate3_benefits
    forged = replace(baseline, confidence_interval=(0.5, 0.6), opportunity_cost=-99.0)
    evidence = EvidenceDimensions(
        contract_compatibility=validation_module.ContractCompatibility.SAME_CONTRACT,
        data_eligibility=validation_module.DataEligibility.DECISION_ELIGIBLE_ADJUSTED,
        execution_provenance=validation_module.ExecutionProvenance.SIMULATED_EXECUTION,
    )

    with pytest.raises(FrozenValidationError, match="raw endpoint evidence|derived endpoint"):
        build_validation_report(
            registry=registry,
            contract=contract,
            endpoints=(forged, candidate3),
            evidence_by_candidate={forged.candidate: evidence, candidate3.candidate: evidence},
            secondary_slices=(),
        )


def test_evidence_dimensions_and_report_remain_separate_and_research_only() -> None:
    registry = _registry()
    evidence = EvidenceDimensions(
        contract_compatibility=validation_module.ContractCompatibility.SAME_CONTRACT,
        data_eligibility=validation_module.DataEligibility.DECISION_ELIGIBLE_ADJUSTED,
        execution_provenance=validation_module.ExecutionProvenance.SIMULATED_EXECUTION,
    )
    baseline = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        development=0.006,
        validation=0.003,
        drawdown=0.08,
        lower=0.001,
        upper=0.005,
        registry=registry,
    )
    candidate3 = _endpoint(
        CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS_WITH_MONOTONIC_TRAILING,
        development=0.008,
        validation=0.004,
        drawdown=0.081,
        lower=0.002,
        upper=0.006,
        registry=registry,
    )
    baseline_benefits = baseline.raw_evidence.validation_benefits
    candidate3_benefits = candidate3.raw_evidence.validation_benefits
    improvement = trading_day_block_bootstrap_difference(
        baseline_benefits,
        candidate3_benefits,
        registry=registry,
        contract=_contract(),
        trading_calendar=_validation_trading_calendar(),
        input_snapshot_hash="a" * 64,
    )
    decision = evaluate_primary_selection(
        baseline,
        candidate3,
        registry=registry,
        candidate2_benefits=baseline_benefits,
        candidate3_benefits=candidate3_benefits,
        trading_calendar=_validation_trading_calendar(),
        input_snapshot_hash="a" * 64,
        contract=_contract(),
    )
    report = build_validation_report(
        registry=registry,
        contract=_contract(),
        endpoints=(baseline, candidate3),
        evidence_by_candidate={baseline.candidate: evidence, candidate3.candidate: evidence},
        secondary_slices=(
            SecondarySlice(candidate=candidate3.candidate, top_n=3, horizon_trading_days=10, value=0.02),
            SecondarySlice(candidate=candidate3.candidate, top_n=20, horizon_trading_days=5, value=0.01),
        ),
    )

    payload = report.as_dict()
    assert payload["primary_endpoint"] == "top20_10_trading_day_tax_fee_adjusted_mean_action_cycle_benefit"
    assert payload["auto_promoted"] is False
    assert payload["selection"]["clustered_improvement_confidence_interval"] == list(
        improvement.confidence_interval
    )
    assert payload["selection"]["minimum_practical_benefit"] == 0.001
    assert payload["candidates"][0]["sample_gate"]["required_independent_days"] == 3
    assert payload["candidates"][0]["confidence_interval"] == list(
        baseline.confidence_interval
    )
    assert payload["candidates"][0]["validation_degradation"] == pytest.approx(-0.003)
    assert payload["candidates"][0]["opportunity_cost"] == 0.004
    assert payload["candidates"][0]["evidence"] == {
        "contract_compatibility": "same_contract",
        "data_eligibility": "decision_eligible_adjusted",
        "execution_provenance": "simulated_execution",
        "sample_sufficiency": "sufficient",
        "promotion_status": "manual_review_required",
    }
    assert {item["role"] for item in payload["secondary_slices"]} == {"secondary"}

    with pytest.raises(FrozenValidationError, match="duplicate candidate"):
        build_validation_report(
            registry=registry,
            contract=_contract(),
            endpoints=(baseline, baseline, candidate3),
            evidence_by_candidate={
                baseline.candidate: evidence,
                candidate3.candidate: evidence,
            },
            secondary_slices=(),
        )

    assert report.selection == decision


def test_evidence_dimensions_reject_unregistered_free_text() -> None:
    with pytest.raises(ValueError, match="registered evidence"):
        EvidenceDimensions(
            contract_compatibility="looks_good",
            data_eligibility="probably_fresh",
            execution_provenance="trust_me",
        )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_development_persistence_rejects_unattested_replay_evidence(app) -> None:
    registry = _registry()
    contract = _contract(run_key="unattested-replay-evidence")
    endpoints, purged_samples = _development_gate_inputs(registry, contract)

    async with app.state.db.session() as session:
        await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=datetime(2026, 7, 14, 9, 0),
        )
        with pytest.raises(FrozenValidationError, match="sealed replay artifact"):
            await mark_development_outcomes_calculated(
                session,
                run_key=contract.run_key,
                registry=registry,
                contract=contract,
                endpoints=endpoints,
                purged_samples=purged_samples,
                calculated_at=datetime(2026, 7, 14, 10, 0),
            )


@pytest.mark.asyncio
async def test_sealed_replay_attestation_rejects_changed_benefits(
    app, tmp_path: Path
) -> None:
    registry = _registry()
    contract = _contract(run_key="changed-benefits-after-replay-seal")
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    replay_attestation = _sealed_replay_attestation(
        tmp_path,
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    changed_raw = replace(
        endpoints[0].raw_evidence,
        development_benefits=(0.99, 0.99, 0.99),
    )
    changed_endpoint = validation_module.derive_candidate_endpoint(
        candidate=endpoints[0].candidate,
        registry=registry,
        contract=contract,
        raw_evidence=changed_raw,
    )
    with pytest.raises(FrozenValidationError, match="sealed replay artifact"):
        validation_module.attest_development_replay_evidence(
            store=ReplayArtifactStore(tmp_path / f"{contract.run_key}.sqlite"),
            replay_contract=_replay_contract(registry),
            registry=registry,
            contract=contract,
            endpoints=(changed_endpoint, endpoints[1]),
            purged_samples=purged_samples,
            max_seconds=5.0,
        )

    async with app.state.db.session() as session:
        with pytest.raises(FrozenValidationError, match="does not match development evidence"):
            await mark_development_outcomes_calculated(
                session,
                run_key=contract.run_key,
                registry=registry,
                contract=contract,
                endpoints=(changed_endpoint, endpoints[1]),
                purged_samples=purged_samples,
                calculated_at=datetime(2026, 7, 14, 10, 0),
                replay_attestation=replay_attestation,
            )


@pytest.mark.asyncio
async def test_registry_and_contract_are_persisted_before_outcomes(
    app, tmp_path: Path
) -> None:
    registry = _registry()
    contract = _contract(run_key="persist-before-outcomes")
    sealed_at = datetime(2026, 7, 14, 9, 0)

    async with app.state.db.session() as session:
        row = await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=sealed_at,
        )
        assert row.id is not None
        assert row.sealed_at == sealed_at
        assert row.candidate_registry_hash == registry.registry_hash
        assert row.validation_contract_hash == contract.contract_hash
        assert row.development_outcomes_calculated_at is None
        await session.commit()

    calculated_at = datetime(2026, 7, 14, 10, 0)
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    replay_attestation = _sealed_replay_attestation(
        tmp_path,
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    artifact = _development_gate_artifact(registry, contract)
    artifact_payload = artifact.as_dict()
    artifact_payload["replay_evidence_attestation_hash"] = (
        replay_attestation.attestation_hash
    )
    artifact_payload["replay_evidence_attestation"] = replay_attestation.as_dict()
    async with app.state.db.session() as session:
        row = await mark_development_outcomes_calculated(
            session,
            run_key=contract.run_key,
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purged_samples,
            calculated_at=calculated_at,
            replay_attestation=replay_attestation,
        )
        await session.commit()
        assert row.development_outcomes_calculated_at == calculated_at
        assert row.development_gate_artifact_json == artifact_payload
        assert row.development_gate_artifact_hash == validation_module.stable_contract_hash(
            artifact_payload
        )

    async with app.state.db.session() as session:
        stored = await session.get(EtfActionValidationRun, row.id)
        assert stored is not None
        assert stored.candidate_registry_json == registry.as_dict()
        assert stored.validation_contract_json == contract.as_dict()
        stored.candidate_registry_hash = "f" * 64
        with pytest.raises(EtfActionValidationImmutableError):
            await session.flush()
        await session.rollback()

    changed_contract = replace(contract, minimum_practical_benefit=0.002)
    async with app.state.db.session() as session:
        with pytest.raises(FrozenValidationError, match="changed"):
            await seal_validation_run(
                session,
                registry=registry,
                contract=changed_contract,
                sealed_at=sealed_at,
            )


@pytest.mark.asyncio
async def test_seal_validation_run_converges_after_a_concurrent_first_insert() -> None:
    registry = _registry()
    contract = _contract(run_key="concurrent-first-seal")
    existing = EtfActionValidationRun(
        run_key=contract.run_key,
        policy_version=contract.policy_version,
        candidate_registry_json=registry.as_dict(),
        candidate_registry_hash=registry.registry_hash,
        validation_contract_json=contract.as_dict(),
        validation_contract_hash=contract.contract_hash,
        sealed_at=datetime(2026, 7, 14, 9, 0),
    )

    class _Nested:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _ConcurrentSession:
        def __init__(self):
            self.scalar_calls = 0

        async def scalar(self, _statement):
            self.scalar_calls += 1
            return None if self.scalar_calls == 1 else existing

        def begin_nested(self):
            return _Nested()

        def add(self, _row):
            return None

        async def flush(self):
            raise IntegrityError("insert", {}, Exception("duplicate run_key"))

    session = _ConcurrentSession()
    resolved = await seal_validation_run(  # type: ignore[arg-type]
        session,
        registry=registry,
        contract=contract,
        sealed_at=datetime(2026, 7, 14, 9, 0),
    )

    assert resolved is existing
    assert session.scalar_calls == 2


@pytest.mark.asyncio
async def test_holdout_is_consumed_once_and_rejects_changed_or_repeated_fresh_use(
    app, tmp_path: Path
) -> None:
    registry = _registry()
    contract = _contract(run_key="one-time-holdout")
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    replay_attestation = _sealed_replay_attestation(
        tmp_path,
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    async with app.state.db.session() as session:
        await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=datetime(2026, 7, 14, 9, 0),
        )
        await mark_development_outcomes_calculated(
            session,
            run_key=contract.run_key,
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purged_samples,
            calculated_at=datetime(2026, 7, 14, 10, 0),
            replay_attestation=replay_attestation,
        )
        await session.commit()

    async with app.state.db.session() as session:
        with pytest.raises(FrozenValidationError, match="caller gate.*persisted"):
            await consume_final_holdout(
                session,
                run_key=contract.run_key,
                registry_hash=registry.registry_hash,
                contract_hash=contract.contract_hash,
                input_snapshot_hash="b" * 64,
                gates_passed=False,
                consumed_at=datetime(2026, 7, 14, 11, 0),
            )
        await session.rollback()

    async with app.state.db.session() as session:
        consumed = await consume_final_holdout(
            session,
            run_key=contract.run_key,
            registry_hash=registry.registry_hash,
            contract_hash=contract.contract_hash,
            input_snapshot_hash="b" * 64,
            gates_passed=True,
            consumed_at=datetime(2026, 7, 14, 11, 0),
        )
        await session.commit()
        assert consumed.holdout_first_consumed_at == datetime(2026, 7, 14, 11, 0)
        assert consumed.holdout_input_snapshot_hash == "b" * 64

    async with app.state.db.session() as session:
        with pytest.raises(HoldoutConsumedError, match="already consumed"):
            await consume_final_holdout(
                session,
                run_key=contract.run_key,
                registry_hash=registry.registry_hash,
                contract_hash=contract.contract_hash,
                input_snapshot_hash="b" * 64,
                gates_passed=True,
                consumed_at=datetime(2026, 7, 14, 12, 0),
            )


@pytest.mark.asyncio
async def test_final_holdout_rejects_the_development_input_snapshot(
    app, tmp_path: Path
) -> None:
    registry = _registry()
    contract = _contract(run_key="holdout-snapshot-separation")
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    replay_attestation = _sealed_replay_attestation(
        tmp_path,
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    async with app.state.db.session() as session:
        await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=datetime(2026, 7, 14, 9, 0),
        )
        await mark_development_outcomes_calculated(
            session,
            run_key=contract.run_key,
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purged_samples,
            calculated_at=datetime(2026, 7, 14, 10, 0),
            replay_attestation=replay_attestation,
        )
        await session.commit()

    async with app.state.db.session() as session:
        with pytest.raises(FrozenValidationError, match="pre-registered holdout snapshot"):
            await consume_final_holdout(
                session,
                run_key=contract.run_key,
                registry_hash=registry.registry_hash,
                contract_hash=contract.contract_hash,
                input_snapshot_hash=contract.development_input_snapshot_hash,
                gates_passed=True,
                consumed_at=datetime(2026, 7, 14, 11, 0),
            )


@pytest.mark.asyncio
async def test_caller_built_holdout_ready_artifact_cannot_be_persisted(app) -> None:
    registry = _registry()
    contract = replace(
        _contract(run_key="forged-development-artifact"),
        sample_gate_complete_action_cycles=4,
    )
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    authentic = _development_gate_artifact(registry, contract)
    assert authentic.holdout_ready is False
    forged = replace(authentic, holdout_ready=True, sample_gate_passed=True)

    async with app.state.db.session() as session:
        row = await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=datetime(2026, 7, 14, 9, 0),
        )
        await session.commit()
        row_id = row.id

    async with app.state.db.session() as session:
        with pytest.raises(TypeError, match="artifact"):
            await mark_development_outcomes_calculated(
                session,
                run_key=contract.run_key,
                registry=registry,
                contract=contract,
                endpoints=endpoints,
                purged_samples=purged_samples,
                artifact=forged,  # type: ignore[call-arg]
                calculated_at=datetime(2026, 7, 14, 10, 0),
            )
        stored = await session.get(EtfActionValidationRun, row_id)
        assert stored is not None
        assert stored.development_outcomes_calculated_at is None
        assert stored.development_gate_artifact_json is None


@pytest.mark.asyncio
async def test_direct_orm_write_cannot_create_development_gate_marker(app) -> None:
    registry = _registry()
    contract = _contract(run_key="direct-forged-development-marker")
    artifact = _development_gate_artifact(registry, contract)
    async with app.state.db.session() as session:
        row = await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=datetime(2026, 7, 14, 9, 0),
        )
        await session.commit()
        row_id = row.id

    async with app.state.db.session() as session:
        stored = await session.get(EtfActionValidationRun, row_id)
        assert stored is not None
        stored.development_outcomes_calculated_at = datetime(2026, 7, 14, 10, 0)
        stored.development_gate_artifact_json = artifact.as_dict()
        stored.development_gate_artifact_hash = artifact.artifact_hash
        with pytest.raises(EtfActionValidationImmutableError, match="write-once"):
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate_suffix", "contract_changes"),
    (
        ("cycles", {"sample_gate_complete_action_cycles": 4}),
        ("coverage", {"sample_gate_minimum_coverage_ratio": 0.8}),
    ),
)
async def test_caller_cannot_override_a_failed_persisted_development_gate(
    app,
    tmp_path: Path,
    gate_suffix: str,
    contract_changes: dict[str, float | int],
) -> None:
    registry = _registry()
    contract = replace(
        _contract(run_key=f"failed-development-gate-{gate_suffix}"),
        **contract_changes,
    )
    artifact = _development_gate_artifact(registry, contract)
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    replay_attestation = _sealed_replay_attestation(
        tmp_path,
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    assert artifact.holdout_ready is False
    assert artifact.coverage_denominator == 4
    assert artifact.input_snapshot_hash == "a" * 64

    async with app.state.db.session() as session:
        row = await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=datetime(2026, 7, 14, 9, 0),
        )
        await mark_development_outcomes_calculated(
            session,
            run_key=contract.run_key,
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purged_samples,
            calculated_at=datetime(2026, 7, 14, 10, 0),
            replay_attestation=replay_attestation,
        )
        await session.commit()
        row_id = row.id

    async with app.state.db.session() as session:
        stored = await session.get(EtfActionValidationRun, row_id)
        assert stored is not None
        stored.holdout_first_consumed_at = datetime(2026, 7, 14, 10, 30)
        stored.holdout_input_snapshot_hash = "b" * 64
        with pytest.raises(EtfActionValidationImmutableError, match="development gate"):
            await session.flush()
        await session.rollback()

    async with app.state.db.session() as session:
        with pytest.raises(SampleGateError, match="persisted development gate"):
            await consume_final_holdout(
                session,
                run_key=contract.run_key,
                registry_hash=registry.registry_hash,
                contract_hash=contract.contract_hash,
                input_snapshot_hash="b" * 64,
                gates_passed=True,
                consumed_at=datetime(2026, 7, 14, 11, 0),
            )


@pytest.mark.asyncio
async def test_development_and_holdout_markers_are_write_once(
    app, tmp_path: Path
) -> None:
    registry = _registry()
    contract = _contract(run_key="write-once-validation-markers")
    endpoints, purged_samples = _development_gate_inputs(registry, contract)
    replay_attestation = _sealed_replay_attestation(
        tmp_path,
        registry=registry,
        contract=contract,
        endpoints=endpoints,
        purged_samples=purged_samples,
    )
    async with app.state.db.session() as session:
        row = await seal_validation_run(
            session,
            registry=registry,
            contract=contract,
            sealed_at=datetime(2026, 7, 14, 9, 0),
        )
        await mark_development_outcomes_calculated(
            session,
            run_key=contract.run_key,
            registry=registry,
            contract=contract,
            endpoints=endpoints,
            purged_samples=purged_samples,
            calculated_at=datetime(2026, 7, 14, 10, 0),
            replay_attestation=replay_attestation,
        )
        row = await consume_final_holdout(
            session,
            run_key=contract.run_key,
            registry_hash=registry.registry_hash,
            contract_hash=contract.contract_hash,
            input_snapshot_hash=contract.final_holdout_input_snapshot_hash,
            gates_passed=True,
            consumed_at=datetime(2026, 7, 14, 11, 0),
        )
        await session.commit()
        row_id = row.id

    async with app.state.db.session() as session:
        stored = await session.get(EtfActionValidationRun, row_id)
        assert stored is not None
        stored.development_gate_artifact_hash = "d" * 64
        with pytest.raises(EtfActionValidationImmutableError, match="write-once"):
            await session.flush()
        await session.rollback()

    async with app.state.db.session() as session:
        stored = await session.get(EtfActionValidationRun, row_id)
        assert stored is not None
        stored.holdout_first_consumed_at = None
        with pytest.raises(EtfActionValidationImmutableError, match="write-once"):
            await session.flush()
        await session.rollback()

    async with app.state.db.session() as session:
        with pytest.raises(FrozenValidationError, match="candidate registry"):
            await consume_final_holdout(
                session,
                run_key=contract.run_key,
                registry_hash="b" * 64,
                contract_hash=contract.contract_hash,
                input_snapshot_hash="c" * 64,
                gates_passed=True,
                consumed_at=datetime(2026, 7, 14, 12, 0),
            )
