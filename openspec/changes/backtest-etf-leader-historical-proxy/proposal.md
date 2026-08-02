## Why

The deployed historical leader proxy can identify a current research candidate, but it provides no evidence about how the same frozen conditions performed after earlier signal dates. A rolling, no-lookahead historical proxy backtest is needed to distinguish an interesting shape from a repeatable research signal while keeping current-vintage membership bias explicit.

## What Changes

- Recompute the frozen leader breakout and former-leader repair proxies on every eligible historical signal date using only adjusted facts available by that date.
- Enter at the next eligible trading-session adjusted close and report 1, 3, 5, 10, and 20-session net outcomes after 5 bps fees and 5 bps slippage per side.
- Evaluate every historical match, preserve zero-match dates, apply the frozen clone policy, and prohibit threshold search or selecting only the current winner.
- Run in resumable bounded pages on the 2-core/4-GB server and persist immutable input, feature, signal, outcome, cost, exclusion, and result hashes.
- Expose sample counts, average and median net returns, win rates, drawdown, coverage, exclusions, confidence intervals, and explicit current-vintage/survivorship limitations as research-only evidence.
- Keep formal PIT sessions, ranking, positions, alerts, email, execution, and holdout state unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `etf-leader-tactics-shadow`: Add a frozen rolling historical-proxy evaluation that uses only date-local adjusted prices and never masquerades as factual PIT replay.
- `etf-research-evidence-contract`: Persist and expose immutable multi-horizon historical-proxy backtest evidence with zero formal promotion credit.
- `etf-label-validation-dashboard`: Display rolling proxy outcomes and limitations separately from formal PIT observations and live recommendations.

## Impact

- Backend: strategy-lab rolling evaluator, bounded production-data runner, immutable evidence adapter/projection, schemas, and focused tests.
- Frontend: the existing leader historical-proxy research card.
- Data: one additive research evidence family/artifact; no schema migration or production-policy mutation.
- Operations: single worker, resumable checkpoints, bounded date/asset pages, and every invocation below 55 seconds.
