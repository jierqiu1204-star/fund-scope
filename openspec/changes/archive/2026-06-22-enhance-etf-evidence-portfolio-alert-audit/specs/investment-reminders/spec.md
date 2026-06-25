## ADDED Requirements

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
