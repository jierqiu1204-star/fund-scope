## ADDED Requirements

### Requirement: Allocation strategy comparison backtest
ETF portfolio backtesting SHALL support comparing rule-based allocation, optimized allocation, and equal-weight allocation under the same evidence contract.

#### Scenario: Compare allocation methods
- **WHEN** a user runs an ETF portfolio backtest
- **THEN** the system SHALL calculate comparable metrics for rule-based, optimized, and equal-weight methods when data is available

#### Scenario: Missing optimized method
- **WHEN** optimized allocation cannot be generated for a backtest date
- **THEN** the system SHALL mark that method unavailable for that date and MUST NOT fill weights from the rule-based method

### Requirement: Execution model separation
ETF portfolio backtesting SHALL keep daily-close simulation and intraday-alert execution simulation separate.

#### Scenario: Intraday alert backtest
- **WHEN** execution model is `intraday_alert`
- **THEN** the system SHALL use historical intraday quotes for alert timing and execution, and MUST NOT use daily close prices as a fallback execution price

#### Scenario: Daily close backtest
- **WHEN** execution model is `daily_close`
- **THEN** the system SHALL label the result as daily-close evidence and MUST NOT claim that it validates intraday email execution

### Requirement: Evidence contract on backtest results
ETF portfolio backtest results SHALL include evidence contract metadata.

#### Scenario: Current strategy result
- **WHEN** a backtest is generated from the current ETF workbench rules
- **THEN** it SHALL include strategy contract hash, rule version, allocation version, and execution model

#### Scenario: Old result display
- **WHEN** a historical backtest lacks the current contract hash
- **THEN** the frontend SHALL display it as an old-method result and exclude it from current strategy reliability claims
