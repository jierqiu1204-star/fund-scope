## MODIFIED Requirements

### Requirement: Exit Signals Are Prioritized And Explainable
The system SHALL choose at most one primary actionable exit signal per tracked position evaluation, SHALL expose non-actionable guard states separately, and SHALL include the reason, threshold, current value, data source, email eligibility, action status, immutable exposure baseline, and absolute target remaining fraction when actionable.

#### Scenario: Hard stop has highest priority
- **WHEN** the current estimated loss breaches the hard-stop threshold using an eligible price source
- **THEN** the primary exit signal is `hard_stop`, marked as insurance-style urgent risk control, and explains current loss, threshold, current price, price source, and email eligibility

#### Scenario: Trailing take-profit protects existing profit
- **WHEN** the tracked position has reached the profit-protection activation threshold and then gives back more than the trailing-giveback threshold using an eligible price source
- **THEN** the primary exit signal is `trailing_take_profit`, proposes the versioned absolute target against the immutable exposure baseline, and explains entry price, highest profit, current profit, giveback, threshold, threshold mode, price source, and email eligibility without multiplying the remaining position

#### Scenario: Trend weakening is guard-only by default
- **WHEN** price or NAV breaks below short moving-average conditions or recent momentum turns negative without a higher-priority hard stop, trailing take-profit, material loss, material giveback, or market-regime confirmation
- **THEN** the system returns `trend_weakening` as a guard-only state that can block adding or lower confidence, and MUST NOT present it as a standalone sell or reduce-position instruction

#### Scenario: Confirmed trend weakening can become actionable
- **WHEN** trend weakening is confirmed by material loss, material giveback, ranking deterioration, or broader market-regime deterioration using eligible data
- **THEN** the system may return a reduce-risk holding signal with the versioned absolute target, confirmation evidence, and email eligibility; another rule requesting the same target only adds reasons to the same action cycle

#### Scenario: Take-profit watch is a soft email-capable reminder
- **WHEN** profit reaches the dynamic take-profit-watch threshold without a higher-priority actionable exit signal
- **THEN** the system returns `take_profit_watch` as `hold/100%/observation-only`, may send a `止盈观察提醒` email, and MUST NOT create or present a trim, reduce, or exit action

#### Scenario: Ineligible data produces explanation but no email
- **WHEN** a threshold would be crossed only under a stale quote, daily close fallback during intraday watch, or missing data-quality context
- **THEN** the system explains the limitation and marks the signal or guard as not email-eligible

### Requirement: Exit Emails Are Restricted To Actionable Holding Signals
The system SHALL send immediate action-oriented email only for persisted action decisions supported by eligible data, MAY send explicitly non-actionable watch summaries, and SHALL keep guard-only, research-only, and data warnings web-only. Notification state SHALL NOT change action status or reentry cooldown.

#### Scenario: Actionable signal sends email
- **WHEN** a tracked position creates a new proposed action from `hard_stop`, `trailing_take_profit`, `confirmed_trend_weakening`, or eligible `exit_watch` using an eligible price source for the job type
- **THEN** the system may send an email when SMTP is configured, links the notification to the existing action, and records SMTP outcome separately without marking the action executed

#### Scenario: Guard-only trend weakening does not send email
- **WHEN** a tracked position only has unconfirmed `trend_weakening`
- **THEN** the system records or returns a web-only guard state and MUST NOT send a sell, reduce-position, or stop-loss email

#### Scenario: Take-profit watch email is clearly soft
- **WHEN** a `take_profit_watch` email is sent
- **THEN** the subject and body identify it as a `止盈观察提醒`, include current profit, dynamic threshold, highest profit, giveback, and data source, and state `hold/仅观察/未生成减仓动作`

#### Scenario: Take-profit watch cooldown checks sent email only
- **WHEN** the system checks the `take_profit_watch` cooldown window
- **THEN** only previous successfully accepted/sent notifications count as notification blockers, and the result MUST NOT affect action eligibility, action status, or reentry cooldown

