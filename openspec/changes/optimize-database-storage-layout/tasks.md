## 1. Evidence-safe quote retention

- [x] 1.1 Introduce one shared ten-trading-session full-detail retention constant and use it in service, job, workflow, scheduler, and admin defaults.
- [x] 1.2 Preserve fail-closed evidence sealing, daily-summary-before-delete ordering, bounded adaptive batches, and complete progress reporting.
- [x] 1.3 Add retention tests for the default window, protected rows, incomplete evidence, summary preservation, and bounded continuation.

## 2. Physical schema and checkpoint storage

- [x] 2.1 Add a reversible migration that drops only the exact duplicate intraday quote index and preserves unique/query access indexes.
- [x] 2.2 Migrate valid version-1 checkpoint arrays into incremental item rows idempotently, verify counts before clearing legacy JSON, and reconstruct arrays on downgrade.
- [x] 2.3 Stop serializing and writing cumulative checkpoint JSON during normal version-2 saves while retaining legacy read compatibility.
- [x] 2.4 Add migration and persistence tests for malformed payload fail-closed behavior, idempotence, lease semantics, and bounded writes.

## 3. Bounded planner-statistics maintenance

- [x] 3.1 Implement a PostgreSQL-only serialized statistics workflow with advisory locking, deterministic catalog selection, at most four tables per slice, and per-table lock/statement timeouts.
- [x] 3.2 Expose the workflow through bounded scheduler/admin orchestration and return stable evidence for completed, skipped, timed-out, and unavailable states.
- [x] 3.3 Add tests proving SQLite fails closed, table limits are enforced, no database-wide ANALYZE is issued, and one failed table does not start concurrent work.

## 4. Acceptance and operational evidence

- [x] 4.1 Document explicit checkpoint `VACUUM FULL` and future quote-partition readiness steps without running either automatically.
- [x] 4.2 Run related tests, database migration tests, backend domain-boundary tests, Ruff, and strict OpenSpec validation with every command hard-limited to 60 seconds.
- [x] 4.3 Record expected storage savings, rollback path, production rollout sequence, and confirmation that ranking, PIT, API, alert, and notification behavior did not change.
