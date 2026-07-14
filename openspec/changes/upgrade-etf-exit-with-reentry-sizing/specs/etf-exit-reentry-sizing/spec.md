## ADDED Requirements

### Requirement: ETF exit decisions produce position actions
The system SHALL convert eligible ETF exit signals into explicit position actions instead of treating every signal as a sell instruction, and every actionable reduction or exit SHALL use an absolute `target_remaining_fraction` against an immutable exposure baseline rather than multiplying the position remaining at evaluation time.

#### Scenario: Hard stop maps to exit or reduce
- **WHEN** a tracked ETF breaches a hard-stop threshold using decision-eligible data
- **THEN** the exit decision returns an absolute remaining target of 0% when full exit is allowed, otherwise the versioned absolute reduce target, and repeated evaluation MUST NOT compound the target

#### Scenario: Trailing take profit maps to partial reduction
- **WHEN** a tracked ETF triggers trailing take-profit while the ETF remains high ranked and the broader trend is not broken
- **THEN** the exit decision returns the versioned absolute partial target against the immutable exposure baseline rather than defaulting to full liquidation or repeatedly reducing the current remainder

#### Scenario: Trend weakening starts as guard
- **WHEN** trend weakening is detected without loss, material giveback, ranking deterioration, or market-regime deterioration
- **THEN** the exit decision returns `position_action=no_add` and MUST NOT send a sell email

#### Scenario: Take-profit watch remains observation-only
- **WHEN** a tracked ETF reaches the take-profit-watch threshold without a higher-priority actionable rule
- **THEN** the decision returns `position_action=hold` with a 100% remaining target, MAY create an explicitly soft watch notification, and MUST NOT create a trim, reduce, or exit action

### Requirement: ETF exits support reentry candidates
The system SHALL evaluate whether an ETF with an owner-confirmed partial or full reduce/exit execution can become a reentry candidate after conditions recover; recommendation creation, alert/email status, notification cooldown, and simulated execution MUST NOT prove execution or start reentry cooldown.

#### Scenario: Reentry after cooldown
- **WHEN** an ETF has an owner-confirmed partial/full reduce or exit execution and the execution-origin cooldown period has elapsed
- **THEN** the system evaluates current ranking, entry timing, theme trend, and data reliability before returning `reentry_candidate`

#### Scenario: Recommendation does not start cooldown
- **WHEN** a reduce or exit recommendation is proposed, acknowledged, emailed, retried, or suppressed without an owner-confirmed execution fact
- **THEN** the system keeps execution provenance unknown, does not advance the action, and does not start or extend reentry cooldown

#### Scenario: Notification cooldown is independent
- **WHEN** notification policy suppresses or repeats an ETF reminder
- **THEN** only notification eligibility changes and action eligibility, action status, and reentry cooldown remain unchanged

#### Scenario: Reentry blocked by weak conditions
- **WHEN** an exited ETF remains outside the accepted ranking bucket or has a weak entry timing label
- **THEN** the system keeps the position action as waiting and explains why reentry is blocked

### Requirement: ETF exit thresholds are bucket-specific
The system SHALL derive ETF exit thresholds from ETF type, theme, volatility bucket, historical percentile, and position profit state.

#### Scenario: High-volatility theme ETF
- **WHEN** a tracked ETF belongs to a high-volatility theme bucket
- **THEN** the system uses wider hard-stop and trailing-giveback candidates than it would for a low-volatility broad-market ETF, within configured safety bounds

#### Scenario: Bond or money ETF
- **WHEN** a tracked ETF belongs to a defensive low-volatility bucket
- **THEN** the system uses tighter risk thresholds and lower profit activation requirements, unless sample evidence is insufficient

#### Scenario: Bucket evidence is insufficient
- **WHEN** a bucket lacks enough historical samples or intraday evidence
- **THEN** the system returns conservative default thresholds marked as insufficient evidence and MUST NOT promote bucket-specific parameters to live usage

### Requirement: ETF exit V2 evidence measures missed upside and protection
The system SHALL measure ETF exit V2 by missed upside, protection success, drawdown improvement, reentry performance, turnover, and alert count.

#### Scenario: Exit sells before continued rally
- **WHEN** an exit action is followed by strong continued gains before a valid reentry
- **THEN** the evidence records missed upside and counts the event as potential over-exit

#### Scenario: Exit prevents deeper drawdown
- **WHEN** an exit or reduction avoids a subsequent drawdown beyond the configured risk window
- **THEN** the evidence records protection success and drawdown improvement

#### Scenario: V2 compared with TopN hold
- **WHEN** ETF exit V2 is backtested
- **THEN** the result compares V2 against TopN fixed hold, executes each unique absolute-target action cycle at most once under its declared simulated-execution model, ignores notification repeats as trades, and MUST NOT claim improvement unless V2 improves risk-adjusted results or materially reduces drawdown without unacceptable missed upside
