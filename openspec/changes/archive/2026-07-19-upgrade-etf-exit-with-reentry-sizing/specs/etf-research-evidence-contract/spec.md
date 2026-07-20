## ADDED Requirements

### Requirement: ETF evidence contract records exit V2 versions
The ETF research evidence contract SHALL record exit action version, immutable exposure-baseline and absolute-target semantics, execution provenance, reentry version, notification policy version, bucket-threshold version, and whether the evidence can influence live rules.

#### Scenario: Exit V2 evidence is created
- **WHEN** ETF exit V2 validation runs
- **THEN** the evidence records signal contract hash, exit action version, exposure/action-cycle identity, absolute target semantics, simulated or owner-confirmed execution provenance, reentry version, notification policy version, bucket-threshold version, execution model, data cutoff, and research-only status

#### Scenario: Legacy relative-action evidence is read
- **WHEN** historical evidence compounds relative reductions, derives cooldown from recommendation/email state, or treats notification repeats as trades
- **THEN** the contract marks it as `legacy_current_semantics/old_contract`, keeps it research-only, and MUST NOT present it as current lifecycle proof

#### Scenario: Evidence is not approved
- **WHEN** exit V2 evidence is a candidate or has insufficient samples
- **THEN** the evidence contract marks it as research-only and live tracked-position rules MUST NOT consume it

### Requirement: ETF evidence contract stores baseline comparison
The ETF research evidence contract SHALL store comparisons against TopN fixed hold, current live exit rules, guard-only behavior, and V2 reentry behavior.

#### Scenario: Baselines are recorded
- **WHEN** an ETF exit V2 validation result is stored
- **THEN** the contract records return, max drawdown, missed upside, protection success, turnover, alert count, and sample count for every baseline

#### Scenario: TopN hold outperforms V2
- **WHEN** TopN fixed hold materially outperforms exit V2 without worse drawdown
- **THEN** the evidence marks V2 as not suitable for live promotion

### Requirement: ETF evidence status blocks stale or mismatched exit evidence
The ETF research evidence contract SHALL prevent stale, mismatched, or daily-only exit evidence from being presented as current intraday exit proof.

#### Scenario: Contract hash differs
- **WHEN** exit evidence was generated from a different signal, allocation, exit, or reentry contract hash
- **THEN** the evidence status is `版本不一致` or `旧口径结果`

#### Scenario: Intraday workflow lacks intraday evidence
- **WHEN** the evidence only uses daily close but the live workflow uses intraday email alerts
- **THEN** the evidence is labeled as daily reference and MUST NOT be shown as proof for intraday exit behavior
