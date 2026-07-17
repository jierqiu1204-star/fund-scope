## ADDED Requirements

### Requirement: Research Replay Uses A Distinct Immutable Ranking Contract
The system SHALL identify every historical ranking replay as `ranking_source_kind=research_replay`, SHALL persist its replay/score manifest, universe, input, cutoff, price-basis, schema, candidate, execution, cost, and seed identities, and MUST NOT present that identity as a historical production `final_score_v3` publication.

#### Scenario: Daily-reconstructable replay is created
- **WHEN** the system initializes a historical ranking replay
- **THEN** it records `daily_reconstructable_v1`, `research_score`, the complete immutable contract hashes, date range, cutoff semantics, and `total_return_adjusted` basis before reading outcomes

#### Scenario: Production v3 inputs are missing historically
- **WHEN** spread, IOPV, premium, provider consensus, catalyst, or another production-v3 input cannot be reconstructed at the historical cutoff
- **THEN** the replay keeps the production contract unavailable and MUST NOT fill the input with zero, 50, current data, raw price, or a research-score alias

### Requirement: Replay Inputs Are Point-In-Time And Reconstructable
The system SHALL use only immutable, externally receipted, effective-dated ETF membership facts and decision-eligible adjusted market data provably compatible with each replay cutoff and SHALL record stable exclusions for unprovable provenance. Operational current-membership rows MUST NOT become historical replay authority.

#### Scenario: External membership receipt is accepted
- **WHEN** a factual backfill supplies a non-conflicting interval with an external source id, provider and version, observation timestamp, evidence hash, and verified raw-payload hash
- **THEN** the system writes one immutable additive fact and makes it eligible only for cutoffs at or after its observation timestamp

#### Scenario: Operational membership exists without a receipt
- **WHEN** the production current-membership table covers a replay date but the immutable replay fact table has no qualifying external receipt
- **THEN** the replay reports that date as unreconstructable and MUST NOT copy, infer, or migrate the operational row

#### Scenario: Backfill is dry-run or conflicting
- **WHEN** the bounded backfill is a dry-run, contains mismatched hashes, contains overlapping distinct facts, or attempts update/delete
- **THEN** it writes nothing, reports or rejects the conflict deterministically, and preserves every existing fact unchanged

#### Scenario: Membership audit reaches a bound
- **WHEN** the audit/backfill reaches its receipt, source-row, date-page, or 55-second runtime bound
- **THEN** it stops at a stable cursor, commits no partial receipt page, and reports every examined date lacking the explicitly required external facts without inferring any member

#### Scenario: Current survivor appears in a historical query
- **WHEN** an ETF is present today but has no factual eligible membership interval covering the replay date
- **THEN** it is absent from that date's authoritative universe and is not inferred from current membership or first stored price

#### Scenario: Future-available input is supplied
- **WHEN** an input, revision, classification, or availability timestamp exceeds the replay cutoff
- **THEN** the input is rejected with a no-look-ahead reason and does not affect feature, score, rank, policy, or outcome

#### Scenario: Later multiplicative adjustment rescales old OHLC
- **WHEN** a documented adjusted provider applies one constant multiplicative scale to every price inside a historical feature window
- **THEN** only contract-declared scale-invariant features may remain eligible after immutable before/after receipts prove the actual revision, invariance tests MUST produce identical values and ranks, and a provider/version label by itself MUST NOT satisfy the proof

### Requirement: Replay Ranks One Complete Date Cross-Section
The system SHALL calculate cross-sectional score, global rank, Top-N cohorts, and `all_scored` only after the full authoritative point-in-time universe for one replay date has a verified completion manifest.

#### Scenario: Stage A code batches are incomplete
- **WHEN** only part of a date's eligible ETF feature rows has been materialized
- **THEN** Stage B does not calculate or persist that date's rank, Top-N, baseline, or outcome

#### Scenario: Full date is materialized
- **WHEN** feature membership, unique codes, contract hashes, and expected counts match the authoritative date manifest
- **THEN** the system computes one deterministic full-date order before any Top-N or presentation filter

#### Scenario: Chunk size changes
- **WHEN** the same contract is processed with different safe code/date batch sizes or interruption points
- **THEN** feature hashes, ranks, Top-N membership, replay events, and summaries are identical

### Requirement: Research Replay Is Bounded And Resumable
Every replay continuation SHALL use one worker, explicit input/output work bounds, a maximum runtime of 55 seconds, atomic complete-unit checkpoints, and idempotent stable output identities.

#### Scenario: Continuation reaches a bound
- **WHEN** a feature or date replay reaches its row, event, date, memory, or 55-second bound
- **THEN** it commits only complete units, records the next cursor, exits normally, and resumes without duplicating prior output

#### Scenario: Replay identity changes
- **WHEN** score manifest, candidate registry, cutoff, universe/input hash, schema version, or execution contract differs from a stored checkpoint
- **THEN** the system rejects resume and requires a new replay identity

### Requirement: Research Replay Cannot Become Canonical Production State
Research replay SHALL write only Strategy Lab artifacts and research validation evidence and MUST NOT publish a canonical ranking or mutate allocation, tracked positions, risk alerts, action decisions, notification items/envelopes, SMTP attempts, or production policy versions.

#### Scenario: Replay finishes successfully
- **WHEN** a research replay produces sufficient historical results
- **THEN** its source kind remains `research_replay`, its promotion status remains review-only, and canonical production selectors cannot return it

#### Scenario: Production contract remains unavailable
- **WHEN** research replay is available but no exact published production snapshot cohort exists
- **THEN** the API shows both facts separately and MUST NOT use replay evidence to clear the production waiting state

### Requirement: Replay Reports Coverage By Independent Dimensions
The system SHALL report point-in-time universe, adjusted-price, feature/component, score-eligible, and forward-outcome coverage separately and SHALL identify the exact denominator and exclusions for each dimension.

#### Scenario: Prices cover the universe but scores do not
- **WHEN** adjusted-price coverage passes but required feature or score coverage is below its gate
- **THEN** the report keeps price coverage successful, score coverage insufficient, and does not claim the date is ranking-eligible

#### Scenario: Future windows are pending
- **WHEN** rankings exist but one or more T+1 entry or horizon exits have not completed
- **THEN** the system reports those observations as pending outcome coverage and excludes them from completed statistics

### Requirement: Ranking Candidate Evaluation Is Frozen And Low-Cardinality
The system SHALL evaluate no more than three pre-registered ranking candidates, SHALL use Top10 five-trading-day cost-adjusted paired excess return versus `all_scored` as the primary ranking endpoint, and MUST NOT run a grid, hyperparameter search, or post-hoc endpoint selection.

#### Scenario: Candidate registry is sealed
- **WHEN** ranking development begins
- **THEN** the system freezes the baseline, Top-k dropout/rank-hysteresis, and hysteresis-plus-existing-regime-gate candidates with every parameter and promotion gate before calculating outcomes

#### Scenario: Exploratory cell performs best
- **WHEN** Top5/20 or a 1/3/10-day result exceeds the Top10 five-day primary endpoint
- **THEN** it remains exploratory and cannot replace the predeclared selection endpoint

#### Scenario: Final holdout is requested twice
- **WHEN** the same frozen replay/candidate contract has already consumed its final holdout
- **THEN** the system rejects another fresh holdout result and preserves the first input hash and consumption time
