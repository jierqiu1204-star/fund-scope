## MODIFIED Requirements

### Requirement: Short ETF Signals Use Research Language
The system SHALL generate separate daily research and actionable ranked ETF signal outputs using deterministic metrics, SHALL identify actionable Top 20 intraday watch candidates only from ETFs that pass the actionable contract, and SHALL frame every conclusion as research observation rather than trading instruction.

#### Scenario: Latest signals are returned
- **WHEN** the user runs short-term ETF signal generation
- **THEN** the system persists one auditable run with independently ordered research and actionable items, score breakdowns, eligibility reasons, risk flags, theme labels, and observation-oriented conclusions

#### Scenario: Top 20 watch candidates are identifiable
- **WHEN** a successful ETF signal run has at least one actionable item
- **THEN** the first 20 actionable ETF items are available to the intraday ETF watch service without recomputing the full ETF universe during market hours

#### Scenario: Research-only ETF is retained
- **WHEN** an ETF passes adjusted-daily research eligibility but fails an actionable gate
- **THEN** it remains in the research output and is absent from the actionable Top 20 with a readable exclusion reason

#### Scenario: Prohibited trade language is absent
- **WHEN** the API returns a short-term ETF signal item from either surface
- **THEN** it does not include buy, sell, target price, expected return, or guaranteed profit fields

### Requirement: ETF Default Display Uses Quality Gates
The system SHALL separate all stored ETFs, the default daily research display, and the actionable subset by applying deterministic, versioned quality gates.

#### Scenario: Qualified ETF appears in default display
- **WHEN** an ETF has at least 61 point-in-time decision-eligible total-return-adjusted sessions ending on the ranking date
- **THEN** it is eligible for the default daily research ranking even if intraday actionable fields are unavailable

#### Scenario: Qualified ETF appears in default research display
- **WHEN** an ETF has at least 61 point-in-time decision-eligible total-return-adjusted sessions ending on the ranking date
- **THEN** it is eligible for the default daily research ranking even if intraday actionable fields are unavailable

#### Scenario: Short-history ETF is visibly provisional
- **WHEN** an ETF has 61 to 119 eligible adjusted sessions
- **THEN** it remains in the research display with a short-history state and cannot appear as actionable

#### Scenario: Qualified ETF appears in actionable display
- **WHEN** an ETF has at least 120 eligible adjusted sessions and passes all current actionable market-data and risk gates
- **THEN** it may appear in the actionable ranking

#### Scenario: Unqualified ETF remains searchable
- **WHEN** an ETF fails a research or actionable quality gate but remains part of the stored ETF universe
- **THEN** it remains searchable with the failing surface and reason shown to the user
