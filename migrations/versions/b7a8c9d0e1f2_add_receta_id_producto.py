"""Add receta id_producto and version constraint

Revision ID: b7a8c9d0e1f2
Revises: 9f1a2b3c4d5e
Create Date: 2026-04-02 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7a8c9d0e1f2"
down_revision = "9f1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {column["name"]: column for column in inspector.get_columns("receta")}
    if "id_producto" not in columns:
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.add_column(sa.Column("id_producto", sa.Integer(), nullable=True))

    inspector = sa.inspect(bind)
    foreign_keys = {
        foreign_key.get("name")
        for foreign_key in inspector.get_foreign_keys("receta")
        if foreign_key.get("name")
    }
    if "fk_receta_id_producto" in foreign_keys:
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.drop_constraint("fk_receta_id_producto", type_="foreignkey")

    op.execute(
        sa.text(
            """
            UPDATE receta
            SET id_producto = (
                SELECT producto.id_producto
                FROM producto
                WHERE producto.id_receta = receta.id_receta
                LIMIT 1
            )
            WHERE id_producto IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE receta
            SET id_producto = (
                SELECT producto.id_producto
                FROM producto
                WHERE LOWER(producto.nombre) = LOWER(receta.nombre)
                LIMIT 1
            )
            WHERE id_producto IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            DELETE r1
            FROM receta r1
            INNER JOIN receta r2
                ON r1.id_receta > r2.id_receta
                AND r1.id_producto = r2.id_producto
                AND r1.version = r2.version
            """
        )
    )

    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("receta")}
    nullable = bool(columns.get("id_producto", {}).get("nullable", True))
    if nullable:
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.alter_column(
                "id_producto",
                existing_type=sa.Integer(),
                nullable=False,
            )

    inspector = sa.inspect(bind)
    unique_constraints = {
        unique_constraint.get("name")
        for unique_constraint in inspector.get_unique_constraints("receta")
        if unique_constraint.get("name")
    }

    with op.batch_alter_table("receta", schema=None) as batch_op:
        if "uq_receta_nombre_version" in unique_constraints:
            batch_op.drop_constraint("uq_receta_nombre_version", type_="unique")
        if "uq_receta_producto_version" not in unique_constraints:
            batch_op.create_unique_constraint(
                "uq_receta_producto_version", ["id_producto", "version"]
            )

    inspector = sa.inspect(bind)
    foreign_keys = {
        foreign_key.get("name")
        for foreign_key in inspector.get_foreign_keys("receta")
        if foreign_key.get("name")
    }
    if "fk_receta_id_producto" not in foreign_keys:
        with op.batch_alter_table("receta", schema=None) as batch_op:
            batch_op.create_foreign_key(
                "fk_receta_id_producto",
                "producto",
                ["id_producto"],
                ["id_producto"],
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("receta")}
    if "id_producto" not in columns:
        return

    unique_constraints = {
        unique_constraint.get("name")
        for unique_constraint in inspector.get_unique_constraints("receta")
        if unique_constraint.get("name")
    }
    foreign_keys = {
        foreign_key.get("name")
        for foreign_key in inspector.get_foreign_keys("receta")
        if foreign_key.get("name")
    }

    with op.batch_alter_table("receta", schema=None) as batch_op:
        if "uq_receta_producto_version" in unique_constraints:
            batch_op.drop_constraint("uq_receta_producto_version", type_="unique")
        if "uq_receta_nombre_version" not in unique_constraints:
            batch_op.create_unique_constraint(
                "uq_receta_nombre_version", ["nombre", "version"]
            )
        batch_op.alter_column(
            "id_producto",
            existing_type=sa.Integer(),
            nullable=True,
        )
        if "fk_receta_id_producto" in foreign_keys:
            batch_op.drop_constraint("fk_receta_id_producto", type_="foreignkey")
        batch_op.drop_column("id_producto")
