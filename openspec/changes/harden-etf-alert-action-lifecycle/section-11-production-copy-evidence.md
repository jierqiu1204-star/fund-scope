# Section 11 production-shaped copy evidence

Recorded on 2026-07-15 (Asia/Shanghai). Every command used an external timeout of 60 seconds or less. The production database was queried read-only and was never migrated or backfilled.

## Production precheck and disposable copy

- Production Alembic revision: `20260712_000038`.
- Production public-table count: `82`.
- Production `tracked_positions` count: `0`.
- Production database size at capture: `46,560,959` bytes.
- A custom-format `pg_dump` completed in 3.5 seconds and produced `%TEMP%\fundscope_alert_lifecycle_prod_20260715.dump` (`4,623,062` bytes).
- To stay within the two-core/four-GB and 60-second limits, the disposable PostgreSQL 16 copy restored the production schema only. This is representative for the lifecycle migration because the production source table contained zero tracked-position rows; no old row was omitted from the lifecycle backfill population.
- Schema-only restore completed in 3.4 seconds with `82` public tables.

## Migration and bounded backfill

- The copy was stamped at the production revision and upgraded with `python -m alembic upgrade head` using a dedicated local PostgreSQL endpoint.
- Revisions `20260714_000039` through `20260715_000044` completed transactionally in 4.2 seconds.
- Post-upgrade revision: `20260715_000044`.
- Post-upgrade public-table count: `89`.
- The bounded backfill was invoked once with the complete explicit production position-id set (empty), `max_items=100`, and cutoff `2026-07-14`; it completed in 6.9 seconds.

Backfill counters:

| Counter | Count |
| --- | ---: |
| requested | 0 |
| processed / migrated | 0 |
| confirmed baseline | 0 |
| estimated baseline | 0 |
| data waiting | 0 |
| legacy unverified | 0 |
| closed | 0 |
| skipped existing | 0 |
| rejected | 0 |

The all-zero result is the real result for the captured production population, not simulated row evidence. Row-shape cases remain covered by the focused migration/backfill tests; they are not substituted for this production-copy result.
