from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    select,
    text,
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


class DatabaseInstanceIdentityImmutableError(ValueError):
    pass


class ValidationEvidenceImmutableError(ValueError):
    pass


class CatalystEvidenceImmutableError(ValueError):
    pass


_SNAPSHOT_PUBLICATION_AUTHORIZATION_KEY = "snapshot_publication_authorized_run_ids"


@contextmanager
def authorize_snapshot_publication(session: Session, *, run_id: int) -> Iterator[None]:
    authorized_run_ids = session.info.setdefault(_SNAPSHOT_PUBLICATION_AUTHORIZATION_KEY, set())
    if not isinstance(authorized_run_ids, set):
        raise RuntimeError("invalid snapshot publication authorization state")
    was_authorized = run_id in authorized_run_ids
    authorized_run_ids.add(run_id)
    try:
        yield
    finally:
        if not was_authorized:
            authorized_run_ids.discard(run_id)
        if not authorized_run_ids:
            session.info.pop(_SNAPSHOT_PUBLICATION_AUTHORIZATION_KEY, None)


class NotificationEnvelopeImmutableError(ValueError):
    pass


class TrackedPositionAuditImmutableError(ValueError):
    pass


class TrackedPositionLifecycleShadowImmutableError(ValueError):
    pass


class TrackedEtfSleeveImmutableError(ValueError):
    pass


class EtfActionValidationImmutableError(ValueError):
    pass


class PitCaptureSourceImmutableError(ValueError):
    pass


class IntradayQuoteEvidenceImmutableError(ValueError):
    pass


class AdjustedPriceRevisionImmutableError(ValueError):
    pass


