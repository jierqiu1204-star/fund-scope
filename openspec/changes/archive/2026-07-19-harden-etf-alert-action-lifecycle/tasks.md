## 1. Freeze Scope And Reproduce Current Failures

- [x] 1.1 Record the implementation success criteria in the change notes: no repeated relative reduction, `take_profit_watch=hold`, no-data produces no action, notification retries never mutate actions, and backtest trades come only from unique action cycles.
- [x] 1.2 Reconcile this change with `upgrade-etf-exit-with-reentry-sizing` and `upgrade-etf-exit-risk-system`; amend their conflicting specs/tasks so executed-fill cooldown, absolute targets, and notification/action separation cannot later be overwritten, then run strict validation for all affected changes.
- [x] 1.3 Add a failing unit regression that proves three repeated 50% evaluations currently compound to 12.5% instead of retaining one 50% exposure-baseline target.
- [x] 1.4 Add a failing unit regression that proves `take_profit_watch` currently produces trim/reduce semantics instead of `hold/仅观察`.
- [x] 1.5 Add a failing service regression that proves an email proposal currently writes `latest_position_action` and can start re-entry cooldown without an execution fact.
- [x] 1.6 Add a failing regression that proves missing ranking/price/provider evidence can currently become `exit_watch` or another actionable outcome instead of a frozen data state.
- [x] 1.7 Add a failing backtest regression that proves repeated notification dates can currently cause repeated trades for one persistent signal.
- [x] 1.8 Capture a versioned diagnostic baseline fixture for the old semantics; label it legacy/research-only and ensure no test treats it as a promotable policy.

## 2. Define Pure Lifecycle And Absolute-Target Contracts

- [x] 2.1 Add contract tests for evaluation data states, alert states/events, action statuses, execution provenance, target stages, notification item/envelope payloads, and stable serialization/hash behavior.
- [x] 2.2 Implement the minimal enums/dataclasses/constants in the existing risk/tracking contract modules without adding database or notification dependencies to `risk_alerts`.
- [x] 2.3 Add table-driven tests for every state transition: normal/pending/firing/recovering/resolved, pending cancellation, recovering relapse, hard-stop bypass, and new alert episode after resolution.
- [x] 2.4 Implement the pure deterministic alert transition function, including stable alert-episode creation and explicit from/to state output.
- [x] 2.5 Add tests proving `data_waiting`, `no_data`, and `error` are mutually exclusive, have stable reason codes, freeze business state/counters, and cannot emit actions or stale-price repeats.
- [x] 2.6 Implement fail-closed data-state handling and remove any mapping from `数据不足`/missing context to `exit_watch` or another sell/reduce action.
- [x] 2.7 Add tests for `position_episode_id`, immutable exposure baseline, exposure-version increment on net add, unchanged version on reduction, new position episode after full close/re-entry, and corporate-action normalization.
- [x] 2.8 Implement position/exposure lifecycle helpers and server-owned baseline fields, including baseline source and adjustment factor.
- [x] 2.9 Add tests for absolute sell-only target calculation: 50% remains 50%, current quantity below target never causes a buy, and normalized quantity respects the frozen baseline.
- [x] 2.10 Implement absolute `target_remaining_fraction`, normalized target quantity/weight, target-stage generation, and `target_already_satisfied` behavior; remove relative `current_weight * target_fraction` semantics from the v2 path.
- [x] 2.11 Add tests that simultaneous rules aggregate to one strictest target, same-target rules only add reasons, and 50%/0% concurrent candidates leave only 0% current.
- [x] 2.12 Implement the position-level action-cycle aggregator, current-target comparison, deterministic reason ordering, and atomic weaker-target supersession intent.
- [x] 2.13 Add rule-mapping tests for hard stop 0%, eligible exit-watch versioned 0%/50%, trailing/confirmed weakening 50%, trend weakening no-add, take-profit watch hold, and ineligible evidence no action.
- [x] 2.14 Update `risk_alerts` rule mapping to satisfy the frozen absolute-target tests without changing comprehensive-ranking inputs or thresholds.

## 3. Add Persistence, Constraints, And Safe Migration

