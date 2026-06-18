## MODIFIED Requirements

### Requirement: ETF Tracking Uses Intraday Price First
The system SHALL calculate active ETF tracking snapshots using fresh intraday quotes before falling back to daily close data, while clearly marking fallback data as non-intraday.

#### Scenario: Current price uses fresh quote
- **WHEN** an active ETF tracking record has a stored intraday quote no older than 3 minutes
- **THEN** current price, estimated value, estimated P&L, max profit, and giveback are calculated from that quote

#### Scenario: Fallback to daily close
- **WHEN** no fresh intraday quote exists for an active ETF tracking record
- **THEN** the tracking snapshot uses the latest daily close, returns `price_source=daily_close`, and states that the value is not a live intraday price

#### Scenario: Daily close fallback cannot trigger intraday email
- **WHEN** the intraday watch job evaluates an ETF tracking snapshot using `price_source=daily_close`
- **THEN** it MUST NOT send `hard_stop`, `trailing_take_profit`, `trend_weakening`, or `take_profit_watch` email from that snapshot

#### Scenario: Manual entry price is preserved
- **WHEN** the user supplies an actual ETF execution price while creating or updating a tracked position
- **THEN** the system uses that manual price as entry price and does not overwrite it with later quote data

#### Scenario: Missing manual entry can use spot estimate
- **WHEN** the user creates an ETF tracking record during market hours without an actual execution price
- **THEN** the system uses the latest fresh quote as the estimated entry price and marks it as `price_source=intraday_quote`

### Requirement: Dynamic Intraday Exit Signals Are Explainable
The system SHALL produce dynamic ETF sell-or-reduce reminders using volatility, trend, liquidity, and structure metrics, but actionable intraday reminder emails SHALL require fresh intraday price data.

#### Scenario: Dynamic hard stop triggers
- **WHEN** an active ETF tracking record's fresh intraday estimated loss breaches its volatility-adjusted hard stop threshold
- **THEN** the system creates an urgent hard-stop reminder with the current loss, threshold, volatility unit, price source, and quote time

#### Scenario: Dynamic trailing profit triggers
- **WHEN** an active ETF tracking record has reached the dynamic profit-protection start level and then gives back more than the dynamic trailing threshold using fresh intraday data
- **THEN** the system creates a warning-level trailing-profit reminder with highest profit, current profit, giveback, threshold, price source, and quote time

#### Scenario: Trend weakening triggers
- **WHEN** an active ETF tracking record's fresh intraday price breaks below short moving-average or VWAP proxy conditions and recent daily momentum is negative
- **THEN** the system creates a trend-weakening reminder explaining the broken trend conditions and that fresh intraday data was used

#### Scenario: Structural risk does not become a command
- **WHEN** an ETF has wide spread, abnormal premium/discount, stale IOPV, or weak turnover but no reliable price-based threshold breach
- **THEN** the system records a risk warning or watch-level signal without presenting it as a direct sell instruction and without sending a sell-or-reduce email

#### Scenario: Ranked status remains independent from holding action
- **WHEN** an ETF remains highly ranked during intraday monitoring but the tracked holding breaches an exit threshold
- **THEN** the system keeps the ranked observation label and tracked-position handling signal as separate fields and explanations

### Requirement: Intraday Alerts Are Deduplicated And Auditable
The system SHALL avoid duplicate intraday alert emails while preserving meaningful alert history for review.

#### Scenario: Same alert type cools down
- **WHEN** the same tracked ETF triggers the same intraday alert type repeatedly within 30 minutes
- **THEN** the system suppresses the duplicate without sending another email and MUST NOT insert a new suppressed alert row for every repeated minute

#### Scenario: Suppression summary is visible
- **WHEN** an intraday job suppresses duplicate alerts
- **THEN** the job result includes suppression counts or status so operators can see that deduplication happened

#### Scenario: Hard stop can escalate
- **WHEN** a hard-stop condition worsens materially after an earlier same-day reminder
- **THEN** the system may send one additional urgent email with the new loss level and updated quote time

#### Scenario: Alert history is returned
- **WHEN** the user opens a tracked ETF detail view
- **THEN** the API returns recent meaningful intraday and daily alert records with alert type, level, trigger reason, quote time, email status, and suppression status when stored

### Requirement: Intraday ETF Profit Watch Can Notify Without Treating Data Warnings As Sell Signals
The system SHALL allow profitable tracked ETFs to send soft take-profit-watch emails while keeping stale quote, missing IOPV, fallback price, and structure warnings as web-only data quality messages.

#### Scenario: Profitable ETF reaches watch threshold
- **WHEN** a tracked ETF has fresh intraday quote data and reaches its dynamic take-profit-watch threshold
- **THEN** the system may send one `止盈观察提醒` email subject to alert deduplication and cooldown rules

#### Scenario: Missing IOPV remains web-only
- **WHEN** a tracked ETF only has missing IOPV or structure-warning context without a holding alert threshold breach
- **THEN** the system displays the warning on the page and MUST NOT send a sell, reduce, or take-profit email

#### Scenario: Fallback price remains web-only during intraday watch
- **WHEN** a tracked ETF only has daily close, stale quote, or provider fallback price during intraday monitoring
- **THEN** the system displays the estimated value as limited data and MUST NOT send a `止盈观察提醒` email
