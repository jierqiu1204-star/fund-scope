## ADDED Requirements

### Requirement: Position and exposure versions isolate alert and action state
The system SHALL assign every active tracked-position holding period a stable `position_episode_id`, SHALL version each immutable exposure baseline inside that period, and SHALL prevent alert or action state from an old position/exposure version from carrying into a later one.

#### Scenario: Reduction remains in one exposure version
- **WHEN** an active tracked position remains above zero and its confirmed quantity decreases without a later net add
- **THEN** the system retains the same position episode and exposure version and evaluates absolute targets against the immutable baseline for that exposure version

#### Scenario: Net add creates a new exposure version
- **WHEN** the owner increases normalized position quantity while the position remains active
- **THEN** the system increments `exposure_version`, freezes a new baseline, resets rule state, and supersedes unfinished actions from the prior exposure version

#### Scenario: Corporate action adjusts quantity
- **WHEN** a split, merge, or equivalent corporate action changes raw quantity without a user net add
- **THEN** the system records the adjustment factor, preserves normalized exposure/version, and does not re-arm actions solely from that quantity change

#### Scenario: Re-entry creates a new episode
- **WHEN** a tracked position has been fully closed and a positive holding is later created again
- **THEN** the system creates a new `position_episode_id` and does not reuse action eligibility, alert state, or high-water state from the closed episode

#### Scenario: Legacy action history is unknown
- **WHEN** an active position is migrated from records that do not distinguish recommendation from execution
- **THEN** the system marks execution provenance as `legacy_unverified`, keeps it outside the action-status enum, and MUST NOT infer an executed action or a new re-entry cooldown from it

### Requirement: Every position mutation preserves lifecycle invariants
Every API, import, recalculation, close/reopen, and action-execution path that can change tracked-position quantity, amount, or status SHALL use one owner-scoped exposure mutation contract with state-version CAS, baseline/version handling, action supersession, and audit; direct field writes that bypass the contract are forbidden.

#### Scenario: Existing edit adds exposure
- **WHEN** the owner PATCH/import path increases normalized confirmed exposure
- **THEN** the shared mutation contract increments exposure version, freezes the new baseline, supersedes old-exposure actions, increments state version, and records one audit event

#### Scenario: Existing path closes and reopens a position
- **WHEN** a close path reaches zero and a later authorized path creates positive exposure
- **THEN** the shared mutation contract closes the old position episode and creates a new position episode rather than reusing old alert/action state

#### Scenario: System fills a previously missing estimate
- **WHEN** a recalculation first resolves an unknown estimated quantity without an owner trade
- **THEN** the shared contract initializes or audibly corrects baseline provenance without treating the calculation as a user net add or silently changing an already-actioned target

#### Scenario: Mutation uses a stale version
- **WHEN** any position write path provides or observes an obsolete `exit_state_version`
- **THEN** the system rejects/retries through the shared contract and MUST NOT commit a direct quantity/status update

### Requirement: Alert lifecycle is driven by valid state transitions
For each position episode, exposure version, policy version, and rule, the system SHALL implement the complete deterministic transitions among `normal`, `pending`, `firing`, `recovering`, and `resolved`, and SHALL create an alert episode when a rule first leaves normal/resolved rather than once per poll or trading day.

#### Scenario: Hard stop fires immediately
- **WHEN** decision-eligible data breaches the hard-stop rule for an active position episode
- **THEN** the rule may transition directly from `normal` to `firing` and records the transition with its input snapshot hash

#### Scenario: Confirmed rule enters firing
- **WHEN** a non-hard-stop rule satisfies its versioned confirmation and hysteresis conditions
- **THEN** the rule transitions from `pending` to `firing` exactly once for that alert episode

#### Scenario: Pending condition disappears
- **WHEN** a pending rule no longer satisfies its trigger before confirmation
- **THEN** the rule returns to `normal`, clears its consecutive-confirmation count, and produces no action

#### Scenario: Persistent condition remains in one episode
- **WHEN** a rule remains true on later evaluations without first reaching `resolved`
- **THEN** the system keeps the same alert episode and MUST NOT treat each trading day or poll as a new activation

#### Scenario: Recovering rule fires again
- **WHEN** a recovering rule satisfies its trigger again before the recovery guard completes
- **THEN** it returns to `firing` in the same alert episode and MUST NOT create a duplicate target action

#### Scenario: Recovery creates no reverse trade
- **WHEN** a firing rule satisfies its recovery condition and later reaches `resolved`
- **THEN** the system records the recovery/resolution transitions and MUST NOT create a buy or re-entry action solely because the alert resolved

