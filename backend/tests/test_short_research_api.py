from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfExitHyperoptItem,
    EtfExitHyperoptRun,
    EtfLabelReplaySample,
    EtfOptimizedAllocationItem,
    EtfOptimizedAllocationSnapshot,
    EtfPriceHistory,
    FundNavHistory,
    NotificationLog,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    TradableEtf,
    utcnow,
)
from app.services.short_research.service import (
    allowed_conclusions,
    ensure_short_research_universe,
    run_etf_observation_portfolio_optimization,
)


async def _seed_short_research_history(app) -> None:
    start = date(2026, 1, 27)
    async with app.state.db.session() as session:
        await ensure_short_research_universe(session)
        for offset in range(130):
            current = start + timedelta(days=offset)
            fund_nav = 1.0 + offset * 0.002
            etf_close = 1.0 + offset * 0.012
            session.add(
                FundNavHistory(
                    fund_code="110020",
                    nav_date=current,
                    nav=fund_nav,
                    accumulated_nav=fund_nav,
                )
            )
            session.add(
                EtfPriceHistory(
                    etf_code="512480",
                    trade_date=current,
                    open=etf_close * 0.99,
                    high=etf_close * 1.02,
                    low=etf_close * 0.98,
                    close=etf_close,
                    volume=2_000_000 + offset * 5000,
                    turnover=220_000_000 + offset * 1_000_000,
                    pct_change=0.0 if offset == 0 else 0.012 / (1.0 + (offset - 1) * 0.012) * 100,
                )
            )
        await session.commit()


async def _seed_entry_timing_etf(
    app,
    *,
    code: str,
    closes: list[float],
    turnovers: list[float] | None = None,
    latest_date: date = date(2026, 6, 5),
) -> None:
    start = latest_date - timedelta(days=len(closes) - 1)
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name=f"买点测试ETF{code}",
                exchange="SH",
                theme_tags_json=["买点测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        for offset, close in enumerate(closes):
            previous = closes[offset - 1] if offset > 0 else close
            turnover = turnovers[offset] if turnovers is not None else 120_000_000
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=2_000_000,
                    turnover=turnover,
                    pct_change=0.0 if offset == 0 else (close / previous - 1.0) * 100,
                )
            )
        await session.commit()


def _steady_uptrend(days: int = 80, *, start: float = 1.0, step: float = 0.01) -> list[float]:
    return [round(start + offset * step, 6) for offset in range(days)]


async def _seed_exit_hyperopt_run(
    session,
    *,
    status: str = "candidate",
) -> EtfExitHyperoptRun:
    run = EtfExitHyperoptRun(
        status="success",
        started_at=utcnow(),
        finished_at=utcnow(),
        as_of_date=date(2026, 7, 3),
        objective="stability_first",
        rule_version="etf_exit_hyperopt_v1",
        calibration_rule_version="etf_exit_calibration_v1",
        execution_model="daily_close",
        contract_hash="api-test-contract",
        data_cutoff=utcnow(),
        train_range_json={"start_date": "2026-01-01", "end_date": "2026-05-01"},
        out_of_sample_range_json={"start_date": "2026-05-02", "end_date": "2026-07-03"},
        search_space_json={"hard_stop_multiplier": [1.2]},
        bucket_summary_json={"all": 1},
        summary_json={
            "bucket_count": 1,
            "candidate_count": 1 if status == "candidate" else 0,
            "rejected_count": 0,
            "evidence_insufficient_count": 0,
            "research_only": True,
            "auto_applied": False,
        },
        created_at=utcnow(),
    )
    session.add(run)
    await session.flush()
    session.add(
        EtfExitHyperoptItem(
            run_id=run.id,
            bucket_type="all",
            bucket_key="all",
            status=status,
            conclusion="候选待确认",
            parameter_json={
                "hard_stop_multiplier": 1.2,
                "profit_start_multiplier": 0.9,
                "trailing_giveback_multiplier": 0.5,
                "trend_confirm_days": 2,
                "take_profit_watch_pct": 3.0,
            },
            train_metrics_json={"sample_count": 5, "total_return": 0.08},
            out_of_sample_metrics_json={"sample_count": 5, "trade_count": 3, "total_return": 0.04},
            rolling_metrics_json={"window_count": 2, "stable_window_rate": 0.5},
            confidence_json={"level": "一般"},
            source_reliability="verified_daily_close",
            score=88.0,
            sample_count=5,
            trade_count=3,
            created_at=utcnow(),
        )
    )
    await session.flush()
    return run


async def _seed_observation_portfolio_signal_run(
    app,
    *,
    run_as_of_date: date = date(2026, 6, 15),
    items: list[dict[str, Any]],
) -> None:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=run_as_of_date,
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": len(items)},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        seen_codes: set[str] = set()
        for item in items:
            code = item["code"]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            session.add(
                TradableEtf(
                    code=code,
                    name=f"观察组合测试{code}",
                    exchange="SH",
                    theme_tags_json=item.get("theme_tags", [f"观察组合测试{code}"]),
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )

        await session.commit()

        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=item["code"],
                    rank=index + 1,
                    total_score=float(item["total_score"]),
                    conclusion=item["conclusion"],
                    score_breakdown_json={},
                    risk_flags_json=item.get("risk_flags", []),
                    rationale_json={"entry_timing_reason": item["entry_timing_reason"]},
                    metrics_json={
                        "entry_timing_label": item["entry_timing_label"],
                        "entry_timing_reason": item["entry_timing_reason"],
                        "return_20d": 0.09,
                        "average_turnover_20d": 150_000_000,
                        "max_drawdown_60d": item.get("max_drawdown_60d", -0.04),
                        "volatility_20d": item.get("volatility_20d", 0.015),
                        "default_display_eligible": item.get("default_display_eligible", True),
                    },
                )
                for index, item in enumerate(items)
            ]
        )
        await session.commit()


