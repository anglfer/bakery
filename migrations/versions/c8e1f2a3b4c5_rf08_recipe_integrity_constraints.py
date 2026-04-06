"""RF08 recipe integrity constraints

Revision ID: c8e1f2a3b4c5
Revises: 65d138ccdfb0
Create Date: 2026-04-02 21:05:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c8e1f2a3b4c5"
down_revision = "65d138ccdfb0"
branch_labels = None
depends_on = None


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = {
        check.get("name")
        for check in inspector.get_check_constraints(table_name)
        if check.get("name")
    }
    return constraint_name in constraints


def _unique_constraint_exists(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = {
        unique.get("name")
        for unique in inspector.get_unique_constraints(table_name)
        if unique.get("name")
    }
    return constraint_name in constraints


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
    dialect = bind.dialect.name

    duplicated_rows = bind.execute(
        sa.text(
            """
            SELECT
                id_receta,
                id_materia_prima,
                SUM(cantidad_base) AS cantidad_total,
                MIN(id_detalle) AS keep_id
            FROM detalle_receta
            GROUP BY id_receta, id_materia_prima
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    for row in duplicated_rows:
        bind.execute(
            sa.text(
                """
                UPDATE detalle_receta
                SET cantidad_base = :cantidad_total
                WHERE id_detalle = :keep_id
                """
            ),
            {
                "cantidad_total": row.cantidad_total,
                "keep_id": row.keep_id,
            },
        )
        bind.execute(
            sa.text(
                """
                DELETE FROM detalle_receta
                WHERE id_receta = :id_receta
                  AND id_materia_prima = :id_materia
                  AND id_detalle <> :keep_id
                """
            ),
            {
                "id_receta": row.id_receta,
                "id_materia": row.id_materia_prima,
                "keep_id": row.keep_id,
            },
        )

    if not _constraint_exists("receta", "ck_receta_version_positiva"):
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_receta_version_positiva",
                "version > 0",
            )

    if not _constraint_exists("receta", "ck_receta_rendimiento_positivo"):
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_receta_rendimiento_positivo",
                "rendimiento_base > 0",
            )

    if not _unique_constraint_exists("detalle_receta", "uq_detalle_receta_materia"):
        with op.batch_alter_table("detalle_receta", schema=None) as batch_op:
            batch_op.create_unique_constraint(
                "uq_detalle_receta_materia",
                ["id_receta", "id_materia_prima"],
            )

    if not _index_exists("receta", "uq_receta_producto_activa"):
        if dialect == "sqlite":
            op.execute(
                """
                CREATE UNIQUE INDEX uq_receta_producto_activa
                ON receta (id_producto)
                WHERE activa = 1
                """
            )
        elif dialect == "postgresql":
            op.execute(
                """
                CREATE UNIQUE INDEX uq_receta_producto_activa
                ON receta (id_producto)
                WHERE activa IS TRUE
                """
            )
        elif dialect == "mysql":
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
            op.create_index(
                "uq_receta_producto_activa",
                "receta",
                ["id_producto_activa"],
                unique=True,
            )


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if _index_exists("receta", "uq_receta_producto_activa"):
        if dialect == "sqlite":
            op.execute("DROP INDEX IF EXISTS uq_receta_producto_activa")
        elif dialect == "postgresql":
            op.execute("DROP INDEX IF EXISTS uq_receta_producto_activa")
        elif dialect == "mysql":
            op.drop_index("uq_receta_producto_activa", table_name="receta")

    if dialect == "mysql" and _column_exists("receta", "id_producto_activa"):
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.drop_column("id_producto_activa")

    if _unique_constraint_exists("detalle_receta", "uq_detalle_receta_materia"):
        with op.batch_alter_table("detalle_receta", schema=None) as batch_op:
            batch_op.drop_constraint("uq_detalle_receta_materia", type_="unique")

    if _constraint_exists("receta", "ck_receta_rendimiento_positivo"):
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.drop_constraint("ck_receta_rendimiento_positivo", type_="check")

    if _constraint_exists("receta", "ck_receta_version_positiva"):
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.drop_constraint("ck_receta_version_positiva", type_="check")
