# Bounded Shadow Session Evidence

Date: 2026-07-19
Contract: `etf_catalyst_sources_v1`
Purpose: deterministic production-shaped acceptance plus one bounded real official-source session.

## Frozen operating limits

- Official allowlist only.
- One fetch worker.
- At most 20 items per source batch.
- Operation timeout at most 50 seconds.
- No second attempt for the same source and session.
- Cursor, receipt IDs, batch hash, latency, and fetch-state counts persist in `etf_catalyst_run_checkpoints`.

## Recorded acceptance session

The bounded session in `backend/tests/test_etf_catalyst_runner.py` used registered NDRC, CSRC, SSE, SZSE, and MIIT source identities and exercised the production receipt/checkpoint path.

| Evidence | Recorded result |
| --- | --- |
| Items received | 4 in the NDRC batch |
| New immutable receipts | 3 |
| Duplicate receipts | 1 identical-content receipt deduplicated |
| Corrections | 1 changed-content receipt linked to its predecessor |
| Successful-empty | 1 explicit `successful_empty` receipt |
| Outage | 1 bounded CSRC timeout recorded as `unavailable` with reproducible error class |
| Coverage states | `item`, `successful_empty`, and `unavailable` remained distinct |
| Cursor | `null -> cursor-4`, persisted before the next batch |
| Latency | Captured as non-negative `latency_ms` for every batch/outage |
| Batch identity | Stable SHA-256 batch hash persisted |
| Retry policy | Same-source/same-session second attempt rejected |
| Concurrency | Concurrent second fetch rejected while the single worker held the lock |
| Resume | Extraction consumed 3 receipts in batches of 2 then 1 with no duplicate processing |

This acceptance deliberately does not label fixture receipts as real official announcements and does not use them in a historical event study, ranking, allocation, alert, or email decision.

## Real server-database session

The GitHub self-hosted runner executed the deployed backend against the production
PostgreSQL service after deployment backup, migration to `20260719_000053`, and
health verification. The CLI refuses SQLite and loopback database hosts.

| Evidence | Recorded result |
| --- | --- |
| Workflow run | `29739031009` |
| Deployed commit | `1d6f43c2b18e826b221b4523e4e495e007d7af94` |
| Session | `acceptance-1d6f43c2b18e` |
| Official source | `ndrc_policy` |
| State | `partial`, because the acceptance intentionally stopped after 3 items |
| Items received | 3 |
| New immutable receipts | 3 |
| Duplicate receipts | 0 |
| Corrections | 0 |
| Fetch states | `item: 3` |
| Cursor | `null -> 1:3` |
| Source latency | 563 ms |
| Batch hash | `cf839087bea8c5aa3cfcdcb13d5e98ef27bf019644d659ec1eb926c51f04ca19` |

No fixture, local database, old evidence, raw-price fallback, ranking mutation,
allocation, alert, or email action was used in this session.
