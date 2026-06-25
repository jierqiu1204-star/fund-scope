# tracked-position-exit-strategy Specification

## Purpose
TBD - created by archiving change enhance-dynamic-exit-strategy. Update Purpose after archive.
## Requirements
### Requirement: Tracked Position Exit Strategy Separates Asset Types
The system SHALL evaluate tracked position exit signals using asset-type-specific data cadence, price sources, and actionable-data eligibility.

#### Scenario: ETF uses intraday-capable strategy
- **WHEN** an active tracked position has `asset_type=etf`
- **THEN** the exit strategy uses fresh intraday quote data when available and falls back to daily close data for display or daily review when intraday data is missing or stale

#### Scenario: ETF intraday email requires fresh quote
- **WHEN** an active tracked ETF is evaluated by the intraday watch job without a fresh intraday quote
- **THEN** the system returns a data-quality or daily-close status and MUST NOT send an actionable intraday email

#### Scenario: Fund uses confirmed NAV strategy
- **WHEN** an active tracked position has `asset_type=fund`
- **THEN** the exit strategy uses confirmed NAV date, confirmed shares when provided, and public daily NAV history without attempting intraday decisions

#### Scenario: Unknown or missing price is not treated as an exit
- **WHEN** an active tracked position has no usable current price or no usable entry price
- **THEN** the system returns a data-waiting status and MUST NOT create a sell-or-reduce email

### Requirement: Dynamic Exit Thresholds Are Derived From Asset Behavior
The system SHALL derive hard-stop, profit-protection, trailing-giveback, and trend-weakening thresholds from each tracked asset's own recent volatility and drawdown behavior.

#### Scenario: High-volatility ETF receives wider thresholds
- **WHEN** a tracked ETF has higher recent realized volatility or ATR than the minimum volatility floor
- **THEN** the dynamic hard-stop and trailing-giveback thresholds are wider than those for a low-volatility ETF, within configured safety bounds

#### Scenario: Fund thresholds use daily NAV behavior
- **WHEN** a tracked fund has enough daily NAV history after its confirmed NAV date
- **THEN** the system computes fund exit thresholds from recent NAV volatility, recent drawdown, and current profit/loss instead of using only one fixed percentage for all funds

#### Scenario: Insufficient sample falls back conservatively
- **WHEN** a tracked asset lacks enough recent price or NAV history to compute dynamic thresholds
- **THEN** the system uses conservative default thresholds and explains that the sample is insufficient

### Requirement: Exit Signals Are Prioritized And Explainable
The system SHALL choose at most one primary exit signal per tracked position evaluation and SHALL expose the reason, threshold, current value, data source, and whether the signal is email-eligible.

#### Scenario: Hard stop has highest priority
- **WHEN** the current estimated loss breaches the hard-stop threshold using an eligible price source
- **THEN** the primary exit signal is `hard_stop`, marked urgent, and explains current loss, threshold, current price, price source, and email eligibility

#### Scenario: Trailing take-profit protects existing profit
- **WHEN** the tracked position has reached the profit-protection start threshold and then gives back more than the trailing-giveback threshold using an eligible price source
- **THEN** the primary exit signal is `trailing_take_profit` and explains highest profit, current profit, giveback, threshold, price source, and email eligibility

#### Scenario: Trend weakening is below profit-protection priority
- **WHEN** price or NAV breaks below short moving-average conditions and recent momentum turns negative without a higher-priority hard stop or trailing take-profit
- **THEN** the primary exit signal is `trend_weakening` and explains the broken trend conditions

#### Scenario: Take-profit watch is a soft email-capable reminder
- **WHEN** profit reaches the dynamic take-profit-watch threshold without a higher-priority exit signal
- **THEN** the system returns `take_profit_watch` as a soft holding reminder that may send a `止盈观察提醒` email and MUST NOT present it as a forced sell instruction

#### Scenario: Ineligible data produces explanation but no email
- **WHEN** a threshold would be crossed only under a stale quote, daily close fallback during intraday watch, or missing data-quality context
- **THEN** the system explains the limitation and marks the signal as not email-eligible

