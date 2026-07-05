## 1. Backend Tests First

- [x] 1.1 Add factor registry tests proving all required factor groups are registered with stable ids, labels, windows, directions, usages, and source types.
- [x] 1.2 Add FactorResult serialization tests for available, insufficient-data, stale, unavailable, seed-only, estimated, and display-only states.
- [x] 1.3 Add scoring profile tests for full profile, degraded profiles, and technical-only unavailable comprehensive scoring.
- [x] 1.4 Add risk gate tests proving overheat, low liquidity, stale data, insufficient history, abnormal ETF structure, and high drawdown cannot be erased by positive factors.
- [x] 1.5 Add API tests proving ranking and detail return cached factor results from the latest successful signal run.
- [x] 1.6 Add sort tests proving `sort=opportunity` ranks numeric comprehensive scores before unavailable comprehensive scores.
- [x] 1.7 Add frontend tests or type-level checks proving unavailable factors render as `暂无` and are not formatted as numeric scores.

## 2. Factor Library Core

- [x] 2.1 Create the short-research factor module and shared factor types for factor id, group, usage, direction, availability, reliability, source, and result payloads.
- [x] 2.2 Implement a factor registry covering price momentum, reversal overheat, volatility risk, liquidity, sector trend, theme event, fund flow, sentiment heat, fundamentals quality, valuation, macro style, and ETF structure.
- [x] 2.3 Implement helpers that convert missing, stale, seed-only, estimated, and display-only source states into `score = null` factor results with readable reasons.
- [x] 2.4 Implement group aggregation utilities that summarize factor results into group scores only when decision-eligible factors exist.
- [x] 2.5 Add deterministic fixtures for robotics, innovative drug, semiconductor, optical-module proxy, broad-market, and no-theme ETFs.

## 3. Existing Factor Migration

- [x] 3.1 Wrap existing technical momentum metrics as price-momentum factor results without changing the current technical score behavior.
- [x] 3.2 Wrap existing chase-risk, overheat, volatility, drawdown, stale-data, insufficient-history, and liquidity flags as risk factors and gates.
- [x] 3.3 Wrap existing sector trend scoring as sector-trend factor results.
- [x] 3.4 Wrap existing theme catalyst and event heat fields as theme-event and sentiment-heat factor results, preserving unavailable/null behavior.
- [x] 3.5 Add ETF-structure base factors for scale, recent turnover eligibility, premium/discount availability, tracking-error availability, and concentration metadata where source data exists.

## 4. New Factor Computation

- [x] 4.1 Add fund-flow factor support from verified ETF share-change, subscription/redemption, or accepted provider data; return unavailable/display-only when no eligible source exists.
- [x] 4.2 Add constituent-breadth factor support for same-theme rising count, moving-average participation, and leader/breadth divergence when constituent or peer data exists.
- [x] 4.3 Add valuation factor placeholders that only become decision-eligible when PE/PB/dividend-yield/valuation-percentile source metadata is verified.
- [x] 4.4 Add macro-style factor placeholders for rate, credit, FX, commodity, risk-appetite, size, and growth/value regime with display-only default behavior.
- [x] 4.5 Add fundamentals-quality placeholders for constituent-weighted quality metrics with unavailable default behavior until source coverage is verified.
- [x] 4.6 Add real sentiment source eligibility hooks while keeping seed-only news, social, search, or research-report heat display-only.

## 5. Factor Profile And Opportunity Scoring

- [x] 5.1 Implement versioned factor profiles with full and degraded weighting versions.
- [x] 5.2 Make comprehensive attention score unavailable when only technical data is decision-eligible.
- [x] 5.3 Record included groups, excluded groups, weights, missing reasons, and profile version in the opportunity breakdown.
- [x] 5.4 Apply risk gates after positive score aggregation so gates can limit labels and display state without being hidden by high scores.
- [x] 5.5 Keep old technical score, sector trend, catalyst, and heat fields compatible for existing frontend and API consumers.

## 6. Signal Run Integration

- [x] 6.1 Insert factor result construction into ETF signal generation after base metrics and sector/theme enrichment.
- [x] 6.2 Persist `factor_profile_version`, `factor_scores`, `factor_group_scores`, `factor_availability`, `risk_gates`, and `opportunity_breakdown` into signal item JSON.
- [x] 6.3 Ensure ETF detail reads factor scores and comprehensive score only from the latest successful cached signal item.
- [x] 6.4 Ensure signal generation failures in optional factor groups degrade to unavailable factor results instead of aborting the whole signal run.
- [x] 6.5 Ensure no factor calculation imports portfolio allocation, tracked positions, risk alerts, notifier, or workflow modules.

## 7. API And Frontend

- [x] 7.1 Extend backend schemas for factor profile, factor group scores, factor results, risk gates, and missing-factor reasons.
- [x] 7.2 Extend `_asset_out` or equivalent API mapping to serialize factor data with `null` for unavailable numeric fields.
- [x] 7.3 Update `/short-term` ranking cards to show compact factor group status without overcrowding the ETF list.
- [x] 7.4 Update ETF detail panel to show factor profile, included/excluded groups, weights, risk gates, and data-source limitations.
- [x] 7.5 Keep display-only factors visually distinct from scoring factors.
- [x] 7.6 Update Chinese UI copy so综合关注, 技术观察, 风险门槛, and 暂无数据 are clearly separated.

## 8. Verification

- [x] 8.1 Run focused backend tests for factor registry, factor results, factor profiles, risk gates, ranking sort, and detail cache consistency.
- [x] 8.2 Run `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 8.3 Run `uv run ruff check .`.
- [x] 8.4 Run frontend typecheck for `/short-term` factor display changes.
- [ ] 8.5 Manually verify robotics, innovative drug, semiconductor, optical-module proxy, broad-market, and no-theme ETFs in ranking and detail.
- [ ] 8.6 After deployment, run ETF signal generation and confirm production API does not expose fallback `50/60` factor or comprehensive scores.
