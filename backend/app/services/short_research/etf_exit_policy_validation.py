from __future__ import annotations

from typing import Any

POLICY_INSURANCE_STOP = "insurance_stop"
POLICY_PROFIT_PROTECTION = "profit_protection"
POLICY_TREND_GUARD = "trend_guard"
POLICY_PORTFOLIO_EXIT = "portfolio_exit"
POLICY_RESEARCH_ONLY = "research_only"
POLICY_EXIT_RISK_VALIDATION = "exit_risk_validation"

MIN_POLICY_SAMPLE_COUNT = 8

_SIGNAL_POLICY_CLASS = {
    "hard_stop": POLICY_INSURANCE_STOP,
    "trailing_take_profit": POLICY_PROFIT_PROTECTION,
    "take_profit_watch": POLICY_PROFIT_PROTECTION,
    "trend_weakening": POLICY_TREND_GUARD,
    "confirmed_trend_weakening": POLICY_TREND_GUARD,
    "exit_watch": POLICY_PORTFOLIO_EXIT,
}

_POLICY_LABELS = {
    POLICY_INSURANCE_STOP: "保险止损",
    POLICY_PROFIT_PROTECTION: "盈利保护",
    POLICY_TREND_GUARD: "趋势警戒",
    POLICY_PORTFOLIO_EXIT: "组合退出",
    POLICY_EXIT_RISK_VALIDATION: "退出规则调参",
    POLICY_RESEARCH_ONLY: "研究候选",
}

_POLICY_USAGE = {
    POLICY_INSURANCE_STOP: "用于控制尾部亏损，重点看少亏和止损后是否快速反弹。",
    POLICY_PROFIT_PROTECTION: "用于保护已有浮盈，重点看回吐后是否继续下跌，以及是否卖飞。",
    POLICY_TREND_GUARD: "用于趋势变弱预警，默认不单独当卖出指令，更适合作为禁止加仓或减仓辅助。",
    POLICY_PORTFOLIO_EXIT: "用于组合/标签退化后的降暴露判断，重点看退出后是否继续走弱。",
    POLICY_EXIT_RISK_VALIDATION: "用于参数候选研究，未人工批准前不影响实时邮件规则。",
    POLICY_RESEARCH_ONLY: "只作为研究证据，不参与实时提醒。",
}


def policy_class_for_signal(signal_type: str | None) -> str:
    return _SIGNAL_POLICY_CLASS.get(signal_type or "", POLICY_RESEARCH_ONLY)


def policy_class_label(policy_class: str | None) -> str:
    return _POLICY_LABELS.get(policy_class or "", policy_class or "旧口径")


def recommended_usage_for_policy(policy_class: str | None) -> str:
    return _POLICY_USAGE.get(policy_class or "", "旧口径证据只能参考，不能证明当前实时规则。")


def evidence_status_for_policy(
    *,
    sample_count: int,
    evidence_level: str | None,
    run_evidence_status: str | None,
    approved_for_live: bool = False,
    research_only: bool = True,
    min_samples: int = MIN_POLICY_SAMPLE_COUNT,
) -> str:
    if approved_for_live:
        return "approved_live"
    if research_only:
        if run_evidence_status not in {None, "同源已验证"}:
            return "not_current_contract"
        if sample_count < min_samples or evidence_level == "样本不足":
            return "sample_insufficient"
        return "research_only_verified"
    if sample_count < min_samples:
        return "sample_insufficient"
    return "verified"


def strong_conclusion_allowed(
    *,
    sample_count: int,
    evidence_level: str | None,
    run_evidence_status: str | None,
    approved_for_live: bool = False,
    min_samples: int = MIN_POLICY_SAMPLE_COUNT,
) -> bool:
    return approved_for_live or (
        run_evidence_status == "同源已验证" and sample_count >= min_samples and evidence_level != "样本不足"
    )


def _percent_metric(key: str, label: str, value: float | None) -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "format": "percent"}


def kpi_summary_for_item(
    *,
    signal_type: str,
    sample_count: int,
    success_avoidance_rate: float | None,
    false_stop_rate: float | None,
    sold_too_early_rate: float | None,
    avg_avoided_drawdown: float | None,
    avg_missed_upside: float | None,
    avg_forward_return: float | None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy_class = policy_class_for_signal(signal_type)
    raw_metrics = metrics or {}
    if policy_class == POLICY_INSURANCE_STOP:
        kpis = [
            _percent_metric("tail_loss_control_rate", "尾部亏损控制", success_avoidance_rate),
            _percent_metric("rebound_after_stop_rate", "止损后反弹率", false_stop_rate),
            _percent_metric("avg_avoided_drawdown", "平均后续下探", avg_avoided_drawdown),
            _percent_metric("avg_forward_return", "平均后续收益", avg_forward_return),
        ]
        focus = "保险止损不是预测卖后必跌，重点看是否减少继续下探。"
    elif policy_class == POLICY_PROFIT_PROTECTION:
        kpis = [
            _percent_metric("profit_protection_rate", "浮盈保护有效率", success_avoidance_rate),
            _percent_metric("missed_upside_rate", "卖飞率", sold_too_early_rate),
            _percent_metric("avg_missed_upside", "平均后续上冲", avg_missed_upside),
            _percent_metric("avg_forward_return", "平均后续收益", avg_forward_return),
        ]
        focus = "盈利保护重点看回吐提醒后是否继续走弱，以及卖飞成本。"
    elif policy_class == POLICY_TREND_GUARD:
        kpis = [
            _percent_metric("trend_guard_rate", "趋势警戒有效率", success_avoidance_rate),
            _percent_metric("false_guard_rate", "警戒后反弹率", false_stop_rate),
            _percent_metric("avg_forward_return", "平均后续收益", avg_forward_return),
        ]
        focus = "趋势警戒默认只是风控辅助，不应单独作为卖出指令。"
    else:
        kpis = [
            _percent_metric("exposure_reduction_help_rate", "降暴露有效率", success_avoidance_rate),
            _percent_metric("false_exit_rate", "退出后反弹率", false_stop_rate),
            _percent_metric("avg_forward_return", "平均后续收益", avg_forward_return),
        ]
        focus = "组合退出重点看降暴露后是否降低后续风险。"

    return {
        "policy_class": policy_class,
        "policy_class_label": policy_class_label(policy_class),
        "focus": focus,
        "recommended_usage": recommended_usage_for_policy(policy_class),
        "sample_count": sample_count,
        "window_days": raw_metrics.get("window_days", 5),
        "metrics": kpis,
    }
