from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel

from app.services.recommendations.constants import ASSET_TYPE_FUND, ASSET_TYPE_STOCK, SAFE_LABELS


class FundCandidateInput(BaseModel):
    code: str
    name: str
    tracking_index_code: str | None
    category: str
    target_allocation: float
    current_allocation: float
    pe_percentile: float | None
    fee_rate: float | None
    fund_size: float | None
    volatility_1y: float | None
    max_drawdown_1y: float | None
    news_risk_score: float
    missing_metrics: list[str]


class StockCandidateInput(BaseModel):
    code: str
    name: str
    industry: str
    pe: float | None
    pb: float | None
    roe: float | None
    gross_margin: float | None
    debt_to_asset: float | None
    momentum_6m: float | None
    volatility_1y: float | None
    turnover: float | None
    industry_allocation: float
    missing_metrics: list[str]


class ScoredCandidate(BaseModel):
    asset_code: str
    asset_name: str
    asset_type: Literal["fund", "stock"]
    total_score: float
    score_breakdown: dict[str, dict[str, Any]]
    rationale: dict[str, Any]
    risk_flags: list[str]
    data_freshness: dict[str, Any]
    safe_label: str


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _round(value: float) -> float:
    return round(value, 2)


def _component(score: float, weight: float, metrics: dict[str, Any]) -> dict[str, Any]:
    bounded = _clamp(score)
    return {
        "score": _round(bounded),
        "weight": weight,
        "weighted_score": _round(bounded * weight),
        "metrics": metrics,
    }


def _fund_valuation_score(pe_percentile: float | None, category: str) -> float:
    if category == "money_market":
        return 65.0
    if pe_percentile is None:
        return 45.0
    return _clamp(100.0 - pe_percentile)


def _fund_gap_score(target_allocation: float, current_allocation: float) -> float:
    if target_allocation <= 0:
        return 35.0
    gap = max(target_allocation - current_allocation, 0.0)
    return _clamp(50.0 + (gap / target_allocation * 50.0))


def _fund_cost_score(fee_rate: float | None) -> float:
    if fee_rate is None:
        return 45.0
    return _clamp(100.0 - fee_rate * 80.0)


def _fund_risk_score(volatility_1y: float | None, max_drawdown_1y: float | None) -> float:
    if volatility_1y is None or max_drawdown_1y is None:
        return 45.0
    return _clamp(100.0 - volatility_1y * 1.4 - max_drawdown_1y * 1.1)


def _fund_size_score(fund_size: float | None) -> float:
    if fund_size is None:
        return 45.0
    return _clamp(35.0 + min(fund_size, 200.0) / 200.0 * 65.0)


def score_fund_candidate(candidate: FundCandidateInput) -> ScoredCandidate:
    breakdown = {
        "valuation_fit": _component(
            _fund_valuation_score(candidate.pe_percentile, candidate.category),
            0.30,
            {"pe_percentile": candidate.pe_percentile, "tracking_index_code": candidate.tracking_index_code},
        ),
        "portfolio_gap_fit": _component(
            _fund_gap_score(candidate.target_allocation, candidate.current_allocation),
            0.25,
            {
                "target_allocation": candidate.target_allocation,
                "current_allocation": candidate.current_allocation,
            },
        ),
        "cost_efficiency": _component(
            _fund_cost_score(candidate.fee_rate),
            0.15,
            {"fee_rate": candidate.fee_rate},
        ),
        "risk_control": _component(
            _fund_risk_score(candidate.volatility_1y, candidate.max_drawdown_1y),
            0.15,
            {
                "volatility_1y": candidate.volatility_1y,
                "max_drawdown_1y": candidate.max_drawdown_1y,
            },
        ),
        "liquidity_size": _component(
            _fund_size_score(candidate.fund_size),
            0.10,
            {"fund_size": candidate.fund_size},
        ),
        "news_risk": _component(
            100.0 - candidate.news_risk_score,
            0.05,
            {"news_risk_score": candidate.news_risk_score},
        ),
    }
    score = sum(component["weighted_score"] for component in breakdown.values())
    risk_flags: list[str] = []
    if candidate.news_risk_score >= 60:
        risk_flags.append("critical_news")
    if candidate.missing_metrics:
        risk_flags.append("missing_metrics")
    if candidate.max_drawdown_1y is not None and candidate.max_drawdown_1y >= 30:
        risk_flags.append("large_drawdown")

    rationale = {
        "summary": "Rule-based fund screening candidate.",
        "drivers": [
            "valuation_fit",
            "portfolio_gap_fit",
            "cost_efficiency",
            "risk_control",
            "liquidity_size",
        ],
    }
    data_freshness = {"missing_metrics": candidate.missing_metrics}
    return ScoredCandidate(
        asset_code=candidate.code,
        asset_name=candidate.name,
        asset_type=cast(Literal["fund", "stock"], ASSET_TYPE_FUND),
        total_score=_round(score),
        score_breakdown=breakdown,
        rationale=rationale,
        risk_flags=risk_flags,
        data_freshness=data_freshness,
        safe_label=SAFE_LABELS[ASSET_TYPE_FUND],
    )


