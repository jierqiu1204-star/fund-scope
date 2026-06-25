## ADDED Requirements

### Requirement: Short-Term Research Reads Cached Results Efficiently
The system SHALL serve `/short-term` ranking data from the latest completed signal cache without recomputing all assets during page load.

#### Scenario: First page ranking uses cached signal items
- **WHEN** the frontend requests the first page of short-term ETF rankings
- **THEN** the backend returns paginated cached signal items and does not scan all ETF price history to recompute every asset

#### Scenario: Observation portfolio avoids unnecessary full recomputation
- **WHEN** the frontend requests the ETF observation portfolio
- **THEN** the backend uses the latest cached ETF ranking and only loads additional history for shortlisted portfolio candidates

### Requirement: Short-Term Workbench Avoids Redundant Initial Queries
The system SHALL avoid duplicate expensive reads when loading the `/short-term` workbench.

#### Scenario: Workbench loads the same context once
- **WHEN** the workbench needs status, ranking, selected detail, observation portfolio, and tracked holdings
- **THEN** shared context such as the latest signal run and advisor reports is read once per request path or through a lightweight bootstrap endpoint

#### Scenario: Legacy endpoints remain compatible
- **WHEN** existing frontend code calls the current short-term endpoints
- **THEN** the endpoints continue to return compatible response shapes while using optimized internal queries
