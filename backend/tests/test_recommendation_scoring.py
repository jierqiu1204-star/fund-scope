from __future__ import annotations

import pytest

from app.services.recommendations.scoring import (
    FundCandidateInput,
    StockCandidateInput,
    apply_optional_llm_explanation,
    score_fund_candidate,
    score_stock_candidate,
)


def test_fund_scoring_is_deterministic_and_rewards_portfolio_gap() -> None:
    base = FundCandidateInput(
        code="007339",
        name="E Fund CSI 300",
        tracking_index_code="CSI300",
        category="equity",
        target_allocation=0.4,
        current_allocation=0.1,
        pe_percentile=18.0,
        fee_rate=0.6,
        fund_size=120.0,
        volatility_1y=18.0,
        max_drawdown_1y=22.0,
        news_risk_score=0.0,
        missing_metrics=[],
    )
    filled = base.model_copy(update={"current_allocation": 0.4})

    first = score_fund_candidate(base)
    second = score_fund_candidate(base)
    no_gap = score_fund_candidate(filled)

    assert first.total_score == second.total_score
    assert first.score_breakdown == second.score_breakdown
    assert first.total_score > no_gap.total_score
    assert first.score_breakdown["portfolio_gap_fit"]["score"] > no_gap.score_breakdown["portfolio_gap_fit"]["score"]


def test_fund_scoring_flags_critical_news_and_missing_metrics() -> None:
    result = score_fund_candidate(
        FundCandidateInput(
            code="001052",
            name="Huaxia SP500",
            tracking_index_code="SP500",
            category="equity",
            target_allocation=0.3,
            current_allocation=0.3,
            pe_percentile=None,
            fee_rate=None,
            fund_size=None,
            volatility_1y=None,
            max_drawdown_1y=None,
            news_risk_score=80.0,
            missing_metrics=["pe_percentile", "fee_rate"],
        )
    )

    assert "critical_news" in result.risk_flags
    assert "missing_metrics" in result.risk_flags
    assert "pe_percentile" in result.data_freshness["missing_metrics"]


def test_stock_scoring_is_deterministic_and_excludes_missing_fundamentals() -> None:
    candidate = StockCandidateInput(
        code="600519.SH",
        name="Kweichow Moutai",
        industry="consumer",
        pe=24.0,
        pb=6.0,
        roe=28.0,
        gross_margin=90.0,
        debt_to_asset=18.0,
        momentum_6m=12.0,
        volatility_1y=22.0,
        turnover=3_000_000_000.0,
        industry_allocation=0.1,
        missing_metrics=[],
    )

    first = score_stock_candidate(candidate)
    second = score_stock_candidate(candidate)
    missing = score_stock_candidate(candidate.model_copy(update={"pe": None, "roe": None, "missing_metrics": ["pe", "roe"]}))

    assert first is not None
    assert second is not None
    assert first.total_score == second.total_score
    assert first.safe_label == "观察候选"
    assert missing is None


def test_llm_explanation_cannot_mutate_score_or_rank() -> None:
    result = score_fund_candidate(
        FundCandidateInput(
            code="000198",
            name="Tianhong YEB",
            tracking_index_code=None,
            category="money_market",
            target_allocation=0.1,
            current_allocation=0.05,
            pe_percentile=None,
            fee_rate=0.2,
            fund_size=800.0,
            volatility_1y=1.0,
            max_drawdown_1y=0.5,
            news_risk_score=0.0,
            missing_metrics=[],
        )
    )

    explained = apply_optional_llm_explanation(result, "候选原因：波动低，适合作为现金管理观察。")

    assert explained.total_score == result.total_score
    assert explained.score_breakdown == result.score_breakdown
    assert explained.rationale["llm_explanation"] == "候选原因：波动低，适合作为现金管理观察。"
    assert "buy" not in explained.model_dump()


@pytest.mark.parametrize("forbidden", ["买入", "卖出", "目标价", "expected_return"])
def test_scored_candidate_has_no_trade_instruction_fields(forbidden: str) -> None:
    result = score_stock_candidate(
        StockCandidateInput(
            code="000333.SZ",
            name="Midea Group",
            industry="consumer",
            pe=14.0,
            pb=2.8,
            roe=21.0,
            gross_margin=27.0,
            debt_to_asset=58.0,
            momentum_6m=8.0,
            volatility_1y=20.0,
            turnover=1_000_000_000.0,
            industry_allocation=0.15,
            missing_metrics=[],
        )
    )

    assert result is not None
    assert forbidden not in str(result.model_dump()).lower()
