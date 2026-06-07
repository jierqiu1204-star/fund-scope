# Spec: portfolio-tracking

## ADDED Requirements

### Requirement: Record fund transactions

The system SHALL allow the user to record mutual fund buy and sell transactions with fund code, amount, net asset value at trade, fee, and trade date.

#### Scenario: Record a successful buy transaction

- **WHEN** the user submits a buy transaction for fund code `007339` with amount `500`, NAV `1.2345`, fee `1.50`, trade date `2026-05-01`
- **THEN** the system SHALL persist the transaction, compute shares as `(500 - 1.50) / 1.2345`, associate it with the user's default portfolio, and make it visible on the transactions page

#### Scenario: Record a sell (redemption) transaction

- **WHEN** the user submits a redemption transaction for fund code `007339` with shares `100` and NAV `1.3500`
- **THEN** the system SHALL persist the transaction with computed proceeds, reduce the holding's share count, and preserve original cost basis for P&L calculation

#### Scenario: Reject transaction with unknown fund code

- **WHEN** the user submits a transaction with a fund code not present in the `funds` table
- **THEN** the system SHALL reject the submission with a validation error indicating the fund must first be added to the watchlist

### Requirement: Compute aggregated holdings

The system SHALL aggregate all transactions into per-fund holdings showing total shares, cost basis, current market value, absolute P&L, and P&L percentage.

#### Scenario: Display holdings with latest NAV

- **WHEN** the user visits `/portfolio` and the latest daily NAV snapshot has run
- **THEN** the system SHALL show one card per fund with: fund name, total shares held, cost basis, current market value (shares × latest NAV), absolute P&L (market value − cost), and P&L percentage

#### Scenario: Display stale-data warning when NAV is outdated

- **WHEN** the latest NAV for a held fund is older than 2 business days
- **THEN** the system SHALL render a visible "数据截至 YYYY-MM-DD" indicator on the fund's card and NOT display a possibly misleading current market value as if it were fresh

### Requirement: Display portfolio value time series

The system SHALL display the total portfolio market value as a time series chart from the first transaction date to the most recent daily snapshot.

#### Scenario: Render portfolio value chart

- **WHEN** the user visits `/portfolio` with at least one completed daily snapshot
- **THEN** the system SHALL render a line or area chart showing daily total portfolio market value, using the `holdings_snapshot` table as the data source

#### Scenario: Empty state for new user

- **WHEN** the user has zero transactions recorded
- **THEN** the system SHALL show an onboarding prompt linking to the default-portfolio-seed action and the transaction entry form, NOT an empty chart

### Requirement: Display asset allocation breakdown

The system SHALL display a breakdown of the current portfolio's market value by fund (and optionally by asset class) as a pie or donut chart.

#### Scenario: Show allocation chart

- **WHEN** the user visits `/portfolio`
- **THEN** the system SHALL render a chart where each slice represents one held fund's share of the total portfolio market value, labelled with fund name and percentage

### Requirement: Support CSV import of transactions

The system SHALL allow the user to bulk-upload historical transactions via a CSV file with a documented column schema.

#### Scenario: Successful CSV import

- **WHEN** the user uploads a CSV with columns `fund_code,action,amount_or_shares,nav,fee,traded_at` and all rows reference known funds
- **THEN** the system SHALL create one transaction per row inside a single database transaction, and display a success summary with row count

#### Scenario: Reject malformed CSV atomically

- **WHEN** the user uploads a CSV where any row fails validation (unknown fund code, invalid date, negative amount)
- **THEN** the system SHALL reject the entire upload with zero transactions inserted and a per-row error report

### Requirement: Daily holdings snapshot job

The system SHALL compute and persist a per-day snapshot of each holding's share count, cost basis, and market value automatically each evening after the NAV fetch job completes.

#### Scenario: Snapshot runs after NAV fetch

- **WHEN** the daily NAV fetch job has succeeded for date `D`
- **THEN** the `daily_holdings_snapshot` job SHALL run and insert or update `holdings_snapshot` rows for date `D` for every fund with non-zero shares

#### Scenario: Snapshot is idempotent

- **WHEN** the daily snapshot job runs twice for the same date
- **THEN** the second run SHALL overwrite rather than duplicate the day's snapshot rows

### Requirement: Seed a default starter portfolio

The system SHALL provide a one-click action to initialize the user's watchlist and default portfolio configuration with a predefined lazy-DCA fund mix.

#### Scenario: Apply default seed on empty account

- **WHEN** the user clicks "Apply default portfolio" on the Onboarding page and currently has no transactions and no custom watchlist entries
- **THEN** the system SHALL add funds `007339`, `001052`, `270042`, `000198` to the watchlist with target allocations `40%`, `30%`, `20%`, `10%` respectively, WITHOUT creating any transactions

#### Scenario: Refuse to overwrite non-empty watchlist

- **WHEN** the user clicks "Apply default portfolio" and the watchlist already contains custom funds
- **THEN** the system SHALL require explicit confirmation and a "replace" vs "merge" choice before modifying the watchlist
