## 1. Backend Ranking

- [x] 1.1 Change `sort=opportunity` to rank by final decision score before falling back to conservative total score.
- [x] 1.2 Preserve `opportunity_score` as theme/sector context instead of the primary comprehensive ranking.
- [x] 1.3 Add or update backend tests proving high theme heat cannot outrank safer assets solely because of `opportunity_score`.

## 2. Frontend Copy And Display

- [x] 2.1 Keep the sort label `综合关注` but update helper text to describe it as final decision score.
- [x] 2.2 Rename opportunity score explanation to theme/sector auxiliary context where it appears.
- [x] 2.3 Ensure selected ETF detail does not present theme heat as the final ranking reason.

## 3. Validation

- [x] 3.1 Run focused backend tests for short-term ranking and domain boundaries.
- [x] 3.2 Run frontend type checking.
- [x] 3.3 Confirm OpenSpec status is apply-ready and task checkboxes reflect completed work.

## 4. Push, Deploy, And Recompute

- [ ] 4.1 Commit with a concise Chinese message and push the branch. Retained as version-control housekeeping only; it MUST NOT publish, deploy, recompute, or promote `final_score_v2` or legacy ranking results.
- [ ] 4.2 Deploy to `110.42.222.9`. **PAUSED / BLOCKED:** resume only when a pinned, published, full-scope, fresh `final_score_v3` snapshot exists with exact contract, universe, and input identity plus a compatible price basis, and deployment consumers preserve that identity without fallback.
- [ ] 4.3 Re-run ETF scoring with latest available data. **PAUSED / BLOCKED:** resume only when a pinned, published, full-scope, fresh `final_score_v3` snapshot exists with exact contract, universe, and input identity plus a compatible price basis, and the recompute consumes and preserves that identity without fallback.
- [ ] 4.4 Re-run ETF funds allocation reference. **PAUSED / BLOCKED:** resume only when a pinned, published, full-scope, fresh `final_score_v3` snapshot exists with exact contract, universe, and input identity plus a compatible price basis, and allocation consumes and preserves that identity without fallback.
- [ ] 4.5 Re-run Top 5/10/20/50 historical outcome validation using the new comprehensive ranking. **PAUSED / BLOCKED:** resume only against a pinned, published, full-scope, fresh `final_score_v3` snapshot with exact contract, universe, and input identity plus a compatible price basis; validation MUST consume that identity without v2/legacy fallback or evidence promotion.
- [ ] 4.6 Report the Top 5/10/20/50 results to the user. **PAUSED / BLOCKED:** resume only for validation derived from a pinned, published, full-scope, fresh `final_score_v3` snapshot with exact contract, universe, and input identity plus a compatible price basis, consumed without fallback; v2/legacy evidence MUST NOT be promoted as current.
