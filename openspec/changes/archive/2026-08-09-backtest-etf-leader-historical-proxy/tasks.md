## 1. Freeze Backtest Contracts

- [x] 1.1 Define historical backtest family, schema, formula, execution, cost, horizon, source-bias, checkpoint, and unavailable-reason constants.
- [x] 1.2 Add pure input, signal, outcome, aggregate, and evidence validation with finite-value and immutable-hash checks.
- [x] 1.3 Add tests proving feature cutoffs precede entry/outcome dates and formal PIT gate credit stays zero.

## 2. Build Rolling Evaluator

- [x] 2.1 Materialize date-local rolling primitives from total-return-adjusted OHLCV without raw-provider fallback.
- [x] 2.2 Compute complete peer cross-sections, prior-leadership windows, frozen formula gates, scores, and clone selection per date.
- [x] 2.3 Generate next-session 1/3/5/10/20-session gross, cost, net, peer benchmark, and peer-excess outcomes.
- [x] 2.4 Aggregate all matches and zero-match dates into candidate/horizon statistics, event-series drawdown, and deterministic block-bootstrap intervals.

## 3. Add Bounded Production-Data Continuation

- [x] 3.1 Load the sealed source cohort and adjusted history into a contract-bound SQLite artifact in resumable asset pages.
- [x] 3.2 Evaluate and finalize with monotonic checkpoints, one worker, no live providers, and a hard return below 55 seconds.
- [x] 3.3 Persist one immutable historical-backtest evidence row idempotently and refuse local database targets.

## 4. Expose Research Evidence

- [x] 4.1 Project latest compatible historical-backtest evidence separately from factual observations and maturity evidence.
- [x] 4.2 Extend API and TypeScript schemas with bounded multi-horizon backtest fields and exact limitations.
- [x] 4.3 Render results beneath the historical proxy card without recommendation, validated-PIT, email, or execution language.

## 5. Verify And Run

- [x] 5.1 Run focused formula, no-lookahead, cost, zero-match, page-consistency, persistence, API, and frontend tests.
- [x] 5.2 Run changed-file Ruff, backend domain boundaries, TypeScript, static separation, formatting, and strict OpenSpec validation with per-command timeouts below 60 seconds.
- [x] 5.3 Deploy once, run bounded production continuations to completion, verify read-only evidence projection, and record the real multi-horizon results and limitations.
