# PostgreSQL history-sync benchmark — 2026-07-17

Environment: local PostgreSQL 18 service, isolated schema `etf_history_sync_benchmark`. The benchmark never read or wrote application tables in `public`. Every command and every script phase had a 60-second command timeout and a 55-second internal timeout.

## Production-shaped dataset

- ETF universe: 1,400
- Exchange-session depth: 300
- Expected matrix size: 420,000 ETF/session observations
- Initial persisted adjusted-price rows: 419,988
- Intentional final-session gaps: 12
- Seed batching: seven batches of 200 ETFs
- Seed phase elapsed time: 1.125–1.484 seconds per batch
- Seed phase process RSS: 94,076,928–95,342,592 bytes
- Price basis: decision-eligible `total_return_adjusted`
- Provider provenance: `eastmoney` / `benchmark-hfq-v1`

## Main bounded continuation

- Status: `partial`
- Stable stop reason: `continuation_required`
- Attempted/completed ETFs: 10 / 10
- Fetched/persisted rows: 3,000 / 3,000
- Maximum page rows: 300, limit 500
- Maximum SQL statements per committed page: 3, limit 8
- Elapsed time: 5.485 seconds, worker/process limits 55/60 seconds
- Peak process RSS: 93,061,120 bytes, limit 805,306,368 bytes
- Remaining intentional gaps after the slice: 2
- No `running` JobRun remained after exit

Acceptance checks passed for the one-worker lease, 10-code cap, 500-row page cap, 5,000-row slice cap, eight-SQL page cap, 768-MiB RSS cap, worker/process deadlines, and no orphan job.

## Pre-commit rollback injection

- Injected error: `benchmark rollback injection`
- Job terminal status: `failed`
- Stable stop reason: `page_persistence_error:RuntimeError`
- ETF price row count before/after: unchanged at 419,998
- No `last_trade_date` checkpoint was persisted
- No `running` JobRun remained

## External cancellation

- Worker task cancellation observed: yes
- In-flight provider task cancellation observed: yes
- Job terminal status: `partial`
- Stable stop reason: `worker_cancelled`
- ETF price row count before/after: unchanged at 419,998
- No `running` JobRun remained

The benchmark script is `backend/scripts/benchmark_etf_history_sync_postgres.py`. Its cleanup phase drops only the exact isolated schema name and was run after recording this evidence.
