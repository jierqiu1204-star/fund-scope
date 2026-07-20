## ADDED Requirements

### Requirement: ETF Ranking Snapshots Have Immutable Identity
The system SHALL persist every ETF ranking run with an explicit scope kind, scope hash, universe snapshot hash, input snapshot hash, score version, rule version, ranking contract hash, score field, data cutoff, trade date, price basis, expected-universe count, decision-data-eligible count and coverage, score-eligible count and coverage, publication state, and idempotency key, and SHALL NOT infer or mutate those identity fields after publication.

#### Scenario: Full ranking snapshot is published
- **WHEN** a complete ETF ranking finishes all publication gates
- **THEN** its immutable identity fields and both coverage dimensions are non-null, its idempotency key is unique, and the persisted v3 score/item order can be reproduced from that snapshot

#### Scenario: Legacy run lacks identity
- **WHEN** an older signal run lacks any mandatory version, scope, or hash field
- **THEN** the system classifies it as legacy and does not synthesize a current value

#### Scenario: Draft snapshot content is sealed
- **WHEN** the materializer finishes persisting a v3 draft and all ranked items
- **THEN** it records canonical hashes for the run content, summary, ordered item contents, each asset's final bucket evidence, and the available v3 input evidence before publication is attempted

#### Scenario: Adjusted-history digest is not emitted by the scorer
- **WHEN** the computed asset does not expose a digest of the exact adjusted-price rows used by its metric windows
- **THEN** the materializer seals the available `v3_input_values`, records the row-provenance digest as unavailable, and MUST NOT fabricate or infer a historical provenance digest

#### Scenario: Adjusted-history digest is emitted by the scorer
- **WHEN** the bounded scorer consumes cutoff-eligible adjusted-price rows
- **THEN** it emits a lowercase SHA-256 digest over the ordered consumed values and their provider/version/source-time provenance, and the materializer rejects malformed digest strings as unavailable

#### Scenario: Sealed draft is changed before publication
- **WHEN** any sealed run identity, summary field, item score, rank, metric, breakdown, rationale, or risk content changes after materialization
- **THEN** the publisher recomputes the draft seal inside the publication transaction and rejects the snapshot before changing its publication state

### Requirement: Canonical Ranking Publication Requires A Complete Data Barrier
The system SHALL publish a canonical ETF ranking snapshot only after the expected point-in-time universe, same-trading-date decision data, configured decision-data coverage threshold, configured score-eligible coverage threshold, finite score inputs, and full-scope item persistence have completed successfully, and SHALL validate that every score-eligible code belongs to the decision-data-eligible cohort.

#### Scenario: Daily synchronization is partial
- **WHEN** any required batch is skipped, deferred, still running, or leaves coverage below the configured threshold
- **THEN** the workflow records a partial or failed result and does not publish a canonical snapshot

#### Scenario: Adjusted prices pass but complete scores do not
- **WHEN** decision-eligible total-return-adjusted price coverage passes but required v3 component or score-eligible coverage is below the configured threshold
- **THEN** the workflow records price coverage and score coverage separately, reports component exclusions, and does not publish a canonical snapshot

#### Scenario: Score cohort contains a price-ineligible code
- **WHEN** a persisted score-eligible item is absent from the same-date decision-data-eligible cohort
- **THEN** the publication gate fails even if both recorded coverage ratios otherwise meet the threshold

#### Scenario: Included assets use different trade dates
- **WHEN** otherwise eligible ETF inputs do not share one exchange trade date
- **THEN** the publication gate fails and reports the affected assets instead of mixing them in one cross-sectional rank

#### Scenario: Snapshot is retried with the same inputs
- **WHEN** a workflow retries the same scope, input snapshot, contract, and trade date
- **THEN** the idempotency key prevents a duplicate canonical publication

#### Scenario: Post-close adjusted data arrives after the market cutoff
- **WHEN** an adjusted daily row is learned after 15:00 but no later than the declared same-session source-availability cutoff
- **THEN** the row may enter daily metrics while intraday quote selection remains bounded by the distinct 15:00 decision cutoff

### Requirement: Canonical Ranking Selection Is Exact And Fresh
The system SHALL select a canonical ETF ranking only when it is published, `scope_kind=full`, compatible with the consumer's score version and ranking contract, and fresh under the exchange trading calendar.

#### Scenario: Later partial run exists
- **WHEN** a published full run is followed by a theme or code-scoped run
- **THEN** default list, detail, live-base, portfolio, and current-evidence consumers continue to select the compatible full run

#### Scenario: No compatible fresh snapshot exists
- **WHEN** all full snapshots are missing, stale, legacy, or contract-incompatible
- **THEN** the consumer receives an explicit waiting, stale, or unavailable state and does not fall back to an arbitrary success

### Requirement: Ranking Identity Is Assigned Before Presentation Filters
The system SHALL compute and persist global rank across the complete declared ranking scope before applying search, theme, observation-label, entry-label, tracking, pagination, or page-size filters.

#### Scenario: Same ETF is requested through a filter
- **WHEN** an ETF appears in both an unfiltered response and a filtered response from the same snapshot and sort contract
- **THEN** its `global_rank` is identical and its `filtered_position` describes only its position in the filtered result

#### Scenario: Asset detail is requested by code
- **WHEN** a client requests one ETF detail from a published snapshot
- **THEN** the detail returns the stored global rank and does not re-enumerate the ETF as rank one

### Requirement: Rank Changes Compare Compatible Scopes Only
The system SHALL calculate rank change only between ranks produced by the same score version, ranking contract, and declared rank scope, and SHALL return no rank change when those identities differ.

#### Scenario: User narrows a live list
- **WHEN** search, theme, label, tracking, or pagination filters narrow the visible live result
- **THEN** the filters change `filtered_position` only and do not create a rank change

#### Scenario: Live watch scope changes
- **WHEN** the live watch-scope hash differs between the compared observations
- **THEN** the API returns a null rank change with a readable scope-change reason

### Requirement: Downstream Consumers Preserve Snapshot Identity
The system SHALL propagate the source snapshot id and ranking contract identity to live ranking, observation allocation, selected-asset detail, validation, evidence summaries, and workbench responses.

#### Scenario: Downstream result is produced
- **WHEN** a downstream consumer derives output from a canonical snapshot
- **THEN** the output records the source snapshot id, score version, contract hash, trade date, and freshness status

#### Scenario: Consumer receives an incompatible snapshot
- **WHEN** a downstream consumer requires a different score version, price basis, or contract hash
- **THEN** it returns unavailable or version-mismatch and does not reinterpret the input as compatible

### Requirement: One Request Pins One Source Snapshot
Each list, detail, live, allocation, or validation request SHALL resolve its source snapshot once and reuse that immutable identity throughout the request.

#### Scenario: A newer run publishes during a live request
- **WHEN** a compatible snapshot is published after the request has resolved its source
- **THEN** all base items, watchlist sources, ranks, and response metadata in that response still reference the originally pinned snapshot

#### Scenario: ETF consumer resolves a source
- **WHEN** an ETF-only consumer requests a canonical snapshot
- **THEN** fund runs and legacy mixed-asset runs are ineligible regardless of creation time or score value

#### Scenario: Client asks the legacy generator to create an ETF run
- **WHEN** a client invokes the compatibility signal-generation endpoint with `asset_type=etf`
- **THEN** the endpoint rejects the request or refreshes canonical state without creating a legacy ETF run
