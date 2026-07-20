## 1. Baseline and cross-change ownership

- [x] 1.1 Reproduce the four known WIP failures with individually bounded commands: missing walk-forward module, missing point-in-time membership model, migration-head mismatch, and intraday score-source regression.
- [x] 1.2 Apply the frozen ownership table to all three active changes: this change owns readiness/manifest/sync work, `enable-etf-point-in-time-ranking-replay` owns PIT replay and walk-forward work, and `harden-etf-comprehensive-ranking` owns live publication plus 11.2/11.9 rollout; mark cross-change dependencies without duplicating implementation tasks.
- [x] 1.3 Freeze fail-closed regression tests for no raw-price fallback, no fabricated historical production snapshot, no current-survivor inference, and no production/research source merging.
- [x] 1.4 Record the pre-change Alembic head, validation provenance shape, cursor scopes, scheduler settings, and bounded resource baseline without treating local data as production evidence.

## 2. Schema and migration reconciliation

- [x] 2.1 Replace the stale hard-coded migration-head expectation with tests requiring one valid Alembic head, complete upgrade/downgrade coverage, and the exact fields genuinely required by the owner changes.
- [x] 2.2 Reconcile the chain without adding an empty revision: add a new revision only for absent source-manifest or attestation schema, and consume the PIT membership revision from its owning replay change.
- [x] 2.3 Add failing model/migration tests for a validation-to-source-event relation containing source date, source kind, production signal-run or replay-event identity, contract/universe/input/event hashes, cutoff, ordering, immutable hash, and canonical manifest hash.
- [x] 2.4 Implement the source-event relation, indexed date/run lookups, manifest identity on the validation header, uniqueness/check constraints, and empty-database upgrade/downgrade behavior.
- [x] 2.5 Verify the replay-owned membership fact satisfies effective interval, receipt/source id, provider/version, observed-at cutoff, evidence/raw hash, delisted retention, current-survivor rejection, and conflict exclusion contracts before this change consumes it.
- [x] 2.6 Add database constraints and tests enforcing one source kind per manifest, mutually exclusive production/replay event identities, unique source dates, and no duplicate source event in one validation cohort.
- [x] 2.7 Reuse existing nullable provenance columns when canonical and remove no legacy data; ambiguous legacy rows remain nullable and untrusted.

## 3. Registered multi-date source provenance

- [x] 3.1 Add production manifest tests covering every source date and signal-run identity plus success state, exact v3 rule/score/basis, as-of date, count consistency, both 95 percent publication gates, ETF-only items, finite eligible scores, and contiguous ranks.
- [x] 3.2 Add canonicalization tests proving event ordering is deterministic, manifest hash changes on any event/count/identity change, and one linked run cannot prove a multi-date aggregate.
- [x] 3.3 Add research manifest integration tests that consume the replay-owned immutable replay key/events and reject publication state, production run identity, or a mixed contract tuple.
- [x] 3.4 Make validation creation require an explicit registered source kind and complete source manifest; revalidate manifest count, uniqueness, contract compatibility, and hash before aggregate evidence is finalized.
- [x] 3.5 Expose source kind, manifest hash, source-date count, and authorized event identities in validation/evidence responses so the UI cannot present replay or partial provenance as formal-list evidence.
- [x] 3.6 Implement a dry-run provenance repair command that reconstructs every aggregate source event only from factual stored links, reports missing/ambiguous dates, and never guesses source kind or current hashes.
- [ ] 3.7 Test and execute factual repair only for complete unambiguous manifests; leave single-link multi-date, partial, mismatched, or hashless aggregates labelled legacy/unregistered.

## 4. Exchange-session planning and reproducible outcomes

