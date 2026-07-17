# Task 1.6 brief

## Task

Convert the failing score-bucket test into `test_score_bucket_validation_requires_current_full_ranking_contract`, covering legacy score, later partial run, contract mismatch, missing score, and non-finite score.

## Scope

- Modify only `backend/tests/test_short_research_api.py` and the hardening task checkbox.
- Replace legacy `opportunity_score`/`total_score` assertions with a target `final_score_v3` full-snapshot contract expressed by the fixture metadata and item breakdown.
- The fixture must include, and assert exclusion of:
  - a hashless/legacy-score run;
  - a later `theme` or `codes` partial run;
  - a full run with a mismatched contract hash;
  - a current-run item with no `ranking_score`;
  - a current-run item with non-finite `ranking_score`.
- The only selected source must be the matching full v3 run; its finite `ranking_score` items determine Top-N/all-scored groups. Legacy `total_score` must never rescue an item.
- Do not modify production code or task 1.7. The resulting test is expected to be RED until snapshot schema/selector and score-source work lands.
- Mark only 1.6 complete after the test is added and its intended RED failure is demonstrated.

## Verification

- Run the renamed test alone, capture the expected RED output, and verify it fails because current code still chooses a permissive/latest run or legacy score fallback.
- Run `git diff --check`.
- Commit the focused RED regression test and report the failure reason; do not claim the full test suite is green.
