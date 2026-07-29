## Why

The production ETF readiness loop can become permanently unable to request providers because Linux lifetime peak RSS is treated as current RSS, while the repository simultaneously defines incompatible 90-percent and 95-percent publication contracts. Until resource admission, publication state, and production PIT capture agree on one auditable contract, ranking availability and any return or alert optimization remain unreliable.

## What Changes

- Replace memory admission based on process-lifetime high-water RSS with bounded current-RSS sampling, while preserving lifetime peak, slice peak, baseline, and delta as separate telemetry.
- Define one three-state ETF readiness contract:
  - `blocked` when target-date adjusted coverage is below 95 percent or 61-session adjusted coverage is below 90 percent;
  - `degraded` when target-date adjusted coverage is at least 95 percent and 61-session adjusted coverage is at least 90 percent but below 95 percent, allowing only an explicitly provisional research surface;
  - `complete` only when both coverage ratios are at least 95 percent, allowing complete dual-snapshot validation and production PIT capture.
- Distinguish a degraded research preview from a complete published dual-ranking snapshot in selectors, APIs, evidence, and the short-term workbench.
- Keep provider work serial, checkpointed, and bounded to one worker, no more than 20 ETF codes, 5,000 rows, 512 MiB current process RSS, and a hard return below 60 seconds.
- Reduce scheduler churn while preserving adaptive 5-to-20-code catch-up and idempotent no-provider behavior after a complete snapshot has published.
- Compose the existing point-in-time research coordinator into a production continuation that advances at most one bounded page after a complete dual snapshot exists.
- Record market decision cutoff and data receipt cutoff independently so post-close production evidence cannot be mistaken for historically available PIT evidence.
- Carry forward the six factual production and long-running verification items left open in the 2026-07-29 archives without rewriting those archive records.
- Do not change ranking factors, weights, live positions, alert policy, SMTP state, or the prohibition on raw Sina/efinance prices increasing decision coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `etf-publish-readiness-sync`: Correct memory admission semantics, define the readiness state machine, reduce scheduler churn, and require complete-publication idempotency evidence.
- `etf-adjusted-history-depth`: Align publication and research-depth lane transitions with the shared blocked/degraded/complete contract.
- `short-etf-research`: Separate provisional research availability from a complete canonical dual-ranking publication.
- `etf-ranking-surfaces`: Define which consumers may read degraded research previews and which require complete or actionable surfaces.
- `etf-research-evidence-contract`: Preserve independent market decision, data receipt, and research replay cutoffs plus resource and readiness provenance.
- `etf-point-in-time-research-loop`: Add bounded production composition and scheduling after complete dual-snapshot publication without introducing production side effects.

## Impact

- Backend: bounded ETF history synchronization, coverage policy, readiness coordinator, snapshot publication and selection, evidence contracts, strategy-lab PIT composition, scheduler, and job telemetry.
- Frontend: short-term ETF readiness and snapshot-state presentation; no trading-action behavior changes.
- Data: additive job/evidence metadata and checkpoints; no deletion or synthetic backfill of historical membership, receipt time, or adjusted prices.
- Operations: one bounded worker at a time on the existing 2-core/4-GB deployment, with commands and continuations hard-capped below 60 seconds.
- Verification: focused resource, threshold-boundary, scheduler-idempotency, PIT-isolation, API/UI-state, domain-boundary, Ruff, and strict OpenSpec checks plus at least three distinct real trading-day observations.
