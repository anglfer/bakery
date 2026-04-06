"""Merge RF13 head with existing movement head

Revision ID: ff14a5b6c7d8
Revises: 2f3a4b5c6d7e, fe13f4a5b6c7
Create Date: 2026-04-02 19:05:00.000000

"""

# revision identifiers, used by Alembic.
revision = "ff14a5b6c7d8"
down_revision = ("2f3a4b5c6d7e", "fe13f4a5b6c7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge revision without DDL changes.
    pass


def downgrade() -> None:
    # Merge revision without reversible DDL changes.
    pass
