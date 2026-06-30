## 1. Backend Permissions And Schemas

- [x] 1.1 Inspect existing auth dependencies, user admin APIs, tracked position owner scoping, and notification settings response schemas.
- [x] 1.2 Add or reuse a `require_super_admin` dependency for admin read-only endpoints.
- [x] 1.3 Define sanitized admin user summary and user detail response schemas that exclude password hash, SMTP password, tokens, API keys, and raw secrets.
- [x] 1.4 Define read-only tracked-position snapshot response using the same calculation semantics as the owner view.
- [x] 1.5 Add lightweight admin read access logging for user detail and tracked-position reads.

## 2. Backend Admin Read-Only APIs

- [x] 2.1 Extend or add `GET /api/admin/users` to include safe account summaries and detail links.
- [x] 2.2 Add `GET /api/admin/users/{user_id}` for read-only account and notification summary.
- [x] 2.3 Add `GET /api/admin/users/{user_id}/tracked-positions` for read-only tracking snapshot.
- [x] 2.4 Add `GET /api/admin/users/{user_id}/alerts` for read-only recent alert and email delivery history.
- [x] 2.5 Ensure all normal tracked-position write endpoints still reject attempts to mutate another user's positions, including from super admin unless a future explicit admin-write capability exists.

## 3. Frontend Admin UI

- [x] 3.1 Add `查看详情` read-only entry to the super-admin user management page.
- [x] 3.2 Create an admin user detail page or drawer showing account summary, approval status, recipient email, and notification configured status.
- [x] 3.3 Add read-only tracked holdings section with asset, entry info, estimated P/L, holding status, latest alert reason, and email status.
- [x] 3.4 Add read-only recent alerts section with alert type, reason, email status, recipient email, and delivery summary.
- [x] 3.5 Ensure admin detail UI has no edit buy info, close, stop tracking, resend, or notification-setting mutation buttons.
- [x] 3.6 Add explanatory copy that market ETF/fund research data is shared globally while tracking and reminders are user-specific.

## 4. Tests

- [x] 4.1 Test super admin can read user list, user detail, tracked snapshot, and alerts.
- [x] 4.2 Test normal approved user cannot read admin user observability endpoints.
- [x] 4.3 Test admin read responses do not include password hash, SMTP password, tokens, API keys, or raw secret fields.
- [x] 4.4 Test super admin cannot mutate another user's tracked position through normal write endpoints.
- [x] 4.5 Test admin tracked snapshot matches owner calculation for estimated P/L, holding signal, data reliability, and latest alert.
- [x] 4.6 Test global short-term market data remains identical across users except for tracked-position overlays.

## 5. Verification

- [x] 5.1 Run backend auth and tracked-position API tests.
- [x] 5.2 Run `uv run ruff check .` in backend.
- [x] 5.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 5.4 Run `corepack pnpm build:static`.
- [x] 5.5 Manually verify qje can view a user's read-only detail and normal users cannot access it.
