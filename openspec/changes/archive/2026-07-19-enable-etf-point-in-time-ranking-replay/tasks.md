## 1. Freeze Replay And Evidence Contracts

- [x] 1.1 Add failing contract tests for the orthogonal ranking source, signal/action compatibility, policy mode, notification provenance, and execution provenance combinations, including rejection of `provider_delivered_live` without provider receipt facts.
- [x] 1.2 Define typed replay provenance enums/contracts and stable canonical serialization without changing production ranking or notification contracts.
- [x] 1.3 Add failing migration/model tests for nullable `EtfSignalValidationRun.ranking_source_kind` and `source_replay_run_key`, legacy-row compatibility, indexes, downgrade, and a single Alembic head.
- [x] 1.4 Implement the additive validation-run provenance migration, ORM, API schema, and typed response fields.
- [x] 1.5 Add contract tests proving production-published and research-replay evidence with shared future prices cannot merge sample counts, coverage, intervals, or evidence status.

## 2. Extract The Daily-Reconstructable Score

- [x] 2.1 Add failing pure-score tests that freeze `daily_reconstructable_v1`, `research_score`, trend/risk/liquidity weights 55/30/15, required finite inputs, and no fallback or weight renormalization.
- [x] 2.2 Add failing tests for the exact symmetric `abs(adjusted_close - adjusted_MA20) / adjusted_ATR20` definition, 21 adjusted OHLC points, and equal penalties above/below MA20.
- [x] 2.3 Add failing adjustment-scale invariance tests and explicit rejection tests for unproven/non-multiplicative adjustment provenance.
- [ ] 2.4 Extract the existing daily replay calculation into a small pure versioned scorer and keep current replay callers behavior-compatible.
- [x] 2.5 Add manifest/hash tests proving `daily_reconstructable_v1` can never compare as `final_score_v3` and cannot consume intraday, catalyst, validation, position, alert, or notification data.

## 3. Load Strict Point-In-Time Inputs

- [x] 3.1 Add failing loader tests for effective-dated membership, cutoff-bounded metadata/adjusted OHLCV, delisted members, unavailable historical membership, and current-survivor rejection.
- [x] 3.2 Implement a read-only Strategy Lab point-in-time universe and adjusted-input loader through the market-data/research boundary, with deterministic ordering and stable input hashes.
- [x] 3.3 Add stable exclusions for `insufficient_point_in_time_universe`, `unproven_adjustment_point_in_time`, raw/fallback provider data, stale/ineligible rows, and future-known inputs.
- [x] 3.4 Add tests proving loading only through T and loading a larger history with the API cutoff at T produce identical eligible inputs, features, and ranks.
- [ ] 3.5 Add a bounded factual-membership audit/backfill command that writes only externally verifiable intervals and reports all unreconstructable dates without inference.

## 4. Materialize Bounded Feature Artifacts

- [x] 4.1 Add failing Stage A tests for one-worker enforcement, source/output/time bounds at 55 seconds, canonical code/date order, atomic cursor checkpoints, and idempotent retry.
- [x] 4.2 Implement `daily_reconstructable_v1` feature rows and paged artifact identities using the existing Strategy Lab artifact/checkpoint store.
- [x] 4.3 Add failure tests for changed score manifest, input/universe hash, cutoff, schema, or candidate registry and require a new replay identity on mismatch.
- [x] 4.4 Add chunk-invariance tests comparing one-shot materialization with multiple code/date page sizes and interrupted resume.
- [x] 4.5 Add bounded continuation/job wiring with explicit row/item limits, normal partial progress state, peak-RSS reporting, and no unbounded full-history query.

## 5. Rank Complete Point-In-Time Cross-Sections

- [x] 5.1 Add failing Stage B tests that reject incomplete/duplicate/mixed-contract date manifests and never rank an individual code chunk.
- [x] 5.2 Implement complete-date manifests, score-eligible coverage, deterministic global rank, Top5/10/20 cohorts, and `all_scored` from one authoritative date universe.
- [x] 5.3 Persist immutable research ranking events/artifacts with replay/manifest/universe/input hashes and an atomic last-complete-date checkpoint.
- [x] 5.4 Add resume/chunk-invariance tests proving identical ranks, Top-N membership, hashes, events, and summaries across safe batch shapes.
- [x] 5.5 Report universe, adjusted-price, feature/component, score-eligible, and forward-outcome coverage with independent denominators and exclusions.

