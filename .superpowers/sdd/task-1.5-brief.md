# Task 1.5 brief

## Task

Convert the failing opportunity-order regression into `test_comprehensive_sort_uses_final_decision_score_not_theme_heat`, preserving theme/catalyst evidence assertions.

## Scope

- Modify only `backend/tests/test_short_research_api.py` and the hardening task checkbox.
- The `sort=opportunity` compatibility mode is comprehensive final-decision order, not theme/catalyst heat.
- In `_seed_opportunity_signal_run`, assert the higher final decision score (`159002`, score 78) sorts before the theme-hot ETF (`159001`, score 70); continue asserting the latter's catalyst, theme, factor and risk evidence by code, not list position.
- Update the companion unavailable-catalyst ordering assertion: a higher comprehensive score may rank first even where catalyst/opportunity data is unavailable. Rename it so its intent no longer says unavailable catalyst must be last.
- Do not change production code, seed data, or task 1.6.
- Mark only task 1.5 complete after both affected tests pass.

## Verification

- Run both updated opportunity-order tests in `backend/tests/test_short_research_api.py`; they must pass.
- Confirm the revised assertions still inspect catalyst/theme evidence.
- Commit one focused test/docs change and report hash, changed files and test output.
