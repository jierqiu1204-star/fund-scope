## ADDED Requirements

### Requirement: Market Research Data Is Shared Across Users
The system SHALL treat ETF and fund market research data as global shared data across approved users.

#### Scenario: Different users view ETF ranking
- **WHEN** two approved users open `/short-term` ETF rankings at the same time
- **THEN** they see the same global market data, rankings, labels, and observation portfolio results, except for user-specific tracked-position overlays

#### Scenario: User-specific overlay exists
- **WHEN** a user has tracked positions for assets shown in the global ranking
- **THEN** the UI may overlay that user's tracking status without changing the underlying global ranking data

### Requirement: User-Specific Data Is Limited To Tracking And Notification Context
The system SHALL keep private user data separate from shared market research outputs.

#### Scenario: User views short-term research
- **WHEN** the user opens `/short-term`
- **THEN** global ETF/fund data comes from shared signal runs, while tracked holdings, alert history, recipient email, and notification state come from the current user context
