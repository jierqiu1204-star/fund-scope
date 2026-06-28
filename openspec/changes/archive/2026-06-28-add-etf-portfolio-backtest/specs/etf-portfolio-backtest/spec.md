## ADDED Requirements

### Requirement: ETF portfolio backtest replays historical decisions
The system SHALL provide a historical daily backtest for the current short-term ETF portfolio workflow, and MUST regenerate signals and portfolio targets for each historical trading day using only data available on or before that day.

#### Scenario: Daily replay avoids future data
- **WHEN** a backtest evaluates a historical date
- **THEN** the system generates that date's ETF ranking, entry timing, portfolio mode, and target weights from data whose date is less than or equal to the evaluated date

#### Scenario: Current portfolio is not backfilled into the past
- **WHEN** today's ETF portfolio contains an ETF that was not eligible on a historical date
- **THEN** the historical date MUST NOT use today's eligibility or target weight for that ETF

### Requirement: ETF portfolio backtest uses the short-term workbench strategy
The backtest SHALL use the same deterministic strategy layers shown on `/short-term`: ETF ranking, observation label, entry timing, risk-on/defensive/cash-wait portfolio mode, single-ETF cap, user ETF capital, and position handling rules.

#### Scenario: Portfolio target is generated
- **WHEN** the replay date has enough eligible ETF data
- **THEN** the system generates target ETF weights using the same risk constraints as ETF资金配置参考, including the 30% single-ETF cap

#### Scenario: Market conditions are defensive or cash-wait
- **WHEN** offensive ETF candidates are insufficient
- **THEN** the system attempts defensive allocation and, if still insufficient, records cash-wait rather than forcing high-risk ETF exposure

### Requirement: ETF portfolio backtest simulates position handling
The backtest SHALL simulate position changes using the current tracked-position risk rules at daily granularity: hard stop, trailing take-profit, trend weakening, take-profit watch, exit watch, and position sizing.

#### Scenario: Exit signal occurs
- **WHEN** a held ETF breaches a hard-stop or exit-watch rule in the daily replay
- **THEN** the backtest records a sell or reduce trade according to the configured position sizing policy

#### Scenario: Trailing profit gives back
- **WHEN** a held ETF reaches its dynamic trailing-profit activation level and then gives back enough profit
- **THEN** the backtest records the trailing take-profit handling action and resulting position adjustment

### Requirement: ETF portfolio backtest stores auditable results
The system SHALL persist each backtest run, daily equity curve, simulated trades, position snapshots, benchmark results, and label outcome summary separately from real tracked positions and legacy strategy runs.

#### Scenario: Backtest completes
- **WHEN** a backtest run finishes successfully
- **THEN** the system stores run metadata, date range, rule version, data coverage, metrics, equity curve, trades, positions, and benchmark metrics

#### Scenario: Backtest fails
- **WHEN** a backtest run encounters a recoverable data or calculation error
- **THEN** the system marks the run as failed with a Chinese-readable error message without changing real tracked positions

### Requirement: ETF portfolio backtest reports comparable metrics
The system SHALL report beginner-readable metrics and comparison baselines for each completed ETF portfolio backtest.

#### Scenario: Metrics are displayed
- **WHEN** the user opens a completed backtest
- **THEN** the system shows cumulative return, maximum drawdown, win rate, average holding days, trade count, turnover, fees, best/worst trade, and data coverage

#### Scenario: Baselines are included
- **WHEN** a backtest result is shown
- **THEN** the system includes at least cash waiting and one ETF buy-and-hold or equal-weight benchmark for comparison

### Requirement: ETF portfolio backtest distinguishes daily replay from intraday validation
The system SHALL clearly label ETF portfolio backtests as daily historical simulations and MUST NOT imply that they validate intraday signals when historical intraday data is unavailable.

#### Scenario: Intraday fields are unavailable
- **WHEN** a backtest has only daily OHLC data
- **THEN** the system labels intraday timing and intraday email behavior as not backtested

#### Scenario: User views result caveat
- **WHEN** the backtest result is displayed
- **THEN** the UI states that the result is historical simulation, does not guarantee future returns, and does not execute trades

### Requirement: ETF portfolio backtest can be run manually
The system SHALL provide an authenticated API and admin job entry to start an ETF portfolio backtest for a configured date range and user ETF capital assumptions.

#### Scenario: User starts backtest
- **WHEN** an approved user requests a valid ETF portfolio backtest
- **THEN** the system creates a run, executes the replay, and returns the run id and status

#### Scenario: Invalid range is requested
- **WHEN** the requested date range lacks enough ETF history
- **THEN** the system rejects or marks the run as sample-insufficient with a Chinese-readable reason
