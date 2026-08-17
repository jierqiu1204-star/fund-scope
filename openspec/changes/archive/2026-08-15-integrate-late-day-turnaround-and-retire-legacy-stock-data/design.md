## Context

The pure `late_day_turnaround_shadow.v1` evaluator already freezes the 14:30–14:50 formula and execution simulation, but it has no database adapter, materialization, scheduler, API, or UI. ETF intraday quotes are persisted; A-share V2 has a 5,540-name PIT universe and adjusted daily facts but no persisted intraday bars. The VPS is limited to two CPU cores and 4 GB RAM, and every scheduled unit must finish within 55 seconds. See `proposal.md` for motivation.

The legacy `stock_price_history` contains a small deterministic seed set and is still consumed by stock recommendation metrics, so it cannot be dropped before that consumer is moved.

## Goals / Non-Goals

**Goals:**

- Materialize independent ETF and A-share late-day-turnaround evidence without touching comprehensive ranking.
- Reuse existing authoritative daily and ETF intraday stores instead of copying entire market datasets.
- Add a bounded A-share intraday fact boundary and fail closed until factual minute inputs are available.
- Move stock recommendation price metrics to A-share PIT adjusted facts and then retire the duplicate table.

**Non-Goals:**

- Changing the frozen formula or optimizing thresholds to force candidates.
- Treating a daily proxy as an intraday signal.
- Sending notifications, placing trades, or modifying holdings.
- Adding a second comprehensive-ranking factor or strategy boost.
- Guaranteeing complete A-share minute coverage from an unverified free provider.

## Decisions

### 1. Use a separate research aggregate

Add late-day-turnaround run and observation tables. A run freezes universe, decision cutoff, input/contract hashes, coverage, provider health, status, and exclusions. Observations store one asset/formula result and its immutable provenance. API reads select one completed compatible run and never call providers.

Alternative considered: append fields to leader-tactics tables. Rejected because lifecycle, formulas, decision windows, and evidence denominators differ and would create accidental cross-strategy coupling.

### 2. Adapt each universe without crossing data stores

The ETF adapter reads existing `etf_intraday_quotes` and prior-session ETF history, forms only closed ten-minute bars, applies receipt and decision cutoffs, and calls the pure evaluator.

The A-share adapter reads the authoritative A-share universe and adjusted daily facts plus a new append-only intraday ten-minute fact table. Intraday facts carry raw OHLCV/amount, provider, source time, receipt time, normalization factor/identity, and content hash. The adapter converts both daily and intraday values to one declared basis before evaluation.

Alternative considered: read `stock_price_history` or derive ten-minute bars from daily data. Rejected because the old table covers only seed stocks and daily OHLC cannot prove an intraday MA5 turn.

### 3. Keep A-share provider work capability-gated

The scheduler exposes a bounded capture job with a durable checkpoint and a provider interface. The capture job may persist only factual closed bars received before its eventual decision cutoff. Per-run asset and response limits, bounded concurrency, response-size checks, memory headroom, and a 52-second work budget are mandatory. If the provider cannot deliver a complete declared screening pool, materialization is unavailable rather than partial.

The default implementation supports deterministic injected/fake provider batches for testing and a guarded Eastmoney five-minute adapter that aggregates pairs into ten-minute bars. A-share production capture remains feature-flagged and reports provider capability/coverage; it is not silently replaced by a post-close fetch.

Alternative considered: fetch every A-share minute history serially at 14:50. Rejected because it cannot satisfy the VPS timeout and would create inconsistent receipt cutoffs.

### 4. Materialize only fixed checkpoints

Scheduler jobs run at 14:30, 14:40, and 14:50 Shanghai time. Each universe has a separate feature flag and lock. The run publishes candidates only after all declared inputs have been evaluated; otherwise it stores coverage and a stable unavailable reason. Repeated calls for the same contract are idempotent.

### 5. Keep the daily A-share proxy visibly separate

When minute facts are missing, a bounded daily adapter may produce `daily_proxy_watchlist` observations using the same broad shape gates. Those rows are non-actionable, excluded from formal candidate counts, and ineligible for live-signal or historical-performance claims.

### 6. Retire only the proven duplicate legacy table

Stock recommendation metrics will query the latest compatible `ashare_adjusted_price_facts` revisions in bounded pages. The seed universe and fundamentals remain unchanged for compatibility, but seed price generation is removed. After model, service, and test references disappear, an Alembic migration drops `stock_price_history`; downgrade recreates only its schema.

No other stock, ETF, leader-tactics, or recommendation table is removed by this change.

### 7. Enforce isolation structurally and in tests

The late-day modules may import pure contracts and research read models, but not comprehensive-ranking publishers, portfolio mutation services, recommendation mailers, or transaction services. Boundary tests scan imports and verify that materialization changes only the new research tables.

## Risks / Trade-offs

- [Free A-share intraday provider is slow or blocked] → Persist provider health, enforce hard timeouts, keep A-share formal candidates unavailable, and retain the daily proxy only as an observation pool.
- [Raw intraday and adjusted daily prices are incompatible] → Require an explicit normalization factor and identity; otherwise exclude the asset.
- [Full-market capture exceeds 2-core/4-GB limits] → Use a declared bounded screening pool, small batches, response caps, memory headroom, and fail closed on incomplete coverage.
- [Dropping the old table breaks recommendation metrics] → Remove ORM/service/test references first and verify migrations from both pre-change and fresh schemas before dropping it.
- [Multiple checkpoint results confuse users] → API defaults to the latest completed cutoff and exposes decision time and manifest hash; no union is presented as one same-time cross-section.

## Migration Plan

1. Deploy additive research tables, flags defaulted off, adapters, read APIs, and UI.
2. Migrate stock recommendation reads to A-share adjusted facts and verify equivalent missing-data behavior.
3. Apply the destructive migration that drops only `stock_price_history`; retain a database backup and downgrade schema path.
4. Enable ETF API/materialization first and validate one real trading session.
5. Enable A-share capture only after provider capability and bounded coverage pass; otherwise keep the stable unavailable state.

Rollback disables scheduler/API flags, reverts application code, and downgrades the migration to recreate the legacy table schema. Research tables are additive and can remain dormant during rollback.
