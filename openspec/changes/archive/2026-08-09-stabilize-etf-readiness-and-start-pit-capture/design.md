## Context

The ETF post-close workflow currently runs in the API process on a 2-core/4-GB server. Its Linux RSS reader uses `ru_maxrss`, a process-lifetime high-water value, as if it were current resident memory. After any historical spike above 512 MiB, every later bounded slice can fail admission before a provider request even when current memory has recovered.

Coverage semantics are also split across code and canonical specs. Production code and the workbench recognize a 90-to-95-percent degraded research publication, while other requirements describe any score coverage below 95 percent as unpublished. The latest workflow can therefore call a run published while production acceptance, PIT promotion, and operators still treat it as incomplete.

The point-in-time research coordinator, manifest, candidates, phases, checkpoints, and production-isolation rules already exist. What is missing is a production composition and scheduler entry that advances factual PIT evidence only after a complete dual-ranking snapshot exists.

The design must preserve the modular-monolith dependency direction, use only decision-eligible total-return-adjusted data, avoid synthetic historical visibility, and keep every continuation below 60 seconds.

## Goals / Non-Goals

**Goals:**

- Restore bounded ETF synchronization after a historical memory spike without weakening the 512 MiB current-RSS admission gate.
- Establish one readiness state machine shared by orchestration, publication, selection, APIs, frontend presentation, evidence, and specs.
- Allow complete dual publication and production PIT capture when target-date coverage is at least 95 percent and 61-session warm-up coverage is at least 90 percent.
- Keep legacy v1 90-to-95-percent snapshots provisional instead of retroactively promoting them under policy v2.
- Schedule only due work, keep one worker, and preserve durable idempotent checkpoints.
- Compose and schedule one bounded PIT page after complete publication while guaranteeing no production decision side effects.
- Produce auditable real-trading-day evidence for coverage, provider health, resource use, publication, skips, and rollback.

**Non-Goals:**

- Changing ETF ranking factors, weights, score versions, candidates, or promotion thresholds.
- Training a model, searching parameters, or promoting research evidence.
- Changing portfolio allocation, tracked positions, risk-alert rules, notification policy, SMTP delivery, or execution provenance.
- Using raw Sina/efinance prices, stale caches, estimates, or post-cutoff backfills to increase decision or PIT coverage.
- Reconstructing historical ETF membership or receipt timestamps that were not factually recorded.
- Guaranteeing 252 PIT sessions or 40 independent primary samples within this implementation iteration.
- Splitting the modular monolith into microservices.

## Decisions

### 1. Use one versioned readiness policy

A single domain-level policy computes:

```text
blocked  = daily < 0.95 OR warmup < 0.90
complete = daily >= 0.95 AND warmup >= 0.90
```

`blocked` produces a waiting result and bounded catch-up only. Policy v2 treats the eligible subset as complete at the explicit 95/90 boundary and records excluded ETFs. The threshold change does not alter 300/500-session research-depth completion, raw-price prohibitions, or statistical promotion gates.

### 2. Derive effective state for legacy snapshots instead of rewriting them

Existing rows are not mutated. Selectors derive effective readiness state from target-date coverage, warm-up coverage, contract identity, surface summary, and the policy version that created the row. A legacy v1 90-to-95-percent run can be exposed only through the provisional research path; complete and actionable selectors reject it.

New runs persist readiness state and policy version in additive summary/evidence JSON. No destructive database migration is required.

### 3. Separate current RSS from lifetime and slice peaks

Memory admission uses current resident bytes:

- Linux reads current RSS from `/proc/self` using a bounded local read.
- Windows retains current working-set measurement.
- Other platforms use a current-process implementation where available and fail closed for production provider admission when current RSS cannot be measured.

The worker records `baseline_rss_bytes`, `current_rss_bytes`, `slice_peak_current_rss_bytes`, `lifetime_peak_rss_bytes`, and `rss_delta_bytes` separately. Only current RSS controls the 512 MiB admission check. Lifetime peak is telemetry and can never permanently poison future slices.

Raising the 512 MiB limit was rejected because it hides the measurement bug and weakens the 2-core/4-GB resource contract. Adding an external process-monitoring dependency was rejected for the first implementation because Linux exposes the required current value directly.

### 4. Keep provider work in the existing bounded worker

