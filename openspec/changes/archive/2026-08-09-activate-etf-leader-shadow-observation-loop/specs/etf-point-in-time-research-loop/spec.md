## ADDED Requirements

### Requirement: Production PIT capture feeds a due-aware leader observation lane
The point-in-time research workflow SHALL offer each complete immutable PIT source to the leader observation lane exactly once per compatible code and hypothesis version, after the source capture commits and without making a live provider request.

#### Scenario: New complete source is due
- **WHEN** PIT capture has committed a complete source that has no compatible completed leader observation
- **THEN** one single-worker leader continuation advances at most one bounded page and records a resumable identity

#### Scenario: Source is already complete
- **WHEN** the compatible observation and all currently mature outcome work for a PIT source are complete
- **THEN** the scheduler skips it without creating a second observation, provider client, production ranking, or notification

#### Scenario: No complete source exists
- **WHEN** publication readiness is blocked or degraded, PIT scheduling is disabled, or cutoff provenance is incomplete
- **THEN** the leader lane returns a stable unavailable reason and performs no observation work

### Requirement: Leader observation cadence cannot starve PIT capture
The workflow SHALL keep PIT source capture and leader observation as separately checkpointed due-aware work, with each trigger bounded below 55 seconds and no concurrent worker.

#### Scenario: Leader backlog exists
- **WHEN** multiple captured PIT sources or pending outcomes require work
- **THEN** the workflow advances the oldest compatible due unit by one page and leaves a monotonic backlog cursor for later triggers
