## 1. Freeze Source And Proxy Contracts

- [x] 1.1 Add contract tests for the five source records, captured-content hashes, disclosure states, interpretation version, and explicit proprietary-signal non-equivalence.
- [x] 1.2 Implement the canonical `etf_leader_tactics_hypothesis_v1` source-to-proxy registry without bundling full article bodies.
- [x] 1.3 Add tests that accept exactly `leader_breakout_proxy_v1`, `former_leader_repair_proxy_v1`, and `cycle_routed_leader_proxy_v1` and reject every runtime formula, threshold, window, weight, Top N, or candidate change.
- [x] 1.4 Implement immutable leader candidate contracts and include source, formula, regime, baseline, split, cost, code, and holdout identities in the manifest hash.
- [x] 1.5 Add tests proving the separate leader experiment cannot alter or masquerade as the existing baseline/hysteresis/regime-liquidity ranking candidate registry.

## 2. Build Exact Point-In-Time Features

- [x] 2.1 Add table-driven tests for adjusted MA5/10/20 alignment, preceding-20-session adjusted-high breakout, 120-session volume maximum, sector percentile, peer-return percentile, and peer-turnover percentile.
- [x] 2.2 Implement `leader_breakout_proxy_v1` with exact gates, equal-weight score, deterministic percentile ties, finite checks, and ordered exclusion reasons.
- [x] 2.3 Add table-driven tests for prior peer leadership, 120-session adjusted-close drawdown, positive stabilization, ATR5/ATR20 compression, and exact symmetric `abs(adjusted_close-adjusted_MA20)/adjusted_ATR20`.
- [x] 2.4 Implement `former_leader_repair_proxy_v1` with the frozen 180-session prerequisite, gates, reverse percentiles, equal-weight score, finite checks, and ordered exclusions.
- [x] 2.5 Add and implement routing tests for `risk_on -> breakout`, `neutral -> repair`, and fail-closed `defensive`, `cash_wait`, stale, unavailable, or incompatible regimes.
- [x] 2.6 Add tests proving raw, stale, estimated, Sina/efinance, current-only taxonomy, late receipt, future membership, future adjustment, and future regime facts cannot populate any feature.
- [x] 2.7 Implement a PIT input adapter over immutable capture/replay artifacts with factual membership, adjusted provenance, taxonomy, regime, and source-cutoff validation.
- [x] 2.8 Add peer-count, clone-representative, tracked-index, issuer, theme, sector, history-tier, and concentration facts without importing production action or notification services.

## 3. Preserve Candidate-Specific Common Support

- [x] 3.1 Add failing tests showing the current joint candidate intersection is invalid for mutually exclusive breakout and repair modes.
- [x] 3.2 Introduce complete candidate observations that distinguish unavailable inputs from observed gate failures and selectable scores.
- [x] 3.3 Implement candidate-versus-baseline common-support panels per candidate while retaining the existing helper as a compatibility wrapper for current callers.
- [x] 3.4 Add tests proving a gate-failed ETF stays in complete baseline support but cannot enter that candidate cohort, while a missing PIT input is excluded only with its exact reason.
- [x] 3.5 Add tests and implementation for `insufficient_candidate_cohort` when fewer than ten qualified non-clone ETFs exist, with no padding or candidate fallback.
- [x] 3.6 Verify batch-size changes, interruption, and resume produce identical candidate observations, cohort order, sample hashes, and exclusions.

## 4. Connect Existing Outcomes, Diagnostics, And Validation

- [x] 4.1 Register the leader family through the existing factor experiment contract with the frozen Top10 five-session paired net-excess endpoint and 5 bps fee plus 5 bps slippage per side.
- [x] 4.2 Reuse existing forward adjusted outcomes and produce candidate-specific primary and exploratory Top5/10/20 and 1/3/5/10-session results without exposing outcomes to feature construction.
- [x] 4.3 Extend diagnostics with signal frequency, qualifying count, complete Top10 dates, history and peer coverage, clone removals, turnover, churn, cost drag, drawdown, and sector/theme/issuer concentration.
- [x] 4.4 Add residual and marginal diagnostics against existing momentum, sector trend, risk, liquidity, and overextension factors, including fold, regime, peer-group, history-tier, and ETF contribution slices.
- [x] 4.5 Apply Holm correction across the one-to-three candidate primary p-values only after candidate-level block-bootstrap results exist.
- [x] 4.6 Enforce existing 252-session, 40-independent-date, three-fold, coverage, purge/embargo, uncertainty, turnover, drawdown, concentration, clone, finite-value, raw-price, and one-time holdout gates.
- [x] 4.7 Add tests proving exploratory horizons, hit rate, absolute return, directional accuracy, or a favorable pooled estimate cannot replace a failed primary or stability gate.
- [x] 4.8 Emit only `insufficient_data`, `unconfirmed`, `rejected`, or `eligible_for_v4_proposal`, with production mutation always false.

