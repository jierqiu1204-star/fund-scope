from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    select,
)
from sqlalchemy import (
    Index as SaIndex,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class PublishedSnapshotImmutableError(ValueError):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    recipient_email: Mapped[str] = mapped_column(String(255))
    reminder_day: Mapped[int] = mapped_column(Integer, default=1)
    reference_index_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    base_monthly_amount: Mapped[float] = mapped_column(Float, default=0.0)
    etf_trading_capital: Mapped[float] = mapped_column(Float, default=10000.0)
    allow_full_exit: Mapped[bool] = mapped_column(Boolean, default=True)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Fund(Base):
    __tablename__ = "funds"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))
    tracking_index_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_allocation: Mapped[float] = mapped_column(Float, default=0.0)
    is_watchlist: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FundNavHistory(Base):
    __tablename__ = "fund_nav_history"
    __table_args__ = (UniqueConstraint("fund_code", "nav_date", name="uq_fund_nav_history"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.code", ondelete="CASCADE"))
    nav_date: Mapped[date] = mapped_column(Date)
    nav: Mapped[float] = mapped_column(Float)
    accumulated_nav: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FundMetric(Base):
    __tablename__ = "fund_metrics"
    __table_args__ = (UniqueConstraint("fund_code", "metric_date", name="uq_fund_metrics"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.code", ondelete="CASCADE"))
    metric_date: Mapped[date] = mapped_column(Date)
    return_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    tracking_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fund_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    news_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    data_quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Index(Base):
    __tablename__ = "indices"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(32))
    is_watchlist: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IndexValuationHistory(Base):
    __tablename__ = "index_valuation_history"
    __table_args__ = (UniqueConstraint("index_code", "valuation_date", name="uq_index_valuation_history"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(ForeignKey("indices.code", ondelete="CASCADE"))
    valuation_date: Mapped[date] = mapped_column(Date)
    pe: Mapped[float] = mapped_column(Float)
    pb: Mapped[float] = mapped_column(Float)
    dividend_yield: Mapped[float] = mapped_column(Float, default=0.0)
    pe_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_window: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.code", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares: Mapped[float] = mapped_column(Float)
    proceeds: Mapped[float | None] = mapped_column(Float, nullable=True)
    nav_at_trade: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    traded_at: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HoldingsSnapshot(Base):
    __tablename__ = "holdings_snapshot"
    __table_args__ = (UniqueConstraint("portfolio_id", "snapshot_date", "fund_code", name="uq_holdings_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.code", ondelete="CASCADE"))
    shares: Mapped[float] = mapped_column(Float)
    cost_basis: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Stock(Base):
    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str] = mapped_column(String(128))
    is_candidate: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StockPriceHistory(Base):
    __tablename__ = "stock_price_history"
    __table_args__ = (UniqueConstraint("stock_code", "trade_date", name="uq_stock_price_history"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(ForeignKey("stocks.code", ondelete="CASCADE"))
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StockFundamental(Base):
    __tablename__ = "stock_fundamentals"
    __table_args__ = (UniqueConstraint("stock_code", "report_date", name="uq_stock_fundamentals"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(ForeignKey("stocks.code", ondelete="CASCADE"))
    report_date: Mapped[date] = mapped_column(Date)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_asset: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_cashflow: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StockMetric(Base):
    __tablename__ = "stock_metrics"
    __table_args__ = (UniqueConstraint("stock_code", "metric_date", name="uq_stock_metrics"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(ForeignKey("stocks.code", ondelete="CASCADE"))
    metric_date: Mapped[date] = mapped_column(Date)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    momentum_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TradableEtf(Base):
    __tablename__ = "tradable_etfs"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str] = mapped_column(String(16))
    theme_tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    trading_rule_label: Mapped[str] = mapped_column(String(64))
    asset_class: Mapped[str] = mapped_column(String(64))
    is_short_term_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    is_watchlist: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfUniverseMembership(Base):
    __tablename__ = "etf_universe_memberships"
    __table_args__ = (
        UniqueConstraint("etf_code", "effective_from", name="uq_etf_universe_membership_effective_from"),
        SaIndex("ix_etf_universe_memberships_effective_lookup", "etf_code", "effective_from", "effective_to"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    tracked_underlying_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfPriceHistory(Base):
    __tablename__ = "etf_price_history"
    __table_args__ = (
        UniqueConstraint("etf_code", "trade_date", name="uq_etf_price_history"),
        SaIndex("ix_etf_price_history_trade_date_decision_eligible", "trade_date", "decision_eligible"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)
    pct_change: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    raw_price_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    research_adjusted_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    research_price_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    adjustment_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decision_ineligibility_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EtfMetric(Base):
    __tablename__ = "etf_metrics"
    __table_args__ = (UniqueConstraint("etf_code", "metric_date", name="uq_etf_metric"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    metric_date: Mapped[date] = mapped_column(Date)
    return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_turnover_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_score: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfThemeExposure(Base):
    __tablename__ = "etf_theme_exposures"
    __table_args__ = (UniqueConstraint("etf_code", "theme", name="uq_etf_theme_exposure"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    theme: Mapped[str] = mapped_column(String(64))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(64), default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfThemeProfile(Base):
    __tablename__ = "etf_theme_profiles"
    __table_args__ = (
        SaIndex("ix_etf_theme_profiles_theme_group", "theme_group"),
        SaIndex("ix_etf_theme_profiles_primary_theme", "primary_theme"),
    )

    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"), primary_key=True)
    asset_bucket: Mapped[str] = mapped_column(String(64), default="unknown")
    theme_group: Mapped[str] = mapped_column(String(64), default="unknown")
    primary_theme: Mapped[str] = mapped_column(String(64), default="未分类")
    secondary_themes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    classification_source: Mapped[str] = mapped_column(String(64), default="unknown")
    classification_confidence: Mapped[str] = mapped_column(String(32), default="unknown")
    classification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfDataHealth(Base):
    __tablename__ = "etf_data_health"

    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest_price_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    successful_rows: Mapped[int] = mapped_column(Integer, default=0)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_attempted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfIntradayQuote(Base):
    __tablename__ = "etf_intraday_quotes"
    __table_args__ = (
        SaIndex("uq_etf_intraday_quote_code_time", "etf_code", "quote_time", unique=True),
        SaIndex("ix_etf_intraday_quotes_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    quote_time: Mapped[datetime] = mapped_column(DateTime)
    trade_date: Mapped[date] = mapped_column(Date)
    latest_price: Mapped[float] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    iopv: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_discount_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="akshare")
    freshness_status: Mapped[str] = mapped_column(String(32), default="fresh")
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfIntradayLatestQuote(Base):
    __tablename__ = "etf_intraday_latest_quotes"
    __table_args__ = (SaIndex("ix_etf_intraday_latest_quotes_quote_time", "quote_time"),)

    etf_code: Mapped[str] = mapped_column(
        ForeignKey("tradable_etfs.code", ondelete="CASCADE"), primary_key=True
    )
    quote_time: Mapped[datetime] = mapped_column(DateTime)
    trade_date: Mapped[date] = mapped_column(Date)
    latest_price: Mapped[float] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    iopv: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_discount_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="akshare")
    freshness_status: Mapped[str] = mapped_column(String(32), default="fresh")
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfIntradayDailySummary(Base):
    __tablename__ = "etf_intraday_daily_summaries"
    __table_args__ = (UniqueConstraint("etf_code", "trade_date", name="uq_etf_intraday_daily_summary"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    trade_date: Mapped[date] = mapped_column(Date)
    quote_count: Mapped[int] = mapped_column(Integer, default=0)
    first_quote_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_quote_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    open_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class IntradayEtfWatchRun(Base):
    __tablename__ = "intraday_etf_watch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(32), default="scheduled")
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    market_session: Mapped[str | None] = mapped_column(String(32), nullable=True)
    watched_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_quote_count: Mapped[int] = mapped_column(Integer, default=0)
    stale_quote_count: Mapped[int] = mapped_column(Integer, default=0)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    email_sent_count: Mapped[int] = mapped_column(Integer, default=0)
    suppressed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ShortEtfSignalRun(Base):
    __tablename__ = "short_etf_signal_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ShortEtfSignalItem(Base):
    __tablename__ = "short_etf_signal_items"
    __table_args__ = (UniqueConstraint("run_id", "etf_code", name="uq_short_etf_signal_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("short_etf_signal_runs.id", ondelete="CASCADE"))
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[float] = mapped_column(Float)
    conclusion: Mapped[str] = mapped_column(String(64))
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    rationale_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShortEtfSignalReview(Base):
    __tablename__ = "short_etf_signal_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("short_etf_signal_runs.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(255), default="rules-v1")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ShortEtfSignalReviewItem(Base):
    __tablename__ = "short_etf_signal_review_items"
    __table_args__ = (UniqueConstraint("review_id", "signal_item_id", name="uq_short_etf_review_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("short_etf_signal_reviews.id", ondelete="CASCADE"))
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("short_etf_signal_items.id", ondelete="CASCADE"))
    etf_code: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[float] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(64))
    agent_notes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShortEtfReliabilityEvaluation(Base):
    __tablename__ = "short_etf_reliability_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    sample_days: Mapped[int] = mapped_column(Integer, default=0)
    conclusion: Mapped[str] = mapped_column(String(64))
    data_coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShortEtfReliabilityEvaluationItem(Base):
    __tablename__ = "short_etf_reliability_evaluation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("short_etf_reliability_evaluations.id", ondelete="CASCADE")
    )
    rank_order: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(255))
    item_type: Mapped[str] = mapped_column(String(64), default="parameter")
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShortEtfPaperPortfolio(Base):
    __tablename__ = "short_etf_paper_portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active")
    started_at: Mapped[date] = mapped_column(Date)
    cash: Mapped[float] = mapped_column(Float)
    latest_equity: Mapped[float] = mapped_column(Float)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ShortEtfPaperOrder(Base):
    __tablename__ = "short_etf_paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("short_etf_paper_portfolios.id", ondelete="CASCADE"))
    signal_run_id: Mapped[int | None] = mapped_column(ForeignKey("short_etf_signal_runs.id", ondelete="SET NULL"), nullable=True)
    trade_date: Mapped[date] = mapped_column(Date)
    etf_code: Mapped[str] = mapped_column(ForeignKey("tradable_etfs.code", ondelete="CASCADE"))
    side: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShortEtfPaperEquityCurve(Base):
    __tablename__ = "short_etf_paper_equity_curve"
    __table_args__ = (UniqueConstraint("paper_id", "curve_date", name="uq_short_etf_paper_equity_curve"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("short_etf_paper_portfolios.id", ondelete="CASCADE"))
    curve_date: Mapped[date] = mapped_column(Date)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShortResearchSignalRun(Base):
    __tablename__ = "short_research_signal_runs"
    __table_args__ = (
        SaIndex(
            "ix_short_research_signal_runs_canonical_snapshot",
            "scope_kind",
            "score_version",
            "ranking_contract_hash",
            "as_of_trade_date",
            "price_basis",
            "publication_state",
        ),
        SaIndex(
            "ux_short_research_signal_runs_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    universe_snapshot_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_snapshot_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    score_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ranking_contract_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    score_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    price_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligible_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    publication_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ShortResearchSignalItem(Base):
    __tablename__ = "short_research_signal_items"
    __table_args__ = (
        UniqueConstraint("run_id", "asset_type", "asset_code", name="uq_short_research_signal_item"),
        SaIndex("ix_short_research_signal_items_run_global_rank", "run_id", "global_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("short_research_signal_runs.id", ondelete="CASCADE"))
    asset_type: Mapped[str] = mapped_column(String(16))
    asset_code: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[float] = mapped_column(Float)
    conclusion: Mapped[str] = mapped_column(String(64))
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    rationale_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ranking_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    global_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EtfSignalValidationRun(Base):
    __tablename__ = "etf_signal_validation_runs"
    __table_args__ = (
        SaIndex(
            "ix_etf_signal_validation_runs_source_contract",
            "source_ranking_contract_hash",
            "source_scope_kind",
            "source_universe_snapshot_hash",
            "source_score_field",
            "price_basis",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="success")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    source_signal_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="SET NULL"), nullable=True
    )
    asset_type: Mapped[str] = mapped_column(String(16), default="etf")
    validation_mode: Mapped[str] = mapped_column(String(32), default="forward_live")
    rule_version: Mapped[str] = mapped_column(String(64), default="label_validation_v1")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source_ranking_contract_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_scope_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_scope_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_universe_snapshot_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_input_snapshot_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_score_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_score_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EtfSignalValidationItem(Base):
    __tablename__ = "etf_signal_validation_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "label",
            "entry_timing_label",
            "horizon_days",
            name="uq_etf_signal_validation_item",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("etf_signal_validation_runs.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(64))
    entry_timing_label: Mapped[str] = mapped_column(String(64))
    horizon_days: Mapped[int] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    worst_forward_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(32), default="insufficient")
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfLabelOutcome(Base):
    __tablename__ = "etf_label_outcomes"
    __table_args__ = (
        UniqueConstraint("signal_item_id", "horizon_days", name="uq_etf_label_outcome_signal_horizon"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_item_id: Mapped[int] = mapped_column(
        ForeignKey("short_research_signal_items.id", ondelete="CASCADE")
    )
    signal_run_id: Mapped[int] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="CASCADE")
    )
    asset_type: Mapped[str] = mapped_column(String(16), default="etf")
    asset_code: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(64))
    entry_timing_label: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64), default="label_validation_v1")
    signal_date: Mapped[date] = mapped_column(Date)
    signal_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer)
    forward_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    adverse_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    favorable_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfLabelReplaySample(Base):
    __tablename__ = "etf_label_replay_samples"
    __table_args__ = (
        UniqueConstraint(
            "validation_run_id",
            "asset_code",
            "replay_date",
            "horizon_days",
            name="uq_etf_label_replay_sample",
        ),
        SaIndex("ix_etf_label_replay_sample_group", "label", "entry_timing_label", "horizon_days", "status"),
        SaIndex("ix_etf_label_replay_sample_asset_date", "asset_code", "replay_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[int] = mapped_column(
        ForeignKey("etf_signal_validation_runs.id", ondelete="CASCADE")
    )
    asset_type: Mapped[str] = mapped_column(String(16), default="etf")
    asset_code: Mapped[str] = mapped_column(String(32))
    asset_name: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(64))
    entry_timing_label: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64), default="label_validation_v1")
    replay_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer)
    horizon_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    forward_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    adverse_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    favorable_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfObservationPortfolioSnapshot(Base):
    __tablename__ = "etf_observation_portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="success")
    source_signal_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="SET NULL"), nullable=True
    )
    validation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_signal_validation_runs.id", ondelete="SET NULL"), nullable=True
    )
    as_of_date: Mapped[date] = mapped_column(Date)
    asset_type: Mapped[str] = mapped_column(String(16), default="etf")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfObservationPortfolioItem(Base):
    __tablename__ = "etf_observation_portfolio_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("etf_observation_portfolio_snapshots.id", ondelete="CASCADE")
    )
    item_type: Mapped[str] = mapped_column(String(32), default="primary")
    rank_order: Mapped[int] = mapped_column(Integer, default=0)
    asset_code: Mapped[str] = mapped_column(String(32))
    asset_name: Mapped[str] = mapped_column(String(255))
    target_weight: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    conclusion: Mapped[str] = mapped_column(String(64))
    entry_timing_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfStrategyHealthcheckSnapshot(Base):
    __tablename__ = "etf_strategy_healthcheck_snapshots"
    __table_args__ = (SaIndex("ix_etf_strategy_healthcheck_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="success")
    source_signal_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="SET NULL"), nullable=True
    )
    validation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_signal_validation_runs.id", ondelete="SET NULL"), nullable=True
    )
    backtest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_portfolio_backtest_runs.id", ondelete="SET NULL"), nullable=True
    )
    as_of_date: Mapped[date] = mapped_column(Date)
    execution_model: Mapped[str] = mapped_column(String(32), default="daily_close")
    evidence_contract_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_status: Mapped[str] = mapped_column(String(64), default="等待验证")
    conclusion: Mapped[str] = mapped_column(String(64), default="数据不足")
    data_window_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    caveats_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfStrategyHealthcheckItem(Base):
    __tablename__ = "etf_strategy_healthcheck_items"
    __table_args__ = (SaIndex("ix_etf_strategy_healthcheck_items_snapshot_type", "snapshot_id", "item_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("etf_strategy_healthcheck_snapshots.id", ondelete="CASCADE")
    )
    item_type: Mapped[str] = mapped_column(String(32))
    item_key: Mapped[str] = mapped_column(String(128))
    conclusion: Mapped[str] = mapped_column(String(64), default="数据不足")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfThemeCatalystEvent(Base):
    __tablename__ = "etf_theme_catalyst_events"
    __table_args__ = (
        UniqueConstraint("theme_key", "title", "event_date", name="uq_etf_theme_catalyst_event"),
        SaIndex(
            "ix_etf_theme_catalyst_active_window",
            "theme_key",
            "status",
            "effective_start",
            "effective_end",
        ),
        SaIndex("ix_etf_theme_catalyst_source_status", "source_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_key: Mapped[str] = mapped_column(String(64))
    theme_name: Mapped[str] = mapped_column(String(128))
    catalyst_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    direction: Mapped[str] = mapped_column(String(32), default="positive")
    strength_score: Mapped[float] = mapped_column(Float, default=50.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=50.0)
    status: Mapped[str] = mapped_column(String(32), default="active")
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfThemeCatalystSnapshot(Base):
    __tablename__ = "etf_theme_catalyst_snapshots"
    __table_args__ = (
        UniqueConstraint("as_of_date", "theme_key", name="uq_etf_theme_catalyst_snapshot"),
        SaIndex("ix_etf_theme_catalyst_snapshot_status_date", "status", "as_of_date"),
        SaIndex("ix_etf_theme_catalyst_snapshot_theme", "theme_key", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    theme_key: Mapped[str] = mapped_column(String(64))
    theme_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="success")
    catalyst_score: Mapped[float] = mapped_column(Float, default=50.0)
    sentiment_heat_score: Mapped[float] = mapped_column(Float, default=50.0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    key_events_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfOptimizedAllocationSnapshot(Base):
    __tablename__ = "etf_optimized_allocation_snapshots"
    __table_args__ = (SaIndex("ix_etf_optimized_allocation_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="success")
    source_signal_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="SET NULL"), nullable=True
    )
    observation_portfolio_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_observation_portfolio_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    as_of_date: Mapped[date] = mapped_column(Date)
    method_set: Mapped[str] = mapped_column(String(64), default="stable_v1")
    evidence_contract_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_window_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    constraints_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    unavailable_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfOptimizedAllocationItem(Base):
    __tablename__ = "etf_optimized_allocation_items"
    __table_args__ = (SaIndex("ix_etf_optimized_allocation_items_snapshot_method", "snapshot_id", "method"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("etf_optimized_allocation_snapshots.id", ondelete="CASCADE")
    )
    method: Mapped[str] = mapped_column(String(64))
    asset_code: Mapped[str] = mapped_column(String(32))
    asset_name: Mapped[str] = mapped_column(String(255))
    target_weight: Mapped[float] = mapped_column(Float, default=0.0)
    expected_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    theme_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfExitHyperoptRun(Base):
    __tablename__ = "etf_exit_hyperopt_runs"
    __table_args__ = (
        SaIndex("ix_etf_exit_hyperopt_runs_status_started", "status", "started_at"),
        SaIndex("ix_etf_exit_hyperopt_runs_finished", "finished_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    objective: Mapped[str] = mapped_column(String(64), default="stability_first")
    rule_version: Mapped[str] = mapped_column(String(64), default="etf_exit_hyperopt_v1")
    calibration_rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contract_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    train_range_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    out_of_sample_range_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    search_space_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    bucket_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfExitHyperoptItem(Base):
    __tablename__ = "etf_exit_hyperopt_items"
    __table_args__ = (
        SaIndex("ix_etf_exit_hyperopt_items_run_bucket", "run_id", "bucket_type", "bucket_key"),
        SaIndex("ix_etf_exit_hyperopt_items_run_score", "run_id", "score"),
        SaIndex("ix_etf_exit_hyperopt_items_status_bucket_score", "status", "bucket_type", "bucket_key", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("etf_exit_hyperopt_runs.id", ondelete="CASCADE"))
    bucket_type: Mapped[str] = mapped_column(String(32))
    bucket_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    conclusion: Mapped[str] = mapped_column(String(64), default="候选待确认")
    parameter_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    train_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    out_of_sample_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rolling_metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)
    confidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)
    source_reliability: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfExitSignalCredibilityRun(Base):
    __tablename__ = "etf_exit_signal_credibility_runs"
    __table_args__ = (
        SaIndex("ix_etf_exit_cred_runs_status_started", "status", "started_at"),
        SaIndex("ix_etf_exit_cred_runs_model_finished", "execution_model", "finished_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    execution_model: Mapped[str] = mapped_column(String(32), default="intraday_alert")
    signal_version: Mapped[str] = mapped_column(String(64), default="short_research_v1")
    exit_rule_version: Mapped[str] = mapped_column(String(64), default="risk_alerts_v1")
    contract_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_status: Mapped[str] = mapped_column(String(32), default="等待验证")
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_window_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    insufficiency_reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfExitSignalCredibilityItem(Base):
    __tablename__ = "etf_exit_signal_credibility_items"
    __table_args__ = (
        SaIndex(
            "ix_etf_exit_cred_items_run_signal_group",
            "run_id",
            "signal_type",
            "group_type",
            "group_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("etf_exit_signal_credibility_runs.id", ondelete="CASCADE")
    )
    signal_type: Mapped[str] = mapped_column(String(64))
    group_type: Mapped[str] = mapped_column(String(32), default="signal")
    group_key: Mapped[str] = mapped_column(String(128), default="all")
    evidence_level: Mapped[str] = mapped_column(String(32), default="样本不足")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    success_avoidance_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    false_stop_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sold_too_early_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_avoided_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_missed_upside: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_forward_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfExitSignalCredibilityEvent(Base):
    __tablename__ = "etf_exit_signal_credibility_events"
    __table_args__ = (
        SaIndex("ix_etf_exit_cred_events_run_signal", "run_id", "signal_type", "signal_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("etf_exit_signal_credibility_runs.id", ondelete="CASCADE")
    )
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_exit_signal_credibility_items.id", ondelete="SET NULL"), nullable=True
    )
    etf_code: Mapped[str] = mapped_column(String(32))
    etf_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(64))
    signal_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signal_date: Mapped[date] = mapped_column(Date)
    signal_price: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(64))
    forward_window_days: Mapped[int] = mapped_column(Integer)
    forward_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_favorable_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_adverse_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfPortfolioBacktestRun(Base):
    __tablename__ = "etf_portfolio_backtest_runs"
    __table_args__ = (
        SaIndex("ix_etf_portfolio_backtest_runs_status", "status"),
        SaIndex("ix_etf_portfolio_backtest_runs_finished_at", "finished_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    asset_type: Mapped[str] = mapped_column(String(16), default="etf")
    rule_version: Mapped[str] = mapped_column(String(64), default="etf_portfolio_backtest_v1")
    ranking_version: Mapped[str] = mapped_column(String(64), default="short_research_v1")
    allocation_version: Mapped[str] = mapped_column(String(64), default="portfolio_allocation_v1")
    exit_rule_version: Mapped[str] = mapped_column(String(64), default="risk_alerts_v1")
    initial_cash: Mapped[float] = mapped_column(Float, default=10000.0)
    fee_rate: Mapped[float] = mapped_column(Float, default=0.001)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    benchmark_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    caveats_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfPortfolioBacktestEquityCurve(Base):
    __tablename__ = "etf_portfolio_backtest_equity_curve"
    __table_args__ = (
        UniqueConstraint("run_id", "curve_date", name="uq_etf_portfolio_backtest_equity_curve"),
        SaIndex("ix_etf_portfolio_backtest_equity_run_date", "run_id", "curve_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("etf_portfolio_backtest_runs.id", ondelete="CASCADE"))
    curve_date: Mapped[date] = mapped_column(Date)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    benchmark_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    portfolio_mode: Mapped[str] = mapped_column(String(32), default="cash_wait")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfPortfolioBacktestTrade(Base):
    __tablename__ = "etf_portfolio_backtest_trades"
    __table_args__ = (SaIndex("ix_etf_portfolio_backtest_trades_run_date", "run_id", "trade_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("etf_portfolio_backtest_runs.id", ondelete="CASCADE"))
    trade_date: Mapped[date] = mapped_column(Date)
    etf_code: Mapped[str] = mapped_column(String(32))
    etf_name: Mapped[str] = mapped_column(String(255))
    side: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfPortfolioBacktestPosition(Base):
    __tablename__ = "etf_portfolio_backtest_positions"
    __table_args__ = (
        UniqueConstraint("run_id", "snapshot_date", "etf_code", name="uq_etf_portfolio_backtest_position"),
        SaIndex("ix_etf_portfolio_backtest_positions_run_date", "run_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("etf_portfolio_backtest_runs.id", ondelete="CASCADE"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    etf_code: Mapped[str] = mapped_column(String(32))
    etf_name: Mapped[str] = mapped_column(String(255))
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)
    cost_basis: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfPortfolioBacktestLabelSummary(Base):
    __tablename__ = "etf_portfolio_backtest_label_summaries"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "label",
            "entry_timing_label",
            "horizon_days",
            name="uq_etf_portfolio_backtest_label_summary",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("etf_portfolio_backtest_runs.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(64))
    entry_timing_label: Mapped[str] = mapped_column(String(64))
    horizon_days: Mapped[int] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    worst_forward_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(32), default="insufficient")
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShortResearchAdvisorReport(Base):
    __tablename__ = "short_research_advisor_reports"
    __table_args__ = (
        UniqueConstraint("signal_run_id", "asset_type", "asset_code", name="uq_short_research_advisor_report"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_run_id: Mapped[int] = mapped_column(ForeignKey("short_research_signal_runs.id", ondelete="CASCADE"))
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("short_research_signal_items.id", ondelete="CASCADE"))
    asset_type: Mapped[str] = mapped_column(String(16))
    asset_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="success")
    action_label: Mapped[str] = mapped_column(String(32))
    plain_summary: Mapped[str] = mapped_column(Text)
    opportunity_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    opposing_view: Mapped[str] = mapped_column(Text)
    watch_conditions_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    holding_note: Mapped[str] = mapped_column(Text)
    data_limitations: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32), default="llm")
    deterministic_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ShortResearchAdvisorAttempt(Base):
    __tablename__ = "short_research_advisor_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_run_id: Mapped[int] = mapped_column(ForeignKey("short_research_signal_runs.id", ondelete="CASCADE"))
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("short_research_signal_items.id", ondelete="CASCADE"))
    asset_type: Mapped[str] = mapped_column(String(16))
    asset_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrackedPosition(Base):
    __tablename__ = "tracked_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    asset_type: Mapped[str] = mapped_column(String(16))
    asset_code: Mapped[str] = mapped_column(String(32))
    asset_name: Mapped[str] = mapped_column(String(255))
    buy_date: Mapped[date] = mapped_column(Date)
    order_time_bucket: Mapped[str] = mapped_column(String(16), default="unknown")
    confirmed_nav_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmed_nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_amount: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TrackedPositionAlert(Base):
    __tablename__ = "tracked_position_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracked_position_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="CASCADE")
    )
    alert_date: Mapped[date] = mapped_column(Date)
    alert_type: Mapped[str] = mapped_column(String(32))
    trigger_label: Mapped[str] = mapped_column(String(64))
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    threshold_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    advisor_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    alert_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    suppression_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_status: Mapped[str] = mapped_column(String(32), default="pending")
    email_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)



class TrackedPositionAlertAudit(Base):
    __tablename__ = "tracked_position_alert_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracked_position_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="CASCADE")
    )
    tracked_position_alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_alerts.id", ondelete="SET NULL"),
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    alert_date: Mapped[date] = mapped_column(Date, nullable=False)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_source: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    quote_freshness: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    threshold_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    smtp_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.code", ondelete="CASCADE"))
    published_at: Mapped[datetime] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    raw_content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NewsSummary(Base):
    __tablename__ = "news_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    news_item_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"), unique=True)
    summary: Mapped[str] = mapped_column(String(280))
    event_type: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(255))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notification_type: Mapped[str] = mapped_column(String(64))
    recipient: Mapped[str] = mapped_column(String(255))
    template_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecommendationProfile(Base):
    __tablename__ = "recommendation_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    asset_type: Mapped[str] = mapped_column(String(16))
    risk_level: Mapped[str] = mapped_column(String(32), default="balanced")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendation_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    asset_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    as_of_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_cutoff_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"
    __table_args__ = (UniqueConstraint("run_id", "asset_code", name="uq_recommendation_item_asset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("recommendation_runs.id", ondelete="CASCADE"))
    asset_code: Mapped[str] = mapped_column(String(32))
    asset_name: Mapped[str] = mapped_column(String(255))
    asset_type: Mapped[str] = mapped_column(String(16))
    rank: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[float] = mapped_column(Float)
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_freshness_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecommendationReview(Base):
    __tablename__ = "recommendation_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("recommendation_runs.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RecommendationReviewItem(Base):
    __tablename__ = "recommendation_review_items"
    __table_args__ = (
        UniqueConstraint("review_id", "recommendation_item_id", name="uq_recommendation_review_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("recommendation_reviews.id", ondelete="CASCADE"))
    recommendation_item_id: Mapped[int] = mapped_column(ForeignKey("recommendation_items.id", ondelete="CASCADE"))
    asset_code: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str] = mapped_column(String(64))
    agent_notes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StrategyDefinition(Base):
    __tablename__ = "strategy_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    strategy_type: Mapped[str] = mapped_column(String(64))
    asset_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="active")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy_definitions.id", ondelete="CASCADE"))
    run_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    date_range_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class StrategyEvaluation(Base):
    __tablename__ = "strategy_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy_definitions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    data_coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conclusion: Mapped[str] = mapped_column(String(64))
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StrategyEvaluationItem(Base):
    __tablename__ = "strategy_evaluation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("strategy_evaluations.id", ondelete="CASCADE"))
    rank_order: Mapped[int] = mapped_column(Integer)
    item_type: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255))
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    in_sample_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    out_of_sample_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rolling_windows_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StrategyOrder(Base):
    __tablename__ = "strategy_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    submitted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    trade_date: Mapped[date] = mapped_column(Date)
    confirmed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    asset_code: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="confirmed")
    platform: Mapped[str] = mapped_column(String(32), default="generic")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StrategyPosition(Base):
    __tablename__ = "strategy_positions"
    __table_args__ = (
        UniqueConstraint("run_id", "snapshot_date", "asset_code", name="uq_strategy_position_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    asset_code: Mapped[str] = mapped_column(String(32))
    shares: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StrategyEquityCurve(Base):
    __tablename__ = "strategy_equity_curve"
    __table_args__ = (UniqueConstraint("run_id", "curve_date", name="uq_strategy_equity_curve_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    curve_date: Mapped[date] = mapped_column(Date)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PaperPortfolio(Base):
    __tablename__ = "paper_portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy_definitions.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active")
    started_at: Mapped[date] = mapped_column(Date)
    cash: Mapped[float] = mapped_column(Float)
    latest_equity: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


_SNAPSHOT_IDENTITY_FIELDS = (
    "scope_kind",
    "scope_hash",
    "universe_snapshot_hash",
    "input_snapshot_hash",
    "score_version",
    "rule_version",
    "ranking_contract_hash",
    "score_field",
    "data_cutoff",
    "as_of_trade_date",
    "price_basis",
    "expected_item_count",
    "eligible_item_count",
    "coverage_ratio",
    "idempotency_key",
)
_SNAPSHOT_ITEM_FIELDS = ("run_id", "rank", "global_rank", "total_score", "ranking_score", "score_eligible")


def _changed_fields(instance: object, fields: tuple[str, ...]) -> bool:
    state = inspect(instance)
    return any(state.attrs[field].history.has_changes() for field in fields)


@event.listens_for(Session, "before_flush")
def _prevent_published_snapshot_mutation(session: Session, _flush_context: object, _instances: object) -> None:
    published_run_ids: set[int] = set()
    item_run_ids: set[int] = set()
    changed_items: list[ShortResearchSignalItem] = []
    for instance in session.dirty:
        if isinstance(instance, ShortResearchSignalRun):
            state = inspect(instance)
            was_published = instance.publication_state == "published" or "published" in state.attrs.publication_state.history.deleted
            if was_published and _changed_fields(instance, _SNAPSHOT_IDENTITY_FIELDS):
                raise PublishedSnapshotImmutableError("published ranking snapshot identity is immutable")
            if "published" in state.attrs.publication_state.history.deleted:
                raise PublishedSnapshotImmutableError("published ranking snapshot is immutable")
        elif isinstance(instance, ShortResearchSignalItem) and _changed_fields(instance, _SNAPSHOT_ITEM_FIELDS):
            changed_items.append(instance)
            item_run_ids.add(instance.run_id)
            item_run_ids.update(inspect(instance).attrs.run_id.history.deleted)
    for instance in session.new:
        if isinstance(instance, ShortResearchSignalItem):
            changed_items.append(instance)
            item_run_ids.add(instance.run_id)
    if item_run_ids:
        published_run_ids = set(
            session.scalars(
                select(ShortResearchSignalRun.id).where(
                    ShortResearchSignalRun.id.in_(item_run_ids),
                    ShortResearchSignalRun.publication_state == "published",
                )
            )
        )
    if any(item.run_id in published_run_ids for item in changed_items):
        raise PublishedSnapshotImmutableError("published ranking snapshot items are immutable")


