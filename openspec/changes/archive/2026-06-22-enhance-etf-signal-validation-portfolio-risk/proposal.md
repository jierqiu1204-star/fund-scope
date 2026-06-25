## Why

FundScope already ranks ETF candidates and sends holding alerts, but the user still has to trust labels like "短线观察" and "健康回踩" without seeing whether those labels historically worked. The next refinement should make the system evidence-driven: validate signal outcomes, allocate observation weights with risk constraints, and make exit alerts adapt to each ETF's volatility instead of using one fixed threshold.

## What Changes

- Add ETF signal effectiveness validation:
  - Track future 1/3/5/10 trading-day outcomes for labels and entry timing states.
  - Show win rate, average return, median return, worst drawdown, sample size, and freshness.
  - Mark labels with insufficient samples as "样本不足", not reliable.
- Add constrained observation portfolio optimization:
  - Replace simple near-equal weights with a risk-aware optimizer.
  - Use score, volatility, drawdown, correlation, liquidity, and theme concentration.
  - Set a single ETF max weight of `30%`.
  - Avoid assigning weight to stale, estimated, unavailable, or decision-ineligible assets.
- Add volatility-adaptive exit alert thresholds:
  - Keep existing alert categories: `hard_stop`, `trailing_take_profit`, `trend_weakening`, `take_profit_watch`, `exit_watch`.
  - Calculate stop-loss and trailing-profit thresholds from recent volatility and drawdown profile.
  - Persist the threshold explanation so email and UI can show "why this line is here".
- Keep AI constrained:
  - AI may explain validation and risk context.
  - AI must not override rules, alter scores, or generate direct buy/sell commands.
- No real trading:
  - No broker connection.
  - No automatic order placement.
  - Alerts remain manual-decision reminders.

## Capabilities

### New Capabilities

- `etf-signal-validation`: Validates ETF ranking labels and entry timing states against future outcomes.
- `etf-observation-portfolio-optimization`: Produces risk-aware observation portfolio weights with explicit constraints.

### Modified Capabilities

- `short-term-research`: Adds validated signal quality summaries and displays whether labels have enough evidence.
- `intraday-etf-watch`: Uses optimized observation scope and exposes whether live score changes are supported by validation data.
- `tracked-position-exit-strategy`: Replaces fixed exit thresholds with volatility-adaptive thresholds while preserving existing alert categories.
- `investment-reminders`: Includes adaptive-threshold reasons and suppresses alerts when decision data is not reliable.
- `market-data-reliability`: Ensures validation, optimization, and alerts only use decision-eligible data.

## Impact

- Backend:
  - New services for signal outcome validation, portfolio optimization, and adaptive exit threshold calculation.
  - New persistence for validation runs/items and optimized observation portfolio snapshots.
  - Extensions to tracked position alert context for dynamic thresholds.
- API:
  - New read endpoints for validation summaries and optimized observation portfolio snapshots.
  - Existing `/short-term` and tracked-position responses get additional explanation fields without breaking old fields.
- Frontend:
  - `/short-term` shows label evidence, optimized observation weights, and adaptive exit reasons.
  - "我的持仓" explains whether an alert threshold is fixed, volatility-adjusted, or unavailable.
- Jobs:
  - Daily validation refresh after short-term ranking generation.
  - Portfolio optimization refresh after validation and ranking are available.
  - Intraday alerts continue during trading sessions but use adaptive thresholds.
- Dependencies:
  - Prefer a small in-project optimizer using `numpy/pandas` first.
  - Do not introduce heavy frameworks like Qlib, FinRL, or Riskfolio-Lib in the first implementation unless explicitly approved later.
