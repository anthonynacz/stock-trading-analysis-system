"""baseline_schema — represents the entire schema as of Alembic adoption.

Revision ID: 5de04647a468
Revises:
Create Date: 2026-05-09

This baseline delegates to `Base.metadata.create_all` / `drop_all` rather
than enumerating every CREATE TABLE statement. Two reasons:

1. Existing DBs (local + Hetzner) are already at this state — they'll be
   stamped to this revision via `alembic stamp head` and never actually
   run upgrade(). The body matters only for fresh-DB creation in CI / new
   environments.
2. Maintaining a hand-written ~500-line baseline as the source of truth
   would diverge from `db/models.py`. With this delegation, models stay
   the source of truth for schema; future migrations are normal autogen
   files that capture diffs from this baseline.

Subsequent migrations (e.g. the planned earnings_calendar fiscal_quarter
unique-key change) are normal autogen revisions and DO contain explicit
op.create_table / op.alter_column calls.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa  # noqa: F401  (imported for symmetry with autogen template)


# revision identifiers, used by Alembic.
revision: str = '5de04647a468'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the entire schema from db.models.Base.metadata.

    Idempotent — Base.metadata.create_all only creates tables that don't
    exist, so running this on a populated DB is a no-op (which is what
    `alembic stamp head` already enforces by skipping upgrade calls
    before the stamped revision).
    """
    from db.models import Base
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """Drop the entire schema. Used by `alembic downgrade base` for full
    reset in CI; never on production. The reverse operation of upgrade()."""
    from db.models import Base
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
