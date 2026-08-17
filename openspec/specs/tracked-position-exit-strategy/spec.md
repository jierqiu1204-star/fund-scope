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

### Requirement: ETF trade sizing requires explicit capital confirmation
The tracked-position exit strategy SHALL produce actionable ETF amount and share sizing only when the owner has explicitly confirmed a finite positive ETF sleeve capital.

#### Scenario: Capital is unconfirmed
- **WHEN** an owner still has a legacy/default capital value without explicit confirmation
- **THEN** the system may show target weight but returns null trade amount and shares with a stable configuration reason

#### Scenario: Capital or price is non-finite
- **WHEN** capital, price, current value, quantity or target weight is NaN, infinite, zero where positive is required, or otherwise invalid
- **THEN** sizing fails closed and does not emit an actionable amount

### Requirement: Owner risk state controls only increases in risk
The tracked-position strategy SHALL apply owner risk state before add or reentry recommendations without suppressing valid risk-reduction evidence.

#### Scenario: State is reduce-only
- **WHEN** the sizing result would add or reenter an ETF while the owner state is `reduce_only`
- **THEN** the result becomes `no_add` with state reasons, while trim/reduce/exit results remain available

#### Scenario: State is data-halt
- **WHEN** owner sleeve evidence is unavailable
- **THEN** new-risk sizing remains unavailable and existing exit warnings state the evidence limitation instead of claiming that no risk exists

### Requirement: ETF liquidity capacity is size-aware
The tracked-position strategy SHALL compare the proposed ETF trade amount with decision-eligible turnover and executable quote structure.

#### Scenario: Entry capacity is adequate
- **WHEN** proposed amount, ADV participation, spread, structure and quote eligibility pass the versioned entry policy
- **THEN** the add recommendation includes normal and stressed capacity evidence and its contract identity

#### Scenario: Entry capacity is inadequate or unavailable
- **WHEN** required capacity evidence is missing or breaches the policy
- **THEN** the strategy returns `no_add` or unavailable and MUST NOT use current turnover alone as proof that the planned amount is executable

#### Scenario: Exit capacity is poor
- **WHEN** a valid reduce or exit signal has a wide spread, low capacity or long estimated liquidation time
- **THEN** the system keeps the risk-reduction signal and labels execution as stressed/unavailable rather than converting it to hold

### Requirement: ETF profit protection uses eligible adjusted risk data

For a tracked ETF routed to the versioned current dynamic holding policy, the system SHALL derive profit-protection risk units only from bounded, decision-eligible, total-return-adjusted daily facts and SHALL fail closed when that evidence is insufficient. This protection contract SHALL NOT be evaluated as an additional trigger for `late_day_turnaround_t1_v1` or `leader_tactics_exit_v1`.

#### Scenario: Eligible adjusted history is available
- **WHEN** at least 30 eligible adjusted sessions exist for a tracked ETF
- **THEN** the system calculates a versioned robust risk unit and records the price basis, sample count and source status

#### Scenario: Only raw or ineligible history exists
- **WHEN** raw daily prices exist but eligible adjusted history is insufficient
- **THEN** the system returns a stable data-waiting or conservative-display state and MUST NOT use those raw rows to make a trailing-profit email actionable

### Requirement: Long profit protection ratchets monotonically

The system SHALL persist the high-water profit and armed protection line for each active position episode, and the effective long protection line SHALL never move downward within that episode.

#### Scenario: Position reaches a new profit high
- **WHEN** current or observed profit exceeds the persisted high-water value after the start threshold is reached
- **THEN** the system raises the high-water value and recalculates a protection line no lower than the previous line

#### Scenario: Volatility expands after protection is armed
- **WHEN** a later risk estimate would imply a wider giveback
- **THEN** the existing protection line remains unchanged or rises and MUST NOT be loosened

#### Scenario: A later chart window omits the historical peak
- **WHEN** the bounded display chart no longer contains the historical peak
- **THEN** the persisted high-water and protection line continue to govern the position

### Requirement: Profit-protection display is historically truthful

The system SHALL distinguish start threshold, allowed giveback and actual protection line, and SHALL expose the protection line as it evolved at each chart date.

#### Scenario: Chart is rendered after protection is armed
- **WHEN** the user opens a tracked ETF detail
- **THEN** the chart shows no protection before arming and a non-decreasing step line afterward instead of backfilling the current line across prior dates

### Requirement: ETF action emails require fresh explicit intraday eligibility
The system SHALL apply the same fail-closed fresh-quote gate to intraday-priced ETF action emails under the current dynamic holding and late-day policies regardless of whether evaluation was started by an intraday or daily scheduled job. A position routed to `leader_tactics_exit_v1` MAY instead emit its explicitly daily-close-based manual exit reminder from a completed, decision-eligible, total-return-adjusted bar; that reminder SHALL disclose its daily-close basis and SHALL NOT claim a fresh executable quote or completed fill.

#### Scenario: Daily ETF review only has closing price
- **WHEN** a daily ETF position review has a usable same-day close but no fresh explicitly decision-eligible intraday snapshot
- **THEN** the system may show the threshold context on the web but MUST NOT send an action email

