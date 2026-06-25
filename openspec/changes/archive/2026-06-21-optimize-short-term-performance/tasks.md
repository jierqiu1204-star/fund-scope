## 1. Database And Retention

- [x] 1.1 Add Alembic migration for performance indexes: latest successful `short_research_signal_runs`, latest `job_runs`, latest intraday quote by ETF, recent intraday alerts, take-profit cooldown alerts.
- [x] 1.2 Add an ETF intraday daily summary table if no equivalent table exists, keyed by `etf_code + trade_date`.
- [x] 1.3 Implement a cleanup service/job that summarizes raw `etf_intraday_quotes` and deletes rows older than 60 trading days.
- [x] 1.4 Expose the cleanup job through the existing admin jobs mechanism without changing existing job URLs.

## 2. Intraday ETF Query Optimization

- [x] 2.1 Rewrite `latest_quotes_by_code` to return at most one latest quote per ETF using `LATERAL ... ORDER BY quote_time DESC, id DESC LIMIT 1` or an equivalent indexed query.
- [x] 2.2 Update live ranking and watch status paths to use the optimized latest quote lookup.
- [x] 2.3 Ensure live ranking continues to mark stale/missing quotes as daily or unavailable, not live decision-eligible.
- [x] 2.4 Add tests proving latest quote lookup does not return duplicate rows and handles missing quotes.

## 3. Tracked Position Performance

- [ ] 3.1 Add batched helpers for latest alerts, recent intraday alerts, latest signal items, advisor reports, latest prices, and intraday snapshots for multiple tracked positions.
- [x] 3.2 Refactor tracked position list output to use batched helpers while keeping the existing response schema.
- [x] 3.3 Keep tracked position detail loading full chart and full alert history for one position only.
- [x] 3.4 Persist or update tracked holding high-water/dynamic threshold state so raw intraday cleanup does not erase reminder context.

## 4. Short-Term Workbench Path

- [x] 4.1 Keep `/api/short-research/assets` cache-first and avoid loading full signal item sets when pagination can be applied in SQL.
- [x] 4.2 Optimize `status_summary` to use run summary counts when available instead of loading all signal items.
- [ ] 4.3 Optimize ETF observation portfolio to load history only for shortlisted candidates.
- [x] 4.4 Remove confirmed dead code in performance-related short research paths.

## 5. Frontend And UX

- [x] 5.1 Keep `/short-term` compatible with existing endpoints; only add a bootstrap endpoint if backend duplication remains after service optimizations.
- [x] 5.2 Show cleanup/summary data as summary or display-only data, never as fresh live quote data.
- [x] 5.3 Verify ETF mode still distinguishes `实时综合分` from `日线基础分`.

## 6. Validation

- [x] 6.1 Add or update backend tests for latest quote query, raw intraday cleanup, tracked position list batching, and stale-data gating.
- [x] 6.2 Run `uv run pytest` for affected backend tests.
- [x] 6.3 Run `uv run ruff check .`.
- [x] 6.4 Run `corepack pnpm exec tsc --noEmit`.
- [x] 6.5 Run `corepack pnpm build:static`.
- [ ] 6.6 On server or local PostgreSQL, compare EXPLAIN for latest quote lookup and confirm it no longer scans all raw intraday rows.
