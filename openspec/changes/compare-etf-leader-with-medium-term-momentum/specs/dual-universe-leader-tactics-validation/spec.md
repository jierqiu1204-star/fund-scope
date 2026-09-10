## ADDED Requirements

### Requirement: ETF breakout V2 has a separately frozen capital-account diagnostic
The system SHALL provide a research-only continuous capital account for the existing ETF breakout V2 formula under a separate account-policy identity. It MUST reuse the frozen V2 candidate and lifecycle definitions, distinguish screening and confirmation from execution, and retain the existing ETF five-session primary endpoint and promotion gates unchanged. Repair, base-launch, A-share candidates and legacy V1 events SHALL NOT be mixed into this account.

#### Scenario: A watchlist observation exists
- **WHEN** an ETF appears only in a post-close watchlist or has not reached causal confirmation
- **THEN** the account does not buy it and does not treat its next eligible observation date as an execution date

#### Scenario: A compatible confirmation occurs
- **WHEN** a confirmation is available by decision cutoff C and satisfies the observation's earliest eligible date
- **THEN** a permitted entry executes at the next exchange session's eligible adjusted close, with C retained as the decision date and no second one-session shift

#### Scenario: A compatible exit occurs
- **WHEN** the frozen lifecycle records an actionable research exit by cutoff X
- **THEN** the account uses the existing exit decision at X and executes at the next exchange session, without using later prices or backdating a late-received transition

### Requirement: ETF breakout account allocations are deterministic and cash constrained
The ETF breakout account SHALL start in cash, contain at most ten unique non-clone ETF positions, process exits before same-cutoff entries, and fill free slots from newly confirmed observations in their frozen candidate order with asset-code tie breaking. Each occupied slot SHALL target ten percent of pre-trade capital only when the selected membership changes; unfilled slots remain cash. Existing positions SHALL not be displaced solely by a better-ranked new signal, and a same-cutoff exit SHALL take precedence over entry for the same asset or clone group.

#### Scenario: Entry demand exceeds free slots
- **WHEN** more newly confirmed assets exist than available slots
- **THEN** deterministic ranking selects only the available count, omitted signals are recorded without creating a delayed-entry queue, and a held asset is not counted twice

#### Scenario: Membership is unchanged
- **WHEN** no accepted entry or exit changes the account membership
- **THEN** no rebalance target is emitted, held quantities drift with prices and no synthetic trading cost is charged

#### Scenario: Membership changes
- **WHEN** at least one accepted entry or exit changes the selected set
- **THEN** all remaining and new positions rebalance toward ten-percent slot targets, actual trades in retained positions incur costs, sells precede buys, and buys are proportionally reduced to available cash including fees

### Requirement: V2 account evidence remains distinct from event and production evidence
The account evidence SHALL identify its V2 formula, observation and transition inputs, clone selection, allocation policy, execution, costs, data mode and comparison interval. It SHALL report unavailable valuations without stale-price substitution and SHALL NOT describe event averages, account diagnostics, or current-vintage history as verified PIT ranking alpha or factual live execution.

#### Scenario: A complete account result is available
- **WHEN** cash, shares, trades and compatible valuation prices support a continuous interval
- **THEN** the report exposes its actual gross and net capital results separately from five-session event statistics, with notification and execution provenance marked none or simulated

#### Scenario: A held ETF has no eligible price
- **WHEN** the account cannot value or execute a held ETF using an eligible price
- **THEN** the affected continuous interval stops with an explicit reason and cannot be repaired using an old close, zero return or fictitious sale
