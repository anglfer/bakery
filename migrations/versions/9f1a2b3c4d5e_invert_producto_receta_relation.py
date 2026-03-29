"""Invert producto-receta relation and enrich receta metadata

Revision ID: 9f1a2b3c4d5e
Revises: d8c99c83efae
Create Date: 2026-03-26 13:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "9f1a2b3c4d5e"
down_revision = "d8c99c83efae"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.add_column(sa.Column("id_receta", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_producto_id_receta",
            "receta",
            ["id_receta"],
            ["id_receta"],
        )

    with op.batch_alter_table("receta", schema=None) as batch_op:
        batch_op.add_column(sa.Column("nombre", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column("descripcion", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "unidad_produccion",
                sa.String(length=50),
                server_default="pieza",
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("categoria", sa.String(length=50), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE receta
            SET nombre = (
                SELECT producto.nombre
                FROM producto
                WHERE producto.id_producto = receta.id_producto
            )
            WHERE nombre IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE receta
            SET nombre = 'Receta ' || id_receta
            WHERE nombre IS NULL OR TRIM(nombre) = ''
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE producto
            SET id_receta = (
                SELECT r.id_receta
                FROM receta r
                WHERE r.id_producto = producto.id_producto
                  AND r.activa = 1
                ORDER BY r.version DESC
                LIMIT 1
            )
            WHERE id_receta IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE producto
            SET id_receta = (
                SELECT r.id_receta
                FROM receta r
                WHERE r.id_producto = producto.id_producto
                ORDER BY r.version DESC
                LIMIT 1
            )
            WHERE id_receta IS NULL
            """
        )
    )

    with op.batch_alter_table("receta", schema=None) as batch_op:
        batch_op.alter_column(
            "nombre", existing_type=sa.String(length=120), nullable=False
        )
        batch_op.alter_column(
            "unidad_produccion",
            existing_type=sa.String(length=50),
            nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "rendimiento_base",
            existing_type=sa.Integer(),
            type_=sa.Numeric(precision=12, scale=4),
            existing_nullable=False,
        )

    op.create_unique_constraint(
        "uq_receta_nombre_version", "receta", ["nombre", "version"]
    )


def downgrade():
    op.drop_constraint("uq_receta_nombre_version", "receta", type_="unique")

    with op.batch_alter_table("receta", schema=None) as batch_op:
        batch_op.alter_column(
            "rendimiento_base",
            existing_type=sa.Numeric(precision=12, scale=4),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        batch_op.drop_column("categoria")
        batch_op.drop_column("unidad_produccion")
        batch_op.drop_column("descripcion")
        batch_op.drop_column("nombre")

    with op.batch_alter_table("producto", schema=None) as batch_op:
        batch_op.drop_constraint("fk_producto_id_receta", type_="foreignkey")
        batch_op.drop_column("id_receta")
