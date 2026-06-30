## Context

FundScope's short-term ETF labels and holding alerts have already moved beyond a static recommendation page, but several key thresholds remain fixed. Fixed values such as "today涨幅超过 2.5% 是冲高", "60日涨幅超过 45% 是追高", or "盈利回吐 2.5 个百分点触发移动止盈" can be reasonable for some ETFs and wrong for others.

Mature trading/research systems typically separate signal logic from threshold calibration. Freqtrade custom stoploss/custom ROI, Backtrader trailing stops, vectorbt stop arrays, and QuantConnect portfolio/risk models all support symbol-specific or volatility-aware rules. FundScope should follow that direction while keeping deterministic, auditable, no-auto-trading behavior.

## Goals / Non-Goals

**Goals:**
- Replace fixed-only ETF label thresholds with dynamic thresholds based on each ETF's own behavior.
- Use asset type, recent volatility, historical percentiles, theme/sector peer context, premium/discount state, and tracked-position state as threshold inputs.
- Keep labels beginner-readable while exposing the underlying threshold context.
- Make high-premium cross-border ETFs visibly risk-gated.
- Reuse dynamic threshold context in short-term labels, tracked-position alerts, and portfolio eligibility.
- Keep AI limited to explanation; deterministic rules decide thresholds and signals.

**Non-Goals:**
- No machine-learning model training.
- No automatic buying or selling.
- No broker execution or account sync.
- No removal of existing label names.
- No change to data freshness rules: stale, estimated, or display-only data cannot trigger email.
- No full rewrite of the ranking engine.

## Decisions

### 1. Add a dynamic threshold context object

Every ETF threshold evaluation should produce a structured context:
- `rule_version`
- `asset_type`
- `asset_bucket`
- `theme_group`
- `volatility_unit_pct`
- `volatility_source`
- `atr14_pct`
- `realized_vol_20d_pct`
- `median_abs_return_60d_pct`
- `return_percentiles`
- `theme_percentile_context`
- `premium_discount_pct`
- `premium_state`
- `holding_state`
- `thresholds`
- `decision_eligible`
- `ineligible_reason`

Rationale: the page and alert emails need to explain why a threshold was tight or wide. A context object also makes historical replay possible.

Alternative considered: directly replace fixed constants inline. Rejected because it would be hard to audit, test, and explain.

### 2. Compute volatility unit from multiple robust inputs

The dynamic engine will calculate:
- ATR-style range over recent daily data where high/low are available.
- 20-day realized volatility from daily returns.
- 60-day median absolute daily return to reduce outlier sensitivity.
- Recent drawdown and normal pullback ranges.

The final `volatility_unit_pct` should be clamped within safety bounds per asset bucket. Example: a bond ETF should not receive the same "normal move" band as a semiconductor ETF.

Rationale: one volatility measure can be noisy. Combining range, realized volatility, and median absolute return is more stable for public ETF data.

Alternative considered: use only ATR. Rejected because some ETF daily data may have weak high/low quality, and range can be distorted by bad prints.

### 3. Use own-history percentiles before peer percentiles

The first comparison should be against the ETF's own recent history. Peer/theme percentile is a secondary context:
- Own 1-day return percentile: "today's move vs this ETF's normal moves".
- Own 20/60-day return percentile: "recent momentum vs this ETF's own history".
- Theme peer percentile: "this ETF vs similar ETFs when theme data exists".

Rationale: a volatile semiconductor ETF and a low-volatility bank ETF should not share the same daily move threshold.

Alternative considered: only compare within theme. Rejected because theme coverage may be incomplete and themes can contain heterogeneous ETFs.

### 4. Treat premium/discount as a hard risk gate where available

For ETFs where IOPV or premium/discount can be trusted, premium risk influences entry timing and portfolio eligibility:
- Mild premium: web warning.
- High premium: force `高溢价别追` or equivalent entry avoidance.
- Extreme premium: exclude from observation portfolio weight.
- Missing IOPV for cross-border ETFs: show structure-data limitation and avoid claiming premium safety.

Rationale: a high-scoring ETF with a large premium can still be a bad short-term entry. This is especially important for cross-border ETFs.

### 5. Keep holding-state thresholds separate from buy observation labels

Buy observation labels answer "is this ETF worth observing now?" Tracked-position thresholds answer "what should I watch for my own entry price and profit state?" The dynamic threshold engine can serve both, but it must include `holding_state` only when evaluating a tracked position.

Rationale: the same ETF can be `短线观察` for the market while a user's held position triggers `移动止盈` because of entry price and profit giveback.

### 6. Preserve deterministic safety over AI flexibility

AI may explain dynamic threshold outputs, but it must not generate threshold values or override rule decisions. If AI fails or uses prohibited wording, the UI uses rule explanation and marks it as such.

Rationale: financial signals need reproducibility and auditability.

## Risks / Trade-offs

- [Risk] Dynamic thresholds become too complex for users. → Mitigation: UI shows simple labels first, then "为什么" with 3-5 concrete metrics.
- [Risk] Not enough history for a new ETF. → Mitigation: use conservative defaults or `数据不足`, and mark threshold mode clearly.
- [Risk] Theme data is missing before sector taxonomy lands. → Mitigation: dynamic thresholds work with own-history first and use peer context only when available.
- [Risk] Premium data is unavailable or stale. → Mitigation: never mark premium as safe without data; show `结构数据不足`.
- [Risk] Overfitting thresholds to recent history. → Mitigation: clamp thresholds with safety bounds and validate through historical label replay.
- [Risk] Existing tests assume fixed thresholds. → Mitigation: add explicit fixture data for high-volatility, low-volatility, high-premium, and held-position cases.

## Migration Plan

1. Add the dynamic threshold module and unit tests without changing production labels.
2. Wire short-term ETF label calculation to read threshold context behind a rule version.
3. Expose dynamic threshold context in ETF detail and cached signal item metadata.
4. Wire tracked-position thresholds to the same engine for ETF assets.
5. Wire observation portfolio eligibility to threshold context and premium gates.
6. Run historical replay to compare fixed-label and dynamic-label outcomes.
7. Deploy with additive response fields and no destructive schema changes.

Rollback: if dynamic labels behave poorly, switch the rule version back to fixed thresholds while keeping the additive context fields unused.

## Open Questions

- Exact premium caps should likely differ by ETF type. The first implementation should start conservative and expose the constants in code.
- Theme peer percentile will be strongest after `add-etf-sector-theme-tags` lands; before that it can use existing theme labels with lower confidence.
- The UI needs a compact wording for "today is X times this ETF's normal daily move" that is understandable for financial beginners.
