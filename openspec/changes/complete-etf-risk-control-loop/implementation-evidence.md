# Implementation evidence

## Delivered contracts

- Added explicit ETF trading-capital confirmation and a full-sleeve reconciliation endpoint. Actionable amount/share sizing is unavailable until capital is finite, positive and explicitly confirmed.
- Added append-only `TrackedEtfSleeveLedgerEvent` and `TrackedEtfSleeveDailySnapshot` persistence, idempotent source hashes, bounded reconstruction and immutable evidence checks. The migration performs no historical NAV or execution backfill.
- Added the versioned `tracked_etf_sleeve_risk_v1` state contract with `normal`, `reduce_only` and `data_halt`, immediate degradation, distinct-session recovery, cooldown and stable reason codes.
- Replaced alert-row counting with distinct eligible action cycles and separately sourced `owner_confirmed` execution facts.
- Added the versioned `etf_liquidity_capacity_v1` contract using decision-eligible 20-session turnover, planned notional, quote eligibility, spread, premium/discount and limit state. Entry/add/reentry fails closed; exit evidence remains visible as stressed or unavailable.
- Added `portfolio_risk_shadow_v1`: a dedicated broad-market observation pool, asymmetric regime state, clone/theme/unknown exposure, bounded three-factor beta, 25% diagonal covariance shrinkage, marginal risk contribution and fixed stress scenarios.
- Reused the same portfolio-risk shadow evidence in production observation portfolios, optimized allocation and PIT backtests. Its `policy_mode` remains `shadow`; it does not change v1 ranking scores or ranking weights.
- ETF daily backtests now cap entries at the frozen ADV participation limit, partially fill capacity-constrained exits and exclude entries when PIT turnover capacity is unavailable. They retain next-eligible total-return-adjusted open, fees and slippage.

## Safety and unavailable behavior

- No default CNY 10,000 capital is used for actionable sizing.
- No Sina/efinance raw price, stale ranking price, SMTP acceptance or unconfirmed alert is treated as valuation or execution evidence.
- Missing capital, incomplete reconciliation, truncated/corrupt ledger, stale or ineligible valuation, non-finite input and incomplete execution coverage produce stable unavailable reasons and `data_halt`.
- `reduce_only` and `data_halt` block only increases in risk. Hold, trim, reduce and exit evidence remains observable.
- Historical sleeve NAV before the first explicit reconciliation remains unavailable and is never inferred.

## Performance bounds

- Owner risk context is built once per owner/run and reused by list, daily and intraday paths; missing bounded contexts use an explicit unavailable sentinel rather than a fallback aggregate query.
- A run is capped at 100 owners/positions for owner materialization, 1,000 ledger events and 500 eligible action cycles per owner. Bound exhaustion fails closed and is counted.
- Intraday capacity work is linear in active tracked positions and uses batched 20-session turnover inputs without provider calls.
- Portfolio covariance/factor work is post-close only and capped at 20 assets by 120 sessions. Per-owner failures are isolated.
- Stable counters cover owner/NAV availability, transitions, blocked additions, capacity ready/blocked/stressed/unavailable states, stressed exits and computation bounds.

## Activation and rollback

- Owner add/reentry protection is active wherever an owner risk context is available and fails closed when the bounded context is unavailable. Existing exit behavior is preserved.
- Portfolio Risk V2 is evidence-only through `policy_mode=shadow`; formal ranking outputs, factors, score weights, publication thresholds and leader-tactics outputs are unchanged.
- Rollback is a code/config deployment rollback: stop consuming the new optional response/evidence fields and restore the prior sizing adapter. The nullable capital-confirmation column and append-only evidence tables can remain safely in place; evidence rows must not be deleted or rewritten. The Alembic downgrade is available only when a deliberate schema rollback is required.

## Verification

Every command below used a hard 55-second process alarm.

- `ruff check` over the backend and related root tests: passed.
- `pytest tests/test_backend_domain_boundaries.py -q`: 12 passed.
- `pytest tests/test_etf_action_transitions_api.py -q`: 17 passed.
- Sleeve rule, persistence, migration and portfolio-risk shadow group: 32 passed.
- `pytest tests/test_etf_portfolio_backtest.py -q`: 20 passed, including PIT turnover entry caps, partial capacity-constrained exits and unavailable turnover exclusion.
- Final combined rule/persistence/migration/action/backtest/workflow/domain suite: 85 passed in 18.66 seconds.
- `alembic heads`: one head, `20260811_000066`.
- Additional focused tracked-position/lifecycle, allocation, observation-portfolio, authentication and API groups passed during implementation.
- `git diff --check`: passed.

The OpenSpec CLI is not installed in the locked project environment. A bounded `npx` attempt did not complete and was terminated. Repository-equivalent validation confirmed the change metadata, proposal/design/task artifacts, five capability spec deltas, requirement/scenario headings and a fully checked task list. Strict CLI validation remains an environment-tooling limitation, not an unverified implementation shortcut.
