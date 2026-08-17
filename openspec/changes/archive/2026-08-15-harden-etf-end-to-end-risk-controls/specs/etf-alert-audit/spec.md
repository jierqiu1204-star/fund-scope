## ADDED Requirements

### Requirement: Alert audit separates legacy production and V2 shadow evidence
The system SHALL keep production alert results and V2 lifecycle shadow results distinguishable while linking both to the same owner-scoped position and immutable input snapshot.

#### Scenario: Legacy and shadow evaluations both complete
- **WHEN** a scheduled evaluation produces a legacy result and a V2 shadow result
- **THEN** the audit evidence identifies both policy versions, data eligibility, input snapshot identity, rule state, recommended absolute target if any, notification side effects, and whether their outcomes differ

#### Scenario: Shadow mode is disabled
- **WHEN** operators disable V2 shadow evaluation through the declared rollback switch
- **THEN** legacy alert evaluation continues unchanged and the job summary reports shadow evaluation as disabled rather than failed

### Requirement: Alert audit records execution provenance without inferring fills
The system SHALL record signal, notification, simulated-fill, owner-confirmed, and broker-confirmed provenance as separate facts.

#### Scenario: Email is accepted by SMTP
- **WHEN** an alert email is accepted by SMTP
- **THEN** the audit records the notification outcome but MUST NOT create owner-confirmed or broker-confirmed execution provenance

#### Scenario: Execution-risk evidence is incomplete
- **WHEN** spread, executable-side price, gap, or slippage evidence is unavailable
- **THEN** the audit records a stable unavailable reason and does not fill the missing value with zero, a daily-close fallback, or an estimated trade
