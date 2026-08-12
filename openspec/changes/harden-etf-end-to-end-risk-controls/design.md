## Context

See `proposal.md` for motivation. The current production path already has strict ETF data gates, a deterministic observation allocator, legacy tracked-position alerts, and a completed V2 lifecycle implementation with shadow-evidence tables. The missing work is orchestration and portfolio-wide enforcement: scheduled jobs call `create_alert_if_needed` directly, final risky weights can still approach full exposure, and execution costs are represented inconsistently across alerts and research backtests.

The implementation must preserve the modular-monolith dependency direction in `AGENTS.md`, keep the 2-core/4-GB deployment bounded, reuse existing JSON/evidence storage, and fail closed whenever price basis, freshness, or execution evidence is not comparable.

## Goals / Non-Goals

**Goals:**

- Exercise the existing V2 lifecycle on every eligible production evaluation in an isolated, idempotent shadow lane before any later cutover decision.
- Make cash an explicit risk-control output rather than a residual that can be lost during weight normalization.
- Apply a transparent portfolio volatility/drawdown/concentration budget after candidate allocation.
- Use one pure execution-risk contract for live alert evidence and research-only fill assumptions.
- Keep every new operation bounded, deterministic, observable, and independently reversible.

**Non-Goals:**

- No V2 production action writes, V2 email generation, broker integration, automatic execution, API change, or schema migration.
- No modification of ETF comprehensive-ranking factors, score weights, publication coverage, leader-tactics isolation, or raw-price eligibility.
- No VaR engine, optimizer dependency, parameter search, or attempt to maximize backtest return in this change.
- No claim that an observed threshold, SMTP acceptance, or simulated fill proves a real user trade.

## Decisions

### 1. Add shadow evaluation in workflow orchestration, not in domain services

The scheduled daily and intraday workflows will invoke a small adapter after the established legacy position evaluation has produced its owner-scoped decision context. The adapter seals the already-fetched eligible evidence into a V2 command and calls the existing lifecycle workflow with `LifecycleRolloutMode.SHADOW`. The existing position analysis is prepared once and shared by both lanes; shadow MUST NOT issue a second history query or provider request.

The shadow lane will:

- reuse the same position analysis, evaluation mode, quote/daily evidence, cutoff, and policy identity instead of fetching a second provider snapshot;
- convert the prepared analysis into the complete frozen rule set, including rules that are currently false, so a previously firing V2 rule can recover and resolve;
- write only `TrackedPositionLifecycleShadowEvidence` through the existing lifecycle implementation;
- expose aggregate counters (`evaluated`, `data_ineligible`, `failed`, `deferred`) in the parent job result;
- process positions serially, cap one run at 100 shadow evaluations, and never change the legacy alert/email result;
- be controlled by one settings flag that defaults on for shadow-only behavior and can be disabled without redeployment-side data repair.

The snapshot hash and repeat slot derive only from the immutable market evidence, never from whether a run was scheduled or manually requested. Missing baseline/episode/exposure state and out-of-order snapshots become stable shadow skips instead of fabricated state. Legacy comparison data is sealed into the first immutable shadow row rather than added later.

Any adapter failure is caught per position and represented by a short stable error code. It must not contain account data or abort the quote/watch job. Calling V2 from `risk_alerts`, `tracked_positions` domain rules, or `notifier` was rejected because cross-domain sequencing belongs in workflows. Replacing the legacy path immediately was rejected because current production has not accumulated the three distinct clean shadow sessions required by the existing cutover gate.

### 2. Apply a versioned risk budget after raw allocation and never renormalize risky assets afterward

A pure `PortfolioRiskBudget` contract will consume a pre-allocation market-risk state, the selected candidates, their already-available decision-eligible adjusted histories, and the proposed weights. It returns constrained weights, explicit cash weight, metrics, binding constraints, and unavailable reasons. Database access and user holdings remain outside this module.

The current cap helper is replaced with proportional capped redistribution. It first preserves relative raw weights, applies per-asset and group capacities, redistributes only remaining admissible weight in deterministic rounds, and leaves infeasible residual as cash. It MUST NOT fill candidates sequentially to their cap; equal raw weights for four unconstrained ETFs therefore remain `25/25/25/25`, rather than collapsing to `30/30/30/10`.

Before allocation, a versioned classifier derives `risk_on/neutral/defensive/cash_wait` only from the same PIT snapshot's broad-equity breadth, 20-session return, and relation to adjusted MA20. Missing or inconsistent evidence yields `cash_wait`; the system never defaults to `risk_on`. Version `portfolio_risk_budget_v1` uses these observation-only limits:

| Market-risk state | Primary + satellite cap | Minimum cash | Defensive capacity |
| --- | ---: | ---: | ---: |
| `risk_on` | 100% | 0% | residual only |
| `neutral` | 60% | 20% | up to residual after cash |
| `defensive` | 25% | 40% | up to residual after cash |
| `cash_wait` | 0% | 100% | 0% |

Existing hard compatibility limits remain 30% per ETF, 15% per satellite ETF, 35% total satellite exposure, and 60% per theme. The existing per-asset volatility/drawdown exposure reducer becomes part of the real weighting path instead of remaining unused. A new 50% combined cap applies to a deterministic high-correlation cluster. Pairs require at least 40 overlapping adjusted-return observations and correlation at or above 0.85; same-underlying clones remain governed by the stricter clone rule. Missing correlation is never treated as zero diversification.