#### Scenario: Invalid data freezes state
- **WHEN** a rule evaluation has `data_waiting`, `no_data`, or `error`
- **THEN** its business state, confirmation count, recovery count, alert episode, and action state remain frozen until a decision-eligible evaluation resumes

#### Scenario: Policy version changes
- **WHEN** a new alert/action policy version becomes active for a position exposure
- **THEN** the system supersedes unfinished old-policy actions, closes old-policy state for new action generation, and initializes the new policy without treating unchanged evidence as multiple independent actions

### Requirement: Data failure cannot become a trading action
The system SHALL represent missing, stale, non-finite, fallback-only, provider-failed, or otherwise decision-ineligible input as one mutually exclusive `data_waiting`, `no_data`, or `error` outcome with a stable reason code and MUST NOT translate that outcome into an exit rule.

#### Scenario: Decision price is unavailable
- **WHEN** an active position lacks a decision-eligible adjusted price or required evidence at the evaluation cutoff
- **THEN** the system records a data outcome, preserves the last business alert state, and creates no reduce or exit action

#### Scenario: Ranking context is missing
- **WHEN** an `exit_watch` evaluation lacks a same-contract ranking or research snapshot
- **THEN** the system marks `exit_watch` as not evaluable and MUST NOT infer an exit from the missing item

#### Scenario: Provider recovers
- **WHEN** a later evaluation again has eligible data after a data outcome
- **THEN** the system resumes normal state-machine evaluation without fabricating transitions for the unavailable interval

#### Scenario: Existing hard stop cannot be revalidated
- **WHEN** a hard-stop episode is already firing but the current repeat slot lacks decision-eligible fresh data
- **THEN** the system keeps the historical action state, suppresses any action-oriented repeat that quotes the old price, and exposes only that current risk cannot be revalidated

### Requirement: Position actions use absolute idempotent targets
Every actionable risk decision SHALL express an absolute `target_remaining_fraction` between 0 and 1 against an immutable exposure baseline, SHALL carry a server-generated idempotency key containing position episode, exposure version, policy version, action cycle, and target stage, and MUST NOT express the action as a multiplier of the position remaining at evaluation time.

#### Scenario: Repeated half-position signal is idempotent
- **WHEN** the same firing episode repeatedly recommends `target_remaining_fraction=0.5`
- **THEN** the system retains one actionable decision targeting 50% of the current exposure-version immutable baseline and MUST NOT produce targets of 25%, 12.5%, or any other compounded fraction

#### Scenario: Different rules request the same target
- **WHEN** trailing take-profit and confirmed trend weakening request the same 50% target in one open action cycle
- **THEN** the system keeps one current 50% action and records both rules as reasons instead of creating one action per rule

#### Scenario: Concurrent evaluations produce one action
- **WHEN** two workers evaluate the same position episode, exposure version, policy, action cycle, and target stage concurrently
- **THEN** the database uniqueness constraint accepts one action decision and both workers resolve to the same persisted action id

#### Scenario: Concurrent targets choose the strictest current action
- **WHEN** concurrent workers propose 50% and 0% targets for the same current action slot
- **THEN** transaction/CAS serialization leaves the 0% action as the only current action and marks any persisted weaker action `superseded`

#### Scenario: Stronger rule escalates the target
- **WHEN** an existing action targets 50% and a later valid higher-priority rule in the same position episode targets 0%
- **THEN** the system creates one new target stage at 0%, atomically supersedes the 50% recommendation, and links all rule reasons without replaying the prior 50% action

#### Scenario: Same-time rules are aggregated
- **WHEN** multiple valid rules produce action candidates in one evaluation
- **THEN** the workflow persists one primary action at the most conservative absolute target and records all contributing rules and reasons

#### Scenario: Current quantity is already below target
- **WHEN** current normalized quantity is at or below the calculated baseline target
- **THEN** the system records `target_already_satisfied`, recommends no buy, and creates no sell action

#### Scenario: Same target reactivates after an unexecuted resolution
- **WHEN** an earlier action cycle expired without execution, its alerts resolved, and a later valid alert opens a new action cycle at the same target
- **THEN** the new cycle receives a new idempotency scope and may create one fresh proposal

### Requirement: Recommended and executed actions remain distinct
The system SHALL persist action status as `proposed`, `acknowledged`, `partially_executed`, `executed`, `expired`, `cancelled`, or `superseded`; SHALL preserve immutable execution facts; and only an owner-authorized partial/full execution fact SHALL update execution progress or start re-entry cooldown.

