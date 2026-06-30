## ADDED Requirements

### Requirement: ETF Dynamic Threshold Context Is Calculated
The system SHALL calculate a dynamic threshold context for ETF research and tracked-position evaluation using each ETF's own recent behavior and available peer context.

#### Scenario: ETF has enough history
- **WHEN** an ETF has enough recent daily data with decision-eligible prices
- **THEN** the system computes volatility unit, ATR-style range, realized volatility, median absolute return, return percentiles, drawdown context, and threshold values

#### Scenario: ETF lacks enough history
- **WHEN** an ETF lacks enough usable history to compute dynamic thresholds
- **THEN** the system marks threshold mode as conservative default or data insufficient and explains why asset-specific thresholds are unavailable

### Requirement: Thresholds Adapt By Asset Bucket And Volatility
The system SHALL adjust ETF thresholds by asset bucket and recent volatility instead of applying one fixed cutoff to all ETFs.

#### Scenario: High-volatility ETF is evaluated
- **WHEN** a high-volatility ETF such as a semiconductor or technology ETF has a daily move within its normal volatility band
- **THEN** the system does not automatically mark it as overextended solely because it exceeds a fixed low-volatility cutoff

#### Scenario: Low-volatility ETF is evaluated
- **WHEN** a low-volatility ETF such as a bond, bank, or dividend ETF has an unusually large move relative to its own history
- **THEN** the system may mark it as overextended or abnormal even if the absolute move is below a high-volatility ETF's normal band

### Requirement: Historical Percentiles Inform Thresholds
The system SHALL compare recent ETF returns and pullbacks against the ETF's own historical percentiles.

#### Scenario: Recent move is historically extreme
- **WHEN** today's move, 5-day return, 20-day return, or 60-day return is above the ETF's configured historical percentile threshold
- **THEN** the dynamic threshold context marks the move as historically elevated and exposes the percentile evidence

#### Scenario: Recent move is normal for the ETF
- **WHEN** today's move is within the ETF's normal historical percentile range
- **THEN** the system avoids using only fixed absolute thresholds to label the move as chase risk

### Requirement: Theme Peer Context Is Used When Available
The system SHALL use normalized theme or sector peer context when available, without depending on it when unavailable.

#### Scenario: Theme peers are available
- **WHEN** an ETF has a theme group and enough comparable ETF peers
- **THEN** the threshold context includes theme-relative rank or percentile for score, return, volatility, and drawdown where computable

#### Scenario: Theme peers are unavailable
- **WHEN** theme classification or peer samples are unavailable
- **THEN** the system falls back to own-history dynamic thresholds and marks peer context as unavailable rather than guessing

### Requirement: Premium And Discount Are Risk Gates
The system SHALL use trustworthy ETF premium/discount data as a risk gate for entry timing and portfolio eligibility.

#### Scenario: ETF has high premium
- **WHEN** an ETF has a trustworthy premium above the configured high-premium threshold
- **THEN** the system marks premium risk in threshold context and prevents a confident entry-timing label such as `健康回踩` or `趋势延续`

#### Scenario: ETF premium is extreme
- **WHEN** an ETF has a trustworthy premium above the configured extreme-premium threshold
- **THEN** the system excludes the ETF from weighted observation portfolio eligibility and explains the premium risk

#### Scenario: Premium data is unavailable
- **WHEN** an ETF requires premium awareness but IOPV or premium data is unavailable or stale
- **THEN** the system marks premium context as unavailable and does not claim the ETF is safe from premium risk

### Requirement: Dynamic Thresholds Are Rule-Versioned And Auditable
The system SHALL expose rule version and threshold inputs for dynamic ETF threshold decisions.

#### Scenario: ETF threshold context is returned
- **WHEN** an ETF ranking, detail, tracked position, or portfolio explanation uses dynamic thresholds
- **THEN** the response includes rule version, threshold mode, key threshold values, input source, and a human-readable reason

#### Scenario: Historical replay is run
- **WHEN** label validation or historical replay evaluates dynamic labels
- **THEN** the replay records the dynamic threshold rule version used for each evaluated sample

### Requirement: AI Does Not Decide Dynamic Thresholds
The system SHALL keep dynamic threshold calculation deterministic and SHALL NOT allow AI output to decide thresholds or override rule decisions.

#### Scenario: AI explanation is available
- **WHEN** AI generates a research explanation for a dynamic threshold label
- **THEN** the explanation may describe the rule result but cannot change the threshold value, label, or alert decision

#### Scenario: AI explanation is unavailable or invalid
- **WHEN** AI is unavailable, fails, or uses prohibited financial wording
- **THEN** the system uses rule explanation and keeps the deterministic threshold result unchanged
