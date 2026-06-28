## Context

FundScope now tracks and ranks a large on-exchange ETF universe, but the short-term workbench still treats many ETFs as a flat list. Users need to compare ETFs by industry or theme because short-term opportunities and risks often cluster by semiconductor, AI, healthcare, financials, dividend, gold, bonds, cross-border, and broad-market exposure.

Current specs already mention `theme labels` and portfolio theme concentration, but they do not define a normalized taxonomy, coverage quality, theme heat statistics, or how the portfolio layer should consume theme groups. This change keeps FundScope as a modular monolith and adds a deterministic classification layer without changing trading boundaries.

## Goals / Non-Goals

**Goals:**
- Classify all tradable ETF records into normalized sector/theme fields with evidence and confidence.
- Add theme filters and theme heat statistics to `/short-term` ETF mode.
- Use normalized theme groups for observation portfolio concentration limits.
- Keep unknown or low-confidence classification visible instead of inventing a confident label.
- Coordinate implementation through parallel research/execution agents, with the main agent reviewing reports and integration.

**Non-Goals:**
- No automatic trading, broker connection, or buy/sell instruction.
- No LLM-only theme classification for production decisions.
- No major redesign of ranking, email alerts, tracked position logic, or market-data freshness rules.
- No new frontend dependency.
- No deletion of existing legacy ETF/fund compatibility fields.

## Decisions

### 1. Use a deterministic normalized taxonomy

ETF theme classification will produce:
- `asset_bucket`: broad asset class such as `equity`, `bond`, `commodity`, `money`, `cross_border`, `broad_base`, or `unknown`.
- `theme_group`: normalized concentration group such as `technology`, `financial`, `healthcare`, `consumer`, `energy`, `materials`, `dividend`, `gold`, `bond`, `cross_border`, `broad_base`, or `unknown`.
- `primary_theme`: beginner-readable Chinese theme label, such as `半导体`, `人工智能`, `生物医药`, `证券`, `银行`, `红利`, `黄金`, `国债`, `纳指`, `恒生科技`.
- `secondary_themes`: optional additional labels used for detail display and search.
- `classification_source`: rule source such as `manual_override`, `index_name`, `fund_name`, `category`, or `unknown`.
- `classification_confidence`: `high`, `medium`, `low`, or `unknown`.
- `classification_reason`: short evidence string for the UI and admin diagnostics.

Rationale: mature research tools treat sector/theme as a first-class grouping dimension. Deterministic rules are easier to audit than model-generated categories and avoid making unsupported financial claims.

Alternative considered: let the LLM classify every ETF name. Rejected because classification would be harder to reproduce and may drift between runs.

### 2. Store theme profiles separately from signal items

Implementation should prefer an additive `etf_theme_profiles` table keyed by ETF code, or equivalent additive model if the existing schema already has suitable fields. Signal generation will copy normalized theme fields into cached signal item payloads for fast reads, but the theme profile remains the source of truth.

Rationale: ETF metadata changes less often than signal runs. Keeping profiles separate avoids reclassifying every asset during page load and allows admin coverage diagnostics.

Alternative considered: store only in `metrics_json` or `rationale_json`. Rejected because that would make filtering and portfolio constraints depend on the latest signal cache and make coverage harder to audit.

### 3. Use strict unknown handling

If an ETF cannot be confidently classified, it remains visible as `未分类` with `classification_confidence=unknown`. It can still be searched and ranked, but it must not be used to claim sector heat or bypass portfolio concentration limits.

Rationale: in a financial UI, a visible "unknown" is safer than a guessed industry label.

### 4. Theme heat reads cached rankings and latest quotes

Theme heat statistics will aggregate from the latest completed ETF signal cache plus latest quote status:
- theme ETF count
- active/ranked count
- average score
- top score and top ETF
- average intraday change when fresh quotes exist
- average latest daily change when intraday is unavailable
- data freshness summary

It must not recompute full ETF metrics on page load.

Rationale: `/short-term` should remain fast and cache-first. Theme heat is a summary layer over existing ranking and quote data.

### 5. Portfolio concentration uses normalized theme groups

Observation portfolio logic will continue to cap single ETF weight at 30%. It will additionally use normalized theme groups to prevent repeated exposure, for example technology ETFs dominating a portfolio just because multiple related ETFs rank high. Exact caps should be constants in the portfolio allocation layer, with explanations recorded in the snapshot.

Rationale: PyPortfolioOpt-style portfolio constraints and existing FundScope portfolio rules both require an explicit sector/theme grouping. Raw name matching is not robust enough.

### 6. Parallel-agent execution model

The main agent acts as coordinator and reviewer. Implementation work can be split into parallel agents:
- Research Agent: verify mature project/product patterns, propose final taxonomy groups, identify ambiguous ETF names and cross-border/premium cases.
- Backend Agent: implement theme profile data model, classifier, refresh job, API serialization, and tests.
- Frontend Agent: implement theme filters, theme heat UI, detail display, and mobile behavior.
- Portfolio Agent: integrate normalized theme caps into observation portfolio logic and explain included/excluded weights.
- Validation Agent: run tests, inspect edge cases, and report discrepancies.

Each agent must return a concise report with files touched, assumptions, tests run, unresolved risks, and suggested follow-up. The coordinator must review those reports, request follow-up if needed, and only then mark tasks done.

## Risks / Trade-offs

- [Risk] ETF names are inconsistent and may map to multiple themes. → Mitigation: use manual overrides first, then index/fund-name rules, and expose secondary themes plus confidence.
- [Risk] Theme filters may imply "hot theme = buy". → Mitigation: use observation-only wording and keep buy-point/risk labels separate.
- [Risk] Taxonomy becomes stale as new ETFs list. → Mitigation: add idempotent refresh job and coverage summary.
- [Risk] Theme concentration cap may exclude a high-scoring ETF users care about. → Mitigation: keep excluded ETFs visible as watch-only with concrete concentration reason.
- [Risk] Extra aggregation slows `/short-term`. → Mitigation: read cached signal items and latest quote snapshots; paginate lists; avoid full recomputation on page load.

## Migration Plan

1. Add additive theme profile storage and migration if needed.
2. Seed deterministic taxonomy rules and manual overrides.
3. Run classification refresh for existing ETF universe.
4. Extend ETF ranking/detail responses with normalized theme fields.
5. Add frontend filters and heat summary while preserving old query parameters.
6. Update observation portfolio to consume normalized theme groups.
7. Deploy and run taxonomy refresh, ETF signal generation, and observation portfolio generation.

Rollback is straightforward because API additions are backward compatible. If classification quality is poor, hide the new filter/heat UI and keep ETF rankings operating with existing fields.

## Open Questions

- Should theme groups include a separate `跨境` group plus an underlying theme such as `纳指/日本/恒生科技`, or should cross-border be represented only as an asset bucket?
- What exact concentration cap should apply to broad defensive assets versus industry themes?
- Do we want a small admin-only page for taxonomy review, or is the existing jobs/status page enough for the first release?