#### Scenario: Recommendation is persisted
- **WHEN** a valid firing transition creates an action recommendation
- **THEN** the action is stored as `proposed` and neither SMTP delivery nor alert creation changes it to `executed`

#### Scenario: User acknowledges without execution
- **WHEN** the position owner acknowledges an action but does not update the holding or provide execution facts
- **THEN** the action becomes `acknowledged` and re-entry cooldown does not start

#### Scenario: User reports partial execution
- **WHEN** the owner submits valid execution quantity, price/source, resulting shares, idempotency key, and expected position state version but the resulting position remains above the target tolerance
- **THEN** the system atomically records the execution event, updates actual shares, marks the action `partially_executed`, and does not claim that the target is complete

#### Scenario: Execution facts are numerically invalid
- **WHEN** an execution request contains non-finite values, non-positive quantity/price, negative fees/shares, a time before the decision or beyond allowed server skew, sell quantity above current shares, or resulting shares inconsistent with before shares minus fills
- **THEN** the system rejects the request and changes neither action, position, cooldown, nor audit projection

#### Scenario: User confirms execution
- **WHEN** the position owner confirms execution with an idempotent request, matching expected state version, and resulting shares at or below the target tolerance or a verified full close
- **THEN** the system atomically records the execution event, updates the position, marks the action `executed`, and uses the first real fill event as the re-entry cooldown origin

#### Scenario: Stronger action supersedes weaker proposal
- **WHEN** a current 50% proposal is upgraded to a 0% target
- **THEN** the 50% action becomes `superseded`, links `superseded_by_action_id`, and later execution requests against it are rejected

#### Scenario: Owner cancels a firing target
- **WHEN** the owner cancels a current action while its contributing alert remains firing
- **THEN** the alert and risk reminders may continue, but the same or weaker target is not recreated in that action cycle; only a stricter escalation or a later post-resolution action cycle may create another action

#### Scenario: Alert resolves before execution
- **WHEN** all action-contributing rules validly resolve while an action remains wholly or partly unfulfilled
- **THEN** the unfulfilled remainder becomes `expired`, prior fills remain auditable, and subsequent requests cannot execute the expired remainder

#### Scenario: Idempotency key payload conflicts
- **WHEN** an action transition reuses an Idempotency-Key with a different payload
- **THEN** the system returns a conflict and changes neither the action nor the position

#### Scenario: Position version is stale
- **WHEN** an action transition provides an `expected_position_state_version` that is not current
- **THEN** the system returns a conflict and requires the owner to refresh before retrying

#### Scenario: Terminal request is retried
- **WHEN** the exact same idempotent execute request is retried after the action reached its terminal result
- **THEN** the system returns the original result without adding another fill or changing shares

#### Scenario: Cross-user update is rejected
- **WHEN** a user attempts to acknowledge, execute, cancel, or expire an action owned by another user
- **THEN** the system denies the transition and leaves the action and position unchanged

### Requirement: Notification repetition is independent from action eligibility
The system SHALL use distinct idempotency keys for notification items and delivery envelopes, both independent from action idempotency, and repeated, retried, grouped, suppressed, or recovery notifications MUST NOT create or advance a position action.

#### Scenario: Unresolved hard stop repeats a reminder
- **WHEN** a hard-stop alert remains firing into a later eligible trading-day repeat slot and its action is still not executed
- **THEN** the system may create a new notification linked to the original action decision but MUST NOT create another exit action

#### Scenario: SMTP retry reuses notification identity
- **WHEN** delivery of a notification fails and is retried
- **THEN** the retry uses the same envelope identity and Message-ID, retains its item associations, and leaves linked action statuses unchanged

#### Scenario: Multiple alerts share one digest
- **WHEN** multiple ordinary alert items for one owner/session/severity are sealed into one digest
- **THEN** each item keeps its own alert-episode key while one envelope key represents the SMTP attempt

#### Scenario: SMTP acceptance commit is uncertain
- **WHEN** SMTP accepts an envelope but the process fails before local status commit
- **THEN** the retry may produce an at-least-once duplicate email but MUST reuse the envelope/Message-ID and MUST NOT duplicate any linked action

#### Scenario: Fresh data is unavailable for repeat
- **WHEN** an existing firing action reaches a later repeat slot but current data is not decision eligible
- **THEN** the system suppresses the action-oriented repeat and emits only the permitted current-data-status item

#### Scenario: Soft watch is notification-only
- **WHEN** `take_profit_watch` becomes firing
- **THEN** the system may create a web or digest notification stating `hold/仅观察` and creates no reduce, trim, or exit action decision
