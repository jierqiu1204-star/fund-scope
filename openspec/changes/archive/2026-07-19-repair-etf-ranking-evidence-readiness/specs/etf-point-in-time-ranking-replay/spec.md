## ADDED Requirements

### Requirement: Point-in-time ETF membership is an externally evidenced fact
Each replay membership fact SHALL record ETF identity, effective-from and effective-to exchange dates, included/excluded state, external receipt or source identifier, provider and provider version, source-observed time, ingestion time, evidence/raw-payload hash, and immutable fact hash.

#### Scenario: Membership fact is eligible at cutoff
- **WHEN** an external membership receipt was observable no later than the replay availability cutoff and its effective interval contains the signal date
- **THEN** the fact may contribute to the point-in-time universe with its receipt, provider/version, cutoff, and hashes preserved

#### Scenario: Membership is observed later
- **WHEN** a fact was first observable after the replay cutoff even if its effective date was earlier
- **THEN** it is rejected as `membership_observed_after_cutoff` and MUST NOT be backdated into that replay universe

#### Scenario: ETF later delists
- **WHEN** an ETF belonged to the historical interval but is absent from the current operational universe
- **THEN** the historical fact remains eligible and MUST NOT be removed by current-survivor filtering

#### Scenario: Current membership has no historical receipt
- **WHEN** only the current operational membership is known for a historical date
- **THEN** the historical membership is unavailable and MUST NOT be copied, inferred, or reconstructed from the survivor universe

#### Scenario: Effective intervals conflict
- **WHEN** overlapping facts disagree for one ETF and exchange date or their external identities cannot be reconciled deterministically
- **THEN** the affected ETF/date is excluded as `membership_fact_conflict`, both receipts remain auditable, and no preferred state is guessed
