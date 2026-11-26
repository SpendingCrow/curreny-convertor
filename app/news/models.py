from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class CurrencyInsight(Base):
    """One generated news card: what an asset did against USD, and why.

    `change_percent` is stored already rounded to 2 decimals because that is the
    figure we display; the full-precision maths happens in
    `app/utils/performance.py` and is only rounded on the way in here.
    """

    __tablename__ = "currency_insights"

    id = Column(Integer, primary_key=True, index=True)

    currency_code = Column(String(10), nullable=False, index=True)
    insight_date = Column(Date, nullable=False, index=True)

    # Null when we have no reliable data, and always null for USD (the base).
    change_percent = Column(Numeric(10, 2), nullable=True)

    # Card fields
    title = Column(Text, nullable=False)
    category = Column(String(50), nullable=False)
    sentiment = Column(String(20), nullable=False)

    short_summary = Column(Text, nullable=False)
    long_summary = Column(Text, nullable=False)

    # Which revision of app/news/prompts.py wrote this row. For our own
    # tracking only: no query filters on it and no code branches on it, but it
    # is what lets a change in output quality be traced to a prompt change.
    # SMALLINT because a version number will never need more than three
    # digits. Rows that predate the column read as 1.
    prompt_version = Column(
        SmallInteger, nullable=False, server_default="1", default=1
    )

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sources = relationship(
        "NewsSource",
        back_populates="insight",
        cascade="all, delete-orphan",
        passive_deletes=True,
        # The summaries cite sources as [1], [2], so display order has to be
        # the model's own numbering, not the publication date.
        order_by="NewsSource.source_number.asc(), NewsSource.id.asc()",
        lazy="selectin",
    )

    __table_args__ = (
        # One insight per currency per day — makes the daily job safe to re-run.
        UniqueConstraint("currency_code", "insight_date", name="uq_insight_currency_date"),
        Index("ix_insight_category_date", "category", "insight_date"),
    )


class NewsSource(Base):
    """A real article backing an insight — the "Reuters +1" line under a card.

    We store the link and its metadata only, never the publisher's article body.
    """

    __tablename__ = "news_sources"

    id = Column(Integer, primary_key=True, index=True)
    insight_id = Column(
        Integer,
        ForeignKey("currency_insights.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 1-based, matching the [n] markers in the insight's summaries. Nullable
    # for rows written before the markers existed.
    source_number = Column(Integer, nullable=True)

    headline = Column(Text, nullable=False)
    publisher = Column(String(255), nullable=True)
    source_url = Column(Text, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)

    insight = relationship("CurrencyInsight", back_populates="sources")
