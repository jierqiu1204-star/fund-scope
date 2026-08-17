## 1. Contracts and persistence

- [x] 1.1 Add pure tests for finite capital validation, tracked sleeve NAV, real peak-to-trough drawdown, valuation/execution coverage, and immutable/idempotent evidence identity.
- [x] 1.2 Add user capital confirmation, explicit full opening reconciliation, append-only ETF sleeve ledger/daily snapshot models plus one bounded Alembic migration without fake backfill.
- [x] 1.3 Implement pure `normal/reduce_only/data_halt` transitions with distinct-session recovery, cooldown, release threshold, and stable reason codes.
- [x] 1.4 Replace raw alert-row counting with distinct eligible signal/action cycles and owner-confirmed execution evidence.

## 2. Owner-scoped integration

- [x] 2.1 Build one owner-scoped risk context from batched positions, executions, alerts and latest NAV/state evidence.
- [x] 2.2 Require explicitly confirmed finite ETF capital for amount/share sizing and remove the 10,000 fallback from actionable output.
- [x] 2.3 Apply owner risk state only to add/reentry decisions; preserve hold/reduce/exit evidence under every state.
- [x] 2.4 Expose compatible risk-control fields in settings/tracked-position responses and audit context.

## 3. Liquidity capacity

- [x] 3.1 Add tests for decision-eligible ADV, participation, spread, premium/discount, limit state, normal/stress exit days, and missing-data behavior.
- [x] 3.2 Implement a versioned pure liquidity-capacity contract and stable manifest/hash.
- [x] 3.3 Load 20-session decision-eligible turnover in batches and attach capacity evidence without provider requests or per-position history queries.
- [x] 3.4 Block only entry/add/reentry when capacity fails; keep exit alerts visible with stressed/unavailable execution wording.

## 4. Portfolio risk V2 shadow

- [x] 4.1 Add tests for dedicated broad-market observations, asymmetric regime recovery, clone/unknown exposure, shrinkage covariance, marginal risk contribution and deterministic stress scenarios.
- [x] 4.2 Decouple market-risk observations from ranked TopN selection and persist/reuse one post-close market-state evidence snapshot.
- [x] 4.3 Extend `PortfolioRiskBudget` metrics with bounded 120×20 factor/clone exposure, marginal risk contribution, concentration and stress results.
- [x] 4.4 Reuse the same V2 shadow evidence in production observation portfolio, optimized allocation and PIT backtest without changing v1 hard weights.
- [x] 4.5 Add capacity/blocked-exit stress to the ETF portfolio backtest while preserving next-eligible adjusted-open, fees, slippage and unavailable semantics.

## 5. Performance and orchestration

- [x] 5.1 Pass immutable owner risk context through list API, daily job, intraday job and V2 shadow adapter; remove aggregate N+1 queries.
- [x] 5.2 Keep intraday work O(active positions), post-close covariance capped at 20 assets and 120 sessions, and per-owner failures isolated.
- [x] 5.3 Add counters for NAV coverage, state transitions, blocked adds, liquidity unavailable/stressed, v2 shadow availability and computation bounds.

## 6. Verification and handoff

- [x] 6.1 Run focused pure-rule, migration/model, API/workflow, allocation and backtest tests in commands bounded to 60 seconds.
- [x] 6.2 Run backend domain-boundary tests and split Ruff checks into bounded groups.
- [x] 6.3 Run strict OpenSpec validation when CLI is available; otherwise run repository-equivalent artifact/schema checks and record the missing CLI limitation.
- [x] 6.4 Record implementation evidence, feature flags, rollback path, unavailable states, performance bounds and confirmation that ranking outputs are unchanged.
