## ADDED Requirements

### Requirement: Quote Reliability Uses A Common Vocabulary
The system SHALL expose ETF quote reliability using a shared vocabulary across ranking, tracking, portfolio optimization, validation, and alert audit outputs.

#### Scenario: Fresh consensus quote exists
- **WHEN** multiple providers return fresh consistent ETF quotes
- **THEN** the quote reliability is `fresh_consensus` and may be decision-eligible

#### Scenario: Single fresh quote exists
- **WHEN** only one provider returns a fresh ETF quote and no contradiction is known
- **THEN** the quote reliability is `single_fresh` and may be decision-eligible with a single-provider note

#### Scenario: Quote is not decision eligible
- **WHEN** quotes are stale, diverged, estimated, missing timestamp, unavailable, or display-only
- **THEN** the quote reliability marks the limitation and MUST NOT be used for live email triggers or optimized weights

### Requirement: Daily Reference Cannot Override Intraday Context
The system SHALL keep daily-cache labels and intraday labels separate for ETF realtime views.

#### Scenario: Fresh intraday quote exists
- **WHEN** an ETF has a fresh intraday quote and a stale daily signal cache
- **THEN** realtime score, intraday change, and intraday entry timing use the intraday quote context

#### Scenario: Market is closed
- **WHEN** the market is closed and no fresh intraday quote is expected
- **THEN** the UI labels the result as closed-market or daily reference instead of stale realtime data
