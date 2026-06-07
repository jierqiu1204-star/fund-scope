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