- [x] 3.1 Add schema tests for `exit_state_version`, action decision fields/statuses, execution events, alert/action correlation fields, notification item/envelope identity, and owner/position foreign keys.
- [x] 3.2 Add constraint tests for unique action-cycle target stages, one current action slot, execution-request idempotency, notification item identity, and envelope identity.
- [x] 3.3 Create the additive Alembic migration and SQLAlchemy models/columns/indexes; do not drop or reinterpret legacy alert/action fields during expand.
- [x] 3.4 Add tests that same-stage concurrent inserts resolve to one action and stricter-stage serialization supersedes the weaker current action without leaving two current slots.
- [x] 3.5 Implement repository operations using a position row lock and `exit_state_version` CAS, treating unique conflicts as idempotent existing results.
- [x] 3.6 Add migration tests for active positions with confirmed shares, estimated shares, missing shares, legacy `latest_position_action`, current firing-looking data, and already-closed status.
- [x] 3.7 Implement a bounded backfill command that processes one explicit position-id batch, creates position/exposure ids and baseline/high-water state only from decision-eligible adjusted history, and marks ambiguous records `needs_user_confirmation/data_waiting`.
- [x] 3.8 Ensure backfill records `legacy_unverified` only as execution provenance, never as an action status or inferred execution/cooldown.
- [x] 3.9 Add upgrade, compatibility-read, and safe rollback tests on a database copy; rollback must disable v2 writes/read priority without deleting v2 audit/action data or reviving relative-action writes.
- [x] 3.10 Add query indexes and bounded repository reads for current action, action history, alert audit cursor pages, repeat-slot items, and pending envelopes; verify query plans on representative data.

## 4. Orchestrate Idempotent Evaluation And Action Cycles

- [x] 4.1 Add workflow integration tests for one transaction performing state CAS, alert transition, action-cycle aggregation, action persistence, audit creation, and notification-item creation.
- [x] 4.2 Implement the cross-domain lifecycle orchestration in `app.services.workflows`, keeping rule evaluation in `risk_alerts`, state/action ownership in `tracked_positions`, and sending in `notifier`.
- [x] 4.3 Add integration tests that unchanged polls write no per-poll suppressed rows and at most one deterministic audit event per sealed snapshot/repeat slot.
- [x] 4.4 Implement sealed-snapshot/slot audit aggregation with event/schema ids, from/to state, actor/request/causation ids, occurred/recorded times, and bounded structured context.
- [x] 4.5 Add tests that valid resolution expires the unfulfilled remainder, a stricter target supersedes the weaker action, net add/policy retirement supersedes old exposure/policy actions, and all prior fills remain auditable.
- [x] 4.6 Implement action terminal-transition rules, `valid_until`/reason handling, `superseded_by_action_id`, and action-cycle closure/reopening.
- [x] 4.7 Add tests that a hard-stop action is created once, may have later fresh-data reminders, and is not recreated by a different contributing rule or by SMTP retry.
- [x] 4.8 Remove the legacy path that writes an email proposal into `latest_position_action`; make v2 re-entry reads use only owner-confirmed execution facts.
- [x] 4.9 Add tests that policy-version cutover cannot generate a duplicate already-satisfied target and that unchanged evidence initializes safely through pending/shadow rules.
- [x] 4.10 Run and fix the backend domain-boundary test so workflow orchestration does not introduce forbidden notifier/market/research imports.
- [x] 4.11 Inventory every existing write to `confirmed_shares`, `estimated_shares`, `buy_amount`, and position `status` (PATCH, share confirmation, close/reopen, import, recalculation, scripts, action execution); add tests and route each through the shared exposure-mutation CAS/audit helper so no direct write bypass remains.

## 5. Implement Owner-Confirmed Partial And Full Execution

