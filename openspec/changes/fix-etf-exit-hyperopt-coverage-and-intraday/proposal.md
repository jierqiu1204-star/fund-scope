## Why

Current ETF exit-rule optimization is not yet decision-grade. The latest server run only covered 271 ETFs because the job first limits the universe to 300 eligible ETFs and then drops assets with insufficient daily history, while the real universe contains more than 1,400 eligible ETFs.

The current optimization result also uses `daily_close`, but the live workflow is intraday: public ETF quotes trigger an email, then the user manually trades a few minutes later. A daily-close optimizer cannot validate whether the live email exit rules are actually better.

## What Changes

- Replace the default exit-rule optimization path with a true intraday-alert replay model.
- Remove the hard default `max_assets=300` sampling behavior for ETF exit optimization; the default run must cover all eligible ETFs with enough data, bounded only by explicit request parameters.
- Add a coverage funnel to every run: all ETF count, eligible ETF count, enough-daily-history count, enough-intraday-history count, final optimized count, and exclusion reasons.
- Replay the real execution chain:
  - use only decision-eligible fresh intraday quotes;
  - trigger theoretical exit signals using the same live rule contract;
  - simulate manual execution after a configurable delay, default 3 minutes;
  - use the next available intraday quote after the delay;
  - never fill missing intraday execution with daily close.
- Compare every candidate parameter set against the current live default rule, not just absolute return.
- Use walk-forward and out-of-sample validation before marking a candidate as usable.
- Keep optimization research-only:
  - no real emails;
  - no tracked-position mutation;
  - no automatic adoption of candidate parameters.
- Expose enough evidence for the UI to clearly show whether optimization is full-coverage, partial, rejected, evidence-insufficient, or eligible for manual approval.

## Capabilities

### New Capabilities

- `etf-exit-intraday-hyperopt`: Full-coverage ETF exit-rule parameter search using historical intraday alert replay, delayed manual execution, baseline comparison, and coverage audit.

### Modified Capabilities

- `tracked-position-exit-strategy`: Live ETF exit thresholds may only use approved calibrated parameters when their evidence was produced by the intraday-alert execution model with sufficient coverage and matching rule contract.
- `etf-research-evidence-contract`: ETF exit optimization evidence must record execution model, coverage funnel, baseline-vs-candidate comparison, contract hash, and whether the result can influence live rules.

## Impact

- Backend services:
  - `app.services.short_research.etf_exit_hyperopt`
  - `app.services.etf_exit_calibration`
  - `app.services.risk_alerts`
  - `app.services.short_research.jobs`
- API:
  - Existing ETF exit hyperopt endpoints remain stable, but response payloads gain coverage, execution, and baseline comparison fields.
- Database:
  - Existing calibration/hyperopt tables may need compatible JSON field expansion; add migration only if typed columns are required.
- Frontend:
  - ETF strategy evidence page must show “current live rule” vs “intraday optimized candidate” and explain why candidates are rejected or not live.
- Scheduler/Admin:
  - Nightly optimization should run after intraday data, daily data, signal generation, and credibility evidence are available.
- Non-goals:
  - Do not connect brokers.
  - Do not auto-trade.
  - Do not auto-approve optimized parameters.
  - Do not use daily-close fallback for intraday exit optimization.
