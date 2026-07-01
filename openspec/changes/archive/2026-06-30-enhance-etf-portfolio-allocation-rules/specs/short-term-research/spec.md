## ADDED Requirements

### Requirement: Short-term workbench displays ETF allocation layers
The short-term research workbench SHALL display ETF observation portfolio output as allocation layers rather than a single flat recommendation list.

#### Scenario: Layered portfolio is available
- **WHEN** the latest ETF observation portfolio includes primary, satellite, defensive, or cash layers
- **THEN** `/short-term` displays the layers as `主配置`, `小仓观察`, `防守配置`, and `等待资金`

#### Scenario: Cash wait is active
- **WHEN** the portfolio mode is `cash_wait`
- **THEN** `/short-term` displays the cash-wait reason and MUST NOT imply that the system failed or forgot to generate a portfolio

#### Scenario: Defensive mode is active
- **WHEN** the portfolio mode is `defensive`
- **THEN** `/short-term` explains that current conditions favor defensive observation over full offensive ETF allocation

### Requirement: Short-term workbench explains ETF weight and exclusion reasons
The short-term research workbench SHALL show why a selected ETF did or did not receive observation weight.

#### Scenario: Selected ETF has weight
- **WHEN** the selected ETF is included in the latest ETF observation portfolio
- **THEN** the detail panel shows its allocation layer, target observation weight, cap constraints, risk factors, data timestamp, and weight reason

#### Scenario: Selected ETF is watch-only
- **WHEN** the selected ETF is watch-only because it is high-position, chase-risk, concentrated, defensive-mismatch, or data-limited
- **THEN** the detail panel shows the watch-only reason without presenting it as a buy or sell instruction

#### Scenario: Selected ETF is excluded
- **WHEN** the selected ETF is excluded from the latest ETF observation portfolio
- **THEN** the detail panel shows the exclusion reason such as stale data, insufficient liquidity, correlation duplicate, theme cap, or entry timing risk

### Requirement: Short-term workbench separates observation from trading instruction
The short-term research workbench SHALL label ETF allocation weights as research references and SHALL NOT present them as automatic trading instructions.

#### Scenario: Portfolio weights are displayed
- **WHEN** `/short-term` shows ETF allocation weights
- **THEN** the UI states that weights are observation references for manual judgment and do not connect to a broker

#### Scenario: Satellite allocation is displayed
- **WHEN** `/short-term` shows a high-watch ETF in the satellite layer
- **THEN** the UI explains that satellite weight means reduced observation exposure, not a strong buy signal

### Requirement: Short-term workbench shows allocation data timestamps
The short-term research workbench SHALL display the data timestamps used by ETF portfolio allocation.

#### Scenario: Portfolio uses latest data
- **WHEN** an ETF portfolio snapshot is displayed
- **THEN** the UI shows portfolio generation time, daily signal date, quote time when available, and whether the result used real-time, daily, or fallback-ineligible data

#### Scenario: Portfolio data is stale
- **WHEN** the latest portfolio snapshot is older than the latest available ETF signal or quote context
- **THEN** the UI shows a waiting or stale-state message instead of presenting the portfolio as freshly generated
