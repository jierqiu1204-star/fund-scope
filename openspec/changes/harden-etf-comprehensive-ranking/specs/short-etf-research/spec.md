## MODIFIED Requirements

### Requirement: ETF Market Data Is Synchronizable
The system SHALL synchronize traceable raw and total-return-aware ETF daily market data for the point-in-time short-term universe, including date, open, high, low, close, adjusted research value, volume, turnover, percentage change, provider, source timestamp, price basis, adjustment version, and decision eligibility.

#### Scenario: Data sync stores daily prices
- **WHEN** the user runs ETF data sync from the web UI
- **THEN** the system stores or updates raw daily ETF rows and separately identifiable research-adjusted values without duplicating existing code/date/basis records

#### Scenario: Data source failure is reported
- **WHEN** one ETF data source request fails during synchronization
- **THEN** the task records the ETF code and readable error message while continuing with other ETFs and marks the overall business result partial when required coverage is not met

#### Scenario: Large universe sync is batched
- **WHEN** the system synchronizes a large ETF universe
- **THEN** the task processes at most 20 ETFs per bounded slice on the small-server profile, commits completed per-code progress, and records expected, attempted, succeeded, updated, failed, skipped, deferred, and decision-eligible counts

#### Scenario: Higher priority ETFs update first
- **WHEN** the daily ETF sync task runs
- **THEN** tracked, missing, stale, default-display, and high-turnover ETFs are prioritized while a persisted cursor guarantees that lower-priority ETFs are eventually attempted

#### Scenario: Same-date coverage is accumulated across slices
- **WHEN** several post-close slices target the same trade date
- **THEN** missing same-date adjusted rows are attempted first, no second slice overlaps the active one, and the combined database coverage is evaluated before materialization rather than requiring one unbounded full-universe request

#### Scenario: Research-adjusted data is unavailable
- **WHEN** a traceable total-return-aware value cannot be obtained for an ETF/date
- **THEN** raw data may remain displayable but return, drawdown, volatility, ranking, and validation calculations for that row are unavailable

### Requirement: ETF Metrics Identify Trend And Risk
The system SHALL compute deterministic ETF metrics for trend, liquidity, volatility, drawdown, short-term return, medium-term return, overextension risk, MA distance, score-bearing turnover windows, and eligible premium/discount context, and SHALL expose finite-value and coverage status for every produced field.

#### Scenario: High recent return triggers chase risk
- **WHEN** an ETF has a recent return above the configured overextension threshold
- **THEN** the ETF metric output includes a chase-risk flag and the signal conclusion cannot be stronger than high-watch observation language

#### Scenario: Low liquidity triggers liquidity risk
- **WHEN** an ETF's recent average turnover is below the configured liquidity threshold
- **THEN** the ETF metric output includes a liquidity-risk flag

#### Scenario: Required sector input is missing
- **WHEN** MA20 distance, 60-day turnover reference, or another declared producer field is absent or non-finite
- **THEN** its consumer receives an unavailable value and MUST NOT substitute zero, neutral score, or a positive breadth observation

#### Scenario: Twenty-day volatility is calculated
- **WHEN** the system publishes a metric described as twenty daily returns
- **THEN** it requires at least 21 valid adjusted closing values and reports the effective return count

### Requirement: Short ETF Signals Use Research Language
The system SHALL generate ranked short-term ETF signal items from one complete point-in-time eligible universe using the declared deterministic score contract, SHALL persist a full immutable snapshot before exposing top candidates, and SHALL frame every conclusion as research observation rather than trading instruction.

#### Scenario: Latest signals are returned
- **WHEN** the full ETF signal workflow completes its data and publication gates
- **THEN** the system persists one full-scope snapshot with ranked items, score breakdowns, risk flags, theme labels, observation-oriented conclusions, universe hash, data cutoff, and contract identity

#### Scenario: Top 20 watch candidates are identifiable
- **WHEN** a compatible published full ETF snapshot exists
- **THEN** its first 20 ranked items are available to the intraday ETF watch service without recomputing percentiles or replacing the full snapshot with a top-20 cohort

#### Scenario: Prohibited trade language is absent
- **WHEN** the API returns a short-term ETF signal item
- **THEN** it does not include buy, sell, target price, expected return, or guaranteed profit fields

#### Scenario: Theme or code research run completes later
- **WHEN** a partial research run is generated after the full snapshot
- **THEN** it remains non-canonical and cannot replace the default workbench, live base, portfolio source, or validation source

### Requirement: ETF Universe Is Refreshable From Public Sources
The system SHALL provide a web-runnable job that refreshes tradable ETF metadata, records effective-dated universe membership and exclusion reasons, and upserts current ETF metadata without erasing historical membership.

#### Scenario: Universe refresh is idempotent
- **WHEN** the ETF universe refresh job is run repeatedly with the same source facts
- **THEN** current metadata and open membership intervals are updated without duplicate ETF or interval rows

#### Scenario: Universe refresh reports counts
- **WHEN** the ETF universe refresh job completes
- **THEN** the result reports discovered, inserted, updated, activated, deactivated, excluded, default-display, and failed ETF counts

#### Scenario: Previously active ETF disappears
- **WHEN** a source no longer lists an ETF or marks it ineligible
- **THEN** the system closes its current membership interval with an effective date and reason while retaining it in historical snapshots and validation denominators

