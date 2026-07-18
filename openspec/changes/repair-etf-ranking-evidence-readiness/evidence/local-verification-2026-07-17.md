# Local verification — 2026-07-17

This evidence was produced from the local workspace with every command bounded
by an outer timeout of at most 60 seconds. It is not signed production evidence
and does not satisfy tasks 3.7, 4.10, 9.4–9.9, or the owner change's real replay
acceptance gates.

## Passing batches

- PostgreSQL-shaped bounded sync benchmark: 1,400 ETFs × 300 sessions; ten-code
  slice completed in 5.485 seconds, peak RSS 93,061,120 bytes, max page 300 rows,
  max page SQL 3, rollback/cancellation/no-orphan checks passed.
- Bounded history sync: 16 passed in 42.08 seconds.
- Database identity, PIT membership, and replay provenance migrations: 11 passed.
- Cursor, validation continuation, and source-manifest migrations: 7 passed.
- Source manifest persistence/repair, exchange-session planner, and validation
  continuation: 20 passed.
- Readiness attestation, entry points, depth lanes, and point-in-time denominator:
  16 passed.
- Backfill jobs, metric window, and score warm-up gate: 24 passed.
- PIT replay input loader including no-production-side-effects: 11 passed.
- Stage A replay: 32 passed.
- Stage B and frozen candidates: 20 passed.
- Forward outcomes and ranking-source contracts: 12 passed.
- Validation endpoints and replay provenance API: 7 passed.
- Intraday critical regression selection: 8 passed, covering reliable same-minute
  activity, stale/mismatched/incomplete history, missing structure, stale base,
  no quote, and provider divergence.
- Backend domain boundaries: 9 passed.
- Readiness schema-failure regression plus attestation/entry-point suite: 10 passed;
  a missing identity migration now returns stable blockers instead of a database
  traceback.
- `uv run ruff check .`: passed.
- `git diff --check`: passed; only line-ending conversion warnings were emitted.

## Strict OpenSpec validation

- `repair-etf-ranking-evidence-readiness`: valid.
- `enable-etf-point-in-time-ranking-replay`: valid.
- `harden-etf-comprehensive-ranking`: valid.

## Remaining dependency blocker

`tests/test_etf_ranking_walk_forward.py` cannot collect because
`app.services.strategy_lab.etf_ranking_walk_forward` is absent. That module and
the paired/walk-forward/holdout behavior remain exclusively owned by
`enable-etf-point-in-time-ranking-replay`. This prevents tasks 4.10, 8.2, 8.5,
and the complete focused-suite gate in 9.1 from being marked complete.

## Read-only configured-database probe

`uv run python -m app.cli etf-readiness --target-date 2026-07-17` completed
without writes, synchronization, replay, or publication. It observed Alembic head
`20260715_000044`, no `database_instance_identity` table, no configured
attestation key, `production_attested=false`, and four SQL statements. The
stable blockers were `readiness_schema_unavailable` and
`database_instance_identity_table_missing`. Deployment and backup remain a
hard prerequisite before any factual repair or bounded history continuation.
