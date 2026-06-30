## 1. Portfolio Rule Audit

- [x] 1.1 Locate current ETF observation portfolio generation, snapshot serialization, and `/api/short-research/observation-portfolio` response code.
- [x] 1.2 Identify existing fields for observation label, entry timing label, score, volatility, drawdown, liquidity, theme, correlation, and data reliability.
- [x] 1.3 Add or update focused tests that reproduce the current empty-portfolio/cash-wait behavior when high-watch healthy ETFs exist.

## 2. Allocation Layer Model

- [x] 2.1 Define internal allocation layer constants for `primary`, `satellite`, `defensive`, `watch_only`, and `excluded`.
- [x] 2.2 Implement deterministic layer classification from observation label, entry timing label, data reliability, liquidity, structural risk, and theme.
- [x] 2.3 Ensure `高位观察 + 健康回踩/趋势延续` can enter `satellite` instead of always becoming zero-weight watch-only.
- [x] 2.4 Ensure `冲高别追`, `跌破等待`, `放量转弱`, stale data, insufficient liquidity, and material structural risk remain zero-weight.

## 3. Dynamic Weighting And Constraints

- [x] 3.1 Implement raw weight calculation using score, entry timing multiplier, label multiplier, liquidity multiplier, and volatility/drawdown penalty.
- [x] 3.2 Apply single ETF cap of `30%`.
- [x] 3.3 Apply total satellite exposure cap and explain capped weights.
- [x] 3.4 Apply theme concentration cap and move excess or duplicate ETFs to watch-only with reasons.
- [x] 3.5 Apply correlation de-duplication or reduction for highly similar ETFs.
- [x] 3.6 Normalize final weights and set `cash_weight` only when constraints cannot produce a valid allocation.

## 4. Market Regime And Defensive/Cash Modes

- [x] 4.1 Implement portfolio modes `risk_on`, `neutral`, `defensive`, and `cash_wait`.
- [x] 4.2 Generate defensive candidates from configured defensive themes such as bond, treasury, money-market, gold, dividend, low-volatility broad index, or equivalent local tags.
- [x] 4.3 Use defensive allocation when offensive candidates are insufficient but defensive candidates are reliable.
- [x] 4.4 Return `cash_wait` only when primary, satellite, and defensive candidates are insufficient or decision-ineligible.
- [x] 4.5 Record clear `cash_reason`, `market_regime`, and layer weight summary in the snapshot.

## 5. Explanation And API Compatibility

- [x] 5.1 Preserve existing response fields: `items`, `watch_only_items`, `excluded_items`, `cash_weight`, and `weight_sum`.
- [x] 5.2 Add compatible explanation fields for allocation layer, weight reason, exclusion reason, constraint summary, quote time, daily signal date, and portfolio generation time.
- [x] 5.3 Ensure old snapshots without new fields still serialize without errors.
- [x] 5.4 Ensure no field or copy implies automatic trading or guaranteed profit.

## 6. Frontend Workbench Updates

- [x] 6.1 Update `/short-term` ETF funding panel title and copy to show `ETF 资金配置参考`.
- [x] 6.2 Render layer sections for `主配置`, `小仓观察`, `防守配置`, and `等待资金`.
- [x] 6.3 Show portfolio mode explanation for `risk_on`, `neutral`, `defensive`, and `cash_wait`.
- [x] 6.4 Show selected ETF allocation weight, layer, cap effects, or exclusion reason in the detail panel.
- [x] 6.5 Show data timestamps for portfolio generation, daily signal date, and quote time.
- [x] 6.6 Keep UI wording research-only and avoid buy/sell instruction language.

## 7. Tests

- [x] 7.1 Add backend tests for primary allocation when enough healthy short-watch ETFs exist.
- [x] 7.2 Add backend tests for satellite allocation when high-watch but healthy ETFs exist.
- [x] 7.3 Add backend tests that chase-risk, trend-break, stale, and low-liquidity ETFs receive zero weight.
- [x] 7.4 Add backend tests for single ETF cap, satellite cap, theme cap, and correlation de-duplication.
- [x] 7.5 Add backend tests for defensive mode and cash-wait mode.
- [x] 7.6 Add frontend type/build coverage for new response fields and old snapshot compatibility.

## 8. Verification

- [x] 8.1 Run focused backend tests for short research portfolio and ETF universe.
- [x] 8.2 Run `uv run ruff check .`.
- [x] 8.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 8.4 Run `corepack pnpm build:static`.
- [x] 8.5 Manually verify `/short-term` shows layer explanations and no longer appears blank when only satellite/watch candidates exist.
