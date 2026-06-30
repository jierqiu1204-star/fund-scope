## Why

FundScope currently uses several fixed thresholds for ETF labels and entry timing, such as fixed daily涨跌、20/60日涨幅、均线距离 and profit-giveback lines. This is too crude because a semiconductor ETF, bond ETF, bank ETF, cross-border ETF, and gold ETF have different normal volatility, premium behavior, liquidity, and holding risk.

This change introduces dynamic ETF thresholds based on asset type, recent volatility, theme/sector group, historical percentile, premium/discount context, and user holding state so labels and alerts are more explainable and less one-size-fits-all.

## What Changes

- Add a deterministic dynamic threshold engine for ETF research and tracked positions.
- Compute per-ETF volatility units from recent ATR-style range, realized volatility, median absolute returns, and drawdown behavior.
- Compare today's move and recent returns against the ETF's own historical percentiles and, when available, its normalized theme/sector peers.
- Adjust `冲高别追`, `健康回踩`, `趋势延续`, `跌破等待`, and `放量转弱` using dynamic thresholds instead of fixed-only cutoffs.
- Add cross-border ETF premium/discount gating so high premium can force `高溢价别追` or exclude portfolio weight.
- Adjust tracked-position hard stop, take-profit watch, trailing take-profit, and trend weakening thresholds using volatility, profit state, and position state.
- Expose threshold context in API/UI: volatility unit, percentile source, theme peer context, premium state, holding state, rule version, and reason.
- Use dynamic threshold context in observation portfolio eligibility and exclusion explanations.
- Keep AI as explanation only. AI must not decide thresholds or override deterministic rules.
- No breaking API changes. New fields are additive and old labels remain compatible.

## Capabilities

### New Capabilities
- `dynamic-etf-thresholds`: Defines dynamic ETF threshold calculation, threshold context, rule versioning, and data eligibility rules.

### Modified Capabilities
- `short-term-research`: ETF labels, entry timing, detail explanations, and validation evidence use dynamic threshold context.
- `tracked-position-exit-strategy`: ETF holding alerts use dynamic thresholds based on volatility, profit state, and position state.
- `etf-observation-portfolio-optimization`: Portfolio eligibility and exclusion reasons consume dynamic threshold context, premium risk, and theme-relative overextension.

## Impact

- Backend:
  - New dynamic threshold service/module.
  - Short research scoring/entry timing logic.
  - Tracked position alert and threshold context.
  - Observation portfolio eligibility/exclusion.
  - Tests for high-volatility, low-volatility, high-premium, missing-data, and held-position cases.
- Frontend:
  - `/short-term` ETF card/detail wording and evidence display.
  - Tracked holding threshold explanation and no-alert reason.
  - Portfolio exclusion explanations.
- Database:
  - Prefer storing dynamic threshold outputs in existing metrics/rationale/alert context JSON.
  - Additive schema only if required for replay/audit; no destructive migration.
- Operations:
  - Existing data sync, real-time quote, tracking, and email schedules remain unchanged.
  - Rule version must be visible for audit and replay.
