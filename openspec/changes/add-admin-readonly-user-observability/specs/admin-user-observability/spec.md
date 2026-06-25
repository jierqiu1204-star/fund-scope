## ADDED Requirements

### Requirement: Super Admin Can View User Overview Read-Only
The system SHALL allow only super administrators to view approved and pending user account overviews in read-only mode.

#### Scenario: Super admin lists users
- **WHEN** a super administrator requests the admin user list
- **THEN** the system returns user id, email, display name, approval status, super-admin status, recipient email, created time, and account status summary without sensitive secrets

#### Scenario: Normal user lists users
- **WHEN** a normal approved user requests the admin user list
- **THEN** the system returns forbidden and does not expose other users

### Requirement: Super Admin Can View User Tracking Snapshot Read-Only
The system SHALL allow only super administrators to view another user's tracked-position snapshot without modifying it.

#### Scenario: Super admin views tracked positions
- **WHEN** a super administrator opens a user's read-only tracking detail
- **THEN** the system returns that user's active and recently closed tracked positions, estimated profit/loss, entry price, current price source, holding status, latest alert reason, and email status

#### Scenario: Read-only detail has no mutation affordance
- **WHEN** a super administrator views another user's tracked positions
- **THEN** the API and UI do not provide edit, close, stop tracking, resend, or mutate actions for those positions

### Requirement: Super Admin Can View Alert And Email Status Read-Only
The system SHALL allow only super administrators to view another user's recent alert and email status without exposing secrets.

#### Scenario: Super admin views user alerts
- **WHEN** a super administrator opens a user's alert history
- **THEN** the system returns alert type, alert reason, asset code, asset name, created time, email status, recipient email, and non-sensitive delivery error summary where available

#### Scenario: SMTP secret is hidden
- **WHEN** a super administrator views a user's notification configuration summary
- **THEN** the system shows configured status and non-sensitive metadata but never returns SMTP authorization code, password hash, JWT, or API keys

### Requirement: Admin Read Access Is Auditable
The system SHALL record or log super administrator read access to another user's account detail.

#### Scenario: User detail is viewed
- **WHEN** a super administrator opens another user's detail page or read-only API
- **THEN** the system records admin user id, target user id, endpoint or action name, and access time in logs or audit storage
