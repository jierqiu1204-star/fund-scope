# Implementation verification

All commands below used a hard 55-second alarm.

| Verification group | Result |
| --- | --- |
| Readiness boundary, current/lifetime RSS, compact readiness | 21 passed |
| Snapshot materialization, publication, and selectors | 43 passed |
| Publication coordinator, scheduler, jobs, research-history lane | 40 passed |
| Bounded-history and publication-readiness sync | 36 passed |
| PIT coordinator, replay inputs, production capture, and migration | 26 passed |
| Stage A/B, candidates, validation, factor evidence, policy shadow, evidence API | 68 passed |
| Final PIT/scheduler/coordinator regression group | 20 passed |
| Backend domain boundaries | 10 passed |
| Frontend TypeScript and dual-ranking static contract | passed |
| Ruff, changed files and repository-wide | passed |
| Strict current-change OpenSpec validation | passed |
| Strict canonical OpenSpec validation | 29 passed, 0 failed |

The broader legacy `tests/test_short_research_api.py` group remains outside this
change's focused acceptance: 23 tests still seed pre-dual-ranking
`final_score_v3` fixtures while the API has required the dual research-surface
identity since the baseline revision. Those tests fail closed with empty/503
responses; this change does not restore the incompatible fallback.

Production rollout evidence is intentionally not claimed here. Tasks 8.2–8.9
still require deployment and distinct real-trading-day observations.
