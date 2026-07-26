## Context

The publication lane already fetches current and 61-session adjusted history
under strict resource limits. A separate manual `etf_history_backfill_job`
already derives a 300-session requirement for the registered 1/3/5/10-session
validation contract, but it is not scheduled and its generic profile permits
only ten codes and 768 MiB RSS. Production evidence shows that most ETFs have
deep raw history but only 69 adjusted sessions, so the missing work is an
operational adjusted-history lane rather than another backtest engine.

## Goals / Non-Goals

**Goals:**

- Reach 300 decision-eligible adjusted sessions per eligible ETF as the primary
  research-depth objective, then accumulate 500 sessions as telemetry.
- Preserve a 95 percent current-day decision-data gate, a separate 90 percent
  score-warmup publication gate, 95 percent research-depth completion gates,
  one worker, bounded memory, bounded commands, durable progress, and provider
  provenance.
- Stop repeatedly calling providers for codes whose accepted adjusted source
  factually returned a short history.
- Separate the full publication denominator from factually seasoned 300/500
  research cohorts without inventing historical membership.
- Complete repairable histories first and fetch only the missing frozen-session
  span for each selected ETF.

**Non-Goals:**

- Changing the 61-session ranking formula, factor weights, formal promotion
  gates, or historical visibility rules.
- Inferring listing dates from first returned prices, backdating universe
  membership, or excluding new ETFs from the authoritative publication
  denominator. A provider-observed listing date from the same complete
  authoritative universe snapshot is permitted only with explicit provenance.
- Using raw prices to synthesize adjusted history.
- Running concurrent ETF fetches or an unbounded full-history job.

## Decisions

### 1. Preserve lane priority

The scheduled research-depth coordinator first reads the authoritative
point-in-time universe and existing readiness projection. If target-date
coverage is below 95 percent or 61-session coverage is below 90 percent, it returns
`publication_priority_active` and starts no provider work. Once both gates pass,
it advances the registered 300-session contract lane. The 500-session telemetry
lane is eligible only after the 300-session 95 percent coverage target is met.

Snapshots published with score coverage from 90 percent up to but excluding 95
percent are explicitly marked `degraded`; ETFs without 61 eligible sessions
remain excluded from scoring. Decision-data publication coverage, 300/500
research-depth completion, validation promotion, and raw-price rejection remain
at their existing stricter policies.

### 2. Add a research-depth selection policy

The bounded runner gains a distinct `research_depth` selection policy. It keeps
the current history-depth calculation but permits adaptive profiles from 5 to 20
codes while enforcing the publication-shaped 512 MiB RSS and six-second
provider-attempt limits. The immutable request identity still records the
effective profile; rotation also falls back to the scope cursor so changing the
profile cannot restart the universe.

The profile starts at 10. Timeout, memory pressure, provider-circuit, or failed
checkpoint halves it to a minimum of 5. Two consecutive healthy slices increase
it by 5 to a maximum of 20. Profile changes affect throughput only, never
coverage facts or result hashes.

### 3. Persist source availability without inventing listing dates

Add one row per ETF and adjusted-provider policy with requested range, earliest
and latest returned eligible date, returned eligible-session count, observation
status, observation time, and retry-after. An accepted adjusted result that
cannot satisfy the requested depth records `source_history_shortfall` and a
bounded cooldown. Empty or failed providers remain provider failures, not
listing evidence.

Candidates in an active cooldown are deferred from provider work but remain in
coverage denominators and readiness blockers. A later observation can replace
the cooldown and prove deeper history. No field is called or exposed as a
listing date unless an authoritative listing source is added in a separate
change.

The same factual cooldown and durable attempt cursor apply to the 61-session
publication lane. Otherwise a newly listed ETF can be selected on every slice
and starve older, repairable gaps even though it correctly remains in the
publication denominator.

### 4. Schedule a non-overlapping bounded window

Register one weekday scheduler trigger every two minutes from 23:00 through
23:28 Asia/Shanghai. Each trigger runs at most one slice and `max_instances=1`.
The coordinator checks for an active ETF history lease before provider work.
The window ends before existing 23:30 and 23:45 strategy jobs.

### 5. Keep evidence compact

