## ADDED Requirements

### Requirement: Catalyst Sources And Receipts Are Auditable
The system SHALL maintain a versioned registry of official and approved public catalyst sources and immutable receipts for every bounded fetch outcome.

#### Scenario: Source item is received
- **WHEN** a registered source returns an announcement or article
- **THEN** the system records source ID, external ID when available, canonical URL, source-published time, server first-received time, fetch time, content hash, raw-content reference, parser version, and fetch state

#### Scenario: Identical item is fetched again
- **WHEN** the same source identity and content hash are observed again
- **THEN** the system reuses the immutable receipt and does not create a duplicate event version

#### Scenario: Source item is corrected
- **WHEN** the same external item is fetched with changed content
- **THEN** the system creates a new receipt version with correction lineage and preserves the earlier version

#### Scenario: Source is not allowlisted
- **WHEN** content is discovered from an unregistered source
- **THEN** it cannot become a verified catalyst receipt

### Requirement: Catalyst Events Are Structured Without Scores
The system SHALL normalize supported receipts into versioned event facts with stable event ID, event type, entities, title, supporting receipts, times, effective period, categorical direction, direct and proxy theme mappings, taxonomy version, extraction method, verification state, and supersession lineage.

#### Scenario: Verified event is created
- **WHEN** an event has registered source evidence, valid dates, supported entities, and a valid theme mapping
- **THEN** the system may mark that event version `verified`

#### Scenario: Event direction is recorded
- **WHEN** a verified source supports a positive, negative, neutral, or uncertain event classification
- **THEN** the direction is stored as a categorical research fact and no strength score or rank delta is created

#### Scenario: Scoring field is requested
- **WHEN** catalyst processing attempts to produce strength, catalyst score, sentiment heat, opportunity score, fixed weight, or rank adjustment
- **THEN** the system rejects that output from the shadow contract

### Requirement: Catalyst Snapshots Are Point-In-Time Reconstructable
The system SHALL generate immutable shadow snapshots using only event versions and receipts that FundScope possessed by the requested cutoff.

#### Scenario: Event was received before cutoff
- **WHEN** both the source-published time and first-received time are no later than cutoff T and the event is effective at T
- **THEN** the event version may appear in the snapshot for T

#### Scenario: Old event was discovered late
- **WHEN** an article published before T was first received after T
- **THEN** it MUST NOT appear in the snapshot for T

#### Scenario: Event was later corrected
- **WHEN** a corrected receipt version is first received after T
- **THEN** the snapshot for T retains the version known at T and identifies later correction only outside the historical evidence

#### Scenario: Snapshot is regenerated
- **WHEN** the same cutoff, source receipt set, taxonomy version, and contract version are used
- **THEN** the system produces the same snapshot hash

### Requirement: Catalyst Coverage States Distinguish Silence From Failure
The system SHALL record theme/session catalyst coverage as `active`, `observed_none`, `unavailable`, or `not_applicable` with source-level counts and reasons.

#### Scenario: Active verified event exists
- **WHEN** at least one verified direct event is effective for the theme and cutoff
- **THEN** the theme/session state is `active`

#### Scenario: Sources were checked successfully
- **WHEN** all required applicable sources were fetched successfully and no qualifying event exists
- **THEN** the state is `observed_none`

#### Scenario: Required source failed
- **WHEN** one or more required applicable sources time out, fail, or are not observed
- **THEN** the state is `unavailable` and MUST NOT be converted to a neutral or no-event state

#### Scenario: Policy excludes observation
- **WHEN** the versioned source/theme policy declares the observation inapplicable
- **THEN** the state is `not_applicable` with the policy identifier

### Requirement: AI Extraction Has No Decision Authority
The system SHALL limit AI to bounded extraction, normalization, classification, and summarization of registered source receipts and SHALL NOT let model output directly affect ranking, allocation, alerts, or notifications.

#### Scenario: AI extracts an event candidate
- **WHEN** the model returns schema-valid fields with receipt citations
- **THEN** the candidate is stored as `pending` until required deterministic checks and configured review pass

#### Scenario: AI output lacks source support
- **WHEN** the model invents a date, entity, direction, or theme mapping not supported by cited receipts
- **THEN** the candidate remains unverified and is excluded from verified snapshots and event studies

#### Scenario: AI service fails
- **WHEN** extraction times out or returns invalid output
- **THEN** the raw receipt remains stored, the error is auditable, and no fallback event is fabricated

### Requirement: Manual And Proxy Catalyst Evidence Is Explicit
The system SHALL label manual seed events and proxy-theme mappings explicitly and SHALL keep them outside direct verified evidence unless they independently satisfy the registered receipt and mapping policy.

#### Scenario: Legacy manual seed has no receipt
- **WHEN** an existing seeded catalyst lacks a registered immutable source receipt
- **THEN** it is migrated as `manual_display_only` and cannot enter a verified snapshot or event study

#### Scenario: Event maps through a proxy theme
- **WHEN** a verified event is related to an ETF only through a proxy theme mapping
- **THEN** the UI and evidence record show the proxy taxonomy version and limitation

#### Scenario: Proxy cohort is studied
- **WHEN** a pre-registered event study explicitly declares a proxy mapping cohort
- **THEN** proxy outcomes are reported separately from direct-theme outcomes and cannot be merged silently

### Requirement: Catalyst Event Studies Are Pre-Registered And Research-Only
The system SHALL evaluate verified catalyst events only through immutable point-in-time event-study manifests and SHALL keep results separate from production ranking and action workflows.

#### Scenario: Event study is registered
- **WHEN** a study is created
- **THEN** it freezes event types, direct or proxy cohorts, controls, execution convention, 1/3/5/10-session horizons, costs, exclusions, splits, uncertainty method, and multiplicity policy before outcomes

#### Scenario: Verified event outcome is evaluated
- **WHEN** a completed event has decision-eligible adjusted future prices and a valid matched control
- **THEN** the study records observed return, matched excess return, adverse excursion, favorable excursion, coverage, and limitations

#### Scenario: Event evidence is insufficient
- **WHEN** verified direct-event samples or matched controls do not meet the frozen minimum
- **THEN** the result is `insufficient_data` and no catalyst formula or weight is inferred

#### Scenario: Event study appears favorable
- **WHEN** an event cohort has positive out-of-sample results
- **THEN** the system reports research evidence only and requires a separate proposal before any score, cap, weight, allocation, alert, or email change

### Requirement: Catalyst Shadow Cannot Change Ranking Or Actions
The system SHALL persist catalyst shadow data in a separate contract and SHALL NOT use it as an input to research score, actionable score, ranking eligibility, portfolio weights, tracked-position signals, or email triggers.

#### Scenario: Strong-looking event is active
- **WHEN** an ETF has an active verified catalyst event
- **THEN** its research and actionable scores and ranks remain identical to results calculated without the shadow

#### Scenario: Catalyst coverage is unavailable
- **WHEN** source coverage is `unavailable`
- **THEN** the system shows the limitation but does not lower, raise, or fabricate ranking evidence

### Requirement: Catalyst Processing Is Bounded And Cached
The system SHALL fetch and process catalyst sources with one worker, bounded item batches, idempotent cursors, exclusive run locking, and hard operation timeouts of at most 55 seconds.

#### Scenario: Source fetch times out
- **WHEN** a source reaches its timeout
- **THEN** the run records one reproducible unavailable reason, persists its cursor, and does not start a concurrent retry loop

#### Scenario: Workbench requests catalyst context
- **WHEN** `/short-term` loads an ETF detail
- **THEN** it reads the latest completed cached shadow snapshot without external fetch or AI inference