For at least 60 comparable sessions, the proposed fixed-weight portfolio return series provides annualized volatility, 60-session maximum drawdown, common-sample count, theme exposure, cluster exposure, and threshold status in the existing risk summary. Version 1 records those portfolio metrics and flags breaches but does not add an unvalidated continuous volatility/drawdown scaling formula. Hard exposure changes come only from the PIT market state, existing per-asset risk reducer, liquidity exclusions, and deterministic concentration caps. If the evidence required for those hard controls is missing or non-finite, allocation fails closed to `cash_wait` or leaves the residual as cash. No later helper may normalize risky weights back to one.

The main observation portfolio, optimized-allocation variants, and historical backtest all call this shared constrained allocator and carry the same contract version/hash. This prevents the current divergence where an optimized reader ignores the source snapshot's market state and cash target. This approach was chosen over covariance optimization, VaR/CVaR, or an external solver because it is explainable, cheap, stable on a 2-core/4-GB host, and adds the missing portfolio-level brake without creating a second allocation engine.

### 3. Repair the ETF email gate before adding execution context

ETF action-email eligibility becomes fail closed for both daily and intraday evaluation modes. A tracked ETF may send an action email only when the current intraday snapshot is fresh, explicitly stores `decision_eligible=true`, has an allowed reliability/provider state, and supplies the executable-side quote required by the action. A same-day close, stale intraday record, fallback source, or legacy row with a missing eligibility field remains usable for web context only. Confirmed mutual-fund NAV behavior remains separate and unchanged.

The quote helper therefore treats a missing `decision_eligible` field as false. This intentionally changes old-row behavior from fail-open to fail-closed. The daily 22:00 ETF review can still update explanations from closing evidence but cannot manufacture an actionable email after the quote freshness window; the minute-level market-hours job remains the eligible ETF email path.

### 4. Reuse live execution evidence and the existing action-replay fill contract

For live holding analysis, `risk_alerts` gains a small pure `ExitExecutionEvidence` value object. It accepts the already-loaded quote context and returns signal price, sell-side bid reference, spread, comparable-basis gap through the stop, a fixed slippage reserve, status, and stable reason code. It never reads providers or writes records. A sell reminder may show bid-based execution context only when bid/ask are finite, ordered, fresh, and decision-eligible; otherwise status is `not_observable`. Latest price and daily close are never relabeled as expected sell price.

Research backtests reuse the existing action-replay `select_adjusted_open_fill`, `ActionFill`, lot-size rounding, and frozen ranking fee/slippage constants rather than introducing a second execution engine. T-day signals may fill no earlier than the next eligible session's adjusted open. Suspension, zero volume, missing adjusted open, an unprovable limit state, or non-finite values keep the order pending or excluded with a stable reason; there is no same-day close or later-known best-price fallback.

The base scenario keeps the frozen 5 bps fee and 5 bps slippage per side. A separately frozen 20 bps slippage stress result is reported alongside it, with spread and signal-to-fill gap shown separately to avoid hidden double counting. Historical daily runs label spread and intraday path as modeled sensitivity; intraday replay requires explicit eligibility and uses ask for buys and bid for sells.

### 5. Reuse existing JSON/evidence columns and preserve provenance labels

Shadow differences use the existing shadow-evidence store. Execution-risk context is added to existing alert/audit context JSON and backtest result/trade JSON with a schema/version marker and bounded fields. Portfolio risk metrics and binding constraints use the existing observation portfolio summary. No migration is required.

Provenance remains categorical and non-promotional:

- `signal_observed`
- `notification_smtp_accepted`
- `simulated_fill_base`
- `simulated_fill_stress`
- `owner_confirmed`
- `broker_confirmed`

No transition between these categories is inferred. In particular, SMTP acceptance and simulation never create real execution provenance.

### 6. Verification is split into short deterministic groups

Tests will cover pure risk-budget and execution-risk contracts first, then workflow integration with fake eligible/ineligible evidence, then existing domain-boundary tests. Expensive historical or network work is excluded from local acceptance. Every command gets a hard timeout of at most 60 seconds; if a test group cannot complete in that budget it is split by file or test name.

## Risks / Trade-offs

- [Shadow adapter accidentally duplicates production actions] → construct the rollout policy internally as `SHADOW`, assert all production-write and notification flags are false, and test database side effects.
- [Shadow work slows the one-minute intraday loop] → reuse fetched evidence, run serially, cap evaluations at 100, aggregate unchanged polls, and keep legacy work independent.
- [Risk budget reduces displayed exposure and historical return] → treat this as an explicit safety outcome, show the binding cap and cash, and compare base versus prior contract in research only.
- [Volatility and correlation look stable before a regime break] → combine them with mode, drawdown, theme, clone, liquidity and cash controls; do not describe historical estimates as guarantees.
- [Adjusted daily price and raw quote are incomparable after a corporate action] → require declared comparable price basis; otherwise record unavailable rather than calculating a false gap.
- [Stress cost double counts spread] → report fee, spread and slippage as separate fields and define total-cost composition in the contract.
- [Existing response consumers assume weights sum to one] → preserve total accounting by including explicit `cash_weight`; risky weights plus cash still sum to one within tolerance.

## Migration Plan

1. Deploy pure execution-risk and portfolio-risk-budget contracts with tests; no scheduled behavior changes yet.
2. Apply risk-budget output to observation portfolios and confirm explicit cash plus existing response compatibility.
3. Enable V2 lifecycle shadow by default, leaving legacy alert/action/notification behavior authoritative.
4. Observe at least three distinct eligible trading sessions and evaluate the existing cutover gates; this change does not perform the production cutover.
5. Rollback by disabling the shadow flag and selecting the previous allocation contract version; keep accumulated shadow and audit evidence intact.
