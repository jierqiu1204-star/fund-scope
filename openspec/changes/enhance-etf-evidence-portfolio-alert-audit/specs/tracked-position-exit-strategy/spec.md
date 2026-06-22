## ADDED Requirements

### Requirement: Exit decision audit context
Tracked-position exit evaluations SHALL expose the context behind each holding处理状态.

#### Scenario: Exit signal context
- **WHEN** a tracked position triggers hard stop, trailing take profit, trend weakening, take profit watch, or exit watch
- **THEN** the result includes the threshold, current profit, high-water profit, price source, freshness, and reason text

#### Scenario: No action context
- **WHEN** a tracked position does not trigger an exit signal
- **THEN** the result includes the main reason no action was taken when that reason is available

### Requirement: Display-only data cannot trigger exit email
Tracked-position exit evaluations SHALL prevent display-only, stale, estimated, or unavailable data from triggering email reminders.

#### Scenario: Stale quote exit blocked
- **WHEN** the latest quote is stale or display-only
- **THEN** the system may show estimated context on the page but MUST NOT send an exit email
