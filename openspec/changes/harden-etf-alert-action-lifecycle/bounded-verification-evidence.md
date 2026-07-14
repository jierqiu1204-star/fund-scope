# Bounded verification evidence (interim)

Recorded on 2026-07-15 (Asia/Shanghai). This is the implementation and bounded-test handoff for sections 9–12; it is not the final section 12.7 evidence because real-session observation and production cutover remain open. Every command below had an external timeout of 55 seconds or less.

## Implementation invariants

- The v2 replay accepts only unique action decisions with absolute exposure-baseline targets. Repeated/retried/recovery notifications and soft watches do not create trade intents.
- Stage B ranks one complete point-in-time cross-section per date, shares candidate portfolio state across all ETFs, executes sell targets from immutable baseline shares, and persists bounded checkpoints.
- The legacy `/etf-backtests` engine remains available only as an explicitly labelled `legacy_diagnostic`: `research_only=true`, `promotion_eligible=false`, and `evidence_status=旧口径结果`. Its relative-current-position output cannot be used as current v2 evidence.
- Legacy backtest summaries no longer synthesize a 20-sample minimum or self-attest as same-contract. Strategy healthcheck excludes them from current intraday evidence.
- Validation candidates, endpoints, bootstrap settings, purge calendar, holdout scope, and evidence hashes are frozen before outcome use. A passing validation result remains review-only and cannot auto-activate a production policy.
- Shadow evidence is isolated from production actions, holdings, notification delivery, and production reads. Rollback returns to display-only/no-new-action mode without reactivating relative compounding or email-as-execution.

## Backend test evidence

The lifecycle/API/notifier verification was split before any command approached the hard timeout. The focused section 12.1 commands covered 202 cases in total:

| Area | Result | Longest command |
| --- | ---: | ---: |
| workflow | 29 passed | 23.23 s |
| rollout | 20 passed | 38.12 s |
| terminal transitions | 8 passed | 30.69 s |
| lifecycle, risk mapping, root exit guards | 53 passed | 0.49 s pytest time |
| repository and bounded reads | 5 passed | 20.79 s |
| schema and exposure mutation | 10 passed | 26.47 s |
| action transition API | 16 passed | 26.94 s |
| audit API | 5 passed | 15.78 s |
| notification delivery | 11 passed | 30.10 s |
| notification envelopes | 5 passed | 18.75 s |
| notification policy/templates | 19 passed | 0.28 s pytest time |
| migration/backfill/validation migration | 6 passed | 8.38 s |
| tracked-position integration | 15 passed | 35.80 s |

Section 9 replay was verified in three bounded groups:

- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_action_replay_contract.py tests/test_etf_action_replay_execution.py tests/test_etf_action_replay_benefit.py tests/test_etf_action_replay_point_in_time.py tests/test_etf_action_replay_features.py -p no:cacheprovider -q`: 54 passed in 0.62 s pytest time.
- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_action_replay_artifact_store.py tests/test_etf_action_replay_checkpoint.py tests/test_etf_action_replay_chunk_invariance.py tests/test_etf_action_replay_continuation.py -p no:cacheprovider -q`: 41 passed in 2.10 s pytest time.
- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_action_replay_portfolio.py tests/test_etf_action_replay_profile.py -p no:cacheprovider -q`: 11 passed in 10.85 s pytest time.

Post-fix focused regression commands:

- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_portfolio_backtest.py -p no:cacheprovider -q`: 14 passed; 47.4 s external wall time.
- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_backtest_evidence_dimensions.py -p no:cacheprovider -q`: 4 passed; 11.8 s external wall time.
- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_action_replay_contract.py -p no:cacheprovider -q`: 14 passed; 14.6 s external wall time.
- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_point_in_time_universe.py -p no:cacheprovider -q`: 1 passed; 17.1 s external wall time.
- `backend/.venv/Scripts/python.exe -m pytest tests/test_etf_action_replay_artifact_store.py -p no:cacheprovider -q`: 14 passed; 14.0 s external wall time.

`tests/test_etf_action_policy_validation.py` was partitioned by test name into candidate/contract/Candidate3, purge, inference/report, persistence/attestation, holdout, and marker-write groups. All 41 unique cases passed; the final-holdout rejection case intentionally appeared in two safety groups. No command exceeded 28.6 seconds external wall time.

- `backend/.venv/Scripts/python.exe -m pytest tests/test_backend_domain_boundaries.py -p no:cacheprovider -q`: 6 passed; 12.2 s external wall time.
- Targeted `backend/.venv/Scripts/ruff.exe check --no-cache ...` groups covered application entry points, lifecycle/workflows, replay/validation, migrations, lifecycle tests, notification/API tests, and replay tests: all checks passed.

The Windows test wrapper adds roughly 13 seconds of fixed startup/shutdown overhead. Suites were split so pytest work stayed bounded; no production timeout or event-loop behavior was changed to hide that overhead.

## Frontend and OpenSpec evidence

- `pnpm exec tsc --noEmit --pretty false`: passed in 8.8 seconds.
- `pnpm run test:backtest-evidence`: passed in 2.8 seconds.
- `ESLINT_USE_FLAT_CONFIG=false pnpm exec eslint --no-cache app/short-term/evidence/page.tsx lib/types.ts`: zero errors; one pre-existing unused `formatNullableRate` warning.
- `openspec validate harden-etf-alert-action-lifecycle --strict`: valid in 4.3 seconds.
- `openspec validate upgrade-etf-exit-risk-system --strict`: valid in 4.3 seconds.
- `openspec validate upgrade-etf-exit-with-reentry-sizing --strict`: valid in 4.3 seconds.

## Resource and migration evidence

- The section 9 representative synthetic batch used one worker, 1,200 ETF codes, 22 weekday sessions, 26,400 source rows, and 2,400 replay feature rows. Peak RSS was 95,928,320 bytes (91.48 MiB), below the 2.5 GiB gate; 28 instrumented SQLite statements, 28,832 bounded rows, a 6,355-byte checkpoint, and 2,315.82 source rows/second were recorded. See `section-9-profile-evidence.md`. CPU affinity was not clamped, so this is not claimed as an exact two-core hardware benchmark.
- A disposable schema-only production-shaped PostgreSQL copy upgraded from revision `20260712_000038` to `20260715_000044`, with public tables increasing from 82 to 89. The real production source had zero tracked-position rows, so every backfill counter was zero. See `section-11-production-copy-evidence.md`.

## Remaining real-environment gates

- 11.3: no substitution has been made for three distinct real trading sessions with decision-eligible data.
- 11.5: v2 production reads/writes have not been enabled; legacy production writes have not been disabled.
- 12.7: the final evidence summary remains open until 11.3 and 11.5 are genuinely complete. The tested rollback is the application rollback path; a runtime rollback was not performed on the disposable production-shaped copy.

No ranking weight, Top-N publication gate, production email threshold, provider fallback policy, or live production policy was auto-changed. Sina/efinance raw prices were not promoted to decision-grade adjusted data.
