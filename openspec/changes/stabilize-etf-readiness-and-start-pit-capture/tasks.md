## 1. Freeze The Shared Readiness Contract

- [x] 1.1 Add threshold-boundary tests for 89.99%, 90%, 94.99%, and 95% combinations and assert the exact `blocked`, `degraded`, and `complete` state.
- [x] 1.2 Implement one pure readiness-policy result containing policy version, state, daily ratio, warm-up ratio, and stable blocker reasons.
- [x] 1.3 Replace independent threshold branching in history readiness, publication coordination, snapshot publication, and snapshot selection with the shared policy.
- [x] 1.4 Add legacy-snapshot tests proving effective readiness is derived from immutable coverage and contract fields without rewriting existing rows.
- [x] 1.5 Add invariants proving degraded results cannot satisfy complete publication, actionable selection, PIT prerequisites, allocation, or rank-derived email selection.

## 2. Correct RSS Admission And Telemetry

- [x] 2.1 Add platform-focused tests that distinguish current RSS from lifetime high-water RSS and cover an unavailable current-RSS measurement.
- [x] 2.2 Implement bounded current-process RSS measurement for production Linux and preserve the existing Windows current working-set behavior.
- [x] 2.3 Split resource telemetry into configured ceiling, baseline, current, slice-peak current, lifetime peak, and delta values.
- [x] 2.4 Change provider and page admission to use only current RSS, fail closed when production current RSS is unavailable, and keep the 512 MiB limit unchanged.
- [x] 2.5 Verify a historical peak above 512 MiB no longer permanently blocks a recovered process while a real current-RSS breach still checkpoints and returns `rss_limit`.
- [x] 2.6 Extend compact readiness and provider-health projections with the new bounded resource fields without embedding repeated universe arrays.

## 3. Separate Provisional Research From Complete Publication

- [x] 3.1 Add materialization tests for blocked, degraded, and complete readiness, including exact eligible and excluded code sets.
- [x] 3.2 Persist policy version and readiness state in additive snapshot summary/evidence metadata.
- [x] 3.3 Materialize a degraded run only as a provisional research preview with no complete dual-publication or actionable identity.
- [x] 3.4 Require dual 95-percent readiness for complete canonical publication and preserve one complete snapshot per trade date and contract.
- [x] 3.5 Update research, complete, and actionable selectors so each rejects incompatible readiness and surface identities.
- [x] 3.6 Keep raw Sina/efinance, stale, estimated, intraday-only, and non-total-return-adjusted rows outside every decision, preview, complete, and PIT coverage numerator.
- [x] 3.7 Return stable unavailable reasons instead of selecting an old or incompatible target-date snapshot.

## 4. Make Catch-up Scheduling Due-aware

- [x] 4.1 Add scheduler tests proving non-due ticks create no tracked slice, provider client, or concurrent worker.
- [x] 4.2 Add a lightweight cadence, trading-session, universe, lease, and complete-publication preflight before tracked work is created.
- [x] 4.3 Preserve the conservative 10-code/5-minute and maximum 20-code/2-minute profiles without increasing concurrency or any hard limit.
- [x] 4.4 Ensure resource or provider degradation returns to the conservative profile and resumes the same durable continuation identity.
- [x] 4.5 Verify every trigger after complete target-date publication skips provider work and does not create repeated no-op continuation records.
- [x] 4.6 Add compact cadence, skip, lease, checkpoint, provider-health, and ETA evidence to the admin data-status projection.

## 5. Compose Production PIT Capture

- [x] 5.1 Add workflow tests for complete-publication gating, one-page advancement, interruption resume, lease exclusion, and no-live-provider behavior.
- [x] 5.2 Implement workflow-layer construction of the immutable PIT manifest and existing phase-handler registry without duplicating strategy algorithms.
- [x] 5.3 Persist `market_decision_cutoff`, `data_receipt_cutoff`, and `replay_visibility_cutoff` independently and reject missing or incompatible cutoff provenance.
- [x] 5.4 Exclude membership and adjusted rows received after a historical replay visibility cutoff without inferring earlier availability.
- [x] 5.5 Add a due-aware scheduler entry that advances at most one PIT page below 55 seconds only after a complete current-contract dual snapshot exists.
- [x] 5.6 Persist factual eligible dates, independent primary dates, folds, exclusions, resource peaks, phase cursor, and stable insufficient-data reasons.
- [x] 5.7 Prove PIT continuation cannot update production ranking, allocation, tracked positions, alerts, notification logs, SMTP state, score weights, or holdout state outside its declared research contract.

## 6. Expose Honest API And Workbench States

- [x] 6.1 Extend API schemas and TypeScript types with readiness state, policy version, snapshot state, cutoff provenance, compact resource evidence, and stable unavailable reason.
- [x] 6.2 Show blocked coverage and next-step reasons without displaying a stale target-date ranking.
- [x] 6.3 Show degraded research rows with a persistent provisional banner, the two coverage ratios, cutoff, exclusions, and no actionability implication.
- [x] 6.4 Show complete research and actionable availability separately and keep the comprehensive research surface selected by default.
- [x] 6.5 Add frontend static tests proving provisional evidence cannot be labeled complete, actionable, validated, or live-email evidence.

## 7. Focused Verification

- [x] 7.1 Run the current-RSS and bounded-history-sync test group with an explicit timeout below 60 seconds per command.
- [x] 7.2 Run readiness policy, coordinator, materializer, publisher, selector, and scheduler test groups with explicit timeouts below 60 seconds per command.
- [x] 7.3 Run PIT manifest, replay-input, continuation, production-isolation, and evidence API test groups with explicit timeouts below 60 seconds per command.
- [x] 7.4 Run frontend TypeScript and focused static-contract checks with explicit timeouts and no production writes.
- [x] 7.5 Run `uv run pytest tests/test_backend_domain_boundaries.py` under an explicit timeout and fix any dependency-direction violation.
- [x] 7.6 Run Ruff on changed backend and test files in bounded groups, then run the repository-level Ruff check only with an explicit timeout and process-status handling.
- [x] 7.7 Run strict OpenSpec validation for this change and all canonical specs.

## 8. Bounded Production Rollout And Real Evidence

- [x] 8.1 Document the six open verification items carried from the 2026-07-29 archives and map each one to this change without editing archive history.
- [ ] 8.2 Deploy the readiness policy and RSS correction with production PIT scheduling disabled, recording artifact version and rollback switches.
- [ ] 8.3 On one real trading day, record authoritative universe, daily and warm-up coverage, provider health, raw-price violations, non-finite violations, baseline/current/slice/lifetime RSS, checkpoint, and server resources.
- [ ] 8.4 If readiness is degraded, verify the provisional research preview is honest, complete/actionable selectors reject it, and one bounded continuation makes monotonic progress.
- [ ] 8.5 After dual 95-percent coverage factually passes, verify exactly one complete dual snapshot publishes and the comprehensive research API returns that snapshot.
- [ ] 8.6 Verify subsequent publication-readiness triggers for the same trade date perform no provider work and remain within the 2-core/4-GB resource profile.
- [ ] 8.7 Enable production PIT scheduling only after complete publication and record monotonic one-page checkpoints across at least three different real trading dates without counting repeats.
- [ ] 8.8 Record final cutoff, coverage, provider, resource, publication, PIT, isolation, and rollback evidence; do not substitute simulated, stale, raw-price, or same-day duplicate evidence.
- [ ] 8.9 Mark the change complete only after all focused checks, domain boundaries, Ruff, strict OpenSpec validation, and the required real-session evidence pass.
