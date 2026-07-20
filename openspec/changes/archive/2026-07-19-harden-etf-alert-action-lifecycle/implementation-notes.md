# Implementation Notes

## Success criteria

- Repeated evaluations of the same persistent signal reuse one action cycle and one absolute exposure-baseline target; they never compound relative reductions.
- `take_profit_watch` remains `hold` / `仅观察` and cannot create a sell proposal.
- Missing, stale, non-finite, or untrusted ranking, price, provider, or adjusted-price evidence freezes business state and creates no action.
- Notification creation, retries, claiming, SMTP attempts, and acceptance never create, advance, execute, or cool down a position action.
- Backtest trades are generated only by unique action cycles and owner-equivalent execution facts, never by repeated notification dates.

## Scope guardrails

- Ranking weights, live ranking thresholds, and provider fallback policy are unchanged by this change.
- Sina/efinance raw prices are not promoted to decision-grade adjusted prices.
- Existing diagnostic outputs are legacy/research-only unless they carry the current evidence contract and pass the new validation gates.
