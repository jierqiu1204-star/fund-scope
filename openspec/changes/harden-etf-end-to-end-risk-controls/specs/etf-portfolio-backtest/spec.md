## ADDED Requirements

### Requirement: ETF backtest models executable-price risk explicitly
ETF portfolio backtests SHALL calculate simulated fills from a predeclared execution model and SHALL report fees, spread, signal-to-fill gap, base slippage, and stressed slippage separately.

#### Scenario: Next eligible session has executable evidence
- **WHEN** a signal is filled on the next eligible session using the declared adjusted open or another preregistered price basis
- **THEN** the simulated trade records reference price, fill price, gap return, fee, spread cost, base slippage, stressed slippage, and total cost under each scenario

#### Scenario: Execution evidence is unavailable
- **WHEN** the next session is suspended, has zero volume, has no valid adjusted execution price, or otherwise cannot prove a fill
- **THEN** the trade remains pending or is excluded with a stable reason and MUST NOT use the signal-day close, a later-known best price, or raw fallback data

### Requirement: ETF backtest reports base and execution-stress outcomes separately
ETF portfolio backtests SHALL preserve one frozen base execution assumption and one frozen adverse execution-cost scenario without selecting whichever produces the preferred result.

#### Scenario: Backtest completes
- **WHEN** a backtest has one or more simulated fills
- **THEN** it reports net return, maximum drawdown, turnover, total costs, and action-cycle benefit for both base and stressed execution assumptions with their contract identities

#### Scenario: Only daily data is available
- **WHEN** the backtest cannot reconstruct historical bid, ask, IOPV, or intraday path
- **THEN** it labels spread and intraday execution as modeled sensitivity rather than observed evidence and MUST NOT claim that intraday stops were historically executable
