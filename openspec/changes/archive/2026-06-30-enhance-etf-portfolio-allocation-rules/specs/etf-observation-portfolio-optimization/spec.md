## ADDED Requirements

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
