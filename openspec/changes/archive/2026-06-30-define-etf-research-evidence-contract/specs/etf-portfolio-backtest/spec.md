## ADDED Requirements

### Requirement: ETF portfolio backtest consumes evidence contracts
ETF portfolio backtests SHALL consume ETF research evidence contracts instead of duplicating short-term page or allocation rules.

#### Scenario: Backtest starts
- **WHEN** an ETF portfolio backtest is created
- **THEN** it records the signal contract version, allocation contract version, execution model, fee model, data cutoff, and contract hash

#### Scenario: Contract is missing
- **WHEN** a backtest cannot find the required signal or allocation contract for the date range
- **THEN** it fails or marks the missing period explicitly instead of silently using a different rule set

#### Scenario: Backtest result is displayed
- **WHEN** a completed ETF portfolio backtest is shown
- **THEN** the result identifies whether it matches the current short-term workbench strategy contract

### Requirement: ETF portfolio backtest cannot update production decisions
ETF portfolio backtest output SHALL be evidence only and SHALL NOT automatically modify live ranking, allocation, tracked positions, or email alert thresholds.

#### Scenario: Backtest completes
- **WHEN** a backtest run finishes successfully
- **THEN** the system stores metrics and evidence summary but does not alter live ETF ranking, portfolio allocation, or alert rules
