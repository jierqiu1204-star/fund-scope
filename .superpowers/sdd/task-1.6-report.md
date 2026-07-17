# Task 1.6 implementation report

- Status: complete, expected RED pending independent review
- Commit: `5bd02ab` (`test: add score bucket contract regression`)
- The renamed test requires a matching full `final_score_v3` source and finite `ranking_score`; it encodes exclusions for legacy/hashless, later partial, contract-mismatch, missing, and non-finite sources/scores.
- Verification: `uv run --extra dev python -m pytest tests/test_short_research_api.py::test_score_bucket_validation_requires_current_full_ranking_contract -q` failed as expected in 7.43s.
- Current failure: score-bucket validation still declares `score_breakdown_json.final_score_v2.final_score` rather than `ranking_score`; remaining assertions will become active as selector/schema work lands.
- No production code was changed. Task 1.7 remains untouched.
