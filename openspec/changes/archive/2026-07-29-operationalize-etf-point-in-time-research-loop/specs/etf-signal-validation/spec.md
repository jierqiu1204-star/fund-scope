## ADDED Requirements

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
