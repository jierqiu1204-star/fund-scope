# Validation log

## Baseline (2026-09-05)

- Repository: `/Users/churchill/Projects/fund-scope/worktree-0811`
- Revision: `b73263f`
- Current research score contract: `daily_reconstructable_v1`
- Current research contract hash: `90967d4f39164c7468942a3c3f4f773351f8cbea8eb49da2cb0fa4b129b74a02`
- Frozen three-candidate registry hash: `5d6841922bbe995f57aa3e2af65ebfe22521bb6bf9844ba1244736ce4f976e14`
- `daily_core_top10`: `b2c5a5f45c5403d2b6d903f47be9492c42feaaf14c3c96f5a12f131737f8b26e`
- `daily_core_top10_hysteresis`: `fc93c699fce80d298af70a00de56c81287cc3aa77af0d0ee5c3549e671dad506`
- `daily_core_top10_hysteresis_regime`: `4b4cbc8d62283985f79f6c4e3e76cddb4a989a0fd1446b1c691b3514bb48c0c0`

The existing implementation already contains immutable source snapshots, Stage A/B ranking events, the frozen three-candidate evaluator, event-style forward-return calculation, paired endpoint/bootstrap/Holm helpers, holdout authorization primitives, bounded single-worker continuation, replay artifacts, checkpoints, and evidence JSON storage.

The following handlers were placeholders at baseline:

- `candidates` always supplied empty prior state and gate facts.
- `forward_outcomes` supplied only the signal date and no adjusted prices.
- `ranking_validation` and `factor_evidence` always emitted pending evidence.
- signal-label validation selected only the latest run, so a fresh run could starve older due horizons.

`backend/tests/test_etf_ranking_walk_forward.py` was an orphan test importing the absent `etf_ranking_walk_forward` module. Its useful chronology and leakage assertions belong with the existing factor/ranking validation implementation; the missing module is not a production interface to restore.

Baseline targeted tests, run with proxy variables cleared because the local environment advertises an unavailable SOCKS transport:

```text
ALL_PROXY= HTTPS_PROXY= HTTP_PROXY= NO_PROXY='*' .venv/bin/pytest \
  tests/test_etf_ranking_candidates.py \
  tests/test_etf_ranking_forward_outcomes.py \
  tests/test_etf_validation_session_planner.py \
  tests/test_etf_leader_tactics_historical_backtest.py -q
31 passed in 0.12s
```

The first `uv run` attempt could not initialize its cache outside the sandbox; a second attempt with `/tmp` cache tried to resolve `hatchling` over blocked network access. The existing `.venv` is therefore used for local validation without installing dependencies.

No production score, candidate registry, portfolio, position, risk, notification, archived change, or runtime configuration was modified while recording this baseline.

## Implementation evidence

### Source recovery and point-in-time loop

- Current `daily_reconstructable_v1` production research sources, legacy `final_score_v3` sources, and research replay sources are selected and grouped by their persisted source identity. Missing hashes remain legacy/unavailable, and fund runs cannot enter ETF cohorts.
- Label validation now pages compatible historical due cohorts instead of selecting only the newest run. Immature and mature-missing-price outcomes remain distinct and retryable; completed outcomes keep the existing `(signal_item_id, horizon_days)` identity.
- Candidate artifacts carry a stable cross-run state-chain identity, predecessor date, and predecessor state hash. A missing or incompatible predecessor fails closed. The gated candidate reads cutoff-valid persisted regime/liquidity facts and records exact exclusions when facts are absent.
- Forward outcomes now load exchange sessions and decision-eligible total-return-adjusted prices, freeze an outcome cutoff/input revision, and revisit newly mature work without changing the source or selection hash. The scheduled continuation uses the existing shared replay artifact store and bounded lease/checkpoint path.

The two-source integration fixture covers source capture/publication, carried candidate state, and mature event outcomes. It correctly yields zero completed continuous five-session samples because later daily targets are missing. A separate review fixture provides six consecutive daily target decisions, exercises the actual continuous calculation and ranking/factor handlers, persists the resulting evidence, and verifies that repeated handlers reuse the sealed calculation and create only one evidence row even when the clock advances. Existing integration checks also verify that production ranking, allocation, tracked-position, risk, and notification paths are not mutated.