def _score_low(value: float | None, best: float, worst: float) -> float:
    if value is None:
        return 45.0
    if value <= best:
        return 100.0
    if value >= worst:
        return 20.0
    return 100.0 - ((value - best) / (worst - best) * 80.0)


def _score_high(value: float | None, weak: float, strong: float) -> float:
    if value is None:
        return 45.0
    if value >= strong:
        return 100.0
    if value <= weak:
        return 20.0
    return 20.0 + ((value - weak) / (strong - weak) * 80.0)


def score_stock_candidate(candidate: StockCandidateInput) -> ScoredCandidate | None:
    required = [candidate.pe, candidate.pb, candidate.roe]
    if any(value is None for value in required):
        return None

    quality = (
        _score_high(candidate.roe, 5.0, 25.0) * 0.45
        + _score_high(candidate.gross_margin, 15.0, 60.0) * 0.30
        + _score_low(candidate.debt_to_asset, 20.0, 80.0) * 0.25
    )
    valuation = _score_low(candidate.pe, 8.0, 55.0) * 0.65 + _score_low(candidate.pb, 1.0, 10.0) * 0.35
    momentum = _score_high(candidate.momentum_6m, -20.0, 30.0)
    risk = _score_low(candidate.volatility_1y, 10.0, 55.0)
    liquidity = _score_high(candidate.turnover, 50_000_000.0, 1_000_000_000.0)
    diversity = _clamp(100.0 - candidate.industry_allocation * 200.0)

    breakdown = {
        "quality": _component(quality, 0.25, {"roe": candidate.roe, "gross_margin": candidate.gross_margin}),
        "valuation": _component(valuation, 0.25, {"pe": candidate.pe, "pb": candidate.pb}),
        "momentum": _component(momentum, 0.15, {"momentum_6m": candidate.momentum_6m}),
        "risk_control": _component(risk, 0.15, {"volatility_1y": candidate.volatility_1y}),
        "liquidity": _component(liquidity, 0.10, {"turnover": candidate.turnover}),
        "portfolio_diversity": _component(
            diversity,
            0.10,
            {"industry": candidate.industry, "industry_allocation": candidate.industry_allocation},
        ),
    }
    risk_flags: list[str] = []
    if candidate.volatility_1y is not None and candidate.volatility_1y >= 40:
        risk_flags.append("high_volatility")
    if candidate.debt_to_asset is not None and candidate.debt_to_asset >= 75:
        risk_flags.append("high_leverage")
    if candidate.missing_metrics:
        risk_flags.append("missing_metrics")

    return ScoredCandidate(
        asset_code=candidate.code,
        asset_name=candidate.name,
        asset_type=cast(Literal["fund", "stock"], ASSET_TYPE_STOCK),
        total_score=_round(sum(component["weighted_score"] for component in breakdown.values())),
        score_breakdown=breakdown,
        rationale={
            "summary": "Rule-based stock watchlist screening candidate.",
            "drivers": ["quality", "valuation", "momentum", "risk_control", "liquidity"],
        },
        risk_flags=risk_flags,
        data_freshness={"missing_metrics": candidate.missing_metrics},
        safe_label=SAFE_LABELS[ASSET_TYPE_STOCK],
    )


def apply_optional_llm_explanation(candidate: ScoredCandidate, explanation: str | None) -> ScoredCandidate:
    if not explanation:
        return candidate
    rationale = dict(candidate.rationale)
    rationale["llm_explanation"] = explanation[:280]
    return candidate.model_copy(update={"rationale": rationale})
