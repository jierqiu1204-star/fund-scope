## ADDED Requirements

### Requirement: ETF Universe Excludes Non-Short-Term Funds
The system SHALL maintain a short-term research universe containing tradable on-exchange ETFs and SHALL exclude ordinary off-exchange funds, one-year holding period funds, closed-period funds, and other products unsuitable for short-term trading simulation.

#### Scenario: One-year holding fund is excluded
- **WHEN** the system builds the short-term ETF universe from available fund and ETF metadata
- **THEN** products whose names or metadata indicate one-year holding, fixed holding, closed period, or off-exchange-only trading are not included in the short-term ETF universe

#### Scenario: ETF universe is visible
- **WHEN** the client requests the short-term ETF universe
- **THEN** the API returns ETF code, name, theme labels, exchange, trading rule label, and latest data status for each included ETF

### Requirement: ETF Market Data Is Synchronizable
The system SHALL synchronize ETF daily market data including date, open, high, low, close, volume, turnover, and percentage change for the configured short-term ETF universe.

#### Scenario: Data sync stores daily prices
- **WHEN** the user runs ETF data sync from the web UI
- **THEN** the system stores or updates daily ETF price rows without duplicating existing code/date records

#### Scenario: Data source failure is reported
- **WHEN** one ETF data source request fails during synchronization
- **THEN** the task records the ETF code and readable error message while continuing with other ETFs

### Requirement: ETF Metrics Identify Trend And Risk
The system SHALL compute deterministic ETF metrics for trend, liquidity, volatility, drawdown, short-term return, medium-term return, and overextension risk.

#### Scenario: High recent return triggers chase risk
- **WHEN** an ETF has a recent return above the configured overextension threshold
- **THEN** the ETF metric output includes a chase-risk flag and the signal conclusion cannot be stronger than high-watch observation language

#### Scenario: Low liquidity triggers liquidity risk
- **WHEN** an ETF's recent average turnover is below the configured liquidity threshold
- **THEN** the ETF metric output includes a liquidity-risk flag

### Requirement: Short ETF Signals Use Research Language
The system SHALL generate ranked short-term ETF signal items using deterministic trend, liquidity, and risk metrics, and SHALL frame every conclusion as research observation rather than trading instruction.

#### Scenario: Latest signals are returned
- **WHEN** the user runs short-term ETF signal generation
- **THEN** the system persists a signal run with ranked items, score breakdowns, risk flags, theme labels, and observation-oriented conclusions

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
The system SHALL expose a Chinese web interface for data preparation, signal generation, signal review, paper portfolio updates, and visual result inspection.

#### Scenario: User opens short ETF tab
- **WHEN** the user opens `/strategy-lab?view=short-etf`
- **THEN** the UI displays ETF data status, sync actions, latest signals, risk labels, paper trading charts, and empty/error states in Chinese

#### Scenario: High risk is visually prominent
- **WHEN** a signal item has chase-risk, high-volatility, or liquidity-risk flags
- **THEN** the UI displays those risks prominently near the ETF name and score
