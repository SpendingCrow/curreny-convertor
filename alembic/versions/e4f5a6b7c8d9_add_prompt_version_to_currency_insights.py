"""Add prompt_version to currency_insights.

Which revision of app/news/prompts.py wrote a row. Tracking only: nothing
queries or branches on it, but without it a change in output quality cannot be
attributed to the prompt change that caused it.

SMALLINT, NOT NULL, server_default 1. Existing rows were all written by the
original prompt, so 1 is the correct backfill rather than a placeholder, and
the default keeps the column safe for any INSERT that predates the code
change. The application sets it explicitly from prompts.PROMPT_VERSION.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "currency_insights",
        sa.Column(
            "prompt_version",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("currency_insights", "prompt_version")
