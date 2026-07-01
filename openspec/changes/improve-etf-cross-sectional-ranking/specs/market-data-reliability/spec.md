## ADDED Requirements

### Requirement: Ranking inputs require decision-eligible market data
The system SHALL prevent stale, estimated, unavailable, inferred-time, or display-only market data from increasing ETF ranking scores, label confidence, or portfolio eligibility.

#### Scenario: Stale quote exists during market hours
- **WHEN** an ETF has only a stale intraday quote during market hours
- **THEN** the ranking response may display the stale quote with limitations but MUST NOT use it to improve realtime score, realtime buy-point label, or portfolio eligibility

#### Scenario: Alternate provider supplies real data
- **WHEN** an alternate provider supplies real market data with valid source, time, and reliability metadata
- **THEN** the ranking may use that data while recording the provider and reliability level in the score breakdown

#### Scenario: Premium data is missing
- **WHEN** premium or discount data is unavailable for an ETF
- **THEN** the ranking reduces confidence or records a limitation instead of assuming a neutral premium state without disclosure

## MODIFIED Requirements

### Requirement: Research Calculations Use Decision-Eligible Data Only
The system SHALL use only verified or alternate-provider real market data for label validation, portfolio weights, intraday score adjustments, and email-triggering holding signals. ETF final ranking score components that affect ordering or confidence SHALL also use only decision-eligible market data.

#### Scenario: Estimated data exists
- **WHEN** a value is estimated, stale, unavailable, generated from AI/rule fallback text, or inferred from a provider fetch time without quote-time proof
- **THEN** it may be displayed with a limitation note but MUST NOT contribute to scores, label validation, portfolio target weight, or email-triggering logic

#### Scenario: Alternate provider real data exists
- **WHEN** a backup provider supplies real market data with valid date, source, and freshness metadata
- **THEN** the system may use it for calculations while recording provider source and reliability level

#### Scenario: Ranking component uses data
- **WHEN** ETF final ranking computes cross-sectional percentile, dynamic threshold adjustment, label evidence adjustment, liquidity quality, or premium penalty
- **THEN** each component records whether its input was decision-eligible, display-only, stale, or unavailable

#### Scenario: Data is not decision-eligible
- **WHEN** an ETF lacks decision-eligible data for a score component
- **THEN** that component cannot improve the final score and the response includes a readable limitation reason
