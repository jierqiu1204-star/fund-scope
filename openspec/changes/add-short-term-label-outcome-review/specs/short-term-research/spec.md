## ADDED Requirements

### Requirement: Short-Term Workbench Shows Label Outcome Evidence
The short-term research workbench SHALL show historical outcome evidence for the selected ETF's current observation label and entry timing label when validation data is available.

#### Scenario: Evidence exists for selected ETF label
- **WHEN** the user selects an ETF whose current label combination has completed validation samples
- **THEN** the detail panel shows sample count, 1/3/5/10 trading-day outcome summary, win rate, median return, worst drawdown, and confidence state

#### Scenario: Evidence is insufficient
- **WHEN** the current label combination has insufficient completed samples
- **THEN** the detail panel shows `样本不足` and explains that the label is still accumulating evidence

### Requirement: Ranked Cards Show Compact Label Confidence
The short-term research workbench SHALL show compact label-confidence status on ETF ranked cards without turning it into a buy instruction.

#### Scenario: Ranked card has validation summary
- **WHEN** an ETF ranked card has validation confidence data
- **THEN** the card shows a short confidence label such as `样本充足`, `样本有限`, `近期走弱`, or `样本不足`

#### Scenario: User views confidence text
- **WHEN** the UI displays label confidence
- **THEN** the UI states that it is historical evidence and not a promise of future returns

### Requirement: Label Evidence Includes Recent Examples
The selected ETF detail view SHALL show recent historical examples of the same or similar label outcome when available.

#### Scenario: Recent examples exist
- **WHEN** recent completed signal examples exist for the selected ETF or current label combination
- **THEN** the UI shows the signal date, original label, future horizon result, and maximum drawdown for a small recent sample list

#### Scenario: Recent examples do not exist
- **WHEN** no recent examples exist
- **THEN** the UI shows an empty state rather than hiding the validation section silently
