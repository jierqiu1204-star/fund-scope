## MODIFIED Requirements

### Requirement: ETF portfolio backtest simulates position handling
The backtest SHALL simulate position changes with the same versioned alert lifecycle and absolute-target action policy used by tracked positions at daily granularity, including hard stop, trailing take-profit, confirmed trend weakening, take-profit watch, eligible exit watch, and position sizing.

#### Scenario: Exit signal occurs
- **WHEN** a held ETF first enters a firing hard-stop or eligible exit-watch episode in the daily replay
- **THEN** the backtest creates one action decision for the episode/action step and records one sell or reduce trade at the absolute target under the configured execution model

#### Scenario: Trailing profit gives back
- **WHEN** a held ETF reaches its dynamic trailing-profit activation level and then gives back enough profit
- **THEN** the backtest records the firing transition, one versioned absolute target action, and the resulting position adjustment without repeating the action on later days that remain in the same episode

#### Scenario: Take-profit watch occurs
- **WHEN** a held ETF enters `take_profit_watch` without a higher-priority actionable rule
- **THEN** the backtest records the watch episode and optional notification event but keeps the position unchanged

#### Scenario: Data becomes unavailable
- **WHEN** required adjusted price or same-contract evidence is unavailable at a replay cutoff
- **THEN** the backtest records a data-waiting/exclusion outcome and MUST NOT synthesize an exit trade from the missing data

### Requirement: ETF portfolio backtest reports comparable metrics
The system SHALL report beginner-readable return, protection, opportunity-cost, action, notification, and comparison-baseline metrics for each completed ETF portfolio backtest.

#### Scenario: Metrics are displayed
- **WHEN** the user opens a completed backtest
- **THEN** the system shows cumulative net return, maximum drawdown, win rate, average holding days, unique action count, turnover, fees, best/worst trade, avoided loss, missed upside, action sample count, notification count/repeat rate, and data coverage

#### Scenario: Baselines are included
- **WHEN** a backtest result is shown
- **THEN** the system includes at least cash waiting, one ETF buy-and-hold or equal-weight benchmark, and a hold-without-alert-actions baseline over the same point-in-time universe and cost assumptions

#### Scenario: Action accuracy is shown
- **WHEN** an actionable action cycle has a completed 1, 3, 5, or 10 trading-day window from its actual simulated fill
- **THEN** the system compares tax/fee-adjusted action-policy NAV with a same-fill-time, same-starting-asset hold counterfactual whose proceeds remain cash for event-level attribution, values both sides at H without a hypothetical terminal liquidation fee, treats later target escalations in that cycle as one policy path, and reports positive-benefit proportion, mean/median benefit, trading-day clustered uncertainty, and sample size without counting soft watches or repeat notifications

### Requirement: ETF portfolio backtest consumes evidence contracts
ETF portfolio backtests SHALL consume ETF research evidence contracts, including signal, allocation, alert lifecycle, action policy, and execution versions, instead of duplicating short-term page, allocation, or notification rules.

#### Scenario: Backtest starts
- **WHEN** an ETF portfolio backtest is created
- **THEN** it records the signal contract version, allocation contract version, alert/action policy version, execution model, fee/slippage model, data cutoff, universe snapshot, and contract hash

#### Scenario: Contract is missing
- **WHEN** a backtest cannot find the required signal, allocation, or action policy contract for the date range
- **THEN** it fails or marks the missing period explicitly instead of silently using a different rule set or a notification template as strategy logic

#### Scenario: Backtest result is displayed
- **WHEN** a completed ETF portfolio backtest is shown
- **THEN** the result identifies whether it matches the current short-term workbench and alert/action contracts and labels differing versions as old-policy evidence

## ADDED Requirements

### Requirement: Backtest execution is separated from notification delivery
ETF portfolio backtests SHALL create simulated trades only from unique action decisions and MUST NOT create trades from notification sends, retries, repeats, suppressions, or recovery messages.

#### Scenario: One action has multiple reminders
- **WHEN** an unresolved hard-stop action has notifications on multiple trading days
- **THEN** the backtest executes the action once and reports the later notifications only in notification metrics

#### Scenario: Notification fails
- **WHEN** a simulated notification delivery is failed or suppressed
- **THEN** the action-decision full-execution scenario remains determined by its declared execution assumption, the SMTP-accepted-email sensitivity excludes the item, and neither scenario creates a second trade or silently changes the action target

