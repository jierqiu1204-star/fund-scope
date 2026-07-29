# investment-reminders Specification

## Purpose
TBD - created by archiving change add-fundscope-mvp. Update Purpose after archive.
## Requirements
### Requirement: Configure reminder preferences

The system SHALL allow the user to configure SMTP credentials, recipient email, monthly reminder day, reference index for dynamic adjustment, and the base monthly DCA amount.

#### Scenario: Save SMTP and reminder configuration

- **WHEN** the user submits the `/settings/notifications` form with valid SMTP host, port, username, app password, recipient email, reminder day `1`, reference index `CSI300`, base amount `833`
- **THEN** the system SHALL persist the configuration (with the app password encrypted at rest or stored only as an env var reference) and send a test email upon request

#### Scenario: Reject invalid SMTP credentials

- **WHEN** the user submits SMTP credentials that fail to authenticate during a test-send
- **THEN** the system SHALL NOT persist the broken configuration and SHALL display the SMTP error to the user

### Requirement: Compute dynamic DCA amount from valuation percentile

The system SHALL compute a recommended monthly DCA amount using a rule-based adjustment of the base amount based on the reference index's current PE percentile.

#### Scenario: Low valuation increases allocation

- **WHEN** the reference index PE percentile is `15` and the base amount is `833`
- **THEN** the computed recommended amount SHALL be `833 × 1.5 = 1249.50` (rounded to 2 decimals) with reason label "低估 加码"

#### Scenario: Normal valuation keeps base allocation

- **WHEN** the reference index PE percentile is `40` and the base amount is `833`
- **THEN** the computed recommended amount SHALL be `833 × 1.0 = 833.00` with reason label "合理 常规定投"

#### Scenario: Elevated valuation reduces allocation

- **WHEN** the reference index PE percentile is `65` and the base amount is `833`
- **THEN** the computed recommended amount SHALL be `833 × 0.5 = 416.50` with reason label "偏高 减量"

#### Scenario: High valuation pauses DCA

- **WHEN** the reference index PE percentile is `85`
- **THEN** the computed recommended amount SHALL be `0` with reason label "高估 暂停本月定投"

#### Scenario: Missing valuation data falls back to base amount

- **WHEN** no valuation row exists for the reference index for the prior business day
- **THEN** the system SHALL use the base amount unmodified and note "估值数据缺失 使用默认金额" in the reminder, NOT skip the reminder entirely

### Requirement: Send monthly reminder email

The system SHALL send an HTML email to the configured recipient on the configured day of each month summarizing the recommended DCA action.

#### Scenario: Monthly reminder dispatched on schedule

- **WHEN** the `monthly_dca_reminder` scheduled job runs at 09:00 on the configured day of the month
- **THEN** the system SHALL compose an HTML email from the `dca_monthly.html.j2` template containing: recommended amount with reason label, per-fund breakdown by target allocation, current valuation status of each watchlist index, current portfolio total return, and send it via SMTP to the configured recipient

#### Scenario: Reminder content reflects current holdings

- **WHEN** the user holds funds with different target allocations (e.g. 40/30/20/10)
- **THEN** the reminder email SHALL allocate the recommended total amount across funds in proportion to their target allocation and list the per-fund sub-amounts

#### Scenario: SMTP failure triggers retry and alert

- **WHEN** the SMTP send fails with a retryable error
- **THEN** the system SHALL retry up to 3 times with exponential backoff, and if all retries fail, log the failure and surface it on the `/admin/jobs` page

### Requirement: Log and audit all notifications sent

The system SHALL persist a record of every notification send attempt including timestamp, recipient, template used, status, and rendered payload.

#### Scenario: Successful send recorded

- **WHEN** a notification email is sent successfully
- **THEN** a row SHALL be inserted into `notification_log` with `status='sent'`, the recipient, template name, and rendered body snapshot

#### Scenario: Failed send recorded

- **WHEN** a notification send raises an exception after all retries
- **THEN** a row SHALL be inserted into `notification_log` with `status='failed'`, the error message, and the attempted payload for later manual inspection

