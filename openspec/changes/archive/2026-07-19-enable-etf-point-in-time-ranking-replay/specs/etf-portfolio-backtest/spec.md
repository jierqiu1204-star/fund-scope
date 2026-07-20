## ADDED Requirements

### Requirement: Policy Shadow Consumes Immutable Research Ranking Cohorts
ETF portfolio/action backtest SHALL accept only complete immutable replay-ranking cohorts for `policy_mode=policy_shadow`, SHALL use the existing pure action lifecycle and declared simulated execution model, and MUST NOT create production action, tracking, alert, notification, or SMTP state.

#### Scenario: Policy shadow processes a replay date
- **WHEN** a complete `research_replay` cross-section is supplied
- **THEN** the backtest records the replay identities, action-policy identity, simulated fills, costs, action-cycle paths, and research-only notification eligibility

#### Scenario: Replay date is incomplete
- **WHEN** universe, score, or feature coverage does not satisfy the replay contract
- **THEN** policy shadow skips the date with an explicit reason and does not substitute a production, legacy, or partial ranking

#### Scenario: Shadow notification is eligible
- **WHEN** the pure lifecycle would permit a notification
- **THEN** the artifact records `shadow_eligible` and creates no notification item, envelope, SMTP attempt, or audit event in production tables

### Requirement: Policy-Shadow And Observed-Live Results Are Reported Separately
ETF portfolio/action evidence SHALL report full policy-shadow simulated benefit, live-notification-linked sensitivity, and user-confirmed execution outcomes as distinct result groups with independent sample gates.

#### Scenario: Only replay evidence exists
- **WHEN** policy shadow has completed action cycles but no live linked notification or user-confirmed execution sample exists
- **THEN** simulated policy accuracy/benefit is reported while live notification accuracy and confirmed execution return remain unavailable

#### Scenario: Live SMTP-accepted subset exists
- **WHEN** real linked `smtp_accepted_live` notifications have completed future windows
- **THEN** the system may report that subset as a notification sensitivity slice but MUST NOT label it provider-delivered or user-executed without those facts
