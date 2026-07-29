## 1. Verify Ordered Prerequisites

- [x] 1.1 Verify separate research/actionable ranking contracts are implemented and catalyst inputs are forbidden from both scores.
- [x] 1.2 Verify the immutable Strategy Lab experiment and production-isolation boundaries from `validate-etf-factor-incremental-alpha`.
- [x] 1.3 Add domain tests proving catalyst ingestion and evidence cannot import or mutate allocation, tracked positions, risk alerts, notifier, or ranking formulas.

## 2. Store Source Registry And Immutable Receipts

- [x] 2.1 Define and approve the initial official/public source allowlist, timezone, cadence, fetch policy, and raw-content retention policy.
- [x] 2.2 Write tests and migrations for versioned source registry, immutable receipts, published and first-received times, hashes, parser versions, fetch outcomes, and correction lineage.
- [x] 2.3 Implement idempotent receipt ingestion and deduplication by source identity, external ID or canonical URL, and content hash.
- [x] 2.4 Add tests that distinguish successful-empty, unavailable, and not-applicable source observations.

## 3. Normalize Event Facts And Theme Mappings

- [x] 3.1 Write schema tests for score-free event versions, categorical direction, direct/proxy mappings, taxonomy version, verification state, and supersession lineage.
- [x] 3.2 Implement deterministic normalization from registered receipts to pending and verified event versions.
- [x] 3.3 Constrain AI extraction to bounded schema output with receipt citations and tests for hallucinated dates, entities, direction, and theme mappings.
- [x] 3.4 Preserve raw receipts and auditable errors when AI extraction fails without fabricating fallback events.
- [x] 3.5 Migrate legacy manual seeds to `manual_display_only` and verify they cannot enter verified snapshots or ranking inputs.

## 4. Build Point-In-Time Shadow Snapshots

- [x] 4.1 Write historical-cutoff tests for first-received time, late discovery, source corrections, effective periods, and deterministic snapshot hashes.
- [x] 4.2 Implement cached theme/session snapshots with `active`, `observed_none`, `unavailable`, and `not_applicable` states and source-level reasons.
- [x] 4.3 Add tests proving unavailable coverage cannot become neutral or observed-none evidence.
- [x] 4.4 Add tests proving active catalyst events leave research score, actionable score, both ranks, allocation, alerts, and notifications unchanged.

## 5. Expose Shadow Evidence

- [x] 5.1 Add evidence-contract serialization for source registry, receipts, events, mappings, snapshots, coverage, extraction, verification, and hashes.
- [x] 5.2 Add cached API fields for the latest ETF catalyst shadow without external fetch or AI inference during page reads.
- [x] 5.3 Add `/short-term` detail states for active direct events, proxy mappings, observed-none, unavailable, not-applicable, corrections, and shadow-only limitations.
- [x] 5.4 Add frontend tests proving catalyst context is visually separate from research rank, actionable rank, allocation, tracked-position actions, and email state.

## 6. Run Research-Only Event Studies

- [x] 6.1 Add immutable event-study manifests with frozen event cohorts, direct/proxy policy, controls, execution, costs, 1/3/5/10-session horizons, exclusions, splits, uncertainty, and multiplicity rules.
- [x] 6.2 Build point-in-time verified-event cohorts and same-date matched controls from decision-eligible adjusted data with common-support reporting.
- [x] 6.3 Calculate observed return, matched excess return, adverse and favorable excursion, coverage, exclusions, and block-bootstrap uncertainty.
- [x] 6.4 Mark small or unstable cohorts `insufficient_data` and prove event-study results cannot derive a score, weight, cap, rank delta, allocation, alert, or email rule.

## 7. Bound And Verify Production Shadowing

- [x] 7.1 Fetch sources with one worker, bounded item batches, exclusive run locking, idempotent cursors, and hard operation timeouts of at most 55 seconds.
- [x] 7.2 Process extraction and mapping in bounded resumable batches and prevent concurrent duplicate full runs.
- [x] 7.3 Run a small allowlisted-source shadow session and record receipts, duplicates, corrections, successful-empty observations, outages, coverage, latency, and cursor evidence.
- [x] 7.4 Run relevant ingestion, no-lookahead, production-isolation, domain-boundary, frontend, and strict OpenSpec validation commands with individual timeouts of at most 55 seconds.
- [x] 7.5 Confirm catalyst ranking weight remains zero and require a separate proposal before any future scoring change.

## 8. Repair Acceptance Regressions

- [x] 8.1 Align ORM and Alembic checkpoint status constraints with every runner state, including `idle` and `failed`.
- [x] 8.2 Require dated, verified exchange-session paths for catalyst T+1 entry and 1/3/5/10-session exits.
- [x] 8.3 Replace the mislabeled Bonferroni confidence adjustment with actual Holm-Bonferroni primary-comparison evidence.
- [x] 8.4 Make catalyst database lease acquisition atomic across workers while retaining the in-process single-worker bound.
- [x] 8.5 Run one bounded real allowlisted-source shadow session and record honest source receipts or reproducible unavailable evidence without substituting fixtures.
- [x] 8.6 Re-run bounded catalyst, frontend, domain-boundary, Ruff, migration, and strict OpenSpec validation.
