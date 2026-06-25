## 1. Backend Portfolio Model

- [x] 1.1 Locate the current ETF observation portfolio generator and document existing inputs, output fields, and cash-weight behavior.
- [x] 1.2 Add a full-invested ETF portfolio mode with target invested weight fixed at 100% when eligible candidates are sufficient.
- [x] 1.3 Filter candidates by decision-eligible data reliability, usable daily history, liquidity, and minimum sample windows before assigning weights.
- [x] 1.4 Implement deterministic score-tilted inverse-volatility initial weights using existing score, volatility, drawdown, and liquidity metrics.
- [x] 1.5 Apply explicit constraints: single ETF cap 30%, theme cap, correlation de-duplication, minimum useful weight, and no fallback-data weights.
- [x] 1.6 Normalize final valid weights to 100% and return an unavailable state instead of fabricating weights when constraints are infeasible.

## 2. Explanation And Audit Output

- [x] 2.1 Extend observation portfolio output with portfolio-level fields: target_invested_weight, weight_sum, constraints_used, risk_summary, data_reliability_summary, and unavailable_reason.
- [x] 2.2 Extend each portfolio item with weight_reason_json covering score contribution, volatility adjustment, drawdown adjustment, liquidity, correlation, theme cap, and data reliability.
- [x] 2.3 Add watch-only and excluded ETF explanations with stable reason codes and Chinese display text.
- [x] 2.4 Ensure residual rounding is explained as rounding context, not cash allocation.

## 3. Frontend Workbench

- [x] 3.1 Rename the observation portfolio section on `/short-term` to `全仓 ETF 观察组合参考`.
- [x] 3.2 Add copy explaining that 100% means the user-designated securities-account ETF capital, not total net worth.
- [x] 3.3 Show weight reasons for included ETFs, including cap, correlation, theme, volatility, and data reliability effects.
- [x] 3.4 Show watch-only or excluded reasons for ranked ETFs that do not receive target weight.
- [x] 3.5 Show an explicit unavailable state when full-invested weights cannot be generated.

## 4. Tests

- [x] 4.1 Add backend tests proving final ETF weights sum to 100% when enough eligible candidates exist.
- [x] 4.2 Add backend tests proving single ETF weight never exceeds 30%.
- [x] 4.3 Add backend tests proving fallback, stale, estimated, or display-only data receives zero target weight.
- [x] 4.4 Add backend tests proving high-correlation or same-theme candidates are reduced or excluded with explanation.
- [x] 4.5 Add frontend type/build validation for the new portfolio fields and unavailable state.

## 5. Verification

- [x] 5.1 Run `uv run pytest` for observation portfolio and short research tests.
- [x] 5.2 Run `uv run ruff check .` in backend.
- [x] 5.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 5.4 Run `corepack pnpm build:static`.
- [x] 5.5 Manually verify `/short-term` shows 100% ETF account capital weights and does not present them as automatic trading instructions.
