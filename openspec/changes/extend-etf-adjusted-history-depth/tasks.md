## 1. Freeze Contracts

- [x] 1.1 Add tests proving publication work always outranks 300/500-session
  research work and the original coverage contracts are frozen before the
  explicitly approved split in section 7.
- [x] 1.2 Add tests proving 300 sessions are authoritative research depth while
  500 sessions are non-authoritative telemetry.
- [x] 1.3 Add tests proving raw/display-only data and inferred listing dates
  cannot increase either depth.

## 2. Persist Adjusted-History Availability

- [x] 2.1 Add an additive per-ETF adjusted-history availability model and
  migration with provider identity, observed range, eligible count, status,
  observation time, and retry-after.
- [x] 2.2 Record factual short-history observations only from accepted adjusted
  provider results and never label the first returned date as a listing date.
- [x] 2.3 Defer active-cooldown codes without removing them from coverage or
  blockers, and prove retries resume after cooldown.
- [x] 2.4 Continue through provenance-valid adjusted fallbacks when an earlier
  provider is shallower than the requested lane, and retain the deepest valid
  partial response when every provider is short.
- [x] 2.5 Add an independently hosted Tencent raw-plus-hfq fallback with a
  distinct provider version, row pairing, and rejection tests so Eastmoney
  network blocks never cause raw-price substitution.
- [x] 2.6 Apply factual short-history cooldowns and durable attempt rotation to
  the 61-session publication lane so newly listed ETFs remain blockers without
  starving repairable gaps.
- [x] 2.7 Evaluate publication availability cooldowns against the exact frozen
  61-session calendar so an ETF with an older buffer row but one missing
  required session cannot be retried indefinitely.

## 3. Extend The Bounded Runner

- [x] 3.1 Add a `research_depth` selection policy with 5–20 codes, 500-row pages,
  5,000-row slices, 512 MiB RSS, six-second provider attempts, and 45/55/60
  deadlines.
- [x] 3.2 Preserve durable rotation through adaptive profile identity changes by
  using the scope cursor as the compatible fallback anchor.
- [x] 3.3 Add adaptive profile tests for healthy growth, timeout/memory/circuit
  backoff, bounds, and result invariance.
- [x] 3.4 Add interruption, duplicate-upsert, row-limit, RSS-limit, lease, and
  compact-checkpoint regression tests for the new policy.

## 4. Add The Research-Depth Coordinator

- [x] 4.1 Add a coordinator that starts no work below either publication gate,
  advances 300 sessions first, and advances 500-session telemetry only after the
  300-session lane is ready.
- [x] 4.2 Reuse the authoritative point-in-time universe and accepted adjusted
  provider contract without introducing a second fetch or persistence engine.
- [x] 4.3 Return compact coverage, deferred availability, effective profile,
  resource, provider-health, and checkpoint evidence.

## 5. Schedule Without Concurrency

- [x] 5.1 Register one weekday 23:00–23:28 two-minute trigger with
  `max_instances=1` and coalescing.
- [x] 5.2 Prove the trigger does not start provider work while publication
  priority, another history lease, weekend, or an incompatible universe is
  active.

## 6. Validate

- [x] 6.1 Run focused history sync, readiness, coordinator, scheduler, migration,
  provenance, and compact-response tests in separately bounded commands.
- [ ] 6.2 Run backend domain-boundary and Ruff checks plus strict OpenSpec
  validation in separately bounded commands.
- [x] 6.3 Record the deploy/rollback path and leave real production coverage
  tasks open until bounded VPS slices provide factual evidence.

## 7. Split Publication Coverage Policy

- [x] 7.1 Keep target-date decision coverage at 95 percent, set only the
  61-session score-warmup publication minimum to 90 percent, and keep 300/500
  research-depth completion at 95 percent.
- [x] 7.2 Publish and select 90–95 percent score-coverage snapshots as
  `degraded`, exclude insufficient-history ETFs, and expose the policy mode in
  the API and ETF research UI.
- [x] 7.3 Add boundary tests for 89/90/95 percent, run focused backend and
  frontend checks, deploy, and verify production without raw-price substitution.

## 8. Bound Research-Depth Preflight

- [x] 8.1 Materialize the target-session set before per-ETF aggregation so the
  300-session research lane does not execute a full-universe outer join with a
  nested session subquery; preserve watchlist-first and shallowest-first order.
- [x] 8.2 Deploy the preflight optimization and prove one production slice
  reaches provider work or returns a durable bounded result within 60 seconds.

## Production Evidence — 2026-07-25

- Coverage-policy deploy: `c1d7f19a043c0986bf5c57359e67e172b689fefd`.
  Research-depth preflight deploy:
  `e55d674ae2a8653d6f9932f4e055ec2f1ddd73d0`. Both are code-only changes with
  no schema migration. Rollback is a normal `git revert` of the affected SHA
  followed by the existing deploy workflow; production data remains compatible.
- For trade date 2026-07-24 the authoritative universe contained 1,485 ETFs.
  Daily total-return-adjusted coverage was 1,484/1,485 (99.93 percent) and the
  strict 61-session warm-up coverage was 1,364/1,485 (91.85 percent), producing
  `coverage_policy_mode=degraded` with thresholds 95/90 percent. Raw or
  unversioned decision violations and non-finite decision values were both zero.
- Formal publication was correctly refused by the independent PIT barrier:
  1,484 rows were received after the 15:00 data cutoff and one row was missing.
  No late data was relabeled, no raw-price substitute was used, and no snapshot
  was published.
- The first production research-depth slice after the preflight optimization
  returned in 12.54 seconds, attempted 10 ETFs, fetched and persisted 633
  total-return-adjusted rows at 50.49 rows/second, used 190,537,728 bytes peak
  RSS, and saved checkpoint
  `85d76d767a865007219f80ab58712b31913682c6a12be4f4b720ff0b907e228c`.
  All ten selected ETFs remained below 300 contiguous sessions, so real 300-day
  coverage remains open rather than being overstated.

## 9. Complete Seasoned Histories Efficiently

- [x] 9.1 Persist authoritative provider-observed ETF listing dates with source
  and observation time; never infer or backdate them from price history.
- [x] 9.2 Keep the full point-in-time publication denominator and project
  independent seasoned 300/500 research cohorts, stable cohort/exclusion hashes,
  and a 95 percent listing-metadata completion gate.
- [x] 9.3 Freeze research dates from the observed ETF exchange-session calendar
  while proving raw rows can identify dates but never increase adjusted
  coverage.
- [x] 9.4 Change research selection to completion-first and fetch only each
  code's missing required-date span with dynamic provider minimums.
- [x] 9.5 Add the reversible migration and focused normalization, cohort,
  no-raw-substitution, queue, gap-window, idempotency, and coordinator tests.
- [ ] 9.6 Run bounded Ruff, domain-boundary, migration-head, focused test, and
  strict OpenSpec validation; deploy and record one factual production slice
  without starting a concurrent or unbounded sync.
