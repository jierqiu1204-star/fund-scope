# Task 1.3 brief

## Task

Pause pending deployment, production recompute, Top-N validation, allocation recompute, and evidence-promotion tasks from overlapping active changes until the v3 publication contract is ready.

## Scope

- Documentation/OpenSpec artifacts only; do not modify production code or tests.
- Use `harden-etf-comprehensive-ranking/overlap-matrix.md` as the complete source list.
- Add explicit pause/block notes next to pending operational tasks in the five overlapping active changes. Do not mark blocked work complete and do not erase its original intent.
- State the unblock gate precisely: a pinned, published, full-scope, fresh `final_score_v3` snapshot with exact contract/universe/input identity and compatible price basis; downstream work must consume that identity without fallback.
- Retained data-refresh/version-control tasks may remain pending, but clearly state they cannot publish or promote v2/legacy ranking results.
- Do not change task 1.2 semantics or implement task 1.4.
- Mark only task 1.3 complete in `harden-etf-comprehensive-ranking/tasks.md`.

## Validation

- Strict-validate every changed OpenSpec change plus `harden-etf-comprehensive-ranking`.
- Demonstrate every blocked row from the overlap matrix has a corresponding explicit pause annotation in its source change.
- Commit one focused change and report hash/files/validation/concerns.
