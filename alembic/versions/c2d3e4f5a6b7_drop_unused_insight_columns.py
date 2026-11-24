"""Drop unused columns from currency_insights.

Three columns were carried in the original news table and never earned their
place:

  image_url       always NULL — nothing ever wrote to it.
  slug            a URL-safe card id the frontend does not route on; a card is
                  addressed by currency_code + insight_date, which is already
                  unique by constraint.
  related_assets  a comma-joined code list that duplicated information already
                  visible in the summary text.

Dropping `slug` also drops the unique constraint that backed it.

This is a destructive, forward-only change: the data in these columns is gone
once it runs. The downgrade recreates the columns so the schema can be rolled
back, but it cannot bring their contents back — and `slug` returns as NULLable
without its unique constraint, because there is no way to regenerate values
that would satisfy it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every statement is IF EXISTS on purpose.
    #
    # Two reasons. First, downgrade() cannot restore the unique constraint on
    # `slug` (see below), so a downgrade/upgrade round trip would otherwise
    # fail on a constraint that is no longer there. Second, we do not know that
    # every deployed database was built by this exact migration chain, and a
    # DROP is the one operation where "it is already gone" is success, not an
    # error worth aborting a deploy over.
    #
    # The constraint goes before the column: Postgres refuses to drop a column
    # that still has one attached.
    op.execute(
        "ALTER TABLE currency_insights "
        "DROP CONSTRAINT IF EXISTS currency_insights_slug_key"
    )
    op.execute("ALTER TABLE currency_insights DROP COLUMN IF EXISTS slug")
    op.execute("ALTER TABLE currency_insights DROP COLUMN IF EXISTS related_assets")
    op.execute("ALTER TABLE currency_insights DROP COLUMN IF EXISTS image_url")


def downgrade() -> None:
    op.add_column(
        "currency_insights", sa.Column("image_url", sa.Text(), nullable=True)
    )
    op.add_column(
        "currency_insights",
        sa.Column("related_assets", sa.String(length=255), nullable=True),
    )
    # Nullable and un-constrained on the way back: the original column was NOT
    # NULL and UNIQUE, which cannot be satisfied for rows that already exist.
    op.add_column(
        "currency_insights", sa.Column("slug", sa.String(length=255), nullable=True)
    )
