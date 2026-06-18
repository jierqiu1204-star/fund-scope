## ADDED Requirements

### Requirement: Market Data Reliability Is Explicit
The system SHALL classify market data and generated explanations by reliability before using them for research display or tracked-position reminders.

#### Scenario: Fresh intraday ETF quote is classified
- **WHEN** an ETF quote is from the intraday quote provider and is within the configured freshness window during market hours
- **THEN** the system marks it as `fresh_intraday` and includes quote time, source, and freshness status in API responses

#### Scenario: Daily close fallback is classified
- **WHEN** an ETF tracking snapshot uses the latest daily close because no fresh intraday quote exists
- **THEN** the system marks it as `daily_close` and states that it is not a live intraday price

#### Scenario: Stale quote is classified
- **WHEN** a quote is older than the freshness window during market hours
- **THEN** the system marks it as stale and MUST NOT present it as fresh real-time data

#### Scenario: Missing data is classified
- **WHEN** no usable price, NAV, or quote exists for a tracked asset
- **THEN** the system returns a data-waiting status and MUST NOT create an actionable reminder

### Requirement: Fallback Data Cannot Silently Drive Actionable Emails
The system SHALL prevent fallback, stale, or incomplete data from being treated as the same quality as fresh actionable market data.

#### Scenario: Intraday ETF alert requires fresh intraday data
- **WHEN** an ETF tracked position is evaluated by the intraday watch job
- **THEN** `hard_stop`, `trailing_take_profit`, `trend_weakening`, and `take_profit_watch` emails are eligible only if the current price source is `fresh_intraday`

#### Scenario: Daily close is limited to daily review
- **WHEN** a tracked ETF is evaluated outside intraday monitoring using daily close data
- **THEN** the reminder explanation states that the result is a daily close review rather than a live intraday signal

#### Scenario: Data quality warning stays web-only
- **WHEN** an ETF has missing IOPV, stale quote, unavailable premium/discount, or provider degradation without an actionable signal based on reliable price data
- **THEN** the system shows a web-only data quality warning and MUST NOT send a sell, reduce, stop, or take-profit email

### Requirement: AI And Rule Fallbacks Are Labeled
The system SHALL label AI-generated, partially rule-completed, and fully rule-based explanations so users can distinguish model output from deterministic fallback.

#### Scenario: Model output is complete
- **WHEN** the model returns a valid complete advisor report
- **THEN** the UI labels the report as AI-generated and shows model metadata

#### Scenario: Model output is partially completed by rules
- **WHEN** the model returns usable content but required fields are filled from rule fallback
- **THEN** the UI labels the report as AI-generated with partial rule completion

#### Scenario: Model output is unavailable
- **WHEN** the model is disabled, fails, times out, or fails validation
- **THEN** the UI labels the report as rule fallback and states that deterministic rules are being used