async def _seed_opportunity_signal_run(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="159001",
                    name="机器人ETF测试",
                    exchange="SZ",
                    theme_tags_json=["机器人"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                TradableEtf(
                    code="159002",
                    name="普通科技ETF测试",
                    exchange="SZ",
                    theme_tags_json=["科技"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
            ]
        )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 3),
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": 2, "score_version": "final_score_v2"},
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="159001",
                    rank=1,
                    total_score=70,
                    conclusion="高位观察",
                    score_breakdown_json={
                        "opportunity_score_v1": {"opportunity_score": 80},
                        "factor_profile_v1": {
                            "score_version": "etf_factor_profile_v1_degraded",
                            "factor_profile": {"profile_version": "etf_factor_profile_v1_degraded", "score": 81.5},
                        },
                    },
                    risk_flags_json=[],
                    rationale_json={"entry_timing_reason": "强势但冲高。"},
                    metrics_json={
                        "entry_timing_label": "冲高别追",
                        "entry_timing_reason": "强势但冲高。",
                        "latest_date": "2026-07-03",
                        "latest_value": 1.2,
                        "usable_days": 120,
                        "default_display_eligible": True,
                        "technical_score": 70,
                        "opportunity_score": 80,
                        "opportunity_label": "主题强但等买点",
                        "catalyst_score": 90,
                        "sentiment_heat_score": 80,
                        "catalyst_summary": "宇树科技 IPO 催化机器人主题。",
                        "catalyst_events": [{"summary": "宇树科技 IPO 催化机器人主题。"}],
                        "catalyst_limitations": ["当前买点为冲高别追，主题催化不能覆盖追高风险。"],
                        "factor_profile_version": "etf_factor_profile_v1_degraded",
                        "factor_profile_status": "gated",
                        "factor_profile_score": 81.5,
                        "factor_group_scores": {
                            "price_momentum": {
                                "group": "price_momentum",
                                "label": "价格动量",
                                "score": 70,
                                "availability": "available",
                            },
                            "theme_event": {
                                "group": "theme_event",
                                "label": "主题事件",
                                "score": 90,
                                "availability": "available",
                            },
                            "fund_flow": {
                                "group": "fund_flow",
                                "label": "资金流",
                                "score": None,
                                "availability": "unavailable",
                                "reason": "资金流数据不可用。",
                            },
                        },
                        "factor_scores": [
                            {
                                "factor_id": "theme_event_score",
                                "group": "theme_event",
                                "label": "主题事件",
                                "score": 90,
                                "availability": "available",
                                "reliability": "verified",
                                "source": "cached_test",
                                "reason": "缓存因子。",
                                "decision_eligible": True,
                            }
                        ],
                        "factor_availability": {
                            "fund_flow_score": {
                                "availability": "unavailable",
                                "reliability": "unavailable",
                                "reason": "资金流数据不可用。",
                                "decision_eligible": False,
                            }
                        },
                        "risk_gates": [
                            {
                                "gate_id": "overheat",
                                "label": "反转/过热",
                                "active": True,
                                "reason": "冲高别追。",
                            }
                        ],
                        "opportunity_breakdown": {
                            "profile_version": "etf_factor_profile_v1_degraded",
                            "score": 81.5,
                            "included_groups": ["price_momentum", "theme_event"],
                            "excluded_groups": ["fund_flow"],
                        },
                    },
                ),
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="159002",
                    rank=2,
                    total_score=78,
                    conclusion="短线观察",
                    score_breakdown_json={"opportunity_score_v1": {"opportunity_score": 69}},
                    risk_flags_json=[],
                    rationale_json={"entry_timing_reason": "趋势延续。"},
                    metrics_json={
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "趋势延续。",
                        "latest_date": "2026-07-03",
                        "latest_value": 1.1,
                        "usable_days": 120,
                        "default_display_eligible": True,
                        "technical_score": 78,
                        "opportunity_score": 69,
                        "opportunity_label": "技术优先",
                        "catalyst_score": 50,
                        "sentiment_heat_score": 50,
                        "catalyst_summary": "暂无可用于评分的主题催化事件。",
                        "catalyst_events": [],
                        "catalyst_limitations": ["主题催化数据不可用。"],
                    },
                ),
            ]
        )
        await session.commit()


async def _seed_score_bucket_signal_runs(app) -> dict[str, Any]:
    signal_date = date(2026, 7, 3)
    available_codes = [f"5890{index:02d}" for index in range(1, 13)]
    unavailable_code = "589098"
    missing_score_code = "589099"
    non_finite_score_code = "589097"
    old_only_code = "589000"
    partial_only_code = "589096"
    mismatched_contract_code = "589095"
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=code,
                    name=f"综合关注分层{code}",
                    exchange="SH",
                    theme_tags_json=["分层验证"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
                for code in [
                    old_only_code,
                    *available_codes,
                    unavailable_code,
                    missing_score_code,
                    non_finite_score_code,
                    partial_only_code,
                    mismatched_contract_code,
                ]
            ]
        )
        await session.flush()

        for code_index, code in enumerate(
            [
                old_only_code,
                *available_codes,
                unavailable_code,
                missing_score_code,
                non_finite_score_code,
                partial_only_code,
                mismatched_contract_code,
            ],
            start=1,
        ):
            for offset in range(11):
                close = 1.0 + (code_index * 0.001 * offset)
                previous = 1.0 + (code_index * 0.001 * (offset - 1)) if offset else close
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=signal_date + timedelta(days=offset),
                        open=close * 0.995,
                        high=close * 1.01,
                        low=close * 0.99,
                        close=close,
                        volume=2_000_000,
                        turnover=180_000_000,
                        pct_change=0.0 if offset == 0 else (close / previous - 1.0) * 100,
                    )
                )

        old_run = ShortResearchSignalRun(
            status="success",
            as_of_date=signal_date,
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": 1},
        )
        latest_run = ShortResearchSignalRun(
            status="success",
            as_of_date=signal_date,
            scope_kind="full",
            scope_hash="full-scope",
            universe_snapshot_hash="universe-current",
            input_snapshot_hash="input-current",
            score_version="final_score_v3",
            rule_version="short_research_rule_v3",
            ranking_contract_hash="current-contract",
            score_field="ranking_score",
            data_cutoff=utcnow(),
            as_of_trade_date=signal_date,
            price_basis="total_return_adjusted",
            expected_item_count=len(available_codes) + 3,
            eligible_item_count=len(available_codes),
            coverage_ratio=1.0,
            idempotency_key="score-bucket-current",
            config_json={
                "asset_type": "etf",
                "language": "research_only",
                "scope_kind": "full",
                "score_version": "final_score_v3",
                "ranking_contract_hash": "current-contract",
                "score_field": "ranking_score",
                "price_basis": "total_return_adjusted",
            },
            summary_json={"item_count": len(available_codes) + 3, "score_version": "final_score_v3"},
        )
        partial_run = ShortResearchSignalRun(
            status="success",
            as_of_date=signal_date,
            scope_kind="theme",
            score_version="final_score_v3",
            ranking_contract_hash="current-contract",
            score_field="ranking_score",
            as_of_trade_date=signal_date,
            price_basis="total_return_adjusted",
            config_json={
                "asset_type": "etf",
                "scope_kind": "theme",
                "score_version": "final_score_v3",
                "ranking_contract_hash": "current-contract",
                "score_field": "ranking_score",
            },
            summary_json={"item_count": 1, "score_version": "final_score_v3"},
        )
        mismatched_contract_run = ShortResearchSignalRun(
            status="success",
            as_of_date=signal_date,
            scope_kind="full",
            score_version="final_score_v3",
            ranking_contract_hash="other-contract",
            score_field="total_score",
            as_of_trade_date=signal_date,
            price_basis="total_return_adjusted",
            config_json={
                "asset_type": "etf",
                "scope_kind": "full",
                "score_version": "final_score_v3",
                "ranking_contract_hash": "other-contract",
                "score_field": "ranking_score",
            },
            summary_json={"item_count": 1, "score_version": "final_score_v3"},
        )
        session.add_all([old_run, latest_run, partial_run, mismatched_contract_run])
        await session.flush()
        session.add(
            ShortResearchSignalItem(
                run_id=old_run.id,
                asset_type="etf",
                asset_code=old_only_code,
                rank=1,
                total_score=100,
                conclusion="短线观察",
                score_breakdown_json={},
                risk_flags_json=[],
                rationale_json={},
                metrics_json={
                    "opportunity_score": 100,
                    "factor_profile_version": "etf_factor_profile_v1",
                    "factor_profile_score": 100,
                    "catalyst_summary": "旧 run 不应进入分层验证。",
                },
            )
        )
        for index, code in enumerate(available_codes, start=1):
            score = 100 - index
            session.add(
                ShortResearchSignalItem(
                    run_id=latest_run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=index,
                    total_score=float(score),
                    ranking_score=float(score),
                    score_eligible=True,
                    global_rank=index,
                    conclusion="短线观察",
                    score_breakdown_json={
                        "final_score_v3": {
                            "score_version": "final_score_v3",
                            "ranking_score": float(score),
                            "score_eligible": True,
                        }
                    },
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={
                        "opportunity_score": score,
                        "factor_profile_version": "etf_factor_profile_v1",
                        "factor_profile_score": score,
                        "catalyst_summary": "真实主题催化可用于综合关注排序。",
                    },
                )
            )
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=latest_run.id,
                    asset_type="etf",
                    asset_code=unavailable_code,
                    rank=20,
                    total_score=99,
                    conclusion="高位观察",
                    score_breakdown_json={"final_score_v3": {"score_version": "final_score_v3"}},
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={
                        "opportunity_score": 999,
                        "catalyst_score": 50,
                        "sentiment_heat_score": 50,
                        "catalyst_summary": "暂无可用于评分的主题催化事件。",
                        "catalyst_limitations": ["主题催化数据不可用。"],
                    },
                ),
                ShortResearchSignalItem(
                    run_id=latest_run.id,
                    asset_type="etf",
                    asset_code=missing_score_code,
                    rank=21,
                    total_score=80,
                    conclusion="短线观察",
                    score_breakdown_json={},
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={"catalyst_summary": "缺少综合关注分。"},
                ),
                ShortResearchSignalItem(
                    run_id=latest_run.id,
                    asset_type="etf",
                    asset_code=non_finite_score_code,
                    rank=22,
                    total_score=79,
                    ranking_score=float("nan"),
                    score_eligible=True,
                    conclusion="短线观察",
                    score_breakdown_json={
                        "final_score_v3": {
                            "score_version": "final_score_v3",
                            "ranking_score": float("nan"),
                            "score_eligible": True,
                        }
                    },
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={"catalyst_summary": "非有限综合关注分。"},
                ),
                ShortResearchSignalItem(
                    run_id=partial_run.id,
                    asset_type="etf",
                    asset_code=partial_only_code,
                    rank=1,
                    total_score=100,
                    ranking_score=100,
                    score_eligible=True,
                    conclusion="短线观察",
                    score_breakdown_json={
                        "final_score_v3": {
                            "score_version": "final_score_v3",
                            "ranking_score": 100,
                            "score_eligible": True,
                        }
                    },
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={},
                ),
                ShortResearchSignalItem(
                    run_id=mismatched_contract_run.id,
                    asset_type="etf",
                    asset_code=mismatched_contract_code,
                    rank=1,
                    total_score=100,
                    ranking_score=100,
                    score_eligible=True,
                    conclusion="短线观察",
                    score_breakdown_json={
                        "final_score_v3": {
                            "score_version": "final_score_v3",
                            "ranking_score": 100,
                            "score_eligible": True,
                        }
                    },
                    risk_flags_json=[],
                    rationale_json={},
                    metrics_json={},
                ),
            ]
        )
        await session.commit()
        latest_run.publication_state = "published"
        latest_run.published_at = utcnow()
        mismatched_contract_run.publication_state = "published"
        mismatched_contract_run.published_at = utcnow()
        await session.commit()
        return {
            "latest_run_id": latest_run.id,
            "old_run_id": old_run.id,
            "partial_run_id": partial_run.id,
            "mismatched_contract_run_id": mismatched_contract_run.id,
            "available_codes": available_codes,
            "unavailable_code": unavailable_code,
            "missing_score_code": missing_score_code,
            "non_finite_score_code": non_finite_score_code,
            "old_only_code": old_only_code,
            "partial_only_code": partial_only_code,
            "mismatched_contract_code": mismatched_contract_code,
        }


