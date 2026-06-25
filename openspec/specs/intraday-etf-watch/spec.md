# intraday-etf-watch Specification

## Purpose
TBD - created by archiving change add-intraday-etf-watch-top20. Update Purpose after archive.
## Requirements
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

### Requirement: Intraday Watch Surfaces Dynamic Threshold Context
The system SHALL expose enough dynamic-threshold context for users to understand ETF intraday holding alerts.

#### Scenario: Tracked ETF card is displayed
- **WHEN** an active tracked ETF appears in the short-term page or quote API
- **THEN** the response includes current price, price source, quote freshness, estimated profit/loss, highest profit, giveback, hard-stop threshold, trailing threshold, trend status, and latest actionable or web-only alert status

#### Scenario: Quote is stale during market hours
- **WHEN** a tracked ETF quote is older than the freshness threshold during market hours
- **THEN** the UI marks the quote as stale and MUST NOT treat stale quote structure warnings as a sell-or-reduce email trigger

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

### Requirement: Intraday Ranking Splits Base Score And Live Adjustment
The system SHALL expose daily base score, intraday adjustment score, live total score, score source, and readable contribution reasons for ETF live ranking.

#### Scenario: Fresh intraday quote exists
- **WHEN** an ETF has a fresh eligible intraday quote during market hours
- **THEN** the API returns score_source=intraday, daily base score, intraday adjustment score, live total score, quote time, and contribution reasons

#### Scenario: Market is closed or quote is stale
- **WHEN** the market is closed or the ETF quote is stale, estimated, or missing quote time
- **THEN** the API returns score_source=daily or unavailable and MUST NOT present the score as real-time

### Requirement: Intraday Watchlist Includes Top Signals And Observed Assets
The system SHALL watch ETF candidates from the latest top ranked daily signals, short-watch labels, high-watch labels, and active tracked positions.

#### Scenario: Watchlist is built from multiple sources
- **WHEN** a successful ETF signal run exists
- **THEN** the intraday watchlist includes top 20 ranked ETFs, ETFs labeled 短线观察, ETFs labeled 高位观察, and active tracked ETFs, with source tags for each item

#### Scenario: Watchlist source is visible
- **WHEN** the UI renders live ranking cards
- **THEN** it shows whether the ETF is watched because of top ranking, short-watch label, high-watch label, or user tracking

### Requirement: Intraday Timing Labels Are Data-Gated
The system SHALL calculate intraday timing labels only from fresh verified or alternate-provider intraday quotes.

#### Scenario: Timing label uses fresh quote
- **WHEN** fresh intraday quote data is available
- **THEN** the system may return timing labels such as 健康回踩, 趋势延续, 冲高别追, or 跌破等待 with contribution reasons

#### Scenario: Timing label cannot use fallback data
- **WHEN** only daily close, stale quote, missing quote time, or estimated price is available
- **THEN** the system returns 数据不足 or daily timing reference and MUST NOT use it for intraday email decisions

### Requirement: Intraday Watch Reads Only Latest Quote Per ETF
The system SHALL retrieve the latest intraday quote for each watched ETF in SQL without loading all historical intraday quote rows into application memory.

#### Scenario: Live ranking fetches latest quotes
- **WHEN** the live ETF ranking is built for a watchlist of ETF codes
- **THEN** the backend returns at most one latest quote row per ETF code from the database query

#### Scenario: Query remains efficient as quote history grows
- **WHEN** `etf_intraday_quotes` contains many days of minute-level rows
- **THEN** the latest quote query uses indexed per-code lookup and does not perform a full table scan plus application-side de-duplication

### Requirement: Intraday Quote Retention Is Bounded
The system SHALL keep raw intraday ETF quote details for the latest 60 trading days and remove older raw intraday rows through a scheduled or manually runnable cleanup job.

#### Scenario: Cleanup removes old raw intraday rows
- **WHEN** the cleanup job runs after the market day
- **THEN** rows older than the 60-trading-day cutoff are deleted from `etf_intraday_quotes`

#### Scenario: Cleanup does not remove decision history
- **WHEN** raw intraday rows are deleted
- **THEN** daily ETF price history, signal cache, alert records, and persisted holding high-water state remain available

### Requirement: Intraday Daily Summary Is Preserved
The system SHALL preserve daily intraday summaries for ETF analysis before or while raw intraday rows are removed.

#### Scenario: Daily summary records key intraday facts
- **WHEN** intraday quotes exist for an ETF trading day
- **THEN** the system stores or updates one daily summary containing last price, high, low, turnover, quote count, and data source

#### Scenario: Historical analysis can use summaries
- **WHEN** raw minute-level quotes are beyond the retention window
- **THEN** the system can still display daily intraday summary information without using stale raw rows for live decisions

### Requirement: Intraday watch scope audit
The intraday ETF watch job SHALL expose why each watched ETF is included in the watch scope.

#### Scenario: Watchlist source returned
- **WHEN** the intraday watch job completes
- **THEN** the result includes sources such as top20 signal, short/high observation label, and tracked position for each watched ETF

### Requirement: Intraday quote freshness audit
The intraday ETF watch job SHALL expose whether quotes are fresh enough for decisions.

#### Scenario: Fresh quote eligible
- **WHEN** a watched ETF has a fresh intraday quote
- **THEN** the job marks it decision-eligible for intraday alert evaluation

#### Scenario: Stale quote blocked
- **WHEN** a watched ETF has stale or missing quote time
- **THEN** the job marks it display-only or unavailable and does not use it for email decisions

### Requirement: Intraday watch uses validation and portfolio scope without weakening reliability gates
The intraday ETF watch SHALL prioritize watched ETFs from tracked holdings, top validated candidates, short/high observation labels, and optimized observation portfolio candidates, while preserving market session and data reliability gates.

#### Scenario: Trading session is open
- **WHEN** the market is open and fresh quotes are available
- **THEN** the intraday watch updates eligible ETFs and records live score changes

#### Scenario: Validation data is unavailable
- **WHEN** validation data is missing or insufficient
- **THEN** the intraday watch may still monitor configured scope but must mark validation confidence as unavailable

#### Scenario: Quote is stale
- **WHEN** an ETF quote is stale or missing quote time
- **THEN** the intraday watch does not use it for live decision-eligible alerts

