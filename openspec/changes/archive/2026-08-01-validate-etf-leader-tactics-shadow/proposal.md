## Why

The saved “最强飞哥” articles describe a leader-breakout mode, an old-leader repair mode, and a market-cycle switch, but the proprietary “起飞信号” is undisclosed and several judgments are subjective. FundScope needs a reproducible way to test the disclosed ideas on ETFs without presenting a proxy as the original method, fitting parameters to short samples, or changing the production ranking and alert chain before point-in-time evidence exists.

## What Changes

- Add a research-only ETF leader-tactics hypothesis contract with source provenance, explicit non-equivalence limitations, immutable formulas, and three pre-registered candidates:
  - `leader_breakout_proxy_v1`: sector-relative leadership, adjusted `MA5 > MA10 > MA20`, a fixed 120-session volume breakout, and a transparent 20-session price-breakout confirmation;
  - `former_leader_repair_proxy_v1`: prior point-in-time peer leadership, a frozen drawdown band, positive stabilization, and range-compression confirmation;
  - `cycle_routed_leader_proxy_v1`: route between the two candidates using the existing frozen market-regime contract, with cash/no-selection when the declared regime is incompatible or unavailable.
- Reuse the existing point-in-time factor experiment, common-support outcome, cost, walk-forward, purge/embargo, block-bootstrap, holdout, and bounded-continuation components; do not create a second backtest engine.
- Evaluate each proxy against the current frozen Top10 baseline with the existing primary endpoint: five-session paired net excess on common support after non-zero fees and slippage. Treat all other horizons and directional diagnostics as exploratory.
- Add explicit ETF-adaptation diagnostics for sector/peer mapping quality, clone concentration, required history, signal scarcity, market-regime dependence, and overlap with existing momentum, sector-trend, risk, and liquidity factors.
- Persist source-hypothesis identity, proxy formulas, data cutoffs, input hashes, exclusions, uncertainty, and production-isolation evidence in the ETF research contract.
- Expose the experiment only on the research evidence surface with `insufficient_data`, `unconfirmed`, `rejected`, or `eligible_for_v4_proposal`; never label it “飞哥原版信号”, production ranking performance, live notification evidence, or confirmed execution.
- Keep `daily_reconstructable_v1`, `final_score_v3`, actionable ranking, allocation, tracked positions, risk alerts, email rules, SMTP state, and the existing three-candidate ranking-promotion loop unchanged. Promotion requires a separate manually approved score-version change after all existing sample, coverage, uncertainty, drawdown, concentration, and one-time holdout gates pass.

## Capabilities

### New Capabilities

- `etf-leader-tactics-shadow`: Defines the transparent ETF proxy hypotheses, immutable candidate registry, point-in-time feature rules, cycle routing, diagnostics, bounded execution, and production isolation.

### Modified Capabilities

- `etf-factor-incremental-alpha-validation`: Requires leader-tactics proxies to prove residual, cost-adjusted incremental alpha against existing factors on common support without expanding the production ranking candidate registry.
- `etf-research-evidence-contract`: Records source-to-hypothesis provenance, disclosed versus unavailable source rules, proxy non-equivalence, candidate formulas, and leader-shadow evidence identity.
- `etf-label-validation-dashboard`: Adds a separately labeled leader-tactics research panel with exact availability reasons and no production, notification, or execution implication.

## Impact

- Backend: `app.services.strategy_lab` hypothesis contracts, point-in-time feature construction, factor experiment registration/continuation, diagnostics, evidence projection, and focused persistence models or additive metadata.
- Frontend: the existing `/short-term/evidence` research surface and TypeScript evidence types; no default ranking or trade-action behavior changes.
- Data: additive immutable experiment manifests, cached factor rows, checkpoints, diagnostics, and result evidence. Historical membership or receipt times are never inferred, and raw Sina/efinance prices cannot increase decision coverage.
- Operations: one worker, deterministic pages of at most 20 ETFs, durable cursors, bounded memory, and at most 55 seconds per continuation for the existing 2-core/4-GB deployment.
- Verification: formula and no-future-data tests, candidate-freeze and non-equivalence tests, common-support/cost/uncertainty tests, interruption/idempotency tests, production-side-effect tests, API/UI evidence-separation tests, backend domain boundaries, Ruff, and strict OpenSpec validation.