async def _seed_unavailable_opportunity_sort_run(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="159010",
                    name="可用催化ETF测试",
                    exchange="SZ",
                    theme_tags_json=["机器人"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                TradableEtf(
                    code="159011",
                    name="无催化ETF测试",
                    exchange="SZ",
                    theme_tags_json=["宽基"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
            ]
        )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 3),
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": 2, "score_version": "final_score_v2"},
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="159010",
                    rank=1,
                    total_score=65,
                    conclusion="短线观察",
                    score_breakdown_json={"opportunity_score_v1": {"opportunity_score": 65}},
                    risk_flags_json=[],
                    rationale_json={"entry_timing_reason": "可用催化测试。"},
                    metrics_json={
                        "entry_timing_label": "健康回踩",
                        "entry_timing_reason": "可用催化测试。",
                        "latest_date": "2026-07-03",
                        "latest_value": 1.1,
                        "usable_days": 120,
                        "default_display_eligible": True,
                        "technical_score": 65,
                        "opportunity_score": 65,
                        "opportunity_label": "常规观察",
                        "catalyst_score": 80,
                        "sentiment_heat_score": 63,
                        "catalyst_summary": "可用主题催化。",
                        "catalyst_events": [{"summary": "可用主题催化。"}],
                        "catalyst_limitations": [],
                    },
                ),
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="159011",
                    rank=2,
                    total_score=90,
                    conclusion="高位观察",
                    score_breakdown_json={"opportunity_score_v1": {"opportunity_score": 99}},
                    risk_flags_json=[],
                    rationale_json={"entry_timing_reason": "无催化测试。"},
                    metrics_json={
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "无催化测试。",
                        "latest_date": "2026-07-03",
                        "latest_value": 1.2,
                        "usable_days": 120,
                        "default_display_eligible": True,
                        "technical_score": 90,
                        "opportunity_score": 99,
                        "opportunity_label": "技术优先",
                        "catalyst_score": 50,
                        "sentiment_heat_score": 50,
                        "catalyst_summary": "暂无可用于评分的主题催化事件。",
                        "catalyst_events": [],
                        "catalyst_limitations": ["主题催化数据不可用。"],
                    },
                ),
            ]
        )
        await session.commit()


async def _seed_sector_trend_opportunity_run(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="159101",
                    name="创新药ETF测试",
                    exchange="SZ",
                    theme_tags_json=["创新药"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                TradableEtf(
                    code="159102",
                    name="无主题ETF测试",
                    exchange="SZ",
                    theme_tags_json=["宽基"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
            ]
        )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 3),
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": 2, "score_version": "final_score_v2"},
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="159101",
                    rank=1,
                    total_score=76,
                    conclusion="短线观察",
                    score_breakdown_json={
                        "opportunity_score_v2": {
                            "opportunity_score": 78,
                            "weights": {"technical": 0.75, "sector_trend": 0.25},
                        },
                        "sector_trend_v1": {"score_version": "sector_trend_v1"},
                    },
                    risk_flags_json=[],
                    rationale_json={"entry_timing_reason": "创新药板块趋势强。"},
                    metrics_json={
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "创新药板块趋势强。",
                        "latest_date": "2026-07-03",
                        "latest_value": 1.2,
                        "usable_days": 120,
                        "default_display_eligible": True,
                        "technical_score": 76,
                        "sector_trend_score": 84,
                        "sector_trend_label": "板块强势",
                        "sector_trend_summary": "创新药上涨家数占优，板块趋势强。",
                        "sector_peer_count": 4,
                        "sector_trend_status": "success",
                        "opportunity_score": 78,
                        "opportunity_label": "板块强但等催化",
                        "opportunity_score_version": "opportunity_score_v2_sector_only",
                        "catalyst_score": 50,
                        "sentiment_heat_score": 50,
                        "catalyst_status": "unavailable",
                        "catalyst_summary": "暂无可用于评分的主题催化事件，先按技术结构观察。",
                        "catalyst_events": [],
                        "catalyst_limitations": ["主题催化数据不可用。"],
                    },
                ),
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="159102",
                    rank=2,
                    total_score=90,
                    conclusion="高位观察",
                    score_breakdown_json={},
                    risk_flags_json=[],
                    rationale_json={"entry_timing_reason": "无主题综合关注。"},
                    metrics_json={
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "无主题综合关注。",
                        "latest_date": "2026-07-03",
                        "latest_value": 1.2,
                        "usable_days": 120,
                        "default_display_eligible": True,
                        "technical_score": 90,
                        "sector_trend_score": None,
                        "sector_trend_status": "unavailable",
                        "sector_trend_reason": "未分类主题无法计算板块趋势。",
                        "opportunity_score": None,
                        "opportunity_label": "暂无综合关注",
                        "opportunity_score_version": "opportunity_score_v2_unavailable",
                        "catalyst_score": 50,
                        "sentiment_heat_score": 50,
                        "catalyst_status": "unavailable",
                        "catalyst_summary": "暂无可用于评分的主题催化事件，先按技术结构观察。",
                        "catalyst_events": [],
                        "catalyst_limitations": ["主题催化数据不可用。"],
                    },
                ),
            ]
        )
        await session.commit()


