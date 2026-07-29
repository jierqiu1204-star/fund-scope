# Validation Evidence

## Local bounded verification

All commands were hard-capped below 60 seconds.

- PIT input, Stage A/B, frozen candidates, forward outcomes, and ranking
  endpoints: 74 passed.
- Factor diagnostics, experiment evidence, promotion gates, holdout support,
  and bounded coordinator: 45 passed.
- Action-policy validation, chunk invariance, portfolio replay, policy shadow,
  and provenance separation: 70 passed.
- Evidence API, ranking contract, and validation-manifest persistence/repair:
  18 passed.
- Domain boundaries plus risk/action contract compatibility and coordinator:
  69 passed.
- Frontend PIT evidence static contract: passed.
- Frontend TypeScript `--noEmit`: passed.
- Backend Ruff: passed.
- Strict OpenSpec validation: passed.

The deterministic coordinator test produces identical final phase artifacts,
coverage, exclusions, and result hashes after interruption and safe 5/10/20 page
size changes. The scheduler-facing job advances exactly one durable page per
trigger.

## Production read-only audit — 2026-07-25

The audit ran on Saturday at 13:21 Asia/Shanghai and did not trigger provider
work, synchronization, ranking generation, or publication.

Latest completed-session production evidence for 2026-07-24:

| Evidence | Observed |
|---|---:|
| Authoritative universe | 1,485 ETFs |
| Target-date decision-eligible adjusted rows | 1,462 / 1,485 (98.4512%) |
| 61-session warm-up coverage | 1,342 / 1,485 (90.3704%) |
| Warm-up gaps | 143 |
| Raw decision-price violations | 0 |
| Non-finite decision violations | 0 |
| Accepted target-date provider | TickFlow backward-adjusted v1 only |

The latest bounded slice was JobRun 9543 with checkpoint identity
`249e5688f8a28a9f1ae688b256f3ee7e1193c033f3c48384e951384b44abf56c`.
It used the conservative 10-code profile, completed 9 of 10 codes in 7.758
seconds, persisted 643 rows, peaked at 231,710,720 bytes RSS, left 143
candidates, and stopped with `continuation_required`. TickFlow had 30 accepted
successes, zero consecutive failures, zero timeouts, and a closed circuit.

Server memory was 3,399 MiB total with 2,158 MiB available. The backend used
325.1 MiB, PostgreSQL 455.2 MiB, and nginx 5.2 MiB; all containers were up.

No current full-scope published dual-ranking snapshot exists. The 61-session
gate is below 95 percent, so this change must remain `insufficient_data`.
Production scores, allocation, positions, alerts, notification policy, and SMTP
state were not changed.

## Remaining real-data gates

Tasks 8.4–8.6 remain open. On the next eligible trading session the existing
bounded publication-readiness scheduler may continue one slice per due trigger.
Only after both factual coverage ratios reach 95 percent may one full dual
snapshot be publication-validated. PIT research continuations must then
accumulate at least 252 eligible sessions, 40 non-overlapping primary dates, and
three chronological folds before any manual promotion review.

Rollback is additive: stop scheduling the new research continuation and keep the
current production ranking contract. Persisted immutable evidence and factual
adjusted rows can remain. Rollback must not delete provenance, enable raw-price
fallback, lower either 95 percent gate, or change live alert/email policy.
