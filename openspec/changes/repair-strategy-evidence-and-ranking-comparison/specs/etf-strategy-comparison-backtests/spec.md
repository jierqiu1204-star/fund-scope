## MODIFIED Requirements

### Requirement: ETF strategy comparison backtests compare multiple deterministic strategies
The system SHALL compare the current frozen research-ranking Top10, Top10 hysteresis, and Top10 hysteresis with regime/liquidity gate through the existing research evidence workflow. Previously stored multi-style daily comparisons SHALL remain readable only as legacy diagnostics; their retired execution interfaces SHALL NOT be restored by this change.

#### Scenario: Comparison strategies are executed
- **WHEN** a compatible ranking research run is continued
- **THEN** the three registered candidates are evaluated over identical declared dates, common-support universe, execution convention, and costs

#### Scenario: Strategy set is deterministic
- **WHEN** the same date range, source snapshots, initial candidate states, costs, and frozen contracts are used twice
- **THEN** selections, state transitions, curves, and summary metrics are identical

#### Scenario: Legacy comparison is displayed
- **WHEN** a prior multi-style comparison lacks the current ranking identity
- **THEN** it is labeled legacy and cannot establish the performance of the current research ranking

### Requirement: Current workbench strategy uses the shared strategy contract
The research-ranking comparison MUST use the same daily research score contract as the current `/short-term` research ranking, while separately identifying the frozen research portfolio-selection and execution contracts. It MUST NOT imply that a Top10 research portfolio is identical to live allocation, actionable ranking, or tracked-position behavior.

#### Scenario: Current strategy generates historical weights
- **WHEN** the comparison evaluates a historical research-ranking date
- **THEN** it consumes the compatible sealed ranking and existing frozen candidate rules using only signal-time facts

#### Scenario: Backtest does not copy page rules
- **WHEN** the current research strategy is implemented for comparison
- **THEN** it reuses the canonical score and candidate definitions instead of maintaining an independent copy of page scoring or live allocation logic

### Requirement: ETF ranking comparison reports realistic diagnostics
ETF strategy comparison SHALL report the existing primary paired Top10 five-session net excess separately from continuous-portfolio diagnostics, including gross and net return, costs, turnover, rank churn, capital drawdown, concentration, coverage, exclusions, and uncertainty. Capital metrics SHALL use actual share/cash accounting and actual trade changes, not compounded overlapping event returns.

#### Scenario: Comparison result is displayed
- **WHEN** a ranking candidate result is available
- **THEN** the frozen Top10 five-session paired endpoint is labeled primary and other horizons, pure-momentum comparisons, and continuous-portfolio diagnostics are labeled diagnostic or exploratory

#### Scenario: Independent dates are insufficient
- **WHEN** the required independent dates, PIT sessions, folds, coverage, or holdout conditions are missing
- **THEN** observed metrics may be displayed with limitations but the result remains insufficient and is not ranked as a validated winner

#### Scenario: Prices change while target weights remain equal
- **WHEN** relative asset prices change and the strategy rebalances to unchanged equal targets
- **THEN** the account first reflects the resulting actual weight drift and charges fees and slippage on any trades required to restore the targets

#### Scenario: A retained position is not traded
- **WHEN** actual quantities are retained without an executed rebalance
- **THEN** no fictitious full round-trip charge is added and the existing price exposure continues

#### Scenario: A held asset cannot be valued reliably
- **WHEN** a required decision-eligible valuation or execution price is unavailable
- **THEN** the affected metric or common-support interval is unavailable with a reason, rather than using an old price, zero return, or fictitious liquidation

## ADDED Requirements

### Requirement: A frozen pure momentum control remains outside ranking promotion
The comparison SHALL expose one diagnostic control that ranks the same eligible non-clone universe by positive 20-session adjusted return, breaks ties by asset code, selects at most ten assets, and assigns ten-percent target weight per selected asset with remaining weight in cash. Its rules, cost assumptions, execution, and source identities SHALL be frozen before reading outcomes.

#### Scenario: The control is calculated
- **WHEN** compatible ranking-candidate data exists
- **THEN** the control uses the same common-support dates, eligible prices, signal and execution cutoffs, and cost policy, and reports its coverage independently

#### Scenario: Fewer than ten positive-momentum assets exist
- **WHEN** fewer than ten assets qualify
- **THEN** the diagnostic control leaves unfilled slots in cash and records its reduced exposure without filling from another strategy

#### Scenario: The control outperforms
- **WHEN** pure momentum has favorable diagnostic returns
- **THEN** it does not replace the frozen formal baseline, become a fourth promotion candidate, trigger tuning, or change live ranking automatically

### Requirement: Ranking cost sensitivity does not select a new strategy
Ranking comparisons SHALL include the frozen base policy of five basis points fee plus five basis points slippage per side and a diagnostic stress using the same fee with ten basis points slippage per side, with factual execution-cost provenance kept distinct where available.

#### Scenario: Costs change the apparent winner
- **WHEN** the ranking changes under the declared stress
- **THEN** the report shows cost sensitivity without changing the primary cost contract or selecting parameters from the stress result
