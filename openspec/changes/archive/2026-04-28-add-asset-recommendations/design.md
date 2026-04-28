## Context

FundScope is already a local single-user FastAPI + Next.js application for fund tracking, index valuation monitoring, news summaries, and monthly DCA reminders. The current data model is fund-first: transactions reference `funds`, holdings snapshots are fund holdings, and valuation signals come from index PE/PB percentiles.

The new requirement is to help the user discover fund and stock candidates. This crosses data ingestion, persistence, scheduled jobs, API contracts, and frontend views, so it needs an explicit design. The system must remain a personal decision-support tool: it can rank candidates and explain why they appeared, but it must not issue buy/sell/target-price instructions or claim expected returns.

## Goals / Non-Goals

**Goals:**

- Recommend fund candidates using deterministic, explainable scoring.
- Score stock watchlist candidates using deterministic, explainable screening metrics.
- Persist recommendation runs and items so results are auditable after source data changes.
- Use portfolio context where available, especially target allocation gaps and index valuation state.
- Expose API and UI surfaces for viewing ranked candidates, score breakdowns, data freshness, and risk flags.
- Allow scheduled and manual recommendation generation through the existing job framework.
- Optionally use the existing OpenAI-compatible LLM client to summarize computed rationales, without allowing it to decide scores or rankings.

**Non-Goals:**

- No automatic trading, broker integration, or ICBC automation.
- No buy/sell/hold commands, target prices, or expected-return forecasts.
- No ML price prediction model.
- No multi-user suitability questionnaire or regulated investment-advice workflow.
- No real-time market push; recommendation jobs use snapshot-style daily data.
- No stock transaction or stock portfolio accounting in this change.

## Decisions

### D1. Add a New `asset-recommendations` Domain Instead of Extending DCA

**Decision:** Build recommendations as a separate capability with new models, routes, services, and frontend pages.

**Rationale:** DCA logic currently answers "how much to invest this month" from an index percentile. Asset recommendations answer "which candidates are worth researching" from multiple metrics and portfolio context. Keeping the domains separate avoids mixing monthly budget rules with candidate ranking.

**Alternatives considered:**

- Extend `dca_calculator.py`: too narrow and would couple ranking behavior to reminder amounts.
- Add ad hoc fields to `funds`: insufficient for historical runs, score breakdowns, and stock candidates.

### D2. Persist Runs and Items as Snapshots

**Decision:** Store every generated recommendation in `recommendation_runs` and `recommendation_items`.

Proposed tables:

```text
recommendation_profiles
  id, name, asset_type, risk_level, is_default, config_json, created_at, updated_at

recommendation_runs
  id, profile_id, asset_type, status, as_of_date, started_at, finished_at,
  data_cutoff_json, details_json, error_message

recommendation_items
  id, run_id, asset_code, asset_name, asset_type, rank, total_score,
  score_breakdown_json, rationale_json, risk_flags_json, data_freshness_json
```

Metric tables are separate from recommendation outputs:

```text
fund_metrics
  fund_code, metric_date, return_1y, volatility_1y, max_drawdown_1y,
  tracking_error, fee_rate, fund_size, news_risk_score, data_quality_json

stocks
  code, exchange, name, industry, is_candidate, created_at

stock_price_history
  stock_code, trade_date, open, high, low, close, volume, turnover

stock_fundamentals
  stock_code, report_date, pe, pb, roe, gross_margin, debt_to_asset,
  operating_cashflow, dividend_yield

stock_metrics
  stock_code, metric_date, quality_score, valuation_score, momentum_score,
  risk_score, liquidity_score, data_quality_json
```

**Rationale:** Snapshot outputs make the UI and tests deterministic. Metric tables can be recomputed without rewriting historical recommendation results.

**Alternatives considered:**

- Compute rankings on every API request: simpler initially, but slow, non-auditable, and hard to debug.
- Store only final rank and score: too opaque for a product whose value depends on explainability.

### D3. Use Rule-Based Scoring with Explicit Weights

**Decision:** Implement scoring in `backend/app/services/recommendations/` with pure functions that accept metrics and return a structured score breakdown.

Fund default score groups:

```text
valuation_fit       30%
portfolio_gap_fit   25%
cost_efficiency     15%
risk_control        15%
liquidity_size      10%
news_risk            5% deduction
```

Stock default score groups:

```text
quality             25%
valuation           25%
momentum            15%
risk_control        15%
liquidity           10%
portfolio_diversity 10%
```

Each group returns a 0-100 score, an input metric list, and warning flags. Missing data lowers confidence and can exclude a candidate when required fields are absent.

**Rationale:** Rules are easier to test, easier to explain, and safer than model-generated picks. Profile configuration can later adjust weights without changing the API shape.

**Alternatives considered:**

- LLM-generated recommendations: unsafe, non-deterministic, and difficult to validate.
- ML ranking model: no labeled training set and outside the project scope.