async def _seed_observation_price_series(
    app,
    *,
    code: str,
    start_price: float = 1.0,
    daily_return: float = 0.002,
    days: int = 70,
    latest_date: date = date(2026, 6, 15),
) -> None:
    start = latest_date - timedelta(days=days - 1)
    async with app.state.db.session() as session:
        close = start_price
        for offset in range(days):
            period_return = 0.0 if offset == 0 else daily_return + (0.001 if offset % 2 else -0.001)
            close = close if offset == 0 else close * (1 + period_return)
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=2_000_000,
                    turnover=160_000_000,
                    pct_change=period_return * 100,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_short_research_status_seeds_about_200_assets(client) -> None:
    response = await client.get("/api/short-research/status")

    assert response.status_code == 200
    body = response.json()
    assert body["asset_count"] == 200
    assert body["fund_count"] == 117
    assert body["etf_count"] == 83
    assert body["data_health"] == []

    detailed = await client.get("/api/short-research/status?include_health=true")
    assert detailed.status_code == 200
    detailed_body = detailed.json()
    assert len(detailed_body["data_health"]) == 200
    assert all("一年持有" not in item["name"] for item in detailed_body["data_health"])


@pytest.mark.asyncio
async def test_short_research_signal_generation_is_deterministic_and_research_only(client, app) -> None:
    await _seed_short_research_history(app)

    response = await client.post("/api/short-research/signals/run", json={"as_of_date": "2026-06-05"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["summary"]["item_count"] == 200
    assert body["summary"]["research_only"] is True
    assert body["summary"]["experiment"]["portfolio_single_weight_cap"] == 0.3
    assert body["summary"]["label_validation"]["rule_version"] == "label_validation_v1"
    assert body["summary"]["label_validation"]["outcome_source"] == "stored_signal_items"
    assert body["items"]
    assert {item["conclusion"] for item in body["items"]}.issubset(allowed_conclusions())
    assert any(item["code"] == "110020" and item["asset_type"] == "fund" for item in body["items"])
    hot_etf = next(item for item in body["items"] if item["code"] == "512480")
    assert hot_etf["conclusion"] in {"高位观察", "谨慎观察", "短线观察"}
    assert "key_reason" in hot_etf["rationale"]
    assert hot_etf["rationale"]["research_only"] is True
    assert hot_etf["rationale"]["no_trade_instruction"] is True

    payload_text = json.dumps(body, ensure_ascii=False).lower()
    for forbidden in ["buy", "sell", "stop_loss", "take_profit", "target_price", "expected_return", "guaranteed_profit"]:
        assert forbidden not in payload_text

    latest = await client.get("/api/short-research/signals/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]

    fund_only = await client.post(
        "/api/short-research/signals/run",
        json={"as_of_date": "2026-06-05", "asset_type": "fund"},
    )
    assert fund_only.status_code == 200
    fund_body = fund_only.json()
    assert fund_body["summary"]["fund_count"] == fund_body["summary"]["item_count"]
    assert fund_body["summary"]["etf_count"] == 0
    assert all(item["asset_type"] == "fund" for item in fund_body["items"])

    latest_fund = await client.get("/api/short-research/signals/latest?asset_type=fund")
    assert latest_fund.status_code == 200
    assert latest_fund.json()["id"] == fund_body["id"]


@pytest.mark.asyncio
async def test_comprehensive_sort_uses_final_decision_score_not_theme_heat(client, app) -> None:
    await _seed_opportunity_signal_run(app)

    response = await client.get("/api/short-research/assets?asset_type=etf&sort=opportunity&universe=all")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    items = body["items"]
    assert [item["code"] for item in items] == ["159002", "159001"]
    by_code = {item["code"]: item for item in items}
    assert by_code["159002"]["total_score"] == 78

    theme_hot = by_code["159001"]
    assert theme_hot["total_score"] == 70
    assert theme_hot["technical_score"] == 70
    assert theme_hot["opportunity_score"] == 80
    assert theme_hot["opportunity_label"] == "主题强但等买点"
    assert theme_hot["catalyst_score"] == 90
    assert theme_hot["sentiment_heat_score"] == 80
    assert theme_hot["catalyst_summary"] == "宇树科技 IPO 催化机器人主题。"
    assert theme_hot["entry_timing_label"] == "冲高别追"
    assert any("冲高别追" in item for item in theme_hot["catalyst_limitations"])
    assert theme_hot["factor_profile_version"] == "etf_factor_profile_v1_degraded"
    assert theme_hot["factor_profile_score"] == 81.5
    assert theme_hot["factor_group_scores"]["theme_event"]["score"] == 90
    assert theme_hot["factor_availability"]["fund_flow_score"]["availability"] == "unavailable"
    assert theme_hot["risk_gates"][0]["active"] is True


@pytest.mark.asyncio
async def test_short_research_asset_detail_uses_cached_etf_signal_scores(client, app) -> None:
    await _seed_opportunity_signal_run(app)

    response = await client.get("/api/short-research/assets/etf/159001")

    assert response.status_code == 200
    asset = response.json()["asset"]
    assert asset["code"] == "159001"
    assert asset["total_score"] == 70
    assert asset["technical_score"] == 70
    assert asset["opportunity_score"] == 80
    assert asset["catalyst_score"] == 90
    assert asset["sentiment_heat_score"] == 80
    assert asset["factor_profile_version"] == "etf_factor_profile_v1_degraded"
    assert asset["factor_profile_score"] == 81.5
    assert asset["opportunity_breakdown"]["profile_version"] == "etf_factor_profile_v1_degraded"


@pytest.mark.asyncio
async def test_short_research_list_and_detail_expose_source_snapshot_metadata(client, app) -> None:
    await _seed_opportunity_signal_run(app)

    list_response = await client.get("/api/short-research/assets?asset_type=etf&universe=all")
    detail_response = await client.get("/api/short-research/assets/etf/159001")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    list_snapshot = list_response.json()["snapshot"]
    detail_snapshot = detail_response.json()["snapshot"]
    required_keys = {
        "snapshot_id",
        "score_version",
        "ranking_contract_hash",
        "scope_kind",
        "as_of_trade_date",
        "generated_at",
        "coverage_ratio",
        "freshness_status",
        "limitations",
    }
    assert required_keys <= set(list_snapshot)
    assert list_snapshot == detail_snapshot
    assert list_snapshot["snapshot_id"] is not None


@pytest.mark.asyncio
async def test_one_code_detail_preserves_persisted_global_rank(client, app) -> None:
    await _seed_opportunity_signal_run(app)

    response = await client.get("/api/short-research/assets/etf/159002")

    assert response.status_code == 200
    assert response.json()["asset"]["rank"] == 2


@pytest.mark.asyncio
async def test_short_research_assets_hide_unavailable_catalyst_scores(client, app) -> None:
    await _seed_opportunity_signal_run(app)

    response = await client.get("/api/short-research/assets?asset_type=etf&sort=opportunity&universe=all")

    assert response.status_code == 200
    items = {item["code"]: item for item in response.json()["items"]}
    unavailable = items["159002"]
    assert unavailable["opportunity_score"] is None
    assert unavailable["catalyst_score"] is None
    assert unavailable["sentiment_heat_score"] is None
    assert any("主题催化数据不可用" in item for item in unavailable["catalyst_limitations"])


@pytest.mark.asyncio
async def test_comprehensive_sort_allows_unavailable_catalyst_to_rank_first(client, app) -> None:
    await _seed_unavailable_opportunity_sort_run(app)

    response = await client.get("/api/short-research/assets?asset_type=etf&sort=opportunity&universe=all")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["code"] for item in items] == ["159011", "159010"]
    assert items[0]["opportunity_score"] is None


@pytest.mark.asyncio
async def test_short_research_assets_keep_sector_only_opportunity_score(client, app) -> None:
    await _seed_sector_trend_opportunity_run(app)

    response = await client.get("/api/short-research/assets?asset_type=etf&sort=opportunity&universe=all")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["code"] for item in items] == ["159101", "159102"]
    sector_only = items[0]
    assert sector_only["opportunity_score"] == 78
    assert sector_only["opportunity_label"] == "板块强但等催化"
    assert sector_only["sector_trend_score"] == 84
    assert sector_only["sector_trend_label"] == "板块强势"
    assert sector_only["sector_peer_count"] == 4
    assert sector_only["opportunity_score_version"] == "opportunity_score_v2_sector_only"
    assert sector_only["catalyst_score"] is None
    assert sector_only["sentiment_heat_score"] is None
    assert items[1]["opportunity_score"] is None


@pytest.mark.asyncio
async def test_short_research_asset_detail_returns_cached_sector_trend_scores(client, app) -> None:
    await _seed_sector_trend_opportunity_run(app)

    response = await client.get("/api/short-research/assets/etf/159101")

    assert response.status_code == 200
    asset = response.json()["asset"]
    assert asset["code"] == "159101"
    assert asset["technical_score"] == 76
    assert asset["sector_trend_score"] == 84
    assert asset["sector_trend_summary"] == "创新药上涨家数占优，板块趋势强。"
    assert asset["opportunity_score"] == 78
    assert asset["opportunity_score_version"] == "opportunity_score_v2_sector_only"


@pytest.mark.asyncio
async def test_short_research_entry_timing_labels_are_explained(client, app) -> None:
    healthy = _steady_uptrend()
    healthy[-1] = round(healthy[-2] * 0.992, 6)

    chasing = _steady_uptrend(step=0.012)
    chasing[-1] = round(chasing[-2] * 1.05, 6)

    broken = _steady_uptrend()
    broken[-1] = round(broken[-10] * 1.005, 6)

    weak_volume = _steady_uptrend()
    weak_volume[-1] = round(weak_volume[-20] * 0.97, 6)
    weak_turnovers = [100_000_000 for _item in weak_volume]
    weak_turnovers[-1] = 260_000_000

    stale = _steady_uptrend()

    await _seed_entry_timing_etf(app, code="560810", closes=healthy)
    await _seed_entry_timing_etf(app, code="560811", closes=chasing)
    await _seed_entry_timing_etf(app, code="560812", closes=broken)
    await _seed_entry_timing_etf(app, code="560813", closes=weak_volume, turnovers=weak_turnovers)
    await _seed_entry_timing_etf(app, code="560814", closes=stale, latest_date=date(2026, 5, 20))

    response = await client.post(
        "/api/short-research/signals/run",
        json={
            "as_of_date": "2026-06-05",
            "asset_type": "etf",
            "codes": ["560810", "560811", "560812", "560813", "560814"],
        },
    )

    assert response.status_code == 200
    items = {item["code"]: item for item in response.json()["items"]}
    assert items["560810"]["entry_timing_label"] == "健康回踩"
    assert items["560811"]["entry_timing_label"] == "冲高别追"
    assert items["560812"]["entry_timing_label"] == "跌破等待"
    assert items["560813"]["entry_timing_label"] == "放量转弱"
    assert items["560814"]["entry_timing_label"] == "数据不足"

    healthy_item = items["560810"]
    assert "今天" in healthy_item["entry_timing_reason"]
    assert "10日线" in healthy_item["entry_timing_reason"]
    assert healthy_item["metrics"]["entry_timing_label"] == "健康回踩"
    assert healthy_item["rationale"]["entry_timing_reason"] == healthy_item["entry_timing_reason"]
    assert healthy_item["metrics"]["today_return_pct"] < 0
    assert healthy_item["metrics"]["ma5"] is not None
    assert healthy_item["metrics"]["ma10"] is not None
    assert healthy_item["metrics"]["ma20"] is not None
    assert healthy_item["metrics"]["pullback_from_5d_high_pct"] <= 0


@pytest.mark.asyncio
async def test_etf_signal_validation_run_records_forward_outcomes(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "562001",
                "total_score": 92.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "验证样本测试。",
            }
        ],
    )
    await _seed_observation_price_series(app, code="562001", days=110, latest_date=date(2026, 7, 5))

    response = await client.post("/api/short-research/validation/run")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "success"
    assert body["source_ranking_snapshot"]["snapshot_id"] == body["source_signal_run_id"]
    assert body["source_ranking_snapshot"]["freshness_status"] in {"legacy", "unpublished", "unverified"}
    assert body["summary"]["evaluated_asset_count"] == 1
    items = body["items"]
    assert {item["horizon_days"] for item in items} >= {1, 3, 5, 10}
    assert any(item["sample_count"] > 0 and item["median_return"] is not None for item in items)
    assert body["summary"]["outcome_source"] == "stored_signal_items"
    assert body["summary"]["evaluated_asset_count"] == 1

    assets_response = await client.get("/api/short-research/assets?asset_type=etf&limit=1")
    assert assets_response.status_code == 200
    asset = assets_response.json()["items"][0]
    evidence = asset["validation_evidence"]
    assert set(evidence["horizons"]) >= {"1", "3", "5", "10"}
    assert evidence["sample_count"] > 0
    assert evidence["sample_quality"]["sample_count_total"] >= evidence["sample_count"]
    assert evidence["freshness"]["rule_version"] == "label_validation_v1"
    assert evidence["recent_examples"]

    latest = await client.get("/api/short-research/validation/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_etf_signal_validation_marks_insufficient_samples(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "562002",
                "total_score": 90.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "短样本验证测试。",
            }
        ],
    )
    await _seed_observation_price_series(app, code="562002", days=72)

    response = await client.post("/api/short-research/validation/run")
    assert response.status_code == 200

    items = response.json()["items"]
    assert items
    assert any(item["confidence"] == "insufficient" for item in items)


