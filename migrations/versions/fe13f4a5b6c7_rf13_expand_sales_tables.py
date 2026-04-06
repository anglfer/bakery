"""RF13 expand sales tables for POS, cash outflow and daily close

Revision ID: fe13f4a5b6c7
Revises: fd12e3f4a5b6
Create Date: 2026-04-02 18:45:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fe13f4a5b6c7"
down_revision = "fd12e3f4a5b6"
branch_labels = None
depends_on = None


TABLE_CORTE = "corte_diario"
TABLE_SALIDA = "salida_efectivo"
TABLE_TICKET = "ticket_venta"


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
    if _table_exists(TABLE_CORTE):
        with op.batch_alter_table(TABLE_CORTE, schema=None) as batch_op:
            if not _column_exists(TABLE_CORTE, "total_transacciones"):
                batch_op.add_column(
                    sa.Column(
                        "total_transacciones",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    )
                )
            if not _column_exists(TABLE_CORTE, "total_efectivo"):
                batch_op.add_column(
                    sa.Column(
                        "total_efectivo",
                        sa.Numeric(12, 2),
                        nullable=False,
                        server_default="0",
                    )
                )
            if not _column_exists(TABLE_CORTE, "total_tarjeta"):
                batch_op.add_column(
                    sa.Column(
                        "total_tarjeta",
                        sa.Numeric(12, 2),
                        nullable=False,
                        server_default="0",
                    )
                )
            if not _column_exists(TABLE_CORTE, "total_salidas"):
                batch_op.add_column(
                    sa.Column(
                        "total_salidas",
                        sa.Numeric(12, 2),
                        nullable=False,
                        server_default="0",
                    )
                )
            if not _column_exists(TABLE_CORTE, "costo_produccion"):
                batch_op.add_column(
                    sa.Column(
                        "costo_produccion",
                        sa.Numeric(12, 2),
                        nullable=False,
                        server_default="0",
                    )
                )

        with op.batch_alter_table(TABLE_CORTE, schema=None) as batch_op:
            if _column_exists(TABLE_CORTE, "total_transacciones"):
                batch_op.alter_column(
                    "total_transacciones",
                    existing_type=sa.Integer(),
                    nullable=False,
                    server_default=None,
                )
            if _column_exists(TABLE_CORTE, "total_efectivo"):
                batch_op.alter_column(
                    "total_efectivo",
                    existing_type=sa.Numeric(12, 2),
                    nullable=False,
                    server_default=None,
                )
            if _column_exists(TABLE_CORTE, "total_tarjeta"):
                batch_op.alter_column(
                    "total_tarjeta",
                    existing_type=sa.Numeric(12, 2),
                    nullable=False,
                    server_default=None,
                )
            if _column_exists(TABLE_CORTE, "total_salidas"):
                batch_op.alter_column(
                    "total_salidas",
                    existing_type=sa.Numeric(12, 2),
                    nullable=False,
                    server_default=None,
                )
            if _column_exists(TABLE_CORTE, "costo_produccion"):
                batch_op.alter_column(
                    "costo_produccion",
                    existing_type=sa.Numeric(12, 2),
                    nullable=False,
                    server_default=None,
                )

    if _table_exists(TABLE_SALIDA):
        with op.batch_alter_table(TABLE_SALIDA, schema=None) as batch_op:
            if not _column_exists(TABLE_SALIDA, "referencia_tipo"):
                batch_op.add_column(
                    sa.Column("referencia_tipo", sa.String(length=50), nullable=True)
                )
            if not _column_exists(TABLE_SALIDA, "referencia_id"):
                batch_op.add_column(
                    sa.Column("referencia_id", sa.Integer(), nullable=True)
                )

    if _table_exists(TABLE_TICKET):
        with op.batch_alter_table(TABLE_TICKET, schema=None) as batch_op:
            if not _column_exists(TABLE_TICKET, "nombre_negocio"):
                batch_op.add_column(
                    sa.Column(
                        "nombre_negocio",
                        sa.String(length=120),
                        nullable=False,
                        server_default="SoftBakery",
                    )
                )

        op.execute(
            sa.text(
                """
                UPDATE ticket_venta
                SET nombre_negocio = 'SoftBakery'
                WHERE nombre_negocio IS NULL OR nombre_negocio = ''
                """
            )
        )

        with op.batch_alter_table(TABLE_TICKET, schema=None) as batch_op:
            if _column_exists(TABLE_TICKET, "nombre_negocio"):
                batch_op.alter_column(
                    "nombre_negocio",
                    existing_type=sa.String(length=120),
                    nullable=False,
                    server_default=None,
                )


def downgrade() -> None:
    if _table_exists(TABLE_TICKET):
        with op.batch_alter_table(TABLE_TICKET, schema=None) as batch_op:
            if _column_exists(TABLE_TICKET, "nombre_negocio"):
                batch_op.drop_column("nombre_negocio")

    if _table_exists(TABLE_SALIDA):
        with op.batch_alter_table(TABLE_SALIDA, schema=None) as batch_op:
            if _column_exists(TABLE_SALIDA, "referencia_id"):
                batch_op.drop_column("referencia_id")
            if _column_exists(TABLE_SALIDA, "referencia_tipo"):
                batch_op.drop_column("referencia_tipo")

    if _table_exists(TABLE_CORTE):
        with op.batch_alter_table(TABLE_CORTE, schema=None) as batch_op:
            if _column_exists(TABLE_CORTE, "costo_produccion"):
                batch_op.drop_column("costo_produccion")
            if _column_exists(TABLE_CORTE, "total_salidas"):
                batch_op.drop_column("total_salidas")
            if _column_exists(TABLE_CORTE, "total_tarjeta"):
                batch_op.drop_column("total_tarjeta")
            if _column_exists(TABLE_CORTE, "total_efectivo"):
                batch_op.drop_column("total_efectivo")
            if _column_exists(TABLE_CORTE, "total_transacciones"):
                batch_op.drop_column("total_transacciones")
