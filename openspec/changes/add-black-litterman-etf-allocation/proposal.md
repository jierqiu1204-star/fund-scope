## Why

Current ETF allocation already has rule-based, minimum-volatility, and risk-parity style outputs, but it still lacks a formal way to combine market-implied prior weights with FundScope's published ranking views and data confidence. Label validation, backtest, and healthcheck evidence remain display-only and cannot become target-weight inputs.

## What Changes

- Add a Black-Litterman ETF allocation method as a research-only comparison inside the ETF allocation layer.
- Build market prior weights from reliable ETF proxies such as AUM, liquidity, or deterministic fallback priors when AUM is unavailable.
- Convert FundScope views into Black-Litterman inputs:
  - short-term score and label state become directional views.
  - data reliability and market-input coverage become confidence; label validation and strategy healthcheck evidence remain display-only.
  - stale, estimated, unavailable, or display-only data receives zero decision confidence.
- Apply existing FundScope portfolio constraints:
  - long-only weights.
  - single ETF maximum weight of 30%.
  - theme/industry concentration limits.
  - liquidity and data reliability eligibility filters.
- Show Black-Litterman output as a comparison against current rule allocation, minimum-volatility, and risk-parity outputs.
- Add backtest/evidence fields so the UI can say whether Black-Litterman performed better, worse, or insufficiently tested on current data.
- Do not use Black-Litterman to send emails, auto-trade, override tracking positions, or silently replace the default ETF funding reference.

## Capabilities

### New Capabilities
- `black-litterman-etf-allocation`: Generates a research-only ETF allocation comparison using market priors, FundScope views, confidence, covariance, and existing portfolio constraints.

### Modified Capabilities

## Impact

- Backend portfolio allocation service gains a new Black-Litterman method behind the ETF optimized allocation flow.
- API responses may be extended with Black-Litterman method results, priors, views, confidence, constraints, and evidence metadata.
- Frontend `/short-term` may display a new "Black-Litterman 对照" block inside strategy evidence or optimized allocation sections.
- Database may need fields or JSON payload extensions for method metadata; no breaking enum or URL changes.
- No changes to alert thresholds, email sending, real trading, user tracking edits, or market data ingestion.
