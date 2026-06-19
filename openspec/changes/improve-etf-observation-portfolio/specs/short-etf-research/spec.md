## MODIFIED Requirements

### Requirement: ETF Observation Portfolio Produces Target Weights
The system SHALL generate a rule-based ETF observation portfolio that expresses selected ETF exposure as target weights plus a cash weight for manual user reference, and SHALL exclude assets whose current observation or entry-timing labels indicate overextension, weak entry timing, stale data, or unsuitable short-term conditions from the primary weighted portfolio.

#### Scenario: Observation portfolio returns weights
- **WHEN** the system generates the latest ETF observation portfolio
- **THEN** the result includes ETF codes, names, target weights, cash weight, score evidence, risk reasons, data date, method explanation, and separate lists for weighted candidates, watch-only candidates, and excluded candidates

#### Scenario: High-risk conditions increase cash
- **WHEN** top-ranked ETFs are overextended, volatile, illiquid, data-stale, highly correlated with selected ETFs, or concentrated in the same theme
- **THEN** the observation portfolio reduces ETF exposure or increases cash weight rather than presenting a fully invested portfolio

#### Scenario: High-watch assets do not receive primary weights
- **WHEN** an ETF is labeled `高位观察`, `高位别追`, `冲高别追`, `跌破等待`, `放量转弱`, `数据不足`, or has stale or insufficient data
- **THEN** the ETF is not assigned a primary target weight and is returned as watch-only or excluded with a readable reason

#### Scenario: Entry timing gates primary portfolio eligibility
- **WHEN** an ETF is labeled `短线观察` and its entry timing is `健康回踩` or `趋势延续`
- **THEN** the ETF can be considered for a primary target weight subject to liquidity, volatility, drawdown, correlation, and theme concentration constraints

#### Scenario: Correlation and theme concentration reduce weights
- **WHEN** two or more candidate ETFs have high recent return correlation or share the same investment theme
- **THEN** the portfolio keeps exposure diversified by lowering or skipping lower-ranked overlapping candidates and explaining the concentration reason

#### Scenario: Observation portfolio is not a trade instruction
- **WHEN** the observation portfolio is returned by API or UI
- **THEN** it is labeled as research-only manual reference and does not include automatic order instructions, guaranteed return, expected return, target price, or direct buy wording
