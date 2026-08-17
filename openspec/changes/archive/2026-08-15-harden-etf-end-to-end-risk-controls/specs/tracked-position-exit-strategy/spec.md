## ADDED Requirements

### Requirement: ETF action emails require fresh explicit intraday eligibility
The system SHALL apply the same fail-closed fresh-quote gate to ETF action emails regardless of whether evaluation was started by an intraday or daily scheduled job.

#### Scenario: Daily ETF review only has closing price
- **WHEN** a daily ETF position review has a usable same-day close but no fresh explicitly decision-eligible intraday snapshot
- **THEN** the system may show the threshold context on the web but MUST NOT send an action email

#### Scenario: Daily ETF review sees an old intraday snapshot
- **WHEN** the latest stored ETF quote is stale, fallback, display-only, missing explicit eligibility, or provider-ineligible
- **THEN** the system records `data_ineligible` or `web_only` and MUST NOT send an action email

#### Scenario: Fund review uses confirmed NAV
- **WHEN** a tracked fund is evaluated by the daily job with decision-eligible confirmed NAV evidence
- **THEN** the fund-specific daily email behavior remains available and does not require ETF bid/ask fields

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
