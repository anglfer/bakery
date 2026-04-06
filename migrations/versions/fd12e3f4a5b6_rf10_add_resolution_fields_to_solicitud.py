"""RF10 add resolution fields to solicitud_produccion

Revision ID: fd12e3f4a5b6
Revises: fc11d2e3f4a5
Create Date: 2026-04-03 01:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fd12e3f4a5b6"
down_revision = "fc11d2e3f4a5"
branch_labels = None
depends_on = None


TABLE_NAME = "solicitud_produccion"


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column.get("name")
        for column in inspector.get_columns(table_name)
        if column.get("name")
    }
    return column_name in columns


def upgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        if not _column_exists(TABLE_NAME, "fecha_resolucion"):
            batch_op.add_column(
                sa.Column("fecha_resolucion", sa.DateTime(), nullable=True)
            )
        if not _column_exists(TABLE_NAME, "observaciones_resolucion"):
            batch_op.add_column(
                sa.Column(
                    "observaciones_resolucion", sa.String(length=255), nullable=True
                )
            )


def downgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        if _column_exists(TABLE_NAME, "observaciones_resolucion"):
            batch_op.drop_column("observaciones_resolucion")
        if _column_exists(TABLE_NAME, "fecha_resolucion"):
            batch_op.drop_column("fecha_resolucion")