@pytest.mark.asyncio
async def test_etf_label_historical_replay_uses_only_past_data(client, app) -> None:
    latest_date = date(2026, 7, 20)
    start = latest_date - timedelta(days=89)
    replay_date = start + timedelta(days=69)
    base_closes = [round(1.0 + offset * 0.01, 6) for offset in range(70)]
    future_up = [round(base_closes[-1] * (1.01 ** offset), 6) for offset in range(1, 21)]
    future_down = [round(base_closes[-1] * (0.97 ** offset), 6) for offset in range(1, 21)]
    async with app.state.db.session() as session:
        for code, suffix in (("588991", "上涨未来"), ("588992", "下跌未来")):
            session.add(
                TradableEtf(
                    code=code,
                    name=f"历史回放{suffix}",
                    exchange="SH",
                    theme_tags_json=["历史回放"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
        await session.flush()
        for code, closes in (
            ("588991", [*base_closes, *future_up]),
            ("588992", [*base_closes, *future_down]),
        ):
            for offset, close in enumerate(closes):
                previous = closes[offset - 1] if offset > 0 else close
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=start + timedelta(days=offset),
                        open=close * 0.995,
                        high=close * 1.01,
                        low=close * 0.99,
                        close=close,
                        volume=2_000_000,
                        turnover=180_000_000,
                        pct_change=0.0 if offset == 0 else (close / previous - 1.0) * 100,
                    )
                )
        await session.commit()

    response = await client.post("/api/short-research/validation/historical-replay/run?days=40&max_assets=2")
    assert response.status_code == 200
    body = response.json()
    assert body["validation_mode"] == "historical_replay"
    assert body["summary"]["outcome_source"] == "historical_replay"
    assert body["summary"]["completed_samples"] > 0

    async with app.state.db.session() as session:
        rows = (
            await session.scalars(
                select(EtfLabelReplaySample)
                .where(
                    EtfLabelReplaySample.replay_date == replay_date,
                    EtfLabelReplaySample.horizon_days == 5,
                    EtfLabelReplaySample.asset_code.in_(["588991", "588992"]),
                )
                .order_by(EtfLabelReplaySample.asset_code.asc())
            )
        ).all()
    assert len(rows) == 2
    assert rows[0].label == rows[1].label
    assert rows[0].entry_timing_label == rows[1].entry_timing_label
    assert rows[0].metrics_json["score"] == rows[1].metrics_json["score"]
    assert rows[0].forward_return != rows[1].forward_return
    assert rows[0].metrics_json["no_lookahead_cutoff"] == replay_date.isoformat()


@pytest.mark.asyncio
async def test_etf_label_historical_replay_defaults_to_all_eligible_etfs(client, app) -> None:
    latest_date = date(2026, 7, 3)
    start = latest_date - timedelta(days=34)
    async with app.state.db.session() as session:
        for index in range(301):
            code = f"589{index:03d}"
            session.add(
                TradableEtf(
                    code=code,
                    name=f"全量回放ETF{index}",
                    exchange="SH",
                    theme_tags_json=["全量回放"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            for offset in range(35):
                close = 1.0 + offset * 0.002 + index * 0.000001
                previous = 1.0 + (offset - 1) * 0.002 + index * 0.000001 if offset else close
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=start + timedelta(days=offset),
                        open=close * 0.995,
                        high=close * 1.01,
                        low=close * 0.99,
                        close=close,
                        volume=2_000_000,
                        turnover=180_000_000,
                        pct_change=0.0 if offset == 0 else (close / previous - 1.0) * 100,
                    )
                )
        await session.commit()

    response = await client.post("/api/short-research/validation/historical-replay/run?days=30")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["universe_scope"] == "all_eligible"
    assert body["summary"]["asset_count"] == 301
    assert body["summary"]["batch_size"] == 25


@pytest.mark.asyncio
async def test_etf_label_historical_replay_api_separates_tracks_and_does_not_notify(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "588993",
                "total_score": 88.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "前瞻验证测试。",
            }
        ],
    )
    await _seed_observation_price_series(app, code="588993", days=130, latest_date=date(2026, 7, 20))

    forward = await client.post("/api/short-research/validation/run")
    replay = await client.post("/api/short-research/validation/run?validation_mode=historical_replay&days=45&max_assets=1")
    assert forward.status_code == 200
    assert replay.status_code == 200
    assert forward.json()["validation_mode"] == "forward_live"
    assert replay.json()["validation_mode"] == "historical_replay"

    latest_forward = await client.get("/api/short-research/validation/latest?validation_mode=forward_live")
    latest_replay = await client.get("/api/short-research/validation/latest?validation_mode=historical_replay")
    assert latest_forward.status_code == 200
    assert latest_replay.status_code == 200
    assert latest_forward.json()["validation_mode"] == "forward_live"
    assert latest_replay.json()["validation_mode"] == "historical_replay"

    assets = await client.get("/api/short-research/assets?asset_type=etf&limit=1")
    assert assets.status_code == 200
    evidence = assets.json()["items"][0]["validation_evidence"]
    assert "historical_replay" in evidence["evidence_tracks"]
    assert "forward_live" in evidence["evidence_tracks"]

    async with app.state.db.session() as session:
        notification_count = await session.scalar(select(func.count()).select_from(NotificationLog))
        alert_count = await session.scalar(select(func.count()).select_from(TrackedPositionAlert))
    assert notification_count == 0
    assert alert_count == 0


