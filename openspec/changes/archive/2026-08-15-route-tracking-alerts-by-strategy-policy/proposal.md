## Why

Tracked positions currently share one generic ETF/fund exit policy even when the user entered from a strategy with a materially different holding horizon. The late-day-turnaround setup needs an immediate post-signal entry contract and a causal T+1 morning exit policy, while leader-tactics holdings need a daily adjusted trend exit that does not sell merely because a fixed profit percentage was reached. Existing positions must remain on the current dynamic policy by default.

## What Changes

- Add an explicit, versioned alert-policy selection when creating or updating a tracked position; keep the current dynamic policy as the persisted default for existing and new positions.
- Add an ETF-only `late_day_turnaround_t1_v1` policy that records strategy provenance and rejects unsupported fund or A-share tracking combinations.
- Add a user-selected `leader_tactics_exit_v1` policy for ETF and A-share holdings. It sends only actionable sell emails after a tracked position exists; candidate discovery, entry confirmation, sentiment guards, and overheat observations remain web-only research evidence.
- Freeze an adjusted-price risk unit at entry, arm a round-trip-cost breakeven floor after one risk unit of profit, and exit when the latest eligible adjusted close reaches the highest of the immutable disaster stop, armed breakeven floor, or same-session adjusted MA5.
- Change the late-day-turnaround execution contract from a fixed ten-minute entry delay to the first fresh executable quote after the 14:30–14:50 signal, bounded by a two-minute entry-latency limit and the 14:55 deadline.
- Evaluate the selected late-day policy only on the next eligible trading morning: hard stop first, then causal morning-high giveback protection, then closed 10-minute-bar MA5 failure, with a mandatory full exit no later than 10:30.
- Keep the existing 10:00 fixed exit as a research benchmark, not the production email rule, and preserve no-lookahead semantics.
- Expose policy identity, rule version, strategy provenance, trigger reason, data eligibility, and unavailable reasons in the tracked-position API and UI.
- Keep late-day research materialization, ETF comprehensive ranking, and leader-tactics ranking isolated; selecting a policy never changes ranking scores and a research candidate never creates a position or email automatically.

## Capabilities

### New Capabilities

- `strategy-aware-tracking-alert-routing`: Versioned policy selection, provenance validation, late-day ETF T+1 evaluation, and user-facing policy evidence.

### Modified Capabilities

- `tracked-position-exit-strategy`: Route each tracked position through its persisted alert policy while retaining the current dynamic exit strategy as the backward-compatible default.

## Impact

- Database: tracked-position policy/provenance columns, supported-policy constraints, and backward-compatible migration defaults.
- Backend: tracked-position schemas/services, late-day execution contract, intraday evaluator, alert audit context, and policy validation.
- Frontend: tracking form policy selector, explicit A-share tracking for leader candidates, and policy/provenance display.
- Tests: migration/default behavior, policy isolation, causal T+1 trigger priority, quote freshness, MA5/high-water logic, and UI/API contracts.
- No new provider calls, background concurrency, ranking writes, broker execution, or SMTP automation from research materialization.
