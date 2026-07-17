# ETF V3 Snapshot Writer Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before claiming success.

**Goal:** Make the production post-close ETF pipeline materialize and canonically publish an immutable `final_score_v3` snapshot only when both total-return-adjusted decision data and finite v3 score coverage meet the contract threshold; make all default readers select that canonical snapshot instead of an arbitrary legacy run.

**Architecture:** Keep legacy fund/partial-scope research generation unchanged. Add one focused v3 materializer, expose historical quotes at a declared cutoff through the market-data boundary, split decision-data coverage from score coverage, and route the lightweight post-close workflow through materialize-then-publish. Research replay remains a separate OpenSpec change and can never satisfy this production publication gate.

**Tech Stack:** Python 3.12, SQLAlchemy async ORM, Alembic, Pydantic, pytest/pytest-asyncio, Ruff, OpenSpec.

## Global constraints and success criteria

- Run every shell/test command with a hard timeout of at most 55 seconds.
- Keep runtime safe for a 2-core/4-GB host: one worker, set-based reads, no post-close historical resync, and at most 20-item flush batches inside one transaction.
- Fail closed: missing, stale, post-cutoff, fallback, divergent, non-finite, or contract-incompatible data remains unavailable; never synthesize 0/50 values.
- Preserve all legacy signal runs exactly as stored; the migration adds nullable columns and performs no backfill.
- A run is publishable only if decision-data coverage and score coverage are each at least 95%, score-eligible codes are a subset of decision-data codes, and persisted global ranks are continuous.
- The post-close job must never call the 120-day history sync.
- Default ETF ranking/read APIs must return explicit waiting/stale/legacy/version-mismatch state when an exact published v3 snapshot does not exist.
- Verification includes the focused tests, backend-domain boundary test, Ruff on changed files, strict validation of `harden-etf-comprehensive-ranking`, and a bounded production-database dry run that does not publish when coverage is insufficient.

---

## Task 1: Freeze the contract loader and persist dual coverage

**Files:**

- Modify: `backend/app/services/short_research/ranking_contract.py`
- Modify: `backend/app/models/entities.py`
- Modify: `backend/app/schemas/short_research.py`
- Create: `backend/alembic/versions/20260715_000045_etf_ranking_dual_coverage.py`
- Modify: `backend/tests/test_etf_ranking_contract.py`
- Modify: `backend/tests/test_etf_ranking_snapshot_migrations.py`

### Step 1: Write failing tests

Add tests proving:

```python
def test_final_score_v3_contract_exposes_rule_and_premium_consensus() -> None:
    contract = final_score_v3_contract()
    assert contract["contract_id"] == "final_score_v3"
    assert contract["rule_version"] == "final_score_v3_rule_v1"
    consensus = contract["freshness"]["premium_discount"]["provider_consensus"]
    assert consensus["minimum_independent_providers"] == 2
    assert consensus["maximum_premium_dispersion_bps"] == 30


def test_dual_coverage_columns_are_nullable_and_not_backfilled() -> None:
    assert ShortResearchSignalRun.__table__.c.decision_data_item_count.nullable
    assert ShortResearchSignalRun.__table__.c.decision_data_coverage_ratio.nullable
```

The migration test must upgrade a schema containing a legacy run and assert both new columns remain `NULL` for that row.

### Step 2: Verify RED

Run from `backend/`:

```powershell
uv run pytest tests/test_etf_ranking_contract.py tests/test_etf_ranking_snapshot_migrations.py -q
```

Expected: failures for the missing loader and missing ORM/migration columns, not an unrelated import or fixture failure.

### Step 3: Implement the smallest contract/schema change

Add this read-only interface to `ranking_contract.py` and have `final_score_v3_manifest()` reuse it:

```python
@lru_cache(maxsize=1)
def final_score_v3_contract() -> Mapping[str, Any]:
    return json.loads(_contract_path().read_text(encoding="utf-8"))
```

Add nullable ORM/Pydantic fields:

```python
decision_data_item_count: int | None
decision_data_coverage_ratio: float | None
```

The Alembic revision is `20260715_000045`, with `down_revision = "20260715_000044"`; `upgrade()` only adds the two nullable columns and `downgrade()` only removes them.

### Step 4: Verify GREEN

