## Context

FundScope already has immutable factor manifests, point-in-time adjusted inputs, common-support forward outcomes, factor diagnostics, purged walk-forward validation, Holm-adjusted block bootstrap, one-time holdout authorization, bounded checkpoints, and research-only evidence persistence. The production ranking loop separately freezes baseline, hysteresis, and regime/liquidity candidates and must remain byte-for-byte compatible.

The source material provides useful hypotheses but not a complete algorithm. In particular, “起飞信号” is proprietary, while hot sector, core leader, strengthening, stabilization, and do-not-chase require interpretation. The tactics were also described for individual stocks, so ETF peer groups, clone products, lower volatility, and executable daily-close timing require explicit adaptation.

One shared factor-panel helper currently requires every candidate score to be finite on the same ETF row. That joint intersection is unsuitable for mutually exclusive breakout and repair modes and could produce an empty or badly selected sample. The correct unit is candidate-versus-baseline common support on the same eligible date, followed by multiplicity adjustment across candidate-level primary results.

## Goals / Non-Goals

**Goals:**

- Convert only disclosed concepts into deterministic, reviewable ETF proxy contracts.
- Reuse the existing factor and evidence infrastructure while preserving candidate-specific support.
- Measure whether the proxies add cost-adjusted information beyond current momentum, sector, risk, liquidity, and overextension factors.
- Make sparse signals, missing PIT taxonomy, clone concentration, and source limitations first-class evidence.
- Allow bounded manual continuation and later disabled-by-default scheduling on the 2-core/4-GB deployment.

**Non-Goals:**

- Reconstruct or claim to reproduce the proprietary “起飞信号”.
- Change `daily_reconstructable_v1`, `final_score_v3`, actionable ranking, allocation, tracked-position rules, `etf_exit_action_v3`, or email policy.
- Backfill historical ETF membership, taxonomy, receipt time, or adjusted data that was not factually available at the signal cutoff.
- Implement intraday T-trading from daily bars, scrape the source account in production, train a model, or search parameter grids.
- Treat favorable in-sample, exploratory-horizon, directional-accuracy, or MA5-exit results as ranking evidence.

## Decisions

### 1. Store a source-hypothesis registry, not copied article content

Add a canonical `etf_leader_tactics_hypothesis_v1` contract under `app.services.strategy_lab`. It contains article title, account, publication timestamp, source URL, normalized captured-content SHA-256, statement IDs, disclosure state, interpretation, proxy formula ID, and limitation text. The initial source registry covers the five locally reviewed methodology articles:

- `飞哥干货 - 龙头战法全解`
- `明天，就俩字！！`
- `接下来的思路！！`
- `被低估的机会！！`
- `直接公布！！`

Only source metadata and compact paraphrased claims are committed. The downloaded article corpus is not bundled into the application or deployment image. Source hash, interpretation version, and proxy registry hash all participate in the experiment manifest.

Alternative considered: copy article bodies into the repository. Rejected because runtime does not need them, it increases copyright and maintenance risk, and it still does not disclose the proprietary formula.

### 2. Model the three proxies as a separate experiment family

Create a `leader_tactics_shadow_v1` experiment family with the exact candidate IDs in the spec. It uses the current production research score only as the frozen baseline and does not modify `FrozenRankingCandidateRegistry`.

The candidate formulas are implemented as pure functions over a date-scoped `LeaderFeaturePanel`. Percentile ranks are in `[0, 1]`, use average ranks for ties, sort deterministically by ETF code after score ties, and are calculated only from factually eligible rows for that date and peer scope.

The formulas use adjusted OHLC for moving averages, breakout, drawdown, and ATR. Volume and turnover must come from the same decision-eligible adjusted-OHLCV record provenance; no quote, Sina/efinance fallback, estimate, current taxonomy, catalyst, notification, or outcome field is accepted. Adjusted ATR uses the existing arithmetic-mean true-range definition, and symmetric overextension reuses `abs(adjusted_close - adjusted_MA20) / adjusted_ATR20`.

