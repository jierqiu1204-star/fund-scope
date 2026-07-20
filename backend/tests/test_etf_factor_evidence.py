from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    NotificationLog,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    ValidationEvidenceImmutableError,
)
from app.services.strategy_lab.etf_factor_evidence import (
    FactorEvidenceConflictError,
    FactorEvidenceContractError,
    FactorEvidencePayload,
    persist_factor_evidence,
)
from app.services.strategy_lab.etf_factor_validation import PromotionDecision


def _payload(
    *,
    manifest_hash: str = "manifest-v1",
    validation_return: float = 0.02,
    promotion_state: Literal[
        "eligible_for_v4_proposal",
        "retain_current_ranking",
    ] = "retain_current_ranking",
) -> FactorEvidencePayload:
    passed = promotion_state == "eligible_for_v4_proposal"
    return FactorEvidencePayload(
        manifest_hash=manifest_hash,
        ranking_contract_hash="ranking-v3",
        code_version="git-sha-1",
        samples=({"asset_code": "510300", "split": "validation"},),
        aggregates={"primary_net_excess": validation_return},
        exclusions=({"asset_code": "159001", "reason": "missing_adjusted_price"},),
        intervals={
            "primary": {"lower": -0.001, "upper": 0.003},
            "multiplicity": {
                "method": "holm_bonferroni",
                "raw_primary_p_values": [0.01, 0.04],
                "adjusted_primary_p_values": [0.02, 0.04],
            },
        },
        split_reports={
            "development": {"sessions": 100},
            "validation": {"sessions": 40},
            "holdout": {"sessions": 20, "consumed": True},
        },
        costs={"fee_bps_per_side": 2.0, "slippage_bps_per_side": 3.0},
        limitations=("research evidence only", "no live ranking mutation"),
        report={"coverage": 0.91},
        promotion=PromotionDecision(
            state=promotion_state,
            passed=passed,
            failed_gates=() if passed else ("adjusted_primary_lower_bound",),
            endpoint="paired_top10_5_session_net_excess_common_support",
        ),
    )


async def _production_counts(session: AsyncSession) -> tuple[int, ...]:
    models = (
        ShortResearchSignalRun,
        ShortResearchSignalItem,
        TrackedPosition,
        TrackedPositionAlert,
        NotificationLog,
    )
    counts: list[int] = []
    for model in models:
        counts.append(
            int(await session.scalar(select(func.count()).select_from(model)) or 0)
        )
    return tuple(counts)


async def test_evidence_is_idempotent_immutable_and_conflict_safe(app) -> None:
    async with app.state.db.session() as session:
        first = await persist_factor_evidence(session, _payload())
        second = await persist_factor_evidence(session, _payload())
        assert first.id == second.id

        try:
            await persist_factor_evidence(session, _payload(validation_return=0.5))
        except FactorEvidenceConflictError:
            pass
        else:
            raise AssertionError("conflicting evidence must be rejected")

        first.report_json = {"tampered": True}
        try:
            await session.commit()
        except ValidationEvidenceImmutableError:
            await session.rollback()
        else:
            raise AssertionError("persisted evidence must be immutable")


async def test_read_only_evidence_endpoint_separates_splits_and_costs(
    app,
    client,
) -> None:
    async with app.state.db.session() as session:
        evidence = await persist_factor_evidence(session, _payload())

    response = await client.get(
        f"/api/strategy-lab/etf-factor-evidence/{evidence.manifest_hash}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["development"]["sessions"] == 100
    assert body["validation"]["sessions"] == 40
    assert body["holdout"]["consumed"] is True
    assert body["costs"]["fee_bps_per_side"] == 2.0
    assert body["limitations"]
    assert body["research_only"] is True
    assert body["production_mutation_allowed"] is False


async def test_factor_evidence_never_mutates_production_decision_tables(app) -> None:
    async with app.state.db.session() as session:
        before = await _production_counts(session)
        weak = await persist_factor_evidence(session, _payload(manifest_hash="weak"))
        strong = await persist_factor_evidence(
            session,
            _payload(
                manifest_hash="strong",
                validation_return=0.5,
                promotion_state="eligible_for_v4_proposal",
            ),
        )
        after = await _production_counts(session)

    assert before == after
    assert weak.promotion_state == "retain_current_ranking"
    assert strong.promotion_state == "eligible_for_v4_proposal"


async def test_factor_evidence_rejects_uncomputed_holm_claim(app) -> None:
    payload = _payload()
    intervals = {
        **payload.intervals,
        "multiplicity": {
            "method": "holm_bonferroni",
            "raw_primary_p_values": [0.01, 0.04],
            "adjusted_primary_p_values": [0.01, 0.04],
        },
    }
    invalid = FactorEvidencePayload(
        **{
            **payload.__dict__,
            "intervals": intervals,
        }
    )

    async with app.state.db.session() as session:
        with pytest.raises(
            FactorEvidenceContractError,
            match="do not match",
        ):
            await persist_factor_evidence(session, invalid)


async def test_missing_factor_evidence_returns_404(client) -> None:
    response = await client.get(
        "/api/strategy-lab/etf-factor-evidence/does-not-exist"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "ETF factor evidence not found"
