## Context

FundScope currently has ETF exit credibility and Hyperopt-style research, but the two are not aligned with the live user workflow.

The server database contains more than 1,400 short-term eligible ETFs with broad daily coverage, but the current Hyperopt job defaults to `max_assets=300`. After filtering for assets with enough daily history, the latest run optimized only 271 ETFs. That number is a sampling artifact, not true coverage.

The current Hyperopt run also records `execution_model=daily_close`. That is useful for a coarse daily-close strategy, but the product behavior is intraday: ETF quotes are pulled during trading hours, an exit email may be triggered, and the user manually trades after seeing the email. The optimizer must validate that same chain.

## Goals / Non-Goals

**Goals:**

- Make ETF exit-rule optimization default to full eligible ETF coverage, not a fixed first-300 sample.
- Use an intraday alert replay model that matches the live email workflow.
- Compare candidate parameters against the current live baseline rule.
- Record a clear coverage funnel and exclusion reasons.
- Keep all results research-only unless explicitly approved later.
- Preserve current API compatibility and live email behavior until a candidate is approved.

**Non-Goals:**

- No broker integration.
- No automatic trading.
- No automatic approval of optimized parameters.
- No direct dependency on Freqtrade, Backtrader, vectorbt, or QuantConnect.
- No fallback from missing intraday execution data to daily close.
- No rewrite of the short-term ranking or portfolio allocation algorithms.

## Decisions

### Decision 1: Reuse mature-project ideas, not their runtime modules

Freqtrade Hyperopt, Backtrader StopTrail, vectorbt stop arrays, and QuantConnect trailing stops provide the right model: parameter search, walk-forward validation, and trailing-stop replay. They are not dropped in as libraries because FundScope has A-share ETF data, PostgreSQL persistence, user-scoped tracking, email alerts, and a strict no-auto-trade boundary.

Alternative considered: embed Backtrader/vectorbt as the backtest engine. Rejected for this change because it would add a large integration surface and still require custom intraday email replay logic.

### Decision 2: Full coverage by default, explicit sampling only by request

The optimizer SHALL load all `is_short_term_eligible` ETFs by default and only limit assets when a caller explicitly passes a smaller `max_assets`. The run summary records whether coverage is full or sampled.

Alternative considered: raise default from 300 to 1000. Rejected because it can still silently miss assets and makes evidence hard to interpret.

### Decision 3: Intraday replay is the primary optimization model

The primary optimizer SHALL use `execution_model=intraday_alert`. It replays historical fresh intraday quotes, computes exit signals using the current rule contract, and simulates manual execution after a delay, default 3 minutes.

Daily-close results may remain as a separate coarse reference, but they cannot be used to claim that live email rules are optimized.

### Decision 4: Candidate quality is relative to baseline

A candidate is useful only if it beats the current live rule on out-of-sample and rolling validation. Absolute return is not enough. The objective should include drawdown, false exits, missed upside, email count, turnover, unfilled alerts, and sample sufficiency.

### Decision 5: Missing intraday execution data blocks evidence

If a signal has no executable intraday quote after the configured delay, the event is counted as unfilled. The optimizer MUST NOT replace it with daily close. This keeps the evidence honest even if fewer historical windows are usable.

### Decision 6: Approved-parameter lookup remains read-only and conservative

Live tracked-position rules may look up approved calibrated parameters, but only when:

- execution model is `intraday_alert`;
- contract hash matches current live rule;
- coverage is sufficient;
- candidate status is `approved`;
- data used by the current evaluation is email-eligible.

Otherwise the system continues using current dynamic defaults.

## Risks / Trade-offs

- [Risk] Full ETF coverage increases nightly job time. → Mitigation: batch load data, record duration, allow explicit sampled dry-runs for development, and run the production job at night.
- [Risk] Intraday history is shorter than daily history. → Mitigation: mark older periods as unavailable instead of faking evidence with daily close.
- [Risk] Candidate parameters overfit a short market regime. → Mitigation: require out-of-sample, rolling-window stability, and baseline comparison.
- [Risk] Optimizer results may still reject all candidates. → Mitigation: treat that as a valid research result and show why defaults remain live.
- [Risk] UI may imply optimized rules are active. → Mitigation: expose current live rule and candidate optimized rule as separate sections.

## Migration Plan

1. Add compatible JSON fields or typed columns only if existing run/item tables cannot store coverage and baseline data.
2. Deploy code with old default rules still live.
3. Run database migration.
4. Run manual intraday Hyperopt job once on the server.
5. Verify coverage funnel and candidate status.
6. Keep live email behavior unchanged unless an approved parameter already exists and passes the new stricter evidence gate.

Rollback is simple: stop scheduling the new job and ignore new evidence fields. Existing live thresholds remain the default dynamic rules.

## Open Questions

- Should production nightly optimization always cover all eligible ETFs, or should very low-liquidity ETFs be excluded before optimization?
- What minimum intraday history window is acceptable for manual approval: 30 trading days, 60 trading days, or 120 trading days?
- Should a later change add a manual approval UI, or should approval remain database/admin-only for now?