#### Scenario: Action full-execution scenario is displayed
- **WHEN** the UI or report shows return produced by following email recommendations
- **THEN** it labels the primary result `动作建议完全执行情景`, optionally shows `仅 SMTP 已接受邮件被执行敏感性`, identifies both as simulated rather than observed user execution, and lists assumed execution delay and costs

#### Scenario: Legacy current semantics are replayed
- **WHEN** the comparison requests the historical relative-reduction baseline
- **THEN** an isolated diagnostic adapter may reproduce old compounding semantics for explanation, but the main action engine rejects that contract and the legacy result is permanently ineligible for production promotion

### Requirement: Daily action execution uses point-in-time realistic assumptions
The daily backtest SHALL evaluate signals at the declared T-day close cutoff and use a predeclared T+1 `adjusted_open` (or fixed timestamp) as the base execution field, with versioned fee, spread, slippage, liquidity, raw-price, and adjustment-factor assumptions; it MUST NOT inspect later T+1 OHLC fields to choose the fill.

#### Scenario: Close signal executes next session
- **WHEN** an action is generated after the T-day close
- **THEN** the base scenario uses decision-eligible T+1 `adjusted_open`, records raw open/adjustment factor/normalized execution price and signal-to-fill delay, and keeps T+1 close or higher slippage as separate predeclared sensitivity scenarios

#### Scenario: T+1 price is unavailable
- **WHEN** the ETF is suspended, has zero tradable volume, is limit-locked without demonstrable liquidity, is delisted, or lacks an eligible T+1 open
- **THEN** the backtest leaves the action pending/deferred or rejects it according to the recorded execution model, MUST NOT fill at T-day close, and starts outcome horizons only from an actual simulated fill

#### Scenario: Intraday evidence is absent
- **WHEN** only daily OHLC/adjusted data exists
- **THEN** the result marks bid/ask, IOPV, intraday hard-stop timing, and real email latency as not validated

#### Scenario: Historical universe is evaluated
- **WHEN** the replay evaluates trading day T
- **THEN** only ETFs whose point-in-time membership shows they were listed and eligible at T participate, and the system MUST NOT substitute the current ETF list

#### Scenario: Cutoff truncation is invariant
- **WHEN** the same T-day signal is computed once with data truncated at T and once with a larger dataset whose API enforces the T cutoff
- **THEN** features, universe, ranking, alert state, and action decision for T are identical within declared numeric tolerance

### Requirement: Historical action replay is bounded and resumable
The backtest SHALL compute point-in-time features in bounded code/date batches, then replay ranking and portfolios by trading day over the complete historical cross-section with shared cash/state, using deterministic checkpoints so full-history research can complete on a 2-core 4-GB host.

#### Scenario: A batch completes
- **WHEN** a configured date/code batch finishes
- **THEN** feature stage stores warm-up-aware outputs, while replay stage stores last completed date, complete candidate cash/positions/high-water/alert/action/pending-fill state, contract/data/code/schema hashes, atomic-completion marker, and stable output keys before proceeding

#### Scenario: A run is interrupted
- **WHEN** the process stops after one or more completed batches
- **THEN** the next invocation resumes after the last compatible checkpoint and merges outputs idempotently without recomputing completed batches

#### Scenario: Contract changes during resume
- **WHEN** checkpoint contract hash, input snapshot hash, code/schema version, or data cutoff differs from the requested run
- **THEN** the system refuses to merge incompatible batches and starts or requires a new run identity

#### Scenario: Code chunks finish feature calculation
- **WHEN** multiple code chunks have completed for a replay date
- **THEN** the portfolio stage waits for the complete point-in-time universe, ranks the full cross-section once, and MUST NOT select Top-N or allocate cash independently inside each chunk

#### Scenario: Chunk size changes
- **WHEN** a fixed small dataset is processed with different feature chunk sizes, resumed from a checkpoint, and processed in one bounded run
- **THEN** all variants produce the same ordered signals, Top-N membership, actions, fills, and equity curve within declared numeric tolerance

#### Scenario: One command reaches its bound
- **WHEN** a feature or replay invocation reaches its explicit work/time bound
- **THEN** it commits a compatible checkpoint and exits for the outer scheduler to continue instead of entering an unbounded internal loop
