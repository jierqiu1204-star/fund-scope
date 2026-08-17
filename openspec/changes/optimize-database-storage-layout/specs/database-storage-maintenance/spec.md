## ADDED Requirements

### Requirement: Database statistics maintenance is bounded and resumable
The system SHALL maintain PostgreSQL planner statistics in small serialized slices with a fixed table limit, per-table statement timeout, lock timeout, and a stable continuation order.

#### Scenario: A bounded statistics slice completes
- **WHEN** the maintenance job finds eligible tables with missing or stale statistics
- **THEN** it analyzes at most the configured table limit, reports every attempted table and outcome, and returns the next eligible table without starting concurrent work

#### Scenario: One table exceeds its budget
- **WHEN** a table cannot be analyzed before the statement or lock timeout
- **THEN** that table is reported as timed out, the transaction is recovered, and no unbounded database-wide analyze is started

#### Scenario: The database is not PostgreSQL
- **WHEN** the maintenance function runs against a non-PostgreSQL test or development database
- **THEN** it returns a stable unavailable reason without issuing PostgreSQL catalog or maintenance SQL

### Requirement: Intraday index maintenance preserves identity and query semantics
The system SHALL remove only the exact non-unique duplicate of the unique `(etf_code, quote_time)` index and SHALL retain the unique identity constraint and latest-per-code query access path.

#### Scenario: Storage migration is upgraded
- **WHEN** the database migration runs on a schema containing the redundant non-unique index
- **THEN** it removes that index without dropping the unique constraint or `(etf_code, quote_time, id)` index

#### Scenario: Migration is rolled back
- **WHEN** the storage migration is downgraded
- **THEN** it recreates the removed non-unique index and leaves quote rows unchanged

### Requirement: Leader checkpoint persistence is incremental
The system SHALL persist leader-tactics checkpoint progress as bounded item rows and SHALL not serialize or rewrite the complete cumulative completed/failed code sets during normal version-2 saves.

#### Scenario: A legacy checkpoint is migrated
- **WHEN** a version-1 checkpoint contains valid completed hashes or failed-code entries
- **THEN** the migration inserts equivalent item rows idempotently, verifies the represented count, clears the cumulative JSON only after verification, and marks the checkpoint as version 2

#### Scenario: A legacy checkpoint is malformed
- **WHEN** cumulative checkpoint JSON cannot be parsed or represented without ambiguity
- **THEN** migration fails closed for that checkpoint and does not discard its legacy JSON

#### Scenario: An incremental checkpoint is updated
- **WHEN** a version-2 collection slice completes or fails a bounded set of assets
- **THEN** only changed item rows and bounded checkpoint metadata are written

### Requirement: Heavy storage reclamation remains explicit
The system SHALL report when table rewrites or blocking reclamation such as `VACUUM FULL` may reclaim space, but SHALL NOT run those operations automatically from migrations, startup, or scheduled maintenance.

#### Scenario: Reclaimable checkpoint bloat remains after migration
- **WHEN** legacy checkpoint JSON has been cleared but physical TOAST space is still allocated
- **THEN** maintenance evidence reports the explicit reclaim command and required maintenance window without executing it

#### Scenario: Quote partitioning would require a second table copy
- **WHEN** free disk or migration evidence cannot accommodate a safe online copy
- **THEN** partitioning remains deferred and the existing table continues operating with bounded retention