#### Scenario: Web-only or suppressed history does not block first email
- **WHEN** a tracked position only has previous `web_only`, `skipped`, `suppressed`, or failed `take_profit_watch` records
- **THEN** the next eligible `take_profit_watch` signal may send the first real email

#### Scenario: Data warning does not send email
- **WHEN** a tracked position has stale data, missing IOPV, wide spread, abnormal premium/discount, fallback price, or liquidity warning without an actionable exit signal based on eligible data
- **THEN** the system records or returns a web-only warning with `email_status=skipped` or equivalent non-email status

#### Scenario: Duplicate intraday alert is suppressed without noisy inserts
- **WHEN** the same tracked ETF triggers the same intraday actionable signal repeatedly inside the configured cooldown window
- **THEN** the duplicate notification is suppressed and the system MUST NOT insert a new suppressed alert row for every repeated intraday poll or create another action; a materially worsened hard stop may use a later notification slot linked to the original action

## ADDED Requirements

### Requirement: ETF exit state is path-dependent
The tracked-position exit strategy SHALL persist and update ETF exit state using entry price, current price, highest observed profit, activation state, current giveback, threshold mode, immutable exposure baseline/version, current action cycle/status, and latest decision-eligible data source.

#### Scenario: Position reaches new high-water profit
- **WHEN** an active tracked ETF reaches a new maximum profit using decision-eligible data
- **THEN** the system updates high-water profit, recalculates giveback, and records the threshold context used by trailing protection

#### Scenario: Profit activation has not occurred
- **WHEN** a tracked ETF has not reached the profit-protection activation threshold
- **THEN** the system MUST NOT trigger trailing take-profit and instead reports the distance to activation

#### Scenario: State is unavailable
- **WHEN** entry price, current price, or decision-eligible history is missing
- **THEN** the system returns a waiting or data-ineligible state and MUST NOT infer a trailing stop from fallback data

### Requirement: ETF protection guards govern alerts and adds
The tracked-position exit strategy SHALL evaluate protection guards that can suppress duplicate alerts, downgrade non-urgent signals, or block add reminders without changing the underlying ranking score.

#### Scenario: Cooldown guard suppresses repeated alert
- **WHEN** an ETF has recently sent an actionable exit alert within the configured cooldown window
- **THEN** the system suppresses or aggregates duplicate notifications and exposes the notification cooldown reason in the holding context without changing action eligibility, action status, or execution-origin reentry cooldown

#### Scenario: Repeated stop-loss guard blocks adds
- **WHEN** repeated hard-stop or confirmed loss events occur within the configured lookback window
- **THEN** the system marks the ETF or portfolio as guarded and blocks add reminders until the guard expires

#### Scenario: Portfolio drawdown guard downgrades urgency
- **WHEN** the user's tracked ETF portfolio breaches the configured drawdown guard
- **THEN** the system marks portfolio protection active and downgrades non-urgent new observation or add messages

### Requirement: Approved exit parameters are required for live rule changes
The tracked-position exit strategy SHALL use only default or manually approved ETF exit parameters for live email decisions.

#### Scenario: Candidate parameters exist
- **WHEN** research validation produces candidate ETF exit parameters
- **THEN** the live tracked-position evaluator MUST NOT use them until they are marked approved

#### Scenario: Approved parameters exist
- **WHEN** manually approved ETF exit parameters are available for the relevant asset bucket or volatility group
- **THEN** the live evaluator may use them and records the approved rule version in the alert context

### Requirement: Action execution and reentry cooldown require execution facts
The tracked-position exit strategy SHALL keep recommendation, owner-confirmed execution, and notification delivery as separate facts, and only an owner-confirmed partial/full execution fact SHALL advance action progress or start reentry cooldown.

#### Scenario: Recommendation is emailed
- **WHEN** a proposed reduce or exit action is sent, accepted, failed, retried, suppressed, or grouped as a notification
- **THEN** the action remains proposed or acknowledged and reentry cooldown does not start or extend

#### Scenario: Owner confirms a real fill
- **WHEN** the owner confirms a partial/full execution with execution time, quantity, price/source, and resulting position facts
- **THEN** the system records immutable execution provenance, advances action progress, and uses the first real fill as the versioned reentry cooldown origin
