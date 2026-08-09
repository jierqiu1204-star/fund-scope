## ADDED Requirements

### Requirement: Rolling leader backtests persist immutable multi-horizon evidence
The ETF research evidence contract SHALL persist the rolling historical-proxy report under a separate experiment family with source, universe, formula, execution, cost, feature, signal, outcome, exclusion, checkpoint, code, and result identities.

#### Scenario: Complete backtest is persisted
- **WHEN** all source assets and eligible signal dates have been sealed and finalized
- **THEN** the evidence exposes per-candidate and combined 1, 3, 5, 10, and 20-session sample counts, average and median net returns, win rates, event-series drawdown, peer excess, coverage, exclusions, and confidence intervals

#### Scenario: Partial or incompatible evidence is read
- **WHEN** the checkpoint is incomplete or any immutable identity is incompatible
- **THEN** the API reports partial or incompatible with no fabricated aggregate and no fallback to a prior formula version

### Requirement: Historical backtest evidence cannot alter formal evidence state
Historical proxy backtest evidence SHALL keep PIT observation counts, independent dates, folds, holdout state, notification provenance, execution provenance, and production mutation permission unchanged.

#### Scenario: Historical sample count exceeds formal gates
- **WHEN** the proxy backtest contains at least 252 dates or 40 independent-looking samples
- **THEN** formal PIT gate credit remains zero because historical membership and receipt provenance are not factual
