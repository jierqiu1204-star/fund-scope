## Context

FundScope already has ETF ranking, label validation, strategy healthcheck, and optimized allocation snapshots. The existing optimizer is intentionally conservative: it uses decision-eligible assets, volatility, drawdown, liquidity, correlation, theme caps, and a single ETF weight cap. The missing piece is a formal method to combine:

- a neutral market prior,
- FundScope's ranking and label views,
- confidence derived from historical evidence and data reliability,
- covariance and portfolio constraints.

Black-Litterman fits this role as an allocation comparison model. It belongs in the `portfolio_allocation` layer. It must not decide intraday alerts, send emails, modify tracked positions, or override real user holdings.

## Goals / Non-Goals

**Goals:**

- Add a Black-Litterman allocation method for ETF research comparison.
- Make the method explainable: prior source, views, confidence, covariance window, constraints, excluded ETFs, and output weights.
- Reuse existing data reliability and evidence principles: stale or display-only data must not influence decision weights.
- Keep current FundScope allocation outputs intact and show Black-Litterman as a comparison method.
- Allow future backtest/evidence reporting against existing rule allocation, minimum-volatility, and risk-parity methods.

**Non-Goals:**

- Do not use Black-Litterman to trigger buy/sell emails.
- Do not connect to brokers or auto-trade.
- Do not replace current ranking labels, entry timing labels, or exit alert rules.
- Do not introduce heavy external optimization dependencies in the first implementation unless existing project dependencies are insufficient.
- Do not use unverifiable AI text as a Black-Litterman view or confidence input.

## Decisions

### Decision 1: Keep Black-Litterman inside Portfolio Allocation

Black-Litterman SHALL live in `app.services.portfolio_allocation` or a direct submodule of it.

Rationale:
- It is a portfolio weight model, not a signal generator or risk alert engine.
- This respects the project dependency direction: Market Data -> Research Signal -> Portfolio Allocation -> Position Tracking -> Risk Alert -> Notification.

Alternative considered:
- Put it in `short_research`; rejected because it would mix ranking/label generation with portfolio construction.

### Decision 2: Use a layered input contract

The model input should be an immutable data structure containing:

- eligible ETF candidates,
- daily return matrix,
- covariance matrix,
- prior weights,
- views,
- confidence,
- constraints,
- evidence metadata.

Rationale:
- Keeps the math testable without database coupling.
- Makes it clear which inputs are eligible for financial decisions.

### Decision 3: Market prior source order

Prior weights should use the strongest reliable source available:

1. ETF AUM / fund size if available and fresh enough.
2. Liquidity-weighted proxy using recent average turnover if AUM is unavailable.
3. Risk-parity or equal prior as a deterministic fallback, clearly labeled as fallback prior.

Rationale:
- Black-Litterman needs a neutral prior. For China ETF data, AUM may be incomplete; the system needs a documented fallback that is not hidden.

### Decision 4: Views come from rules, not AI prose

Views should be derived from structured FundScope fields:

- short-term score,
- buy observation label,
- entry timing label,
- label validation result,
- strategy healthcheck result,
- market regime and theme constraints.

AI-generated explanation text can explain the result but cannot be an input to the math.

Rationale:
- Reproducibility matters more than rich language in financial calculations.

### Decision 5: Confidence is capped by evidence and data reliability

Confidence should be reduced or zeroed when:

- price data is stale, estimated, display-only, or unavailable,
- label validation has insufficient samples,
- healthcheck says evidence is weak or unstable,
- ETF history is too short for covariance.

Rationale:
- Black-Litterman can look precise even when inputs are weak. Confidence must make uncertainty visible.

### Decision 6: Enforce FundScope constraints after posterior returns

The optimizer should compute posterior expected returns, then solve constrained long-only weights with:

- single ETF <= 30%,
- theme concentration cap,
- liquidity eligibility,
- data reliability eligibility,
- optional max volatility / drawdown controls.

Rationale:
- Black-Litterman posterior returns alone do not enforce user-facing risk constraints.

## Risks / Trade-offs

- [Risk] Black-Litterman output may look more authoritative than it is.
  - Mitigation: label it as "Black-Litterman 对照", show priors/views/confidence, and require backtest evidence before treating it as better than current allocation.

- [Risk] ETF AUM data may be incomplete.
  - Mitigation: use documented prior source order and display fallback prior source.

- [Risk] Optimization may fail due to covariance singularity or insufficient history.
  - Mitigation: return `unavailable` with reasons; do not silently fall back to misleading weights.

- [Risk] Too many methods can confuse the UI.
  - Mitigation: show Black-Litterman as one compact comparison block, not a new primary recommendation by default.

- [Risk] Adding a heavy numerical dependency can destabilize deployment.
  - Mitigation: first attempt implementation with existing `numpy`/`pandas` stack and simple constrained projection; introduce external libraries only through a separate dependency review.
