## ADDED Requirements

### Requirement: Tracked Position Lists Use Batched Context Reads
The system SHALL build tracked position lists with batched reads for prices, alerts, short-term signal context, advisor reports, and recent intraday alerts.

#### Scenario: User has multiple tracked positions
- **WHEN** a user opens the tracked holdings section with multiple active positions
- **THEN** the backend does not repeat the same latest signal run, signal item list, advisor report list, and latest alert queries for every position

#### Scenario: Detail endpoint can load full history
- **WHEN** a user opens one tracked position detail
- **THEN** the backend may load full chart and full alert history for that single position

### Requirement: Holding High-Water State Survives Intraday Quote Cleanup
The system SHALL persist each tracked holding's high-water profit and dynamic exit context independently from raw intraday quote retention.

#### Scenario: Raw intraday rows are cleaned
- **WHEN** old raw intraday quote rows are removed
- **THEN** existing tracked positions still retain their max profit, giveback context, and dynamic line state needed for future reminders

#### Scenario: Reminder evaluation uses fresh data
- **WHEN** a tracked ETF is evaluated during market hours
- **THEN** reminder decisions use fresh intraday quotes and persisted holding state rather than deleted or stale raw quote rows
