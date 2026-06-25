# market-data-reliability Specification

## Purpose
TBD - created by archiving change harden-financial-alert-data-safety. Update Purpose after archive.
## Requirements
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

### Requirement: Research Calculations Use Decision-Eligible Data Only
The system SHALL use only verified or alternate-provider real market data for label validation, portfolio weights, intraday score adjustments, and email-triggering holding signals.

#### Scenario: Estimated data exists
- **WHEN** a value is estimated, stale, unavailable, or generated from AI/rule fallback text
- **THEN** it may be displayed with a limitation note but MUST NOT contribute to scores, label validation, portfolio target weight, or email-triggering logic

#### Scenario: Alternate provider real data exists
- **WHEN** a backup provider supplies real market data with valid date, source, and freshness metadata
- **THEN** the system may use it for calculations while recording provider source and reliability level

### Requirement: Data Reliability Is Auditable In Research Outputs
The system SHALL expose data reliability summaries for ranking, label validation, portfolio weights, and tracked-position alerts.

#### Scenario: User views ranking or portfolio
- **WHEN** the UI displays a score, validation result, or portfolio weight
- **THEN** it shows the relevant data date, source, reliability status, and whether missing data limited the calculation

#### Scenario: Email alert is evaluated
- **WHEN** the system evaluates a tracked-position email alert
- **THEN** the alert record includes price source, quote time or NAV date, reliability level, and email eligibility reason

### Requirement: Missing Data Prefers Waiting Over Fake Numbers
The system SHALL prefer waiting states over numeric placeholders when financial data is missing.

#### Scenario: Missing current value
- **WHEN** holdings, tracked positions, or research metrics lack usable price or NAV data
- **THEN** the API returns unavailable or null values with a readable status instead of returning zero, negative 100 percent, or fake fallback values

### Requirement: Performance Optimizations Preserve Data Reliability Boundaries
The system SHALL distinguish hot raw data, long-term verified data, and display-only derived summaries when optimizing storage and queries.

#### Scenario: Raw intraday data is retained only as hot data
- **WHEN** raw ETF intraday rows exceed the retention window
- **THEN** they are removed only after verified daily data, summaries, signal caches, or holding state needed for research have been preserved

#### Scenario: Old or summarized data cannot trigger live emails
- **WHEN** only daily close data or intraday daily summary data is available
- **THEN** the system does not treat it as fresh intraday data for live email reminders

### Requirement: Query Optimizations Do Not Introduce Misleading Fallbacks
The system SHALL prefer no value or display-only labels over estimated fallback values in optimized query paths.

#### Scenario: Latest fresh quote is unavailable
- **WHEN** optimized live ranking or tracked position queries cannot find a fresh intraday quote
- **THEN** the response marks the data as daily, stale, or unavailable and does not expose it as a live decision-eligible quote

#### Scenario: Summary data is used for display
- **WHEN** daily intraday summary data is shown after raw quote cleanup
- **THEN** the UI or API marks it as summary/display data rather than current live quote data

### Requirement: Evidence data reliability labels
ETF label evidence, portfolio weights, and alert audits SHALL label data reliability consistently.

#### Scenario: Decision-eligible evidence
- **WHEN** evidence or portfolio calculations use verified or alternate-provider data
- **THEN** the result marks the data as decision-eligible and records its source

#### Scenario: Display-only evidence
- **WHEN** evidence or portfolio calculations encounter stale, estimated, or unavailable data
- **THEN** the result excludes it from decision calculations or marks it display-only with a reason

### Requirement: No fallback in financial decisions
The system SHALL NOT use fallback explanations, zero placeholders, stale quotes, or estimated prices to produce financial decision emails or observation weights.

#### Scenario: Fallback blocked
- **WHEN** only fallback or estimated data is available
- **THEN** the system shows an unavailable/display-only state instead of generating weights or email reminders

### Requirement: Reliability gates apply to validation and optimization
Market data reliability SHALL determine whether data may be used for signal validation, observation portfolio optimization, and adaptive exit reminders.

#### Scenario: Data is verified or alternate provider
- **WHEN** data is marked `verified` or `alternate_provider`
- **THEN** it may be used for validation, optimization, and reminders if freshness rules are also satisfied

#### Scenario: Data is estimated or stale
- **WHEN** data is marked `estimated` or `stale`
- **THEN** it may be displayed for context but MUST NOT be used for validation outcomes, optimized weights, or reminder emails

#### Scenario: Data is unavailable
- **WHEN** required data is unavailable
- **THEN** the system returns an explicit unavailable state instead of filling numeric outputs with zero or stale values

