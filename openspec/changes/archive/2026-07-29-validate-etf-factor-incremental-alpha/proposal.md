## Why

FundScope has several plausible ETF factors and proposed longer-history variants, but a plausible formula is not evidence that it adds out-of-sample net return beyond the existing ranking. We need a pre-registered, point-in-time factor diagnostic workflow before changing weights, adding 120/250-day inputs, or creating `final_score_v4`.

## What Changes

- Add a Strategy Lab-only factor experiment workflow that consumes the separated daily research contract from `separate-etf-research-and-actionable-ranks`.
- Pre-register every factor definition, direction, transform, universe, buckets, horizons, cost model, exclusion rules, primary endpoint, holdout, and experiment hash before reading outcomes.
- Measure daily cross-sectional rank IC, IC stability, quantile returns and spreads, Top 5/10/20 net returns, factor correlations, marginal contribution, turnover, rank churn, drawdown, concentration, and exclusion coverage.
- Use point-in-time total-return-adjusted data, historical universe membership, next-session execution, fixed non-zero costs, purged walk-forward splits, a 10-session embargo, and block-bootstrap uncertainty.
- Make the primary comparison the paired Top 10 five-session net excess return against the frozen baseline on identical eligible dates and ETFs.
- Test whether sector trend contributes information after controlling for technical momentum and whether 120/250-session candidates add stable information beyond the current 5/10/20/60-session research score.
- Limit each frozen experiment to at most three declared candidate score variants and prevent grid search, holdout-driven selection, or automatic production updates.
- Persist factor evidence separately from live ranking, portfolio allocation, tracked positions, alerts, and notification settings.

## Capabilities

### New Capabilities
- `etf-factor-incremental-alpha-validation`: Defines pre-registered point-in-time factor diagnostics, paired out-of-sample comparisons, uncertainty reporting, and promotion gates.

### Modified Capabilities
- `etf-signal-validation`: Adds factor- and rank-level outcome evidence while preserving no-lookahead, future-window exclusion, and research-only boundaries.
- `etf-research-evidence-contract`: Records factor experiment manifests, hashes, split roles, candidate identities, costs, exclusions, and their non-production evidence status.

## Impact

- Strategy Lab research services, point-in-time daily replay readers, experiment persistence, and evidence APIs.
- No change to `daily_reconstructable_v1`, `final_score_v3`, `actionable_rank_v1`, production weights, portfolio rules, email rules, or tracked-position alerts.
- Implementation starts only after the first change provides stable, distinct ranking contracts and sufficient point-in-time adjusted-data coverage.
