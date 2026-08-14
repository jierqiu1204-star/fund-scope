## ADDED Requirements

### Requirement: Tracked Position Exit Strategy Routes By Persisted Policy
The system SHALL route each tracked-position evaluation through the position's persisted alert policy and SHALL NOT combine triggers from different policies in one decision.

#### Scenario: Default policy position is evaluated
- **WHEN** a tracked position uses the current dynamic holding policy
- **THEN** the existing hard-stop, profit-protection, trend-weakening, and data-eligibility behavior remains in effect

#### Scenario: Late-day policy position is evaluated
- **WHEN** an ETF tracked position uses `late_day_turnaround_t1_v1`
- **THEN** the generic dynamic policy does not independently emit a competing sell or reduce email for that evaluation

#### Scenario: Leader policy position is evaluated
- **WHEN** an ETF or A-share tracked position uses `leader_tactics_exit_v1`
- **THEN** only its adjusted daily-close stop contract is evaluated and the generic dynamic and late-day policies do not emit competing emails

#### Scenario: Policy is changed explicitly
- **WHEN** the owner changes a tracked position to another supported alert policy
- **THEN** policy-specific high-water and lifecycle state is reset, the change is auditable, and evaluation starts under the new version without reusing incompatible state

### Requirement: Alert Policy Choice Is Owner Scoped
The system SHALL allow only the tracked-position owner to select or change its alert policy.

#### Scenario: Another user attempts policy update
- **WHEN** a user attempts to change the policy on a tracked position owned by someone else
- **THEN** the system denies access and does not expose or mutate the other user's policy or provenance

### Requirement: Leader Exit Uses The Highest Active Protection Line
The system SHALL freeze the leader entry risk from eligible adjusted evidence and SHALL emit a full-exit decision when the latest eligible adjusted close reaches the highest active value among the immutable disaster stop, an armed one-R breakeven floor, and same-session adjusted MA5.

#### Scenario: Trend breaks while the holding remains profitable
- **WHEN** the adjusted close remains above the entry reference but closes at or below the adjusted MA5
- **THEN** the system emits a leader MA5 full-exit signal rather than waiting for an account loss

#### Scenario: One-R profit arms breakeven
- **WHEN** the adjusted closing high reaches one immutable entry risk unit
- **THEN** the round-trip-cost breakeven floor remains armed for that position episode and cannot move down

#### Scenario: Adjusted evidence is unavailable
- **WHEN** the required entry reference, ATR20, MA5, provider, receipt cutoff, revision, or common adjustment basis is unavailable or invalid
- **THEN** the system reports data waiting and sends no actionable email
