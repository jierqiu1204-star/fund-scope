## MODIFIED Requirements

### Requirement: Short ETF Signals Use Research Language
The system SHALL generate ranked short-term ETF signal items using deterministic trend, liquidity, and risk metrics, SHALL identify the top 20 ranked ETFs as the default intraday watch candidates, and SHALL frame every conclusion as research observation rather than trading instruction.

#### Scenario: Latest signals are returned
- **WHEN** the user runs short-term ETF signal generation
- **THEN** the system persists a signal run with ranked items, score breakdowns, risk flags, theme labels, and observation-oriented conclusions

#### Scenario: Top 20 watch candidates are identifiable
- **WHEN** a successful ETF signal run is persisted
- **THEN** the first 20 ranked ETF items are available to the intraday ETF watch service without recomputing the full ETF universe during market hours

#### Scenario: Prohibited trade language is absent
- **WHEN** the API returns a short-term ETF signal item
- **THEN** it does not include buy, sell, target price, expected return, or guaranteed profit fields
