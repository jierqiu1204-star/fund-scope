## ADDED Requirements

### Requirement: A-share candidate screen separates raw qualification from shadow actionability
The A-share screen SHALL return the immutable raw candidate result together with sentiment risk state, action mode, new-entry permission, proxy provenance, and stable reasons. The default candidate ordering SHALL remain the frozen raw V2 score ordering.

#### Scenario: Risk overlay blocks a new shadow entry
- **WHEN** a qualified A-share breakout is covered by `warning`, `risk_off`, or `unavailable`
- **THEN** the row remains visible in its original order and lifecycle state while the response clearly marks it observe-only

#### Scenario: Existing filters are used
- **WHEN** callers use the existing universe, formula, state, as-of, cursor, or pagination filters
- **THEN** the same snapshot and keyset semantics are preserved and no provider work is triggered

### Requirement: ETF and A-share screening remain isolated
The risk overlay SHALL execute only for the A-share screen and SHALL NOT import, read, score, filter, or mutate ETF comprehensive-ranking or ETF leader-tactics state.

#### Scenario: Same materialization run handles ETF rows
- **WHEN** ETF candidates are screened or read
- **THEN** their hashes, raw qualification, score, ordering, exclusions, and API output remain compatible with the pre-change ETF contract
