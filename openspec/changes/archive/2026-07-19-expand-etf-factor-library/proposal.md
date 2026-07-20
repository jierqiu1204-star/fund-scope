## Why

FundScope ETF scoring now has technical score, sector trend, and limited theme catalyst support, but it still lacks a complete factor layer for flows, liquidity, overheat, ETF structure, valuation, macro style, breadth, and data-source availability. Without a formal factor library, new factors can easily become ad hoc score tweaks, misleading fallbacks, or duplicated risk logic.

## What Changes

- Introduce an ETF factor library that defines each factor's source, calculation window, score direction, availability state, reliability, and whether it contributes to scoring, risk gating, or display-only context.
- Add factor groups for:
  - price momentum
  - reversal and overheat
  - volatility and drawdown risk
  - liquidity
  - sector trend
  - theme and event catalysts
  - fund flow
  - sentiment heat
  - constituent breadth and quality
  - valuation
  - macro and style regime
  - ETF structure
- Upgrade comprehensive ETF scoring from a small fixed blend into a versioned factor profile that can reweight across available real factors and return `null` for unavailable factors.
- Preserve strict fallback behavior: missing, stale, estimated, or seed-only data is labeled unavailable or display-only and does not masquerade as a real score.
- Keep risk factors as gates or penalties where appropriate. Overheat, poor liquidity, stale data, abnormal premium/discount, high drawdown, and insufficient history cannot be erased by strong theme or momentum scores.
- Expose factor breakdown, weighting profile, missing-factor reasons, and score version in the latest ETF signal run cache, API responses, and `/short-term` UI.
- Add tests proving factor scores, missing states, sorting, detail page consistency, and risk gates behave deterministically.
- Do not add automated trading, buy/sell instructions, target prices, or guaranteed-return language.

## Capabilities

### New Capabilities

- `etf-factor-library`: Defines ETF factor taxonomy, factor availability, reliability, scoring direction, factor profile weighting, risk gates, and API-ready factor breakdowns.

### Modified Capabilities

- `short-etf-research`: ETF signal generation and presentation must consume the versioned factor library, cache factor breakdowns in the latest signal run, expose missing factors as `null`, and sort comprehensive scores without neutral fallback values.

## Impact

- Backend:
  - Add a factor layer under the short research boundary, for example `app.services.short_research.factors`, without moving scoring into market data, portfolio allocation, tracking, risk alerts, or notification services.
  - Compute factor outputs from existing ETF daily data, ETF metadata, theme classification, signal-run metrics, and only verified external datasets when available.
  - Store factor breakdowns and score profile metadata in signal item JSON so ranking and detail pages use the same cached source.
- API:
  - Extend ETF ranking/detail responses with factor profile, factor breakdown, unavailable reasons, risk gates, and score version.
  - Keep unavailable factors as `null`, not `50`, `60`, zero, or stale placeholders.
- Frontend:
  - Show compact factor groups, score profile, and missing-data reasons in `/short-term`.
  - Keep Chinese research wording and separate “综合关注” from entry timing and risk labels.
- Tests:
  - Add focused factor calculation tests, profile weighting tests, risk-gate tests, API serialization tests, sort tests, and frontend missing-state tests.
- Operations:
  - No new production data source is treated as scoring-eligible until its freshness, reliability, and fallback behavior are explicitly validated.