class CanonicalPublicationImmutableError(ValueError):
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
    etf_trading_capital_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
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
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    listing_date_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    listing_date_observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfListingDateObservation(Base):
    __tablename__ = "etf_listing_date_observations"
    __table_args__ = (
        UniqueConstraint(
            "evidence_hash",
            name="uq_etf_listing_date_observation_evidence",
        ),
        SaIndex(
            "ix_etf_listing_date_observations_cutoff",
            "etf_code",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("tradable_etfs.code", ondelete="CASCADE"),
    )
    exchange: Mapped[str] = mapped_column(String(16))
    listing_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    universe_snapshot_hash: Mapped[str] = mapped_column(String(64))
    raw_payload_hash: Mapped[str] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


class EtfPointInTimeMembershipFact(Base):
    __tablename__ = "etf_point_in_time_membership_facts"
    __table_args__ = (
        SaIndex(
            "ix_etf_pit_membership_facts_effective_lookup",
            "etf_code",
            "effective_from",
            "effective_to",
        ),
        SaIndex(
            "ix_etf_pit_membership_facts_observed_cursor",
            "observed_at",
            "etf_code",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_etf_pit_membership_facts_effective_interval",
        ),
        CheckConstraint(
            "membership_state IN ('included', 'excluded')",
            name="ck_etf_pit_membership_facts_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(String(32))
    external_source_id: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    membership_state: Mapped[str] = mapped_column(String(16), default="included")
    evidence_hash: Mapped[str] = mapped_column(String(64), unique=True)
    raw_payload_hash: Mapped[str] = mapped_column(String(64))
    fact_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


class EtfAdjustedPriceRevision(Base):
    """Append-only adjusted-price evidence; ``EtfPriceHistory`` is its projection."""

    __tablename__ = "etf_adjusted_price_revisions"
    __table_args__ = (
        UniqueConstraint(
            "revision_hash",
            name="uq_etf_adjusted_price_revisions_revision_hash",
        ),
        SaIndex(
            "ix_etf_adjusted_price_revisions_pit_lookup",
            "etf_code",
            "trade_date",
            "first_seen_at",
            "observed_at",
            "id",
        ),
        SaIndex(
            "ix_etf_adjusted_price_revisions_payload",
            "etf_code",
            "trade_date",
            "payload_hash",
        ),
        SaIndex(
            "ix_etf_adjusted_price_revisions_supersession",
            "supersedes_revision_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("tradable_etfs.code", ondelete="CASCADE"),
    )
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)
    pct_change: Mapped[float] = mapped_column(Float)
    raw_price_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    research_adjusted_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    research_price_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_provider: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime)
    adjustment_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_eligible: Mapped[bool] = mapped_column(Boolean)
    decision_ineligibility_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    payload_hash: Mapped[str] = mapped_column(String(64))
    revision_hash: Mapped[str] = mapped_column(String(64))
    supersedes_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_adjusted_price_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
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


class EtfTaxonomyFact(Base):
    """Immutable taxonomy evidence; ``EtfThemeProfile`` is its current projection."""

    __tablename__ = "etf_taxonomy_facts"
    __table_args__ = (
        UniqueConstraint("evidence_hash", name="uq_etf_taxonomy_facts_evidence"),
        UniqueConstraint("fact_hash", name="uq_etf_taxonomy_facts_fact"),
        SaIndex(
            "ix_etf_taxonomy_facts_cutoff",
            "etf_code",
            "observed_at",
            "id",
        ),
        SaIndex("ix_etf_taxonomy_facts_supersession", "supersedes_fact_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("tradable_etfs.code", ondelete="CASCADE"),
    )
    external_source_id: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    asset_bucket: Mapped[str] = mapped_column(String(64))
    theme_group: Mapped[str] = mapped_column(String(64))
    primary_theme: Mapped[str] = mapped_column(String(64))
    secondary_themes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    classification_source: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[str] = mapped_column(String(128))
    classification_reason: Mapped[str] = mapped_column(Text)
    raw_payload_hash: Mapped[str] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    fact_hash: Mapped[str] = mapped_column(String(64))
    supersedes_fact_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_taxonomy_facts.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfTrackedUnderlyingFact(Base):
    """Immutable formal-underlying evidence; membership is its current projection."""

    __tablename__ = "etf_tracked_underlying_facts"
    __table_args__ = (
        UniqueConstraint("evidence_hash", name="uq_etf_tracked_underlying_facts_evidence"),
        UniqueConstraint("fact_hash", name="uq_etf_tracked_underlying_facts_fact"),
        SaIndex(
            "ix_etf_tracked_underlying_facts_cutoff",
            "etf_code",
            "observed_at",
            "id",
        ),
        SaIndex(
            "ix_etf_tracked_underlying_facts_supersession",
            "supersedes_fact_id",
        ),
        CheckConstraint(
            "identity_state IN ('resolved', 'unresolved')",
            name="ck_etf_tracked_underlying_facts_state",
        ),
        CheckConstraint(
            "(identity_state = 'resolved' AND tracked_underlying_id IS NOT NULL) "
            "OR (identity_state = 'unresolved' AND tracked_underlying_id IS NULL)",
            name="ck_etf_tracked_underlying_facts_resolution",
        ),
        CheckConstraint(
            "mapping_basis IN ('authoritative', 'manual')",
            name="ck_etf_tracked_underlying_facts_basis",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("tradable_etfs.code", ondelete="CASCADE"),
    )
    external_source_id: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    identity_state: Mapped[str] = mapped_column(String(16))
    tracked_underlying_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mapping_basis: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[str] = mapped_column(String(128))
    identity_reason: Mapped[str] = mapped_column(Text)
    raw_payload_hash: Mapped[str] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    fact_hash: Mapped[str] = mapped_column(String(64))
    supersedes_fact_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_tracked_underlying_facts.id"),
        nullable=True,
    )
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


class EtfSyncCursor(Base):
    __tablename__ = "etf_sync_cursors"

    scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_priority_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_regular_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_lane: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfAdjustedHistoryAvailability(Base):
    __tablename__ = "etf_adjusted_history_availability"
    __table_args__ = (
        CheckConstraint(
            "status IN ('sufficient', 'source_history_shortfall')",
            name="ck_etf_adjusted_history_availability_status",
        ),
        CheckConstraint(
            "eligible_session_count >= 0",
            name="ck_etf_adjusted_history_availability_count",
        ),
        SaIndex(
            "ix_etf_adjusted_history_availability_retry",
            "provider_policy_version",
            "scope",
            "required_calendar_hash",
            "retry_after",
        ),
    )

    etf_code: Mapped[str] = mapped_column(
        ForeignKey("tradable_etfs.code", ondelete="CASCADE"),
        primary_key=True,
    )
    provider_policy_version: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    scope: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    required_calendar_hash: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    provider: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    adjustment_version: Mapped[str] = mapped_column(String(128))
    requested_from: Mapped[date] = mapped_column(Date)
    requested_to: Mapped[date] = mapped_column(Date)
    earliest_eligible_date: Mapped[date] = mapped_column(Date)
    latest_eligible_date: Mapped[date] = mapped_column(Date)
    eligible_session_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DatabaseInstanceIdentity(Base):
    __tablename__ = "database_instance_identity"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_database_instance_identity_singleton"),
        CheckConstraint(
            "declared_environment IN ('local', 'test', 'acceptance', 'production')",
            name="ck_database_instance_identity_environment",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    instance_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    declared_environment: Mapped[str] = mapped_column(String(32))
    provisioned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    provisioned_by_deploy: Mapped[str] = mapped_column(String(128))
    attestation_key_id: Mapped[str] = mapped_column(String(128))
    creation_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EtfDailyWorkflowLock(Base):
    __tablename__ = "etf_daily_workflow_locks"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


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


class EtfIntradayQuoteEvidenceRef(Base):
    """Immutable reference to the exact intraday input used by a decision artifact."""

    __tablename__ = "etf_intraday_quote_evidence_refs"
    __table_args__ = (
        UniqueConstraint(
            "owner_kind",
            "owner_id",
            "asset_code",
            "evidence_purpose",
            name="uq_etf_intraday_quote_evidence_owner",
        ),
        CheckConstraint(
            "evidence_state IN ('protected', 'unavailable')",
            name="ck_etf_intraday_quote_evidence_state",
        ),
        CheckConstraint(
            "(evidence_state = 'protected' AND quote_id IS NOT NULL "
            "AND quote_hash IS NOT NULL AND unavailable_reason IS NULL) OR "
            "(evidence_state = 'unavailable' AND quote_id IS NULL "
            "AND quote_hash IS NULL AND unavailable_reason IS NOT NULL)",
            name="ck_etf_intraday_quote_evidence_payload",
        ),
        SaIndex("ix_etf_intraday_quote_evidence_quote_id", "quote_id"),
        SaIndex(
            "ix_etf_intraday_quote_evidence_owner_lookup",
            "owner_kind",
            "owner_id",
            "evidence_purpose",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_intraday_quotes.id", ondelete="RESTRICT"),
        nullable=True,
    )
    owner_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_code: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_state: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_cutoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    receipt_cutoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unavailable_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    protected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


class EtfIntradayCleanupCheckpoint(Base):
    __tablename__ = "etf_intraday_cleanup_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cutoff_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_etf_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    deleted_rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summarized_groups_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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
    decision_data_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_data_coverage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    eligible_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    publication_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EtfCanonicalPublicationRegistry(Base):
    """Immutable identity records plus the bounded canonical-selection head."""

    __tablename__ = "etf_canonical_publication_registry"
    __table_args__ = (
        UniqueConstraint(
            "publication_identity_hash",
            name="uq_etf_canonical_publication_registry_identity",
        ),
        UniqueConstraint(
            "source_signal_run_id",
            name="uq_etf_canonical_publication_registry_source_run",
        ),
        CheckConstraint(
            "provider_health_state IN "
            "('compatible', 'missing', 'stale', 'incompatible', 'not_applicable')",
            name="ck_etf_canonical_publication_registry_provider_health_state",
        ),
        SaIndex(
            "ix_etf_canonical_publication_registry_trade_lookup",
            "as_of_trade_date",
            "scope_kind",
            "price_basis",
            "surface_group_hash",
        ),
        SaIndex(
            "ix_etf_canonical_publication_registry_supersession",
            "supersedes_publication_id",
        ),
        SaIndex(
            "ux_etf_canonical_publication_registry_current_slot",
            "canonical_slot_hash",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current = true"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_signal_run_id: Mapped[int] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    as_of_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    price_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    research_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    actionable_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    readiness_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_policy_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    universe_snapshot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    provider_health_seal_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_health_check_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_health_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_health_covered_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider_health_source_range: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_health_state: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_health_unavailable_reason: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    surface_group_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    publication_identity_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_slot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    supersedes_publication_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_canonical_publication_registry.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
        SaIndex(
            "ix_etf_signal_validation_runs_ranking_source_kind",
            "ranking_source_kind",
        ),
        SaIndex(
            "ix_etf_signal_validation_runs_source_replay_run_key",
            "source_replay_run_key",
        ),
        SaIndex(
            "ix_etf_signal_validation_runs_source_manifest_hash",
            "source_manifest_hash",
        ),
        UniqueConstraint(
            "id",
            "ranking_source_kind",
            name="uq_etf_validation_run_manifest_kind",
        ),
        CheckConstraint(
            "ranking_source_kind IS NULL OR "
            "ranking_source_kind IN ('production_published', 'research_replay')",
            name="ck_etf_validation_ranking_source_kind",
        ),
        CheckConstraint(
            "status <> 'success' OR ranking_source_kind IS NULL OR "
            "(source_manifest_hash IS NOT NULL AND source_event_count > 0 AND "
            "((ranking_source_kind = 'research_replay' AND "
            "source_replay_run_key IS NOT NULL AND source_signal_run_id IS NULL) OR "
            "(ranking_source_kind = 'production_published' AND "
            "source_replay_run_key IS NULL)))",
            name="ck_etf_validation_ranking_source_identity",
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
    ranking_source_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_replay_run_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_manifest_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_event_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class EtfSignalValidationSourceEvent(Base):
    __tablename__ = "etf_signal_validation_source_events"
    __table_args__ = (
        UniqueConstraint(
            "validation_run_id",
            "event_order",
            name="uq_etf_validation_source_event_order",
        ),
        UniqueConstraint(
            "validation_run_id",
            "source_date",
            name="uq_etf_validation_source_event_date",
        ),
        UniqueConstraint(
            "validation_run_id",
            "immutable_hash",
            name="uq_etf_validation_source_event_hash",
        ),
        ForeignKeyConstraint(
            ["validation_run_id", "ranking_source_kind"],
            [
                "etf_signal_validation_runs.id",
                "etf_signal_validation_runs.ranking_source_kind",
            ],
            name="fk_etf_validation_source_event_manifest_kind",
            ondelete="CASCADE",
        ),
        SaIndex(
            "ix_etf_validation_source_events_source_date",
            "ranking_source_kind",
            "source_date",
        ),
        SaIndex(
            "ix_etf_validation_source_events_signal_run",
            "source_signal_run_id",
        ),
        SaIndex(
            "ix_etf_validation_source_events_replay_key",
            "source_replay_run_key",
        ),
        CheckConstraint(
            "ranking_source_kind IN ('production_published', 'research_replay')",
            name="ck_etf_validation_source_event_kind",
        ),
        CheckConstraint(
            "(ranking_source_kind = 'production_published' AND "
            "source_signal_run_id IS NOT NULL AND source_replay_run_key IS NULL AND "
            "source_replay_contract_hash IS NULL) OR "
            "(ranking_source_kind = 'research_replay' AND "
            "source_signal_run_id IS NULL AND source_replay_run_key IS NOT NULL AND "
            "source_replay_contract_hash IS NOT NULL)",
            name="ck_etf_validation_source_event_identity",
        ),
        CheckConstraint(
            "event_order >= 0",
            name="ck_etf_validation_source_event_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[int] = mapped_column(
        Integer
    )
    event_order: Mapped[int] = mapped_column(Integer)
    source_date: Mapped[date] = mapped_column(Date)
    ranking_source_kind: Mapped[str] = mapped_column(String(32))
    source_signal_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_replay_run_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_replay_contract_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    source_event_hash: Mapped[str] = mapped_column(String(128))
    ranking_contract_hash: Mapped[str] = mapped_column(String(128))
    scope_hash: Mapped[str] = mapped_column(String(128))
    universe_snapshot_hash: Mapped[str] = mapped_column(String(128))
    input_snapshot_hash: Mapped[str] = mapped_column(String(128))
    availability_cutoff: Mapped[datetime] = mapped_column(DateTime)
    score_version: Mapped[str] = mapped_column(String(64))
    score_field: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64))
    price_basis: Mapped[str] = mapped_column(String(64))
    publication_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope_kind: Mapped[str] = mapped_column(String(32))
    source_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expected_asset_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_data_covered_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligible_asset_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_data_coverage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_coverage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    etf_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finite_eligible_score_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contiguous_global_rank: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    immutable_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfValidationContinuation(Base):
    __tablename__ = "etf_validation_continuations"
    __table_args__ = (
        UniqueConstraint(
            "validation_run_id",
            name="uq_etf_validation_continuation_run",
        ),
        UniqueConstraint(
            "identity_hash",
            name="uq_etf_validation_continuation_identity",
        ),
        SaIndex(
            "ix_etf_validation_continuation_status",
            "status",
            "updated_at",
        ),
        CheckConstraint(
            "status IN ('running', 'partial', 'complete', 'failed')",
            name="ck_etf_validation_continuation_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[int] = mapped_column(
        ForeignKey("etf_signal_validation_runs.id", ondelete="CASCADE")
    )
    identity_hash: Mapped[str] = mapped_column(String(128))
    manifest_hash: Mapped[str] = mapped_column(String(128))
    execution_contract_hash: Mapped[str] = mapped_column(String(128))
    candidate_registry_hash: Mapped[str] = mapped_column(String(128))
    horizon_set_hash: Mapped[str] = mapped_column(String(128))
    schema_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="running")
    checkpoint_source_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checkpoint_horizon: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkpoint_asset_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    rolling_aggregate_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    final_aggregate_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EtfValidationMaterializedSample(Base):
    __tablename__ = "etf_validation_materialized_samples"
    __table_args__ = (
        UniqueConstraint(
            "continuation_id",
            "source_date",
            "horizon_sessions",
            "asset_key",
            name="uq_etf_validation_materialized_sample_key",
        ),
        SaIndex(
            "ix_etf_validation_materialized_sample_source",
            "validation_run_id",
            "source_date",
            "horizon_sessions",
        ),
        SaIndex(
            "ix_etf_validation_materialized_sample_status",
            "validation_run_id",
            "status",
        ),
        CheckConstraint(
            "status IN ('completed', 'pending', 'overlapping', 'excluded')",
            name="ck_etf_validation_materialized_sample_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    continuation_id: Mapped[int] = mapped_column(
        ForeignKey("etf_validation_continuations.id", ondelete="CASCADE")
    )
    validation_run_id: Mapped[int] = mapped_column(
        ForeignKey("etf_signal_validation_runs.id", ondelete="CASCADE")
    )
    source_event_id: Mapped[int] = mapped_column(
        ForeignKey("etf_signal_validation_source_events.id", ondelete="RESTRICT")
    )
    source_event_hash: Mapped[str] = mapped_column(String(128))
    manifest_hash: Mapped[str] = mapped_column(String(128))
    price_basis: Mapped[str] = mapped_column(String(64))
    source_date: Mapped[date] = mapped_column(Date)
    horizon_sessions: Mapped[int] = mapped_column(Integer)
    asset_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    adjusted_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    adjusted_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_rate: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_rate: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost_rate: Mapped[float] = mapped_column(Float, default=0.0)
    adverse_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    interval_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    interval_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_hash: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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


class EtfCatalystSourceRegistry(Base):
    __tablename__ = "etf_catalyst_source_registry"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "registry_version",
            name="uq_etf_catalyst_source_registry_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    registry_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_class: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(1000), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    cadence: Mapped[str] = mapped_column(String(64), nullable=False)
    fetch_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_retention_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfCatalystReceipt(Base):
    __tablename__ = "etf_catalyst_receipts"
    __table_args__ = (
        UniqueConstraint(
            "source_registry_id",
            "item_identity_hash",
            "content_hash",
            name="uq_etf_catalyst_receipt_version",
        ),
        SaIndex(
            "ix_etf_catalyst_receipt_cutoff",
            "source_registry_id",
            "first_received_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    source_registry_id: Mapped[int] = mapped_column(
        ForeignKey("etf_catalyst_source_registry.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_time_precision: Mapped[str] = mapped_column(String(32), nullable=False)
    first_received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fetch_state: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    item_identity_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_content_ref: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    correction_of_receipt_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_catalyst_receipts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfCatalystExtractionAttempt(Base):
    __tablename__ = "etf_catalyst_extraction_attempts"
    __table_args__ = (
        UniqueConstraint(
            "receipt_id",
            "extractor_version",
            "attempt_hash",
            name="uq_etf_catalyst_extraction_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[int] = mapped_column(
        ForeignKey("etf_catalyst_receipts.id", ondelete="RESTRICT")
    )
    extractor_version: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cited_receipt_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfCatalystEventVersion(Base):
    __tablename__ = "etf_catalyst_event_versions"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "event_version",
            name="uq_etf_catalyst_event_version",
        ),
        SaIndex(
            "ix_etf_catalyst_event_cutoff",
            "verification_state",
            "first_received_at",
            "effective_start",
            "effective_end",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entities_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    supporting_receipt_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    first_received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    direct_theme_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    proxy_theme_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_state: Mapped[str] = mapped_column(String(32), nullable=False)
    supersedes_event_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("etf_catalyst_event_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfCatalystCoverageObservation(Base):
    __tablename__ = "etf_catalyst_coverage_observations"
    __table_args__ = (
        UniqueConstraint(
            "source_registry_id",
            "theme_id",
            "session_date",
            "cutoff_at",
            name="uq_etf_catalyst_coverage_observation",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_registry_id: Mapped[int] = mapped_column(
        ForeignKey("etf_catalyst_source_registry.id", ondelete="RESTRICT")
    )
    theme_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    observation_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfCatalystShadowSnapshot(Base):
    __tablename__ = "etf_catalyst_shadow_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "theme_id",
            "session_date",
            "cutoff_at",
            "contract_version",
            name="uq_etf_catalyst_shadow_snapshot_cutoff",
        ),
        SaIndex(
            "ix_etf_catalyst_shadow_snapshot_latest",
            "theme_id",
            "session_date",
            "cutoff_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    coverage_state: Mapped[str] = mapped_column(String(32), nullable=False)
    event_versions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_coverage_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    receipt_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfCatalystRunCheckpoint(Base):
    __tablename__ = "etf_catalyst_run_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('idle', 'running', 'partial', 'complete', 'failed')",
            name="ck_etf_catalyst_run_status",
        ),
        UniqueConstraint(
            "run_kind",
            "policy_hash",
            name="uq_etf_catalyst_run_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_cursor_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    processed_receipt_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    batch_hashes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EtfCatalystEventStudyEvidence(Base):
    __tablename__ = "etf_catalyst_event_study_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manifest_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    ranking_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cohorts_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    outcomes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    exclusions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    intervals_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_state: Mapped[str] = mapped_column(String(32), nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


class EtfActionValidationRun(Base):
    __tablename__ = "etf_action_validation_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_etf_action_validation_run_key"),
        SaIndex("ix_etf_action_validation_runs_policy", "policy_version", "sealed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_key: Mapped[str] = mapped_column(String(128))
    policy_version: Mapped[str] = mapped_column(String(64))
    candidate_registry_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    candidate_registry_hash: Mapped[str] = mapped_column(String(64))
    validation_contract_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation_contract_hash: Mapped[str] = mapped_column(String(64))
    sealed_at: Mapped[datetime] = mapped_column(DateTime)
    development_outcomes_calculated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    development_gate_artifact_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    development_gate_artifact_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    holdout_first_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    holdout_input_snapshot_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )


@event.listens_for(Session, "before_flush")
def _prevent_etf_action_validation_contract_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    frozen_fields = (
        "run_key",
        "policy_version",
        "candidate_registry_json",
        "candidate_registry_hash",
        "validation_contract_json",
        "validation_contract_hash",
        "sealed_at",
    )
    write_once_fields = (
        "development_outcomes_calculated_at",
        "development_gate_artifact_json",
        "development_gate_artifact_hash",
        "holdout_first_consumed_at",
        "holdout_input_snapshot_hash",
    )
    for instance in session.new:
        if isinstance(instance, EtfActionValidationRun) and any(
            getattr(instance, field) is not None for field in write_once_fields
        ):
            raise EtfActionValidationImmutableError(
                "ETF action validation evidence markers are write-once service outputs"
            )
    for instance in session.dirty:
        if not isinstance(instance, EtfActionValidationRun):
            continue
        state = inspect(instance)
        if any(state.attrs[field].history.has_changes() for field in frozen_fields):
            raise EtfActionValidationImmutableError(
                "sealed ETF action validation candidate and run contracts are immutable"
            )
        changed_write_once_fields = tuple(
            field
            for field in write_once_fields
            if state.attrs[field].history.has_changes()
        )
        holdout_is_being_consumed = any(
            field in changed_write_once_fields
            for field in (
                "holdout_first_consumed_at",
                "holdout_input_snapshot_hash",
            )
        )
        if holdout_is_being_consumed and not (
            isinstance(instance.development_gate_artifact_json, dict)
            and instance.development_gate_artifact_json.get("holdout_ready") is True
        ):
            raise EtfActionValidationImmutableError(
                "holdout cannot be consumed before the persisted development gate passes"
            )
        if changed_write_once_fields:
            raise EtfActionValidationImmutableError(
                "ETF action validation evidence markers are write-once service outputs"
            )


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
    exit_state_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="active")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TrackedPositionActionDecision(Base):
    __tablename__ = "tracked_position_action_decisions"
    __table_args__ = (
        UniqueConstraint(
            "position_episode_id",
            "exposure_version",
            "policy_version",
            "action_cycle_id",
            "target_stage",
            name="uq_tracked_action_target_stage",
        ),
        CheckConstraint(
            "target_remaining_fraction >= 0 AND target_remaining_fraction <= 1",
            name="ck_tracked_action_target_fraction",
        ),
        CheckConstraint(
            "status IN ('proposed','acknowledged','partially_executed','executed','expired','cancelled','superseded')",
            name="ck_tracked_action_status",
        ),
        SaIndex(
            "uq_tracked_action_current_slot",
            "tracked_position_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current"),
        ),
        SaIndex(
            "ix_tracked_action_history",
            "tracked_position_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tracked_position_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="CASCADE")
    )
    position_episode_id: Mapped[str] = mapped_column(String(64))
    exposure_version: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(String(64))
    action_cycle_id: Mapped[str] = mapped_column(String(64))
    target_stage: Mapped[str] = mapped_column(String(32))
    target_remaining_fraction: Mapped[float] = mapped_column(Float)
    baseline_normalized_quantity: Mapped[float] = mapped_column(Float)
    baseline_account_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_adjustment_factor: Mapped[float] = mapped_column(Float, default=1.0)
    baseline_source: Mapped[str] = mapped_column(String(64))
    target_normalized_quantity: Mapped[float] = mapped_column(Float)
    target_account_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64))
    data_state: Mapped[str] = mapped_column(String(32), default="eligible")
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    execution_provenance: Mapped[str] = mapped_column(String(32), default="none")
    cumulative_executed_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    contributing_rules_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    alert_episode_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_action_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TrackedPositionActionExecution(Base):
    __tablename__ = "tracked_position_action_executions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_tracked_action_execution_request"),
        CheckConstraint("execution_quantity > 0", name="ck_tracked_execution_quantity"),
        CheckConstraint("execution_price > 0", name="ck_tracked_execution_price"),
        CheckConstraint("fees >= 0", name="ck_tracked_execution_fees"),
        SaIndex(
            "ix_tracked_action_execution_history",
            "action_decision_id",
            "executed_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_decision_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_position_action_decisions.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tracked_position_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    execution_provenance: Mapped[str] = mapped_column(String(32), default="owner_confirmed")
    execution_quantity: Mapped[float] = mapped_column(Float)
    execution_price: Mapped[float] = mapped_column(Float)
    price_source: Mapped[str] = mapped_column(String(64))
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    before_normalized_quantity: Mapped[float] = mapped_column(Float)
    resulting_normalized_quantity: Mapped[float] = mapped_column(Float)
    resulting_position_state_version: Mapped[int] = mapped_column(Integer)
    executed_at: Mapped[datetime] = mapped_column(DateTime)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TrackedPositionActionTransitionReceipt(Base):
    __tablename__ = "tracked_position_action_transition_receipts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_tracked_action_transition_request",
        ),
        SaIndex(
            "ix_tracked_action_transition_history",
            "action_decision_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_decision_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_position_action_decisions.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tracked_position_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    transition: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TrackedEtfSleeveLedgerEvent(Base):
    __tablename__ = "tracked_etf_sleeve_ledger_events"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "sequence_no",
            name="uq_tracked_etf_sleeve_ledger_sequence",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_tracked_etf_sleeve_ledger_idempotency",
        ),
        UniqueConstraint(
            "event_hash",
            name="uq_tracked_etf_sleeve_ledger_event_hash",
        ),
        CheckConstraint(
            "sequence_no >= 1",
            name="ck_tracked_etf_sleeve_ledger_sequence",
        ),
        CheckConstraint(
            "event_type IN ("
            "'opening_reconciliation','reconciliation','cash_deposit','cash_withdrawal',"
            "'buy','sell','distribution','fee','quantity_adjustment')",
            name="ck_tracked_etf_sleeve_ledger_event_type",
        ),
        CheckConstraint(
            "provenance IN ("
            "'owner_confirmed','owner_documented','broker_verified','provider_verified')",
            name="ck_tracked_etf_sleeve_ledger_provenance",
        ),
        CheckConstraint(
            "(event_type NOT IN ('opening_reconciliation','reconciliation',"
            "'cash_deposit','cash_withdrawal','buy','sell','fee') "
            "OR provenance IN ('owner_confirmed','owner_documented','broker_verified'))",
            name="ck_tracked_etf_sleeve_ledger_account_provenance",
        ),
        CheckConstraint(
            "(provenance = 'owner_confirmed' OR evidence_ref IS NOT NULL)",
            name="ck_tracked_etf_sleeve_ledger_external_evidence",
        ),
        CheckConstraint(
            "cash_balance_after IS NULL OR cash_balance_after >= 0",
            name="ck_tracked_etf_sleeve_ledger_cash_balance",
        ),
        CheckConstraint(
            "quantity_after IS NULL OR quantity_after >= 0",
            name="ck_tracked_etf_sleeve_ledger_quantity_after",
        ),
        CheckConstraint(
            "execution_price IS NULL OR execution_price > 0",
            name="ck_tracked_etf_sleeve_ledger_execution_price",
        ),
        CheckConstraint(
            "fees >= 0",
            name="ck_tracked_etf_sleeve_ledger_fees",
        ),
        CheckConstraint(
            "adjustment_factor > 0",
            name="ck_tracked_etf_sleeve_ledger_adjustment_factor",
        ),
        CheckConstraint(
            "(event_type NOT IN ('opening_reconciliation','reconciliation') "
            "OR cash_balance_after IS NOT NULL)",
            name="ck_tracked_etf_sleeve_ledger_reconciliation_payload",
        ),
        CheckConstraint(
            "(event_type != 'buy' OR (tracked_position_id IS NOT NULL "
            "AND asset_code IS NOT NULL AND quantity_delta > 0 AND quantity_after >= 0 "
            "AND cash_delta < 0 AND execution_price > 0))",
            name="ck_tracked_etf_sleeve_ledger_buy_payload",
        ),
        CheckConstraint(
            "(event_type != 'sell' OR (tracked_position_id IS NOT NULL "
            "AND asset_code IS NOT NULL AND quantity_delta < 0 AND quantity_after >= 0 "
            "AND cash_delta > 0 AND execution_price > 0))",
            name="ck_tracked_etf_sleeve_ledger_sell_payload",
        ),
        CheckConstraint(
            "(event_type != 'cash_deposit' OR cash_delta > 0) "
            "AND (event_type != 'cash_withdrawal' OR cash_delta < 0) "
            "AND (event_type != 'distribution' OR (asset_code IS NOT NULL AND cash_delta > 0)) "
            "AND (event_type != 'fee' OR cash_delta < 0)",
            name="ck_tracked_etf_sleeve_ledger_cash_event_payload",
        ),
        CheckConstraint(
            "(event_type != 'quantity_adjustment' OR (tracked_position_id IS NOT NULL "
            "AND asset_code IS NOT NULL AND quantity_delta IS NOT NULL "
            "AND quantity_delta != 0 AND quantity_after >= 0))",
            name="ck_tracked_etf_sleeve_ledger_quantity_event_payload",
        ),
        SaIndex(
            "ix_tracked_etf_sleeve_ledger_owner_effective",
            "user_id",
            "effective_date",
            "sequence_no",
        ),
        SaIndex(
            "ix_tracked_etf_sleeve_ledger_position",
            "tracked_position_id",
            "sequence_no",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    tracked_position_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(32))
    asset_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    cash_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_balance_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    adjustment_factor: Mapped[float] = mapped_column(Float, default=1.0)
    holdings_after_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    provenance: Mapped[str] = mapped_column(String(32))
    evidence_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contract_version: Mapped[str] = mapped_column(String(64))
    predecessor_event_hash: Mapped[str | None] = mapped_column(
        ForeignKey(
            "tracked_etf_sleeve_ledger_events.event_hash",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TrackedEtfSleeveDailySnapshot(Base):
    __tablename__ = "tracked_etf_sleeve_daily_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "sequence_no",
            name="uq_tracked_etf_sleeve_snapshot_sequence",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_tracked_etf_sleeve_snapshot_idempotency",
        ),
        UniqueConstraint(
            "snapshot_hash",
            name="uq_tracked_etf_sleeve_snapshot_hash",
        ),
        CheckConstraint(
            "sequence_no >= 1",
            name="ck_tracked_etf_sleeve_snapshot_sequence",
        ),
        CheckConstraint(
            "coverage_state IN ('eligible','unavailable')",
            name="ck_tracked_etf_sleeve_snapshot_coverage_state",
        ),
        CheckConstraint(
            "risk_state IN ('normal','reduce_only','data_halt')",
            name="ck_tracked_etf_sleeve_snapshot_risk_state",
        ),
        CheckConstraint(
            "cash_balance IS NULL OR cash_balance >= 0",
            name="ck_tracked_etf_sleeve_snapshot_cash",
        ),
        CheckConstraint(
            "market_value IS NULL OR market_value >= 0",
            name="ck_tracked_etf_sleeve_snapshot_market_value",
        ),
        CheckConstraint(
            "equity IS NULL OR equity >= 0",
            name="ck_tracked_etf_sleeve_snapshot_equity",
        ),
        CheckConstraint(
            "flow_adjusted_nav IS NULL OR flow_adjusted_nav > 0",
            name="ck_tracked_etf_sleeve_snapshot_nav",
        ),
        CheckConstraint(
            "high_water_nav IS NULL OR high_water_nav > 0",
            name="ck_tracked_etf_sleeve_snapshot_high_water",
        ),
        CheckConstraint(
            "drawdown_pct IS NULL OR (drawdown_pct >= -1 AND drawdown_pct <= 0)",
            name="ck_tracked_etf_sleeve_snapshot_drawdown",
        ),
        CheckConstraint(
            "holding_count >= 0 AND valued_holding_count >= 0 "
            "AND valued_holding_count <= holding_count",
            name="ck_tracked_etf_sleeve_snapshot_counts",
        ),
        CheckConstraint(
            "ledger_coverage_ratio >= 0 AND ledger_coverage_ratio <= 1 "
            "AND valuation_coverage_ratio >= 0 AND valuation_coverage_ratio <= 1",
            name="ck_tracked_etf_sleeve_snapshot_coverage_ratios",
        ),
        CheckConstraint(
            "recovery_streak >= 0 AND signal_stop_cycle_count >= 0 "
            "AND cooldown_sessions_remaining >= 0 "
            "AND confirmed_stop_cycle_count >= 0 "
            "AND confirmed_stop_cycle_count <= signal_stop_cycle_count",
            name="ck_tracked_etf_sleeve_snapshot_risk_counters",
        ),
        CheckConstraint(
            "(coverage_state != 'eligible' OR (cash_balance IS NOT NULL "
            "AND market_value IS NOT NULL AND equity IS NOT NULL "
            "AND flow_adjusted_nav IS NOT NULL AND high_water_nav IS NOT NULL "
            "AND (risk_state = 'data_halt' OR drawdown_pct IS NOT NULL) "
            "AND ledger_head_event_hash IS NOT NULL "
            "AND ledger_coverage_ratio = 1 AND valuation_coverage_ratio = 1 "
            "AND valued_holding_count = holding_count))",
            name="ck_tracked_etf_sleeve_snapshot_eligible_payload",
        ),
        CheckConstraint(
            "(coverage_state != 'unavailable' OR (risk_state = 'data_halt' "
            "AND equity IS NULL AND flow_adjusted_nav IS NULL "
            "AND high_water_nav IS NULL AND drawdown_pct IS NULL))",
            name="ck_tracked_etf_sleeve_snapshot_unavailable_payload",
        ),
        SaIndex(
            "ix_tracked_etf_sleeve_snapshot_owner_date",
            "user_id",
            "snapshot_date",
            "sequence_no",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    snapshot_date: Mapped[date] = mapped_column(Date)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    cash_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_external_flow: Mapped[float] = mapped_column(Float, default=0.0)
    flow_adjusted_nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_water_nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_count: Mapped[int] = mapped_column(Integer, default=0)
    valued_holding_count: Mapped[int] = mapped_column(Integer, default=0)
    ledger_coverage_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    valuation_coverage_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_state: Mapped[str] = mapped_column(String(32))
    risk_state: Mapped[str] = mapped_column(String(32))
    recovery_streak: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_sessions_remaining: Mapped[int] = mapped_column(Integer, default=0)
    signal_stop_cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_stop_cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    ledger_head_event_hash: Mapped[str | None] = mapped_column(
        ForeignKey(
            "tracked_etf_sleeve_ledger_events.event_hash",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    input_contract_hash: Mapped[str] = mapped_column(String(64))
    nav_contract_hash: Mapped[str] = mapped_column(String(64))
    risk_policy_hash: Mapped[str] = mapped_column(String(64))
    contract_version: Mapped[str] = mapped_column(String(64))
    predecessor_snapshot_hash: Mapped[str | None] = mapped_column(
        ForeignKey(
            "tracked_etf_sleeve_daily_snapshots.snapshot_hash",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


@event.listens_for(Session, "before_flush")
def _prevent_tracked_etf_sleeve_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    immutable_types = (TrackedEtfSleeveLedgerEvent, TrackedEtfSleeveDailySnapshot)
    if any(
        isinstance(instance, immutable_types)
        and session.is_modified(instance, include_collections=True)
        for instance in session.dirty
    ):
        raise TrackedEtfSleeveImmutableError(
            "tracked ETF sleeve ledger events and daily snapshots are immutable"
        )
    if any(isinstance(instance, immutable_types) for instance in session.deleted):
        raise TrackedEtfSleeveImmutableError(
            "tracked ETF sleeve ledger events and daily snapshots cannot be deleted"
        )


class TrackedPositionLifecycleShadowEvidence(Base):
    __tablename__ = "tracked_position_lifecycle_shadow_evidence"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tracked_position_id",
            "policy_version",
            "position_episode_id",
            "exposure_version",
            "stream_sequence",
            name="uq_tracked_lifecycle_shadow_stream_sequence",
        ),
        SaIndex(
            "ix_tracked_lifecycle_shadow_history",
            "user_id",
            "tracked_position_id",
            "policy_version",
            "position_episode_id",
            "exposure_version",
            "stream_sequence",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tracked_position_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="CASCADE")
    )
    policy_version: Mapped[str] = mapped_column(String(64))
    position_episode_id: Mapped[str] = mapped_column(String(64))
    exposure_version: Mapped[int] = mapped_column(Integer)
    stream_sequence: Mapped[int] = mapped_column(Integer)
    predecessor_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    event_schema_version: Mapped[str] = mapped_column(String(32))
    trade_session: Mapped[date] = mapped_column(Date)
    repeat_slot: Mapped[str] = mapped_column(String(64))
    sealed_snapshot_hash: Mapped[str] = mapped_column(String(64))
    production_position_state_version: Mapped[int] = mapped_column(Integer)
    rule_states_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    transitions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    action_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_state: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


@event.listens_for(Session, "before_flush")
def _prevent_tracked_position_lifecycle_shadow_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    if any(
        isinstance(instance, TrackedPositionLifecycleShadowEvidence)
        and session.is_modified(instance, include_collections=True)
        for instance in session.dirty
    ):
        raise TrackedPositionLifecycleShadowImmutableError(
            "tracked position lifecycle shadow evidence is immutable during retention"
        )


class TrackedPositionNotificationEnvelope(Base):
    __tablename__ = "tracked_position_notification_envelopes"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "trade_session",
            "route",
            "severity",
            "channel",
            "sealed_snapshot_hash",
            "digest_revision",
            name="uq_tracked_notification_envelope_identity",
        ),
        SaIndex(
            "ix_tracked_notification_envelope_pending",
            "status",
            "lease_expires_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    trade_session: Mapped[date] = mapped_column(Date)
    route: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(32))
    sealed_snapshot_hash: Mapped[str] = mapped_column(String(64))
    digest_revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    claim_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    smtp_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rendered_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TrackedPositionNotificationItem(Base):
    __tablename__ = "tracked_position_notification_items"
    __table_args__ = (
        UniqueConstraint(
            "alert_episode_id",
            "transition",
            "recipient",
            "channel",
            "repeat_slot",
            name="uq_tracked_notification_item_identity",
        ),
        SaIndex(
            "ix_tracked_notification_item_repeat",
            "tracked_position_id",
            "repeat_slot",
            "created_at",
            "id",
        ),
        SaIndex(
            "ix_tracked_notification_item_envelope",
            "envelope_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tracked_position_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_positions.id", ondelete="CASCADE")
    )
    action_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_action_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    alert_episode_id: Mapped[str] = mapped_column(String(64))
    transition: Mapped[str] = mapped_column(String(32))
    recipient: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(32))
    repeat_slot: Mapped[str] = mapped_column(String(64))
    route: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    suppression_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_eligible_repeat_slot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    envelope_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_notification_envelopes.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


_SEALED_NOTIFICATION_ITEM_FIELDS = frozenset(
    {
        "user_id",
        "tracked_position_id",
        "action_decision_id",
        "alert_episode_id",
        "transition",
        "recipient",
        "channel",
        "repeat_slot",
        "route",
        "severity",
        "payload_json",
        "status",
        "suppression_reason",
        "next_eligible_repeat_slot",
        "envelope_id",
    }
)
_SEALED_NOTIFICATION_ENVELOPE_FIELDS = frozenset(
    {
        "user_id",
        "trade_session",
        "route",
        "severity",
        "channel",
        "sealed_snapshot_hash",
        "digest_revision",
        "message_id",
        "template_name",
        "template_version",
        "sealed_at",
        "rendered_subject",
        "rendered_body",
        "rendered_content_hash",
    }
)


def _notification_was_sealed(instance: object, *, item: bool) -> bool:
    state = inspect(instance)
    if state is None:
        return False
    if item:
        status = state.attrs.status
        return "sealed" in status.history.deleted or (
            getattr(instance, "status", None) == "sealed" and not status.history.has_changes()
        )
    sealed_at = state.attrs.sealed_at
    first_attempt_at = state.attrs.first_attempt_at
    return (
        any(value is not None for value in sealed_at.history.deleted)
        or (getattr(instance, "sealed_at", None) is not None and not sealed_at.history.has_changes())
        or any(value is not None for value in first_attempt_at.history.deleted)
        or (
            getattr(instance, "first_attempt_at", None) is not None
            and not first_attempt_at.history.has_changes()
        )
    )


@event.listens_for(Session, "before_flush")
def _prevent_sealed_notification_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    assigned_envelope_ids: set[int] = set()
    for instance in session.dirty:
        if isinstance(instance, TrackedPositionNotificationItem):
            if _notification_was_sealed(instance, item=True) and _changed_fields(
                instance, _SEALED_NOTIFICATION_ITEM_FIELDS
            ):
                raise NotificationEnvelopeImmutableError("sealed notification item is immutable")
        elif isinstance(instance, TrackedPositionNotificationEnvelope):
            if _notification_was_sealed(instance, item=False) and _changed_fields(
                instance, _SEALED_NOTIFICATION_ENVELOPE_FIELDS
            ):
                raise NotificationEnvelopeImmutableError("sealed notification envelope is immutable")
    for instance in (*session.new, *session.dirty):
        if not isinstance(instance, TrackedPositionNotificationItem):
            continue
        envelope_id = instance.envelope_id
        envelope_history = inspect(instance).attrs.envelope_id.history
        if envelope_id is not None and (
            instance in session.new or envelope_history.has_changes()
        ):
            assigned_envelope_ids.add(envelope_id)
    if not assigned_envelope_ids:
        return
    sealed_envelope_ids = set(
        session.scalars(
            select(TrackedPositionNotificationEnvelope.id).where(
                TrackedPositionNotificationEnvelope.id.in_(assigned_envelope_ids),
                (TrackedPositionNotificationEnvelope.sealed_at.is_not(None))
                | (TrackedPositionNotificationEnvelope.first_attempt_at.is_not(None)),
            )
        )
    )
    sealed_envelope_ids.update(
        envelope.id
        for envelope in session.new
        if isinstance(envelope, TrackedPositionNotificationEnvelope)
        and envelope.id in assigned_envelope_ids
        and (envelope.sealed_at is not None or envelope.first_attempt_at is not None)
    )
    if sealed_envelope_ids:
        raise NotificationEnvelopeImmutableError(
            "late notification item cannot attach to a sealed envelope"
        )


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
    alert_episode_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alert_transition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_action_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    notification_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_notification_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    notification_envelope_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_notification_envelopes.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_status: Mapped[str] = mapped_column(String(32), default="pending")
    email_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)



class TrackedPositionAlertAudit(Base):
    __tablename__ = "tracked_position_alert_audits"
    __table_args__ = (
        SaIndex(
            "ix_tracked_alert_audit_cursor",
            "tracked_position_id",
            "created_at",
            "id",
        ),
    )

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
    event_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    event_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    alert_episode_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alert_transition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_action_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    notification_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_notification_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    notification_envelope_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_position_notification_envelopes.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    execution_provenance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


@event.listens_for(Session, "before_flush")
def _prevent_tracked_position_audit_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    if any(
        isinstance(instance, TrackedPositionAlertAudit)
        and session.is_modified(instance, include_collections=True)
        for instance in session.dirty
    ):
        raise TrackedPositionAuditImmutableError(
            "tracked position audit events are immutable during retention"
        )


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


class EtfFactorExperimentEvidence(Base):
    __tablename__ = "etf_factor_experiment_evidence"
    __table_args__ = (
        UniqueConstraint("manifest_hash", name="uq_etf_factor_evidence_manifest"),
        UniqueConstraint("evidence_hash", name="uq_etf_factor_evidence_hash"),
        SaIndex(
            "ix_etf_factor_evidence_family_latest",
            "experiment_family",
            "created_at",
        ),
        SaIndex(
            "ix_etf_factor_evidence_hypothesis_registry",
            "hypothesis_registry_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    ranking_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    code_version: Mapped[str] = mapped_column(String(128), nullable=False)
    experiment_family: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    hypothesis_registry_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    evidence_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    samples_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    aggregates_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    exclusions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    intervals_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    split_reports_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    costs_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    promotion_state: Mapped[str] = mapped_column(String(64), nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfPitCaptureSource(Base):
    """Append-only production provenance for one complete ETF PIT source."""

    __tablename__ = "etf_pit_capture_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_signal_run_id",
            name="uq_etf_pit_capture_source_signal_run",
        ),
        UniqueConstraint(
            "source_context_hash",
            name="uq_etf_pit_capture_source_context_hash",
        ),
        CheckConstraint(
            "readiness_state = 'complete'",
            name="ck_etf_pit_capture_source_complete_readiness",
        ),
        CheckConstraint(
            "target_date_coverage_ratio >= 0 AND target_date_coverage_ratio <= 1",
            name="ck_etf_pit_capture_source_target_coverage",
        ),
        CheckConstraint(
            "warmup_coverage_ratio >= 0 AND warmup_coverage_ratio <= 1",
            name="ck_etf_pit_capture_source_warmup_coverage",
        ),
        SaIndex(
            "ix_etf_pit_capture_sources_trade_date",
            "as_of_trade_date",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_signal_run_id: Mapped[int] = mapped_column(
        ForeignKey("short_research_signal_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    as_of_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_context_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    universe_manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    ranking_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    research_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    actionable_contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    readiness_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    readiness_state: Mapped[str] = mapped_column(String(32), nullable=False)
    target_date_coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    warmup_coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    market_decision_cutoff: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    data_receipt_cutoff: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    replay_visibility_cutoff: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cutoff_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_health_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfFactorExperimentCheckpoint(Base):
    __tablename__ = "etf_factor_experiment_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "manifest_hash",
            "code_version",
            name="uq_etf_factor_checkpoint_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    code_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    cursor_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    processed_asset_codes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    completed_batch_hashes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    cached_factor_rows_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    exclusion_count: Mapped[int] = mapped_column(Integer, default=0)
    batch_count: Mapped[int] = mapped_column(Integer, default=0)
    peak_batch_size: Mapped[int] = mapped_column(Integer, default=0)
    runtime_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    peak_memory_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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


_SNAPSHOT_RUN_FIELDS = (
    "status",
    "started_at",
    "finished_at",
    "as_of_date",
    "config_json",
    "summary_json",
    "error_message",
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
    "decision_data_item_count",
    "decision_data_coverage_ratio",
    "eligible_item_count",
    "coverage_ratio",
    "published_at",
    "idempotency_key",
)
_SNAPSHOT_ITEM_FIELDS = (
    "run_id",
    "asset_type",
    "asset_code",
    "rank",
    "total_score",
    "conclusion",
    "score_breakdown_json",
    "risk_flags_json",
    "rationale_json",
    "metrics_json",
    "created_at",
    "ranking_score",
    "score_eligible",
    "global_rank",
)
_INITIAL_PUBLICATION_ALLOWED_RUN_FIELDS = frozenset(
    {"summary_json", "publication_state", "published_at"}
)
_CANONICAL_PUBLICATION_FROZEN_FIELDS = (
    "source_signal_run_id",
    "as_of_trade_date",
    "scope_kind",
    "scope_hash",
    "price_basis",
    "research_contract_hash",
    "actionable_contract_hash",
    "readiness_policy_version",
    "readiness_policy_hash",
    "universe_snapshot_hash",
    "input_snapshot_hash",
    "data_cutoff",
    "provider_health_seal_hash",
    "provider_health_check_time",
    "provider_health_policy",
    "provider_health_covered_fields",
    "provider_health_source_range",
    "provider_health_state",
    "provider_health_unavailable_reason",
    "surface_group_hash",
    "publication_identity_hash",
    "canonical_slot_hash",
    "supersedes_publication_id",
    "created_at",
)


def _changed_fields(instance: object, fields: Collection[str]) -> bool:
    state = inspect(instance)
    if state is None:
        return False
    return any(state.attrs[field].history.has_changes() for field in fields)


@event.listens_for(Session, "before_flush")
def _prevent_published_snapshot_mutation(session: Session, _flush_context: object, _instances: object) -> None:
    published_run_ids: set[int] = set()
    sealing_run_ids: set[int] = set()
    item_run_ids: set[int] = set()
    authorized_run_ids = session.info.get(_SNAPSHOT_PUBLICATION_AUTHORIZATION_KEY)
    if not isinstance(authorized_run_ids, set):
        authorized_run_ids = set()
    for instance in session.dirty:
        if isinstance(instance, EtfIntradayQuoteEvidenceRef):
            raise IntradayQuoteEvidenceImmutableError(
                "intraday quote evidence references are immutable"
            )
        if isinstance(instance, EtfCanonicalPublicationRegistry):
            if _changed_fields(instance, _CANONICAL_PUBLICATION_FROZEN_FIELDS):
                raise CanonicalPublicationImmutableError(
                    "canonical publication identity and provenance are immutable"
                )
            current_history = inspect(instance).attrs.is_current.history
            if current_history.has_changes():
                explicitly_superseded = (
                    instance.is_current is False
                    and True in current_history.deleted
                    and any(
                        isinstance(candidate, EtfCanonicalPublicationRegistry)
                        and candidate.supersedes_publication_id == instance.id
                        for candidate in session.new
                    )
                )
                if not explicitly_superseded:
                    raise CanonicalPublicationImmutableError(
                        "canonical publication current state may change only through explicit supersession"
                    )
        if isinstance(instance, EtfAdjustedPriceRevision):
            raise AdjustedPriceRevisionImmutableError(
                "adjusted price revisions are immutable"
            )
        if isinstance(instance, DatabaseInstanceIdentity):
            raise DatabaseInstanceIdentityImmutableError(
                "database instance identity is immutable after provisioning"
            )
        if isinstance(instance, EtfValidationMaterializedSample):
            raise ValidationEvidenceImmutableError(
                "validation materialized samples are immutable"
            )
        if isinstance(instance, EtfFactorExperimentEvidence):
            raise ValidationEvidenceImmutableError(
                "factor experiment evidence is immutable"
            )
        if isinstance(instance, EtfPitCaptureSource):
            raise PitCaptureSourceImmutableError("PIT capture source is immutable")
        if isinstance(
            instance,
            (
                EtfCatalystSourceRegistry,
                EtfCatalystReceipt,
                EtfCatalystExtractionAttempt,
                EtfCatalystEventVersion,
                EtfCatalystCoverageObservation,
                EtfCatalystShadowSnapshot,
                EtfCatalystEventStudyEvidence,
            ),
        ):
            raise CatalystEvidenceImmutableError(
                "catalyst registry and evidence records are immutable"
            )
        if isinstance(instance, EtfSignalValidationSourceEvent):
            validation_run = session.get(
                EtfSignalValidationRun,
                instance.validation_run_id,
            )
            if validation_run is not None and validation_run.status == "success":
                raise ValidationEvidenceImmutableError(
                    "registered validation source events are immutable"
                )
        if (
            isinstance(instance, EtfValidationContinuation)
            and instance.status == "complete"
            and inspect(instance).attrs.status.history.has_changes() is False
        ):
            raise ValidationEvidenceImmutableError(
                "completed validation continuation is immutable"
            )
        if isinstance(instance, ShortResearchSignalRun):
            state = inspect(instance)
            publication_history = state.attrs.publication_state.history
            initial_publication = (
                instance.publication_state == "published"
                and publication_history.has_changes()
                and "published" not in publication_history.deleted
            )
            if initial_publication:
                if instance.id not in authorized_run_ids:
                    raise PublishedSnapshotImmutableError(
                        "initial publication requires the authorized snapshot publisher"
                    )
                changed_fields = {
                    field
                    for field in (*_SNAPSHOT_RUN_FIELDS, "publication_state")
                    if state.attrs[field].history.has_changes()
                }
                disallowed_fields = changed_fields - _INITIAL_PUBLICATION_ALLOWED_RUN_FIELDS
                if disallowed_fields:
                    raise PublishedSnapshotImmutableError(
                        "initial publication seal cannot include run content changes: "
                        + ", ".join(sorted(disallowed_fields))
                    )
                sealing_run_ids.add(instance.id)
            was_published = "published" in publication_history.deleted or (
                instance.publication_state == "published" and not publication_history.has_changes()
            )
            if was_published and _changed_fields(instance, _SNAPSHOT_RUN_FIELDS):
                raise PublishedSnapshotImmutableError("published ranking snapshot content is immutable")
            if "published" in publication_history.deleted:
                raise PublishedSnapshotImmutableError("published ranking snapshot is immutable")
        elif isinstance(instance, ShortResearchSignalItem) and _changed_fields(instance, _SNAPSHOT_ITEM_FIELDS):
            item_run_ids.add(instance.run_id)
            item_run_ids.update(inspect(instance).attrs.run_id.history.deleted)
    for instance in session.new:
        if isinstance(instance, ShortResearchSignalRun) and instance.publication_state == "published":
            raise PublishedSnapshotImmutableError(
                "ranking snapshot cannot be created as published"
            )
        if isinstance(instance, EtfCanonicalPublicationRegistry) and instance.is_current is not True:
            raise CanonicalPublicationImmutableError(
                "canonical publication registry entries must be created as current"
            )
        if isinstance(instance, ShortResearchSignalItem):
            item_run_ids.add(instance.run_id)
    for instance in session.deleted:
        if isinstance(instance, EtfIntradayQuoteEvidenceRef):
            raise IntradayQuoteEvidenceImmutableError(
                "intraday quote evidence references cannot be deleted"
            )
        if isinstance(instance, EtfAdjustedPriceRevision):
            raise AdjustedPriceRevisionImmutableError(
                "adjusted price revisions cannot be deleted"
            )
        if isinstance(instance, EtfPitCaptureSource):
            raise PitCaptureSourceImmutableError(
                "PIT capture source cannot be deleted"
            )
        if isinstance(instance, EtfCanonicalPublicationRegistry):
            raise CanonicalPublicationImmutableError(
                "canonical publication registry entries cannot be deleted"
            )
        if isinstance(instance, DatabaseInstanceIdentity):
            raise DatabaseInstanceIdentityImmutableError(
                "database instance identity cannot be deleted"
            )
        if isinstance(
            instance,
            (
                EtfSignalValidationSourceEvent,
                EtfValidationMaterializedSample,
                EtfValidationContinuation,
                EtfFactorExperimentEvidence,
            ),
        ):
            raise ValidationEvidenceImmutableError(
                "validation evidence cannot be deleted"
            )
        if isinstance(
            instance,
            (
                EtfCatalystSourceRegistry,
                EtfCatalystReceipt,
                EtfCatalystExtractionAttempt,
                EtfCatalystEventVersion,
                EtfCatalystCoverageObservation,
                EtfCatalystShadowSnapshot,
                EtfCatalystRunCheckpoint,
                EtfCatalystEventStudyEvidence,
            ),
        ):
            raise CatalystEvidenceImmutableError(
                "catalyst registry and evidence records cannot be deleted"
            )
        if isinstance(instance, ShortResearchSignalRun) and instance.publication_state == "published":
            raise PublishedSnapshotImmutableError("published ranking snapshot is immutable")
        if isinstance(instance, ShortResearchSignalItem):
            item_run_ids.add(instance.run_id)
    if item_run_ids:
        locked_runs = session.execute(
            select(
                ShortResearchSignalRun.id,
                ShortResearchSignalRun.publication_state,
            )
            .where(ShortResearchSignalRun.id.in_(item_run_ids))
            .order_by(ShortResearchSignalRun.id.asc())
            .with_for_update()
        ).all()
        published_run_ids = {
            run_id
            for run_id, publication_state in locked_runs
            if publication_state == "published"
        }
    if sealing_run_ids & item_run_ids:
        raise PublishedSnapshotImmutableError(
            "initial publication seal cannot include item mutations"
        )
    if published_run_ids & item_run_ids:
        raise PublishedSnapshotImmutableError("published ranking snapshot items are immutable")
