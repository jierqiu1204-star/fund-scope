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
- Preserve publication priority, dual 95 percent gates, one worker, bounded
  memory, bounded commands, durable progress, and provider provenance.
- Stop repeatedly calling providers for codes whose accepted adjusted source
  factually returned a short history.

**Non-Goals:**

- Changing the 61-session ranking formula, factor weights, formal promotion
  gates, or historical visibility rules.
- Inferring listing dates from first returned prices or excluding new ETFs from
  the authoritative publication denominator.
- Using raw prices to synthesize adjusted history.
- Running concurrent ETF fetches or an unbounded full-history job.

## Decisions

### 1. Preserve lane priority

The scheduled research-depth coordinator first reads the authoritative
point-in-time universe and existing readiness projection. If target-date or
61-session coverage is below 95 percent, it returns
`publication_priority_active` and starts no provider work. Once both gates pass,
it advances the registered 300-session contract lane. The 500-session telemetry
lane is eligible only after the 300-session coverage target is met.

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

## Risks / Trade-offs

- [Many ETFs are genuinely new] → Keep them in the denominator, persist factual
  short-history observations, and wait rather than fabricate data.
- [Provider returns a capped range] → Record requested and returned boundaries;
  retry after cooldown and never infer a listing date.
- [More rows raise memory] → Retain 500-row pages, 5,000-row slices, 512 MiB RSS,
  and adaptive downshift.
- [Profile changes lose progress] → Resume rotation from the durable scope
  cursor independently of request profile identity.
- [Research work delays live jobs] → Run only after publication gates and in a
  separate 23:00–23:28 window.

## Migration Plan

1. Add availability persistence and migration.
2. Add the research-depth request policy, cursor-compatible adaptive profile, and
   tests.
3. Add the coordinator and scheduler trigger disabled only by factual gate
   blockers.
4. Run bounded local tests and production-shaped benchmarks.
5. Deploy conservatively at ten codes; allow automatic growth only after healthy
   real slices.

Rollback removes the research-depth scheduler trigger and leaves all accepted
adjusted rows intact. The additive availability table may remain for audit.
