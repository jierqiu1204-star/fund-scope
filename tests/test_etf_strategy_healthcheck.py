from types import SimpleNamespace

import pytest

from app.services.etf_research_evidence import (
    EVIDENCE_STATUS_SAME_CONTRACT,
    EVIDENCE_STATUS_VERSION_MISMATCH,
)
from app.services.short_research.healthcheck import (
    HEALTHCHECK_CONCLUSION_FAILED,
    HEALTHCHECK_CONCLUSION_INSUFFICIENT,
    HEALTHCHECK_CONCLUSION_OK,
    HEALTHCHECK_CONCLUSION_WATCH,
    HealthcheckStats,
    aggregate_replay_samples_by_metric,
    aggregate_validation_rows,
    build_healthcheck_evidence_issue,
    classify_healthcheck_conclusion,
    classify_item_conclusion,
    publishable_healthcheck_groups,
)


def test_healthcheck_marks_insufficient_samples() -> None:
    conclusion = classify_healthcheck_conclusion(
        full_sample_count=10,
        recent_sample_count=4,
        full_avg_return=0.02,
        recent_avg_return=0.01,
        full_win_rate=0.6,
        recent_win_rate=0.55,
        recent_max_drawdown=-0.03,
    )

    assert conclusion == HEALTHCHECK_CONCLUSION_INSUFFICIENT


def test_healthcheck_marks_recent_degradation() -> None:
    conclusion = classify_healthcheck_conclusion(
        full_sample_count=80,
        recent_sample_count=20,
        full_avg_return=0.03,
        recent_avg_return=-0.01,
        full_win_rate=0.6,
        recent_win_rate=0.35,
        recent_max_drawdown=-0.12,
    )

    assert conclusion == HEALTHCHECK_CONCLUSION_FAILED


def test_healthcheck_marks_stable_label_ok() -> None:
    conclusion = classify_healthcheck_conclusion(
        full_sample_count=80,
        recent_sample_count=20,
        full_avg_return=0.03,
        recent_avg_return=0.02,
        full_win_rate=0.6,
        recent_win_rate=0.58,
        recent_max_drawdown=-0.04,
    )

    assert conclusion == HEALTHCHECK_CONCLUSION_OK


def test_healthcheck_watch_when_recent_data_is_mixed() -> None:
    stats = HealthcheckStats(
        sample_count=20,
        avg_return=0.002,
        win_rate=0.49,
        max_drawdown=-0.04,
    )

    assert classify_item_conclusion(stats) == HEALTHCHECK_CONCLUSION_WATCH


def test_aggregate_validation_rows_groups_by_label() -> None:
    rows = [
        SimpleNamespace(label="短线观察", sample_count=1, avg_return=0.02, win_rate=0.6, worst_forward_drawdown=-0.03),
        SimpleNamespace(label="短线观察", sample_count=1, avg_return=0.04, win_rate=0.7, worst_forward_drawdown=-0.05),
        SimpleNamespace(label="高位观察", sample_count=1, avg_return=-0.01, win_rate=0.4, worst_forward_drawdown=-0.08),
    ]

    result = aggregate_validation_rows(rows, key_attr="label")

    assert result["短线观察"].sample_count == 2
    assert result["短线观察"].avg_return == 0.03
    assert result["短线观察"].win_rate == pytest.approx(0.65)
    assert result["短线观察"].max_drawdown == -0.05
    assert result["高位观察"].sample_count == 1


def test_aggregate_replay_samples_by_theme_metric() -> None:
    rows = [
        SimpleNamespace(
            status="completed",
            forward_return=0.03,
            adverse_drawdown=-0.02,
            metrics_json={"theme_group": "半导体"},
        ),
        SimpleNamespace(
            status="completed",
            forward_return=-0.01,
            adverse_drawdown=-0.05,
            metrics_json={"theme_group": "半导体"},
        ),
        SimpleNamespace(
            status="completed",
            forward_return=0.02,
            adverse_drawdown=-0.01,
            metrics_json={"market_regime": "defensive"},
        ),
    ]

    result = aggregate_replay_samples_by_metric(rows, keys=("theme_group", "market_regime"))

    assert result["半导体"].sample_count == 2
    assert result["半导体"].avg_return == pytest.approx(0.01)
    assert result["半导体"].max_drawdown == -0.05
    assert result["defensive"].sample_count == 1


def test_publishable_healthcheck_groups_requires_same_contract_and_samples() -> None:
    groups = {
        "健康回踩": HealthcheckStats(sample_count=0, avg_return=None, win_rate=None, max_drawdown=None),
        "趋势延续": HealthcheckStats(sample_count=12, avg_return=0.01, win_rate=0.55, max_drawdown=-0.03),
    }

    assert publishable_healthcheck_groups(groups, evidence_status=EVIDENCE_STATUS_VERSION_MISMATCH) == {}
    assert publishable_healthcheck_groups(groups, evidence_status=EVIDENCE_STATUS_SAME_CONTRACT) == {
        "趋势延续": groups["趋势延续"]
    }


def test_healthcheck_evidence_issue_explains_unusable_evidence() -> None:
    assert (
        build_healthcheck_evidence_issue(
            evidence_status=EVIDENCE_STATUS_VERSION_MISMATCH,
            full_sample_count=0,
            intraday_available=False,
        )
        == "标签验证来自旧信号快照，请重新生成同源证据。"
    )
    assert (
        build_healthcheck_evidence_issue(
            evidence_status=EVIDENCE_STATUS_SAME_CONTRACT,
            full_sample_count=0,
            intraday_available=False,
        )
        == "缺少可用标签回放样本，请先运行标签历史回放或策略回测。"
    )
