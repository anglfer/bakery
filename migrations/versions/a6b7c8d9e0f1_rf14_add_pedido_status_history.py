"""RF14 add pedido status history table

Revision ID: a6b7c8d9e0f1
Revises: ff14a5b6c7d8
Create Date: 2026-04-05 12:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a6b7c8d9e0f1"
down_revision = "ff14a5b6c7d8"
branch_labels = None
depends_on = None


TABLE_NAME = "pedido_estado_historial"
INDEX_NAME = "ix_pedido_estado_historial_pedido_fecha"


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


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
        op.create_table(
            TABLE_NAME,
            sa.Column("id_historial", sa.Integer(), nullable=False),
            sa.Column("id_pedido", sa.Integer(), nullable=False),
            sa.Column("estado_anterior", sa.String(length=20), nullable=True),
            sa.Column("estado_nuevo", sa.String(length=20), nullable=False),
            sa.Column("detalle", sa.String(length=255), nullable=True),
            sa.Column("id_usuario", sa.Integer(), nullable=True),
            sa.Column(
                "fecha_cambio",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(["id_pedido"], ["pedido.id_pedido"]),
            sa.ForeignKeyConstraint(["id_usuario"], ["usuario.id_usuario"]),
            sa.PrimaryKeyConstraint("id_historial"),
        )

    if _table_exists(TABLE_NAME) and not _index_exists(TABLE_NAME, INDEX_NAME):
        op.create_index(
            INDEX_NAME, TABLE_NAME, ["id_pedido", "fecha_cambio"], unique=False
        )

    op.execute(
        sa.text(
            """
            INSERT INTO pedido_estado_historial (
                id_pedido,
                estado_anterior,
                estado_nuevo,
                detalle,
                id_usuario,
                fecha_cambio
            )
            SELECT
                p.id_pedido,
                NULL,
                COALESCE(p.estado_pedido, 'PENDIENTE'),
                'Estado inicial migrado desde pedido existente.',
                p.id_usuario_cliente,
                COALESCE(p.fecha_pedido, CURRENT_TIMESTAMP)
            FROM pedido p
            WHERE NOT EXISTS (
                SELECT 1
                FROM pedido_estado_historial h
                WHERE h.id_pedido = p.id_pedido
            )
            """
        )
    )


def downgrade() -> None:
    if _table_exists(TABLE_NAME) and _index_exists(TABLE_NAME, INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)

    if _table_exists(TABLE_NAME):
        op.drop_table(TABLE_NAME)