Alternative considered: add a fixed bonus to `final_score_v3`. Rejected because a bonus magnitude would be arbitrary, would contaminate production before validation, and would make incremental attribution harder.

### 3. Separate complete observations from candidate qualification

Introduce a candidate observation with:

- complete input and provenance status;
- finite component values;
- `qualifies` boolean and ordered gate reasons;
- selectable score only when qualified;
- baseline score and adjusted outcomes independently available.

A failed tactical gate is an observed negative signal, not missing market data. It remains in date-level baseline support, but it is not selectable by that candidate. A truly unavailable input is excluded from that candidate's support. Candidate Top10 is built only from qualified rows; baseline Top10 is built from the same date's complete baseline universe. If a candidate has fewer than ten qualified non-clone ETFs, that candidate-date is excluded rather than padded with baseline ETFs or another proxy.

Extend the shared factor-panel infrastructure with `build_candidate_common_support_panels`, returning one panel per candidate. Each panel pairs that candidate with the baseline on identical signal dates and factual outcome availability. Holm correction is applied only after the one-to-three candidate primary p-values are produced. Keep the current joint-support helper as a compatibility wrapper until all existing callers and tests prove unchanged.

Alternative considered: intersect all three candidate score sets. Rejected because breakout and repair are intentionally mutually exclusive. Alternative considered: encode failed gates as zero scores. Rejected because it could silently fill a sparse Top10 with non-signals.

### 4. Reuse PIT replay inputs and fail closed on taxonomy

The workflow reads immutable `EtfPitCaptureSource` and existing replay input artifacts. It must use:

- membership recorded visible by T;
- total-return-adjusted OHLCV recorded visible by T;
- a taxonomy/peer mapping whose effective and receipt cutoffs are both compatible with T;
- the existing market-regime contract and cutoff for the routed candidate.

No current-universe or current-theme lookup may fill historical gaps. Unknown peer mapping, fewer than five peers, or a late mapping becomes a stable exclusion. Sector trend is rebuilt from the eligible T panel or read from a matching immutable PIT factor artifact; the current live sector payload is never reused as historical truth.

Alternative considered: use today's 1,337-ETF universe and current theme labels for all historical dates. Rejected because it creates survivorship and taxonomy look-ahead bias.

### 5. Reuse the existing outcome and validation endpoint

Each candidate uses the existing primary endpoint:

- signal after session T close;
- entry at the next decision-eligible adjusted close;
- exit five sessions later;
- 5 bps fee and 5 bps slippage per side;
- paired Top10 net excess against baseline on the same eligible dates;
- non-overlapping primary samples, expanding walk-forward, 10-session purge/embargo, moving-block bootstrap, Holm adjustment, and one-time holdout.

The report additionally includes factor and baseline Spearman correlation, residual IC after existing factor controls, qualifying counts, complete Top10 dates, signal frequency, history tiers, sector/regime slices, turnover, churn, cost drag, drawdown, clone removals, and concentration. Existing hard promotion thresholds remain authoritative.

The MA5 lifecycle adapter consumes the same sealed entry samples and computes a research-only exit at the next eligible adjusted close after a close-below-MA5 observation. It compares against fixed 5- and 10-session holds but remains outside the primary ranking endpoint and existing action-policy registry.

Alternative considered: optimize on MA5 exit profit or directional accuracy. Rejected because it changes the objective after seeing outcomes and can reward lower accuracy-adjusted economic value.

### 6. Reuse evidence tables with an indexed experiment family

Add nullable, indexed `experiment_family` and `hypothesis_registry_hash` columns to `EtfFactorExperimentEvidence`; existing rows retain null or `ranking_promotion_v1` semantics and require no evidence rewrite. The full leader registry, candidate facts, diagnostics, and provenance remain in immutable `report_json`, while the two columns support bounded selection of the latest compatible family.

