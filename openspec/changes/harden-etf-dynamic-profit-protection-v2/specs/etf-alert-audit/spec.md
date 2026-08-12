## ADDED Requirements

### Requirement: ETF profit-protection decisions are replayable

The alert audit SHALL record the adjusted-data eligibility, sample count, risk-unit source, armed state, persisted high-water, previous protection line, effective protection line and distance to trigger.

#### Scenario: Profit protection is unavailable
- **WHEN** the required adjusted history is unavailable or ineligible
- **THEN** the audit and UI expose a stable unavailable reason and no actionable trailing-profit email is generated

#### Scenario: Profit protection is evaluated
- **WHEN** a tracked ETF is evaluated with eligible adjusted history
- **THEN** the saved context contains enough versioned inputs and state to reproduce why the line held, rose or triggered
