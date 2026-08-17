## Context

Production PostgreSQL occupies about 22 GB on a 59 GB host. `etf_intraday_quotes` alone is about 19 GB for roughly two months of captures because its 11.5 million rows retain normalized values plus per-row provider payloads. The host has 2 CPU cores, 4 GB RAM, no swap, and about 16 GB free disk, so copying or rewriting the live quote table is unsafe. Existing evidence references and daily-summary tables already provide the required evidence-safe retention boundary.

The checkpoint table also contains approximately 105 MB of TOAST allocation created by legacy cumulative JSON, while its live logical content is under 1 MB. One non-unique quote index exactly duplicates the key columns of an existing unique index and consumes about 506 MB. Many restored or bulk-loaded tables have no recorded analyze timestamp.

## Goals / Non-Goals

**Goals:**
- Bound quote-table growth without deleting decision evidence.
- Reduce redundant index and checkpoint write/storage amplification.
- Restore planner statistics through short, serialized maintenance slices.
- Make every cleanup and maintenance action observable, resumable, and safe on a 2-core/4-GB host.

**Non-Goals:**
- No quote-table partition rewrite in this change.
- No raw provider batch-deduplication schema without a verified capture-batch contract.
- No broad relocation of the 117 SQLAlchemy model classes.
- No change to ranking formulas, PIT eligibility, public APIs, alerts, or notifications.
- No automatic `VACUUM FULL`, `REINDEX`, or other blocking whole-table operation.

## Decisions

### Use a ten-session hot window and immutable evidence exceptions

The default unprotected full-detail window is frozen at ten trading sessions. The retention workflow first seals published-snapshot evidence, then upserts daily summaries, then deletes only rows not referenced by evidence. This reuses the current evidence hash, which includes provider payload data, and avoids inventing a second source-of-truth table.

Ten sessions retain recent debugging and intraday review while cutting the dominant storage footprint sharply. The value is a named constant shared by service, job, and workflow defaults so callers cannot silently drift back to 60.

### Keep deletion adaptive but serialized

Each run keeps the existing hard timeout and adaptive 500-5,000-row batches. It processes a single session and returns explicit continuation evidence. Backlog catch-up runs hourly from 01:20 through 06:20 or through manual invocation, always with `max_instances=1`; it never starts concurrent workers or an unbounded loop. Statistics maintenance runs separately at 04:45 so the two I/O jobs do not start together.

### Drop only the proven duplicate index

The migration drops `ix_etf_intraday_quotes_code_time`, whose columns and ordering exactly duplicate the unique `(etf_code, quote_time)` index. It retains both the unique index and `(etf_code, quote_time,id)` index. A future change may replace the latter with a covering unique index only after production `EXPLAIN (ANALYZE, BUFFERS)` evidence.

### Migrate checkpoint rows before stopping cumulative writes

The migration parses each version-1 checkpoint using PostgreSQL JSON functions, inserts completed/failed entries into `leader_tactics_v2_checkpoint_items`, verifies the represented item count, then clears legacy arrays and changes `storage_version` to 2. Any malformed or conflicting payload aborts migration rather than losing progress. Application saves send `[]` for legacy JSON fields and write only changed item rows.

Clearing logical JSON does not immediately shrink TOAST files. The maintenance report exposes an explicit `VACUUM (FULL, ANALYZE) leader_tactics_v2_checkpoints` option for a separately approved maintenance window.

### Analyze only a few catalog-selected tables per run

A PostgreSQL-only workflow selects tables with missing statistics or meaningful modifications, orders deterministically, and analyzes at most four per invocation. It uses an advisory lock, a one-second lock timeout, and a per-table statement timeout no greater than ten seconds. Each table uses a savepoint/transaction recovery boundary so one timeout does not poison later work. SQLite returns a stable unavailable result.

### Defer partitioning and model decomposition

Partitioning a 19 GB live table needs temporary disk and a longer migration window than this host can safely provide. After retention reduces the live table and an explicit capacity check passes, a separate OpenSpec may introduce monthly partitions. Model-module decomposition is also separated because it is a dependency-boundary refactor rather than a physical storage fix.

## Risks / Trade-offs

- A ten-session window removes old unprotected minute detail. Mitigation: daily summaries and all decision-linked rows remain immutable.
- Cleanup may need many invocations to drain the existing backlog. Mitigation: bounded adaptive slices preserve availability and report progress.
- Dropping an index can alter a query plan. Mitigation: only the exact duplicate is removed; equivalent unique index ordering remains.
- Legacy JSON migration can encounter malformed data. Mitigation: validate first and fail closed in the transaction.
- `ANALYZE` still consumes I/O. Mitigation: serialize, cap tables, enforce per-table timeout, and schedule outside market hours.

## Migration Plan

1. Deploy code and migration without running any blocking reclaim.
2. Upgrade schema: migrate version-1 checkpoints and drop the exact duplicate quote index.
3. Run one bounded statistics slice and one bounded retention slice; inspect reported evidence and resource usage.
4. Continue retention slices off-hours until the ten-session boundary is reached.
5. After confirming logical bloat removal and a maintenance window, optionally run explicit checkpoint-table `VACUUM FULL`; never run it automatically.
6. Rollback restores the non-unique index. Checkpoint downgrade reconstructs legacy arrays from item rows before removing version-2 storage.

## Open Questions

- Whether production query plans justify retiring `(etf_code, quote_time,id)` after the table shrinks.
- Whether monthly partitioning is worthwhile after bounded retention stabilizes table size.
