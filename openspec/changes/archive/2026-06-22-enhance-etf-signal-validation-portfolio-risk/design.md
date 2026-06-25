## Context

FundScope currently focuses on short-term ETF/fund research, ETF intraday watch, tracked holdings, and email reminders. The system has already separated buy-observation labels from holding-exit states, and it has strict data reliability rules to avoid using stale or fallback data for decisions.

The remaining gap is evidence. Labels and scores are visible, but the user cannot yet see whether a label historically led to better forward returns. Observation portfolio weights are still simple and can over-concentrate in similar themes. Exit thresholds exist, but fixed thresholds do not fit ETFs with different volatility profiles.

## Goals / Non-Goals

**Goals:**

- Validate ETF labels and entry timing states with forward outcome statistics.
- Use validation output to explain confidence, not to claim certainty.
- Generate observation portfolio weights with explicit risk constraints, including a single ETF max weight of 30%.
- Make holding exit thresholds volatility-adaptive and explainable.
- Keep all decision paths data-reliability gated.
- Keep the UI understandable for a financial beginner.

**Non-Goals:**

- No broker connection or automatic ETF trading.
- No AI-generated buy/sell commands.
- No heavy quant framework integration in this iteration.
- No training of reinforcement learning or deep learning models.
- No guarantee that validation results predict future returns.

## Decisions

### 1. Build Validation As A Separate Research Layer

Decision: Create `etf-signal-validation` as a separate service and storage layer that reads historical signal runs and price data, then writes validation summaries.

Rationale: This avoids mixing research evidence with live ranking logic. A label can remain visible while being marked as "样本不足" or "近期有效性较弱".

Alternatives considered:

- Recompute validation inside `/short-term` on page load: rejected because it would slow the page and hide reproducibility.
- Store only frontend-derived validation: rejected because evidence should be server-side and auditable.

### 2. Use Forward Windows Rather Than One Outcome

Decision: Validate 1/3/5/10 trading-day forward returns, worst drawdown after signal, and sample count.

Rationale: The user trades short-term, sometimes one or two weeks, so multiple horizons are needed. One horizon would overfit the UI to one behavior.

### 3. Optimize Observation Weights With A Small Deterministic Engine

Decision: Implement a simple deterministic optimizer in-project using existing `numpy/pandas` style calculations before adding any external optimization dependency.

Rationale: PyPortfolioOpt and Riskfolio-Lib are useful references, but adding solver dependencies increases deployment risk. A first version can compute risk-adjusted target weights with caps, theme limits, correlation penalties, and liquidity gates.

Alternatives considered:

- PyPortfolioOpt: good future option, but not required for first implementation.
- Riskfolio-Lib: powerful but too broad and solver-heavy for current product scope.
- Equal weights: too naive and ignores correlation/concentration.

### 4. Use Explicit Constraints

Decision: Observation portfolio optimization must enforce:

- Single ETF max weight: 30%.
- No weight for decision-ineligible assets.
- Theme concentration cap.
- Minimum liquidity gate.
- Correlation penalty for near-duplicate ETFs.

Rationale: These constraints are easier to explain and audit than a black-box optimizer.

### 5. Make Exit Thresholds Volatility-Adaptive

Decision: Keep existing alert categories, but compute thresholds from recent realized volatility, recent drawdown, and current profit state.

Rationale: A fixed 2.5 percentage point giveback can be too tight for volatile ETFs and too loose for stable ETFs. Freqtrade and Backtrader-style trailing stops show that thresholds should follow price movement and lock in profit after a configured offset.

### 6. Persist Threshold Context

Decision: Store threshold values and explanation in tracked position state/alert context.

Rationale: The user needs to know why an email fired. Persisted context also prevents raw intraday cleanup from erasing the high-water/profit state.

### 7. AI Is Explanation Only

Decision: AI may summarize why validation/portfolio/exit rules reached a state, but rule outputs remain deterministic.

Rationale: Financial reminders must be reproducible. AI text can help comprehension but must not become the decision engine.

## Risks / Trade-offs

- [Risk] Validation can look convincing with too few samples.  
  Mitigation: Every validation summary must show sample count and mark insufficient samples.

- [Risk] Portfolio optimization can overfit recent returns.  
  Mitigation: Use conservative caps, liquidity gates, and validation confidence rather than maximizing recent return only.

- [Risk] Adaptive stops can still sell too early or too late.  
  Mitigation: Display threshold reasoning and keep alerts as manual-decision reminders.

- [Risk] More metrics can make UI harder for beginners.  
  Mitigation: Show a short conclusion first, with expandable details for evidence.

- [Risk] Solver dependencies can complicate deployment.  
  Mitigation: First implementation uses a deterministic in-project optimizer; external libraries remain future options.

## Migration Plan

1. Add tables for validation runs/items and observation portfolio snapshots.
2. Add nullable fields or JSON context for adaptive thresholds without breaking existing alerts.
3. Backfill validation from existing signal runs where price data is available.
4. Deploy jobs disabled-by-default or safe-to-run, then enable daily refresh after verification.
5. Rollback: disable new jobs and keep existing ranking/reminder behavior; new tables can remain unused.

## Open Questions

- What minimum sample count should mark a label as reliable: 30, 50, or 100 observations?
- What theme cap should be used for the first version: 50% or 60%?
- Should the first optimization target choose 4 ETFs or allow 3-6 depending on eligible candidates?
