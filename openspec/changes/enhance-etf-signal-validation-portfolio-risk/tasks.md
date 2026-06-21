## 1. Data Model And Jobs

- [x] 1.1 Add Alembic migration for ETF signal validation runs/items.
- [x] 1.2 Add Alembic migration for optimized observation portfolio snapshots and snapshot items.
- [x] 1.3 Add or extend tracked position exit state fields for adaptive threshold context if existing JSON state is insufficient.
- [x] 1.4 Add daily job registration for ETF signal validation after short-term ranking generation.
- [x] 1.5 Add daily job registration for observation portfolio optimization after validation is refreshed.

## 2. ETF Signal Validation Service

- [x] 2.1 Implement validation service that reads historical ETF signal items and decision-eligible price history.
- [x] 2.2 Compute forward 1/3/5/10 trading-day returns, win rate, median return, average return, and worst forward drawdown.
- [x] 2.3 Exclude samples with stale, estimated, display-only, or unavailable prices and record exclusion reasons.
- [x] 2.4 Classify validation confidence as sufficient, limited, or insufficient based on sample count and freshness.
- [x] 2.5 Add API endpoints to create a validation run, list recent validation runs, and read latest validation summaries.

## 3. Observation Portfolio Optimization

- [x] 3.1 Implement deterministic in-project optimizer using ranking score, volatility, drawdown, liquidity, correlation, validation confidence, and theme constraints.
- [x] 3.2 Enforce single ETF max weight of 30%.
- [x] 3.3 Enforce zero weight for decision-ineligible assets.
- [x] 3.4 Add theme concentration cap and high-correlation duplicate exposure penalty.
- [x] 3.5 Persist optimized portfolio snapshots with included weights and excluded asset reasons.
- [x] 3.6 Update existing observation portfolio API to return optimized weights and explanations while preserving compatibility.

## 4. Volatility-Adaptive Exit Strategy

- [x] 4.1 Implement realized volatility and recent drawdown calculation for tracked ETF holdings using decision-eligible prices.
- [x] 4.2 Replace fixed hard-stop and trailing-take-profit thresholds with adaptive thresholds when enough data exists.
- [x] 4.3 Preserve conservative fixed thresholds only when marked as fixed fallback and only when price data is decision-eligible.
- [x] 4.4 Persist high-water profit, adaptive threshold values, threshold mode, and calculation explanation.
- [x] 4.5 Update alert creation so emails include adaptive-threshold reason, current profit, high-water profit, and crossed line.
- [x] 4.6 Ensure stale/display-only data can create webpage-only context but cannot trigger an email.

## 5. Frontend UX

- [x] 5.1 Add validation evidence display to `/short-term` cards and detail panel.
- [x] 5.2 Show `样本不足` when validation sample count is below the configured threshold.
- [x] 5.3 Add optimized observation portfolio section with weights, caps, excluded candidates, and explanation.
- [x] 5.4 Update tracked holding cards to show adaptive stop/take-profit line and reason.
- [x] 5.5 Keep beginner-facing copy clear: labels are research states, weights are observation references, reminders require manual judgment.

## 6. AI Explanation Constraints

- [x] 6.1 Update LLM prompts so AI can explain validation and risk context but cannot change scores, weights, labels, or thresholds.
- [x] 6.2 Ensure AI output uses approved wording and avoids direct buy/sell commands.
- [x] 6.3 Display rule-generated explanation clearly when AI is unavailable or suppressed.

## 7. Tests

- [x] 7.1 Add backend tests for validation horizon calculations and sample exclusions.
- [x] 7.2 Add backend tests for insufficient sample classification.
- [x] 7.3 Add backend tests for optimizer constraints: 30% max weight, decision-ineligible exclusion, theme cap, and correlation penalty.
- [x] 7.4 Add backend tests for volatility-adaptive hard stop and trailing take-profit thresholds.
- [x] 7.5 Add backend tests proving stale/display-only data does not trigger emails.
- [x] 7.6 Add frontend type/build coverage for new response fields.

## 8. Validation Commands

- [x] 8.1 Run targeted backend tests for ETF validation, observation portfolio optimization, tracked positions, and reminders.
- [x] 8.2 Run `uv run ruff check .`.
- [x] 8.3 Run `uv run mypy app`.
- [x] 8.4 Run `corepack pnpm exec tsc --noEmit`.
- [x] 8.5 Run `corepack pnpm build:static`.
- [x] 8.6 Run `openspec validate enhance-etf-signal-validation-portfolio-risk --strict`.

## 9. Server Verification

- [ ] 9.1 Deploy to `110.42.222.9` after tests pass.
- [ ] 9.2 Run database migration on the server.
- [ ] 9.3 Trigger one validation job and one observation portfolio optimization job manually.
- [ ] 9.4 Verify `/short-term` shows validation evidence and optimized weights.
- [ ] 9.5 Verify tracked holdings show adaptive threshold reasons and no email is sent from stale/display-only data.

