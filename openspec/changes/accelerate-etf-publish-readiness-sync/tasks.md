## 1. Freeze Current Failures And Safety Contracts

- [x] 1.1 Add a regression test proving the post-close adjusted sync returns waiting when target-date adjusted coverage is below 95 percent and does not publish or use raw fallback rows.
- [x] 1.2 Add a regression test proving target-date coverage and 61-session score warm-up coverage are independent and both must reach 95 percent.
- [x] 1.3 Add provider-budget tests reproducing that an outer timeout around the whole provider chain can prevent accepted fallback, and freeze the expected per-provider fallback behavior.
- [x] 1.4 Add interruption, duplicate-upsert, overlapping-lease, identity-mismatch, RSS-limit, row-limit, and 60-second deadline cases for the publication-readiness profile.

## 2. Extend The Bounded Continuation For Publication Readiness

- [x] 2.1 Add a pure candidate planner that orders target-date gaps before 61-session warm-up gaps, preserves tracked/default-display priority within each class, and rotates all other authoritative ETF codes without starvation.
- [x] 2.2 Extend the bounded request identity with target trade date, publication-readiness selection policy, provider policy version, adjustment contract, price basis, and the frozen resource profile.
- [x] 2.3 Add the conservative 10-code and maximum 20-code publication profiles while retaining one worker, 500 rows per page, 5,000 rows per slice, 512 MiB RSS, 45/55/60-second deadlines, and eight SQL statements per page.
- [x] 2.4 Allow a target-date gap fetch to request the bounded 61-session exchange window plus calendar buffer without increasing the row or memory limits.
- [x] 2.5 Recompute target-date and warm-up completion independently from committed decision-eligible rows after each ETF and advance only factual lane progress.
- [x] 2.6 Persist each complete code/date page, checkpoint, attempted/completed rotation state, resource counters, and remaining counts atomically, then prove compatible resume is idempotent.

## 3. Bound And Harden Adjusted-Price Providers

- [x] 3.1 Define a versioned accepted adjusted-provider policy for TickFlow backward-adjusted, Eastmoney HFQ, and provenance-valid efinance `fqt=2`, excluding Sina and every raw-only result from decision coverage.
- [x] 3.2 Refactor history fetching so each provider attempt receives an independent remaining-time budget of at most six seconds and cannot consume the whole continuation budget before fallback.
- [x] 3.3 Reuse one bounded HTTP connection pool per slice, enforce bounded connection limits, and close or cancel all provider work before the hard process deadline.
- [x] 3.4 Remove fixed per-ETF sleep and repeated same-provider retries from the publication-readiness path; replace them with bounded fallback, rotation, and cooldown.
- [x] 3.5 Persist per-provider consecutive failures, accepted successes, timeout counts, latency, circuit state, and `retry_after` in the latest compatible continuation health payload and restore it on resume.
- [x] 3.6 Add provenance tests proving raw Sina/efinance/intraday rows, incomplete adjusted pairs, non-finite values, mismatched dates, or missing versions never increase readiness coverage.

## 4. Build The Post-Close Readiness Coordinator

- [x] 4.1 Add bounded indexed queries for authoritative-universe target-date coverage and 61-session score coverage, returning compact counts, ratios, reason aggregates, and bounded samples.
- [x] 4.2 Implement the post-close coordinator flow: validate same-day authoritative universe, resolve an existing dual snapshot, measure both gates, run at most one continuation slice, and remeasure both gates.
- [x] 4.3 When either gate remains below 95 percent, persist an explicit partial waiting result with no ranking generation, allocation, alert, email, or fallback side effect.
- [x] 4.4 When both gates pass, build the complete expected and eligible sets once, materialize one full dual-ranking snapshot, run existing publication validation, and preserve idempotent already-published behavior.
- [x] 4.5 Replace `post_close_etf_adjusted_sync_job` use of the old fixed daily sync path with the new coordinator while leaving manual bounded history/research lanes separate.
- [x] 4.6 Add job-level tests for universe failure, daily gap, warm-up gap, provider circuit, continuation partial, gate transition, publication failure, publication success, and repeated invocation.