### Requirement: Exit Emails Are Restricted To Actionable Holding Signals
The system SHALL send email only for tracked-position handling signals that are supported by eligible data and SHALL keep non-actionable warnings web-only.

#### Scenario: Actionable signal sends email
- **WHEN** a tracked position creates `hard_stop`, `trailing_take_profit`, `trend_weakening`, `exit_watch`, or `take_profit_watch` using an eligible price source for the job type
- **THEN** the system sends an email when SMTP is configured and records `email_status=sent` or `email_status=failed`

#### Scenario: Take-profit watch email is clearly soft
- **WHEN** a `take_profit_watch` email is sent
- **THEN** the subject and body identify it as a `止盈观察提醒`, include current profit, dynamic threshold, highest profit, giveback, and data source, and state that the user must manually decide whether to sell or reduce

#### Scenario: Take-profit watch cooldown checks sent email only
- **WHEN** the system checks the `take_profit_watch` cooldown window
- **THEN** only previous alerts with `email_status=sent` count as cooldown blockers

#### Scenario: Web-only or suppressed history does not block first email
- **WHEN** a tracked position only has previous `web_only`, `skipped`, `suppressed`, or failed `take_profit_watch` records
- **THEN** the next eligible `take_profit_watch` signal may send the first real email

#### Scenario: Data warning does not send email
- **WHEN** a tracked position has stale data, missing IOPV, wide spread, abnormal premium/discount, fallback price, or liquidity warning without an actionable exit signal based on eligible data
- **THEN** the system records or returns a web-only warning with `email_status=skipped` or equivalent non-email status

#### Scenario: Duplicate intraday alert is suppressed without noisy inserts
- **WHEN** the same tracked ETF triggers the same intraday actionable signal repeatedly inside the configured cooldown window
- **THEN** the duplicate email is suppressed and the system MUST NOT insert a new suppressed alert row for every repeated intraday poll unless hard-stop loss materially worsens

### Requirement: Tracked Position Alerts Do Not Depend On Latest Ranking Membership
The system SHALL evaluate active tracked positions for hard stop, trailing take-profit, trend weakening, and take-profit watch even when the asset is absent from the latest short-term signal items.

#### Scenario: Tracked asset is absent from ranking
- **WHEN** an active tracked position has usable entry price and current price but no latest signal item
- **THEN** the system still computes dynamic thresholds, estimated profit/loss, highest profit, giveback, and eligible holding alerts

#### Scenario: Exit watch requires ranking context
- **WHEN** no latest signal item exists for a tracked position
- **THEN** the system MUST NOT create `exit_watch` solely from missing ranking data

### Requirement: Dynamic Exit Lines Explain Threshold Source And Distance
The system SHALL show how each tracked-position dynamic exit line was calculated and how far the current position is from triggering it.

#### Scenario: Dynamic lines are returned
- **WHEN** a tracked position snapshot is returned
- **THEN** the response includes hard-stop threshold, take-profit-watch threshold, trailing-giveback threshold, trend-weakening state, threshold source, rule version, and current distance to each applicable line

#### Scenario: Threshold lacks enough data
- **WHEN** the system cannot compute an asset-specific dynamic threshold due to insufficient history or ineligible price data
- **THEN** it uses a conservative default or waiting status and explains that the threshold is not asset-specific yet

### Requirement: Profit Protection Adapts To Volatility And Existing Profit
The system SHALL make take-profit-watch and trailing-giveback thresholds responsive to recent volatility, drawdown behavior, and current maximum profit.

#### Scenario: High volatility receives wider giveback
- **WHEN** a tracked ETF has higher recent volatility or larger normal pullbacks
- **THEN** the trailing-giveback threshold is wider within configured safety bounds and the UI explains the volatility reason

#### Scenario: Larger profit receives stronger protection
- **WHEN** a tracked position has materially higher maximum profit after entry
- **THEN** the profit-protection explanation highlights highest profit, current profit, giveback, and whether the soft watch or trailing condition is close to triggering

### Requirement: Holding Signals Remain Separate From Buy Observation Labels
The system SHALL keep ranked asset observation labels separate from tracked-position handling signals.