- [x] 5.1 Add request/response tests for `POST /api/tracked-positions/{position_id}/actions/{action_id}/transitions`, covering acknowledge, partial execute, complete execute, cancel, invalid transition, and terminal retry.
- [x] 5.2 Define the narrow transition schema with `Idempotency-Key`, `expected_position_state_version`, and server-owned owner/episode/policy/target fields; execute payloads require finite time/quantity/price/source/fees and resulting shares or close fact with explicit numeric/clock/lot tolerances.
- [x] 5.3 Add authorization/security tests for cross-owner ids, forged targets/episode/policy, stale CAS, action not in current exposure, superseded/expired action, idempotency-key reuse with a different payload, non-finite/negative values, pre-decision/future time, oversell, inconsistent resulting shares, and inconsistent 0%-target close facts.
- [x] 5.4 Implement the owner-scoped route and service validation, returning the prior response for an identical retry and conflict for stale/different requests.
- [x] 5.5 Add transaction tests proving an execution event, cumulative fill, action status, confirmed/estimated shares or close status, exit-state version, and audit event commit atomically.
- [x] 5.6 Implement server-verified before-minus-fills accounting, partial-execution accumulation, versioned target/quantity tolerance, and cancellation semantics; same/weaker targets stay declined in the current firing cycle while a stricter escalation or later post-resolution cycle may create a new action.
- [x] 5.7 Add tests that the first real partial/full fill starts re-entry cooldown, while proposal, acknowledgement, SMTP acceptance, simulated fill, resolution, and legacy context do not.
- [x] 5.8 Expose current/history action projections through existing tracked-position responses with bounded fields; do not return internal unique keys or unredacted evidence unnecessarily.

## 6. Separate Notification Items, Envelopes, And SMTP Attempts

- [x] 6.1 Add tests that each transition/repeat has one item key, multiple ordinary items can share one owner/session/route/severity/channel/snapshot envelope, and any late item after sealing/first SMTP attempt rolls to a new immutable digest revision.
- [x] 6.2 Implement notification item persistence, deterministic digest-envelope assembly, stable digest revisions, immutable sealed item/template/render/Message-ID content, late-item rollover, and a single-item urgent hard-stop route.
- [x] 6.3 Add tests for ordinary same-slot suppression, trading-calendar repeat slots, hard-stop digest bypass, higher-severity inhibition, user silence, and continued evaluation/audit during suppression.
- [x] 6.4 Implement notification grouping/repeat/inhibition as workflow policy only; it must neither evaluate risk rules nor change action eligibility/status.
- [x] 6.5 Add tests that invalid current data suppresses action-oriented repeats, preserves the historical proposed action, and emits only the allowed “当前无法复核” item.
- [x] 6.6 Add notifier tests for atomic envelope CAS claim/lease, competing workers, lease expiry/reclaim, fixed Message-ID, `smtp_accepted/failed/unknown`, redacted errors, same-envelope retry, and SMTP-accepted-before-local-commit ambiguity under at-least-once delivery.
- [x] 6.7 Update `notifier` and delivery audit to claim envelopes before send, record SMTP acceptance rather than unverified delivery, and reuse envelope identity without ever creating actions.
- [x] 6.8 Update email templates: action mail shows cutoff, data source/freshness, policy/evidence hash, absolute exposure-baseline target, action id/status, and “未自动执行”; take-profit watch says `hold/仅观察/未生成减仓动作`.
- [x] 6.9 Add template fail-closed tests for missing action id, policy version, eligible data time, evidence hash, or target semantics.

## 7. Expose Safe Audit And Action UX

- [x] 7.1 Extend tracked-position/action/audit API schemas with separate alert state, data state, action status/progress, execution provenance, item/envelope SMTP status, and correlation ids.
- [x] 7.2 Add cursor-pagination, maximum-page-size, field-whitelist, redaction, and per-event context-size tests for the audit API.
- [x] 7.3 Implement bounded audit/action reads and privacy-retention behavior; audit events are immutable during retention but may be deleted/anonymized by existing owner/privacy deletion policy.
- [x] 7.4 Update frontend types and the short-term holding panel to distinguish `仅观察`, `等待数据`, `建议待确认`, `部分执行`, `已执行`, `已过期`, `已取消`, and `已被更严格建议替代`.
- [x] 7.5 Add owner UI controls for acknowledge/execute/cancel that display the server target, require current execution facts, send the current state version/idempotency key, and refresh on conflict.
- [x] 7.6 Update alert history UI to show one action-cycle path with multiple alert reasons and notification attempts rather than multiple sell decisions; keep technical ids/context expandable.
- [x] 7.7 Update delivery labels from ambiguous “已送达” to evidence-backed `SMTP 已接受`, `发送失败`, or `状态未知`, and explain external duplicates are possible but do not repeat actions.
- [x] 7.8 Run focused frontend tests/typecheck under a hard timeout and fix only regressions caused by the new fields/controls.