The current single-worker continuation, lease, 5-to-20-code adaptive batch, 500-row page, 5,000-row cap, 45-second admission cutoff, 55-second checkpoint target, and 60-second hard return remain. A separate service is not introduced.

If corrected current-RSS evidence still shows the API process cannot safely admit useful work, a later change may isolate the same modular-monolith command in a short-lived worker process. That fallback is deliberately not bundled into this change.

### 5. Make scheduler ticks due-aware before creating tracked runs

The scheduler performs a lightweight due check before creating a tracked JobRun or provider client. It records a compact run only when a bounded slice, provisional materialization, complete publication, or meaningful blocker evaluation is due. One live lease remains authoritative.

After a complete target-date snapshot exists, publication-readiness ticks perform no provider work. PIT capture uses a separate due-aware continuation and advances at most one page per trigger.

### 6. Compose PIT capture in the workflow layer

A workflow-layer composition supplies the existing PIT coordinator with frozen manifest construction and phase handlers. Scheduler and API remain orchestration only; strategy-lab rules remain research-only and do not import production tracking or notification domains.

Production PIT capture:

- requires a complete target-date dual snapshot;
- reads only persisted factual point-in-time inputs;
- records one immutable manifest and one durable phase/page checkpoint;
- performs no live provider request;
- advances at most one page and returns below 55 seconds;
- persists only research evidence and stable unavailable reasons.

### 7. Preserve three independent cutoff concepts

Evidence records:

- `market_decision_cutoff`: when the production ranking decision is defined;
- `data_receipt_cutoff`: latest receipt time allowed for that production run;
- `replay_visibility_cutoff`: latest factual availability time allowed for a historical PIT sample.

Post-close production data may support that day's production research result when allowed by its receipt contract, but it cannot be relabeled as historically visible before receipt. A result missing any required cutoff is unavailable for PIT validation or promotion.

## Risks / Trade-offs

- [A corrected current RSS may still exceed 512 MiB under real provider load] → Stop safely, preserve the checkpoint, report baseline/current/delta evidence, and consider a later isolated-process change rather than raising the limit.
- [Users may confuse a provisional research preview with a complete ranking] → Use a distinct readiness state, selector path, API metadata, amber workbench banner, and no actionable rank.
- [Legacy rows lack the new readiness field] → Derive effective state from immutable coverage and contract fields without rewriting historical records.
- [A due-aware scheduler could skip recoverable work after clock or lease errors] → Use stable reason codes, bounded stale-lease recovery, and tests at cadence and trading-session boundaries.
- [PIT capture may accumulate too slowly] → Report factual eligible dates, independent samples, folds, exclusions, and ETA inputs; never weaken sample gates or synthesize history.
- [Production composition could cross domain boundaries] → Place cross-domain calls in `app.services.workflows`, retain strategy-lab production isolation tests, and run the mandatory domain-boundary suite.

## Migration Plan

1. Add the shared readiness policy and threshold-boundary tests without changing production selection.
2. Add current-RSS measurement and split telemetry; verify Linux current-versus-lifetime behavior under injected readings.
3. Update the coordinator, materializer, publisher, selectors, API metadata, and frontend to use blocked/degraded/complete semantics.
4. Deploy with PIT scheduling disabled; run bounded production readiness slices and compare old/new telemetry.
5. Verify one complete policy-v2 dual publication at daily 95 percent and warm-up 90 percent, while a legacy v1 90-to-95-percent row remains provisional.
6. Enable the due-aware PIT continuation after complete publication and observe at least three different trading dates.
7. Run focused tests, domain-boundary checks, Ruff, frontend type/static checks, and strict OpenSpec validation, each under an explicit timeout.

Rollback disables PIT scheduling and the new selector policy, leaving immutable evidence, adjusted rows, and checkpoints intact. It does not delete provenance, restore lifetime-RSS admission, enable raw-price fallback, change live alert/email behavior, or rewrite archive history.

## Open Questions

- Production evidence will determine whether corrected current-RSS admission leaves enough headroom in the API process. Worker-process isolation remains a separate follow-up only if factual slices still cannot progress safely.
- The exact catch-up window may be tuned from real elapsed-time evidence, but cadence changes cannot increase concurrency or extend the 60-second limit.
