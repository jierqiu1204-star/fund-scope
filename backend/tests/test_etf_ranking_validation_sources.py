from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app.services.etf_research_evidence import RankingSourceKind
from app.services.strategy_lab.etf_ranking_validation import (
    RankingScoreObservation,
    RankingValidationContractError,
    RankingValidationSourceEvent,
    freeze_ranking_validation_source_cohort,
    validate_score_bucket_observations,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _event_payload(event: RankingValidationSourceEvent) -> dict[str, object]:
    payload = dict(event.__dict__)
    payload.pop("immutable_hash")
    return payload


def _event(
    source_kind: RankingSourceKind,
    signal_date: date,
) -> RankingValidationSourceEvent:
    research = source_kind is RankingSourceKind.RESEARCH_REPLAY
    draft = RankingValidationSourceEvent(
        ranking_source_kind=source_kind,
        signal_date=signal_date,
        source_signal_run_id=None if research else int(signal_date.strftime("%m%d")),
        source_replay_run_key="research-run-1" if research else None,
        source_replay_contract_hash=_hash("replay-contract") if research else None,
        source_event_hash=_hash(f"event:{source_kind.value}:{signal_date}"),
        ranking_contract_hash=_hash(
            "daily-reconstructable" if research else "final-score-v3"
        ),
        universe_snapshot_hash=_hash(f"universe:{source_kind.value}:{signal_date}"),
        input_snapshot_hash=_hash(f"input:{source_kind.value}:{signal_date}"),
        score_version="daily_reconstructable_v1" if research else "final_score_v3",
        score_field="research_score" if research else "ranking_score",
        price_basis="total_return_adjusted",
        publication_state=None if research else "published",
        scope_kind="research_replay" if research else "full",
        availability_cutoff=datetime(
            signal_date.year,
            signal_date.month,
            signal_date.day,
            15,
            30,
            tzinfo=UTC,
        ),
        immutable_hash="pending",
    )
    return replace(
        draft,
        immutable_hash=stable_contract_hash(_event_payload(draft)),
    )


def _score_payload(row: RankingScoreObservation) -> dict[str, object]:
    payload = dict(row.__dict__)
    payload.pop("observation_hash")
    return payload


def _score(
    event: RankingValidationSourceEvent,
    code: str,
    *,
    declared_score: float | None,
    declared_score_field: str | None = None,
    score_eligible: bool = True,
    legacy_total_score: float | None = None,
) -> RankingScoreObservation:
    draft = RankingScoreObservation(
        source_event_hash=event.source_event_hash,
        asset_code=code,
        declared_score=declared_score,
        declared_score_field=declared_score_field or event.score_field,
        score_eligible=score_eligible,
        legacy_total_score=legacy_total_score,
        observation_hash="pending",
    )
    return replace(
        draft,
        observation_hash=stable_contract_hash(_score_payload(draft)),
    )


def test_exact_production_and_research_sources_are_frozen_separately() -> None:
    days = (date(2026, 4, 1), date(2026, 4, 2))
    production = freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.PRODUCTION_PUBLISHED,
        events=tuple(_event(RankingSourceKind.PRODUCTION_PUBLISHED, day) for day in days),
    )
    research = freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        events=tuple(_event(RankingSourceKind.RESEARCH_REPLAY, day) for day in days),
    )

    assert production.ranking_source_kind is RankingSourceKind.PRODUCTION_PUBLISHED
    assert production.source_replay_run_key is None
    assert production.score_field == "ranking_score"
    assert research.ranking_source_kind is RankingSourceKind.RESEARCH_REPLAY
    assert research.source_replay_run_key == "research-run-1"
    assert research.score_field == "research_score"
    assert production.independent_dates == research.independent_dates == days
    assert production.cohort_hash != research.cohort_hash

    with pytest.raises(RankingValidationContractError, match="cannot be merged"):
        freeze_ranking_validation_source_cohort(
            ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
            events=(research.events[0], production.events[1]),
        )


def test_research_source_requires_complete_replay_identity() -> None:
    event = _event(RankingSourceKind.RESEARCH_REPLAY, date(2026, 4, 1))

    with pytest.raises(RankingValidationContractError, match="replay run key"):
        replace(event, source_replay_run_key=None)
    with pytest.raises(RankingValidationContractError, match="replay contract hash"):
        replace(event, source_replay_contract_hash=None)
    with pytest.raises(RankingValidationContractError, match="immutable"):
        freeze_ranking_validation_source_cohort(
            ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
            events=(replace(event, input_snapshot_hash=_hash("tampered")),),
        )


def test_production_source_requires_exact_published_full_scope_v3() -> None:
    event = _event(RankingSourceKind.PRODUCTION_PUBLISHED, date(2026, 4, 1))

    for changes, message in (
        ({"source_signal_run_id": None}, "source run id"),
        ({"publication_state": "shadow"}, "published"),
        ({"scope_kind": "partial"}, "full scope"),
        ({"score_version": "legacy_v2"}, "final_score_v3"),
        ({"score_field": "total_score"}, "ranking_score"),
    ):
        with pytest.raises(RankingValidationContractError, match=message):
            replace(event, **changes)


def test_score_bucket_never_falls_back_to_legacy_total_score() -> None:
    event = _event(RankingSourceKind.RESEARCH_REPLAY, date(2026, 4, 1))
    cohort = freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        events=(event,),
    )
    valid = _score(event, "510001", declared_score=78.5, legacy_total_score=99.0)
    missing = _score(
        event,
        "510002",
        declared_score=None,
        legacy_total_score=100.0,
    )
    legacy_field = _score(
        event,
        "510003",
        declared_score=88.0,
        declared_score_field="total_score",
        legacy_total_score=88.0,
    )

    result = validate_score_bucket_observations(
        source_cohort=cohort,
        observations=(legacy_field, missing, valid),
    )

    assert tuple(item.asset_code for item in result.accepted) == ("510001",)
    assert result.accepted[0].declared_score == 78.5
    assert tuple((item.asset_code, item.reason) for item in result.exclusions) == (
        ("510002", "missing_declared_score_no_legacy_fallback"),
        ("510003", "incompatible_score_field"),
    )
    assert result.ranking_source_kind is RankingSourceKind.RESEARCH_REPLAY
    assert result.result_hash


def test_score_bucket_rejects_duplicate_or_cross_source_observations() -> None:
    research_event = _event(RankingSourceKind.RESEARCH_REPLAY, date(2026, 4, 1))
    production_event = _event(
        RankingSourceKind.PRODUCTION_PUBLISHED,
        date(2026, 4, 1),
    )
    cohort = freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        events=(research_event,),
    )
    row = _score(research_event, "510001", declared_score=70.0)

    with pytest.raises(RankingValidationContractError, match="duplicate"):
        validate_score_bucket_observations(
            source_cohort=cohort,
            observations=(row, row),
        )
    with pytest.raises(RankingValidationContractError, match="outside"):
        validate_score_bucket_observations(
            source_cohort=cohort,
            observations=(_score(production_event, "510001", declared_score=70.0),),
        )
