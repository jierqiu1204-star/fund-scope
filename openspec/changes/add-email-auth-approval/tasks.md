## 1. Data Model And Configuration

- [x] 1.1 Add backend auth settings for JWT secret, token expiry days, and bootstrap qje admin env values.
- [x] 1.2 Add password hashing and JWT support using the Python standard library without new backend dependencies.
- [x] 1.3 Add Alembic migration fields on `users`: password hash, display name, approval flag, super-admin flag, last login time.
- [x] 1.4 Add `tracked_positions.user_id`, backfill all existing tracked positions to qje, and add an owner/status index.
- [x] 1.5 Add startup bootstrap logic that creates or updates qje as approved super admin using env-provided password and recipient email.

## 2. Backend Authentication And Authorization

- [x] 2.1 Implement auth service utilities for password hashing, password verification, JWT creation, and JWT decoding.
- [x] 2.2 Implement current-user dependencies: authenticated user, approved user, and super admin.
- [x] 2.3 Add auth schemas and routes for register, login, current user, and logout/no-op session cleanup.
- [x] 2.4 Add admin user-management schemas and routes for listing users and approving or disabling users.
- [x] 2.5 Protect admin routes and manual job endpoints with super-admin authorization.

## 3. User-Scoped Tracking And Notifications

- [x] 3.1 Update tracked-position list, create, detail, update, and close routes to require approved user and filter by owner.
- [x] 3.2 Update tracked-position service creation path to persist the current user's id.
- [x] 3.3 Update alert creation and intraday/daily tracked-position jobs to load the tracked position owner and send to that owner's recipient email.
- [x] 3.4 Update notification settings routes to read and write the current user's settings instead of `User(1)`.
- [x] 3.5 Keep shared short-term research, market data, and ranking endpoints authenticated but not duplicated per user.

## 4. Frontend Login And Approval UI

- [x] 4.1 Add token storage helpers and an API interceptor that attaches `Authorization: Bearer <token>`.
- [x] 4.2 Add auth state provider or equivalent app-level current-user loading flow.
- [x] 4.3 Add `/login`, `/register`, and `/pending-approval` pages with Chinese copy.
- [x] 4.4 Add route guarding so protected pages redirect anonymous users and block unapproved users.
- [x] 4.5 Add `/admin/users` approval page for qje with approve and disable actions.
- [x] 4.6 Update navigation to show current user, logout, and admin-only user approval entry.
- [x] 4.7 Ensure tracked-position UI only shows the current user's positions and email recipient.

## 5. Deployment

- [x] 5.1 Update IP/domain nginx deployment configs to remove Basic Auth while keeping static frontend and `/api/` proxy behavior.
- [x] 5.2 Update deployment docs or env examples with JWT secret and bootstrap qje admin env names, without committing secrets.
- [ ] 5.3 Run migration on the server and verify qje can log in with the configured email/password.
- [ ] 5.4 Verify existing tracked positions are still visible under qje and reminders use qje's bound email.

## 6. Verification And Review

- [x] 6.1 Add backend tests for register, pending-login rejection, approved login, current user, and super-admin approval.
- [x] 6.2 Add backend tests for tracked-position user isolation and owner-recipient alert sending.
- [x] 6.3 Add frontend type/build checks for login flow, approval page, and tokenized API client.
- [x] 6.4 Run `uv run pytest`, `uv run ruff check .`, `uv run mypy app`, `corepack pnpm exec tsc --noEmit`, and `corepack pnpm build:static`.
- [ ] 6.5 Have 5.3codex execute implementation groups after task assignment, then run a final human-level review for auth boundary leaks, user data isolation, and deployment safety before marking the change done.
