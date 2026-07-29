## ADDED Requirements

### Requirement: ETF research evidence preserves independent provenance dimensions
ETF research evidence SHALL record `ranking_source_kind`, `policy_mode`, `notification_provenance`, and `execution_provenance` independently and MUST NOT infer a stronger provenance state from another dimension.

#### Scenario: Policy shadow emits a simulated notification
- **WHEN** a replay lifecycle produces a shadow-eligible notification
- **THEN** policy mode is `policy_shadow`, notification provenance is `simulated`, and execution provenance remains `simulated_execution` or `none` as separately observed

#### Scenario: SMTP accepted a live message
- **WHEN** a live notifier records SMTP acceptance without provider receipt or user execution
- **THEN** notification provenance is `smtp_accepted_live`, provider delivery remains unconfirmed, and execution provenance remains `none`

### Requirement: ETF research evidence exposes immutable identity and quality
ETF research evidence SHALL expose the manifest hash, data cutoff, ranking contract, candidate registry, price basis, execution and cost policy, split and holdout identity, coverage dimensions, exclusions, sample counts, uncertainty, and primary or exploratory status used by each result.

#### Scenario: Primary ranking evidence is returned
- **WHEN** the API returns a completed Top10 five-session paired result
- **THEN** it includes common-support coverage, independent dates, costs, bootstrap interval, multiplicity adjustment, drawdown comparison, holdout state, and manifest identity

#### Scenario: Evidence cannot be computed
- **WHEN** a required data, compatibility, coverage, future-window, sample, notification, or execution condition is missing
- **THEN** the API returns a stable unavailable reason instead of a fabricated metric

### Requirement: Production promotion requires separate approval
ETF research evidence SHALL distinguish promotion eligibility from production promotion and MUST NOT mutate the current score contract.

#### Scenario: Every promotion gate passes
- **WHEN** evidence satisfies all pre-registered data, sample, uncertainty, stability, drawdown, concentration, and holdout gates
- **THEN** it may be marked `promotion_eligible` while `final_score_v3` remains active until a separately approved score-version change
