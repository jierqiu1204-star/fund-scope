## ADDED Requirements

### Requirement: ETF label evidence is display-only research evidence
The system SHALL expose compatible ETF label validation evidence as a read-only research summary and SHALL NOT use validation, replay, backtest, or healthcheck output to modify the current score, label, rank, allocation, tracked position, alert, or notification.

#### Scenario: Evidence is sufficient and current
- **WHEN** label validation evidence matches the current rule version or evidence contract, has sufficient samples, and uses decision-eligible data
- **THEN** the system records the evidence summary separately from score-bearing breakdown components and leaves all current decision outputs unchanged

#### Scenario: Evidence is insufficient
- **WHEN** label validation evidence has too few samples, stale results, mismatched rule version, mismatched evidence contract, or display-only data
- **THEN** the system records the limitation and leaves all current decision outputs unchanged

#### Scenario: Evidence recently weakens
- **WHEN** recent validation outcomes are materially weaker than longer-window outcomes for the same label combination
- **THEN** the system displays the affected horizon and weakening evidence without applying a score or confidence penalty to the current ranking

#### Scenario: Evidence motivates a future contract change
- **WHEN** reviewed validation or backtest evidence suggests that ranking behavior should change
- **THEN** it may motivate a separately proposed and human-reviewed future contract version but MUST NOT mutate the current contract or trigger current signal generation

## MODIFIED Requirements

### Requirement: Validation does not alter live labels automatically
The system SHALL keep validation evidence separate from live label generation and every other decision domain. Validation evidence MUST NOT rewrite or adjust current live labels, scores, ranks, allocation weights, tracked positions, holding alerts, notification records, or their update timestamps.

#### Scenario: Validation shows weak evidence
- **WHEN** a label combination has poor historical forward outcomes
- **THEN** the system displays the weak evidence but leaves the current label, score, rank, allocation, tracking, alert, and notification outputs unchanged

#### Scenario: Validation shows strong evidence
- **WHEN** a label combination has strong historical forward outcomes and sufficient current samples
- **THEN** the system displays the strong evidence without applying a ranking confidence boost or changing any current decision output

#### Scenario: Validation evidence is the only positive input
- **WHEN** an ETF has weak current trend, poor data reliability, poor liquidity, or abnormal premium state
- **THEN** positive validation evidence MUST NOT change the ETF's ranking eligibility, score, label, rank, or portfolio weight
