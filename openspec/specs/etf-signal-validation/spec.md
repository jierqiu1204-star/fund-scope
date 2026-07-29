# etf-signal-validation Specification

## Purpose
TBD - created by archiving change enhance-etf-signal-validation-portfolio-risk. Update Purpose after archive.
## Requirements
### Requirement: ETF signal outcomes are validated against forward returns
The system SHALL validate ETF ranking labels and entry timing labels against future 1, 3, 5, and 10 trading-day outcomes using decision-eligible historical price data.

#### Scenario: Validation run computes label outcomes
- **WHEN** a validation run processes historical ETF signal items with enough future prices
- **THEN** it records sample count, average return, median return, win rate, worst forward drawdown, and horizon for each label combination

#### Scenario: Missing future price excludes a sample
- **WHEN** a signal item does not have decision-eligible future prices for a horizon
- **THEN** the system excludes that sample from that horizon and records the exclusion reason

### Requirement: Validation confidence is explicit
The system SHALL classify validation summaries as sufficient, limited, or insufficient based on sample count and data freshness.

#### Scenario: Label has too few samples
- **WHEN** a label combination has fewer than the configured minimum sample count
- **THEN** the validation result is marked `insufficient_samples` and MUST NOT be displayed as reliable evidence

#### Scenario: Validation data is stale
- **WHEN** the latest validation run is older than the configured freshness window
- **THEN** the UI and API indicate that validation evidence is stale

### Requirement: Validation does not alter live labels automatically
The system SHALL keep label, factor, and ranking evidence separate from live ranking labels and MUST NOT automatically change labels, scores, factor weights, ranking contracts, allocation, or notification behavior based only on validation output.

#### Scenario: Validation shows weak evidence
- **WHEN** a label combination or candidate factor has poor historical forward outcomes
- **THEN** the system displays the weak evidence but does not silently rewrite the live label, score, or downstream rule

#### Scenario: Validation shows strong factor evidence
- **WHEN** a candidate factor passes the registered research gates
- **THEN** the system may mark it eligible for a separate proposal but MUST NOT publish or weight it automatically

### Requirement: ETF Signal Validation Prevents Lookahead Bias
The ETF signal validation process SHALL only use information that would have been available at the signal timestamp.

#### Scenario: Historical signal is replayed
- **WHEN** the validator evaluates a signal generated on a historical date
- **THEN** it uses ranking inputs, quote reliability, and price history available on or before that date

#### Scenario: Future data would affect the signal
- **WHEN** a metric requires data after the signal timestamp
- **THEN** the validator excludes that metric from signal reconstruction and records a lookahead-prevention reason

### Requirement: ETF Signal Validation Tracks Recent Degradation
The ETF signal validation process SHALL compare recent label outcomes with longer-window label outcomes.

#### Scenario: Recent outcomes weaken
- **WHEN** recent forward outcomes are materially worse than the longer-window summary for the same label combination
- **THEN** the validation result marks the label as recently weakened and includes the affected horizon

#### Scenario: Recent sample is too small
- **WHEN** recent completed samples are below the configured minimum
- **THEN** the validation result marks recent comparison as insufficient instead of producing a degradation claim

### Requirement: ETF signal validation supports historical replay
The system SHALL support historical replay validation for ETF observation labels and entry timing labels, using only data available on or before each replay date.

#### Scenario: Historical replay reconstructs labels
- **WHEN** a historical replay validation run evaluates an ETF on a replay date
- **THEN** it reconstructs the observation label, entry timing label, score inputs, and data reliability using only prices and metrics dated on or before that replay date

#### Scenario: Future data is unavailable during reconstruction
- **WHEN** a metric or source value would require data after the replay date
- **THEN** the validator excludes that value from label reconstruction and records a no-lookahead exclusion reason

#### Scenario: Historical replay records rule version
- **WHEN** a historical replay validation run is created
- **THEN** it records the signal rule version, replay date range, universe source, horizons, and data coverage summary

### Requirement: ETF historical replay records forward outcomes
The system SHALL calculate forward ETF outcomes after each replayed signal for configured 1, 3, 5, and 10 trading-day horizons.

#### Scenario: Forward outcome is completed
- **WHEN** the replayed signal has enough future decision-eligible ETF daily prices for a horizon
- **THEN** the system records forward return, maximum adverse excursion, maximum favorable excursion, horizon end date, and price source

#### Scenario: Forward outcome is not completed
- **WHEN** the replayed signal lacks enough future decision-eligible ETF daily prices for a horizon
- **THEN** the system excludes that sample for the horizon and records the missing-data reason

#### Scenario: Replay uses daily close only
- **WHEN** historical replay calculates entry and future prices
- **THEN** it uses verified ETF daily close data and MUST NOT use stale intraday quotes, estimated prices, or AI-generated values

### Requirement: ETF historical replay aggregates label evidence
The system SHALL aggregate historical replay outcomes by observation label, entry timing label, horizon, asset type, and rule version.

#### Scenario: Label combination is summarized
- **WHEN** replay samples exist for a label combination and horizon
- **THEN** the system records sample count, average return, median return, win rate, worst forward drawdown, median maximum favorable excursion, data coverage, and confidence state

#### Scenario: Label combination has insufficient samples
- **WHEN** a label combination has fewer than the configured minimum completed samples
- **THEN** the system marks the evidence as `insufficient` and MUST NOT expose it as reliable evidence

