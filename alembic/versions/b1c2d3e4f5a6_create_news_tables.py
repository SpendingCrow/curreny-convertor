"""create currency_insights and news_sources tables

Adds the two news tables. Nothing existing is altered — `currency_rates` and its
USD-based storage are untouched.

Revision ID: b1c2d3e4f5a6
Revises: ee74055fcc61
Create Date: 2026-11-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "ee74055fcc61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "currency_insights",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("currency_code", sa.String(length=10), nullable=False),
        sa.Column("insight_date", sa.Date(), nullable=False),
        # Null for USD (the base) and whenever we lack reliable rate data.
        sa.Column("change_percent", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("sentiment", sa.String(length=20), nullable=False),
        sa.Column("related_assets", sa.String(length=255), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("short_summary", sa.Text(), nullable=False),
        sa.Column("long_summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        sa.UniqueConstraint(
            "currency_code", "insight_date", name="uq_insight_currency_date"
        ),
    )
    op.create_index(
        op.f("ix_currency_insights_id"), "currency_insights", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_currency_insights_currency_code"),
        "currency_insights",
        ["currency_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_currency_insights_insight_date"),
        "currency_insights",
        ["insight_date"],
        unique=False,
    )
    # Serves the grouped feed query.
    op.create_index(
        "ix_insight_category_date",
        "currency_insights",
        ["category", "insight_date"],
        unique=False,
    )

    op.create_table(
        "news_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("insight_id", sa.Integer(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["insight_id"], ["currency_insights.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_news_sources_id"), "news_sources", ["id"], unique=False)
    op.create_index(
        op.f("ix_news_sources_insight_id"), "news_sources", ["insight_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_news_sources_insight_id"), table_name="news_sources")
    op.drop_index(op.f("ix_news_sources_id"), table_name="news_sources")
    op.drop_table("news_sources")

    op.drop_index("ix_insight_category_date", table_name="currency_insights")
    op.drop_index(
        op.f("ix_currency_insights_insight_date"), table_name="currency_insights"
    )
    op.drop_index(
        op.f("ix_currency_insights_currency_code"), table_name="currency_insights"
    )
    op.drop_index(op.f("ix_currency_insights_id"), table_name="currency_insights")
    op.drop_table("currency_insights")
