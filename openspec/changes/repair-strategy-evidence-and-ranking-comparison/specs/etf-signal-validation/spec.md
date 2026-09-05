## MODIFIED Requirements

### Requirement: ETF signal outcomes are validated against forward returns
The system SHALL validate ETF ranking labels and entry timing labels against future 1, 3, 5, and 10 trading-day outcomes using decision-eligible historical price data and the original immutable signal identity. Signal inputs MUST be visible at the signal cutoff; future outcome facts MUST be visible at their separately recorded outcome cutoff.

#### Scenario: Validation run computes label outcomes
- **WHEN** a validation run processes historical ETF signal items with enough future prices
- **THEN** it records sample count, average return, median return, win rate, worst forward drawdown, and horizon for each label combination

#### Scenario: Missing future price excludes a sample
- **WHEN** the required future sessions have elapsed but a required decision-eligible entry or exit price is missing
- **THEN** the sample is excluded from completed statistics with a missing-price reason and can be reconsidered when a valid later outcome fact is received without reconstructing the original signal

#### Scenario: Future window has not elapsed
- **WHEN** the required future exchange sessions have not elapsed
- **THEN** the sample remains pending and does not enter completed-sample statistics

### Requirement: ETF signal validation is grouped by evidence contract
ETF signal validation SHALL group historical outcomes by their source kind, ranking contract ID and hash, score field and version, rule version, price basis, execution model, and horizon. Each sample SHALL retain its original signal date, source snapshot identity, label, entry timing label, and data reliability state; compatible dates MAY be summarized together only with an explicit date range and independent-date count.

#### Scenario: Label outcome is calculated
- **WHEN** ETF label validation calculates forward returns and drawdowns
- **THEN** the result preserves the original observation label, entry timing label, signal rule version, reliability state, signal date, and complete source identity

#### Scenario: Current signal version changes
- **WHEN** the signal rule or ranking contract changes
- **THEN** old validation results remain in a separate evidence group and are displayed as old evidence instead of being relabeled as current validation

#### Scenario: Production and replay share a score formula
- **WHEN** a production-published cohort and a research-replay cohort use the same score formula
- **THEN** their source kinds and evidence groups remain distinct and neither is silently used to fill gaps in the other

### Requirement: ETF ranking validation has one fixed primary estimand
Current research-ranking validation SHALL use the paired difference in five-session net return between the continuous candidate Top10 policy and the frozen continuous baseline Top10 policy on identical non-overlapping signal windows and the same common-support eligible universe as its only primary ranking endpoint. The execution and endpoint sub-contract SHALL explicitly distinguish this account-based return from legacy fixed-holding-period event returns.

#### Scenario: Primary outcome is computed
- **WHEN** signal date T has complete account valuations from the T+1 close before rebalancing through the T+6 close before rebalancing
- **THEN** the five-session return is ending equity divided by starting equity minus one, including only actual trades and their five-basis-point fee plus five-basis-point slippage per side executed at T+1 through T+5 closes, without a fictitious window-boundary liquidation or an additional round-trip deduction

#### Scenario: Exploratory cell is favorable
- **WHEN** a Top5, Top20, 1-day, 3-day, or 10-day exploratory cell outperforms while the primary endpoint fails
- **THEN** the candidate remains ineligible for promotion

#### Scenario: A primary window starts with an existing holding
- **WHEN** the continuous policy already holds an asset and no actual trade occurs during the window
- **THEN** that holding incurs no artificial entry or exit charge and its cash/share state is carried across windows

#### Scenario: A legacy fixed-horizon sample is available
- **WHEN** an old event sample uses a separately opened position and fixed round-trip fees
- **THEN** it remains an event diagnostic under its original identity and cannot enter the new continuous-policy primary sample

## ADDED Requirements

### Requirement: ETF forward validation matures historical published cohorts independently of current publication
The system SHALL revisit compatible historical published cohorts with due unfinished outcomes even when today's ETF ranking is absent, delayed, or unavailable. It SHALL process bounded oldest-due work idempotently and SHALL keep current-publication availability distinct from historical-outcome progress.

#### Scenario: Today has no ETF publication
- **WHEN** today's ranking is unavailable but a previously published cohort now has valid future prices
- **THEN** its due outcomes mature and its summary advances while today's publication remains unavailable

#### Scenario: New cohorts arrive every day
- **WHEN** a new ranking is published while older cohorts still have incomplete horizons
- **THEN** new observation collection does not replace or permanently skip the older cohorts' maturity work

#### Scenario: Completed work is retried
- **WHEN** the same signal, horizon, execution contract, and outcome-input identity are processed again
- **THEN** completed counts and returns remain unchanged and no duplicate sample is added

#### Scenario: A completed label outcome has a different execution contract or input revision
- **WHEN** an existing completed label outcome has a legacy or incompatible identity, or corrected prices arrive later
- **THEN** the original completed outcome remains unchanged and is not relabeled as current evidence; completed-label recalculation is unavailable in this change, while ranking outcome revisions use their separately identified artifact/evidence path

### Requirement: ETF ranking validation selects sources by explicit ranking contract
The system SHALL select and interpret validation sources using their explicit source kind, actual persisted ranking contract, score field, rule version, publication state, and price provenance. Current research-score sources SHALL be supported without accepting unrelated legacy score rows or weakening publication-quality gates.

#### Scenario: Current research ranking is validated
- **WHEN** a compatible published daily research-score cohort is available
- **THEN** its persisted research score is used and the resulting validation records its actual source contract rather than a legacy final-score identity

#### Scenario: A source is incompatible
- **WHEN** a source lacks a required contract, score, publication state, adjusted-price provenance, or coverage
- **THEN** validation records the specific reason and does not substitute a different date or score generation to claim current evidence

#### Scenario: Historical snapshot lacks a recoverable identity
- **WHEN** required source identity cannot be verified from an immutable original snapshot
- **THEN** the result remains legacy or unavailable and no current hash is manufactured
