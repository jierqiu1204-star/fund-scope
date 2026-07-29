## Shadow verification

Verification date: 2026-07-19 (Asia/Shanghai)

The bounded shadow fixture contains 1,405 deterministic ETF candidates, matching the
production universe shape without reading live providers or substituting market data.

- Research rows: 1,405
- Actionable rows: 1,053
- Actionable exclusions: 352
- Exclusion summary: `final_score_v3:cap_violation = 352`
- Deterministic surface hash:
  `bb90260cec8f268f2ab85cfbb3daf4a148c1b11118bb48dd957f6f9486c65632`
- Hash reconciliation: regenerated twice from the current frozen
  `daily_reconstructable_v1`, `final_score_v3`, and `actionable_rank_v1`
  component contract hashes; input reversal produced the same result.
- Ranking generation latency: 0.515977 seconds in the recorded run
- Full-universe service batches: 71 (`70 * 20 + 5`)
- Peak batch size: 20 ETFs
- Peak history row bound: `20 * 180`

The provider-outage regression publishes and serves the research surface, returns
`waiting` for the actionable selector, produces no allocation candidate, and leaves
the observation portfolio at 100% cash. Rank-derived action context records exact
contract, hash, as-of, eligibility, rank, and score suppression reasons. There is no
separate rank-derived candidate-email producer in the current codebase; tracked-position
emails remain on their independent lifecycle and decision-eligible quote gate.

## Consumer switch

- `/api/short-research/assets?asset_type=etf` defaults to `ranking_surface=research`.
- `ranking_surface=actionable` returns only rows with a matching eligible
  `actionable_rank_v1` identity.
- The `/short-term` workbench defaults to the cached research list and exposes a
  separate actionable filter.
- ETF observation allocation and optimized allocation require the actionable contract,
  hash, as-of date, eligibility, finite score, and positive actionable rank.
- Tracked-position risk alerts do not require research-rank membership and still require
  their own fresh decision-eligible quote before email delivery.

## Rollback

Rollback does not rewrite stored evidence:

1. Restore the previous cached ETF consumer selection in the API/workbench.
2. Restore the previous allocation selector only if an operational rollback is explicitly
   approved.
3. Keep additive dual-surface fields and snapshots for audit.
4. Do not relabel research rows as actionable and do not reuse either surface hash for the
   other.

The rollback path is code-only and preserves the frozen `final_score_v3` contract.