## 6. Validate Ranking Candidates Without Overfitting

- [x] 6.1 Add failing registry tests for no more than the three frozen ranking candidates and rejection of grids, dynamic threshold variants, or ranking-by-action Cartesian searches.
- [x] 6.2 Implement `daily_core_top10`, `daily_core_top10_hysteresis` (buffer rank 15, one replacement/session, three-session minimum hold), and `daily_core_top10_hysteresis_regime` using the existing versioned regime/liquidity gates.
- [x] 6.3 Add forward-outcome tests for T+1 adjusted-close entry, 1/3/5/10 full-session exits, 5-bps fee plus 5-bps slippage per side, missing entry/exit, and no signal-close substitution.
- [ ] 6.4 Extend score-bucket validation to accept immutable `research_replay` sources separately from exact `production_published` sources and remove every legacy-score fallback.
- [ ] 6.5 Implement the fixed Top10 five-day paired net excess primary endpoint, exploratory labels for other cells, non-overlapping dates, turnover/rank-churn/cost metrics, block-bootstrap interval, and drawdown gate.
- [ ] 6.6 Add chronological walk-forward, ten-session purge/embargo, frozen selection, sample sufficiency, and one-time final-holdout guards for the ranking stage.

## 7. Connect Policy Shadow Without Production Side Effects

- [ ] 7.1 Add failing integration tests that feed complete research rankings into the existing pure action lifecycle while snapshotting production ranking, allocation, tracking, risk-alert, action, notification, SMTP, and audit tables.
- [ ] 7.2 Implement a `policy_shadow` adapter that emits research action events, simulated fills, and `shadow_eligible` notification facts only inside replay artifacts.
- [ ] 7.3 Bind the frozen selected ranking contract to the existing action candidate registry without changing its Top20 ten-day action-cycle primary endpoint or T+1 adjusted-open execution model.
- [ ] 7.4 Report policy-shadow simulated benefit, live `smtp_accepted_live` sensitivity, provider-delivery facts, and user-confirmed execution outcomes as separate groups with independent sample gates.
- [ ] 7.5 Add tests proving notification attempts/repeats never become unique action samples or returns and that missing live samples remain `no_live_notification_sample`/`no_user_confirmed_execution`.

## 8. Expose Truthful Evidence And Readiness

- [ ] 8.1 Add API/service tests for explicit ranking-source/provenance fields, immutable replay identity, separate coverage dimensions, primary/exploratory labels, costs, uncertainty, turnover, churn, and limitations.
- [ ] 8.2 Replace context-free research N/A output with stable reasons for absent production snapshots, absent replay, incompatible manifest, PIT universe gaps, score coverage, independent dates, future windows, adjusted prices, live notifications, and confirmed executions.
- [ ] 8.3 Update workbench evidence types/presentation so production v3, daily research replay, policy shadow, SMTP facts, provider delivery, and confirmed execution cannot be visually merged.
- [ ] 8.4 Add frontend tests for waiting versus insufficient versus incompatible states and for research-only warning/primary endpoint presentation.

## 9. Real-Data Acceptance And Verification

- [ ] 9.1 Run bounded factual membership and adjusted-provider audits; record exact earliest eligible replay date and every unreconstructable date/source without using raw Sina/efinance data.
- [ ] 9.2 Run sequential replay continuations with one worker and 55-second bounds until the eligible date range completes or a reproducible data gate blocks progress; record peak RSS and resume evidence.
- [ ] 9.3 Generate Top5/10/20 1/3/5/10-day research results and policy-shadow Top20 ten-day action evidence only from eligible replay artifacts, keeping insufficient/live-unavailable states explicit.
- [ ] 9.4 Run relevant ranking, replay, action-policy, migration, API, no-side-effect, backend-domain-boundary, Ruff, type, and frontend tests in separate bounded commands.
- [ ] 9.5 Run `openspec validate enable-etf-point-in-time-ranking-replay --strict` and publish a final evidence summary with commands, timeouts, test counts, contract hashes, coverage, exclusions, costs, holdout state, peak RSS, and confirmation that no production policy changed automatically.
