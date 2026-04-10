"""RF15 link solicitud_produccion to pedido

Revision ID: b9c1d2e3f4a5
Revises: a6b7c8d9e0f1
Create Date: 2026-04-10 14:20:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b9c1d2e3f4a5"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None

TABLE_NAME = "solicitud_produccion"
COLUMN_NAME = "id_pedido"
FK_NAME = "fk_solicitud_produccion_pedido"


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


def _fk_exists(table_name: str, fk_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    foreign_keys = {
        foreign_key.get("name")
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key.get("name")
    }
    return fk_name in foreign_keys


def upgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    if not _column_exists(TABLE_NAME, COLUMN_NAME):
        op.add_column(
            TABLE_NAME,
            sa.Column(COLUMN_NAME, sa.Integer(), nullable=True),
        )

    if _column_exists(TABLE_NAME, COLUMN_NAME) and not _fk_exists(TABLE_NAME, FK_NAME):
        op.create_foreign_key(
            FK_NAME,
            TABLE_NAME,
            "pedido",
            [COLUMN_NAME],
            ["id_pedido"],
        )


def downgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    if _fk_exists(TABLE_NAME, FK_NAME):
        op.drop_constraint(FK_NAME, TABLE_NAME, type_="foreignkey")

    if _column_exists(TABLE_NAME, COLUMN_NAME):
        op.drop_column(TABLE_NAME, COLUMN_NAME)
