## ADDED Requirements

### Requirement: Users can register with email and password
The system SHALL allow a visitor to create an application account using an email address, password, and optional display name.

#### Scenario: New user registers
- **WHEN** a visitor submits a valid unused email and password to `POST /api/auth/register`
- **THEN** the system creates a user with `is_approved=false`, `is_super_admin=false`, `recipient_email` equal to the registered email, and does not issue an access token

#### Scenario: Duplicate email is rejected
- **WHEN** a visitor submits an email already used by another account
- **THEN** the system rejects the registration with a readable Chinese error and does not create a duplicate user

### Requirement: Approved users can log in
The system SHALL issue a 30-day bearer token only to approved users with valid credentials.

#### Scenario: Approved user logs in
- **WHEN** an approved user submits the correct email and password to `POST /api/auth/login`
- **THEN** the system returns a JWT access token, expiration time, and the current user summary

#### Scenario: Pending user cannot log in
- **WHEN** an unapproved user submits correct credentials
- **THEN** the system returns a forbidden response explaining that the account is waiting for administrator approval and does not issue a token

#### Scenario: Invalid credentials are rejected
- **WHEN** a user submits an unknown email or wrong password
- **THEN** the system returns an unauthorized response without revealing which field was wrong

### Requirement: Authenticated sessions expose the current user
The system SHALL provide an authenticated endpoint for the frontend to restore login state and determine user permissions.

#### Scenario: Current user is requested with a valid token
- **WHEN** the frontend calls `GET /api/auth/me` with a valid bearer token
- **THEN** the system returns the current user's id, email, display name, approval status, super-admin status, and recipient email

#### Scenario: Current user is requested without a token
- **WHEN** the frontend calls a protected API without a bearer token
- **THEN** the system returns an unauthorized response and the frontend redirects to the login page

### Requirement: Super admin can approve users
The system SHALL allow only super administrators to approve or disable user accounts.

#### Scenario: Super admin views users
- **WHEN** qje or another super administrator opens the user approval page
- **THEN** the system lists pending and approved users with email, display name, approval status, super-admin status, and creation time

#### Scenario: Super admin approves a pending user
- **WHEN** a super administrator approves a pending user
- **THEN** the user becomes able to log in and use approved-user features

#### Scenario: Non-admin cannot approve users
- **WHEN** a normal approved user calls a user approval API
- **THEN** the system returns forbidden and does not change the target user

### Requirement: qje bootstrap admin exists
The system SHALL ensure the qje account is approved, marked as super administrator, and bound to the configured owner email during deployment.

#### Scenario: Bootstrap env is configured
- **WHEN** the application starts with bootstrap admin email, display name, and password environment variables
- **THEN** the system creates or updates the qje super-admin account with a password hash and `recipient_email=19535838578@163.com`

#### Scenario: Existing User 1 is migrated
- **WHEN** the auth migration runs on an existing database
- **THEN** the existing owner user is marked approved and super-admin, and existing tracked positions can be assigned to that user

### Requirement: Frontend protects business pages
The frontend SHALL require application login for business pages and SHALL show administrator entry points only to super administrators.

#### Scenario: Anonymous user opens a business page
- **WHEN** an anonymous visitor opens `/short-term`, `/strategy-lab`, `/settings/notifications`, or `/admin/jobs`
- **THEN** the frontend redirects the visitor to `/login`

#### Scenario: Pending user opens a business page
- **WHEN** a logged-in but unapproved user opens a business page
- **THEN** the frontend shows a waiting-for-approval page and does not load protected business data

#### Scenario: Super admin sees approval entry
- **WHEN** qje is logged in
- **THEN** the navigation includes a user approval entry, while normal approved users do not see that entry

### Requirement: Deployment no longer relies on Basic Auth
The deployed web server SHALL route browser and API requests without nginx Basic Auth, relying on application authentication for access control.

#### Scenario: Domain or IP is opened after deployment
- **WHEN** a visitor opens the site by IP or configured domain
- **THEN** nginx serves the frontend without a Basic Auth prompt and the application login page controls access
