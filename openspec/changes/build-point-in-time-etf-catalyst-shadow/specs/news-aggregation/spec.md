## ADDED Requirements

### Requirement: ETF Catalyst News Ingestion Records Source Receipts
News ingestion used for ETF catalyst research SHALL preserve immutable source receipts with publication and first-received timestamps, content hashes, parser versions, canonical source identity, and fetch outcome.

#### Scenario: Catalyst-capable news item is fetched
- **WHEN** an approved official or public source item is ingested for ETF catalyst research
- **THEN** the news item references an immutable source receipt that can reconstruct when FundScope first possessed that content

#### Scenario: Content changes at the source
- **WHEN** a previously ingested item is corrected or updated
- **THEN** ingestion stores a new receipt version and retains the earlier content hash and timeline

### Requirement: ETF Catalyst News Ingestion Distinguishes Empty And Unavailable
News ingestion used for ETF catalyst research SHALL distinguish successful-empty source observations from source failures and inapplicable policies.

#### Scenario: Source has no qualifying items
- **WHEN** a required source fetch succeeds for a session and returns no qualifying event items
- **THEN** ingestion records a successful-empty observation eligible for `observed_none`

#### Scenario: Source fetch fails
- **WHEN** a required source fetch times out or fails
- **THEN** ingestion records `unavailable` with a reproducible error summary and MUST NOT report a successful-empty observation

#### Scenario: Source does not apply
- **WHEN** the versioned source/theme policy marks a source inapplicable
- **THEN** ingestion records `not_applicable` with the policy identifier
