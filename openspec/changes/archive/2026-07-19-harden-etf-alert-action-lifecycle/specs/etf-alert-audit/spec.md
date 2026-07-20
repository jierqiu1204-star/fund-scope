## MODIFIED Requirements

### Requirement: Tracked position alert audit trail
The system SHALL record an immutable-during-retention, owner-scoped decision trail that distinguishes sealed-snapshot evaluation outcomes, alert transitions, action decisions/status changes, notification item/envelope suppression, and SMTP attempts; privacy deletion or anonymization remains governed by the product retention policy.

#### Scenario: Email sent audit
- **WHEN** a tracked-position notification is sent
- **THEN** the audit records the tracked position/exposure/action/alert episodes, transition, alert type, data source, price freshness, threshold context, policy version, linked action decision, item/envelope ids, Message-ID, SMTP acceptance/result, redacted recipient, and attempt timestamp without claiming end-to-end delivery

#### Scenario: Web-only audit
- **WHEN** a tracked-position evaluation produces a soft watch, unconfirmed guard, data-waiting, or other web-only note
- **THEN** the audit records why no action and/or email was produced and labels the evaluation and notification outcomes separately

#### Scenario: Suppressed duplicate audit
- **WHEN** a tracked-position evaluation suppresses a duplicate notification or an already-satisfied absolute action target
- **THEN** the audit records the notification repeat slot or action idempotency reason without creating a misleading new email or action event

#### Scenario: Unchanged polls share one audit event
- **WHEN** repeated polls observe no state transition inside one sealed snapshot/repeat slot
- **THEN** the system stores at most one deterministic event for that slot and reports poll counts as bounded aggregate metrics rather than one audit row per poll

## ADDED Requirements

### Requirement: Alert audit distinguishes recommendation from execution
The audit SHALL preserve action lifecycle transitions as separate facts and MUST NOT infer execution from signal evaluation or notification delivery.

#### Scenario: Action is proposed
- **WHEN** an alert transition produces a position action decision
- **THEN** the audit records `proposed`, the absolute target, action idempotency key, evidence hash, policy/rule versions, and linked alert episode without an execution timestamp

#### Scenario: SMTP accepts notification
- **WHEN** SMTP accepts an email linked to a proposed action
- **THEN** the audit records only the strongest observed provenance such as `smtp_accepted` or verified delivery receipt while the action remains `proposed` or `acknowledged`

#### Scenario: Action is executed
- **WHEN** the owner confirms an execution with position/execution facts
- **THEN** the audit records the authorized state transition, execution source and timestamp, and the resulting position state without rewriting the original proposal event

#### Scenario: Legacy action is displayed
- **WHEN** an old alert has no verifiable action status
- **THEN** the audit exposes it as `legacy_unverified` and MUST NOT label it as executed

### Requirement: Alert audit exposes correlation identifiers
The alert audit API SHALL expose stable correlation identifiers needed to trace one evaluation through its episode, action, and notifications, SHALL use bounded cursor pagination and response-size limits, and SHALL return only schema-whitelisted/redacted technical context.

#### Scenario: Owner traces an alert episode
- **WHEN** the owner reads audit history for a tracked position
- **THEN** each event returns available `event_id`, schema version, position/exposure/action/alert episode ids, action decision id, notification item/envelope ids, policy version, input snapshot hash, from/to state, actor/request/causation ids, occurred/recorded times, human-readable summary, and size-bounded whitelisted structured context

#### Scenario: Repeated reminder is shown
- **WHEN** multiple notifications refer to one unresolved hard-stop action
- **THEN** the UI/API shows one action with multiple delivery events rather than multiple sell decisions

#### Scenario: Audit page contains sensitive fields
- **WHEN** audit context contains recipient, provider payload, SMTP error, credentials, or unbounded evidence text
- **THEN** the API redacts or omits sensitive/unapproved fields, truncates to the schema limit, and never exposes SMTP secrets

#### Scenario: Owner requests the next page
- **WHEN** more events exist than the configured page size
- **THEN** the API returns a stable next cursor and MUST NOT load or return the full audit history in one response
