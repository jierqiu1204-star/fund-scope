## MODIFIED Requirements

### Requirement: Comparison metrics include return and risk
The system SHALL report beginner-readable return, risk, action-effectiveness, opportunity-cost, notification-quality, and trading metrics for each compared strategy under the same data and execution contract.

#### Scenario: Metrics are displayed
- **WHEN** the user views a completed strategy comparison
- **THEN** the system shows cumulative net return, maximum drawdown, volatility, return-to-drawdown ratio, unique action count, turnover, fees/slippage, win rate, avoided loss, missed upside, action accuracy with sample size, notification count/repeat rate, cash-wait days, and data coverage for each strategy

#### Scenario: Strategy has insufficient data
- **WHEN** a strategy has too few valid trading days, independent action episodes, completed outcome windows, or indicator warm-up observations under the pre-registered gate
- **THEN** the system marks that strategy as `样本不足`, shows the unmet gate, and MUST NOT rank it as historically better

#### Scenario: Metrics use different contracts
- **WHEN** two candidate results do not share the same point-in-time universe, adjusted-price cutoff, fee/slippage model, or outcome windows
- **THEN** the system marks them non-comparable and MUST NOT produce a winner from the mismatched results

### Requirement: Comparison backtests are research-only
The system SHALL present ETF strategy comparison backtests as research evidence, not trading instructions, and SHALL require an explicit versioned promotion decision outside the backtest before any live rule changes.

#### Scenario: User views comparison summary
- **WHEN** the UI renders strategy comparison results
- **THEN** it states that the results are historical simulations, identifies train/validation/final-holdout windows, does not guarantee future returns, and does not execute trades

#### Scenario: Best historical strategy exists
- **WHEN** one strategy has the strongest historical or validation metric
- **THEN** the system MUST NOT automatically replace the live `/short-term` strategy, action policy, or email alert rules

#### Scenario: Final holdout is inspected
- **WHEN** the frozen final-holdout result is generated
- **THEN** the system records that candidate definitions and promotion gates were frozen beforehand and MUST NOT use the holdout result to retune the same candidates

## ADDED Requirements

### Requirement: Alert action policy comparison uses a frozen small candidate set
The system SHALL compare no more than three pre-registered alert action policy candidates for this change and MUST NOT run a threshold grid, hyperparameter search, or automatic best-parameter selection.

#### Scenario: Candidate set is created
- **WHEN** an alert action comparison run is initialized
- **THEN** it records exactly the selected subset of diagnostic-only `current_semantics_baseline`, `idempotent_absolute_targets`, and `idempotent_absolute_targets_with_monotonic_trailing`, including every policy and parameter version before outcomes are computed

#### Scenario: Extra candidate is requested
- **WHEN** a run requests more than three candidates or dynamically generated threshold variants
- **THEN** the system rejects the run for this validation protocol and explains that a new pre-registered research change is required

#### Scenario: Semantic fix is isolated
- **WHEN** `idempotent_absolute_targets` is compared with the baseline
- **THEN** ranking inputs and threshold values remain equal while only absolute targeting, episode idempotency, recommendation/execution separation, and `take_profit_watch=hold` differ

#### Scenario: Legacy baseline appears strongest
- **WHEN** the diagnostic `current_semantics_baseline` has the strongest historical metric
- **THEN** the system still marks it ineligible for promotion because it intentionally violates the absolute-target production contract

#### Scenario: Monotonic trailing candidate is evaluated
- **WHEN** `idempotent_absolute_targets_with_monotonic_trailing` is included
- **THEN** its long-position effective stop never decreases after tightening and `k`, ATR warm-up, adjusted high-versus-close peak source, trigger price, recovery hysteresis, same-day OHLC ordering, and missing-data behavior are frozen before validation

### Requirement: Alert action candidates use time-ordered validation
Candidate evaluation SHALL preserve chronological order, use point-in-time decision-eligible data, purge outcome overlap across window boundaries, and reserve a final holdout that is evaluated once per frozen run contract after candidates and gates are frozen.

#### Scenario: Walk-forward evaluation runs
- **WHEN** sufficient historical action episodes exist
- **THEN** the system reports each candidate across multiple ordered train/validation windows without allowing later observations into earlier decisions, and purges an action from the prior label window when its longest 10-trading-day outcome crosses the boundary

#### Scenario: Final holdout is not ready
- **WHEN** adjusted-price coverage, independent episode count, completed outcome windows, or contract consistency is below the pre-registered gate
- **THEN** the system keeps the result `research_only/sample_insufficient` and does not consume or substitute the final holdout with old or simulated evidence

#### Scenario: Final holdout is consumed
- **WHEN** all frozen gates pass and the final holdout is evaluated for a policy/run contract
- **THEN** the system persists first-consumed time and input snapshot hash and rejects attempts to inspect changed candidates or repeatedly rerun the same contract as a fresh holdout

#### Scenario: Candidate degrades outside training
- **WHEN** a candidate improves in the development window but loses protection/return stability or materially increases missed upside in validation windows
- **THEN** the comparison reports the degradation and MUST NOT recommend production promotion based only on the development result

#### Scenario: Correlated same-day actions are summarized
- **WHEN** multiple ETF actions share a signal trading day
- **THEN** uncertainty uses a pre-registered trading-day cluster or block bootstrap and MUST NOT treat every ETF action as an independent observation

### Requirement: Alert action comparison has one primary selection endpoint
The validation protocol SHALL use Top20 10-trading-day tax/fee-adjusted mean action-cycle benefit as its primary selection endpoint and SHALL apply a pre-registered maximum-drawdown non-inferiority gate; candidate 3 may be proposed over the correctness baseline candidate 2 only when its clustered improvement lower bound exceeds a pre-registered minimum practical benefit, while other Top-N and horizons remain secondary diagnostics.

#### Scenario: Primary endpoint is registered
- **WHEN** the first validation run contract is sealed
- **THEN** it records Top20, 10 trading days, action-cycle mean benefit, H-day mark-to-market convention, counterfactual cash handling, cost model, drawdown tolerance, minimum practical benefit, sample gate, bootstrap seed/resample count/block length, and candidate-selection rule before calculating outcomes

#### Scenario: A secondary slice looks better
- **WHEN** Top3/5/10/20 portfolio returns, 1/3/5-day outcomes, or one exit reason shows a more favorable result than the primary endpoint
- **THEN** the report labels it secondary and MUST NOT substitute it post hoc as the candidate-selection criterion

### Requirement: Action and notification quality are evaluated separately
The comparison SHALL evaluate unique action-cycle policy paths independently from notification SMTP-attempt frequency so a strategy cannot appear better or worse solely because it sends more emails.

#### Scenario: Persistent alert sends repeated reminders
- **WHEN** one alert episode creates one action and multiple permitted reminders
- **THEN** return, turnover, and action accuracy count one action-cycle path while notification metrics separately count item, envelope, repeat-slot, and `smtp_accepted/failed/unknown` attempts

#### Scenario: Soft watch predicts future weakness
- **WHEN** `take_profit_watch` is followed by negative 1, 3, 5, or 10 trading-day returns
- **THEN** the comparison may report watch-signal outcome quality but MUST NOT insert a simulated reduction into action-policy return
