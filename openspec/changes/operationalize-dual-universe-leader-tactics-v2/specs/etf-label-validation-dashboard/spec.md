## ADDED Requirements

### Requirement: Leader-tactics evidence supports explicit ETF and A-share views
The research evidence dashboard SHALL provide an `ETF` and `个股` universe switch for leader-tactics V2, default to `ETF`, preserve the selected `as_of` cutoff, and never merge candidates or coverage denominators across universes.

#### Scenario: User switches to individual stocks
- **WHEN** the user selects `个股`
- **THEN** the dashboard requests the A-share research surface and shows its own candidate count, coverage, provenance, and availability state

#### Scenario: One universe is unavailable
- **WHEN** A-share evidence is unavailable but ETF evidence exists, or vice versa
- **THEN** the unavailable view shows its exact reason and the dashboard does not silently display the other universe

### Requirement: Leader-tactics candidates are filterable by formula and state
The dashboard SHALL offer formula filters for all, breakout, base launch, and former-leader repair, plus lifecycle filters for preparing, confirmed, and invalidated, and SHALL show deterministic paginated candidate rows.

#### Scenario: User filters confirmed base-launch candidates
- **WHEN** the selected formula is base launch and the selected state is confirmed
- **THEN** only matching rows are displayed with code, name, theme, score, signal date, confirmation date, and cutoff

#### Scenario: Filtered result is empty
- **WHEN** no row matches the selected filters
- **THEN** the page shows a Chinese empty state with candidate and exclusion counts rather than implying a loading failure

### Requirement: Dashboard distinguishes disclosed rules from transparent proxies
The leader-tactics panel SHALL display the source-disclosed conditions, the exact transparent proxy identity, unavailable proprietary elements, and a visible statement that the result is research evidence rather than the author's original signal or an investment recommendation.

#### Scenario: User opens candidate details
- **WHEN** a candidate row is expanded
- **THEN** the dashboard shows every gate fact, failed or passed state, formula version, source identity, data cutoff, manifest hash, and non-equivalence notice

### Requirement: Dashboard separates lifecycle, validation, and live provenance
The dashboard SHALL present preparing or confirmed research signals, historical replay outcomes, validation conclusions, notifications, and executions as separate evidence layers.

#### Scenario: A confirmed research signal exists without execution
- **WHEN** the V2 state machine confirms a candidate but no production notification or user-confirmed trade exists
- **THEN** the page shows a confirmed research proxy while notification and execution remain unavailable

#### Scenario: Validation samples are insufficient
- **WHEN** the promotion gates lack enough factual PIT sessions or independent dates
- **THEN** the panel displays `样本不足` with exact counts and MUST NOT describe the formula as validated or currently usable for live trading
