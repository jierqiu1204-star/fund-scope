from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.entities import EtfCatalystEventStudyEvidence
from app.services.etf_catalyst_shadow.event_study import (
    FORBIDDEN_OUTPUT_FIELDS,
    CatalystEventStudyContractError,
    CatalystEventStudyEvidenceConflictError,
    CatalystEventStudyManifest,
    CatalystEventStudySample,
    persist_catalyst_event_study,
    run_catalyst_event_study,
)


def _manifest(
    *,
    event_types: tuple[str, ...] = ("industry_policy",),
) -> CatalystEventStudyManifest:
    return CatalystEventStudyManifest(
        study_id="official-policy-direct-v1",
        code_version="test-sha",
        ranking_contract_hash="r" * 64,
        event_types=event_types,
        bootstrap_resamples=100,
    )


def _exchange_sessions() -> tuple[date, ...]:
    return (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
        date(2026, 1, 12),
        date(2026, 1, 13),
        date(2026, 1, 14),
        date(2026, 1, 15),
        date(2026, 1, 16),
        date(2026, 1, 19),
    )


def _sample(
    index: int,
    *,
    event_type: str = "industry_policy",
    mapping_kind: str = "direct",
    common_support: bool = True,
    decision_eligible: bool = True,
    fold_id: int | None = None,
    slope: float = 0.002,
) -> CatalystEventStudySample:
    entry = 100.0
    closes = tuple(entry * (1.0 + slope * horizon) for horizon in range(11))
    control = tuple(entry * (1.0 + 0.0004 * horizon) for horizon in range(11))
    return CatalystEventStudySample(
        event_id=f"event-{index}",
        event_version=1,
        event_type=event_type,
        mapping_kind=mapping_kind,  # type: ignore[arg-type]
        verification_state="verified",
        snapshot_hash=f"{index:064x}"[-64:],
        event_session=date(2026, 1, 2),
        decision_cutoff=datetime(2026, 1, 2, 15, 20),
        etf_code=f"51{index:04d}",
        control_code=f"56{index:04d}",
        fold_id=fold_id or index % 3 + 1,
        regime="trend" if index % 2 else "range",
        price_basis="total_return_adjusted",
        decision_eligible=decision_eligible,
        common_support=common_support,
        session_dates=_exchange_sessions(),
        adjusted_closes=closes,
        adjusted_highs=tuple(value * 1.003 for value in closes),
        adjusted_lows=tuple(value * 0.997 for value in closes),
        control_adjusted_closes=control,
    )


def test_manifest_freezes_event_study_policy_before_outcomes() -> None:
    manifest = _manifest()
    assert manifest.horizons == (1, 3, 5, 10)
    assert manifest.execution_policy == "next_session_adjusted_close_t_plus_1"
    assert manifest.price_basis == "total_return_adjusted"
    assert manifest.minimum_direct_samples == 30
    assert manifest.minimum_common_support == 0.8
    assert manifest.chronological_folds == 3
    assert manifest.minimum_regimes == 2
    assert manifest.multiplicity_policy == "holm_family_wise"
    assert len(manifest.manifest_hash) == 64
    with pytest.raises(CatalystEventStudyContractError):
        replace(manifest, horizons=(1, 5, 10)).validate()


def test_event_study_reports_direct_and_proxy_separately_with_excursions() -> None:
    samples = tuple(_sample(index) for index in range(30)) + (
        _sample(100, mapping_kind="proxy"),
    )
    result = run_catalyst_event_study(
        _manifest(),
        samples,
        exchange_session_dates=_exchange_sessions(),
    )

    assert result.result_state == "research_only"
    assert [item["cohort_id"] for item in result.cohorts] == [
        "direct:industry_policy",
        "proxy:industry_policy",
    ]
    direct = result.outcomes["direct:industry_policy"]["5"]
    assert direct["observed_return"] > 0
    assert direct["matched_excess_return"] > 0
    assert direct["adverse_excursion"] < 0
    assert direct["favorable_excursion"] > 0
    assert result.intervals["direct:industry_policy"]["confidence"] == pytest.approx(
        0.95
    )
    assert result.outcomes["production_isolation"]["ranking_weight"] == 0
    assert not FORBIDDEN_OUTPUT_FIELDS.intersection(result.outcomes)


def test_ineligible_prices_and_unstable_small_cohorts_fail_closed() -> None:
    samples = tuple(_sample(index) for index in range(8)) + (
        _sample(20, decision_eligible=False),
        _sample(21, common_support=False),
    )
    result = run_catalyst_event_study(
        _manifest(),
        samples,
        exchange_session_dates=_exchange_sessions(),
    )

    assert result.result_state == "insufficient_data"
    assert result.cohorts[0]["state"] == "insufficient_data"
    assert result.cohorts[0]["common_support_coverage"] == pytest.approx(0.8)
    assert {item["reason"] for item in result.exclusions} == {
        "adjusted_price_not_decision_eligible",
        "no_common_support_control",
    }
    assert "direct:industry_policy:insufficient_or_unstable" in result.limitations


@pytest.mark.asyncio
async def test_event_study_evidence_is_immutable_and_idempotent(app) -> None:
    result = run_catalyst_event_study(
        _manifest(),
        tuple(_sample(index) for index in range(30)),
        exchange_session_dates=_exchange_sessions(),
    )
    async with app.state.db.session() as session:
        first = await persist_catalyst_event_study(session, result)
        second = await persist_catalyst_event_study(session, result)
        assert second.id == first.id
        stored = await session.scalar(
            select(EtfCatalystEventStudyEvidence).where(
                EtfCatalystEventStudyEvidence.id == first.id
            )
        )
        assert stored is not None
        stored.result_state = "insufficient_data"
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()
        await session.rollback()

    conflicting = replace(result, limitations=("changed",))
    async with app.state.db.session() as session:
        with pytest.raises(CatalystEventStudyEvidenceConflictError):
            await persist_catalyst_event_study(session, conflicting)


def test_event_study_rejects_shifted_session_paths_and_applies_holm() -> None:
    shifted = replace(
        _sample(999),
        session_dates=_exchange_sessions()[1:] + (date(2026, 1, 20),),
    )
    shifted_result = run_catalyst_event_study(
        _manifest(),
        (shifted,),
        exchange_session_dates=_exchange_sessions(),
    )

    assert shifted_result.exclusions[0]["reason"] == (
        "non_consecutive_exchange_session_path"
    )

    samples = tuple(
        _sample(index, event_type="industry_policy")
        for index in range(30)
    ) + tuple(
        _sample(index + 100, event_type="exchange_notice")
        for index in range(30)
    )
    result = run_catalyst_event_study(
        _manifest(event_types=("industry_policy", "exchange_notice")),
        samples,
        exchange_session_dates=_exchange_sessions(),
    )

    intervals = [
        result.intervals["direct:exchange_notice"],
        result.intervals["direct:industry_policy"],
    ]
    assert all(
        item["multiplicity_method"] == "holm_bonferroni"
        for item in intervals
    )
    assert all(
        item["holm_adjusted_primary_p_value"]
        >= item["raw_primary_p_value"]
        for item in intervals
    )
