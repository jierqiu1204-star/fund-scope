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