- [x] 4.1 Add pure calendar tests for theoretical minimum 60/100/140/240-session spans for 20 non-overlapping 1/3/5/10-session outcomes, including T+1 entry, holidays, and incomplete future windows.
- [x] 4.2 Add sparse-source tests proving the planner iteratively expands indexed compatible snapshot reads beyond the theoretical span until 20 completed non-overlapping dates exist or a retention/query cap is reached.
- [x] 4.3 Replace the fixed 180-calendar-day query with the exchange-session plus actual-source-date planner and return theoretical span, searched span, retention cap, and exact source-date shortfall.
- [x] 4.4 Separate calculation success from statistical sufficiency, returning compatible, completed, pending, overlapping, excluded, non-overlapping, paired, and required counts for every horizon.
- [x] 4.5 Emit stable reasons for missing manifests, incompatible snapshots, missing adjusted entry/exit legs, insufficient independent or paired dates, low asset/date coverage, sparse sources, and future windows still pending.
- [x] 4.6 Add forward-outcome tests for T+1 adjusted-close entry, T+1-plus-horizon exit, immutable fee/slippage legs, forbidden raw-price repair, and exact pending/exclusion denominators.
- [x] 4.7 Add validation-continuation tests for stable 500-sample pages, 5,000 samples per slice, 768 MiB RSS, no new page after 45 seconds, checkpoint by 55 seconds, exit by 60 seconds, no partial final aggregate, and deterministic resume.
- [x] 4.8 Implement the single-worker validation continuation with an atomic `(manifest_hash, source_date, horizon, asset_key)` checkpoint, no more than eight SQL statements per page, and a new identity on contract mismatch.
- [x] 4.9 Persist immutable production source events and per-ETF/per-horizon completed, pending, overlapping, and excluded samples so counts, coverage, costs, returns, intervals, and aggregate hashes can be reproduced.
- [ ] 4.10 Verify the replay-owned Top10 five-session paired endpoint and walk-forward tasks enforce common completed dates, 20 non-overlapping pairs, 95 percent paired coverage, one frozen primary endpoint, uncertainty output, purge/embargo, and registered effect/drawdown gates.
- [x] 4.11 Preserve the registered 20-date and 95 percent gates and add endpoint tests proving zero compatible sources are `unavailable/unregistered`, while a complete manifest with too few samples is registered `insufficient` rather than zero return or a prediction.

## 5. Freshness, score warm-up, and derived-depth lanes

- [x] 5.1 Add tests for a current-day ETF with fewer than 61 adjusted sessions, proving it leaves the freshness gap but remains score-ineligible and pending in the warm-up lane.
- [x] 5.2 Add tests for independent `daily_freshness`, `history_depth_61`, and `history_depth_required:<contract_hash>` scopes, including scope-hash drift and universe changes; keep 180-session depth telemetry explicitly non-authoritative.
- [x] 5.3 Derive replay depth from warm-up, horizon, independent-date, non-overlap, and future-exit requirements, requiring at least 200 sessions for the five-session primary and at least 300 when ten-session outcomes are requested before exclusions.
- [x] 5.4 Split current-day coverage, 61-session score readiness, contract-derived replay depth, and historical production-snapshot readiness in selection, cursor persistence, health, and APIs.
- [x] 5.5 Calculate denominators from the same decision-eligible point-in-time universe used by the applicable ranking/replay contract and report expected, attempted, covered, eligible, and excluded counts.
- [x] 5.6 Enforce publication semantics: fewer than 61 eligible sessions excludes the ETF from score eligibility, no degraded score is synthesized, and publication requires both decision-data and post-exclusion score coverage to meet 95 percent.
- [x] 5.7 Prevent a same-day upsert from advancing a depth cursor unless contiguous decision-eligible adjusted sessions satisfy that exact lane contract.
- [x] 5.8 Prioritize depth gaps deterministically so current publication candidates and shallowest eligible histories progress without starving the remaining universe.

## 6. Bounded resumable history synchronization

- [x] 6.1 Add deadline/cancellation tests for one worker, no new provider requests after 45 seconds, atomic stop by 55 seconds, process exit by 60 seconds, idempotent resume, no orphan process, and no half-page checkpoint.
- [x] 6.2 Replace unbounded `sync_all_etfs=True` history work with an explicit continuation capped at 10 codes, 500 rows per page, 5,000 fetched rows, 768 MiB process RSS, target date span, and remaining wall-clock budget.
- [x] 6.3 Read existing keys once per page and batch inserts/updates with no more than eight SQL statements per committed page, removing per-row SELECT/UPSERT amplification.
- [x] 6.4 Restrict metric recomputation to the trailing contract-required session window and defer final readiness until the ETF's requested range is complete.
- [x] 6.5 Commit small pages, release ORM/provider objects between pages, and record elapsed time, peak RSS, rows per second, SQL count, retries, exclusions, and last durable checkpoint.
- [x] 6.6 Keep scheduler concurrency at one, reject an overlapping lease with a stable reason, and expose stale/failed checkpoint and circuit-breaker health.
- [x] 6.7 Run a mandatory PostgreSQL production-shaped benchmark with at least 1,400 ETFs by 300 sessions and verify every time, RSS, code/page/row, SQL, cancellation, rollback, and no-orphan acceptance limit.
- [x] 6.8 Add provider-failure tests with per-attempt timeout bounded by eight seconds and remaining safety reserve, explicit reproducible errors, bounded retry/circuit behavior, and no raw Sina/efinance promotion.

