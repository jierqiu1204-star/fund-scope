# etf-portfolio-backtest Specification

## Purpose
TBD - created by archiving change add-etf-portfolio-backtest. Update Purpose after archive.
## Requirements

### Requirement: ETF portfolio backtest replays historical decisions
The system SHALL provide a historical daily backtest for the current short-term ETF portfolio workflow, and MUST regenerate signals and portfolio targets for each historical trading day using only data available on or before that day.

#### Scenario: Daily replay avoids future data
- **WHEN** a backtest evaluates a historical date
- **THEN** the system generates that date's ETF ranking, entry timing, portfolio mode, and target weights from data whose date is less than or equal to the evaluated date

#### Scenario: Current portfolio is not backfilled into the past
- **WHEN** today's ETF portfolio contains an ETF that was not eligible on a historical date
- **THEN** the historical date MUST NOT use today's eligibility or target weight for that ETF

### Requirement: ETF portfolio backtest uses the short-term workbench strategy
The backtest SHALL use the same deterministic strategy contract shown on `/short-term`: ETF ranking, observation label, entry timing, risk-on/defensive/cash-wait portfolio mode, single-ETF cap, user ETF capital, and position handling rules. The backtest MUST call the shared strategy contract used by ETF资金配置参考 and MUST record the strategy contract version in every run.

#### Scenario: Portfolio target is generated
- **WHEN** the replay date has enough eligible ETF data
- **THEN** the system generates target ETF weights using the same risk constraints as ETF资金配置参考, including the 30% single-ETF cap

#### Scenario: Market conditions are defensive or cash-wait
- **WHEN** offensive ETF candidates are insufficient
- **THEN** the system attempts defensive allocation and, if still insufficient, records cash-wait rather than forcing high-risk ETF exposure

#### Scenario: Backtest strategy contract matches page strategy
- **WHEN** a replay date and data snapshot are used by both the ETF资金配置参考 and the ETF portfolio backtest
- **THEN** both paths produce the same portfolio mode, target weights, exclusion reasons, and strategy version for the current workbench strategy

#### Scenario: Strategy contract changes
- **WHEN** the shared ETF strategy contract version changes
- **THEN** new backtest runs record the new version and old backtest runs remain auditable with their original version metadata

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

### Requirement: ETF portfolio backtest exposes current strategy evidence to comparison backtests
The ETF portfolio backtest SHALL make the current workbench strategy executable as one strategy inside ETF strategy comparison backtests.

#### Scenario: Comparison requests current strategy
- **WHEN** a strategy comparison backtest includes `current_workbench`
- **THEN** the ETF portfolio backtest engine provides its daily equity curve, trades, positions, and metrics through the same result contract used by other comparison strategies

#### Scenario: Current strategy evidence is missing
- **WHEN** no current strategy backtest exists for the requested date range
- **THEN** the comparison result marks current strategy evidence as unavailable rather than using legacy strategy-lab simulations

### Requirement: ETF portfolio backtest consumes evidence contracts
ETF portfolio backtests SHALL consume ETF research evidence contracts instead of duplicating short-term page or allocation rules.

#### Scenario: Backtest starts
- **WHEN** an ETF portfolio backtest is created
- **THEN** it records the signal contract version, allocation contract version, execution model, fee model, data cutoff, and contract hash

#### Scenario: Contract is missing
- **WHEN** a backtest cannot find the required signal or allocation contract for the date range
- **THEN** it fails or marks the missing period explicitly instead of silently using a different rule set

#### Scenario: Backtest result is displayed
- **WHEN** a completed ETF portfolio backtest is shown
- **THEN** the result identifies whether it matches the current short-term workbench strategy contract

### Requirement: ETF portfolio backtest cannot update production decisions
ETF portfolio backtest output SHALL be evidence only and SHALL NOT automatically modify live ranking, allocation, tracked positions, or email alert thresholds.

#### Scenario: Backtest completes
- **WHEN** a backtest run finishes successfully
- **THEN** the system stores metrics and evidence summary but does not alter live ETF ranking, portfolio allocation, or alert rules

### Requirement: ETF portfolio backtest accepts complete research ranking cohorts
ETF portfolio backtest SHALL accept only complete immutable point-in-time ranking cohorts for `policy_mode=policy_shadow` and SHALL bind every action result to the ranking and action-policy contract identities.

#### Scenario: Ranking cohort is complete
- **WHEN** a replay date has a complete eligible Top20 research cohort
- **THEN** the portfolio and pure `etf_exit_action_v3` lifecycle may produce research-only positions, actions, simulated fills, and shadow notification facts

#### Scenario: Ranking cohort is incomplete
- **WHEN** coverage, provenance, adjusted price, or score eligibility is incomplete
- **THEN** policy shadow records an exclusion and does not construct a fallback portfolio

### Requirement: ETF policy shadow has a fixed action endpoint
ETF policy-shadow evaluation SHALL use the existing Top20 ten-session tax-and-fee-adjusted mean action-cycle benefit relative to continuing to hold under the declared action execution model.

#### Scenario: Exit action completes
- **WHEN** an action and its comparison hold window complete
- **THEN** the system records action-cycle benefit, costs, execution provenance, and directional stop or profit diagnostics separately

#### Scenario: Directional accuracy improves but benefit does not
- **WHEN** stop or profit directional accuracy improves while cost-adjusted action-cycle benefit does not
- **THEN** the policy is not promoted based on accuracy alone

### Requirement: ETF policy shadow has no production side effects
ETF policy shadow MUST NOT create or update production portfolio, tracked-position, risk-alert, notification, or SMTP records.

#### Scenario: Policy replay completes
- **WHEN** policy-shadow artifacts are committed
- **THEN** only research evidence and replay artifact stores change

### Requirement: ETF policy evidence distinguishes delivery and execution
ETF portfolio backtest SHALL report full policy-shadow simulation, live-notification-linked sensitivity, provider-delivery evidence, and user-confirmed execution as separate result groups with independent sample gates.

#### Scenario: Only simulated evidence exists
- **WHEN** policy shadow has complete simulated actions but no live delivery or user-confirmed execution sample
- **THEN** simulated benefit is reported while live notification and confirmed execution results remain unavailable

### Requirement: ETF backtest models size-aware liquidity capacity
ETF portfolio backtests SHALL evaluate proposed fills against PIT-visible decision-eligible turnover and the same versioned liquidity-capacity contract used by live research outputs.

#### Scenario: Simulated entry exceeds capacity
- **WHEN** a proposed buy exceeds the frozen participation or liquidation-time policy at the execution cutoff
- **THEN** the fill is capped, deferred or excluded according to the preregistered rule and records the capacity reason

#### Scenario: Simulated exit is capacity constrained
- **WHEN** an exit signal occurs but PIT-visible volume cannot support the full simulated order
- **THEN** the backtest models pending/partial liquidation or an adverse stress outcome and MUST NOT assume an immediate full exit at the daily close

### Requirement: Portfolio backtest reports fixed risk stress scenarios
ETF portfolio backtests SHALL report deterministic market, theme, correlation, volatility and liquidity stress results separately from realized historical returns.

#### Scenario: Stress evidence is produced
- **WHEN** the portfolio has sufficient PIT factor and liquidity evidence
- **THEN** the result reports each frozen scenario, maximum stress loss, capacity coverage and contract hash without selecting scenarios to improve apparent performance

#### Scenario: Stress inputs are unavailable
- **WHEN** a scenario requires missing factor, clone or liquidity facts
- **THEN** that scenario is unavailable with a stable reason rather than being filled with zero loss

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
