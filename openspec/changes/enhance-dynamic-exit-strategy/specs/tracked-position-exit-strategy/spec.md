## ADDED Requirements

### Requirement: Tracked Position Exit Strategy Separates Asset Types
The system SHALL evaluate tracked position exit signals using asset-type-specific data cadence and price sources.

#### Scenario: ETF uses intraday-capable strategy
- **WHEN** an active tracked position has `asset_type=etf`
- **THEN** the exit strategy uses fresh intraday quote data when available and falls back to daily close data when intraday data is missing or stale

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

#### Scenario: Take-profit watch is web-only
- **WHEN** profit is positive and high-position or chase-risk conditions exist but no explicit exit threshold is breached
- **THEN** the system returns `take_profit_watch` as a web-only status and MUST NOT send a sell-or-reduce email

### Requirement: Exit Emails Are Restricted To Actionable Holding Signals
The system SHALL send email only for actionable tracked-position handling signals and SHALL record skipped email status for non-actionable warnings.

#### Scenario: Actionable signal sends email
- **WHEN** a tracked position creates `hard_stop`, `trailing_take_profit`, `trend_weakening`, or `exit_watch`
- **THEN** the system sends an email when SMTP is configured and records `email_status=sent` or `email_status=failed`

#### Scenario: Data warning does not send email
- **WHEN** a tracked position has stale data, missing IOPV, wide spread, abnormal premium/discount, or liquidity warning without an actionable exit signal
- **THEN** the system records a web-only warning with `email_status=skipped`

#### Scenario: Duplicate intraday alert is suppressed
- **WHEN** the same tracked ETF triggers the same intraday actionable signal repeatedly inside the configured cooldown window
- **THEN** the duplicate alert is recorded as suppressed and no duplicate email is sent unless hard-stop loss materially worsens
