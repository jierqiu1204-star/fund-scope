## Context

The production ranking path and the historical research path are intentionally different. Production evidence must come from immutable `published/full/final_score_v3` snapshots and future decision-eligible adjusted prices. Historical dates that lack point-in-time quote structure, IOPV, premium consensus, catalysts, or authoritative universe receipts can only use the separately identified `daily_reconstructable_v1` research contract.

Three implementation defects currently obscure that boundary. First, the integrated score-bucket validator links published snapshots but does not persist `ranking_source_kind=production_published` or the complete multi-date source manifest, so the API cannot register the aggregate evidence source. Second, a fixed 180-calendar-day query is shorter than the exchange-session span required by the non-overlapping 5- and 10-session gates and does not search around sparse real snapshot dates. Third, the small-server sync cursor treats an ETF with today's row as fresh even when it lacks the 61-session adjusted warm-up or contract-derived replay depth needed by scoring and validation, while the separate full-history job is unbounded and performs row-level read/write work.

The checked-in WIP also contains deliberately failing future-facing tests: the point-in-time membership fact model/migration and ranking walk-forward module are absent, and one intraday eligibility regression remains. These are checkpoint facts, not production-ready behavior. All commands and continuations in this change must remain bounded to 55 seconds on the 2-core/4-GB deployment target.

## Goals / Non-Goals

**Goals:**

- Make every successful ranking validation carry one source kind and a verifiable canonical manifest of all production or replay source events.
- Make sample sufficiency derive from exchange sessions, outcome horizon, non-overlap, coverage, and future-window completion.
- Make current-day freshness and historical warm-up depth independently observable and independently resumable.
- Replace the unbounded history job with one-worker, checkpointed, memory-bounded continuations.
- Provide a read-only environment-attested readiness report so local, acceptance, and VPS facts cannot be merged accidentally.
- Restore a green bounded-test baseline for the checked-in WIP before production rollout resumes.

**Non-Goals:**

- Fabricating historical `final_score_v3` snapshots or backdating publication identities.
- Treating `daily_reconstructable_v1`, policy shadow, SMTP acceptance, provider delivery, and user-confirmed execution as the same evidence.
- Optimizing score weights, selecting the best horizon after outcomes, or automatically changing live ranking, allocation, alert, or notification policy.
- Running concurrent provider workers or an unbounded full-universe command on the production server.
- Exposing database credentials, tokens, hostnames with credentials, or raw environment secrets in readiness output.

## Decisions

### 1. Persist one source kind and a complete multi-date source manifest

Production score-bucket validation creates its run with `ranking_source_kind=production_published`; it never carries a replay key. It persists an ordered child event for every source date containing the real signal-run id, date, contract/universe/input/event hashes, cutoff, score tuple, basis, and immutable hash, plus a canonical manifest hash on the validation header. Research validation creates its run with `ranking_source_kind=research_replay`, an immutable `source_replay_run_key`, ordered replay source events, and a manifest hash; it never points to a production signal run. Completion revalidates manifest order, count, uniqueness, cohort compatibility, and hash before aggregates become registered evidence.

Existing validation rows are repaired only when every source date and immutable event can be enumerated factually and all identities agree. A single linked signal-run id cannot repair a multi-date aggregate. Ambiguous, partial, legacy, mismatched, or missing-source rows remain null/legacy. The migration reuses existing nullable provenance columns where they are canonical, adds only the manifest/event relation that is absent, and does not invent a duplicate revision merely because a WIP test expected a later head.

Alternative considered: infer source kind only in the API. Rejected because persistence, SQL evidence grouping, and downstream audits would remain ambiguous.

### 2. Plan validation windows in exchange sessions, not fixed calendar days

A pure planner accepts the horizon set, required independent-date count, non-overlap rule, exchange calendar, future-window requirement, and maximum retention. It first computes the theoretical minimum spans of 60/100/140/240 exchange sessions for 20 non-overlapping 1/3/5/10-session outcomes. It then walks actual compatible source snapshots and iteratively expands indexed reads until it obtains the requested completed cohort or reaches a declared retention/query cap. The result reports theoretical span, searched span, required, available, compatible, completed, overlapping, pending, and excluded dates.

The default production primary endpoint remains Top10 five-session paired net excess return with 20 common completed non-overlapping candidate/baseline dates and 95% paired coverage. The execution-cost contract, uncertainty method, effect gate, and drawdown non-inferiority gate are frozen in the candidate registry before outcomes are observed. A request that includes the ten-session horizon uses the longer derived span. If history or future outcomes cannot satisfy the plan, the run succeeds as an evidence record but reports `insufficient_independent_dates` or `future_window_pending`; it does not return a context-free `N/A` and does not reduce the gate.

The state machine distinguishes source absence from sample insufficiency. Zero compatible source events or an invalid manifest may finish diagnostic execution, but the evidence result is `unavailable/unregistered`, carries no registered success manifest, and cannot receive a direction. A complete compatible manifest with too few completed/paired samples is a registered successful evidence run whose direction is `insufficient` with exact blockers.

