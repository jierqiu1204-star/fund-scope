## ADDED Requirements

### Requirement: Historical proxy evaluation recomputes every eligible signal date without future features
The leader-tactics shadow SHALL recompute the frozen breakout and former-leader repair formulas for every eligible historical signal date using only adjusted bars at or before that date, the sealed current-vintage source cohort, and the frozen peer and clone mappings.

#### Scenario: Historical date has one or more matches
- **WHEN** one or more ETFs satisfy every frozen formula gate using facts no later than the signal close
- **THEN** every match remaining after the frozen clone policy is recorded with its signal date, feature values, score, peer group, and immutable feature hash

#### Scenario: Historical date has no match
- **WHEN** no ETF satisfies either frozen formula on an otherwise eligible signal date
- **THEN** the evaluator records a zero-match date and MUST NOT substitute the highest-scoring non-match

### Requirement: Historical proxy outcomes use next-session execution and frozen costs
Each recorded match SHALL enter at the next eligible total-return-adjusted close and SHALL report 1, 3, 5, 10, and 20-session outcomes after 5 basis points of fees and 5 basis points of slippage per side.

#### Scenario: Forward window is complete
- **WHEN** the entry close and requested exit close are decision eligible and finite
- **THEN** the result reports gross return, 20-basis-point round-trip cost, net return, peer benchmark return, and net peer excess for that horizon

#### Scenario: Forward window is incomplete
- **WHEN** a required future session or adjusted price is unavailable
- **THEN** that horizon is excluded with a stable reason and MUST NOT be filled with a later quote, raw close, or zero return

### Requirement: Historical proxy evaluation is bounded and resumable
The evaluator SHALL persist an immutable contract and monotonic checkpoint, process one worker at a time, and produce identical final evidence across compatible page sizes and interruption boundaries.

#### Scenario: Invocation approaches its time budget
- **WHEN** the bounded continuation reaches its configured stop threshold below 55 seconds
- **THEN** it commits the current page and returns a resumable cursor without publishing a partial result as complete

### Requirement: Historical proxy results disclose current-vintage bias
Historical proxy results MUST state that source membership and peer taxonomy come from a later sealed source snapshot and therefore do not constitute factual PIT evidence.

#### Scenario: Backtest metrics appear favorable
- **WHEN** any horizon has positive average return, win rate, or confidence interval
- **THEN** the result remains research-only, grants zero formal PIT promotion credit, and does not become a recommendation or production signal
