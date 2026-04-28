## 1. Data Model and Migration

- [x] 1.1 Add SQLAlchemy models for recommendation profiles, recommendation runs, recommendation items, fund metrics, stocks, stock price history, stock fundamentals, and stock metrics.
- [x] 1.2 Add Alembic migration for the new recommendation and stock tables with uniqueness constraints for metric dates and asset codes.
- [x] 1.3 Add default seed data for fund and stock recommendation profiles.
- [x] 1.4 Add model-level tests or migration smoke tests that verify the new tables can be created and queried.

## 2. Recommendation Schemas and Contracts

- [x] 2.1 Add Pydantic schemas for recommendation runs, recommendation items, score breakdowns, rationales, risk flags, and latest recommendation responses.
- [x] 2.2 Add frontend TypeScript types matching the recommendation API response shapes.
- [x] 2.3 Add constants or enums for asset types, run statuses, score groups, and safe action labels.

## 3. Fund Metrics and Scoring

- [x] 3.1 Implement fund metric computation from existing fund metadata, NAV history, valuation data, holdings snapshots, and news summaries.
- [x] 3.2 Implement deterministic fund scoring with valuation fit, portfolio gap fit, cost efficiency, risk control, liquidity or size, and news risk components.
- [x] 3.3 Add fund scoring tests for deterministic ordering, portfolio gap influence, missing optional metrics, and critical risk flags.
- [x] 3.4 Persist fund metrics and fund recommendation items with score breakdowns, rationales, risk flags, and data freshness metadata.

## 4. Stock Universe, Metrics, and Scoring

- [x] 4.1 Add stock data provider functions for a small MVP stock universe, daily price history, and fundamental snapshots.
- [x] 4.2 Implement stock metric computation for quality, valuation, momentum, risk control, liquidity, and portfolio diversity.
- [x] 4.3 Implement deterministic stock scoring as watchlist screening results without stock transaction or holding support.
- [x] 4.4 Add stock scoring tests for deterministic ordering, insufficient fundamentals handling, safe observation labels, and missing data flags.
- [x] 4.5 Persist stock metrics and stock recommendation items with score breakdowns, rationales, risk flags, and data freshness metadata.

## 5. Recommendation Generation Jobs

- [x] 5.1 Implement a recommendation generation service that creates a run, computes metrics, scores candidates, persists items, and marks the run success or failed.
- [x] 5.2 Add `daily_recommendation_metrics` and `daily_asset_recommendations` jobs using the existing `run_job` framework.
- [x] 5.3 Register scheduled recommendation jobs after fund NAV, valuation, holdings snapshot, and news jobs.
- [x] 5.4 Add admin job trigger support and job history details for recommendation generation.
- [x] 5.5 Add tests for successful runs, failed runs, partial metric failures, and persisted job diagnostics.

## 6. Recommendation API

- [x] 6.1 Add `/api/recommendations/runs` for recent run history filtered by asset type.
- [x] 6.2 Add `/api/recommendations/latest` for latest successful recommendations by asset type, including empty-state responses.
- [x] 6.3 Add `/api/recommendations/runs/{run_id}` for full run details and ranked items.
- [x] 6.4 Include disclaimer text and ensure API responses do not expose buy, sell, target-price, or expected-return fields.
- [x] 6.5 Add API tests for latest fund results, latest stock results, empty states, run history ordering, and prohibited field absence.

## 7. Optional LLM Explanation Formatting

- [x] 7.1 Add a recommendation explanation prompt that only formats computed metrics, score breakdowns, and risk flags.
- [x] 7.2 Add parser and validation logic that treats LLM explanations as optional display text and never changes ranks or scores.
- [x] 7.3 Add fallback behavior when LLM explanation generation fails.
- [x] 7.4 Add tests proving LLM output cannot mutate recommendation scores or ordering.

## 8. Frontend Recommendation Workspace

- [x] 8.1 Add a `/recommendations` page with fund and stock tabs.
- [x] 8.2 Display latest run status, data cutoff, ranked candidates, total scores, score breakdowns, rationales, and risk flags.
- [x] 8.3 Add empty, loading, error, and stale-data states for both fund and stock tabs.
- [x] 8.4 Add safe UI copy using observation and research language instead of buy, sell, target-price, or expected-return language.
- [x] 8.5 Add frontend tests for populated recommendations, empty states, tab switching, and prohibited wording.

## 9. Verification and Documentation

- [x] 9.1 Update README or local run docs with recommendation job behavior and required stock data source configuration.
- [x] 9.2 Run backend tests, lint, type checks, frontend lint, TypeScript checks, and static build.
- [x] 9.3 Run `openspec validate add-asset-recommendations --strict`.
- [x] 9.4 Manually verify local app navigation to recommendations, admin job trigger behavior, and recommendation empty/populated states.
