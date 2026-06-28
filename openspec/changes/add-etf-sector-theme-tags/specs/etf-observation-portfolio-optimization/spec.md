## MODIFIED Requirements

### Requirement: Theme concentration is capped
The system SHALL limit total weight assigned to one normalized theme group or highly similar theme group using the ETF sector/theme taxonomy.

#### Scenario: Top candidates are all technology ETFs
- **WHEN** the top ranked ETF candidates are concentrated in the same normalized technology theme group
- **THEN** the optimizer reduces theme concentration, excludes or lowers lower-ranked overlapping candidates, and records the cap in the snapshot explanation

#### Scenario: ETF has unknown theme classification
- **WHEN** an otherwise eligible ETF has unknown or low-confidence theme classification
- **THEN** the optimizer treats it conservatively for concentration control and records the classification limitation in the weight or exclusion reason

## ADDED Requirements

### Requirement: Observation Portfolio Uses Normalized Theme Profiles
The ETF observation portfolio optimizer SHALL use normalized ETF theme profiles instead of raw name matching when applying theme concentration constraints.

#### Scenario: Portfolio candidates include theme profiles
- **WHEN** the optimizer evaluates ETF candidates
- **THEN** it reads each candidate's normalized theme group, primary theme, classification confidence, and classification source before assigning weights

#### Scenario: Theme profile is missing
- **WHEN** a candidate lacks a usable theme profile
- **THEN** the optimizer either excludes it or assigns it only under conservative constraints with a clear reason

### Requirement: Portfolio Explains Theme-Based Decisions
The ETF observation portfolio result SHALL explain how theme concentration affected included, watch-only, and excluded ETFs.

#### Scenario: ETF weight is capped by theme concentration
- **WHEN** an ETF receives a lower weight because its theme group is already represented
- **THEN** the portfolio response includes the theme cap reason and the related theme exposure

#### Scenario: ETF is watch-only because of theme overlap
- **WHEN** an ETF is not assigned weight because a similar theme ETF is already included
- **THEN** the response returns it as watch-only or excluded with a readable theme-overlap explanation

### Requirement: Theme Heat Does Not Override Portfolio Risk Rules
Theme heat SHALL NOT override data reliability, liquidity, correlation, premium, entry timing, or single-ETF cap constraints.

#### Scenario: Theme is hot but candidate is ineligible
- **WHEN** a theme has strong heat statistics but an ETF has stale data, weak entry timing, high premium, low liquidity, or excessive correlation
- **THEN** the optimizer keeps the ETF ineligible for target weight and records the concrete reason