## 8. Correct Backtest Event And Execution Semantics

- [x] 8.1 Add backtest tests that the v2 engine consumes unique action cycles/absolute targets, ignores notification repeats/retries/recovery/soft watches, and cannot accept relative actions.
- [x] 8.2 Implement a shared live/backtest lifecycle adapter; keep `legacy_current_semantics` in an isolated diagnostic adapter permanently ineligible for promotion.
- [x] 8.3 Add execution tests fixing the base fill to T+1 `adjusted_open`, recording raw open/adjustment factor/normalized price, and proving T+1 high/low/close cannot influence fill selection.
- [x] 8.4 Implement pending/deferred fills for suspension, zero volume, non-demonstrably tradable limit lock, delisting, or missing open; never fill at stale T close and start outcome horizons at actual fill.
- [x] 8.5 Add point-in-time universe and cutoff-truncation tests proving current ETF membership/future rows cannot alter historical eligibility, ranking, alert state, or action at T.
- [x] 8.6 Add action-benefit tests using same fill time/start asset, cash-held event counterfactual, strategy-defined portfolio cash reallocation, incurred tax/fee costs, symmetric H-day mark-to-market without hypothetical terminal liquidation fees, and one sample per complete action-cycle path even after 50%→0% escalation.
- [x] 8.7 Implement 1/3/5/10-trading-day action benefit, positive-benefit accuracy, mean/median, avoided loss, missed upside, signal-to-fill delay, turnover, fees, and sample counts.
- [x] 8.8 Add separate scenario tests and labels for `动作建议完全执行情景` and `仅 SMTP 已接受邮件被执行敏感性`; neither may be presented as observed user execution.
- [x] 8.9 Update Strategy Lab result schemas/UI to show action, notification, execution-model, coverage, and daily-versus-intraday limitations separately.

## 9. Build Two-Stage Bounded Replay For 2-Core 4-GB Hosts

- [x] 9.1 Add feature-stage tests for code/date chunking with complete indicator warm-up, stable keys, adjusted-data provenance, and reuse across all candidates.
- [x] 9.2 Implement stage A to compute/write only bounded point-in-time feature batches; default one worker and expose explicit `max_items/max_seconds<=55` limits.
- [x] 9.3 Add replay-stage tests proving it waits for the complete historical cross-section, ranks Top-N once per date, and shares cash/positions/high-water/episodes/actions/pending fills across all ETFs.
- [x] 9.4 Implement stage B as date-ordered portfolio replay over full daily cross-sections; never rank or allocate independently inside a code chunk.
- [x] 9.5 Add checkpoint tests for contract/input-snapshot/code/schema hashes, warm-up boundary, candidate portfolio state, last completed unit, atomic-completion marker, and incompatible-resume rejection.
- [x] 9.6 Implement atomic checkpoints and idempotent merge/resume; revised data at the same cutoff must require a new run identity.
- [x] 9.7 Add chunk-invariance tests comparing multiple chunk sizes, interrupted/resumed execution, and one bounded small-sample run event by event within declared numeric tolerance.
- [x] 9.8 Add a bounded outer continuation command that performs exactly one feature/replay batch per invocation and exits with progress/checkpoint metadata; it MUST NOT hide an unbounded internal full-history loop.
- [x] 9.9 Profile a representative batch on a 2-core/4-GB-equivalent limit, keep default peak RSS at or below 2.5 GiB and worker count at 1, and record rows/sec, memory, query count, and checkpoint size.

## 10. Enforce Anti-Overfitting Validation Contracts