Run the same two test files. Expected: all pass in under 55 seconds.

### Step 5: Commit checkpoint

```powershell
git add backend/app/services/short_research/ranking_contract.py backend/app/models/entities.py backend/app/schemas/short_research.py backend/alembic/versions/20260715_000045_etf_ranking_dual_coverage.py backend/tests/test_etf_ranking_contract.py backend/tests/test_etf_ranking_snapshot_migrations.py
git commit -m "fix: split ETF decision and score coverage"
```

---

## Task 2: Select immutable intraday evidence at the declared cutoff

**Files:**

- Modify: `backend/app/services/intraday_etf/service.py`
- Modify: `backend/app/services/market_data.py`
- Modify: `backend/tests/test_intraday_etf_watch.py`
- Modify: `backend/tests/test_backend_domain_boundaries.py`

### Step 1: Write failing cutoff and provenance tests

Cover all of these behaviors:

```python
async def test_quotes_at_cutoff_ignores_later_quote(...): ...
async def test_quotes_at_cutoff_selects_latest_history_row_per_code(...): ...
def test_consensus_keeps_provider_iopv_premium_bid_ask_and_time(...): ...
def test_premium_consensus_uses_two_fresh_independent_sources(...): ...
def test_single_provider_uses_contract_score_60_only_when_eligible(...): ...
def test_divergent_premiums_are_unavailable_even_when_prices_agree(...): ...
```

Fixtures must include one row before and one after the cutoff for the same ETF. The selected result must be the latest history row satisfying both `trade_date == requested_trade_date` and `quote_time <= decision_cutoff`.

### Step 2: Verify RED

```powershell
uv run pytest tests/test_intraday_etf_watch.py tests/test_backend_domain_boundaries.py -q
```

Expected: only the new historical-cutoff/consensus assertions fail.

### Step 3: Add the bounded query and market-data facade

Add to `intraday_etf/service.py`:

```python
async def quotes_at_or_before_cutoff(
    session: AsyncSession,
    codes: Sequence[str],
    *,
    trade_date: date,
    decision_cutoff: datetime,
) -> dict[str, EtfIntradayQuote]:
    """Return one immutable history row per code at or before the declared cutoff."""
```

Use one window-function or grouped-max subquery against `EtfIntradayQuote`; do not loop over codes and do not query `EtfIntradayLatestQuote`. Expose the result through a thin `market_data` function so the research domain does not import the intraday implementation module directly.

Extend `select_consensus_quotes()` provider provenance with `latest_price`, `iopv`, calculated `premium_discount_bps`, `bid1`, `ask1`, and `quote_time`. The market-data layer emits auditable status/count/dispersion plus its generic 0-100 consensus quality; it must not import the research domain. Task 3 maps that evidence through the frozen JSON contract before it becomes the `premium_provider_consensus` ranking primitive:

- two or more fresh independent premiums with dispersion <=30 bps: score 100;
- one otherwise eligible fresh provider: score 60;
- divergent, stale, fallback, missing timestamp/price/IOPV: `None` with an explicit reason.

### Step 4: Verify GREEN

Run the same tests. Expected: all pass and the domain-boundary direction remains valid.

### Step 5: Commit checkpoint

```powershell
git add backend/app/services/intraday_etf/service.py backend/app/services/market_data.py backend/tests/test_intraday_etf_watch.py backend/tests/test_backend_domain_boundaries.py
git commit -m "fix: bind ETF quote evidence to decision cutoff"
```

---

## Task 3: Make v3 component freshness depend on cutoff, not wall clock

**Files:**

- Modify: `backend/app/services/short_research/service.py`
- Modify: `backend/tests/test_short_research_metrics.py`

### Step 1: Write failing component tests

Add tests for:

```python
def test_quote_at_1450_is_fresh_for_1500_cutoff_when_job_runs_later(...): ...
def test_quote_after_decision_cutoff_is_rejected(...): ...
def test_quote_from_other_trade_date_is_rejected(...): ...
def test_structure_maps_consistent_single_and_diverged_reliability(...): ...
def test_premium_requires_contract_defined_consensus(...): ...
```

The first test freezes job wall time after 16:00 while declaring a 15:00 cutoff; a 14:50 quote must remain fresh. The second inserts a 15:01 quote and must produce `score_unavailable` for a 15:00 cutoff.