Checkpoint persistence continues to use `EtfFactorExperimentCheckpoint` keyed by manifest and code version. Candidate/date pages and MA5 lifecycle phase cursors are included in cached row hashes so resuming with a different formula or phase cannot reuse incompatible work.

Alternative considered: a parallel leader-backtest schema. Rejected because it would duplicate costs, splits, outcomes, uncertainty, holdout, and idempotency logic.

### 7. Keep orchestration outside domain services

Add pure hypothesis/feature code and adapters under `app.services.strategy_lab`. A workflow module constructs the manifest, reads immutable PIT artifacts, and advances exactly one bounded page. An admin job endpoint may request one continuation. Automatic scheduling is disabled by default and, if enabled later, advances only one due continuation after complete PIT prerequisites exist; it never calls a live provider.

This preserves the project dependency direction: market data and short research do not import Strategy Lab, while API/admin code only orchestrates and projects results.

### 8. Expose a dedicated read-only evidence contract

Add a read-only leader-tactics evidence endpoint consumed by `/short-term/evidence`. Its stable response includes:

- status and unavailable reason;
- hypothesis and candidate registry versions/hashes;
- source links and non-equivalence limitations;
- ranking source, policy, notification, and execution provenance;
- cutoff, universe/input/feature hashes, coverage, exclusions, and resource facts;
- primary, exploratory, residual, concentration, regime, holdout, and MA5-policy results.

The frontend renders it as a distinct research panel. It never injects these scores into `/short-term` ranking rows and never replaces the existing evidence overview surfaces.

Alternative considered: add a “龙头” option to the production ranking selector. Rejected because that presentation would imply current decision eligibility before validation.

## Risks / Trade-offs

- [ETF adaptation may not represent a stock-leader tactic] → Keep source statement and proxy formula side by side, prohibit “original signal” wording, and require separate ETF evidence.
- [Breakout and deep-repair gates may be too sparse for Top10] → Report qualifying counts and `insufficient_candidate_cohort`; do not loosen thresholds after seeing outcomes or pad cohorts.
- [The 120/180-session windows reduce coverage] → Report common support and history tiers; no raw or shorter-history fallback.
- [Historical peer taxonomy may be incomplete] → Fail closed and accumulate factual PIT mappings going forward rather than backdating current labels.
- [Momentum and sector exposure may duplicate current factors] → Require residual IC and marginal cost-adjusted contribution before any proposal eligibility.
- [Three related tests increase false-positive risk] → Freeze all three before outcomes and use Holm-adjusted primary inference.
- [A single regime can dominate] → Require fold, regime, peer-group, and ETF concentration stability and expose every slice.
- [MA5 exits can look attractive through execution assumptions] → Execute only at the next eligible close, charge declared costs, keep the result exploratory, and prohibit notification effects.
- [JSON evidence can grow] → Persist compact aggregates and hashed sample references; keep large date/ETF rows in bounded cached artifacts rather than embedding repeated panels.
- [Migration or deployment fails] → Add nullable columns first, keep the endpoint and scheduler disabled behind feature flags, and rollback code before dropping no data.

## Migration Plan

1. Add contract and formula tests before persistence or API changes.
2. Add nullable evidence-family columns and deploy with leader workflow, endpoint, and scheduler disabled.
3. Register the immutable source/proxy manifest and run fixture-only PIT tests.
4. Enable the read-only endpoint; an absent experiment returns a stable unavailable state.
5. Run one bounded development continuation against production PIT artifacts without consuming holdout or touching production decisions.
6. Accumulate development and validation evidence; authorize the final holdout only after non-holdout evidence is frozen and manually reviewed.
7. Keep any successful result at `eligible_for_v4_proposal`; create a separate OpenSpec change for production promotion.

Rollback disables the admin continuation and evidence panel, then reverts code. Existing additive evidence rows and nullable columns can remain inert; no production ranking or alert state requires restoration.
