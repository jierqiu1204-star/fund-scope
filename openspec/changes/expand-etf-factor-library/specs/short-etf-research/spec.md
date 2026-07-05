## ADDED Requirements

### Requirement: Short ETF Signals Use Versioned Factor Profiles
The system SHALL generate ETF comprehensive attention scores from versioned ETF factor profiles instead of undocumented fixed fallback blends.

#### Scenario: Signal run computes factor profile
- **WHEN** the system generates a short-term ETF signal run
- **THEN** each signal item records the factor profile version, included factor groups, excluded factor groups, weights, and missing-factor reasons

#### Scenario: Comprehensive score is unavailable
- **WHEN** a signal item has only technical data and no decision-eligible non-technical factor group
- **THEN** the comprehensive attention score is `null` and the signal item explains that comprehensive scoring is waiting for more factor evidence

### Requirement: Short ETF Ranking Sorts Only Real Comprehensive Scores
The system SHALL sort ETF comprehensive attention rankings using only real numeric comprehensive scores and SHALL place unavailable comprehensive scores behind available scores.

#### Scenario: Opportunity sort has mixed availability
- **WHEN** the user requests ETF ranking sorted by comprehensive attention and some ETFs have unavailable comprehensive scores
- **THEN** ETFs with numeric comprehensive scores sort before ETFs with unavailable comprehensive scores

#### Scenario: Missing score is displayed
- **WHEN** an ETF has unavailable comprehensive attention score
- **THEN** the API and UI show a waiting or unavailable state instead of a numeric fallback

### Requirement: Short ETF Detail Uses Cached Factor Results
The system SHALL return ETF detail scoring fields from the latest successful ETF signal item cache and SHALL not recompute factor scores using a singleton ETF list.

#### Scenario: Detail score matches ranking
- **WHEN** a user opens ETF detail for an ETF present in the latest successful signal run
- **THEN** the technical score, factor group scores, comprehensive score, score version, and risk gates match the cached ranking item

#### Scenario: Cached signal item is missing
- **WHEN** a user opens ETF detail for an ETF that is not present in the latest successful signal run
- **THEN** the detail API returns a waiting or unavailable scoring state instead of generating neutral fallback scores

### Requirement: Short ETF UI Shows Factor Availability
The short ETF web interface SHALL show factor scores only when they are numeric and SHALL show readable unavailable states when factor data is missing, stale, seed-only, or display-only.

#### Scenario: Factor is numeric
- **WHEN** a factor group has a real numeric score
- **THEN** the UI formats and displays the score with its factor group label and profile context

#### Scenario: Factor is unavailable
- **WHEN** a factor group score is `null`
- **THEN** the UI displays `暂无` or a readable missing-data reason and does not format it as a numeric score

#### Scenario: Factor is display only
- **WHEN** a factor is available only as display context
- **THEN** the UI labels it as display-only context and does not imply that it contributed to comprehensive scoring

### Requirement: Short ETF Risk Labels Remain Independent From Factor Bonuses
The system SHALL keep entry timing labels, chase-risk labels, stale-data labels, low-liquidity labels, and insufficient-history labels independent from positive factor bonuses.

#### Scenario: Positive factors coexist with chase risk
- **WHEN** an ETF has strong momentum, sector trend, theme event, or fund-flow factors but also has chase-risk or overheat gates
- **THEN** the risk label remains visible and the final observation language stays constrained

#### Scenario: Positive factors coexist with low liquidity
- **WHEN** an ETF has strong positive factors but fails liquidity or ETF-structure gates
- **THEN** the UI and API keep the liquidity or ETF-structure limitation visible
