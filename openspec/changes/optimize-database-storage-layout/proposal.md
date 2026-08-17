## Why

The 59 GB production host already carries a 22 GB PostgreSQL database, of which `etf_intraday_quotes` consumes 19 GB for only about two months of data, while duplicate indexes, stale statistics, and legacy checkpoint JSON add avoidable write and storage pressure. The storage layout must be bounded before normal ingestion pushes the 2-core/4-GB server back into disk or I/O exhaustion.

## What Changes

- Replace the fixed 60-session full-detail retention rule with tiered retention: keep ten trading sessions of unprotected full-detail quotes, preserve immutable decision-linked quote evidence without age expiry, and retain durable daily summaries before deletion.
- Keep provider raw payloads on hot or evidence-protected quote rows. Do not introduce an unverified capture-batch payload model or rewrite the 19 GB table in place.
- Remove the exact duplicate `(etf_code, quote_time)` intraday quote index. Keep the unique identity index and the existing `(etf_code, quote_time, id)` access path until production query-plan evidence proves a covering replacement is safe.
- Finish incremental leader-tactics checkpoint migration, remove cumulative legacy JSON writes, and provide a bounded maintenance path to reclaim historical TOAST bloat.
- Add bounded database statistics maintenance after bulk ingestion or restore, with per-table timeouts and no concurrent full-database maintenance on the 2-core host.
- Report partitioning and model-module decomposition as follow-up readiness work; neither is performed while the live quote table is larger than available free disk or without a separately validated dependency migration.

## Capabilities

### New Capabilities

- `database-storage-maintenance`: Bounded statistics, index, bloat, checkpoint, and migration maintenance for the constrained production database.

### Modified Capabilities

- `intraday-etf-watch`: Replace fixed 60-session full raw-detail retention with evidence-safe tiered retention.

## Impact

- Affects PostgreSQL migrations, ETF intraday retention, leader-tactics checkpoint persistence, bounded scheduler maintenance jobs, and related tests.
- Does not change ETF comprehensive-ranking scores, provider eligibility, PIT cutoffs, leader-tactics formulas, portfolio state, alerts, notification behavior, or public API response shapes.
- Production migration and reclaim operations remain explicit deployment/maintenance actions; this change does not run destructive DDL automatically on import or startup.