Job results report lane, required sessions, covered/expected counts, deferred
short-history count, bounded samples, effective profile, elapsed time, RSS,
rows/sec, checkpoint identity, and stop reason. Full code arrays remain internal
to the runner and are not persisted in scheduler job summaries.

### 6. Keep one independent adjusted fallback

The adjusted provider chain may use Tencent raw and `hfq` daily series as an
explicitly versioned fallback after TickFlow and Eastmoney. Tencent rows become
decision eligible only when raw and `hfq` dates pair successfully and every row
passes the existing provenance checks. Tencent remains a fallback because its
three-decimal adjusted values are less precise than TickFlow. It is independent
of Eastmoney's `push2his` host, so an Eastmoney/efinance network block does not
force raw-price substitution.

### 7. Separate publication and seasoned research denominators

The current authoritative point-in-time universe remains the exact denominator
for target-date and 61-session publication coverage. A 300-session research
cohort contains only current authoritative members whose provider-observed
listing date is on or before the first frozen 300-session date. The 500-session
cohort applies the same rule against the first frozen 500-session date.

Listing metadata is accepted only from a complete authoritative universe
snapshot and is persisted with source and observation time. Missing listing
metadata stays explicit and cannot be inferred from price history or membership
observation. A research lane cannot be declared complete unless listing metadata
covers at least 95 percent of the full authoritative universe; unknown and
structurally unseasoned ETFs are reported separately with stable hashes.

The frozen session calendar is derived from observed ETF trade dates across all
price bases because raw daily rows can prove that an exchange session existed.
Only fully versioned decision-eligible `total_return_adjusted` rows count toward
per-ETF coverage.

### 8. Finish the nearest histories and request only missing spans

Within a research cohort, pending ETFs are ordered by existing required-session
depth descending, then watchlist priority and code. Durable rotation and
cooldowns still prevent starvation, but do not make the queue start from the
shallowest histories.

Before provider work, the runner compares one ETF's accepted adjusted dates with
the frozen required-session set. It requests from the earliest missing date to
the latest missing date and asks the provider chain to satisfy the number and
identity of missing required sessions. Persistence remains idempotent, and the
same database comparison reconstructs the breakpoint after interruption or a
batch-profile change.

## Risks / Trade-offs

- [Many ETFs are genuinely new] → Keep them in the denominator, persist factual
  listing metadata, exclude them only from a research horizon they could not
  factually satisfy, keep them in the publication denominator, and label a
  90–95 percent publication as degraded rather than fabricate data.
- [Listing metadata is incomplete] → Continue bounded work for the known
  seasoned cohort but block research completion below 95 percent metadata
  coverage and expose unknown codes separately.
- [Raw rows pollute coverage] → Permit raw dates only in the shared observed
  session calendar; every per-ETF depth query still requires decision-eligible,
  versioned total-return-adjusted rows.
- [Repeated full-range requests waste the slice] → Recompute exact missing
  required dates from persisted adjusted rows before each fetch and request only
  their enclosing span.
- [Provider returns a capped range] → Record requested and returned boundaries;
  retry after cooldown and never infer a listing date.
- [Eastmoney host is unreachable] → Try the independently hosted, explicitly
  versioned Tencent `hfq` response; never promote Tencent raw-only rows.
- [More rows raise memory] → Retain 500-row pages, 5,000-row slices, 512 MiB RSS,
  and adaptive downshift.
- [Profile changes lose progress] → Resume rotation from the durable scope
  cursor independently of request profile identity.
- [Research work delays live jobs] → Run only after publication gates and in a
  separate 23:00–23:28 window.

## Migration Plan

1. Add authoritative listing-date persistence and migration.
2. Project separate full-publication and seasoned 300/500 research denominators
   with a 95 percent metadata gate.
3. Freeze observed session calendars, add completion-first selection and
   per-code missing-span requests.
4. Run bounded local tests and production-shaped benchmarks.
5. Deploy conservatively at ten codes; allow automatic growth only after healthy
   real slices.

Rollback removes the research-depth scheduler trigger and reverts the
cohort/queue code while leaving all accepted adjusted rows intact. The additive
availability table and nullable listing metadata may remain for audit.
