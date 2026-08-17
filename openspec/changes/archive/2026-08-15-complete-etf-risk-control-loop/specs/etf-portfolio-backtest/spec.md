## ADDED Requirements

### Requirement: ETF backtest models size-aware liquidity capacity
ETF portfolio backtests SHALL evaluate proposed fills against PIT-visible decision-eligible turnover and the same versioned liquidity-capacity contract used by live research outputs.

#### Scenario: Simulated entry exceeds capacity
- **WHEN** a proposed buy exceeds the frozen participation or liquidation-time policy at the execution cutoff
- **THEN** the fill is capped, deferred or excluded according to the preregistered rule and records the capacity reason

#### Scenario: Simulated exit is capacity constrained
- **WHEN** an exit signal occurs but PIT-visible volume cannot support the full simulated order
- **THEN** the backtest models pending/partial liquidation or an adverse stress outcome and MUST NOT assume an immediate full exit at the daily close

### Requirement: Portfolio backtest reports fixed risk stress scenarios
ETF portfolio backtests SHALL report deterministic market, theme, correlation, volatility and liquidity stress results separately from realized historical returns.

#### Scenario: Stress evidence is produced
- **WHEN** the portfolio has sufficient PIT factor and liquidity evidence
- **THEN** the result reports each frozen scenario, maximum stress loss, capacity coverage and contract hash without selecting scenarios to improve apparent performance

#### Scenario: Stress inputs are unavailable
- **WHEN** a scenario requires missing factor, clone or liquidity facts
- **THEN** that scenario is unavailable with a stable reason rather than being filled with zero loss
