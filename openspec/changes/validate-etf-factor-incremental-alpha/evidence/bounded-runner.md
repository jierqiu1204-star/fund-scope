# Bounded runner evidence

Date: 2026-07-19

This evidence uses a deterministic production-shaped fixture. It does not fetch
market history, start a full-history synchronization, or substitute simulated
returns for research conclusions.

- Universe shape: 1,405 unique ETF identifiers
- Worker count: 1
- Completed batches: 71
- Maximum history-fetch batch: 20 ETFs
- Cached factor rows: 1,405
- Coverage: 100%
- Exclusions: 1 declared fixture exclusion
- Final date cursor: 2026-03-12
- Peak memory: measured with `tracemalloc`, persisted on the checkpoint, greater
  than zero and below the enforced 256 MiB test ceiling
- Runner runtime: persisted on the checkpoint, greater than zero and below the
  30-second test ceiling
- Test-suite runtime: 11.60 seconds reported by pytest for the production-shaped,
  interruption/resume, timeout, and duplicate-run cases

Interruption evidence:

- A provider failure after two completed batches persisted `partial` state, the
  exact cursor, cached rows, completed-batch hashes, and
  `OSError: provider unavailable`.
- Resume reused the first two batch hashes, fetched only the remaining five
  identifiers, and completed without duplicating cached rows.
- A timed-out data operation persisted a reproducible `TimeoutError` summary.
- A concurrent run with the same manifest and code version was rejected.
