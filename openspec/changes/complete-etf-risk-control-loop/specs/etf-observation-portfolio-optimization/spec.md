## ADDED Requirements

### Requirement: Market-risk state uses a dedicated PIT observation universe
The observation portfolio SHALL derive market-risk state from a versioned broad-market observation set that is independent of ranked TopN selection.

#### Scenario: Ranked candidates omit broad ETFs
- **WHEN** the current actionable ranking slice contains few or no broad-market ETFs
- **THEN** the market-risk classifier still uses the dedicated cutoff-consistent observation set and does not infer risk state from two incidental candidates

#### Scenario: State reopens after a risk reduction
- **WHEN** broad-market evidence improves after a defensive or unavailable state
- **THEN** risk reopening requires consecutive eligible trade sessions, while a deterioration may reduce risk immediately

### Requirement: Portfolio risk V2 reports factor and clone concentration
The observation portfolio SHALL report clone, theme, asset-class and unknown exposure separately and SHALL not treat unknown identity as proven diversification.

#### Scenario: Multiple ETFs track the same underlying
- **WHEN** final candidates share a decision-eligible clone group
- **THEN** their combined exposure and representative identities are reported together in V2 risk evidence

#### Scenario: Identity is unknown
- **WHEN** a candidate lacks required underlying or theme identity
- **THEN** V2 evidence reports unknown exposure and an unavailable or conservative status rather than silently granting diversification credit

### Requirement: Portfolio risk V2 is bounded and shadow-first
The observation portfolio SHALL calculate covariance, marginal risk contribution and deterministic stress evidence only for the bounded final portfolio and SHALL keep the new continuous metrics shadow-only until separately approved.

#### Scenario: Sufficient common history exists
- **WHEN** at most 20 candidates have up to 252 common decision-eligible adjusted return observations
- **THEN** the system reports shrinkage covariance risk, marginal/total risk contributions, concentration, effective holding count and fixed stress losses

#### Scenario: History is insufficient
- **WHEN** common history, clone identity or factor coverage is below the declared minimum
- **THEN** V2 returns stable unavailable reasons and does not modify v1 hard weights or estimate missing exposure as zero

#### Scenario: Full ETF universe is present
- **WHEN** the authoritative universe contains more than one thousand ETFs
- **THEN** V2 still computes only over the final bounded portfolio and MUST NOT construct a universe-wide intraday covariance matrix
