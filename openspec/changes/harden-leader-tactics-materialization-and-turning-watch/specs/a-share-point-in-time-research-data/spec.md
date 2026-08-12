## MODIFIED Requirements

### Requirement: Theme membership preserves both effective and receipt time
The system SHALL store each A-share fine-grained theme or sector membership with taxonomy version, effective-from and effective-to times, receipt time, source, confidence state, and hierarchy level, and SHALL NOT infer historical membership from a current theme constituent list. When multiple cutoff-visible facts exist, a versioned resolver SHALL prefer a compatible factual fine-grained theme such as `稀土` or `稀土永磁`; it SHALL use a broad level-one industry only as an explicit fallback and SHALL expose the selected fact and fallback reason.

#### Scenario: Theme membership is decision eligible
- **WHEN** the membership was effective and factually received before the cutoff under a compatible taxonomy
- **THEN** the security may participate in that theme's peer statistics with the membership provenance attached

#### Scenario: Fine-grained membership is decision eligible
- **WHEN** a compatible fine-grained membership was effective and factually received before the cutoff
- **THEN** the security participates in that fine-grained peer group and the selected taxonomy, source, hierarchy level, and fact hash are attached

#### Scenario: Only broad industry membership is visible
- **WHEN** no compatible fine-grained theme fact is visible by the cutoff but a factual level-one industry fact is visible
- **THEN** the security may participate in the broad peer group with `broad_industry_fallback` recorded and MUST NOT be labeled as having a fine-grained theme

#### Scenario: Only current theme membership exists
- **WHEN** no historical membership fact was received by the session cutoff
- **THEN** the security is excluded from theme-relative gates with `missing_pit_theme_membership`

## ADDED Requirements

### Requirement: A-share feature materialization is two-stage and resumable
The system SHALL derive and persist compact per-asset PIT feature facts in deterministic pages before computing cross-sectional ranks and final candidate observations. Both stages SHALL share one immutable run identity, use one database lease, enforce a hard per-invocation budget no greater than 55 seconds, and resume from durable idempotent rows without retaining the full market history in memory.

#### Scenario: Feature stage reaches its budget
- **WHEN** a feature page completes and the remaining time or memory headroom is below the declared continuation reserve
- **THEN** complete feature facts are committed, the missing-asset set remains derivable from the frozen universe, the lease is released, and no cross-sectional stage starts

#### Scenario: Cross-sectional stage resumes
- **WHEN** every expected asset has a compatible terminal feature fact and a later invocation has sufficient resource headroom
- **THEN** the system computes global theme ranks from compact facts, finalizes one bounded PIT peer group at a time, and publishes exactly one compatible materialized manifest

#### Scenario: A page is retried
- **WHEN** a timeout, restart, or lease expiry causes the same page to run again
- **THEN** feature facts remain unique by run and asset and the completed output hashes match an uninterrupted run

### Requirement: Resource recovery triggers bounded continuation
The scheduler SHALL treat insufficient materialization memory as a resumable waiting state and SHALL retry at most once per configured scheduler occurrence after rechecking current memory and lease state; it SHALL NOT rebuild completed pages, run concurrent materializers, or bypass the configured minimum headroom.

#### Scenario: Memory recovers after a waiting run
- **WHEN** a later scheduler occurrence observes sufficient headroom and a compatible incomplete checkpoint
- **THEN** it resumes the next incomplete stage within the normal time budget

#### Scenario: Memory remains insufficient
- **WHEN** available memory remains below the declared threshold
- **THEN** the system records one bounded waiting result and performs no provider work or partial publication
