## Context

FundScope now has a mature short-term ETF workflow: daily ranking, intraday ETF watch, tracked holdings, email reminders, strict market-data reliability labels, and AI explanations constrained to research text. The next problem is auditability. A user can see a label, a portfolio weight, or an email result, but cannot always inspect the evidence and decision path behind it.

This change is intentionally incremental. It must not replace the existing ranking engine, intraday watch job, tracked-position alert engine, login flow, SMTP settings, or broker/manual-trading boundary. It adds evidence and audit output around those systems so the user can understand what happened and why.

## Goals / Non-Goals

**Goals:**

- Make ETF buy-observation labels and entry timing states evidence-backed and visibly graded by sample quality.
- Make ETF observation portfolio weights explainable, including inclusion and exclusion reasons.
- Add an alert audit trail for tracked positions, covering sent, skipped, suppressed, web-only, and data-ineligible outcomes.
- Preserve strict data-reliability gates for all decision paths.
- Keep UI copy beginner-friendly: clear conclusion first, expandable technical evidence second.
- Define implementation boundaries so subagents can work independently without breaking current production flows.

**Non-Goals:**

- No automatic trading, broker integration, or securities-account synchronization.
- No AI-generated buy/sell commands.
- No replacement of the existing ranking, watch, notification, or authentication systems.
- No heavy new quant framework dependency in this iteration.
- No claim that historical validation guarantees future returns.

## Decisions

### 1. Audit Around Existing Decision Engines

Decision: Alert audit records SHALL observe and explain the current tracked-position alert engine instead of becoming a second decision engine.

Rationale: The current system already protects against stale data and unwanted emails. Duplicating decision logic would create conflicting behavior and financial risk.

Alternative rejected: Recompute alert eligibility in a new audit service. This would drift from the actual send path and make the audit untrustworthy.

### 2. Evidence Is Separate From Ranking

Decision: Label evidence SHALL be displayed as confidence and history, but it MUST NOT directly rewrite ranking labels inside this change.

Rationale: The user needs to know whether labels worked historically, but changing labels based on validation creates a new strategy change that needs separate review.

Alternative rejected: Auto-demote all low-confidence labels. This could silently change ranking behavior and confuse existing workflows.

### 3. Portfolio Explanations Extend Current Optimizer Output

Decision: Portfolio optimization SHALL keep the existing 30% single-ETF cap and add explanation fields for included and excluded candidates.

Rationale: The current optimizer behavior should remain stable. The missing piece is transparency: why one ETF got a weight and another got zero.

Alternative rejected: Add PyPortfolioOpt immediately. It is useful as a reference, but solver dependency and deployment risk are unnecessary for this iteration.

### 4. Structured Audit Data First, AI Summary Second

Decision: The backend SHALL produce deterministic structured audit fields first. AI MAY summarize those fields, but MUST NOT alter the audit result.

Rationale: Financial auditability requires reproducible data. AI text can help a beginner read it, but the source of truth must be deterministic.

### 5. Subagent Work Boundaries

Decision: If implementation is delegated to 5.3 codex subagents, split work by file boundary:

- Backend evidence/API subagent: validation and portfolio explanation fields only.
- Backend audit subagent: alert audit persistence and read endpoints only.
- Frontend subagent: display-only changes under `/short-term` and tracked-holding panels only.
- Test subagent: targeted tests for evidence, portfolio explanations, and audit outcomes.

Each subagent MUST preserve existing API fields and avoid refactoring unrelated modules.

## Risks / Trade-offs

- [Risk] Evidence can look authoritative with too few samples.
  Mitigation: Always show sample count and confidence; insufficient samples MUST be clearly labeled.

- [Risk] Audit records may grow quickly during intraday watch.
  Mitigation: Store meaningful state transitions and send/suppression decisions, not every unchanged minute.

- [Risk] Extra UI detail can overwhelm a beginner.
  Mitigation: Show one short Chinese explanation first, with expandable evidence/audit details.

- [Risk] New audit table or JSON could miss historical records.
  Mitigation: Historical alerts can show partial audit derived from existing alert/log rows; full audit applies going forward.

- [Risk] Subagents may broaden scope.
  Mitigation: Tasks explicitly forbid changes to login, SMTP setup, broker integration, automatic trading, and ranking formula replacement.

## Migration Plan

1. Add audit persistence using a backward-compatible migration or existing JSON fields where sufficient.
2. Add read APIs behind existing authentication and ownership checks.
3. Extend existing short-term and tracked-position responses with optional fields.
4. Deploy with current jobs unchanged; audit recording begins after deployment.
5. Rollback by hiding new frontend sections and disabling audit writes; existing ranking and email paths remain intact.

## Open Questions

- Should alert audit retention match intraday quote retention, or keep all email-relevant audit records permanently?
- What minimum sample count should display `证据较充分`: 30, 50, or 100 observations?
- Should portfolio explanation show exact correlation values by default, or hide them behind an expandable detail block?
