> **Lifecycle reconciliation:** Checked tasks below record the legacy implementation completed by this change. They MUST NOT be used to restore relative reductions, infer execution/cooldown from a proposed or sent recommendation, let notification cooldown decide action eligibility, or map `take_profit_watch` to trim/reduce. Current production remediation and acceptance are owned by `harden-etf-alert-action-lifecycle`; legacy behavior remains diagnostic/research-only until that change cuts over.

## 1. Evidence And Contracts

- [x] 1.1 Add exit risk validation contract fields to research evidence payloads using existing JSON fields where possible.
- [x] 1.2 Add tests that unavailable exit evidence returns waiting or insufficient status instead of fallback confidence.
- [x] 1.3 Extend exit hyperopt payloads with policy class, approval status, protection guard version, and baseline comparison metadata.
- [x] 1.4 Add API tests confirming candidate parameters remain research-only and are not marked approved automatically.

## 2. Path-Based Exit Validation

- [x] 2.1 Implement path replay for hold baseline, current default, candidate parameters, and guard-enabled policy.
- [x] 2.2 Add realistic ETF holding state inputs: entry price, high-water profit, activation state, giveback, holding age, and decision-eligible price source.
- [x] 2.3 Support TopN signal-run synthetic entries with explicit research-simulation metadata.
- [x] 2.4 Calculate validation metrics for return, max drawdown, false exit, missed upside, alert count, rolling stability, and data coverage.
- [x] 2.5 Add backend tests for policy comparison, missing path exclusion, rolling confidence, and no future-data leakage.

## 3. Tracked Position Exit State

- [x] 3.1 Audit existing tracked-position fields and decide whether high-water profit and threshold context can stay in JSON or require a migration.
- [x] 3.2 Persist ETF exit state updates from decision-eligible quotes or daily data only.
- [x] 3.3 Refactor trailing take-profit to activate only after profit-protection threshold is reached.
- [x] 3.4 Mark missing entry price, stale quote, or fallback price as data-ineligible for live email triggers.
- [x] 3.5 Add tests for high-water updates, activation distance, giveback triggering, and data-ineligible no-email states.

## 4. Protection Guards

- [x] 4.1 Add a notification cooldown guard that suppresses duplicate ETF holding emails and records the suppression reason without changing action eligibility, action status, or execution-origin reentry cooldown.
- [x] 4.2 Add repeated stop-loss guard that blocks add reminders or downgrades urgency after repeated loss events.
- [x] 4.3 Add portfolio drawdown guard for tracked ETF holdings using owner-scoped positions.
- [x] 4.4 Add low-profit ETF guard that can downgrade repeat low-quality ETF actions.
- [x] 4.5 Add tests for guard activation, guard expiration, owner scoping, and guard-only no-email behavior.

## 5. Signal Classification And Live Eligibility

- [x] 5.1 Introduce action classes such as `actionable_exit`, `soft_watch`, `guard_only`, `data_waiting`, and `research_only`.
- [x] 5.2 Change unconfirmed `trend_weakening` to guard-only by default.
- [x] 5.3 Allow confirmed trend weakening only when supported by loss, giveback, ranking deterioration, or market-regime confirmation.
- [x] 5.4 Ensure live tracked-position evaluation reads only default or approved parameters.
- [x] 5.5 Add regression tests that candidate parameters do not affect live alerts.

## 6. API And UI

- [x] 6.1 Extend tracked-position API responses with action class, guard state, threshold context, approval status, and no-alert reason.
- [x] 6.2 Update `/short-term` holding cards to separate observation labels, actionable holding signals, guard-only states, and research evidence.
- [x] 6.3 Update evidence page wording from generic credibility to policy validation, baseline comparison, and research-only candidate status.
- [x] 6.4 Add frontend tests or type checks for null, insufficient, guard-only, candidate, and approved states.

## 7. Operational Jobs And Validation

- [x] 7.1 Add admin/background job option for exit risk validation with `latest_opportunity_top` Top50 default and full eligible calibration mode.
- [x] 7.2 Make long-running validation batch-safe with batch size, progress summary, selected codes, exclusions, and failure recording.
- [ ] 7.3 Run Top50 validation and full eligible calibration after implementation and record the resulting run ids.
- [x] 7.4 Verify `uv run pytest` for related short research and tracked-position tests.
- [x] 7.5 Verify `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 7.6 Verify `uv run ruff check .`.
- [x] 7.7 Verify frontend type checking (`pnpm exec tsc --noEmit` is blocked by approve-builds; `./node_modules/.bin/tsc.cmd --noEmit` passed).

## 8. Alert Action Lifecycle Reconciliation

- [x] 8.1 Reconcile this change's proposal, design, and specs with immutable exposure baselines, absolute targets, owner-confirmed execution facts, notification/action separation, and `take_profit_watch=hold`.
- [ ] 8.2 Before archiving or promoting this change, complete the implementation remediation and cutover in `harden-etf-alert-action-lifecycle`; legacy path-replay or notification-cooldown tests cannot satisfy current lifecycle acceptance.