#### Scenario: Daily ETF review sees an old intraday snapshot
- **WHEN** the latest stored ETF quote is stale, fallback, display-only, missing explicit eligibility, or provider-ineligible
- **THEN** the system records `data_ineligible` or `web_only` and MUST NOT send an action email

#### Scenario: Fund review uses confirmed NAV
- **WHEN** a tracked fund is evaluated by the daily job with decision-eligible confirmed NAV evidence
- **THEN** the fund-specific daily email behavior remains available and does not require ETF bid/ask fields

#### Scenario: Leader-policy ETF closes through its protection line
- **WHEN** an ETF routed to `leader_tactics_exit_v1` has a completed decision-eligible adjusted daily bar that triggers its frozen full-exit rule but no fresh intraday bid and ask
- **THEN** the system may send the policy's daily-close manual exit reminder with explicit non-execution provenance, while generic intraday-priced ETF actions remain ineligible

### Requirement: V2 lifecycle shadow accompanies production position evaluation
The system SHALL evaluate the V2 tracked-position lifecycle in an isolated shadow mode alongside each eligible scheduled legacy position evaluation while legacy output remains the production source of alerts and emails.

#### Scenario: Eligible position is evaluated
- **WHEN** a scheduled daily or intraday job evaluates an active tracked position with a sealed, decision-eligible input snapshot
- **THEN** the system records one idempotent V2 shadow evaluation for the same position, policy version, evaluation mode, and input snapshot without creating a V2 action or notification

#### Scenario: Input data is not decision-eligible
- **WHEN** a scheduled position evaluation only has stale, estimated, display-only, incomplete, or otherwise ineligible price evidence
- **THEN** the V2 shadow records a data-waiting or ineligible result, freezes business-state progression, and MUST NOT infer an action from legacy or fallback data

#### Scenario: Shadow evaluation fails
- **WHEN** one V2 shadow evaluation raises a recoverable error
- **THEN** the system records a bounded diagnostic, continues evaluating other positions, and does not suppress or duplicate the established legacy result

### Requirement: Exit evaluation exposes execution-risk context
The tracked-position exit strategy SHALL distinguish an observed threshold breach from a proven executable fill and SHALL expose the price, freshness, spread, signal-to-quote gap, and slippage evidence available at evaluation time.

#### Scenario: Fresh executable quote context is available
- **WHEN** an ETF exit signal is evaluated from a fresh decision-eligible quote with usable bid and ask
- **THEN** the result records the quote time, reference price, executable-side price basis, spread, estimated base slippage, stressed slippage, and states that the output remains a manual action reminder

#### Scenario: Executable price cannot be established
- **WHEN** a threshold is crossed but fresh executable-side quote evidence is missing or inconsistent
- **THEN** the system marks execution risk unavailable or display-only and MUST NOT describe the reminder, email, or threshold crossing as a completed trade

### Requirement: Tracked Position Exit Strategy Routes By Persisted Policy
The system SHALL route each tracked-position evaluation through the position's persisted alert policy and SHALL NOT combine triggers from different policies in one decision.

#### Scenario: Default policy position is evaluated
- **WHEN** a tracked position uses the current dynamic holding policy
- **THEN** the existing hard-stop, profit-protection, trend-weakening, and data-eligibility behavior remains in effect

#### Scenario: Late-day policy position is evaluated
- **WHEN** an ETF tracked position uses `late_day_turnaround_t1_v1`
- **THEN** the generic dynamic policy does not independently emit a competing sell or reduce email for that evaluation

#### Scenario: Leader policy position is evaluated
- **WHEN** an ETF or A-share tracked position uses `leader_tactics_exit_v1`
- **THEN** only its adjusted daily-close stop contract is evaluated and the generic dynamic and late-day policies do not emit competing emails

#### Scenario: Policy is changed explicitly
- **WHEN** the owner changes a tracked position to another supported alert policy
- **THEN** policy-specific high-water and lifecycle state is reset, owner-level sleeve risk state is preserved, the change is auditable, and evaluation starts under the new version without reusing incompatible policy state

### Requirement: Alert Policy Choice Is Owner Scoped
The system SHALL allow only the tracked-position owner to select or change its alert policy.

#### Scenario: Another user attempts policy update
- **WHEN** a user attempts to change the policy on a tracked position owned by someone else
- **THEN** the system denies access and does not expose or mutate the other user's policy or provenance

### Requirement: Leader Exit Uses The Highest Active Protection Line
The system SHALL freeze the leader entry risk from eligible adjusted evidence and SHALL emit a full-exit decision when the latest eligible adjusted close reaches the highest active value among the immutable disaster stop, an armed one-R breakeven floor, and same-session adjusted MA5.

#### Scenario: Trend breaks while the holding remains profitable
- **WHEN** the adjusted close remains above the entry reference but closes at or below the adjusted MA5
- **THEN** the system emits a leader MA5 full-exit signal rather than waiting for an account loss

#### Scenario: One-R profit arms breakeven
- **WHEN** the adjusted closing high reaches one immutable entry risk unit
- **THEN** the round-trip-cost breakeven floor remains armed for that position episode and cannot move down

#### Scenario: Adjusted evidence is unavailable
- **WHEN** the required entry reference, ATR20, MA5, provider, receipt cutoff, revision, or common adjustment basis is unavailable or invalid
- **THEN** the system reports data waiting and sends no actionable email