### Step 2: Verify RED

```powershell
uv run pytest tests/test_short_research_metrics.py -q
```

Expected: freshness tests fail because current code uses `utcnow()`/latest quote.

### Step 3: Thread the explicit cutoff through existing v3 calculation

Change the private interfaces to require the decision cutoff:

```python
async def _with_final_score_v3_shadow(..., decision_cutoff: datetime) -> list[ComputedAsset]: ...
def _v3_premium_inputs(..., decision_cutoff: datetime) -> dict[str, Any]: ...
def _v3_structure_inputs(..., decision_cutoff: datetime) -> dict[str, Any]: ...
```

Fetch quotes through the market-data cutoff facade. Define freshness only as:

```python
quote.trade_date == snapshot_trade_date
and decision_cutoff - timedelta(minutes=15) <= quote.quote_time <= decision_cutoff
```

Update every call site to pass a deterministic cutoff. Interactive shadow callers may derive a declared cutoff once at the boundary, but no helper may call `utcnow()` for freshness.

### Step 4: Verify GREEN

Run the test file again. Expected: all pass.

### Step 5: Commit checkpoint

```powershell
git add backend/app/services/short_research/service.py backend/tests/test_short_research_metrics.py
git commit -m "fix: evaluate ETF v3 inputs at declared cutoff"
```

---

## Task 4: Materialize an immutable production final_score_v3 snapshot

**Files:**

- Create: `backend/app/services/short_research/snapshot_materialization.py`
- Modify: `backend/app/services/short_research/service.py`
- Create: `backend/tests/test_etf_v3_snapshot_materialization.py`

### Step 1: Write the materializer contract tests

Use a small three-ETF PIT universe and stub only the deterministic calculation boundary. Test:

```python
async def test_materializer_persists_complete_final_score_v3_identity(...): ...
async def test_materializer_ranks_by_v3_score_then_code(...): ...
async def test_materializer_reuses_same_idempotency_key(...): ...
async def test_materializer_persists_exclusions_without_neutral_fill(...): ...
async def test_materializer_rejects_non_finite_scores(...): ...
async def test_materializer_never_mutates_legacy_runs(...): ...
async def test_materializer_uses_declared_cutoff(...): ...
```

Assert that only finite `v3_ranking_score` values with `v3_score_eligible is True` become `ShortResearchSignalItem`s; excluded assets stay in summary evidence only. Ties use ascending asset code.

### Step 2: Verify RED

```powershell
uv run pytest tests/test_etf_v3_snapshot_materialization.py -q
```

Expected: import failure for the new module/interface is the initial RED.

### Step 3: Implement the focused materializer

Expose exactly:

```python
async def materialize_final_score_v3_snapshot(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
) -> ShortResearchSignalRun:
    ...
```

Implementation order:

1. Build the PIT universe for `trade_date` and decision-data coverage barrier.
2. Compute assets with existing v3 logic at `decision_cutoff`.
3. Split finite score-eligible rows from explicit exclusions.
4. Build contract identity using `build_ranking_contract()` and the raw contract's `contract_id`, `rule_version`, calculation manifest, price basis, freshness/reliability policy, universe members, cutoff, and compact actual input provenance.
5. Derive idempotency from canonical `{trade_date, scope_hash, input_snapshot_hash, ranking_contract_hash}`.
6. Reuse an existing run with that idempotency key; do not mutate it.
7. Persist the run as `scope_kind="full"`, `score_field="ranking_score"`, `publication_state="candidate"`, dual coverage fields, and nested `summary_json["coverage"]` for decision data and score exclusions.
8. Persist only eligible items, ranked by `(-ranking_score, asset_code)`, flushing at most 20 items per batch without committing partial state.

The input snapshot stores only contract-required primitives and provider provenance, never the full raw provider payload. Legacy `run_signal_generation()` remains the fund/partial-scope writer.

### Step 4: Verify GREEN

Run the materializer tests. Expected: all pass, including idempotency and legacy immutability.

### Step 5: Commit checkpoint

```powershell
git add backend/app/services/short_research/snapshot_materialization.py backend/app/services/short_research/service.py backend/tests/test_etf_v3_snapshot_materialization.py
git commit -m "feat: materialize canonical ETF v3 snapshots"
```

