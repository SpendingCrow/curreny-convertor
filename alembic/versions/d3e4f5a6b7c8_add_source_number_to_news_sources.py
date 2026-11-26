"""Add source_number to news_sources.

The insight summaries now carry [1]-style citation markers, so a source's
position is part of the content: [1] has to be the first source the card
lists. Ordering by publication date — what the relationship did before — would
renumber them silently.

Nullable on purpose. Rows written before the markers existed have no number to
backfill, and inventing one would claim a link to text that never cited it.
Those rows fall back to insertion order (`id`), which is what the old
date-ordered output effectively was.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_sources", sa.Column("source_number", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("news_sources", "source_number")
