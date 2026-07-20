## MODIFIED Requirements

### Requirement: Exit Signals Are Prioritized And Explainable
The system SHALL choose at most one primary exit signal per tracked position evaluation and SHALL expose the reason, threshold, current value, data source, data eligibility, policy version, alert episode, and absolute target remaining fraction when an action is proposed.

#### Scenario: Hard stop has highest priority
- **WHEN** the current estimated loss breaches the hard-stop threshold using an eligible price source
- **THEN** the primary exit signal is `hard_stop`, marked urgent, and explains current loss, threshold, current price, price source, email eligibility, and an absolute target remaining fraction of 0

#### Scenario: Trailing take-profit protects existing profit
- **WHEN** the tracked position has reached the profit-protection start threshold and then gives back more than the trailing-giveback threshold using an eligible price source
- **THEN** the primary exit signal is `trailing_take_profit` and explains highest profit, current profit, giveback, threshold, price source, email eligibility, and the versioned absolute target

#### Scenario: Trend weakening is below profit-protection priority
- **WHEN** price or NAV breaks below short moving-average conditions and recent momentum turns negative without a higher-priority hard stop or trailing take-profit
- **THEN** the primary exit signal is `trend_weakening`, explains the broken trend conditions, and remains `no_add/hold` until the versioned confirmation rule produces `confirmed_trend_weakening`

#### Scenario: Take-profit watch is a soft email-capable reminder
- **WHEN** profit reaches the dynamic take-profit-watch threshold without a higher-priority exit signal
- **THEN** the system returns `take_profit_watch` as a soft holding reminder that may send a `止盈观察提醒` email, reports `hold` with a 100% remaining target, and MUST NOT create a trim, reduce, or exit action

#### Scenario: Ineligible data produces explanation but no email
- **WHEN** a threshold would be crossed only under a stale quote, daily close fallback during intraday watch, missing same-contract evidence, or missing data-quality context
- **THEN** the system records an explicit data-waiting/error explanation, preserves the prior alert episode state, and marks the evaluation as ineligible for email and action

### Requirement: Exit Emails Are Restricted To Actionable Holding Signals
The system SHALL send immediate action-oriented email only for tracked-position action decisions supported by eligible data, MAY send explicitly non-actionable watch summaries, and SHALL keep data warnings and unconfirmed guard states web-only.

#### Scenario: Actionable signal sends email
- **WHEN** a tracked position creates a new action decision from `hard_stop`, `trailing_take_profit`, `confirmed_trend_weakening`, or eligible `exit_watch` for the job type
- **THEN** the system may send an email when SMTP and user preferences permit, links it to the existing action decision, and records `email_status=smtp_accepted`, `failed`, or `unknown` without changing the action status or claiming end-to-end delivery

#### Scenario: Take-profit watch email is clearly soft
- **WHEN** a `take_profit_watch` email is sent
- **THEN** the subject and body identify it as a `止盈观察提醒`, include current profit, dynamic threshold, highest profit, giveback, and data source, and state that no reduce action was generated and the current recommendation is `hold/仅观察`

#### Scenario: Take-profit watch cooldown checks sent email only
- **WHEN** the system checks the legacy `take_profit_watch` cooldown or the new trading-day repeat slot
- **THEN** only previous notifications with `email_status=smtp_accepted` or a migration-compatible legacy `sent` count as notification blockers, and the result MUST NOT affect action eligibility

#### Scenario: Web-only or suppressed history does not block first email
- **WHEN** a tracked position only has previous `web_only`, `skipped`, `suppressed`, or failed `take_profit_watch` notification records in the current alert episode
- **THEN** the next eligible repeat slot may send the first real watch email while still creating no position action

#### Scenario: Data warning does not send email
- **WHEN** a tracked position has stale data, missing IOPV, wide spread, abnormal premium/discount, fallback price, provider failure, or liquidity warning without an actionable exit signal based on eligible data
- **THEN** the system records or returns a data/web-only warning with `email_status=skipped` or equivalent non-email status and creates no position action

#### Scenario: Duplicate intraday alert is suppressed without noisy inserts
- **WHEN** the same tracked ETF remains in the same intraday firing episode inside the configured notification window
- **THEN** the duplicate email is suppressed and the system MUST NOT insert a new suppressed alert row for every repeated poll or create another action, while a materially worsened hard stop may use a later notification repeat slot linked to the original action

### Requirement: ETF Exit Signals Are Replayable
The tracked-position exit strategy SHALL persist enough versioned threshold, episode, data, action, and notification context to replay why a signal changed state, why an action was or was not proposed, and why a notification was or was not sent.

#### Scenario: Email alert is sent
- **WHEN** an ETF tracked-position email is sent
- **THEN** the audit stores entry price, current price, quote time, quote reliability, highest profit, current profit, giveback, threshold values, input snapshot hash, policy/rule version, position/exposure/action/alert episode ids, linked action decision id, notification item/envelope ids, and trigger reason

#### Scenario: Email alert is not sent
- **WHEN** an ETF tracked position does not send an email because thresholds are not crossed, the episode did not transition, the repeat slot is blocked, or data is ineligible
- **THEN** the latest position snapshot and audit expose the main no-email reason and whether the outcome was normal, pending, persistent firing, data waiting, suppressed, or web-only

## ADDED Requirements

### Requirement: ETF exit rules map to absolute position targets
The ETF tracked-position strategy SHALL map each actionable rule to a versioned absolute target remaining fraction and SHALL aggregate simultaneous candidates before sizing a trade.

#### Scenario: Half-position rule repeats
- **WHEN** `trailing_take_profit` or `confirmed_trend_weakening` remains active after producing a 50% absolute target
- **THEN** later evaluations keep the target at 50% of the immutable exposure baseline and MUST NOT calculate 50% of the already reduced position

#### Scenario: Hard stop is actionable once
- **WHEN** an eligible hard stop first fires for a position episode
- **THEN** the strategy proposes one 0% target in the current action cycle and later reminders reuse that action unless a new exposure/action cycle is validly opened

#### Scenario: Exit watch requires same-contract evidence
- **WHEN** `exit_watch` is evaluated with eligible point-in-time ranking and research evidence whose contract hash matches the current policy
- **THEN** the strategy may return its versioned absolute exit/reduce target, while missing or mismatched evidence returns no action

#### Scenario: Multiple rules choose one target
- **WHEN** more than one actionable rule is valid for a tracked position at the same cutoff
- **THEN** the strategy returns one most-conservative absolute target and retains every contributing reason for audit

### Requirement: ETF re-entry cooldown uses real execution facts
The ETF tracked-position strategy SHALL derive re-entry cooldown from the first owner-confirmed partial/full execution event and MUST NOT derive it from alert creation, recommendation time, notification status, or a simulated fill.

#### Scenario: Action email is accepted by SMTP
- **WHEN** an action-oriented email reaches `smtp_accepted` while its action remains proposed
- **THEN** the tracked position does not start or extend re-entry cooldown

#### Scenario: Owner reports a real reduction
- **WHEN** the owner records the first valid partial or complete execution for a reduce/exit action
- **THEN** the re-entry cooldown starts from that execution event under the versioned re-entry policy

#### Scenario: Legacy recommendation has no execution proof
- **WHEN** a prior `latest_position_action` or sent email cannot prove execution
- **THEN** it remains `legacy_unverified` provenance and MUST NOT block re-entry under the new policy
