## MODIFIED Requirements

### Requirement: Exit Signals Are Prioritized And Explainable
The system SHALL choose at most one primary exit signal per tracked position evaluation and SHALL expose the reason, threshold, current value, and data source.

#### Scenario: Hard stop has highest priority
- **WHEN** the current estimated loss breaches the hard-stop threshold
- **THEN** the primary exit signal is `hard_stop`, marked urgent, and explains current loss, threshold, current price, and price source

#### Scenario: Trailing take-profit protects existing profit
- **WHEN** the tracked position has reached the profit-protection start threshold and then gives back more than the trailing-giveback threshold
- **THEN** the primary exit signal is `trailing_take_profit` and explains highest profit, current profit, giveback, and threshold

#### Scenario: Trend weakening is below profit-protection priority
- **WHEN** price or NAV breaks below short moving-average conditions and recent momentum turns negative without a higher-priority hard stop or trailing take-profit
- **THEN** the primary exit signal is `trend_weakening` and explains the broken trend conditions

#### Scenario: Take-profit watch is a soft email-capable reminder
- **WHEN** profit reaches the dynamic take-profit-watch threshold without a higher-priority exit signal
- **THEN** the system returns `take_profit_watch` as a soft holding reminder that may send a `止盈观察提醒` email and MUST NOT present it as a forced sell instruction

### Requirement: Exit Emails Are Restricted To Actionable Holding Signals
The system SHALL send email only for tracked-position handling signals and SHALL record skipped email status for non-actionable warnings.

#### Scenario: Actionable signal sends email
- **WHEN** a tracked position creates `hard_stop`, `trailing_take_profit`, `trend_weakening`, `exit_watch`, or `take_profit_watch`
- **THEN** the system sends an email when SMTP is configured and records `email_status=sent` or `email_status=failed`

#### Scenario: Take-profit watch email is clearly soft
- **WHEN** a `take_profit_watch` email is sent
- **THEN** the subject and body identify it as a `止盈观察提醒`, include current profit, dynamic threshold, highest profit, giveback, and data source, and state that the user must manually decide whether to sell or reduce

#### Scenario: Data warning does not send email
- **WHEN** a tracked position has stale data, missing IOPV, wide spread, abnormal premium/discount, or liquidity warning without an actionable holding signal
- **THEN** the system records a web-only warning with `email_status=skipped`

#### Scenario: Duplicate intraday alert is suppressed
- **WHEN** the same tracked ETF triggers the same intraday actionable signal repeatedly inside the configured cooldown window
- **THEN** the duplicate alert is recorded as suppressed and no duplicate email is sent unless hard-stop loss materially worsens

## ADDED Requirements

### Requirement: Tracked Position Alerts Do Not Depend On Latest Ranking Membership
The system SHALL evaluate active tracked positions for hard stop, trailing take-profit, trend weakening, and take-profit watch even when the asset is absent from the latest short-term signal items.

#### Scenario: Tracked asset is absent from ranking
- **WHEN** an active tracked position has usable entry price and current price but no latest signal item
- **THEN** the system still computes dynamic thresholds, estimated profit/loss, highest profit, giveback, and eligible holding alerts

#### Scenario: Exit watch requires ranking context
- **WHEN** no latest signal item exists for a tracked position
- **THEN** the system MUST NOT create `exit_watch` solely from missing ranking data
