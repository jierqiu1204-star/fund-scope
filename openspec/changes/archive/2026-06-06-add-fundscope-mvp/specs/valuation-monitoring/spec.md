# Spec: valuation-monitoring

## ADDED Requirements

### Requirement: Maintain index watchlist

The system SHALL maintain a user-editable watchlist of market indices to monitor, with a predefined default set on first install.

#### Scenario: Default indices seeded on fresh install

- **WHEN** the database is first initialized via Alembic migration with seed data
- **THEN** the `indices` watchlist SHALL contain `CSI300` (沪深300), `CSI500` (中证500), `CSI800` (中证800), `CHINEXT` (创业板指), `SP500` (标普500), `NDX100` (纳斯达克100)

#### Scenario: User adds an index to watchlist

- **WHEN** the user submits an add-index form with a valid index code
- **THEN** the system SHALL insert the index into the watchlist and trigger an initial historical-data backfill job

#### Scenario: User removes an index from watchlist

- **WHEN** the user removes an index from the watchlist
- **THEN** the system SHALL stop scheduling new daily fetches for that index but retain historical data rows for audit

### Requirement: Fetch daily index valuation data

The system SHALL retrieve PE ratio, PB ratio, and dividend yield for every watchlist index every trading day.

#### Scenario: Daily valuation fetch succeeds

- **WHEN** the `daily_valuation` scheduled job runs at 19:15 on trading day `D`
- **THEN** the system SHALL insert a new row into `index_valuation_history` for each watchlist index with date `D`, PE, PB, and dividend yield from the primary data source

#### Scenario: Primary data source fails, fallback succeeds

- **WHEN** the AKShare fetch fails for a given index
- **THEN** the system SHALL retry up to 2 times, then attempt the fallback data source, and record the outcome in the job run log

#### Scenario: All data sources fail for an index

- **WHEN** both primary and fallback data sources fail for an index on date `D`
- **THEN** the system SHALL skip that index for date `D`, log a warning, and send a job-failure email after the full run completes

### Requirement: Compute rolling historical percentile

The system SHALL compute a 10-year rolling percentile rank for each watchlist index's PE and PB values on every data update.

#### Scenario: Percentile computed on new data point

- **WHEN** a new valuation row is inserted for index `CSI300` on date `D`
- **THEN** the system SHALL compute the percentile rank of that day's PE within the trailing 10 years of `CSI300` PE values and persist it on the same row

#### Scenario: Insufficient history for full 10-year window

- **WHEN** an index has less than 10 years of available history (newly added index)
- **THEN** the system SHALL compute percentile over the available history, persist the value, and flag the row with the effective window length so the UI can show a "data incomplete" indicator

### Requirement: Display valuation dashboard

The system SHALL render a dashboard page at `/valuation` showing every watchlist index's current valuation state.

#### Scenario: Render valuation cards

- **WHEN** the user visits `/valuation`
- **THEN** the system SHALL render one card per watchlist index showing: current PE, current PB, PE percentile (0-100), PB percentile (0-100), and the data-as-of date

#### Scenario: Colour-code by percentile band

- **WHEN** an index card is rendered with PE percentile `p`
- **THEN** the system SHALL apply green styling if `p < 30`, neutral styling if `30 ≤ p < 70`, red styling if `p ≥ 70`

#### Scenario: Render historical percentile chart

- **WHEN** the user clicks on an index card
- **THEN** the system SHALL open a detail view with a line chart of PE and PB percentile over the full available history, with a toggle to switch between PE and PB series

### Requirement: Expose valuation data via API

The system SHALL expose authenticated JSON endpoints for valuation data so the frontend and downstream features (investment-reminders) can consume it.

#### Scenario: Read current valuation snapshot

- **WHEN** a request is made to `GET /api/valuation/current`
- **THEN** the system SHALL return a JSON array of objects containing `index_code`, `pe`, `pb`, `pe_percentile`, `pb_percentile`, `as_of_date` for every watchlist index

#### Scenario: Read historical valuation series

- **WHEN** a request is made to `GET /api/valuation/{index_code}/history?from=YYYY-MM-DD&to=YYYY-MM-DD`
- **THEN** the system SHALL return a JSON array of daily rows in the requested date range, sorted ascending by date
