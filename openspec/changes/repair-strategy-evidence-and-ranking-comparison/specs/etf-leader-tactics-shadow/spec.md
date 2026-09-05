## ADDED Requirements

### Requirement: Historical leader lifecycle research has an explicit execution contract
Historical leader lifecycle research SHALL be a separately versioned diagnostic from fixed-horizon next-close evaluation. It SHALL freeze T signal-close selection, T+1 close confirmation, T+2 adjusted-open entry, declared daily exit evaluation, next eligible adjusted-open exit, and five basis points fee plus five basis points slippage per side before calculating outcomes.

#### Scenario: A lifecycle completes
- **WHEN** a confirmed signal has valid entry and exit facts under the frozen lifecycle policy
- **THEN** the report records actual entry and exit timing, gross return, charged costs, net return, holding sessions, and a same-period peer comparison with an explicit cost convention

#### Scenario: A price or confirmation fact is unavailable
- **WHEN** an execution or confirmation fact is missing, invalid, or not visible by its cutoff
- **THEN** the lifecycle records a specific pending or exclusion reason and does not substitute a later or raw price

#### Scenario: Fixed-horizon evidence already exists
- **WHEN** a fixed-horizon or zero-cost legacy report is read
- **THEN** it retains its original execution/cost identity and is not merged with the new lifecycle result

### Requirement: Historical lifecycle risk is anchored to entry-time facts
Historical leader lifecycle research SHALL initialize risk using the actual adjusted entry price, the original signal low, and ATR derived only from completed sessions preceding entry. The frozen risk and break-even anchor SHALL not depend on the entry session's future close, high, or low.

#### Scenario: Entry session has a large move
- **WHEN** the entry-session close or intraday range differs substantially from the entry open
- **THEN** changing those later facts cannot change the already frozen initial risk, risk unit, or cost-price anchor

#### Scenario: Entry-time risk cannot be formed
- **WHEN** known entry-time inputs cannot form a finite positive risk unit
- **THEN** the research lifecycle is unavailable or excluded with a reason and does not synthesize a risk estimate

### Requirement: Corrected historical lifecycle evidence remains isolated
Corrected lifecycle evidence SHALL retain current-vintage membership limitations and zero PIT promotion credit, identify event-series drawdown separately from capital drawdown, and remain distinct from the live tracked-position and email exit contracts.

#### Scenario: Corrected historical results improve
- **WHEN** net returns or win rates improve after the research correction
- **THEN** no production score, ranking, allocation, tracked-position threshold, alert, or notification is modified

#### Scenario: Live exit semantics differ
- **WHEN** live tracked-position risk uses a different anchor or initialization time
- **THEN** the research report declares the contract mismatch and does not claim the corrected backtest reproduces current live email behavior