### Requirement: Manually trigger reminder

The system SHALL expose an authenticated admin endpoint to manually trigger the monthly reminder for testing and recovery.

#### Scenario: Manual trigger sends reminder

- **WHEN** a request is made to `POST /api/admin/jobs/monthly_dca_reminder/run`
- **THEN** the system SHALL execute the same logic as the scheduled job and return the computed amount and email send status in the response body

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

### Requirement: Tracked Position Lists Use Batched Context Reads
The system SHALL build tracked position lists with batched reads for prices, alerts, short-term signal context, advisor reports, and recent intraday alerts.

#### Scenario: User has multiple tracked positions
- **WHEN** a user opens the tracked holdings section with multiple active positions
- **THEN** the backend does not repeat the same latest signal run, signal item list, advisor report list, and latest alert queries for every position

#### Scenario: Detail endpoint can load full history
- **WHEN** a user opens one tracked position detail
- **THEN** the backend may load full chart and full alert history for that single position

### Requirement: Holding High-Water State Survives Intraday Quote Cleanup
The system SHALL persist each tracked holding's high-water profit and dynamic exit context independently from raw intraday quote retention.

#### Scenario: Raw intraday rows are cleaned
- **WHEN** old raw intraday quote rows are removed
- **THEN** existing tracked positions still retain their max profit, giveback context, and dynamic line state needed for future reminders

#### Scenario: Reminder evaluation uses fresh data
- **WHEN** a tracked ETF is evaluated during market hours
- **THEN** reminder decisions use fresh intraday quotes and persisted holding state rather than deleted or stale raw quote rows

### Requirement: Reminder delivery audit
Investment reminders SHALL record delivery-level status for every attempted tracked-position email.

#### Scenario: Sent delivery audit
- **WHEN** a reminder email is successfully sent
- **THEN** the system records status sent, recipient, template, timestamp, and linked tracked-position alert when available

#### Scenario: Failed delivery audit
- **WHEN** SMTP login or send fails
- **THEN** the system records status failed with a redacted error message and does not expose SMTP secrets

### Requirement: Reminder suppression audit
Investment reminders SHALL record why an otherwise detected signal did not send email.

#### Scenario: Cooldown suppression
- **WHEN** a signal is suppressed due to duplicate window or take-profit cooldown
- **THEN** the system records suppression reason and next eligible reminder time when available

### Requirement: Reminder emails include adaptive threshold reasons
Investment reminder emails SHALL include the rule category, adaptive threshold mode, threshold value, and plain-language reason when an ETF holding alert is sent.

#### Scenario: Moving take-profit reminder fires
- **WHEN** a `trailing_take_profit` reminder is sent
- **THEN** the email explains highest profit, current profit, giveback, volatility mode, and the threshold that was crossed

#### Scenario: Hard stop reminder fires
- **WHEN** a `hard_stop` reminder is sent
- **THEN** the email explains current loss, stop threshold, volatility mode, and why the alert is a risk reminder

### Requirement: Reminder emails require decision-eligible data
Investment reminders SHALL NOT send emails from stale, estimated, fallback-only, display-only, research-only, or otherwise action-ineligible data.

#### Scenario: Data is display-only
- **WHEN** a tracked ETF only has display-only data
- **THEN** the system records a webpage-only status and does not send an email

#### Scenario: Candidate was selected by research rank
- **WHEN** a workflow proposes an email because an ETF belongs to a research-ranked candidate set
- **THEN** the system sends no email unless the ETF has an eligible same-context `actionable_rank_v1` row and records the suppression reason

#### Scenario: Actionable rank context does not match
- **WHEN** the candidate's actionable rank hash, as-of session, or required source context differs from the workflow context
- **THEN** the system fails closed and records a version or context mismatch

#### Scenario: Independent tracked-position risk alert is actionable
- **WHEN** an existing tracked position triggers its independent risk lifecycle using fresh decision-eligible quote and holding state
- **THEN** missing research-rank membership alone does not suppress the alert

