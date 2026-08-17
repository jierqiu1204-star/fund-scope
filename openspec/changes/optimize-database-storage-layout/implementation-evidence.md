## Implemented scope

- Intraday quote full-detail default is one shared constant: 10 trading sessions.
- Evidence sealing remains fail-closed; protected quote rows never age out; daily summaries are written before unprotected rows are deleted.
- Cleanup remains adaptive (500-5,000 rows), serialized, and hard-bounded. Catch-up runs hourly from 01:20 through 06:20 with `max_instances=1`.
- Migration `20260817_000071` removes only `ix_etf_intraday_quotes_code_time`, preserves the unique code/time index and code/time/id index, and recreates the removed index on downgrade.
- The same migration validates and converts every version-1 leader checkpoint into incremental item rows before clearing legacy arrays. Downgrade reconstructs the legacy arrays.
- Version-2 checkpoint saves validate cumulative in-memory state but no longer serialize or bind it as cumulative JSON; only changed item rows are upserted.
- PostgreSQL statistics maintenance is serialized, advisory-locked, limited to four tables, uses one-second lock and eight-second statement timeouts, and runs at 04:45 to avoid cleanup overlap. Non-PostgreSQL databases return a stable unavailable reason.
- Blocking reclamation and quote partitioning remain explicit, separately approved maintenance actions.

## Expected storage effect

- Dropping the observed duplicate production index should return approximately 506 MB to the filesystem immediately.
- Reducing unprotected quote history from roughly two months to 10 sessions should make most old heap pages reusable and stop continued linear growth. Ordinary deletion/vacuum does not return those heap pages to the operating system.
- After the logical live set is small enough, a separate capacity-checked partition/copy migration can return the reusable quote heap space to the filesystem without requiring a second 19 GB copy.
- Clearing legacy checkpoint JSON removes roughly 105 MB of logical TOAST bloat; an explicitly scheduled `VACUUM (FULL, ANALYZE) leader_tactics_v2_checkpoints` can reclaim it physically because this table is small. The application never runs that command automatically.

## Production rollout and rollback

1. Take and verify the normal bounded backup.
2. Apply Alembic through `20260817_000071`; verify checkpoint item counts and all three retained quote indexes/constraints before continuing.
3. Run one manual `database_statistics_maintenance` slice and one `intraday_etf_cleanup` slice; inspect timings, lock outcomes, deletion counts, disk I/O, and memory.
4. Let hourly off-market cleanup drain the backlog. Do not run a second worker or an unbounded manual loop.
5. Optionally reclaim the small checkpoint table in a maintenance window. Do not rewrite the quote table until a later capacity check proves the reduced copy fits.
6. Rollback downgrades `20260817_000071`, reconstructs legacy checkpoint JSON, and restores the removed non-unique index. Quote rows and protected evidence are not changed by schema downgrade.

## Verification evidence

- Core storage, checkpoint, retention, and scheduler group: 14 passed.
- All migration tests: 49 passed.
- Intraday watch and evidence retention group: 68 passed.
- Leader checkpoint capture/workflow regression group: 23 passed.
- Scheduler and backend domain-boundary group: 17 passed.
- Focused post-schedule group: 10 passed.
- Full backend Ruff: passed.
- Strict OpenSpec validation: passed.
- `git diff --check`: passed.

All commands used a process alarm of at most 55 seconds. No production database, ranking formula, PIT eligibility rule, API response contract, alert policy, notification behavior, commit, push, or deployment was changed during this implementation.
