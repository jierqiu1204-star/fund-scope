# Task 1.7 brief

## Task

Add failing regression tests for partial-run canonical pollution, stale canonical cache, one-code detail rank, filtered live rank change, missing-hash classification, post-enrichment caps, negative evidence, and validation side effects before changing production behavior.

## Scope

- Tests and the hardening task checkbox only. Do not change production code.
- Add one focused test for each named regression using existing targeted test modules where possible:
  - canonical selector ignores a later theme/codes partial run;
  - no compatible fresh full snapshot returns stale/waiting rather than an earlier cache;
  - detail preserves persisted global rank for a selected code;
  - live rank/rank change are calculated before filtering and are null for incomparable scope;
  - evidence without contract hash is `旧口径结果`, never synthesized as current;
  - stale/unavailable caps apply after enrichment and cannot be bypassed;
  - negative/sufficient/stale label evidence does not change current live score or rank;
  - every validation mode changes only evidence/validation tables, not signal/allocation/position/alert/notification tables.
- Prefer focused fixtures and existing API/service seams. Each test must encode the target behavior even if it currently fails.
- Do not change task 1.6 or any implementation task.
- Mark only 1.7 after the added tests have been run and their expected RED failures documented.

## Verification

- Run each added test, or a bounded grouped command, and record which assertions fail because the current implementation is permissive/incorrect.
- Keep failure output limited to the intended new tests; do not fix production code.
- Run `git diff --check`, then commit the focused RED test suite and report file list/test results.
