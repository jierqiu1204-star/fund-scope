## ADDED Requirements

### Requirement: A-share low-base outcomes use existing causal execution

The system SHALL report 1, 3, 5, 10, and 20-session cost-adjusted outcomes from the first eligible session after each signal, with missing or untradeable entry/exit facts excluded rather than imputed.

#### Scenario: Future horizon is not complete

- **WHEN** a signal lacks a qualified adjusted close for its required future exit session
- **THEN** that horizon remains pending or unavailable and is not counted as a completed return
