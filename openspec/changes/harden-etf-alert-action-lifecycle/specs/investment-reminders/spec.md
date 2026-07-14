## MODIFIED Requirements

### Requirement: Reminder delivery audit
Investment reminders SHALL record envelope- and item-level status for every attempted tracked-position email and SHALL distinguish SMTP acceptance from verified end-to-end delivery.

#### Scenario: SMTP accepts an envelope
- **WHEN** a reminder envelope is successfully accepted by SMTP
- **THEN** the system records status `smtp_accepted`, redacted recipient, template/version, Message-ID, timestamp, item ids, and linked tracked-position actions without claiming delivery or execution

#### Scenario: Failed delivery audit
- **WHEN** SMTP login or send fails
- **THEN** the system records status `failed` with a redacted error message, preserves the envelope identity for retry, and does not expose SMTP secrets

### Requirement: Reminder suppression audit
Investment reminders SHALL record why an otherwise eligible notification item did not enter a new envelope, at most once per sealed snapshot or repeat slot rather than once per unchanged poll.

#### Scenario: Cooldown suppression
- **WHEN** a notification item is suppressed due to duplicate/repeat slot, user silence, digest grouping, or severity inhibition
- **THEN** the system records the item key, suppression reason, linked action id when present, and next eligible trading-session slot without changing action eligibility

## ADDED Requirements

### Requirement: Tracked-position reminders are action-neutral projections
Investment reminders SHALL consume persisted alert/action facts through an orchestration-provided notification payload and MUST NOT evaluate market rules, create a position action, update action status, or start re-entry cooldown.

#### Scenario: Proposed action is emailed
- **WHEN** the workflow provides a notification payload linked to a `proposed` action
- **THEN** the notifier renders and sends the recommendation with its absolute target and action id while leaving the action `proposed`

#### Scenario: SMTP accepts a message
- **WHEN** SMTP accepts a tracked-position email
- **THEN** the system records `smtp_accepted` rather than unverified end-to-end delivery and MUST NOT mark the linked action `acknowledged` or `executed`

#### Scenario: SMTP retry occurs
- **WHEN** a failed delivery is retried
- **THEN** the retry reuses the same envelope identity, Message-ID, item associations, and linked action ids and MUST NOT generate a new position action

#### Scenario: SMTP acceptance commit is interrupted
- **WHEN** SMTP accepts the envelope but the process stops before recording acceptance
- **THEN** a retry is allowed under documented at-least-once semantics, may duplicate the external email, and MUST NOT duplicate any item or action

### Requirement: Reminder envelopes use atomic send claims
Each notification envelope SHALL be atomically claimed with a worker token and bounded lease before SMTP send, and only the current claim holder SHALL attempt that envelope.

#### Scenario: Two workers claim one pending envelope
- **WHEN** two notification workers concurrently try to claim the same pending envelope
- **THEN** one CAS claim succeeds, the other skips it, and at most one worker enters SMTP send during the active lease

#### Scenario: Claimed worker stops before completion
- **WHEN** a worker stops after claiming an envelope and its lease expires without a terminal SMTP status
- **THEN** another worker may reclaim the same envelope/Message-ID under at-least-once semantics while preserving all item/action identities

#### Scenario: Soft watch is rendered
- **WHEN** a `take_profit_watch` payload is rendered
- **THEN** the email states `仅观察/未生成减仓动作`, omits execution language, and does not expose a reduce or exit target

### Requirement: Reminder grouping and repetition follow trading sessions
Tracked-position notification policy SHALL assign each alert transition/repeat to an idempotent notification item, SHALL group ordinary items into envelopes by owner, trading session, route, severity, channel, sealed snapshot, and digest revision, and SHALL allow urgent hard-stop envelopes to bypass ordinary digest delay.

#### Scenario: Ordinary reminders are grouped
- **WHEN** one owner has multiple non-urgent eligible reminders after the same decision snapshot is sealed
- **THEN** the system may attach their distinct item keys to one envelope key and send one session digest instead of one email per evaluation poll

#### Scenario: Sealed envelope receives a late item
- **WHEN** an envelope has been sealed or its first SMTP attempt has started and another eligible item arrives for the same session
- **THEN** the sealed item set, template version, rendered content, envelope key, and Message-ID remain immutable and the late item enters a new `digest_revision`

#### Scenario: Persistent ordinary alert repeats
- **WHEN** an ordinary alert remains firing with no transition in the same trading-session repeat slot
- **THEN** the system suppresses another email for that slot and creates no action

#### Scenario: Hard stop is not delayed by digest
- **WHEN** an eligible hard stop first enters firing
- **THEN** the workflow creates its urgent notification without waiting for the ordinary digest and links it to the single hard-stop action

#### Scenario: Hard stop repeats on a later trading day
- **WHEN** the hard stop remains unresolved, current data is decision eligible, and notification policy permits a later trading-day reminder
- **THEN** the system sends a reminder linked to the original action and identifies that no automatic execution occurred

#### Scenario: Hard stop cannot be freshly evaluated
- **WHEN** a hard-stop action remains proposed but current data is stale, missing, fallback-only, or provider-failed
- **THEN** the system suppresses the action-oriented repeat and may send only a current-data-status item that does not quote the old price as current

### Requirement: Reminder inhibition does not stop evaluation
Notification suppression, user silence, and severity inhibition SHALL affect delivery only; risk evaluation and audit SHALL continue, and data ineligibility SHALL separately prevent action generation.

#### Scenario: Higher-priority alert inhibits lower-priority email
- **WHEN** a hard stop and a take-profit watch are active for the same position episode
- **THEN** the hard-stop notification inhibits the watch email while both evaluation outcomes remain auditable and only the hard-stop action exists

#### Scenario: User silences a reminder
- **WHEN** the owner silences a rule or position until a specified trading session
- **THEN** notifications are suppressed for that interval while state transitions and action decisions continue to be evaluated and audited

#### Scenario: Data is not decision eligible
- **WHEN** stale, fallback-only, provider-failed, or otherwise ineligible data is evaluated
- **THEN** notification policy cannot turn it into a trading recommendation and the user receives only the permitted data-status explanation

### Requirement: Action-oriented emails expose execution status and evidence
Every action-oriented tracked-position email SHALL identify the decision cutoff, policy version, data source/freshness, absolute target, action status, and the fact that FundScope has not automatically executed the trade.

#### Scenario: User reads a proposed reduction
- **WHEN** a reduce recommendation email is sent
- **THEN** the body identifies the target as an absolute remaining position, shows `等待用户执行/确认`, and provides a link or instruction for updating the owned position without claiming a fill

#### Scenario: Recommendation data is incomplete
- **WHEN** the notification payload lacks the action id, policy version, eligible data time, or evidence hash required by the template
- **THEN** the notifier fails closed to a non-actionable data message or suppresses delivery and records the missing contract field