### D4. Treat Stocks as a Watchlist Screener, Not a Portfolio Asset Class

**Decision:** Add stock universe, price, fundamentals, and scoring data, but do not add stock transactions or stock holdings in this change.

**Rationale:** The current application tracks fund transactions only. Stock portfolio accounting would add a second asset-accounting model and should be a separate change. This change only recommends stock candidates for manual research.

**Alternatives considered:**

- Reuse `funds` and `transactions` for stocks: creates mixed semantics and breaks existing fund assumptions.
- Implement full stock portfolio tracking now: too much scope for a recommendation MVP.

### D5. Reuse Existing Jobs and Admin Run Pattern

**Decision:** Add jobs such as `daily_recommendation_metrics` and `daily_asset_recommendations`, exposed through the existing `/api/admin/jobs/{job_name}/run` endpoint and scheduled after NAV, valuation, holdings, and news jobs.

**Rationale:** The current app already has `JobRun`, `run_job`, APScheduler registration, and admin job history. Reusing that pattern keeps operations simple.

**Alternatives considered:**

- Add Celery or a separate worker: unnecessary for a local single-user app.
- Generate only on-demand from the UI: easier but gives worse observability and slower page loads.

### D6. API Design Uses Latest Runs by Default

**Decision:** Add `/api/recommendations` routes:

```text
GET  /api/recommendations/runs?asset_type=fund|stock
GET  /api/recommendations/latest?asset_type=fund|stock
GET  /api/recommendations/runs/{run_id}
POST /api/admin/jobs/daily_asset_recommendations/run
```

The latest endpoint returns a run summary and ranked items. Run detail includes score breakdowns, rationales, data freshness, and risk flags.

**Rationale:** The frontend mostly needs latest results, while history and diagnostics need run IDs.

**Alternatives considered:**

- Separate `/fund-recommendations` and `/stock-recommendations` route trees: clearer names but duplicates pagination, history, and response types.

### D7. Frontend Adds a Recommendation Workspace

**Decision:** Add a `/recommendations` page with fund and stock tabs. Each tab shows latest run status, ranked candidates, score breakdown, risk flags, and a detail panel. The UI copy uses "候选", "观察", "评分", and "原因", not "买入", "卖出", or "目标价".

**Rationale:** The user needs a single place to compare candidates and inspect reasons. Safety wording belongs in the product surface, not only backend docs.

**Alternatives considered:**

- Add recommendations to the dashboard: dashboard already has portfolio, valuation, and reminders; recommendation review deserves a focused workflow.

### D8. LLM Is an Explanation Formatter Only

**Decision:** If enabled, the LLM receives computed metrics, score breakdowns, and risk flags and returns a short Chinese rationale. The persisted item keeps both the structured rationale and optional text explanation. If LLM fails, recommendations still persist with deterministic rationale fields.

**Rationale:** This keeps ranking testable while improving readability.

**Alternatives considered:**

- No LLM at all: safer and simpler, but misses an existing project capability that can make dense metrics easier to read.
- LLM decides rankings: rejected because it violates determinism and safety goals.

## Risks / Trade-offs

- **[Data quality differences across funds and stocks]** -> Store `data_quality_json`, data freshness fields, and missing-data flags; exclude candidates that lack required core metrics.
- **[Recommendation wording looks like regulated advice]** -> Enforce backend `disclaimer`, safe action labels, and frontend copy tests that reject buy/sell/target-price wording.
- **[AKShare endpoints change]** -> Keep ingestion isolated behind provider functions and allow metric jobs to report partial failures through `JobRun.details_json`.
- **[Too many metrics for MVP]** -> Start with a small deterministic score set and make metric fields nullable; expand after the UI and run history are working.
- **[Stock scope expands into portfolio accounting]** -> Keep stocks as candidates only. Any stock transaction support requires a separate OpenSpec change.
- **[LLM output drift]** -> Treat LLM text as optional display copy and persist structured rationale separately.

## Migration Plan

1. Add Alembic migration for recommendation, fund metric, and stock metric tables.
2. Seed a default recommendation profile for funds and stocks.
3. Add backend services and tests for deterministic scoring before wiring jobs or APIs.
4. Add data ingestion and metric computation jobs.
5. Add recommendation generation job and admin trigger.
6. Add API routes and frontend pages.
7. Deploy normally with existing local/Docker process.

Rollback is schema-based: disable scheduled recommendation jobs first, then roll back the Alembic migration if needed. Existing portfolio, valuation, news, and reminder tables are not modified.

## Open Questions

1. Which stock universe should be seeded first: A-share broad index constituents, US large-cap stocks, or a small manually curated list?
2. Which fund candidate universe should be supported first: only manually added funds, broad index funds from AKShare, or a curated default list?
3. Should recommendation profiles be editable in the UI for MVP, or should weights stay code/config-only until the scoring behavior stabilizes?