@pytest.mark.asyncio
async def test_score_bucket_validation_requires_current_full_ranking_contract(client, app) -> None:
    seeded = await _seed_score_bucket_signal_runs(app)

    response = await client.post("/api/short-research/validation/score-buckets/run?days=180")

    assert response.status_code == 200
    body = response.json()
    summary = body["summary"]
    assert body["validation_mode"] == "score_bucket_replay"
    assert summary["score_field"] == "ranking_score"
    assert summary["top_n"] == [5, 10, 20, 50]
    assert summary["baseline"] == "all_scored"
    assert summary["source_signal_run_ids"] == [seeded["latest_run_id"]]
    assert seeded["old_run_id"] not in summary["source_signal_run_ids"]
    assert seeded["partial_run_id"] not in summary["source_signal_run_ids"]
    assert seeded["mismatched_contract_run_id"] not in summary["source_signal_run_ids"]
    assert summary["excluded_unavailable_score_count"] == 3
    assert seeded["unavailable_code"] in summary["excluded_codes"]["missing_ranking_score"]
    assert seeded["missing_score_code"] in summary["excluded_codes"]["missing_ranking_score"]
    assert seeded["non_finite_score_code"] in summary["excluded_codes"]["non_finite_ranking_score"]
    assert summary["excluded_items"]["missing_ranking_score"] == [
        {"asset_code": seeded["unavailable_code"], "key": "missing_ranking_score", "signal_date": "2026-07-03"},
        {"asset_code": seeded["missing_score_code"], "key": "missing_ranking_score", "signal_date": "2026-07-03"},
    ]

    groups = {(item["label"], item["entry_timing_label"]): item for item in summary["groups"]}
    available_codes = seeded["available_codes"]
    assert groups[("Top 5", "cumulative")]["selected_codes"] == available_codes[:5]
    assert groups[("Top 10", "cumulative")]["selected_codes"] == available_codes[:10]
    assert groups[("1-5", "marginal")]["selected_codes"] == available_codes[:5]
    assert groups[("6-10", "marginal")]["selected_codes"] == available_codes[5:10]
    assert groups[("11-20", "marginal")]["selected_codes"] == available_codes[10:12]
    assert groups[("all_scored", "baseline")]["selected_codes"] == available_codes
    assert seeded["old_only_code"] not in groups[("Top 5", "cumulative")]["selected_codes"]
    assert seeded["partial_only_code"] not in groups[("all_scored", "baseline")]["selected_codes"]
    assert seeded["mismatched_contract_code"] not in groups[("all_scored", "baseline")]["selected_codes"]

    top5_window = groups[("Top 5", "cumulative")]["windows"]["5"]
    assert top5_window["sample_count"] == 5
    assert top5_window["win_rate"] == 1.0
    assert top5_window["median_return"] > 0
    assert groups[("Top 50", "cumulative")]["windows"]["10"]["sample_count"] == 12

    latest = await client.get("/api/short-research/validation/score-buckets/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]

    status = await client.get("/api/short-research/status")
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["score_bucket_validation"]["validation_mode"] == "score_bucket_replay"
    assert status_body["score_bucket_validation_generated_at"] is not None


@pytest.mark.asyncio
async def test_canonical_assets_ignore_later_partial_run(client, app) -> None:
    seeded = await _seed_score_bucket_signal_runs(app)

    response = await client.get("/api/short-research/assets?asset_type=etf&universe=all&limit=100")

    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["items"]}
    assert seeded["available_codes"][0] in codes
    assert seeded["partial_only_code"] not in codes
    assert seeded["mismatched_contract_code"] not in codes


@pytest.mark.asyncio
async def test_stale_full_snapshot_is_not_returned_as_current_cache(client, app) -> None:
    seeded = await _seed_score_bucket_signal_runs(app)
    async with app.state.db.session() as session:
        for run_id in (seeded["partial_run_id"], seeded["mismatched_contract_run_id"]):
            run = await session.get(ShortResearchSignalRun, run_id)
            assert run is not None
            run.status = "failed"
        await session.commit()

    response = await client.get(f"/api/short-research/assets/etf/{seeded['available_codes'][0]}")

    assert response.status_code == 503
    detail = str(response.json().get("detail", "")).lower()
    assert "stale" in detail or "等待" in detail


