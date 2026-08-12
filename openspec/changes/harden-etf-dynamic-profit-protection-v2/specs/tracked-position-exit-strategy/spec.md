## ADDED Requirements

### Requirement: ETF profit protection uses eligible adjusted risk data

The system SHALL derive ETF profit-protection risk units only from bounded, decision-eligible, total-return-adjusted daily facts and SHALL fail closed when that evidence is insufficient.

#### Scenario: Eligible adjusted history is available
- **WHEN** at least 30 eligible adjusted sessions exist for a tracked ETF
- **THEN** the system calculates a versioned robust risk unit and records the price basis, sample count and source status

#### Scenario: Only raw or ineligible history exists
- **WHEN** raw daily prices exist but eligible adjusted history is insufficient
- **THEN** the system returns a stable data-waiting or conservative-display state and MUST NOT use those raw rows to make a trailing-profit email actionable

### Requirement: Long profit protection ratchets monotonically

The system SHALL persist the high-water profit and armed protection line for each active position episode, and the effective long protection line SHALL never move downward within that episode.

#### Scenario: Position reaches a new profit high
- **WHEN** current or observed profit exceeds the persisted high-water value after the start threshold is reached
- **THEN** the system raises the high-water value and recalculates a protection line no lower than the previous line

#### Scenario: Volatility expands after protection is armed
- **WHEN** a later risk estimate would imply a wider giveback
- **THEN** the existing protection line remains unchanged or rises and MUST NOT be loosened

#### Scenario: A later chart window omits the historical peak
- **WHEN** the bounded display chart no longer contains the historical peak
- **THEN** the persisted high-water and protection line continue to govern the position

### Requirement: Profit-protection display is historically truthful

The system SHALL distinguish start threshold, allowed giveback and actual protection line, and SHALL expose the protection line as it evolved at each chart date.

#### Scenario: Chart is rendered after protection is armed
- **WHEN** the user opens a tracked ETF detail
- **THEN** the chart shows no protection before arming and a non-decreasing step line afterward instead of backfilling the current line across prior dates
