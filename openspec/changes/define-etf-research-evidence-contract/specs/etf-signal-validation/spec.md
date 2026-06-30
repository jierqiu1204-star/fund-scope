## ADDED Requirements

### Requirement: ETF signal validation is grouped by evidence contract
ETF signal validation SHALL group historical outcomes by the signal contract fields used by the short-term workbench.

#### Scenario: Label outcome is calculated
- **WHEN** ETF label validation calculates forward returns and drawdowns
- **THEN** it groups results by observation label, entry timing label, signal rule version, data reliability state, and signal date

#### Scenario: Current signal version changes
- **WHEN** the signal rule version changes
- **THEN** old validation results are not presented as current-version validation unless explicitly marked as old evidence

### Requirement: ETF signal validation reports evidence quality
ETF signal validation SHALL report sample sufficiency, coverage, and future-window completion for each label combination.

#### Scenario: Enough completed samples exist
- **WHEN** a label combination has enough completed future windows
- **THEN** the validation summary includes sample count, win rate, average forward return, maximum drawdown, and confidence state

#### Scenario: Future window is incomplete
- **WHEN** a label was generated recently and the required forward window has not elapsed
- **THEN** the validation excludes that item from completed-sample statistics and records it as pending
