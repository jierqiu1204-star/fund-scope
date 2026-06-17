## ADDED Requirements

### Requirement: Notification settings are scoped to the current user
The system SHALL read and write notification settings for the authenticated user rather than a global default user.

#### Scenario: User reads notification settings
- **WHEN** an approved user opens `/settings/notifications`
- **THEN** the system returns that user's recipient email, reminder preferences, and SMTP metadata

#### Scenario: User updates recipient email
- **WHEN** an approved user changes the notification recipient email
- **THEN** the system persists the change only for that user and does not modify other users' recipient emails

### Requirement: Notification admin jobs require super admin
The system SHALL allow only super administrators to manually run global notification and data jobs from admin endpoints.

#### Scenario: Super admin runs a reminder job
- **WHEN** qje calls an admin notification job endpoint with a valid token
- **THEN** the system runs the job and records the job result

#### Scenario: Normal user is blocked from admin job
- **WHEN** a normal approved user calls an admin job endpoint
- **THEN** the system returns forbidden and does not run the job

### Requirement: Monthly reminders use each user's recipient email
The system SHALL send scheduled user-facing reminders to each relevant user's configured recipient email.

#### Scenario: Multiple users have notification settings
- **WHEN** a scheduled reminder job sends user-facing reminders
- **THEN** each email is addressed to the owning user's configured recipient email and logged with that recipient
