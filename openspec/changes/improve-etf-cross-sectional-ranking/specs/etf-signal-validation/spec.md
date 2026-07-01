## ADDED Requirements

### Requirement: ETF label evidence can inform ranking confidence
The system SHALL allow current, sufficient, decision-eligible ETF label validation evidence to inform ETF ranking confidence through a bounded score adjustment.

#### Scenario: Evidence is sufficient and current
- **WHEN** label validation evidence matches the current rule version or evidence contract, has sufficient samples, and uses decision-eligible data
- **THEN** the ranking may apply a bounded confidence adjustment and records the evidence summary in score breakdown

#### Scenario: Evidence is insufficient
- **WHEN** label validation evidence has too few samples, stale results, mismatched rule version, mismatched evidence contract, or display-only data
- **THEN** the ranking records the limitation and MUST NOT improve the ETF score because of that evidence

#### Scenario: Evidence recently weakens
- **WHEN** recent validation outcomes are materially weaker than longer-window outcomes for the same label combination
- **THEN** the ranking may apply a bounded confidence penalty and records the affected horizon

## MODIFIED Requirements

### Requirement: Validation does not alter live labels automatically
The system SHALL keep validation evidence separate from live label generation and MUST NOT automatically rewrite live labels based only on validation output. Current, sufficient, decision-eligible validation evidence MAY contribute a bounded confidence adjustment to the ETF final ranking score, but it MUST NOT by itself create buy, sell, reduce, stop-loss, target price, or email-triggering actions.

#### Scenario: Validation shows weak evidence
- **WHEN** a label combination has poor historical forward outcomes
- **THEN** the system displays the weak evidence and may apply a bounded ranking confidence penalty, but does not silently rewrite the live label or create a holding alert

#### Scenario: Validation shows strong evidence
- **WHEN** a label combination has strong historical forward outcomes and sufficient current samples
- **THEN** the system may apply a bounded ranking confidence boost, but does not present the result as a guaranteed return or direct buy instruction

#### Scenario: Validation evidence is the only positive input
- **WHEN** an ETF has weak current trend, poor data reliability, poor liquidity, or abnormal premium state
- **THEN** positive validation evidence alone MUST NOT make the ETF eligible for a high-confidence ranking or primary portfolio weight