#### Scenario: Discovery fails or suspiciously shrinks
- **WHEN** the configured live provider fails or returns a materially smaller universe than the frozen active membership
- **THEN** the refresh is non-authoritative, preserves all current membership intervals, performs no seed fallback or mass deactivation, and blocks canonical publication

#### Scenario: Provider returns an equal-sized disjoint replacement
- **WHEN** a provider returns as many eligible rows as the frozen membership but retains fewer than 80 percent of the previously active codes
- **THEN** the refresh is non-authoritative and performs no inserts, updates, activations, or deactivations

#### Scenario: Primary universe provider fails but fallback is complete
- **WHEN** the bounded Eastmoney universe request fails or is incomplete and AKShare returns a complete unique ETF list
- **THEN** the refresh uses the AKShare facts as the authoritative snapshot and records the actual membership source

#### Scenario: Public universe expands beyond the seed cohort
- **WHEN** an authoritative provider returns the complete public ETF list while the database contains only a frozen seed cohort
- **THEN** the refresh bulk-loads existing metadata, activates every eligible newly discovered ETF idempotently, and does not trigger adjusted-price history synchronization in the same operation

## ADDED Requirements

### Requirement: ETF Cross-Sectional Ranking Uses Homogeneous Point-In-Time Cohorts
The system SHALL calculate ETF cross-sectional distributions from the full point-in-time eligible cohort, use homogeneous asset buckets where return and structure behavior differ, and prevent multiple clones of the same tracked underlying from dominating peer distributions.

#### Scenario: Multiple ETFs track the same index
- **WHEN** several eligible ETFs share one underlying index or equivalent exposure
- **THEN** the percentile and breadth calculation deduplicates or cluster-weights that exposure according to the versioned contract while preserving individual ETFs in the displayed ranking

#### Scenario: User requests one theme
- **WHEN** the user filters the published ranking to one theme
- **THEN** ETF scores remain those calculated against the declared full cohort and are not recomputed from the smaller theme set

### Requirement: Score-Bearing Producers And Consumers Share One Contract
Every score-bearing sector, theme, factor, liquidity, and premium consumer SHALL declare the exact producer field, units, source date, reliability, asset-bucket eligibility, and minimum valid peer count it requires.

#### Scenario: Unit fixture provides a field production omits
- **WHEN** an integration test runs the production metric builder through a score-bearing consumer
- **THEN** the same typed contract used in production is validated and the test cannot pass solely through fixture-only fields

#### Scenario: Peer field coverage is incomplete
- **WHEN** only part of a peer cohort has a valid metric
- **THEN** the component reports its own valid peer count and coverage rather than using the total cohort size

### Requirement: Auxiliary Factor Degradation Is Explicit
The system SHALL record factor profile version, available groups, missing groups, source dates, and degradation reasons and SHALL NOT compare or reweight incompatible degraded profiles as if they were the same comprehensive score.

#### Scenario: Planned factor has no producer
- **WHEN** fund-flow, fundamental, valuation, macro, or another planned factor has no point-in-time production value
- **THEN** the profile marks it unavailable and the final score manifest does not silently redistribute its weight to available price factors

#### Scenario: Quality gate rejects theme evidence
- **WHEN** stale data, low liquidity, or another quality gate makes a theme/catalyst component unavailable
- **THEN** a later factor/profile assignment cannot restore that component to decision-eligible status

#### Scenario: Theme catalyst coverage is sparse under rule v2
- **WHEN** current theme/catalyst evidence is unavailable for part or all of the universe
- **THEN** it remains explanatory-only with zero weight and cannot improve or block the five-component comprehensive score

### Requirement: Theme And Catalyst Evidence Has A Maximum Age
The system SHALL version and expire theme/catalyst snapshots and SHALL expose unavailable evidence when the latest snapshot exceeds its configured maximum age.

#### Scenario: Theme snapshot is old
- **WHEN** the most recent theme/catalyst snapshot is older than the allowed freshness window
- **THEN** it remains visible only as stale context and cannot improve a current comprehensive score

### Requirement: Full ETF Signal Generation Materializes The Declared Final Score
The full-universe ETF signal generator SHALL build the canonical ranking contract and persist only finite score-eligible `final_score_v3` items with continuous global ranks and complete typed snapshot identity before publication is attempted.

#### Scenario: Full ETF generation succeeds
- **WHEN** the full ETF generator has a complete authoritative universe, input cohort, and eligible v3 results
- **THEN** it persists `ranking_score`, `score_eligible=true`, continuous `global_rank`, score/rule versions, hashes, cutoff, price basis, both coverage dimensions, and an idempotency key while leaving publication state unpublished

#### Scenario: Required v3 input is unavailable
- **WHEN** an ETF lacks any required finite score-bearing input
- **THEN** the ETF is recorded in score exclusions and is not persisted by copying legacy `total_score` into the canonical score field

#### Scenario: Legacy signal run already exists
- **WHEN** an older run lacks canonical v3 item or identity fields
- **THEN** the generator leaves that run legacy/unpublished and MUST NOT infer missing identity from current configuration

#### Scenario: Full-universe metrics are computed on a small server
- **WHEN** the materializer scores the complete eligible ETF cohort
- **THEN** adjusted history and health are prefetched in bounded constant-count queries without concurrent use of one async session, and the produced scores remain identical to the sequential calculation
