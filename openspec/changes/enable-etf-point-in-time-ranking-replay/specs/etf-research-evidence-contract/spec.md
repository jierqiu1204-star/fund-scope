## ADDED Requirements

### Requirement: Ranking And Action Evidence Provenance Is Orthogonal
ETF research evidence SHALL persist ranking source kind, signal-contract compatibility, action-policy compatibility, policy mode, notification provenance, and execution provenance as independent fields and MUST NOT collapse them into one same-contract or delivery status.

#### Scenario: Current action policy runs on research ranking
- **WHEN** policy shadow uses the current action-policy contract with a `research_replay` ranking
- **THEN** action compatibility may be `same_contract` while signal compatibility is `same_replay_contract`, policy mode is `policy_shadow`, and the result is not production-v3 proof

#### Scenario: SMTP accepts a live notification
- **WHEN** the production mail server accepts a linked notification
- **THEN** notification provenance is `smtp_accepted_live`, provider delivery remains unconfirmed, and execution provenance remains `none` unless a separate fact exists

#### Scenario: Provider delivery is confirmed
- **WHEN** a provider receipt id and receipt timestamp are persisted for a live notification
- **THEN** notification provenance may become `provider_delivered_live` without implying user-confirmed execution

### Requirement: Replay Evidence Identity Is Immutable And Complete
Every research-replay evidence result SHALL retain the replay run key, score/candidate manifest, universe/input/feature hashes, data cutoff, price basis, execution/cost model, chronological split, purge, seed, and final-holdout identity used to produce it.

#### Scenario: Replay artifact is summarized
- **WHEN** an API or workbench response presents replay results
- **THEN** the response exposes the immutable identities and limitations needed to reproduce and classify the result

#### Scenario: Required identity is missing
- **WHEN** a replay or policy-shadow result lacks a mandatory hash, cutoff, split, or provenance field
- **THEN** it is legacy/unavailable and cannot be merged with complete same-replay-contract evidence

### Requirement: Notification And Execution Facts Do Not Create Each Other
Research evidence SHALL derive economic returns from simulated or user-confirmed fills and SHALL treat notification eligibility, attempts, acceptance, delivery, and repetition as separate communication facts.

#### Scenario: Policy shadow would notify
- **WHEN** a replayed policy reaches a notification-eligible transition
- **THEN** it records `shadow_eligible` inside research artifacts only and creates no production notification item, envelope, or SMTP attempt

#### Scenario: Email metrics are requested without live samples
- **WHEN** no linked live notification/action cycle and completed future window exist
- **THEN** live notification accuracy remains unavailable even if policy-shadow accuracy and simulated benefit are available
