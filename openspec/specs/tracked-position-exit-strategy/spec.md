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
