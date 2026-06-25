## ADDED Requirements

### Requirement: Multi-Provider Quote Reliability Is Explicit
The system SHALL classify ETF intraday quote reliability using provider freshness, provider agreement, and field completeness before any financial calculation uses the quote.

#### Scenario: Providers agree
- **WHEN** multiple fresh providers return materially consistent ETF prices and usable quote times
- **THEN** the quote reliability is classified as verified or alternate-provider backed and may be used for decision-eligible calculations

#### Scenario: Providers diverge
- **WHEN** fresh providers return ETF prices that differ beyond the configured tolerance
- **THEN** the quote reliability is classified as display-only and the API explains the source disagreement

#### Scenario: Provider fallback time is used
- **WHEN** a provider quote time is inferred from server fetch time instead of provider data
- **THEN** the quote reliability is classified as display-only and MUST NOT drive realtime scoring, portfolio weights, or alert emails

### Requirement: Multi-Provider Metadata Is Exposed To Users
The system SHALL expose concise source and reliability metadata wherever ETF realtime quotes are displayed.

#### Scenario: Realtime quote is displayed
- **WHEN** the UI displays a current ETF intraday price, realtime score, buy-point label, or tracked-position P&L
- **THEN** the API response includes selected source, consensus status, quote time, decision eligibility, and a readable limitation reason when not eligible

#### Scenario: Source disagreement exists
- **WHEN** provider disagreement makes the quote display-only
- **THEN** the UI can show a small data-quality note and MUST NOT label the value as decision-ready realtime data

### Requirement: Provider Failures Do Not Create Fake Fresh Data
The system SHALL prefer unavailable or display-only states over filling realtime ETF values with stale cache or estimated provider output.

#### Scenario: Cache exists but providers fail
- **WHEN** current provider fetches fail but an older cached quote exists
- **THEN** the cached quote may be shown as stale context but MUST NOT be marked fresh or decision-eligible

#### Scenario: Partial provider fields exist
- **WHEN** a provider returns price without required freshness metadata
- **THEN** the system may display the price with limitations but MUST NOT use it for email-triggering logic