Label-outcome revision boundary: the label rule remains `label_validation_v1`, but newly calculated rows declare `signal_adjusted_close_full_horizon_v1`, immutable adjusted-price receipt cutoffs, and the complete source identity. Older completed rows have no such identity and remain legacy; incompatible completed rows are preserved and excluded from current-contract summaries. The only caller is the normal label-validation runner, with no API to request a new execution contract or recalculate a completed label. Corrected prices can mature pending/missing rows, but they do not recalculate completed labels or create a second label revision. Separate outcome revisions are implemented for the ranking artifact/evidence path. Task 2.3 and the corresponding label scenario were narrowed to this actual supported boundary during review.

Review fixes (2026-09-05): source retention now applies after full contract and mixed-asset checks using bounded keyset pages, so newer malformed published runs cannot immediately hide older compatible sources. Unknown exchange-calendar years and missing next-year calendars produce explicit unavailable label horizons rather than jumping years or scanning to date overflow. Leader lifecycle confirmation, entry, and subsequent required sessions use exact calendar dates; a missing bar cannot become a later entry/exit. Production uses the verified exchange calendar (currently 2026); the sealed synthetic 2025 fixture explicitly supplies its original artificial session calendar and retains its engineering-only, zero-PIT-credit meaning. Historical years without a verified or explicitly sealed calendar remain unavailable.

Review regression: 80 tests passed in 5.92s across `test_etf_validation_session_planner.py`, `test_etf_leader_tactics_historical_backtest.py`, `test_etf_leader_historical_backtest_script.py`, `test_etf_leader_tactics_evidence_api.py`, domain-boundary tests, and six existing label/score-bucket API cases. The added tests cover a malformed-source page hiding a valid older cohort, unknown source years and year-end horizons, missing confirmation/entry/lifecycle exchange sessions, and default-calendar rejection of the artificial 2025 fixture. Targeted Ruff and `git diff --check` passed.

### Ranking comparison and statistical controls

- All three frozen candidates remain unchanged under registry hash `5d6841922bbe995f57aa3e2af65ebfe22521bb6bf9844ba1244736ce4f976e14`.
- The base continuous account charges 5 bp fee plus 5 bp slippage per executed side. The fixed stress diagnostic keeps the 5 bp fee and uses 10 bp slippage. Cash and shares carry across dates; valuation drift occurs before target rebalancing; retained quantities are not charged a fictitious round trip.
- The primary paired sample is extracted from the same continuous ledger from T+1 pre-rebalance equity through T+6 pre-rebalance equity. It does not liquidate at a window boundary or deduct event-style fees again.
- A frozen positive 20-session momentum Top10 control is reported only as a diagnostic. It uses code tie-breaking, 10% target weight per qualifying asset, and leaves unused slots in cash. It does not change the candidate registry or primary baseline.
- Existing paired endpoint, block bootstrap, Holm adjustment, promotion gates, and chronological split helpers are wired to calculated evidence. The useful purge/leakage assertions from the orphan walk-forward test were moved into existing ranking/factor tests; the orphan file was removed rather than restoring another engine.
- Holdout consumption has a fixed-key artifact receipt claimed before the expected holdout result is read. Reopening the artifact store returns the same receipt, while another evidence hash is rejected. The current insufficient fixture leaves the holdout unconsumed.

Production thresholds remain unchanged: 252 point-in-time sessions, 40 independent primary dates, 3 walk-forward folds with a 10-session purge/embargo, at least 95% decision-data coverage, and at least 90% score/warm-up coverage.

### Leader historical lifecycle

The corrected research lifecycle uses the actual T+2 adjusted open, the original T signal low, and ATR from completed sessions before entry. It freezes a new execution/risk/cost identity and charges 5 bp fee plus 5 bp slippage per side without changing live risk or email rules.

The sealed deterministic engineering fixture is documented in `docs/leader-historical-lifecycle-v2-fixture.md`. It is synthetic and grants zero PIT promotion credit. Its one closed breakout event changed as follows:

| Metric | Legacy zero-cost lifecycle | Corrected lifecycle |
| --- | ---: | ---: |
| Gross return | 0.210735% | 0.210735% |
| Net return | 0.210735% | 0.010514% |
| Peer net excess | 0.007936% | 0.007920% |

The repair candidate has zero events in this fixture. These numbers prove calculation and identity isolation only; they are not market-performance evidence.

### Strategy evidence status

No local production database or sealed real-market artifact was present in this worktree, so this change cannot produce a legitimate same-contract performance winner. The two-date fixture produces event diagnostics but no completed continuous paired evidence. The complete daily-target review fixture persists calculated candidate diagnostics and remains `insufficient_data`; no production holdout is consumed and no promotion is emitted.

