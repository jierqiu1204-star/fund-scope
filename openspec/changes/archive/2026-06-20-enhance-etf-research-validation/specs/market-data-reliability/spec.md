## ADDED Requirements

### Requirement: Research Calculations Use Decision-Eligible Data Only
The system SHALL use only verified or alternate-provider real market data for label validation, portfolio weights, intraday score adjustments, and email-triggering holding signals.

#### Scenario: Estimated data exists
- **WHEN** a value is estimated, stale, unavailable, or generated from AI/rule fallback text
- **THEN** it may be displayed with a limitation note but MUST NOT contribute to scores, label validation, portfolio target weight, or email-triggering logic

#### Scenario: Alternate provider real data exists
- **WHEN** a backup provider supplies real market data with valid date, source, and freshness metadata
- **THEN** the system may use it for calculations while recording provider source and reliability level

### Requirement: Data Reliability Is Auditable In Research Outputs
The system SHALL expose data reliability summaries for ranking, label validation, portfolio weights, and tracked-position alerts.

#### Scenario: User views ranking or portfolio
- **WHEN** the UI displays a score, validation result, or portfolio weight
- **THEN** it shows the relevant data date, source, reliability status, and whether missing data limited the calculation

#### Scenario: Email alert is evaluated
- **WHEN** the system evaluates a tracked-position email alert
- **THEN** the alert record includes price source, quote time or NAV date, reliability level, and email eligibility reason

### Requirement: Missing Data Prefers Waiting Over Fake Numbers
The system SHALL prefer waiting states over numeric placeholders when financial data is missing.

#### Scenario: Missing current value
- **WHEN** holdings, tracked positions, or research metrics lack usable price or NAV data
- **THEN** the API returns unavailable or null values with a readable status instead of returning zero, negative 100 percent, or fake fallback values
