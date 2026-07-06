## ADDED Requirements

### Requirement: ETF portfolio backtest compares exit V2 with TopN hold
ETF portfolio backtests SHALL compare exit V2 against TopN fixed hold, current live exit rules, and guard-only behavior.

#### Scenario: Comparison run completes
- **WHEN** an ETF portfolio backtest runs with exit V2 enabled
- **THEN** the result includes separate equity curves and metrics for TopN fixed hold, current exit rules, guard-only, and exit V2 with reentry

#### Scenario: Exit V2 underperforms hold
- **WHEN** exit V2 produces lower return than TopN hold without meaningful drawdown improvement
- **THEN** the result marks exit V2 as not validated for live promotion

### Requirement: ETF portfolio backtest simulates reentry after exit
ETF portfolio backtests SHALL simulate reentry after a reduction or exit using only data available at the replay time.

#### Scenario: Reentry conditions are met
- **WHEN** a previously exited ETF returns to an accepted ranking bucket after cooldown and has a valid entry timing state
- **THEN** the backtest records a simulated reentry according to target weight and position sizing rules

#### Scenario: Reentry conditions are not met
- **WHEN** a previously exited ETF remains weak or data is ineligible
- **THEN** the backtest keeps the position out and records the no-reentry reason

### Requirement: ETF portfolio backtest reports exit quality metrics
ETF portfolio backtests SHALL report exit-quality metrics in addition to portfolio returns.

#### Scenario: Metrics displayed
- **WHEN** a backtest result is shown
- **THEN** the system reports missed upside, protection success, false exit count, reentry count, average time out of market, turnover, alert count, and drawdown improvement

#### Scenario: Metrics insufficient
- **WHEN** the backtest lacks enough exit or reentry samples
- **THEN** the result marks exit-quality evidence as insufficient instead of drawing a strong conclusion
