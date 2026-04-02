"""Add business check constraints for core inventory/production tables

Revision ID: e4f7a1b2c3d4
Revises: 9f1a2b3c4d5e
Create Date: 2026-03-29 12:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e4f7a1b2c3d4"
down_revision = "9f1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("materia_prima", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_mp_factor_conversion_positiva", "factor_conversion > 0"
        )
        batch_op.create_check_constraint(
            "ck_mp_porcentaje_merma_no_negativo", "porcentaje_merma >= 0"
        )
        batch_op.create_check_constraint(
            "ck_mp_stock_minimo_no_negativo", "stock_minimo >= 0"
        )

    with op.batch_alter_table("detalle_compra", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_detalle_compra_cantidad_positiva", "cantidad_comprada > 0"
        )
        batch_op.create_check_constraint(
            "ck_detalle_compra_precio_no_negativo", "precio_unitario >= 0"
        )
        batch_op.create_check_constraint(
            "ck_detalle_compra_cantidad_base_positiva", "cantidad_base > 0"
        )

    with op.batch_alter_table("detalle_receta", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_detalle_receta_cantidad_positiva", "cantidad_base > 0"
        )

    with op.batch_alter_table("solicitud_produccion", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_solicitud_cantidad_positiva", "cantidad > 0"
        )

    with op.batch_alter_table("orden_produccion", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_orden_cantidad_positiva", "cantidad_producir > 0"
        )


def downgrade():
    with op.batch_alter_table("orden_produccion", schema=None) as batch_op:
        batch_op.drop_constraint("ck_orden_cantidad_positiva", type_="check")

    with op.batch_alter_table("solicitud_produccion", schema=None) as batch_op:
        batch_op.drop_constraint("ck_solicitud_cantidad_positiva", type_="check")

    with op.batch_alter_table("detalle_receta", schema=None) as batch_op:
        batch_op.drop_constraint("ck_detalle_receta_cantidad_positiva", type_="check")

    with op.batch_alter_table("detalle_compra", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_detalle_compra_cantidad_base_positiva", type_="check"
        )
        batch_op.drop_constraint("ck_detalle_compra_precio_no_negativo", type_="check")
        batch_op.drop_constraint("ck_detalle_compra_cantidad_positiva", type_="check")

    with op.batch_alter_table("materia_prima", schema=None) as batch_op:
        batch_op.drop_constraint("ck_mp_stock_minimo_no_negativo", type_="check")
        batch_op.drop_constraint("ck_mp_porcentaje_merma_no_negativo", type_="check")
        batch_op.drop_constraint("ck_mp_factor_conversion_positiva", type_="check")
