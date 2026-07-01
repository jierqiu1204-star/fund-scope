## ADDED Requirements

### Requirement: Short-Term Workbench Shows Strategy Comparison Evidence
The short-term research workbench SHALL show historical comparison evidence for the current ETF workbench strategy and selected deterministic baseline strategies when such evidence is available.

#### Scenario: Strategy comparison result is available
- **WHEN** a completed ETF strategy comparison backtest exists
- **THEN** `/short-term` shows the compared strategies, date range, current strategy metrics, benchmark metrics, and data coverage in Chinese

#### Scenario: Strategy comparison result is unavailable
- **WHEN** no completed ETF strategy comparison backtest exists
- **THEN** `/short-term` shows that strategy comparison evidence is not yet available and MUST NOT imply that the current labels are proven

### Requirement: Strategy comparison display distinguishes evidence from live recommendations
The short-term research workbench SHALL clearly distinguish historical strategy comparison evidence from current ETF rankings, portfolio weights, and tracked-position alerts.

#### Scenario: Historical strategy outperforms
- **WHEN** a compared strategy has better historical return or risk metrics than the current strategy
- **THEN** the UI displays it as historical research evidence and MUST NOT automatically change the current ETF资金配置参考 or tracked-position email rules

#### Scenario: User views current ETF ranking
- **WHEN** the user views current ETF rankings and selected ETF detail
- **THEN** strategy comparison data appears as a separate evidence section and does not override the current ranking label, entry timing, quote price, or holding action

### Requirement: Strategy comparison summary explains limitations
The short-term research workbench SHALL explain the limits of strategy comparison backtests in beginner-readable Chinese.

#### Scenario: User opens comparison evidence
- **WHEN** the user views a strategy comparison summary
- **THEN** the UI states that the comparison is based on historical daily data, does not validate minute-level intraday alerts, and does not guarantee future returns

#### Scenario: Sample is short or incomplete
- **WHEN** the strategy comparison has short sample history, missing data, or warm-up limitations
- **THEN** the UI marks the evidence as 样本不足 or 数据覆盖有限 and avoids confident wording
