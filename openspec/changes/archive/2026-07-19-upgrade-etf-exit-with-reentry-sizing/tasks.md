> **Lifecycle reconciliation:** Checked tasks below record the legacy implementation completed by this change. They MUST NOT be used to restore relative `current * fraction` reductions, treat a proposed/sent recommendation as execution, start reentry cooldown without an owner-confirmed partial/full execution fact, let notification cooldown decide action eligibility, or map `take_profit_watch` to trim/reduce. Current production remediation and acceptance are owned by `harden-etf-alert-action-lifecycle`; legacy behavior remains diagnostic/research-only until that change cuts over.

## 1. Boundary And Data Contract

- [x] 1.1 Add an exit V2 contract version constant for position actions, reentry rules, bucket thresholds, and evidence output.
- [x] 1.2 Define typed payload helpers for `position_action`, `action_class`, `reentry_state`, `bucket_threshold_source`, and `evidence_status`.
- [x] 1.3 Ensure the new helpers live below `risk_alerts` or research evidence layers and do not import notifier or API modules.
- [x] 1.4 Add domain-boundary tests proving exit V2 helpers do not violate the backend dependency direction.

## 2. Position Action Engine

- [x] 2.1 Implement deterministic mapping from existing ETF signals to actions: `hold`, `no_add`, `trim`, `reduce`, `exit`, and `reentry_candidate`.
- [x] 2.2 Change unconfirmed `trend_weakening` to guard-only `no_add` unless loss, giveback, ranking deterioration, or market-regime confirmation is present.
- [x] 2.3 Add sizing logic for trim/reduce/exit actions using user ETF capital, current market value, target exposure, and single-ETF cap.
- [x] 2.4 Ensure action output is included in tracked-position API responses without changing existing field names.
- [x] 2.5 Add tests for hard stop, trailing take-profit, trend guard, take-profit watch, and exit-watch action mapping.

## 3. Reentry State Machine

- [x] 3.1 Persist or derive the latest reduce/exit action context, including action time, trigger signal, reference price, cooldown end, and reentry rule version.
- [x] 3.2 Implement reentry eligibility checks using current ranking bucket, entry timing label, theme trend, data reliability, and cooldown state.
- [x] 3.3 Expose reentry candidate state on tracked-position snapshots without creating real buy records or sending buy emails.
- [x] 3.4 Add tests for reentry allowed, reentry blocked by cooldown, reentry blocked by weak ranking, and reentry blocked by ineligible data.

## 4. Bucket-Specific Thresholds

- [x] 4.1 Create threshold buckets by ETF asset bucket, theme group, volatility percentile, current profit state, and data reliability.
- [x] 4.2 Add bucket threshold candidate generation for hard stop, profit start, trailing giveback, take-profit watch, and trend confirmation.
- [x] 4.3 Mark bucket thresholds as insufficient evidence when sample counts or intraday coverage are too low.
- [x] 4.4 Keep current live defaults unless a bucket parameter set is explicitly approved and contract-matched.
- [x] 4.5 Add tests for high-volatility theme ETF, low-volatility defensive ETF, insufficient bucket evidence, and approved-parameter fallback.

## 5. Backtest And Evidence

- [x] 5.1 Extend ETF portfolio backtest to compare TopN fixed hold, current live exit rules, guard-only behavior, and exit V2 with reentry.
- [x] 5.2 Add exit-quality metrics: missed upside, protection success, false exit count, reentry count, average time out of market, turnover, alert count, and drawdown improvement.
- [x] 5.3 Ensure intraday replay uses decision-eligible intraday data and never fills missing execution with daily close.
- [x] 5.4 Store exit V2 evidence with signal contract hash, exit action version, reentry version, bucket-threshold version, execution model, and data cutoff.
- [x] 5.5 Add tests showing V2 is not promoted when TopN fixed hold has better return without worse drawdown.

## 6. Frontend Display

- [x] 6.1 Update tracked-position cards to show action label, sizing amount, reentry state, and concise reason.
- [x] 6.2 Update strategy evidence page to compare TopN fixed hold, current exit rules, guard-only, and exit V2 in one table.
- [x] 6.3 Show missed upside and protection success separately so users can see whether exits are selling too early.
- [x] 6.4 Mark research-only, old-contract, insufficient-sample, and unapproved evidence clearly.
- [x] 6.5 Keep UI wording conservative: no “自动卖出”, no “保证收益”, no AI-made trading instruction.

## 7. Jobs And Operations

- [x] 7.1 Add or extend an admin job to run exit V2 validation for Top 5/10/20/50 and selected theme buckets.
- [x] 7.2 Make job output include selected universe, coverage funnel, samples, candidate count, rejected count, and run ids.
- [x] 7.3 Ensure research jobs do not create notification logs, real alerts, tracked-position mutations, or approved live parameters.
- [x] 7.4 Document recommended server validation order: update ETF data, generate ranking, run exit V2 evidence, then review results.

## 8. Validation

- [x] 8.1 Run targeted backend tests for tracked positions, risk alerts, ETF exit hyperopt, portfolio backtest, and evidence contracts.
- [x] 8.2 Run `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 8.3 Run `uv run ruff check .`.
- [x] 8.4 Run frontend type checking using the project-approved non-hanging command path.
- [ ] 8.5 On server, run Top 5/10/20/50 exit V2 validation and compare against TopN fixed hold.
- [ ] 8.6 Summarize whether exit V2 improves risk-adjusted results enough to remain research-only or become a candidate for manual approval.

## 9. Alert Action Lifecycle Reconciliation

- [x] 9.1 Reconcile this change's proposal, design, and specs with immutable exposure baselines, absolute targets, execution-origin cooldown, notification/action separation, and `take_profit_watch=hold`.
- [ ] 9.2 Before archiving or promoting this change, complete the implementation remediation and cutover in `harden-etf-alert-action-lifecycle`; do not satisfy this item with legacy relative-action tests or historical email evidence.
