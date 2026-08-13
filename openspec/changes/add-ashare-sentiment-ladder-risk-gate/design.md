## Context

The V2 screen already calculates PIT theme hotness, peer-relative `core_score`, adjusted moving averages, raw formula qualification, lifecycle state, append-only gate facts, and a bounded read-only API. It has no factual consecutive-limit-up dataset and no separate market-sentiment action overlay. See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**

- Add an O(n), pure, deterministic A-share cross-sectional risk calculation using already-loaded inputs.
- Preserve the distinction between raw alpha qualification and shadow actionability.
- Carry one contract identity and complete risk facts through persistence, API, summary and UI.
- Fail closed when PIT support is insufficient and keep all ETF paths byte-for-byte compatible where practical.

**Non-Goals:**

- Reconstruct proprietary “起飞信号” or factual limit-up/failed-board events from adjusted prices.
- Change the three V2 formulas, their scores, lifecycle, ranking order, or promotion gates.
- Add a database table, provider request, intraday computation, production order, holding, alert, mail, or ETF-ranking dependency.
- Tune thresholds against current outcomes.

## Decisions

### Use a transparent middle-echelon breadth proxy

The screen already has the only causal inputs available across the full materialized A-share cross-section: adjusted bars, PIT theme and peer scores. The risk cohort is therefore frozen as leaders at `core_score >= 0.80` and middle echelon at `[0.50, 0.80)` inside themes whose hot score is at least `2/3`.

Alternative considered: infer limit-up chains from adjusted close changes. This is rejected because exchange price-limit rules vary by board, ST state, listing age and date, and adjusted prices are not factual executable limit prices.

### Compute four robust cross-sectional components in one pass

The pure risk module receives compact points containing code, theme, hot/core scores, one-session return, latest adjusted MA5 relation and cutoff. It computes median returns and simple ratios without retaining duplicate bar histories:

1. middle median one-session return `<= 0`;
2. middle positive breadth `< 0.40`;
3. middle below-MA5 ratio `> 0.60`;
4. leader median is positive and leader-minus-middle median return spread `>= 0.02`.

Two components produce `warning`; three or four produce `risk_off`. Minimum support is two hot themes, three leaders and twelve middle-tier names. All constants and semantic fields are covered by a dedicated stable contract hash.

Alternative considered: a two-day state machine. It requires a separate prior-snapshot store and complicates replay/resume identity. The first version keeps the signal stateless and exposes all facts so temporal confirmation can be evaluated in the existing replay layer before a future version.

### Overlay only A-share breakout shadow entries

The raw observation remains authoritative for formula qualification and lifecycle. The new risk state is attached to gate facts and projected as top-level API fields. Only A-share `leader_breakout_proxy_v2` maps healthy to `shadow_entry_allowed`; all other states map to `observe_only`. Base-launch and former-leader-repair report `not_applicable` because the quoted market heuristic concerns sentiment/limit-board leadership rather than slower repair setups.

Alternative considered: add risk reasons to formula exclusions. Rejected because it would rewrite alpha evidence, change rankings, and prevent paired evaluation of the overlay.

### Reuse existing persistence and derive projections

Stage A computes the market-wide snapshot from bounded scalar features before Stage B theme groups are merged. The finalized observation set stores one full immutable snapshot on the deterministic first observation and a compact contract/state/`snapshot_hash` reference on every observation. This avoids repeating the same object thousands of times while binding every row to the exact metrics and cutoff; existing feature hashes, manifest writes, pagination and replay idempotence protect the evidence without a schema migration. The read layer resolves and validates both the contract and snapshot hashes before projecting `sentiment_risk_state`, `action_mode`, and `new_entry_allowed`; a missing, mismatched, tampered or legacy snapshot becomes unavailable rather than healthy. Summary counts are aggregated from persisted JSON only.

### Keep rendering informational

The research panel displays an A-share-only badge and concise reason. It uses “研究观察/仅观察” language and retains the global research-only disclaimer. ETF cards render exactly as before.

## Risks / Trade-offs

- [The proxy is not a factual limit-board ladder] → Name and provenance say `adjusted_bar_middle_echelon_proxy_v1`; unsupported fields remain unavailable.
- [Cross-sectional thresholds may be regime dependent] → Freeze them, run shadow validation, and require a new version for any change.
- [A broad warning could mark many rows identically] → Compute one market-wide snapshot from Stage A scalars and attach one full anchor plus compact per-row references; do not recompute per asset or per theme group.
- [Large materializations could increase memory] → Pass scalar points only, use O(n) lists, and do not copy bar arrays or issue additional SQL/provider work.
- [Legacy manifests lack risk facts] → Project a stable unavailable state; never backfill healthy.

## Migration Plan

1. Deploy the pure contract and tests with no feature flag or provider change.
2. Materialize the next A-share research snapshot under the new code/feature hash; old manifests remain readable with unavailable risk evidence.
3. Verify candidates, summary and UI against the persisted snapshot, including ETF isolation.
4. Accumulate shadow outcomes before any separately approved production action policy.

Rollback removes the projection and calculator call; no database migration or data deletion is required, and earlier manifests remain immutable.
