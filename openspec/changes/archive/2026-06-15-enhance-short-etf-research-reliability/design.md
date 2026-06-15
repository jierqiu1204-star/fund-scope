## Context

FundScope already has a short-term ETF research tab, daily ETF data sync, ranked observation signals, rule-first review, and a virtual paper portfolio. The current version is intentionally conservative, but it relies on one public data source and provides limited evidence about data quality and signal robustness. The user wants a more stable, cautious, and comprehensive research workflow for short-term ETF observation.

## Goals / Non-Goals

**Goals:**
- Improve ETF data reliability with provider fallback, per-ETF data health, and visible retry controls.
- Strengthen short-term signal risk evidence without hiding the transparent rule-based scoring.
- Add reliability evaluation so the user can judge whether signal rules survive historical replay, fee assumptions, and nearby parameter changes.
- Keep all outputs in research language that a finance beginner can understand.

**Non-Goals:**
- No broker integration,支付宝 integration, live order placement, or automatic real trading.
- No direct adoption of Qlib, TradingAgents, vn.py, Backtrader, or vectorbt as core runtime dependencies.
- No AI-generated buy/sell instruction, target price, or guaranteed return.
- No intraday matching engine in this change; the engine continues to use daily prices.

## Decisions

- **Use AKShare as primary data source and efinance as backup.** AKShare remains the existing integration and broad public-data layer; efinance is only used when a single ETF fetch fails or returns no usable rows. This avoids replacing the current pipeline while reducing single-source fragility.
- **Persist data health separately from price rows.** Price history remains the canonical series, while health metadata records provider, latest date, failure reason, and consecutive failure count. This keeps analytics queries clean and makes UI status easy to explain.
- **Keep scoring deterministic and review rule-first.** The system expands risk tags and review notes, but the signal score and rank stay traceable. Any LLM-style multi-role pattern is used only as a structured explanation model, not as an authority that changes scores.
- **Store evaluations separately from signal runs and paper portfolios.** Reliability checks are research evidence, not operational state. Evaluation results must not rewrite signal parameters, paper holdings, or historical orders.
- **Use sample-size gates.** If ETF data history is too short, the UI and API must mark the evaluation as insufficient rather than presenting strategy quality as proven.

## Risks / Trade-offs

- Public data sources can rate-limit or change response formats. → The sync job records provider-specific failures and continues other ETFs; tests cover adapter failure and fallback.
- Backup sources may disagree slightly on adjusted prices or fields. → Store provider metadata and use raw daily close/turnover consistently; show provider status in the UI.
- More risk tags can make the UI feel noisy. → Group tags into beginner-friendly categories and prioritize high-severity warnings near the ETF name.
- Historical evaluation can invite overconfidence. → Always show sample-size warnings, fee assumptions, and “research only” copy; never output buy/sell recommendations.
- Extra dependency increases build surface. → Add efinance as a narrow optional runtime dependency with adapter tests and graceful failure if unavailable.

## Migration Plan

- Add data-health and evaluation tables through Alembic without altering existing ETF price, signal, review, or paper tables.
- Backfill health rows lazily during the next ETF sync; no destructive migration is required.
- Deploy backend first, run migration, rebuild frontend, then run one ETF data sync and one reliability evaluation from the web/API.
- Rollback by disabling new admin/UI actions and leaving existing short ETF signal and paper endpoints intact.

## Open Questions

- None for v1. The implementation SHALL use AKShare primary, efinance backup, daily-price-only evaluation, and research-only language.
