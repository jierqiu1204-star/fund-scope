## MODIFIED Requirements

### Requirement: Tracked Position Exit Strategy Uses Explicit Action Classes
The system SHALL expose tracked-position exit decisions with explicit action classes so that actionable exits, soft warnings, guard-only states, data waiting states, and research-only evidence are not confused.

#### Scenario: Actionable exit signal
- **WHEN** a tracked ETF position triggers an approved live `hard_stop`, `trailing_take_profit`, or confirmed `exit_watch`
- **THEN** the API response SHALL expose an actionable exit action class and the email workflow MAY send a user notification according to cooldown rules

#### Scenario: Guard-only signal
- **WHEN** a tracked ETF position only triggers unconfirmed `trend_weakening`
- **THEN** the API response SHALL expose a guard-only action class and SHALL NOT send a standalone sell email

#### Scenario: Research-only optimized parameter
- **WHEN** an optimized exit parameter exists but is not approved or does not match the current evidence contract
- **THEN** the tracked-position response SHALL keep using current live defaults and SHALL expose the optimized parameter only as research evidence

#### Scenario: Data-ineligible price
- **WHEN** the latest price is stale, display-only, estimated, or otherwise not decision eligible
- **THEN** the tracked-position response SHALL block actionable email triggering and explain the no-alert reason
