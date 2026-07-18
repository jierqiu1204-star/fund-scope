## ADDED Requirements

### Requirement: Ranking validation persists one registered source kind
Every successful ranking validation SHALL identify exactly one `production_published` or `research_replay` source kind and SHALL persist a canonical immutable source manifest containing every source event used by the multi-date cohort. The validation header SHALL persist the manifest hash; a production manifest SHALL contain one published signal-run identity per source date, while a research manifest SHALL contain its replay identity and one replay event per source date.

#### Scenario: Production-published validation starts
- **WHEN** validation consumes compatible published full `final_score_v3` snapshots
- **THEN** the run persists `ranking_source_kind=production_published`, an ordered manifest of every real source signal-run id/date/hash tuple, its canonical manifest hash, exact cohort contract identities, and no replay key

#### Scenario: Production snapshot compatibility is checked
- **WHEN** a snapshot is considered for `production_published` validation
- **THEN** it SHALL be a successful, published, full, ETF-only run for the exact signal date under `final_score_v3`, `final_score_v3_rule_v2`, `ranking_score`, and `total_return_adjusted`; carry data cutoff, idempotency key, scope, universe, input, and ranking-contract hashes; have internally consistent expected, decision-covered, eligible, and item counts; meet both 95 percent publication coverage gates; and contain only finite eligible scores with contiguous global ranks

#### Scenario: Research-replay validation starts
- **WHEN** validation consumes immutable `daily_reconstructable_v1` replay cohorts
- **THEN** the run persists `ranking_source_kind=research_replay`, an immutable replay key, ordered replay-event manifest and manifest hash, and no production signal-run identity

#### Scenario: Research replay identity is checked
- **WHEN** a `daily_reconstructable_v1` cohort is registered
- **THEN** its immutable tuple is `daily_reconstructable_v1` / `research_score` / `total_return_adjusted` / `research_replay` / no publication state, and its replay contract hash, source-event hash, availability cutoff, and universe/input identities are immutable and identical across the cohort

#### Scenario: Source identity is inconsistent
- **WHEN** source kind, any manifest event, signal-run id, replay key, source date, publication state, contract hash, score field, price basis, ordering, count, or manifest hash is inconsistent
- **THEN** validation fails closed or remains unregistered and MUST NOT become same-contract evidence

#### Scenario: Legacy aggregate has only one linked source
- **WHEN** a multi-date legacy validation row links one signal run but cannot reproduce every source date and immutable event in its aggregate
- **THEN** factual repair leaves it unregistered and MUST NOT infer the missing source manifest

#### Scenario: No compatible source event exists
- **WHEN** source discovery finds zero compatible immutable events or cannot build a valid manifest
- **THEN** execution may finish normally for diagnostics, but evidence status is `unavailable/unregistered`, no registered source kind or success manifest is persisted, and no sufficiency classification is produced

#### Scenario: Manifest exists but samples are insufficient
- **WHEN** a complete compatible manifest exists but completed, independent, paired, or coverage counts remain below their gates
- **THEN** the validation is a registered successful evidence run with direction `insufficient`, exact blockers, and no supportive/negative promotion

### Requirement: Forward outcomes use one registered T+1 net-return contract
Every validation sample SHALL use the next exchange session's decision-eligible total-return-adjusted close as entry and the close `horizon` exchange sessions after entry as exit, under one immutable execution-cost contract.

#### Scenario: Completed adjusted-price outcome is calculated
- **WHEN** both the T+1 entry and T+1-plus-horizon exit have positive finite decision-eligible `total_return_adjusted` closes
- **THEN** the sample records gross return, the registered fee and slippage legs, and net return under the same execution contract as every compared sample

#### Scenario: Future exit has not occurred
- **WHEN** the exchange calendar has not yet reached the required exit session
- **THEN** the sample is `future_window_pending` and is not counted as completed or silently replaced by an earlier close

#### Scenario: Adjusted entry or exit is missing
- **WHEN** either required adjusted-price leg is missing, ineligible, non-finite, raw, or from a forbidden fallback provider basis
- **THEN** the sample is excluded with `missing_adjusted_entry_or_exit` and raw Sina/efinance prices, estimates, and stale prices MUST NOT repair it

### Requirement: Validation lookback is derived from exchange-session requirements
The system SHALL determine its source-date range from the configured horizons, non-overlap policy, required independent dates, exchange calendar, future-window completion, and bounded retention rather than a universal fixed calendar-day default.

