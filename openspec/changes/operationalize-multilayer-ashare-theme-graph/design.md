## Context

The production A-share screen already has immutable universe, adjusted-price, broad-industry, staged-feature, candidate, and fine-theme research tables. Fine-theme capture currently depends on current Eastmoney concept constituents through AKShare, retains one row per asset in the adapter, and has persisted zero memberships. The reliable TickFlow universe catalog already exposes aligned SW1/SW2/SW3 pools, while BaoStock and the frozen CAPCO snapshot cover part of the remaining universe at broader and incompatible granularities. The server is limited to two CPU cores and 4 GB RAM, and each scheduled invocation must finish within 55 seconds.

## Goals / Non-Goals

**Goals:**

- Make a complete SW1/SW2/SW3 primary-industry path the reliable fine peer layer where available.
- Preserve multi-label provider concepts and transparent curated industry-union proxies without collapsing them into one ingestion row.
- Materialize daily context strength independently and make the existing screen consume one deterministic compatible context.
- Deploy additively, retain existing manifests, and verify real production rows and candidate provenance.

**Non-Goals:**

- No paid Tushare/JQData dependency or fabricated historical concept membership.
- No attempt to reproduce an undisclosed proprietary `起飞信号`.
- No ETF taxonomy, comprehensive-ranking, portfolio, notification, or execution changes.
- No full concept-universe crawl on every invocation and no in-memory full-history market join.

## Decisions

### Use four append-only research identities

1. `ashare_industry_path_facts` stores one source-specific primary path with L1/L2/L3 codes and labels.
2. The existing fine-theme relation store is extended to retain provider code, relation kind, membership reason, exposure weight, and source snapshot identity; its uniqueness remains fact-hash based so one asset may have many themes.
3. `ashare_theme_state_facts` stores one daily state per context and immutable input hash.
4. `ashare_theme_capture_runs` seals per-source expected count, completed count, content hash, status, cursor, timing, and error reason.

This separates membership from performance and permits additive migration. Replacing the existing broad table was rejected because old manifests and PIT evidence must remain replayable.

### Derive the primary industry path from the existing TickFlow universe catalog

TickFlow exposes aligned SW1, SW2, and SW3 universe metadata under a shared terminal classification code. The collector will fetch the catalog once, fetch SW3 member pools in bounded batches, join parent labels from catalog metadata, and persist one path per member. BaoStock/CAPCO remain explicit broad fallbacks and are never promoted to an SW3 path.

Fetching every SW1/SW2/SW3 pool was rejected because it triples response volume without adding membership information. Scraping provider pages was rejected because the current Eastmoney TLS path is unhealthy and cannot meet the bounded production contract.

### Maintain a small versioned theme registry and distinguish factual concepts from proxies

The registry stores canonical key, display label, aliases, relation-kind priority, optional provider board identity, and optional disclosed SW3 component rules. Existing innovation-drug, rare-earth/permanent-magnet, and passive-component/MLCC families remain registered. Reliable SW3 unions may create `industry_union_proxy` memberships; current provider concepts remain `provider_concept` and become effective only when captured. The API always exposes relation kind.

Automatically treating every SW3 industry as a market concept was rejected. A large manually guessed concept taxonomy was also rejected; registry expansion requires explicit evidence and tests.

### Resolve context without outcome-based cherry-picking

Each input retains every compatible context. The resolver filters by complete source snapshot, cutoff, freshness, finite state, and five-peer minimum, then orders by frozen relation-kind priority, confidence, registry priority, hierarchy depth, and stable context key. Candidate score is never part of context selection. The selected membership remains compatible with the existing formula engine while alternative contexts and rejected reasons are added to staged facts and evidence.

### Compute state from compact staged facts

After per-asset staged features are complete, the cross-sectional stage streams one context group at a time and calculates breadth, median returns, market-relative return, amount participation, and leader count. Available factual limit-board metrics remain nullable. The state row is sealed before candidates reference it, avoiding a second full-history load.

### Resume capture by source and page

Catalog identity and source date define the run. SW3 pool IDs are sorted and split into bounded batches; complete pages commit before continuation. Provider concept aliases remain one bounded subprocess each. A partial source run is never selected. The scheduler performs at most one page or source continuation per occurrence and uses the existing lease.

## Risks / Trade-offs

- [Risk] SW3 is an industry classification, not a dynamic market concept. → Expose it as `industry_l3`; use disclosed unions only as `industry_union_proxy`, never as provider concepts.
- [Risk] Current-only concept sources cannot validate historical membership. → Preserve factual receipt time and start forward PIT accumulation only after successful capture.
- [Risk] A stock with many themes can increase relation rows and peer joins. → Store compact integer/text identities, index by snapshot/context/asset, cap active registry definitions, and stream one group at a time.
- [Risk] Source snapshots may complete on different dates. → Seal and resolve each source independently; never require a global maximum date shared by unrelated themes.
- [Risk] Existing candidate hashes could change. → Introduce a new classification-registry and manifest identity; retain old rows and do not rewrite prior evidence.
- [Risk] Fine groups can be too small or unstable. → Require five eligible peers and fall back through factual hierarchy with recorded reasons.

## Migration Plan

1. Apply additive tables, columns, indexes, and registry seeds; retain existing rows and manifests.
2. Deploy code with new capture and resolution disabled, run migration and bounded read-only readiness checks.
3. Enable TickFlow industry-path capture, resume until one complete source snapshot is sealed, and verify counts/hashes/resource use.
4. Materialize theme states and a new A-share shadow manifest; compare protected ETF table hashes and candidate API provenance.
5. Enable the new resolver only after complete-path coverage and state integrity pass; keep provider concepts optional and fail closed.
6. Roll back by disabling the new resolver and jobs. Additive research tables may remain; previous manifests and broad resolver continue to work.
