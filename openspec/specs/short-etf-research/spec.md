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
The system SHALL synchronize ETF daily market data including date, open, high, low, close, volume, turnover, percentage change, adjusted research value, price basis, provider version, adjustment version, source timestamp, and decision eligibility for the dynamic short-term ETF universe.

#### Scenario: Data sync stores daily prices
- **WHEN** the user or post-close coordinator runs ETF data sync
- **THEN** the system stores or updates daily ETF price rows without duplicating existing code/date records and preserves stronger decision-eligible provenance over weaker raw-only data

#### Scenario: Data source failure is reported
- **WHEN** one ETF data source request fails during synchronization
- **THEN** the task records the ETF code and bounded readable error reason while continuing with other eligible candidates within the slice budget

#### Scenario: Large universe sync is batched
- **WHEN** the system synchronizes a large ETF universe
- **THEN** one worker processes stable code/date pages under code, row, memory, SQL, provider, and 60-second time limits and records a durable resumable checkpoint

#### Scenario: Publication data retains priority
- **WHEN** target-date adjusted coverage is below 95 percent or 61-session adjusted coverage is below the configured publication threshold
- **THEN** no 300-session or 500-session research-depth provider work starts

#### Scenario: Research history accumulates after publication readiness
- **WHEN** both publication gates pass
- **THEN** scheduled serial slices advance 300 adjusted sessions first and only then advance non-authoritative 500-session telemetry
- **AND** both research-depth completion gates remain 95 percent

#### Scenario: Current-data request also covers warm-up
- **WHEN** an ETF is missing target-date adjusted data
- **THEN** the bounded request may include the recent 61-session date window so one provider round trip can persist current and warm-up data while the two readiness lanes remain independently measured

#### Scenario: Continuation is resumed
- **WHEN** a compatible bounded synchronization slice previously ended as partial
- **THEN** the next slice resumes idempotently from persisted pages and rotation state instead of restarting an unbounded universe scan

#### Scenario: Publication and research horizons have different eligibility

- **WHEN** a current authoritative ETF is factually too new to have existed at
  the first required 300-session or 500-session date
- **THEN** it remains part of the publication universe
- **AND** only the corresponding seasoned research cohort excludes it, with the
  official listing source/version, observation cutoff, metadata coverage, cohort
  evidence hash, session-calendar hash, and exclusion hash exposed

### Requirement: ETF Metrics Identify Trend And Risk
The system SHALL compute deterministic ETF metrics for trend, liquidity, volatility, drawdown, short-term return, medium-term return, and overextension risk.

#### Scenario: High recent return triggers chase risk
- **WHEN** an ETF has a recent return above the configured overextension threshold
- **THEN** the ETF metric output includes a chase-risk flag and the signal conclusion cannot be stronger than high-watch observation language

#### Scenario: Low liquidity triggers liquidity risk
- **WHEN** an ETF's recent average turnover is below the configured liquidity threshold
- **THEN** the ETF metric output includes a liquidity-risk flag

### Requirement: Short ETF Signals Use Research Language
The system SHALL generate separate daily research and actionable ranked ETF signal outputs using deterministic metrics, SHALL identify actionable Top 20 intraday watch candidates only from ETFs that pass the actionable contract, and SHALL frame every conclusion as research observation rather than trading instruction.

#### Scenario: Latest signals are returned
- **WHEN** the user runs short-term ETF signal generation
- **THEN** the system persists one auditable run with independently ordered research and actionable items, score breakdowns, eligibility reasons, risk flags, theme labels, and observation-oriented conclusions

#### Scenario: Top 20 watch candidates are identifiable
- **WHEN** a successful ETF signal run has at least one actionable item
- **THEN** the first 20 actionable ETF items are available to the intraday ETF watch service without recomputing the full ETF universe during market hours

#### Scenario: Research-only ETF is retained
- **WHEN** an ETF passes adjusted-daily research eligibility but fails an actionable gate
- **THEN** it remains in the research output and is absent from the actionable Top 20 with a readable exclusion reason

#### Scenario: Prohibited trade language is absent
- **WHEN** the API returns a short-term ETF signal item from either surface
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
The system SHALL separate all stored ETFs, the default daily research display, and the actionable subset by applying deterministic, versioned quality gates.

#### Scenario: Qualified ETF appears in default display
- **WHEN** an ETF has at least 61 point-in-time decision-eligible total-return-adjusted sessions ending on the ranking date
- **THEN** it is eligible for the default daily research ranking even if intraday actionable fields are unavailable

#### Scenario: Qualified ETF appears in default research display
- **WHEN** an ETF has at least 61 point-in-time decision-eligible total-return-adjusted sessions ending on the ranking date
- **THEN** it is eligible for the default daily research ranking even if intraday actionable fields are unavailable

#### Scenario: Short-history ETF is visibly provisional
- **WHEN** an ETF has 61 to 119 eligible adjusted sessions
- **THEN** it remains in the research display with a short-history state and cannot appear as actionable

#### Scenario: Qualified ETF appears in actionable display
- **WHEN** an ETF has at least 120 eligible adjusted sessions and passes all current actionable market-data and risk gates
- **THEN** it may appear in the actionable ranking

#### Scenario: Unqualified ETF remains searchable
- **WHEN** an ETF fails a research or actionable quality gate but remains part of the stored ETF universe
- **THEN** it remains searchable with the failing surface and reason shown to the user

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

### Requirement: ETF ranking publication requires current and score-ready adjusted data
The system SHALL publish a full ETF ranking only when at least 95 percent of the authoritative target-date universe have decision-eligible `total_return_adjusted` data for the target session and at least 90 percent are score-eligible with 61 exchange sessions, and SHALL otherwise return an explicit waiting state.

#### Scenario: Decision-data coverage is insufficient
- **WHEN** target-date decision-eligible adjusted coverage is below 95 percent
- **THEN** the system publishes no ranking and reports the current coverage blocker

#### Scenario: Score coverage is insufficient
- **WHEN** decision-data coverage reaches 95 percent but 61-session score-eligible coverage remains below 90 percent
- **THEN** the system publishes no degraded ranking and reports the warm-up blocker

#### Scenario: Raw fallback rows exist
- **WHEN** Sina, efinance, intraday snapshot, stale cache, estimated, or other raw-only rows exist without complete adjusted provenance
- **THEN** those rows remain display-only or unavailable and MUST NOT increase either publication coverage ratio

#### Scenario: Both gates pass
- **WHEN** the registered 95 percent decision-data gate and 90 percent score-warmup gate pass for the same target trade date and authoritative universe
- **THEN** the system may materialize and publication-validate the full dual-ranking snapshot using only decision-eligible adjusted inputs
