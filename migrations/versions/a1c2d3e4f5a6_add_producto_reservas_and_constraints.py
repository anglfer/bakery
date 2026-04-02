"""Add reserved stock column and product stock constraints

Revision ID: a1c2d3e4f5a6
Revises: e4f7a1b2c3d4
Create Date: 2026-03-29 13:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1c2d3e4f5a6"
down_revision = "e4f7a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "cantidad_reservada",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    op.execute(
        sa.text(
            """
            UPDATE producto
            SET cantidad_reservada = 0
            WHERE cantidad_reservada IS NULL
            """
        )
    )

    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.alter_column(
            "cantidad_reservada",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=None,
        )
        batch_op.create_check_constraint(
            "ck_producto_stock_no_negativo",
            "cantidad_disponible >= 0",
        )
        batch_op.create_check_constraint(
            "ck_producto_stock_minimo_no_negativo",
            "stock_minimo >= 0",
        )
        batch_op.create_check_constraint(
            "ck_producto_reserva_no_negativa",
            "cantidad_reservada >= 0",
        )
        batch_op.create_check_constraint(
            "ck_producto_reserva_no_supera_stock",
            "cantidad_reservada <= cantidad_disponible",
        )


def downgrade():
    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_producto_reserva_no_supera_stock",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_producto_reserva_no_negativa",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_producto_stock_minimo_no_negativo",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_producto_stock_no_negativo",
            type_="check",
        )
        batch_op.drop_column("cantidad_reservada")
