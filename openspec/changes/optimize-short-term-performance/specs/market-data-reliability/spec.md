## ADDED Requirements

### Requirement: Performance Optimizations Preserve Data Reliability Boundaries
The system SHALL distinguish hot raw data, long-term verified data, and display-only derived summaries when optimizing storage and queries.

#### Scenario: Raw intraday data is retained only as hot data
- **WHEN** raw ETF intraday rows exceed the retention window
- **THEN** they are removed only after verified daily data, summaries, signal caches, or holding state needed for research have been preserved

#### Scenario: Old or summarized data cannot trigger live emails
- **WHEN** only daily close data or intraday daily summary data is available
- **THEN** the system does not treat it as fresh intraday data for live email reminders

### Requirement: Query Optimizations Do Not Introduce Misleading Fallbacks
The system SHALL prefer no value or display-only labels over estimated fallback values in optimized query paths.

#### Scenario: Latest fresh quote is unavailable
- **WHEN** optimized live ranking or tracked position queries cannot find a fresh intraday quote
- **THEN** the response marks the data as daily, stale, or unavailable and does not expose it as a live decision-eligible quote

#### Scenario: Summary data is used for display
- **WHEN** daily intraday summary data is shown after raw quote cleanup
- **THEN** the UI or API marks it as summary/display data rather than current live quote data
