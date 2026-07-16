# 2026-07-15 bounded real-data v3 shadow

This probe was read-only and does not count as a new exchange-session rollout sample.

## Guardrails

- PostgreSQL transaction forced to `READ ONLY`.
- Core calculation wrapped by a 45-second asyncio timeout and a 55-second process timeout.
- No materializer flush, publication, history synchronization, fallback quote, raw Sina/efinance price, or synthetic input.
- Before/after database counts stayed at 6 signal runs, 498 signal items, and 0 published snapshots.

## Database state

- Alembic revision before deployment: `20260715_000044`; additive revision `20260715_000045` was not yet applied.
- Latest adjusted-price trade date: 2026-07-13.
- No 2026-07-14 adjusted-price session was present.
- Stored intraday latest quotes: 0.
- Stored immutable intraday quote rows: 0.

## Result for 2026-07-13

- Point-in-time decision-price coverage: 83/83 (100%).
- Final-score-v3 score-eligible coverage: 0/83 (0%).
- Technical momentum available: 68/83.
- Risk quality available: 68/83.
- Sector trend available: 67/83.
- Theme/catalyst available: 7/83.
- Premium/discount available: 0/83.
- Structure/liquidity available: 0/83.
- `overextension_atr` finite: 83/83, all with `available_adjusted_atr20`.
- Incompatible asset bucket: 12 assets; remaining exclusions include peer-count, quality-gate, and theme/catalyst gaps.
- Elapsed core calculation time: 9.1 seconds on the current data set.

## Decision

The adjusted-price publication barrier passes, but the score publication barrier does not. No snapshot may be published. The observed `N/A` is not caused by the symmetric adjusted ATR definition; it is caused primarily by absent cutoff-bound intraday quote/consensus evidence and insufficient catalyst coverage. This date is old evidence and must not be counted toward the three distinct post-fix exchange sessions required by task 11.9.
