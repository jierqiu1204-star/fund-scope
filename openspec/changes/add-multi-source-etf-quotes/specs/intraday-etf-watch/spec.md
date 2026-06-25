## ADDED Requirements

### Requirement: Intraday ETF Quotes Are Cross-Validated Across Providers
The system SHALL fetch ETF intraday quotes from multiple public providers and derive one primary quote only after normalizing and validating provider results.

#### Scenario: Multiple providers return consistent fresh quotes
- **WHEN** two or more configured providers return valid fresh quotes for the same ETF within the freshness window
- **THEN** the system stores a primary quote with `consensus_status=consistent`, records provider details, and marks the quote decision-eligible

#### Scenario: Only one provider returns a valid fresh quote
- **WHEN** exactly one configured provider returns a valid fresh quote and other providers fail, time out, or omit the ETF
- **THEN** the system may store that quote with `consensus_status=single_provider`, records the missing providers, and keeps it decision-eligible only if quote time and required price fields are verified

#### Scenario: Provider prices diverge
- **WHEN** two or more fresh provider quotes for the same ETF differ beyond the configured tolerance
- **THEN** the system stores or returns the newest display quote with `consensus_status=diverged` and MUST NOT use it for realtime score decisions or tracked-position email alerts

#### Scenario: Provider quote time is missing
- **WHEN** a provider quote has no usable quote time and requires server-time fallback
- **THEN** that provider quote is excluded from decision eligibility and recorded as display-only evidence

### Requirement: Intraday ETF Watch Job Reports Provider Health
The system SHALL include provider-level health and cross-validation summaries in intraday watch job results.

#### Scenario: Job completes with mixed provider results
- **WHEN** one provider succeeds and another provider fails during the same watch job
- **THEN** the job result includes provider success counts, failure counts, timeout/error summaries, and the number of quotes that are consistent, single-provider, diverged, stale, or unavailable

#### Scenario: All providers fail
- **WHEN** every configured provider fails or times out
- **THEN** the job returns a readable failure or cache-only status, does not create decision-eligible quotes, and does not send ETF holding alert emails from cached data

### Requirement: Intraday Ranking Uses Cross-Validated Quote Eligibility
The system SHALL apply intraday ranking adjustments only when the selected quote is fresh and cross-validation eligible.

#### Scenario: Quote is cross-validation eligible
- **WHEN** the primary quote for an ETF is fresh and has `consensus_status=consistent` or eligible `single_provider`
- **THEN** the live ranking may use its price, change percent, turnover, and timing evidence for realtime score and buy-point labels

#### Scenario: Quote is not cross-validation eligible
- **WHEN** the primary quote is diverged, stale, server-time fallback, estimated, or unavailable
- **THEN** the live ranking returns daily score or unavailable realtime status and MUST NOT present the quote as realtime decision evidence

### Requirement: Tracked ETF Alerts Use Cross-Validated Quotes
The system SHALL send ETF tracked-position emails only when the current price comes from a fresh cross-validation eligible quote.

#### Scenario: Eligible quote triggers holding signal
- **WHEN** a tracked ETF breaches hard stop, trailing take profit, trend weakening, take profit watch, or exit watch using a cross-validation eligible quote
- **THEN** the alert audit records provider consensus status, selected source, quote time, and price difference summary

#### Scenario: Diverged quote would cross a threshold
- **WHEN** a tracked ETF threshold would be crossed only by a diverged or display-only provider quote
- **THEN** the system returns a web-only data-quality warning and MUST NOT send a sell, reduce, stop, or take-profit email
