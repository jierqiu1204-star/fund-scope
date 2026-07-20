## Context

The latest ETF exit credibility run showed that several current exit signals have high false-exit rates when treated as short-term direction predictors. This is expected for some risk controls: a hard stop is insurance against unacceptable loss, while a trailing stop is a position-state rule that depends on entry price and high-water profit. The current implementation mixes these concepts with observation labels and research evidence, which makes the UI and validation results look weaker and less actionable than they should be.

The project already has separate domains for market data, research signal, tracked positions, risk alerts, notification, and workflow orchestration. This design keeps that direction: market/research services produce evidence, tracked-position services own user position state, risk-alert services evaluate holding rules, and notification services only render and send orchestration-approved action notifications or explicitly non-actionable watch summaries.

This change predates `harden-etf-alert-action-lifecycle`. The later lifecycle contract explicitly supersedes any reading of this design that treats relative reduction, recommendation/email state as execution, notification cooldown as action eligibility, or `take_profit_watch` as a reduce action. Historical implementations remain useful only as legacy/research diagnostics until the lifecycle cutover is complete.

## Goals / Non-Goals

**Goals:**
- Separate exit signals into insurance stops, profit-protection state, trend/market guards, and research-only evidence.
- Evaluate ETF exits on realistic tracked-position paths, not only "signal fired, did price fall later" statistics.
- Add protection guards for execution-origin reentry cooldown, notification repetition, repeated stop-loss, portfolio drawdown, low-profit ETF behavior, and market regime without conflating notification and action state.
- Keep candidate exit parameters research-only until explicitly approved.
- Make low-confidence, guard-only, stale-data, and research-only states explicit in API and UI output.

**Non-Goals:**
- No broker integration, automatic trading, or auto-sell execution.
- No automatic approval of optimized parameters.
- No external AI dependency for exit decisions.
- No change to ETF ranking score weights or buy-observation labels.
- No attempt to prove future profitability; validation remains historical research evidence.

## Decisions

### Decision: Treat hard stop as risk insurance

Hard stop evidence will be evaluated by maximum loss capped, tail drawdown improvement, and extreme-loss prevention rather than directional win rate alone.

Alternative considered: continue evaluating hard stop by future 1/3/5/10-day decline rate. This was rejected because it incorrectly penalizes a valid insurance rule during strong markets.

### Decision: Model trailing take-profit as a state machine

ETF trailing protection will use tracked-position state: entry price, current price, high-water profit, activation threshold, giveback threshold, threshold mode, and decision-eligible data source. Trailing logic only becomes actionable after profit activation.

When actionable, trailing protection returns a versioned absolute remaining-position target against an immutable exposure baseline. Repeated evaluations or a second rule requesting the same target reuse the same action cycle; they never multiply the current remaining position. `take_profit_watch` is not a trailing action: it remains `hold/100%/observation-only`.

Alternative considered: keep generating trailing events from historical price series without tracked-position state. This was rejected because it produces weak or zero samples and cannot reflect the user's actual holding path.

### Decision: Make trend weakening a guard before it is an exit

Trend weakening will become guard-only by default. It can block new adds, lower alert urgency, or request confirmation, but it should only become an actionable reduce/sell signal when combined with loss, material giveback, ranking deterioration, or market-regime confirmation.

Alternative considered: tune the moving-average thresholds. This was rejected because the recent evidence shows high false-exit rates from using trend weakening as a standalone sell signal.

### Decision: Add protection guards alongside per-position exits

Protection guards will evaluate cross-position and repeated-trade conditions: reentry cooldown after an owner-confirmed partial/full exit execution, repeated stop-loss guard, portfolio drawdown guard, low-profit ETF guard, and market-regime guard. A separate notification repeat cooldown may suppress or aggregate emails, but it cannot create, execute, cancel, or re-arm an action and cannot start or extend reentry cooldown. Guards can suppress or downgrade notifications and can pause new add reminders without changing the underlying ranking.

Alternative considered: only adjust per-ETF thresholds. This was rejected because mature systems separate risk overlays from entry/exit indicators.

### Decision: Separate action, execution, and notification facts

An eligible rule may create a `proposed` absolute-target action. SMTP acceptance, failure, retry, grouping, or suppression is a notification fact only. Action progress and reentry cooldown require an owner-confirmed partial/full execution fact with position updates; no mail or recommendation state may substitute for execution.

### Decision: Keep validation research-only until approval

Exit-policy validation will compare hold baseline, current default, candidate parameters, and guard-enabled policies. It will store confidence, sample count, rolling stability, false exit, missed upside, drawdown, alert count, and data coverage. Candidate parameters will remain `candidate` until manually approved.

Alternative considered: automatically apply the best hyperopt result. This was rejected because sample windows are limited and overfitting risk is high.

## Risks / Trade-offs

- Path-based validation needs realistic entry assumptions → Use active tracked-position history when available and explicit synthetic entry rules only when labeled as research simulation.
- Guard layers can suppress useful alerts → Store suppression reasons and expose them in UI so users can audit why no email was sent; suppression never changes action eligibility or execution state.
- More states can confuse users → Use concise Chinese labels: `保险止损`, `盈利保护`, `趋势警戒`, `组合保护`, `样本不足`, and keep observation ranking separate.
- Parameter search can overfit recent markets → Require holdout windows, rolling stability, minimum trade count, and no automatic approval.
- Existing API consumers may expect `trend_weakening` as actionable → Preserve field compatibility but add action class, eligibility, and guard-only flags.

## Migration Plan

1. Reconcile action/notification/cooldown semantics with `harden-etf-alert-action-lifecycle`; preserve old relative or email-derived behavior only in a legacy diagnostic adapter.
2. Add research-only validation structures using existing run/item JSON fields where possible.
3. Add tracked-position exit-state fields only if existing tracked-position JSON cannot safely persist high-water profit and threshold context.
4. Ship backend risk classification and validation first with no change to live email behavior.
5. Update `/short-term` and evidence UI to display new states and low-confidence wording.
6. Run validation on comprehensive Top50 and full eligible calibration batches.
7. Only after review, allow approved parameters to be read by live tracked-position evaluation; policy approval never converts notification history into execution facts.

Rollback is straightforward while the change is research-only: stop reading the new evidence fields and keep current default thresholds. If a migration is added for position state, rollback must preserve columns but ignore new fields rather than deleting user data.

## Open Questions

- Should synthetic validation entries use comprehensive TopN signal dates, user-tracked entry dates, or both as separate evidence groups?
- What manual approval UI is acceptable for promoting candidate exit parameters to approved live parameters?
- Should market-regime guards use only internal ETF breadth data first, or also external index/futures data once available?
