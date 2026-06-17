## ADDED Requirements

### Requirement: Tracked positions are scoped to their owner
The system SHALL bind every tracked position to a user and SHALL only expose tracked positions to their owner.

#### Scenario: User lists tracked positions
- **WHEN** an approved user calls `GET /api/tracked-positions`
- **THEN** the system returns only tracked positions where `user_id` equals the current user's id

#### Scenario: User creates tracked position
- **WHEN** an approved user creates a tracked position
- **THEN** the system stores the current user's id on the tracked position

#### Scenario: User accesses another user's tracked position
- **WHEN** an approved user tries to read, update, or close a tracked position owned by another user
- **THEN** the system returns not found or forbidden and does not expose the other user's data

#### Scenario: Existing tracked positions are migrated
- **WHEN** the user-scoping migration runs on an existing database
- **THEN** all existing tracked positions are assigned to the qje owner account

### Requirement: Tracked position emails use owner recipient
The system SHALL send actionable tracked-position emails to the recipient email configured by the tracked position owner.

#### Scenario: Owner has configured recipient email
- **WHEN** a tracked position triggers `hard_stop`, `trailing_take_profit`, `trend_weakening`, or `exit_watch`
- **THEN** the system sends the email to that position owner's `recipient_email` and records the recipient in the alert or notification log

#### Scenario: Owner email is not configured
- **WHEN** a tracked position triggers an actionable signal but the owner has no usable recipient email or SMTP channel
- **THEN** the system records the alert with a failed or skipped email status and continues evaluating other users' positions

#### Scenario: Data-only warning is created
- **WHEN** a tracked position has a data-quality warning without an actionable exit signal
- **THEN** the system keeps the warning web-only and does not email the owner
