## ADDED Requirements

### Requirement: ETF production identity ingestion supplies explicit tracked underlyings
The production data workflow SHALL ingest explicitly sourced ETF tracked-underlying identities through bounded resumable work and SHALL retain source evidence and actual observation time. It MUST distinguish an unresolved identity from a provider failure and MUST NOT infer the tracked underlying from a fund name.

#### Scenario: Two ETFs track the same explicitly identified index
- **WHEN** the provider explicitly identifies the same underlying for two ETFs
- **THEN** their persisted identities support the same canonical clone group and repeated ingestion does not duplicate unchanged facts

#### Scenario: Identity is received after a historical decision
- **WHEN** a tracked-underlying fact is first received after a historical decision cutoff
- **THEN** the historical comparison remains unavailable for that fact and later eligible decisions can use it

### Requirement: Research input windows retain coherent source evidence
Research input loading SHALL preserve each stage's declared history length and use a complete decision-eligible price sequence with coherent provider and adjustment version visible at the cutoff. It MUST NOT join incompatible price bases, ignore a known invalidating revision, or lower history coverage thresholds to produce a result.

#### Scenario: Stage continuation loads inputs
- **WHEN** the existing 61-session ranking stage resumes a bounded page
- **THEN** it requests its declared window successfully while the 120-session leader and 127-session comparison windows remain independently enforced

#### Scenario: No complete coherent history exists
- **WHEN** available rows cannot form a complete trusted history at the cutoff
- **THEN** the asset remains excluded with an actionable reason and bounded ingestion can continue without inventing prices

### Requirement: Research task readiness reflects durable data output
Affected research workflows SHALL report business waiting, failure, partial progress and completion consistently with their durable outputs. Operational verification MUST check identity coverage, required history coverage, latest complete source and ETF-specific research output separately from process health.

#### Scenario: A scheduled slice cannot advance
- **WHEN** the slice is blocked by missing data or an incomplete calendar
- **THEN** its recorded business state and reason reveal the blocker rather than claiming research completion

#### Scenario: Restart or repeated trigger resumes ingestion
- **WHEN** a bounded task is interrupted and resumed or the same source is received again
- **THEN** durable progress is retained and completed facts are not duplicated or backdated

#### Scenario: Projection dates span multiple providers
- **WHEN** legacy price projection rows span multiple accepted providers but no single provider has the complete required window
- **THEN** readiness reports the same incomplete history as the cutoff-aware research reader and does not pass a gate using the union

#### Scenario: A maintained library supplies ETF history
- **WHEN** the existing AKShare library supplies raw and adjusted ETF history
- **THEN** ingestion retains the real upstream provider identity, aligned raw prices and adjusted closes, current receipt time and existing integrity checks, with a bounded cancellable invocation

#### Scenario: First full-universe history collection
- **WHEN** the operator invokes the existing input-repair command without a sample code list
- **THEN** a bounded slice resumes the durable universe cursor, reports remaining work truthfully, and leaves later daily incremental collection available