- [x] 10.1 Add candidate-registry tests allowing at most the three pre-registered candidates and rejecting grids, generated thresholds, extra candidates, or promotion of the legacy baseline.
- [x] 10.2 Implement the frozen candidate registry and persist all parameter/contract hashes before any outcomes are calculated.
- [x] 10.3 Freeze candidate 3 inputs before validation: ATR `k`, warm-up, adjusted-high versus adjusted-close peak, trigger field, recovery hysteresis, OHLC ordering, missing-data behavior, and monotonic-stop invariant.
- [x] 10.4 Add walk-forward tests that purge any action whose longest 10-day outcome crosses a train/validation/holdout boundary.
- [x] 10.5 Add inference tests that cluster/block-bootstrap by signal trading day, freeze seed/resample count/block length, and do not treat same-day correlated ETF actions as independent samples.
- [x] 10.6 Implement the fixed primary endpoint (Top20, 10-trading-day tax/fee-adjusted mean action-cycle benefit), pre-registered maximum-drawdown non-inferiority gate, and candidate-3-versus-candidate-2 lower-bound/minimum-practical-benefit selection rule; label Top3/5/10/20 and 1/3/5-day slices secondary.
- [x] 10.7 Add holdout-guard tests that persist first-consumed time/input hash and reject changed candidates or repeated “fresh” holdout runs for the same policy/run contract.
- [x] 10.8 Implement evidence dimensions separately: contract compatibility, data eligibility, execution provenance, sample sufficiency, and promotion status.
- [x] 10.9 Add report tests that show sample gates, confidence intervals, validation degradation, opportunity cost, and secondary-slice labels, and never auto-promote a candidate.

## 11. Shadow, Cut Over, And Roll Back Safely

- [x] 11.1 Run the additive migration and bounded backfill on a disposable production-shaped database copy; record counts for migrated, confirmed-baseline, estimated-baseline, data-waiting, legacy-unverified, and rejected rows.
- [x] 11.2 Add a shadow mode that computes/stores isolated v2 evidence without sending email, mutating real action state, changing holdings, or reading v2 shadow as production.
- [ ] 11.3 Observe at least three distinct real trading sessions with decision-eligible data and compare legacy versus v2 alert transitions, duplicate actions, target stages, stale-data blocks, notification items, and audit completeness; old/simulated sessions cannot substitute.
- [x] 11.4 Define and verify cutover gates: zero repeated same-stage actions, zero action from ineligible data, zero SMTP-to-executed transitions, deterministic concurrent strictest target, bounded error rate, and explainable legacy differences.
- [ ] 11.5 Enable v2 reads/writes in stages, disable legacy relative-action writes and `latest_position_action` cooldown reads, and verify no dual-written action or duplicate envelope occurs.
- [x] 11.6 Exercise rollback to safe display-only/no-new-action mode while preserving v2 evidence; verify rollback does not reactivate relative compounding or email-as-execution semantics.
- [x] 11.7 Keep candidate validation and production policy activation separate; even a passing final holdout produces only a reviewable version proposal, never an automatic live switch.

## 12. Bounded Verification And Handoff

- [x] 12.1 Run lifecycle, rule-mapping, repository/CAS, migration, API-auth, notifier, audit, and tracked-position focused tests in separate commands, each with an external hard timeout no greater than 60 seconds; stop and split any suite that reaches the bound.
- [x] 12.2 Run backtest execution, point-in-time universe, truncation, two-stage batching, resume, candidate registry, purge, inference, and holdout-guard tests in separate <=60-second commands.
- [x] 12.3 Run `uv run pytest tests/test_backend_domain_boundaries.py` (or the repository-equivalent path) with a <=60-second hard timeout and fix only dependency violations introduced by this change.
- [x] 12.4 Run targeted Ruff checks on touched backend/migration/test files in bounded groups with a <=60-second hard timeout; do not retry a hung command without first splitting/diagnosing it.
- [x] 12.5 Run frontend typecheck/lint/tests for touched short-term and Strategy Lab files in bounded commands with a <=60-second hard timeout.
- [x] 12.6 Run `openspec validate harden-etf-alert-action-lifecycle --strict` and strict validation for reconciled overlapping changes.
- [ ] 12.7 Publish a final evidence summary containing exact commands/timeouts, test counts, migration/backfill counts, peak RSS, shadow-session dates, action/notification invariant results, unresolved data gaps, and explicit confirmation that ranking weights/production thresholds were not auto-changed.
