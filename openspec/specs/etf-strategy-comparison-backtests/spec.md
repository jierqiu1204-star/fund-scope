# etf-strategy-comparison-backtests Specification

## Purpose
TBD - created by archiving change add-etf-strategy-comparison-backtests. Update Purpose after archive.
## Requirements
### Requirement: ETF strategy comparison backtests compare multiple deterministic strategies
The system SHALL provide historical daily comparison backtests for multiple deterministic ETF strategy styles using the same ETF daily data store.

#### Scenario: Comparison strategies are executed
- **WHEN** a user or admin starts an ETF strategy comparison backtest
- **THEN** the system runs the current FundScope workbench strategy, pure momentum Top N, momentum with volatility weighting, momentum with market-regime cash filter, and an equal-weight benchmark over the same date range

#### Scenario: Strategy set is deterministic
- **WHEN** the same date range, data snapshot, fee assumptions, and strategy parameters are used twice
- **THEN** the comparison backtest returns the same trades, equity curves, and summary metrics

### Requirement: Current workbench strategy uses the shared strategy contract
The current FundScope strategy inside the comparison backtest MUST use the same strategy contract as the `/short-term` ETF资金配置参考.

#### Scenario: Current strategy generates historical weights
- **WHEN** the comparison backtest evaluates a historical trading day for the current FundScope strategy
- **THEN** it calls the shared ETF strategy contract to produce ranking, entry timing, portfolio mode, target weights, and exclusion reasons using only data available on or before that day

#### Scenario: Backtest does not copy page rules
- **WHEN** the current workbench strategy is implemented for comparison
- **THEN** the code MUST NOT maintain a separate copy of ranking, entry timing, or portfolio allocation rules that can diverge from the short-term workbench

### Requirement: Comparison results are auditable
The system SHALL store enough metadata for each strategy comparison run to explain what was tested.

#### Scenario: Comparison run completes
- **WHEN** a strategy comparison backtest finishes
- **THEN** the system stores the date range, strategy names, strategy versions, parameter versions, data coverage, fee assumptions, generated metrics, and any sample limitations

#### Scenario: Strategy fails during comparison
- **WHEN** one strategy fails because of data insufficiency or calculation error
- **THEN** the system records the failed strategy and readable reason while preserving results for other strategies when possible

### Requirement: Comparison metrics include return and risk
The system SHALL report beginner-readable return, risk, and trading metrics for each compared strategy.

#### Scenario: Metrics are displayed
- **WHEN** the user views a completed strategy comparison
- **THEN** the system shows cumulative return, maximum drawdown, volatility, return-to-drawdown ratio, trade count, turnover, win rate, cash-wait days, and data coverage for each strategy

#### Scenario: Strategy has insufficient data
- **WHEN** a strategy has too few valid trading days or cannot warm up indicators
- **THEN** the system marks that strategy as 样本不足 and MUST NOT rank it as historically better

### Requirement: Comparison backtests are research-only
The system SHALL present ETF strategy comparison backtests as research evidence, not trading instructions.

#### Scenario: User views comparison summary
- **WHEN** the UI renders strategy comparison results
- **THEN** it states that the results are historical daily simulations, do not guarantee future returns, and do not execute trades

#### Scenario: Best historical strategy exists
- **WHEN** one strategy has the strongest historical metric
- **THEN** the system MUST NOT automatically replace the live `/short-term` strategy or email alert rules

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

