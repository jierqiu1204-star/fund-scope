# Task 1.7 implementation report

- Status: complete, expected RED pending independent review
- Commit: `e882be3` (`test: lock ranking hardening regressions`)
- Added coverage for all eight requested risks across static API, live ranking, evidence classification, and ranking caps/evidence.
- Bounded grouped run: 11 failed, 1 passed, 72 deselected in 19.48s.
- Expected REDs: detail rank 1 fabrication; later partial/mismatched run pollution; stale cache still served; filtered live rank becomes 1; missing hash synthesized current; stale/unavailable caps exceed 55/45; four evidence states alter current score/rank.
- Passing invariant: validation request(s) preserve signal/allocation/position/alert/notification counts; an existing filtered-live context test also remains compatible where selected.
- No production code was changed.
