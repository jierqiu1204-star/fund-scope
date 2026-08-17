# etf-alert-audit Specification

## Purpose
TBD - created by archiving change enhance-etf-evidence-portfolio-alert-audit. Update Purpose after archive.
## Requirements

### Requirement: Tracked position alert audit trail
The system SHALL record an auditable decision trail for tracked-position alert evaluations without changing the existing alert decision logic.

#### Scenario: Email sent audit
- **WHEN** a tracked-position evaluation sends an email reminder
- **THEN** the audit trail records the tracked position, alert type, data source, price freshness, threshold context, SMTP result, recipient, and sent timestamp

#### Scenario: Web-only audit
- **WHEN** a tracked-position evaluation produces a web-only note
- **THEN** the audit trail records why no email was sent and labels the outcome as web-only

#### Scenario: Suppressed duplicate audit
- **WHEN** a tracked-position evaluation suppresses a duplicate intraday signal
- **THEN** the audit trail records the duplicate window or cooldown reason without creating a misleading email event

### Requirement: Alert audit read API
The system SHALL expose alert audit history for the owner of a tracked position.

#### Scenario: Owner reads audit history
- **WHEN** an approved user requests audit history for their tracked position
- **THEN** the system returns ordered audit events with human-readable Chinese reasons and raw structured context

#### Scenario: Cross-user access denied
- **WHEN** a user requests audit history for another user's tracked position
- **THEN** the system denies access

### Requirement: Alert audit UI explanation
The UI SHALL explain why an email was sent, skipped, suppressed, or kept as a webpage-only提示.

#### Scenario: Beginner-facing audit summary
- **WHEN** a tracked position has recent audit events
- **THEN** the UI shows a short Chinese summary first and keeps technical fields expandable

### Requirement: Alert audit records owner risk and capacity context
The alert audit SHALL record the owner risk state, sleeve evidence identity, capital confirmation, liquidity capacity and whether the state changed the proposed action.

#### Scenario: Add is blocked by owner risk
- **WHEN** an otherwise eligible add or reentry recommendation is blocked by `reduce_only` or `data_halt`
- **THEN** the audit records original action, final action, state, trigger reasons, NAV/source hash and policy version

#### Scenario: Exit has poor capacity
- **WHEN** an exit signal remains valid but liquidity capacity is stressed or unavailable
- **THEN** the audit preserves the exit signal and records spread, participation, estimated liquidation time and unavailable reasons without inferring a fill

### Requirement: Risk context is reused within one owner evaluation run
The system SHALL build owner aggregate risk context once per bounded evaluation run and reuse it for every position in that run.

#### Scenario: Owner has many tracked positions
- **WHEN** a list or scheduled workflow evaluates multiple positions for one owner
- **THEN** owner-level NAV/state/stop aggregates are loaded in batches and do not issue the same aggregate queries once per position

### Requirement: ETF profit-protection decisions are replayable

The alert audit SHALL record the adjusted-data eligibility, sample count, risk-unit source, armed state, persisted high-water, previous protection line, effective protection line and distance to trigger.

#### Scenario: Profit protection is unavailable
- **WHEN** the required adjusted history is unavailable or ineligible
- **THEN** the audit and UI expose a stable unavailable reason and no actionable trailing-profit email is generated

#### Scenario: Profit protection is evaluated
- **WHEN** a tracked ETF is evaluated with eligible adjusted history
- **THEN** the saved context contains enough versioned inputs and state to reproduce why the line held, rose or triggered

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
