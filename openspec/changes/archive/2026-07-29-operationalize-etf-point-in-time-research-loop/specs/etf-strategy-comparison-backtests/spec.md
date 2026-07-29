## ADDED Requirements

### Requirement: ETF ranking candidate comparison is frozen
ETF strategy comparison SHALL compare only the frozen baseline Top10, Top10 hysteresis, and Top10 hysteresis with regime/liquidity gate for ranking promotion evidence.

#### Scenario: Frozen candidates are compared
- **WHEN** a point-in-time ranking cohort is complete
- **THEN** all candidates use the same cohort, execution convention, cost policy, signal dates, common support, and primary endpoint

#### Scenario: Parameter search is requested
- **WHEN** a caller requests a weight grid, horizon search, Top N search, or outcome-driven candidate change
- **THEN** the comparison rejects the request rather than selecting the best historical cell

### Requirement: ETF ranking comparison reports realistic diagnostics
ETF strategy comparison SHALL report primary paired net excess, absolute gross and net returns, turnover, cost drag, rank churn, maximum drawdown, concentration, coverage, exclusions, and uncertainty.

#### Scenario: Comparison result is displayed
- **WHEN** a ranking candidate result is available
- **THEN** Top10 five-session paired net excess is labeled primary and all Top5/20 or 1/3/10-session results are labeled exploratory

#### Scenario: Independent dates are insufficient
- **WHEN** fewer than the required non-overlapping primary dates are complete
- **THEN** the result is `insufficient_data` and is not ranked as a historical winner
