## ADDED Requirements

### Requirement: Short-Term Workbench Shows ETF Strategy Backtest Evidence
The short-term research workbench SHALL expose historical daily backtest evidence for the same ETF portfolio workflow used by the page when such evidence is available.

#### Scenario: Backtest result is available
- **WHEN** the user opens `/short-term` and a completed ETF portfolio backtest exists
- **THEN** the page shows the backtest date range, strategy return, benchmark return, maximum drawdown, trade count, win rate, and data coverage in Chinese

#### Scenario: Backtest result is unavailable
- **WHEN** no completed ETF portfolio backtest exists
- **THEN** the page shows that the current ETF strategy has not yet been historically replayed and MUST NOT imply that the labels are proven

### Requirement: Short-Term Workbench Separates Current Strategy Backtest From Legacy Simulation
The short-term research workbench SHALL distinguish current ETF portfolio backtest evidence from legacy strategy-lab simulation or paper portfolio results.

#### Scenario: Legacy simulation exists
- **WHEN** the system has legacy strategy-lab simulation results
- **THEN** `/short-term` labels them as old strategy experiments or advanced strategy records and does not present them as proof of the current ETF workbench strategy

#### Scenario: ETF backtest exists
- **WHEN** the current ETF portfolio backtest is available
- **THEN** the page identifies it as matching the ETF资金配置参考, 买入观察, 今日买点, and 持仓风控 rules used by the short-term workbench

### Requirement: Short-Term Workbench Explains Backtest Limits
The short-term research workbench SHALL display clear limits for historical ETF backtest evidence, including daily granularity, data coverage, and non-guarantee wording.

#### Scenario: User views ETF backtest summary
- **WHEN** a backtest summary is rendered
- **THEN** the UI states that it is a historical daily simulation, not live trading, not future prediction, and not a guarantee of profit

#### Scenario: Intraday strategy is not backtested
- **WHEN** the backtest used daily data only
- **THEN** the UI states that intraday ranking, minute-level alerts, and broker-app price differences are validated only by forward live tracking
