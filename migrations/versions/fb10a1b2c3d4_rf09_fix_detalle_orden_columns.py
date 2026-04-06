"""RF09 fix detalle_orden_produccion missing columns

Revision ID: fb10a1b2c3d4
Revises: fa09c0d1e2f3
Create Date: 2026-04-02 23:59:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fb10a1b2c3d4"
down_revision = "fa09c0d1e2f3"
branch_labels = None
depends_on = None


TABLE_NAME = "detalle_orden_produccion"


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


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {
        index.get("name")
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }
    return index_name in indexes


def upgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    missing_columns = [
        column_name
        for column_name in (
            "cantidad_real_descontada",
            "stock_previo",
            "stock_posterior",
        )
        if not _column_exists(TABLE_NAME, column_name)
    ]

    if missing_columns:
        with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
            if "cantidad_real_descontada" in missing_columns:
                batch_op.add_column(
                    sa.Column(
                        "cantidad_real_descontada",
                        sa.Numeric(12, 4),
                        nullable=True,
                    )
                )
            if "stock_previo" in missing_columns:
                batch_op.add_column(
                    sa.Column(
                        "stock_previo",
                        sa.Numeric(12, 4),
                        nullable=True,
                    )
                )
            if "stock_posterior" in missing_columns:
                batch_op.add_column(
                    sa.Column(
                        "stock_posterior",
                        sa.Numeric(12, 4),
                        nullable=True,
                    )
                )

    bind = op.get_bind()

    if _column_exists(TABLE_NAME, "cantidad_real_descontada"):
        if _column_exists(TABLE_NAME, "cantidad_real"):
            bind.execute(
                sa.text(
                    """
                    UPDATE detalle_orden_produccion
                    SET cantidad_real_descontada = COALESCE(cantidad_real_descontada, cantidad_real, 0)
                    """
                )
            )
        else:
            bind.execute(
                sa.text(
                    """
                    UPDATE detalle_orden_produccion
                    SET cantidad_real_descontada = COALESCE(cantidad_real_descontada, 0)
                    """
                )
            )

    if _column_exists(TABLE_NAME, "stock_previo"):
        bind.execute(
            sa.text(
                """
                UPDATE detalle_orden_produccion
                SET stock_previo = COALESCE(stock_previo, 0)
                """
            )
        )

    if _column_exists(TABLE_NAME, "stock_posterior"):
        bind.execute(
            sa.text(
                """
                UPDATE detalle_orden_produccion
                SET stock_posterior = COALESCE(stock_posterior, 0)
                """
            )
        )

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        if _column_exists(TABLE_NAME, "cantidad_real_descontada"):
            batch_op.alter_column(
                "cantidad_real_descontada",
                existing_type=sa.Numeric(12, 4),
                nullable=False,
            )
        if _column_exists(TABLE_NAME, "stock_previo"):
            batch_op.alter_column(
                "stock_previo",
                existing_type=sa.Numeric(12, 4),
                nullable=False,
            )
        if _column_exists(TABLE_NAME, "stock_posterior"):
            batch_op.alter_column(
                "stock_posterior",
                existing_type=sa.Numeric(12, 4),
                nullable=False,
            )

    if not _index_exists(TABLE_NAME, "ix_detalle_orden_id_orden"):
        op.create_index(
            "ix_detalle_orden_id_orden",
            TABLE_NAME,
            ["id_orden"],
            unique=False,
        )


def downgrade() -> None:
    if not _table_exists(TABLE_NAME):
        return

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        if _column_exists(TABLE_NAME, "stock_posterior"):
            batch_op.drop_column("stock_posterior")
        if _column_exists(TABLE_NAME, "stock_previo"):
            batch_op.drop_column("stock_previo")
        if _column_exists(TABLE_NAME, "cantidad_real_descontada"):
            batch_op.drop_column("cantidad_real_descontada")
