## Why

Production has 1,485 authoritative ETFs, but decision-eligible
`total_return_adjusted` history is currently only 2–89 sessions per ETF with a
69-session median, while raw history has a 503-session median. The existing
publication continuation intentionally stops after current-date and 61-session
coverage, and the existing 300-session backfill is manual and limited to ten
codes per invocation. This restores publication safety but leaves point-in-time
research, walk-forward validation, and market-regime evidence starved.

## What Changes

- Keep target-date publication coverage at 95 percent, set the independent
  61-session score-warmup publication minimum to 90 percent, and expose
  90–95 percent snapshots as degraded coverage rather than complete coverage.
- Add a separate, lower-priority research-depth coordinator that runs only after
  publication prerequisites pass, advances the existing 300-session contract
  lane first, and advances a non-authoritative 500-session telemetry lane only
  after the 300-session lane is complete enough.
- Reuse the single-worker bounded history runner with adaptive 5–20-code
  profiles, at most 5,000 rows, 500 rows per page, 512 MiB RSS, 45/55/60-second
  deadlines, and durable rotation across profile changes.
- Persist provider-observed adjusted-history availability and cooldowns so a
  genuinely short or upstream-truncated history is not retried every slice.
  Provider observation is not treated as an inferred listing date.
- Continue to accept only fully versioned `total_return_adjusted` rows. Sina,
  efinance raw, intraday snapshots, estimated prices, and display-only rows
  cannot increase any publication or research-depth coverage.
- Schedule one serial research-depth slice every two minutes in a bounded
  post-publication window that does not overlap publication catch-up or later
  strategy jobs.

## Capabilities

### New Capabilities

- `etf-adjusted-history-depth`: Defines prioritized 300/500-session adjusted
  history accumulation, adaptive bounded execution, availability cooldowns, and
  compact research-depth evidence.

### Modified Capabilities

- `short-etf-research`: Adds a scheduled post-publication research-depth lane
  without changing the ranking formula; only the independent score-warmup
  publication minimum changes.
- `short-etf-research-reliability`: Persists adjusted-provider availability
  observations without treating raw history or inferred listing dates as
  decision evidence.

## Impact

- Backend bounded ETF history synchronization, post-close scheduling, readiness
  projection, and compact JobRun telemetry.
- One additive table for per-ETF adjusted-history availability observations and
  retry cooldown.
- No ranking/API contract change, no new worker concurrency, and no production
  allocation, position, alert, or notification side effect.