Alternative considered: change the default from 180 to a fixed 420 days. Rejected as the only fix because holidays, horizon changes, retention, and already-completed dates still require calendar-aware planning. A conservative calendar-day cap may remain as an operational limit.

### 3. Validation materialization is a bounded continuation

Validation may create more than one million ETF/date/horizon observations, so it cannot run as one transaction or rely on an external command kill. One worker processes stable `(manifest_hash, source_date, horizon, asset_key)` pages under the same small-server envelope: 500 samples per page, 5,000 samples per slice, at most eight SQL statements per page, 768 MiB process RSS, no new page after 45 seconds, atomic checkpoint by 55 seconds, and process exit by 60 seconds. A partial slice exposes progress and samples but never publishes a partial aggregate as final evidence. Resume is idempotent; a manifest, execution, candidate-registry, horizon, or schema mismatch requires a new validation identity.

### 4. Use independent synchronization lanes

The existing cursor table uses distinct `scope` namespaces for:

- `daily_freshness:<universe>`: candidates missing the target trade date;
- `history_depth_61:<universe>`: candidates with fewer than the required recent eligible adjusted sessions;
- `history_depth_required:<contract_hash>:<universe>`: candidates missing the exchange-session depth derived from warm-up, horizon, independent-date, and future-exit requirements.

`history_depth_180:<universe>` may remain an optional operational telemetry lane but never proves replay sufficiency. With a 61-session warm-up, the registered Top10 five-session primary endpoint needs at least 200 eligible sessions and a request including ten-session exploratory outcomes needs at least 300, before accounting for exclusions or sparse actual source facts. Production v3 historical readiness additionally requires compatible point-in-time published snapshots; price depth alone cannot prove it.

Freshness coverage is necessary but not sufficient for same-session publication. Fewer than 61 eligible sessions makes that ETF explicitly score-ineligible; no degraded score is synthesized. A full run may publish only after excluding such items when both registered decision-data and score-eligible coverage ratios still meet 95 percent. Advancing one lane never advances another. Selection is based on decision-eligible `total_return_adjusted` rows and exchange sessions, not the latest raw row or a simple row count that includes duplicates/fallbacks.

The eligible universe and the provider fetch universe are compared before a batch. Eligible/watchlist drift produces a stable exclusion and does not count an unattempted code as processed.

Alternative considered: overload the current missing/stale selector with a larger `from_date`. Rejected because an ETF with one current row would still mask a historical-depth gap and cursor progress would remain ambiguous.

### 5. Historical backfill is a resumable code/date continuation

A backfill identity freezes universe hash, target date range, price basis, provider policy, adjustment contract, and page size. A dedicated additive checkpoint stores the last fully committed code/date page, counts, exclusions, and contract hash. The production acceptance profile is one worker, at most 10 codes, 500 rows per page, 5,000 fetched rows, eight SQL statements per page, 768 MiB process RSS, provider attempts bounded by an eight-second timeout and remaining budget, no new requests after 45 seconds, atomic stop by 55 seconds, and process exit by 60 seconds. Each invocation:

1. acquires one non-overlapping worker lease;
2. selects one bounded code cohort from the history-depth lane;
3. fetches with provider-level timeouts and circuit-breaker state;
4. processes rows in stable date pages;
5. reads existing keys once per page and bulk inserts/updates changed rows;
6. commits the page and checkpoint atomically;
7. recomputes metrics from only the bounded trailing window after a code is complete;
8. stops accepting provider work at 45 seconds and completes cancellation, commit/rollback, checkpoint, and process exit within the 55/60-second limits.

Retrying the same continuation is idempotent. A contract/hash mismatch starts a new identity rather than resuming incompatible work. Normal deadline exhaustion is `partial`, not `failed`. Provider or data-basis failures remain explicit and never promote raw Sina/efinance values.

Alternative considered: retain `sync_all_etfs=True` and add a longer process timeout. Rejected because it cannot checkpoint inside the universe, amplifies N+1 database work, and can exhaust the server.

### 6. Readiness is a read-only, environment-attested projection

An orchestration/admin service, not the evidence-contract domain, assembles the readiness report. Each database is provisioned with an immutable random instance UUID and declared environment. Production configuration supplies the expected UUID, deploy artifact identity, and server-owned attestation key id/material outside the repository. The response contains the UUID, environment, deploy identity, Alembic head, non-secret key id, signed observation, target trade date, compatible source-manifest dates, registered validation source counts, current-day/warm-up/derived-depth coverage, provider/job/checkpoint health, resource peaks, and stable blockers. Unset or mismatched identity/signature fails closed as non-production.

The attestation threat model is accidental environment misattribution and configuration drift. It does not claim to detect an authorized operator deliberately cloning the database UUID, deploy configuration, and signing material together. Adversarial clone resistance would require an external challenge verifier or a non-exportable platform key and is outside this change.