@pytest.mark.asyncio
async def test_every_validation_mode_has_no_live_domain_side_effects(client, app) -> None:
    await _seed_score_bucket_signal_runs(app)
    protected_models = (
        ShortResearchSignalRun,
        ShortResearchSignalItem,
        EtfOptimizedAllocationSnapshot,
        EtfOptimizedAllocationItem,
        TrackedPosition,
        TrackedPositionAlert,
        NotificationLog,
    )

    async def snapshots() -> dict[str, tuple[tuple[Any, ...], ...]]:
        async with app.state.db.session() as session:
            values: dict[str, tuple[tuple[Any, ...], ...]] = {}
            for model in protected_models:
                rows = (await session.scalars(select(model).order_by(model.id.asc()))).all()
                columns = tuple(model.__table__.columns)
                values[model.__tablename__] = tuple(
                    tuple(getattr(row, column.name) for column in columns) for row in rows
                )
            return values

    validation_requests = (
        "/api/short-research/validation/run?validation_mode=forward_live",
        "/api/short-research/validation/run?validation_mode=historical_replay&days=30&max_assets=1",
        "/api/short-research/validation/score-buckets/run?days=180",
    )
    for path in validation_requests:
        before = await snapshots()
        response = await client.post(path)
        assert response.status_code == 200
        assert await snapshots() == before


