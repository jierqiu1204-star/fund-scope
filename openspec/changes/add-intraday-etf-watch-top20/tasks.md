## 1. Data Model And Schemas

- [x] 1.1 Add Alembic migration for `etf_intraday_quotes` with ETF code, quote time, trade date, latest price, change percent, volume, turnover, bid, ask, IOPV, premium/discount percent, source, freshness status, raw JSON, and timestamps.
- [x] 1.2 Add Alembic migration for `intraday_etf_watch_runs` to record each manual or scheduled watch run status, watched count, updated quote count, alert count, skipped reason, error message, and timestamps.
- [x] 1.3 Extend tracked position alert storage or metadata to represent intraday alert level, quote time, suppression status, and alert source without breaking existing daily alert records.
- [x] 1.4 Add backend Pydantic schemas for intraday ETF quote snapshots, watch status, tracked ETF intraday snapshot, and dynamic exit thresholds.

## 2. Intraday Quote Fetching

- [x] 2.1 Add an ETF intraday quote service that calls AKShare `fund_etf_spot_em` once per refresh and normalizes the returned fields.
- [x] 2.2 Implement field alias parsing for latest price, bid, ask, IOPV, premium/discount, volume, turnover, change percent, and quote time with missing-field tolerance.
- [x] 2.3 Persist only watched ETF quote snapshots instead of writing all returned ETFs to the database.
- [x] 2.4 Mark quotes stale when the latest quote is older than 3 minutes during market hours.
- [x] 2.5 Add source failure handling with readable Chinese errors, exponential backoff state, and fallback to the latest stored quote.

## 3. Watchlist Selection

- [x] 3.1 Implement a service that reads the latest successful ETF signal run and returns the top 20 ETF codes in rank order.
- [x] 3.2 Merge active tracked ETF positions into the watchlist even when they are outside the top 20.
- [x] 3.3 Return watchlist source metadata: `top20_signal`, `tracked_position`, or both.
- [x] 3.4 Handle missing or stale ETF signal runs by watching active tracked ETFs only and returning a visible status message.

## 4. Dynamic Exit Signal Engine

- [x] 4.1 Compute ETF volatility unit `R` from recent 20-day realized volatility and ATR percentage, with 2% fallback when sample data is insufficient.
- [x] 4.2 Replace ETF fixed hard stop logic with dynamic hard stop `-clamp(1.5R, 1.2%, 4.5%)`.
- [x] 4.3 Replace ETF fixed profit-protection logic with dynamic start `max(1.5R, 1.0%)` and dynamic trailing giveback `clamp(0.9R, 0.8%, 2.5%)`.
- [x] 4.4 Add ETF trend-weakening detection using intraday price, daily 5/10-day moving averages, VWAP or intraday average proxy, and negative recent momentum.
- [x] 4.5 Add liquidity and structure risk checks for stale quotes, widened bid/ask spread, weak turnover, abnormal premium/discount, and missing IOPV.
- [x] 4.6 Ensure all ETF exit signal reasons include the concrete price, threshold, quote time, and source data used.

## 5. Tracking And Alert APIs

- [x] 5.1 Update ETF tracked-position creation so manual actual execution price is preserved as entry price.
- [x] 5.2 Update ETF tracked-position creation so missing execution price uses a fresh intraday quote during market hours and daily close only as fallback.
- [x] 5.3 Extend `GET /api/tracked-positions` and `GET /api/tracked-positions/{id}` to return intraday snapshot, price source, quote time, dynamic thresholds, and intraday alert history.
- [x] 5.4 Add `GET /api/etf-quotes/tracked` for current watchlist status and latest quote snapshots.
- [x] 5.5 Add `POST /api/admin/jobs/intraday_etf_watch/run` for a manual one-shot intraday watch run.
- [x] 5.6 Deduplicate same ETF same alert type for 30 minutes, while allowing material hard-stop escalation.

## 6. Scheduler And Jobs

- [x] 6.1 Register an `intraday_etf_watch` scheduler job that runs every 60 seconds only during A-share trading windows in `Asia/Shanghai`.
- [x] 6.2 Skip external quote fetching outside market hours and record an off-hours watch run result.
- [x] 6.3 Ensure the daily short-research signal job remains the source for the next trading session's top 20 watchlist.
- [x] 6.4 Add admin job result summaries for watched count, quotes updated, stale quotes, alerts created, emails sent, suppressed duplicates, and failures.

## 7. Frontend

- [x] 7.1 Update `/short-term` ETF mode to show intraday watch status, latest refresh time, watched ETF count, and top-20 source status.
- [x] 7.2 Update tracked position cards to display all active tracked positions grouped by asset type, so ETF mode does not hide off-exchange fund tracking records.
- [x] 7.3 Add ETF real-time fields on tracking cards: latest price, quote time, price source, estimated P&L, max profit, giveback, dynamic stop line, and alert status.
- [x] 7.4 Add stale quote and public-data boundary warnings in Chinese.
- [x] 7.5 Poll backend quote cache every 30 seconds without calling external data sources from the browser.
- [x] 7.6 Add admin/jobs buttons and result rendering for manual intraday watch runs.

## 8. Tests And Verification

- [x] 8.1 Add backend tests for top-20 watchlist selection and active tracked ETF merge.
- [x] 8.2 Add backend tests for quote normalization, stale quote handling, and provider failure fallback.
- [x] 8.3 Add backend tests for manual entry price priority and intraday quote entry fallback.
- [x] 8.4 Add backend tests for dynamic hard stop, trailing profit, trend weakening, liquidity risk, premium/discount risk, and alert cooldown.
- [x] 8.5 Add API tests for tracked-position intraday fields and `GET /api/etf-quotes/tracked`.
- [x] 8.6 Run `uv run pytest`, `uv run ruff check .`, `uv run mypy app`, `corepack pnpm exec tsc --noEmit`, and `corepack pnpm build:static`.
- [ ] 8.7 Deploy to `110.42.222.9`, run migrations, manually run `intraday_etf_watch`, and verify `/short-term` shows fresh ETF quote status.

