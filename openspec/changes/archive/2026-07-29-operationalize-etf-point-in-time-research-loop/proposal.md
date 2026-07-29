## Why

FundScope already has point-in-time ranking, factor diagnostics, frozen candidate, forward-outcome, and action-policy primitives, but they are not connected as one bounded production-shaped research loop. As a result, short current-vintage replays can be mistaken for strategy evidence even when historical visibility, independent samples, or policy execution provenance are insufficient.

## What Changes

- Add one resumable, single-worker coordinator that materializes immutable point-in-time ranking cohorts, evaluates the three frozen ranking candidates, computes the pre-registered cost-aware primary endpoint, and persists deterministic checkpoints and evidence.
- Operationalize factor IC, quantile, residual, marginal-contribution, walk-forward, purge/embargo, bootstrap, multiplicity, holdout, and promotion gates without changing `final_score_v3`.
- Feed complete replay rankings through a research-only portfolio and `etf_exit_action_v3` policy shadow while keeping simulated notifications, SMTP acceptance, provider delivery, and user-confirmed execution distinct.
- Expose stable evidence provenance, coverage, exclusions, costs, uncertainty, sample sufficiency, and unavailable reasons through the existing research evidence API and workbench.
- Require dual 95 percent production coverage plus pre-registered sample, uncertainty, drawdown, concentration, and holdout gates before a separately approved score-version proposal can be created.
- Keep every local, remote, provider, and test command bounded to at most 55 seconds, with adaptive batches of 5 to 20 ETFs and persistent resume state for the 2-core/4-GB server profile.

## Capabilities

### New Capabilities

- `etf-point-in-time-research-loop`: Defines the bounded coordinator, immutable replay identities, checkpoints, frozen ranking candidates, and production-isolated policy-shadow lifecycle.

### Modified Capabilities

- `etf-research-evidence-contract`: Adds explicit ranking source, policy mode, notification and execution provenance, immutable manifests, coverage dimensions, uncertainty, and stable unavailable reasons.
- `etf-signal-validation`: Fixes the primary ranking estimand, execution and cost convention, chronological validation, multiplicity, holdout, and promotion gates.
- `etf-strategy-comparison-backtests`: Restricts primary candidate selection to three frozen candidates and separates primary evidence from exploratory Top N and horizon cells.
- `etf-portfolio-backtest`: Adds research-only policy-shadow action-cycle evidence and prohibits replay writes to production position, alert, notification, or SMTP state.
- `etf-label-validation-dashboard`: Separates production ranking, research replay, policy shadow, live notification, and confirmed execution evidence in API and UI states.

## Impact

- Backend Strategy Lab replay coordination, ranking/factor validation, policy-shadow orchestration, evidence persistence, and research API serialization.
- Existing database evidence records or additive migrations for immutable run, checkpoint, aggregate, exclusion, and provenance fields.
- `/short-term` evidence presentation and frontend contracts; no change to current ranking weights, live allocation, tracked positions, alert thresholds, or notifier decisions.
- Production readiness remains dependent on `accelerate-etf-publish-readiness-sync` tasks 8.4–8.6 and real dual 95 percent adjusted-data coverage.
