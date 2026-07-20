## ADDED Requirements

### Requirement: ETF Factor Library Defines Complete Factor Taxonomy
The system SHALL define a complete ETF factor taxonomy covering price momentum, reversal and overheat, volatility risk, liquidity, sector trend, theme events, fund flow, sentiment heat, fundamentals quality, valuation, macro style, and ETF structure.

#### Scenario: Factor registry lists complete taxonomy
- **WHEN** the ETF factor registry is loaded
- **THEN** it includes factor groups for price momentum, reversal and overheat, volatility risk, liquidity, sector trend, theme events, fund flow, sentiment heat, fundamentals quality, valuation, macro style, and ETF structure

#### Scenario: Factor has stable metadata
- **WHEN** a factor is registered
- **THEN** it has a stable factor id, Chinese label, factor group, calculation window, direction, usage, source type, and description

### Requirement: ETF Factor Results Expose Availability And Reliability
The system SHALL expose every ETF factor result with score, availability, reliability, source, as-of date, raw value, reason, and component metadata.

#### Scenario: Factor is available
- **WHEN** a factor has sufficient decision-eligible source data
- **THEN** its factor result includes a numeric score, `available` availability, reliability metadata, source metadata, as-of date, and calculation components

#### Scenario: Factor data is missing
- **WHEN** a factor lacks sufficient decision-eligible source data
- **THEN** its factor result has `score = null`, a non-available status, and a readable reason instead of a neutral fallback score

#### Scenario: Factor source is display only
- **WHEN** a factor source is seed-only, estimated, stale, or not validated for scoring
- **THEN** the factor result is marked `display_only` or unavailable and MUST NOT contribute to comprehensive scoring

### Requirement: ETF Factor Usage Separates Scores, Penalties, Gates, And Display Context
The system SHALL distinguish factors that add to score, factors that subtract risk, factors that gate eligibility, and factors that only explain context.

#### Scenario: Positive scoring factor exists
- **WHEN** a price momentum, sector trend, theme event, fund flow, breadth, valuation, or macro factor is decision-eligible
- **THEN** it may contribute through its configured scoring profile weight

#### Scenario: Risk factor exists
- **WHEN** an overheat, volatility, drawdown, abnormal premium, stale-data, or low-liquidity factor crosses a configured risk threshold
- **THEN** the system records a risk penalty or risk gate that can limit the final observation label

#### Scenario: Display-only factor exists
- **WHEN** a factor is useful for explanation but not decision-eligible
- **THEN** the API may expose it with source and limitation metadata but MUST NOT include it in the numeric score

### Requirement: ETF Factor Profiles Are Versioned
The system SHALL compute comprehensive ETF scores through a versioned factor profile that records included groups, excluded groups, weights, and missing-factor reasons.

#### Scenario: Full factor profile is available
- **WHEN** all required factor groups for the full profile are available
- **THEN** the system computes a comprehensive score and records the full profile version, group weights, and factor breakdown

#### Scenario: Factor profile is degraded
- **WHEN** one or more factor groups are unavailable but enough non-technical evidence remains
- **THEN** the system selects a documented degraded profile, recomputes weights only across eligible groups, and records which factors were excluded

#### Scenario: Only technical data is available
- **WHEN** no non-technical factor group is decision-eligible
- **THEN** the comprehensive score is unavailable and the system returns an explicit waiting or unavailable state

### Requirement: ETF Risk Gates Override Positive Factor Strength
The system SHALL keep risk gates independent from positive factor scores so that strong momentum, sector trend, theme events, or fund flow cannot erase hard risk states.

#### Scenario: Overheat risk is present
- **WHEN** an ETF has a short-term overheat or chase-risk gate
- **THEN** the final observation language remains limited even if theme, sector, or fund-flow factors are strong

#### Scenario: Liquidity risk is present
- **WHEN** an ETF fails the configured liquidity gate
- **THEN** it cannot be promoted by high factor scores without exposing the liquidity limitation

#### Scenario: Data reliability risk is present
- **WHEN** required source data is stale, estimated, unavailable, or display-only
- **THEN** the affected factors are excluded from scoring and the factor breakdown records the reliability limitation

### Requirement: ETF Factor Outputs Are Cacheable In Signal Items
The system SHALL persist factor results, factor group scores, factor profile metadata, risk gates, and comprehensive score breakdowns in the ETF signal item cache.

#### Scenario: Signal item is persisted
- **WHEN** an ETF signal run is generated
- **THEN** each signal item stores factor results, factor group scores, factor profile version, opportunity score version, risk gates, and missing-factor reasons

#### Scenario: Detail page reads cached factors
- **WHEN** the ETF detail API returns a current score
- **THEN** it reads factor scores and comprehensive score from the latest successful signal item rather than recomputing singleton normalized scores

### Requirement: ETF External Factor Sources Require Eligibility Checks
The system SHALL require explicit data-source eligibility before external sentiment, fund flow, valuation, macro, or fundamentals data can affect ETF scoring.

#### Scenario: External source is validated
- **WHEN** an external factor source provides freshness, source identity, coverage, and reliability metadata
- **THEN** the system may mark the factor decision-eligible according to the factor registry

#### Scenario: External source is not validated
- **WHEN** an external factor source lacks freshness, coverage, or reliability metadata
- **THEN** the system marks the factor as unavailable or display-only and excludes it from scoring

### Requirement: ETF Factor Comparison Supports Theme ETFs
The system SHALL allow ETFs from themes such as robotics, innovative drug, semiconductor, optical module, artificial intelligence, and broad market groups to be compared using the same factor output contract.

#### Scenario: Theme ETF has comparable factor groups
- **WHEN** the ranking includes robotics, innovative drug, semiconductor, optical-module proxy, artificial-intelligence, and broad-market ETFs
- **THEN** the API exposes factor group scores and missing-factor states using the same schema for every ETF

#### Scenario: Theme-specific catalyst is missing
- **WHEN** a theme ETF lacks a verified theme event or sentiment source
- **THEN** the theme or sentiment factor remains unavailable and does not block other available factor groups from being shown

