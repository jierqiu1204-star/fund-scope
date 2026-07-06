## ADDED Requirements

### Requirement: Tracked ETF exit signals map to position actions
Tracked ETF exit evaluations SHALL expose a position action and action reason alongside the existing primary exit signal.

#### Scenario: Action is returned with signal
- **WHEN** a tracked ETF is evaluated for hard stop, trailing take-profit, trend weakening, take-profit watch, or exit watch
- **THEN** the response includes `position_action`, `recommended_action_label`, target exposure, recommended trade amount, and a human-readable reason

#### Scenario: Guard-only signal does not sell
- **WHEN** a tracked ETF only has an unconfirmed trend-weakening signal
- **THEN** the response marks the signal as guard-only, returns `position_action=no_add`, and MUST NOT create a sell-or-reduce email

### Requirement: Tracked ETF exit state supports reentry
Tracked ETF exit evaluations SHALL preserve enough state to evaluate cooldown and reentry eligibility after a reduction or exit.

#### Scenario: Exit state is persisted
- **WHEN** a tracked ETF receives a reduce or exit action
- **THEN** the audit context records action time, trigger signal, execution reference price, cooldown end, and reentry rule version

#### Scenario: Reentry state is exposed
- **WHEN** a previously reduced or exited ETF meets reentry conditions
- **THEN** the tracked-position response includes a reentry candidate state without automatically creating a buy transaction

### Requirement: Position action emails include sizing context
Actionable ETF holding emails SHALL include the proposed position action and sizing context without implying automatic execution.

#### Scenario: Reduction email
- **WHEN** a tracked ETF sends an actionable trailing take-profit or trend-confirmed reduction email
- **THEN** the email includes the suggested reduction percentage, estimated amount, estimated shares, price source, and manual confirmation language

#### Scenario: Reentry is not auto-emailed as buy instruction
- **WHEN** a reduced or exited ETF becomes a reentry candidate
- **THEN** the system MAY show the state on the page but MUST NOT send a buy instruction email unless a future approved requirement explicitly enables it