#### Scenario: High ranked ETF triggers exit signal
- **WHEN** an ETF remains high ranked but the user tracked position breaches an exit threshold
- **THEN** the UI shows the ranked observation state separately from the holding signal and explains why both can be true

#### Scenario: High watch without threshold breach
- **WHEN** a tracked position is profitable and the asset is 高位观察 but no threshold is breached
- **THEN** the system shows a web-only caution and MUST NOT send a sell or reduce-position email

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

### Requirement: Exit decision audit context
Tracked-position exit evaluations SHALL expose the context behind each holding处理状态.

#### Scenario: Exit signal context
- **WHEN** a tracked position triggers hard stop, trailing take profit, trend weakening, take profit watch, or exit watch
- **THEN** the result includes the threshold, current profit, high-water profit, price source, freshness, and reason text

#### Scenario: No action context
- **WHEN** a tracked position does not trigger an exit signal
- **THEN** the result includes the main reason no action was taken when that reason is available

### Requirement: Display-only data cannot trigger exit email
Tracked-position exit evaluations SHALL prevent display-only, stale, estimated, or unavailable data from triggering email reminders.

#### Scenario: Stale quote exit blocked
- **WHEN** the latest quote is stale or display-only
- **THEN** the system may show estimated context on the page but MUST NOT send an exit email

### Requirement: Exit thresholds are volatility-adaptive
The tracked position exit strategy SHALL calculate hard stop and trailing take-profit thresholds using recent realized volatility, recent drawdown, current profit state, and data reliability.

#### Scenario: ETF has high recent volatility
- **WHEN** a tracked ETF has high recent realized volatility and decision-eligible prices
- **THEN** the exit strategy uses wider volatility-adjusted thresholds and records the calculation reason

#### Scenario: ETF has low recent volatility
- **WHEN** a tracked ETF has low recent realized volatility and decision-eligible prices
- **THEN** the exit strategy uses tighter volatility-adjusted thresholds and records the calculation reason

#### Scenario: Volatility data is insufficient
- **WHEN** recent volatility cannot be calculated from decision-eligible data
- **THEN** the system falls back to conservative fixed thresholds marked as `fixed_fallback` or suppresses the alert if the price is not decision-eligible

### Requirement: Adaptive threshold context is persisted
The tracked position exit strategy SHALL persist high-water profit, current threshold values, threshold mode, and explanation for each active tracked position.

#### Scenario: Position reaches a new high-water profit
- **WHEN** a tracked ETF reaches a new high-water profit
- **THEN** the persisted exit state updates high-water profit and recalculates trailing threshold context

#### Scenario: Alert is generated
- **WHEN** an exit alert is generated
- **THEN** the alert includes threshold mode, threshold value, current profit, high-water profit, and human-readable reason

### Requirement: ETF Exit Thresholds Expose Rule Version
The tracked-position exit strategy SHALL expose the rule version and threshold inputs used for ETF hard stop, take-profit watch, trailing take-profit, and trend weakening.

#### Scenario: Tracked ETF is evaluated
- **WHEN** an active ETF tracked position is evaluated
- **THEN** the result includes rule version, volatility unit, hard-stop threshold, take-profit-watch threshold, trailing-giveback threshold, trend inputs, and current distance to each threshold

#### Scenario: Threshold uses fallback defaults
- **WHEN** asset-specific volatility thresholds cannot be calculated from eligible data
- **THEN** the result marks the threshold mode as conservative default and explains why it is not asset-specific

### Requirement: ETF Exit Signals Are Replayable
The tracked-position exit strategy SHALL persist enough threshold context to replay why an alert was or was not sent.

#### Scenario: Email alert is sent
- **WHEN** an ETF tracked-position email is sent
- **THEN** the alert audit stores entry price, current price, quote time, quote reliability, highest profit, current profit, giveback, threshold values, and trigger reason

#### Scenario: Email alert is not sent
- **WHEN** an ETF tracked position does not send an email because thresholds are not crossed or data is ineligible
- **THEN** the latest position snapshot exposes the main no-email reason for the UI