## 5. Add MA5 Lifecycle Diagnostics Without Live Effects

- [x] 5.1 Add tests for next-close entry, close-below-same-session-adjusted-MA5 observation, next-eligible-close exit, non-zero costs, missing future price, and a maximum ten-session comparison window.
- [x] 5.2 Implement `ma5_exit_proxy_v1` as an exploratory adapter over sealed leader entry samples and compare it with frozen five- and ten-session holds.
- [x] 5.3 Mark source-described intraday T-trading unavailable unless a separate executable PIT model exists; never infer intraday fills from daily OHLCV.
- [x] 5.4 Add side-effect tests proving MA5 evidence cannot modify `etf_exit_action_v3`, production positions, alerts, notification records, or SMTP state.

## 6. Persist And Continue Within Resource Bounds

- [x] 6.1 Add a migration and model tests for nullable indexed `experiment_family` and `hypothesis_registry_hash` on ETF factor evidence, preserving all existing rows.
- [x] 6.2 Extend immutable evidence serialization with source/proxy registry, formula, cutoff, input, feature, regime, coverage, exclusion, diagnostic, uncertainty, holdout, and provenance fields.
- [x] 6.3 Extend checkpoint hashes and cursors for candidate/date feature pages, outcome pages, diagnostics, and MA5 policy phase without duplicating completed artifacts.
- [x] 6.4 Implement a workflow continuation using one worker, pages of at most 20 ETFs, bounded memory, exclusive lease, and a hard return within 55 seconds.
- [x] 6.5 Add timeout, lease-conflict, crash-resume, idempotency, different-batch-size, and reproducible-error-summary tests.
- [x] 6.6 Add a disabled-by-default admin continuation entry that advances one page, never calls a live provider, and reports compact progress and resource telemetry.

## 7. Expose Honest Research Evidence

- [x] 7.1 Add read-only API schemas and a latest leader-evidence endpoint with stable unavailable reasons, independent ranking/policy/notification/execution provenance, and no score recomputation.
- [x] 7.2 Add API tests for missing registry, missing PIT input, sparse cohort, pending outcomes, insufficient dates, incompatible evidence, failed gates, and proposal-eligible evidence.
- [x] 7.3 Add frontend types and a separate `龙头战术透明代理（研究）` panel on `/short-term/evidence`.
- [x] 7.4 Show source links, proxy formulas, non-equivalence, primary versus exploratory labels, coverage, exclusions, residual overlap, concentration, regime stability, holdout state, and MA5 policy evidence.
- [x] 7.5 Add static and component tests proving the panel cannot appear in production ranking selectors or claim original-signal, live-email, provider-delivery, confirmed-execution, guaranteed-return, or author-endorsement status.

## 8. Focused Verification And Controlled Rollout

- [x] 8.1 Run source/contract, formula, PIT-cutoff, candidate-support, diagnostics, validation, MA5, persistence, and continuation test groups in bounded commands with hard timeouts below 60 seconds.
- [x] 8.2 Run evidence API and frontend focused tests plus TypeScript checks in bounded commands with hard timeouts below 60 seconds.
- [x] 8.3 Run `uv run pytest tests/test_backend_domain_boundaries.py` with a hard timeout below 60 seconds and fix any dependency-direction violation.
- [x] 8.4 Run Ruff on changed backend and test files in bounded groups, then run repository Ruff only with explicit timeout and process-status handling.
- [x] 8.5 Run strict OpenSpec validation for this change and the modified canonical specifications.
- [x] 8.6 Deploy nullable persistence and read-only code with continuation and scheduling disabled, recording artifact version, migration state, feature flags, and rollback commands.
- [x] 8.7 Run at most one bounded development continuation from real immutable production PIT artifacts when prerequisites exist; otherwise record the stable factual `insufficient_data` reason.
- [x] 8.8 Verify production ranking, allocation, tracked positions, risk alerts, notification logs, SMTP state, current candidate registry, and holdout state are unchanged.
- [x] 8.9 Leave automatic scheduling and holdout consumption disabled until non-holdout evidence is complete and manually reviewed; require a separate OpenSpec change for any V4 promotion.
