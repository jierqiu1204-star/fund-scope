## Why

FundScope already shows ETF rankings, labels, observation weights, tracked holdings, and email reminders, but the user still needs clearer evidence for whether labels have worked, why a portfolio weight is assigned, and why an alert was or was not sent. This change makes the short-term ETF workflow more auditable without changing the current trading boundary: research and reminders only, no broker connection and no automatic orders.

## What Changes

- Strengthen ETF label effectiveness evidence:
  - Show historical forward outcome statistics for buy-observation labels and entry timing labels.
  - Surface sample size, confidence level, freshness, exclusion reasons, and recent degradation warnings.
  - Mark weak or insufficient evidence clearly instead of presenting every label as equally reliable.
- Strengthen ETF observation portfolio weighting:
  - Keep the single ETF weight cap at `30%`.
  - Explain every included weight using score, validation confidence, volatility, drawdown, liquidity, correlation, and theme concentration.
  - Explain every excluded candidate with a concrete reason such as stale data, insufficient evidence, excessive correlation, or liquidity gate failure.
- Add alert audit capability:
  - Provide a per-position audit trail for email-sent, web-only, skipped, suppressed, and data-ineligible decisions.
  - Record the data source, quote freshness, signal type, threshold context, duplicate/cooldown reason, and SMTP result.
  - Let the UI answer: "Why did I receive this email?" and "Why did I not receive an email?"
- Keep existing live watch and reminder links intact:
  - Do not relax strict data-reliability gates.
  - Do not send emails for `risk_warning`, stale quotes, missing IOPV, or display-only data.
  - Do not change login, SMTP configuration, or tracked-position creation flow except for adding audit visibility.
- Keep AI constrained:
  - AI may explain evidence, weights, and alert reasons.
  - AI must not modify labels, portfolio weights, thresholds, or email-send decisions.

## Capabilities

### New Capabilities
- `etf-alert-audit`: Provides an auditable trail for tracked-position alert decisions, including sent, skipped, suppressed, web-only, and data-ineligible outcomes.

### Modified Capabilities
- `short-term-research`: Adds visible label evidence, confidence, and degradation context to ETF ranking and detail views.
- `short-etf-research`: Adds stronger ETF-specific evidence and observation portfolio explanations while preserving existing ranking fields.
- `tracked-position-exit-strategy`: Adds auditable decision context for whether a tracked position generated a sell/reduce reminder, web-only note, or no action.
- `investment-reminders`: Adds delivery-level audit requirements for SMTP sent/failed/skipped decisions and duplicate/cooldown suppression.
- `intraday-etf-watch`: Adds watch-scope and quote-freshness audit output without changing trading-session behavior.
- `market-data-reliability`: Ensures validation, portfolio weights, and alert audits label data as decision-eligible or display-only.

## Impact

- Backend:
  - Add or extend services that compute label evidence summaries, portfolio explanation details, and alert audit events.
  - Add persistence for alert audit records or structured audit JSON linked to tracked-position alerts.
  - Keep existing notification and intraday watch services as the decision source; audit must observe and explain, not replace.
- API:
  - Extend `/short-term` and ETF observation portfolio responses with evidence and explanation fields.
  - Add read endpoints for alert audit history by tracked position.
  - Preserve existing response fields for frontend compatibility.
- Frontend:
  - `/short-term` should show label confidence and "why this weight" in compact beginner-facing copy.
  - "我的持仓" should show a timeline of alert decisions and whether each one sent email, stayed web-only, or was suppressed.
- Jobs:
  - Reuse existing daily ranking/validation/portfolio jobs where possible.
  - Reuse existing intraday watch and daily tracked-position checks; add audit recording around their decisions.
- Dependencies:
  - No new heavy quant framework in this change.
  - No broker, payment, or securities-account integration.
