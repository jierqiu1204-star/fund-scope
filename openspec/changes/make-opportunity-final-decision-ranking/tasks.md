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

- [ ] 4.1 Commit with a concise Chinese message and push the branch.
- [ ] 4.2 Deploy to `110.42.222.9`.
- [ ] 4.3 Re-run ETF scoring with latest available data.
- [ ] 4.4 Re-run ETF funds allocation reference.
- [ ] 4.5 Re-run Top 5/10/20/50 historical outcome validation using the new comprehensive ranking.
- [ ] 4.6 Report the Top 5/10/20/50 results to the user.
