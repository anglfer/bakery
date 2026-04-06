"""RF08 mysql unique active recipe per product

Revision ID: d1f2e3a4b5c6
Revises: c8e1f2a3b4c5
Create Date: 2026-04-02 21:35:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d1f2e3a4b5c6"
down_revision = "c8e1f2a3b4c5"
branch_labels = None
depends_on = None


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {
        index.get("name")
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }
    return index_name in indexes


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column.get("name")
        for column in inspector.get_columns(table_name)
        if column.get("name")
    }
    return column_name in columns


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    if not _column_exists("receta", "id_producto_activa"):
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "id_producto_activa",
                    sa.Integer(),
                    sa.Computed(
                        "CASE WHEN activa = 1 THEN id_producto ELSE NULL END",
                        persisted=True,
                    ),
                    nullable=True,
                )
            )

    if not _index_exists("receta", "uq_receta_producto_activa"):
        op.create_index(
            "uq_receta_producto_activa",
            "receta",
            ["id_producto_activa"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    if _index_exists("receta", "uq_receta_producto_activa"):
        op.drop_index("uq_receta_producto_activa", table_name="receta")

    if _column_exists("receta", "id_producto_activa"):
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.drop_column("id_producto_activa")