---

## Task 5: Enforce both publication barriers and wire the lightweight post-close job

**Files:**

- Modify: `backend/app/services/short_research/snapshot_publication.py`
- Modify: `backend/app/services/workflows/etf_daily_research.py`
- Modify: `backend/app/services/short_research/jobs.py`
- Modify: `backend/tests/test_etf_snapshot_publication.py`
- Modify: `backend/tests/test_etf_daily_research_workflow.py`
- Modify: `backend/tests/test_short_research_jobs.py`

### Step 1: Write failing publication and orchestration tests

Add cases proving:

```python
async def test_full_price_coverage_does_not_hide_low_score_coverage(...): ...
async def test_full_score_coverage_does_not_hide_low_price_coverage(...): ...
async def test_ranked_codes_must_be_decision_data_subset(...): ...
async def test_post_close_job_materializes_then_publishes(...): ...
async def test_post_close_job_does_not_repeat_history_sync(...): ...
```

The job test must make any call to the 120-day sync fail immediately; the post-close job must still pass.

### Step 2: Verify RED

```powershell
uv run pytest tests/test_etf_snapshot_publication.py tests/test_etf_daily_research_workflow.py tests/test_short_research_jobs.py -q
```

Expected: current publisher conflates coverage, and current job still calls the legacy writer.

### Step 3: Implement dual publication validation

Require these identity fields in `_validate_publishable()`:

```python
decision_data_item_count
decision_data_coverage_ratio
eligible_item_count
coverage_ratio
```

Validate both ratios against 0.95 and their independently computed counts. Read decision-data codes from the coverage barrier and score-eligible codes from persisted items; require `score_codes <= decision_data_codes`. Preserve continuous ranks and finite-score checks.

Write summary coverage as:

```json
{
  "decision_data": {"expected_count": 0, "included_count": 0, "coverage_ratio": 0.0, "excluded": []},
  "score": {"eligible_count": 0, "coverage_ratio": 0.0, "excluded": []}
}
```

### Step 4: Add a no-sync production workflow

Extract and expose:

```python
async def generate_and_publish_etf_snapshot(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
) -> ShortResearchSignalRun:
    run = await materialize_final_score_v3_snapshot(...)
    return await publish_full_snapshot(session, run_id=run.id)
```

The existing broader workflow may call this after its explicit sync, but `post_close_etf_signals_job()` calls only the lightweight function. On insufficient coverage it records a bounded reproducible error/JobRun result and does not retry or sync history.

### Step 5: Verify GREEN

Run the same three files. Expected: all pass and no sync mock is invoked.

### Step 6: Commit checkpoint

```powershell
git add backend/app/services/short_research/snapshot_publication.py backend/app/services/workflows/etf_daily_research.py backend/app/services/short_research/jobs.py backend/tests/test_etf_snapshot_publication.py backend/tests/test_etf_daily_research_workflow.py backend/tests/test_short_research_jobs.py
git commit -m "fix: publish ETF v3 snapshots behind dual gates"
```

---

## Task 6: Make exact canonical v3 selection the default read path

**Files:**

- Modify: `backend/app/services/short_research/snapshot_selector.py`
- Modify: `backend/app/services/short_research/service.py`
- Modify: `backend/app/api/routes/short_research.py`
- Modify: `backend/tests/test_etf_snapshot_selector.py`
- Modify: `backend/tests/test_short_research_snapshot_scope.py`
- Modify: `backend/tests/test_short_research_api.py`

### Step 1: Write failing reader tests

Create a newer legacy success run, an unpublished v3 candidate, a published wrong-contract v3 run, and an older exact published v3 run. Assert the default ETF reader selects only the exact published v3 run and otherwise returns one of:

```text
waiting
stale
legacy
version_mismatch
```

It must never fall back to the newer arbitrary success run.

### Step 2: Verify RED

```powershell
uv run pytest tests/test_etf_snapshot_selector.py tests/test_short_research_snapshot_scope.py tests/test_short_research_api.py -q
```

Expected: default service/API currently selects the arbitrary latest successful run.

### Step 3: Wire the selector using the current immutable contract