#### Scenario: Top10 five-session primary endpoint is requested
- **WHEN** the primary endpoint requires 20 completed non-overlapping five-session observations
- **THEN** the planner selects a source range capable of containing those exchange-session windows or reports the exact shortfall

#### Scenario: Ten-session exploratory outcome is requested
- **WHEN** a validation run includes the ten-session horizon
- **THEN** the planner accounts for its longer non-overlap and future-exit span instead of reusing the shorter five-session range

#### Scenario: Compatible source dates are sparse
- **WHEN** the theoretical minimum span contains fewer than the required compatible completed source snapshots
- **THEN** the planner iteratively searches older indexed compatible source dates until it obtains the required completed non-overlapping cohort or reaches the declared retention/query cap, while separately reporting the theoretical 60/100/140/240-session span for 1/3/5/10-session horizons and the actual source-date shortfall

#### Scenario: Retention cannot satisfy the plan
- **WHEN** the bounded available history cannot contain the required completed independent dates
- **THEN** the result is `insufficient_independent_dates` with required, completed, pending, overlapping, and excluded counts and the confidence gate remains unchanged

### Requirement: Validation distinguishes calculation availability from evidence sufficiency
The system SHALL preserve calculated exploratory metrics while separately reporting source registration, outcome completion, coverage, independent-date sufficiency, and effect direction.

#### Scenario: Returns exist but source is unregistered
- **WHEN** aggregate returns were calculated from a validation row without a valid registered source identity
- **THEN** the API exposes the calculation as legacy/unregistered and MUST NOT report `同源已验证`

#### Scenario: Source is exact but future window is open
- **WHEN** a compatible source exists but one or more required future exits have not occurred
- **THEN** validation reports `future_window_pending` and excludes pending dates from completed-sample confidence

#### Scenario: Exact source and sample gates pass
- **WHEN** registered same-contract sources provide the required non-overlapping dates and at least 95 percent required date and asset coverage
- **THEN** validation may classify evidence as sufficient and separately reports supportive, inconclusive, or negative direction

### Requirement: Candidate evidence is evaluated as pre-registered paired net excess
The primary candidate comparison SHALL pair candidate and baseline observations on identical completed signal dates and execution contracts and SHALL evaluate the pre-registered endpoint without selecting a horizon or cohort after observing returns.

#### Scenario: Paired sufficiency is evaluated
- **WHEN** a candidate is compared with its registered baseline
- **THEN** only common completed non-overlapping signal dates count, at least 20 paired dates and 95 percent paired coverage are required, and unmatched candidate-only or baseline-only dates remain excluded from the paired estimate

#### Scenario: Candidate direction is classified
- **WHEN** paired samples satisfy coverage and date gates
- **THEN** the result reports paired net excess return, uncertainty interval, win rate, and drawdown comparison and applies the frozen candidate-registry effect and drawdown gates rather than treating a positive unpaired mean as supportive evidence

#### Scenario: Multiple horizons are available
- **WHEN** exploratory 1/3/5/10-session results are present
- **THEN** the registered Top10 five-session primary endpoint remains primary and other horizons remain exploratory unless a new candidate contract was registered before outcomes were observed

### Requirement: Validation materialization is bounded and resumable
The system SHALL materialize source-date, ETF, and horizon samples through one idempotent continuation with stable pages, an atomic checkpoint, bounded resources, and aggregate publication only after all required pages for the manifest are complete.

#### Scenario: Validation slice reaches its budget
- **WHEN** a validation slice reaches 45 seconds, 5,000 sample rows, 500 rows per page, 768 MiB process RSS, or the remaining safety reserve
- **THEN** it starts no new page, atomically persists the last complete page and checkpoint by 55 seconds, exits by 60 seconds, and exposes no partial aggregate as final evidence

#### Scenario: Validation resumes
- **WHEN** the same manifest and execution contract resume from a durable `(manifest_hash, source_date, horizon, asset_key)` checkpoint
- **THEN** already committed samples are not duplicated, the next stable page is processed, and final aggregation is deterministic across page sizes and interruptions

#### Scenario: Validation contract changes during resume
- **WHEN** manifest, price basis, execution cost, horizon set, candidate registry, or schema hash differs from the checkpoint
- **THEN** the incompatible continuation is rejected and a new validation identity is required

#### Scenario: Validation page is persisted
- **WHEN** one bounded sample page completes
- **THEN** sample and checkpoint persistence uses no more than eight SQL statements, commits atomically, and releases page-scoped ORM objects before continuing
