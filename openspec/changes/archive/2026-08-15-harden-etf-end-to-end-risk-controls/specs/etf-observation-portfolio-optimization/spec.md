## ADDED Requirements

### Requirement: Observation portfolio applies a portfolio-level risk budget
The ETF observation portfolio SHALL apply a versioned portfolio-level risk budget after asset selection and before publishing final observation weights.

#### Scenario: Risk-on mode has sufficient evidence
- **WHEN** the portfolio mode is `risk_on` and volatility, drawdown, liquidity, theme, and correlation evidence is decision-eligible
- **THEN** final risky-asset exposure does not exceed the configured risk-on cap and the remaining weight is explicitly reported as cash

#### Scenario: Neutral or defensive mode is selected
- **WHEN** the portfolio mode is `neutral` or `defensive`
- **THEN** the system enforces that mode's lower risky-exposure cap and minimum cash weight in addition to single-ETF, satellite, theme, and correlation constraints

#### Scenario: Portfolio risk evidence is insufficient
- **WHEN** aggregate volatility, drawdown, liquidity, or correlation evidence needed by the risk budget is missing, non-finite, stale, or based on too few observations
- **THEN** the system fails closed to a stricter exposure cap or `cash_wait`, records the missing evidence, and MUST NOT renormalize eligible ETF weights back to full exposure

### Requirement: Observation portfolio constrains correlated exposure clusters
The ETF observation portfolio SHALL treat highly correlated same-theme or same-underlying ETFs as one exposure cluster for concentration control.

#### Scenario: Multiple candidates form a correlated cluster
- **WHEN** multiple eligible ETFs exceed the configured correlation threshold with sufficient overlapping observations
- **THEN** their combined final weight is capped, weaker duplicates are reduced or excluded deterministically, and the cluster decision is included in the explanation

#### Scenario: Correlation evidence is too short
- **WHEN** a candidate pair lacks the minimum overlapping observations
- **THEN** the pair is not declared diversified and receives a conservative unavailable or watch-only treatment instead of a zero-correlation assumption

### Requirement: Risk scaling preserves cash instead of restoring full exposure
The ETF observation portfolio SHALL preserve de-risking produced by concentration, volatility, drawdown, liquidity, or market-mode controls.

#### Scenario: Raw allocation exceeds the risk budget
- **WHEN** otherwise valid raw ETF weights exceed the applicable risky-exposure cap
- **THEN** the system scales risky weights down deterministically, assigns the difference to cash, and MUST NOT subsequently normalize risky weights to one
