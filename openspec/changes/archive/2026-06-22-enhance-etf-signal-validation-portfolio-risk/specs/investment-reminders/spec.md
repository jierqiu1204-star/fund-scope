## ADDED Requirements

### Requirement: Reminder emails include adaptive threshold reasons
Investment reminder emails SHALL include the rule category, adaptive threshold mode, threshold value, and plain-language reason when an ETF holding alert is sent.

#### Scenario: Moving take-profit reminder fires
- **WHEN** a `trailing_take_profit` reminder is sent
- **THEN** the email explains highest profit, current profit, giveback, volatility mode, and the threshold that was crossed

#### Scenario: Hard stop reminder fires
- **WHEN** a `hard_stop` reminder is sent
- **THEN** the email explains current loss, stop threshold, volatility mode, and why the alert is a risk reminder

### Requirement: Reminder emails require decision-eligible data
Investment reminders SHALL NOT send emails from stale, estimated, fallback-only, or display-only data.

#### Scenario: Data is display-only
- **WHEN** a tracked ETF only has display-only data
- **THEN** the system records a webpage-only status and does not send an email
