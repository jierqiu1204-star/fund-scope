## Context

FundScope already has portfolio tracking, fund recommendations, strategy lab backtests, strategy evaluations, paper portfolios, and a short ETF research tab. The current UI exposes too many concepts at the same level, while the user's actual workflow is narrower: look at funds and ETFs, focus on short-term opportunities from one week to several months, inspect simple charts, understand why something is ranked, and then manually decide what to do outside FundScope.

The existing short ETF module is the closest fit because it already has daily ETF price sync, deterministic ranking, risk tags, reviews, reliability checks, and paper simulation. Fund data is less rich for short-term trading because it mainly has daily NAV, not intraday prices or turnover. The new design should unify the presentation without pretending that off-exchange funds behave like exchange-traded ETFs.

## Goals / Non-Goals

**Goals:**

- Make `/short-term` the beginner-friendly main workflow for fund and ETF short-term research.
- Provide a unified ranking and detail view across funds and ETFs, with clear differences in available evidence.
- Expand the default research pool to about 200 assets while filtering out products unsuitable for short-term observation.
- Explain each label with data evidence, risk evidence, investment direction, and opposing view.
- Replace long-term 730-day language with short-term data sufficiency gates.
- Keep legacy strategy lab and short ETF APIs available for advanced use and compatibility.

**Non-Goals:**

- No Alipay account sync, broker connection, automatic real trading, target price, stop-loss instruction, or guaranteed return language.
- No intraday matching, high-frequency strategy, leverage, futures, options, or individual stock trading.
- No required LLM or API key for core ranking. AI explanation can remain optional and must not change deterministic scores.
- No full adoption of Qlib, vectorbt, TradingAgents, vn.py, or Backtrader as runtime dependencies in this change.

## Decisions

- **Add a unified short research layer instead of rewriting strategy lab.** The strategy lab remains useful for advanced backtests and paper portfolios, but a new `/short-term` page gives the user's primary workflow a clean entry point. Alternative considered: collapse the existing strategy lab tabs. That would be faster but would leave unrelated concepts in the same module.

- **Use deterministic scoring as the source of truth.** Rankings are based on recent trend, drawdown, volatility, liquidity where available, data freshness, and sample sufficiency. TradingAgents-style multi-role wording is used only as structured explanation. Alternative considered: LLM-driven ranking. That is less reproducible, needs keys, and is unsuitable as the primary financial signal.

- **Separate ETF and fund evidence while sharing UI shape.** ETF details can show close price, candle-like daily ranges, turnover, liquidity, and T+1 rule notes. Fund details show NAV, accumulated NAV, return windows, drawdown, theme/category, and redemption/confirmation cautions. Alternative considered: force one metric schema. That would hide important differences and mislead beginners.

- **Use short-term sample gates.** Under 20 trading days is data insufficient; 20-59 days is very short sample; 60-119 days is short sample; 120+ days can be observed; 250+ days gets stronger historical context. Alternative considered: keep 730 days. That fits long-term strategy validation but blocks useful short-term research and confuses the product goal.

- **Expand the universe conservatively.** Target about 200 assets, split across broad index, technology, semiconductor, AI, photovoltaic, power equipment, new energy, military, finance, medicine, consumption, gold, bond, cross-border, and selected fund equivalents. Alternative considered: 500+ assets. That increases data failures and noise before the ranking and explanation experience is stable.

## Risks / Trade-offs

- Public data sources can lag or fail -> Show latest data date, per-source status, stale warnings, and retry actions.
- Bigger asset pools can create noisy rankings -> Apply eligibility filters, sample gates, liquidity checks, and clear risk labels.
- Fund and ETF results may look comparable when they are not -> Display asset type, trading rule, data fields used, and beginner explanation in every detail view.
- Short-term historical checks can create overconfidence -> Use observation-only labels and require visible risk/opposing-view text for every ranked item.
- Existing UI has mojibake in some files -> Clean touched short-term-facing copy during implementation and avoid translating API fields or enum values.

## Migration Plan

- Add the new short-term research service and API without removing existing strategy lab or short ETF endpoints.
- Expand default asset seed data through a new idempotent migration or startup seeding path.
- Build `/short-term` and update navigation so normal users enter it first; keep `/strategy-lab` as an advanced link.
- Deploy backend migrations first, then frontend, then run web-triggered data sync and signal generation.
- Roll back by hiding `/short-term` from navigation while preserving existing endpoints and data tables.

## Open Questions

- None for v1. The default is a deterministic, public-data, observation-only short-term research homepage for funds and ETFs with about 200 assets.
