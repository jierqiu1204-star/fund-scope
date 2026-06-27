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
)
from sqlalchemy import (
    Index as SaIndex,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.utcnow()


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


class EtfPriceHistory(Base):
    __tablename__ = "etf_price_history"
    __table_args__ = (UniqueConstraint("etf_code", "trade_date", name="uq_etf_price_history"),)

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ShortResearchSignalItem(Base):
    __tablename__ = "short_research_signal_items"
    __table_args__ = (UniqueConstraint("run_id", "asset_type", "asset_code", name="uq_short_research_signal_item"),)

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


class EtfSignalValidationRun(Base):
    __tablename__ = "etf_signal_validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="success")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    source_signal_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="SET NULL"), nullable=True
    )
    asset_type: Mapped[str] = mapped_column(String(16), default="etf")
    rule_version: Mapped[str] = mapped_column(String(64), default="label_validation_v1")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


