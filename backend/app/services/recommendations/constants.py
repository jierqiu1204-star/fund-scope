from __future__ import annotations

ASSET_TYPE_FUND = "fund"
ASSET_TYPE_STOCK = "stock"

RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"

SAFE_LABELS = {
    ASSET_TYPE_FUND: "研究候选",
    ASSET_TYPE_STOCK: "观察候选",
}

RECOMMENDATION_DISCLAIMER = (
    "These screening results are for research support only. "
    "FundScope does not execute trades and does not provide trade commands, price targets, "
    "or return forecasts."
)