The next strategy to iterate is `daily_core_top10_hysteresis`, as the predeclared primary comparison. It preserves the current research score and adds only a minimum holding period, rank-15 buffer, and one-replacement-per-day limit, directly targeting turnover and cost drag with the fewest new factual dependencies. This is an iteration priority, not a claim that it already outperforms. Keep `daily_core_top10` as the frozen production/research baseline. Evaluate `daily_core_top10_hysteresis_regime` after regime/liquidity fact coverage is adequate; keep pure momentum diagnostic-only.

A strategy can be evaluated only after explicitly preregistering development/validation/holdout dates, fold length and declared regimes, then collecting at least 252 compatible PIT sessions, 40 non-overlapping five-session primary dates, 3 valid chronological folds, the required coverage, complete common-support adjusted prices, and an authorized single holdout read. Waiting for more data alone does not create a frozen split or manual authorization. Leader tactics additionally require sealed historical membership/classification and real adjusted-price inputs.

### Ranking review fixes and operator inputs (2026-09-05)

Continuous samples now include actual later daily targets through their five-session window. Missing target dates stop the account at the last observable pre-trade boundary; missing candidate pages fail closed rather than shrinking the cohort denominator. Capital drawdown comes from the complete continuous ledger, and an incomplete capital curve has no drawdown value. Momentum selections read adjusted-price revisions visible at each original source cutoff. Ranking results, samples and account summaries are sealed once; factor evidence consumes that seal rather than recalculating at a later time.

The production protocol uses existing SQLite research artifacts. An operator supplies a payload from `freeze_production_ranking_validation_plan` under `_ranking_protocol_key(manifest)`, phase `validation_plan`, item `frozen-plan`. This narrow payload contains explicit `ChronologicalSplit`, fold length, declared regimes and registration time. It is immutable and code/contract-bound. No plan is invented from observed returns: absence produces `frozen_chronological_split_missing`. Plan bounds apply before reading daily source artifacts, and automatic non-holdout calculations exclude holdout dates. Actual fold tests use complete holding windows, ten-session purge and ten-session embargo; fold/regime signs, outcome coverage, the Holm adjustment and a conservative multiplicity-adjusted interval feed the existing promotion gates.

Holdout input is operator-owned evidence, not an unauthenticated API. Only after all non-holdout gates pass does the workflow read phase `holdout_authorization`, item `frozen-approval`. Its `authorization` is the existing `FrozenHoldoutAuthorization` shape produced by `freeze_production_holdout_authorization`, binding plan, code, candidates, gates and frozen non-holdout evidence; it also names the exact expected result hash. The workflow atomically claims a receipt before reading phase `holdout_result`, item `frozen-result`. The receipt scope uses the ETF PIT data owner and physical holdout date window, so changing a daily source, code version or training range cannot create another ticket for that same window. The result must carry the complete endpoint, frozen candidate/cost/execution identities, consistent coverage and distinct non-overlapping sample identities, mature cutoff after the holdout close, and finite capital drawdowns; a `passed` boolean alone is rejected. The server operator's ability to write these artifacts is the trust boundary; a caller-supplied historical registration timestamp is not independent proof of preregistration.

No production plan, approval or holdout result was created in this implementation. Later approval does not overwrite a previously sealed `not_consumed` factor row. A subsequent daily source has a new evidence manifest; the narrow authorized-read function can also return the immutable receipt/result directly. Existing receipts and evidence remain immutable across retries and deployment. Review validation covered 57 ranking/factor/production-capture/domain tests before the final additional import guards; the final combined check is recorded by the deployment review.

### Validation commands (2026-09-05)

The existing virtual environment was used because `uv run` could not initialize its external cache or resolve `hatchling` under the sandbox. No dependency was installed.

