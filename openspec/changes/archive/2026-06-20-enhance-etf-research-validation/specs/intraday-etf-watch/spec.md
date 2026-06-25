## ADDED Requirements

### Requirement: Intraday Ranking Splits Base Score And Live Adjustment
The system SHALL expose daily base score, intraday adjustment score, live total score, score source, and readable contribution reasons for ETF live ranking.

#### Scenario: Fresh intraday quote exists
- **WHEN** an ETF has a fresh eligible intraday quote during market hours
- **THEN** the API returns score_source=intraday, daily base score, intraday adjustment score, live total score, quote time, and contribution reasons

#### Scenario: Market is closed or quote is stale
- **WHEN** the market is closed or the ETF quote is stale, estimated, or missing quote time
- **THEN** the API returns score_source=daily or unavailable and MUST NOT present the score as real-time

### Requirement: Intraday Watchlist Includes Top Signals And Observed Assets
The system SHALL watch ETF candidates from the latest top ranked daily signals, short-watch labels, high-watch labels, and active tracked positions.

#### Scenario: Watchlist is built from multiple sources
- **WHEN** a successful ETF signal run exists
- **THEN** the intraday watchlist includes top 20 ranked ETFs, ETFs labeled 短线观察, ETFs labeled 高位观察, and active tracked ETFs, with source tags for each item

#### Scenario: Watchlist source is visible
- **WHEN** the UI renders live ranking cards
- **THEN** it shows whether the ETF is watched because of top ranking, short-watch label, high-watch label, or user tracking

### Requirement: Intraday Timing Labels Are Data-Gated
The system SHALL calculate intraday timing labels only from fresh verified or alternate-provider intraday quotes.

#### Scenario: Timing label uses fresh quote
- **WHEN** fresh intraday quote data is available
- **THEN** the system may return timing labels such as 健康回踩, 趋势延续, 冲高别追, or 跌破等待 with contribution reasons

#### Scenario: Timing label cannot use fallback data
- **WHEN** only daily close, stale quote, missing quote time, or estimated price is available
- **THEN** the system returns 数据不足 or daily timing reference and MUST NOT use it for intraday email decisions