#### Scenario: Recent evidence weakens
- **WHEN** recent replay outcomes are materially worse than the longer replay summary for the same label combination
- **THEN** the system marks the label evidence as recently weakened and includes the affected horizon

### Requirement: Historical replay evidence remains research-only
The system SHALL keep historical replay validation separate from live ranking, portfolio allocation, tracked-position alerts, and notification sending.

#### Scenario: Historical replay finds weak evidence
- **WHEN** a label combination has weak historical replay outcomes
- **THEN** the system displays the evidence but does not automatically rewrite current labels, scores, portfolio weights, or email alert decisions

#### Scenario: Historical replay run completes
- **WHEN** a historical replay validation run finishes successfully
- **THEN** it updates validation evidence only and MUST NOT create tracked-position alerts or notification logs

### Requirement: ETF signal validation is grouped by evidence contract
ETF signal validation SHALL group historical outcomes by the signal contract fields used by the short-term workbench.

#### Scenario: Label outcome is calculated
- **WHEN** ETF label validation calculates forward returns and drawdowns
- **THEN** it groups results by observation label, entry timing label, signal rule version, data reliability state, and signal date

#### Scenario: Current signal version changes
- **WHEN** the signal rule version changes
- **THEN** old validation results are not presented as current-version validation unless explicitly marked as old evidence

### Requirement: ETF signal validation reports evidence quality
ETF signal validation SHALL report sample sufficiency, coverage, and future-window completion for each label combination.

#### Scenario: Enough completed samples exist
- **WHEN** a label combination has enough completed future windows
- **THEN** the validation summary includes sample count, win rate, average forward return, maximum drawdown, and confidence state

#### Scenario: Future window is incomplete
- **WHEN** a label was generated recently and the required forward window has not elapsed
- **THEN** the validation excludes that item from completed-sample statistics and records it as pending

### Requirement: ETF Signal Validation Supports Factor And Rank Evidence
ETF signal validation SHALL support factor-level and ranked-portfolio outcome evidence in addition to label-combination evidence.

#### Scenario: Factor evidence is generated
- **WHEN** a registered factor experiment completes valid point-in-time samples
- **THEN** validation stores factor IC, quantile outcomes, paired baseline comparisons, costs, turnover, rank churn, coverage, exclusions, and uncertainty by experiment hash

#### Scenario: Ranking-surface evidence is generated
- **WHEN** a ranked candidate is evaluated
- **THEN** validation records the ranking contract ID and hash, score field, signal cutoff, execution convention, future horizon, and common-support sample identity

#### Scenario: Factor evidence lacks matching contract
- **WHEN** samples mix ranking contracts, experiment hashes, execution rules, or data-provenance policies
- **THEN** the validator separates or rejects them instead of aggregating incompatible evidence

### Requirement: ETF ranking validation has one fixed primary estimand
ETF ranking validation SHALL use the paired difference in five-session net return between a candidate Top10 and the frozen baseline Top10 on identical signal dates and common-support ETFs as its only primary ranking endpoint.

#### Scenario: Primary outcome is computed
- **WHEN** a non-overlapping signal date has complete T+1 and five-session adjusted closes
- **THEN** the validator applies 5 bps fee and 5 bps slippage per side, charges realized turnover, and records paired candidate and baseline net returns

#### Scenario: Exploratory cell is favorable
- **WHEN** a Top5, Top20, 1-day, 3-day, or 10-day exploratory cell outperforms while the primary endpoint fails
- **THEN** the candidate remains ineligible for promotion

### Requirement: ETF ranking validation is chronological and multiplicity controlled
ETF ranking validation SHALL use expanding chronological walk-forward folds, purge overlapping labels, apply a 10-session embargo, use date-block bootstrap uncertainty, and adjust at most three primary candidate comparisons using Holm-Bonferroni.

#### Scenario: Fold boundary overlaps an outcome window
- **WHEN** training or validation dates overlap the next partition's forward label window
- **THEN** the overlapping dates are purged and the declared embargo is applied

#### Scenario: Candidate confidence is evaluated
- **WHEN** primary comparisons complete
- **THEN** promotion uses the Holm-adjusted 95 percent interval and cannot use an unadjusted or secondary interval

### Requirement: ETF ranking promotion has hard sample and risk gates
ETF ranking validation SHALL report `insufficient_data` unless dual production coverage is at least 95 percent, at least 252 factual point-in-time sessions and 40 non-overlapping primary dates exist, and at least three chronological folds complete.

#### Scenario: Warm-up exists but promotion sample is short
- **WHEN** 61 adjusted sessions are available but any formal sample gate is missing
- **THEN** ranking may be displayed as research while promotion remains `insufficient_data`

#### Scenario: Promotion evidence is complete
- **WHEN** the adjusted 95 percent primary interval is above zero, required fold and regime signs are stable, maximum drawdown is no more than two percentage points worse than baseline, and all coverage, non-finite, concentration, clone-policy, exclusion, and raw-price gates pass
- **THEN** the evidence may be marked `promotion_eligible`

### Requirement: ETF ranking holdout is consumed once
ETF ranking validation SHALL freeze the manifest and non-holdout evidence before holdout access and SHALL permit the final holdout to be consumed once per immutable experiment identity.

#### Scenario: Holdout was already consumed
- **WHEN** a caller retries, retunes, or changes a candidate under the same holdout identity
- **THEN** the validator rejects the operation and requires a new pre-registered experiment

