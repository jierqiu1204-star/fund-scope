## ADDED Requirements

### Requirement: Admin Read-Only Tracking Does Not Break Owner Isolation
The system SHALL keep normal tracked-position owner isolation while allowing super-admin read-only access through dedicated admin endpoints.

#### Scenario: Owner uses normal endpoint
- **WHEN** a normal user calls tracked-position APIs
- **THEN** the system returns or mutates only positions owned by that user

#### Scenario: Super admin uses read-only endpoint
- **WHEN** a super administrator calls an admin read-only tracked-position endpoint for another user
- **THEN** the system returns that user's tracking snapshot without allowing mutation

#### Scenario: Super admin attempts normal mutation on another user's position
- **WHEN** a super administrator calls normal tracked-position write endpoints for a position owned by another user
- **THEN** the system rejects the mutation unless an explicit future admin-write capability exists

### Requirement: Admin Tracking Snapshot Reuses Existing Calculation Semantics
The admin read-only tracking snapshot SHALL use the same estimate, price-source, data reliability, and alert status calculations as the owner view.

#### Scenario: Super admin views user tracking
- **WHEN** the admin tracking snapshot is generated
- **THEN** estimated profit/loss, holding signal, latest alert, and data quality status match what the target user would see for the same position
