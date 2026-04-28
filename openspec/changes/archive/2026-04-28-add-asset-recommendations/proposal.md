## Why

FundScope can currently track fund holdings, monitor index valuations, and adjust monthly DCA amounts, but it does not help the user discover which funds or stocks should enter the watchlist next. The next useful step is an explainable recommendation layer that ranks fund candidates and stock watchlist candidates without turning the product into an opaque investment-advice or trading system.

## What Changes

- Add an explainable asset recommendation capability with two initial surfaces:
  - fund candidate recommendations for watchlist expansion and portfolio fit
  - stock watchlist candidate scoring for manual research
- Add recommendation runs and recommendation items so each generated ranking is auditable, reproducible, and tied to a timestamped data snapshot.
- Add rule-based scoring for funds and stocks using transparent metric groups such as valuation, quality, risk, liquidity, fees, portfolio fit, and warning flags.
- Add API endpoints and frontend views for browsing recommendation runs, inspecting ranked candidates, and seeing metric-level rationales.
- Add admin job support to generate recommendations manually and on a schedule.
- Keep recommendations advisory in wording and behavior:
  - no automatic trades
  - no buy/sell/target-price wording
  - no expected-return forecasts
  - no LLM-driven ranking
- Allow the LLM only to summarize already-computed scoring rationales into concise Chinese explanations.

## Capabilities

### New Capabilities

- `asset-recommendations`: Generate explainable fund candidate recommendations and stock watchlist candidate scores from deterministic metrics, portfolio context, and risk flags.

### Modified Capabilities

- None.

## Impact

- **Backend**: new SQLAlchemy models, Alembic migration, Pydantic schemas, recommendation scoring services, repository queries, API routes, and scheduled/admin job integration.
- **Frontend**: new recommendation views with tabs for fund candidates and stock watchlist candidates, detail panels for rationales, and empty/error/loading states.
- **Data**: new tables for recommendation profiles, recommendation runs, recommendation items, fund metrics, stock universe records, stock fundamentals, stock prices, and stock metrics.
- **External sources**: fund metrics can reuse existing NAV and fund metadata sources; stock metrics require an additional market/fundamental data ingestion path, preferably through AKShare-compatible sources for the MVP.
- **Safety**: recommendation copy and API fields must make the output a screening aid, not personalized financial advice or execution guidance.
- **Testing**: deterministic scoring tests, no-future-data-leakage tests, API contract tests, and frontend rendering tests for empty and populated recommendation states.