The endpoint requires the existing approved-admin authorization and performs only bounded SELECTs. It uses pre-aggregated health/checkpoint rows, at most 25 SQL statements, at most 5,000 inspected/returned rows per statement, a two-second statement timeout, no full historical-price scan, and a ten-second endpoint deadline. It never starts sync, publication, replay, alert, or notification work. Evidence documents may call a session production only when the complete signed attestation verifies.

Alternative considered: rely on free-form Markdown evidence and local `.env` inspection. Rejected because those mechanisms caused the local-versus-VPS attribution error.

### 7. Production evidence and research replay remain parallel lanes

Exact production validation consumes only published v3 snapshots. Reconstructable historical ranking uses immutable research artifacts and the `research_replay` source kind. Both may use the same forward adjusted-price table, but their samples, coverage, intervals, confidence, and evidence status never merge.

This change integrates, but does not duplicate, the unfinished work in `enable-etf-point-in-time-ranking-replay`. That change exclusively owns the membership fact model/migration, replay scorer/materialization, research source adapter, paired endpoint, walk-forward, purge/embargo, and holdout. This repair owns the shared manifest contract and verifies those dependency outputs before exposure. `harden-etf-comprehensive-ranking` exclusively owns live publication behavior and tasks 11.2/11.9. Final holdout remains one-time and unavailable until point-in-time coverage and sample gates pass.

Alternative considered: wait only for future production sessions and remove historical research output. Rejected because users still need truthful historical research results, provided they are visibly separate from production proof.

### 8. The checked-in WIP must become green before rollout mutations

Implementation begins by reproducing and classifying the known failures. It verifies that `enable-etf-point-in-time-ranking-replay` supplies the membership fact migration/model and frozen walk-forward contract under one Alembic head, without reimplementing them here. This change adds only its owned source-manifest/attestation schema and diagnoses the intraday score-source regression without weakening quote freshness or provider-consensus gates. Ruff, migration-head, domain-boundary, targeted tests, and strict OpenSpec validation must pass before any real-data continuation is authorized.

Alternative considered: treat the WIP commit as a deployable baseline. Rejected because it contains collection failures and a behavioral regression by design.

## Risks / Trade-offs

- [Warm-up backfill takes multiple sessions] → Report monotonic lane progress and ETA ranges; never increase concurrency on the small server.
- [Provider calls consume most of a 55-second slice] → Use per-request timeouts, circuit breaking, and checkpoint before/after each durable page.
- [Bulk upsert behaves differently on SQLite and PostgreSQL] → Keep a dialect-neutral batch merge contract and add production-PostgreSQL migration/integration coverage.
- [A factual provenance backfill may find few eligible dates] → Preserve explicit unreconstructable dates and keep research output insufficient instead of weakening cutoffs.
- [Environment fingerprints change after legitimate redeployments] → Version the attestation and retain previous fingerprints with deploy timestamps; never store raw credentials.
- [Dynamic validation windows can query more history] → Plan the minimum required date range, cap it, use indexed predicates, and aggregate one date at a time.
- [Existing active changes overlap] → Record task ownership explicitly and run strict validation across all three changes before implementation completion.

## Migration Plan

1. Reproduce the known WIP failures and freeze expected source-kind, window-planner, depth-lane, checkpoint, and readiness contracts in tests.
2. Reconcile the Alembic chain across owners: reuse existing provenance columns, add the validation source-manifest relation here, consume the separately owned membership fact migration, and retain one head with downgrade coverage; fix a stale expected-head test instead of adding an empty revision.
3. Fix source-kind persistence/API evidence grouping and run a dry factual audit of existing validation rows before any conditional backfill.
4. Add the exchange-session/source-date planner and bounded validation continuation; expose required/available diagnostics and never finalize a partial aggregate.
5. Deploy separate cursor namespaces and read-only depth metrics; do not start history mutation yet.
6. Deploy the bounded backfill continuation disabled by default, then validate one small dry-run and one committed page under resource limits.
7. Enable scheduled single-worker depth slices, keeping daily freshness and publication priority ahead of warm-up work.
8. Verify completion of the separately owned research replay integration and walk-forward tasks, still disabled from production evidence and live policy writes; do not reimplement them in this change.
9. Expose the authenticated readiness report and update rollout evidence only from an attested production instance.
10. Accumulate production snapshots/future outcomes prospectively; three distinct qualified sessions complete only the rollout acceptance gate, while formal return evidence still requires 20 completed non-overlapping paired dates and its real coverage/effect/drawdown gates.

Rollback disables the history-depth scheduler, replay adapter, and readiness UI while retaining additive nullable columns and immutable checkpoints. Daily freshness/publication keeps its previous fail-closed behavior. No rollback rewrites published snapshots, validation results, alerts, notifications, or research artifacts.

## Open Questions

- Confirm the retention target for the optional 180-session lane after measuring disk and provider cost; 61 sessions is the minimum score warm-up requirement.
