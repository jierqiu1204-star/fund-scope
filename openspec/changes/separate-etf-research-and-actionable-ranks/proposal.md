## Why

The current ETF workbench uses one ranking surface for both broad daily research and decisions that require trustworthy intraday execution data. This collapses a 1,400-plus ETF research universe to the small subset with complete live fields and makes missing bid/ask, IOPV, premium, or provider consensus look like missing daily research evidence.

## What Changes

- Introduce two explicit, independently versioned ETF ranking surfaces:
  - a full-universe daily research rank based only on point-in-time, total-return-adjusted daily OHLCV;
  - a strict actionable rank that applies current liquidity, spread, premium/discount, provider, freshness, risk, and sample-confidence gates.
- Treat 61 decision-eligible adjusted sessions as the minimum needed to calculate the existing 5/10/20/60-day research factors, not as the backtest length or a claim of sufficient confidence.
- Classify 61-119 sessions as provisional short history, 120-249 sessions as standard actionable history, and at least 250 sessions as full-history context; keep the thresholds visible and versioned.
- Keep research ranking available when intraday data is unavailable, while failing the actionable rank closed instead of substituting raw, stale, estimated, or single-source display data.
- Make the `/short-term` ETF list default to the broad research surface and expose actionable eligibility as a filter and clearly explained state.
- Require portfolio allocation and any rank-derived email candidate selection to consume only the actionable contract; tracked-position risk alerts remain governed by their existing independent lifecycle.
- Persist distinct contract IDs, versions, hashes, score fields, eligibility reasons, coverage, and timestamps so research results cannot be presented as actionable or same-source validated evidence.
- Preserve the current `final_score_v3` behavior during migration and add compatibility fields rather than silently changing its meaning.

## Capabilities

### New Capabilities
- `etf-ranking-surfaces`: Defines the daily research and actionable ranking contracts, history-confidence tiers, fail-closed gates, and allowed downstream consumers.

### Modified Capabilities
- `short-etf-research`: Generates and persists separate broad research and strict actionable ETF ranking outputs.
- `short-term-research`: Displays the broad research rank by default and exposes actionable eligibility without hiding otherwise analyzable ETFs.
- `etf-research-evidence-contract`: Records distinct rank identities and prevents evidence from one ranking contract from validating the other.
- `investment-reminders`: Prevents rank-derived candidate emails from using research-only or action-ineligible ranking rows.

## Impact

- Backend ETF ranking contracts, score snapshots, signal generation, portfolio candidate selection, and workflow orchestration.
- `/api/short-research` response metadata and cached ranking reads, with additive compatibility fields.
- `/short-term` ETF filters, status labels, coverage summaries, and data limitation explanations.
- Evidence contract hashes and tests that enforce domain dependencies and prevent cross-contract evidence reuse.
- No automatic change to ranking weights, alert thresholds, tracked-position exit rules, or notification delivery behavior.
