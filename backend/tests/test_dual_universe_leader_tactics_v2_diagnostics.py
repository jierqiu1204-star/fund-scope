from __future__ import annotations

from datetime import date

from app.services.strategy_lab.dual_universe_leader_tactics_v2_diagnostics import (
    build_v2_diagnostic_report,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    V2PairedOutcome,
    source_label,
)


def test_label_fidelity_and_economic_metrics_are_reported_separately() -> None:
    labels = (
        source_label(
            article_id="article",
            asset_code="603039",
            label_date=date(2026, 8, 3),
            theme="ai-application",
            label_kind="core",
            observability="observed",
            evidence_hash="a" * 64,
        ),
    )
    paired = (
        V2PairedOutcome(
            signal_date=date(2026, 8, 3),
            candidate_gross_return=0.10,
            benchmark_gross_return=0.02,
            candidate_net_return=0.098,
            benchmark_net_return=0.018,
            net_excess_return=0.08,
            cost_drag=0.002,
        ),
    )
    report = build_v2_diagnostic_report(
        predicted_codes=("603039", "002131"),
        source_labels=labels,
        sample_session_count=10,
        paired_outcomes=paired,
        turnover=0.2,
        maximum_drawdown=-0.1,
        concentration=0.3,
        regime_dependence=0.4,
        coverage=0.95,
        residual_factor_overlap={"trend": 0.1},
    )
    payload = report.to_dict()
    assert payload["source_label"] == {
        "precision": 0.5,
        "recall": 1.0,
        "frequency": 0.2,
    }
    assert payload["economic"]["primary_net_excess"] == 0.08
    assert payload["economic"]["coverage"] == 0.95
