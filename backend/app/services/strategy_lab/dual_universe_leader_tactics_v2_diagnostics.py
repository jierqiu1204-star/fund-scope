"""Separate source-label fidelity from V2 economic diagnostics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any

from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    V2PairedOutcome,
    V2SourceLabel,
)


@dataclass(frozen=True)
class V2DiagnosticReport:
    source_label_precision: float | None
    source_label_recall: float | None
    source_label_frequency: float | None
    turnover: float | None
    cost_drag: float | None
    maximum_drawdown: float | None
    concentration: float | None
    regime_dependence: float | None
    coverage: float | None
    residual_factor_overlap: Mapping[str, float | None]
    primary_net_excess: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_label": {
                "precision": self.source_label_precision,
                "recall": self.source_label_recall,
                "frequency": self.source_label_frequency,
            },
            "economic": {
                "turnover": self.turnover,
                "cost_drag": self.cost_drag,
                "maximum_drawdown": self.maximum_drawdown,
                "concentration": self.concentration,
                "regime_dependence": self.regime_dependence,
                "coverage": self.coverage,
                "residual_factor_overlap": dict(self.residual_factor_overlap),
                "primary_net_excess": self.primary_net_excess,
            },
        }


def _finite_mean(values: Sequence[float]) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return mean(finite) if finite else None


def build_v2_diagnostic_report(
    *,
    predicted_codes: Sequence[str],
    source_labels: Sequence[V2SourceLabel],
    sample_session_count: int,
    paired_outcomes: Sequence[V2PairedOutcome] = (),
    turnover: float | None = None,
    maximum_drawdown: float | None = None,
    concentration: float | None = None,
    regime_dependence: float | None = None,
    coverage: float | None = None,
    residual_factor_overlap: Mapping[str, float | None] | None = None,
) -> V2DiagnosticReport:
    """Build diagnostics without allowing label fidelity to choose promotion."""

    predicted = {str(code) for code in predicted_codes if str(code).strip()}
    observed_positive = {
        label.asset_code
        for label in source_labels
        if label.asset_code
        and label.observability == "observed"
        and label.label_kind in {"positive", "core"}
    }
    true_positive = len(predicted & observed_positive)
    return V2DiagnosticReport(
        source_label_precision=true_positive / len(predicted) if predicted else None,
        source_label_recall=(true_positive / len(observed_positive) if observed_positive else None),
        source_label_frequency=(
            len(predicted) / sample_session_count if sample_session_count > 0 else None
        ),
        turnover=turnover,
        cost_drag=_finite_mean([item.cost_drag for item in paired_outcomes]),
        maximum_drawdown=maximum_drawdown,
        concentration=concentration,
        regime_dependence=regime_dependence,
        coverage=coverage,
        residual_factor_overlap=dict(residual_factor_overlap or {}),
        primary_net_excess=_finite_mean([item.net_excess_return for item in paired_outcomes]),
    )


__all__ = ["V2DiagnosticReport", "build_v2_diagnostic_report"]
