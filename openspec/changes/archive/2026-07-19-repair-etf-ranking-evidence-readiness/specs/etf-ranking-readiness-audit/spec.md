## ADDED Requirements

### Requirement: Ranking readiness is bound to an attested runtime environment
The system SHALL attach a database-persisted random instance UUID and environment declaration, application/deploy artifact identity, schema head, non-secret key id, and signed attestation to every production ranking-readiness observation. Production signing material SHALL be provisioned outside the repository and SHALL never appear in the response.

This attestation SHALL prevent accidental local/test/production attribution and configuration drift. It MUST NOT claim resistance to an authorized operator deliberately cloning the database identity, deploy configuration, and signing material together; adversarial clone resistance requires an external verifier challenge or non-exportable platform key and is outside this change.

#### Scenario: Production readiness is recorded
- **WHEN** an operator reads ranking readiness from the configured production instance
- **THEN** the stored instance UUID/environment, configured expected production identity, deploy artifact, and attestation signature all agree before the observation can be labelled production

#### Scenario: Evidence environment does not match
- **WHEN** the persisted identity, configured expected identity, deploy identity, key id, or signature is unset, invalid, or mismatched
- **THEN** the system MUST NOT label that observation as production rollout evidence

### Requirement: Ranking readiness reports independent evidence dimensions
The system SHALL report formal snapshot compatibility, registered ranking-source manifests, current-session adjusted-price coverage, 61-session warm-up depth, contract-derived replay depth, optional 180-session operational depth, provider/job/checkpoint health, component and score coverage, future-outcome completion, and validation sample sufficiency as separate dimensions.

#### Scenario: Current prices are complete but warm-up is incomplete
- **WHEN** same-session adjusted-price coverage passes but fewer than the required ETFs have sufficient eligible adjusted history
- **THEN** readiness reports publication freshness and warm-up readiness separately and identifies the depth shortfall

#### Scenario: Validation calculation exists without registered source
- **WHEN** a validation result has metrics but lacks a valid production or replay source identity
- **THEN** readiness reports `unregistered_ranking_source` and MUST NOT count the result as same-contract evidence

### Requirement: Ranking readiness inspection is bounded and read-only
The system SHALL assemble readiness through pre-aggregated health/checkpoint rows and bounded indexed reads and MUST NOT start synchronization, publication, replay, allocation, alert, notification, or SMTP work. One request SHALL use no more than 25 SQL statements, return or inspect no more than 5,000 rows per statement, apply a two-second statement timeout, avoid full historical-price scans, and finish within ten seconds.

#### Scenario: Admin reads readiness
- **WHEN** an authorized admin requests the readiness report
- **THEN** the request performs no decision-domain writes and returns within the configured bounded query budget

#### Scenario: Anonymous user requests readiness
- **WHEN** an unauthenticated or unauthorized user requests protected readiness details
- **THEN** the system rejects the request without disclosing environment or database fingerprints

### Requirement: Unavailable readiness states have stable reasons
The system SHALL return a stable blocker key and readable explanation instead of a context-free `N/A` whenever a readiness dimension is unavailable.

#### Scenario: Formal snapshots have not accumulated
- **WHEN** fewer compatible published v3 source dates exist than the formal validation plan requires
- **THEN** the system reports `insufficient_production_snapshot_dates` with required, available, and shortfall counts

#### Scenario: Historical production inputs never existed
- **WHEN** exact historical v3 quote or component evidence cannot be reconstructed
- **THEN** the system reports `historical_production_evidence_unreconstructable` and points to the separate research-replay state without merging it
