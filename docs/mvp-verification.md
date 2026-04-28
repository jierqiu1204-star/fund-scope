# FundScope MVP Verification Record

Date: 2026-04-28

This record closes the local release-readiness gaps for `add-fundscope-mvp` through automated tests, documented deployment checks, and generated portfolio evidence. It does not claim that a real VPS deployment was performed.

## Scope Audit

| MVP task | Evidence | Status |
| --- | --- | --- |
| 4.4 Data ingestion fallback tests | `backend/tests/test_fund_data.py`, `backend/tests/test_index_data.py` cover AKShare parser behavior plus fallback behavior with local fixtures/stubs. | Complete |
| 6.9 Portfolio spec coverage | `backend/tests/test_portfolio_api.py` covers buy/sell transactions, unknown funds, CSV success, CSV atomic rejection, holdings with latest NAV, stale NAV, value history, empty state source data, default seed, and daily snapshot idempotency. | Complete |
| 7.7 Valuation spec coverage | `backend/tests/test_valuation_api.py` and `backend/tests/test_index_data.py` cover default seeded indices, watchlist add/remove, successful daily valuation inserts, fallback valuation fetch, partial failure logging, percentile calculation, current API output, and historical API sorting. | Complete |
| 12.3 GitHub deployment secrets | `deploy/README.md` documents `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, where to configure them, and how to verify presence with `gh secret list`. | Documentation complete; private values remain external. |
| 13.1 Local full stack | `docker compose up -d --build` was attempted from `deploy/`. Startup was blocked by missing local `.env`; see Docker record below. | Blocker recorded with next action. |
| 13.2 Transaction to holdings to value history | Covered by `test_holdings_include_latest_nav_values_for_allocation_source_data`, `test_portfolio_value_history_aggregates_latest_snapshots`, and `test_daily_holdings_snapshot_is_idempotent_for_same_snapshot_date`. | Complete |
| 13.3 Daily valuation | Covered with fixture data by `test_daily_valuation_job_inserts_rows_for_watchlist_indices` and partial-failure coverage. No live CSIndex spot check was performed. | Complete by fixture evidence. |
| 13.4 Monthly DCA reminder | `backend/tests/test_reminders.py` covers all percentile bands and manual reminder payload/send behavior. | Complete |
| 13.5 Daily news fetch | `backend/tests/test_news_api.py` covers summarized news, title-only fallback, duplicate skipping, summary backfill, and LLM failure details. | Complete |
| 13.6 VPS deployment | `deploy/README.md` now has a VPS checklist for HTTPS, basic auth, health, nginx proxying, scheduler, and backup verification. | External; not locally complete. |
| 13.7 Portfolio screenshots | Generated screenshots are stored under `docs/assets/screenshots/`. | Complete |

## Watchlist Scope Decision

Valuation index watchlist add/remove remains in scope. The backend now exposes minimal API support:

- `POST /api/valuation/watchlist`
- `DELETE /api/valuation/watchlist/{index_code}`

The implementation uses the existing `indices.is_watchlist` field and retains existing `index_valuation_history` rows when an index is removed.

## Commands Run

Backend baseline before changes:

```text
uv run pytest
47 passed in 55.10s
```

Portfolio coverage after additions:

```text
uv run pytest tests/test_portfolio_api.py
12 passed in 24.48s
```

Valuation/watchlist/fallback coverage after additions:

```text
uv run pytest tests/test_valuation_api.py tests/test_index_data.py
9 passed in 14.26s
```

Data ingestion fallback coverage after additions:

```text
uv run pytest tests/test_fund_data.py tests/test_index_data.py
4 passed in 1.96s
```

Full final verification is recorded in the final OpenSpec apply output after all changes are complete.

## Docker Compose Record

Command:

```text
docker compose up -d --build
```

Working directory:

```text
deploy/
```

Output:

```text
time="2026-04-28T11:37:42+08:00" level=warning msg="The \"FQDN\" variable is not set. Defaulting to a blank string."
env file F:\Program\fund-scope\.env not found: GetFileAttributesEx F:\Program\fund-scope\.env: The system cannot find the file specified.
```

Next action:

1. Copy `.env.example` to `.env` and replace placeholders.
2. Export `FQDN=<local-or-public-hostname>`.
3. Generate `deploy/.htpasswd` with `deploy/create-htpasswd.sh`.
4. Re-run `docker compose up -d --build` from `deploy/`.

## Manual Workflow Evidence

- Transaction flow: automated tests create transactions, generate daily holdings snapshots from NAV history, and verify holdings/value-history output.
- Daily valuation: automated tests run the job with fixture provider data, verify rows inserted, and verify per-index failures are captured in job details.
- Monthly DCA: automated tests cover all five percentile amount bands and manual job email payload composition.
- Daily news: automated tests cover summary success, LLM raw fallback, duplicate skipping, and summary backfill.
- Recommendations compatibility: recommendation tests passed in the baseline run; final full test verification should be rerun after this change before archive.

## Screenshot Evidence

Generated with static frontend export output and mocked API data:

- `docs/assets/screenshots/portfolio.png`
- `docs/assets/screenshots/valuation.png`
- `docs/assets/screenshots/news.png`
- `docs/assets/screenshots/recommendations.png`
- `docs/assets/screenshots/sample-email.png`
- `docs/assets/screenshots/sample-email.html`

The screenshots use sample values and do not expose private account data.

## External Items

- GitHub Actions secrets must be created by a repository administrator.
- Real VPS deployment and scheduled-job observation require access to the production host and credentials.
- No private secret values are stored in this repository.
