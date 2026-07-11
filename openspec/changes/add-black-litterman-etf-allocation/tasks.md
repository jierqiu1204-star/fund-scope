## 1. Input Contract

- [x] 1.1 Add a Black-Litterman input data structure under the portfolio allocation layer for candidates, returns, priors, views, confidence, constraints, and evidence metadata.
- [x] 1.2 Add candidate eligibility mapping so stale, estimated, unavailable, or display-only data receives zero decision confidence or is excluded.
- [x] 1.3 Add covariance input preparation using ETF daily returns with explicit insufficient-history and invalid-covariance reasons.

## 2. Prior, Views, and Confidence

- [x] 2.1 Implement market prior construction with documented source order: AUM/fund size, liquidity proxy, deterministic fallback prior.
- [x] 2.2 Supersede validation/healthcheck inputs; structured views use the published ranking contract, current labels, market regime, and theme metadata only.
- [x] 2.3 Supersede evidence-driven confidence; runtime confidence uses data reliability and market-history coverage only.
- [x] 2.4 Ensure AI prose is not used as a mathematical view or confidence input.

## 3. Black-Litterman Allocation Engine

- [x] 3.1 Implement posterior expected return calculation with deterministic numeric behavior.
- [x] 3.2 Implement constrained long-only weight generation with single ETF 30% cap, theme cap, correlation/duplicate exposure controls, and liquidity eligibility.
- [x] 3.3 Return explicit unavailable output when covariance, candidates, confidence, or constraints are insufficient.
- [x] 3.4 Record excluded ETF reasons and method-level diagnostics.

## 4. Persistence and API Integration

- [x] 4.1 Extend optimized allocation snapshot payloads to include a `black_litterman` method result without breaking existing methods.
- [x] 4.2 Store prior source, view count, confidence summary, covariance window, constraints, excluded assets, and generated weights in existing JSON payloads or a compatible migration.
- [x] 4.3 Ensure Black-Litterman output is research-only and does not call tracked positions, risk alerts, notifier, or email code.
- [x] 4.4 Update admin/job result summaries to include Black-Litterman success, unavailable reason, and candidate count.

## 5. Frontend Display

- [x] 5.1 Add a compact `Black-Litterman 对照` block to `/short-term` optimized allocation or strategy evidence display.
- [x] 5.2 Show prior source, views/confidence summary, constraints, top weights, excluded reasons, and evidence status.
- [x] 5.3 Label the output as research comparison requiring manual judgment, not a trading instruction.
- [x] 5.4 Keep current primary ETF funding reference visible and do not silently replace it with Black-Litterman output.

## 6. Tests and Verification

- [x] 6.1 Add unit tests for prior construction, structured views, confidence scaling, and stale-data exclusion.
- [x] 6.2 Add unit tests for 30% single ETF cap, theme concentration cap, and unavailable covariance behavior.
- [x] 6.3 Add API/job tests proving existing optimized allocation methods remain compatible.
- [x] 6.4 Add frontend type coverage for Black-Litterman response fields.
- [x] 6.5 Run `uv run pytest tests/test_backend_domain_boundaries.py` and confirm portfolio allocation does not import tracked positions or notifier.
- [ ] 6.6 Run relevant backend tests, `uv run ruff check .`, `corepack pnpm exec tsc --noEmit`, and server-side static build/deployment verification if implementing.
