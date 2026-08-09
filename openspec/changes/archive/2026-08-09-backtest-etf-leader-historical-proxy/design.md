## Context

The deployed `leader_tactics_historical_proxy_v1` evidence evaluates one sealed source date and explicitly uses current-vintage membership and taxonomy. Production contains enough total-return-adjusted ETF history for a bounded rolling research replay, but it does not contain factual historical membership receipts. The server has 2 cores, about 4 GB RAM, no swap, and every command or continuation must remain below 60 seconds.

## Goals / Non-Goals

**Goals:**

- Reuse the frozen transparent proxy formulas and current-vintage source identity.
- Generate date-local signals and next-session multi-horizon net outcomes without future feature data.
- Persist a resumable artifact and one immutable research evidence report.
- Make favorable or unfavorable results equally visible.

**Non-Goals:**

- Claim factual PIT membership, formal Alpha validation, or a buy recommendation.
- Tune thresholds, search parameter grids, reconstruct delisted ETFs, or consume the locked holdout.
- Change ranking, allocation, positions, alerts, notification, SMTP, or execution state.

## Decisions

### 1. Freeze the deployed formulas before reading outcomes

The evaluator uses the deployed breakout and former-leader repair thresholds, including the exact symmetric overextension formula, peer percentiles, and clone policy. Formula and cost hashes are part of the run contract. Parameter search is rejected because the available window is short and would overfit the same sample used for evaluation.

### 2. Use a three-phase SQLite artifact

`LOAD` pages source assets and adjusted OHLCV into an artifact database, `EVALUATE` computes compact date-local signals and outcomes, and `FINALIZE` emits immutable aggregates and evidence. Each committed page advances a monotonic cursor. This is preferred to loading the whole production result set into SQLAlchemy objects or keeping a long process alive.

### 3. Precompute date-local primitives and cross-sectional percentiles

Per-asset rolling primitives are computed once from sorted adjusted bars. Peer return/liquidity percentiles are then computed per signal date and peer group, followed by the historical prior-leadership window. Candidate scores are formed only after the complete date cross-section is sealed, so page size cannot change ranks.

### 4. Use an explicit next-close event study

Signal features use the close of date T; entry uses the adjusted close at T+1; each horizon exits H sessions after entry. A fixed 20-basis-point round-trip deduction is applied. The report is an overlapping event study, not a capital-constrained portfolio simulation, so drawdown is labelled `event_series_max_drawdown`.

### 5. Compare against same-date peer outcomes

For each event and horizon, the evaluator reports absolute net return and net excess over the equal-weight eligible peer-group return. A historical comprehensive-ranking baseline is unavailable without factual prior ranking snapshots and will not be fabricated.

### 6. Persist a separate zero-credit evidence family

Completed reports use `leader_tactics_historical_backtest_v1`. The leader evidence view reads this family independently and nests it under the historical proxy section. Formal observation and maturity families are not merged with it.

### 7. Keep continuation resource bounded

One process runs at a time. Asset pages and evaluation work stop before 55 seconds, memory-sensitive data structures stay in the artifact, and live providers are never constructed. Re-running a completed compatible contract returns the existing immutable result.

## Risks / Trade-offs

- [Current-vintage membership creates survivorship and taxonomy lookahead] → Label every output as historical proxy, report the sealed source date, and force formal gate credit to zero.
- [Many overlapping events inflate apparent sample size] → Report unique signal dates, event counts, date-level aggregates, and deterministic block-bootstrap intervals.
- [Thin peer groups make percentiles unstable] → Preserve the existing minimum peer count and record exclusions.
- [Short histories reduce 20-session outcomes] → Exclude incomplete horizons rather than shortening or imputing them.
- [Server interruption leaves partial work] → Commit every page and verify the contract hash before resume.

## Migration Plan

1. Deploy the evaluator and read-only projection with no scheduled invocation.
2. Run focused no-lookahead, cost, page-consistency, and production-isolation tests.
3. Execute one bounded manual continuation at a time until the artifact is complete.
4. Persist one immutable evidence row, verify the API/UI, and keep all production policy unchanged.
5. Roll back by hiding the new evidence family; retain immutable artifacts for audit.
