## ADDED Requirements

### Requirement: Intraday ETF Watchlist Is Derived From Daily Signals
The system SHALL build the intraday ETF watchlist from the latest daily short-term ETF ranking plus active tracked ETF positions.

#### Scenario: Top 20 ETFs are selected
- **WHEN** a successful ETF short-term signal run exists
- **THEN** the intraday watchlist includes the top 20 ranked ETF signal items from that run

#### Scenario: Held ETFs remain watched
- **WHEN** an active tracked ETF is not in the latest top 20 ranking
- **THEN** the intraday watchlist still includes that ETF and marks its source as `tracked_position`

#### Scenario: Daily signal is unavailable
- **WHEN** no successful ETF signal run exists
- **THEN** the intraday watchlist includes active tracked ETFs only and returns a readable stale-or-missing-signal status

### Requirement: Intraday ETF Quotes Are Refreshed During Market Hours
The system SHALL refresh public ETF spot quotes for the intraday watchlist during A-share trading hours.

#### Scenario: Scheduled market-hour refresh
- **WHEN** the server time is within `09:30-11:30` or `13:00-15:00` Asia/Shanghai on a trading weekday
- **THEN** the scheduler runs the ETF intraday watch job every 60 seconds

#### Scenario: Off-hours refresh is skipped
- **WHEN** the intraday watch job is invoked outside configured market hours
- **THEN** the job skips external quote fetching and returns an off-hours status without sending trade alerts

#### Scenario: Quote snapshot is stored
- **WHEN** public ETF spot quotes are fetched successfully
- **THEN** the system stores latest price, quote time, change percent, volume, turnover, bid price, ask price, IOPV, premium/discount percent, source, and raw snapshot for watched ETFs

#### Scenario: Stale quote is visible
- **WHEN** the latest stored quote for an ETF is older than 3 minutes during market hours
- **THEN** the API marks the quote as stale and the UI does not present it as fresh real-time data

### Requirement: ETF Tracking Uses Intraday Price First
The system SHALL calculate active ETF tracking snapshots using fresh intraday quotes before falling back to daily close data.

#### Scenario: Current price uses fresh quote
- **WHEN** an active ETF tracking record has a stored intraday quote no older than 3 minutes
- **THEN** current price, estimated value, estimated P&L, max profit, and giveback are calculated from that quote

#### Scenario: Fallback to daily close
- **WHEN** no fresh intraday quote exists for an active ETF tracking record
- **THEN** the tracking snapshot uses the latest daily close and returns `price_source=daily_close`

#### Scenario: Manual entry price is preserved
- **WHEN** the user supplies an actual ETF execution price while creating or updating a tracked position
- **THEN** the system uses that manual price as entry price and does not overwrite it with later quote data

#### Scenario: Missing manual entry can use spot estimate
- **WHEN** the user creates an ETF tracking record during market hours without an actual execution price
- **THEN** the system uses the latest fresh quote as the estimated entry price and marks it as `price_source=intraday_quote`

### Requirement: Dynamic Intraday Exit Signals Are Explainable
The system SHALL produce dynamic ETF sell-or-reduce reminders using volatility, trend, liquidity, and structure metrics instead of fixed percentages only.

#### Scenario: Dynamic hard stop triggers
- **WHEN** an active ETF tracking record's intraday estimated loss breaches its volatility-adjusted hard stop threshold
- **THEN** the system creates an urgent hard-stop reminder with the current loss, threshold, volatility unit, and quote time

#### Scenario: Dynamic trailing profit triggers
- **WHEN** an active ETF tracking record has reached the dynamic profit-protection start level and then gives back more than the dynamic trailing threshold
- **THEN** the system creates a warning-level trailing-profit reminder with highest profit, current profit, giveback, and threshold

#### Scenario: Trend weakening triggers
- **WHEN** an active ETF tracking record's intraday price breaks below short moving-average or VWAP proxy conditions and recent daily momentum is negative
- **THEN** the system creates a trend-weakening reminder explaining the broken trend conditions

#### Scenario: Structural risk does not become a command
- **WHEN** an ETF has wide spread, abnormal premium/discount, stale IOPV, or weak turnover but no loss/profit-protection breach
- **THEN** the system records a risk warning or watch-level signal without presenting it as a direct sell instruction

### Requirement: Intraday Alerts Are Deduplicated And Auditable
The system SHALL avoid duplicate intraday alert emails while preserving alert history for review.

#### Scenario: Same alert type cools down
- **WHEN** the same tracked ETF triggers the same intraday alert type repeatedly within 30 minutes
- **THEN** the system records the duplicate as suppressed and does not send another email

#### Scenario: Hard stop can escalate
- **WHEN** a hard-stop condition worsens materially after an earlier same-day reminder
- **THEN** the system may send one additional urgent email with the new loss level and updated quote time

#### Scenario: Alert history is returned
- **WHEN** the user opens a tracked ETF detail view
- **THEN** the API returns recent intraday and daily alert records with alert type, level, trigger reason, quote time, email status, and suppression status

### Requirement: Intraday ETF Watch Is Visible In The Web UI
The system SHALL expose intraday ETF monitoring status and tracked ETF snapshots in the Chinese web interface.

#### Scenario: User opens short-term page
- **WHEN** the user opens `/short-term` in ETF mode
- **THEN** the page shows intraday watch status, latest quote refresh time, watched ETF count, top-20 source status, and data freshness in Chinese

#### Scenario: Tracked ETF card shows real-time fields
- **WHEN** an active ETF tracking card is displayed
- **THEN** it shows latest price, quote time, price source, estimated P&L, max profit, giveback, dynamic stop line, and current reminder status

#### Scenario: Public-data boundary is visible
- **WHEN** intraday ETF data is displayed
- **THEN** the UI states that prices come from public data, may be delayed, do not connect to the broker account, and do not execute trades