## 7. Environment-attested read-only readiness

- [x] 7.1 Add migration/model tests for an immutable database instance UUID, declared environment, creation metadata, expected deploy identity, and non-secret attestation key id without storing signing material in the database or repository.
- [x] 7.2 Implement server provisioning and signed-observation verification so unset, invalid, or mismatched instance/deploy/key identities fail closed as non-production and secrets never enter logs or responses; document that this prevents accidental attribution, not deliberate cloning of identity plus signing material.
- [x] 7.3 Add tests requiring every report to include verified environment identity, schema head, deploy artifact, observed-at time, trade date, contract/manifest hashes, and read-only status.
- [x] 7.4 Implement the readiness service from pre-aggregated health/checkpoint rows plus bounded indexed reads, reporting publication gates, snapshot age, provider health, intraday quote coverage, components, cap violations, non-finite rejects, rank churn, validation exclusions, and source manifests.
- [x] 7.5 Include independent freshness, 61-session depth, contract-derived replay depth, optional 180-session telemetry, compatible production source dates, completed/paired outcome counts, and sufficiency without claiming price depth proves historical v3.
- [x] 7.6 Enforce at most 25 SQL statements, 5,000 inspected/returned rows per statement, a two-second statement timeout, no historical full scan, a ten-second endpoint deadline, and no decision-domain writes.
- [x] 7.7 Add stable blocker keys with observed values and thresholds, redacting credentials, raw endpoints, signing material, and host secrets.
- [x] 7.8 Add the authorized admin/CLI entry point and tests proving local, stale, unsigned, or same-date repeated observations cannot inflate attested production sessions.

## 8. Replay dependency integration and green baseline

- [x] 8.1 Keep PIT membership persistence, replay scorer/materialization, research adapter, paired endpoint, walk-forward, purge/embargo, and holdout implementation exclusively in `enable-etf-point-in-time-ranking-replay`; block integration here until its corresponding tasks pass.
- [ ] 8.2 Run the replay-owned model, migration, cutoff, survivor-bias, source-adapter, outcome, paired, and walk-forward tests against this change's manifest/readiness contracts without copying their implementation.
- [x] 8.3 Fix the intraday comparison regression so same-minute history is used only when reliability gates pass and remains `unavailable` for stale, mismatched, or incomplete evidence.
- [x] 8.4 Add integration tests proving research replay cannot modify production ranking, allocation, tracking, risk alert, action, notification, or SMTP state.
- [ ] 8.5 Make all four known baseline failure groups pass without weakening publication, provenance, freshness, adjusted-price, or provider-consensus assertions.
- [x] 8.6 Add domain-boundary tests for new modules so market data remains upstream of research and notification remains downstream-only.

## 9. Verification and production rollout

- [x] 9.1 Run focused migration, manifest, planner, sync, readiness, replay-integration, intraday, and endpoint test batches with a hard timeout of at most 60 seconds per command.
- [x] 9.2 Run `uv run pytest tests/test_backend_domain_boundaries.py`, targeted regression suites, and `uv run ruff check .`, splitting any suite that cannot finish inside 60 seconds.
- [x] 9.3 Run strict OpenSpec validation for this change and both owner/dependency changes, reconciling every completed checkbox with test, migration, benchmark, or real-environment evidence.
- [x] 9.4 Deploy migrations and code through the existing workflow only after backup and single-head checks pass; verify application/database health and retain a tested rollback point.
- [ ] 9.5 Run signed production readiness before any sync, then execute only single-worker bounded continuations while the applicable adjusted-history lane is below threshold.
- [ ] 9.6 Generate and validate a production shadow only when real current-day, 61-session, decision-data, and score-coverage publication gates pass; otherwise record blockers and stop without fallback publication.
- [ ] 9.7 Accumulate three distinct qualified signed production trade-date sessions for `harden-etf-comprehensive-ranking` 11.2/11.9, never substituting simulation, stale evidence, or repeated same-day runs.
- [ ] 9.8 Keep formal return evidence insufficient until its separate 20 completed non-overlapping paired dates, coverage, registered effect, and drawdown gates pass; three rollout sessions do not satisfy this requirement.
- [ ] 9.9 Archive each change only when its own task list, dependencies, strict validation, and applicable real-environment gates are complete, then disable only the automation whose acceptance work is genuinely finished; do not rely on a hard-coded total task count.
