## ADDED Requirements

### Requirement: Backtest reports requested, available, and trading periods separately
The ETF portfolio backtest SHALL distinguish the user's requested date range from the effective data range and actual trading period.

#### Scenario: Data starts after requested start
- **WHEN** the requested backtest start date is earlier than available verified ETF history
- **THEN** the result reports requested start/end, effective data start/end, warm-up period, and caveats without implying the whole requested period was tradable

#### Scenario: First trade occurs after data start
- **WHEN** the backtest has verified data before the first portfolio trade
- **THEN** the result reports first signal date and first trade date separately from the effective data start date

### Requirement: Backtest attributes cash-wait days
The ETF portfolio backtest SHALL summarize why the strategy stayed in cash on replay dates.

#### Scenario: Cash wait due to warm-up
- **WHEN** a replay date has insufficient verified history to calculate required indicators
- **THEN** the backtest attributes that date to data warm-up or insufficient history

#### Scenario: Cash wait due to candidate shortage
- **WHEN** indicators are available but not enough ETFs pass allocation gates
- **THEN** the backtest attributes that date to qualified candidate shortage or risk filter constraints

#### Scenario: Partial allocation exists
- **WHEN** some ETFs qualify but full exposure is not justified
- **THEN** the backtest records ETF exposure and waiting cash separately instead of treating the date as fully cash-wait

### Requirement: Backtest uses expanded verified ETF history when available
The ETF portfolio backtest SHALL use the expanded verified ETF daily history created by long-history backfill.

#### Scenario: Long history exists
- **WHEN** `etf_price_history` contains verified rows before the default recent refresh window
- **THEN** the backtest uses those rows for historical replay, indicators, benchmarks, and label summaries

#### Scenario: Long history is still insufficient
- **WHEN** verified history remains insufficient for a requested range
- **THEN** the result marks the run as sample-insufficient or reports the missing coverage in Chinese-readable caveats
