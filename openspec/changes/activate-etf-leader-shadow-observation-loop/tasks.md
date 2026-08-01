## 1. Freeze Observation Contracts

- [x] 1.1 Add leader observation report, progress, current-match, pending-outcome, and promotion-gate schemas with stable version and reason constants.
- [x] 1.2 Define deterministic observation and maturity manifest identities bound to PIT source, proxy registries, cutoffs, cost policy, and code version.
- [x] 1.3 Add contract tests proving fewer than 252 sessions permits accumulation but never promotion eligibility.

## 2. Build Bounded Observation Materialization

- [x] 2.1 Add a source-bound loader for complete `EtfPitCaptureSource`, its published signal items, factual taxonomy/sector/baseline/regime fields, and adjusted PIT history.
- [x] 2.2 Persist at most 20 compact per-ETF primitive or exclusion artifacts per continuation page without scoring a partial peer cross-section.
- [x] 2.3 Finalize a sealed same-session cross-section into the three frozen proxy observations with clone policy, finite checks, ranks, hashes, and zero-match support.
- [x] 2.4 Append the immutable session observation through the existing factor-evidence model and make duplicate resumes idempotent.
- [x] 2.5 Add interruption, batch-size consistency, source-conflict, missing-taxonomy, late-fact, finite-value, and memory-bound tests.

## 3. Mature Outcomes Independently

- [x] 3.1 Add due detection for pending five-session, ten-session, and MA5 policy-shadow outcomes without blocking newer observations.
- [x] 3.2 Materialize only decision-eligible adjusted entry and exit facts using the frozen execution and cost contracts.
- [x] 3.3 Append a maturity identity bound to the original observation and deduplicate the latest compatible state per signal date.
- [x] 3.4 Add tests for future-window pending, later maturation, missing prices, no holdout consumption, and no fabricated intraday fills.

## 4. Compose Production-Safe Workflow

- [x] 4.1 Replace the 252-session collection preflight with first-source collection eligibility while retaining the 252-session promotion gate.
- [x] 4.2 Build the production leader manifest and handler registry from the oldest compatible due PIT source and existing artifact store.
- [x] 4.3 Add one-page workflow preflight, lease, checkpoint, oldest-backlog ordering, and already-complete skip behavior.
- [x] 4.4 Add a separately gated scheduler/admin continuation that performs no live provider work and returns below 55 seconds.
- [x] 4.5 Prove the workflow cannot mutate production ranking, allocation, tracked positions, alerts, notifications, SMTP, score weights, or holdout state.

## 5. Expose Partial Evidence

- [x] 5.1 Extend the leader evidence projection with observation counts, completed sessions, latest complete matches, pending and matured outcomes, gate progress, and partial checkpoint state.
- [x] 5.2 Preserve compatibility with disabled, not-materialized, prior final-report, zero-match, and incompatible-manifest evidence.
- [x] 5.3 Extend the API schema and TypeScript contract with exact research-only observation fields and stable unavailable reasons.
- [x] 5.4 Add API tests proving early observations return `insufficient_data`, no return metric is fabricated, and production provenance remains absent.

## 6. Render The Research Observation Surface

- [x] 6.1 Add `当日 Shadow 观察`, zero-match, partial-progress, pending-outcome, and promotion-gate sections to the existing leader evidence panel.
- [x] 6.2 Label every current match as unvalidated research and keep formal ranking, email, delivery, and execution visually unavailable.
- [x] 6.3 Add frontend static-contract checks for observation rendering and the prohibition on buy-signal or validated-return language.

## 7. Verify The Change

- [x] 7.1 Run focused leader contract, PIT adapter, continuation, observation, outcome, workflow, evidence API, and migration-free persistence tests with per-command timeouts below 60 seconds.
- [x] 7.2 Run frontend TypeScript and leader-tactics static checks with bounded timeouts.
- [x] 7.3 Run backend domain-boundary tests and Ruff on changed files in bounded groups.
- [ ] 7.4 Run strict OpenSpec validation and verify all observation work remains research-only and production-isolated.