Derive the expected score version, rule version, price basis, and ranking contract hash from the same loader/builder used by the materializer. Route full-scope ETF reads through `resolve_canonical_etf_snapshot()`. Keep explicit legacy-run lookup only for endpoints that request an exact historical run id.

Extend `EtfRankingSnapshotMetadataOut` to expose both:

```python
decision_data_coverage_ratio: float | None
score_coverage_ratio: float | None
```

`coverage_ratio` remains a compatibility alias for score coverage during this change. Do not label a research replay artifact as canonical.

### Step 4: Verify GREEN

Run the same three files. Expected: all pass with explicit unavailable states.

### Step 5: Commit checkpoint

```powershell
git add backend/app/services/short_research/snapshot_selector.py backend/app/services/short_research/service.py backend/app/api/routes/short_research.py backend/tests/test_etf_snapshot_selector.py backend/tests/test_short_research_snapshot_scope.py backend/tests/test_short_research_api.py
git commit -m "fix: read exact published ETF v3 snapshots"
```

---

## Task 7: Bounded verification and honest production dry run

**Files:**

- Modify only if a test exposes a scoped defect in files already listed above.
- Update: `openspec/changes/harden-etf-comprehensive-ranking/tasks.md` only after corresponding evidence exists.

### Step 1: Run focused tests in bounded groups

Each command is a separate invocation with a 55-second external timeout:

```powershell
uv run pytest tests/test_etf_ranking_contract.py tests/test_etf_ranking_snapshot_migrations.py -q
uv run pytest tests/test_intraday_etf_watch.py tests/test_short_research_metrics.py -q
uv run pytest tests/test_etf_v3_snapshot_materialization.py tests/test_etf_snapshot_publication.py -q
uv run pytest tests/test_etf_daily_research_workflow.py tests/test_short_research_jobs.py -q
uv run pytest tests/test_etf_snapshot_selector.py tests/test_short_research_snapshot_scope.py tests/test_short_research_api.py -q
uv run pytest tests/test_backend_domain_boundaries.py -q
```

### Step 2: Run static verification

```powershell
uv run ruff check app/services/short_research/ranking_contract.py app/services/short_research/snapshot_materialization.py app/services/short_research/snapshot_publication.py app/services/short_research/snapshot_selector.py app/services/short_research/service.py app/services/short_research/jobs.py app/services/intraday_etf/service.py app/services/market_data.py app/services/workflows/etf_daily_research.py tests/test_etf_v3_snapshot_materialization.py tests/test_etf_snapshot_publication.py tests/test_intraday_etf_watch.py
```

### Step 3: Validate OpenSpec and migrations

From repository root:

```powershell
openspec validate harden-etf-comprehensive-ranking --strict
```

From `backend/`:

```powershell
uv run alembic upgrade head
```

### Step 4: Run one production-database dry materialization

Use a dedicated bounded CLI/function that loads the production database, prints trade date, cutoff, decision-data count/ratio, score-eligible count/ratio, exclusions by reason, and candidate run id. Do not publish unless both real ratios are at least 0.95. Do not sync history, do not retry providers, and stop within 55 seconds.

Expected on currently known data: the chain produces explicit component/score exclusions instead of a misleading v3 result; publication remains blocked until real structure/premium/catalyst inputs meet the contract.

### Step 5: Mark only evidenced OpenSpec tasks complete

- Mark 13.1-13.9 only when their tests and production wiring are verified.
- Mark 11.3 and 11.4 only after scheduler and default-reader tests pass.
- Leave 11.9 open until three distinct real trading dates pass the actual publication threshold.
- Never use research replay evidence to close 11.9.

### Step 6: Final review and commit

Review `git diff --check`, `git status --short`, and the exact staged diff. Do not stage `.superpowers/` or unrelated user files. Commit only the verified scoped changes.

---

## Follow-on plan (separate change)

After this production repair passes Tasks 1-7, create and execute a separate detailed plan for `enable-etf-point-in-time-ranking-replay`. It will materialize `daily_reconstructable_v1`, run no more than three preregistered ranking candidates with walk-forward/purge/one holdout, and evaluate Top10/5-day cost-adjusted excess plus Top20/10-day action-policy benefit. Its artifacts are permanently `research_replay`/`policy_shadow` and never replace production v3 or live email evidence.
