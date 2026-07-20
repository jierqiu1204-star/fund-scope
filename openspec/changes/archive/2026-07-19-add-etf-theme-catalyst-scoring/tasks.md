## 1. Data Model And Seed Data

- [x] 1.1 Add database models and migration for ETF theme catalyst events and theme catalyst snapshots.
- [x] 1.2 Add indexes for active catalyst lookup by theme key, status, effective window, and snapshot date.
- [x] 1.3 Add v1 seed data for robotics, semiconductor/chip, and optical-module/CPO proxy themes with source, strength, confidence, and expiry metadata.
- [x] 1.4 Add an admin or scheduled refresh job that loads seed events and generates latest theme catalyst snapshots without duplicating existing active events.
- [x] 1.5 Add tests proving expired, source-missing, and pending/unverified events do not raise active catalyst scores.

## 2. Catalyst Scoring Service

- [x] 2.1 Create a short-research catalyst service that reads ETF theme profiles and theme catalyst snapshots without importing portfolio, tracked-position, risk-alert, or notifier modules.
- [x] 2.2 Implement catalyst snapshot scoring with event strength, confidence, recency/effective-window decay, event count, limitations, and proxy-theme confidence handling.
- [x] 2.3 Implement opportunity score calculation with default weights: technical 70%, catalyst 20%, sentiment/heat 10%.
- [x] 2.4 Implement hard guards so catalyst scoring never removes data, liquidity, premium, drawdown, or entry timing risk labels.
- [x] 2.5 Add unit tests for strong-catalyst/chase-risk, no-catalyst, proxy-theme, stale-data, and low-liquidity scenarios.

## 3. Signal Generation And API Integration

- [x] 3.1 Extend ETF signal generation to attach `opportunity_score`, `opportunity_label`, `catalyst_score`, `sentiment_heat_score`, `catalyst_summary`, `catalyst_events`, `catalyst_limitations`, and `opportunity_breakdown` into signal item JSON.
- [x] 3.2 Extend short-research response schemas and route mappers while preserving existing `total_score`, `conclusion`, `entry_timing_label`, and old clients.
- [x] 3.3 Add an opportunity-aware sort mode that orders by `opportunity_score` without bypassing existing label, entry timing, holding, and risk filters.
- [x] 3.4 Ensure missing catalyst snapshots degrade to neutral/unavailable catalyst state and do not fail signal generation.
- [x] 3.5 Add API tests for response compatibility, catalyst fields, opportunity sorting, and risk-label preservation.

## 4. Frontend Workbench

- [x] 4.1 Update `/short-term` TypeScript types for opportunity and catalyst fields.
- [x] 4.2 Update ETF ranked cards to show technical score, opportunity score, concise catalyst summary, and existing risk/entry timing labels.
- [x] 4.3 Update selected ETF detail to show catalyst events, effective dates, source type, proxy limitations, and “theme strong but wait for entry” wording.
- [x] 4.4 Add opportunity-score sort UI without removing existing score, label, entry timing, range, search, and holding filters.
- [x] 4.5 Verify desktop and mobile layouts keep catalyst text concise and do not overlap existing controls.

## 5. AI Boundary And Operations

- [x] 5.1 Keep LLM usage optional and explanation-only in v1; do not call AI during deterministic scoring.
- [x] 5.2 If candidate extraction is added, store AI results as pending/unverified and exclude them from active scoring until confirmed.
- [x] 5.3 Add admin/job status output for catalyst refresh counts, skipped expired events, unavailable themes, and generated snapshots.
- [x] 5.4 Document the v1 scoring weights, AI boundary, manual seed process, and rollback behavior.

## 6. Verification

- [x] 6.1 Run backend unit tests for catalyst scoring and short-research API integration.
- [x] 6.2 Run `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 6.3 Run `uv run ruff check .`.
- [x] 6.4 Run frontend typecheck/build for the updated `/short-term` workbench.
- [ ] 6.5 On the server dataset, run catalyst refresh and ETF short-research signal generation, then verify robotics, semiconductor, and optical-module/proxy themes display expected catalyst states. **PAUSED / BLOCKED:** the bundled refresh/generation/verification task MUST NOT generate or validate a production ranking until it produces and consumes a pinned, published, full-scope, fresh `final_score_v3` snapshot with exact contract, universe, and input identity plus a compatible price basis, preserving that identity without fallback.
