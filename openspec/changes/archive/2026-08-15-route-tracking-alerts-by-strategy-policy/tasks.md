## 1. Persisted Policy Contract

- [x] 1.1 Add the tracked-position policy and provenance columns with backward-compatible defaults, constraints, and downgrade support.
- [x] 1.2 Extend tracked-position create, update, and response schemas with stable policy validation and owner-scoped mutations.
- [x] 1.3 Validate optional late-day candidate provenance against compatible persisted ETF evidence while labeling absent evidence as manual selection.

## 2. Late-Day Execution And Exit Logic

- [x] 2.1 Change the late-day research execution contract to the first eligible post-decision quote within two minutes and before 14:55, retaining the fixed 10:00 exit only as a benchmark.
- [x] 2.2 Implement a pure T+1 late-day evaluator with same-session suppression, hard-stop priority, causal morning-high protection, closed 10-minute MA5 failure, mandatory 10:30 exit, and stable data-unavailable reasons.
- [x] 2.3 Route tracked-position evaluations by persisted policy without mixing generic and late-day triggers or state.

## 3. Alerts, Audit, And API Evidence

- [x] 3.1 Map late-day full-exit signals through existing cooldown, owner-recipient, SMTP, and position-action infrastructure.
- [x] 3.2 Persist and expose policy identity, rule version, provenance, evaluation session, trigger context, data cutoff, and no-action reason.
- [x] 3.3 Reset incompatible mutable state and record auditable context when the owner explicitly changes policy.

## 4. User Interface

- [x] 4.1 Add a tracking-form policy selector with the current dynamic policy selected by default and late-day policy limited to ETFs.
- [x] 4.2 Show concise policy, provenance, lifecycle, and unavailable-reason evidence on tracked-position cards without presenting research signals as automatic trades.

## 5. Verification

- [x] 5.1 Add migration and schema tests for defaults, invalid combinations, owner scope, candidate-backed provenance, and manual provenance.
- [x] 5.2 Add deterministic tests for immediate bounded entry, T+1 trigger priority, high-water causality, closed-bar MA5, timed exit, stale quotes, and policy-state reset.
- [x] 5.3 Add API/alert tests proving policy isolation, cooldown/audit/SMTP reuse, and no writes to comprehensive-ranking or research manifests.
- [x] 5.4 Run focused backend tests and Ruff in commands bounded below 60 seconds.
- [x] 5.5 Run frontend strategy/type/prettier checks in commands bounded below 60 seconds.
- [x] 5.6 Run strict OpenSpec validation and record final bounded verification evidence.

## 6. Leader-Tactics Sell-Only Policy

- [x] 6.1 Add `leader_tactics_exit_v1` to persisted policy validation for ETF and stock positions while rejecting funds and all unsupported policy/asset combinations.
- [x] 6.2 Implement the pure adjusted daily-close evaluator with immutable two-ATR/signal-low risk, one-R breakeven arming, adjusted-MA5 exit, monotonic state, and fail-closed evidence rules.
- [x] 6.3 Route leader positions through bounded ETF/A-share adjusted data reads and the existing full-exit action, audit, cooldown, owner, and SMTP pipeline without generic-policy trigger mixing.
- [x] 6.4 Validate optional candidate-backed provenance against a complete, matching, qualifying, confirmed leader-tactics V2 episode; otherwise record manual selection.
- [x] 6.5 Add explicit A-share tracked-position creation/read support using authoritative adjusted facts only, and expose the policy evidence without claiming adjusted reference return is actual P&L.
- [x] 6.6 Add the leader policy to the user tracking UI while keeping candidate/entry notifications absent and all research/ranking stores isolated.
- [x] 6.7 Add focused backend/frontend/domain-boundary tests and run each acceptance command with a hard timeout below 60 seconds.
