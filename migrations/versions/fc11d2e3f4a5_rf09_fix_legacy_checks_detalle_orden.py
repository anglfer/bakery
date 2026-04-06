"""RF09 fix legacy checks on detalle_orden_produccion

Revision ID: fc11d2e3f4a5
Revises: fb10a1b2c3d4
Create Date: 2026-04-03 00:12:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fc11d2e3f4a5"
down_revision = "fb10a1b2c3d4"
branch_labels = None
depends_on = None


TABLE_NAME = "detalle_orden_produccion"


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _check_exists(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    checks = {
        check.get("name")
        for check in inspector.get_check_constraints(table_name)
        if check.get("name")
    }
    return constraint_name in checks


def upgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    # En esquemas legacy este check apunta a `cantidad_real` y rompe inserts RF09.
    if _check_exists(TABLE_NAME, "ck_detalle_orden_cantidad_real_positiva"):
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            batch_op.drop_constraint(
                "ck_detalle_orden_cantidad_real_positiva",
                type_="check",
            )

    if not _check_exists(TABLE_NAME, "ck_detalle_orden_cantidad_real_positiva"):
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_detalle_orden_cantidad_real_positiva",
                "cantidad_real_descontada > 0",
            )

    if not _check_exists(TABLE_NAME, "ck_detalle_orden_stock_previo_no_negativo"):
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_detalle_orden_stock_previo_no_negativo",
                "stock_previo >= 0",
            )

    if not _check_exists(TABLE_NAME, "ck_detalle_orden_stock_posterior_no_negativo"):
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_detalle_orden_stock_posterior_no_negativo",
                "stock_posterior >= 0",
            )


def downgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    if _check_exists(TABLE_NAME, "ck_detalle_orden_stock_posterior_no_negativo"):
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            batch_op.drop_constraint(
                "ck_detalle_orden_stock_posterior_no_negativo",
                type_="check",
            )

    if _check_exists(TABLE_NAME, "ck_detalle_orden_stock_previo_no_negativo"):
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            batch_op.drop_constraint(
                "ck_detalle_orden_stock_previo_no_negativo",
                type_="check",
            )

    if _check_exists(TABLE_NAME, "ck_detalle_orden_cantidad_real_positiva"):
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            batch_op.drop_constraint(
                "ck_detalle_orden_cantidad_real_positiva",
                type_="check",
            )

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_detalle_orden_cantidad_real_positiva",
            "cantidad_real > 0",
        )
