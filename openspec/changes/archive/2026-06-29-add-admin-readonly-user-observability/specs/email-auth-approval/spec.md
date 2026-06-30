## ADDED Requirements

### Requirement: User Approval List Links To Read-Only Details
The super-admin user management page SHALL provide a read-only detail entry for each user without changing approval behavior.

#### Scenario: Super admin opens user management
- **WHEN** qje or another super administrator opens the user management page
- **THEN** each listed user has a read-only detail link or button in addition to existing approval controls

#### Scenario: Approval controls remain scoped
- **WHEN** a super administrator approves or disables a user
- **THEN** only account approval/status changes are allowed, and tracked holdings or notification settings are not modified

### Requirement: Admin User Responses Hide Secrets
The user management API SHALL hide authentication and SMTP secrets in all super-admin responses.

#### Scenario: User list is returned
- **WHEN** the system returns users to a super administrator
- **THEN** the response excludes password hash, SMTP password, tokens, API keys, and any raw secret fields