## 5. Compact Progress And Operational Visibility

- [x] 5.1 Replace repeated full expected/included/excluded code arrays in partial JobRun details with counts, ratios, reason aggregates, at most 20 samples, checkpoint identity, and remaining estimates.
- [x] 5.2 Expose current profile, target date, both coverage ratios, attempted/completed/failed counts, latest stop reason, provider health, elapsed time, peak RSS, rows per second, and checkpoint age in the existing authenticated readiness/admin output.
- [x] 5.3 Preserve full coverage sets only inside final publication validation and verify they are not returned or persisted by default on below-gate runs.
- [x] 5.4 Add serialization and response-size tests proving a 1,500-ETF below-gate result stays bounded independently of universe size except for aggregate counts.

## 6. Add Catch-Up Scheduling And Adaptive Profile Control

- [x] 6.1 Register one weekday post-close catch-up trigger beginning after universe/data close jobs, and use the database lease plus decision context to prevent weekend, pre-close, duplicate, or overlapping work.
- [x] 6.2 Implement conservative profile state using at most 10 codes and a five-minute effective cadence until real production evidence satisfies the promotion gates.
- [x] 6.3 Implement promotion to at most 20 codes and a two-minute effective cadence only when elapsed, RSS, provider-rate-limit, circuit, and checkpoint-health gates pass.
- [x] 6.4 Implement automatic demotion to the conservative profile when resource or provider health degrades, without changing the 60-second limit or starting concurrent workers.
- [x] 6.5 Stop catch-up provider work after a target-date dual snapshot is published, when the universe is not authoritative, or when the configured post-close window ends.
- [x] 6.6 Add deterministic scheduler/profile tests for initial cadence, promotion, demotion, lease overlap, published stop, and next-trading-day identity reset.

## 7. Bounded Performance And Regression Verification

- [x] 7.1 Run a PostgreSQL-shaped 1,500 ETF × 61 session benchmark for the 10-code profile and record elapsed time, peak RSS, rows per second, SQL statements, and checkpoint monotonicity.
- [x] 7.2 Run the same benchmark for the 20-code profile and require P95 elapsed below 30 seconds, peak RSS below 512 MiB, page SQL at most eight, and hard exit below 60 seconds before enabling it.
- [x] 7.3 Verify provider timeout/circuit tests with blocked primary, working adjusted fallback, all-provider failure, raw-only fallback, cancellation, and no orphan task cases.
- [x] 7.4 Run the focused short ETF sync, bounded history, readiness, snapshot publication, dual ranking, scheduler, API, and frontend/admin tests with every command capped at 60 seconds.
- [x] 7.5 Run `uv run pytest tests/test_backend_domain_boundaries.py`, relevant Ruff checks, TypeScript checks, migration-head checks if schema changes, and strict OpenSpec validation with per-command timeouts of at most 60 seconds.

## 8. Controlled Production Rollout

- [ ] 8.1 Deploy the coordinator with the conservative 10-code/five-minute profile and confirm no legacy unbounded or concurrent synchronization entry point is scheduled.
- [ ] 8.2 Record at least three real VPS slices with authoritative universe hash, both coverage deltas, elapsed time, RSS, provider health, checkpoint identity, stop reason, response size, and absence of raw decision inputs.
- [ ] 8.3 Promote to the 20-code/two-minute profile only if all promotion gates pass; otherwise retain the conservative profile and record the exact blocker.
- [ ] 8.4 Continue bounded slices until both real production coverage ratios reach 95 percent, then verify one full dual-ranking snapshot publishes without lowering gates or using Sina/efinance raw prices.
- [ ] 8.5 Confirm subsequent triggers skip provider work for the published trade date, the comprehensive ranking API returns the published research surface, and server health remains within the 2-core/4-GB profile.
- [ ] 8.6 Record rollback instructions and final production evidence, then run strict validation before marking the change complete.
