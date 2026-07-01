# etf-observation-portfolio-optimization Specification

## Purpose
TBD - created by archiving change enhance-etf-signal-validation-portfolio-risk. Update Purpose after archive.
## Requirements
### Requirement: Observation portfolio uses constrained optimization
The system SHALL generate ETF observation portfolio weights using decision-eligible assets, ranking scores, volatility, drawdown, liquidity, correlation, and theme concentration constraints.

#### Scenario: Portfolio weights are generated
- **WHEN** enough decision-eligible ETF candidates and price history are available
- **THEN** the system produces a portfolio snapshot with weights, constraints used, excluded assets, and explanation for each weight

#### Scenario: Asset is decision-ineligible
- **WHEN** an ETF has stale, estimated, unavailable, or display-only data
- **THEN** the optimizer assigns it zero weight and records the exclusion reason

### Requirement: Single ETF weight is capped at 30 percent
The system SHALL cap each ETF observation weight at 30%.

#### Scenario: Candidate has dominant score
- **WHEN** one ETF has the highest score by a large margin
- **THEN** its optimized weight does not exceed 30%

### Requirement: Theme concentration is capped
The system SHALL limit total weight assigned to one theme or highly similar theme group.

#### Scenario: Top candidates are all technology ETFs
- **WHEN** the top ranked ETF candidates are concentrated in the same theme
- **THEN** the optimizer reduces theme concentration and records the cap in the snapshot explanation

### Requirement: Correlation reduces duplicate exposure
The system SHALL penalize highly correlated ETF pairs when assigning observation weights.

#### Scenario: Two ETFs are near duplicates
- **WHEN** two eligible ETFs have high return correlation over the configured lookback window
- **THEN** the optimizer reduces combined exposure or excludes the weaker candidate with an explanation

### Requirement: Optimization output is informational
The system SHALL present optimized weights as observation references, not trading instructions.

#### Scenario: User views optimized portfolio
- **WHEN** the optimized observation portfolio is displayed
- **THEN** the UI states that weights are research references and require manual judgment

### Requirement: Optimized Portfolio Has Deterministic Fallback
The ETF observation portfolio optimizer SHALL provide a deterministic fallback when constrained optimization cannot produce a valid solution.

#### Scenario: Optimization is feasible
- **WHEN** eligible ETF candidates have enough return history, volatility, and correlation data
- **THEN** the system returns optimized observation weights with constraints and reasons

#### Scenario: Optimization is not feasible
- **WHEN** covariance, history, candidate count, or constraints make optimization unavailable
- **THEN** the system returns simplified risk-based weights or no-weight output with a clear fallback reason

### Requirement: Observation Portfolio Explains Exclusions
The ETF observation portfolio SHALL explain why eligible ranked ETFs did or did not receive weight.

#### Scenario: ETF receives weight
- **WHEN** an ETF is assigned observation weight
- **THEN** the result includes score contribution, volatility effect, correlation effect, theme cap effect, and final weight

#### Scenario: ETF is excluded
- **WHEN** an ETF is excluded because of data, liquidity, correlation, theme concentration, or weight floor
- **THEN** the result includes the exclusion reason and keeps the asset available as watch-only

### Requirement: ETF portfolio candidates are layered by allocation role
The ETF observation portfolio optimizer SHALL classify eligible ETFs into allocation layers before assigning weights.

#### Scenario: Primary candidate is available
- **WHEN** an ETF has reliable data, sufficient liquidity, an observation label of `短线观察`, and an entry timing label of `健康回踩` or `趋势延续`
- **THEN** the optimizer classifies it as a primary allocation candidate unless concentration, correlation, or risk constraints exclude it

#### Scenario: High-watch candidate remains healthy
- **WHEN** an ETF has reliable data, sufficient liquidity, an observation label of `高位观察`, and an entry timing label of `健康回踩` or `趋势延续`
- **THEN** the optimizer MAY classify it as a satellite allocation candidate with lower maximum exposure instead of forcing zero weight

#### Scenario: Asset is watch-only
- **WHEN** an ETF has `冲高别追`, `跌破等待`, `放量转弱`, stale data, unavailable data, insufficient liquidity, or material structural risk
- **THEN** the optimizer assigns it zero weight and records it as watch-only or excluded with a concrete reason

### Requirement: ETF portfolio weights use dynamic risk constraints
The ETF observation portfolio optimizer SHALL assign weights using score, entry timing, observation label, volatility, drawdown, liquidity, data reliability, theme concentration, and correlation constraints.

#### Scenario: High-volatility ETF receives lower weight
- **WHEN** two ETFs have similar scores and data quality but one has materially higher recent volatility or drawdown
- **THEN** the optimizer assigns lower raw weight to the higher-risk ETF before applying final caps

#### Scenario: Satellite allocation is capped
- **WHEN** high-watch ETFs are classified as satellite candidates
- **THEN** the optimizer caps both each satellite ETF weight and total satellite exposure according to configured constraints

#### Scenario: Theme concentration is excessive
- **WHEN** multiple candidate ETFs belong to the same or highly similar theme
- **THEN** the optimizer reduces or excludes lower-ranked duplicates and records the theme concentration reason

### Requirement: ETF portfolio supports market regime allocation modes
The ETF observation portfolio optimizer SHALL output an allocation mode that explains whether the result is risk-on, neutral, defensive, or cash-wait.

#### Scenario: Risk-on mode is available
- **WHEN** enough primary ETF candidates satisfy data, liquidity, entry timing, and risk constraints
- **THEN** the optimizer returns `portfolio_mode=risk_on` and allocates most ETF capital to primary candidates within all caps

#### Scenario: Neutral mode uses mixed allocation
- **WHEN** primary candidates are not enough but satellite or defensive candidates satisfy constraints
- **THEN** the optimizer returns `portfolio_mode=neutral` and allocates across primary, satellite, and defensive layers with explicit layer weights

#### Scenario: Defensive mode is used
- **WHEN** offensive candidates are insufficient but defensive ETF candidates satisfy data and risk constraints
- **THEN** the optimizer returns `portfolio_mode=defensive` and allocates to defensive candidates without labeling them as low-risk guarantees

#### Scenario: Cash wait is used
- **WHEN** primary, satellite, and defensive candidates are insufficient or decision-ineligible
- **THEN** the optimizer returns `portfolio_mode=cash_wait`, sets cash weight to one, and records the cash-wait reason

### Requirement: ETF portfolio explains every allocation decision
The ETF observation portfolio optimizer SHALL provide a machine-readable and human-readable explanation for every weighted, watch-only, and excluded ETF.

#### Scenario: ETF receives weight
- **WHEN** an ETF receives target weight
- **THEN** the result includes allocation layer, final weight, score effect, entry timing effect, volatility effect, liquidity effect, theme cap effect, correlation effect, and data timestamp

#### Scenario: ETF is watch-only
- **WHEN** an ETF is not assigned weight but remains relevant to observe
- **THEN** the result includes the main reason such as high-position watch, chase risk, concentration, insufficient freshness, or defensive mismatch

#### Scenario: ETF is excluded
- **WHEN** an ETF is excluded from allocation
- **THEN** the result includes a concrete exclusion reason and does not silently omit the asset from the audit payload

