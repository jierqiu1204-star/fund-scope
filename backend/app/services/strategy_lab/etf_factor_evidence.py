from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentEvidence
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_validation import (
    PromotionDecision,
    holm_bonferroni,
)


class FactorEvidenceConflictError(ValueError):
    pass


class FactorEvidenceContractError(ValueError):
    pass


@dataclass(frozen=True)
class FactorEvidencePayload:
    manifest_hash: str
    ranking_contract_hash: str
    code_version: str
    samples: tuple[dict[str, Any], ...]
    aggregates: dict[str, Any]
    exclusions: tuple[dict[str, Any], ...]
    intervals: dict[str, Any]
    split_reports: dict[str, Any]
    costs: dict[str, Any]
    limitations: tuple[str, ...]
    report: dict[str, Any]
    promotion: PromotionDecision

    def validate(self) -> None:
        multiplicity = self.intervals.get("multiplicity")
        if not isinstance(multiplicity, dict):
            raise FactorEvidenceContractError(
                "factor evidence must include multiplicity metadata"
            )
        if multiplicity.get("method") != "holm_bonferroni":
            raise FactorEvidenceContractError(
                "factor evidence multiplicity method must be Holm-Bonferroni"
            )
        raw = multiplicity.get("raw_primary_p_values")
        adjusted = multiplicity.get("adjusted_primary_p_values")
        if not isinstance(raw, list) or not isinstance(adjusted, list):
            raise FactorEvidenceContractError(
                "raw and adjusted primary p-values are required"
            )
        try:
            raw_values = tuple(float(value) for value in raw)
            adjusted_values = tuple(float(value) for value in adjusted)
        except (TypeError, ValueError) as exc:
            raise FactorEvidenceContractError(
                "primary p-values must be numeric"
            ) from exc
        expected = holm_bonferroni(raw_values)
        if len(adjusted_values) != len(expected) or any(
            abs(actual - target) > 1e-12
            for actual, target in zip(adjusted_values, expected, strict=True)
        ):
            raise FactorEvidenceContractError(
                "claimed Holm-Bonferroni values do not match raw primary p-values"
            )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "manifest_hash": self.manifest_hash,
            "ranking_contract_hash": self.ranking_contract_hash,
            "code_version": self.code_version,
            "samples": self.samples,
            "aggregates": self.aggregates,
            "exclusions": self.exclusions,
            "intervals": self.intervals,
            "split_reports": self.split_reports,
            "costs": self.costs,
            "limitations": self.limitations,
            "report": self.report,
            "promotion": {
                "state": self.promotion.state,
                "passed": self.promotion.passed,
                "failed_gates": self.promotion.failed_gates,
                "endpoint": self.promotion.endpoint,
            },
        }

    @property
    def evidence_hash(self) -> str:
        return stable_contract_hash(self.canonical_payload())


async def persist_factor_evidence(
    session: AsyncSession,
    payload: FactorEvidencePayload,
) -> EtfFactorExperimentEvidence:
    payload.validate()
    if not payload.manifest_hash or not payload.ranking_contract_hash:
        raise ValueError("manifest and ranking contract hashes are required")
    existing = await session.scalar(
        select(EtfFactorExperimentEvidence).where(
            EtfFactorExperimentEvidence.manifest_hash == payload.manifest_hash
        )
    )
    if existing is not None:
        if existing.evidence_hash != payload.evidence_hash:
            raise FactorEvidenceConflictError(
                "experiment identity already has different immutable evidence"
            )
        return existing
    evidence = EtfFactorExperimentEvidence(
        manifest_hash=payload.manifest_hash,
        ranking_contract_hash=payload.ranking_contract_hash,
        code_version=payload.code_version,
        evidence_hash=payload.evidence_hash,
        samples_json=list(payload.samples),
        aggregates_json=payload.aggregates,
        exclusions_json=list(payload.exclusions),
        intervals_json=payload.intervals,
        split_reports_json=payload.split_reports,
        costs_json=payload.costs,
        limitations_json=list(payload.limitations),
        promotion_state=payload.promotion.state,
        report_json=payload.report,
    )
    session.add(evidence)
    await session.commit()
    await session.refresh(evidence)
    return evidence


async def get_factor_evidence(
    session: AsyncSession,
    manifest_hash: str,
) -> EtfFactorExperimentEvidence | None:
    return await session.scalar(
        select(EtfFactorExperimentEvidence).where(
            EtfFactorExperimentEvidence.manifest_hash == manifest_hash
        )
    )


def factor_evidence_view(evidence: EtfFactorExperimentEvidence) -> dict[str, Any]:
    split_reports = evidence.split_reports_json
    return {
        "manifest_hash": evidence.manifest_hash,
        "ranking_contract_hash": evidence.ranking_contract_hash,
        "code_version": evidence.code_version,
        "evidence_hash": evidence.evidence_hash,
        "development": split_reports.get("development", {}),
        "validation": split_reports.get("validation", {}),
        "holdout": split_reports.get("holdout", {}),
        "samples": evidence.samples_json,
        "aggregates": evidence.aggregates_json,
        "exclusions": evidence.exclusions_json,
        "intervals": evidence.intervals_json,
        "costs": evidence.costs_json,
        "limitations": evidence.limitations_json,
        "promotion_state": evidence.promotion_state,
        "report": evidence.report_json,
        "research_only": True,
        "production_mutation_allowed": False,
    }
