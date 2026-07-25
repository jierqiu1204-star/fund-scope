## 1. Freeze Contracts

- [x] 1.1 Add tests proving publication work always outranks 300/500-session
  research work and both 95 percent gates remain unchanged.
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
- [ ] 6.3 Record the deploy/rollback path and leave real production coverage
  tasks open until bounded VPS slices provide factual evidence.
