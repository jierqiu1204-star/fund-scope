# short-etf-research Specification

## Purpose
TBD - created by archiving change add-short-etf-research-lab. Update Purpose after archive.
## Requirements
### Requirement: ETF Universe Excludes Non-Short-Term Funds
The system SHALL maintain a dynamic short-term research universe containing tradable on-exchange ETFs and SHALL exclude ordinary off-exchange funds, money market ETFs, LOF products, one-year holding period funds, closed-period funds, and other products unsuitable for short-term ETF research.

#### Scenario: One-year holding fund is excluded
- **WHEN** the system builds the short-term ETF universe from available fund and ETF metadata
- **THEN** products whose names or metadata indicate one-year holding, fixed holding, closed period, or off-exchange-only trading are not included in the short-term ETF universe

#### Scenario: Non-ETF products are excluded
- **WHEN** the system refreshes the ETF universe from public exchange or fund data sources
- **THEN** money market ETFs, LOF products, off-exchange funds, closed products, and products without ETF-style exchange trading are not marked as default short-term ETF candidates

#### Scenario: ETF universe is visible
- **WHEN** the client requests the short-term ETF universe
- **THEN** the API returns ETF code, name, theme labels, exchange, trading rule label, universe membership, default-display eligibility, and latest data status for each included ETF

### Requirement: ETF Market Data Is Synchronizable
The system SHALL synchronize ETF daily market data including date, open, high, low, close, volume, turnover, and percentage change for the dynamic short-term ETF universe.

#### Scenario: Data sync stores daily prices
- **WHEN** the user runs ETF data sync from the web UI
- **THEN** the system stores or updates daily ETF price rows without duplicating existing code/date records

#### Scenario: Data source failure is reported
- **WHEN** one ETF data source request fails during synchronization
- **THEN** the task records the ETF code and readable error message while continuing with other ETFs

#### Scenario: Large universe sync is batched
- **WHEN** the system synchronizes a large ETF universe
- **THEN** the task processes ETFs in bounded batches and records progress counts for total, succeeded, updated, failed, and skipped ETFs

#### Scenario: Higher priority ETFs update first
- **WHEN** the daily ETF sync task runs
- **THEN** tracked ETFs, default-display ETFs, and high-turnover ETFs are synchronized before low-priority ETF records

### Requirement: ETF Metrics Identify Trend And Risk
The system SHALL compute deterministic ETF metrics for trend, liquidity, volatility, drawdown, short-term return, medium-term return, and overextension risk.

#### Scenario: High recent return triggers chase risk
- **WHEN** an ETF has a recent return above the configured overextension threshold
- **THEN** the ETF metric output includes a chase-risk flag and the signal conclusion cannot be stronger than high-watch observation language

#### Scenario: Low liquidity triggers liquidity risk
- **WHEN** an ETF's recent average turnover is below the configured liquidity threshold
- **THEN** the ETF metric output includes a liquidity-risk flag

### Requirement: Short ETF Signals Use Research Language
The system SHALL generate ranked short-term ETF signal items using deterministic trend, liquidity, and risk metrics, SHALL identify the top 20 ranked ETFs as the default intraday watch candidates, and SHALL frame every conclusion as research observation rather than trading instruction.

#### Scenario: Latest signals are returned
- **WHEN** the user runs short-term ETF signal generation
- **THEN** the system persists a signal run with ranked items, score breakdowns, risk flags, theme labels, and observation-oriented conclusions

#### Scenario: Top 20 watch candidates are identifiable
- **WHEN** a successful ETF signal run is persisted
- **THEN** the first 20 ranked ETF items are available to the intraday ETF watch service without recomputing the full ETF universe during market hours

#### Scenario: Prohibited trade language is absent
- **WHEN** the API returns a short-term ETF signal item
- **THEN** it does not include buy, sell, target price, expected return, or guaranteed profit fields

### Requirement: Short ETF Paper Trading Simulates Conservative Daily Trades
The system SHALL provide a short-term ETF paper portfolio that creates virtual orders, positions, cash, equity curve, drawdown, and trading statistics using daily ETF prices and conservative trading constraints.

#### Scenario: T+1 sell restriction is enforced
- **WHEN** a stock-style ETF is bought in the paper portfolio on a trade date
- **THEN** the paper trading engine does not sell that same ETF position until at least the next trading date

#### Scenario: Paper run creates charts data
- **WHEN** a short-term ETF paper run completes successfully
- **THEN** the API returns latest equity, cash, positions, orders, equity curve, drawdown values, and summary metrics

### Requirement: Short ETF Review Explains Risks Without Changing Rank
The system SHALL generate a rule-first multi-role review for short-term ETF signal runs, and the review SHALL not alter the original signal ranking or score.

#### Scenario: Review explains each candidate
- **WHEN** the user generates a review for the latest short-term ETF signal run
- **THEN** each signal item includes data, strategy, risk, opposing-view, and summary notes

#### Scenario: Review preserves signal order
- **WHEN** a review is generated for a signal run
- **THEN** the original signal item ranks and scores remain unchanged

