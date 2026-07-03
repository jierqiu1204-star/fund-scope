"""ETF exit-rule calibration facade.

This module is the public research-layer boundary for ETF exit calibration.
It intentionally does not import notifier, tracked-position mutation paths, or API modules.
"""

from app.services.short_research.etf_exit_hyperopt import (
    CALIBRATION_RULE_VERSION,
    EXECUTION_MODEL_DAILY_CLOSE,
    EXECUTION_MODEL_INTRADAY_ALERT,
    OBJECTIVE_STABILITY_FIRST,
    RULE_VERSION,
    STATUS_APPROVED,
    STATUS_CANDIDATE,
    STATUS_EVIDENCE_INSUFFICIENT,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    HyperoptIntradayPoint,
    HyperoptPricePoint,
    etf_exit_hyperopt_payload,
    latest_etf_exit_hyperopt_run,
    run_etf_exit_hyperopt,
    simulate_intraday_exit_rule,
)

__all__ = [
    "CALIBRATION_RULE_VERSION",
    "EXECUTION_MODEL_DAILY_CLOSE",
    "EXECUTION_MODEL_INTRADAY_ALERT",
    "HyperoptIntradayPoint",
    "HyperoptPricePoint",
    "OBJECTIVE_STABILITY_FIRST",
    "RULE_VERSION",
    "STATUS_APPROVED",
    "STATUS_CANDIDATE",
    "STATUS_EVIDENCE_INSUFFICIENT",
    "STATUS_EXPIRED",
    "STATUS_REJECTED",
    "etf_exit_hyperopt_payload",
    "latest_etf_exit_hyperopt_run",
    "run_etf_exit_hyperopt",
    "simulate_intraday_exit_rule",
]
