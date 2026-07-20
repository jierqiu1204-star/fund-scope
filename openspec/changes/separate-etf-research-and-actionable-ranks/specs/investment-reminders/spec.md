## MODIFIED Requirements

### Requirement: Reminder emails require decision-eligible data
Investment reminders SHALL NOT send emails from stale, estimated, fallback-only, display-only, research-only, or otherwise action-ineligible data.

#### Scenario: Data is display-only
- **WHEN** a tracked ETF only has display-only data
- **THEN** the system records a webpage-only status and does not send an email

#### Scenario: Candidate was selected by research rank
- **WHEN** a workflow proposes an email because an ETF belongs to a research-ranked candidate set
- **THEN** the system sends no email unless the ETF has an eligible same-context `actionable_rank_v1` row and records the suppression reason

#### Scenario: Actionable rank context does not match
- **WHEN** the candidate's actionable rank hash, as-of session, or required source context differs from the workflow context
- **THEN** the system fails closed and records a version or context mismatch

#### Scenario: Independent tracked-position risk alert is actionable
- **WHEN** an existing tracked position triggers its independent risk lifecycle using fresh decision-eligible quote and holding state
- **THEN** missing research-rank membership alone does not suppress the alert
