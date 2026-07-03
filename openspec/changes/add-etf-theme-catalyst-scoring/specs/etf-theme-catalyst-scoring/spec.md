## ADDED Requirements

### Requirement: ETF Theme Catalysts Are Structured And Auditable
The system SHALL store ETF theme catalyst inputs as structured research events with theme, catalyst type, title, source, event date, effective window, direction, strength, confidence, status, and source type.

#### Scenario: Active manual catalyst is stored
- **WHEN** an operator seeds a robotics catalyst such as a Unitree IPO event
- **THEN** the system records the event with a theme key, event date, source reference, active status, strength score, confidence score, and expiry date

#### Scenario: Expired catalyst is ignored for scoring
- **WHEN** a catalyst event's effective window has expired
- **THEN** the system excludes or materially decays that event from the active theme catalyst score

#### Scenario: Missing source limits scoring
- **WHEN** a catalyst event has no verifiable source or event date
- **THEN** the system MUST NOT use it to raise any ETF opportunity score

### Requirement: Theme Catalyst Snapshots Are Generated Per Theme
The system SHALL generate theme-level catalyst snapshots that summarize active events into `catalyst_score`, `sentiment_heat_score`, event count, key summaries, limitations, and generation metadata.

#### Scenario: Theme has active catalysts
- **WHEN** a theme has one or more active high-confidence events
- **THEN** the snapshot includes a catalyst score above neutral, a concise summary, event count, and the most important supporting events

#### Scenario: Theme has no catalysts
- **WHEN** a theme has no active or verified catalyst events
- **THEN** the snapshot returns a neutral or unavailable catalyst state and MUST NOT fabricate a bullish or bearish theme narrative

#### Scenario: Snapshot records limitations
- **WHEN** catalyst evidence is manual, incomplete, stale, or based on proxy themes
- **THEN** the snapshot records limitations that can be shown in API responses and the UI

### Requirement: Opportunity Score Combines Technical And Catalyst Inputs
The system SHALL calculate ETF `opportunity_score` separately from the existing short-term `total_score` using the default formula `technical_score * 0.70 + catalyst_score * 0.20 + sentiment_heat_score * 0.10`.

#### Scenario: Strong catalyst lifts attention but not technical score
- **WHEN** an ETF has `total_score` 70, `catalyst_score` 90, and `sentiment_heat_score` 80
- **THEN** the system calculates an `opportunity_score` around 76 and leaves the ETF's `total_score` unchanged

#### Scenario: No catalyst data exists
- **WHEN** an ETF's theme has no catalyst snapshot
- **THEN** the system keeps deterministic short-term ranking usable and returns a neutral or unavailable catalyst component without failing the signal run

#### Scenario: Weight version is recorded
- **WHEN** opportunity scoring is applied
- **THEN** the system records the score formula version and component weights in the ETF's opportunity breakdown

### Requirement: Catalyst Inputs Cannot Override Risk Or Data Gates
The system SHALL keep data reliability, liquidity, entry timing, and risk labels independent from theme catalyst scoring.

#### Scenario: ETF is chase-risk despite strong catalyst
- **WHEN** an ETF has a strong catalyst score and `entry_timing_label` is `冲高别追`
- **THEN** the system may mark it as high attention but MUST preserve `冲高别追` and MUST NOT present it as suitable for chasing

#### Scenario: ETF data is ineligible
- **WHEN** an ETF has stale, unavailable, estimated, insufficient, or otherwise decision-ineligible market data
- **THEN** catalyst scoring MUST NOT raise the ETF into a decision-ready state

#### Scenario: ETF liquidity is insufficient
- **WHEN** an ETF has low liquidity or abnormal premium/discount risk
- **THEN** catalyst scoring MUST NOT remove the liquidity or premium risk limitation

### Requirement: AI May Extract Candidates But Cannot Directly Score
The system SHALL allow an external AI model only to summarize or extract candidate catalyst events, and SHALL NOT use unverified AI prose as a direct scoring, label, allocation, or alert input.

#### Scenario: AI extracts event candidate
- **WHEN** AI extracts a catalyst from news text
- **THEN** the candidate is stored as pending or unverified until a deterministic rule or human review confirms source, date, theme, strength, and confidence

#### Scenario: AI output is unverified
- **WHEN** AI output lacks a verifiable source or conflicts with structured data
- **THEN** the system excludes it from active catalyst scoring and records the limitation

#### Scenario: AI explanation is displayed
- **WHEN** the UI shows an AI-assisted explanation
- **THEN** the system states that AI explains structured evidence and does not change scores, labels, weights, or alerts

### Requirement: Proxy Theme Coverage Is Explicit
The system SHALL explicitly identify when a user-requested theme is represented by proxy ETFs rather than exact-name ETFs.

#### Scenario: Exact ETF theme exists
- **WHEN** an ETF's theme taxonomy directly matches robotics or semiconductor themes
- **THEN** catalyst scoring applies the matching theme snapshot directly

#### Scenario: Exact optical-module ETF is unavailable
- **WHEN** no ETF directly matches optical module, CPO, or optical communication
- **THEN** the system may map related communication, 5G, or information-technology ETFs as proxy themes and MUST display the proxy limitation

#### Scenario: Proxy theme is used in score
- **WHEN** a proxy theme contributes catalyst score to an ETF
- **THEN** the opportunity breakdown identifies the proxy theme and lowers or qualifies confidence
