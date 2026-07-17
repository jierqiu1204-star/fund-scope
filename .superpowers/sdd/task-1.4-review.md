# Task 1.4 review package

- Base: `dd18e3e`
- Head: `f006375`
- Inspect: `git diff -U10 dd18e3e..f006375`
- Brief: `.superpowers/sdd/task-1.4-brief.md`
- Report: `.superpowers/sdd/task-1.4-report.md`

## Binding checks

- One machine-readable target contract freezes the complete DAG, disjoint primitive lineage, fixed sum-to-one weights, required inputs, buckets, aliases, caps, missing/tie policy, and selector.
- Validation/history/healthcheck/AI/user state are not score-bearing.
- Missing score-bearing inputs make v3 unavailable without neutral fill or weight renormalization.
- `final_score_v2` and `opportunity_score` are not aliases to v3.
- The target selector does not prematurely activate production readers; publication/cutover remain gated.
- Only docs/config and task 1.4 changed.

Report Critical/Important only; otherwise `APPROVED`.