```text
ALL_PROXY= HTTPS_PROXY= HTTP_PROXY= NO_PROXY='*' .venv/bin/pytest -q \
  tests/test_etf_evidence_overview_api.py \
  tests/test_etf_factor_experiment_contract.py \
  tests/test_etf_factor_validation.py \
  tests/test_etf_leader_historical_backtest_script.py \
  tests/test_etf_leader_tactics_evidence_api.py \
  tests/test_etf_leader_tactics_historical_backtest.py \
  tests/test_etf_production_pit_capture.py \
  tests/test_etf_ranking_candidates.py \
  tests/test_etf_ranking_forward_outcomes.py \
  tests/test_etf_ranking_validation_endpoints.py \
  tests/test_etf_ranking_validation_sources.py \
  tests/test_etf_validation_session_planner.py \
  tests/test_backend_domain_boundaries.py \
  tests/test_short_research_api.py::test_short_research_entry_timing_labels_are_explained \
  tests/test_short_research_api.py::test_etf_signal_validation_run_records_forward_outcomes \
  tests/test_short_research_api.py::test_etf_signal_validation_marks_insufficient_samples \
  tests/test_short_research_api.py::test_etf_label_historical_replay_uses_only_past_data \
  tests/test_short_research_api.py::test_etf_label_historical_replay_defaults_to_all_eligible_etfs \
  tests/test_short_research_api.py::test_etf_label_historical_replay_api_separates_tracks_and_does_not_notify \
  tests/test_short_research_api.py::test_score_bucket_validation_requires_current_full_ranking_contract \
  tests/test_short_research_api.py::test_score_bucket_zero_sources_is_unavailable_and_unregistered \
  tests/test_short_research_api.py::test_score_bucket_validation_skips_overlapping_signal_windows \
  tests/test_short_research_api.py::test_score_bucket_validation_uses_historical_source_membership_after_etf_deactivation
138 passed in 10.91s

ALL_PROXY= HTTPS_PROXY= HTTP_PROXY= NO_PROXY='*' .venv/bin/ruff check .
All checks passed!

./node_modules/.bin/prettier --check app/short-term/evidence/page.tsx
All matched files use Prettier code style!

./node_modules/.bin/tsc --noEmit
passed

git diff --check
passed

node /Users/churchill/.npm/_npx/20f2b75ddc8bce88/node_modules/@fission-ai/openspec/bin/openspec.js \
  validate repair-strategy-evidence-and-ranking-comparison --strict
Change 'repair-strategy-evidence-and-ranking-comparison' is valid
```

### Run and rollback notes

Deployment and a production batch continuation are outside this implementation and were not executed. After deployment, use a new code version so old placeholder-complete checkpoints resolve to a new immutable manifest. The existing scheduler will continue bounded oldest-due work through the shared `production-pit-research.sqlite3` artifact store. Inspect `/api/short-research/evidence/etf/latest` for the overview and `/api/strategy-lab/etf-factor-evidence/{manifest_hash}` for the stored comparison evidence.

There is no schema migration. To roll back, stop new continuations and restore the previous application revision; retain the database and replay artifacts because source, candidate, outcome revision, evidence, and holdout receipts are immutable evidence. Do not delete or overwrite the new evidence when rolling back code.

### Scope audit

The final diff adds no dependency, database table, migration, API route, service, parameter grid, or retired seven-strategy engine. It does not change the production score formula, frozen candidate hashes, allocation weights, tracked positions, risk thresholds, notification behavior, or archived changes. New metadata is stored in existing JSON/artifact/evidence fields and the existing evidence page renders the three candidate diagnostics without changing existing field types.

## Independent review and release preparation (2026-09-05)

The initial 138-test implementation pass was not sufficient to establish correctness. Independent review reproduced and fixed missing future daily targets, skipped history gaps, incorrect event-series risk input, selection prices read at the wrong cutoff, mutable evidence retry payloads, disabled capture continuing old work, and cross-phase scheduler starvation. Candidate state now checks the exact prior exchange session and its chain identity. Work artifact keys include the code/execution work identity so a new deployment cannot collide with an old payload. The final summary projects the sealed ranking/factor counts and exposes ranking diagnostics separately from incomplete policy-shadow evidence.

The review also fixed qualified historical-source pagination, unsupported calendar years, exact leader confirmation/entry/exit dates, and candidate diagnostics overwriting existing exploratory metrics. The detailed checks live in the existing relevant tests plus `test_etf_pit_continuation_review.py` and `test_etf_ranking_evidence_review.py`.

Final combined backend regression: **234 passed in 15.50s**, bounded by a 150-second subprocess timeout. It includes the original change's relevant test modules, the two review modules, factor-evidence persistence, research-loop leases/restarts, PIT decision data, price cutoffs, ranking Stage A/B, runtime bounds, domain boundaries, and the ten listed short-research API cases. No production data, holdings, or notifications were used by these checks.

The user explicitly authorized push and deployment after review. GitHub branch `codex-post-close-watchlist` was verified at baseline `b73263f1fb7d5b85e8f78c43fedd6b7f0477050c`, matching the most recent successful deployed commit (run `33942980554`). Release uses a fast-forward push of this branch and the existing `Deploy` workflow through `workflow_dispatch`. The active repository configuration is `docker-compose.ip.yml`, runner `fundscope-vps`; the documented public health endpoint is `http://110.42.222.9/api/health`. Final deployment SHA, workflow result, and post-deploy health are reported with the release rather than assumed from this preparation record.