### Requirement: Short ETF Frontend Is Operable From The Web
The system SHALL expose a Chinese web interface for data preparation, signal generation, signal review, paper portfolio updates, universe filtering, and visual result inspection.

#### Scenario: User opens short ETF tab
- **WHEN** the user opens the ETF mode in `/short-term`
- **THEN** the UI displays ETF universe counts, data status, sync actions, latest signals, risk labels, chart details, tracking actions, and empty/error states in Chinese

#### Scenario: High risk is visually prominent
- **WHEN** a signal item has chase-risk, high-volatility, liquidity-risk, stale-data, or insufficient-history flags
- **THEN** the UI displays those risks prominently near the ETF name and score

#### Scenario: User switches ETF universe view
- **WHEN** the user changes the ETF universe filter between default selected, all analyzable, and low-liquidity-inclusive views
- **THEN** the list refreshes without mixing off-exchange funds into the ETF result set

### Requirement: ETF Universe Is Refreshable From Public Sources
The system SHALL provide a web-runnable job that refreshes the tradable ETF universe from public data sources and upserts ETF metadata into persistent storage.

#### Scenario: Universe refresh is idempotent
- **WHEN** the ETF universe refresh job is run repeatedly
- **THEN** existing ETF records are updated without duplicate ETF rows and newly discovered eligible ETFs are inserted

#### Scenario: Universe refresh reports counts
- **WHEN** the ETF universe refresh job completes
- **THEN** the result reports total discovered ETFs, inserted ETFs, updated ETFs, excluded ETFs, default-display ETFs, and failures

### Requirement: ETF Default Display Uses Quality Gates
The system SHALL separate all stored ETFs from the default short-term display by applying deterministic quality gates.

#### Scenario: Qualified ETF appears in default display
- **WHEN** an ETF has sufficient history, current data, valid daily prices, and recent average turnover above the configured threshold
- **THEN** it is eligible for the default ETF ranking view

#### Scenario: Unqualified ETF remains searchable
- **WHEN** an ETF fails a default-display quality gate but still has analyzable data
- **THEN** it remains available in the all-analyzable view with the failing quality reason shown to the user

### Requirement: ETF Observation Portfolio Produces Target Weights
The system SHALL generate a rule-based ETF observation portfolio that expresses selected ETF exposure as target weights plus a cash weight for manual user reference, and SHALL exclude assets whose current observation or entry-timing labels indicate overextension, weak entry timing, stale data, or unsuitable short-term conditions from the primary weighted portfolio.

#### Scenario: Observation portfolio returns weights
- **WHEN** the system generates the latest ETF observation portfolio
- **THEN** the result includes ETF codes, names, target weights, cash weight, score evidence, risk reasons, data date, method explanation, and separate lists for weighted candidates, watch-only candidates, and excluded candidates

#### Scenario: High-risk conditions increase cash
- **WHEN** top-ranked ETFs are overextended, volatile, illiquid, data-stale, highly correlated with selected ETFs, or concentrated in the same theme
- **THEN** the observation portfolio reduces ETF exposure or increases cash weight rather than presenting a fully invested portfolio

#### Scenario: High-watch assets do not receive primary weights
- **WHEN** an ETF is labeled `高位观察`, `高位别追`, `冲高别追`, `跌破等待`, `放量转弱`, `数据不足`, or has stale or insufficient data
- **THEN** the ETF is not assigned a primary target weight and is returned as watch-only or excluded with a readable reason

#### Scenario: Entry timing gates primary portfolio eligibility
- **WHEN** an ETF is labeled `短线观察` and its entry timing is `健康回踩` or `趋势延续`
- **THEN** the ETF can be considered for a primary target weight subject to liquidity, volatility, drawdown, correlation, and theme concentration constraints

#### Scenario: Correlation and theme concentration reduce weights
- **WHEN** two or more candidate ETFs have high recent return correlation or share the same investment theme
- **THEN** the portfolio keeps exposure diversified by lowering or skipping lower-ranked overlapping candidates and explaining the concentration reason

#### Scenario: Observation portfolio is not a trade instruction
- **WHEN** the observation portfolio is returned by API or UI
- **THEN** it is labeled as research-only manual reference and does not include automatic order instructions, guaranteed return, expected return, target price, or direct buy wording

### Requirement: ETF observation weight explanation
ETF observation portfolio results SHALL include an explanation for every included ETF weight.

#### Scenario: Included ETF explanation
- **WHEN** an ETF receives a non-zero observation weight
- **THEN** the response explains the score, validation confidence, volatility, drawdown, liquidity, correlation, and theme factors that contributed

### Requirement: ETF observation exclusion explanation
ETF observation portfolio results SHALL include concrete reasons for excluded candidates.

#### Scenario: Excluded ETF explanation
- **WHEN** an ETF candidate receives zero weight
- **THEN** the response identifies whether it was excluded for stale data, decision ineligibility, low confidence, high correlation, theme concentration, liquidity, or risk constraints

### Requirement: Weight cap preservation
ETF observation portfolio results SHALL keep the single ETF max weight at 30%.

#### Scenario: Single ETF cap
- **WHEN** observation weights are generated
- **THEN** no individual ETF weight exceeds 30%