@pytest.mark.asyncio
async def test_etf_exit_hyperopt_latest_endpoint_returns_research_only_evidence(client, app) -> None:
    async with app.state.db.session() as session:
        run = await _seed_exit_hyperopt_run(session)
        await session.commit()

    response = await client.get("/api/short-research/etf-exit-hyperopt/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run.id
    assert body["research_only"] is True
    assert body["no_trade_instruction"] is True
    assert body["calibration_rule_version"] == "etf_exit_calibration_v1"
    assert body["items"][0]["status"] == "candidate"
    assert body["items"][0]["source_reliability"] == "verified_daily_close"


@pytest.mark.asyncio
async def test_etf_exit_hyperopt_manual_run_endpoint_returns_structured_summary(client, app, monkeypatch) -> None:
    from app.api.routes import short_research as short_research_routes

    async def fake_run_etf_exit_hyperopt(session, **kwargs):
        assert kwargs["days"] == 180
        assert kwargs["max_assets"] == 20
        return await _seed_exit_hyperopt_run(session)

    monkeypatch.setattr(short_research_routes, "run_etf_exit_hyperopt", fake_run_etf_exit_hyperopt)

    response = await client.post(
        "/api/short-research/etf-exit-hyperopt/run",
        json={"days": 180, "max_assets": 20, "objective": "stability_first"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["research_only"] is True
    assert body["summary"]["auto_applied"] is False
    assert body["items"]
    async with app.state.db.session() as session:
        notification_count = await session.scalar(select(func.count()).select_from(NotificationLog))
        alert_count = await session.scalar(select(func.count()).select_from(TrackedPositionAlert))
    assert notification_count == 0
    assert alert_count == 0


@pytest.mark.asyncio
async def test_short_research_asset_detail_returns_charts_and_beginner_explanations(client, app) -> None:
    await _seed_short_research_history(app)

    response = await client.get("/api/short-research/assets/fund/110020")

    assert response.status_code == 200
    body = response.json()
    assert body["asset"]["code"] == "110020"
    assert body["asset"]["asset_type"] == "fund"
    assert body["chart"]
    assert body["return_windows"]["return_20d"] is not None
    assert set(body["explanation_sections"]) == {"投资方向", "为什么上榜", "今日买点", "主要风险", "反方提醒", "数据说明"}
    assert "公开基金净值数据" in body["explanation_sections"]["数据说明"]


@pytest.mark.asyncio
async def test_short_research_filters_sort_and_data_sync_endpoint(client, app, monkeypatch) -> None:
    await _seed_short_research_history(app)
    signal = await client.post(
        "/api/short-research/signals/run",
        json={"as_of_date": "2026-06-05", "asset_type": "etf"},
    )
    assert signal.status_code == 200

    filtered = await client.get("/api/short-research/assets?asset_type=etf&theme=半导体&sort=return_20d")

    assert filtered.status_code == 200
    items = filtered.json()["items"]
    assert items
    assert all(item["asset_type"] == "etf" for item in items)
    assert any(item["code"] == "512480" for item in items)

    from app.services.short_research import service as short_research_service

    async def fake_fund_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"funds": 0, "rows_inserted": 0, "rows_updated": 0, "failed": 0, "failures": []}

    async def fake_etf_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"etfs": 1, "inserted": 3, "updated": 0, "failed": 0, "failures": []}

    monkeypatch.setattr(short_research_service, "sync_fund_nav_history", fake_fund_sync)
    monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

    sync = await client.post(
        "/api/short-research/data/sync",
        json={"from_date": "2026-06-01", "to_date": "2026-06-05", "asset_type": "etf", "codes": ["512480"]},
    )

    assert sync.status_code == 200
    body = sync.json()
    assert body["asset_count"] == 1
    assert body["etfs"]["inserted"] == 3
    assert body["failed"] == 0


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_filters_out_high_watch_and_bad_timing(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560901",
                "total_score": 94.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "今日回踩 5 日线附近，短线结构可再观察。",
            },
            {
                "code": "560902",
                "total_score": 99.0,
                "conclusion": "高位观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "今日买点标签示例。",
            },
            {
                "code": "560903",
                "total_score": 98.0,
                "conclusion": "短线观察",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "今日短线已转弱，等待修复。",
            },
            {
                "code": "560904",
                "total_score": 97.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "趋势延续，仍可观察。",
            },
            {
                "code": "560910",
                "total_score": 93.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "第四只合格 ETF，用于凑满全仓组合。",
                "theme_tags": ["红利"],
            },
            {
                "code": "560911",
                "total_score": 92.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "第五只合格 ETF，用于验证权重归一。",
                "theme_tags": ["消费"],
            },
            {
                "code": "560905",
                "total_score": 96.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "短线指标偏强。",
                "risk_flags": ["追高风险", "流动性不足"],
            },
            {
                "code": "560906",
                "total_score": 95.0,
                "conclusion": "短线观察",
                "entry_timing_label": "数据不足",
                "entry_timing_reason": "今日数据缺口。",
            },
        ],
    )

    response = await client.get("/api/short-research/observation-portfolio?limit=4")
    assert response.status_code == 200

    body = response.json()
    assert body["asset_type"] == "etf"
    assert body["source_ranking_snapshot"]["snapshot_id"] is not None
    assert body["source_ranking_snapshot"]["freshness_status"] in {"legacy", "unpublished", "unverified"}
    assert body["cash_weight"] == 0.0
    assert body["weight_sum"] == 1.0
    assert body["portfolio_mode"] == "risk_on"
    assert body["market_regime"] == "risk_on"
    assert body["risk_exposure_weight"] == 1.0
    assert body["defensive_weight"] == 0.0
    assert body["defensive_items"] == []
    assert body["target_invested_weight"] == 1.0
    assert body["single_weight_cap"] == 0.3
    assert body["total_exposure_cap"] == 1.0
    assert body["constraint_summary"]["single_weight_cap"] == 0.3
    assert body["constraint_summary"]["target_invested_weight"] == 1.0
    assert body["methodology"]
    assert len(body["items"]) == 4
    assert [item["code"] for item in body["items"]] == ["560904", "560901", "560910", "560911"]
    assert all(item["target_weight"] <= 0.3 for item in body["items"])
    assert all(item["weight_reason_json"] for item in body["items"])
    assert all(item["code"] not in {"560902", "560903", "560905", "560906"} for item in body["items"])
    assert {item["code"] for item in body["watch_only_items"]} >= {"560902", "560903"}
    assert {item["code"] for item in body["excluded_items"]} >= {"560905", "560906"}
    assert "今日买点：趋势延续" in body["items"][0]["risk_reasons"]
    assert any("买点原因：" in item for item in body["items"][0]["risk_reasons"])
    assert (
        "高位/追高/数据不足资产会分到观察或等待分组，不进入主组合权重。"
        in (body["note"] or "")
    )


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_uses_defensive_layer_when_attack_candidates_are_insufficient(
    client,
    app,
) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560931",
                "total_score": 94.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "唯一进攻候选。",
                "theme_tags": ["科技"],
            },
            {
                "code": "560932",
                "total_score": 91.0,
                "conclusion": "谨慎观察",
                "entry_timing_label": "跌破等待",
                "entry_timing_reason": "债券候选轻微等待，用作防守层测试。",
                "theme_tags": ["债券"],
            },
            {
                "code": "560933",
                "total_score": 90.0,
                "conclusion": "谨慎观察",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "黄金候选偏热，只能作为防守参考。",
                "theme_tags": ["黄金"],
            },
            {
                "code": "560934",
                "total_score": 89.0,
                "conclusion": "谨慎观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "红利候选用于防守补位。",
                "theme_tags": ["红利"],
            },
            {
                "code": "560935",
                "total_score": 88.0,
                "conclusion": "谨慎观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "低波动宽基候选用于防守补位。",
                "theme_tags": ["宽基"],
                "volatility_20d": 0.012,
                "max_drawdown_60d": -0.03,
            },
        ],
    )

    response = await client.get("/api/short-research/observation-portfolio?limit=4")
    assert response.status_code == 200

    body = response.json()
    assert body["portfolio_mode"] == "neutral"
    assert body["market_regime"] == "neutral"
    assert body["cash_weight"] == 0.0
    assert body["weight_sum"] == 1.0
    assert body["risk_exposure_weight"] > 0
    assert body["defensive_weight"] > 0
    assert len(body["items"]) == 1
    assert len(body["defensive_items"]) == 3
    assert all(item["target_weight"] <= 0.3 for item in [*body["items"], *body["defensive_items"]])
    assert {item["code"] for item in body["defensive_items"]}.issubset({"560932", "560933", "560934", "560935"})
    assert "防守" in body["note"]


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_persists_snapshot(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560921",
                "total_score": 94.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "健康回踩，适合作为观察权重测试。",
                "theme_tags": ["红利"],
            },
            {
                "code": "560922",
                "total_score": 93.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "趋势延续，适合作为观察权重测试。",
                "theme_tags": ["消费"],
            },
            {
                "code": "560923",
                "total_score": 92.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "健康回踩，第三只权重测试。",
                "theme_tags": ["医药"],
            },
            {
                "code": "560924",
                "total_score": 91.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "趋势延续，第四只权重测试。",
                "theme_tags": ["金融"],
            },
        ],
    )

    async with app.state.db.session() as session:
        snapshot = await run_etf_observation_portfolio_optimization(session, limit=4)
        snapshot_id = snapshot.id

    response = await client.get("/api/short-research/observation-portfolio?limit=4")
    assert response.status_code == 200

    body = response.json()
    assert body["snapshot_id"] == snapshot_id
    assert body["generated_at"] is not None
    assert len(body["items"]) == 4
    assert all(item["target_weight"] <= 0.3 for item in body["items"])
    assert body["constraint_summary"]["target_invested_weight"] == 1.0
    assert body["total_exposure_cap"] == 1.0
    assert body["weight_sum"] == 1.0
    first_item = body["items"][0]
    assert first_item["weight_explanation"]
    assert "观察权重" in first_item["weight_explanation"]
    assert first_item["decision_factors"]["target_weight"] == first_item["target_weight"]
    assert first_item["weight_reason_json"]["final_weight"] == first_item["target_weight"]
    assert first_item["metrics"]["portfolio_weight_explanation"] == first_item["weight_explanation"]
    assert body["risk_summary"]["single_weight_cap"] == 0.3
    assert body["data_reliability_summary"]["item_count"] == len(body["items"])


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_no_match_returns_full_cash(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560907",
                "total_score": 99.0,
                "conclusion": "高位观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "高位观察示例。",
            },
            {
                "code": "560908",
                "total_score": 98.0,
                "conclusion": "短线观察",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "短线偏热，不适合再买。",
            },
            {
                "code": "560909",
                "total_score": 97.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "偏强。",
                "risk_flags": ["追高风险"],
            },
        ],
    )

    response = await client.get("/api/short-research/observation-portfolio?limit=5")
    assert response.status_code == 200

    body = response.json()
    assert body["items"] == []
    assert len(body["satellite_items"]) == 1
    assert body["satellite_items"][0]["code"] == "560907"
    assert body["watch_only_items"]
    assert body["excluded_items"] == []
    assert body["defensive_items"] == []
    assert body["cash_weight"] == pytest.approx(0.85)
    assert body["weight_sum"] == pytest.approx(0.15)
    assert body["portfolio_mode"] == "neutral"
    assert body["market_regime"] == "neutral"
    assert body["satellite_weight"] == pytest.approx(0.15)
    assert "小仓" in body["note"]


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_allows_partial_allocation(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560941",
                "total_score": 94.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "第一只合格 ETF。",
                "theme_tags": ["科技"],
            },
            {
                "code": "560942",
                "total_score": 92.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "第二只合格 ETF。",
                "theme_tags": ["红利"],
            },
            {
                "code": "560943",
                "total_score": 91.0,
                "conclusion": "高位观察",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "高位不追。",
            },
        ],
    )

    response = await client.get("/api/short-research/observation-portfolio?limit=4")
    assert response.status_code == 200

    body = response.json()
    assert body["portfolio_mode"] == "risk_on"
    assert body["weight_sum"] == 0.6
    assert body["cash_weight"] == 0.4
    assert len(body["items"]) == 2
    assert all(item["target_weight"] <= 0.3 for item in body["items"])
    assert body["cash_reason"]
    assert "等待" in body["cash_reason"]


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_reduces_theme_and_correlation_overlap(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "561001",
                "total_score": 99.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "科技方向健康回踩。",
                "theme_tags": ["科技"],
            },
            {
                "code": "561002",
                "total_score": 98.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "同方向健康回踩。",
                "theme_tags": ["科技"],
            },
            {
                "code": "561003",
                "total_score": 97.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "红利方向趋势延续。",
                "theme_tags": ["红利"],
            },
            {
                "code": "561004",
                "total_score": 96.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "医药方向健康回踩。",
                "theme_tags": ["医药"],
            },
            {
                "code": "561005",
                "total_score": 95.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "消费方向趋势延续。",
                "theme_tags": ["消费"],
            },
        ],
    )
    await _seed_observation_price_series(app, code="561001", daily_return=0.002)
    await _seed_observation_price_series(app, code="561002", daily_return=0.002)

    response = await client.get("/api/short-research/observation-portfolio?limit=5")
    assert response.status_code == 200

    body = response.json()
    item_codes = {item["code"] for item in body["items"]}
    watch_codes = {item["code"] for item in body["watch_only_items"]}
    assert "561001" in item_codes
    assert "561002" not in item_codes
    assert "561002" in watch_codes
    assert any("相关性" in "；".join(item["risk_reasons"]) for item in body["watch_only_items"])
    assert all(item["target_weight"] <= 0.3 for item in body["items"])
    assert body["weight_sum"] == 1.0
    assert body["cash_weight"] == 0.0



