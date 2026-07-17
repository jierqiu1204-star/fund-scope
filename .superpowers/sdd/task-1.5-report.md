# Task 1.5 implementation report

- Status: complete, pending independent review
- Commit: `dbfc20d` (`test: 修正综合关注排序回归`)
- Changed files: `backend/tests/test_short_research_api.py`; `openspec/changes/harden-etf-comprehensive-ranking/tasks.md`.
- RED baseline: the old opportunity-order assertions failed because the API returned `159002` before theme-hot `159001`, and returned catalyst-unavailable `159011` before `159010`.
- GREEN verification: `cd backend && uv run --extra dev python -m pytest tests/test_short_research_api.py -k "comprehensive_sort_uses_final_decision_score_not_theme_heat or comprehensive_sort_allows_unavailable_catalyst_to_rank_first"` — 2 passed, 23 deselected in 6.54s.
- Only task 1.5 was checked; task 1.6 remains unchecked. No production code or seed data changed.
