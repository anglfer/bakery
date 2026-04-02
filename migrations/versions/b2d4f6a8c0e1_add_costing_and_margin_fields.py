"""Add costing, margin and sales snapshot fields

Revision ID: b2d4f6a8c0e1
Revises: a1c2d3e4f5a6
Create Date: 2026-04-01 15:20:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2d4f6a8c0e1"
down_revision = "a1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "costo_produccion_actual",
                sa.Numeric(precision=12, scale=2),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "margen_objetivo_pct",
                sa.Numeric(precision=5, scale=2),
                nullable=False,
                server_default="25",
            )
        )
        batch_op.add_column(
            sa.Column(
                "precio_sugerido",
                sa.Numeric(precision=12, scale=2),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("fecha_costo_actualizado", sa.DateTime(), nullable=True)
        )

    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.alter_column(
            "costo_produccion_actual",
            existing_type=sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "margen_objetivo_pct",
            existing_type=sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default=None,
        )
        batch_op.create_check_constraint(
            "ck_producto_costo_produccion_no_negativo",
            "costo_produccion_actual >= 0",
        )
        batch_op.create_check_constraint(
            "ck_producto_margen_objetivo_rango",
            "margen_objetivo_pct > 0 AND margen_objetivo_pct < 100",
        )
        batch_op.create_check_constraint(
            "ck_producto_precio_sugerido_no_negativo",
            "precio_sugerido IS NULL OR precio_sugerido >= 0",
        )

    with op.batch_alter_table("detalle_venta", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "costo_unitario_produccion",
                sa.Numeric(precision=12, scale=2),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "utilidad_unitaria",
                sa.Numeric(precision=12, scale=2),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("detalle_venta", schema=None) as batch_op:
        batch_op.drop_column("utilidad_unitaria")
        batch_op.drop_column("costo_unitario_produccion")

    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_producto_precio_sugerido_no_negativo",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_producto_margen_objetivo_rango",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_producto_costo_produccion_no_negativo",
            type_="check",
        )
        batch_op.drop_column("fecha_costo_actualizado")
        batch_op.drop_column("precio_sugerido")
        batch_op.drop_column("margen_objetivo_pct")
        batch_op.drop_column("costo_produccion_actual")
