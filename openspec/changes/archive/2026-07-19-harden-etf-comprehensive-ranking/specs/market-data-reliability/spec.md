## MODIFIED Requirements

### Requirement: Research Calculations Use Decision-Eligible Data Only
The system SHALL use only finite, traceable, point-in-time verified or accepted alternate-provider market data with the declared price basis for comprehensive ranking, label validation, portfolio weights, intraday score adjustments, and email-triggering holding signals.

#### Scenario: Estimated data exists
- **WHEN** a value is estimated, stale, unavailable, generated from AI/rule fallback text, uses an incompatible price basis, or lacks source date metadata
- **THEN** it may be displayed with a limitation note but MUST NOT contribute to ranking, label validation, portfolio target weight, live score, or email-triggering logic

#### Scenario: Alternate provider real data exists
- **WHEN** a backup provider supplies real market data with compatible basis, valid date, source, adjustment version, and freshness metadata
- **THEN** the system may use it for declared calculations while recording provider source and reliability level

#### Scenario: Numeric value is not finite
- **WHEN** an input or derived financial metric is NaN or infinite
- **THEN** it is treated as unavailable, excluded from sorting and validation, and reported with a readable reason

### Requirement: Daily Reference Cannot Override Intraday Context
The system SHALL keep daily snapshot identity and intraday quote identity separate and SHALL require both a compatible fresh daily base and eligible fresh quote before presenting an intraday comprehensive score.

#### Scenario: Fresh intraday quote and compatible daily snapshot exist
- **WHEN** an ETF has a fresh eligible intraday quote and a current compatible daily ranking snapshot
- **THEN** the live API may calculate intraday adjustment and timing fields while retaining the daily snapshot id and score version

#### Scenario: Fresh quote exists but daily snapshot is stale
- **WHEN** an ETF has a fresh intraday quote but its daily ranking snapshot is stale or contract-incompatible
- **THEN** the quote may be displayed separately but the API MUST NOT present an intraday comprehensive score or claim that the stale base became current

#### Scenario: Market is closed
- **WHEN** the exchange calendar reports a closed session and no fresh intraday quote is expected
- **THEN** the UI labels the result as closed-market or daily reference instead of stale realtime data

## ADDED Requirements

### Requirement: Research Price Series Are Total-Return-Aware And Auditable
ETF return, volatility, drawdown, percentile, and forward-outcome calculations SHALL use a traceable total-return-aware research series, while fund cross-distribution returns SHALL use accumulated NAV, and raw values SHALL remain separately identifiable.

#### Scenario: ETF distributes cash or changes units
- **WHEN** an ETF undergoes a distribution, split, merge, or unit adjustment inside a calculation window
- **THEN** research returns reflect economic total return and do not report the mechanical price adjustment as market loss or gain

#### Scenario: Fund distributes income
- **WHEN** a fund's unit NAV drops because of a distribution while accumulated NAV preserves economic value
- **THEN** research return uses accumulated NAV and retains unit NAV only for appropriate display context

#### Scenario: Adjustment provenance is missing
- **WHEN** a historical value has no traceable adjustment mode or provider version
- **THEN** it is not used for current-contract ranking or validation and remains legacy/display-only

### Requirement: Cross-Sectional Ranking Uses One Trade Date
All decision-eligible assets included in one comprehensive ranking snapshot SHALL use market data from the same exchange trade date and the snapshot SHALL report expected-universe and included-universe coverage.

#### Scenario: One asset has only an older daily close
- **WHEN** the current trade-date barrier is evaluated
- **THEN** the asset is excluded with a stale-date reason and is not compared against current-date peers

#### Scenario: Coverage is below threshold
- **WHEN** same-date compatible data covers less than the configured publication threshold
- **THEN** the workflow does not publish a canonical ranking and returns a waiting or partial-data state

### Requirement: Intraday Display Quotes Cannot Become Official Daily Research Data
The system SHALL NOT promote an intraday quote, server-time fallback, diverged quote, display-only quote, or pre-close approximation into verified daily research history solely because the clock has passed a configured time.

#### Scenario: Quote exists shortly before exchange close
- **WHEN** an intraday quote is captured between 14:55 and the official closing data publication
- **THEN** it may be stored as intraday evidence but does not satisfy the verified daily-close publication gate

#### Scenario: Official daily provider is unavailable
- **WHEN** no compatible verified daily value is available after close
- **THEN** the ranking workflow waits or publishes a partial failure and does not fill the daily row from display-only intraday data

### Requirement: Exchange Session State Uses An Exchange Calendar
Market-open, lunch-break, close, freshness, and next-required-trade-date decisions SHALL use an Asia/Shanghai exchange calendar including holidays and exceptional sessions.

#### Scenario: Statutory holiday falls on a weekday
- **WHEN** the local clock is inside ordinary weekday trading hours but the exchange calendar is closed
- **THEN** the system reports closed, skips live decision processing, and does not schedule polling as an open session

#### Scenario: Session crosses a boundary
- **WHEN** time crosses open, lunch, afternoon reopen, or close
- **THEN** the API reports the new session state and a next polling interval appropriate to that state
