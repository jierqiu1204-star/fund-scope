## ADDED Requirements

### Requirement: Cross-route ETF research declares input readiness before outcomes
Cross-route ETF research SHALL freeze its requested interval, route identities, universe eligibility, source mode, decision cutoffs, calendar and cost policy before evaluating outcomes. Its preflight SHALL report per-date counts and reasons for authoritative membership, adjustment history, clone mapping, V2 observations and transitions, and artifact availability. The shared selection pool SHALL contain at least ten non-clone ETFs with 127 complete eligible session closes before any positive-return or strategy-signal filter; the history-coverage denominator SHALL be the cutoff-visible eligible non-clone universe before checking prices, and the numerator SHALL be the number passing that history check. Missing data SHALL NOT be interpreted as a valid zero-signal decision or profitable cash allocation. This minimum diagnostic pool SHALL NOT replace stricter existing provenance or promotion coverage gates.

#### Scenario: A required source is missing
- **WHEN** a required universe, historical window, compatible manifest or research artifact is absent
- **THEN** the affected route reports the exact unavailable layer, available and required counts, and earliest evaluable condition without changing its trading rules or reading results to choose another interval

#### Scenario: A complete screen selects no assets
- **WHEN** all required source facts and screening stages are complete but no asset passes the frozen rules
- **THEN** the route records a valid empty selection and can hold cash, distinguishable from missing-data unavailability

#### Scenario: No asset has the required history
- **WHEN** zero assets, or fewer than ten assets, have complete eligible 127-close histories in the declared non-clone universe
- **THEN** the comparison decision is unavailable with its actual coverage numerator and denominator, and cannot emit a valid all-cash account decision

### Requirement: Existing research artifacts can be prepared without rewriting history
The research workflow SHALL offer bounded, idempotent preparation or continuation of compatible research artifacts using existing stores and checkpoints. Creating a configured empty research store SHALL NOT count as successful input preparation. Imported or replayed records MUST retain original decision, receipt and availability times and immutable source identities; incompatible records SHALL remain separate.

#### Scenario: The configured shared store is absent
- **WHEN** preflight discovers an absent store and compatible source records are available
- **THEN** preparation creates or connects only the declared research store and advances bounded compatible work; readiness becomes complete only after the required content is present and validated

#### Scenario: Older per-date artifacts exist
- **WHEN** older artifacts are considered for continuation
- **THEN** only schema- and identity-compatible records can be reused with their original timestamps, while incompatible or incomplete inputs produce explicit exclusions and are not relabeled as current evidence

#### Scenario: Historical membership arrived later
- **WHEN** the only authoritative membership or decision price was first available after the historical decision cutoff
- **THEN** strict PIT research remains unavailable for that decision; separately labeled current-vintage historical diagnostics cannot receive PIT or promotion credit

### Requirement: Cross-route preparation preserves bounded research isolation
Cross-route preparation and replay SHALL retain the existing single-worker lease, at-most-55-second continuation, resource checks and research-only write boundaries. It SHALL NOT enable production scheduling, provider polling or unrestricted historical backfills as a side effect of requesting a comparison.

#### Scenario: A continuation is interrupted
- **WHEN** preparation stops at a time or resource bound and resumes with identical immutable inputs
- **THEN** it continues from committed work without duplicate facts or changed original timestamps and produces the same final content hashes

#### Scenario: Research preparation completes
- **WHEN** compatible inputs and artifacts become available
- **THEN** production scores, ranking registry, allocations, positions, risk alerts and notification state retain their prior behavior and identities
