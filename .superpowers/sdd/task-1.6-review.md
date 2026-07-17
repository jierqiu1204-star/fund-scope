# Task 1.6 review package

- Base: `dbfc20d`
- Head: `5bd02ab`
- Inspect: `git diff -U10 dbfc20d..5bd02ab`
- Brief: `.superpowers/sdd/task-1.6-brief.md`
- Report: `.superpowers/sdd/task-1.6-report.md`

## Binding checks

- The regression test requires a full, matching `final_score_v3` ranking contract and `ranking_score`; no legacy score fallback.
- It covers hashless legacy, later partial, contract mismatch, missing score, and non-finite score.
- It is intentionally RED for the documented current implementation gap; no production code changed.
- Only task 1.6 is checked.

Report Critical/Important only; otherwise `APPROVED`.
