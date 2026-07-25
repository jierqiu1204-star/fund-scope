## MODIFIED Requirements

### Requirement: ETF Data Sync Uses Provider Fallback

The system SHALL use only configured provenance-valid adjusted providers for
publication and research-depth synchronization and SHALL reject raw-only or
version-incompatible rows without increasing decision coverage.

#### Scenario: Adjusted fallback fills a provider failure

- **WHEN** the first adjusted provider times out, fails, or returns no valid
  adjusted rows
- **THEN** the bounded provider chain may try the next configured adjusted
  provider within its six-second per-attempt and slice deadlines

#### Scenario: Primary adjusted history is shallower than the lane target

- **WHEN** the first provider returns valid adjusted rows but fewer unique
  sessions than the 61, 300, or 500-session lane requests
- **THEN** the provider chain continues to eligible adjusted fallbacks and uses
  the first response meeting the target, or the deepest valid partial response
  when every provider is short

#### Scenario: Raw fallback is available

- **WHEN** Sina, raw efinance, intraday, estimated, or display-only prices exist
- **THEN** those rows do not satisfy 61, 300, or 500 adjusted-session depth

#### Scenario: A response mixes valid and invalid rows

- **WHEN** one provider response contains both provenance-valid adjusted rows
  and rows with an incompatible version or price basis
- **THEN** only the individually valid rows are returned to persistence

#### Scenario: Provider history is factually short

- **WHEN** an accepted adjusted provider returns fewer eligible sessions than
  requested
- **THEN** the system persists the observed boundaries and retry-after without
  inferring a listing date or removing the ETF from the coverage denominator

#### Scenario: A different history lane is active

- **WHEN** any 61, 300, or 500-session history worker holds a live lease
- **THEN** another history lane cannot start provider work
