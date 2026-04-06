"""RF09 production orders traceability

Revision ID: fa09c0d1e2f3
Revises: d1f2e3a4b5c6
Create Date: 2026-04-02 23:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fa09c0d1e2f3"
down_revision = "d1f2e3a4b5c6"
branch_labels = None
depends_on = None


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


def _column_nullable(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for column in inspector.get_columns(table_name):
        if column.get("name") == column_name:
            return bool(column.get("nullable"))
    return True


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {
        index.get("name")
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }
    return index_name in indexes


def upgrade():
    if not _column_exists("orden_produccion", "observaciones"):
        with op.batch_alter_table("orden_produccion", schema=None) as batch_op:
            batch_op.add_column(sa.Column("observaciones", sa.String(length=255)))

    if _column_exists("orden_produccion", "id_solicitud") and not _column_nullable(
        "orden_produccion", "id_solicitud"
    ):
        with op.batch_alter_table("orden_produccion", schema=None) as batch_op:
            batch_op.alter_column(
                "id_solicitud",
                existing_type=sa.Integer(),
                nullable=True,
            )

    if not _table_exists("detalle_orden_produccion"):
        op.create_table(
            "detalle_orden_produccion",
            sa.Column("id_detalle", sa.Integer(), nullable=False),
            sa.Column("id_orden", sa.Integer(), nullable=False),
            sa.Column("id_materia_prima", sa.Integer(), nullable=False),
            sa.Column("cantidad_receta", sa.Numeric(12, 4), nullable=False),
            sa.Column("cantidad_necesaria", sa.Numeric(12, 4), nullable=False),
            sa.Column("porcentaje_merma", sa.Numeric(5, 2), nullable=False),
            sa.Column("cantidad_real_descontada", sa.Numeric(12, 4), nullable=False),
            sa.Column("stock_previo", sa.Numeric(12, 4), nullable=False),
            sa.Column("stock_posterior", sa.Numeric(12, 4), nullable=False),
            sa.ForeignKeyConstraint(["id_materia_prima"], ["materia_prima.id_materia"]),
            sa.ForeignKeyConstraint(["id_orden"], ["orden_produccion.id_orden"]),
            sa.PrimaryKeyConstraint("id_detalle"),
            sa.UniqueConstraint(
                "id_orden",
                "id_materia_prima",
                name="uq_detalle_orden_materia",
            ),
            sa.CheckConstraint(
                "cantidad_receta > 0",
                name="ck_detalle_orden_cantidad_receta_positiva",
            ),
            sa.CheckConstraint(
                "cantidad_necesaria > 0",
                name="ck_detalle_orden_cantidad_necesaria_positiva",
            ),
            sa.CheckConstraint(
                "porcentaje_merma >= 0",
                name="ck_detalle_orden_merma_no_negativa",
            ),
            sa.CheckConstraint(
                "cantidad_real_descontada > 0",
                name="ck_detalle_orden_cantidad_real_positiva",
            ),
            sa.CheckConstraint(
                "stock_previo >= 0",
                name="ck_detalle_orden_stock_previo_no_negativo",
            ),
            sa.CheckConstraint(
                "stock_posterior >= 0",
                name="ck_detalle_orden_stock_posterior_no_negativo",
            ),
        )

    if not _index_exists("detalle_orden_produccion", "ix_detalle_orden_id_orden"):
        op.create_index(
            "ix_detalle_orden_id_orden",
            "detalle_orden_produccion",
            ["id_orden"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()

    if _index_exists("detalle_orden_produccion", "ix_detalle_orden_id_orden"):
        op.drop_index(
            "ix_detalle_orden_id_orden", table_name="detalle_orden_produccion"
        )

    if _table_exists("detalle_orden_produccion"):
        op.drop_table("detalle_orden_produccion")

    if _column_exists("orden_produccion", "observaciones"):
        with op.batch_alter_table("orden_produccion", schema=None) as batch_op:
            batch_op.drop_column("observaciones")

    if _column_exists("orden_produccion", "id_solicitud") and _column_nullable(
        "orden_produccion",
        "id_solicitud",
    ):
        null_rows = bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM orden_produccion
                WHERE id_solicitud IS NULL
                """
            )
        ).scalar()
        if null_rows:
            fallback_id = bind.execute(
                sa.text("SELECT MIN(id_solicitud) FROM solicitud_produccion")
            ).scalar()
            if fallback_id is not None:
                bind.execute(
                    sa.text(
                        """
                        UPDATE orden_produccion
                        SET id_solicitud = :fallback_id
                        WHERE id_solicitud IS NULL
                        """
                    ),
                    {"fallback_id": int(fallback_id)},
                )
            else:
                bind.execute(
                    sa.text(
                        """
                        DELETE FROM orden_produccion
                        WHERE id_solicitud IS NULL
                        """
                    )
                )

        with op.batch_alter_table("orden_produccion", schema=None) as batch_op:
            batch_op.alter_column(
                "id_solicitud",
                existing_type=sa.Integer(),
                nullable=False,
            )
