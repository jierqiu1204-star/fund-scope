## ADDED Requirements

### Requirement: Tracked ETF exit signals map to position actions
Tracked ETF exit evaluations SHALL expose a position action and action reason alongside the existing primary exit signal, and actionable reductions SHALL use an absolute target against an immutable exposure baseline.

#### Scenario: Action is returned with signal
- **WHEN** a tracked ETF is evaluated for hard stop, trailing take-profit, trend weakening, take-profit watch, or exit watch
- **THEN** the response includes `position_action`, `recommended_action_label`, absolute target remaining exposure, immutable-baseline identity, proposed action status when actionable, recommended trade amount, and a human-readable reason

#### Scenario: Guard-only signal does not sell
- **WHEN** a tracked ETF only has an unconfirmed trend-weakening signal
- **THEN** the response marks the signal as guard-only, returns `position_action=no_add`, and MUST NOT create a sell-or-reduce email

#### Scenario: Take-profit watch does not reduce
- **WHEN** a tracked ETF only triggers `take_profit_watch`
- **THEN** the response returns `position_action=hold` and a 100% remaining target, MAY expose a soft watch notification, and MUST NOT create a trim, reduce, or exit action

### Requirement: Tracked ETF exit state supports reentry
Tracked ETF exit evaluations SHALL preserve recommendation and owner-confirmed execution as separate facts and SHALL derive reentry cooldown only from the first owner-confirmed partial/full reduction or exit execution.

#### Scenario: Recommendation state is persisted
- **WHEN** a tracked ETF receives a reduce or exit recommendation without an owner-confirmed execution fact
- **THEN** the audit context records the proposed absolute target, immutable exposure baseline, trigger signal, policy version, and recommendation time without recording an execution price or starting reentry cooldown

#### Scenario: Execution state starts cooldown
- **WHEN** the owner confirms a partial/full reduction or exit with execution time, quantity, price/source, and resulting position facts
- **THEN** the audit records immutable execution provenance and uses the first real fill as the versioned reentry cooldown origin

#### Scenario: Reentry state is exposed
- **WHEN** a previously reduced or exited ETF meets reentry conditions
- **THEN** the tracked-position response includes a reentry candidate state without automatically creating a buy transaction

### Requirement: Position action emails include sizing context
Actionable ETF holding emails SHALL include the proposed absolute-target action and sizing context without implying automatic execution, and notification delivery state SHALL NOT update action or reentry state.

#### Scenario: Reduction email
- **WHEN** a tracked ETF sends an actionable trailing take-profit or trend-confirmed reduction email
- **THEN** the email includes the absolute target remaining fraction against the immutable exposure baseline, estimated amount/shares, price source, `proposed/未自动执行` status, and manual confirmation language

#### Scenario: SMTP status is notification-only
- **WHEN** an action-oriented email is sent, accepted, failed, retried, suppressed, or grouped
- **THEN** the notification result is audited separately and MUST NOT mark the action executed or start/extend reentry cooldown

#### Scenario: Reentry is not auto-emailed as buy instruction
- **WHEN** a reduced or exited ETF becomes a reentry candidate
- **THEN** the system MAY show the state on the page but MUST NOT send a buy instruction email unless a future approved requirement explicitly enables it
