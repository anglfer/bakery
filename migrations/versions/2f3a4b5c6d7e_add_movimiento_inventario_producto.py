"""add movimiento inventario producto terminado

Revision ID: 2f3a4b5c6d7e
Revises: 65d138ccdfb0
Create Date: 2026-04-02 18:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2f3a4b5c6d7e"
down_revision = "65d138ccdfb0"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(idx.get("name") == index_name for idx in indexes)


def upgrade() -> None:
    table_name = "movimiento_inventario_producto"
    index_name = "ix_movimiento_producto_producto_fecha"

    if not _table_exists(table_name):
        op.create_table(
            table_name,
            sa.Column("id_movimiento", sa.Integer(), primary_key=True),
            sa.Column("fecha_creacion", sa.DateTime(), nullable=False),
            sa.Column("id_producto", sa.Integer(), nullable=False),
            sa.Column("tipo", sa.String(length=20), nullable=False),
            sa.Column("cantidad", sa.Integer(), nullable=False),
            sa.Column("stock_anterior", sa.Integer(), nullable=False),
            sa.Column("stock_posterior", sa.Integer(), nullable=False),
            sa.Column("referencia_id", sa.String(length=50), nullable=True),
            sa.Column("id_usuario", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["id_producto"], ["producto.id_producto"]),
            sa.ForeignKeyConstraint(["id_usuario"], ["usuario.id_usuario"]),
            sa.CheckConstraint(
                "cantidad > 0", name="ck_movimiento_producto_cantidad_positiva"
            ),
        )

    if _table_exists(table_name) and not _index_exists(table_name, index_name):
        op.create_index(
            index_name,
            table_name,
            ["id_producto", "fecha_creacion"],
        )


def downgrade() -> None:
    table_name = "movimiento_inventario_producto"
    index_name = "ix_movimiento_producto_producto_fecha"

    if _table_exists(table_name) and _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)

    if _table_exists(table_name):
        op.drop_table(table_name)
