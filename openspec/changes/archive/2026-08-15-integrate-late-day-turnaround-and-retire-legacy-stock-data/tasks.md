## 1. Research schema and contracts

- [x] 1.1 Add additive late-day run, observation, A-share intraday fact, and capture-checkpoint schema with immutable identities and bounded indexes
- [x] 1.2 Add ORM/read contracts and stable unavailable reasons without importing ranking or production-action writers
- [x] 1.3 Add migration tests for fresh install, upgrade, idempotent uniqueness, and downgrade of the additive schema

## 2. Point-in-time adapters and storage

- [x] 2.1 Implement the ETF adapter over prior-session ETF history and causally visible intraday quotes, producing only closed ten-minute bars
- [x] 2.2 Implement the A-share adapter over authoritative adjusted daily facts and normalized intraday ten-minute facts
- [x] 2.3 Implement bounded run/observation persistence and latest-compatible-manifest reads with idempotent hashes
- [x] 2.4 Implement a separately labeled A-share daily proxy watchlist that can never become a formal candidate

## 3. Bounded capture and materialization

- [x] 3.1 Implement a guarded Eastmoney five-minute A-share provider adapter with response caps, timestamp validation, bounded concurrency, and deterministic ten-minute aggregation
- [x] 3.2 Implement checkpointed A-share capture and independent ETF/A-share materializers with 52-second work and 55-second hard limits
- [x] 3.3 Register 14:30, 14:40, and 14:50 Shanghai scheduler jobs behind separate feature flags and per-universe locks
- [x] 3.4 Persist partial/unavailable evidence and prohibit candidate publication when declared coverage is incomplete

## 4. Read API and frontend research surface

- [x] 4.1 Add read-only contract, readiness, and paginated candidate endpoints with universe and cutoff validation
- [x] 4.2 Add a frontend contract and independent late-day-turnaround panel with ETF/A-share switching, formal/proxy separation, coverage, exclusions, and provenance
- [x] 4.3 Add navigation and deployment feature flags while retaining an actionable disabled/unavailable state

## 5. Legacy stock price retirement

- [x] 5.1 Migrate stock recommendation price metrics to bounded compatible reads from `ashare_adjusted_price_facts`
- [x] 5.2 Remove synthetic stock price seeding and update recommendation data-quality provenance and tests
- [x] 5.3 Remove all runtime/model/test references to `StockPriceHistory`, then add a migration that drops only `stock_price_history` and recreates its schema on downgrade
- [x] 5.4 Add a repository reference audit proving the retained stock tables still have consumers and no deleted-table consumer remains

## 6. Isolation, reliability, and acceptance

- [x] 6.1 Add PIT cutoff, future-bar rejection, price-basis normalization, closed-bar, duplicate, non-finite, and incomplete-coverage tests
- [x] 6.2 Add scheduler timeout, checkpoint resume, idempotency, memory/input cap, API no-provider-work, and frontend boundary tests
- [x] 6.3 Add domain-boundary tests proving no comprehensive-ranking, holdings, alert, SMTP, transaction, or execution mutation path is reachable
- [x] 6.4 Run focused tests, Ruff, migration tests, frontend checks, repository reference audit, and strict OpenSpec validation with every command bounded to at most 60 seconds
